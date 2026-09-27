# -*- coding: UTF-8 -*-
"""
Coordenadas Module for Revit MCP
Punto base del proyecto, punto de reconocimiento, norte verdadero y sistema
de coordenadas compartido. Lo usan get_revit_model_info (resumen) y las rutas
get_project_location / set_project_location.

Unidades: las posiciones se devuelven en milimetros y los angulos en grados.
"""

from utils import get_element_name, get_element_id_value, make_element_id, punto_a_mm, xyz_desde_mm, FEET_TO_MM
from seguridad import requiere_token
from escritura import ejecutar, transaccion, simulacion, EscrituraRechazada, es_forzado
from pyrevit import routes, revit, DB
import math
import traceback
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


def register_coordenadas_routes(api):
    """Register project location routes with the API."""

    @api.route("/project_location/", methods=["GET"])
    @requiere_token
    def get_project_location(doc):
        """Punto base, punto de reconocimiento, norte verdadero y sistema activo."""
        try:
            if not doc:
                return routes.make_response(data={"error": "No active Revit document"}, status=503)
            datos = ubicacion_proyecto(doc)
            datos["status"] = "success"
            return routes.make_response(data=datos)
        except Exception as e:
            logger.error("get_project_location failed: {}".format(str(e)))
            return routes.make_response(
                data={"error": str(e), "traceback": traceback.format_exc()}, status=500
            )

    @api.route("/set_project_location/", methods=["POST"])
    @requiere_token
    def set_project_location(doc, request):
        """Mueve el punto base / de reconocimiento, gira el norte o adquiere coordenadas. Acepta `simular`."""

        def cuerpo(ctx):
            data = ctx["data"]
            base_mm = data.get("base_point_mm")
            survey_mm = data.get("survey_point_mm")
            norte = data.get("true_north_deg")
            link_id = data.get("acquire_from_link_id")
            if base_mm is None and survey_mm is None and norte is None and link_id is None:
                raise EscrituraRechazada(
                    "Give base_point_mm, survey_point_mm, true_north_deg and/or acquire_from_link_id", 400
                )

            acciones = []
            punto_base = _punto_base(doc, True) if base_mm is not None else None
            punto_rec = _punto_base(doc, False) if survey_mm is not None else None
            if base_mm is not None:
                if punto_base is None:
                    raise EscrituraRechazada("Project base point not found", 404)
                try:
                    destino = xyz_desde_mm(base_mm)
                except ValueError as error:
                    raise EscrituraRechazada("base_point_mm: {}".format(error), 400)
                acciones.append({"accion": "mover_punto_base", "antes_mm": _posicion_mm(punto_base),
                                 "despues_mm": punto_a_mm(destino)})
            if survey_mm is not None:
                if punto_rec is None:
                    raise EscrituraRechazada("Survey point not found", 404)
                try:
                    destino_rec = xyz_desde_mm(survey_mm)
                except ValueError as error:
                    raise EscrituraRechazada("survey_point_mm: {}".format(error), 400)
                acciones.append({"accion": "mover_punto_reconocimiento", "antes_mm": _posicion_mm(punto_rec),
                                 "despues_mm": punto_a_mm(destino_rec)})
            if norte is not None:
                try:
                    norte = float(norte)
                except (TypeError, ValueError):
                    raise EscrituraRechazada("true_north_deg must be a number (degrees)", 400)
                acciones.append({"accion": "girar_norte_verdadero", "antes_deg": norte_verdadero_grados(doc),
                                 "despues_deg": norte})
            vinculo = None
            if link_id is not None:
                vinculo = doc.GetElement(make_element_id(link_id))
                if vinculo is None or not isinstance(vinculo, DB.RevitLinkInstance):
                    raise EscrituraRechazada("acquire_from_link_id {} is not a Revit link instance".format(link_id), 404)
                acciones.append({"accion": "adquirir_coordenadas", "link_id": int(link_id),
                                 "link": get_element_name(vinculo)})

            if link_id is not None and (base_mm is not None or survey_mm is not None or norte is not None):
                raise EscrituraRechazada(
                    "acquire_from_link_id overwrites the base point, survey point and true north: "
                    "send it alone, without the other arguments", 400
                )
            if not es_forzado(data):
                for etiqueta, punto in (("project base point", punto_base), ("survey point", punto_rec)):
                    if punto is None:
                        continue
                    fijado = recortado = False
                    try:
                        fijado = bool(punto.Pinned)
                    except Exception:
                        pass
                    try:
                        recortado = bool(punto.Clipped)
                    except Exception:
                        pass
                    if fijado or recortado:
                        raise EscrituraRechazada(
                            "The {} is {}: moving it {}. Unpin/unclip it in Revit first or pass forzar=true.".format(
                                etiqueta,
                                "pinned" if fijado and not recortado else "clipped" if recortado and not fijado else "pinned and clipped",
                                "fails in Revit" if fijado and not recortado else "changes the shared coordinate system of the whole model",
                            ),
                            409,
                            {"pinned": fijado, "clipped": recortado},
                        )

            antes = ubicacion_proyecto(doc)
            if ctx["simular"]:
                return simulacion(acciones, antes=antes)

            with transaccion(doc, "Coordenadas del proyecto"):
                if base_mm is not None:
                    delta = destino.Subtract(punto_base.Position)
                    DB.ElementTransformUtils.MoveElement(doc, punto_base.Id, delta)
                if survey_mm is not None:
                    delta = destino_rec.Subtract(punto_rec.Position)
                    DB.ElementTransformUtils.MoveElement(doc, punto_rec.Id, delta)
                if norte is not None:
                    ubicacion = doc.ActiveProjectLocation
                    posicion = ubicacion.GetProjectPosition(DB.XYZ.Zero)
                    posicion.Angle = math.radians(norte)
                    ubicacion.SetProjectPosition(DB.XYZ.Zero, posicion)
                if vinculo is not None:
                    doc.AcquireCoordinates(vinculo.Id)

            despues = ubicacion_proyecto(doc)
            desajustes = []
            if base_mm is not None:
                real = (despues.get("project_base_point") or {}).get("posicion_mm") or {}
                pedido = punto_a_mm(destino)
                if any(abs(real.get(e, 0) - pedido[e]) > 1.0 for e in ("x", "y", "z")):
                    desajustes.append("project base point is at {} instead of {}".format(real, pedido))
            if survey_mm is not None:
                real = (despues.get("survey_point") or {}).get("posicion_mm") or {}
                pedido = punto_a_mm(destino_rec)
                if any(abs(real.get(e, 0) - pedido[e]) > 1.0 for e in ("x", "y", "z")):
                    desajustes.append("survey point is at {} instead of {}".format(real, pedido))
            if norte is not None:
                real = despues.get("true_north_deg")
                if real is None or abs(((real - norte) + 180.0) % 360.0 - 180.0) > 0.01:
                    desajustes.append("true north is {} deg instead of {}".format(real, norte))
            resultado = {
                "acciones": acciones,
                "antes": antes,
                "despues": despues,
                "ok": not desajustes,
                "message": "Applied {} project location change(s)".format(len(acciones)),
            }
            resultado["verificacion"] = (
                {"coincide": True} if not desajustes else {"coincide": False, "detalle": "; ".join(desajustes)}
            )
            return resultado

        return ejecutar(doc, "/set_project_location/", request, cuerpo)

    logger.info("Coordenadas routes registered successfully")
