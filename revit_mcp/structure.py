# -*- coding: UTF-8 -*-
"""
Structure Module for Revit MCP
Handles grid creation and structural framing placement.

Todas las rutas pasan por escritura.ejecutar (copia, log, simular, IA:).
"""

from utils import (
    get_element_name, get_element_id_value, make_element_id, xyz_desde_mm, punto_a_mm,
    elementos_por_nombre, mapa_niveles, coleccion_niveles,
)
from seguridad import requiere_token
from escritura import ejecutar, transaccion, simulacion, EscrituraRechazada, resultado_creacion
from pyrevit import routes, revit, DB
import logging

logger = logging.getLogger(__name__)


def register_structure_routes(api):
    """Register all structure routes with the API"""

    @api.route("/create_grid/", methods=["POST"])
    @requiere_token
    def create_grid_handler(doc, request):
        """Create grid lines in the Revit model. Accepts `simular`."""

        def cuerpo(ctx):
            data = ctx["data"]
            grids = data.get("grids", [])
            if not grids:
                raise EscrituraRechazada("No grids provided", 400)

            existentes = elementos_por_nombre(
                DB.FilteredElementCollector(doc).OfClass(DB.Grid).WhereElementIsNotElementType().ToElements()
            )
            planes = []
            errors = []
            for i, grid_def in enumerate(grids):
                try:
                    start = xyz_desde_mm(grid_def.get("start_point", {}))
                    end = xyz_desde_mm(grid_def.get("end_point", {}))
                    if start.DistanceTo(end) < 0.001:
                        raise ValueError("Grid {}: zero length, skipped".format(i))
                    name = grid_def.get("name")
                    if name and name in existentes:
                        raise ValueError("Grid {}: a grid named '{}' already exists".format(i, name))
                    planes.append({"idx": i, "start": start, "end": end, "name": name})
                except ValueError as grid_err:
                    errors.append(str(grid_err))
                except Exception as grid_err:
                    errors.append("Grid {}: {}".format(i, str(grid_err)))

            if ctx["simular"]:
                haria = [
                    {
                        "accion": "crear", "element_type": "grid", "name": p["name"],
                        "start_mm": punto_a_mm(p["start"]), "end_mm": punto_a_mm(p["end"]),
                    }
                    for p in planes
                ]
                return simulacion(haria, count=len(haria), errors=errors)

            ids = []
            with transaccion(doc, "Crear rejillas"):
                for plan in planes:
                    try:
                        grid = DB.Grid.Create(doc, DB.Line.CreateBound(plan["start"], plan["end"]))
                        if plan["name"]:
                            try:
                                grid.Name = plan["name"]
                            except Exception as name_err:
                                errors.append(
                                    "Grid {}: could not set name '{}': {}".format(
                                        plan["idx"], plan["name"], str(name_err)
                                    )
                                )
                        ids.append(get_element_id_value(grid))
                    except Exception as grid_err:
                        errors.append("Grid {}: {}".format(plan["idx"], str(grid_err)))

            resultado = resultado_creacion(
                doc, ids, errors,
                {"message": "Created {} grid line{}".format(len(ids), "s" if len(ids) != 1 else "")},
            )
            for creado in resultado["creados"]:
                try:
                    creado["name"] = get_element_name(doc.GetElement(make_element_id(creado["id"])))
                except Exception:
                    pass
            return resultado

        return ejecutar(doc, "/create_grid/", request, cuerpo)

    @api.route("/create_framing/", methods=["POST"])
    @requiere_token
    def create_framing_handler(doc, request):
        """Create structural framing (beams) in the Revit model. Accepts `simular`."""

        def cuerpo(ctx):
            data = ctx["data"]
            elements = data.get("elements", [])
            if not elements:
                raise EscrituraRechazada("No elements provided", 400)

            framing_map = elementos_por_nombre(
                DB.FilteredElementCollector(doc)
                .OfCategory(DB.BuiltInCategory.OST_StructuralFraming)
                .OfClass(DB.FamilySymbol)
                .ToElements()
            )
            if not framing_map:
                raise EscrituraRechazada(
                    "No structural framing types found — load beam families into the project", 404
                )
            level_map = mapa_niveles(doc)
            niveles = list(coleccion_niveles(doc))

            planes = []
            errors = []
            for i, elem_def in enumerate(elements):
                try:
                    start = xyz_desde_mm(elem_def.get("start_point", {}))
                    end = xyz_desde_mm(elem_def.get("end_point", {}))
                    if start.DistanceTo(end) < 0.001:
                        raise ValueError("Element {}: beam has zero length, skipped".format(i))

                    type_name = elem_def.get("type_name")
                    symbol = framing_map.get(type_name) if type_name else None
                    if type_name and symbol is None:
                        raise ValueError(
                            "Element {}: framing type '{}' not found. Available: {}".format(
                                i, type_name, ", ".join(sorted(framing_map.keys())[:10])
                            )
                        )
                    if symbol is None:
                        symbol = list(framing_map.values())[0]

                    level_name = elem_def.get("level_name")
                    level = level_map.get(level_name) if level_name else None
                    if level_name and level is None:
                        raise ValueError(
                            "Element {}: Level '{}' not found. Available levels: {}".format(
                                i, level_name, ", ".join(sorted(level_map.keys()))
                            )
                        )
                    if level is None and niveles:
                        level = niveles[0]

                    # Place the beam at its level's elevation: the point z is an
                    # offset from the level (consistent with walls/floors).
                    level_elev = 0.0
                    try:
                        if level is not None:
                            level_elev = level.Elevation
                    except Exception:
                        level_elev = 0.0
                    if abs(level_elev) > 1e-9:
                        start = DB.XYZ(start.X, start.Y, start.Z + level_elev)
                        end = DB.XYZ(end.X, end.Y, end.Z + level_elev)

                    planes.append({
                        "idx": i, "start": start, "end": end, "symbol": symbol,
                        "level": level, "name": elem_def.get("name", ""),
                    })
                except ValueError as elem_err:
                    errors.append(str(elem_err))
                except Exception as elem_err:
                    errors.append("Element {}: {}".format(i, str(elem_err)))

            if ctx["simular"]:
                haria = [
                    {
                        "accion": "crear", "element_type": "beam",
                        "type": get_element_name(p["symbol"]),
                        "level": get_element_name(p["level"]) if p["level"] else None,
                        "start_mm": punto_a_mm(p["start"]), "end_mm": punto_a_mm(p["end"]),
                    }
                    for p in planes
                ]
                return simulacion(haria, count=len(haria), errors=errors)

            ids = []
            with transaccion(doc, "Crear vigas"):
                for plan in planes:
                    try:
                        symbol = plan["symbol"]
                        if not symbol.IsActive:
                            symbol.Activate()
                            doc.Regenerate()
                        beam = doc.Create.NewFamilyInstance(
                            DB.Line.CreateBound(plan["start"], plan["end"]),
                            symbol,
                            plan["level"],
                            DB.Structure.StructuralType.Beam,
                        )
                        ids.append(get_element_id_value(beam))
                    except Exception as elem_err:
                        errors.append("Element {}: {}".format(plan["idx"], str(elem_err)))

            return resultado_creacion(
                doc, ids, errors,
                {"message": "Created {} structural framing element{}".format(
                    len(ids), "s" if len(ids) != 1 else "")},
            )

        return ejecutar(doc, "/create_framing/", request, cuerpo)

    logger.info("Structure routes registered successfully")
