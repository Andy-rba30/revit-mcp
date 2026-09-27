# -*- coding: UTF-8 -*-
"""
Structure Module for Revit MCP
Handles grid creation and structural framing placement.

Todas las rutas pasan por escritura.ejecutar (copia, log, simular, IA:).

0.4.0: planificar_rejilla / crear_rejilla y mapas_vigas / planificar_viga /
crear_viga los reutiliza lotes.py (/create_elements/).
"""

from utils import (
    elevacion_interna,
    get_element_name, get_element_id_value, make_element_id, xyz_desde_mm, punto_a_mm,
    elementos_por_nombre, mapa_niveles, coleccion_niveles,
)
from seguridad import requiere_token
from escritura import ejecutar, transaccion, simulacion, EscrituraRechazada, resultado_creacion
from pyrevit import routes, revit, DB
import logging

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Rejillas
# ---------------------------------------------------------------------------
def rejillas_existentes(doc):
    """{nombre: DB.Grid} de las rejillas del documento."""
    return elementos_por_nombre(
        DB.FilteredElementCollector(doc).OfClass(DB.Grid).WhereElementIsNotElementType().ToElements()
    )


def planificar_rejilla(grid_def, i, existentes):
    """Valida {start_point, end_point, name}. Lanza ValueError. Devuelve el plan."""
    start = xyz_desde_mm(grid_def.get("start_point", {}))
    end = xyz_desde_mm(grid_def.get("end_point", {}))
    if start.DistanceTo(end) < 0.001:
        raise ValueError("Grid {}: zero length, skipped".format(i))
    name = grid_def.get("name")
    if name and name in existentes:
        raise ValueError("Grid {}: a grid named '{}' already exists".format(i, name))
    return {"idx": i, "kind": "grid", "start": start, "end": end, "name": name}


def haria_rejilla(plan):
    return {
        "accion": "crear", "element_type": "grid", "name": plan["name"],
        "start_mm": punto_a_mm(plan["start"]), "end_mm": punto_a_mm(plan["end"]),
    }


def crear_rejilla(doc, start, end, name=None):
    """DB.Grid.Create entre dos XYZ (pies) y, si se da, el nombre.

    Devuelve (grid, error_de_nombre): el error es None si el nombre se aplico
    (o no se pidio); si Revit lo rechaza (nombre repetido), la rejilla queda
    creada con su nombre automatico y se devuelve el texto del error.
    Lo reutilizan macros.create_grid_and_levels y lotes.create_elements."""
    grid = DB.Grid.Create(doc, DB.Line.CreateBound(start, end))
    error = None
    if name:
        try:
            grid.Name = name
        except Exception as name_err:
            error = str(name_err)
    return grid, error


# ---------------------------------------------------------------------------
# Vigas
# ---------------------------------------------------------------------------
def mapas_vigas(doc):
    """Tipos de viga, niveles por nombre y lista de niveles (una lectura por lote)."""
    return {
        "framing": elementos_por_nombre(
            DB.FilteredElementCollector(doc)
            .OfCategory(DB.BuiltInCategory.OST_StructuralFraming)
            .OfClass(DB.FamilySymbol)
            .ToElements()
        ),
        "levels": mapa_niveles(doc),
        "niveles": list(coleccion_niveles(doc)),
    }


def planificar_viga(elem_def, i, mapas):
    """Valida {start_point, end_point, type_name, level_name}; z es desfase desde el nivel. Lanza ValueError."""
    framing_map = mapas["framing"]
    if not framing_map:
        raise ValueError("Element {}: No structural framing types found — load beam families into the project".format(i))
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

    level_name = elem_def.get("level_name") or elem_def.get("level")
    level = mapas["levels"].get(level_name) if level_name else None
    if level_name and level is None:
        raise ValueError(
            "Element {}: Level '{}' not found. Available levels: {}".format(
                i, level_name, ", ".join(sorted(mapas["levels"].keys()))
            )
        )
    if level is None and mapas["niveles"]:
        level = mapas["niveles"][0]

    # Place the beam at its level's elevation: the point z is an
    # offset from the level (consistent with walls/floors).
    level_elev = 0.0
    try:
        if level is not None:
            level_elev = elevacion_interna(level)
    except Exception:
        level_elev = 0.0
    if abs(level_elev) > 1e-9:
        start = DB.XYZ(start.X, start.Y, start.Z + level_elev)
        end = DB.XYZ(end.X, end.Y, end.Z + level_elev)

    return {
        "idx": i, "kind": "beam", "start": start, "end": end, "symbol": symbol,
        "level": level, "name": elem_def.get("name", ""),
    }


def haria_viga(plan):
    return {
        "accion": "crear", "element_type": "beam",
        "type": get_element_name(plan["symbol"]),
        "level": get_element_name(plan["level"]) if plan["level"] else None,
        "start_mm": punto_a_mm(plan["start"]), "end_mm": punto_a_mm(plan["end"]),
    }


def crear_viga(doc, plan):
    """NewFamilyInstance(line, symbol, level, StructuralType.Beam). Dentro de una transaccion."""
    symbol = plan["symbol"]
    if not symbol.IsActive:
        symbol.Activate()
        doc.Regenerate()
    return doc.Create.NewFamilyInstance(
        DB.Line.CreateBound(plan["start"], plan["end"]),
        symbol,
        plan["level"],
        DB.Structure.StructuralType.Beam,
    )


# ---------------------------------------------------------------------------
# Rutas
# ---------------------------------------------------------------------------
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

            existentes = rejillas_existentes(doc)
            planes = []
            errors = []
            for i, grid_def in enumerate(grids):
                try:
                    planes.append(planificar_rejilla(grid_def, i, existentes))
                except ValueError as grid_err:
                    errors.append(str(grid_err))
                except Exception as grid_err:
                    errors.append("Grid {}: {}".format(i, str(grid_err)))

            if ctx["simular"]:
                haria = [haria_rejilla(p) for p in planes]
                return simulacion(haria, count=len(haria), errors=errors)

            ids = []
            with transaccion(doc, "Crear rejillas"):
                for plan in planes:
                    try:
                        grid, error_nombre = crear_rejilla(doc, plan["start"], plan["end"], plan["name"])
                        if error_nombre:
                            errors.append(
                                "Grid {}: could not set name '{}': {}".format(
                                    plan["idx"], plan["name"], error_nombre
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

            mapas = mapas_vigas(doc)
            if not mapas["framing"]:
                raise EscrituraRechazada(
                    "No structural framing types found — load beam families into the project", 404
                )

            planes = []
            errors = []
            for i, elem_def in enumerate(elements):
                try:
                    planes.append(planificar_viga(elem_def, i, mapas))
                except ValueError as elem_err:
                    errors.append(str(elem_err))
                except Exception as elem_err:
                    errors.append("Element {}: {}".format(i, str(elem_err)))

            if ctx["simular"]:
                haria = [haria_viga(p) for p in planes]
                return simulacion(haria, count=len(haria), errors=errors)

            ids = []
            with transaccion(doc, "Crear vigas"):
                for plan in planes:
                    try:
                        ids.append(get_element_id_value(crear_viga(doc, plan)))
                    except Exception as elem_err:
                        errors.append("Element {}: {}".format(plan["idx"], str(elem_err)))

            return resultado_creacion(
                doc, ids, errors,
                {"message": "Created {} structural framing element{}".format(
                    len(ids), "s" if len(ids) != 1 else "")},
            )

        return ejecutar(doc, "/create_framing/", request, cuerpo)

    logger.info("Structure routes registered successfully")
