# -*- coding: UTF-8 -*-
"""
Coordinates Module for Revit MCP
Shared coordinates: project position, survey point and project base point.

Why this exists: Revit's internal origin cannot move, and all geometry should
stay within 16 km of it. A georeferenced CAD file (a Civil 3D surface in UTM
sits hundreds of kilometres from 0,0) linked "Origin to Origin" lands that far
away, so Revit warns on every link. The fix is to tell Revit which real-world
coordinates the internal origin has (set_project_coordinates, the API
equivalent of "Specify Coordinates at a Point") and then link the file
"By Shared Coordinates" (link_file with placement="shared").
"""

from utils import get_element_name, get_element_id_value, suppress_warnings
from seguridad import requiere_token
from coordinates_core import (
    describe_position,
    normalize_units,
    shared_coordinates_set,
    to_feet,
    from_feet,
)
from pyrevit import routes, revit, DB
import json
import math
import traceback
import logging

logger = logging.getLogger(__name__)

MM_TO_FEET = 1.0 / 304.8


def _xyz_from_mm(point):
    """Internal XYZ (feet) from a {"x", "y", "z"} dict in mm; the origin when empty."""
    if not point:
        return DB.XYZ.Zero
    return DB.XYZ(
        float(point.get("x", 0)) * MM_TO_FEET,
        float(point.get("y", 0)) * MM_TO_FEET,
        float(point.get("z", 0)) * MM_TO_FEET,
    )


def _point(xyz, units):
    """{"x", "y", "z"} in the requested units from an internal XYZ (feet)."""
    return {
        "x": round(from_feet(xyz.X, units), 4),
        "y": round(from_feet(xyz.Y, units), 4),
        "z": round(from_feet(xyz.Z, units), 4),
    }


def _base_point(getter, doc, units):
    """Project base point or survey point (Revit 2020+ BasePoint API); None if unavailable."""
    try:
        bp = getter(doc)
        if bp is None:
            return None
        return {
            "element_id": get_element_id_value(bp),
            "internal": _point(bp.Position, units),
            "shared": _point(bp.SharedPosition, units),
        }
    except Exception as error:
        logger.debug("Base point not available: {}".format(str(error)))
        return None


def _site(doc):
    """Site location: latitude/longitude and, from Revit 2018, the GIS coordinate system id."""
    site = {}
    try:
        sl = doc.SiteLocation
        site["latitude_degrees"] = round(math.degrees(sl.Latitude), 7)
        site["longitude_degrees"] = round(math.degrees(sl.Longitude), 7)
        try:
            site["place_name"] = sl.PlaceName
        except Exception:
            pass
        try:
            # Same codes Civil 3D uses (e.g. "UTM84-18S"); empty when none assigned.
            site["geo_coordinate_system"] = sl.GeoCoordinateSystemId or None
        except Exception:
            pass
    except Exception as error:
        logger.debug("Site location not available: {}".format(str(error)))
    return site


def describe_coordinates(doc, units="m"):
    """Everything an agent needs to know about the model's coordinate systems."""
    units = normalize_units(units)
    active = doc.ActiveProjectLocation
    pos = active.GetProjectPosition(DB.XYZ.Zero)
    locations = []
    try:
        for loc in doc.ProjectLocations:
            locations.append(get_element_name(loc))
    except Exception:
        pass
    return {
        "units": units,
        "active_location": get_element_name(active),
        "locations": locations,
        "shared_coordinates_set": shared_coordinates_set(
            pos.EastWest, pos.NorthSouth, pos.Elevation, pos.Angle),
        "internal_origin": describe_position(
            pos.EastWest, pos.NorthSouth, pos.Elevation, pos.Angle, units),
        "project_base_point": _base_point(
            lambda d: DB.BasePoint.GetProjectBasePoint(d), doc, units),
        "survey_point": _base_point(
            lambda d: DB.BasePoint.GetSurveyPoint(d), doc, units),
        "site": _site(doc),
    }


def _new_project_position(doc, east_ft, north_ft, elevation_ft, angle_rad):
    try:
        return doc.Application.Create.NewProjectPosition(east_ft, north_ft, elevation_ft, angle_rad)
    except Exception:
        return DB.ProjectPosition(east_ft, north_ft, elevation_ft, angle_rad)


def _set_position(doc, xyz, pos):
    """SetProjectPosition on the active location.

    Since Revit 2018 the read-only "Internal" location can be the active one;
    then the first other location is activated and used (or a duplicate named
    "MCP" when there is none). Returns (location, switched).
    """
    active = doc.ActiveProjectLocation
    try:
        active.SetProjectPosition(xyz, pos)
        return active, False
    except Exception as first_error:
        active_id = get_element_id_value(active)
        candidates = [loc for loc in doc.ProjectLocations
                      if get_element_id_value(loc) != active_id]
        if not candidates:
            candidates = [active.Duplicate("MCP")]
        target = candidates[0]
        doc.ActiveProjectLocation = target
        target.SetProjectPosition(xyz, pos)
        logger.info("Active location '{}' rejected SetProjectPosition ({}); switched to '{}'".format(
            get_element_name(active), str(first_error), get_element_name(target)))
        return target, True


def register_coordinates_routes(api):
    """Register all coordinate routes with the API"""

    @api.route("/project_coordinates/", methods=["GET"])
    @requiere_token
    def project_coordinates_handler(doc, request):
        """Shared coordinates of the model (project position, base points, site)."""
        try:
            if not doc:
                return routes.make_response(
                    data={"error": "No active Revit document"}, status=503
                )
            query = getattr(request, "query_params", None) or {}
            units = query.get("units") if isinstance(query, dict) else None
            try:
                units = normalize_units(units)
            except ValueError as units_error:
                return routes.make_response(data={"error": str(units_error)}, status=400)
            return routes.make_response(data=describe_coordinates(doc, units))
        except Exception as e:
            logger.error("Failed to read project coordinates: {}".format(str(e)))
            return routes.make_response(
                data={"error": str(e), "traceback": traceback.format_exc()}, status=500
            )

    @api.route("/set_project_coordinates/", methods=["POST"])
    @requiere_token
    def set_project_coordinates_handler(doc, request):
        """Give an internal point real-world shared coordinates ("Specify Coordinates at a Point")."""
        try:
            if not doc:
                return routes.make_response(
                    data={"error": "No active Revit document"}, status=503
                )

            data = json.loads(request.data) if isinstance(request.data, str) else request.data

            east = data.get("east")
            north = data.get("north")
            if east is None or north is None:
                return routes.make_response(
                    data={"error": "east and north are required (real-world coordinates of the point)"},
                    status=400,
                )
            try:
                units = normalize_units(data.get("units"))
                east_ft = to_feet(east, units)
                north_ft = to_feet(north, units)
                elevation_ft = to_feet(data.get("elevation") or 0, units)
                angle_rad = math.radians(float(data.get("angle_degrees") or 0))
            except (TypeError, ValueError) as value_error:
                return routes.make_response(data={"error": str(value_error)}, status=400)

            xyz = _xyz_from_mm(data.get("point"))
            before = describe_coordinates(doc, units)

            t = DB.Transaction(doc, "Set project coordinates via MCP")
            t.Start()
            suppress_warnings(t)
            try:
                pos = _new_project_position(doc, east_ft, north_ft, elevation_ft, angle_rad)
                location, switched = _set_position(doc, xyz, pos)
                t.Commit()
            except Exception as tx_error:
                if t.HasStarted() and not t.HasEnded():
                    t.RollBack()
                raise tx_error

            after = describe_coordinates(doc, units)
            origin = after["internal_origin"]
            message = (
                "Shared coordinates set on location '{}'{}: internal point ({}, {}, {}) mm is now "
                "E={} N={} elevation={} {} with true north at {} degrees. Internal origin is now "
                "E={} N={} elevation={} {}. Link georeferenced CAD files with placement='shared'."
            ).format(
                get_element_name(location),
                " (activated instead of the read-only one)" if switched else "",
                round(xyz.X / MM_TO_FEET, 1), round(xyz.Y / MM_TO_FEET, 1), round(xyz.Z / MM_TO_FEET, 1),
                east, north, data.get("elevation") or 0, units, data.get("angle_degrees") or 0,
                origin["east"], origin["north"], origin["elevation"], units,
            )
            return routes.make_response(
                data={
                    "status": "success",
                    "message": message,
                    "location": get_element_name(location),
                    "location_switched": switched,
                    "before": before,
                    "after": after,
                }
            )

        except Exception as e:
            logger.error("Failed to set project coordinates: {}".format(str(e)))
            return routes.make_response(
                data={"error": str(e), "traceback": traceback.format_exc()}, status=500
            )

    logger.info("Coordinates routes registered successfully")
