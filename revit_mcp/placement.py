# -*- coding: UTF-8 -*-
"""
Placement Module for Revit MCP
Handles family placement, family loading and family/level listing.

place_family y load_family pasan por escritura.ejecutar (copia, log,
`simular`, transaccion "IA: ..." y verificacion de lo creado).
"""

from utils import (
    elevacion_interna, elevacion_mostrada,
    buscar_por_nombre,
    get_element_name, find_family_symbol_safely, get_element_id_value, make_element_id,
    xyz_desde_mm, punto_a_mm, mapa_niveles, FEET_TO_MM,
)
from seguridad import requiere_token
from escritura import ejecutar, transaccion, simulacion, EscrituraRechazada, resultado_creacion, describir_elemento
from pyrevit import routes, revit, DB
import os
import logging

logger = logging.getLogger(__name__)


def _familias_disponibles(doc, maximo=20):
    nombres = set()
    try:
        symbols = DB.FilteredElementCollector(doc).OfClass(DB.FamilySymbol).ToElements()
        for symbol in symbols:
            try:
                nombres.add(get_element_name(symbol.Family))
            except Exception:
                continue
            if len(nombres) >= 50:
                break
    except Exception:
        return ["Could not retrieve family list"]
    return sorted(nombres)[:maximo]


def _necesita_muro(symbol):
    """True si la familia debe alojarse en un muro (puertas, ventanas, hosted)."""
    try:
        if symbol.Family.FamilyPlacementType == DB.FamilyPlacementType.OneLevelBasedHosted:
            return True
    except Exception:
        pass
    try:
        if symbol.Category:
            cat_id = get_element_id_value(symbol.Category.Id)
            if cat_id in (int(DB.BuiltInCategory.OST_Windows), int(DB.BuiltInCategory.OST_Doors)):
                return True
    except Exception:
        pass
    return False


def _muro_mas_cercano(doc, point):
    best_dist = None
    host_wall = None
    walls = (
        DB.FilteredElementCollector(doc)
        .OfCategory(DB.BuiltInCategory.OST_Walls)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    for w in walls:
        try:
            wloc = w.Location
            if not wloc or not hasattr(wloc, "Curve"):
                continue
            d = wloc.Curve.Distance(point)
            if best_dist is None or d < best_dist:
                best_dist = d
                host_wall = w
        except Exception:
            continue
    return host_wall


def register_placement_routes(api):
    """Register all placement-related routes with the API"""

    @api.route("/place_family/", methods=["POST"])
    @requiere_token
    def place_family(doc, request):
        """
        Place a family instance at a specified location in the model.

        Expected request data:
        {
            "family_name": "Basic Wall",
            "type_name": "Generic - 200mm",
            "location": {"x": 0.0, "y": 0.0, "z": 0.0},   # mm
            "rotation": 0.0,
            "level_name": "Level 1",
            "properties": {"Mark": "A1"},
            "simular": false
        }
        """

        def cuerpo(ctx):
            data = ctx["data"]
            family_name = data.get("family_name")
            type_name = data.get("type_name")
            location = data.get("location", {})
            rotation = data.get("rotation", 0.0) or 0.0
            level_name = data.get("level_name")
            properties = data.get("properties", {}) or {}

            if not family_name:
                raise EscrituraRechazada("No family_name provided", 400)
            if not location or not all(k in location for k in ["x", "y", "z"]):
                raise EscrituraRechazada("Invalid location - must include x, y, z coordinates", 400)

            target_symbol = find_family_symbol_safely(doc, family_name, type_name)
            if not target_symbol:
                raise EscrituraRechazada(
                    "Family type not found: {} - {}".format(family_name, type_name or "Any"),
                    404,
                    {"available_families": _familias_disponibles(doc)},
                )

            target_level = None
            if level_name:
                target_level = mapa_niveles(doc).get(level_name)
                if not target_level:
                    raise EscrituraRechazada("Level not found: {}".format(level_name), 404)

            try:
                point = xyz_desde_mm(location)
            except ValueError as coord_error:
                raise EscrituraRechazada("Invalid coordinates: {}".format(coord_error), 400)
            try:
                rotation = float(rotation)
            except (TypeError, ValueError):
                raise EscrituraRechazada("rotation must be a number (degrees)", 400)

            needs_wall_host = _necesita_muro(target_symbol)
            host_wall = _muro_mas_cercano(doc, point) if needs_wall_host else None
            if needs_wall_host and host_wall is None:
                raise EscrituraRechazada(
                    "Family '{}' must be hosted by a wall, but no wall was found near the requested "
                    "location. Create the host wall first.".format(family_name),
                    400,
                )

            if ctx["simular"]:
                return simulacion([{
                    "accion": "colocar",
                    "family_name": get_element_name(target_symbol.Family),
                    "type_name": get_element_name(target_symbol),
                    "level": level_name,
                    "location_mm": punto_a_mm(point),
                    "rotation_degrees": rotation,
                    "host_wall_id": get_element_id_value(host_wall) if host_wall is not None else None,
                    "properties": properties,
                }])

            properties_set = []
            properties_failed = []
            with transaccion(doc, "Colocar {}".format(family_name)):
                if not target_symbol.IsActive:
                    target_symbol.Activate()
                    doc.Regenerate()

                if host_wall is not None:
                    if target_level:
                        new_instance = doc.Create.NewFamilyInstance(
                            point, target_symbol, host_wall, target_level,
                            DB.Structure.StructuralType.NonStructural,
                        )
                    else:
                        new_instance = doc.Create.NewFamilyInstance(
                            point, target_symbol, host_wall, DB.Structure.StructuralType.NonStructural
                        )
                elif target_level:
                    new_instance = doc.Create.NewFamilyInstance(
                        point, target_symbol, target_level, DB.Structure.StructuralType.NonStructural
                    )
                else:
                    new_instance = doc.Create.NewFamilyInstance(
                        point, target_symbol, DB.Structure.StructuralType.NonStructural
                    )

                if rotation != 0:
                    try:
                        rotation_radians = rotation * (3.14159265359 / 180.0)
                        axis = DB.Line.CreateBound(point, point.Add(DB.XYZ(0, 0, 1)))
                        if hasattr(new_instance.Location, "Rotate"):
                            if not new_instance.Location.Rotate(axis, rotation_radians):
                                properties_failed.append("rotation (element may not support rotation)")
                    except Exception as rotate_err:
                        properties_failed.append("rotation (error: {})".format(str(rotate_err)))

                for param_name, param_value in properties.items():
                    try:
                        param = buscar_por_nombre(new_instance, param_name)
                        if param and not param.IsReadOnly:
                            if param.StorageType == DB.StorageType.String:
                                param.Set(str(param_value))
                            elif param.StorageType == DB.StorageType.Integer:
                                param.Set(int(param_value))
                            elif param.StorageType == DB.StorageType.Double:
                                param.Set(float(param_value))
                            else:
                                properties_failed.append("{} (unsupported type)".format(param_name))
                                continue
                            properties_set.append(param_name)
                        elif param:
                            properties_failed.append("{} (read-only)".format(param_name))
                        else:
                            properties_failed.append("{} (not found)".format(param_name))
                    except Exception as param_error:
                        properties_failed.append("{} (error: {})".format(param_name, str(param_error)))

                new_id = get_element_id_value(new_instance)

            # Verificacion: la instancia existe y donde quedo realmente
            resultado = resultado_creacion(doc, [new_id])
            try:
                actual_location = new_instance.Location.Point
                actual_coords = punto_a_mm(actual_location)
            except Exception:
                actual_coords = punto_a_mm(point)
            requested = punto_a_mm(point)
            desvio = max(abs(actual_coords[e] - requested[e]) for e in ("x", "y", "z"))
            resultado.update({
                "element_id": new_id,
                "family_name": family_name,
                "type_name": type_name,
                "requested_location": requested,
                "actual_location": actual_coords,
                "rotation_degrees": rotation,
                "level": level_name if target_level else None,
                "host_wall_id": get_element_id_value(host_wall) if host_wall is not None else None,
                "properties_set": properties_set,
                "properties_failed": properties_failed,
            })
            if desvio > 1.0 and resultado["ok"]:
                resultado["verificacion"] = {
                    "coincide": False,
                    "detalle": "Instance was created but Revit placed it {} mm away from the requested "
                    "point (hosted/level constraints); check actual_location.".format(round(desvio, 1)),
                }
                resultado["ok"] = False
            return resultado

        return ejecutar(doc, "/place_family/", request, cuerpo)

    @api.route("/load_family/", methods=["POST"])
    @requiere_token
    def load_family(doc, request):
        """
        Load a Revit family (.rfa) from disk into the active document, so its
        types become available to place_family. LoadFamily manages its own
        transaction, so this route does not open one (but it does back up and
        log like every other write). Accepts `simular`.

        Payload: { "file_path": "C:\\\\path\\\\to\\\\Family.rfa" }
        """

        def cuerpo(ctx):
            data = ctx["data"]
            file_path = data.get("file_path")
            if not file_path:
                raise EscrituraRechazada("file_path is required (full path to a .rfa file)", 400)
            if not os.path.exists(file_path):
                raise EscrituraRechazada("Family file not found: {}".format(file_path), 404)
            fam_name = os.path.splitext(os.path.basename(file_path))[0]

            if ctx["simular"]:
                return simulacion([{
                    "accion": "cargar_familia", "family_name": fam_name, "file_path": file_path,
                    "size_kb": int(os.path.getsize(file_path) / 1024),
                }])

            try:
                result = doc.LoadFamily(file_path)
                # IronPython may return (bool, Family) for the out-param overload
                ok = result[0] if isinstance(result, tuple) else result
            except Exception as le:
                raise EscrituraRechazada("LoadFamily failed: {}".format(str(le)), 500)

            # Verificacion: la familia esta en el documento y cuantos tipos tiene
            familia = None
            for fam in DB.FilteredElementCollector(doc).OfClass(DB.Family).ToElements():
                try:
                    if get_element_name(fam) == fam_name:
                        familia = fam
                        break
                except Exception:
                    continue
            tipos = []
            if familia is not None:
                try:
                    for sid in familia.GetFamilySymbolIds():
                        tipos.append(get_element_name(doc.GetElement(sid)))
                except Exception:
                    pass

            resultado = {
                "status": "success" if ok else "already_loaded",
                "family_name": fam_name,
                "file_path": file_path,
                "loaded": bool(ok),
                "family_id": get_element_id_value(familia) if familia is not None else None,
                "types": sorted(tipos),
                "ok": familia is not None,
                "message": (
                    "Loaded family '{}'. Its types are now available to place_family.".format(fam_name)
                    if ok else
                    "Family '{}' was already loaded (or no new types added)".format(fam_name)
                ),
            }
            if familia is None:
                resultado["verificacion"] = {
                    "coincide": False,
                    "detalle": "No family named '{}' was found in the document after LoadFamily".format(fam_name),
                }
            else:
                resultado["verificacion"] = {"coincide": True}
            return resultado

        return ejecutar(doc, "/load_family/", request, cuerpo)

    @api.route("/list_families/", methods=["GET"])
    @requiere_token
    def list_families(doc, request):
        """
        Flat list of family types in the current model, filtered by query params:
          contains  - substring (case-insensitive) of "family_name type_name"
          category  - substring (case-insensitive) of the category name
          limit     - max results (default 50)
        Returns:
            {"families": [{family_name, type_name, category, is_active, type_id}],
             "count", "total_matched", "truncated", "filters"}
        """
        try:
            if not doc:
                return routes.make_response(
                    data={"error": "No active Revit document"}, status=503
                )

            params = getattr(request, "query_params", None) or {}
            if not isinstance(params, dict):
                params = {}
            contains = (params.get("contains") or "").strip().lower()
            category = (params.get("category") or "").strip().lower()
            try:
                limit = int(params.get("limit") or 50)
            except (TypeError, ValueError):
                limit = 50
            if limit <= 0:
                limit = 50

            symbols = (
                DB.FilteredElementCollector(doc).OfClass(DB.FamilySymbol).ToElements()
            )
            families = []
            total_matched = 0
            for symbol in symbols:
                try:
                    family_name = get_element_name(symbol.Family)
                    type_name = get_element_name(symbol)
                    cat_name = symbol.Category.Name if symbol.Category else "Unknown"
                except Exception:
                    continue
                if category and category not in cat_name.lower():
                    continue
                if contains and contains not in (family_name + " " + type_name).lower():
                    continue
                total_matched += 1
                if len(families) >= limit:
                    continue
                try:
                    is_active = symbol.IsActive
                except Exception:
                    is_active = None
                families.append(
                    {
                        "family_name": family_name,
                        "type_name": type_name,
                        "category": cat_name,
                        "is_active": is_active,
                        "type_id": get_element_id_value(symbol),
                    }
                )
            return routes.make_response(
                data={
                    "families": families,
                    "count": len(families),
                    "total_matched": total_matched,
                    "truncated": total_matched > len(families),
                    "truncated_total": len(families),
                    "filters": {"contains": contains or None, "category": category or None, "limit": limit},
                    "status": "success",
                }
            )
        except Exception as e:
            logger.error("Failed to list families: {}".format(str(e)))
            return routes.make_response(
                data={"error": "Failed to list families: {}".format(str(e))}, status=500
            )

    @api.route("/list_family_categories/", methods=["GET"])
    @requiere_token
    def list_family_categories(doc):
        """
        Get a list of all family categories in the current Revit model

        Returns:
            dict: List of categories with family counts
        """
        try:
            if not doc:
                return routes.make_response(
                    data={"error": "No active Revit document"}, status=503
                )

            logger.info("Listing all family categories")

            # Get all family symbols
            symbols = (
                DB.FilteredElementCollector(doc).OfClass(DB.FamilySymbol).ToElements()
            )

            categories = {}

            for symbol in symbols:
                try:
                    # Get category name
                    category_name = "Unknown"
                    try:
                        if symbol.Category:
                            category_name = symbol.Category.Name
                    except:
                        pass

                    if category_name not in categories:
                        categories[category_name] = 0

                    categories[category_name] += 1

                except Exception as e:
                    logger.warning("Could not process family symbol: {}".format(str(e)))
                    continue

            # Sort by name
            sorted_categories = dict(sorted(categories.items()))

            return routes.make_response(
                data={
                    "categories": sorted_categories,
                    "total_categories": len(sorted_categories),
                    "status": "success",
                }
            )

        except Exception as e:
            logger.error("Failed to list family categories: {}".format(str(e)))
            return routes.make_response(
                data={"error": "Failed to list family categories: {}".format(str(e))},
                status=500,
            )

    @api.route("/list_levels/", methods=["GET"])
    @requiere_token
    def list_levels(doc):
        """
        Get a list of all levels in the current Revit model

        Returns:
            dict: List of levels with their elevations
        """
        try:
            if not doc:
                return routes.make_response(
                    data={"error": "No active Revit document"}, status=503
                )

            logger.info("Listing all available levels")

            # Get all levels
            levels = (
                DB.FilteredElementCollector(doc)
                .OfCategory(DB.BuiltInCategory.OST_Levels)
                .WhereElementIsNotElementType()
                .ToElements()
            )

            levels_info = []

            for level in levels:
                try:
                    level_name = get_element_name(level)
                    elevation = elevacion_interna(level)

                    levels_info.append(
                        {
                            "name": level_name,
                            "elevation_mm": round(elevation * 304.8, 0),
                            "elevation_shown_mm": round(elevacion_mostrada(level) * 304.8, 0),
                            "elevation_feet": round(elevation, 4),
                            "id": get_element_id_value(level),
                        }
                    )

                except Exception as e:
                    logger.warning("Could not process level: {}".format(str(e)))
                    continue

            # Sort by elevation
            levels_info.sort(key=lambda x: x["elevation_feet"])

            return routes.make_response(
                data={
                    "levels": levels_info,
                    "total_levels": len(levels_info),
                    "status": "success",
                }
            )

        except Exception as e:
            logger.error("Failed to list levels: {}".format(str(e)))
            return routes.make_response(
                data={"error": "Failed to list levels: {}".format(str(e))}, status=500
            )

    logger.info("Placement routes registered successfully")
