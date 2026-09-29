# -*- coding: UTF-8 -*-
"""
Macros del editor de familias (0.6.0, entrega 2c):

  POST /family/validate/   family_doc*, flex_cases[] {name, type, values}, restore (true)
                           Cada caso va en un TransactionGroup "IA: Validar <caso>": Set de valores,
                           Regenerate y comprobacion de que cada solido sigue teniendo volumen > 0.
                           RollBack del grupo si `restore`. Los errores de regeneracion no aparecen en
                           GetWarnings(): llegan al preprocesador (_FailureSwallower, ULTIMOS_ERRORES) o
                           como excepcion en Regenerate / Commit.

  POST /family/build/      spec*, save_path, load_into_project, close, simular, forzar
                           Valida el spec COMPLETO antes de abrir nada (plantilla, planos referenciados,
                           formulas, tipos, material_parameter) y ejecuta en orden: family_open ->
                           parametros -> planos -> cotas -> solidos y vaciados (con bloqueos) -> conectores
                           -> tipos -> family_validate (un caso por tipo) -> guardar -> cargar. Ante
                           cualquier fallo cierra sin guardar y devuelve el paso que fallo.

Formato del `spec` (unidades del contrato: mm y grados):
  {
    "name": "Placa base",
    "template": "Modelo generico metrico.rft" | ruta,        # nombre de archivo en FamilyTemplatePath
    "category": "OST_StructConnections",                     # opcional, BuiltInCategory
    "parameters": [{"name", "data_type", "group", "is_instance", "formula", "shared_parameter_guid"}],
    "reference_planes": [{"name", "origin_mm", "direction" (x|y|horizontal), "view", "is_reference"}],
    "dimensions": [{"reference_planes": [..], "parameter", "equal", "view", "offset_mm"}],
    "solids": [{"name", "kind", "profile", "sketch_plane", "start_mm", "end_mm", "is_void",
                "lock_ends_to": {"start", "end"}, "lock_faces": [{"face", "reference_plane"}], "material_parameter", ...}],
    "connectors": [{"domain", "solid" (nombre del solido del spec), "face", "system_type", "size_mm"}],
    "types": [{"name", "values": {parametro: valor}}],
    "flex_cases": [{"name", "type", "values"}]                # opcional: por defecto un caso por tipo
  }
  `view` y `sketch_plane` se dan como {"view_type": "FloorPlan", "level": "Ref. Level"} (el nivel se
  resuelve por el primer Level si el nombre no coincide) o como el nombre de un plano de referencia.

Compatibilidad: IronPython 2.7.
"""

from utils import get_element_name, get_element_id_value, make_element_id
from seguridad import requiere_token
from escritura import (
    ejecutar_familia, transaccion, simulacion, EscrituraRechazada, comprobar_alcance, TransaccionRevertida,
    titulo_documento, ruta_documento,
)
from pyrevit import DB
import utils as _utils
import familias as F
import familias_edicion as E
import os
import logging

logger = logging.getLogger(__name__)

try:
    _texto = unicode  # IronPython 2.7
    _cadena = basestring
except NameError:  # pragma: no cover - CPython 3 en las pruebas
    _texto = str
    _cadena = str

_t = F._texto_seguro
PASOS = ("open", "category", "parameters", "reference_planes", "dimensions", "solids", "connectors", "types",
         "validate", "save", "load", "close")
MAX_CASOS = 50


# ---------------------------------------------------------------------------
# family_validate
# ---------------------------------------------------------------------------
def _tipo_por_nombre(doc_familia, nombre):
    for tipo in F.tipos_familia(doc_familia):
        if _t(tipo.Name) == _t(nombre):
            return tipo
    return None


def planificar_casos(doc_familia, data, tipos_extra=()):
    """[caso] con {name, type (FamilyType | None), values{param: valor}}. Sin flex_cases: un caso por tipo."""
    casos = data.get("flex_cases") if isinstance(data, dict) else None
    tipos = F.tipos_familia(doc_familia)
    if not casos:
        if not tipos:
            raise EscrituraRechazada(u"The family has no types and no flex_cases were given", 400)
        return [{"index": i, "name": _t(t.Name), "type_name": _t(t.Name), "tipo": t, "changes": []} for i, t in enumerate(tipos)]
    if not isinstance(casos, (list, tuple)):
        raise EscrituraRechazada(u"flex_cases must be a list of {name, type, values}", 400)
    if len(casos) > MAX_CASOS:
        raise EscrituraRechazada(u"At most {} flex_cases per call".format(MAX_CASOS), 400)
    planes = []
    for i, caso in enumerate(casos):
        if not isinstance(caso, dict):
            raise EscrituraRechazada(u"flex_cases[{}]: must be an object".format(i), 400, {"index": i})
        nombre_tipo = _t(caso.get("type") or caso.get("type_name")).strip() or None
        tipo = None
        if nombre_tipo:
            tipo = _tipo_por_nombre(doc_familia, nombre_tipo)
            if tipo is None and nombre_tipo not in tipos_extra:
                raise EscrituraRechazada(u"flex_cases[{}]: type '{}' not found".format(i, nombre_tipo), 404,
                                         {"index": i, "available_types": [_t(t.Name) for t in tipos]})
        valores = caso.get("values") or {}
        if not isinstance(valores, dict):
            raise EscrituraRechazada(u"flex_cases[{}]: values must be an object".format(i), 400, {"index": i})
        cambios = []
        for nombre_param, valor in valores.items():
            param = F.parametro_familia(doc_familia, nombre_param)
            if param is None:
                raise EscrituraRechazada(u"flex_cases[{}]: parameter '{}' not found".format(i, nombre_param), 404,
                                         {"index": i, "available_parameters": [_t(p.Definition.Name) for p in F._parametros_familia(doc_familia)]})
            try:
                interno, visible = E._valor_para_tipo(doc_familia, param, valor)
            except EscrituraRechazada as error:
                E._rechazo(error, "flex_cases", i)
            cambios.append({"parameter": nombre_param, "param": param, "value": valor, "internal": interno, "display": visible})
        planes.append({"index": i, "name": _t(caso.get("name")).strip() or (nombre_tipo or u"caso {}".format(i)),
                       "type_name": nombre_tipo, "tipo": tipo, "changes": cambios})
    return planes


def _volumenes(doc_familia):
    """[{id, kind, volume_m3}] de cada GenericForm del documento."""
    lista = []
    for forma in F.formas(doc_familia):
        lista.append({"id": get_element_id_value(forma), "kind": F._kind_forma(forma), "volume_m3": F.volumen_m3(forma)})
    return lista


def _grupo_transaccion(doc_familia, nombre):
    grupo = DB.TransactionGroup(doc_familia, u"IA: " + _t(nombre)[:60])
    grupo.Start()
    return grupo


def _cerrar_grupo(grupo, restaurar):
    try:
        if restaurar:
            grupo.RollBack()
        else:
            grupo.Assimilate()
    except Exception as error:
        logger.warning(u"No se pudo cerrar el TransactionGroup: %s", error)


def ejecutar_caso(doc_familia, caso, restaurar=True):
    """Un caso de flexion en su TransactionGroup. Devuelve el dict del caso con ok/motivo/solids."""
    resultado = {"index": caso["index"], "name": caso["name"], "type": caso["type_name"],
                 "values": dict((c["parameter"], c["display"]) for c in caso["changes"]), "ok": False, "motivo": None,
                 "solids": [], "empty_solids": [], "revit_errors": [], "restored": restaurar}
    grupo = _grupo_transaccion(doc_familia, u"Validar {}".format(caso["name"]))
    try:
        gestor = doc_familia.FamilyManager
        try:
            with transaccion(doc_familia, u"Flexionar {}".format(caso["name"])):
                if caso["tipo"] is not None:
                    gestor.CurrentType = caso["tipo"]
                elif caso["type_name"]:
                    tipo = _tipo_por_nombre(doc_familia, caso["type_name"])
                    if tipo is None:
                        raise EscrituraRechazada(u"type '{}' not found".format(caso["type_name"]), 404)
                    gestor.CurrentType = tipo
                for cambio in caso["changes"]:
                    gestor.Set(cambio["param"], cambio["internal"])
                doc_familia.Regenerate()
                resultado["solids"] = _volumenes(doc_familia)
        except TransaccionRevertida as error:
            resultado["motivo"] = u"Revit revirtio la transaccion: {}".format(error)
            resultado["revit_errors"] = list(getattr(_utils, "ULTIMOS_ERRORES", []) or [])
            return resultado
        except EscrituraRechazada as error:
            resultado["motivo"] = error.mensaje
            return resultado
        except Exception as error:
            resultado["motivo"] = u"Regenerate / Set failed: {}".format(error)
            resultado["revit_errors"] = list(getattr(_utils, "ULTIMOS_ERRORES", []) or [])
            return resultado
        vacios = [s for s in resultado["solids"] if not s["volume_m3"] or s["volume_m3"] <= 0]
        resultado["empty_solids"] = [s["id"] for s in vacios]
        if vacios:
            resultado["motivo"] = u"{} solid(s) have no volume after regeneration: {}".format(len(vacios), resultado["empty_solids"])
        elif not resultado["solids"]:
            resultado["motivo"] = u"the family has no solids"
        else:
            resultado["ok"] = True
        return resultado
    finally:
        _cerrar_grupo(grupo, restaurar)


def validar_familia(doc_familia, data, casos=None):
    restaurar = F._es_verdadero(data.get("restore"), True)
    casos = casos if casos is not None else planificar_casos(doc_familia, data)
    resultados = [ejecutar_caso(doc_familia, caso, restaurar) for caso in casos]
    fallidos = [r for r in resultados if not r["ok"]]
    return {"cases": resultados, "count": len(resultados), "passed": len(resultados) - len(fallidos), "failed": len(fallidos),
            "failed_cases": [{"name": r["name"], "type": r["type"], "motivo": r["motivo"], "revit_errors": r["revit_errors"]}
                             for r in fallidos],
            "restored": restaurar, "ok": not fallidos,
            "verificacion": {"coincide": not fallidos, "detalle": None if not fallidos else
                             u"{} of {} flex case(s) failed".format(len(fallidos), len(resultados))},
            "metodo": u"TransactionGroup por caso: CurrentType + Set + Regenerate; un solido con volumen 0, un fallo de "
                      u"severidad Error (ULTIMOS_ERRORES) o una excepcion en Regenerate marcan el caso como fallido"}


# ---------------------------------------------------------------------------
# Validacion del spec (sin abrir nada)
# ---------------------------------------------------------------------------
def _error_spec(mensaje, extra=None, status=400):
    datos = {"spec_error": True}
    datos.update(extra or {})
    raise EscrituraRechazada(u"spec: " + mensaje, status, datos)


def _nombres(lista, clave, seccion):
    nombres = []
    for i, item in enumerate(lista):
        if not isinstance(item, dict):
            _error_spec(u"{}[{}] must be an object".format(seccion, i), {"section": seccion, "index": i})
        nombre = _t(item.get(clave)).strip()
        if not nombre:
            _error_spec(u"{}[{}].{} is required".format(seccion, i, clave), {"section": seccion, "index": i})
        if nombre in nombres:
            _error_spec(u"{}: '{}' is repeated".format(seccion, nombre), {"section": seccion, "index": i})
        nombres.append(nombre)
    return nombres


def _lista_spec(spec, clave):
    valor = spec.get(clave) or []
    if not isinstance(valor, (list, tuple)):
        _error_spec(u"{} must be a list".format(clave), {"section": clave})
    return list(valor)


def validar_spec(doc, spec):
    """Comprueba el spec completo. Devuelve el resumen del plan (recuentos y pasos) sin tocar Revit."""
    if not isinstance(spec, dict):
        _error_spec(u"spec must be an object")
    nombre = _t(spec.get("name")).strip()
    if not nombre:
        _error_spec(u"name is required", {"section": "name"})
    if not spec.get("template"):
        _error_spec(u"template is required (.rft file name in FamilyTemplatePath, or a path)", {"section": "template"})
    try:
        plantilla = F.resolver_plantilla(doc, spec.get("template"))
    except EscrituraRechazada as error:
        _error_spec(error.mensaje, dict(error.extra, section="template"), error.status)
    if spec.get("category"):
        bic = getattr(DB.BuiltInCategory, _t(spec.get("category")).strip(), None)
        if bic is None:
            _error_spec(u"category '{}' is not a BuiltInCategory".format(spec.get("category")), {"section": "category"})

    parametros = _lista_spec(spec, "parameters")
    nombres_param = _nombres(parametros, "name", "parameters")
    conocidos = set()
    for i, item in enumerate(parametros):
        if not item.get("shared_parameter_guid"):
            try:
                clave, categoria = F.normalizar_tipo_dato(item.get("data_type"))
            except EscrituraRechazada as error:
                _error_spec(u"parameters[{}] ('{}'): {}".format(i, nombres_param[i], error.mensaje),
                            dict(error.extra, section="parameters", index=i))
            if clave == "family_type" and getattr(DB.BuiltInCategory, categoria, None) is None:
                _error_spec(u"parameters[{}] ('{}'): '{}' is not a BuiltInCategory".format(i, nombres_param[i], categoria),
                            {"section": "parameters", "index": i})
        try:
            F.resolver_grupo(item.get("group"))
        except EscrituraRechazada as error:
            _error_spec(u"parameters[{}] ('{}'): {}".format(i, nombres_param[i], error.mensaje),
                        dict(error.extra, section="parameters", index=i))
        formula = _t(item.get("formula")).strip()
        if formula:
            desconocidos = [n for n in E._nombres_formula(formula) if n not in conocidos and n != nombres_param[i]
                            and n not in nombres_param]
            if desconocidos:
                _error_spec(u"parameters[{}] ('{}'): formula uses undefined parameters: {}".format(
                    i, nombres_param[i], u", ".join(desconocidos)), {"section": "parameters", "index": i, "undefined": desconocidos})
        conocidos.add(nombres_param[i])

    planos = _lista_spec(spec, "reference_planes")
    nombres_planos = _nombres(planos, "name", "reference_planes")
    for i, item in enumerate(planos):
        direccion = F._normalizar(item.get("direction") or "x")
        if F._ALIAS_DIRECCION.get(direccion, direccion) not in F.DIRECCIONES_PLANO:
            _error_spec(u"reference_planes[{}] ('{}'): direction must be x, y or horizontal".format(i, nombres_planos[i]),
                        {"section": "reference_planes", "index": i})
        if item.get("origin_mm") is not None and not isinstance(item.get("origin_mm"), dict):
            _error_spec(u"reference_planes[{}] ('{}'): origin_mm must be {{x, y, z}}".format(i, nombres_planos[i]),
                        {"section": "reference_planes", "index": i})
        try:
            E._es_referencia(item.get("is_reference"))
        except EscrituraRechazada as error:
            _error_spec(u"reference_planes[{}] ('{}'): {}".format(i, nombres_planos[i], error.mensaje),
                        dict(error.extra, section="reference_planes", index=i))
    # los planos de la plantilla no se conocen sin abrirla: solo se admiten los del spec
    planos_validos = set(nombres_planos)

    def _plano_existe(nombre_plano, donde):
        if _t(nombre_plano).strip() not in planos_validos:
            _error_spec(u"{}: reference plane '{}' is not defined in reference_planes".format(donde, nombre_plano),
                        {"section": donde.split("[")[0], "available_reference_planes": sorted(planos_validos)})

    cotas = _lista_spec(spec, "dimensions")
    for i, item in enumerate(cotas):
        if not isinstance(item, dict):
            _error_spec(u"dimensions[{}] must be an object".format(i), {"section": "dimensions", "index": i})
        refs = item.get("reference_planes") or item.get("reference_plane_names") or []
        if isinstance(refs, _cadena):
            refs = [refs]
        if len(refs) < 2:
            _error_spec(u"dimensions[{}]: reference_planes needs at least two names".format(i), {"section": "dimensions", "index": i})
        for ref in refs:
            _plano_existe(ref, u"dimensions[{}]".format(i))
        etiqueta = _t(item.get("parameter") or item.get("parameter_name")).strip()
        igual = F._es_verdadero(item.get("equal"))
        if etiqueta and etiqueta not in nombres_param:
            _error_spec(u"dimensions[{}]: parameter '{}' is not defined in parameters".format(i, etiqueta),
                        {"section": "dimensions", "index": i, "available_parameters": nombres_param})
        if not etiqueta and not igual:
            _error_spec(u"dimensions[{}]: parameter or equal=true is required".format(i), {"section": "dimensions", "index": i})
        if igual and len(refs) < 3:
            _error_spec(u"dimensions[{}]: equal=true needs at least three reference planes".format(i), {"section": "dimensions", "index": i})

    solidos = _lista_spec(spec, "solids")
    nombres_solidos = []
    for i, item in enumerate(solidos):
        if not isinstance(item, dict):
            _error_spec(u"solids[{}] must be an object".format(i), {"section": "solids", "index": i})
        nombre_solido = _t(item.get("name")).strip() or u"{} {}".format(item.get("kind") or "extrusion", i)
        nombres_solidos.append(nombre_solido)
        kind = F._normalizar(item.get("kind") or "extrusion")
        kind = F._ALIAS_KIND.get(kind, kind)
        if kind not in F.KINDS_SOLIDO:
            _error_spec(u"solids[{}] ('{}'): kind must be extrusion, sweep, revolution or blend".format(i, nombre_solido),
                        {"section": "solids", "index": i})
        if kind == "extrusion" and item.get("end_mm") is None:
            _error_spec(u"solids[{}] ('{}'): end_mm is required".format(i, nombre_solido), {"section": "solids", "index": i})
        if kind in ("extrusion", "revolution", "sweep") and not item.get("profile"):
            _error_spec(u"solids[{}] ('{}'): profile is required".format(i, nombre_solido), {"section": "solids", "index": i})
        if kind == "blend" and not (item.get("base_profile") or item.get("profile")) or (kind == "blend" and not item.get("top_profile")):
            _error_spec(u"solids[{}] ('{}'): blend needs base_profile and top_profile".format(i, nombre_solido), {"section": "solids", "index": i})
        boceto = item.get("sketch_plane")
        if isinstance(boceto, _cadena):
            _plano_existe(boceto, u"solids[{}].sketch_plane".format(i))
        elif isinstance(boceto, dict) and boceto.get("reference_plane"):
            _plano_existe(boceto.get("reference_plane"), u"solids[{}].sketch_plane".format(i))
        material = _t(item.get("material_parameter")).strip()
        if material and material not in nombres_param:
            _error_spec(u"solids[{}] ('{}'): material_parameter '{}' is not declared in parameters".format(i, nombre_solido, material),
                        {"section": "solids", "index": i, "available_parameters": nombres_param})
        bloqueos = item.get("lock_ends_to")
        if bloqueos:
            if isinstance(bloqueos, (list, tuple)):
                bloqueos = {"start": bloqueos[0] if len(bloqueos) > 0 else None, "end": bloqueos[1] if len(bloqueos) > 1 else None}
            if not isinstance(bloqueos, dict):
                _error_spec(u"solids[{}] ('{}'): lock_ends_to must be {{start, end}} or [start, end]".format(i, nombre_solido),
                            {"section": "solids", "index": i})
            for extremo in ("start", "end"):
                if bloqueos.get(extremo):
                    _plano_existe(bloqueos[extremo], u"solids[{}].lock_ends_to.{}".format(i, extremo))
        for k, bloqueo in enumerate(item.get("lock_faces") or []):
            if not isinstance(bloqueo, dict) or not bloqueo.get("face") or not bloqueo.get("reference_plane"):
                _error_spec(u"solids[{}].lock_faces[{}] needs face and reference_plane".format(i, k), {"section": "solids", "index": i})
            _plano_existe(bloqueo["reference_plane"], u"solids[{}].lock_faces[{}]".format(i, k))
            try:
                F._vector(bloqueo["face"], "face")
            except EscrituraRechazada as error:
                _error_spec(u"solids[{}].lock_faces[{}]: {}".format(i, k, error.mensaje), {"section": "solids", "index": i})

    conectores = _lista_spec(spec, "connectors")
    for i, item in enumerate(conectores):
        if not isinstance(item, dict):
            _error_spec(u"connectors[{}] must be an object".format(i), {"section": "connectors", "index": i})
        dominio = F._normalizar(item.get("domain"))
        if F._ALIAS_DOMINIO.get(dominio, dominio) not in F.DOMINIOS:
            _error_spec(u"connectors[{}]: domain must be hvac, piping or electrical".format(i), {"section": "connectors", "index": i})
        if _t(item.get("solid")).strip() not in nombres_solidos:
            _error_spec(u"connectors[{}]: solid '{}' is not a solid of the spec".format(i, item.get("solid")),
                        {"section": "connectors", "index": i, "available_solids": nombres_solidos})

    tipos = _lista_spec(spec, "types")
    nombres_tipos = _nombres(tipos, "name", "types") if tipos else []
    for i, item in enumerate(tipos):
        valores = item.get("values") or {}
        if not isinstance(valores, dict):
            _error_spec(u"types[{}] ('{}'): values must be an object".format(i, nombres_tipos[i]), {"section": "types", "index": i})
        for nombre_param in valores:
            if nombre_param not in nombres_param:
                _error_spec(u"types[{}] ('{}'): unknown parameter '{}'".format(i, nombres_tipos[i], nombre_param),
                            {"section": "types", "index": i, "available_parameters": nombres_param})
    casos = _lista_spec(spec, "flex_cases")
    for i, caso in enumerate(casos):
        if not isinstance(caso, dict):
            _error_spec(u"flex_cases[{}] must be an object".format(i), {"section": "flex_cases", "index": i})
        tipo = _t(caso.get("type") or caso.get("type_name")).strip()
        if tipo and tipo not in nombres_tipos:
            _error_spec(u"flex_cases[{}]: type '{}' is not defined in types".format(i, tipo), {"section": "flex_cases", "index": i})
        for nombre_param in (caso.get("values") or {}):
            if nombre_param not in nombres_param:
                _error_spec(u"flex_cases[{}]: unknown parameter '{}'".format(i, nombre_param), {"section": "flex_cases", "index": i})

    pasos = ["open"] + (["category"] if spec.get("category") else [])
    for seccion in ("parameters", "reference_planes", "dimensions", "solids", "connectors", "types"):
        if _lista_spec(spec, seccion):
            pasos.append(seccion)
    if tipos or casos:
        pasos.append("validate")
    return {
        "name": nombre, "template": plantilla, "category": spec.get("category"),
        "counts": {"parameters": len(parametros), "reference_planes": len(planos), "dimensions": len(cotas),
                   "solids": len([s for s in solidos if not F._es_verdadero(s.get("is_void"))]),
                   "voids": len([s for s in solidos if F._es_verdadero(s.get("is_void"))]),
                   "connectors": len(conectores), "types": len(tipos), "flex_cases": len(casos) or len(tipos)},
        "parameters": nombres_param, "reference_planes": nombres_planos, "solids": nombres_solidos, "types": nombres_tipos,
        "steps": pasos, "total": len(parametros) + len(planos) + len(cotas) + len(solidos) + len(conectores) + len(tipos),
    }


# ---------------------------------------------------------------------------
# build_family_from_spec
# ---------------------------------------------------------------------------
def _cerrar_sin_guardar(doc_familia):
    try:
        titulo = titulo_documento(doc_familia)
        doc_familia.Close(False)
        F.DOCUMENTOS_ABIERTOS.pop(titulo, None)
        return True
    except Exception as error:
        logger.warning(u"No se pudo cerrar la familia tras el fallo: %s", error)
        return False


def construir_familia(doc, spec, plan, data):
    """Ejecuta los pasos del spec sobre un documento nuevo. Devuelve el dict de resultado."""
    guardar_en = _t(data.get("save_path")).strip() or None
    cargar = F._es_verdadero(data.get("load_into_project"))
    cerrar = F._es_verdadero(data.get("close"))
    sobrescribir = F._es_verdadero(data.get("overwrite"))
    if guardar_en:
        if not guardar_en.lower().endswith(F.EXTENSION_FAMILIA):
            guardar_en = os.path.join(guardar_en, plan["name"] + F.EXTENSION_FAMILIA) if os.path.isdir(guardar_en) else guardar_en + F.EXTENSION_FAMILIA
        if os.path.isfile(guardar_en) and not sobrescribir:
            raise EscrituraRechazada(u"save_path already exists: {} (use overwrite=true)".format(guardar_en), 409, {"file_path": guardar_en})
        if not os.path.isdir(os.path.dirname(guardar_en) or "."):
            raise EscrituraRechazada(u"save_path folder does not exist: {}".format(os.path.dirname(guardar_en)), 404)
    if cargar and not guardar_en:
        raise EscrituraRechazada(u"load_into_project=true needs save_path", 400)
    existente, _ = F._familia_proyecto(doc, plan["name"])
    if cargar and existente is not None and not F._es_verdadero(data.get("overwrite_parameters")):
        raise EscrituraRechazada(u"Family '{}' is already loaded in the project; use overwrite_parameters=true".format(plan["name"]), 409,
                                 {"already_loaded": True, "family_id": get_element_id_value(existente)})

    pasos = []
    resultado = {"name": plan["name"], "template": plan["template"], "steps": pasos, "counts": plan["counts"], "avisos": []}
    paso_actual = ["open"]

    def _paso(nombre, **datos):
        registro = {"step": nombre, "ok": True}
        registro.update(datos)
        pasos.append(registro)
        return registro

    doc_familia = F.abrir_documento(doc, {"modo": "new", "template": plan["template"], "name": plan["name"]})
    _paso("open", family_doc=titulo_documento(doc_familia), template=plan["template"])
    resultado["family_doc"] = titulo_documento(doc_familia)
    try:
        if spec.get("category"):
            paso_actual[0] = "category"
            categoria, bic = F._categoria_familia(doc_familia, spec.get("category"))
            with transaccion(doc_familia, u"Categoria de familia"):
                doc_familia.OwnerFamily.FamilyCategory = categoria
            _paso("category", category=spec.get("category"), categoria=F.categoria_familia(doc_familia)[0])
        parametros = _lista_spec(spec, "parameters")
        if parametros:
            paso_actual[0] = "parameters"
            planes = E.planificar_parametros(doc_familia, parametros)
            with transaccion(doc_familia, u"Parametros de familia ({})".format(len(planes))):
                creados = E.aplicar_parametros(doc_familia, planes)
            _paso("parameters", count=len(creados), names=[_t(p.Definition.Name) for p in creados])
        planos = _lista_spec(spec, "reference_planes")
        if planos:
            paso_actual[0] = "reference_planes"
            planes = E.planificar_planos(doc_familia, planos)
            with transaccion(doc_familia, u"Planos de referencia ({})".format(len(planes))):
                creados = E.aplicar_planos(doc_familia, planes)
            resultado["avisos"].extend(p["aviso"] for p in planes if p.get("aviso"))
            _paso("reference_planes", count=len(creados), names=[get_element_name(p) for p in creados])
        cotas = _lista_spec(spec, "dimensions")
        if cotas:
            paso_actual[0] = "dimensions"
            planes = E.planificar_cotas(doc_familia, cotas)
            with transaccion(doc_familia, u"Cotas con etiqueta ({})".format(len(planes))):
                creadas = E.aplicar_cotas(doc_familia, planes)
            _paso("dimensions", count=len(creadas), labels=[p["parameter"] for p in planes])
        solidos = _lista_spec(spec, "solids")
        formas = {}
        if solidos:
            paso_actual[0] = "solids"
            planes = E.planificar_solidos(doc_familia, solidos)
            avisos = []
            with transaccion(doc_familia, u"Solidos de familia ({})".format(len(planes))):
                creados = E.aplicar_solidos(doc_familia, planes, avisos)
            resultado["avisos"].extend(avisos)
            for p, forma in zip(planes, creados):
                formas[p["name"]] = forma
            _paso("solids", count=len(creados), solids=[dict(F.describir_forma(doc_familia, f), name=p["name"]) for f, p in zip(creados, planes)])
        conectores = _lista_spec(spec, "connectors")
        if conectores:
            paso_actual[0] = "connectors"
            lista = []
            for item in conectores:
                copia = dict(item)
                copia["solid_id"] = get_element_id_value(formas[_t(item.get("solid")).strip()])
                lista.append(copia)
            planes = E.planificar_conectores(doc_familia, lista)
            avisos = []
            with transaccion(doc_familia, u"Conectores de familia ({})".format(len(planes))):
                creados = E.aplicar_conectores(doc_familia, planes, avisos)
            resultado["avisos"].extend(avisos)
            _paso("connectors", count=len(creados))
        tipos = _lista_spec(spec, "types")
        if tipos:
            paso_actual[0] = "types"
            planes = E.planificar_tipos(doc_familia, [{"type_name": t.get("name"), "values": t.get("values") or {}} for t in tipos])
            with transaccion(doc_familia, u"Tipos de familia ({})".format(len(planes))):
                resultados = E.aplicar_tipos(doc_familia, planes)
            fallidos = [dict(f, type_name=p["type_name"]) for p, _, _, _, fallo in resultados for f in fallo]
            if fallidos:
                raise EscrituraRechazada(u"types: {} value(s) could not be set".format(len(fallidos)), 500, {"fallidos": fallidos})
            _paso("types", count=len(resultados), names=[p["type_name"] for p, _, _, _, _ in resultados])
        if tipos or spec.get("flex_cases"):
            paso_actual[0] = "validate"
            casos_spec = spec.get("flex_cases") or [{"name": t.get("name"), "type": t.get("name")} for t in tipos]
            casos = planificar_casos(doc_familia, {"flex_cases": casos_spec})
            validacion = validar_familia(doc_familia, {"restore": True}, casos)
            registro = _paso("validate", passed=validacion["passed"], failed=validacion["failed"], failed_cases=validacion["failed_cases"])
            resultado["validation"] = validacion
            if validacion["failed"]:
                registro["ok"] = False
                raise EscrituraRechazada(u"validate: {} flex case(s) failed".format(validacion["failed"]), 500,
                                         {"failed_cases": validacion["failed_cases"]})
        if guardar_en:
            paso_actual[0] = "save"
            titulo_anterior = titulo_documento(doc_familia)
            opciones = DB.SaveAsOptions()
            try:
                opciones.OverwriteExistingFile = sobrescribir
            except Exception:
                pass
            doc_familia.SaveAs(guardar_en, opciones)
            resultado["family_doc"] = F._actualizar_titulo(doc_familia, titulo_anterior)
            resultado["file_path"] = guardar_en
            _paso("save", file_path=guardar_en, size_kb=int(os.path.getsize(guardar_en) / 1024) if os.path.isfile(guardar_en) else None)
        if cargar:
            paso_actual[0] = "load"
            opciones = F._OpcionesCarga(F._es_verdadero(data.get("overwrite_parameters")))
            familia, avisos_carga = F.cargar_familia_en_proyecto(doc, doc_familia, opciones, plan["name"])
            resultado["avisos"].extend(avisos_carga)
            if familia is None:
                familia, _ = F._familia_proyecto(doc, plan["name"])
            simbolos = F._simbolos_de_familia(doc, familia) if familia is not None else []
            resultado["family_id"] = get_element_id_value(familia) if familia is not None else None
            resultado["loaded_types"] = [{"type_id": get_element_id_value(s), "type": get_element_name(s)} for s in simbolos]
            _paso("load", family_id=resultado["family_id"], types=[t["type"] for t in resultado["loaded_types"]])
        if cerrar:
            paso_actual[0] = "close"
            if _mismo_activo(doc, doc_familia):
                resultado["avisos"].append(u"the family document is the active document: not closed")
            else:
                _cerrar_sin_guardar(doc_familia) if not guardar_en else _cerrar(doc_familia)
                resultado["closed"] = True
                _paso("close")
    except EscrituraRechazada as error:
        cerrado = _cerrar_sin_guardar(doc_familia)
        raise EscrituraRechazada(u"build failed at step '{}': {}".format(paso_actual[0], error.mensaje), error.status,
                                 dict(error.extra, failed_step=paso_actual[0], steps=pasos, closed_without_saving=cerrado))
    except Exception as error:
        cerrado = _cerrar_sin_guardar(doc_familia)
        raise EscrituraRechazada(u"build failed at step '{}': {}".format(paso_actual[0], error), 500,
                                 {"failed_step": paso_actual[0], "steps": pasos, "closed_without_saving": cerrado})
    resultado["summary"] = F.resumen_familia(doc_familia, completo=True) if not resultado.get("closed") else None
    resultado["ok"] = all(p.get("ok", True) for p in pasos)
    resultado["verificacion"] = {"coincide": resultado["ok"]}
    resultado["message"] = u"Family '{}' built in {} step(s)".format(plan["name"], len(pasos))
    return resultado


def _mismo_activo(doc, doc_familia):
    return F._mismo_documento(doc_familia, doc)


def _cerrar(doc_familia):
    try:
        titulo = titulo_documento(doc_familia)
        doc_familia.Close(False)
        F.DOCUMENTOS_ABIERTOS.pop(titulo, None)
    except Exception as error:
        logger.warning(u"No se pudo cerrar la familia: %s", error)


def register_spec_routes(api):
    """Rutas macro del editor de familias (0.6.0)."""

    @api.route("/family/validate/", methods=["POST"])
    @requiere_token
    def family_validate(doc, request):
        """MACRO: flexiona la familia (un TransactionGroup por caso, RollBack si restore) y comprueba los solidos. Acepta `simular`."""

        def cuerpo(ctx):
            doc_familia = ctx["doc_familia"]
            casos = planificar_casos(doc_familia, ctx["data"])
            comprobar_alcance(ctx["data"], len(casos), "casos de flexion")
            haria = [{"accion": "flexionar", "index": c["index"], "name": c["name"], "type": c["type_name"],
                      "values": dict((x["parameter"], x["display"]) for x in c["changes"])} for c in casos]
            if ctx["simular"]:
                return simulacion(haria, count=len(haria), solids=_volumenes(doc_familia))
            return validar_familia(doc_familia, ctx["data"], casos)

        return ejecutar_familia(doc, "/family/validate/", request, cuerpo, resolver=lambda data: F.resolver_documento(doc, data))

    @api.route("/family/build/", methods=["POST"])
    @requiere_token
    def build_family_from_spec(doc, request):
        """MACRO: valida el spec entero y construye la familia paso a paso (nueva -> parametros -> planos -> cotas ->
        solidos -> conectores -> tipos -> validar -> guardar -> cargar). Acepta `simular` (devuelve el plan)."""

        def cuerpo(ctx):
            data = ctx["data"]
            spec = data.get("spec")
            if not isinstance(spec, dict):
                raise EscrituraRechazada(u"spec is required (object; see CONTRATO.md, build_family_from_spec)", 400)
            plan = validar_spec(doc, spec)
            comprobar_alcance(data, plan["total"], "elementos de la familia")
            haria = [{"accion": "construir_familia", "name": plan["name"], "template": plan["template"], "steps": plan["steps"],
                      "save_path": data.get("save_path"), "load_into_project": F._es_verdadero(data.get("load_into_project"))}]
            if ctx["simular"]:
                return simulacion(haria, plan=plan, count=plan["total"])
            return construir_familia(doc, spec, plan, data)

        return ejecutar_familia(doc, "/family/build/", request, cuerpo)
