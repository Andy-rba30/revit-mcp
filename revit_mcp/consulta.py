# -*- coding: UTF-8 -*-
"""
Consulta Module for Revit MCP
Rutas de solo lectura anadidas en la version 0.2.0:

  GET  /warnings/         advertencias del modelo (doc.GetWarnings)
  GET  /worksets/         subproyectos (id, nombre, propietario, editable, abierto)
  GET  /phases_options/   fases y opciones de diseno
  GET  /links/            vinculos RVT / CAD / IFC
  POST /find_elements/    busqueda por categoria, nombre, tipo, nivel y parametro
  POST /element_geometry/ caja envolvente, curvas de ubicacion o solidos
  POST /element_types/    tipos disponibles de una categoria
  GET  /log/              ultimas lineas de mcp_log.jsonl

Ninguna abre transaccion ni modifica el modelo.

0.3.0: GET /warnings/?group_by=description agrupa las advertencias por
FailureDefinitionId con recuento, ids y sugerencia (navegacion.agrupar_avisos),
y POST /find_elements/ es un alias de POST /query/ (navegacion.consultar_elementos)
que conserva sus campos de siempre.
"""

from utils import get_element_name, get_element_id_value, make_element_id, punto_a_mm, FEET_TO_MM, buscar_por_nombre
from seguridad import requiere_token
from escritura import describir_elemento, bbox_mm, nombre_nivel, nombre_categoria, nombre_tipo, leer_log, datos_peticion, EscrituraRechazada
from parameters import valor_parametro
from clash import _resolve_bic
from pyrevit import routes, revit, DB
import traceback
import logging

logger = logging.getLogger(__name__)

SQFT_TO_SQM = 0.09290304
CUFT_TO_CUM = 0.028316846592

# Parametros de tipo que suelen describir un tipo (se devuelven los que existan)
PARAMETROS_TIPO_PRINCIPALES = [
    "Width", "Depth", "Height", "Thickness", "Default Thickness", "b", "h", "Diameter",
    "Length", "Type Mark", "Description", "Structural Material", "Material", "Function",
    "Fire Rating", "Manufacturer", "Model", "Cost", "Keynote", "Assembly Code",
]


def _respuesta_error(e):
    logger.error("Consulta fallida: {}".format(str(e)))
    return routes.make_response(
        data={"error": str(e), "traceback": traceback.format_exc()}, status=500
    )


def _sin_documento():
    return routes.make_response(data={"error": "No active Revit document"}, status=503)


def _query(request):
    params = getattr(request, "query_params", None) or {}
    return params if isinstance(params, dict) else {}


def _entero(valor, por_defecto):
    try:
        numero = int(valor)
        return numero if numero > 0 else por_defecto
    except (TypeError, ValueError):
        return por_defecto


def _texto_seguro(valor):
    try:
        return unicode(valor)
    except Exception:
        try:
            return str(valor)
        except Exception:
            return u"?"


def _ids(coleccion):
    resultado = []
    try:
        for identificador in coleccion:
            try:
                resultado.append(get_element_id_value(identificador))
            except Exception:
                continue
    except Exception:
        pass
    return resultado


def _describir_aviso(aviso):
    try:
        severidad = str(aviso.GetSeverity()).split(".")[-1]
    except Exception:
        severidad = "?"
    try:
        descripcion = _texto_seguro(aviso.GetDescriptionText())
    except Exception:
        descripcion = u"?"
    datos = {
        "descripcion": descripcion,
        "severidad": severidad,
        "element_ids": _ids(aviso.GetFailingElements()),
    }
    try:
        adicionales = _ids(aviso.GetAdditionalElements())
        if adicionales:
            datos["additional_element_ids"] = adicionales
    except Exception:
        pass
    return datos


def _workset_id(workset):
    try:
        return int(workset.Id.IntegerValue)
    except Exception:
        try:
            return int(workset.Id.Value)
        except Exception:
            return None


def listar_worksets(doc):
    """[{id, nombre, propietario, editable, abierto, activo, por_defecto}]"""
    if not doc.IsWorkshared:
        return []
    tabla = doc.GetWorksetTable()
    try:
        activo = tabla.GetActiveWorksetId()
        activo = int(activo.IntegerValue)
    except Exception:
        activo = None
    resultado = []
    coleccion = DB.FilteredWorksetCollector(doc).OfKind(DB.WorksetKind.UserWorkset)
    for ws in coleccion:
        identificador = _workset_id(ws)
        datos = {
            "id": identificador,
            "nombre": _texto_seguro(ws.Name),
            "propietario": _texto_seguro(ws.Owner) if ws.Owner else None,
            "editable": bool(ws.IsEditable),
            "abierto": bool(ws.IsOpen),
            "activo": identificador == activo,
        }
        try:
            datos["por_defecto"] = bool(ws.IsDefaultWorkset)
        except Exception:
            pass
        try:
            datos["visible_por_defecto"] = bool(ws.IsVisibleByDefault)
        except Exception:
            pass
        resultado.append(datos)
    resultado.sort(key=lambda w: w["nombre"])
    return resultado


def _ruta_referencia(tipo):
    """Ruta en disco de un RevitLinkType / CADLinkType, o None."""
    try:
        referencia = tipo.GetExternalFileReference()
        if referencia is None:
            return None, None
        ruta = DB.ModelPathUtils.ConvertModelPathToUserVisiblePath(referencia.GetAbsolutePath())
        estado = str(referencia.GetLinkedFileStatus()).split(".")[-1]
        return _texto_seguro(ruta), estado
    except Exception:
        return None, None


def _parametro_texto(elem, nombres):
    for nombre in nombres:
        try:
            p = buscar_por_nombre(elem, nombre)
            if p and p.HasValue:
                return _texto_seguro(p.AsString() or p.AsValueString() or u"")
        except Exception:
            continue
    return None


def _posicion_vinculo(instancia, sitio):
    """interno / compartido / origen segun sitio compartido y transformacion."""
    if sitio and not sitio.startswith("<"):
        return "compartido"
    try:
        transformacion = instancia.GetTotalTransform()
        if transformacion.IsIdentity:
            return "origen"
    except Exception:
        pass
    return "interno"


def listar_vinculos(doc):
    vinculos = []
    # Vinculos RVT (y IFC, que Revit vincula a traves de un RVT cache)
    for instancia in DB.FilteredElementCollector(doc).OfClass(DB.RevitLinkInstance).ToElements():
        try:
            tipo = doc.GetElement(instancia.GetTypeId())
            ruta, estado = _ruta_referencia(tipo)
            cargado = False
            try:
                cargado = instancia.GetLinkDocument() is not None
            except Exception:
                cargado = estado == "Loaded"
            sitio = _parametro_texto(instancia, ["Shared Site", "Emplazamiento compartido"])
            origen = None
            try:
                origen = punto_a_mm(instancia.GetTotalTransform().Origin)
            except Exception:
                pass
            vinculos.append({
                "id": get_element_id_value(instancia),
                "type_id": get_element_id_value(tipo) if tipo is not None else None,
                "nombre": get_element_name(instancia),
                "tipo": "IFC" if (ruta or "").lower().endswith(".ifc") else "RVT",
                "ruta": ruta,
                "cargado": bool(cargado),
                "estado": estado,
                "posicion": _posicion_vinculo(instancia, sitio),
                "sitio_compartido": sitio,
                "origen_mm": origen,
                "fijado": bool(getattr(instancia, "Pinned", False)),
            })
        except Exception as e:
            logger.warning("Could not process RVT link: {}".format(str(e)))
    # CAD (DWG/DXF/DGN...) vinculados o importados
    for instancia in DB.FilteredElementCollector(doc).OfClass(DB.ImportInstance).ToElements():
        try:
            tipo = doc.GetElement(instancia.GetTypeId())
            ruta, estado = _ruta_referencia(tipo) if tipo is not None else (None, None)
            nombre = None
            try:
                nombre = get_element_name(instancia.Category) if instancia.Category else None
            except Exception:
                nombre = None
            if not nombre and tipo is not None:
                nombre = get_element_name(tipo)
            origen = None
            try:
                origen = punto_a_mm(instancia.GetTotalTransform().Origin)
            except Exception:
                pass
            extension = (ruta or nombre or "").split(".")[-1].upper() if (ruta or nombre) else "CAD"
            vinculos.append({
                "id": get_element_id_value(instancia),
                "type_id": get_element_id_value(tipo) if tipo is not None else None,
                "nombre": nombre,
                "tipo": extension if len(extension) <= 4 else "CAD",
                "ruta": ruta,
                "cargado": (estado == "Loaded") if estado else None,
                "estado": estado,
                "vinculado": bool(instancia.IsLinked),
                "posicion": "origen" if (origen and all(abs(origen[e]) < 0.5 for e in ("x", "y", "z"))) else "interno",
                "origen_mm": origen,
                "solo_vista": bool(instancia.ViewSpecific),
                "fijado": bool(getattr(instancia, "Pinned", False)),
            })
        except Exception as e:
            logger.warning("Could not process CAD import: {}".format(str(e)))
    return vinculos


def _solidos(geometria):
    """Recorre GeometryElement (incluidas instancias) y devuelve los solidos con volumen."""
    solidos = []
    if geometria is None:
        return solidos
    for objeto in geometria:
        try:
            if isinstance(objeto, DB.Solid):
                if objeto.Volume > 1e-9:
                    solidos.append(objeto)
            elif isinstance(objeto, DB.GeometryInstance):
                solidos.extend(_solidos(objeto.GetInstanceGeometry()))
        except Exception:
            continue
    return solidos


def _curvas_ubicacion(elem):
    loc = getattr(elem, "Location", None)
    if loc is None:
        return None
    if hasattr(loc, "Curve"):
        curva = loc.Curve
        datos = {
            "tipo": "curva",
            "clase": type(curva).__name__,
            "start_mm": punto_a_mm(curva.GetEndPoint(0)),
            "end_mm": punto_a_mm(curva.GetEndPoint(1)),
            "length_mm": round(curva.Length * FEET_TO_MM, 1),
        }
        try:
            if isinstance(curva, DB.Arc):
                datos["center_mm"] = punto_a_mm(curva.Center)
                datos["radius_mm"] = round(curva.Radius * FEET_TO_MM, 1)
        except Exception:
            pass
        return datos
    if hasattr(loc, "Point"):
        datos = {"tipo": "punto", "point_mm": punto_a_mm(loc.Point)}
        try:
            import math
            datos["rotation_deg"] = round(math.degrees(loc.Rotation), 4)
        except Exception:
            pass
        return datos
    return {"tipo": "desconocido"}


def _tipo_de(doc, elem):
    try:
        tipo_id = elem.GetTypeId()
        if tipo_id and tipo_id != DB.ElementId.InvalidElementId:
            return doc.GetElement(tipo_id)
    except Exception:
        pass
    return None


def _nombre_familia(tipo):
    try:
        if hasattr(tipo, "Family") and tipo.Family:
            return get_element_name(tipo.Family)
    except Exception:
        pass
    try:
        return _texto_seguro(tipo.FamilyName)
    except Exception:
        return None


def register_consulta_routes(api):
    """Register the read-only query routes with the API."""

    @api.route("/warnings/", methods=["GET"])
    @requiere_token
    def list_warnings(doc, request):
        """Advertencias del modelo: descripcion, severidad y element_ids. ?max= (100)."""
        try:
            if not doc:
                return _sin_documento()
            consulta = _query(request)
            maximo = _entero(consulta.get("max"), 100)
            group_by = _texto_seguro(consulta.get("group_by") or u"").strip().lower()
            if group_by and group_by != "description":
                return routes.make_response(
                    data={"error": "group_by only supports 'description'"}, status=400
                )
            avisos = list(doc.GetWarnings())
            if group_by:
                # 0.3.0: agrupadas por FailureDefinitionId (independiente del idioma)
                # con recuento, ids y una sugerencia por tipo.
                from navegacion import agrupar_avisos

                grupos = agrupar_avisos(avisos)
                return routes.make_response(data={
                    "status": "success",
                    "group_by": "description",
                    "groups": grupos[:maximo],
                    "count": min(len(grupos), maximo),
                    "group_count": len(grupos),
                    "total": len(avisos),
                    "truncated": len(grupos) > maximo,
                })
            lista = [_describir_aviso(a) for a in avisos[:maximo]]
            return routes.make_response(data={
                "status": "success",
                "warnings": lista,
                "count": len(lista),
                "total": len(avisos),
                "truncated": len(avisos) > len(lista),
            })
        except Exception as e:
            return _respuesta_error(e)

    @api.route("/worksets/", methods=["GET"])
    @requiere_token
    def list_worksets(doc):
        """Subproyectos del modelo (vacio si no es de trabajo compartido)."""
        try:
            if not doc:
                return _sin_documento()
            worksets = listar_worksets(doc)
            return routes.make_response(data={
                "status": "success",
                "is_workshared": bool(doc.IsWorkshared),
                "worksets": worksets,
                "count": len(worksets),
            })
        except Exception as e:
            return _respuesta_error(e)

    @api.route("/phases_options/", methods=["GET"])
    @requiere_token
    def list_phases_and_options(doc):
        """Fases (en orden) y opciones de diseno con su conjunto."""
        try:
            if not doc:
                return _sin_documento()
            fases = []
            try:
                orden = 0
                for fase in doc.Phases:
                    fases.append({
                        "id": get_element_id_value(fase),
                        "nombre": get_element_name(fase),
                        "orden": orden,
                    })
                    orden += 1
            except Exception as e:
                logger.warning("Could not list phases: {}".format(str(e)))

            opciones = []
            activa = None
            try:
                activa = get_element_id_value(DB.DesignOption.GetActiveDesignOptionId(doc))
                if activa == -1:
                    activa = None
            except Exception:
                activa = None
            for opcion in DB.FilteredElementCollector(doc).OfClass(DB.DesignOption).ToElements():
                try:
                    datos = {
                        "id": get_element_id_value(opcion),
                        "nombre": get_element_name(opcion),
                        "es_principal": bool(opcion.IsPrimary),
                        "activa": get_element_id_value(opcion) == activa,
                    }
                    try:
                        conjunto = opcion.get_Parameter(DB.BuiltInParameter.OPTION_SET_ID)
                        if conjunto:
                            conjunto_elem = doc.GetElement(conjunto.AsElementId())
                            if conjunto_elem is not None:
                                datos["conjunto"] = get_element_name(conjunto_elem)
                                datos["conjunto_id"] = get_element_id_value(conjunto_elem)
                    except Exception:
                        pass
                    opciones.append(datos)
                except Exception:
                    continue
            return routes.make_response(data={
                "status": "success",
                "phases": fases,
                "design_options": opciones,
                "active_design_option_id": activa,
            })
        except Exception as e:
            return _respuesta_error(e)

    @api.route("/links/", methods=["GET"])
    @requiere_token
    def list_links(doc):
        """Vinculos RVT/DWG/IFC: id, nombre, ruta, cargado, posicion."""
        try:
            if not doc:
                return _sin_documento()
            vinculos = listar_vinculos(doc)
            return routes.make_response(data={
                "status": "success",
                "links": vinculos,
                "count": len(vinculos),
            })
        except Exception as e:
            return _respuesta_error(e)

    @api.route("/find_elements/", methods=["POST"])
    @requiere_token
    def find_elements(doc, request):
        """Busca elementos por categoria, nombre, tipo, nivel y/o parametro.

        Desde 0.3.0 es un alias de POST /query/ (navegacion.consultar_elementos)
        y conserva sus campos: elements, ids, count, total_matched, scanned,
        truncated y filters. `max` se limita a 500 (page_size de /query/)."""
        try:
            if not doc:
                return _sin_documento()
            data = datos_peticion(request)
            category = data.get("category")
            name_contains = (data.get("name_contains") or "").strip().lower()
            type_name = (data.get("type_name") or "").strip()
            level_name = (data.get("level_name") or "").strip()
            parameter_name = (data.get("parameter_name") or "").strip()
            parameter_value = data.get("parameter_value")
            maximo = _entero(data.get("max"), 100)
            solo_ids = bool(data.get("ids_only", False))

            if not any([category, name_contains, type_name, level_name, parameter_name]):
                return routes.make_response(
                    data={"error": "Give at least one filter: category, name_contains, type_name, level_name or parameter_name"},
                    status=400,
                )
            criterios = {
                "category": category, "name_contains": name_contains, "type_name": type_name,
                "level": level_name, "page": 1, "page_size": maximo, "ids_only": solo_ids,
            }
            if parameter_name:
                criterios["filters"] = [{
                    "parameter": parameter_name,
                    "op": "=" if parameter_value is not None else "exists",
                    "value": parameter_value,
                }]
            from navegacion import consultar_elementos

            resultado = consultar_elementos(doc, criterios)
            return routes.make_response(data={
                "status": "success",
                "elements": resultado["elements"],
                "ids": resultado["ids"],
                "count": resultado["count"],
                "total_matched": resultado["total_matched"],
                "scanned": resultado["scanned"],
                "truncated": resultado["truncated"],
                "filters": {
                    "category": category, "name_contains": name_contains or None,
                    "type_name": type_name or None, "level_name": level_name or None,
                    "parameter_name": parameter_name or None, "parameter_value": parameter_value,
                    "max": maximo,
                },
            })
        except EscrituraRechazada as rechazo:
            cuerpo_error = {"error": rechazo.mensaje}
            cuerpo_error.update(rechazo.extra)
            return routes.make_response(data=cuerpo_error, status=rechazo.status)
        except Exception as e:
            return _respuesta_error(e)

    @api.route("/element_geometry/", methods=["POST"])
    @requiere_token
    def get_element_geometry(doc, request):
        """Caja envolvente en mm y, si se pide, curvas de ubicacion o solidos."""
        try:
            if not doc:
                return _sin_documento()
            data = datos_peticion(request)
            element_id = data.get("element_id")
            if element_id is None:
                return routes.make_response(data={"error": "element_id is required"}, status=400)
            detail = (data.get("detail") or "bbox").lower()
            if detail not in ("bbox", "curves", "solids"):
                return routes.make_response(
                    data={"error": "detail must be bbox, curves or solids"}, status=400
                )
            elem = doc.GetElement(make_element_id(element_id))
            if elem is None:
                return routes.make_response(
                    data={"error": "Element {} not found".format(element_id)}, status=404
                )
            caja = bbox_mm(elem)
            resultado = {
                "status": "success",
                "element_id": int(element_id),
                "categoria": nombre_categoria(elem),
                "tipo": nombre_tipo(doc, elem),
                "nivel": nombre_nivel(doc, elem),
                "detail": detail,
                "bbox_mm": caja,
            }
            if caja and caja.get("min") and caja.get("max"):
                resultado["center_mm"] = dict(
                    (e, round((caja["min"][e] + caja["max"][e]) / 2.0, 1)) for e in ("x", "y", "z")
                )
                resultado["size_mm"] = dict(
                    (e, round(caja["max"][e] - caja["min"][e], 1)) for e in ("x", "y", "z")
                )
            if detail == "curves":
                resultado["location"] = _curvas_ubicacion(elem)
            elif detail == "solids":
                opciones = DB.Options()
                opciones.DetailLevel = DB.ViewDetailLevel.Fine
                try:
                    opciones.ComputeReferences = False
                except Exception:
                    pass
                solidos = _solidos(elem.get_Geometry(opciones))
                lista = []
                volumen_total = 0.0
                area_total = 0.0
                for solido in solidos:
                    volumen = solido.Volume * CUFT_TO_CUM
                    area = solido.SurfaceArea * SQFT_TO_SQM
                    volumen_total += volumen
                    area_total += area
                    datos = {
                        "volume_m3": round(volumen, 4),
                        "area_m2": round(area, 3),
                        "faces": solido.Faces.Size,
                        "edges": solido.Edges.Size,
                    }
                    try:
                        datos["centroid_mm"] = punto_a_mm(solido.ComputeCentroid())
                    except Exception:
                        pass
                    lista.append(datos)
                resultado["solids"] = lista
                resultado["solid_count"] = len(lista)
                resultado["volume_m3"] = round(volumen_total, 4)
                resultado["area_m2"] = round(area_total, 3)
            return routes.make_response(data=resultado)
        except EscrituraRechazada as rechazo:
            return routes.make_response(data={"error": rechazo.mensaje}, status=rechazo.status)
        except Exception as e:
            return _respuesta_error(e)

    @api.route("/element_types/", methods=["POST"])
    @requiere_token
    def list_element_types(doc, request):
        """Tipos de una categoria: id, familia, tipo, parametros principales, ejemplares."""
        try:
            if not doc:
                return _sin_documento()
            data = datos_peticion(request)
            category = data.get("category")
            family_name = (data.get("family_name") or "").strip()
            maximo = _entero(data.get("max"), 200)
            if not category:
                return routes.make_response(data={"error": "category is required"}, status=400)
            bic = _resolve_bic(category)
            if bic is None:
                return routes.make_response(
                    data={"error": "Invalid category '{}'. Use BuiltInCategory names like OST_Walls or aliases like 'walls'".format(category)},
                    status=400,
                )

            # Ejemplares por tipo (para saber que tipos se usan)
            ejemplares = {}
            try:
                for inst in DB.FilteredElementCollector(doc).OfCategory(bic).WhereElementIsNotElementType():
                    try:
                        clave = get_element_id_value(inst.GetTypeId())
                        ejemplares[clave] = ejemplares.get(clave, 0) + 1
                    except Exception:
                        continue
            except Exception:
                pass

            tipos = []
            total = 0
            for tipo in DB.FilteredElementCollector(doc).OfCategory(bic).WhereElementIsElementType():
                try:
                    familia = _nombre_familia(tipo)
                    if family_name and (familia or u"").lower() != family_name.lower():
                        continue
                    total += 1
                    if len(tipos) >= maximo:
                        continue
                    identificador = get_element_id_value(tipo)
                    parametros = {}
                    for nombre in PARAMETROS_TIPO_PRINCIPALES:
                        try:
                            p = buscar_por_nombre(tipo, nombre)
                            if p and p.HasValue:
                                parametros[nombre] = valor_parametro(p, doc)
                        except Exception:
                            continue
                    datos = {
                        "id": identificador,
                        "familia": familia,
                        "tipo": get_element_name(tipo),
                        "parametros": parametros,
                        "ejemplares": ejemplares.get(identificador, 0),
                    }
                    try:
                        datos["activo"] = bool(tipo.IsActive)
                    except Exception:
                        pass
                    tipos.append(datos)
                except Exception:
                    continue
            tipos.sort(key=lambda t: ((t["familia"] or u""), t["tipo"]))
            return routes.make_response(data={
                "status": "success",
                "category": category,
                "types": tipos,
                "count": len(tipos),
                "total_matched": total,
                "truncated": total > len(tipos),
            })
        except EscrituraRechazada as rechazo:
            return routes.make_response(data={"error": rechazo.mensaje}, status=rechazo.status)
        except Exception as e:
            return _respuesta_error(e)

    @api.route("/log/", methods=["GET"])
    @requiere_token
    def read_log(doc, request):
        """Ultimas lineas de mcp_log.jsonl (?last_n=, 50 por defecto)."""
        try:
            last_n = _entero(_query(request).get("last_n"), 50)
            ruta, entradas = leer_log(doc if doc else _DocSinRuta(), last_n)
            return routes.make_response(data={
                "status": "success",
                "ruta": ruta,
                "entradas": entradas,
                "count": len(entradas),
            })
        except Exception as e:
            return _respuesta_error(e)

    logger.info("Consulta routes registered successfully")


class _DocSinRuta(object):
    """Documento vacio para leer el log de %LOCALAPPDATA% sin documento activo."""
    PathName = u""
    Title = u""


def _nivel_id(doc, nombre):
    for nivel in DB.FilteredElementCollector(doc).OfCategory(DB.BuiltInCategory.OST_Levels).WhereElementIsNotElementType():
        if get_element_name(nivel) == nombre:
            return nivel.Id
    raise ValueError("Level '{}' not found".format(nombre))
