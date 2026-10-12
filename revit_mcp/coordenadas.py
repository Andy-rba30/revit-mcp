# -*- coding: UTF-8 -*-
"""
Coordenadas Module for Revit MCP
Punto base del proyecto, punto de reconocimiento, norte verdadero y sistema
de coordenadas compartido. Lo usan get_revit_model_info (resumen) y las rutas
get_project_location / set_project_location.

Unidades: las posiciones se devuelven en milimetros y los angulos en grados.

0.6.5: `shared_coordinates` en set_project_location (el "Especificar coordenadas
en un punto" de Revit, ProjectLocation.SetProjectPosition) para georreferenciar
el modelo con los valores que da Civil 3D (extension_superficie) y poder
vincular el DWG por coordenadas compartidas; evaluar_extension dice a que
distancia del origen interno quedo un vinculo.
"""

from utils import get_element_name, get_element_id_value, make_element_id, punto_a_mm, xyz_desde_mm, FEET_TO_MM, MM_TO_FEET
from seguridad import requiere_token
from escritura import ejecutar, transaccion, simulacion, EscrituraRechazada, es_forzado
from pyrevit import routes, revit, DB
import math
import traceback
import logging

logger = logging.getLogger(__name__)

# Autodesk: toda la geometria deberia quedar a menos de 10 millas (16 km) del
# origen interno; a partir de 20 millas (33 km) Revit avisa ("muy lejos del
# origen") y, al vincular un CAD, cambia la colocacion a "centro con centro"
# por su cuenta. Un DWG en UTM vinculado "origen con origen" cae a cientos de
# kilometros: por eso hay que fijar antes las coordenadas compartidas.
RADIO_RECOMENDADO_MM = 16093400.0
LIMITE_REVIT_MM = 32186900.0
UNIDADES_A_MM = {"m": 1000.0, "mm": 1.0, "ft": 304.8}


def coordenadas_definidas(doc):
    """True si el proyecto ya tiene coordenadas compartidas distintas de las internas."""
    try:
        posicion = doc.ActiveProjectLocation.GetProjectPosition(DB.XYZ.Zero)
        return any(abs(float(v)) > 1e-6
                   for v in (posicion.EastWest, posicion.NorthSouth, posicion.Elevation, posicion.Angle))
    except Exception:
        return False


def posicion_compartida(doc, xyz, unidades="m"):
    """Coordenadas compartidas de un punto interno: {east, north, elevation, true_north_deg, units}."""
    posicion = doc.ActiveProjectLocation.GetProjectPosition(xyz)
    factor = FEET_TO_MM / UNIDADES_A_MM[unidades]
    return {
        "east": round(posicion.EastWest * factor, 4),
        "north": round(posicion.NorthSouth * factor, 4),
        "elevation": round(posicion.Elevation * factor, 4),
        "true_north_deg": round(math.degrees(posicion.Angle), 4),
        "units": unidades,
    }


def _sitio(doc):
    """Latitud, longitud y sistema GIS del emplazamiento (SiteLocation), si Revit los da."""
    sitio = {}
    try:
        sl = doc.SiteLocation
        sitio["latitude_deg"] = round(math.degrees(sl.Latitude), 7)
        sitio["longitude_deg"] = round(math.degrees(sl.Longitude), 7)
        try:
            # Revit 2018+: mismos codigos que Civil 3D (p. ej. "UTM84-18S"); vacio sin sistema
            sitio["geo_coordinate_system"] = sl.GeoCoordinateSystemId or None
        except Exception:
            pass
    except Exception:
        pass
    return sitio


def evaluar_extension(doc, element_id):
    """Donde quedo un vinculo o importacion: distancia de la esquina mas lejana de su
    caja al origen interno (m), avisos a partir de 16 km y de 33 km, y coordenadas
    compartidas de su centro. None si el elemento no tiene caja."""
    if element_id is None:
        return None
    try:
        elem = doc.GetElement(make_element_id(element_id))
        caja = elem.get_BoundingBox(None) if elem is not None else None
        if caja is None:
            return None
        esquinas = [(x, y, z) for x in (caja.Min.X, caja.Max.X) for y in (caja.Min.Y, caja.Max.Y)
                    for z in (caja.Min.Z, caja.Max.Z)]
        lejana_mm = max(math.sqrt(x * x + y * y + z * z) for x, y, z in esquinas) * FEET_TO_MM
        centro = DB.XYZ((caja.Min.X + caja.Max.X) / 2.0, (caja.Min.Y + caja.Max.Y) / 2.0,
                        (caja.Min.Z + caja.Max.Z) / 2.0)
        resultado = {
            "max_distance_m": round(lejana_mm / 1000.0, 1),
            "far_from_origin": lejana_mm > RADIO_RECOMENDADO_MM,
            "beyond_revit_limit": lejana_mm > LIMITE_REVIT_MM,
            "center_internal_mm": punto_a_mm(centro),
        }
        try:
            resultado["center_shared_m"] = posicion_compartida(doc, centro, "m")
        except Exception:
            pass
        if resultado["beyond_revit_limit"]:
            resultado["hint"] = (
                "geometry is {:.1f} km from the internal origin, beyond the 33 km (20 miles) Revit supports: "
                "Revit warns and may override the placement. For a georeferenced CAD file call "
                "set_project_location(shared_coordinates={{east, north, elevation}}) with the real-world "
                "coordinates of the internal origin (e.g. the surface centre) and link again with "
                "placement='shared'.".format(lejana_mm / 1e6))
        elif resultado["far_from_origin"]:
            resultado["hint"] = (
                "geometry is {:.1f} km from the internal origin, beyond the 16 km (10 miles) Autodesk "
                "recommends; precision may degrade. Consider set_project_location(shared_coordinates=...) "
                "plus placement='shared'.".format(lejana_mm / 1e6))
        return resultado
    except Exception as error:
        logger.debug("evaluar_extension: %s", str(error))
        return None


def _nueva_posicion(doc, este_ft, norte_ft, cota_ft, angulo_rad):
    try:
        return doc.Application.Create.NewProjectPosition(este_ft, norte_ft, cota_ft, angulo_rad)
    except Exception:
        return DB.ProjectPosition(este_ft, norte_ft, cota_ft, angulo_rad)


def _fijar_posicion(doc, xyz, posicion):
    """SetProjectPosition sobre la ubicacion activa. Desde Revit 2018 la ubicacion
    "Internal" es de solo lectura: si es la activa, se activa otra (o se duplica
    una como "MCP") y se fija ahi. Devuelve (ubicacion, cambiada)."""
    activa = doc.ActiveProjectLocation
    try:
        activa.SetProjectPosition(xyz, posicion)
        return activa, False
    except Exception as primer_error:
        id_activa = get_element_id_value(activa)
        candidatas = [u for u in doc.ProjectLocations if get_element_id_value(u) != id_activa]
        if not candidatas:
            candidatas = [activa.Duplicate("MCP")]
        destino = candidatas[0]
        doc.ActiveProjectLocation = destino
        destino.SetProjectPosition(xyz, posicion)
        logger.info("La ubicacion '%s' rechazo SetProjectPosition (%s); se activo '%s'",
                    get_element_name(activa), str(primer_error), get_element_name(destino))
        return destino, True


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


def puntos_base(doc):
    """[(etiqueta, DB.BasePoint)] del punto base del proyecto y del punto de reconocimiento que existan."""
    return [(etiqueta, punto) for etiqueta, punto in
            (("project base point", _punto_base(doc, True)), ("survey point", _punto_base(doc, False)))
            if punto is not None]


def comprobar_puntos_base_libres(puntos):
    """409 si alguno de los puntos base esta fijado (Pinned) o recortado (Clipped).

    Moverlo falla en Revit o cambia el sistema de coordenadas compartidas de todo
    el modelo. Lo usan set_project_location e import_from_civil (macros)."""
    for etiqueta, punto in puntos:
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
        "shared_coordinates_set": coordenadas_definidas(doc),
        "active_project_location": None,
        "project_locations": [],
        "site": _sitio(doc),
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
            compartidas = data.get("shared_coordinates")
            if base_mm is None and survey_mm is None and norte is None and link_id is None and compartidas is None:
                raise EscrituraRechazada(
                    "Give base_point_mm, survey_point_mm, true_north_deg, acquire_from_link_id and/or shared_coordinates", 400
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
            # 0.6.5: "Especificar coordenadas en un punto" con valores (Civil 3D)
            posicion_nueva = punto_xyz = unidades = None
            if compartidas is not None:
                if base_mm is not None or survey_mm is not None or link_id is not None:
                    raise EscrituraRechazada(
                        "shared_coordinates already defines the base point, survey point and coordinate system: "
                        "send it alone (optionally with true_north_deg)", 400
                    )
                if not isinstance(compartidas, dict) or compartidas.get("east") is None or compartidas.get("north") is None:
                    raise EscrituraRechazada(
                        "shared_coordinates must be {\"east\", \"north\", \"elevation\"}: the real-world "
                        "coordinates of at_point_mm (the internal origin by default)", 400
                    )
                unidades = str(data.get("units") or "m").strip().lower()
                if unidades not in UNIDADES_A_MM:
                    raise EscrituraRechazada("units must be one of m, mm, ft", 400)
                try:
                    este_mm = float(compartidas["east"]) * UNIDADES_A_MM[unidades]
                    norte_mm = float(compartidas["north"]) * UNIDADES_A_MM[unidades]
                    cota_mm = float(compartidas.get("elevation") or 0) * UNIDADES_A_MM[unidades]
                except (TypeError, ValueError):
                    raise EscrituraRechazada("shared_coordinates east, north and elevation must be numbers", 400)
                try:
                    punto_xyz = xyz_desde_mm(data.get("at_point_mm") or {"x": 0, "y": 0, "z": 0})
                except ValueError as error:
                    raise EscrituraRechazada("at_point_mm: {}".format(error), 400)
                angulo = math.radians(norte) if norte is not None else math.radians(norte_verdadero_grados(doc) or 0.0)
                acciones.insert(0, {
                    "accion": "fijar_coordenadas_compartidas", "punto_mm": punto_a_mm(punto_xyz),
                    "east": compartidas["east"], "north": compartidas["north"],
                    "elevation": compartidas.get("elevation") or 0, "units": unidades,
                    "true_north_deg": round(math.degrees(angulo), 4),
                    "antes": posicion_compartida(doc, punto_xyz, unidades),
                })
                if coordenadas_definidas(doc) and not es_forzado(data):
                    raise EscrituraRechazada(
                        "The project already has shared coordinates (project position is not zero); "
                        "shared_coordinates would overwrite them. Pass forzar=true to do it anyway.", 409,
                        {"shared_coordinates_set": True, "antes": ubicacion_proyecto(doc)},
                    )
                posicion_nueva = _nueva_posicion(doc, este_mm * MM_TO_FEET, norte_mm * MM_TO_FEET,
                                                 cota_mm * MM_TO_FEET, angulo)
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
                comprobar_puntos_base_libres((("project base point", punto_base), ("survey point", punto_rec)))

            antes = ubicacion_proyecto(doc)
            if ctx["simular"]:
                return simulacion(acciones, antes=antes)

            ubicacion_usada = cambiada = None
            with transaccion(doc, "Coordenadas del proyecto"):
                if posicion_nueva is not None:
                    ubicacion_usada, cambiada = _fijar_posicion(doc, punto_xyz, posicion_nueva)
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
            if posicion_nueva is not None:
                real = posicion_compartida(doc, punto_xyz, unidades)
                tolerancia = 1.0 / UNIDADES_A_MM[unidades]   # 1 mm en las unidades pedidas
                pedido = {"east": float(compartidas["east"]), "north": float(compartidas["north"]),
                          "elevation": float(compartidas.get("elevation") or 0)}
                if any(abs(real[e] - pedido[e]) > tolerancia for e in ("east", "north", "elevation")):
                    desajustes.append("shared coordinates of the point are {} instead of {}".format(real, pedido))
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
            if ubicacion_usada is not None:
                resultado["project_location"] = {"name": get_element_name(ubicacion_usada), "switched": cambiada}
                resultado["message"] = (
                    "Shared coordinates set on location '{}'{}: internal point {} mm is now E={} N={} elevation={} {}. "
                    "Link georeferenced CAD files with link_file(placement=\"shared\")."
                ).format(get_element_name(ubicacion_usada),
                         " (activated instead of the read-only one)" if cambiada else "",
                         punto_a_mm(punto_xyz), compartidas["east"], compartidas["north"],
                         compartidas.get("elevation") or 0, unidades)
            return resultado

        return ejecutar(doc, "/set_project_location/", request, cuerpo)

    logger.info("Coordenadas routes registered successfully")
