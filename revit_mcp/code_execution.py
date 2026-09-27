# -*- coding: UTF-8 -*-
"""
Code Execution Module for Revit MCP
Handles direct execution of IronPython code in Revit context.

Cada ejecucion se envuelve en un DB.TransactionGroup llamado
"IA: <descripcion>" para que todo lo que haga el codigo aparezca como UNA sola
entrada en el historial de deshacer de Revit (Ctrl+Z). Dentro del grupo sigue
abriendose la Transaction de siempre, asi el codigo recibido no necesita crear
la suya (y no debe: Revit no permite transacciones anidadas).

Escritura segura: la ruta pasa por escritura.ejecutar (copia de seguridad,
log con el codigo completo, `simular`). `description` es obligatoria; el
codigo que borra colecciones con doc.Delete(<coleccion>) se rechaza salvo
`forzar`. Routes no expone DocumentChangedEventArgs, asi que
`elementos_modificados` se calcula comparando los ids del modelo y las
advertencias antes y despues de ejecutar.

Nota: este modulo NO importa print_function de __future__ a proposito: exec()
hereda los flags de __future__ del modulo y el codigo recibido dejaria de
aceptar la sentencia print de Python 2, que hoy funciona.
"""
from pyrevit import routes, revit, DB
from utils import suppress_warnings, get_element_id_value, make_element_id, buscar_por_nombre
from seguridad import requiere_token
from escritura import (
    ejecutar, simulacion, EscrituraRechazada, es_forzado, nombre_transaccion,
    describir_elemento, esperar_copia_pendiente,
)
import json
import logging
import re
import sys
import traceback
from StringIO import StringIO

# Standard logger setup
logger = logging.getLogger(__name__)

LONGITUD_MAX_DESCRIPCION = 60
MAX_DESCRITOS = 50

_PATRON_DELETE = re.compile(r"doc\s*\.\s*Delete\s*\(\s*([^)]*)\)")
_INDICIOS_COLECCION = ("List[", "ToElementIds", "ids", "Ids", "[", "set(", "list(", "Collector")


def obtener_descripcion(data):
    """Descripcion de la orden (obligatoria) recortada a 60 caracteres, o None."""
    try:
        descripcion = (data.get("description") or u"").strip()
    except Exception:
        descripcion = u""
    return descripcion[:LONGITUD_MAX_DESCRIPCION] or None


def borra_colecciones(codigo):
    """True si el codigo llama a doc.Delete( con algo que parece una coleccion."""
    for coincidencia in _PATRON_DELETE.finditer(codigo or u""):
        argumento = coincidencia.group(1).strip()
        for indicio in _INDICIOS_COLECCION:
            if indicio in argumento:
                return True
    return False


def cerrar_transacciones_abiertas(espacio):
    """Hace RollBack de toda DB.Transaction viva que dejo el codigo en su espacio.

    Devuelve la lista de nombres de variable cuya transaccion no pudo cerrarse.
    """
    no_cerradas = []
    for nombre, valor in list(espacio.items()):
        try:
            if not isinstance(valor, DB.Transaction):
                continue
            if valor.HasStarted() and not valor.HasEnded():
                valor.RollBack()
        except Exception as error:
            logger.warning(
                u"No se pudo cerrar la Transaction '%s': %s", nombre, str(error)
            )
            no_cerradas.append(nombre)
    return no_cerradas


def _grupo_abierto(grupo):
    try:
        return grupo is not None and grupo.HasStarted() and not grupo.HasEnded()
    except Exception:
        return False


def _ids_modelo(doc):
    """Conjunto de ids (int) de todos los elementos que no son tipos."""
    try:
        ids = DB.FilteredElementCollector(doc).WhereElementIsNotElementType().ToElementIds()
        return set(get_element_id_value(i) for i in ids)
    except Exception as error:
        logger.warning(u"No se pudieron contar los elementos: %s", str(error))
        return None


def _avisos(doc):
    """Lista de (texto, ids) de doc.GetWarnings(), o None si falla."""
    try:
        avisos = []
        for aviso in doc.GetWarnings():
            try:
                ids = tuple(sorted(get_element_id_value(i) for i in aviso.GetFailingElements()))
            except Exception:
                ids = ()
            try:
                texto = aviso.GetDescriptionText()
            except Exception:
                texto = u"?"
            avisos.append((texto, ids))
        return avisos
    except Exception:
        return None


def elementos_modificados(doc, ids_antes, avisos_antes):
    """Diferencia de ids y de advertencias entre antes y despues del codigo."""
    resumen = {"metodo": "comparacion de ids y advertencias antes/despues (Routes no expone DocumentChanged)"}
    ids_despues = _ids_modelo(doc)
    if ids_antes is not None and ids_despues is not None:
        nuevos = sorted(ids_despues - ids_antes)
        borrados = sorted(ids_antes - ids_despues)
        descritos = []
        por_categoria = {}
        for identificador in nuevos[:MAX_DESCRITOS]:
            descripcion = describir_elemento(doc, identificador)
            if descripcion is None:
                continue
            descritos.append(descripcion)
            categoria = descripcion.get("categoria") or u"(sin categoria)"
            por_categoria[categoria] = por_categoria.get(categoria, 0) + 1
        resumen.update({
            "creados": descritos,
            "creados_count": len(nuevos),
            "creados_ids": nuevos[:500],
            "eliminados": borrados[:500],
            "eliminados_count": len(borrados),
            "por_categoria": por_categoria,
        })
        if len(nuevos) > MAX_DESCRITOS:
            resumen["nota"] = u"Solo se describen los primeros {} elementos creados".format(MAX_DESCRITOS)
    avisos_despues = _avisos(doc)
    if avisos_antes is not None and avisos_despues is not None:
        previos = set(avisos_antes)
        nuevas = [
            {"descripcion": texto, "element_ids": list(ids)}
            for texto, ids in avisos_despues if (texto, ids) not in previos
        ]
        resumen["advertencias"] = {
            "antes": len(avisos_antes),
            "despues": len(avisos_despues),
            "nuevas": nuevas[:MAX_DESCRITOS],
        }
    return resumen


def _espacio(doc, captured_output):
    """Espacio de nombres del codigo: doc, DB, revit, clr, System, print y los
    helpers make_element_id, get_element_id_value y buscar_parametro."""
    # System and clr are pre-imported so callers can use the Revit 2027-safe
    # ElementId pattern: DB.ElementId(System.Int64(id)). In 2027 a bare
    # DB.ElementId(<int>) raises "Multiple targets could match";
    # make_element_id(id) does the same for every Revit version.
    import clr as _clr
    import System as _System
    return {
        "doc": doc,
        "DB": DB,
        "revit": revit,
        "clr": _clr,
        "System": _System,
        "make_element_id": make_element_id,
        "get_element_id_value": get_element_id_value,
        "buscar_parametro": buscar_por_nombre,
        "__builtins__": __builtins__,
        "print": lambda *args: captured_output.write(
            " ".join(str(arg) for arg in args) + "\n"
        ),
    }


def _pistas(error_type, error_msg):
    hints = []
    if error_type == "AttributeError":
        if "Name" in error_msg:
            hints.append(
                "The 'Name' property may not be directly accessible in IronPython. "
                "Try using getattr(element, 'Name', 'N/A') or "
                "element.get_Parameter(DB.BuiltInParameter.ALL_MODEL_TYPE_NAME).AsString()"
            )
        else:
            hints.append(
                "Some Revit API properties are not directly accessible in IronPython. "
                "Try using getattr(obj, 'property_name', default_value) for safe access."
            )
    elif error_type == "NullReferenceException" or "NoneType" in error_msg:
        hints.append(
            "An object is None/null. Ensure you check if elements exist before "
            "accessing their properties: 'if element:' or 'if element is not None:'"
        )
    elif error_type == "InvalidOperationException":
        hints.append(
            "This operation may require being inside a transaction, or the element "
            "may be in a state that doesn't allow this operation."
        )
    elif "Transaction" in error_msg or "transaction" in error_msg:
        hints.append(
            "Transaction error. Note that this endpoint already wraps your code "
            "in a transaction. Avoid starting nested transactions."
        )
    if "Multiple targets could match" in error_msg:
        hints.append(
            "Revit 2027: build ElementIds with make_element_id(id) (already defined, any Revit version) "
            "or DB.ElementId(System.Int64(id)) instead of DB.ElementId(id)."
        )
    if error_type == "ImportError" and "utils" in error_msg:
        hints.append(
            "Do not import the server modules: make_element_id, get_element_id_value and "
            "buscar_parametro(elem, name) are already defined in the code's namespace."
        )
    return hints


def _correr_sin_documento(code_to_execute, description):
    """Sin documento activo el codigo se ejecuta sin grupo ni transaccion."""
    old_stdout = sys.stdout
    captured_output = StringIO()
    try:
        sys.stdout = captured_output
        exec(code_to_execute, _espacio(None, captured_output))
        sys.stdout = old_stdout
        output = captured_output.getvalue()
        return routes.make_response(
            data={
                "status": "success",
                "description": description,
                "undo_name": None,
                "output": output if output else "Code executed successfully (no output)",
                "code_executed": code_to_execute,
                "note": "No active document: the code ran without a document, group or transaction.",
            }
        )
    except Exception as exec_error:
        sys.stdout = old_stdout
        error_type = type(exec_error).__name__
        return routes.make_response(
            data={
                "status": "error",
                "error": "{}: {}".format(error_type, str(exec_error)),
                "error_type": error_type,
                "traceback": traceback.format_exc(),
                "code_attempted": code_to_execute,
                "partial_output": captured_output.getvalue(),
                "hints": _pistas(error_type, str(exec_error)),
            },
            status=500,
        )
    finally:
        sys.stdout = old_stdout


def register_code_execution_routes(api):
    """Register code execution routes with the API."""

    @api.route("/execute_code/", methods=["POST"])
    @requiere_token
    def execute_code(doc, request):
        """
        Execute IronPython code in Revit context.

        Expected payload:
        {
            "code": "python code as string",
            "description": "what the code does (required, max 60 chars)",
            "simular": false,
            "forzar": false
        }

        Everything the code changes is grouped in a Revit TransactionGroup
        named "IA: <description>" (one undo entry). Without an active document
        the code runs without any group or transaction.
        """
        if doc is None:
            try:
                data = json.loads(request.data) if isinstance(request.data, str) else (request.data or {})
            except Exception as error:
                return routes.make_response(data={"error": "Invalid JSON format: {}".format(error)}, status=400)
            code_to_execute = data.get("code", "")
            if not code_to_execute:
                return routes.make_response(data={"error": "No code provided"}, status=400)
            description = obtener_descripcion(data)
            if not description:
                return routes.make_response(
                    data={"error": "description is required: say in a few words what the code does"},
                    status=400,
                )
            return _correr_sin_documento(code_to_execute, description)

        def cuerpo(ctx):
            data = ctx["data"]
            code_to_execute = data.get("code", "")
            if not code_to_execute:
                raise EscrituraRechazada("No code provided", 400)
            description = obtener_descripcion(data)
            if not description:
                raise EscrituraRechazada(
                    "description is required: say in a few words what the code does "
                    "(it names the Revit undo entry 'IA: <description>')",
                    400,
                )
            colecciones = borra_colecciones(code_to_execute)
            if colecciones and not es_forzado(data):
                raise EscrituraRechazada(
                    "The code deletes a collection with doc.Delete(<collection>). Use delete_elements "
                    "with the explicit ids (it lists them first, max 200) or repeat with forzar=true.",
                    400,
                    {"borra_colecciones": True},
                )

            nombre_grupo = nombre_transaccion(description)
            if ctx["simular"]:
                return simulacion([{
                    "accion": "execute_code",
                    "description": description,
                    "undo_name": nombre_grupo,
                    "lineas": len(code_to_execute.splitlines()),
                    "borra_colecciones": colecciones,
                    "nota": "El codigo no se ejecuta con simular=true.",
                }])

            logger.info(u"Executing code: {}".format(description))
            ids_antes = _ids_modelo(doc)
            avisos_antes = _avisos(doc)

            tg = None
            t = None
            namespace = {}
            old_stdout = sys.stdout
            captured_output = StringIO()

            try:
                # Grupo: una sola entrada de deshacer para toda la orden
                tg = DB.TransactionGroup(doc, nombre_grupo)
                tg.Start()
                t = DB.Transaction(doc, nombre_grupo)
                t.Start()
                suppress_warnings(t)

                sys.stdout = captured_output
                namespace = _espacio(doc, captured_output)
                exec(code_to_execute, namespace)
                sys.stdout = old_stdout

                output = captured_output.getvalue()
                captured_output.close()

                if t is not None:
                    # 0.4.0: la copia del .rvt corre en un hilo; nunca se confirma sin ella
                    esperar_copia_pendiente()
                    estado = t.Commit()
                    if estado != DB.TransactionStatus.Committed:
                        raise RuntimeError(
                            u"Revit revirtio la transaccion (estado {}): el modelo no cambio".format(estado)
                        )
                if tg is not None:
                    tg.Assimilate()

                return {
                    "status": "success",
                    "description": description,
                    "undo_name": nombre_grupo,
                    "output": output if output else "Code executed successfully (no output)",
                    "code_executed": code_to_execute,
                    "elementos_modificados": elementos_modificados(doc, ids_antes, avisos_antes),
                    "borra_colecciones": colecciones,
                }

            except Exception as exec_error:
                sys.stdout = old_stdout
                partial_output = captured_output.getvalue()
                try:
                    captured_output.close()
                except Exception:
                    pass
                error_traceback = traceback.format_exc()

                # 1) Transactions the code itself left open in its namespace
                no_cerradas = cerrar_transacciones_abiertas(namespace)

                # 2) Our own transaction, if still active
                try:
                    if t is not None and t.HasStarted() and not t.HasEnded():
                        t.RollBack()
                except Exception as error:
                    logger.warning(u"No se pudo revertir la Transaction: %s", str(error))

                # 3) The group: only if the document is no longer being modified
                transaccion_colgada = False
                try:
                    transaccion_colgada = bool(doc.IsModifiable)
                except Exception:
                    transaccion_colgada = False

                if transaccion_colgada:
                    logger.error(
                        u"Quedo una Transaction abierta tras el error; el grupo '%s' "
                        u"no se revierte para no perder el estado. Revisar en Revit.",
                        nombre_grupo,
                    )
                elif _grupo_abierto(tg):
                    try:
                        tg.RollBack()
                    except Exception as error:
                        logger.warning(u"No se pudo revertir el grupo: %s", str(error))

                error_type = type(exec_error).__name__
                error_msg = str(exec_error)
                enhanced_message = "{}: {}".format(error_type, error_msg)
                logger.error("Code execution failed: {}".format(enhanced_message))
                logger.error("Traceback: {}".format(error_traceback))

                response_data = {
                    "status": "error",
                    "error": enhanced_message,
                    "error_type": error_type,
                    "traceback": error_traceback,
                    "code_attempted": code_to_execute,
                    "undo_name": nombre_grupo,
                }
                if transaccion_colgada:
                    response_data["error"] = (
                        u"Quedó una Transaction abierta que no se pudo cerrar "
                        u"(doc.IsModifiable sigue siendo True): revísala en Revit antes de "
                        u"continuar. El grupo '{}' NO se ha revertido. Error original: {}"
                    ).format(nombre_grupo, enhanced_message)
                    response_data["open_transaction"] = True
                    if no_cerradas:
                        response_data["unclosed_transactions"] = no_cerradas
                if partial_output:
                    response_data["partial_output"] = partial_output
                hints = _pistas(error_type, error_msg)
                if hints:
                    response_data["hints"] = hints
                if ctx.get("copia"):
                    response_data["copia"] = ctx["copia"]
                return routes.make_response(data=response_data, status=500)

        return ejecutar(doc, "/execute_code/", request, cuerpo)

    logger.info("Code execution routes registered successfully.")
