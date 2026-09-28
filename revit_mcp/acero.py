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
# DB.Structure.ReleaseType -> nombre del contrato
_TIPO_LIBERACION_POR_ENUM = {"fixed": "fixed", "pinned": "pinned", "bendingmoment": "bending_moment", "userdefined": "user_defined"}
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
    datos = {"type": _TIPO_LIBERACION_POR_ENUM.get((tipo or u"").lower(), (tipo or u"?").lower()), "source": "AnalyticalMember"}
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
# Escritura: utilidades comunes
# ---------------------------------------------------------------------------
def _numero(valor, etiqueta, status=400, extra=None):
    try:
        if isinstance(valor, bool):
            raise ValueError("bool")
        return float(valor)
    except (TypeError, ValueError):
        raise EscrituraRechazada("{} must be a number".format(etiqueta), status, extra or {})


def _punto_mm(valor, etiqueta, extra=None):
    try:
        return xyz_desde_mm(valor)
    except ValueError as error:
        raise EscrituraRechazada("{}: {}".format(etiqueta, error), 400, extra or {})


def _nivel_por_nombre(niveles, nombre, etiqueta, extra=None):
    texto = _texto_seguro(nombre).strip()
    if not texto:
        raise EscrituraRechazada("{} is required".format(etiqueta), 400, extra or {})
    nivel = niveles.get(texto)
    if nivel is None:
        datos = dict(extra or {})
        datos["available_levels"] = sorted(niveles.keys())
        raise EscrituraRechazada(u"{}: level '{}' not found".format(etiqueta, texto), 404, datos)
    return nivel


def _simbolos_categoria(doc, nombre_categoria):
    bic = getattr(DB.BuiltInCategory, nombre_categoria, None)
    if bic is None:
        return []
    try:
        return list(DB.FilteredElementCollector(doc).OfCategory(bic).OfClass(DB.FamilySymbol).ToElements())
    except Exception:
        return []


def _elegir_simbolo(doc, nombre_categoria, nombre, etiqueta):
    """FamilySymbol de la categoria por nombre o 'Familia: Tipo'; 404 con los disponibles."""
    candidatos = _simbolos_categoria(doc, nombre_categoria)
    disponibles = sorted(set(etiqueta_tipo(t) for t in candidatos))
    texto = _texto_seguro(nombre).strip()
    if not texto:
        raise EscrituraRechazada("{} is required".format(etiqueta), 400, {"available_types": disponibles[:40]})
    if not candidatos:
        raise EscrituraRechazada(
            u"{}: no {} families are loaded; load one with load_steel_profile".format(etiqueta, nombre_categoria), 404,
            {"available_types": []},
        )
    coincidencias = buscar_tipo_por_nombre(candidatos, texto)
    if not coincidencias:
        raise EscrituraRechazada(u"{}: type '{}' not found".format(etiqueta, texto), 404,
                                 {"available_types": disponibles[:40]})
    if len(coincidencias) > 1:
        raise EscrituraRechazada(u"{}: type '{}' is ambiguous, use 'Family: Type'".format(etiqueta, texto), 400,
                                 {"matches": [etiqueta_tipo(t) for t in coincidencias]})
    return coincidencias[0]


def _activar(symbol, doc):
    try:
        if not symbol.IsActive:
            symbol.Activate()
            doc.Regenerate()
    except Exception:
        pass


def _fijar_marca(elem, marca):
    """ALL_MODEL_MARK = marca; devuelve None si se fijo o el motivo si no."""
    param, existe = _parametro_bip(elem, "ALL_MODEL_MARK")
    if not existe:
        return u"ALL_MODEL_MARK no existe en esta version"
    if param is None:
        return u"el elemento no tiene el parametro Marca"
    try:
        if param.IsReadOnly:
            return u"Marca es de solo lectura"
        param.Set(_texto(marca))
        return None
    except Exception as error:
        return _texto_seguro(error)


def _nivel_de(doc, elem):
    """DB.Level del elemento (LevelId o los parametros de nivel), o None."""
    candidatos = []
    try:
        candidatos.append(elem.LevelId)
    except Exception:
        pass
    for nombre in ("FAMILY_LEVEL_PARAM", "LEVEL_PARAM", "FAMILY_BASE_LEVEL_PARAM", "SCHEDULE_LEVEL_PARAM"):
        param, _ = _parametro_bip(elem, nombre)
        try:
            if param is not None:
                candidatos.append(param.AsElementId())
        except Exception:
            continue
    for eid in candidatos:
        if _es_invalido(eid):
            continue
        nivel = doc.GetElement(eid)
        if isinstance(nivel, DB.Level):
            return nivel
    return None


def _curva_ubicacion(elem):
    try:
        return elem.Location.Curve
    except Exception:
        return None


def _agrupar_creados(doc, ids, completar=None):
    """resultado_creacion + un dict {id: creado} para regrupar; `completar(creado)` anade extras."""
    resultado = resultado_creacion(doc, ids)
    por_id = {}
    for creado in resultado["creados"]:
        if completar is not None:
            try:
                completar(creado)
            except Exception:
                pass
        por_id[creado["id"]] = creado
    return resultado, por_id


# ---------------------------------------------------------------------------
# /load_steel_profile/
# ---------------------------------------------------------------------------
def _familia_por_nombre(doc, nombre):
    try:
        for familia in DB.FilteredElementCollector(doc).OfClass(DB.Family).ToElements():
            if get_element_name(familia) == nombre:
                return familia
    except Exception:
        pass
    return None


def _simbolos_de_familia(doc, familia):
    simbolos = []
    try:
        for sid in familia.GetFamilySymbolIds():
            simbolo = doc.GetElement(sid)
            if simbolo is not None:
                simbolos.append(simbolo)
        return simbolos
    except Exception:
        pass
    try:
        for simbolo in DB.FilteredElementCollector(doc).OfClass(DB.FamilySymbol).ToElements():
            try:
                if simbolo.Family is not None and simbolo.Family.Id == familia.Id:
                    simbolos.append(simbolo)
            except Exception:
                continue
    except Exception:
        pass
    return simbolos


def _nombres_tipos(valor):
    if valor in (None, ""):
        return []
    if isinstance(valor, _cadena):
        return [valor.strip()]
    if not isinstance(valor, (list, tuple)):
        raise EscrituraRechazada("type_names must be a list of type names", 400)
    nombres = []
    for tipo in valor:
        texto = _texto_seguro(tipo).strip()
        if texto and texto not in nombres:
            nombres.append(texto)
    return nombres


def planificar_carga_perfil(doc, data):
    """Valida file_path | family_name, type_names y overwrite; devuelve el plan de carga."""
    ruta = _texto_seguro(data.get("file_path")).strip()
    nombre = _texto_seguro(data.get("family_name")).strip()
    if not ruta and not nombre:
        raise EscrituraRechazada("file_path (.rfa) or family_name (searched in the library paths) is required", 400)
    if not ruta:
        ruta = buscar_rfa_en_biblioteca(doc, nombre)
        if ruta is None:
            raise EscrituraRechazada(
                u"Family '{}' not found as <name>.rfa in the library paths".format(nombre), 404,
                {"library_paths": rutas_biblioteca(doc)},
            )
    if not os.path.isfile(ruta):
        raise EscrituraRechazada("Family file not found: {}".format(ruta), 404)
    if not ruta.lower().endswith(".rfa"):
        raise EscrituraRechazada("file_path must be a .rfa file", 400)
    nombre_familia_rfa = os.path.splitext(os.path.basename(ruta))[0]
    pedidos = _nombres_tipos(data.get("type_names"))
    sobrescribir = _es_verdadero(data.get("overwrite", False))
    catalogo = _ruta_catalogo(ruta)
    tipos_catalogo = []
    if catalogo:
        try:
            tipos_catalogo, _ = leer_catalogo(catalogo)
        except Exception as error:
            raise EscrituraRechazada(u"Type catalog {} could not be read: {}".format(catalogo, error), 400)
        if not pedidos:
            raise EscrituraRechazada(
                u"'{}' has a type catalog: give type_names (LoadFamilySymbol loads one catalog type at a time)".format(
                    nombre_familia_rfa), 400,
                {"available_types": tipos_catalogo[:MAX_TIPOS_CATALOGO], "catalog_path": catalogo},
            )
        faltan = [t for t in pedidos if t not in tipos_catalogo]
        if faltan:
            raise EscrituraRechazada(
                u"Types not in the catalog of '{}': {}".format(nombre_familia_rfa, u", ".join(faltan)), 404,
                {"available_types": tipos_catalogo[:MAX_TIPOS_CATALOGO], "catalog_path": catalogo},
            )
    familia = _familia_por_nombre(doc, nombre_familia_rfa)
    existentes = []
    if familia is not None:
        existentes = [get_element_name(s) for s in _simbolos_de_familia(doc, familia)]
    ya_existian = [t for t in pedidos if t in existentes]
    if familia is not None and not sobrescribir:
        nuevos = [t for t in pedidos if t not in existentes]
        if not pedidos or not nuevos:
            raise EscrituraRechazada(
                u"Family '{}' is already loaded{}; pass overwrite=true to reload it".format(
                    nombre_familia_rfa, u" with the requested types" if pedidos else u""), 409,
                {"already_loaded": True, "family_id": get_element_id_value(familia), "types": sorted(existentes)},
            )
        pedidos = nuevos
    return {
        "ruta": ruta, "family": nombre_familia_rfa, "catalog": catalogo, "catalog_types": tipos_catalogo,
        "type_names": pedidos, "overwrite": sobrescribir, "ya_existian": ya_existian,
        "already_loaded": familia is not None, "family_id": get_element_id_value(familia) if familia is not None else None,
    }


def cargar_perfil(doc, plan):
    """Dentro de una transaccion: LoadFamilySymbol por tipo (catalogo) o LoadFamily + activar. Devuelve (ids, tipos, avisos)."""
    ids = []
    tipos = []
    avisos = []
    if plan["catalog"]:
        for nombre_tipo in plan["type_names"]:
            referencia = clr.Reference[DB.FamilySymbol]()
            try:
                cargado = doc.LoadFamilySymbol(plan["ruta"], nombre_tipo, referencia)
            except Exception as error:
                raise EscrituraRechazada(u"LoadFamilySymbol('{}') failed: {}".format(nombre_tipo, error), 500)
            simbolo = referencia.Value
            if simbolo is None:
                familia = _familia_por_nombre(doc, plan["family"])
                for candidato in (_simbolos_de_familia(doc, familia) if familia is not None else []):
                    if get_element_name(candidato) == nombre_tipo:
                        simbolo = candidato
                        break
            if simbolo is None:
                avisos.append(u"LoadFamilySymbol devolvio {} para '{}' y el tipo no aparece en el proyecto".format(
                    cargado, nombre_tipo))
                continue
            _activar(simbolo, doc)
            ids.append(get_element_id_value(simbolo))
            tipos.append({"type_id": ids[-1], "type": get_element_name(simbolo), "loaded": bool(cargado)})
        return ids, tipos, avisos
    referencia = clr.Reference[DB.Family]()
    try:
        cargada = doc.LoadFamily(plan["ruta"], referencia)
    except Exception as error:
        raise EscrituraRechazada(u"LoadFamily failed: {}".format(error), 500)
    familia = referencia.Value
    if familia is None:
        familia = _familia_por_nombre(doc, plan["family"])
    if familia is None:
        raise EscrituraRechazada(
            u"LoadFamily returned {} but no family named '{}' is in the project".format(cargada, plan["family"]), 500,
        )
    simbolos = _simbolos_de_familia(doc, familia)
    disponibles = [get_element_name(s) for s in simbolos]
    if plan["type_names"]:
        faltan = [t for t in plan["type_names"] if t not in disponibles]
        if faltan:
            raise EscrituraRechazada(
                u"Family '{}' has no types named {}".format(plan["family"], u", ".join(faltan)), 404,
                {"available_types": sorted(disponibles)},
            )
    for simbolo in simbolos:
        nombre_tipo = get_element_name(simbolo)
        if plan["type_names"] and nombre_tipo not in plan["type_names"]:
            continue
        _activar(simbolo, doc)
        ids.append(get_element_id_value(simbolo))
        tipos.append({"type_id": ids[-1], "type": nombre_tipo, "loaded": bool(cargada)})
    return ids, tipos, avisos


# ---------------------------------------------------------------------------
# /create_steel_frame/ (macro)
# ---------------------------------------------------------------------------
DIRECCIONES_VIGAS = ("x", "y", "both")


def _linea_rejilla(rejilla):
    """(p0, p1) en pies de la linea de la rejilla, o None si es curva."""
    try:
        curva = rejilla.Curve
    except Exception:
        return None
    if curva is None:
        return None
    try:
        if isinstance(curva, DB.Arc):
            return None
        p0 = curva.GetEndPoint(0)
        p1 = curva.GetEndPoint(1)
    except Exception:
        return None
    if p0.DistanceTo(p1) < 1e-9:
        return None
    return p0, p1


def rejillas_clasificadas(doc):
    """({nombre: datos}, curvas): datos = {name, p0, p1, dir ("x" = linea de X constante, "y" = de Y constante)}."""
    rejillas = {}
    curvas = []
    try:
        coleccion = DB.FilteredElementCollector(doc).OfClass(DB.Grid).WhereElementIsNotElementType().ToElements()
    except Exception:
        coleccion = []
    for rejilla in coleccion:
        nombre = get_element_name(rejilla)
        linea = _linea_rejilla(rejilla)
        if linea is None:
            curvas.append(nombre)
            continue
        p0, p1 = linea
        dx, dy = p1.X - p0.X, p1.Y - p0.Y
        rejillas[nombre] = {"name": nombre, "p0": p0, "p1": p1, "id": get_element_id_value(rejilla),
                            "dir": "x" if abs(dx) <= abs(dy) else "y"}
    return rejillas, curvas


def _interseccion_plana(a, b):
    """Punto (x, y) en pies donde se cruzan las lineas de las rejillas a y b, o None si son paralelas o no se tocan."""
    p, q = a["p0"], b["p0"]
    dx1, dy1 = a["p1"].X - p.X, a["p1"].Y - p.Y
    dx2, dy2 = b["p1"].X - q.X, b["p1"].Y - q.Y
    cruz = dx1 * dy2 - dy1 * dx2
    if abs(cruz) < 1e-9:
        return None
    t = ((q.X - p.X) * dy2 - (q.Y - p.Y) * dx2) / cruz
    s = ((q.X - p.X) * dy1 - (q.Y - p.Y) * dx1) / cruz
    tolerancia = 1.0 / 304.8   # 1 mm en pies, sobre el parametro normalizado a la longitud
    if t < -tolerancia or t > 1.0 + tolerancia or s < -tolerancia or s > 1.0 + tolerancia:
        return None
    return (p.X + t * dx1, p.Y + t * dy1)


def _resolver_rejillas(rejillas, pedidas, direccion, etiqueta):
    if pedidas in (None, ""):
        pedidas = []
    if isinstance(pedidas, _cadena):
        pedidas = [pedidas]
    if not isinstance(pedidas, (list, tuple)):
        raise EscrituraRechazada("{} must be a list of grid names".format(etiqueta), 400)
    if not pedidas:
        lista = [r for r in rejillas.values() if r["dir"] == direccion]
    else:
        lista = []
        faltan = []
        for nombre in pedidas:
            texto = _texto_seguro(nombre).strip()
            rejilla = rejillas.get(texto)
            if rejilla is None:
                faltan.append(texto)
            elif rejilla not in lista:
                lista.append(rejilla)
        if faltan:
            raise EscrituraRechazada(
                u"{}: grids not found: {}".format(etiqueta, u", ".join(faltan)), 404,
                {"available_grids": sorted(rejillas.keys())},
            )
    if not lista:
        raise EscrituraRechazada(
            u"{}: no straight grids along that direction in the project (create them with create_grid_and_levels)".format(
                etiqueta), 400, {"available_grids": sorted(rejillas.keys())},
        )
    return lista


def _etiqueta_interseccion(gy, gx):
    return u"{}-{}".format(gy["name"], gx["name"])


def _coincide_etiqueta(texto, gx, gy):
    texto = _texto_seguro(texto).strip()
    return texto in (u"{}-{}".format(gy["name"], gx["name"]), u"{}-{}".format(gx["name"], gy["name"]))


def _normalizar_saltos(valor, etiqueta):
    if valor in (None, ""):
        return []
    if isinstance(valor, _cadena):
        return [valor]
    if not isinstance(valor, (list, tuple)):
        raise EscrituraRechazada("{} must be a list of labels like \"A-1\"".format(etiqueta), 400)
    return [_texto_seguro(v).strip() for v in valor if _texto_seguro(v).strip()]


def _partes_vano(texto):
    for separador in ("/", "|", ">", " a "):
        if separador in texto:
            partes = [p.strip() for p in texto.split(separador, 1)]
            if len(partes) == 2 and all(partes):
                return partes
    return None


def planificar_portico(doc, data):
    """Plan del portico: pilares por interseccion y nivel, vigas entre pilares consecutivos."""
    mapas_pilar = estructural.mapas_pilares(doc)
    if not mapas_pilar["simbolos"]:
        raise EscrituraRechazada("No structural column families are loaded; load one with load_steel_profile", 404,
                                 {"available_types": []})
    try:
        simbolo_pilar = estructural._elegir(mapas_pilar["simbolos"], data.get("column_type"), "structural column", "column_type")
    except ValueError as error:
        raise EscrituraRechazada(_texto_seguro(error), 404 if "not found" in _texto_seguro(error) else 400,
                                 {"available_types": sorted(set(etiqueta_tipo(t) for t in mapas_pilar["simbolos"]))[:40]})
    if not _texto_seguro(data.get("column_type")).strip():
        raise EscrituraRechazada("column_type is required", 400,
                                 {"available_types": sorted(set(etiqueta_tipo(t) for t in mapas_pilar["simbolos"]))[:40]})
    simbolo_viga = _elegir_simbolo(doc, "OST_StructuralFraming", data.get("beam_type"), "beam_type")
    direccion = _texto_seguro(data.get("beam_directions") or "both").strip().lower()
    if direccion not in DIRECCIONES_VIGAS:
        raise EscrituraRechazada("beam_directions must be x, y or both", 400)
    rotacion = _numero(data.get("column_orientation_deg", 0) or 0, "column_orientation_deg")
    prefijo = _texto_seguro(data.get("mark_prefix")).strip()

    rejillas, curvas = rejillas_clasificadas(doc)
    if not rejillas:
        raise EscrituraRechazada("The project has no straight grids; create them with create_grid_and_levels first", 400)
    grids_x = _resolver_rejillas(rejillas, data.get("grids_x"), "x", "grids_x")
    grids_y = _resolver_rejillas(rejillas, data.get("grids_y"), "y", "grids_y")
    grids_x.sort(key=lambda r: (min(r["p0"].X, r["p1"].X), min(r["p0"].Y, r["p1"].Y)))
    grids_y.sort(key=lambda r: (min(r["p0"].Y, r["p1"].Y), min(r["p0"].X, r["p1"].X)))

    niveles = mapa_niveles(doc)
    todos = sorted(set(niveles.values()), key=elevacion_interna)
    pedidos = data.get("levels") or []
    if isinstance(pedidos, _cadena):
        pedidos = [pedidos]
    if not isinstance(pedidos, (list, tuple)):
        raise EscrituraRechazada("levels must be a list of level names", 400)
    if pedidos:
        elegidos = []
        for nombre in pedidos:
            nivel = _nivel_por_nombre(niveles, nombre, "levels")
            if nivel not in elegidos:
                elegidos.append(nivel)
        elegidos.sort(key=elevacion_interna)
    else:
        elegidos = list(todos)
    if not elegidos:
        raise EscrituraRechazada("The project has no levels", 400)

    saltos_pilares = _normalizar_saltos(data.get("skip_columns_at"), "skip_columns_at")
    saltos_vigas = _normalizar_saltos(data.get("skip_beams_at"), "skip_beams_at")

    # intersecciones (una por pareja de rejillas que se cruzan)
    intersecciones = []
    sin_cruce = []
    for gy in grids_y:
        for gx in grids_x:
            punto = _interseccion_plana(gx, gy)
            etiqueta = _etiqueta_interseccion(gy, gx)
            if punto is None:
                sin_cruce.append(etiqueta)
                continue
            intersecciones.append({"label": etiqueta, "gx": gx, "gy": gy, "x": punto[0], "y": punto[1],
                                   "skip": any(_coincide_etiqueta(s, gx, gy) for s in saltos_pilares)})
    desconocidos = [s for s in saltos_pilares if not any(_coincide_etiqueta(s, i["gx"], i["gy"]) for i in intersecciones)]
    if desconocidos:
        raise EscrituraRechazada(
            u"skip_columns_at: unknown intersections {}".format(u", ".join(desconocidos)), 400,
            {"available_labels": [i["label"] for i in intersecciones]},
        )
    if not intersecciones:
        raise EscrituraRechazada("The selected grids do not intersect", 400,
                                 {"grids_x": [g["name"] for g in grids_x], "grids_y": [g["name"] for g in grids_y]})

    # vanos a saltar: "A-1/A-2"
    vanos_saltados = []
    for texto in saltos_vigas:
        partes = _partes_vano(texto)
        if partes is None:
            raise EscrituraRechazada(u"skip_beams_at: use \"A-1/A-2\" (two intersection labels); got '{}'".format(texto), 400)
        extremos = []
        for parte in partes:
            coincidencia = [i for i in intersecciones if _coincide_etiqueta(parte, i["gx"], i["gy"])]
            if not coincidencia:
                raise EscrituraRechazada(u"skip_beams_at: unknown intersection '{}'".format(parte), 400,
                                         {"available_labels": [i["label"] for i in intersecciones]})
            extremos.append(coincidencia[0]["label"])
        vanos_saltados.append(frozenset(extremos))

    def siguiente_nivel(nivel):
        cota = elevacion_interna(nivel)
        posteriores = [n for n in elegidos if elevacion_interna(n) > cota + 1e-9]
        if not posteriores:
            posteriores = [n for n in todos if elevacion_interna(n) > cota + 1e-9]
        return posteriores[0] if posteriores else None

    avisos = []
    if curvas:
        avisos.append(u"Rejillas curvas ignoradas: {}".format(u", ".join(curvas)))
    if sin_cruce:
        avisos.append(u"Parejas de rejillas sin cruce: {}".format(u", ".join(sin_cruce[:20])))
    pilares = []
    vigas = []
    saltados_pilares = []
    saltados_vigas = []
    for nivel in elegidos:
        superior = siguiente_nivel(nivel)
        if superior is None:
            avisos.append(u"No hay nivel por encima de '{}': sus pilares se crean sin nivel superior".format(
                get_element_name(nivel)))
        for inter in intersecciones:
            if inter["skip"]:
                saltados_pilares.append({"label": inter["label"], "level": get_element_name(nivel)})
                continue
            definicion = {
                "point": {"x": inter["x"] * FEET_TO_MM, "y": inter["y"] * FEET_TO_MM, "z": 0},
                "base_level": get_element_name(nivel),
                "top_level": get_element_name(superior) if superior is not None else None,
                "type_name": etiqueta_tipo(simbolo_pilar),
                "rotation": rotacion,
            }
            try:
                plan = estructural.planificar_pilar(definicion, len(pilares), mapas_pilar)
            except ValueError as error:
                raise EscrituraRechazada(_texto_seguro(error), 400)
            plan["symbol"] = simbolo_pilar
            plan["label"] = inter["label"]
            plan["grid_x"] = inter["gx"]["name"]
            plan["grid_y"] = inter["gy"]["name"]
            pilares.append(plan)
        # vigas: a lo largo de cada rejilla Y (direccion x) y de cada rejilla X (direccion y)
        recorridos = []
        if direccion in ("x", "both"):
            for gy in grids_y:
                fila = sorted([i for i in intersecciones if i["gy"] is gy], key=lambda i: (i["x"], i["y"]))
                recorridos.append(("x", fila))
        if direccion in ("y", "both"):
            for gx in grids_x:
                columna = sorted([i for i in intersecciones if i["gx"] is gx], key=lambda i: (i["y"], i["x"]))
                recorridos.append(("y", columna))
        cota = elevacion_interna(nivel)
        for sentido, fila in recorridos:
            for a, b in zip(fila, fila[1:]):
                par = frozenset([a["label"], b["label"]])
                if par in vanos_saltados:
                    saltados_vigas.append({"from": a["label"], "to": b["label"], "level": get_element_name(nivel)})
                    continue
                inicio = DB.XYZ(a["x"], a["y"], cota)
                fin = DB.XYZ(b["x"], b["y"], cota)
                vigas.append({
                    "idx": len(vigas), "kind": "beam", "start": inicio, "end": fin, "symbol": simbolo_viga,
                    "level": nivel, "name": u"", "from": a["label"], "to": b["label"], "direction": sentido,
                })
    total = len(pilares) + len(vigas)
    if total == 0:
        raise EscrituraRechazada("Nothing to create: every column and beam was skipped", 400)
    if prefijo:
        ancho = max(2, len(_texto(total)))
        for indice, plan in enumerate(pilares + vigas):
            plan["mark"] = u"{}{}".format(prefijo, _texto(indice + 1).zfill(ancho))
    else:
        for plan in pilares + vigas:
            plan["mark"] = None

    resumen_pilares = []
    for plan in pilares:
        haria = estructural.haria_pilar(plan)
        resumen_pilares.append({"label": plan["label"], "grid_x": plan["grid_x"], "grid_y": plan["grid_y"],
                                "point_mm": haria["point_mm"], "base_level": haria["base_level"],
                                "top_level": haria["top_level"], "mark": plan["mark"]})
    resumen_vigas = []
    for plan in vigas:
        haria = structure.haria_viga(plan)
        resumen_vigas.append({"from": plan["from"], "to": plan["to"], "level": haria["level"], "direction": plan["direction"],
                              "start_mm": haria["start_mm"], "end_mm": haria["end_mm"],
                              "length_mm": round(plan["start"].DistanceTo(plan["end"]) * FEET_TO_MM, 1), "mark": plan["mark"]})
    return {
        "pilares": pilares, "vigas": vigas,
        "plan": {
            "counts": {"columns": len(pilares), "beams": len(vigas), "total": total},
            "column_type": etiqueta_tipo(simbolo_pilar), "beam_type": etiqueta_tipo(simbolo_viga),
            "beam_directions": direccion, "column_orientation_deg": rotacion, "mark_prefix": prefijo or None,
            "grids_x": [g["name"] for g in grids_x], "grids_y": [g["name"] for g in grids_y],
            "levels": [get_element_name(n) for n in elegidos],
            "intersections": [i["label"] for i in intersecciones],
            "columns": resumen_pilares, "beams": resumen_vigas,
            "skipped": {"columns": saltados_pilares, "beams": saltados_vigas},
            "warnings": avisos,
        },
    }


# ---------------------------------------------------------------------------
# /create_bracing/ (lote)
# ---------------------------------------------------------------------------
PATRONES_ARRIOSTRE = ("single", "X", "V", "inverted_V", "K")
_ALIAS_PATRON = {"simple": "single", "x": "X", "cruz": "X", "v": "V", "inverted_v": "inverted_V",
                 "invertida": "inverted_V", "chevron": "inverted_V", "k": "K"}


def _barras_patron(patron, inicio, fin, z_inferior, z_superior):
    """Lista de (a, b) en pies para el patron dentro del vano inicio-fin entre las dos cotas."""
    a = DB.XYZ(inicio.X, inicio.Y, z_inferior)
    b = DB.XYZ(fin.X, fin.Y, z_inferior)
    c = DB.XYZ(inicio.X, inicio.Y, z_superior)
    d = DB.XYZ(fin.X, fin.Y, z_superior)
    medio_superior = DB.XYZ((c.X + d.X) / 2.0, (c.Y + d.Y) / 2.0, z_superior)
    medio_inferior = DB.XYZ((a.X + b.X) / 2.0, (a.Y + b.Y) / 2.0, z_inferior)
    medio_inicio = DB.XYZ(inicio.X, inicio.Y, (z_inferior + z_superior) / 2.0)
    if patron == "single":
        return [(a, d)]
    if patron == "X":
        return [(a, d), (b, c)]
    if patron == "V":
        return [(a, medio_superior), (b, medio_superior)]
    if patron == "inverted_V":
        return [(c, medio_inferior), (d, medio_inferior)]
    return [(b, medio_inicio), (d, medio_inicio)]   # K: desde el centro del lado inicial a los dos extremos del final


def planificar_arriostres(doc, data):
    """Valida bays[] y brace_type; devuelve (simbolo, planes por vano)."""
    vanos = data.get("bays")
    if not isinstance(vanos, (list, tuple)) or not vanos:
        raise EscrituraRechazada(
            "bays is required: [{\"start_point_mm\", \"end_point_mm\", \"level_bottom\", \"level_top\", \"pattern\"}]", 400,
            {"available_patterns": list(PATRONES_ARRIOSTRE)},
        )
    simbolo = _elegir_simbolo(doc, "OST_StructuralFraming", data.get("brace_type"), "brace_type")
    niveles = mapa_niveles(doc)
    planes = []
    for indice, vano in enumerate(vanos):
        etiqueta = "bays[{}]".format(indice)
        extra = {"index": indice}
        if not isinstance(vano, dict):
            raise EscrituraRechazada("{} must be an object".format(etiqueta), 400, extra)
        inicio = _punto_mm(vano.get("start_point_mm") or vano.get("start_point"), etiqueta + ".start_point_mm", extra)
        fin = _punto_mm(vano.get("end_point_mm") or vano.get("end_point"), etiqueta + ".end_point_mm", extra)
        if abs(inicio.X - fin.X) < 1e-9 and abs(inicio.Y - fin.Y) < 1e-9:
            raise EscrituraRechazada("{}: start and end points coincide in plan".format(etiqueta), 400, extra)
        inferior = _nivel_por_nombre(niveles, vano.get("level_bottom"), etiqueta + ".level_bottom", extra)
        superior = _nivel_por_nombre(niveles, vano.get("level_top"), etiqueta + ".level_top", extra)
        z_inferior = elevacion_interna(inferior)
        z_superior = elevacion_interna(superior)
        if z_superior <= z_inferior + 1e-9:
            raise EscrituraRechazada("{}: level_top must be above level_bottom".format(etiqueta), 400, extra)
        crudo = _texto_seguro(vano.get("pattern") or "single").strip()
        patron = _ALIAS_PATRON.get(crudo.lower(), crudo)
        if patron not in PATRONES_ARRIOSTRE:
            raise EscrituraRechazada("{}: pattern '{}' not supported".format(etiqueta, crudo), 400,
                                     dict(extra, available_patterns=list(PATRONES_ARRIOSTRE)))
        barras = _barras_patron(patron, inicio, fin, z_inferior, z_superior)
        planes.append({
            "index": indice, "pattern": patron, "level_bottom": inferior, "level_top": superior,
            "start_mm": punto_a_mm(DB.XYZ(inicio.X, inicio.Y, z_inferior)),
            "end_mm": punto_a_mm(DB.XYZ(fin.X, fin.Y, z_inferior)),
            "height_mm": round((z_superior - z_inferior) * FEET_TO_MM, 1),
            "barras": barras,
        })
    return simbolo, planes


def _haria_arriostre(plan, simbolo, indice, a, b):
    return {
        "accion": "crear", "element_type": "brace", "bay": plan["index"], "pattern": plan["pattern"], "brace": indice,
        "type": etiqueta_tipo(simbolo), "level": get_element_name(plan["level_bottom"]),
        "start_mm": punto_a_mm(a), "end_mm": punto_a_mm(b), "length_mm": round(a.DistanceTo(b) * FEET_TO_MM, 1),
    }


# ---------------------------------------------------------------------------
# /create_truss/ (lote)
# ---------------------------------------------------------------------------
def _tipos_cercha(doc):
    clase = getattr(getattr(DB, "Structure", None), "TrussType", None)
    if clase is None:
        return None
    try:
        return list(DB.FilteredElementCollector(doc).OfClass(clase).ToElements())
    except Exception:
        return []


def planificar_cerchas(doc, data):
    cerchas = data.get("trusses")
    if not isinstance(cerchas, (list, tuple)) or not cerchas:
        raise EscrituraRechazada("trusses is required: [{\"truss_type\", \"start_point_mm\", \"end_point_mm\", \"level\"}]", 400)
    tipos = _tipos_cercha(doc)
    if tipos is None:
        raise EscrituraRechazada("This Revit API has no DB.Structure.TrussType / Truss", 409, {"no_soportado": True})
    disponibles = sorted(set(etiqueta_tipo(t) for t in tipos))
    if not tipos:
        raise EscrituraRechazada("No truss types are loaded in the project (load a truss family first)", 404,
                                 {"available_types": []})
    niveles = mapa_niveles(doc)
    planes = []
    for indice, cercha in enumerate(cerchas):
        etiqueta = "trusses[{}]".format(indice)
        extra = {"index": indice}
        if not isinstance(cercha, dict):
            raise EscrituraRechazada("{} must be an object".format(etiqueta), 400, extra)
        nombre = _texto_seguro(cercha.get("truss_type") or cercha.get("type_name")).strip()
        if not nombre:
            raise EscrituraRechazada("{}: truss_type is required".format(etiqueta), 400, dict(extra, available_types=disponibles))
        coincidencias = buscar_tipo_por_nombre(tipos, nombre)
        if not coincidencias:
            raise EscrituraRechazada(u"{}: truss type '{}' not found".format(etiqueta, nombre), 404,
                                     dict(extra, available_types=disponibles))
        if len(coincidencias) > 1:
            raise EscrituraRechazada(u"{}: truss type '{}' is ambiguous, use 'Family: Type'".format(etiqueta, nombre), 400, extra)
        nivel = _nivel_por_nombre(niveles, cercha.get("level") or cercha.get("level_name"), etiqueta + ".level", extra)
        inicio = _punto_mm(cercha.get("start_point_mm") or cercha.get("start_point"), etiqueta + ".start_point_mm", extra)
        fin = _punto_mm(cercha.get("end_point_mm") or cercha.get("end_point"), etiqueta + ".end_point_mm", extra)
        cota = elevacion_interna(nivel)
        inicio = DB.XYZ(inicio.X, inicio.Y, cota + inicio.Z)
        fin = DB.XYZ(fin.X, fin.Y, cota + fin.Z)
        if inicio.DistanceTo(fin) < 1e-6:
            raise EscrituraRechazada("{}: zero-length truss".format(etiqueta), 400, extra)
        planes.append({"index": indice, "tipo": coincidencias[0], "nivel": nivel, "inicio": inicio, "fin": fin})
    return planes


def _haria_cercha(plan):
    return {
        "accion": "crear", "element_type": "truss", "index": plan["index"], "type": etiqueta_tipo(plan["tipo"]),
        "level": get_element_name(plan["nivel"]), "start_mm": punto_a_mm(plan["inicio"]), "end_mm": punto_a_mm(plan["fin"]),
        "length_mm": round(plan["inicio"].DistanceTo(plan["fin"]) * FEET_TO_MM, 1),
    }


# ---------------------------------------------------------------------------
# /set_structural_properties/ (lote sobre lotes.resolver_parametros)
# ---------------------------------------------------------------------------
_PROPIEDAD_POR_BIP = dict((bip, clave) for clave, bip, _ in PROPIEDADES_ESTRUCTURALES)
for _prefijo, _clave in (("START", "start_release"), ("END", "end_release")):
    _PROPIEDAD_POR_BIP["STRUCTURAL_{}_RELEASE_TYPE".format(_prefijo)] = _clave
    for _componente in LIBERACIONES:
        _PROPIEDAD_POR_BIP["STRUCTURAL_{}_RELEASE_{}".format(_prefijo, _componente)] = _clave
PROPIEDADES_ADMITIDAS = ("start_release", "end_release") + tuple(c for c, _, _ in PROPIEDADES_ESTRUCTURALES)


def _indice_enum(clave, valor):
    """Entero para un parametro de enumeracion: numero, o nombre del enum de la API (o su alias)."""
    if isinstance(valor, bool):
        raise EscrituraRechazada("{}: use an integer or an enum name, not a boolean".format(clave), 400)
    if isinstance(valor, (int, float)):
        return int(valor)
    texto = _texto_seguro(valor).strip()
    if not texto:
        raise EscrituraRechazada("{} must not be empty".format(clave), 400)
    if texto.lstrip("-").isdigit():
        return int(texto)
    nombre = _ALIAS_ENUM.get(clave, {}).get(texto.lower(), texto)
    buscado = nombre.lower().replace("_", "").replace(" ", "")
    enumeracion = getattr(getattr(DB, "Structure", None), _ENUMS_PROPIEDAD[clave], None)
    miembros = []
    if enumeracion is not None:
        for atributo in dir(enumeracion):
            if atributo.startswith("_"):
                continue
            miembros.append(atributo)
            if atributo.lower() == buscado:
                try:
                    return int(getattr(enumeracion, atributo))
                except Exception:
                    break
    raise EscrituraRechazada(
        u"{}: '{}' is not a value of DB.Structure.{}; use an integer or one of {}".format(
            clave, texto, _ENUMS_PROPIEDAD[clave], u", ".join(sorted(m for m in miembros if m[0].isupper()))), 400,
    )


def _normalizar_liberacion(clave, valor):
    """('type', indice) para pinned/fixed/... o ('components', {FX: bool...}) para un diccionario parcial."""
    if isinstance(valor, dict):
        componentes = {}
        for nombre, activo in valor.items():
            componente = _texto_seguro(nombre).strip().upper()
            if componente == "TYPE":
                continue
            if componente not in LIBERACIONES:
                raise EscrituraRechazada(
                    u"{}: unknown release component '{}'; use {}".format(clave, nombre, ", ".join(LIBERACIONES)), 400,
                )
            componentes[componente] = _es_verdadero(activo)
        if not componentes:
            raise EscrituraRechazada("{}: give at least one component (FX, FY, FZ, MX, MY, MZ)".format(clave), 400)
        return "components", componentes
    if isinstance(valor, bool):
        raise EscrituraRechazada("{}: use pinned, fixed, bending_moment or a {{FX: true, ...}} object".format(clave), 400)
    if isinstance(valor, (int, float)):
        indice = int(valor)
    else:
        texto = _texto_seguro(valor).strip().lower()
        if texto.isdigit():
            indice = int(texto)
        elif texto in _TIPO_LIBERACION_POR_NOMBRE:
            indice = _TIPO_LIBERACION_POR_NOMBRE[texto]
        else:
            raise EscrituraRechazada(
                u"{}: '{}' is not a release; use pinned, fixed, bending_moment, user_defined or {{FX: true, ...}}".format(
                    clave, valor), 400,
            )
    if indice not in TIPOS_LIBERACION:
        raise EscrituraRechazada("{}: release type index must be 0-3".format(clave), 400)
    return "type", indice


def normalizar_propiedades(data):
    """{propiedad: valor normalizado} a partir del cuerpo; lanza 400 si no hay ninguna."""
    propiedades = {}
    for clave in ("start_release", "end_release"):
        if data.get(clave) is not None:
            propiedades[clave] = _normalizar_liberacion(clave, data[clave])
    for clave, _, clase in PROPIEDADES_ESTRUCTURALES:
        if data.get(clave) is None:
            continue
        valor = data[clave]
        if clase == "enum":
            propiedades[clave] = _indice_enum(clave, valor)
        else:
            numero = _numero(valor, clave)
            # se conserva el numero tal como llego (50, no 50.0) para que `haria` lo muestre igual
            propiedades[clave] = valor if isinstance(valor, (int, float)) and not isinstance(valor, bool) else numero
    if not propiedades:
        raise EscrituraRechazada("Give at least one property to set", 400,
                                 {"available_properties": list(PROPIEDADES_ADMITIDAS)})
    return propiedades


def _operaciones_propiedades(ids, propiedades, no_disponibles):
    """(fase1, fase2, liberaciones): tuplas (element_id, BIP, valor, False) para resolver_parametros.

    fase2 son los componentes FX..MZ, que Revit solo deja editar cuando el tipo de
    liberacion ya es 'definido por el usuario' (se fijan tras la fase 1)."""
    fase1 = []
    fase2 = []
    liberaciones = {}
    for clave, valor in propiedades.items():
        if clave in ("start_release", "end_release"):
            prefijo = "START" if clave == "start_release" else "END"
            nombre_tipo = "STRUCTURAL_{}_RELEASE_TYPE".format(prefijo)
            if _bip(nombre_tipo) is None:
                no_disponibles.append({"property": clave, "builtin": nombre_tipo})
                continue
            modo, contenido = valor
            liberaciones[clave] = (prefijo, modo, contenido)
            indice_tipo = contenido if modo == "type" else 3
            for element_id in ids:
                fase1.append((element_id, nombre_tipo, indice_tipo, False))
                if modo == "components":
                    for componente, activo in sorted(contenido.items()):
                        nombre = "STRUCTURAL_{}_RELEASE_{}".format(prefijo, componente)
                        if _bip(nombre) is None:
                            entrada = {"property": clave, "builtin": nombre}
                            if entrada not in no_disponibles:
                                no_disponibles.append(entrada)
                            continue
                        fase2.append((element_id, nombre, 1 if activo else 0, False))
            continue
        nombre_bip = [b for c, b, _ in PROPIEDADES_ESTRUCTURALES if c == clave][0]
        if _bip(nombre_bip) is None:
            no_disponibles.append({"property": clave, "builtin": nombre_bip})
            continue
        for element_id in ids:
            fase1.append((element_id, nombre_bip, valor, False))
    return fase1, fase2, liberaciones


def _traducir_fallidos(fallidos):
    """Los fallidos de resolver_parametros con la propiedad del contrato en vez del BuiltInParameter."""
    traducidos = []
    for fallo in fallidos:
        entrada = {"element_id": fallo["element_id"], "property": _PROPIEDAD_POR_BIP.get(fallo["parameter_name"], fallo["parameter_name"]),
                   "builtin": fallo["parameter_name"], "motivo": fallo["motivo"]}
        traducidos.append(entrada)
    return traducidos


def _plan_analitico(doc, elemento, element_id, liberaciones, prefijos_sin_parametro):
    """Liberaciones que iran al AnalyticalMember (2023+) porque el elemento no tiene los parametros."""
    miembro = miembro_analitico(doc, elemento)
    if miembro is None:
        return None
    acciones = []
    for clave, (prefijo, modo, contenido) in liberaciones.items():
        if prefijo not in prefijos_sin_parametro:
            continue
        inicio = prefijo == "START"
        acciones.append({
            "element_id": element_id, "property": clave, "inicio": inicio, "modo": modo, "contenido": contenido,
            "antes": liberaciones_analiticas(miembro, inicio),
        })
    if not acciones:
        return None
    return {"miembro": miembro, "analytical_id": get_element_id_value(miembro), "acciones": acciones}


def _aplicar_analitico(plan):
    """SetReleaseType / SetReleaseConditions en el AnalyticalMember. Dentro de la transaccion."""
    miembro = plan["miembro"]
    for accion in plan["acciones"]:
        selector = _selector(accion["inicio"])
        tipos = DB.Structure.ReleaseType
        if accion["modo"] == "type":
            nombre = {0: "Fixed", 1: "Pinned", 2: "BendingMoment", 3: "UserDefined"}[accion["contenido"]]
            miembro.SetReleaseType(selector, getattr(tipos, nombre))
            continue
        miembro.SetReleaseType(selector, tipos.UserDefined)
        actuales = {}
        try:
            condiciones = miembro.GetReleaseConditions(selector)
            for componente in LIBERACIONES:
                actuales[componente] = bool(getattr(condiciones, componente.capitalize()))
        except Exception:
            for componente in LIBERACIONES:
                actuales[componente] = False
        actuales.update(accion["contenido"])
        nuevas = DB.Structure.ReleaseConditions(
            accion["inicio"], actuales["FX"], actuales["FY"], actuales["FZ"], actuales["MX"], actuales["MY"], actuales["MZ"],
        )
        miembro.SetReleaseConditions(nuevas)


def _haria_analitico(plan):
    lista = []
    for accion in plan["acciones"]:
        if accion["modo"] == "type":
            despues = TIPOS_LIBERACION[accion["contenido"]]
        else:
            despues = dict(accion["contenido"], type="user_defined")
        lista.append({
            "accion": "set_release", "element_id": accion["element_id"], "property": accion["property"],
            "analytical_member_id": plan["analytical_id"], "source": "AnalyticalMember",
            "antes": accion["antes"], "despues": despues,
        })
    return lista


# ---------------------------------------------------------------------------
# /create_steel_connection/ (lote)
# ---------------------------------------------------------------------------
def _tipo_conexion(tipos, nombre_o_id, etiqueta, extra):
    disponibles = [{"id": get_element_id_value(t), "tipo": get_element_name(t)} for t in tipos]
    if nombre_o_id in (None, ""):
        raise EscrituraRechazada("{}: connection_type is required".format(etiqueta), 400, dict(extra, available_types=disponibles))
    if isinstance(nombre_o_id, (int, float)) and not isinstance(nombre_o_id, bool):
        for tipo in tipos:
            if get_element_id_value(tipo) == int(nombre_o_id):
                return tipo
    texto = _texto_seguro(nombre_o_id).strip()
    coincidencias = buscar_tipo_por_nombre(tipos, texto)
    if not coincidencias:
        coincidencias = [t for t in tipos if _texto_seguro(get_element_id_value(t)) == texto]
    if not coincidencias:
        raise EscrituraRechazada(u"{}: connection type '{}' not found".format(etiqueta, texto), 404,
                                 dict(extra, available_types=disponibles))
    return coincidencias[0]


def _tipo_aprobacion(doc, data):
    """StructuralConnectionApprovalType pedido (approval_status: nombre o id) cuando approve=true; None si no se pide."""
    if not _es_verdadero(data.get("approve", False)):
        return None
    disponibles = tipos_aprobacion(doc)
    pedido = data.get("approval_status")
    if pedido in (None, ""):
        raise EscrituraRechazada(
            "approve=true needs approval_status (the approval type name as Revit shows it, or its id)", 400,
            {"available_approval_types": disponibles},
        )
    for entrada in disponibles:
        if _texto_seguro(pedido).strip() in (entrada["nombre"], _texto_seguro(entrada["id"])):
            return doc.GetElement(make_element_id(entrada["id"]))
    raise EscrituraRechazada(u"approval_status '{}' not found".format(pedido), 404,
                             {"available_approval_types": disponibles})


def planificar_conexiones(doc, data):
    handler, tipos = tipos_conexion_disponibles(doc)
    conexiones = data.get("connections")
    if not isinstance(conexiones, (list, tuple)) or not conexiones:
        raise EscrituraRechazada("connections is required: [{\"element_ids\": [...], \"connection_type\": ...}]", 400,
                                 {"available_types": [get_element_name(t) for t in tipos]})
    aprobacion = _tipo_aprobacion(doc, data)
    planes = []
    for indice, conexion in enumerate(conexiones):
        etiqueta = "connections[{}]".format(indice)
        extra = {"index": indice}
        if not isinstance(conexion, dict):
            raise EscrituraRechazada("{} must be an object".format(etiqueta), 400, extra)
        ids = conexion.get("element_ids")
        if isinstance(ids, (int, float)):
            ids = [ids]
        if not isinstance(ids, (list, tuple)) or not ids:
            raise EscrituraRechazada("{}: element_ids is required".format(etiqueta), 400, extra)
        elementos = []
        for identificador in ids:
            try:
                elem = doc.GetElement(make_element_id(identificador))
            except ValueError as error:
                raise EscrituraRechazada("{}: {}".format(etiqueta, error), 400, extra)
            if elem is None:
                raise EscrituraRechazada("{}: element {} not found".format(etiqueta, identificador), 404, extra)
            elementos.append(elem)
        tipo = _tipo_conexion(tipos, conexion.get("connection_type"), etiqueta, extra)
        planes.append({"index": indice, "elementos": elementos, "ids": [get_element_id_value(e) for e in elementos],
                       "tipo": tipo, "aprobacion": aprobacion})
    return handler, planes


# ---------------------------------------------------------------------------
# /add_plate/
# ---------------------------------------------------------------------------
CARAS = ("top", "bottom", "web")


def _eje_host(doc, host):
    """(p0, p1, direccion) del eje del anfitrion: su curva de ubicacion o, en un pilar, la vertical de su caja."""
    curva = _curva_ubicacion(host)
    if curva is not None:
        try:
            p0, p1 = curva.GetEndPoint(0), curva.GetEndPoint(1)
            if p0.DistanceTo(p1) > 1e-9:
                return p0, p1, p1.Subtract(p0).Normalize()
        except Exception:
            pass
    try:
        punto = host.Location.Point
        caja = host.get_BoundingBox(None)
        if caja is not None:
            return DB.XYZ(punto.X, punto.Y, caja.Min.Z), DB.XYZ(punto.X, punto.Y, caja.Max.Z), DB.XYZ(0, 0, 1)
    except Exception:
        pass
    raise EscrituraRechazada("The host has no location curve nor point with a bounding box", 400)


def _caras_con_referencia(host):
    """[(cara, normal, area, referencia)] de la geometria del anfitrion con ComputeReferences=True."""
    opciones = DB.Options()
    try:
        opciones.ComputeReferences = True
    except Exception:
        pass
    try:
        opciones.DetailLevel = DB.ViewDetailLevel.Fine
    except Exception:
        pass
    try:
        geometria = host.get_Geometry(opciones)
    except Exception as error:
        raise EscrituraRechazada("get_Geometry failed on the host: {}".format(error), 400)
    caras = []

    def recorrer(objetos, transformacion):
        for objeto in objetos or []:
            try:
                if isinstance(objeto, DB.Solid):
                    for cara in objeto.Faces:
                        normal = None
                        try:
                            normal = cara.FaceNormal
                        except Exception:
                            try:
                                normal = cara.ComputeNormal(DB.UV(0.5, 0.5))
                            except Exception:
                                normal = None
                        if normal is None:
                            continue
                        if transformacion is not None:
                            try:
                                normal = transformacion.OfVector(normal)
                            except Exception:
                                pass
                        referencia = getattr(cara, "Reference", None)
                        try:
                            area = float(cara.Area)
                        except Exception:
                            area = 0.0
                        caras.append((cara, normal, area, referencia, transformacion))
                elif isinstance(objeto, DB.GeometryInstance):
                    # las referencias validas para colocar son las de la geometria del simbolo
                    try:
                        recorrer(objeto.GetSymbolGeometry(), objeto.Transform)
                    except Exception:
                        recorrer(objeto.GetInstanceGeometry(), None)
            except Exception:
                continue

    recorrer(geometria, None)
    return caras


def _elegir_cara(caras, cara_pedida, direccion):
    """La cara superior (normal +Z), inferior (-Z) o el alma (vertical, perpendicular al eje) con referencia."""
    candidatas = [c for c in caras if c[3] is not None]
    if not candidatas:
        return None
    if cara_pedida == "top":
        candidatas = [c for c in candidatas if c[1].Z > 0.7]
        candidatas.sort(key=lambda c: (-c[1].Z, -c[2]))
    elif cara_pedida == "bottom":
        candidatas = [c for c in candidatas if c[1].Z < -0.7]
        candidatas.sort(key=lambda c: (c[1].Z, -c[2]))
    else:
        candidatas = [c for c in candidatas if abs(c[1].Z) < 0.3 and abs(c[1].DotProduct(direccion)) < 0.3]
        candidatas.sort(key=lambda c: -c[2])
    return candidatas[0] if candidatas else None


def _punto_sobre_cara(cara, transformacion, punto):
    """Proyeccion del punto sobre la cara (en coordenadas del modelo)."""
    try:
        local = punto
        if transformacion is not None:
            local = transformacion.Inverse.OfPoint(punto)
        resultado = cara.Project(local)
        proyectado = resultado.XYZPoint if resultado is not None else local
        if transformacion is not None:
            proyectado = transformacion.OfPoint(proyectado)
        return proyectado
    except Exception:
        return punto


def _posiciones(valores, longitud_mm):
    """Posiciones en mm desde el inicio: un valor <= 1 se toma como fraccion de la longitud."""
    if valores in (None, ""):
        valores = [0.5]
    if isinstance(valores, (int, float)) and not isinstance(valores, bool):
        valores = [valores]
    if not isinstance(valores, (list, tuple)) or not valores:
        raise EscrituraRechazada("positions must be a list of mm from the start (or fractions 0-1)", 400)
    resultado = []
    for indice, crudo in enumerate(valores):
        numero = _numero(crudo, "positions[{}]".format(indice))
        if numero < 0:
            raise EscrituraRechazada("positions[{}] must be >= 0".format(indice), 400)
        mm = numero * longitud_mm if numero <= 1.0 else numero
        if mm > longitud_mm + 0.5:
            raise EscrituraRechazada("positions[{}] = {} mm is beyond the host length ({} mm)".format(indice, round(mm, 1), round(longitud_mm, 1)), 400)
        resultado.append(min(mm, longitud_mm))
    return resultado


def _tipo_colocacion(symbol):
    try:
        return _nombre_enum(symbol.Family.FamilyPlacementType)
    except Exception:
        return None


def planificar_placas(doc, data):
    host = _elemento(doc, data.get("host_id"))
    if not isinstance(host, DB.FamilyInstance):
        raise EscrituraRechazada("host_id {} is not a family instance (beam, brace or column)".format(data.get("host_id")), 400)
    familia = _texto_seguro(data.get("family_name")).strip()
    tipo = _texto_seguro(data.get("type_name")).strip()
    if not familia or not tipo:
        raise EscrituraRechazada("family_name and type_name are required", 400)
    symbol = find_family_symbol_safely(doc, familia, tipo)
    if symbol is None:
        disponibles = []
        try:
            for candidato in DB.FilteredElementCollector(doc).OfClass(DB.FamilySymbol).ToElements():
                if nombre_familia(candidato) == familia:
                    disponibles.append(get_element_name(candidato))
        except Exception:
            pass
        raise EscrituraRechazada(
            u"Family type '{}: {}' not found{}".format(familia, tipo, u"" if disponibles else u" (family not loaded)"), 404,
            {"available_types": sorted(disponibles)},
        )
    cara = _texto_seguro(data.get("face") or "top").strip().lower()
    if cara not in CARAS:
        raise EscrituraRechazada("face must be top, bottom or web", 400, {"available_faces": list(CARAS)})
    p0, p1, direccion = _eje_host(doc, host)
    longitud = p0.DistanceTo(p1) * FEET_TO_MM
    posiciones = _posiciones(data.get("positions"), longitud)
    colocacion = _tipo_colocacion(symbol)
    en_cara = colocacion in ("WorkPlaneBased", "OneLevelBasedHosted") or colocacion is None
    cara_elegida = None
    if en_cara:
        cara_elegida = _elegir_cara(_caras_con_referencia(host), cara, direccion)
        if cara_elegida is None:
            if colocacion is None:
                en_cara = False
            else:
                raise EscrituraRechazada(
                    u"No {} face with a reference was found on host {} (get_Geometry with ComputeReferences); "
                    u"use a point-based family or another face".format(cara, get_element_id_value(host)), 400,
                )
    puntos = []
    for mm in posiciones:
        punto = p0.Add(direccion.Multiply(mm * MM_TO_FEET))
        if cara_elegida is not None:
            punto = _punto_sobre_cara(cara_elegida[0], cara_elegida[4], punto)
        puntos.append((mm, punto))
    return {
        "host": host, "host_id": get_element_id_value(host), "symbol": symbol, "face": cara, "en_cara": en_cara,
        "cara": cara_elegida, "direccion": direccion, "puntos": puntos, "placement_type": colocacion,
        "level": _nivel_de(doc, host), "length_mm": round(longitud, 1),
    }


def _direccion_en_cara(direccion, normal):
    """Direccion de referencia dentro del plano de la cara (perpendicular a la normal)."""
    proyectada = direccion.Subtract(normal.Multiply(direccion.DotProduct(normal)))
    if proyectada.GetLength() < 1e-6:
        for eje in (DB.XYZ(1, 0, 0), DB.XYZ(0, 1, 0), DB.XYZ(0, 0, 1)):
            proyectada = eje.Subtract(normal.Multiply(eje.DotProduct(normal)))
            if proyectada.GetLength() > 1e-6:
                break
    return proyectada.Normalize()


def colocar_placa(doc, plan, punto):
    """NewFamilyInstance en la cara (Reference, XYZ, refDir, symbol) o en el punto. Dentro de una transaccion."""
    _activar(plan["symbol"], doc)
    if plan["en_cara"] and plan["cara"] is not None:
        cara, normal, _, referencia, _ = plan["cara"]
        return doc.Create.NewFamilyInstance(referencia, punto, _direccion_en_cara(plan["direccion"], normal), plan["symbol"])
    if plan["level"] is not None:
        return doc.Create.NewFamilyInstance(punto, plan["symbol"], plan["level"], DB.Structure.StructuralType.NonStructural)
    return doc.Create.NewFamilyInstance(punto, plan["symbol"], DB.Structure.StructuralType.NonStructural)


# ---------------------------------------------------------------------------
# /split_beam/
# ---------------------------------------------------------------------------
AVISOS_DIVIDIR = (
    u"Las uniones de geometria, los recortes y las conexiones de acero del elemento original no se conservan en "
    u"los tramos nuevos (CopyElement copia el elemento, no sus relaciones); revisalas y vuelve a unir con join_geometry.",
    u"Revit puede ajustar los extremos de cada tramo por la union automatica de vigas (join ends); compara start_mm/end_mm.",
)


def planificar_division(doc, data):
    elem = _elemento(doc, data.get("element_id"))
    curva = _curva_ubicacion(elem)
    if curva is None:
        raise EscrituraRechazada("Element {} has no location curve (only beams and braces can be split)".format(data.get("element_id")), 400)
    try:
        p0, p1 = curva.GetEndPoint(0), curva.GetEndPoint(1)
    except Exception:
        raise EscrituraRechazada("The location curve of element {} has no end points".format(data.get("element_id")), 400)
    if isinstance(curva, DB.Arc) or not isinstance(curva, DB.Line):
        raise EscrituraRechazada("Only straight beams (Line) can be split", 400)
    longitud = p0.DistanceTo(p1) * FEET_TO_MM
    crudos = data.get("at_mm")
    if isinstance(crudos, (int, float)) and not isinstance(crudos, bool):
        crudos = [crudos]
    if not isinstance(crudos, (list, tuple)) or not crudos:
        raise EscrituraRechazada("at_mm is required: distances from the start in mm", 400, {"length_mm": round(longitud, 1)})
    cortes = []
    for indice, crudo in enumerate(crudos):
        mm = _numero(crudo, "at_mm[{}]".format(indice))
        if mm <= 1.0 or mm >= longitud - 1.0:
            raise EscrituraRechazada(
                "at_mm[{}] = {} must be inside the beam (0 < at_mm < {} mm)".format(indice, mm, round(longitud, 1)), 400,
                {"length_mm": round(longitud, 1)},
            )
        if any(abs(mm - otro) < 1.0 for otro in cortes):
            raise EscrituraRechazada("at_mm[{}] = {} is repeated".format(indice, mm), 400)
        cortes.append(mm)
    cortes.sort()
    direccion = p1.Subtract(p0).Normalize()
    hitos = [p0] + [p0.Add(direccion.Multiply(mm * MM_TO_FEET)) for mm in cortes] + [p1]
    tramos = []
    for indice in range(len(hitos) - 1):
        a, b = hitos[indice], hitos[indice + 1]
        tramos.append({"segment": indice, "start_mm": punto_a_mm(a), "end_mm": punto_a_mm(b),
                       "length_mm": round(a.DistanceTo(b) * FEET_TO_MM, 1), "a": a, "b": b})
    return {"elem": elem, "element_id": get_element_id_value(elem), "curva": curva, "p0": p0, "p1": p1,
            "length_mm": round(longitud, 1), "cortes": cortes, "tramos": tramos}


def _resumen_tramo(tramo):
    return {"segment": tramo["segment"], "start_mm": tramo["start_mm"], "end_mm": tramo["end_mm"], "length_mm": tramo["length_mm"]}


def dividir_viga(doc, plan):
    """Copia el elemento por tramo (CopyElement) y ajusta LocationCurve; el original queda como primer tramo."""
    elem = plan["elem"]
    nuevos = []
    for tramo in plan["tramos"][1:]:
        copiados = list(DB.ElementTransformUtils.CopyElement(doc, elem.Id, DB.XYZ(0, 0, 0)))
        if not copiados:
            raise EscrituraRechazada("CopyElement returned no element for segment {}".format(tramo["segment"]), 500)
        copia = doc.GetElement(copiados[0])
        copia.Location.Curve = DB.Line.CreateBound(tramo["a"], tramo["b"])
        nuevos.append((tramo, get_element_id_value(copia)))
    primero = plan["tramos"][0]
    elem.Location.Curve = DB.Line.CreateBound(primero["a"], primero["b"])
    return nuevos


# ---------------------------------------------------------------------------
# Rutas
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

    @api.route("/load_steel_profile/", methods=["POST"])
    @requiere_token
    def load_steel_profile(doc, request):
        """Carga tipos de una familia de perfiles: LoadFamilySymbol por tipo (catalogo .txt) o LoadFamily. Acepta `simular`."""

        def cuerpo(ctx):
            plan = planificar_carga_perfil(doc, ctx["data"])
            haria = [{
                "accion": "cargar_tipo" if plan["catalog"] else "cargar_familia", "family": plan["family"],
                "type": tipo, "file_path": plan["ruta"], "catalog": bool(plan["catalog"]),
            } for tipo in (plan["type_names"] or [None])]
            if ctx["simular"]:
                return simulacion(haria, plan={"family": plan["family"], "file_path": plan["ruta"], "catalog_path": plan["catalog"],
                                               "type_names": plan["type_names"], "already_loaded": plan["already_loaded"],
                                               "ya_existian": plan["ya_existian"], "overwrite": plan["overwrite"]},
                                  count=len(plan["type_names"]))
            with transaccion(doc, u"Cargar perfil {}".format(plan["family"])):
                ids, tipos, avisos = cargar_perfil(doc, plan)
            familia = _familia_por_nombre(doc, plan["family"])
            resultado = resultado_creacion(doc, ids)
            resultado.update({
                "family": plan["family"], "family_id": get_element_id_value(familia) if familia is not None else None,
                "file_path": plan["ruta"], "catalog_path": plan["catalog"], "types": tipos,
                "ya_existian": plan["ya_existian"], "avisos": avisos,
                "message": u"Loaded {} type(s) of '{}'".format(len(ids), plan["family"]),
            })
            if familia is None:
                resultado["ok"] = False
                resultado["verificacion"] = {"coincide": False,
                                             "detalle": u"No family named '{}' is in the project after loading".format(plan["family"])}
            return resultado

        return ejecutar(doc, "/load_steel_profile/", request, cuerpo)

    @api.route("/create_steel_frame/", methods=["POST"])
    @requiere_token
    def create_steel_frame(doc, request):
        """MACRO: pilares en las intersecciones de rejillas y vigas entre pilares consecutivos, una transaccion. Acepta `simular`."""

        def cuerpo(ctx):
            data = ctx["data"]
            plan = planificar_portico(doc, data)
            pilares, vigas = plan["pilares"], plan["vigas"]
            comprobar_alcance(data, len(pilares) + len(vigas), "pilares y vigas a crear")
            haria = ([dict(estructural.haria_pilar(p), label=p["label"], mark=p["mark"]) for p in pilares]
                     + [dict(structure.haria_viga(v), **{"from": v["from"], "to": v["to"], "mark": v["mark"]}) for v in vigas])
            if ctx["simular"]:
                return simulacion(haria, plan=plan["plan"], count=len(haria))
            avisos = list(plan["plan"]["warnings"])
            ids_pilares = []
            ids_vigas = []
            with transaccion(doc, u"Portico metalico"):
                for p in pilares:
                    columna = estructural.crear_pilar(doc, p)
                    ids_pilares.append(get_element_id_value(columna))
                    if p["mark"]:
                        motivo = _fijar_marca(columna, p["mark"])
                        if motivo:
                            avisos.append(u"Pilar {}: marca no fijada ({})".format(p["label"], motivo))
                for v in vigas:
                    viga = structure.crear_viga(doc, v)
                    ids_vigas.append(get_element_id_value(viga))
                    if v["mark"]:
                        motivo = _fijar_marca(viga, v["mark"])
                        if motivo:
                            avisos.append(u"Viga {}-{}: marca no fijada ({})".format(v["from"], v["to"], motivo))
            resultado, por_id = _agrupar_creados(doc, ids_pilares + ids_vigas)
            columnas = []
            for p, identificador in zip(pilares, ids_pilares):
                creado = por_id.get(identificador)
                if creado is None:
                    continue
                estructural.describir_pilar(doc, creado)
                creado.update({"label": p["label"], "grid_x": p["grid_x"], "grid_y": p["grid_y"], "mark": p["mark"]})
                columnas.append(creado)
            vigas_creadas = []
            for v, identificador in zip(vigas, ids_vigas):
                creado = por_id.get(identificador)
                if creado is None:
                    continue
                creado.update({"from": v["from"], "to": v["to"], "direction": v["direction"], "mark": v["mark"],
                               "start_mm": punto_a_mm(v["start"]), "end_mm": punto_a_mm(v["end"])})
                vigas_creadas.append(creado)
            resultado["creados"] = {"columns": columnas, "beams": vigas_creadas}
            resultado["creados_ids"] = ids_pilares + ids_vigas
            resultado["count"] = len(ids_pilares) + len(ids_vigas)
            resultado["plan"] = plan["plan"]
            resultado["avisos"] = avisos
            resultado["message"] = u"Created {} column(s) and {} beam(s)".format(len(columnas), len(vigas_creadas))
            return resultado

        return ejecutar(doc, "/create_steel_frame/", request, cuerpo)

    @api.route("/create_bracing/", methods=["POST"])
    @requiere_token
    def create_bracing(doc, request):
        """LOTE: arriostres (StructuralType.Brace) por vano y patron (single, X, V, inverted_V, K). Acepta `simular`."""

        def cuerpo(ctx):
            data = ctx["data"]
            simbolo, planes = planificar_arriostres(doc, data)
            total = sum(len(p["barras"]) for p in planes)
            comprobar_alcance(data, total, "arriostres a crear")
            haria = []
            for plan in planes:
                for indice, (a, b) in enumerate(plan["barras"]):
                    haria.append(_haria_arriostre(plan, simbolo, indice, a, b))
            resumen = [{"bay": p["index"], "pattern": p["pattern"], "level_bottom": get_element_name(p["level_bottom"]),
                        "level_top": get_element_name(p["level_top"]), "start_mm": p["start_mm"], "end_mm": p["end_mm"],
                        "height_mm": p["height_mm"], "braces": len(p["barras"])} for p in planes]
            if ctx["simular"]:
                return simulacion(haria, plan={"bays": resumen, "brace_type": etiqueta_tipo(simbolo),
                                               "counts": {"bays": len(planes), "braces": total}}, count=total)
            creados_por_vano = []
            with transaccion(doc, u"Crear {} arriostres".format(total)):
                _activar(simbolo, doc)
                for plan in planes:
                    ids = []
                    for a, b in plan["barras"]:
                        arriostre = doc.Create.NewFamilyInstance(
                            DB.Line.CreateBound(a, b), simbolo, plan["level_bottom"], DB.Structure.StructuralType.Brace,
                        )
                        ids.append((get_element_id_value(arriostre), a, b))
                    creados_por_vano.append((plan, ids))
            todos = [identificador for _, ids in creados_por_vano for identificador, _, _ in ids]
            resultado, por_id = _agrupar_creados(doc, todos)
            vanos = []
            for plan, ids in creados_por_vano:
                barras = []
                for identificador, a, b in ids:
                    creado = por_id.get(identificador)
                    if creado is None:
                        continue
                    creado.update({"start_mm": punto_a_mm(a), "end_mm": punto_a_mm(b),
                                   "length_mm": round(a.DistanceTo(b) * FEET_TO_MM, 1)})
                    barras.append(creado)
                vanos.append({"bay": plan["index"], "pattern": plan["pattern"],
                              "level_bottom": get_element_name(plan["level_bottom"]),
                              "level_top": get_element_name(plan["level_top"]), "braces": barras})
            resultado["creados"] = vanos
            resultado["creados_ids"] = todos
            resultado["count"] = len(todos)
            resultado["plan"] = {"bays": resumen, "counts": {"bays": len(planes), "braces": total}}
            resultado["message"] = u"Created {} brace(s) in {} bay(s)".format(len(todos), len(planes))
            return resultado

        return ejecutar(doc, "/create_bracing/", request, cuerpo)

    @api.route("/create_truss/", methods=["POST"])
    @requiere_token
    def create_truss(doc, request):
        """LOTE: cerchas (Truss.Create sobre un SketchPlane en el nivel). Acepta `simular`."""

        def cuerpo(ctx):
            data = ctx["data"]
            planes = planificar_cerchas(doc, data)
            comprobar_alcance(data, len(planes), "cerchas a crear")
            haria = [_haria_cercha(p) for p in planes]
            if ctx["simular"]:
                return simulacion(haria, plan={"counts": {"trusses": len(planes)}, "trusses": haria}, count=len(planes))
            ids = []
            with transaccion(doc, u"Crear {} cerchas".format(len(planes))):
                planos = {}
                for plan in planes:
                    clave = get_element_id_value(plan["nivel"])
                    if clave not in planos:
                        planos[clave] = DB.SketchPlane.Create(doc, plan["nivel"].Id)
                    cercha = DB.Structure.Truss.Create(
                        doc, plan["tipo"].Id, planos[clave].Id, DB.Line.CreateBound(plan["inicio"], plan["fin"]),
                    )
                    ids.append(get_element_id_value(cercha))
            resultado, por_id = _agrupar_creados(doc, ids)
            for plan, identificador in zip(planes, ids):
                creado = por_id.get(identificador)
                if creado is not None:
                    creado.update({"index": plan["index"], "truss_type": etiqueta_tipo(plan["tipo"]),
                                   "start_mm": punto_a_mm(plan["inicio"]), "end_mm": punto_a_mm(plan["fin"])})
            resultado["creados_ids"] = ids
            resultado["plan"] = {"counts": {"trusses": len(planes)}}
            resultado["message"] = u"Created {} truss(es)".format(len(ids))
            return resultado

        return ejecutar(doc, "/create_truss/", request, cuerpo)

    @api.route("/set_structural_properties/", methods=["POST"])
    @requiere_token
    def set_structural_properties(doc, request):
        """LOTE: liberaciones, justificaciones, desfases, rotacion, extensiones, analyze_as y uso, por BuiltInParameter. Acepta `simular`."""

        def cuerpo(ctx):
            data = ctx["data"]
            ids = data.get("element_ids")
            if isinstance(ids, (int, float)) and not isinstance(ids, bool):
                ids = [ids]
            if not isinstance(ids, (list, tuple)) or not ids:
                raise EscrituraRechazada("element_ids is required and must not be empty", 400)
            enteros = []
            for identificador in ids:
                try:
                    entero = int(identificador)
                except (TypeError, ValueError):
                    raise EscrituraRechazada("element id {!r} is not an integer".format(identificador), 400)
                if entero not in enteros:
                    enteros.append(entero)
            propiedades = normalizar_propiedades(data)
            no_disponibles = []
            fase1, fase2, liberaciones = _operaciones_propiedades(enteros, propiedades, no_disponibles)
            comprobar_alcance(data, len(fase1) + len(fase2), "propiedades (elementos x propiedades)")
            planes, fallidos_crudos = resolver_parametros(doc, fase1)
            fallidos = []
            analiticos = []
            sin_parametro = {}
            for fallo in _traducir_fallidos(fallidos_crudos):
                if fallo["property"] in liberaciones and fallo["motivo"] == "parameter not found":
                    sin_parametro.setdefault(fallo["element_id"], set()).add(liberaciones[fallo["property"]][0])
                    continue
                fallidos.append(fallo)
            for element_id, prefijos in sorted(sin_parametro.items()):
                elem = doc.GetElement(make_element_id(element_id))
                plan_analitico = _plan_analitico(doc, elem, element_id, liberaciones, prefijos) if elem is not None else None
                if plan_analitico is None:
                    for clave, (prefijo, _, _) in liberaciones.items():
                        if prefijo in prefijos:
                            fallidos.append({"element_id": element_id, "property": clave,
                                             "builtin": "STRUCTURAL_{}_RELEASE_TYPE".format(prefijo),
                                             "motivo": "parameter not found and no analytical member associated"})
                    continue
                analiticos.append(plan_analitico)
            # los componentes FX..MZ de un elemento cuya liberacion va por el AnalyticalMember no se fijan por parametro
            fase2 = [op for op in fase2 if op[0] not in sin_parametro]
            if not planes and not analiticos and not fase2:
                raise EscrituraRechazada("No property can be set on the given elements", 400,
                                         {"fallidos": fallidos, "no_disponibles": no_disponibles})
            elementos = sorted(set([p["element_id"] for p in planes] + [a["acciones"][0]["element_id"] for a in analiticos]))

            def propiedad_de(plan):
                return _PROPIEDAD_POR_BIP.get(plan["parameter_name"], plan["parameter_name"])

            if ctx["simular"]:
                haria = [{
                    "accion": "set_parameter", "element_id": p["element_id"], "property": propiedad_de(p),
                    "builtin": p["parameter_name"], "parameter_label": p["parameter_label"],
                    "antes": p["antes"], "despues": despues_simulado(p["param"], p["value"], p["convertido"]),
                } for p in planes]
                for element_id, nombre, valor, _ in fase2:
                    haria.append({"accion": "set_parameter", "element_id": element_id, "property": _PROPIEDAD_POR_BIP.get(nombre),
                                  "builtin": nombre, "despues": bool(valor),
                                  "nota": u"se fija despues de poner el tipo de liberacion en definido por el usuario"})
                for plan_analitico in analiticos:
                    haria.extend(_haria_analitico(plan_analitico))
                return simulacion(haria, count=len(haria), elements=len(elementos), fallidos=fallidos,
                                  no_disponibles=no_disponibles)

            antes_analitico = {}
            for plan_analitico in analiticos:
                for accion in plan_analitico["acciones"]:
                    antes_analitico.setdefault(_texto(accion["element_id"]), {})[accion["property"]] = accion["antes"]
            planes2 = []
            with transaccion(doc, u"Propiedades estructurales ({} elementos)".format(len(elementos))):
                for plan in planes:
                    if plan["aplicar"]:
                        plan["param"].Set(plan["convertido"])
                if fase2:
                    planes2, fallidos2 = resolver_parametros(doc, fase2)
                    fallidos.extend(_traducir_fallidos(fallidos2))
                    for plan in planes2:
                        plan["param"].Set(plan["convertido"])
                for plan_analitico in analiticos:
                    _aplicar_analitico(plan_analitico)

            cambios = []
            desajustes = []
            antes = {}
            despues = {}
            for plan in planes + planes2:
                clave = _texto(plan["element_id"])
                propiedad = propiedad_de(plan)
                valor_despues = valor_parametro(plan["param"], doc)
                coincide = coincide_valor(plan["param"], plan["value"])
                cambios.append({"element_id": plan["element_id"], "property": propiedad, "builtin": plan["parameter_name"],
                                "parameter_label": plan["parameter_label"], "antes": plan["antes"], "despues": valor_despues,
                                "coincide": bool(coincide)})
                etiqueta = propiedad if plan["parameter_name"].endswith("_RELEASE_TYPE") or propiedad not in ("start_release", "end_release") \
                    else u"{}.{}".format(propiedad, plan["parameter_name"].rsplit("_", 1)[-1])
                antes.setdefault(clave, {})[etiqueta] = plan["antes"]
                despues.setdefault(clave, {})[etiqueta] = valor_despues
                if not coincide:
                    desajustes.append(u"element {} {}: requested {!r}, read back {!r}".format(
                        plan["element_id"], propiedad, plan["value"], valor_despues))
            for plan_analitico in analiticos:
                for accion in plan_analitico["acciones"]:
                    clave = _texto(accion["element_id"])
                    leido = liberaciones_analiticas(plan_analitico["miembro"], accion["inicio"])
                    antes.setdefault(clave, {})[accion["property"]] = antes_analitico[clave][accion["property"]]
                    despues.setdefault(clave, {})[accion["property"]] = leido
                    esperado = TIPOS_LIBERACION[accion["contenido"]] if accion["modo"] == "type" else "user_defined"
                    coincide = bool(leido) and leido.get("type") == esperado
                    if coincide and accion["modo"] == "components":
                        coincide = all(leido.get(c) == v for c, v in accion["contenido"].items())
                    cambios.append({"element_id": accion["element_id"], "property": accion["property"],
                                    "source": "AnalyticalMember", "analytical_member_id": plan_analitico["analytical_id"],
                                    "antes": antes_analitico[clave][accion["property"]], "despues": leido, "coincide": coincide})
                    if not coincide:
                        desajustes.append(u"element {} {}: analytical member read back {!r}".format(
                            accion["element_id"], accion["property"], leido))
            resultado = {
                "count": len(elementos), "elements": elementos, "properties_set": len(cambios), "changes": cambios,
                "antes": antes, "despues": despues, "fallidos": fallidos, "no_disponibles": no_disponibles,
                "ok": not desajustes,
                "message": u"Set {} structural propert(y/ies) on {} element(s), {} failed".format(
                    len(cambios), len(elementos), len(fallidos)),
            }
            resultado["verificacion"] = ({"coincide": True} if not desajustes else
                                         {"coincide": False, "detalle": u"; ".join(desajustes)})
            return resultado

        return ejecutar(doc, "/set_structural_properties/", request, cuerpo)

    @api.route("/create_steel_connection/", methods=["POST"])
    @requiere_token
    def create_steel_connection(doc, request):
        """LOTE: StructuralConnectionHandler.Create por conexion; 409 no_soportado sin el modulo. Acepta `simular`."""

        def cuerpo(ctx):
            data = ctx["data"]
            handler, planes = planificar_conexiones(doc, data)
            comprobar_alcance(data, len(planes), "conexiones a crear")
            haria = [{
                "accion": "conectar", "element_type": "steel_connection", "index": p["index"], "element_ids": p["ids"],
                "connection_type": get_element_name(p["tipo"]),
                "approval_status": get_element_name(p["aprobacion"]) if p["aprobacion"] is not None else None,
            } for p in planes]
            if ctx["simular"]:
                return simulacion(haria, plan={"counts": {"connections": len(planes)}}, count=len(planes))
            ids = []
            avisos = []
            with transaccion(doc, u"Crear {} conexiones".format(len(planes))):
                for plan in planes:
                    conexion = handler.Create(doc, _lista_ids([e.Id for e in plan["elementos"]]), plan["tipo"].Id)
                    if plan["aprobacion"] is not None:
                        try:
                            conexion.ApprovalStatus = plan["aprobacion"].Id
                        except Exception as error:
                            avisos.append(u"connections[{}]: ApprovalStatus no se pudo fijar: {}".format(plan["index"], error))
                    ids.append(get_element_id_value(conexion))
            resultado, por_id = _agrupar_creados(doc, ids)
            for plan, identificador in zip(planes, ids):
                creado = por_id.get(identificador)
                if creado is None:
                    continue
                creado.update({"index": plan["index"], "element_ids": plan["ids"], "connection_type": get_element_name(plan["tipo"])})
                if plan["aprobacion"] is not None:
                    conexion = doc.GetElement(make_element_id(identificador))
                    try:
                        estado = doc.GetElement(conexion.ApprovalStatus)
                        creado["approval_status"] = get_element_name(estado) if estado is not None else None
                    except Exception:
                        creado["approval_status"] = None
            resultado["creados_ids"] = ids
            resultado["avisos"] = avisos
            resultado["plan"] = {"counts": {"connections": len(planes)}}
            resultado["message"] = u"Created {} steel connection(s)".format(len(ids))
            return resultado

        return ejecutar(doc, "/create_steel_connection/", request, cuerpo)

    @api.route("/add_plate/", methods=["POST"])
    @requiere_token
    def add_plate_or_stiffener(doc, request):
        """Placas o rigidizadores (familia alojada en cara o de punto) en posiciones de una viga o pilar. Acepta `simular`."""

        def cuerpo(ctx):
            data = ctx["data"]
            plan = planificar_placas(doc, data)
            comprobar_alcance(data, len(plan["puntos"]), "placas a colocar")
            haria = [{
                "accion": "colocar", "element_type": "plate", "host_id": plan["host_id"], "family": nombre_familia(plan["symbol"]),
                "type": get_element_name(plan["symbol"]), "face": plan["face"] if plan["en_cara"] else None,
                "hosted_on_face": plan["en_cara"], "placement_type": plan["placement_type"],
                "position_mm": round(mm, 1), "point_mm": punto_a_mm(punto),
            } for mm, punto in plan["puntos"]]
            if ctx["simular"]:
                return simulacion(haria, plan={"host_length_mm": plan["length_mm"], "counts": {"plates": len(haria)}}, count=len(haria))
            ids = []
            with transaccion(doc, u"Colocar {} {} en {}".format(len(plan["puntos"]), nombre_familia(plan["symbol"]), plan["host_id"])):
                for mm, punto in plan["puntos"]:
                    instancia = colocar_placa(doc, plan, punto)
                    ids.append((get_element_id_value(instancia), mm, punto))
            resultado, por_id = _agrupar_creados(doc, [i for i, _, _ in ids])
            for identificador, mm, punto in ids:
                creado = por_id.get(identificador)
                if creado is not None:
                    creado.update({"position_mm": round(mm, 1), "point_mm": punto_a_mm(punto)})
            resultado["host"] = describir_elemento(doc, plan["host"])
            resultado["face"] = plan["face"] if plan["en_cara"] else None
            resultado["hosted_on_face"] = plan["en_cara"]
            resultado["creados_ids"] = [i for i, _, _ in ids]
            resultado["message"] = u"Placed {} instance(s) of {}: {} on host {}".format(
                len(ids), nombre_familia(plan["symbol"]), get_element_name(plan["symbol"]), plan["host_id"])
            return resultado

        return ejecutar(doc, "/add_plate/", request, cuerpo)

    @api.route("/split_beam/", methods=["POST"])
    @requiere_token
    def split_beam(doc, request):
        """Divide una viga recta en tramos (CopyElement + LocationCurve); el original queda como primer tramo. Acepta `simular`."""

        def cuerpo(ctx):
            data = ctx["data"]
            plan = planificar_division(doc, data)
            comprobar_alcance(data, len(plan["tramos"]), "tramos a crear")
            resumen = [_resumen_tramo(t) for t in plan["tramos"]]
            haria = [{"accion": "dividir", "element_id": plan["element_id"], "length_mm": plan["length_mm"],
                      "at_mm": plan["cortes"], "segments": resumen}]
            if ctx["simular"]:
                return simulacion(haria, plan={"counts": {"segments": len(resumen), "new_elements": len(resumen) - 1}},
                                  avisos=list(AVISOS_DIVIDIR))
            with transaccion(doc, u"Dividir viga {}".format(plan["element_id"])):
                nuevos = dividir_viga(doc, plan)
            resultado, por_id = _agrupar_creados(doc, [identificador for _, identificador in nuevos])
            for tramo, identificador in nuevos:
                creado = por_id.get(identificador)
                if creado is not None:
                    creado.update(_resumen_tramo(tramo))
            primero = plan["tramos"][0]
            despues = _curva_ubicacion(plan["elem"])
            resultado["original"] = {
                "id": plan["element_id"],
                "antes": {"start_mm": punto_a_mm(plan["p0"]), "end_mm": punto_a_mm(plan["p1"]), "length_mm": plan["length_mm"]},
                "despues": {"start_mm": punto_a_mm(despues.GetEndPoint(0)), "end_mm": punto_a_mm(despues.GetEndPoint(1)),
                            "length_mm": round(despues.Length * FEET_TO_MM, 1)} if despues is not None else None,
                "segment": 0,
            }
            if resultado["original"]["despues"] is not None and abs(resultado["original"]["despues"]["length_mm"] - primero["length_mm"]) > 1.0:
                resultado["ok"] = False
                resultado["verificacion"] = {"coincide": False, "detalle": u"the original beam is {} mm long instead of {} mm".format(
                    resultado["original"]["despues"]["length_mm"], primero["length_mm"])}
            resultado["segments"] = resumen
            resultado["creados_ids"] = [identificador for _, identificador in nuevos]
            resultado["avisos"] = list(AVISOS_DIVIDIR)
            resultado["message"] = u"Split beam {} into {} segments ({} new)".format(plan["element_id"], len(resumen), len(nuevos))
            return resultado

        return ejecutar(doc, "/split_beam/", request, cuerpo)

    logger.info("Acero routes registered successfully")
