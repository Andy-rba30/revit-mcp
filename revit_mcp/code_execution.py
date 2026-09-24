# -*- coding: UTF-8 -*-
"""
Code Execution Module for Revit MCP
Handles direct execution of IronPython code in Revit context.

Cada ejecucion se envuelve en un DB.TransactionGroup llamado
"IA: <descripcion>" para que todo lo que haga el codigo aparezca como UNA sola
entrada en el historial de deshacer de Revit (Ctrl+Z). Dentro del grupo sigue
abriendose la Transaction de siempre, asi el codigo recibido no necesita crear
la suya (y no debe: Revit no permite transacciones anidadas).

Nota: este modulo NO importa print_function de __future__ a proposito: exec()
hereda los flags de __future__ del modulo y el codigo recibido dejaria de
aceptar la sentencia print de Python 2, que hoy funciona.
"""
from pyrevit import routes, revit, DB
from utils import suppress_warnings
from seguridad import requiere_token
import json
import logging
import sys
import traceback
from StringIO import StringIO

# Standard logger setup
logger = logging.getLogger(__name__)

LONGITUD_MAX_DESCRIPCION = 60
DESCRIPCION_POR_DEFECTO = u"Code execution"


def obtener_descripcion(data, codigo):
    """Descripcion de la orden para el nombre del grupo "IA: <descripcion>".

    Orden de preferencia: data["description"]; si esta vacia, la primera linea
    del codigo cuando empieza por "#" (sin el #, y saltando la declaracion de
    codificacion); si no, "Code execution". Se recorta a 60 caracteres.
    """
    descripcion = u""
    try:
        descripcion = (data.get("description") or u"").strip()
    except Exception:
        descripcion = u""

    if not descripcion and codigo:
        for linea in codigo.strip().splitlines():
            linea = linea.strip()
            if not linea:
                continue
            if linea.startswith("#"):
                candidata = linea.lstrip("#").strip()
                if candidata and "coding" not in candidata.lower():
                    descripcion = candidata
            break

    if not descripcion:
        descripcion = DESCRIPCION_POR_DEFECTO
    return descripcion[:LONGITUD_MAX_DESCRIPCION]


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
            "description": "optional description of what the code does"
        }

        Everything the code changes is grouped in a Revit TransactionGroup
        named "IA: <description>" (one undo entry). Without an active document
        the code runs without any group or transaction.
        """
        try:
            # Parse the request data
            data = (
                json.loads(request.data)
                if isinstance(request.data, str)
                else request.data
            )
            code_to_execute = data.get("code", "")

            if not code_to_execute:
                return routes.make_response(
                    data={"error": "No code provided"}, status=400
                )

            description = obtener_descripcion(data, code_to_execute)
            nombre_grupo = u"IA: " + description
            logger.info(u"Executing code: {}".format(description))

            tg = None
            t = None
            namespace = {}
            old_stdout = sys.stdout
            captured_output = StringIO()

            try:
                if doc is not None:
                    # Grupo: una sola entrada de deshacer para toda la orden
                    tg = DB.TransactionGroup(doc, nombre_grupo)
                    tg.Start()
                    # Create a transaction for any model modifications
                    t = DB.Transaction(doc, "MCP Code Execution: {}".format(description))
                    t.Start()
                    suppress_warnings(t)

                # Capture stdout to return any print statements
                sys.stdout = captured_output

                # Create a namespace with common Revit objects available.
                # System and clr are pre-imported so callers can use the
                # Revit 2027-safe ElementId pattern: DB.ElementId(System.Int64(id)).
                # In 2027 a bare DB.ElementId(<int>) raises "Multiple targets
                # could match" because of new BuiltInParameter/BuiltInCategory/Int64
                # overloads, so exposing System here avoids a common foot-gun.
                import clr as _clr
                import System as _System
                namespace = {
                    "doc": doc,
                    "DB": DB,
                    "revit": revit,
                    "clr": _clr,
                    "System": _System,
                    "__builtins__": __builtins__,
                    "print": lambda *args: captured_output.write(
                        " ".join(str(arg) for arg in args) + "\n"
                    ),
                }

                # Execute the code
                exec(code_to_execute, namespace)

                # Restore stdout
                sys.stdout = old_stdout

                # Get any printed output
                output = captured_output.getvalue()
                captured_output.close()

                # Commit the transaction and fold it into the group
                if t is not None:
                    t.Commit()
                if tg is not None:
                    tg.Assimilate()

                return routes.make_response(
                    data={
                        "status": "success",
                        "description": description,
                        "undo_name": nombre_grupo if doc is not None else None,
                        "output": (
                            output
                            if output
                            else "Code executed successfully (no output)"
                        ),
                        "code_executed": code_to_execute,
                    }
                )

            except Exception as exec_error:
                # Restore stdout if something went wrong
                sys.stdout = old_stdout

                # Capture any partial output before the error
                partial_output = captured_output.getvalue()
                captured_output.close()

                # Get the full traceback
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
                    transaccion_colgada = doc is not None and bool(doc.IsModifiable)
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

                # Build enhanced error message with hints
                error_type = type(exec_error).__name__
                error_msg = str(exec_error)
                enhanced_message = "{}: {}".format(error_type, error_msg)

                # Add helpful hints for common errors
                hints = []
                if error_type == "AttributeError":
                    if error_msg == "Name" or "Name" in error_msg:
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

                logger.error("Code execution failed: {}".format(enhanced_message))
                logger.error("Traceback: {}".format(error_traceback))

                response_data = {
                    "status": "error",
                    "error": enhanced_message,
                    "error_type": error_type,
                    "traceback": error_traceback,
                    "code_attempted": code_to_execute,
                    "undo_name": nombre_grupo if doc is not None else None,
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

                if hints:
                    response_data["hints"] = hints

                return routes.make_response(
                    data=response_data,
                    status=500,
                )

        except Exception as e:
            logger.error("Execute code request failed: {}".format(str(e)))
            return routes.make_response(data={"error": str(e)}, status=500)

    logger.info("Code execution routes registered successfully.")
