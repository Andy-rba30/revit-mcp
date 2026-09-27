# -*- coding: UTF-8 -*-
"""
Navegacion profunda para Revit MCP (0.3.0). Rutas de solo lectura:

  POST /describe/           element_id*, depth (0-2), include_geometry
  POST /dependency_graph/   element_id*, max_nodes (500 como mucho), max_depth (2)
  POST /query/              category, family, type_name, level, view_id, workset,
                            phase, filters[] {parameter, op, value}, bbox_min_mm,
                            bbox_max_mm, sort_by, page, page_size (500 como mucho),
                            fields[], name_contains, ids_only
  POST /schedule/           name* o view_id*, start_row, max_rows
  POST /view_extents/       view_id* (o view_name)

Ninguna abre transaccion ni modifica el modelo. `POST /find_elements/` y la
agrupacion de `GET /warnings/?group_by=description` (consulta.py) llaman a
consultar_elementos() y agrupar_avisos() de este modulo.

Idioma de Revit: los parametros se resuelven con utils.buscar_por_nombre
(nombre visible, nombre de BuiltInParameter o alias ingles), las categorias con
BuiltInCategory y los tipos de dato con SpecTypeId; las sugerencias de las
advertencias se eligen por el FailureDefinitionId, nunca por el texto.

Unidades: milimetros (mm, mm2, mm3, grados) hacia fuera; los valores de
`filters[].value` y de `fields` van en esas unidades.
"""

from utils import (
    get_element_name, get_element_id_value, make_element_id, xyz_desde_mm, punto_a_mm,
    buscar_por_nombre, mapa_niveles, nombre_familia, FEET_TO_MM, MM_TO_FEET,
)
from seguridad import requiere_token
from escritura import (
    describir_elemento, bbox_mm, nombre_nivel, nombre_categoria, nombre_tipo, datos_peticion,
    EscrituraRechazada,
)
from parameters import (
    valor_parametro, factor_a_interno, unidad_contrato, contexto_elemento, buscar_parametro,
)
from clash import _resolve_bic
from pyrevit import routes, revit, DB
from System.Collections.Generic import List
import math
import traceback
import logging

logger = logging.getLogger(__name__)

try:
    _texto = unicode  # IronPython 2.7
    _cadena = basestring
except NameError:  # pragma: no cover - CPython 3 en las pruebas
    _texto = str
    _cadena = str

LIMITE_PAGINA = 500
PAGINA_DEFECTO = 100
MAX_NODOS = 500
NODOS_DEFECTO = 100
PROFUNDIDAD_DEFECTO = 2
MAX_PROFUNDIDAD = 6
MAX_RELACIONADOS = 200
MAX_MUESTRA_PARAMETRO = 50
FILAS_DEFECTO = 500
MAX_FILAS = 5000
MAX_IDS_POR_GRUPO = 50
TOLERANCIA_CONTRATO = 1e-3      # mm, mm2, mm3 o grados al comparar con "="
EPSILON_INTERNO = 1e-6          # tolerancia de FilterDoubleRule (unidades internas)
SQFT_TO_SQM = 0.09290304
CUFT_TO_CUM = 0.028316846592

OPS = ("=", "!=", ">", "<", ">=", "<=", "contains", "starts", "empty", "not_empty", "exists")
OPS_SIN_VALOR = ("empty", "not_empty", "exists")
OPS_ORDEN = (">", "<", ">=", "<=")
OPS_NATIVOS = ("=", ">", "<", ">=", "<=", "contains", "starts")
_ALIAS_OPS = {
    "==": "=", "eq": "=", "equals": "=", "<>": "!=", "ne": "!=", "not": "!=",
    "gt": ">", "lt": "<", "ge": ">=", "gte": ">=", "le": "<=", "lte": "<=",
    "startswith": "starts", "starts_with": "starts", "begins": "starts",
    "like": "contains", "is_empty": "empty", "isempty": "empty", "has_value": "not_empty",
    "not empty": "not_empty", "notempty": "not_empty", "has": "exists",
}
_EVALUADORES_NUMERICOS = {
    "=": "FilterNumericEquals", ">": "FilterNumericGreater", ">=": "FilterNumericGreaterOrEqual",
    "<": "FilterNumericLess", "<=": "FilterNumericLessOrEqual",
}
_EVALUADORES_TEXTO = {"=": "FilterStringEquals", "contains": "FilterStringContains", "starts": "FilterStringBeginsWith"}

# Sugerencia por tipo de advertencia. La clave es el miembro de DB.BuiltInFailures
# (independiente del idioma); se resuelve a su GUID en tiempo de ejecucion y los
# miembros que no existan en la version de Revit se ignoran.
SUGERENCIAS_FALLOS = (
    ("OverlapFailures", "WallsOverlap",
     u"Muros solapados: revisa las lineas de ubicacion, une los muros (join_geometry) o acorta uno de ellos."),
    ("OverlapFailures", "DuplicateInstances",
     u"Ejemplares identicos en el mismo sitio: lista los ids y borra los duplicados con delete_elements."),
    ("OverlapFailures", "WallRoomSeparationOverlap",
     u"Un muro y una linea de separacion de habitaciones se solapan: borra la linea redundante."),
    ("OverlapFailures", "RoomSeparationLinesOverlap",
     u"Lineas de separacion de habitaciones solapadas: borra las repetidas."),
    ("OverlapFailures", "FloorsOverlap",
     u"Suelos solapados: revisa los contornos o el nivel y desfase de cada suelo."),
    ("RoomFailures", "RoomNotEnclosed",
     u"Habitacion sin recinto cerrado: cierra el recinto con muros o lineas de separacion, o borra la habitacion."),
    ("RoomFailures", "RoomNotInPlaced",
     u"Habitacion sin colocar: colocala en un recinto o borrala."),
    ("RoomFailures", "RoomsInSameRegion",
     u"Varias habitaciones en el mismo recinto: borra las sobrantes o separalas con lineas de separacion."),
    ("JoinElementsFailures", "CannotKeepJoined",
     u"Elementos unidos que ya no se cortan: separalos con join_geometry(unjoin=true)."),
    ("JoinElementsFailures", "CannotKeepJoinedWarning",
     u"Elementos unidos que ya no se cortan: separalos con join_geometry(unjoin=true)."),
    ("InaccurateFailures", "InaccurateLine",
     u"Linea ligeramente fuera de eje: mueve sus extremos a coordenadas redondas (transform_elements) o redibujala."),
    ("InaccurateFailures", "InaccurateWall",
     u"Muro ligeramente fuera de eje: corrige los extremos de su linea de ubicacion."),
    ("InaccurateFailures", "InaccurateBeamOrBrace",
     u"Viga o arriostre ligeramente fuera de eje: corrige los extremos de su linea de ubicacion."),
    ("InaccurateFailures", "InaccurateGrid",
     u"Rejilla ligeramente fuera de eje: redibujala entre coordenadas redondas."),
    ("InaccurateFailures", "InaccurateRefPlane",
     u"Plano de referencia ligeramente fuera de eje: redibujalo."),
    ("GeneralFailures", "DuplicateValue",
     u"Valores duplicados (Marca, Numero...): asigna valores unicos con set_parameter."),
    ("WallFailures", "WallNotAttached",
     u"Muro sin enlazar: revisa la restriccion superior o enlazalo a su suelo o cubierta."),
    ("AreaFailures", "AreaNotEnclosed",
     u"Area sin recinto cerrado: cierra el contorno con lineas de area."),
)
SUGERENCIA_GENERICA = u"Sin sugerencia especifica para este tipo: revisa los elementos listados en Revit."
_tabla_sugerencias = None


# ---------------------------------------------------------------------------
# Utilidades comunes
# ---------------------------------------------------------------------------
def _respuesta_error(e):
    logger.error("Navegacion fallida: {}".format(str(e)))
    return routes.make_response(
        data={"error": str(e), "traceback": traceback.format_exc()}, status=500
    )


def _sin_documento():
    return routes.make_response(data={"error": "No active Revit document"}, status=503)


def _texto_seguro(valor):
    if valor is None:
        return u""
    try:
        return _texto(valor)
    except Exception:
        try:
            return str(valor)
        except Exception:
            return u"?"


def _entero(valor, por_defecto, minimo=None, maximo=None):
    try:
        numero = int(valor)
    except (TypeError, ValueError):
        numero = por_defecto
    if minimo is not None and numero < minimo:
        numero = minimo
    if maximo is not None and numero > maximo:
        numero = maximo
    return numero


def _es_invalido(eid):
    try:
        return eid is None or eid == DB.ElementId.InvalidElementId
    except Exception:
        return True


def _es_numero(valor):
    if isinstance(valor, bool):
        return False
    try:
        float(valor)
        return True
    except (TypeError, ValueError):
        return False


def _elemento(doc, element_id):
    """Elemento por id; 400 si el id no es entero y 404 si no existe."""
    if element_id is None:
        raise EscrituraRechazada("element_id is required", 400)
    try:
        eid = make_element_id(element_id)
    except ValueError as error:
        raise EscrituraRechazada(str(error), 400)
    elem = doc.GetElement(eid)
    if elem is None:
        raise EscrituraRechazada("Element {} not found".format(element_id), 404)
    return elem


def _tipo_de(doc, elem):
    try:
        tipo_id = elem.GetTypeId()
        if not _es_invalido(tipo_id):
            return doc.GetElement(tipo_id)
    except Exception:
        pass
    return None


def _nombre_clase(elem):
    try:
        return _texto_seguro(elem.GetType().Name)
    except Exception:
        return type(elem).__name__


def _responder(doc, request, cuerpo):
    """Plantilla de las rutas de lectura: 503 sin documento, 400/404 controlados, 500 con traza."""
    try:
        if not doc:
            return _sin_documento()
        data = datos_peticion(request)
        return routes.make_response(data=cuerpo(data))
    except EscrituraRechazada as rechazo:
        cuerpo_error = {"error": rechazo.mensaje}
        cuerpo_error.update(rechazo.extra)
        return routes.make_response(data=cuerpo_error, status=rechazo.status)
    except Exception as e:
        return _respuesta_error(e)


# ---------------------------------------------------------------------------
# Parametros: valor en unidades del contrato y descripcion completa
# ---------------------------------------------------------------------------
def _nombre_builtin(param):
    """Nombre del BuiltInParameter del parametro (independiente del idioma), o None."""
    try:
        bip = param.Definition.BuiltInParameter
    except Exception:
        return None
    try:
        nombre = str(bip)
    except Exception:
        return None
    if not nombre or nombre == "INVALID" or nombre.startswith("-"):
        return None
    return nombre


def valor_contrato(param, doc):
    """(valor, unidad) del parametro en unidades del contrato.

    Double de longitud, area, volumen o angulo -> mm, mm2, mm3 o grados (unidad
    "mm", "mm2", "mm3" o "deg"); otro Double -> valor interno; Integer -> int;
    String -> texto; ElementId -> nombre del elemento referenciado."""
    try:
        if not param.HasValue:
            return None, unidad_contrato(param)
        tipo = param.StorageType
        if tipo == DB.StorageType.Double:
            bruto = param.AsDouble()
            factor = factor_a_interno(param)
            if factor:
                return round(bruto / factor, 4), unidad_contrato(param)
            return bruto, None
        if tipo == DB.StorageType.Integer:
            return param.AsInteger(), None
        if tipo == DB.StorageType.String:
            return param.AsString(), None
        if tipo == DB.StorageType.ElementId:
            eid = param.AsElementId()
            if _es_invalido(eid):
                return None, None
            elem = doc.GetElement(eid)
            if elem is None:
                return get_element_id_value(eid), None
            return get_element_name(elem), None
    except Exception:
        return None, None
    return None, None


def describir_parametro(param, doc, es_tipo=False):
    """Nombre, valor (contrato), valor visible, unidad, solo lectura, compartido, guid y BuiltInParameter."""
    try:
        nombre = _texto_seguro(param.Definition.Name)
    except Exception:
        nombre = u"?"
    valor, unidad = valor_contrato(param, doc)
    datos = {
        "name": nombre,
        "value": valor,
        "display": valor_parametro(param, doc),
        "unit": unidad,
        "storage_type": None,
        "is_read_only": None,
        "is_instance": not es_tipo,
        "is_type_parameter": bool(es_tipo),
        "is_shared": False,
        "guid": None,
        "builtin": _nombre_builtin(param),
    }
    try:
        datos["storage_type"] = str(param.StorageType)
    except Exception:
        pass
    try:
        datos["is_read_only"] = bool(param.IsReadOnly)
    except Exception:
        pass
    try:
        if param.IsShared:
            datos["is_shared"] = True
            datos["guid"] = _texto_seguro(param.GUID)
    except Exception:
        pass
    return datos


def parametros_de(elem, doc, es_tipo=False):
    """Parametros del elemento en el orden de Revit, sin nombres repetidos."""
    if elem is None:
        return []
    try:
        iterador = list(elem.GetOrderedParameters())
    except Exception:
        try:
            iterador = list(elem.Parameters)
        except Exception:
            iterador = []
    lista = []
    vistos = set()
    for param in iterador:
        try:
            datos = describir_parametro(param, doc, es_tipo)
        except Exception:
            continue
        if datos["name"] in vistos:
            continue
        vistos.add(datos["name"])
        lista.append(datos)
    return lista


# ---------------------------------------------------------------------------
# Relaciones: alojados, unidos, dependientes y referencias
# ---------------------------------------------------------------------------
def _dependientes(doc, elem):
    """Ids (int) de Element.GetDependentElements sin el propio elemento."""
    try:
        propio = get_element_id_value(elem)
    except Exception:
        return []
    ids = []
    try:
        for eid in elem.GetDependentElements(None):
            try:
                valor = get_element_id_value(eid)
            except Exception:
                continue
            if valor != propio and valor not in ids:
                ids.append(valor)
    except Exception as error:
        logger.debug("GetDependentElements fallo en %s: %s", propio, str(error))
    return ids


def _es_de_modelo(elem):
    try:
        categoria = elem.Category
        return categoria is not None and categoria.CategoryType == DB.CategoryType.Model
    except Exception:
        return False


def ids_alojados(doc, elem):
    """Elementos alojados en `elem`: HostObject.FindInserts mas el inverso de FamilyInstance.Host."""
    try:
        propio = get_element_id_value(elem)
    except Exception:
        return []
    ids = []

    def agregar(valor):
        if valor != propio and valor not in ids:
            ids.append(valor)

    try:
        if isinstance(elem, DB.HostObject):
            for eid in elem.FindInserts(True, True, True, True):
                try:
                    agregar(get_element_id_value(eid))
                except Exception:
                    continue
    except Exception as error:
        logger.debug("FindInserts fallo en %s: %s", propio, str(error))
    for dep_id in _dependientes(doc, elem):
        try:
            dep = doc.GetElement(make_element_id(dep_id))
        except Exception:
            continue
        if not isinstance(dep, DB.FamilyInstance):
            continue
        try:
            host = dep.Host
            if host is not None and get_element_id_value(host) == propio:
                agregar(dep_id)
        except Exception:
            continue
    return ids


def ids_unidos(doc, elem):
    """Elementos unidos geometricamente (JoinGeometryUtils.GetJoinedElements)."""
    try:
        return [get_element_id_value(i) for i in DB.JoinGeometryUtils.GetJoinedElements(doc, elem)]
    except Exception:
        return []


def ids_dependientes_modelo(doc, elem, excluir=()):
    """Dependientes de categoria de modelo (se borrarian con el elemento), sin los de `excluir`."""
    ids = []
    for dep_id in _dependientes(doc, elem):
        if dep_id in excluir:
            continue
        try:
            dep = doc.GetElement(make_element_id(dep_id))
        except Exception:
            continue
        if dep is None or not _es_de_modelo(dep):
            continue
        ids.append(dep_id)
    return ids


def referencias_en_vista_activa(doc, elem):
    """Cotas y etiquetas de la VISTA ACTIVA que referencian al elemento (recorrer todas no escala)."""
    try:
        vista = doc.ActiveView
    except Exception:
        vista = None
    if vista is None:
        return [], None
    propio = elem.Id
    lista = []
    try:
        for cota in DB.FilteredElementCollector(doc, vista.Id).OfClass(DB.Dimension):
            try:
                for ref in cota.References:
                    if ref.ElementId == propio:
                        lista.append({"id": get_element_id_value(cota), "kind": "dimension",
                                      "categoria": nombre_categoria(cota)})
                        break
            except Exception:
                continue
    except Exception as error:
        logger.debug("No se pudieron leer las cotas de la vista activa: %s", str(error))
    try:
        for etiqueta in DB.FilteredElementCollector(doc, vista.Id).OfClass(DB.IndependentTag):
            try:
                try:
                    ids = list(etiqueta.GetTaggedLocalElementIds())
                except Exception:
                    ids = [etiqueta.TaggedLocalElementId]
                if any(i == propio for i in ids):
                    lista.append({"id": get_element_id_value(etiqueta), "kind": "tag",
                                  "categoria": nombre_categoria(etiqueta)})
            except Exception:
                continue
    except Exception as error:
        logger.debug("No se pudieron leer las etiquetas de la vista activa: %s", str(error))
    try:
        datos_vista = {"id": get_element_id_value(vista), "name": get_element_name(vista)}
    except Exception:
        datos_vista = None
    return lista, datos_vista


def _relacion(doc, ids, depth):
    """Lista de relacionados: solo ids (depth 0), resumen (1) o resumen con sus alojados y unidos (2)."""
    lista = []
    for identificador in ids[:MAX_RELACIONADOS]:
        if depth <= 0:
            lista.append({"id": identificador})
            continue
        datos = describir_elemento(doc, identificador) or {"id": identificador}
        if depth >= 2:
            try:
                otro = doc.GetElement(make_element_id(identificador))
            except Exception:
                otro = None
            if otro is not None:
                datos["hosted_ids"] = ids_alojados(doc, otro)
                datos["joined_ids"] = ids_unidos(doc, otro)
        lista.append(datos)
    return lista


def _geometria(doc, elem):
    from consulta import _curvas_ubicacion, _solidos

    geometria = {"location": _curvas_ubicacion(elem)}
    opciones = DB.Options()
    try:
        opciones.DetailLevel = DB.ViewDetailLevel.Fine
    except Exception:
        pass
    try:
        opciones.ComputeReferences = False
    except Exception:
        pass
    solidos = []
    try:
        solidos = _solidos(elem.get_Geometry(opciones))
    except Exception as error:
        geometria["error"] = "get_Geometry failed: {}".format(error)
    lista = []
    volumen_total = 0.0
    area_total = 0.0
    for solido in solidos:
        try:
            volumen = solido.Volume * CUFT_TO_CUM
            area = solido.SurfaceArea * SQFT_TO_SQM
        except Exception:
            continue
        volumen_total += volumen
        area_total += area
        datos = {"volume_m3": round(volumen, 4), "area_m2": round(area, 3)}
        try:
            datos["faces"] = solido.Faces.Size
            datos["edges"] = solido.Edges.Size
        except Exception:
            pass
        try:
            datos["centroid_mm"] = punto_a_mm(solido.ComputeCentroid())
        except Exception:
            pass
        lista.append(datos)
    geometria["solids"] = lista
    geometria["solid_count"] = len(lista)
    geometria["volume_m3"] = round(volumen_total, 4)
    geometria["area_m2"] = round(area_total, 3)
    return geometria


def describir_a_fondo(doc, elem, depth=0, include_geometry=False):
    """Descripcion completa de un elemento (ver POST /describe/)."""
    base = describir_elemento(doc, elem) or {}
    contexto = contexto_elemento(doc, elem)
    tipo = _tipo_de(doc, elem)
    datos = {
        "id": base.get("id"),
        "unique_id": None,
        "class": _nombre_clase(elem),
        "categoria": base.get("categoria"),
        "familia": nombre_familia(tipo) if tipo is not None else None,
        "tipo": base.get("tipo"),
        "type_id": get_element_id_value(tipo) if tipo is not None else None,
        "nivel": base.get("nivel"),
        "host_id": contexto.get("host_id"),
        "host": None,
        "workset": contexto.get("workset"),
        "phase_created": contexto.get("phase_created"),
        "phase_demolished": contexto.get("phase_demolished"),
        "design_option": contexto.get("design_option"),
        "pinned": contexto.get("pinned"),
        "view_specific": None,
        "bbox_mm": base.get("bbox_mm"),
    }
    try:
        datos["unique_id"] = _texto_seguro(elem.UniqueId)
    except Exception:
        pass
    try:
        datos["view_specific"] = bool(elem.ViewSpecific)
    except Exception:
        pass
    if datos["host_id"] is not None:
        if depth > 0:
            datos["host"] = describir_elemento(doc, datos["host_id"]) or {"id": datos["host_id"]}
        else:
            datos["host"] = {"id": datos["host_id"]}

    datos["parameters"] = {
        "instance": parametros_de(elem, doc, False),
        "type": parametros_de(tipo, doc, True) if tipo is not None else [],
    }

    alojados = ids_alojados(doc, elem)
    unidos = ids_unidos(doc, elem)
    dependientes = ids_dependientes_modelo(doc, elem, excluir=alojados)
    datos["hosted_elements"] = _relacion(doc, alojados, depth)
    datos["joined_elements"] = _relacion(doc, unidos, depth)
    datos["dependents"] = _relacion(doc, dependientes, depth)
    datos["counts"] = {"hosted": len(alojados), "joined": len(unidos), "dependents": len(dependientes)}
    if max(len(alojados), len(unidos), len(dependientes)) > MAX_RELACIONADOS:
        datos["truncated"] = True
        datos["nota"] = u"Las listas de relacionados se cortan a {} elementos".format(MAX_RELACIONADOS)
    referencias, vista = referencias_en_vista_activa(doc, elem)
    datos["referenced_by"] = referencias
    datos["active_view"] = vista
    datos["depth"] = depth
    if include_geometry:
        datos["geometry"] = _geometria(doc, elem)
    return datos


# ---------------------------------------------------------------------------
# Grafo de dependencias
# ---------------------------------------------------------------------------
def grafo_dependencias(doc, raiz, max_nodes=NODOS_DEFECTO, max_depth=PROFUNDIDAD_DEFECTO):
    """Nodos y aristas (hosts, joins, depends) alrededor de `raiz`, en anchura."""
    nodos = []
    indice = {}
    aristas = []
    claves = set()
    cola = [(raiz, 0)]
    encolados = set([get_element_id_value(raiz)])
    truncado = False
    while cola:
        elem, profundidad = cola.pop(0)
        try:
            eid = get_element_id_value(elem)
        except Exception:
            continue
        if eid in indice:
            continue
        if len(indice) >= max_nodes:
            truncado = True
            break
        nodo = describir_elemento(doc, elem) or {"id": eid}
        nodo["depth"] = profundidad
        indice[eid] = nodo
        nodos.append(nodo)
        if profundidad >= max_depth:
            continue

        vecinos = []
        if isinstance(elem, DB.FamilyInstance):
            for atributo in ("Host", "SuperComponent"):
                try:
                    otro = getattr(elem, atributo)
                except Exception:
                    otro = None
                if otro is None:
                    continue
                try:
                    vecinos.append((otro, get_element_id_value(otro), eid, "hosts"))
                except Exception:
                    continue
        alojados = ids_alojados(doc, elem)
        for hid in alojados:
            vecinos.append((None, eid, hid, "hosts"))
        for jid in ids_unidos(doc, elem):
            a, b = (eid, jid) if eid < jid else (jid, eid)
            vecinos.append((None, a, b, "joins"))
        for did in ids_dependientes_modelo(doc, elem, excluir=alojados):
            vecinos.append((None, eid, did, "depends"))

        for objeto, origen, destino, tipo in vecinos:
            clave = (origen, destino, tipo)
            if clave in claves:
                continue
            claves.add(clave)
            aristas.append({"from": origen, "to": destino, "kind": tipo})
            otro_id = destino if origen == eid else origen
            if otro_id in indice or otro_id in encolados:
                continue
            otro = objeto
            if otro is None:
                try:
                    otro = doc.GetElement(make_element_id(otro_id))
                except Exception:
                    otro = None
            if otro is not None:
                cola.append((otro, profundidad + 1))
                encolados.add(otro_id)

    validas = [a for a in aristas if a["from"] in indice and a["to"] in indice]
    return {
        "root_id": get_element_id_value(raiz),
        "nodes": nodos,
        "edges": validas,
        "node_count": len(nodos),
        "edge_count": len(validas),
        "edges_dropped": len(aristas) - len(validas),
        "truncated": truncado or len(validas) < len(aristas),
        "max_nodes": max_nodes,
        "max_depth": max_depth,
        "kinds": {"hosts": "anfitrion -> alojado", "joins": "union de geometria (sin direccion)",
                  "depends": "elemento -> dependiente de categoria de modelo"},
    }


# ---------------------------------------------------------------------------
# Motor de consulta (query_elements y find_elements)
# ---------------------------------------------------------------------------
def _categorias(data):
    valor = data.get("category")
    if valor in (None, "", []):
        valor = data.get("categories")
    if valor in (None, "", []):
        return []
    if isinstance(valor, _cadena):
        valores = [valor]
    elif isinstance(valor, (list, tuple)):
        valores = list(valor)
    else:
        raise EscrituraRechazada("category must be a BuiltInCategory name (or a list of them)", 400)
    bics = []
    for nombre in valores:
        bic = _resolve_bic(nombre)
        if bic is None:
            raise EscrituraRechazada(
                "Invalid category '{}'. Use BuiltInCategory names like OST_Walls or aliases like "
                "'walls', 'beams'".format(nombre), 400,
            )
        bics.append(bic)
    return bics


def normalizar_filtros(crudos):
    """Valida filters[] y devuelve [{"parameter", "op", "value"}]. Lanza EscrituraRechazada(400)."""
    if crudos in (None, ""):
        return []
    if isinstance(crudos, dict):
        crudos = [crudos]
    if not isinstance(crudos, (list, tuple)):
        raise EscrituraRechazada("filters must be a list of {parameter, op, value}", 400)
    filtros = []
    for indice, filtro in enumerate(crudos):
        if not isinstance(filtro, dict):
            raise EscrituraRechazada("filters[{}] must be an object {{parameter, op, value}}".format(indice), 400)
        nombre = _texto_seguro(filtro.get("parameter") or filtro.get("parameter_name") or filtro.get("name")).strip()
        if not nombre:
            raise EscrituraRechazada("filters[{}]: parameter is required".format(indice), 400)
        op = _texto_seguro(filtro.get("op") or "=").strip().lower()
        op = _ALIAS_OPS.get(op, op)
        if op not in OPS:
            raise EscrituraRechazada(
                "filters[{}]: op '{}' not supported; use one of {}".format(indice, op, ", ".join(OPS)), 400,
            )
        valor = filtro.get("value")
        if op not in OPS_SIN_VALOR and valor is None:
            raise EscrituraRechazada("filters[{}]: value is required for op '{}'".format(indice, op), 400)
        if op in OPS_ORDEN and not _es_numero(valor):
            raise EscrituraRechazada(
                "filters[{}]: op '{}' needs a numeric value (mm, mm2, mm3 or degrees)".format(indice, op), 400,
            )
        filtros.append({"parameter": nombre, "op": op, "value": valor})
    return filtros


def _texto_valor(param, doc):
    """Texto del parametro para contains/starts: AsString o el valor visible."""
    try:
        if param.StorageType == DB.StorageType.String:
            return param.AsString() or u""
    except Exception:
        pass
    return _texto_seguro(valor_parametro(param, doc))


def _numero_contrato(param):
    """Valor numerico en unidades del contrato para Double/Integer; None si no."""
    try:
        if param.StorageType == DB.StorageType.Double:
            factor = factor_a_interno(param)
            bruto = param.AsDouble()
            return bruto / factor if factor else bruto
        if param.StorageType == DB.StorageType.Integer:
            return float(param.AsInteger())
    except Exception:
        return None
    return None


def _vacio(param):
    try:
        if not param.HasValue:
            return True
        tipo = param.StorageType
        if tipo == DB.StorageType.String:
            return not (param.AsString() or u"").strip()
        if tipo == DB.StorageType.ElementId:
            return _es_invalido(param.AsElementId())
    except Exception:
        return True
    return False


def _valor_booleano(valor):
    if isinstance(valor, bool):
        return 1 if valor else 0
    if isinstance(valor, _cadena):
        bajo = valor.strip().lower()
        if bajo in ("true", "yes", "si", "sí"):
            return 1
        if bajo in ("false", "no"):
            return 0
    return None


def _iguales(param, valor, doc):
    try:
        tipo = param.StorageType
    except Exception:
        return False
    buscado = _texto_seguro(valor).strip().lower()
    if tipo == DB.StorageType.Double:
        if _es_numero(valor):
            numero = _numero_contrato(param)
            return numero is not None and abs(numero - float(valor)) <= TOLERANCIA_CONTRATO
        return _texto_valor(param, doc).strip().lower() == buscado
    if tipo == DB.StorageType.Integer:
        booleano = _valor_booleano(valor)
        if booleano is not None:
            return param.AsInteger() == booleano
        if _es_numero(valor):
            return param.AsInteger() == int(float(valor))
        return _texto_valor(param, doc).strip().lower() == buscado
    if tipo == DB.StorageType.ElementId:
        eid = param.AsElementId()
        if _es_invalido(eid):
            return False
        if _es_numero(valor) and not isinstance(valor, _cadena):
            return get_element_id_value(eid) == int(valor)
        elem = doc.GetElement(eid)
        nombre = get_element_name(elem).strip().lower() if elem is not None else u""
        if nombre == buscado:
            return True
        return buscado.isdigit() and get_element_id_value(eid) == int(buscado)
    return (param.AsString() or u"").strip().lower() == buscado


def cumple_filtro(param, op, valor, doc):
    """Evalua en Python un filtro sobre un parametro ya resuelto."""
    if op == "exists":
        return True
    if op == "empty":
        return _vacio(param)
    if op == "not_empty":
        return not _vacio(param)
    if op in ("contains", "starts"):
        texto = _texto_valor(param, doc).lower()
        buscado = _texto_seguro(valor).lower()
        return (buscado in texto) if op == "contains" else texto.startswith(buscado)
    if op in OPS_ORDEN:
        numero = _numero_contrato(param)
        if numero is None:
            try:
                numero = float(_texto_valor(param, doc))
            except (TypeError, ValueError):
                return False
        objetivo = float(valor)
        if op == ">":
            return numero > objetivo
        if op == "<":
            return numero < objetivo
        if op == ">=":
            return numero >= objetivo
        return numero <= objetivo
    iguales = _iguales(param, valor, doc)
    return iguales if op == "=" else not iguales


def _proveedor(param):
    """ParameterValueProvider por BuiltInParameter o por parametro compartido; (None, None) si no."""
    nombre_bip = _nombre_builtin(param)
    if nombre_bip:
        try:
            bip = getattr(DB.BuiltInParameter, nombre_bip)
            return DB.ParameterValueProvider(DB.ElementId(bip)), "BuiltInParameter {}".format(nombre_bip)
        except Exception:
            return None, None
    try:
        if param.IsShared:
            return DB.ParameterValueProvider(param.Id), "shared parameter {}".format(_texto_seguro(param.GUID))
    except Exception:
        pass
    return None, None


def regla_nativa(param, op, valor):
    """(DB.ElementParameterFilter, motivo) para el filtro, o (None, None) si se evalua en Python.

    Solo para BuiltInParameter o parametros compartidos de EJEMPLAR y ops
    =, >, <, >=, <=, contains, starts. `!=`, `empty`, `not_empty` y `exists`
    van siempre en Python: el filtro invertido de Revit deja pasar a los
    elementos que no tienen el parametro."""
    if op not in OPS_NATIVOS:
        return None, None
    proveedor, motivo = _proveedor(param)
    if proveedor is None:
        return None, None
    try:
        tipo = param.StorageType
        if tipo == DB.StorageType.String and op in _EVALUADORES_TEXTO:
            evaluador = getattr(DB, _EVALUADORES_TEXTO[op])()
            try:
                regla = DB.FilterStringRule(proveedor, evaluador, _texto_seguro(valor))
            except TypeError:
                # Revit < 2023 exige el argumento caseSensitive
                regla = DB.FilterStringRule(proveedor, evaluador, _texto_seguro(valor), False)
        elif tipo == DB.StorageType.Double and op in _EVALUADORES_NUMERICOS and _es_numero(valor):
            factor = factor_a_interno(param) or 1.0
            evaluador = getattr(DB, _EVALUADORES_NUMERICOS[op])()
            regla = DB.FilterDoubleRule(proveedor, evaluador, float(valor) * factor, EPSILON_INTERNO)
        elif tipo == DB.StorageType.Integer and op in _EVALUADORES_NUMERICOS:
            entero = _valor_booleano(valor)
            if entero is None:
                if not _es_numero(valor):
                    return None, None
                entero = int(float(valor))
            evaluador = getattr(DB, _EVALUADORES_NUMERICOS[op])()
            regla = DB.FilterIntegerRule(proveedor, evaluador, entero)
        elif (tipo == DB.StorageType.ElementId and op == "=" and _es_numero(valor)
              and not isinstance(valor, _cadena)):
            regla = DB.FilterElementIdRule(proveedor, DB.FilterNumericEquals(), make_element_id(int(valor)))
        else:
            return None, None
        return DB.ElementParameterFilter(regla), motivo
    except Exception as error:
        logger.debug("No se pudo construir el filtro nativo para %s: %s", motivo, str(error))
        return None, None


def _coincide_tipo(type_name, nombre_tipo_e, nombre_fam, nombre_elem):
    candidatos = [nombre_tipo_e, nombre_elem]
    if nombre_fam and nombre_tipo_e:
        candidatos.append(u"{}: {}".format(nombre_fam, nombre_tipo_e))
        candidatos.append(u"{} : {}".format(nombre_fam, nombre_tipo_e))
    return type_name in [c for c in candidatos if c]


def _clave_orden(doc, elem, clave):
    """Clave de ordenacion sin mezclar numeros y textos: (falta, es_texto, valor)."""
    bajo = clave.lower()
    texto = None
    if bajo == "id":
        return (0, 0, float(get_element_id_value(elem)))
    if bajo in ("categoria", "category"):
        texto = nombre_categoria(elem)
    elif bajo in ("tipo", "type", "type_name"):
        texto = nombre_tipo(doc, elem)
    elif bajo in ("nivel", "level", "level_name"):
        texto = nombre_nivel(doc, elem)
    elif bajo in ("nombre", "name"):
        texto = get_element_name(elem)
    elif bajo in ("familia", "family"):
        tipo = _tipo_de(doc, elem)
        texto = nombre_familia(tipo) if tipo is not None else None
    else:
        param = buscar_parametro(doc, elem, clave)
        if param is None:
            return (1, 1, u"")
        numero = _numero_contrato(param)
        if numero is not None:
            return (0, 0, numero)
        texto = _texto_valor(param, doc)
    if texto is None:
        return (1, 1, u"")
    return (0, 1, _texto_seguro(texto).lower())


def _valores_campos(doc, elem, campos):
    valores = {}
    for nombre in campos:
        try:
            param = buscar_parametro(doc, elem, nombre)
            valores[nombre] = valor_contrato(param, doc)[0] if param is not None else None
        except Exception:
            valores[nombre] = None
    return valores


def consultar_elementos(doc, data):
    """Motor de POST /query/ (y de /find_elements/). Devuelve el dict de la respuesta.

    Filtros nativos (en Revit): categoria, vista, nivel (solo sin categoria),
    subproyecto, caja envolvente y los `filters` sobre BuiltInParameter o
    parametros compartidos de ejemplar. En Python: familia, tipo, name_contains,
    fase, el nombre del nivel y el resto de `filters`."""
    bics = _categorias(data)
    familia = _texto_seguro(data.get("family")).strip()
    type_name = _texto_seguro(data.get("type_name")).strip()
    name_contains = _texto_seguro(data.get("name_contains")).strip().lower()
    level_name = _texto_seguro(data.get("level") or data.get("level_name")).strip()
    view_id = data.get("view_id")
    workset_name = _texto_seguro(data.get("workset")).strip()
    phase_name = _texto_seguro(data.get("phase")).strip()
    filtros = normalizar_filtros(data.get("filters"))
    campos = data.get("fields") or []
    if isinstance(campos, _cadena):
        campos = [campos]
    solo_ids = bool(data.get("ids_only", False))
    page = _entero(data.get("page"), 1, minimo=1)
    page_size = _entero(data.get("page_size") or data.get("max"), PAGINA_DEFECTO, minimo=1)
    avisos = []
    if page_size > LIMITE_PAGINA:
        avisos.append(u"page_size {} reducido al maximo {}".format(page_size, LIMITE_PAGINA))
        page_size = LIMITE_PAGINA
    sort_by = _texto_seguro(data.get("sort_by")).strip()
    descendente = sort_by.startswith("-")
    clave_orden = sort_by.lstrip("-+").strip()

    vista = None
    if view_id is not None:
        vista = _elemento(doc, view_id)
        if not isinstance(vista, DB.View):
            raise EscrituraRechazada("view_id {} is not a view".format(view_id), 400)
        try:
            if vista.IsTemplate:
                raise EscrituraRechazada("view_id {} is a view template; give a real view".format(view_id), 400)
        except EscrituraRechazada:
            raise
        except Exception:
            pass
    nivel = None
    if level_name:
        mapa = mapa_niveles(doc)
        nivel = mapa.get(level_name)
        if nivel is None:
            raise EscrituraRechazada(
                "Level '{}' not found".format(level_name), 404, {"available_levels": sorted(mapa.keys())},
            )
    workset_id = None
    if workset_name:
        if not doc.IsWorkshared:
            raise EscrituraRechazada("The document is not workshared: it has no worksets", 400)
        from consulta import listar_worksets

        worksets = listar_worksets(doc)
        for ws in worksets:
            if ws["nombre"] == workset_name:
                workset_id = ws["id"]
                break
        if workset_id is None:
            raise EscrituraRechazada(
                "Workset '{}' not found".format(workset_name), 404,
                {"available_worksets": [w["nombre"] for w in worksets]},
            )
    fase_id = None
    if phase_name:
        nombres = []
        try:
            for fase in doc.Phases:
                nombre = get_element_name(fase)
                nombres.append(nombre)
                if nombre == phase_name:
                    fase_id = fase.Id
                    break
        except Exception:
            pass
        if fase_id is None:
            raise EscrituraRechazada(
                "Phase '{}' not found".format(phase_name), 404, {"available_phases": nombres},
            )
    contorno = None
    bbox_min = data.get("bbox_min_mm")
    bbox_max = data.get("bbox_max_mm")
    if (bbox_min is None) != (bbox_max is None):
        raise EscrituraRechazada("bbox_min_mm and bbox_max_mm go together", 400)
    if bbox_min is not None:
        try:
            a = xyz_desde_mm(bbox_min)
            b = xyz_desde_mm(bbox_max)
        except ValueError as error:
            raise EscrituraRechazada("bbox: {}".format(error), 400)
        contorno = DB.Outline(
            DB.XYZ(min(a.X, b.X), min(a.Y, b.Y), min(a.Z, b.Z)),
            DB.XYZ(max(a.X, b.X), max(a.Y, b.Y), max(a.Z, b.Z)),
        )

    if not any([bics, familia, type_name, name_contains, nivel is not None, vista is not None,
                workset_id is not None, fase_id is not None, contorno is not None, filtros]):
        raise EscrituraRechazada(
            "Give at least one criterion: category, family, type_name, name_contains, level, view_id, "
            "workset, phase, filters or bbox_min_mm/bbox_max_mm", 400,
        )

    aplicados = []

    def base():
        col = DB.FilteredElementCollector(doc, vista.Id) if vista is not None else DB.FilteredElementCollector(doc)
        if len(bics) == 1:
            col = col.OfCategory(bics[0])
        elif bics:
            lista = List[DB.BuiltInCategory]()
            for bic in bics:
                lista.Add(bic)
            col = col.WherePasses(DB.ElementMulticategoryFilter(lista))
        col = col.WhereElementIsNotElementType()
        if nivel is not None and not bics and vista is None:
            try:
                col = col.WherePasses(DB.ElementLevelFilter(nivel.Id))
            except Exception:
                pass
        if workset_id is not None:
            col = col.WherePasses(DB.ElementWorksetFilter(DB.WorksetId(workset_id)))
        if contorno is not None:
            col = col.WherePasses(DB.BoundingBoxIntersectsFilter(contorno))
        return col

    if bics:
        aplicados.append("category")
    if vista is not None:
        aplicados.append("view_id")
    if nivel is not None and not bics and vista is None:
        aplicados.append("level (ElementLevelFilter)")
    if workset_id is not None:
        aplicados.append("workset")
    if contorno is not None:
        aplicados.append("bbox")

    # Filtros de parametro: nativos cuando una muestra de elementos resuelve el
    # parametro a un BuiltInParameter o compartido de ejemplar; si no, en Python.
    nativos = []
    en_python = []
    if filtros:
        muestra = []
        try:
            for elem in base():
                muestra.append(elem)
                if len(muestra) >= MAX_MUESTRA_PARAMETRO:
                    break
        except Exception as error:
            logger.debug("No se pudo tomar la muestra para los filtros: %s", str(error))
            muestra = []
        for filtro in filtros:
            filtro_nativo = None
            motivo = None
            for elem in muestra:
                param = buscar_por_nombre(elem, filtro["parameter"])
                if param is None:
                    continue
                filtro_nativo, motivo = regla_nativa(param, filtro["op"], filtro["value"])
                break
            if filtro_nativo is not None:
                nativos.append((filtro, filtro_nativo, motivo))
            else:
                en_python.append(filtro)

    col = base()
    for filtro, filtro_nativo, motivo in nativos:
        col = col.WherePasses(filtro_nativo)

    coincidentes = []
    escaneados = 0
    for elem in col:
        escaneados += 1
        try:
            if nivel is not None and nombre_nivel(doc, elem) != level_name:
                continue
            if fase_id is not None:
                try:
                    creada = elem.CreatedPhaseId
                except Exception:
                    creada = None
                if creada is None or creada != fase_id:
                    continue
            if familia or type_name or name_contains:
                tipo = _tipo_de(doc, elem)
                nombre_t = get_element_name(tipo) if tipo is not None else None
                nombre_f = nombre_familia(tipo) if tipo is not None else None
                nombre_e = get_element_name(elem)
                if familia and (nombre_f or u"").lower() != familia.lower():
                    continue
                if type_name and not _coincide_tipo(type_name, nombre_t, nombre_f, nombre_e):
                    continue
                if name_contains:
                    textos = u" ".join([nombre_e or u"", nombre_t or u"", nombre_f or u""]).lower()
                    if name_contains not in textos:
                        continue
            cumple = True
            for filtro in en_python:
                param = buscar_parametro(doc, elem, filtro["parameter"])
                if param is None or not cumple_filtro(param, filtro["op"], filtro["value"], doc):
                    cumple = False
                    break
            if not cumple:
                continue
            coincidentes.append(elem)
        except Exception as error:
            logger.debug("Elemento saltado en la consulta: %s", str(error))
            continue

    total = len(coincidentes)
    if clave_orden:
        coincidentes.sort(key=lambda e: _clave_orden(doc, e, clave_orden), reverse=descendente)
    inicio = (page - 1) * page_size
    pagina = coincidentes[inicio:inicio + page_size]
    elementos = []
    for elem in pagina:
        try:
            if solo_ids:
                elementos.append({"id": get_element_id_value(elem)})
                continue
            datos = describir_elemento(doc, elem) or {"id": get_element_id_value(elem)}
            tipo = _tipo_de(doc, elem)
            datos["familia"] = nombre_familia(tipo) if tipo is not None else None
            if campos:
                datos["fields"] = _valores_campos(doc, elem, campos)
            elementos.append(datos)
        except Exception:
            continue
    paginas = int(math.ceil(total / float(page_size))) if total else 0
    if not bics and vista is None and nivel is None and contorno is None and workset_id is None:
        avisos.append(u"Sin category, view_id, level, workset ni bbox se recorren todos los elementos "
                      u"del modelo ({} escaneados)".format(escaneados))
    return {
        "elements": elementos,
        "ids": [e["id"] for e in elementos],
        "count": len(elementos),
        "total_matched": total,
        "scanned": escaneados,
        "page": page,
        "page_size": page_size,
        "pages": paginas,
        "truncated": inicio + len(elementos) < total,
        "sort_by": sort_by or None,
        "native": aplicados + [
            u"filter {} {} ({})".format(f["parameter"], f["op"], motivo) for f, _, motivo in nativos
        ],
        "python_filters": [{"parameter": f["parameter"], "op": f["op"]} for f in en_python],
        "warnings": avisos,
    }


# ---------------------------------------------------------------------------
# Advertencias agrupadas por tipo (FailureDefinitionId)
# ---------------------------------------------------------------------------
def tabla_sugerencias():
    """{guid en minusculas: (nombre del fallo, sugerencia)} resuelta contra DB.BuiltInFailures."""
    global _tabla_sugerencias
    if _tabla_sugerencias is not None:
        return _tabla_sugerencias
    tabla = {}
    grupos = getattr(DB, "BuiltInFailures", None)
    for grupo, nombre, sugerencia in SUGERENCIAS_FALLOS:
        try:
            definicion = getattr(getattr(grupos, grupo), nombre)
            guid = _texto_seguro(definicion.Guid).lower()
        except Exception:
            continue
        if guid:
            tabla[guid] = (u"{}.{}".format(grupo, nombre), sugerencia)
    _tabla_sugerencias = tabla
    return tabla


def agrupar_avisos(avisos, max_ids=MAX_IDS_POR_GRUPO):
    """Agrupa doc.GetWarnings() por FailureDefinitionId (o por descripcion si no lo hay)."""
    tabla = tabla_sugerencias()
    grupos = {}
    orden = []
    for aviso in avisos:
        guid = None
        try:
            guid = _texto_seguro(aviso.GetFailureDefinitionId().Guid).lower() or None
        except Exception:
            guid = None
        try:
            descripcion = _texto_seguro(aviso.GetDescriptionText())
        except Exception:
            descripcion = u"?"
        clave = guid or descripcion
        grupo = grupos.get(clave)
        if grupo is None:
            nombre, sugerencia = tabla.get(guid, (None, None)) if guid else (None, None)
            try:
                severidad = str(aviso.GetSeverity()).split(".")[-1]
            except Exception:
                severidad = "?"
            grupo = {
                "descripcion": descripcion,
                "failure_definition_guid": guid,
                "failure": nombre,
                "severidad": severidad,
                "count": 0,
                "element_ids": [],
                "elements_total": 0,
                "sugerencia": sugerencia or SUGERENCIA_GENERICA,
            }
            grupos[clave] = grupo
            orden.append(clave)
        grupo["count"] += 1
        try:
            ids = [get_element_id_value(i) for i in aviso.GetFailingElements()]
        except Exception:
            ids = []
        for identificador in ids:
            if identificador in grupo["element_ids"]:
                continue
            grupo["elements_total"] += 1
            if len(grupo["element_ids"]) < max_ids:
                grupo["element_ids"].append(identificador)
    lista = [grupos[clave] for clave in orden]
    lista.sort(key=lambda g: -g["count"])
    for grupo in lista:
        grupo["ids_truncated"] = grupo["elements_total"] > len(grupo["element_ids"])
    return lista


# ---------------------------------------------------------------------------
# Tablas de planificacion
# ---------------------------------------------------------------------------
def _tablas(doc):
    tablas = []
    try:
        for tabla in DB.FilteredElementCollector(doc).OfClass(DB.ViewSchedule).WhereElementIsNotElementType():
            try:
                if tabla.IsTemplate:
                    continue
            except Exception:
                pass
            tablas.append(tabla)
    except Exception:
        pass
    return tablas


def _buscar_tabla(doc, data):
    view_id = data.get("view_id")
    nombre = _texto_seguro(data.get("name") or data.get("schedule_name")).strip()
    if view_id is None and not nombre:
        raise EscrituraRechazada("name or view_id is required", 400)
    if view_id is not None:
        tabla = _elemento(doc, view_id)
        if not isinstance(tabla, DB.ViewSchedule):
            raise EscrituraRechazada("view_id {} is not a schedule (ViewSchedule)".format(view_id), 400)
        return tabla
    disponibles = []
    for tabla in _tablas(doc):
        etiqueta = get_element_name(tabla)
        if etiqueta == nombre:
            return tabla
        disponibles.append(etiqueta)
    raise EscrituraRechazada(
        u"Schedule '{}' not found".format(nombre), 404, {"available_schedules": sorted(disponibles)[:50]},
    )


def tabla_a_json(doc, tabla, start_row=0, max_rows=FILAS_DEFECTO):
    """Encabezados y filas del cuerpo de la tabla (GetTableData / GetCellText)."""
    definicion = tabla.Definition
    campos = []
    try:
        for indice in range(definicion.GetFieldCount()):
            campo = definicion.GetField(indice)
            datos = {"name": None, "heading": None, "hidden": False}
            try:
                datos["name"] = _texto_seguro(campo.GetName())
            except Exception:
                pass
            try:
                datos["heading"] = _texto_seguro(campo.ColumnHeading)
            except Exception:
                pass
            try:
                datos["hidden"] = bool(campo.IsHidden)
            except Exception:
                pass
            campos.append(datos)
    except Exception as error:
        logger.debug("No se pudieron leer los campos de la tabla: %s", str(error))
    mostrar_encabezados = True
    try:
        mostrar_encabezados = bool(definicion.ShowHeaders)
    except Exception:
        pass

    datos_tabla = tabla.GetTableData()
    seccion = datos_tabla.GetSectionData(DB.SectionType.Body)
    total_filas = int(seccion.NumberOfRows)
    columnas = int(seccion.NumberOfColumns)
    fin = min(total_filas, start_row + max_rows)
    filas = []
    for fila in range(start_row, fin):
        celdas = []
        for columna in range(columnas):
            try:
                celdas.append(_texto_seguro(tabla.GetCellText(DB.SectionType.Body, fila, columna)))
            except Exception:
                celdas.append(u"")
        filas.append(celdas)
    if mostrar_encabezados and start_row == 0 and filas:
        encabezados = filas[0]
        filas = filas[1:]
        origen_encabezados = "body row 0"
    else:
        encabezados = [c["heading"] or c["name"] or u"" for c in campos if not c["hidden"]]
        origen_encabezados = "field headings"
    titulo = None
    try:
        titulo = _texto_seguro(tabla.GetCellText(DB.SectionType.Header, 0, 0))
    except Exception:
        pass
    resultado = {
        "schedule": {"id": get_element_id_value(tabla), "name": get_element_name(tabla), "title": titulo},
        "headers": encabezados,
        "headers_from": origen_encabezados,
        "rows": filas,
        "row_count": len(filas),
        "total_rows": total_filas,
        "columns": columnas,
        "start_row": start_row,
        "truncated": fin < total_filas,
        "fields": campos,
    }
    for atributo, clave in (("IsItemized", "is_itemized"), ("ShowGrandTotal", "show_grand_total")):
        try:
            resultado[clave] = bool(getattr(definicion, atributo))
        except Exception:
            resultado[clave] = None
    return resultado


# ---------------------------------------------------------------------------
# Extension de una vista
# ---------------------------------------------------------------------------
def _nombre_nivel_rango(doc, nivel_id):
    for atributo, etiqueta in (("Unlimited", "unlimited"), ("Current", "current"),
                               ("LevelAbove", "level_above"), ("LevelBelow", "level_below")):
        try:
            if nivel_id == getattr(DB.PlanViewRange, atributo):
                return etiqueta
        except Exception:
            continue
    if _es_invalido(nivel_id):
        return None
    try:
        nivel = doc.GetElement(nivel_id)
        return get_element_name(nivel) if nivel is not None else get_element_id_value(nivel_id)
    except Exception:
        return None


def _rango_vista(doc, vista):
    try:
        rango = vista.GetViewRange()
    except Exception:
        return None
    planos = (("top", "TopClipPlane"), ("cut", "CutPlane"), ("bottom", "BottomClipPlane"),
              ("view_depth", "ViewDepthPlane"), ("underlay_bottom", "UnderlayBottom"))
    resultado = {}
    for clave, nombre in planos:
        try:
            plano = getattr(DB.PlanViewPlane, nombre)
            resultado[clave] = {
                "level": _nombre_nivel_rango(doc, rango.GetLevelId(plano)),
                "offset_mm": round(rango.GetOffset(plano) * FEET_TO_MM, 1),
            }
        except Exception:
            continue
    return resultado or None


def _caja_vista(caja):
    if caja is None:
        return None
    datos = {"min_mm": punto_a_mm(caja.Min), "max_mm": punto_a_mm(caja.Max)}
    try:
        transformacion = caja.Transform
        datos["min_model_mm"] = punto_a_mm(transformacion.OfPoint(caja.Min))
        datos["max_model_mm"] = punto_a_mm(transformacion.OfPoint(caja.Max))
        datos["origin_mm"] = punto_a_mm(transformacion.Origin)
    except Exception:
        pass
    return datos


def extension_vista(doc, vista):
    """Recorte, rango de vista, escala, nivel, disciplina y plantilla de una vista."""
    datos = {
        "view_id": get_element_id_value(vista),
        "name": get_element_name(vista),
        "view_type": None,
        "is_template": None,
        "scale": None,
        "level": None,
        "discipline": None,
        "detail_level": None,
        "view_template": None,
        "view_template_id": None,
        "crop": {"active": None, "visible": None, "box": None},
        "view_range": None,
        "section_box": None,
        "sheet_number": None,
        "phase": None,
    }
    for atributo, clave in (("ViewType", "view_type"), ("IsTemplate", "is_template"), ("Scale", "scale"),
                            ("Discipline", "discipline"), ("DetailLevel", "detail_level")):
        try:
            valor = getattr(vista, atributo)
            datos[clave] = bool(valor) if clave == "is_template" else (
                int(valor) if clave == "scale" else str(valor))
        except Exception:
            continue
    try:
        nivel = vista.GenLevel
        if nivel is not None:
            datos["level"] = get_element_name(nivel)
    except Exception:
        pass
    try:
        plantilla_id = vista.ViewTemplateId
        if not _es_invalido(plantilla_id):
            plantilla = doc.GetElement(plantilla_id)
            datos["view_template_id"] = get_element_id_value(plantilla_id)
            datos["view_template"] = get_element_name(plantilla) if plantilla is not None else None
    except Exception:
        pass
    try:
        datos["crop"]["active"] = bool(vista.CropBoxActive)
        datos["crop"]["visible"] = bool(vista.CropBoxVisible)
    except Exception:
        pass
    try:
        datos["crop"]["box"] = _caja_vista(vista.CropBox)
    except Exception:
        pass
    if isinstance(vista, DB.ViewPlan):
        datos["view_range"] = _rango_vista(doc, vista)
    if isinstance(vista, DB.View3D):
        try:
            datos["section_box"] = {"active": bool(vista.IsSectionBoxActive), "box": _caja_vista(vista.GetSectionBox())}
        except Exception:
            pass
    for bip, clave in (("VIEWER_SHEET_NUMBER", "sheet_number"), ("VIEW_PHASE", "phase")):
        try:
            param = vista.get_Parameter(getattr(DB.BuiltInParameter, bip))
            if param is not None and param.HasValue:
                datos[clave] = valor_parametro(param, doc) or None
        except Exception:
            continue
    return datos


# ---------------------------------------------------------------------------
# Rutas
# ---------------------------------------------------------------------------
def register_navegacion_routes(api):
    """Register the deep-navigation read routes with the API."""

    @api.route("/describe/", methods=["POST"])
    @requiere_token
    def describe_element(doc, request):
        """Descripcion completa: parametros, alojados, unidos, dependientes, referencias y geometria."""

        def cuerpo(data):
            elem = _elemento(doc, data.get("element_id"))
            depth = _entero(data.get("depth"), 0, minimo=0, maximo=2)
            include_geometry = bool(data.get("include_geometry", False))
            datos = describir_a_fondo(doc, elem, depth, include_geometry)
            datos["status"] = "success"
            return datos

        return _responder(doc, request, cuerpo)

    @api.route("/dependency_graph/", methods=["POST"])
    @requiere_token
    def dependency_graph(doc, request):
        """Nodos y aristas (hosts, joins, depends) alrededor de un elemento."""

        def cuerpo(data):
            elem = _elemento(doc, data.get("element_id"))
            max_nodes = _entero(data.get("max_nodes"), NODOS_DEFECTO, minimo=1, maximo=MAX_NODOS)
            max_depth = _entero(data.get("max_depth"), PROFUNDIDAD_DEFECTO, minimo=1, maximo=MAX_PROFUNDIDAD)
            datos = grafo_dependencias(doc, elem, max_nodes, max_depth)
            datos["status"] = "success"
            return datos

        return _responder(doc, request, cuerpo)

    @api.route("/query/", methods=["POST"])
    @requiere_token
    def query_elements(doc, request):
        """Consulta paginada con filtros de parametro (nativos cuando se puede)."""

        def cuerpo(data):
            datos = consultar_elementos(doc, data)
            datos["status"] = "success"
            return datos

        return _responder(doc, request, cuerpo)

    @api.route("/schedule/", methods=["POST"])
    @requiere_token
    def schedule_to_json(doc, request):
        """Encabezados y filas de una tabla de planificacion (por nombre o view_id)."""

        def cuerpo(data):
            tabla = _buscar_tabla(doc, data)
            start_row = _entero(data.get("start_row"), 0, minimo=0)
            max_rows = _entero(data.get("max_rows"), FILAS_DEFECTO, minimo=1, maximo=MAX_FILAS)
            datos = tabla_a_json(doc, tabla, start_row, max_rows)
            datos["status"] = "success"
            return datos

        return _responder(doc, request, cuerpo)

    @api.route("/view_extents/", methods=["POST"])
    @requiere_token
    def get_view_extents(doc, request):
        """Recorte, rango de vista, escala, nivel, disciplina y plantilla de una vista."""

        def cuerpo(data):
            view_id = data.get("view_id")
            nombre = _texto_seguro(data.get("view_name")).strip()
            if view_id is None and not nombre:
                raise EscrituraRechazada("view_id (or view_name) is required", 400)
            if view_id is not None:
                vista = _elemento(doc, view_id)
            else:
                from utils import buscar_vista

                vista = buscar_vista(doc, nombre)
                if vista is None:
                    raise EscrituraRechazada(u"View '{}' not found".format(nombre), 404)
            if not isinstance(vista, DB.View):
                raise EscrituraRechazada("Element {} is not a view".format(view_id), 400)
            datos = extension_vista(doc, vista)
            datos["status"] = "success"
            return datos

        return _responder(doc, request, cuerpo)

    logger.info("Navegacion routes registered successfully")
