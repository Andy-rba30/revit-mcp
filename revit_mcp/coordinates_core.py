# -*- coding: UTF-8 -*-
"""
Pure helpers for shared coordinates (no pyRevit import).

Unit conversion, link placement names and the distance-to-internal-origin
check live here so they run the same in IronPython 2.7 (inside Revit) and in
CPython (tests/test_coordinates_core.py).
"""
from __future__ import division

import math

METERS_PER_FOOT = 0.3048
FEET_PER_METER = 1.0 / METERS_PER_FOOT

# Autodesk guidance: keep all geometry within 10 miles (16 km) of the internal
# origin. From 20 miles (33 km) Revit warns ("extents greater than 20 miles")
# and, when linking a CAD file, silently replaces the requested placement by
# "Center to Center", which loses the georeference.
RECOMMENDED_RADIUS_M = 16093.4
REVIT_LIMIT_M = 32186.9

UNITS_TO_FEET = {
    "m": FEET_PER_METER,
    "mm": FEET_PER_METER / 1000.0,
    "ft": 1.0,
}

PLACEMENTS = ("origin", "shared", "center", "site")

_PLACEMENT_ALIASES = {
    "origin": "origin",
    "origin_to_origin": "origin",
    "internal": "origin",
    "internal_origin": "origin",
    "shared": "shared",
    "shared_coordinates": "shared",
    "by_shared_coordinates": "shared",
    "auto_by_shared_coordinates": "shared",
    "center": "center",
    "centered": "center",
    "center_to_center": "center",
    "site": "site",
    "project_base_point": "site",
    "by_project_base_point": "site",
}

_UNIT_ALIASES = {
    "meter": "m", "meters": "m", "metre": "m", "metres": "m", "metro": "m", "metros": "m",
    "millimeter": "mm", "millimeters": "mm", "millimetre": "mm", "millimetres": "mm",
    "milimetro": "mm", "milimetros": "mm",
    "foot": "ft", "feet": "ft", "pie": "ft", "pies": "ft",
}


def normalize_units(units):
    """Return 'm', 'mm' or 'ft' ('m' when empty); raise ValueError otherwise."""
    if units is None or str(units).strip() == "":
        return "m"
    key = str(units).strip().lower()
    key = _UNIT_ALIASES.get(key, key)
    if key not in UNITS_TO_FEET:
        raise ValueError("units '{}' not supported. Use: m, mm, ft".format(units))
    return key


def to_feet(value, units="m"):
    """Length in Revit internal units (feet) from a value in m, mm or ft."""
    return float(value) * UNITS_TO_FEET[normalize_units(units)]


def from_feet(value_ft, units="m"):
    """Length in m, mm or ft from Revit internal units (feet)."""
    return float(value_ft) / UNITS_TO_FEET[normalize_units(units)]


def normalize_placement(name):
    """Canonical link placement ('origin' when empty); raise ValueError if unknown.

    origin  = Auto - Origin to Internal Origin
    shared  = Auto - By Shared Coordinates
    center  = Auto - Center to Center
    site    = Auto - Origin to Project Base Point
    """
    if name is None or str(name).strip() == "":
        return "origin"
    key = str(name).strip().lower().replace("-", "_").replace(" ", "_")
    try:
        return _PLACEMENT_ALIASES[key]
    except KeyError:
        raise ValueError("placement '{}' not supported. Use: {}".format(
            name, ", ".join(PLACEMENTS)))


def shared_coordinates_set(east_ft, north_ft, elevation_ft, angle_rad, tolerance_ft=1e-6):
    """True when the project position of a point differs from its internal coordinates.

    With everything at zero the shared coordinate system coincides with the
    internal one, so "By Shared Coordinates" places a file exactly like
    "Origin to Origin".
    """
    return (abs(east_ft) > tolerance_ft or abs(north_ft) > tolerance_ft
            or abs(elevation_ft) > tolerance_ft or abs(angle_rad) > 1e-9)


def distance_from_origin_m(x_ft, y_ft, z_ft=0.0):
    """Distance in metres from the internal origin to an internal point (feet)."""
    return math.sqrt(x_ft * x_ft + y_ft * y_ft + z_ft * z_ft) * METERS_PER_FOOT


def evaluate_extents(min_xyz_ft, max_xyz_ft):
    """Distance of the farthest corner of a bounding box (feet) from the internal origin.

    Returns a dict with max_distance_m, far_from_origin (beyond the 16 km
    Autodesk recommends), beyond_revit_limit (beyond the 33 km at which Revit
    warns and overrides the placement) and a hint when either flag is set.
    """
    farthest = 0.0
    for x in (min_xyz_ft[0], max_xyz_ft[0]):
        for y in (min_xyz_ft[1], max_xyz_ft[1]):
            for z in (min_xyz_ft[2], max_xyz_ft[2]):
                farthest = max(farthest, distance_from_origin_m(x, y, z))
    result = {
        "max_distance_m": round(farthest, 1),
        "far_from_origin": farthest > RECOMMENDED_RADIUS_M,
        "beyond_revit_limit": farthest > REVIT_LIMIT_M,
    }
    if result["beyond_revit_limit"]:
        result["hint"] = (
            "geometry is {:.1f} km from the internal origin, beyond the 33 km (20 miles) "
            "Revit supports: Revit will warn and may override the placement. For a "
            "georeferenced CAD file, call set_project_coordinates with the real-world "
            "coordinates of the internal origin (e.g. the centre of the surface) and link "
            "again with placement='shared'.".format(farthest / 1000.0))
    elif result["far_from_origin"]:
        result["hint"] = (
            "geometry is {:.1f} km from the internal origin, beyond the 16 km (10 miles) "
            "Autodesk recommends; precision may degrade. Consider set_project_coordinates "
            "plus placement='shared'.".format(farthest / 1000.0))
    return result


def describe_position(east_ft, north_ft, elevation_ft, angle_rad, units="m"):
    """Project position (ProjectPosition fields, feet and radians) in the given units and degrees."""
    units = normalize_units(units)
    return {
        "east": round(from_feet(east_ft, units), 4),
        "north": round(from_feet(north_ft, units), 4),
        "elevation": round(from_feet(elevation_ft, units), 4),
        "angle_degrees": round(math.degrees(angle_rad), 6),
        "units": units,
    }
