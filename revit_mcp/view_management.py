# -*- coding: UTF-8 -*-
"""
View Management Module for Revit MCP
Handles view creation and active view switching.

Ambas rutas pasan por escritura.ejecutar (copia, log, simular, IA:).
set_active_view no abre transaccion (es una accion de interfaz) pero se
registra y verifica igual.
"""

from utils import get_element_name, get_element_id_value, xyz_desde_mm, mapa_niveles, buscar_vista, MM_TO_FEET
from seguridad import requiere_token
from escritura import ejecutar, transaccion, simulacion, EscrituraRechazada, resultado_creacion
from pyrevit import routes, revit, DB
import logging

logger = logging.getLogger(__name__)


def _view_family_type(doc, familia, etiqueta):
    for vf in DB.FilteredElementCollector(doc).OfClass(DB.ViewFamilyType).ToElements():
        if vf.ViewFamily == familia:
            return vf
    raise EscrituraRechazada("No {} view family type found in project".format(etiqueta), 400)


def register_view_management_routes(api):
    """Register all view management routes with the API"""

    @api.route("/create_view/", methods=["POST"])
    @requiere_token
    def create_view_handler(doc, request):
        """Create a new view in the Revit model. Accepts `simular`."""

        def cuerpo(ctx):
            data = ctx["data"]
            view_type = data.get("view_type")
            name = data.get("name")
            if not view_type:
                raise EscrituraRechazada(
                    "view_type is required (floor_plan, ceiling_plan, section, elevation, 3d)", 400
                )
            if not name:
                raise EscrituraRechazada("name is required", 400)
            if view_type not in ("floor_plan", "ceiling_plan", "section", "elevation", "3d"):
                raise EscrituraRechazada(
                    "Unsupported view_type '{}'. Use: floor_plan, ceiling_plan, section, elevation, 3d".format(view_type),
                    400,
                )
            if buscar_vista(doc, name) is not None:
                raise EscrituraRechazada("A view named '{}' already exists".format(name), 400)

            level_name = data.get("level_name")
            plan = {"view_type": view_type, "name": name}

            if view_type in ("floor_plan", "ceiling_plan"):
                if not level_name:
                    raise EscrituraRechazada("level_name is required for {} views".format(view_type), 400)
                level_map = mapa_niveles(doc)
                target_level = level_map.get(level_name)
                if not target_level:
                    raise EscrituraRechazada(
                        "Level '{}' not found".format(level_name), 404,
                        {"available_levels": sorted(level_map.keys())},
                    )
                plan["level"] = target_level
                plan["vft"] = _view_family_type(
                    doc,
                    DB.ViewFamily.FloorPlan if view_type == "floor_plan" else DB.ViewFamily.CeilingPlan,
                    view_type,
                )
            elif view_type == "section":
                section_box = data.get("section_box")
                if not section_box:
                    raise EscrituraRechazada("section_box is required for section views", 400)
                plan["vft"] = _view_family_type(doc, DB.ViewFamily.Section, "section")
                origin = section_box.get("origin", {})
                direction = section_box.get("direction", {"x": 0, "y": 1, "z": 0})
                up = section_box.get("up", {"x": 0, "y": 0, "z": 1})
                plan["width"] = float(section_box.get("width", 10000)) * MM_TO_FEET
                plan["height"] = float(section_box.get("height", 10000)) * MM_TO_FEET
                plan["depth"] = float(section_box.get("depth", 10000)) * MM_TO_FEET
                plan["origin"] = xyz_desde_mm(origin)
                plan["dir"] = DB.XYZ(
                    float(direction.get("x", 0)), float(direction.get("y", 1)), float(direction.get("z", 0))
                ).Normalize()
                plan["up"] = DB.XYZ(
                    float(up.get("x", 0)), float(up.get("y", 0)), float(up.get("z", 1))
                ).Normalize()
            elif view_type == "3d":
                plan["vft"] = _view_family_type(doc, DB.ViewFamily.ThreeDimensional, "3D")
            else:  # elevation
                plan["vft"] = _view_family_type(doc, DB.ViewFamily.Elevation, "elevation")
                plan_view = None
                plan_views = (
                    DB.FilteredElementCollector(doc).OfClass(DB.ViewPlan).WhereElementIsNotElementType().ToElements()
                )
                if level_name:
                    for pv in plan_views:
                        if pv.ViewType == DB.ViewType.FloorPlan and not pv.IsTemplate:
                            if pv.GenLevel and get_element_name(pv.GenLevel) == level_name:
                                plan_view = pv
                                break
                if not plan_view:
                    active = doc.ActiveView
                    if hasattr(active, "ViewType") and active.ViewType == DB.ViewType.FloorPlan:
                        plan_view = active
                if not plan_view:
                    for pv in plan_views:
                        if pv.ViewType == DB.ViewType.FloorPlan and not pv.IsTemplate:
                            plan_view = pv
                            break
                if not plan_view:
                    raise EscrituraRechazada("No floor plan view found to host the elevation marker", 400)
                plan["plan_view"] = plan_view

            if ctx["simular"]:
                return simulacion([{
                    "accion": "crear", "element_type": "view", "view_type": view_type, "name": name,
                    "level": level_name,
                    "view_family_type": get_element_name(plan["vft"]),
                    "host_plan_view": get_element_name(plan["plan_view"]) if "plan_view" in plan else None,
                }])

            with transaccion(doc, "Crear vista {}".format(name)):
                if view_type in ("floor_plan", "ceiling_plan"):
                    new_view = DB.ViewPlan.Create(doc, plan["vft"].Id, plan["level"].Id)
                elif view_type == "section":
                    right_vec = plan["dir"].CrossProduct(plan["up"]).Normalize()
                    transform = DB.Transform.Identity
                    transform.Origin = plan["origin"]
                    transform.BasisX = right_vec
                    transform.BasisY = plan["up"]
                    transform.BasisZ = plan["dir"]
                    section_bb = DB.BoundingBoxXYZ()
                    section_bb.Transform = transform
                    section_bb.Min = DB.XYZ(-plan["width"] / 2.0, -plan["height"] / 2.0, 0)
                    section_bb.Max = DB.XYZ(plan["width"] / 2.0, plan["height"] / 2.0, plan["depth"])
                    new_view = DB.ViewSection.CreateSection(doc, plan["vft"].Id, section_bb)
                elif view_type == "3d":
                    new_view = DB.View3D.CreateIsometric(doc, plan["vft"].Id)
                else:
                    marker = DB.ElevationMarker.CreateElevationMarker(doc, plan["vft"].Id, DB.XYZ.Zero, 1)
                    new_view = marker.CreateElevation(doc, plan["plan_view"].Id, 0)
                if new_view is None:
                    raise EscrituraRechazada("Revit did not return a view", 500)
                new_view.Name = name
                view_id = get_element_id_value(new_view)

            resultado = resultado_creacion(doc, [view_id])
            nombre_real = get_element_name(new_view)
            actual_type = ""
            try:
                actual_type = str(new_view.ViewType)
            except Exception:
                actual_type = view_type
            resultado.update({
                "view_id": view_id,
                "name": nombre_real,
                "view_type": actual_type,
                "message": "Created {} view '{}'".format(view_type, nombre_real),
            })
            if nombre_real != name:
                resultado["ok"] = False
                resultado["verificacion"] = {
                    "coincide": False,
                    "detalle": "View was created but its name is {!r}, not {!r}".format(nombre_real, name),
                }
            return resultado

        return ejecutar(doc, "/create_view/", request, cuerpo)

    @api.route("/set_active_view/", methods=["POST"])
    @requiere_token
    def set_active_view_handler(doc, uidoc, request):
        """Set the active view in Revit UI. Accepts `simular`."""

        def cuerpo(ctx):
            data = ctx["data"]
            view_name = data.get("view_name")
            if not view_name:
                raise EscrituraRechazada("view_name is required", 400)
            if uidoc is None:
                raise EscrituraRechazada("No active UI document", 503)

            target_view = buscar_vista(doc, view_name)
            if not target_view:
                available = []
                for v in DB.FilteredElementCollector(doc).OfClass(DB.View).WhereElementIsNotElementType().ToElements():
                    try:
                        if not v.IsTemplate:
                            available.append(get_element_name(v))
                    except Exception:
                        continue
                raise EscrituraRechazada(
                    "View '{}' not found".format(view_name), 404,
                    {"available_views": sorted(available)[:30]},
                )

            antes = None
            try:
                antes = {"view_id": get_element_id_value(uidoc.ActiveView), "name": get_element_name(uidoc.ActiveView)}
            except Exception:
                antes = None
            objetivo = {"view_id": get_element_id_value(target_view), "name": view_name, "view_type": str(target_view.ViewType)}

            if ctx["simular"]:
                return simulacion([{"accion": "activar_vista", "antes": antes, "despues": objetivo}])

            uidoc.ActiveView = target_view

            despues = None
            try:
                despues = {"view_id": get_element_id_value(uidoc.ActiveView), "name": get_element_name(uidoc.ActiveView)}
            except Exception:
                despues = None
            coincide = despues is not None and despues["view_id"] == objetivo["view_id"]
            resultado = {
                "view_id": objetivo["view_id"],
                "name": view_name,
                "view_type": objetivo["view_type"],
                "antes": antes,
                "despues": despues,
                "ok": coincide,
                "message": "Switched to view '{}'".format(view_name),
            }
            resultado["verificacion"] = (
                {"coincide": True} if coincide else
                {"coincide": False, "detalle": "uidoc.ActiveView is still {}".format(despues)}
            )
            return resultado

        return ejecutar(doc, "/set_active_view/", request, cuerpo)

    logger.info("View management routes registered successfully")
