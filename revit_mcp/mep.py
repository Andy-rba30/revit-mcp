# -*- coding: UTF-8 -*-
"""
MEP Module for Revit MCP
Handles duct, pipe, and MEP system creation.

Las tres rutas pasan por escritura.ejecutar (copia, log, simular, IA:).
"""

from utils import (
    get_element_name, get_element_id_value, make_element_id, xyz_desde_mm, punto_a_mm,
    elementos_por_nombre, mapa_niveles, coleccion_niveles, MM_TO_FEET,
)
from seguridad import requiere_token
from escritura import ejecutar, transaccion, simulacion, EscrituraRechazada, resultado_creacion, describir_elemento
from pyrevit import routes, revit, DB
import logging

logger = logging.getLogger(__name__)


def _puntos(data):
    start_point = data.get("start_point")
    end_point = data.get("end_point")
    if not start_point or not end_point:
        raise EscrituraRechazada("start_point and end_point are required", 400)
    try:
        start = xyz_desde_mm(start_point)
        end = xyz_desde_mm(end_point)
    except ValueError as error:
        raise EscrituraRechazada(str(error), 400)
    if start.DistanceTo(end) < 0.001:
        raise EscrituraRechazada("Start and end points must be different", 400)
    return start, end


def _tipo(mapa, nombre, etiqueta, clave_lista):
    if not mapa:
        raise EscrituraRechazada(
            "No {} types available — load a MEP template or family first.".format(etiqueta), 400
        )
    if nombre:
        tipo = mapa.get(nombre)
        if not tipo:
            raise EscrituraRechazada(
                "{} type '{}' not found".format(etiqueta.capitalize(), nombre), 404,
                {clave_lista: sorted(mapa.keys())},
            )
        return tipo
    return list(mapa.values())[0]


def _sistema(mapa, nombre):
    if not mapa:
        return None
    if nombre and nombre in mapa:
        return mapa[nombre]
    return list(mapa.values())[0]


def _nivel(doc, data, start):
    level_name = data.get("level_name")
    level_map = mapa_niveles(doc)
    if level_name:
        nivel = level_map.get(level_name)
        if not nivel:
            raise EscrituraRechazada(
                "Level '{}' not found".format(level_name), 404,
                {"available_levels": sorted(level_map.keys())},
            )
        return nivel
    niveles = list(coleccion_niveles(doc))
    if not niveles:
        raise EscrituraRechazada("No levels found in the project", 400)
    return min(niveles, key=lambda lv: abs(lv.Elevation - start.Z))


def _fijar_medida(elem, nombre, valor_mm):
    if valor_mm is None:
        return None
    p = elem.LookupParameter(nombre)
    if p and not p.IsReadOnly:
        p.Set(float(valor_mm) * MM_TO_FEET)
        return True
    return False


def _medida_mm(elem, nombre):
    try:
        p = elem.LookupParameter(nombre)
        if p and p.HasValue:
            return round(p.AsDouble() / MM_TO_FEET, 1)
    except Exception:
        pass
    return None


def register_mep_routes(api):
    """Register all MEP routes with the API"""

    @api.route("/create_duct/", methods=["POST"])
    @requiere_token
    def create_duct_handler(doc, request):
        """Create a duct between two points. Accepts `simular`."""

        def cuerpo(ctx):
            data = ctx["data"]
            start, end = _puntos(data)
            duct_types = elementos_por_nombre(
                DB.FilteredElementCollector(doc).OfClass(DB.Mechanical.DuctType).ToElements()
            )
            target_duct_type = _tipo(duct_types, data.get("duct_type"), "duct", "available_duct_types")
            system_types = elementos_por_nombre(
                DB.FilteredElementCollector(doc).OfClass(DB.Mechanical.MechanicalSystemType).ToElements()
            )
            target_system_type = _sistema(system_types, data.get("system_type"))
            target_level = _nivel(doc, data, start)
            medidas = {
                "Diameter": data.get("diameter"), "Width": data.get("width"), "Height": data.get("height"),
            }

            if ctx["simular"]:
                return simulacion([{
                    "accion": "crear", "element_type": "duct",
                    "duct_type": get_element_name(target_duct_type),
                    "system_type": get_element_name(target_system_type) if target_system_type else None,
                    "level": get_element_name(target_level),
                    "start_mm": punto_a_mm(start), "end_mm": punto_a_mm(end),
                    "dimensions_mm": dict((k.lower(), v) for k, v in medidas.items() if v is not None),
                }])

            with transaccion(doc, "Crear conducto"):
                sys_type_id = target_system_type.Id if target_system_type else DB.ElementId.InvalidElementId
                duct = DB.Mechanical.Duct.Create(
                    doc, sys_type_id, target_duct_type.Id, target_level.Id, start, end
                )
                for nombre, valor in medidas.items():
                    _fijar_medida(duct, nombre, valor)
                duct_id = get_element_id_value(duct)

            resultado = resultado_creacion(doc, [duct_id])
            resultado.update({
                "duct_id": duct_id,
                "system_type": get_element_name(target_system_type) if target_system_type else "None",
                "duct_type": get_element_name(target_duct_type),
                "level": get_element_name(target_level),
                "dimensions_mm": {
                    "diameter": _medida_mm(duct, "Diameter"),
                    "width": _medida_mm(duct, "Width"),
                    "height": _medida_mm(duct, "Height"),
                },
                "message": "Created duct on level '{}'".format(get_element_name(target_level)),
            })
            return resultado

        return ejecutar(doc, "/create_duct/", request, cuerpo)

    @api.route("/create_pipe/", methods=["POST"])
    @requiere_token
    def create_pipe_handler(doc, request):
        """Create a pipe between two points. Accepts `simular`."""

        def cuerpo(ctx):
            data = ctx["data"]
            start, end = _puntos(data)
            pipe_types = elementos_por_nombre(
                DB.FilteredElementCollector(doc).OfClass(DB.Plumbing.PipeType).ToElements()
            )
            target_pipe_type = _tipo(pipe_types, data.get("pipe_type"), "pipe", "available_pipe_types")
            system_types = elementos_por_nombre(
                DB.FilteredElementCollector(doc).OfClass(DB.Plumbing.PipingSystemType).ToElements()
            )
            target_system_type = _sistema(system_types, data.get("system_type"))
            target_level = _nivel(doc, data, start)
            diameter = data.get("diameter")

            if ctx["simular"]:
                return simulacion([{
                    "accion": "crear", "element_type": "pipe",
                    "pipe_type": get_element_name(target_pipe_type),
                    "system_type": get_element_name(target_system_type) if target_system_type else None,
                    "level": get_element_name(target_level),
                    "start_mm": punto_a_mm(start), "end_mm": punto_a_mm(end),
                    "diameter_mm": diameter,
                }])

            with transaccion(doc, "Crear tuberia"):
                sys_type_id = target_system_type.Id if target_system_type else DB.ElementId.InvalidElementId
                pipe = DB.Plumbing.Pipe.Create(
                    doc, sys_type_id, target_pipe_type.Id, target_level.Id, start, end
                )
                _fijar_medida(pipe, "Diameter", diameter)
                pipe_id = get_element_id_value(pipe)

            resultado = resultado_creacion(doc, [pipe_id])
            resultado.update({
                "pipe_id": pipe_id,
                "system_type": get_element_name(target_system_type) if target_system_type else "None",
                "pipe_type": get_element_name(target_pipe_type),
                "level": get_element_name(target_level),
                "diameter_mm": _medida_mm(pipe, "Diameter"),
                "message": "Created pipe on level '{}'".format(get_element_name(target_level)),
            })
            return resultado

        return ejecutar(doc, "/create_pipe/", request, cuerpo)

    @api.route("/create_mep_system/", methods=["POST"])
    @requiere_token
    def create_mep_system_handler(doc, request):
        """Create (or rename) a mechanical or piping system. Accepts `simular`."""

        def cuerpo(ctx):
            data = ctx["data"]
            system_type = data.get("system_type")
            system_name = data.get("system_name")
            if not system_type:
                raise EscrituraRechazada("system_type is required (mechanical or piping)", 400)
            if not system_name:
                raise EscrituraRechazada("system_name is required", 400)
            if system_type not in ("mechanical", "piping"):
                raise EscrituraRechazada("system_type must be 'mechanical' or 'piping'", 400)
            element_ids = data.get("element_ids", [])
            if not element_ids:
                raise EscrituraRechazada(
                    "element_ids is required. Provide IDs of ducts/pipes to group into a system.", 400,
                    {"hint": "Create ducts or pipes first, then group them."},
                )

            elementos = []
            for eid in element_ids:
                elem = doc.GetElement(make_element_id(eid))
                if not elem:
                    raise EscrituraRechazada("Element {} not found".format(eid), 404)
                elementos.append(elem)

            def _conectores(elem):
                conn_mgr = None
                if hasattr(elem, "ConnectorManager"):
                    conn_mgr = elem.ConnectorManager
                elif hasattr(elem, "MEPModel") and elem.MEPModel:
                    conn_mgr = elem.MEPModel.ConnectorManager
                return list(conn_mgr.Connectors) if conn_mgr else []

            existing_systems = set()
            sin_sistema = []
            for elem in elementos:
                for c in _conectores(elem):
                    if hasattr(c, "MEPSystem") and c.MEPSystem:
                        existing_systems.add(get_element_id_value(c.MEPSystem))
                    else:
                        sin_sistema.append(c)

            if existing_systems:
                accion = {
                    "accion": "renombrar_sistema", "system_id": sorted(existing_systems)[0],
                    "system_name": system_name, "system_type": system_type,
                }
            elif sin_sistema:
                accion = {
                    "accion": "crear_sistema", "system_name": system_name, "system_type": system_type,
                    "connectors": len(sin_sistema), "element_ids": element_ids,
                }
            else:
                raise EscrituraRechazada(
                    "All connectors are already assigned to systems and could not be renamed.", 400
                )
            if ctx["simular"]:
                return simulacion([accion])

            with transaccion(doc, "Sistema MEP {}".format(system_name)):
                new_system = None
                if existing_systems:
                    new_system = doc.GetElement(make_element_id(sorted(existing_systems)[0]))
                else:
                    first_connector = sin_sistema[0]
                    unused_connectors = DB.ConnectorSet()
                    for c in sin_sistema:
                        unused_connectors.Insert(c)
                    if system_type == "mechanical":
                        new_system = doc.Create.NewMechanicalSystem(
                            first_connector, unused_connectors, DB.Mechanical.DuctSystemType.SupplyAir
                        )
                    else:
                        new_system = doc.Create.NewPipingSystem(
                            first_connector, unused_connectors, DB.Plumbing.PipeSystemType.DomesticHotWater
                        )
                if new_system is None:
                    raise EscrituraRechazada("Failed to create {} system".format(system_type), 500)
                name_param = new_system.LookupParameter("System Name")
                if name_param and not name_param.IsReadOnly:
                    name_param.Set(system_name)
                system_id = get_element_id_value(new_system)

            # Verificacion: el sistema existe y tiene el nombre pedido
            descripcion = describir_elemento(doc, system_id)
            nombre_real = None
            try:
                p = doc.GetElement(make_element_id(system_id)).LookupParameter("System Name")
                nombre_real = p.AsString() if p else None
            except Exception:
                pass
            coincide = descripcion is not None and nombre_real == system_name
            resultado = {
                "system_id": system_id,
                "system_name": nombre_real,
                "system_type": system_type,
                "element_count": len(element_ids),
                "creados": [descripcion] if (descripcion and not existing_systems) else [],
                "antes": {"system_ids": sorted(existing_systems)},
                "despues": {"system_id": system_id, "system_name": nombre_real},
                "ok": coincide,
                "message": "{} {} system '{}'".format(
                    "Renamed" if existing_systems else "Created", system_type, system_name
                ),
            }
            resultado["verificacion"] = (
                {"coincide": True} if coincide else
                {"coincide": False, "detalle": "System name read back is {!r}, requested {!r}".format(
                    nombre_real, system_name)}
            )
            return resultado

        return ejecutar(doc, "/create_mep_system/", request, cuerpo)

    logger.info("MEP routes registered successfully")
