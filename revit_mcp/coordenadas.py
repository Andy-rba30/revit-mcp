# -*- coding: UTF-8 -*-
"""
Coordenadas Module for Revit MCP
Punto base del proyecto, punto de reconocimiento, norte verdadero y sistema
de coordenadas compartido. Lo usan get_revit_model_info (resumen) y las rutas
get_project_location / set_project_location.

Unidades: las posiciones se devuelven en milimetros y los angulos en grados.
"""

from utils import get_element_name, get_element_id_value, punto_a_mm, FEET_TO_MM
from pyrevit import routes, revit, DB
import math
import logging

logger = logging.getLogger(__name__)


def _punto_base(doc, de_proyecto):
    """DB.BasePoint del proyecto (True) o de reconocimiento (False), o None."""
    try:
        if de_proyecto:
            return DB.BasePoint.GetProjectBasePoint(doc)
        return DB.BasePoint.GetSurveyPoint(doc)
    except Exception:
        pass
    # Revit < 2022: buscar por categoria
    try:
        categoria = DB.BuiltInCategory.OST_ProjectBasePoint if de_proyecto else DB.BuiltInCategory.OST_SharedBasePoint
        for elem in DB.FilteredElementCollector(doc).OfCategory(categoria).WhereElementIsNotElementType():
            return elem
    except Exception:
        pass
    return None


def _posicion_mm(punto, compartida=False):
    try:
        xyz = punto.SharedPosition if compartida else punto.Position
        return punto_a_mm(xyz)
    except Exception:
        return None


def _parametro_mm(punto, bip):
    try:
        p = punto.get_Parameter(getattr(DB.BuiltInParameter, bip))
        if p:
            return round(p.AsDouble() * FEET_TO_MM, 1)
    except Exception:
        pass
    return None


def norte_verdadero_grados(doc):
    """Angulo del norte verdadero respecto al norte de proyecto, en grados."""
    try:
        posicion = doc.ActiveProjectLocation.GetProjectPosition(DB.XYZ.Zero)
        return round(math.degrees(posicion.Angle), 4)
    except Exception:
        pass
    try:
        punto = _punto_base(doc, True)
        p = punto.get_Parameter(DB.BuiltInParameter.BASEPOINT_ANGLETON_PARAM)
        return round(math.degrees(p.AsDouble()), 4)
    except Exception:
        return None


def describir_punto(doc, de_proyecto):
    punto = _punto_base(doc, de_proyecto)
    if punto is None:
        return None
    datos = {
        "id": get_element_id_value(punto),
        "posicion_mm": _posicion_mm(punto),
        "posicion_compartida_mm": _posicion_mm(punto, compartida=True),
        "este_oeste_mm": _parametro_mm(punto, "BASEPOINT_EASTWEST_PARAM"),
        "norte_sur_mm": _parametro_mm(punto, "BASEPOINT_NORTHSOUTH_PARAM"),
        "elevacion_mm": _parametro_mm(punto, "BASEPOINT_ELEVATION_PARAM"),
    }
    try:
        datos["fijado"] = bool(punto.Pinned)
    except Exception:
        pass
    try:
        datos["recortado"] = bool(punto.Clipped)
    except Exception:
        pass
    return datos


def ubicacion_proyecto(doc):
    """Resumen completo: puntos base, norte y sistema de coordenadas activo."""
    resumen = {
        "project_base_point": describir_punto(doc, True),
        "survey_point": describir_punto(doc, False),
        "true_north_deg": norte_verdadero_grados(doc),
        "active_project_location": None,
        "project_locations": [],
    }
    try:
        activa = doc.ActiveProjectLocation
        resumen["active_project_location"] = {
            "id": get_element_id_value(activa),
            "name": get_element_name(activa),
        }
        try:
            posicion = activa.GetProjectPosition(DB.XYZ.Zero)
            resumen["active_project_location"]["origin_offset_mm"] = {
                "east_west": round(posicion.EastWest * FEET_TO_MM, 1),
                "north_south": round(posicion.NorthSouth * FEET_TO_MM, 1),
                "elevation": round(posicion.Elevation * FEET_TO_MM, 1),
            }
        except Exception:
            pass
        try:
            resumen["active_project_location"]["site_name"] = get_element_name(activa.GetSiteLocation())
        except Exception:
            pass
    except Exception as error:
        logger.debug("ActiveProjectLocation no disponible: %s", str(error))
    try:
        for ubicacion in doc.ProjectLocations:
            resumen["project_locations"].append({
                "id": get_element_id_value(ubicacion),
                "name": get_element_name(ubicacion),
            })
    except Exception:
        pass
    return resumen


def resumen_para_model_info(doc):
    """Version corta para get_revit_model_info."""
    pb = describir_punto(doc, True)
    sp = describir_punto(doc, False)
    return {
        "project_base_point_mm": pb["posicion_mm"] if pb else None,
        "survey_point_mm": sp["posicion_mm"] if sp else None,
        "true_north_deg": norte_verdadero_grados(doc),
    }
