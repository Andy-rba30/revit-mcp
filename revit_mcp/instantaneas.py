# -*- coding: UTF-8 -*-
"""
Instantaneas del modelo para Revit MCP (0.3.0):

  POST /snapshot/        name*, categories[], parameters[], include_parameters,
                         max_elements, overwrite
  POST /diff_snapshots/  a*, b (otra instantanea o "actual" = el modelo ahora),
                         max_items

snapshot_model guarda <carpeta del rvt>\\snapshots\\<name>.json (o
%LOCALAPPDATA%\\RevitMcp\\snapshots\\ si el modelo no esta guardado) con, por
elemento: id, UniqueId, categoria, tipo, nivel, bbox_mm, un hash de sus
parametros y, por defecto, los valores de esos parametros (asi diff_snapshots
puede decir cuales cambiaron). Por defecto entran los elementos de categoria
de modelo que no son especificos de una vista, mas los niveles y las rejillas.
No abre transaccion ni modifica el modelo; el limite de elementos es
configurable y la respuesta avisa si se trunca.

diff_snapshots compara dos instantaneas por UniqueId: `added`, `removed` y
`modified` (parametros que cambiaron, cambios de tipo o nivel y movimientos
de la caja envolvente de mas de 1 mm).

0.4.0, rendimiento medible: en la validacion de 0.3.1 la instantanea tardo
8,2 s con 2746 elementos bloqueando Revit. Ahora `timings` desglosa los ms
por etapa (recoleccion, descripcion, bbox, parametros, hash, escritura),
`include_bbox=false` evita get_BoundingBox, los parametros se leen una vez
con GetOrderedParameters() y se hashea la cadena concatenada, y los nombres
de tipo, nivel y de los elementos referenciados por ElementId se cachean por
id en vez de pedirselos a Revit por cada parametro de cada elemento.
"""

from utils import get_element_name, get_element_id_value, buscar_por_nombre, leer_texto_utf8
from seguridad import requiere_token
from escritura import (
    describir_elemento, carpeta_log, EscrituraRechazada, titulo_documento, ruta_documento,
    bbox_mm, nombre_categoria, _iso, _ahora, _a_texto,
)
from parameters import factor_a_interno
from clash import _resolve_bic
from navegacion import _responder, _texto_seguro, _entero, _es_de_modelo, _es_invalido
from pyrevit import routes, revit, DB
from System.Collections.Generic import List
import io
import json
import os
import re
import time
import zlib
import logging

try:
    import hashlib
except ImportError:  # pragma: no cover - IronPython sin hashlib
    hashlib = None

logger = logging.getLogger(__name__)

try:
    _texto = unicode  # IronPython 2.7
    _cadena = basestring
except NameError:  # pragma: no cover - CPython 3 en las pruebas
    _texto = str
    _cadena = str

CARPETA_SNAPSHOTS = "snapshots"
VERSION_FORMATO = 1
MAX_ELEMENTOS_DEFECTO = 20000
MAX_ELEMENTOS_TOPE = 200000
MAX_ITEMS_DEFECTO = 500
MAX_ITEMS_TOPE = 5000
TOLERANCIA_BBOX_MM = 1.0
# Categorias que no son de modelo (CategoryType.Annotation) pero que forman
# parte del esqueleto del proyecto y entran en la instantanea por defecto.
CATEGORIAS_EXTRA = ("OST_Levels", "OST_Grids")
ALIAS_ACTUAL = ("", "actual", "current", "live", "model", "modelo", "now")


# ---------------------------------------------------------------------------
# Rutas de archivo
# ---------------------------------------------------------------------------
def carpeta_snapshots(doc):
    return os.path.join(carpeta_log(doc), CARPETA_SNAPSHOTS)


def nombre_limpio(name):
    limpio = re.sub(r"[^\w\-. ]+", "_", _texto_seguro(name), flags=re.UNICODE).strip(" .")
    if not limpio:
        raise EscrituraRechazada("name must contain letters or digits", 400)
    return limpio


def ruta_snapshot(doc, name):
    """Ruta del .json de la instantanea, siempre dentro de carpeta_snapshots(doc).

    `name` es un nombre, no una ruta: con separadores o ruta absoluta se rechaza
    (400) para que /snapshot/ y /diff_snapshots/ no escriban ni lean archivos
    arbitrarios."""
    texto = _texto_seguro(name).strip()
    if not texto:
        raise EscrituraRechazada("name is required", 400)
    if os.path.isabs(texto) or os.sep in texto or "/" in texto or "\\" in texto:
        raise EscrituraRechazada(
            u"name must be a snapshot name, not a path (snapshots are stored in {})".format(carpeta_snapshots(doc)), 400,
        )
    if texto.lower().endswith(".json"):
        texto = texto[:-5]
    return os.path.join(carpeta_snapshots(doc), nombre_limpio(texto) + ".json")


def listar_snapshots(doc, maximo=50):
    carpeta = carpeta_snapshots(doc)
    if not os.path.isdir(carpeta):
        return []
    nombres = sorted(n[:-5] for n in os.listdir(carpeta) if n.lower().endswith(".json"))
    return nombres[:maximo]


# ---------------------------------------------------------------------------
# Firma de cada elemento
# ---------------------------------------------------------------------------
def _hash(texto):
    datos = texto.encode("utf-8")
    if hashlib is not None:
        try:
            return hashlib.md5(datos).hexdigest()
        except Exception:
            pass
    return "crc32:{:08x}".format(zlib.crc32(datos) & 0xffffffff)


class CacheNombres(object):
    """Nombres de tipo, nivel y elementos referenciados, por id (una lectura por id y no por parametro)."""

    def __init__(self, doc):
        self.doc = doc
        self._nombres = {}      # id -> nombre del elemento (tipos, niveles, referencias)
        self._niveles = {}      # id de elemento -> nombre de su nivel (via LevelId o parametro)
        self.consultas = 0

    def nombre_por_id(self, eid):
        if _es_invalido(eid):
            return None
        try:
            clave = get_element_id_value(eid)
        except Exception:
            return None
        if clave not in self._nombres:
            self.consultas += 1
            try:
                elem = self.doc.GetElement(eid)
                self._nombres[clave] = get_element_name(elem) if elem is not None else clave
            except Exception:
                self._nombres[clave] = clave
        return self._nombres[clave]

    def tipo(self, elem):
        try:
            tipo_id = elem.GetTypeId()
        except Exception:
            tipo_id = None
        if _es_invalido(tipo_id):
            try:
                return get_element_name(elem)
            except Exception:
                return None
        return self.nombre_por_id(tipo_id)

    def nivel(self, elem):
        nivel_id = None
        try:
            nivel_id = elem.LevelId
        except Exception:
            nivel_id = None
        if _es_invalido(nivel_id):
            for bip in ("FAMILY_LEVEL_PARAM", "LEVEL_PARAM", "SCHEDULE_LEVEL_PARAM", "FAMILY_BASE_LEVEL_PARAM"):
                try:
                    parametro = elem.get_Parameter(getattr(DB.BuiltInParameter, bip))
                    if parametro:
                        candidato = parametro.AsElementId()
                        if not _es_invalido(candidato):
                            nivel_id = candidato
                            break
                except Exception:
                    continue
        if _es_invalido(nivel_id):
            return None
        return self.nombre_por_id(nivel_id)


def _valor_serializable(param, doc, cache=None):
    """Valor comparable: Double en unidades del contrato (3 decimales), Integer,
    String o el nombre del elemento referenciado por un ElementId."""
    try:
        if not param.HasValue:
            return None
        tipo = param.StorageType
        if tipo == DB.StorageType.Double:
            factor = factor_a_interno(param)
            bruto = param.AsDouble()
            return round(bruto / factor if factor else bruto, 3)
        if tipo == DB.StorageType.Integer:
            return param.AsInteger()
        if tipo == DB.StorageType.String:
            return param.AsString()
        if tipo == DB.StorageType.ElementId:
            eid = param.AsElementId()
            if _es_invalido(eid):
                return None
            if cache is not None:
                return cache.nombre_por_id(eid)
            elem = doc.GetElement(eid)
            return get_element_name(elem) if elem is not None else get_element_id_value(eid)
    except Exception:
        return None
    return None


def _parametros_de(elem):
    """GetOrderedParameters() una sola vez (reserva: Parameters)."""
    try:
        return list(elem.GetOrderedParameters())
    except Exception:
        pass
    try:
        return list(elem.Parameters)
    except Exception:
        return []


def _parametros_elemento(doc, elem, nombres, cache=None):
    """{nombre visible: valor} de los parametros pedidos o de todos los de ejemplar."""
    valores = {}
    if nombres:
        for nombre in nombres:
            param = buscar_por_nombre(elem, nombre)
            if param is None:
                continue
            try:
                etiqueta = _texto_seguro(param.Definition.Name)
            except Exception:
                etiqueta = _texto_seguro(nombre)
            valores[etiqueta] = _valor_serializable(param, doc, cache)
        return valores
    for param in _parametros_de(elem):
        try:
            etiqueta = _texto_seguro(param.Definition.Name)
        except Exception:
            continue
        if etiqueta in valores:
            continue  # Revit repite algunos nombres visibles
        valores[etiqueta] = _valor_serializable(param, doc, cache)
    return valores


def _texto_hash(valores):
    return u"|".join(
        u"{}={}".format(clave, u"<null>" if valores[clave] is None else _texto_seguro(valores[clave]))
        for clave in sorted(valores.keys())
    )


def firma_elemento(doc, elem, nombres=None, incluir_parametros=True, incluir_bbox=True, cache=None, timings=None):
    """Firma de un elemento; con `cache` y `timings` (dict de ms) se usa desde instantanea()."""
    if cache is None:
        cache = CacheNombres(doc)
    t0 = time.time()
    try:
        identificador = get_element_id_value(elem)
    except Exception:
        identificador = None
    try:
        unique_id = _texto_seguro(elem.UniqueId)
    except Exception:
        unique_id = None
    datos = {
        "id": identificador,
        "unique_id": unique_id,
        "categoria": nombre_categoria(elem),
        "tipo": cache.tipo(elem),
        "nivel": cache.nivel(elem),
    }
    t1 = time.time()
    datos["bbox_mm"] = bbox_mm(elem) if incluir_bbox else None
    t2 = time.time()
    valores = _parametros_elemento(doc, elem, nombres, cache)
    t3 = time.time()
    datos["hash"] = _hash(_texto_hash(valores))
    t4 = time.time()
    if incluir_parametros:
        datos["params"] = valores
    if timings is not None:
        timings["descripcion_ms"] = timings.get("descripcion_ms", 0.0) + (t1 - t0) * 1000
        timings["bbox_ms"] = timings.get("bbox_ms", 0.0) + (t2 - t1) * 1000
        timings["parametros_ms"] = timings.get("parametros_ms", 0.0) + (t3 - t2) * 1000
        timings["hash_ms"] = timings.get("hash_ms", 0.0) + (t4 - t3) * 1000
    return datos


# ---------------------------------------------------------------------------
# Seleccion de elementos e instantanea en memoria
# ---------------------------------------------------------------------------
def _bics(categorias):
    bics = []
    for nombre in categorias:
        bic = _resolve_bic(nombre)
        if bic is None:
            raise EscrituraRechazada(
                "Invalid category '{}'. Use BuiltInCategory names like OST_Walls or aliases like "
                "'walls', 'beams'".format(nombre), 400,
            )
        bics.append(bic)
    return bics


def elementos_snapshot(doc, categorias=None):
    """(elementos, etiquetas de categoria) a incluir en la instantanea."""
    if categorias:
        bics = _bics(categorias)
        lista = List[DB.BuiltInCategory]()
        for bic in bics:
            lista.Add(bic)
        col = (
            DB.FilteredElementCollector(doc)
            .WherePasses(DB.ElementMulticategoryFilter(lista))
            .WhereElementIsNotElementType()
        )
        return list(col), [str(b) for b in bics]

    extras = set()
    for nombre in CATEGORIAS_EXTRA:
        bic = getattr(DB.BuiltInCategory, nombre, None)
        if bic is not None:
            try:
                extras.add(int(bic))
            except Exception:
                continue
    elementos = []
    for elem in DB.FilteredElementCollector(doc).WhereElementIsNotElementType():
        try:
            categoria = elem.Category
            if categoria is None:
                continue
            if _es_de_modelo(elem):
                try:
                    if elem.ViewSpecific:
                        continue
                except Exception:
                    pass
                elementos.append(elem)
                continue
            if get_element_id_value(categoria.Id) in extras:
                elementos.append(elem)
        except Exception:
            continue
    return elementos, ["model"] + list(CATEGORIAS_EXTRA)


def instantanea(doc, name, categorias=None, parametros=None, incluir_parametros=True,
                max_elements=MAX_ELEMENTOS_DEFECTO, incluir_bbox=True):
    """Instantanea en memoria (dict listo para guardar en JSON) con `timings` por etapa (ms)."""
    inicio = time.time()
    elementos, etiquetas = elementos_snapshot(doc, categorias)
    total = len(elementos)
    timings = {"recoleccion_ms": (time.time() - inicio) * 1000, "descripcion_ms": 0.0, "bbox_ms": 0.0,
               "parametros_ms": 0.0, "hash_ms": 0.0}
    cache = CacheNombres(doc)
    firmas = {}
    for elem in elementos[:max_elements]:
        try:
            firma = firma_elemento(doc, elem, parametros, incluir_parametros, incluir_bbox, cache, timings)
        except Exception as error:
            logger.debug("Elemento saltado en la instantanea: %s", str(error))
            continue
        if not firma.get("unique_id"):
            continue
        firmas[firma["unique_id"]] = firma
    timings = dict((clave, int(round(valor))) for clave, valor in timings.items())
    timings["total_ms"] = int((time.time() - inicio) * 1000)
    timings["elements"] = len(firmas)
    timings["name_lookups"] = cache.consultas
    return {
        "version": VERSION_FORMATO,
        "name": name,
        "documento": titulo_documento(doc),
        "rvt": ruta_documento(doc) or None,
        "fecha": _iso(_ahora()),
        "categories": etiquetas,
        "categories_requested": list(categorias) if categorias else None,
        "parameters": list(parametros) if parametros else None,
        "include_parameters": bool(incluir_parametros),
        "include_bbox": bool(incluir_bbox),
        "max_elements": max_elements,
        "count": len(firmas),
        "total_elements": total,
        "truncated": total > max_elements,
        "elements": firmas,
        "ms": timings["total_ms"],
        "timings": timings,
    }


def guardar_snapshot(datos, ruta, overwrite=False):
    if os.path.exists(ruta) and not overwrite:
        raise EscrituraRechazada(
            "Snapshot already exists: {}; pass overwrite=true to replace it".format(ruta), 409, {"ruta": ruta},
        )
    carpeta = os.path.dirname(ruta)
    if carpeta and not os.path.isdir(carpeta):
        os.makedirs(carpeta)
    texto = json.dumps(datos, ensure_ascii=False, default=_a_texto)
    with io.open(ruta, "w", encoding="utf-8") as archivo:
        archivo.write(_texto(texto))
    return ruta


def cargar_snapshot(doc, name):
    ruta = ruta_snapshot(doc, name)
    if not os.path.isfile(ruta):
        raise EscrituraRechazada(
            "Snapshot not found: {}".format(ruta), 404, {"available_snapshots": listar_snapshots(doc)},
        )
    datos = json.loads(leer_texto_utf8(ruta))
    if not isinstance(datos, dict) or not isinstance(datos.get("elements"), dict):
        raise EscrituraRechazada("{} is not a snapshot file".format(ruta), 400)
    return datos, ruta


def _meta(datos, ruta):
    return {
        "name": datos.get("name"),
        "ruta": ruta,
        "fecha": datos.get("fecha"),
        "documento": datos.get("documento"),
        "count": datos.get("count"),
        "truncated": datos.get("truncated"),
        "categories": datos.get("categories"),
        "parameters": datos.get("parameters"),
        "include_parameters": datos.get("include_parameters"),
    }


# ---------------------------------------------------------------------------
# Comparacion
# ---------------------------------------------------------------------------
def _bbox_movido(a, b):
    if not a or not b:
        return bool(a) != bool(b)
    for extremo in ("min", "max"):
        pa = a.get(extremo) or {}
        pb = b.get(extremo) or {}
        for eje in ("x", "y", "z"):
            try:
                if abs(float(pa.get(eje) or 0) - float(pb.get(eje) or 0)) > TOLERANCIA_BBOX_MM:
                    return True
            except (TypeError, ValueError):
                return True
    return False


def _sin_params(entrada):
    return dict((clave, valor) for clave, valor in entrada.items() if clave != "params")


def _orden(entrada):
    return (_texto_seguro(entrada.get("categoria")), entrada.get("id") or 0)


def comparar(a, b, max_items=MAX_ITEMS_DEFECTO):
    """added / removed / modified entre dos instantaneas (dicts cargados)."""
    ea = a.get("elements") or {}
    eb = b.get("elements") or {}
    anadidos = [eb[clave] for clave in eb if clave not in ea]
    eliminados = [ea[clave] for clave in ea if clave not in eb]
    modificados = []
    comunes = 0
    for clave in ea:
        if clave not in eb:
            continue
        comunes += 1
        antes = ea[clave]
        despues = eb[clave]
        cambios = []
        if antes.get("hash") != despues.get("hash"):
            pa = antes.get("params")
            pb = despues.get("params")
            if isinstance(pa, dict) and isinstance(pb, dict):
                for nombre in sorted(set(pa.keys()) | set(pb.keys())):
                    if pa.get(nombre) != pb.get(nombre):
                        cambios.append({"parametro": nombre, "antes": pa.get(nombre), "despues": pb.get(nombre)})
            if not cambios:
                cambios.append({
                    "parametro": None,
                    "detalle": u"hash distinto; la instantanea no guardo los valores de los parametros "
                               u"(include_parameters=false)",
                })
        for campo in ("categoria", "tipo", "nivel"):
            if antes.get(campo) != despues.get(campo):
                cambios.append({"parametro": campo, "antes": antes.get(campo), "despues": despues.get(campo)})
        movido = _bbox_movido(antes.get("bbox_mm"), despues.get("bbox_mm"))
        if not cambios and not movido:
            continue
        modificados.append({
            "id": despues.get("id"),
            "unique_id": clave,
            "categoria": despues.get("categoria"),
            "tipo": despues.get("tipo"),
            "nivel": despues.get("nivel"),
            "cambios": cambios,
            "bbox_movido": movido,
            "bbox_antes_mm": antes.get("bbox_mm") if movido else None,
            "bbox_despues_mm": despues.get("bbox_mm") if movido else None,
        })
    anadidos.sort(key=_orden)
    eliminados.sort(key=_orden)
    modificados.sort(key=_orden)
    return {
        "added": [_sin_params(e) for e in anadidos[:max_items]],
        "removed": [_sin_params(e) for e in eliminados[:max_items]],
        "modified": modificados[:max_items],
        "counts": {
            "added": len(anadidos),
            "removed": len(eliminados),
            "modified": len(modificados),
            "unchanged": comunes - len(modificados),
        },
        "truncated": max(len(anadidos), len(eliminados), len(modificados)) > max_items,
        "max_items": max_items,
    }


# ---------------------------------------------------------------------------
# Rutas
# ---------------------------------------------------------------------------
def _lista(valor):
    if valor in (None, "", []):
        return []
    if isinstance(valor, _cadena):
        return [valor]
    if isinstance(valor, (list, tuple)):
        return list(valor)
    raise EscrituraRechazada("categories and parameters must be lists of names", 400)


def register_instantaneas_routes(api):
    """Register snapshot / diff routes with the API."""

    @api.route("/snapshot/", methods=["POST"])
    @requiere_token
    def snapshot_model(doc, request):
        """Guarda la instantanea del modelo en snapshots\\<name>.json (sin transaccion)."""

        def cuerpo(data):
            name = data.get("name")
            ruta = ruta_snapshot(doc, name)
            categorias = _lista(data.get("categories"))
            parametros = _lista(data.get("parameters")) or None
            incluir = bool(data.get("include_parameters", True))
            incluir_bbox = bool(data.get("include_bbox", True))
            max_elements = _entero(data.get("max_elements"), MAX_ELEMENTOS_DEFECTO, minimo=1, maximo=MAX_ELEMENTOS_TOPE)
            overwrite = bool(data.get("overwrite", False))
            if os.path.exists(ruta) and not overwrite:
                raise EscrituraRechazada(
                    "Snapshot already exists: {}; pass overwrite=true to replace it".format(ruta), 409,
                    {"ruta": ruta},
                )
            etiqueta = os.path.splitext(os.path.basename(ruta))[0]
            datos = instantanea(doc, etiqueta, categorias, parametros, incluir, max_elements, incluir_bbox)
            inicio_escritura = time.time()
            guardar_snapshot(datos, ruta, overwrite)
            datos["timings"]["escritura_ms"] = int((time.time() - inicio_escritura) * 1000)
            datos["timings"]["total_ms"] += datos["timings"]["escritura_ms"]
            datos["ms"] = datos["timings"]["total_ms"]
            respuesta = dict((clave, valor) for clave, valor in datos.items() if clave != "elements")
            respuesta["ruta"] = ruta
            respuesta["status"] = "success"
            respuesta["message"] = u"Snapshot '{}' con {} elementos guardado en {}".format(
                etiqueta, datos["count"], ruta)
            if datos["truncated"]:
                respuesta["warning"] = (
                    u"Se guardaron {} de {} elementos (max_elements={}): sube max_elements o limita "
                    u"categories".format(datos["count"], datos["total_elements"], max_elements)
                )
            return respuesta

        return _responder(doc, request, cuerpo)

    @api.route("/diff_snapshots/", methods=["POST"])
    @requiere_token
    def diff_snapshots(doc, request):
        """Compara dos instantaneas (o una con el modelo actual) por UniqueId."""

        def cuerpo(data):
            a = data.get("a")
            if not a:
                raise EscrituraRechazada("a is required (older snapshot name)", 400)
            b = data.get("b")
            max_items = _entero(data.get("max_items"), MAX_ITEMS_DEFECTO, minimo=1, maximo=MAX_ITEMS_TOPE)
            datos_a, ruta_a = cargar_snapshot(doc, a)
            if _texto_seguro(b).strip().lower() in ALIAS_ACTUAL:
                datos_b = instantanea(
                    doc, "actual", datos_a.get("categories_requested"), datos_a.get("parameters"),
                    datos_a.get("include_parameters", True), datos_a.get("max_elements") or MAX_ELEMENTOS_DEFECTO,
                    datos_a.get("include_bbox", True),
                )
                ruta_b = None
            else:
                datos_b, ruta_b = cargar_snapshot(doc, b)
            resultado = comparar(datos_a, datos_b, max_items)
            resultado["status"] = "success"
            resultado["a"] = _meta(datos_a, ruta_a)
            resultado["b"] = _meta(datos_b, ruta_b)
            if (datos_a.get("categories") != datos_b.get("categories")
                    or datos_a.get("parameters") != datos_b.get("parameters")):
                resultado["warning"] = (
                    u"Las instantaneas no cubren las mismas categorias o parametros; parte de added/"
                    u"removed/modified puede deberse a eso"
                )
            return resultado

        return _responder(doc, request, cuerpo)

    logger.info("Instantaneas routes registered successfully")
