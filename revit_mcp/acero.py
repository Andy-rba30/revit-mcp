# -*- coding: UTF-8 -*-
"""
Estructuras metalicas para Revit MCP (0.5.0, entrega 2b, bloque B).

Lectura (sin transaccion):
  POST /steel_profiles/     standard (AISC, EN, todos), shape (W, HSS, L, C, WT, Pipe), loaded_only (true)
  POST /steel_quantities/   group_by (type, level, family, mark), element_ids[] (vacio = todo el acero)
  bloque_estructural()      bloque `structural` de POST /describe/ con include_structural=true
  tipos_conexion()          POST /element_types/ con category="connections"

Escritura (todas con `simular`; patron de escritura.ejecutar: copia, mcp_log.jsonl,
transaccion "IA: ...", creados / antes-despues):
  POST /load_steel_profile/         file_path | family_name, type_names[], overwrite
  POST /create_steel_frame/         MACRO: pilares en las intersecciones de rejillas y vigas entre
                                    pilares consecutivos, una transaccion "IA: Portico metalico"
  POST /create_bracing/             LOTE: bays[] {start_point_mm, end_point_mm, level_bottom, level_top, pattern}
  POST /create_truss/               LOTE: trusses[] {truss_type, start_point_mm, end_point_mm, level}
  POST /set_structural_properties/  LOTE sobre lotes.resolver_parametros: liberaciones, justificaciones,
                                    desfases, rotacion, extensiones, analyze_as, uso estructural
  POST /create_steel_connection/    LOTE: connections[] {element_ids, connection_type}; 409 no_soportado
  POST /add_plate/                  familia alojada en cara (o de punto) sobre una viga o pilar
  POST /split_beam/                 CopyElement por tramo y ajuste de LocationCurve

Idioma de Revit: todo lo estructural se lee y escribe por BuiltInParameter, las
categorias por BuiltInCategory y el material por StructuralMaterialType /
StructuralAssetClass; los nombres devueltos conservan las tildes. La norma
(AISC / EN) y la forma (W, HSS...) de un perfil se deducen de la designacion
del tipo (W12X26, IPE300...), que es la misma en todos los idiomas: la API no
expone la norma.

Compatibilidad: IronPython 2.7; los miembros que cambian entre Revit 2024 y
2027 (releases en el AnalyticalMember desde 2023, StructuralConnectionHandler
solo con el modulo de conexiones) van con try/except y ruta alternativa.
"""

from utils import (
    get_element_name, get_element_id_value, make_element_id, xyz_desde_mm, punto_a_mm,
    mapa_niveles, buscar_tipo_por_nombre, etiqueta_tipo, elevacion_interna, buscar_por_nombre,
    nombre_familia, leer_texto_utf8, find_family_symbol_safely, MM_TO_FEET, FEET_TO_MM,
)
from seguridad import requiere_token
from escritura import (
    ejecutar, transaccion, simulacion, EscrituraRechazada, resultado_creacion, comprobar_alcance,
    describir_elemento, nombre_nivel, bbox_mm,
)
from parameters import valor_parametro, convertir_valor, coincide_valor, despues_simulado
from navegacion import _responder, _entero, _elemento
from lotes import resolver_parametros
import estructural
import structure
from pyrevit import routes, revit, DB
from System.Collections.Generic import List
import clr
import io
import math
import os
import re
import logging

logger = logging.getLogger(__name__)

try:
    _texto = unicode  # IronPython 2.7
    _cadena = basestring
except NameError:  # pragma: no cover - CPython 3 en las pruebas
    _texto = str
    _cadena = str

CUFT_TO_CUM = 0.028316846592
KG_POR_FT3_A_KG_POR_M3 = 35.3146667   # densidad interna de Revit (kg/ft3) -> kg/m3
KG_POR_FT_A_KG_POR_M = 1.0 / 0.3048    # masa lineal interna (kg/ft) -> kg/m
MAX_ELEMENTOS_CANTIDADES = 2000
MAX_ELEMENTOS_ANALITICO = 500
MAX_FAMILIAS_BIBLIOTECA = 100
MAX_ARCHIVOS_BIBLIOTECA = 5000
MAX_TIPOS_CATALOGO = 60
MAX_IDS_POR_GRUPO = 50

CATEGORIAS_ACERO = ("OST_StructuralFraming", "OST_StructuralColumns")
NORMAS = ("AISC", "EN", "todos")
FORMAS = ("W", "HSS", "L", "C", "WT", "Pipe")
GROUP_BY = ("type", "level", "family", "mark")
LIBERACIONES = ("FX", "FY", "FZ", "MX", "MY", "MZ")
TIPOS_LIBERACION = {0: "fixed", 1: "pinned", 2: "bending_moment", 3: "user_defined"}
_TIPO_LIBERACION_POR_NOMBRE = {
    "fixed": 0, "empotrado": 0, "empotrada": 0, "pinned": 1, "articulado": 1, "articulada": 1,
    "bending_moment": 2, "bending": 2, "momento": 2, "user_defined": 3, "user": 3, "custom": 3,
}
# StructuralSectionShape -> forma AISC del filtro `shape`
_FORMAS_POR_SECCION = {
    "IWideFlange": "W", "IParallelFlange": "W", "ISlopedFlange": "W", "IWelded": "W", "ISplitParallelFlange": "WT",
    "RectangleHSS": "HSS", "RoundHSS": "HSS", "RectangularHSS": "HSS",
    "LAngle": "L", "LProfile": "L",
    "CProfile": "C", "CParallelFlange": "C", "CSlopedFlange": "C",
    "StructuralTees": "WT", "TSection": "WT", "TParallelFlange": "WT",
    "PipeStandard": "Pipe",
}
# Designacion del tipo -> forma (la designacion es la misma en todos los idiomas de Revit)
_FORMAS_POR_DESIGNACION = (
    (re.compile(r"^\s*(HSS|RHS|SHS|CHS)\b", re.I), "HSS"),
    (re.compile(r"^\s*(WT|MT|ST|T)\s*\d", re.I), "WT"),
    (re.compile(r"^\s*(W|HP|M|S|IPE|IPN|HE[ABM]|HD|HL|UB|UC)\s*\d", re.I), "W"),
    (re.compile(r"^\s*(L)\s*\d", re.I), "L"),
    (re.compile(r"^\s*(C|MC|UPN|UPE|PFC)\s*\d", re.I), "C"),
    (re.compile(r"^\s*(Pipe|Tubo)\b", re.I), "Pipe"),
)
_PATRON_AISC = re.compile(r"^\s*(W|HP|M|S|HSS|WT|MT|ST|C|MC)\s*\d+(\.\d+)?X\d", re.I)
_PATRON_PIPE_AISC = re.compile(r"^\s*Pipe\s*\d", re.I)
_PATRON_EN = re.compile(r"^\s*(IPE|IPN|HEA|HEB|HEM|HD|HL|UPN|UPE|UB|UC|CHS|RHS|SHS|PFC)\s*\d", re.I)
_PATRON_ANGULO = re.compile(r"^\s*L\s*\d", re.I)
_DIMENSIONES_SECCION = (
    ("height", "STRUCTURAL_SECTION_COMMON_HEIGHT"),
    ("width", "STRUCTURAL_SECTION_COMMON_WIDTH"),
    ("web_thickness", "STRUCTURAL_SECTION_COMMON_WEB_THICKNESS"),
    ("flange_thickness", "STRUCTURAL_SECTION_COMMON_FLANGE_THICKNESS"),
)
# Propiedad de set_structural_properties / describe -> BuiltInParameter y tipo de valor
PROPIEDADES_ESTRUCTURALES = (
    ("structural_usage", "INSTANCE_STRUCT_USAGE_PARAM", "enum"),
    ("y_justification", "Y_JUSTIFICATION", "enum"),
    ("z_justification", "Z_JUSTIFICATION", "enum"),
    ("y_offset_mm", "Y_OFFSET_VALUE", "mm"),
    ("z_offset_mm", "Z_OFFSET_VALUE", "mm"),
    ("section_rotation_deg", "STRUCTURAL_BEND_DIR_ANGLE", "deg"),
    ("start_extension_mm", "START_EXTENSION", "mm"),
    ("end_extension_mm", "END_EXTENSION", "mm"),
    ("analyze_as", "STRUCTURAL_ANALYZES_AS", "enum"),
)
_ENUMS_PROPIEDAD = {
    "structural_usage": "StructuralInstanceUsage",
    "y_justification": "YJustification",
    "z_justification": "ZJustification",
    "analyze_as": "AnalyzeAs",
}
_ALIAS_ENUM = {
    "y_justification": {"origen": "Origin", "izquierda": "Left", "centro": "Center", "derecha": "Right"},
    "z_justification": {"origen": "Origin", "arriba": "Top", "superior": "Top", "centro": "Center",
                        "abajo": "Bottom", "inferior": "Bottom"},
    "structural_usage": {"pilar": "Column", "viga": "Girder", "vigueta": "Joist", "correa": "Purlin",
                         "arriostre": "HorizontalBracing", "automatico": "Automatic", "otro": "Other"},
    "analyze_as": {"gravedad": "Gravity", "lateral": "Lateral", "no": "NotForAnalysis"},
}


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------
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


def _nombre_enum(valor):
    """"Steel" a partir de DB.Structure.StructuralMaterialType.Steel (o None)."""
    if valor is None:
        return None
    nombre = _texto_seguro(valor).split(".")[-1].strip()
    return nombre or None


def _bip(nombre):
    """El BuiltInParameter `nombre` o None si no existe en esta version de Revit."""
    return getattr(DB.BuiltInParameter, nombre, None)


def _parametro_bip(elem, nombre):
    """(param, existe_en_version): el parametro integrado del elemento, o None."""
    bip = _bip(nombre)
    if bip is None or elem is None:
        return None, bip is not None
    try:
        return elem.get_Parameter(bip), True
    except Exception:
        return None, True


def _tipo_de(doc, elem):
    try:
        tipo_id = elem.GetTypeId()
        if tipo_id and tipo_id != DB.ElementId.InvalidElementId:
            return doc.GetElement(tipo_id)
    except Exception:
        pass
    return None


def _es_invalido(eid):
    try:
        return eid is None or eid == DB.ElementId.InvalidElementId
    except Exception:
        return True


def _mm(valor_pies):
    return round(float(valor_pies) * FEET_TO_MM, 2)


def _desde_interno(valor, nombre_unidad, factor_reserva):
    """UnitUtils.ConvertFromInternalUnits(valor, UnitTypeId.<nombre>) o el factor de reserva."""
    try:
        return float(DB.UnitUtils.ConvertFromInternalUnits(valor, getattr(DB.UnitTypeId, nombre_unidad)))
    except Exception:
        return float(valor) * factor_reserva


def _lista_ids(ids):
    lista = List[DB.ElementId]()
    for identificador in ids:
        lista.Add(identificador)
    return lista


def _es_verdadero(valor):
    if isinstance(valor, _cadena):
        return valor.strip().lower() in ("1", "true", "si", "sí", "yes")
    return bool(valor)


# ---------------------------------------------------------------------------
# Acero: material estructural, forma, norma y dimensiones
# ---------------------------------------------------------------------------
def tipo_material_estructural(symbol):
    """"Steel", "Concrete", "Wood"... de Family.StructuralMaterialType, o None si no esta definido."""
    try:
        familia = symbol.Family
    except Exception:
        return None
    if familia is None:
        return None
    try:
        valor = familia.StructuralMaterialType
    except Exception:
        return None
    nombre = _nombre_enum(valor)
    if nombre in (None, "Undefined"):
        return None
    return nombre


def material_de(doc, elem):
    """Material estructural (STRUCTURAL_MATERIAL_PARAM) del elemento o, si no, de su tipo. None si no hay."""
    objetivos = [elem]
    tipo = _tipo_de(doc, elem)
    if tipo is not None:
        objetivos.append(tipo)
    for objetivo in objetivos:
        param, _ = _parametro_bip(objetivo, "STRUCTURAL_MATERIAL_PARAM")
        if param is None:
            continue
        try:
            if not param.HasValue:
                continue
            eid = param.AsElementId()
        except Exception:
            continue
        if _es_invalido(eid):
            continue
        material = doc.GetElement(eid)
        if material is not None:
            return material
    return None


def activo_estructural(doc, material):
    """(StructuralAsset, motivo) del material: Material.StructuralAssetId -> PropertySetElement.GetStructuralAsset()."""
    if material is None:
        return None, u"sin material estructural"
    try:
        activo_id = material.StructuralAssetId
    except Exception:
        return None, u"Material.StructuralAssetId no disponible"
    if _es_invalido(activo_id):
        return None, u"el material '{}' no tiene activo estructural".format(get_element_name(material))
    conjunto = doc.GetElement(activo_id)
    if conjunto is None:
        return None, u"el activo estructural del material '{}' no existe".format(get_element_name(material))
    try:
        return conjunto.GetStructuralAsset(), None
    except Exception as error:
        return None, u"GetStructuralAsset fallo: {}".format(error)


def clase_activo(activo):
    try:
        return _nombre_enum(activo.StructuralAssetClass)
    except Exception:
        return None


def densidad_kg_m3(activo):
    """Densidad del activo en kg/m3 (UnitUtils; reserva: interno kg/ft3 x 35.31), o None."""
    try:
        bruto = float(activo.Density)
    except Exception:
        return None
    if bruto <= 0:
        return None
    return _desde_interno(bruto, "KilogramsPerCubicMeter", KG_POR_FT3_A_KG_POR_M3)


def es_acero(doc, symbol, cache=None):
    """(True/False/None, metodo): acero segun Family.StructuralMaterialType o la clase Metal del activo del material."""
    clave = None
    if cache is not None:
        try:
            clave = get_element_id_value(symbol)
        except Exception:
            clave = None
        if clave in cache:
            return cache[clave]
    material_tipo = tipo_material_estructural(symbol)
    if material_tipo is not None:
        resultado = (material_tipo == "Steel", u"Family.StructuralMaterialType = {}".format(material_tipo))
    else:
        material = material_de(doc, symbol)
        activo, motivo = activo_estructural(doc, material)
        if activo is None:
            resultado = (None, motivo)
        else:
            clase = clase_activo(activo)
            resultado = (clase == "Metal", u"StructuralAssetClass = {}".format(clase))
    if cache is not None and clave is not None:
        cache[clave] = resultado
    return resultado


def forma_por_designacion(nombre):
    """Forma (W, HSS, L, C, WT, Pipe) por la designacion del tipo, o None."""
    if not nombre:
        return None
    for patron, forma in _FORMAS_POR_DESIGNACION:
        if patron.match(nombre):
            return forma
    return None


def forma_perfil(symbol):
    """(forma, origen): StructuralSectionShape del tipo o, si no la define, la designacion del nombre."""
    try:
        seccion = symbol.GetStructuralSection()
    except Exception:
        seccion = None
    if seccion is not None:
        try:
            nombre = _nombre_enum(seccion.StructuralSectionShape)
        except Exception:
            nombre = None
        forma = _FORMAS_POR_SECCION.get(nombre)
        if forma:
            return forma, u"StructuralSectionShape {}".format(nombre)
    forma = forma_por_designacion(get_element_name(symbol))
    return forma, (u"designacion del tipo" if forma else None)


def norma_perfil(nombre):
    """"AISC", "EN" o None deducido de la designacion (W12X26, HSS6X6X1/4 / IPE300, HEB200, L100x100x10)."""
    if not nombre:
        return None
    if _PATRON_EN.match(nombre):
        return "EN"
    if _PATRON_AISC.match(nombre) or _PATRON_PIPE_AISC.match(nombre):
        return "AISC"
    if _PATRON_ANGULO.match(nombre):
        return "AISC" if ("/" in nombre or "X" in nombre) else "EN"
    return None


def dimensiones_seccion(symbol, no_disponibles):
    """{height, width, web_thickness, flange_thickness} en mm con los BuiltInParameter que existan."""
    datos = {}
    for clave, nombre in _DIMENSIONES_SECCION:
        param, existe = _parametro_bip(symbol, nombre)
        if not existe:
            if nombre not in no_disponibles:
                no_disponibles.append(nombre)
            continue
        try:
            if param is not None and param.HasValue:
                datos[clave] = _mm(param.AsDouble())
        except Exception:
            continue
    return datos


def _simbolos_estructurales(doc):
    """[(nombre BuiltInCategory, FamilySymbol)] de armazon y pilares estructurales."""
    lista = []
    for nombre in CATEGORIAS_ACERO:
        bic = getattr(DB.BuiltInCategory, nombre, None)
        if bic is None:
            continue
        try:
            for symbol in DB.FilteredElementCollector(doc).OfCategory(bic).OfClass(DB.FamilySymbol).ToElements():
                lista.append((nombre, symbol))
        except Exception as error:
            logger.debug("No se pudieron leer los tipos de %s: %s", nombre, error)
    return lista


def _ejemplares_por_tipo(doc):
    recuento = {}
    for nombre in CATEGORIAS_ACERO:
        bic = getattr(DB.BuiltInCategory, nombre, None)
        if bic is None:
            continue
        try:
            for inst in DB.FilteredElementCollector(doc).OfCategory(bic).WhereElementIsNotElementType():
                try:
                    clave = get_element_id_value(inst.GetTypeId())
                    recuento[clave] = recuento.get(clave, 0) + 1
                except Exception:
                    continue
        except Exception:
            continue
    return recuento


def _normalizar_norma(valor):
    texto = _texto_seguro(valor).strip()
    if not texto or texto.lower() in ("todos", "todas", "all", "*"):
        return "todos"
    for norma in NORMAS:
        if texto.upper() == norma.upper():
            return norma
    raise EscrituraRechazada("standard must be one of {}".format(", ".join(NORMAS)), 400,
                             {"available_standards": list(NORMAS)})


def _normalizar_forma(valor):
    texto = _texto_seguro(valor).strip()
    if not texto:
        return None
    for forma in FORMAS:
        if texto.lower() == forma.lower():
            return forma
    raise EscrituraRechazada("shape must be one of {}".format(", ".join(FORMAS)), 400,
                             {"available_shapes": list(FORMAS)})


def perfiles_cargados(doc, norma="todos", forma=None):
    """Perfiles de acero cargados (FamilySymbol de acero) con familia, tipo, forma, norma y dimensiones."""
    ejemplares = _ejemplares_por_tipo(doc)
    no_disponibles = []
    cache = {}
    perfiles = []
    descartados = 0
    sin_material = 0
    for categoria, symbol in _simbolos_estructurales(doc):
        acero, metodo = es_acero(doc, symbol, cache)
        if acero is None:
            sin_material += 1
            continue
        if not acero:
            descartados += 1
            continue
        nombre_tipo = get_element_name(symbol)
        forma_tipo, origen_forma = forma_perfil(symbol)
        norma_tipo = norma_perfil(nombre_tipo)
        if forma and forma_tipo != forma:
            continue
        if norma != "todos" and norma_tipo != norma:
            continue
        identificador = get_element_id_value(symbol)
        material = material_de(doc, symbol)
        datos = {
            "type_id": identificador,
            "family": nombre_familia(symbol),
            "type": nombre_tipo,
            "category": categoria,
            "categoria": get_element_name(symbol.Category) if symbol.Category else None,
            "is_active": None,
            "instances": ejemplares.get(identificador, 0),
            "material": get_element_name(material) if material is not None else None,
            "material_type": tipo_material_estructural(symbol) or "Steel",
            "steel_by": metodo,
            "shape": forma_tipo,
            "shape_source": origen_forma,
            "standard": norma_tipo,
            "dimensions_mm": dimensiones_seccion(symbol, no_disponibles),
        }
        try:
            datos["is_active"] = bool(symbol.IsActive)
        except Exception:
            pass
        perfiles.append(datos)
    perfiles.sort(key=lambda p: ((p["family"] or u""), p["type"]))
    return perfiles, {"not_steel": descartados, "unknown_material": sin_material, "no_disponibles": no_disponibles}


# ---------------------------------------------------------------------------
# Biblioteca de familias (.rfa con catalogo .txt al lado)
# ---------------------------------------------------------------------------
def rutas_biblioteca(doc):
    """Rutas de Application.GetLibraryPaths() (IDictionary<string, string>) como lista de textos."""
    try:
        diccionario = doc.Application.GetLibraryPaths()
    except Exception as error:
        logger.debug("GetLibraryPaths fallo: %s", error)
        return []
    valores = None
    try:
        valores = list(diccionario.Values)
    except Exception:
        pass
    if valores is None:
        try:
            valores = list(diccionario.values())
        except Exception:
            valores = None
    if valores is None:
        valores = []
        try:
            for par in diccionario:
                valores.append(getattr(par, "Value", par))
        except Exception:
            pass
    rutas = []
    for valor in valores:
        texto = _texto_seguro(valor).strip()
        if texto and texto not in rutas:
            rutas.append(texto)
    return rutas


def leer_catalogo(ruta_txt):
    """(tipos, columnas) de un catalogo de tipos .txt: la primera columna de cada fila es el tipo."""
    texto = leer_texto_utf8(ruta_txt, "replace")
    lineas = [l.strip() for l in texto.splitlines() if l.strip()]
    if not lineas:
        return [], []
    columnas = [c.strip().strip('"') for c in lineas[0].split(",")]
    tipos = []
    for linea in lineas[1:]:
        nombre = linea.split(",")[0].strip().strip('"')
        if nombre and nombre not in tipos:
            tipos.append(nombre)
    return tipos, columnas


def _ruta_catalogo(ruta_rfa):
    base = os.path.splitext(ruta_rfa)[0]
    for extension in (".txt", ".TXT", ".Txt"):
        candidata = base + extension
        if os.path.isfile(candidata):
            return candidata
    return None


def buscar_rfa_en_biblioteca(doc, family_name):
    """Ruta del primer <family_name>.rfa dentro de las rutas de biblioteca, o None."""
    objetivo = _texto_seguro(family_name).strip().lower()
    if objetivo.endswith(".rfa"):
        objetivo = objetivo[:-4]
    for raiz in rutas_biblioteca(doc):
        if not os.path.isdir(raiz):
            continue
        for carpeta, _, archivos in os.walk(raiz):
            for archivo in archivos:
                if archivo.lower().endswith(".rfa") and os.path.splitext(archivo)[0].lower() == objetivo:
                    return os.path.join(carpeta, archivo)
    return None


def perfiles_biblioteca(doc, norma="todos", forma=None, familias_cargadas=()):
    """Familias .rfa de la biblioteca con catalogo .txt cuyos tipos parecen perfiles de acero."""
    familias = []
    escaneados = 0
    truncado = False
    rutas = rutas_biblioteca(doc)
    for raiz in rutas:
        if not os.path.isdir(raiz):
            continue
        for carpeta, _, archivos in os.walk(raiz):
            for archivo in sorted(archivos):
                if not archivo.lower().endswith(".rfa"):
                    continue
                escaneados += 1
                if escaneados > MAX_ARCHIVOS_BIBLIOTECA or len(familias) >= MAX_FAMILIAS_BIBLIOTECA:
                    truncado = True
                    break
                ruta_rfa = os.path.join(carpeta, archivo)
                ruta_txt = _ruta_catalogo(ruta_rfa)
                if ruta_txt is None:
                    continue
                try:
                    tipos, _ = leer_catalogo(ruta_txt)
                except Exception as error:
                    logger.debug("Catalogo ilegible %s: %s", ruta_txt, error)
                    continue
                coincidentes = []
                formas = []
                normas = []
                for tipo in tipos:
                    forma_tipo = forma_por_designacion(tipo)
                    if forma_tipo is None:
                        continue
                    norma_tipo = norma_perfil(tipo)
                    if forma and forma_tipo != forma:
                        continue
                    if norma != "todos" and norma_tipo != norma:
                        continue
                    coincidentes.append(tipo)
                    if forma_tipo not in formas:
                        formas.append(forma_tipo)
                    if norma_tipo and norma_tipo not in normas:
                        normas.append(norma_tipo)
                if not coincidentes:
                    continue
                nombre = os.path.splitext(archivo)[0]
                familias.append({
                    "family": nombre,
                    "path": ruta_rfa,
                    "catalog_path": ruta_txt,
                    "types": coincidentes[:MAX_TIPOS_CATALOGO],
                    "types_total": len(coincidentes),
                    "catalog_types_total": len(tipos),
                    "shapes": formas,
                    "standards": normas,
                    "is_loaded": nombre in familias_cargadas,
                })
            if truncado:
                break
        if truncado:
            break
    return familias, {"library_paths": rutas, "scanned_files": escaneados, "truncated": truncado}


# ---------------------------------------------------------------------------
# Elementos de acero y cantidades
# ---------------------------------------------------------------------------
def elementos_acero(doc, element_ids=None, maximo=MAX_ELEMENTOS_CANTIDADES):
    """(elementos, info): los ids pedidos (existan o no: `not_found`) o todo el acero (FamilyInstance
    de armazon y pilares estructurales cuyo tipo es de acero), hasta `maximo`."""
    cache = {}
    info = {"not_found": [], "not_steel": [], "truncated": False, "scanned": 0}
    elementos = []
    if element_ids:
        for identificador in element_ids:
            try:
                elem = doc.GetElement(make_element_id(identificador))
            except Exception:
                elem = None
            if elem is None:
                info["not_found"].append(identificador)
                continue
            tipo = _tipo_de(doc, elem)
            if tipo is not None:
                acero, _ = es_acero(doc, tipo, cache)
                if acero is False:
                    info["not_steel"].append(get_element_id_value(elem))
            elementos.append(elem)
        return elementos, info
    for nombre in CATEGORIAS_ACERO:
        bic = getattr(DB.BuiltInCategory, nombre, None)
        if bic is None:
            continue
        try:
            coleccion = DB.FilteredElementCollector(doc).OfCategory(bic).OfClass(DB.FamilyInstance).WhereElementIsNotElementType()
        except Exception:
            continue
        for elem in coleccion:
            info["scanned"] += 1
            tipo = _tipo_de(doc, elem)
            if tipo is None:
                continue
            acero, _ = es_acero(doc, tipo, cache)
            if not acero:
                continue
            if len(elementos) >= maximo:
                info["truncated"] = True
                break
            elementos.append(elem)
        if info["truncated"]:
            break
    return elementos, info


def longitud_mm(elem):
    """(mm, fuente): INSTANCE_LENGTH_PARAM, la curva de ubicacion o la altura de la caja envolvente."""
    param, _ = _parametro_bip(elem, "INSTANCE_LENGTH_PARAM")
    try:
        if param is not None and param.HasValue and param.AsDouble() > 0:
            return _mm(param.AsDouble()), "INSTANCE_LENGTH_PARAM"
    except Exception:
        pass
    try:
        curva = elem.Location.Curve
        return _mm(curva.Length), "LocationCurve"
    except Exception:
        pass
    caja = bbox_mm(elem)
    if caja and caja.get("min") and caja.get("max"):
        return round(caja["max"]["z"] - caja["min"]["z"], 2), "bbox"
    return None, None


def volumen_m3(elem):
    param, _ = _parametro_bip(elem, "HOST_VOLUME_COMPUTED")
    try:
        if param is not None and param.HasValue and param.AsDouble() > 0:
            return round(param.AsDouble() * CUFT_TO_CUM, 6)
    except Exception:
        pass
    return None


def masa_lineal_kg_m(tipo):
    """STRUCTURAL_SECTION_NOMINAL_WEIGHT del tipo en kg/m, o None."""
    param, _ = _parametro_bip(tipo, "STRUCTURAL_SECTION_NOMINAL_WEIGHT")
    try:
        if param is not None and param.HasValue and param.AsDouble() > 0:
            return _desde_interno(param.AsDouble(), "KilogramsPerMeter", KG_POR_FT_A_KG_POR_M)
    except Exception:
        pass
    return None


def _marca(elem):
    param, _ = _parametro_bip(elem, "ALL_MODEL_MARK")
    try:
        if param is not None and param.HasValue:
            return param.AsString() or None
    except Exception:
        pass
    return None


def cantidades_elemento(doc, elem, cache_densidad):
    """Longitud (mm), volumen (m3) y peso (kg) de un elemento con el metodo usado o el motivo del fallo."""
    tipo = _tipo_de(doc, elem)
    longitud, fuente_longitud = longitud_mm(elem)
    volumen = volumen_m3(elem)
    material = material_de(doc, elem)
    densidad = None
    motivo_densidad = None
    clave_material = get_element_id_value(material) if material is not None else None
    if clave_material in cache_densidad:
        densidad, motivo_densidad = cache_densidad[clave_material]
    else:
        activo, motivo_densidad = activo_estructural(doc, material)
        if activo is not None:
            densidad = densidad_kg_m3(activo)
            if densidad is None:
                motivo_densidad = u"el activo estructural del material no tiene densidad (> 0)"
        cache_densidad[clave_material] = (densidad, motivo_densidad)
    datos = {
        "id": get_element_id_value(elem),
        "type": etiqueta_tipo(tipo) if tipo is not None else None,
        "family": nombre_familia(tipo) if tipo is not None else None,
        "level": nombre_nivel(doc, elem),
        "mark": _marca(elem),
        "material": get_element_name(material) if material is not None else None,
        "length_mm": longitud,
        "length_source": fuente_longitud,
        "volume_m3": volumen,
        "density_kg_m3": round(densidad, 2) if densidad else None,
        "weight_kg": None,
        "method": None,
        "motivo": None,
    }
    if volumen is not None and densidad:
        datos["weight_kg"] = round(volumen * densidad, 3)
        datos["method"] = "volumen x densidad"
        return datos
    masa_lineal = masa_lineal_kg_m(tipo) if tipo is not None else None
    if masa_lineal and longitud:
        datos["weight_kg"] = round(masa_lineal * longitud / 1000.0, 3)
        datos["method"] = "masa lineal (STRUCTURAL_SECTION_NOMINAL_WEIGHT) x longitud"
        datos["linear_mass_kg_m"] = round(masa_lineal, 3)
        return datos
    motivos = []
    if volumen is None:
        motivos.append(u"sin volumen (HOST_VOLUME_COMPUTED)")
    if not densidad:
        motivos.append(motivo_densidad or u"sin densidad")
    if masa_lineal is None:
        motivos.append(u"el tipo no tiene masa lineal (STRUCTURAL_SECTION_NOMINAL_WEIGHT)")
    elif not longitud:
        motivos.append(u"sin longitud")
    datos["motivo"] = u"; ".join(motivos)
    return datos


def _clave_grupo(datos, group_by):
    if group_by == "type":
        return datos["type"] or u"(sin tipo)"
    if group_by == "level":
        return datos["level"] or u"(sin nivel)"
    if group_by == "family":
        return datos["family"] or u"(sin familia)"
    return datos["mark"] or u"(sin marca)"


def cantidades_acero(doc, data):
    """Cuerpo de POST /steel_quantities/."""
    group_by = _texto_seguro(data.get("group_by") or "type").strip().lower()
    if group_by not in GROUP_BY:
        raise EscrituraRechazada("group_by must be one of {}".format(", ".join(GROUP_BY)), 400,
                                 {"available_group_by": list(GROUP_BY)})
    element_ids = data.get("element_ids") or []
    if not isinstance(element_ids, (list, tuple)):
        raise EscrituraRechazada("element_ids must be a list", 400)
    maximo = _entero(data.get("max"), MAX_ELEMENTOS_CANTIDADES, minimo=1)
    elementos, info = elementos_acero(doc, element_ids, maximo)
    cache_densidad = {}
    grupos = {}
    orden = []
    sin_peso = []
    totales = {"count": 0, "length_mm": 0.0, "weight_kg": 0.0, "with_weight": 0, "without_weight": 0}
    for elem in elementos:
        try:
            datos = cantidades_elemento(doc, elem, cache_densidad)
        except Exception as error:
            sin_peso.append({"element_id": get_element_id_value(elem), "motivo": u"error: {}".format(error)})
            continue
        clave = _clave_grupo(datos, group_by)
        grupo = grupos.get(clave)
        if grupo is None:
            grupo = {"group": clave, "count": 0, "length_mm": 0.0, "weight_kg": 0.0, "with_weight": 0,
                     "element_ids": [], "elements_total": 0, "methods": {}}
            grupos[clave] = grupo
            orden.append(clave)
        grupo["count"] += 1
        grupo["elements_total"] += 1
        if len(grupo["element_ids"]) < MAX_IDS_POR_GRUPO:
            grupo["element_ids"].append(datos["id"])
        totales["count"] += 1
        if datos["length_mm"]:
            grupo["length_mm"] += datos["length_mm"]
            totales["length_mm"] += datos["length_mm"]
        if datos["weight_kg"] is not None:
            grupo["weight_kg"] += datos["weight_kg"]
            grupo["with_weight"] += 1
            grupo["methods"][datos["method"]] = grupo["methods"].get(datos["method"], 0) + 1
            totales["weight_kg"] += datos["weight_kg"]
            totales["with_weight"] += 1
        else:
            totales["without_weight"] += 1
            sin_peso.append({"element_id": datos["id"], "type": datos["type"], "material": datos["material"],
                             "motivo": datos["motivo"]})
    lista = []
    for clave in orden:
        grupo = grupos[clave]
        grupo["length_mm"] = round(grupo["length_mm"], 1)
        grupo["weight_kg"] = round(grupo["weight_kg"], 2)
        grupo["ids_truncated"] = grupo["elements_total"] > len(grupo["element_ids"])
        lista.append(grupo)
    totales["length_mm"] = round(totales["length_mm"], 1)
    totales["weight_kg"] = round(totales["weight_kg"], 2)
    return {
        "group_by": group_by,
        "groups": lista,
        "group_count": len(lista),
        "totals": totales,
        "sin_peso": sin_peso,
        "metodo": (u"peso = volumen (HOST_VOLUME_COMPUTED) x densidad del activo estructural del material "
                   u"(Material.StructuralAssetId -> PropertySetElement.GetStructuralAsset().Density, kg/m3); "
                   u"si el material no tiene activo, masa lineal del tipo (STRUCTURAL_SECTION_NOMINAL_WEIGHT) "
                   u"x longitud (INSTANCE_LENGTH_PARAM o curva de ubicacion); el resto va a sin_peso"),
        "not_found": info["not_found"],
        "not_steel": info["not_steel"],
        "scanned": info["scanned"],
        "truncated": info["truncated"],
        "max": maximo,
    }


# ---------------------------------------------------------------------------
# Modelo analitico: asociacion fisico <-> analitico (lo reutiliza analitico.py)
# ---------------------------------------------------------------------------
def gestor_asociaciones(doc):
    """AnalyticalToPhysicalAssociationManager del documento (2023+), o None si la API no lo tiene."""
    try:
        return DB.Structure.AnalyticalToPhysicalAssociationManager.GetAnalyticalToPhysicalAssociationManager(doc)
    except Exception as error:
        logger.debug("AnalyticalToPhysicalAssociationManager no disponible: %s", error)
        return None


def miembro_analitico(doc, elem, gestor=None):
    """AnalyticalMember (o elemento analitico) asociado al elemento fisico, o None."""
    if gestor is None:
        gestor = gestor_asociaciones(doc)
    if gestor is None:
        return None
    try:
        analitico_id = gestor.GetAssociatedElementId(elem.Id)
    except Exception:
        return None
    if _es_invalido(analitico_id):
        return None
    return doc.GetElement(analitico_id)


def _selector(inicio):
    selectores = DB.Structure.AnalyticalElementSelector
    return selectores.StartOrBase if inicio else selectores.EndOrTop


def liberaciones_analiticas(miembro, inicio):
    """{type, FX..MZ} del extremo del AnalyticalMember (GetReleaseType / GetReleaseConditions), o None."""
    if miembro is None:
        return None
    try:
        selector = _selector(inicio)
        tipo = _nombre_enum(miembro.GetReleaseType(selector))
    except Exception:
        return None
    datos = {"type": (tipo or u"?").lower(), "source": "AnalyticalMember"}
    try:
        condiciones = miembro.GetReleaseConditions(selector)
        for clave in LIBERACIONES:
            datos[clave] = bool(getattr(condiciones, clave.capitalize()))
    except Exception:
        pass
    return datos


# ---------------------------------------------------------------------------
# Bloque `structural` de /describe/ (include_structural)
# ---------------------------------------------------------------------------
def _valor_enum_parametro(param, doc):
    """{value (texto visible), index (int)} de un parametro entero de enumeracion."""
    datos = {"value": valor_parametro(param, doc)}
    try:
        datos["index"] = param.AsInteger()
    except Exception:
        pass
    return datos


def _liberaciones_parametros(elem, prefijo, no_disponibles, no_aplica):
    """Liberaciones de un extremo por BuiltInParameter (STRUCTURAL_<prefijo>_RELEASE_*); None si no hay ninguno."""
    nombre_tipo = "STRUCTURAL_{}_RELEASE_TYPE".format(prefijo)
    param, existe = _parametro_bip(elem, nombre_tipo)
    if not existe:
        no_disponibles.append(nombre_tipo)
        return None
    if param is None:
        no_aplica.append(nombre_tipo)
        return None
    datos = {"source": "BuiltInParameter"}
    try:
        indice = param.AsInteger()
        datos["type"] = TIPOS_LIBERACION.get(indice, _texto_seguro(indice))
        datos["type_index"] = indice
    except Exception:
        datos["type"] = None
    for clave in LIBERACIONES:
        nombre = "STRUCTURAL_{}_RELEASE_{}".format(prefijo, clave)
        p, existe = _parametro_bip(elem, nombre)
        if not existe:
            no_disponibles.append(nombre)
            continue
        if p is None:
            no_aplica.append(nombre)
            continue
        try:
            datos[clave] = bool(p.AsInteger())
        except Exception:
            datos[clave] = None
    return datos


def bloque_estructural(doc, elem):
    """Bloque `structural` de POST /describe/: uso, material, liberaciones, justificaciones, desfases..."""
    no_disponibles = []
    no_aplica = []
    datos = {"no_disponibles": no_disponibles, "no_aplica": no_aplica}
    for clave, nombre, clase in PROPIEDADES_ESTRUCTURALES:
        param, existe = _parametro_bip(elem, nombre)
        if not existe:
            no_disponibles.append(nombre)
            continue
        if param is None:
            no_aplica.append(nombre)
            continue
        try:
            if not param.HasValue:
                datos[clave] = None
            elif clase == "mm":
                datos[clave] = _mm(param.AsDouble())
            elif clase == "deg":
                datos[clave] = round(math.degrees(param.AsDouble()), 4)
            else:
                datos[clave] = _valor_enum_parametro(param, doc)
        except Exception as error:
            datos[clave] = None
            logger.debug("No se pudo leer %s: %s", nombre, error)
    try:
        uso = _nombre_enum(elem.StructuralUsage)
        if uso:
            datos.setdefault("structural_usage", {})
            if isinstance(datos["structural_usage"], dict):
                datos["structural_usage"]["enum"] = uso
    except Exception:
        pass
    material = material_de(doc, elem)
    datos["structural_material"] = get_element_name(material) if material is not None else None
    datos["structural_material_id"] = get_element_id_value(material) if material is not None else None
    tipo = _tipo_de(doc, elem)
    datos["structural_material_type"] = tipo_material_estructural(tipo) if tipo is not None else None
    try:
        propio = _nombre_enum(elem.StructuralMaterialType)
        if propio and propio != "Undefined":
            datos["structural_material_type"] = propio
    except Exception:
        pass
    if tipo is not None:
        acero, metodo = es_acero(doc, tipo)
        datos["is_steel"] = acero
        datos["steel_by"] = metodo
    inicio = _liberaciones_parametros(elem, "START", no_disponibles, no_aplica)
    fin = _liberaciones_parametros(elem, "END", no_disponibles, no_aplica)
    miembro = None
    if inicio is None or fin is None:
        # 2023+: las liberaciones viven en el AnalyticalMember asociado
        miembro = miembro_analitico(doc, elem)
        if inicio is None:
            inicio = liberaciones_analiticas(miembro, True)
        if fin is None:
            fin = liberaciones_analiticas(miembro, False)
    datos["releases"] = {"start": inicio, "end": fin}
    if miembro is not None:
        datos["analytical_member_id"] = get_element_id_value(miembro)
    return datos


# ---------------------------------------------------------------------------
# Conexiones de acero: tipos disponibles (list_types category="connections")
# ---------------------------------------------------------------------------
def clases_conexiones():
    """(StructuralConnectionHandler, StructuralConnectionHandlerType, StructuralConnectionApprovalType) o Nones."""
    estructura = getattr(DB, "Structure", None)
    return (getattr(estructura, "StructuralConnectionHandler", None),
            getattr(estructura, "StructuralConnectionHandlerType", None),
            getattr(estructura, "StructuralConnectionApprovalType", None))


NO_SOPORTADO_CONEXIONES = (
    u"Las conexiones de acero no estan disponibles: la API no expone StructuralConnectionHandler / "
    u"StructuralConnectionHandlerType (falta el modulo Steel Connections for Revit) o el proyecto no tiene "
    u"ningun tipo de conexion cargado (Estructura > Conexion > Ajustes de conexiones en Revit)."
)


def tipos_conexion_disponibles(doc):
    """(clase_handler, tipos[]): lanza EscrituraRechazada(409, no_soportado) sin el modulo o sin tipos."""
    handler, clase_tipo, _ = clases_conexiones()
    if handler is None or clase_tipo is None:
        raise EscrituraRechazada(NO_SOPORTADO_CONEXIONES, 409, {"no_soportado": True, "motivo": "api"})
    tipos = []
    try:
        tipos = list(DB.FilteredElementCollector(doc).OfClass(clase_tipo).ToElements())
    except Exception as error:
        logger.debug("No se pudieron leer los tipos de conexion: %s", error)
    if not tipos:
        try:
            por_defecto = clase_tipo.GetDefaultConnectionHandlerType(doc)
            if por_defecto is not None:
                tipos = [por_defecto]
        except Exception:
            pass
    if not tipos:
        raise EscrituraRechazada(NO_SOPORTADO_CONEXIONES, 409, {"no_soportado": True, "motivo": "sin_tipos"})
    return handler, tipos


def tipos_aprobacion(doc):
    """[{id, nombre}] de StructuralConnectionApprovalType (vacio si la API no lo expone)."""
    _, _, clase = clases_conexiones()
    if clase is None:
        return []
    lista = []
    try:
        ids = list(clase.GetAllStructuralConnectionApprovalTypes(doc))
    except Exception:
        try:
            ids = [t.Id for t in DB.FilteredElementCollector(doc).OfClass(clase).ToElements()]
        except Exception:
            ids = []
    for identificador in ids:
        elem = doc.GetElement(identificador)
        if elem is not None:
            lista.append({"id": get_element_id_value(elem), "nombre": get_element_name(elem)})
    return lista


def tipos_conexion(doc, data):
    """Cuerpo de POST /element_types/ con category="connections"."""
    handler, tipos = tipos_conexion_disponibles(doc)
    ejemplares = {}
    try:
        for conexion in DB.FilteredElementCollector(doc).OfClass(handler).WhereElementIsNotElementType():
            try:
                clave = get_element_id_value(conexion.GetTypeId())
                ejemplares[clave] = ejemplares.get(clave, 0) + 1
            except Exception:
                continue
    except Exception:
        pass
    maximo = _entero(data.get("max"), 200, minimo=1)
    lista = []
    for tipo in tipos:
        identificador = get_element_id_value(tipo)
        lista.append({"id": identificador, "familia": nombre_familia(tipo), "tipo": get_element_name(tipo),
                      "parametros": {}, "ejemplares": ejemplares.get(identificador, 0)})
    lista.sort(key=lambda t: t["tipo"])
    return {
        "status": "success",
        "category": "connections",
        "class": "StructuralConnectionHandlerType",
        "types": lista[:maximo],
        "count": min(len(lista), maximo),
        "total_matched": len(lista),
        "truncated": len(lista) > maximo,
        "approval_types": tipos_aprobacion(doc),
    }


# ---------------------------------------------------------------------------
# Rutas de lectura
# ---------------------------------------------------------------------------
def register_acero_routes(api):
    """Register the steel routes (0.5.0) with the API."""

    @api.route("/steel_profiles/", methods=["POST"])
    @requiere_token
    def list_steel_profiles(doc, request):
        """Perfiles de acero cargados (y, con loaded_only=false, los .rfa con catalogo de la biblioteca)."""

        def cuerpo(data):
            norma = _normalizar_norma(data.get("standard"))
            forma = _normalizar_forma(data.get("shape"))
            solo_cargados = _es_verdadero(data.get("loaded_only", True))
            perfiles, info = perfiles_cargados(doc, norma, forma)
            respuesta = {
                "status": "success",
                "filters": {"standard": norma, "shape": forma, "loaded_only": solo_cargados},
                "loaded": perfiles,
                "count": len(perfiles),
                "not_steel": info["not_steel"],
                "unknown_material": info["unknown_material"],
                "no_disponibles": info["no_disponibles"],
                "metodo": (u"cargados: FamilySymbol de OST_StructuralFraming y OST_StructuralColumns cuyo "
                           u"Family.StructuralMaterialType es Steel (o cuyo material tiene un activo estructural "
                           u"de clase Metal); forma por StructuralSectionShape o por la designacion del tipo; "
                           u"norma deducida de la designacion (la API no la expone)"),
            }
            if not perfiles:
                respuesta["nota"] = (u"No hay perfiles de acero cargados en el proyecto: usa load_steel_profile "
                                     u"(o list_steel_profiles(loaded_only=false) para ver la biblioteca).")
            if not solo_cargados:
                cargadas = set(p["family"] for p in perfiles if p["family"])
                familias, info_biblioteca = perfiles_biblioteca(doc, norma, forma, cargadas)
                respuesta["library"] = familias
                respuesta["library_count"] = len(familias)
                respuesta["library_paths"] = info_biblioteca["library_paths"]
                respuesta["library_scanned_files"] = info_biblioteca["scanned_files"]
                respuesta["library_truncated"] = info_biblioteca["truncated"]
                respuesta["library_metodo"] = (u".rfa de Application.GetLibraryPaths() con un .txt de catalogo al lado "
                                               u"cuyos tipos tienen designacion de perfil (W, HSS, L, C, WT, Pipe, IPE, "
                                               u"HEB...); sin suponer nombres de carpeta")
            return respuesta

        return _responder(doc, request, cuerpo)

    @api.route("/steel_quantities/", methods=["POST"])
    @requiere_token
    def steel_quantities(doc, request):
        """Recuento, longitud (mm) y peso (kg) del acero por tipo, nivel, familia o marca."""

        def cuerpo(data):
            datos = cantidades_acero(doc, data)
            datos["status"] = "success"
            return datos

        return _responder(doc, request, cuerpo)

    logger.info("Acero routes registered successfully")
