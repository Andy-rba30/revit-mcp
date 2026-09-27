# -*- coding: UTF-8 -*-
"""
Lotes en una sola transaccion para Revit MCP (0.4.0):

  POST /set_parameters/   changes[] de {element_ids[] | element_id, parameters: {nombre: valor},
                          type_parameters} o {element_id, parameter_name, value} (compatibilidad),
                          type_parameters (global), simular, forzar
                          -> UNA transaccion "IA: Parametros (<n> elementos)"
  POST /create_elements/  elements[] de {kind, ...argumentos del creador}, simular, forzar
                          -> UNA transaccion "IA: Crear <n> elementos"

Motivo: en la validacion de 0.3.1 Revit respondia en menos de un segundo por
llamada y aun asi cambiar 20 comentarios eran 20 idas y vueltas (y 20
entradas de deshacer). Aqui el agente manda el lote entero.

Ambas rutas siguen el patron de escritura de la fase 1 (ejecutar, simular,
comprobar_alcance, EscrituraRechazada, transaccion, resultado_creacion /
antes-despues) y reutilizan los helpers `planificar_*` / `crear_*` que 0.4.0
extrajo de building.py, structure.py, estructural.py, topografia.py, rooms.py,
detail.py, mep.py y placement.py. Las rutas antiguas (set_parameter,
create_line, create_column...) siguen existiendo.

/set_parameters/: resuelve cada parametro con utils.buscar_por_nombre (nombre
visible, alias ingles o BuiltInParameter) una sola vez por elemento (o por
tipo, si type_parameters), convierte los Double con parameters.convertir_valor
(mm, mm2, mm3, grados), NO aborta el lote por un parametro de solo lectura o
inexistente (lo informa en `fallidos`) y devuelve antes/despues por elemento y
parametro con `verificacion.coincide` global.

/create_elements/: valida TODOS los elementos antes de abrir la transaccion
(400 con `index` y `kind` del primero que falla); si uno falla dentro de la
transaccion, Revit revierte todo y la respuesta (500) dice cual (`index`,
`kind`, `revit_error`). `creados` viene agrupado por `kind`.
"""

from utils import (
    get_element_name, get_element_id_value, make_element_id, buscar_por_nombre, mapa_niveles,
)
from seguridad import requiere_token
from escritura import (
    ejecutar, transaccion, simulacion, EscrituraRechazada, resultado_creacion, comprobar_alcance,
    describir_elemento, verificar_creados,
)
from parameters import (
    valor_parametro, convertir_valor, coincide_valor, despues_simulado, nombres_parametros, _safe_str,
)
import building
import structure
import estructural
import topografia
import rooms
import detail
import mep
import placement
from pyrevit import routes, revit, DB
import logging

logger = logging.getLogger(__name__)

try:
    _texto = unicode  # IronPython 2.7
    _cadena = basestring
except NameError:  # pragma: no cover - CPython 3 en las pruebas
    _texto = str
    _cadena = str

KINDS = (
    "wall", "floor", "roof", "ceiling", "level", "grid", "column", "beam", "foundation", "opening",
    "toposolid", "room", "room_separation", "detail_line", "duct", "pipe", "family_instance",
)
_ALIAS_KIND = {
    "muro": "wall", "walls": "wall", "suelo": "floor", "floors": "floor", "cubierta": "roof", "roofs": "roof",
    "techo": "ceiling", "ceilings": "ceiling", "nivel": "level", "levels": "level", "rejilla": "grid",
    "grids": "grid", "pilar": "column", "structural_column": "column", "columns": "column",
    "viga": "beam", "beams": "beam", "framing": "beam", "structural_framing": "beam",
    "cimentacion": "foundation", "zapata": "foundation", "foundations": "foundation",
    "hueco": "opening", "openings": "opening", "toposolido": "toposolid", "habitacion": "room", "rooms": "room",
    "separacion": "room_separation", "linea_detalle": "detail_line", "conducto": "duct", "ducts": "duct",
    "tuberia": "pipe", "pipes": "pipe", "familia": "family_instance", "family": "family_instance",
    "instance": "family_instance", "place_family": "family_instance",
}


# ---------------------------------------------------------------------------
# /set_parameters/
# ---------------------------------------------------------------------------
def _es_verdadero(valor):
    if isinstance(valor, _cadena):
        return valor.strip().lower() in ("1", "true", "si", "sí", "yes")
    return bool(valor)


def normalizar_cambios(data):
    """changes[] -> lista de (element_id, parameter_name, value, type_parameters). Lanza EscrituraRechazada."""
    cambios = data.get("changes")
    if cambios is None:
        # compatibilidad con set_parameter / modify_element en el mismo cuerpo
        if data.get("element_id") is not None and (data.get("parameter_name") or data.get("parameters")):
            cambios = [data]
        else:
            raise EscrituraRechazada(
                "changes is required: [{\"element_ids\": [...], \"parameters\": {name: value}}]", 400
            )
    if not isinstance(cambios, (list, tuple)) or not cambios:
        raise EscrituraRechazada("changes must be a non-empty list", 400)
    global_tipo = _es_verdadero(data.get("type_parameters", False))
    operaciones = []
    for indice, cambio in enumerate(cambios):
        etiqueta = "changes[{}]".format(indice)
        if not isinstance(cambio, dict):
            raise EscrituraRechazada("{} must be an object".format(etiqueta), 400)
        de_tipo = _es_verdadero(cambio.get("type_parameters", global_tipo))
        ids = cambio.get("element_ids")
        if ids is None:
            ids = [cambio["element_id"]] if cambio.get("element_id") is not None else []
        if isinstance(ids, (int, float)) or isinstance(ids, _cadena):
            ids = [ids]
        if not ids:
            raise EscrituraRechazada("{}: element_ids (or element_id) is required".format(etiqueta), 400)
        parametros = cambio.get("parameters")
        if parametros is None:
            nombre = cambio.get("parameter_name")
            if not nombre:
                raise EscrituraRechazada(
                    "{}: parameters {{name: value}} (or parameter_name + value) is required".format(etiqueta), 400
                )
            if "value" not in cambio or cambio.get("value") is None:
                raise EscrituraRechazada("{}: value is required".format(etiqueta), 400)
            parametros = {nombre: cambio.get("value")}
        if not isinstance(parametros, dict) or not parametros:
            raise EscrituraRechazada("{}: parameters must be a non-empty object {{name: value}}".format(etiqueta), 400)
        for identificador in ids:
            try:
                entero = int(identificador)
            except (TypeError, ValueError):
                raise EscrituraRechazada("{}: element id {!r} is not an integer".format(etiqueta, identificador), 400)
            for nombre, valor in parametros.items():
                if valor is None:
                    raise EscrituraRechazada("{}: value of '{}' is required".format(etiqueta, nombre), 400)
                operaciones.append((entero, nombre, valor, de_tipo))
    return operaciones


def _tipo_de(doc, elem):
    try:
        tipo_id = elem.GetTypeId()
        if tipo_id and tipo_id != DB.ElementId.InvalidElementId:
            return doc.GetElement(tipo_id)
    except Exception:
        pass
    return None


def resolver_parametros(doc, operaciones):
    """Resuelve cada (elemento, parametro) una sola vez. Devuelve (planes, fallidos).

    Un plan: {element_id, target_id, parameter_name, parameter_label, param, value,
    convertido, is_type_parameter, antes}. Los fallos (elemento inexistente,
    parametro inexistente, de solo lectura o valor invalido) van a `fallidos`
    con su motivo y no detienen el lote."""
    planes = []
    fallidos = []
    elementos = {}
    resueltos = {}       # (target_id, nombre) -> param o None
    disponibles = {}     # target_id -> nombres de parametro (para available_parameters)
    vistos = set()       # (target_id, nombre): un parametro de tipo compartido se fija una vez
    for element_id, nombre, valor, de_tipo in operaciones:
        if element_id not in elementos:
            try:
                elementos[element_id] = doc.GetElement(make_element_id(element_id))
            except Exception:
                elementos[element_id] = None
        elem = elementos[element_id]
        if elem is None:
            fallidos.append({"element_id": element_id, "parameter_name": nombre, "motivo": "element not found"})
            continue
        objetivo = elem
        es_de_tipo = False
        if de_tipo:
            objetivo = _tipo_de(doc, elem)
            es_de_tipo = True
            if objetivo is None:
                fallidos.append({"element_id": element_id, "parameter_name": nombre, "motivo": "element has no type"})
                continue
        target_id = get_element_id_value(objetivo)
        clave = (target_id, nombre)
        if clave not in resueltos:
            param = buscar_por_nombre(objetivo, nombre)
            if param is None and not de_tipo:
                tipo = _tipo_de(doc, elem)
                if tipo is not None:
                    param = buscar_por_nombre(tipo, nombre)
                    if param is not None:
                        # como set_parameter: parametro de tipo alcanzado desde el ejemplar
                        resueltos[clave] = (param, True, get_element_id_value(tipo))
                        param = None
            if clave not in resueltos:
                resueltos[clave] = (param, es_de_tipo, target_id)
        param, es_de_tipo, target_id = resueltos[clave]
        if param is None:
            if target_id not in disponibles:
                disponibles[target_id] = nombres_parametros(objetivo)
            fallidos.append({"element_id": element_id, "parameter_name": nombre, "motivo": "parameter not found",
                             "available_parameters": disponibles[target_id]})
            continue
        if param.IsReadOnly:
            fallidos.append({"element_id": element_id, "parameter_name": nombre, "motivo": "read-only"})
            continue
        try:
            convertido = convertir_valor(param, valor)
        except ValueError as error:
            fallidos.append({"element_id": element_id, "parameter_name": nombre, "motivo": str(error)})
            continue
        try:
            etiqueta = _safe_str(param.Definition.Name)
        except Exception:
            etiqueta = nombre
        plan = {
            "element_id": element_id, "target_id": target_id, "parameter_name": nombre,
            "parameter_label": etiqueta, "param": param, "value": valor, "convertido": convertido,
            "is_type_parameter": es_de_tipo, "antes": valor_parametro(param, doc),
            "aplicar": (target_id, nombre) not in vistos,
        }
        vistos.add((target_id, nombre))
        planes.append(plan)
    return planes, fallidos


def _agrupar(planes, clave_valor):
    """{element_id: {parameter_label: valor}} a partir de los planes."""
    agrupado = {}
    for plan in planes:
        por_elemento = agrupado.setdefault(_texto(plan["element_id"]), {})
        por_elemento[plan["parameter_label"]] = plan[clave_valor]
    return agrupado


# ---------------------------------------------------------------------------
# /create_elements/
# ---------------------------------------------------------------------------
class _Cache(object):
    """Mapas de tipos/niveles leidos una vez por lote (solo cuando un kind los necesita)."""

    def __init__(self, doc):
        self.doc = doc
        self._mapas = {}

    def get(self, clave, fabrica):
        if clave not in self._mapas:
            self._mapas[clave] = fabrica(self.doc)
        return self._mapas[clave]


def _kind_de(elem, indice):
    if not isinstance(elem, dict):
        raise EscrituraRechazada("elements[{}] must be an object with kind".format(indice), 400,
                                 {"index": indice})
    kind = elem.get("kind") or elem.get("element_type")
    if not kind:
        raise EscrituraRechazada("elements[{}]: kind is required ({})".format(indice, ", ".join(KINDS)), 400,
                                 {"index": indice, "available_kinds": list(KINDS)})
    kind = _texto(kind).strip().lower()
    kind = _ALIAS_KIND.get(kind, kind)
    if kind not in KINDS:
        raise EscrituraRechazada("elements[{}]: kind '{}' not supported ({})".format(indice, kind, ", ".join(KINDS)),
                                 400, {"index": indice, "kind": kind, "available_kinds": list(KINDS)})
    return kind


def planificar_elemento(doc, elem, indice, cache):
    """Plan de un elemento del lote segun su kind. Lanza ValueError o EscrituraRechazada."""
    kind = _kind_de(elem, indice)
    if kind in ("wall", "beam") and not elem.get("element_type"):
        elem = dict(elem, element_type=kind)
    if kind in ("floor", "roof", "ceiling") and not elem.get("element_type"):
        elem = dict(elem, element_type=kind)
    if kind == "wall":
        plan = building.planificar_lineal(elem, indice, cache.get("lineales", building.mapas_lineales))
        plan["haria"] = building.haria_lineal(plan)
    elif kind == "beam":
        # vigas: el creador de structure.py (z = desfase desde el nivel)
        plan = structure.planificar_viga(elem, indice, cache.get("vigas", structure.mapas_vigas))
        plan["haria"] = structure.haria_viga(plan)
    elif kind in ("floor", "roof", "ceiling"):
        plan = building.planificar_superficie(elem, indice, cache.get("superficies", building.mapas_superficies))
        plan["haria"] = building.haria_superficie(plan)
    elif kind == "level":
        existentes = cache.get("niveles", mapa_niveles)
        plan = building.planificar_nivel(elem, indice, existentes)
        if plan["name"]:
            # un nombre repetido dentro del mismo lote tambien se rechaza
            pedidos = cache.get("niveles_pedidos", lambda d: set())
            if plan["name"] in pedidos:
                raise ValueError("Level {}: name '{}' repeated in the request".format(indice, plan["name"]))
            pedidos.add(plan["name"])
        plan["haria"] = building.haria_nivel(plan)
    elif kind == "grid":
        existentes = cache.get("rejillas", structure.rejillas_existentes)
        plan = structure.planificar_rejilla(elem, indice, existentes)
        if plan["name"]:
            pedidos = cache.get("rejillas_pedidas", lambda d: set())
            if plan["name"] in pedidos:
                raise ValueError("Grid {}: name '{}' repeated in the request".format(indice, plan["name"]))
            pedidos.add(plan["name"])
        plan["haria"] = structure.haria_rejilla(plan)
    elif kind == "column":
        plan = estructural.planificar_pilar(elem, indice, cache.get("pilares", estructural.mapas_pilares))
        plan["haria"] = estructural.haria_pilar(plan)
    elif kind == "foundation":
        plan = estructural.planificar_cimentacion(doc, elem, indice, cache.get("cimentaciones", estructural.mapas_cimentaciones))
        plan["haria"] = estructural.haria_cimentacion(plan)
    elif kind == "opening":
        plan = estructural.planificar_hueco(doc, elem)
    elif kind == "toposolid":
        plan = topografia.planificar_toposolido(doc, elem)
    elif kind == "room":
        plan = rooms.planificar_habitacion(doc, elem)
    elif kind == "room_separation":
        plan = rooms.planificar_separacion(doc, elem)
        plan["haria"] = {"accion": "crear", "element_type": "room_separation", "lines": len(plan["curvas"]),
                         "view": plan["haria"][0]["view"] if plan["haria"] else None}
    elif kind == "detail_line":
        plan = detail.planificar_linea_detalle(doc, elem)
    elif kind == "duct":
        plan = mep.planificar_conducto(doc, elem)
    elif kind == "pipe":
        plan = mep.planificar_tuberia(doc, elem)
    else:  # family_instance
        plan = placement.planificar_colocacion(doc, elem)
    plan["kind"] = kind
    plan["index"] = indice
    return plan


def crear_elemento(doc, plan):
    """Crea el elemento del plan (dentro de la transaccion). Devuelve la lista de ids creados."""
    kind = plan["kind"]
    if kind == "wall":
        return [get_element_id_value(building.crear_lineal(doc, plan))]
    if kind == "beam":
        return [get_element_id_value(structure.crear_viga(doc, plan))]
    if kind in ("floor", "roof", "ceiling"):
        return [get_element_id_value(building.crear_superficie(doc, plan))]
    if kind == "level":
        return [get_element_id_value(building.crear_nivel(doc, plan["elevation_mm"], plan["name"]))]
    if kind == "grid":
        grid, error_nombre = structure.crear_rejilla(doc, plan["start"], plan["end"], plan["name"])
        if error_nombre:
            raise RuntimeError("could not set grid name '{}': {}".format(plan["name"], error_nombre))
        return [get_element_id_value(grid)]
    if kind == "column":
        return [get_element_id_value(estructural.crear_pilar(doc, plan))]
    if kind == "foundation":
        return [get_element_id_value(estructural.crear_cimentacion(doc, plan))]
    if kind == "opening":
        return [get_element_id_value(estructural.crear_hueco(doc, plan))]
    if kind == "toposolid":
        return [get_element_id_value(topografia.crear_toposolido(doc, plan["puntos"], plan["tipo"], plan["nivel"], plan["contorno"]))]
    if kind == "room":
        return [get_element_id_value(rooms.crear_habitacion(doc, plan))]
    if kind == "room_separation":
        return list(rooms.crear_separacion(doc, plan))
    if kind == "detail_line":
        return [get_element_id_value(detail.crear_linea_detalle(doc, plan))]
    if kind == "duct":
        return [get_element_id_value(mep.crear_conducto(doc, plan))]
    if kind == "pipe":
        return [get_element_id_value(mep.crear_tuberia(doc, plan))]
    instancia, fijadas, fallidas = placement.colocar_familia(doc, plan)
    plan["properties_set"] = fijadas
    plan["properties_failed"] = fallidas
    return [get_element_id_value(instancia)]


def _completar_creado(doc, plan, creado):
    """Datos extra por kind (nombre del nivel/rejilla, nivel superior del pilar...)."""
    kind = plan["kind"]
    creado["kind"] = kind
    creado["index"] = plan["index"]
    try:
        if kind == "level":
            building.describir_nivel(doc, creado)
        elif kind == "grid":
            creado["name"] = get_element_name(doc.GetElement(make_element_id(creado["id"])))
        elif kind == "column":
            estructural.describir_pilar(doc, creado)
        elif kind == "family_instance":
            creado["properties_set"] = plan.get("properties_set", [])
            creado["properties_failed"] = plan.get("properties_failed", [])
        elif kind == "room":
            creado.update(rooms.describir_habitacion(doc.GetElement(make_element_id(creado["id"]))))
    except Exception:
        pass
    return creado


def _contar(planes):
    recuento = {}
    for plan in planes:
        recuento[plan["kind"]] = recuento.get(plan["kind"], 0) + 1
    return recuento


class _FalloEnLote(Exception):
    def __init__(self, indice, kind, error):
        Exception.__init__(self, error)
        self.indice = indice
        self.kind = kind
        self.error = error


# ---------------------------------------------------------------------------
# Rutas
# ---------------------------------------------------------------------------
def register_lotes_routes(api):
    """Register the batch routes (/set_parameters/, /create_elements/)."""

    @api.route("/set_parameters/", methods=["POST"])
    @requiere_token
    def set_parameters(doc, request):
        """Varios parametros de varios elementos en UNA transaccion. Acepta `simular` y `forzar`."""

        def cuerpo(ctx):
            data = ctx["data"]
            operaciones = normalizar_cambios(data)
            comprobar_alcance(data, len(operaciones), "parametros (elementos x parametros)")
            planes, fallidos = resolver_parametros(doc, operaciones)
            if not planes:
                raise EscrituraRechazada(
                    "No parameter can be set: every element x parameter pair failed", 400,
                    {"fallidos": fallidos},
                )
            elementos = sorted(set(p["element_id"] for p in planes))

            if ctx["simular"]:
                haria = [{
                    "accion": "set_parameter",
                    "element_id": p["element_id"],
                    "parameter_name": p["parameter_name"],
                    "parameter_label": p["parameter_label"],
                    "is_type_parameter": p["is_type_parameter"],
                    "antes": p["antes"],
                    "despues": despues_simulado(p["param"], p["value"], p["convertido"]),
                } for p in planes]
                return simulacion(haria, count=len(haria), elements=len(elementos), fallidos=fallidos)

            with transaccion(doc, "Parametros ({} elementos)".format(len(elementos))):
                for plan in planes:
                    if not plan["aplicar"]:
                        continue
                    plan["aceptado"] = plan["param"].Set(plan["convertido"])

            cambios = []
            desajustes = []
            for plan in planes:
                despues = valor_parametro(plan["param"], doc)
                plan["despues"] = despues
                coincide = coincide_valor(plan["param"], plan["value"])
                cambios.append({
                    "element_id": plan["element_id"],
                    "parameter_name": plan["parameter_name"],
                    "parameter_label": plan["parameter_label"],
                    "is_type_parameter": plan["is_type_parameter"],
                    "type_id": plan["target_id"] if plan["is_type_parameter"] else None,
                    "antes": plan["antes"],
                    "despues": despues,
                    "coincide": bool(coincide),
                })
                if not coincide:
                    desajustes.append("element {} '{}': requested {!r}, read back {!r}".format(
                        plan["element_id"], plan["parameter_label"], plan["value"], despues))
            resultado = {
                "count": len(elementos),
                "elements": elementos,
                "parameters_set": len(cambios),
                "changes": cambios,
                "antes": _agrupar(planes, "antes"),
                "despues": _agrupar(planes, "despues"),
                "fallidos": fallidos,
                "ok": not desajustes,
                "message": "Set {} parameter(s) on {} element(s), {} failed".format(
                    len(cambios), len(elementos), len(fallidos)),
            }
            if desajustes:
                resultado["verificacion"] = {
                    "coincide": False,
                    "detalle": "Values read back after commit differ from the request: " + "; ".join(desajustes),
                }
            else:
                resultado["verificacion"] = {"coincide": True}
            return resultado

        return ejecutar(doc, "/set_parameters/", request, cuerpo)

    @api.route("/create_elements/", methods=["POST"])
    @requiere_token
    def create_elements(doc, request):
        """Varios elementos de kind mezclado en UNA transaccion. Acepta `simular` y `forzar`."""

        def cuerpo(ctx):
            data = ctx["data"]
            elements = data.get("elements")
            if not isinstance(elements, (list, tuple)) or not elements:
                raise EscrituraRechazada(
                    "elements is required: [{\"kind\": \"wall\" | \"level\" | ..., ...}]", 400,
                    {"available_kinds": list(KINDS)},
                )
            comprobar_alcance(data, len(elements), "elementos a crear")

            # Fase 1: validar TODOS los elementos antes de abrir la transaccion
            cache = _Cache(doc)
            planes = []
            for indice, elem in enumerate(elements):
                try:
                    planes.append(planificar_elemento(doc, elem, indice, cache))
                except EscrituraRechazada as rechazo:
                    extra = dict(rechazo.extra)
                    extra.setdefault("index", indice)
                    if isinstance(elem, dict) and elem.get("kind"):
                        extra.setdefault("kind", elem.get("kind"))
                    mensaje = rechazo.mensaje
                    if not mensaje.startswith("elements["):
                        mensaje = "elements[{}]: {}".format(indice, mensaje)
                    raise EscrituraRechazada(mensaje, rechazo.status if rechazo.status in (400, 404) else 400, extra)
                except ValueError as error:
                    raise EscrituraRechazada("elements[{}]: {}".format(indice, error), 400,
                                             {"index": indice, "kind": (elem.get("kind") if isinstance(elem, dict) else None)})
            recuento = _contar(planes)
            haria = [dict(p["haria"], index=p["index"], kind=p["kind"]) for p in planes]
            if ctx["simular"]:
                return simulacion(haria, count=len(planes), plan={"counts": recuento, "total": len(planes)})

            # Fase 2: crear dentro de una sola transaccion; un fallo revierte todo
            creados_por_plan = []
            try:
                with transaccion(doc, "Crear {} elementos".format(len(planes))):
                    for plan in planes:
                        try:
                            ids = crear_elemento(doc, plan)
                        except Exception as error:
                            raise _FalloEnLote(plan["index"], plan["kind"], _texto(error))
                        creados_por_plan.append((plan, ids))
            except _FalloEnLote as fallo:
                raise EscrituraRechazada(
                    "elements[{}] ({}) failed in Revit: {}; the transaction was rolled back and nothing was "
                    "created".format(fallo.indice, fallo.kind, fallo.error),
                    500, {"index": fallo.indice, "kind": fallo.kind, "revit_error": fallo.error, "rolled_back": True},
                )

            todos = []
            for plan, ids in creados_por_plan:
                todos.extend(ids)
            resultado = resultado_creacion(doc, todos, None, {"plan": {"counts": recuento, "total": len(planes)}})
            por_id = dict((c["id"], c) for c in resultado["creados"])
            agrupados = {}
            for plan, ids in creados_por_plan:
                for identificador in ids:
                    creado = por_id.get(identificador)
                    if creado is None:
                        continue
                    agrupados.setdefault(plan["kind"], []).append(_completar_creado(doc, plan, creado))
            resultado["creados"] = agrupados
            resultado["creados_ids"] = todos
            resultado["count"] = len(todos)
            resultado["message"] = "Created {} element(s): {}".format(
                len(todos), ", ".join("{} {}".format(n, k) for k, n in sorted(recuento.items())))
            return resultado

        return ejecutar(doc, "/create_elements/", request, cuerpo)

    logger.info("Lotes routes registered successfully")
