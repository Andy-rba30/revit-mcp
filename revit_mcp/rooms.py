# -*- coding: UTF-8 -*-
"""
Rooms Module for Revit MCP
Handles room creation and room separation lines.

Ambas rutas pasan por escritura.ejecutar (copia, log, simular, IA:).
"""

from utils import get_element_name, get_element_id_value, xyz_desde_mm, punto_a_mm, mapa_niveles, buscar_vista, MM_TO_FEET
from seguridad import requiere_token
from escritura import ejecutar, transaccion, simulacion, EscrituraRechazada, resultado_creacion
from pyrevit import routes, revit, DB
import logging

logger = logging.getLogger(__name__)


def _texto_parametro(elem, nombre):
    try:
        p = elem.LookupParameter(nombre)
        if p:
            return p.AsString() or ""
    except Exception:
        pass
    return ""


def register_room_routes(api):
    """Register all room routes with the API"""

    @api.route("/create_room/", methods=["POST"])
    @requiere_token
    def create_room_handler(doc, request):
        """Create a room at a specified level. Accepts `simular`."""

        def cuerpo(ctx):
            data = ctx["data"]
            level_name = data.get("level_name")
            if not level_name:
                raise EscrituraRechazada("level_name is required", 400)

            level_map = mapa_niveles(doc)
            target_level = level_map.get(level_name)
            if not target_level:
                raise EscrituraRechazada(
                    "Level '{}' not found".format(level_name), 404,
                    {"available_levels": sorted(level_map.keys())},
                )

            # Rooms require a phase
            phases = doc.Phases
            if not phases or phases.Size == 0:
                raise EscrituraRechazada("No phases found in the project", 400)
            phase = phases.get_Item(phases.Size - 1)

            location = data.get("location")
            point = None
            if location:
                xyz = xyz_desde_mm(location)
                point = DB.UV(xyz.X, xyz.Y)
            room_name = data.get("name")
            room_number = data.get("number")

            if ctx["simular"]:
                return simulacion([{
                    "accion": "crear", "element_type": "room", "level": level_name,
                    "location_mm": punto_a_mm(xyz) if location else None,
                    "name": room_name, "number": room_number,
                    "phase": get_element_name(phase),
                }])

            with transaccion(doc, "Crear habitacion"):
                if point is not None:
                    room = doc.Create.NewRoom(target_level, point)
                else:
                    room = doc.Create.NewRoom(phase)
                if not room:
                    raise EscrituraRechazada(
                        "Failed to create room — no enclosed area found at the specified location. "
                        "Add walls or room separation lines first.",
                        400,
                    )
                if room_name:
                    name_param = room.LookupParameter("Name")
                    if name_param and not name_param.IsReadOnly:
                        name_param.Set(room_name)
                if room_number:
                    number_param = room.LookupParameter("Number")
                    if number_param and not number_param.IsReadOnly:
                        number_param.Set(room_number)
                room_id = get_element_id_value(room)

            resultado = resultado_creacion(doc, [room_id])
            area = 0.0
            try:
                area_param = room.LookupParameter("Area")
                if area_param and area_param.HasValue:
                    area = round(area_param.AsDouble() * 0.092903, 2)  # sq ft to sq m
            except Exception:
                pass
            actual_name = _texto_parametro(room, "Name")
            actual_number = _texto_parametro(room, "Number")
            resultado.update({
                "room_id": room_id,
                "name": actual_name,
                "number": actual_number,
                "level": level_name,
                "area": area,
                "message": "Room '{}' created on level '{}'".format(
                    actual_name or actual_number or "Unnamed", level_name
                ),
            })
            if area <= 0:
                resultado["warning"] = "Room is unplaced or has no enclosed area (area 0)"
            return resultado

        return ejecutar(doc, "/create_room/", request, cuerpo)

    @api.route("/create_room_separation/", methods=["POST"])
    @requiere_token
    def create_room_separation_handler(doc, request):
        """Create room separation lines. Accepts `simular`."""

        def cuerpo(ctx):
            data = ctx["data"]
            lines = data.get("lines", [])
            if not lines:
                raise EscrituraRechazada("No lines provided", 400)

            view_name = data.get("view_name")
            if view_name:
                target_view = buscar_vista(doc, view_name, solo_planta=True)
                if not target_view:
                    raise EscrituraRechazada("View '{}' not found".format(view_name), 404)
            else:
                active_view = doc.ActiveView
                if hasattr(active_view, "ViewType") and active_view.ViewType in [
                    DB.ViewType.FloorPlan, DB.ViewType.CeilingPlan, DB.ViewType.AreaPlan
                ]:
                    target_view = active_view
                else:
                    raise EscrituraRechazada(
                        "Active view is not a plan view — specify a view_name or switch to a plan view", 400
                    )

            curvas = []
            haria = []
            for idx, line_def in enumerate(lines):
                try:
                    start = xyz_desde_mm(line_def.get("start_point", {}))
                    end = xyz_desde_mm(line_def.get("end_point", {}))
                except ValueError as error:
                    raise EscrituraRechazada("Line {}: {}".format(idx, error), 400)
                if start.DistanceTo(end) < 0.001:
                    raise EscrituraRechazada("Line {}: zero length".format(idx), 400)
                curvas.append((start, end))
                haria.append({
                    "accion": "crear", "element_type": "room_separation",
                    "view": get_element_name(target_view),
                    "start_mm": punto_a_mm(start), "end_mm": punto_a_mm(end),
                })

            if ctx["simular"]:
                return simulacion(haria, count=len(haria))

            ids = []
            with transaccion(doc, "Crear separaciones de habitacion"):
                curve_array = DB.CurveArray()
                for start, end in curvas:
                    curve_array.Append(DB.Line.CreateBound(start, end))
                sp_plane = target_view.SketchPlane
                if not sp_plane:
                    level_id = target_view.GenLevel.Id if target_view.GenLevel else None
                    if level_id:
                        sp_plane = DB.SketchPlane.Create(doc, level_id)
                separator = doc.Create.NewRoomBoundaryLines(sp_plane, curve_array, target_view)
                if separator:
                    for elem in separator:
                        ids.append(get_element_id_value(elem))

            resultado = resultado_creacion(doc, ids)
            resultado["line_count"] = len(ids)
            resultado["line_ids"] = ids
            resultado["message"] = "Created {} room separation line{}".format(
                len(ids), "s" if len(ids) != 1 else ""
            )
            if len(ids) != len(curvas):
                resultado["ok"] = False
                resultado["verificacion"] = {
                    "coincide": False,
                    "detalle": "Requested {} lines, Revit created {}".format(len(curvas), len(ids)),
                }
            return resultado

        return ejecutar(doc, "/create_room_separation/", request, cuerpo)

    logger.info("Room routes registered successfully")
