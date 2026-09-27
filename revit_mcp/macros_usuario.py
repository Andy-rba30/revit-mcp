# -*- coding: UTF-8 -*-
"""
Macros propias del usuario para Revit MCP (0.4.0):

  GET  /macros/       catalogo: manifiestos validos (name, description, version, args,
                      writes, timeout_s) e invalidos con su error
  POST /macros/run/   {"name", "args", "simular", "forzar"} -> ejecuta macro.py

Objetivo: lo repetitivo se ejecuta como un plugin determinista del usuario y
la IA solo elige cuando y con que argumentos.

Carpeta: %LOCALAPPDATA%\\RevitMcp\\macros\\<nombre>\\ (o la variable de entorno
REVIT_MCP_MACROS) con:

  macro.json  {"name", "description", "version",
               "args": {<nombre>: {"type": "int|float|str|bool|list|element_id|element_ids|level|view",
                                   "required", "default", "description"}},
               "writes": true|false, "timeout_s": 120}
  macro.py    def run(doc, uidoc, args, api)          obligatoria
              def plan(doc, args, api)                opcional: lo que haria (simular)

`api` expone los helpers ya probados del servidor: make_element_id,
get_element_id_value, buscar_por_nombre, buscar_parametro, xyz_desde_mm,
punto_a_mm, convertir_valor, resolver_nivel, resolver_vista, nombre_nivel,
get_element_name, log(texto), DB y las constantes MM_TO_FEET / FEET_TO_MM.

La macro NO abre transacciones. Si `writes` es verdadero, el servidor la
envuelve en escritura.ejecutar + transaccion(doc, "Macro <nombre>") con el
patron de escritura completo (copia, registro en mcp_log.jsonl, `simular` con
el `plan` que devuelva plan(), comprobar_alcance sobre plan["count"] y
resultado_creacion / verificar_eliminados / antes-despues sobre lo que
devuelva run()). Si `writes` es falso se ejecuta sin copia ni transaccion (y
Revit rechaza cualquier cambio que intente).

Carga: cada macro.py se carga con imp.load_source (IronPython 2.7; en CPython
3.12+ con importlib.util) y se recarga si cambio el archivo (mtime), asi el
usuario edita sin reiniciar Revit. Los args se validan contra el manifiesto
(400 con `faltan`, `sobran` e `invalidos`); `level` y `view` llegan a la macro
ya resueltos como DB.Level / DB.View; `element_id(s)` se comprueban en el
documento.

Add-ins en C# o comandos de pyRevit existentes: ver CONTRATO.md (se envuelven
en una macro `writes: false` que llama a PostCommand; no admiten argumentos ni
transaccion propia y el servidor no puede verificar lo que hacen).
"""

from utils import (
    get_element_name, get_element_id_value, make_element_id, buscar_por_nombre, xyz_desde_mm, punto_a_mm,
    mapa_niveles, MM_TO_FEET, FEET_TO_MM,
)
from seguridad import requiere_token
from escritura import (
    ejecutar, transaccion, simulacion, EscrituraRechazada, resultado_creacion, verificar_eliminados,
    comprobar_alcance, datos_peticion, nombre_nivel, _a_texto,
)
from parameters import convertir_valor, buscar_parametro
from navegacion import _responder, _texto_seguro
from pyrevit import routes, revit, DB
import io
import json
import os
import re
import time
import traceback
import logging

try:
    import imp as _imp  # IronPython 2.7 / CPython <= 3.11
except ImportError:  # pragma: no cover - CPython 3.12+ en las pruebas
    _imp = None

logger = logging.getLogger(__name__)

try:
    _texto = unicode  # IronPython 2.7
    _cadena = basestring
    _enteros = (int, long)
except NameError:  # pragma: no cover - CPython 3 en las pruebas
    _texto = str
    _cadena = str
    _enteros = (int,)

VARIABLE_CARPETA = "REVIT_MCP_MACROS"
NOMBRE_MANIFIESTO = "macro.json"
NOMBRE_CODIGO = "macro.py"
TIPOS = ("int", "float", "str", "bool", "list", "element_id", "element_ids", "level", "view")
TIMEOUT_DEFECTO_S = 120
_PATRON_NOMBRE = re.compile(r"^[A-Za-z0-9_\-]+$")
_PREFIJO_MODULO = "revit_mcp_macro_"

# nombre -> {"mtime": float, "modulo": module}
_modulos = {}


# ---------------------------------------------------------------------------
# Carpeta y manifiestos
# ---------------------------------------------------------------------------
def carpeta_macros():
    """REVIT_MCP_MACROS o %LOCALAPPDATA%\\RevitMcp\\macros."""
    configurada = os.environ.get(VARIABLE_CARPETA)
    if configurada:
        return configurada
    return os.path.join(os.environ.get("LOCALAPPDATA", ""), "RevitMcp", "macros")


def _bool(valor, por_defecto=False):
    if valor is None:
        return por_defecto
    if isinstance(valor, _cadena):
        return valor.strip().lower() in ("1", "true", "si", "sí", "yes")
    return bool(valor)


def leer_manifiesto(carpeta):
    """Lee y valida <carpeta>/macro.json. Lanza ValueError con un mensaje claro."""
    nombre_carpeta = os.path.basename(carpeta.rstrip("\\/"))
    ruta_json = os.path.join(carpeta, NOMBRE_MANIFIESTO)
    ruta_py = os.path.join(carpeta, NOMBRE_CODIGO)
    if not os.path.isfile(ruta_json):
        raise ValueError("falta {}".format(NOMBRE_MANIFIESTO))
    if not os.path.isfile(ruta_py):
        raise ValueError("falta {}".format(NOMBRE_CODIGO))
    try:
        with io.open(ruta_json, "r", encoding="utf-8-sig") as archivo:
            datos = json.load(archivo)
    except Exception as error:
        raise ValueError("{} no es JSON valido: {}".format(NOMBRE_MANIFIESTO, error))
    if not isinstance(datos, dict):
        raise ValueError("{} debe ser un objeto JSON".format(NOMBRE_MANIFIESTO))
    nombre = _texto_seguro(datos.get("name") or nombre_carpeta).strip()
    if not _PATRON_NOMBRE.match(nombre):
        raise ValueError("name '{}' no valido: letras, digitos, '_' y '-'".format(nombre))
    if nombre != nombre_carpeta:
        raise ValueError("name '{}' no coincide con la carpeta '{}'".format(nombre, nombre_carpeta))
    args = datos.get("args") or {}
    if not isinstance(args, dict):
        raise ValueError("args debe ser un objeto {nombre: {type, required, default, description}}")
    argumentos = {}
    for clave, spec in args.items():
        if not isinstance(spec, dict):
            raise ValueError("args.{} debe ser un objeto {{type, required, default, description}}".format(clave))
        tipo = _texto_seguro(spec.get("type") or "str").strip().lower()
        if tipo not in TIPOS:
            raise ValueError("args.{}: type '{}' no admitido ({})".format(clave, tipo, ", ".join(TIPOS)))
        argumentos[_texto_seguro(clave)] = {
            "type": tipo,
            "required": _bool(spec.get("required"), False),
            "default": spec.get("default"),
            "description": _texto_seguro(spec.get("description") or u""),
        }
    timeout_s = datos.get("timeout_s", TIMEOUT_DEFECTO_S)
    try:
        timeout_s = float(timeout_s)
    except (TypeError, ValueError):
        raise ValueError("timeout_s debe ser un numero de segundos")
    return {
        "name": nombre,
        "description": _texto_seguro(datos.get("description") or u""),
        "version": _texto_seguro(datos.get("version") or u""),
        "args": argumentos,
        "writes": _bool(datos.get("writes"), False),
        "timeout_s": timeout_s,
        "carpeta": carpeta,
        "ruta_py": ruta_py,
        "ruta_json": ruta_json,
    }


def catalogo():
    """(validas, invalidas): manifiestos de cada subcarpeta de carpeta_macros()."""
    carpeta = carpeta_macros()
    validas = []
    invalidas = []
    if not os.path.isdir(carpeta):
        return validas, invalidas
    for nombre in sorted(os.listdir(carpeta)):
        ruta = os.path.join(carpeta, nombre)
        if not os.path.isdir(ruta) or nombre.startswith(".") or nombre.startswith("_"):
            continue
        try:
            validas.append(leer_manifiesto(ruta))
        except ValueError as error:
            invalidas.append({"name": nombre, "carpeta": ruta, "error": _texto_seguro(error)})
    return validas, invalidas


def _publico(manifiesto):
    return dict((clave, manifiesto[clave]) for clave in
                ("name", "description", "version", "args", "writes", "timeout_s", "carpeta"))


def buscar_macro(name):
    """Manifiesto de la macro `name`; 404 con `available_macros` si no existe o es invalido."""
    nombre = _texto_seguro(name).strip()
    if not nombre:
        raise EscrituraRechazada("name is required (macro folder name)", 400)
    validas, invalidas = catalogo()
    for manifiesto in validas:
        if manifiesto["name"] == nombre:
            return manifiesto
    for invalida in invalidas:
        if invalida["name"] == nombre:
            raise EscrituraRechazada(
                "Macro '{}' has an invalid manifest: {}".format(nombre, invalida["error"]), 400,
                {"available_macros": [m["name"] for m in validas]},
            )
    raise EscrituraRechazada(
        "Macro '{}' not found in {}".format(nombre, carpeta_macros()), 404,
        {"available_macros": [m["name"] for m in validas], "carpeta": carpeta_macros()},
    )


# ---------------------------------------------------------------------------
# Carga del modulo (recarga por mtime)
# ---------------------------------------------------------------------------
def _cargar_archivo(nombre_modulo, ruta):
    if _imp is not None:
        return _imp.load_source(nombre_modulo, ruta)
    import importlib.util  # CPython 3.12+ (las pruebas); no existe en IronPython 2.7
    spec = importlib.util.spec_from_file_location(nombre_modulo, ruta)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def cargar_modulo(manifiesto):
    """Modulo de macro.py, cargado la primera vez y recargado si cambio el mtime. 400 si no importa."""
    ruta = manifiesto["ruta_py"]
    try:
        mtime = os.path.getmtime(ruta)
    except OSError as error:
        raise EscrituraRechazada("Cannot read {}: {}".format(ruta, error), 400)
    entrada = _modulos.get(manifiesto["name"])
    if entrada is not None and entrada["mtime"] == mtime and entrada["ruta"] == ruta:
        return entrada["modulo"], False
    try:
        modulo = _cargar_archivo(_PREFIJO_MODULO + manifiesto["name"], ruta)
    except Exception as error:
        raise EscrituraRechazada(
            "macro.py of '{}' could not be loaded: {}: {}".format(manifiesto["name"], type(error).__name__, error), 400,
            {"traceback": traceback.format_exc()},
        )
    if not hasattr(modulo, "run") or not callable(getattr(modulo, "run")):
        raise EscrituraRechazada("macro.py of '{}' does not define run(doc, uidoc, args, api)".format(manifiesto["name"]), 400)
    _modulos[manifiesto["name"]] = {"mtime": mtime, "ruta": ruta, "modulo": modulo}
    return modulo, entrada is not None


# ---------------------------------------------------------------------------
# Argumentos
# ---------------------------------------------------------------------------
def _entero(valor):
    if isinstance(valor, bool):
        raise ValueError("expected an integer")
    if isinstance(valor, float) and not valor.is_integer():
        raise ValueError("expected an integer")
    return int(valor)


def _convertir_arg(doc, tipo, valor):
    """Valor normalizado (o resuelto en el documento) segun el tipo del manifiesto. Lanza ValueError."""
    if tipo == "int":
        return _entero(valor)
    if tipo == "float":
        if isinstance(valor, bool):
            raise ValueError("expected a number")
        return float(valor)
    if tipo == "str":
        return valor if isinstance(valor, _cadena) else _texto(valor)
    if tipo == "bool":
        if isinstance(valor, _cadena):
            bajo = valor.strip().lower()
            if bajo in ("true", "1", "si", "sí", "yes"):
                return True
            if bajo in ("false", "0", "no"):
                return False
            raise ValueError("expected true or false")
        return bool(valor)
    if tipo == "list":
        if not isinstance(valor, (list, tuple)):
            raise ValueError("expected a list")
        return list(valor)
    if tipo == "element_id":
        identificador = _entero(valor)
        if doc.GetElement(make_element_id(identificador)) is None:
            raise ValueError("element {} not found".format(identificador))
        return identificador
    if tipo == "element_ids":
        if not isinstance(valor, (list, tuple)):
            raise ValueError("expected a list of element ids")
        ids = []
        for crudo in valor:
            identificador = _entero(crudo)
            if doc.GetElement(make_element_id(identificador)) is None:
                raise ValueError("element {} not found".format(identificador))
            ids.append(identificador)
        return ids
    if tipo == "level":
        niveles = mapa_niveles(doc)
        nivel = niveles.get(_texto_seguro(valor))
        if nivel is None:
            raise ValueError("level '{}' not found (available: {})".format(valor, ", ".join(sorted(niveles.keys()))))
        return nivel
    if tipo == "view":
        vista = None
        if isinstance(valor, _enteros) and not isinstance(valor, bool):
            vista = doc.GetElement(make_element_id(valor))
        else:
            nombre = _texto_seguro(valor)
            for candidata in DB.FilteredElementCollector(doc).OfClass(DB.View).WhereElementIsNotElementType():
                try:
                    if not candidata.IsTemplate and get_element_name(candidata) == nombre:
                        vista = candidata
                        break
                except Exception:
                    continue
        if vista is None or not isinstance(vista, DB.View):
            raise ValueError("view '{}' not found".format(valor))
        return vista
    raise ValueError("unsupported type {}".format(tipo))


def validar_args(doc, manifiesto, args):
    """args recibidos -> (args para la macro, args para el registro). 400 con faltan/sobran/invalidos."""
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise EscrituraRechazada("args must be an object {name: value}", 400)
    spec = manifiesto["args"]
    sobran = sorted(clave for clave in args if clave not in spec)
    faltan = []
    invalidos = {}
    normalizados = {}
    registro = {}
    for nombre, definicion in spec.items():
        if nombre in args and args[nombre] is not None:
            valor = args[nombre]
        elif definicion["required"]:
            faltan.append(nombre)
            continue
        else:
            valor = definicion["default"]
            if valor is None:
                normalizados[nombre] = None
                registro[nombre] = None
                continue
        try:
            normalizados[nombre] = _convertir_arg(doc, definicion["type"], valor)
        except ValueError as error:
            invalidos[nombre] = "{}: {}".format(definicion["type"], error)
            continue
        registro[nombre] = valor
    if faltan or sobran or invalidos:
        partes = []
        if faltan:
            partes.append("missing: {}".format(", ".join(sorted(faltan))))
        if sobran:
            partes.append("unknown: {}".format(", ".join(sobran)))
        if invalidos:
            partes.append("invalid: {}".format("; ".join("{} ({})".format(k, invalidos[k]) for k in sorted(invalidos))))
        raise EscrituraRechazada(
            "Arguments of macro '{}' do not match macro.json ({})".format(manifiesto["name"], "; ".join(partes)), 400,
            {"faltan": sorted(faltan), "sobran": sobran, "invalidos": invalidos, "args_spec": spec},
        )
    return normalizados, registro


# ---------------------------------------------------------------------------
# API que recibe la macro
# ---------------------------------------------------------------------------
class ApiMacro(object):
    """Helpers del servidor para macro.py (todos ya probados en las rutas)."""

    def __init__(self, doc):
        self.doc = doc
        self.DB = DB
        self.MM_TO_FEET = MM_TO_FEET
        self.FEET_TO_MM = FEET_TO_MM
        self.make_element_id = make_element_id
        self.get_element_id_value = get_element_id_value
        self.get_element_name = get_element_name
        self.buscar_por_nombre = buscar_por_nombre
        self.xyz_desde_mm = xyz_desde_mm
        self.punto_a_mm = punto_a_mm
        self.convertir_valor = convertir_valor
        self._salida = []

    def buscar_parametro(self, elem, nombre, incluir_tipo=True):
        """Parametro de ejemplar (o de tipo) por nombre visible, alias ingles o BuiltInParameter."""
        return buscar_parametro(self.doc, elem, nombre, incluir_tipo)

    def resolver_nivel(self, nombre):
        """DB.Level por el nombre que muestra Revit. Lanza ValueError con los disponibles."""
        return _convertir_arg(self.doc, "level", nombre)

    def resolver_vista(self, nombre_o_id):
        """DB.View (no plantilla) por nombre exacto o id. Lanza ValueError."""
        return _convertir_arg(self.doc, "view", nombre_o_id)

    def nombre_nivel(self, elem):
        """Nombre del nivel del elemento (LevelId o parametro de nivel), o None."""
        return nombre_nivel(self.doc, elem)

    def log(self, texto):
        """Anade una linea a `output` de la respuesta."""
        self._salida.append(_texto_seguro(texto))

    def salida(self):
        return u"\n".join(self._salida)


# ---------------------------------------------------------------------------
# Ejecucion
# ---------------------------------------------------------------------------
def _serializable(valor, profundidad=0):
    """Deja el resultado de la macro listo para JSON (ElementId -> int, objetos -> texto)."""
    if profundidad > 6:
        return _a_texto(valor)
    if valor is None or isinstance(valor, (bool, float) + _enteros) or isinstance(valor, _cadena):
        return valor
    if isinstance(valor, dict):
        return dict((_texto_seguro(k), _serializable(v, profundidad + 1)) for k, v in valor.items())
    if isinstance(valor, (list, tuple, set)):
        return [_serializable(v, profundidad + 1) for v in valor]
    return _a_texto(valor)


def _ids(valor):
    """Lista de ids enteros si `valor` es una lista de ids/elementos, si no None."""
    if not isinstance(valor, (list, tuple)):
        return None
    ids = []
    for elemento in valor:
        try:
            ids.append(get_element_id_value(elemento))
        except Exception:
            return None
    return ids


def _plan_de(modulo, doc, args, api):
    if not hasattr(modulo, "plan") or not callable(getattr(modulo, "plan")):
        return None
    return _serializable(modulo.plan(doc, args, api))


def _alcance(plan):
    if isinstance(plan, dict):
        for clave in ("count", "total", "elements"):
            valor = plan.get(clave)
            if isinstance(valor, _enteros) and not isinstance(valor, bool):
                return valor
    return 0


def interpretar_resultado(doc, resultado):
    """run() puede devolver ids, un dict con creados/eliminados/antes/despues, o cualquier otra cosa."""
    salida = {}
    ids = _ids(resultado)
    if ids is not None:
        salida.update(resultado_creacion(doc, ids))
        salida["result"] = ids
        return salida
    if isinstance(resultado, dict):
        creados = _ids(resultado.get("creados"))
        if creados is not None and "creados" in resultado:
            salida.update(resultado_creacion(doc, creados))
        eliminados = _ids(resultado.get("eliminados"))
        if eliminados is not None and "eliminados" in resultado:
            verificacion = verificar_eliminados(doc, eliminados, [])
            salida["eliminados"] = verificacion["eliminados"]
            if not verificacion["ok"]:
                salida["ok"] = False
                salida["verificacion"] = verificacion["verificacion"]
        for clave in ("antes", "despues"):
            if clave in resultado:
                salida[clave] = _serializable(resultado[clave])
        salida["result"] = _serializable(dict(
            (k, v) for k, v in resultado.items() if k not in ("creados", "eliminados", "antes", "despues")
        ))
        if "ok" in resultado and not resultado["ok"]:
            salida["ok"] = False
        return salida
    salida["result"] = _serializable(resultado)
    return salida


def register_macros_usuario_routes(api):
    """Register the user-macro routes with the API."""

    @api.route("/macros/", methods=["GET"])
    @requiere_token
    def list_macros(doc):
        """Catalogo de macros del usuario (manifiestos validos e invalidos)."""
        try:
            validas, invalidas = catalogo()
            return routes.make_response(data={
                "carpeta": carpeta_macros(),
                "macros": [_publico(m) for m in validas],
                "invalidas": invalidas,
                "count": len(validas),
                "status": "success",
            })
        except Exception as error:
            logger.error("No se pudo leer el catalogo de macros: %s", str(error))
            return routes.make_response(data={"error": str(error), "traceback": traceback.format_exc()}, status=500)

    @api.route("/macros/run/", methods=["POST"])
    @requiere_token
    def run_macro(doc, uidoc, request):
        """Ejecuta una macro del usuario; con `writes` pasa por escritura.ejecutar (copia, log, transaccion)."""
        if doc is None:
            return routes.make_response(data={"error": "No active Revit document"}, status=503)
        try:
            data = datos_peticion(request)
            manifiesto = buscar_macro(data.get("name"))
        except EscrituraRechazada as rechazo:
            cuerpo_error = {"error": rechazo.mensaje}
            cuerpo_error.update(rechazo.extra)
            return routes.make_response(data=cuerpo_error, status=rechazo.status)

        def preparar(ctx_data):
            args, registro = validar_args(doc, manifiesto, ctx_data.get("args"))
            modulo, recargado = cargar_modulo(manifiesto)
            return args, registro, modulo, recargado

        if not manifiesto["writes"]:
            def cuerpo_lectura(ctx_data):
                inicio = time.time()
                args, registro, modulo, recargado = preparar(ctx_data)
                api_macro = ApiMacro(doc)
                if _bool(ctx_data.get("simular")):
                    plan = _plan_de(modulo, doc, args, api_macro)
                    return {"name": manifiesto["name"], "args": registro, "writes": False, "simulado": True,
                            "plan": plan, "output": api_macro.salida(), "reloaded": recargado,
                            "nota": None if plan is not None else u"la macro no define plan()",
                            "ms": int((time.time() - inicio) * 1000), "status": "success"}
                resultado = modulo.run(doc, uidoc, args, api_macro)
                respuesta = {"name": manifiesto["name"], "args": registro, "writes": False,
                             "result": _serializable(resultado), "output": api_macro.salida(),
                             "reloaded": recargado, "ok": True, "ms": int((time.time() - inicio) * 1000),
                             "status": "success"}
                return respuesta

            return _responder(doc, request, cuerpo_lectura)

        def cuerpo(ctx):
            ctx_data = ctx["data"]
            args, registro, modulo, recargado = preparar(ctx_data)
            api_macro = ApiMacro(doc)
            plan = _plan_de(modulo, doc, args, api_macro)
            comprobar_alcance(ctx_data, _alcance(plan), "elementos segun plan() de la macro")
            if ctx["simular"]:
                haria = [{"accion": "macro", "name": manifiesto["name"], "args": registro, "writes": True,
                          "undo_name": u"IA: Macro {}".format(manifiesto["name"])}]
                extra = {"plan": plan, "output": api_macro.salida(), "name": manifiesto["name"], "reloaded": recargado}
                if plan is None:
                    extra["nota"] = u"la macro no define plan(): no se puede anticipar lo que haria"
                return simulacion(haria, **extra)
            with transaccion(doc, u"Macro {}".format(manifiesto["name"])):
                resultado = modulo.run(doc, uidoc, args, api_macro)
            respuesta = {"name": manifiesto["name"], "args": registro, "writes": True, "plan": plan,
                         "output": api_macro.salida(), "reloaded": recargado}
            respuesta.update(interpretar_resultado(doc, resultado))
            respuesta.setdefault("ok", True)
            return respuesta

        return ejecutar(doc, "/macros/run/", data, cuerpo)

    logger.info("Macros de usuario routes registered successfully")
