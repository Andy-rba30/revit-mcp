# -*- coding: UTF-8 -*-
"""
Annotation Module for Revit MCP
Handles dimensions and wall tagging.

Ambas rutas pasan por escritura.ejecutar (copia, log, simular, IA:, creados).
"""

from utils import get_element_name, get_element_id_value, make_element_id
from seguridad import requiere_token
from escritura import ejecutar, transaccion, simulacion, EscrituraRechazada, resultado_creacion
from pyrevit import routes, revit, DB
import logging

logger = logging.getLogger(__name__)


def _referencia(elem, active_view):
    """Referencia acotable de un elemento (linea de ubicacion o geometria)."""
    ref = None
    try:
        ref = elem.GetReferenceByName("Center")
    except Exception:
        ref = None
    if ref:
        return ref
    options = DB.Options()
    options.ComputeReferences = True
    options.View = active_view
    geom = elem.get_Geometry(options)
    if not geom:
        return None
    for geom_obj in geom:
        if hasattr(geom_obj, "Reference") and geom_obj.Reference:
            return geom_obj.Reference
        if hasattr(geom_obj, "GetInstanceGeometry"):
            inst_geom = geom_obj.GetInstanceGeometry()
            if inst_geom:
                for ig in inst_geom:
                    if hasattr(ig, "Reference") and ig.Reference:
                        return ig.Reference
    return None


def register_annotation_routes(api):
    """Register all annotation routes with the API"""

    @api.route("/create_dimensions/", methods=["POST"])
    @requiere_token
    def create_dimensions_handler(doc, request):
        """Create dimension annotations in the current view. Accepts `simular`."""

        def cuerpo(ctx):
            data = ctx["data"]
            element_ids = data.get("element_ids", [])
            dimension_type = data.get("dimension_type", "linear")
            if not element_ids:
                raise EscrituraRechazada("No element_ids provided", 400)

            active_view = doc.ActiveView
            if not active_view:
                raise EscrituraRechazada("No active view", 400)
            supported_types = [
                DB.ViewType.FloorPlan, DB.ViewType.CeilingPlan, DB.ViewType.Section,
                DB.ViewType.Elevation, DB.ViewType.Detail, DB.ViewType.EngineeringPlan,
                DB.ViewType.AreaPlan,
            ]
            if active_view.ViewType not in supported_types:
                raise EscrituraRechazada(
                    "Current view does not support dimensions — switch to a plan, section, or elevation view",
                    400,
                )

            ref_array = DB.ReferenceArray()
            element_points = []
            referenciados = []
            for eid in element_ids:
                elem = doc.GetElement(make_element_id(eid))
                if not elem:
                    raise EscrituraRechazada(
                        "Element {} not found or not visible in the current view".format(eid), 404
                    )
                try:
                    loc = elem.Location if hasattr(elem, "Location") else None
                    if loc is not None and hasattr(loc, "Curve"):
                        ref = _referencia(elem, active_view)
                        if ref:
                            ref_array.Append(ref)
                            element_points.append(loc.Curve.Evaluate(0.5, True))
                            referenciados.append(eid)
                    elif loc is not None and hasattr(loc, "Point"):
                        element_points.append(loc.Point)
                except Exception as ref_err:
                    logger.warning("Could not get reference for element {}: {}".format(eid, str(ref_err)))

            if ref_array.Size < 2:
                raise EscrituraRechazada(
                    "Need at least 2 elements with valid geometry references to create a dimension", 400
                )

            # Dimension line position (offset from midpoint, perpendicular)
            if len(element_points) >= 2:
                p1 = element_points[0]
                p2 = element_points[-1]
                dx = p2.X - p1.X
                dy = p2.Y - p1.Y
                length = (dx * dx + dy * dy) ** 0.5
                if length > 0.001:
                    offset_dist = 3.0  # 3 feet
                    nx = -dy / length
                    ny = dx / length
                    dim_line = DB.Line.CreateBound(
                        DB.XYZ(p1.X + nx * offset_dist, p1.Y + ny * offset_dist, p1.Z),
                        DB.XYZ(p2.X + nx * offset_dist, p2.Y + ny * offset_dist, p2.Z),
                    )
                else:
                    dim_line = DB.Line.CreateBound(p1, p2)
            else:
                dim_line = DB.Line.CreateBound(DB.XYZ(0, 10, 0), DB.XYZ(100, 10, 0))

            if ctx["simular"]:
                return simulacion([{
                    "accion": "acotar", "dimension_type": dimension_type,
                    "element_ids": referenciados, "references": ref_array.Size,
                    "view": get_element_name(active_view),
                }])

            with transaccion(doc, "Crear cotas"):
                dim = doc.Create.NewDimension(active_view, dim_line, ref_array)
                if not dim:
                    raise EscrituraRechazada("NewDimension returned no dimension", 500)
                dim_id = get_element_id_value(dim)

            resultado = resultado_creacion(doc, [dim_id])
            dim_value = ""
            try:
                dim_value = dim.ValueString
            except Exception:
                pass
            for creado in resultado["creados"]:
                creado["elements_dimensioned"] = referenciados
                creado["value"] = dim_value or "N/A"
            resultado["message"] = "Created {} dimension annotation{} in the current view".format(
                resultado["count"], "s" if resultado["count"] != 1 else ""
            )
            return resultado

        return ejecutar(doc, "/create_dimensions/", request, cuerpo)

    @api.route("/tag_walls/", methods=["POST"])
    @requiere_token
    def tag_walls_handler(doc, request):
        """Tag all untagged walls in the current view. Accepts `simular`."""

        def cuerpo(ctx):
            data = ctx["data"]
            use_leader = bool(data.get("use_leader", False))
            tag_type_name = data.get("tag_type_name")

            active_view = doc.ActiveView
            if not active_view:
                raise EscrituraRechazada("No active view", 400)

            tag_symbols = (
                DB.FilteredElementCollector(doc)
                .OfCategory(DB.BuiltInCategory.OST_WallTags)
                .OfClass(DB.FamilySymbol)
                .ToElements()
            )
            if not tag_symbols or len(tag_symbols) == 0:
                raise EscrituraRechazada(
                    "No wall tag types found: load a wall tag family into the project (load_family) and retry", 400
                )
            target_tag = None
            if tag_type_name:
                for sym in tag_symbols:
                    try:
                        if get_element_name(sym) == tag_type_name:
                            target_tag = sym
                            break
                    except Exception:
                        continue
                if target_tag is None:
                    raise EscrituraRechazada("Wall tag type '{}' not found".format(tag_type_name), 404)
            if target_tag is None:
                target_tag = tag_symbols[0]

            walls = (
                DB.FilteredElementCollector(doc, active_view.Id)
                .OfCategory(DB.BuiltInCategory.OST_Walls)
                .WhereElementIsNotElementType()
                .ToElements()
            )
            if not walls or len(walls) == 0:
                raise EscrituraRechazada("Current view has no walls to tag", 400)

            existing_tags = (
                DB.FilteredElementCollector(doc, active_view.Id)
                .OfCategory(DB.BuiltInCategory.OST_WallTags)
                .WhereElementIsNotElementType()
                .ToElements()
            )
            tagged_wall_ids = set()
            for tag in existing_tags:
                try:
                    if hasattr(tag, "TaggedLocalElementId"):
                        tagged_wall_ids.add(get_element_id_value(tag.TaggedLocalElementId))
                except Exception:
                    continue

            pendientes = []
            skipped = []
            walls_already_tagged = 0
            for wall in walls:
                wall_id_val = get_element_id_value(wall)
                if wall_id_val in tagged_wall_ids:
                    walls_already_tagged += 1
                    continue
                loc = wall.Location
                if not loc or not hasattr(loc, "Curve"):
                    skipped.append({"wall_id": wall_id_val, "reason": "Wall has no location curve to anchor a tag"})
                    continue
                pendientes.append((wall, wall_id_val, loc.Curve.Evaluate(0.5, True)))

            if ctx["simular"]:
                return simulacion(
                    [{"accion": "etiquetar", "wall_id": wid, "tag_type": get_element_name(target_tag),
                      "view": get_element_name(active_view)} for _, wid, _ in pendientes],
                    count=len(pendientes), walls_already_tagged=walls_already_tagged,
                    walls_total=len(walls), skipped=skipped,
                )

            tag_ids = []
            with transaccion(doc, "Etiquetar muros"):
                if not target_tag.IsActive:
                    target_tag.Activate()
                    doc.Regenerate()
                for wall, wall_id_val, mid in pendientes:
                    try:
                        tag = DB.IndependentTag.Create(
                            doc, active_view.Id, DB.Reference(wall), use_leader,
                            DB.TagMode.TM_ADDBY_CATEGORY, DB.TagOrientation.Horizontal, mid,
                        )
                        if tag:
                            tag_ids.append(get_element_id_value(tag))
                        else:
                            skipped.append({"wall_id": wall_id_val, "reason": "IndependentTag.Create returned no tag"})
                    except Exception as tag_err:
                        reason = str(tag_err)
                        logger.warning("Could not tag wall {}: {}".format(wall_id_val, reason))
                        skipped.append({"wall_id": wall_id_val, "reason": reason})

            resultado = resultado_creacion(doc, tag_ids)
            tags_placed = resultado["count"]
            resultado.update({
                "tags_placed": tags_placed,
                "tag_ids": tag_ids,
                "walls_already_tagged": walls_already_tagged,
                "walls_total": len(walls),
                "message": "Tagged {} wall{} ({} already tagged, {} failed, {} total in view)".format(
                    tags_placed, "s" if tags_placed != 1 else "", walls_already_tagged, len(skipped), len(walls)
                ),
            })
            if skipped:
                resultado["skipped"] = skipped
            return resultado

        return ejecutar(doc, "/tag_walls/", request, cuerpo)

    logger.info("Annotation routes registered successfully")
