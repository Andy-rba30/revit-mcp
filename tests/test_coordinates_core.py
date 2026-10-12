# -*- coding: utf-8 -*-
"""Unit tests (CPython, pytest) for revit_mcp/coordinates_core.py.

The module has no pyRevit dependency, so the conversions and the distance
check that protect link_file / set_project_coordinates run here as they do
inside Revit (IronPython 2.7). Run: uv run pytest tests/test_coordinates_core.py
"""
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "revit_mcp"))

import coordinates_core as cc  # noqa: E402

FT_PER_M = 1.0 / 0.3048


# ---------------------------------------------------------------- units
def test_units_to_and_from_feet():
    assert cc.to_feet(1, "m") == pytest.approx(3.280839895)
    assert cc.to_feet(304.8, "mm") == pytest.approx(1.0)
    assert cc.to_feet(2.5, "ft") == 2.5
    assert cc.from_feet(1, "m") == pytest.approx(0.3048)
    assert cc.from_feet(1, "mm") == pytest.approx(304.8)
    assert cc.from_feet(cc.to_feet(500000.0, "m"), "m") == pytest.approx(500000.0)


@pytest.mark.parametrize("alias, expected", [
    (None, "m"), ("", "m"), ("M", "m"), ("metros", "m"), ("meters", "m"),
    ("mm", "mm"), ("millimeters", "mm"), ("ft", "ft"), ("feet", "ft"), ("pies", "ft"),
])
def test_normalize_units_aliases(alias, expected):
    assert cc.normalize_units(alias) == expected


def test_normalize_units_rejects_unknown():
    with pytest.raises(ValueError, match="m, mm, ft"):
        cc.normalize_units("inches")


# ---------------------------------------------------------------- placement
@pytest.mark.parametrize("name, expected", [
    (None, "origin"), ("", "origin"), ("origin", "origin"), ("Origin to Origin", "origin"),
    ("shared", "shared"), ("Shared", "shared"), ("by-shared-coordinates", "shared"),
    ("By Shared Coordinates", "shared"),
    ("center", "center"), ("Centered", "center"), ("center-to-center", "center"),
    ("site", "site"), ("project base point", "site"),
])
def test_normalize_placement(name, expected):
    assert cc.normalize_placement(name) == expected


def test_normalize_placement_rejects_unknown():
    with pytest.raises(ValueError, match="origin, shared, center, site"):
        cc.normalize_placement("manual")


# ---------------------------------------------------------------- shared coordinates flag
def test_shared_coordinates_set():
    assert cc.shared_coordinates_set(0, 0, 0, 0) is False
    assert cc.shared_coordinates_set(1e-9, 0, 0, 0) is False, "numerical noise is not a position"
    assert cc.shared_coordinates_set(500000 * FT_PER_M, 8500000 * FT_PER_M, 0, 0) is True
    assert cc.shared_coordinates_set(0, 0, 2400 * FT_PER_M, 0) is True
    assert cc.shared_coordinates_set(0, 0, 0, math.radians(1.5)) is True


# ---------------------------------------------------------------- distance to the internal origin
def test_distance_from_origin_in_metres():
    assert cc.distance_from_origin_m(FT_PER_M, 0, 0) == pytest.approx(1.0)
    assert cc.distance_from_origin_m(3 * FT_PER_M, 4 * FT_PER_M, 0) == pytest.approx(5.0)


def test_extents_near_origin_are_fine():
    r = cc.evaluate_extents((-100 * FT_PER_M, -50 * FT_PER_M, 0), (300 * FT_PER_M, 200 * FT_PER_M, 40 * FT_PER_M))
    assert r["far_from_origin"] is False and r["beyond_revit_limit"] is False
    assert "hint" not in r
    assert r["max_distance_m"] == pytest.approx(math.sqrt(300 ** 2 + 200 ** 2 + 40 ** 2), abs=0.1)


def test_extents_in_utm_are_beyond_the_revit_limit():
    # A Civil 3D surface in UTM linked origin-to-origin: ~8 500 km from the internal origin
    r = cc.evaluate_extents((500000 * FT_PER_M, 8500000 * FT_PER_M, 2400 * FT_PER_M),
                            (501000 * FT_PER_M, 8501000 * FT_PER_M, 2500 * FT_PER_M))
    assert r["beyond_revit_limit"] is True and r["far_from_origin"] is True
    assert r["max_distance_m"] > 8000000
    assert "set_project_coordinates" in r["hint"] and "placement='shared'" in r["hint"]
    assert "33 km" in r["hint"]


def test_extents_between_16_and_33_km_only_warn():
    r = cc.evaluate_extents((20000 * FT_PER_M, 0, 0), (20500 * FT_PER_M, 500 * FT_PER_M, 0))
    assert r["far_from_origin"] is True and r["beyond_revit_limit"] is False
    assert "16 km" in r["hint"]


def test_extents_use_the_farthest_corner():
    # Box that straddles the origin: the far corner, not the centre, decides
    r = cc.evaluate_extents((-20000 * FT_PER_M, 0, 0), (100 * FT_PER_M, 0, 0))
    assert r["max_distance_m"] == pytest.approx(20000.0, abs=0.1)
    assert r["far_from_origin"] is True


# ---------------------------------------------------------------- position description
def test_describe_position_converts_units_and_angle():
    d = cc.describe_position(500000 * FT_PER_M, 8500000 * FT_PER_M, 2400 * FT_PER_M, math.radians(-2.5), "m")
    assert d == {"east": 500000.0, "north": 8500000.0, "elevation": 2400.0, "angle_degrees": -2.5, "units": "m"}
    d_mm = cc.describe_position(FT_PER_M, 0, 0, 0, "mm")
    assert d_mm["east"] == pytest.approx(1000.0) and d_mm["units"] == "mm"
