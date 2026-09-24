# -*- coding: UTF-8 -*-
"""
Tags Module for Revit MCP
Handles element tagging with annotation symbols.

Pasa por escritura.ejecutar (copia, log, simular, IA:, creados).
"""

from utils import get_element_name, get_element_id_value, make_element_id, buscar_vista, MM_TO_FEET
from seguridad import requiere_token
from escritura import ejecutar, transaccion, simulacion, EscrituraRechazada, resultado_creacion
from pyrevit import routes, revit, DB
import logging

logger = logging.getLogger(__name__)


def register_tag_routes(api):
    """Register all tag routes with the API"""

    @api.route("/tag_elements/", methods=["POST"])
    @requiere_token
    def tag_elements_handler(doc, request):
        """Tag elements with annotation symbols in a view. Accepts `simular`."""

        def cuerpo(ctx):
            data = ctx["data"]
            element_ids = data.get("element_ids", [])
            if not element_ids:
                raise EscrituraRechazada("element_ids is required and must not be empty", 400)

            view_name = data.get("view_name")
            if view_name:
                target_view = buscar_vista(doc, view_name)
                if not target_view:
                    raise EscrituraRechazada("View '{}' not found".format(view_name), 404)
            else:
                target_view = doc.ActiveView

            add_leader = bool(data.get("add_leader", False))
            orientation = data.get("orientation", "horizontal")
            offset = data.get("offset", {}) or {}
            tag_type_name = data.get("tag_type_name")
            offset_x = float(offset.get("x", 0)) * MM_TO_FEET
            offset_y = float(offset.get("y", 0)) * MM_TO_FEET
            tag_orientation = (
                DB.TagOrientation.Vertical if orientation == "vertical" else DB.TagOrientation.Horizontal
            )

            CATEGORY_TO_TAG_CATEGORY = {
                DB.BuiltInCategory.OST_Walls: DB.BuiltInCategory.OST_WallTags,
                DB.BuiltInCategory.OST_Doors: DB.BuiltInCategory.OST_DoorTags,
                DB.BuiltInCategory.OST_Windows: DB.BuiltInCategory.OST_WindowTags,
                DB.BuiltInCategory.OST_Rooms: DB.BuiltInCategory.OST_RoomTags,
                DB.BuiltInCategory.OST_Floors: DB.BuiltInCategory.OST_FloorTags,
                DB.BuiltInCategory.OST_StructuralFraming: DB.BuiltInCategory.OST_StructuralFramingTags,
                DB.BuiltInCategory.OST_StructuralColumns: DB.BuiltInCategory.OST_StructuralColumnTags,
            }
            cache_tipos = {}

            def find_tag_type_for_element(element):
                """Find the appropriate tag type for an element's category."""
                elem_cat = element.Category
                if not elem_cat:
                    return None
                try:
                    bic = elem_cat.BuiltInCategory
                    tag_bic = CATEGORY_TO_TAG_CATEGORY.get(bic)
                    if not tag_bic:
                        return None
                    if bic in cache_tipos:
                        return cache_tipos[bic]
                    tag_symbols = (
                        DB.FilteredElementCollector(doc)
                        .OfCategory(tag_bic)
                        .OfClass(DB.FamilySymbol)
                        .ToElements()
                    )
                    elegido = None
                    if tag_type_name:
                        for ts in tag_symbols:
                            if get_element_name(ts) == tag_type_name:
                                elegido = ts
                                break
                    if elegido is None and tag_symbols and tag_symbols.Count > 0:
                        elegido = tag_symbols[0]
                    cache_tipos[bic] = elegido
                    return elegido
                except Exception:
                    return None

            def punto_de(elem):
                try:
                    loc = elem.Location
                    if hasattr(loc, "Point"):
                        pt = loc.Point
                        return DB.XYZ(pt.X + offset_x, pt.Y + offset_y, pt.Z)
                    if hasattr(loc, "Curve"):
                        mid = loc.Curve.Evaluate(0.5, True)
                        return DB.XYZ(mid.X + offset_x, mid.Y + offset_y, mid.Z)
                except Exception:
                    pass
                bb = elem.get_BoundingBox(target_view)
                if bb:
                    return DB.XYZ(
                        (bb.Min.X + bb.Max.X) / 2.0 + offset_x,
                        (bb.Min.Y + bb.Max.Y) / 2.0 + offset_y,
                        (bb.Min.Z + bb.Max.Z) / 2.0,
                    )
                return None

            # Fase 1: resolver que se etiquetaria (sin transaccion)
            planes = []
            skipped = []
            for eid in element_ids:
                elem = doc.GetElement(make_element_id(eid))
                if not elem:
                    skipped.append({"element_id": eid, "reason": "Element not found"})
                    continue
                if not elem.Category:
                    skipped.append({"element_id": eid, "reason": "Element has no category"})
                    continue
                tag_type = find_tag_type_for_element(elem)
                if not tag_type:
                    skipped.append({
                        "element_id": eid,
                        "reason": "No tag type found for category '{}'".format(get_element_name(elem.Category)),
                    })
                    continue
                tag_point = punto_de(elem)
                if not tag_point:
                    skipped.append({"element_id": eid, "reason": "Cannot determine element location"})
                    continue
                planes.append({"eid": eid, "elem": elem, "tag_type": tag_type, "point": tag_point})

            if ctx["simular"]:
                haria = [
                    {
                        "accion": "etiquetar", "element_id": p["eid"],
                        "category": get_element_name(p["elem"].Category),
                        "tag_type": get_element_name(p["tag_type"]),
                        "view": get_element_name(target_view),
                    }
                    for p in planes
                ]
                return simulacion(haria, count=len(haria), skipped=skipped)

            tagged_ids = []
            with transaccion(doc, "Etiquetar {} elementos".format(len(planes))):
                for plan in planes:
                    try:
                        tag_type = plan["tag_type"]
                        if not tag_type.IsActive:
                            tag_type.Activate()
                            doc.Regenerate()
                        # IndependentTag.Create (Revit 2024+), 7-arg signature
                        tag = DB.IndependentTag.Create(
                            doc, tag_type.Id, target_view.Id, DB.Reference(plan["elem"]),
                            add_leader, tag_orientation, plan["point"],
                        )
                        if tag:
                            tagged_ids.append(get_element_id_value(tag))
                        else:
                            skipped.append({
                                "element_id": plan["eid"],
                                "reason": "Tag creation returned null — no tag type available for this category",
                            })
                    except Exception as tag_err:
                        skipped.append({"element_id": plan["eid"], "reason": "Tag failed: {}".format(str(tag_err))})

            resultado = resultado_creacion(doc, tagged_ids)
            resultado.update({
                "tagged_count": len(tagged_ids),
                "tag_ids": tagged_ids,
                "skipped": skipped,
                "message": "Tagged {} element{}, {} skipped".format(
                    len(tagged_ids), "s" if len(tagged_ids) != 1 else "", len(skipped)
                ),
            })
            return resultado

        return ejecutar(doc, "/tag_elements/", request, cuerpo)

    logger.info("Tag routes registered successfully")
