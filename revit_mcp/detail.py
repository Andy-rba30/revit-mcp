# -*- coding: UTF-8 -*-
"""
Detail Module for Revit MCP
Handles detail line creation for view-specific annotation.

Pasa por escritura.ejecutar (copia, log, simular, IA:).
"""

from utils import get_element_name, get_element_id_value, xyz_desde_mm, punto_a_mm, buscar_vista
from seguridad import requiere_token
from escritura import ejecutar, transaccion, simulacion, EscrituraRechazada, resultado_creacion
from pyrevit import routes, revit, DB
import logging

logger = logging.getLogger(__name__)


def register_detail_routes(api):
    """Register all detail routes with the API"""

    @api.route("/create_detail_line/", methods=["POST"])
    @requiere_token
    def create_detail_line_handler(doc, request):
        """Create a detail line in a view. Accepts `simular`."""

        def cuerpo(ctx):
            data = ctx["data"]
            start_point = data.get("start_point")
            end_point = data.get("end_point")
            if not start_point or not end_point:
                raise EscrituraRechazada("start_point and end_point are required", 400)

            view_name = data.get("view_name")
            if view_name:
                target_view = buscar_vista(doc, view_name)
                if not target_view:
                    raise EscrituraRechazada("View '{}' not found".format(view_name), 404)
            else:
                target_view = doc.ActiveView

            allowed_types = [
                DB.ViewType.FloorPlan, DB.ViewType.CeilingPlan,
                DB.ViewType.Section, DB.ViewType.Detail,
                DB.ViewType.Elevation, DB.ViewType.DraftingView,
                DB.ViewType.AreaPlan,
            ]
            try:
                if target_view.ViewType not in allowed_types:
                    raise EscrituraRechazada(
                        "Cannot create detail line — the specified view is not a plan or detail view.", 400
                    )
            except EscrituraRechazada:
                raise
            except Exception:
                pass

            try:
                start = xyz_desde_mm(start_point)
                end = xyz_desde_mm(end_point)
            except ValueError as error:
                raise EscrituraRechazada(str(error), 400)
            if start.DistanceTo(end) < 0.001:
                raise EscrituraRechazada("Start and end points must be different", 400)

            line_style = data.get("line_style")
            estilo = None
            if line_style:
                try:
                    line_cat = doc.Settings.Categories.get_Item(DB.BuiltInCategory.OST_Lines)
                    for sub_cat in line_cat.SubCategories:
                        if get_element_name(sub_cat) == line_style:
                            estilo = sub_cat.GetGraphicsStyle(DB.GraphicsStyleType.Projection)
                            break
                except Exception as style_err:
                    logger.debug("Could not resolve line style: {}".format(str(style_err)))
                if estilo is None:
                    raise EscrituraRechazada("Line style '{}' not found".format(line_style), 404)

            actual_view_name = get_element_name(target_view)
            if ctx["simular"]:
                return simulacion([{
                    "accion": "crear", "element_type": "detail_line", "view": actual_view_name,
                    "start_mm": punto_a_mm(start), "end_mm": punto_a_mm(end), "line_style": line_style,
                }])

            with transaccion(doc, "Crear linea de detalle"):
                detail_curve = doc.Create.NewDetailCurve(target_view, DB.Line.CreateBound(start, end))
                if not detail_curve:
                    raise EscrituraRechazada("Failed to create detail line", 500)
                if estilo is not None:
                    detail_curve.LineStyle = estilo
                line_id = get_element_id_value(detail_curve)

            resultado = resultado_creacion(doc, [line_id])
            resultado["line_id"] = line_id
            resultado["view_name"] = actual_view_name
            resultado["message"] = "Created detail line in view '{}'".format(actual_view_name)
            return resultado

        return ejecutar(doc, "/create_detail_line/", request, cuerpo)

    logger.info("Detail routes registered successfully")
