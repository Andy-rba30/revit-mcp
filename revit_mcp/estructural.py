# -*- coding: UTF-8 -*-
"""
Estructural Module for Revit MCP
Pilares estructurales, cimentaciones y huecos.

  POST /create_column/      columns[] {point (mm), base_level, top_level, type_name, rotation}
  POST /create_foundation/  foundations[] {point, level, type_name} (zapata aislada)
                                          {wall_id | curve, type_name} (zapata corrida, WallFoundation)
                                          {boundary, level, type_name} (losa de cimentacion)
  POST /create_opening/     host_id*, points[] (mm) -> doc.Create.NewOpening en muro, suelo o cubierta

Todas pasan por escritura.ejecutar (copia, log, simular, IA:, creados).
"""

from utils import (
    get_element_name, get_element_id_value, make_element_id, xyz_desde_mm, punto_a_mm,
    mapa_niveles, buscar_tipo_por_nombre, etiqueta_tipo, MM_TO_FEET,
)
from seguridad import requiere_token
from escritura import ejecutar, transaccion, simulacion, EscrituraRechazada, resultado_creacion, describir_elemento, comprobar_alcance
from pyrevit import routes, revit, DB
from System.Collections.Generic import List
import math
import logging

logger = logging.getLogger(__name__)


def _simbolos(doc, categoria):
    return list(
        DB.FilteredElementCollector(doc)
        .OfCategory(categoria)
        .OfClass(DB.FamilySymbol)
        .ToElements()
    )


def _elegir(candidatos, nombre, etiqueta, idx):
    """Tipo por nombre (o el primero si no se da nombre). Lanza ValueError."""
    if not candidatos:
        raise ValueError("{}: no {} types loaded in the project".format(idx, etiqueta))
    if not nombre:
        return candidatos[0]
    coincidencias = buscar_tipo_por_nombre(candidatos, nombre)
    if not coincidencias:
        raise ValueError("{}: {} type '{}' not found. Available: {}".format(
            idx, etiqueta, nombre, ", ".join(sorted(set(etiqueta_tipo(t) for t in candidatos))[:15])))
    if len(coincidencias) > 1:
        raise ValueError("{}: {} type '{}' is ambiguous, use 'Family: Type'".format(idx, etiqueta, nombre))
    return coincidencias[0]


def _nivel(level_map, nombre, idx, obligatorio=True):
    if not nombre:
        if obligatorio:
            raise ValueError("{}: level is required".format(idx))
        return None
    nivel = level_map.get(nombre)
    if nivel is None:
        raise ValueError("{}: level '{}' not found. Available: {}".format(
            idx, nombre, ", ".join(sorted(level_map.keys()))))
    return nivel


def _activar(symbol, doc):
    try:
        if not symbol.IsActive:
            symbol.Activate()
            doc.Regenerate()
    except Exception:
        pass


def _muro_bajo_curva(doc, start, end):
    """Muro cuya linea de ubicacion pasa por el punto medio de la curva (< 100 mm)."""
    medio = DB.XYZ((start.X + end.X) / 2.0, (start.Y + end.Y) / 2.0, (start.Z + end.Z) / 2.0)
    mejor = None
    mejor_dist = None
    for muro in DB.FilteredElementCollector(doc).OfClass(DB.Wall).WhereElementIsNotElementType():
        try:
            curva = muro.Location.Curve
            d = curva.Distance(medio)
            if mejor_dist is None or d < mejor_dist:
                mejor_dist = d
                mejor = muro
        except Exception:
            continue
    if mejor is not None and mejor_dist is not None and mejor_dist * 304.8 <= 100.0:
        return mejor
    return None


def register_estructural_routes(api):
    """Register structural column / foundation / opening routes."""

    @api.route("/create_column/", methods=["POST"])
    @requiere_token
    def create_structural_column(doc, request):
        """Pilares estructurales (StructuralType.Column). Acepta `simular`."""

        def cuerpo(ctx):
            data = ctx["data"]
            columns = data.get("columns", [])
            if not columns:
                raise EscrituraRechazada("columns is required and must not be empty", 400)
            comprobar_alcance(data, len(columns), "pilares a crear")
            simbolos = _simbolos(doc, DB.BuiltInCategory.OST_StructuralColumns)
            if not simbolos:
                raise EscrituraRechazada(
                    "No structural column families loaded — load a column family (load_family) first", 404
                )
            level_map = mapa_niveles(doc)
            if not level_map:
                raise EscrituraRechazada("No levels found in the project", 404)

            planes = []
            errors = []
            for idx, col in enumerate(columns):
                try:
                    punto = col.get("point")
                    if not punto:
                        raise ValueError("Column {}: point is required".format(idx))
                    xyz = xyz_desde_mm(punto)
                    base = _nivel(level_map, col.get("base_level"), "Column {}".format(idx))
                    top = _nivel(level_map, col.get("top_level"), "Column {}".format(idx), obligatorio=False)
                    if top is not None and top.Elevation <= base.Elevation:
                        raise ValueError("Column {}: top_level must be above base_level".format(idx))
                    symbol = _elegir(simbolos, col.get("type_name"), "structural column", "Column {}".format(idx))
                    rotation = float(col.get("rotation", 0) or 0)
                    # El punto se coloca a la cota del nivel base + z pedido (z = desfase)
                    xyz = DB.XYZ(xyz.X, xyz.Y, base.Elevation + xyz.Z)
                    planes.append({
                        "idx": idx, "xyz": xyz, "base": base, "top": top, "symbol": symbol,
                        "rotation": rotation, "top_offset": col.get("top_offset"),
                    })
                except ValueError as error:
                    errors.append(str(error))
                except Exception as error:
                    errors.append("Column {}: {}".format(idx, str(error)))

            if ctx["simular"]:
                return simulacion(
                    [{
                        "accion": "crear", "element_type": "structural_column",
                        "type": etiqueta_tipo(p["symbol"]), "base_level": get_element_name(p["base"]),
                        "top_level": get_element_name(p["top"]) if p["top"] else None,
                        "point_mm": punto_a_mm(p["xyz"]), "rotation_deg": p["rotation"],
                    } for p in planes],
                    count=len(planes), errors=errors,
                )

            ids = []
            with transaccion(doc, "Crear {} pilares".format(len(planes))):
                for plan in planes:
                    try:
                        _activar(plan["symbol"], doc)
                        columna = doc.Create.NewFamilyInstance(
                            plan["xyz"], plan["symbol"], plan["base"], DB.Structure.StructuralType.Column
                        )
                        if plan["top"] is not None:
                            p_top = columna.get_Parameter(DB.BuiltInParameter.FAMILY_TOP_LEVEL_PARAM)
                            if p_top and not p_top.IsReadOnly:
                                p_top.Set(plan["top"].Id)
                            if plan["top_offset"] is not None:
                                p_off = columna.get_Parameter(DB.BuiltInParameter.FAMILY_TOP_LEVEL_OFFSET_PARAM)
                                if p_off and not p_off.IsReadOnly:
                                    p_off.Set(float(plan["top_offset"]) * MM_TO_FEET)
                        if abs(plan["rotation"]) > 1e-9:
                            eje = DB.Line.CreateBound(plan["xyz"], plan["xyz"].Add(DB.XYZ(0, 0, 1)))
                            DB.ElementTransformUtils.RotateElement(
                                doc, columna.Id, eje, math.radians(plan["rotation"])
                            )
                        ids.append(get_element_id_value(columna))
                    except Exception as error:
                        errors.append("Column {}: {}".format(plan["idx"], str(error)))

            resultado = resultado_creacion(
                doc, ids, errors, {"message": "Created {} structural column(s)".format(len(ids))}
            )
            for creado in resultado["creados"]:
                try:
                    columna = doc.GetElement(make_element_id(creado["id"]))
                    p_top = columna.get_Parameter(DB.BuiltInParameter.FAMILY_TOP_LEVEL_PARAM)
                    if p_top:
                        top = doc.GetElement(p_top.AsElementId())
                        creado["top_level"] = get_element_name(top) if top is not None else None
                    creado["point_mm"] = punto_a_mm(columna.Location.Point)
                except Exception:
                    pass
            return resultado

        return ejecutar(doc, "/create_column/", request, cuerpo)

    @api.route("/create_foundation/", methods=["POST"])
    @requiere_token
    def create_foundation(doc, request):
        """Cimentaciones: zapata aislada, corrida (WallFoundation) o losa. Acepta `simular`."""

        def cuerpo(ctx):
            data = ctx["data"]
            foundations = data.get("foundations", [])
            if not foundations:
                raise EscrituraRechazada("foundations is required and must not be empty", 400)
            comprobar_alcance(data, len(foundations), "cimentaciones a crear")
            level_map = mapa_niveles(doc)
            zapatas = _simbolos(doc, DB.BuiltInCategory.OST_StructuralFoundation)
            corridas = []
            try:
                corridas = list(DB.FilteredElementCollector(doc).OfClass(DB.WallFoundationType).ToElements())
            except Exception:
                corridas = []
            losas = []
            try:
                for ft in DB.FilteredElementCollector(doc).OfClass(DB.FloorType).ToElements():
                    try:
                        if ft.IsFoundationSlab:
                            losas.append(ft)
                    except Exception:
                        continue
            except Exception:
                losas = []

            planes = []
            errors = []
            for idx, f in enumerate(foundations):
                etiqueta = "Foundation {}".format(idx)
                try:
                    if f.get("point"):
                        xyz = xyz_desde_mm(f["point"])
                        nivel = _nivel(level_map, f.get("level") or f.get("level_name"), etiqueta)
                        symbol = _elegir(zapatas, f.get("type_name"), "structural foundation", etiqueta)
                        xyz = DB.XYZ(xyz.X, xyz.Y, nivel.Elevation + xyz.Z)
                        planes.append({"idx": idx, "kind": "isolated", "xyz": xyz, "level": nivel,
                                       "symbol": symbol, "rotation": float(f.get("rotation", 0) or 0)})
                    elif f.get("wall_id") is not None or f.get("curve"):
                        if f.get("wall_id") is not None:
                            muro = doc.GetElement(make_element_id(f["wall_id"]))
                            if muro is None or not isinstance(muro, DB.Wall):
                                raise ValueError("{}: wall_id {} is not a wall".format(etiqueta, f["wall_id"]))
                        else:
                            curva = f["curve"]
                            start = xyz_desde_mm(curva.get("start_point") or curva.get("start") or {})
                            end = xyz_desde_mm(curva.get("end_point") or curva.get("end") or {})
                            muro = _muro_bajo_curva(doc, start, end)
                            if muro is None:
                                raise ValueError(
                                    "{}: a WallFoundation needs a host wall and none was found under the curve "
                                    "(within 100 mm); create the wall first or pass wall_id".format(etiqueta)
                                )
                        tipo = _elegir(corridas, f.get("type_name"), "wall foundation", etiqueta)
                        planes.append({"idx": idx, "kind": "wall", "wall": muro, "type": tipo})
                    elif f.get("boundary"):
                        nivel = _nivel(level_map, f.get("level") or f.get("level_name"), etiqueta)
                        tipo = _elegir(losas, f.get("type_name"), "foundation slab (floor)", etiqueta)
                        puntos = [xyz_desde_mm(p) for p in f["boundary"]]
                        if len(puntos) < 3:
                            raise ValueError("{}: boundary needs at least 3 points".format(etiqueta))
                        if puntos[0].DistanceTo(puntos[-1]) < 0.001:
                            puntos = puntos[:-1]
                        planes.append({"idx": idx, "kind": "slab", "level": nivel, "type": tipo, "puntos": puntos})
                    else:
                        raise ValueError("{}: give point (+level) for an isolated footing, wall_id or curve for a "
                                         "wall foundation, or boundary (+level) for a foundation slab".format(etiqueta))
                except ValueError as error:
                    errors.append(str(error))
                except Exception as error:
                    errors.append("{}: {}".format(etiqueta, str(error)))

            if ctx["simular"]:
                haria = []
                for p in planes:
                    if p["kind"] == "isolated":
                        haria.append({"accion": "crear", "element_type": "isolated_footing",
                                      "type": etiqueta_tipo(p["symbol"]), "level": get_element_name(p["level"]),
                                      "point_mm": punto_a_mm(p["xyz"])})
                    elif p["kind"] == "wall":
                        haria.append({"accion": "crear", "element_type": "wall_foundation",
                                      "type": get_element_name(p["type"]), "wall_id": get_element_id_value(p["wall"])})
                    else:
                        haria.append({"accion": "crear", "element_type": "foundation_slab",
                                      "type": get_element_name(p["type"]), "level": get_element_name(p["level"]),
                                      "points": len(p["puntos"])})
                return simulacion(haria, count=len(haria), errors=errors)

            ids = []
            with transaccion(doc, "Crear {} cimentaciones".format(len(planes))):
                for plan in planes:
                    try:
                        if plan["kind"] == "isolated":
                            _activar(plan["symbol"], doc)
                            zapata = doc.Create.NewFamilyInstance(
                                plan["xyz"], plan["symbol"], plan["level"], DB.Structure.StructuralType.Footing
                            )
                            if abs(plan["rotation"]) > 1e-9:
                                eje = DB.Line.CreateBound(plan["xyz"], plan["xyz"].Add(DB.XYZ(0, 0, 1)))
                                DB.ElementTransformUtils.RotateElement(doc, zapata.Id, eje, math.radians(plan["rotation"]))
                            ids.append(get_element_id_value(zapata))
                        elif plan["kind"] == "wall":
                            corrida = DB.WallFoundation.Create(doc, plan["type"].Id, plan["wall"].Id)
                            ids.append(get_element_id_value(corrida))
                        else:
                            loop = DB.CurveLoop()
                            puntos = plan["puntos"]
                            for i in range(len(puntos)):
                                loop.Append(DB.Line.CreateBound(puntos[i], puntos[(i + 1) % len(puntos)]))
                            loops = List[DB.CurveLoop]()
                            loops.Add(loop)
                            losa = DB.Floor.Create(doc, loops, plan["type"].Id, plan["level"].Id)
                            ids.append(get_element_id_value(losa))
                    except Exception as error:
                        errors.append("Foundation {}: {}".format(plan["idx"], str(error)))

            return resultado_creacion(doc, ids, errors, {"message": "Created {} foundation(s)".format(len(ids))})

        return ejecutar(doc, "/create_foundation/", request, cuerpo)

    @api.route("/create_opening/", methods=["POST"])
    @requiere_token
    def create_opening(doc, request):
        """Hueco en muro (2 puntos) o en suelo/cubierta/techo (poligono). Acepta `simular`."""

        def cuerpo(ctx):
            data = ctx["data"]
            host_id = data.get("host_id")
            points = data.get("points", [])
            if host_id is None:
                raise EscrituraRechazada("host_id is required", 400)
            if not points or len(points) < 2:
                raise EscrituraRechazada("points is required: 2 corners for a wall, 3+ for a floor/roof/ceiling", 400)
            host = doc.GetElement(make_element_id(host_id))
            if host is None:
                raise EscrituraRechazada("Host element {} not found".format(host_id), 404)
            try:
                puntos = [xyz_desde_mm(p) for p in points]
            except ValueError as error:
                raise EscrituraRechazada(str(error), 400)

            es_muro = isinstance(host, DB.Wall)
            es_superficie = isinstance(host, (DB.Floor, DB.RoofBase, DB.Ceiling)) if hasattr(DB, "Ceiling") else isinstance(host, (DB.Floor, DB.RoofBase))
            if not es_muro and not es_superficie:
                raise EscrituraRechazada(
                    "Host {} is a {}; openings are supported in walls, floors, roofs and ceilings".format(
                        host_id, get_element_name(host.Category) if host.Category else type(host).__name__),
                    400,
                )
            if es_muro:
                # Dos esquinas opuestas del hueco, en el plano del muro, tal como llegan:
                # recombinarlas como (min, min, min)/(max, max, max) saca los puntos del plano
                # en cualquier muro oblicuo y NewOpening los rechaza.
                if len(puntos) != 2:
                    raise EscrituraRechazada(
                        "A wall opening takes exactly 2 opposite corners on the wall face; got {}".format(len(puntos)), 400
                    )
                esquina_a, esquina_b = puntos[0], puntos[1]
                if esquina_a.DistanceTo(esquina_b) < 0.001:
                    raise EscrituraRechazada("The two corners of the opening must be different", 400)
                descripcion = {"accion": "crear", "element_type": "wall_opening",
                               "host_id": int(host_id), "corner_a_mm": punto_a_mm(esquina_a),
                               "corner_b_mm": punto_a_mm(esquina_b)}
            else:
                if len(puntos) < 3:
                    raise EscrituraRechazada("A floor/roof opening needs at least 3 points", 400)
                if puntos[0].DistanceTo(puntos[-1]) < 0.001:
                    puntos = puntos[:-1]
                descripcion = {"accion": "crear", "element_type": "shaft_opening",
                               "host_id": int(host_id), "points": len(puntos),
                               "host_category": get_element_name(host.Category) if host.Category else None}

            if ctx["simular"]:
                return simulacion([descripcion])

            with transaccion(doc, "Crear hueco en {}".format(host_id)):
                if es_muro:
                    hueco = doc.Create.NewOpening(host, esquina_a, esquina_b)
                else:
                    curvas = DB.CurveArray()
                    for i in range(len(puntos)):
                        curvas.Append(DB.Line.CreateBound(puntos[i], puntos[(i + 1) % len(puntos)]))
                    hueco = doc.Create.NewOpening(host, curvas, True)
                hueco_id = get_element_id_value(hueco)

            resultado = resultado_creacion(doc, [hueco_id])
            resultado["opening_id"] = hueco_id
            resultado["host"] = describir_elemento(doc, host)
            resultado["message"] = "Created opening {} in host {}".format(hueco_id, host_id)
            return resultado

        return ejecutar(doc, "/create_opening/", request, cuerpo)

    logger.info("Estructural routes registered successfully")
