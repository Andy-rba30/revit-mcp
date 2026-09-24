# -*- coding: UTF-8 -*-
"""
Building Module for Revit MCP
Handles creation of line-based elements (walls, beams), surface-based
elements (floors, roofs, ceilings), and levels.

Todas las rutas pasan por escritura.ejecutar: copia de seguridad, log,
`simular`, transaccion "IA: ..." y verificacion de lo creado.
"""

from utils import (
    get_element_name, get_element_id_value, make_element_id, xyz_desde_mm, punto_a_mm,
    elementos_por_nombre, mapa_niveles, nivel_mas_bajo, MM_TO_FEET,
)
from seguridad import requiere_token
from escritura import ejecutar, transaccion, simulacion, EscrituraRechazada, resultado_creacion
from pyrevit import routes, revit, DB
from System.Collections.Generic import List
import logging

logger = logging.getLogger(__name__)


def _nivel_de(elem, level_map, idx):
    """Nivel pedido (o el mas bajo). Lanza ValueError si no existe."""
    level_name = elem.get("level_name")
    if level_name:
        level = level_map.get(level_name)
        if not level:
            raise ValueError(
                "Element {}: Level '{}' not found. Available levels: {}".format(
                    idx, level_name, ", ".join(sorted(level_map.keys()))
                )
            )
        return level
    return nivel_mas_bajo(level_map)


def _tipo_de(elem, mapa_tipos, idx, etiqueta, preferido=None):
    """Tipo pedido por nombre o el primero disponible. Lanza ValueError."""
    type_name = elem.get("type_name")
    if type_name:
        tipo = mapa_tipos.get(type_name)
        if not tipo:
            raise ValueError(
                "Element {}: {} type '{}' not found. Available types: {}".format(
                    idx, etiqueta, type_name, ", ".join(sorted(mapa_tipos.keys())[:10])
                )
            )
        return tipo
    if not mapa_tipos:
        raise ValueError(
            "Element {}: No {} types available — load {} families into the project".format(
                idx, etiqueta.lower(), etiqueta.lower()
            )
        )
    if preferido is not None:
        return preferido
    return list(mapa_tipos.values())[0]


def _segmentos(boundary, idx):
    """Normaliza el contorno (puntos o segmentos) a [{"p0", "p1"}] cerrado."""
    if len(boundary) < 3:
        raise ValueError(
            "Element {}: Boundary requires at least 3 points/segments, got {}".format(idx, len(boundary))
        )
    # Point format: [{"x","y","z"}, ...]  /  Segment format: [{"p0":..,"p1":..}, ...]
    if "x" in boundary[0] or "y" in boundary[0]:
        points = list(boundary)
        if len(points) > 1:
            f, l = points[0], points[-1]
            same = (
                abs(float(f.get("x", 0)) - float(l.get("x", 0))) < 0.001
                and abs(float(f.get("y", 0)) - float(l.get("y", 0))) < 0.001
                and abs(float(f.get("z", 0)) - float(l.get("z", 0))) < 0.001
            )
            if same:
                points = points[:-1]
        boundary = []
        for pi in range(len(points)):
            boundary.append({"p0": points[pi], "p1": points[(pi + 1) % len(points)]})

    first_p0 = boundary[0].get("p0", {})
    last_p1 = boundary[-1].get("p1", {})
    fp = (float(first_p0.get("x", 0)), float(first_p0.get("y", 0)), float(first_p0.get("z", 0)))
    lp = (float(last_p1.get("x", 0)), float(last_p1.get("y", 0)), float(last_p1.get("z", 0)))
    dist = ((fp[0] - lp[0]) ** 2 + (fp[1] - lp[1]) ** 2 + (fp[2] - lp[2]) ** 2) ** 0.5
    if dist > 1.0:  # tolerance of 1mm
        raise ValueError(
            "Element {}: Boundary must form a closed polygon — last point {} does not match first point {}".format(
                idx, lp, fp
            )
        )
    return boundary


def register_building_routes(api):
    """Register all building creation routes with the API"""

    @api.route("/create_line/", methods=["POST"])
    @requiere_token
    def create_line_based(doc, request):
        """
        Create line-based elements (walls, beams) in the Revit model.
        Supports batch creation via elements array. Accepts `simular`.
        """

        def cuerpo(ctx):
            data = ctx["data"]
            elements = data.get("elements", [])
            if not elements:
                raise EscrituraRechazada(
                    "No elements provided — pass an array of element definitions", 400
                )

            level_map = mapa_niveles(doc)
            if not level_map:
                raise EscrituraRechazada(
                    "No levels found in the project — create levels first", 404
                )
            wall_type_map = elementos_por_nombre(
                DB.FilteredElementCollector(doc).OfClass(DB.WallType).ToElements()
            )
            beam_type_map = elementos_por_nombre(
                DB.FilteredElementCollector(doc)
                .OfCategory(DB.BuiltInCategory.OST_StructuralFraming)
                .OfClass(DB.FamilySymbol)
                .ToElements()
            )

            # Fase 1: validar y resolver todo sin abrir transaccion
            planes = []
            errors = []
            for idx, elem in enumerate(elements):
                try:
                    element_type = elem.get("element_type")
                    if not element_type:
                        raise ValueError("Element {}: element_type is required".format(idx))
                    if element_type not in ("wall", "beam"):
                        raise ValueError(
                            "Element {}: element_type '{}' not supported — use 'wall' or 'beam'".format(
                                idx, element_type
                            )
                        )
                    start = elem.get("start_point")
                    end = elem.get("end_point")
                    if not start or not end:
                        raise ValueError("Element {}: start_point and end_point are required".format(idx))
                    sp = xyz_desde_mm(start)
                    ep = xyz_desde_mm(end)
                    if sp.DistanceTo(ep) < 0.001:
                        raise ValueError(
                            "Element {}: Start and end points must be different (zero-length element)".format(idx)
                        )
                    level = _nivel_de(elem, level_map, idx)
                    plan = {
                        "idx": idx,
                        "element_type": element_type,
                        "name": elem.get("name", ""),
                        "sp": sp,
                        "ep": ep,
                        "level": level,
                    }
                    if element_type == "wall":
                        plan["tipo"] = _tipo_de(elem, wall_type_map, idx, "Wall")
                        plan["height"] = float(elem.get("height", 3000)) * MM_TO_FEET
                        plan["offset"] = float(elem.get("offset", 0)) * MM_TO_FEET
                        plan["structural"] = bool(elem.get("structural", False))
                    else:
                        plan["tipo"] = _tipo_de(elem, beam_type_map, idx, "Beam")
                    planes.append(plan)
                except ValueError as elem_err:
                    errors.append(str(elem_err))
                except Exception as elem_err:
                    errors.append("Element {}: {}".format(idx, str(elem_err)))

            if ctx["simular"]:
                haria = []
                for plan in planes:
                    haria.append({
                        "accion": "crear",
                        "element_type": plan["element_type"],
                        "type": get_element_name(plan["tipo"]),
                        "level": get_element_name(plan["level"]),
                        "start_mm": punto_a_mm(plan["sp"]),
                        "end_mm": punto_a_mm(plan["ep"]),
                        "height_mm": round(plan["height"] / MM_TO_FEET, 1) if "height" in plan else None,
                    })
                return simulacion(haria, count=len(haria), errors=errors)

            # Fase 2: crear dentro de una sola transaccion
            ids = []
            with transaccion(doc, "Crear muros/vigas"):
                for plan in planes:
                    try:
                        line = DB.Line.CreateBound(plan["sp"], plan["ep"])
                        if plan["element_type"] == "wall":
                            wall = DB.Wall.Create(
                                doc, line, plan["tipo"].Id, plan["level"].Id,
                                plan["height"], plan["offset"], False, plan["structural"],
                            )
                            ids.append(get_element_id_value(wall))
                        else:
                            symbol = plan["tipo"]
                            if not symbol.IsActive:
                                symbol.Activate()
                                doc.Regenerate()
                            beam = doc.Create.NewFamilyInstance(
                                line, symbol, plan["level"], DB.Structure.StructuralType.Beam
                            )
                            ids.append(get_element_id_value(beam))
                    except Exception as elem_err:
                        errors.append("Element {}: {}".format(plan["idx"], str(elem_err)))

            return resultado_creacion(
                doc, ids, errors, {"message": "Created {} element(s)".format(len(ids))}
            )

        return ejecutar(doc, "/create_line/", request, cuerpo)

    @api.route("/create_surface/", methods=["POST"])
    @requiere_token
    def create_surface_based(doc, request):
        """
        Create surface-based elements (floors, roofs, ceilings) in the Revit model.
        Supports batch creation via elements array. Accepts `simular`.
        """

        def cuerpo(ctx):
            data = ctx["data"]
            elements = data.get("elements", [])
            if not elements:
                raise EscrituraRechazada(
                    "No elements provided — pass an array of element definitions", 400
                )

            level_map = mapa_niveles(doc)
            if not level_map:
                raise EscrituraRechazada("No levels found — create levels first", 404)

            floor_type_map = elementos_por_nombre(
                DB.FilteredElementCollector(doc).OfClass(DB.FloorType).ToElements()
            )
            roof_type_map = elementos_por_nombre(
                DB.FilteredElementCollector(doc).OfClass(DB.RoofType).ToElements()
            )
            # Prefer a real (non-foundation) floor type as default: the raw first
            # type often lands on "Foundation Slab" (a Structural Foundation).
            floor_por_defecto = None
            for ft in floor_type_map.values():
                try:
                    if not getattr(ft, "IsFoundationSlab", False):
                        floor_por_defecto = ft
                        break
                except Exception:
                    continue

            planes = []
            errors = []
            for idx, elem in enumerate(elements):
                try:
                    element_type = elem.get("element_type")
                    if not element_type:
                        raise ValueError("Element {}: element_type is required".format(idx))
                    if element_type not in ("floor", "roof", "ceiling"):
                        raise ValueError(
                            "Element {}: element_type must be 'floor', 'roof', or 'ceiling'".format(idx)
                        )
                    boundary = _segmentos(elem.get("boundary", []), idx)
                    level = _nivel_de(elem, level_map, idx)
                    puntos = []
                    for seg in boundary:
                        puntos.append((xyz_desde_mm(seg.get("p0", {})), xyz_desde_mm(seg.get("p1", {}))))
                    plan = {
                        "idx": idx,
                        "element_type": element_type,
                        "name": elem.get("name", ""),
                        "level": level,
                        "puntos": puntos,
                        "offset": float(elem.get("offset", 0)),
                    }
                    if element_type == "roof":
                        plan["tipo"] = _tipo_de(elem, roof_type_map, idx, "Roof")
                    else:
                        plan["tipo"] = _tipo_de(elem, floor_type_map, idx, "Floor", floor_por_defecto)
                    planes.append(plan)
                except ValueError as elem_err:
                    errors.append(str(elem_err))
                except Exception as elem_err:
                    errors.append("Element {}: {}".format(idx, str(elem_err)))

            if ctx["simular"]:
                haria = []
                for plan in planes:
                    haria.append({
                        "accion": "crear",
                        "element_type": plan["element_type"],
                        "type": get_element_name(plan["tipo"]),
                        "level": get_element_name(plan["level"]),
                        "segmentos": len(plan["puntos"]),
                        "primer_punto_mm": punto_a_mm(plan["puntos"][0][0]),
                        "offset_mm": plan["offset"],
                    })
                return simulacion(haria, count=len(haria), errors=errors)

            ids = []
            with transaccion(doc, "Crear suelos/cubiertas"):
                for plan in planes:
                    try:
                        if plan["element_type"] == "roof":
                            curve_array = DB.CurveArray()
                            for s, e in plan["puntos"]:
                                curve_array.Append(DB.Line.CreateBound(s, e))
                            import clr
                            model_curves = clr.Reference[DB.ModelCurveArray]()
                            roof = doc.Create.NewFootPrintRoof(
                                curve_array, plan["level"], plan["tipo"], model_curves
                            )
                            ids.append(get_element_id_value(roof))
                        else:
                            curve_loop = DB.CurveLoop()
                            for s, e in plan["puntos"]:
                                curve_loop.Append(DB.Line.CreateBound(s, e))
                            curve_loops = List[DB.CurveLoop]()
                            curve_loops.Add(curve_loop)
                            floor = DB.Floor.Create(doc, curve_loops, plan["tipo"].Id, plan["level"].Id)
                            if plan["offset"] != 0:
                                offset_param = floor.get_Parameter(
                                    DB.BuiltInParameter.FLOOR_HEIGHTABOVELEVEL_PARAM
                                )
                                if offset_param and not offset_param.IsReadOnly:
                                    offset_param.Set(plan["offset"] * MM_TO_FEET)
                            ids.append(get_element_id_value(floor))
                    except Exception as elem_err:
                        errors.append("Element {}: {}".format(plan["idx"], str(elem_err)))

            return resultado_creacion(
                doc, ids, errors, {"message": "Created {} element(s)".format(len(ids))}
            )

        return ejecutar(doc, "/create_surface/", request, cuerpo)

    @api.route("/create_level/", methods=["POST"])
    @requiere_token
    def create_level_handler(doc, request):
        """
        Create building levels at specified elevations.
        Supports batch creation via levels array. Accepts `simular`.
        """

        def cuerpo(ctx):
            data = ctx["data"]
            levels = data.get("levels", [])
            if not levels:
                raise EscrituraRechazada(
                    "No levels provided — pass an array of level definitions", 400
                )

            existentes = mapa_niveles(doc)
            planes = []
            errors = []
            for idx, lv in enumerate(levels):
                try:
                    elevation_mm = lv.get("elevation")
                    if elevation_mm is None:
                        raise ValueError("Level {}: elevation is required".format(idx))
                    name = lv.get("name")
                    if name and name in existentes:
                        raise ValueError("Level {}: a level named '{}' already exists".format(idx, name))
                    planes.append({
                        "idx": idx,
                        "elevation_mm": float(elevation_mm),
                        "name": str(name) if name else None,
                    })
                except ValueError as lv_err:
                    errors.append(str(lv_err))
                except Exception as lv_err:
                    errors.append("Level {}: {}".format(idx, str(lv_err)))

            if ctx["simular"]:
                haria = [
                    {"accion": "crear", "element_type": "level", "name": p["name"], "elevation_mm": p["elevation_mm"]}
                    for p in planes
                ]
                return simulacion(haria, count=len(haria), errors=errors)

            ids = []
            with transaccion(doc, "Crear niveles"):
                for plan in planes:
                    try:
                        new_level = DB.Level.Create(doc, plan["elevation_mm"] * MM_TO_FEET)
                        if plan["name"]:
                            new_level.Name = plan["name"]
                        ids.append(get_element_id_value(new_level))
                    except Exception as lv_err:
                        errors.append("Level {}: {}".format(plan["idx"], str(lv_err)))

            resultado = resultado_creacion(
                doc, ids, errors, {"message": "Created {} level(s)".format(len(ids))}
            )
            # Verificacion adicional: elevacion real de cada nivel creado
            for creado in resultado["creados"]:
                try:
                    nivel = doc.GetElement(make_element_id(creado["id"]))
                    creado["elevation_mm"] = round(nivel.Elevation / MM_TO_FEET, 1)
                    creado["name"] = get_element_name(nivel)
                except Exception:
                    pass
            return resultado

        return ejecutar(doc, "/create_level/", request, cuerpo)

    logger.info("Building routes registered successfully")
