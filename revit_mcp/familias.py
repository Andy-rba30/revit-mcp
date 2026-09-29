# -*- coding: UTF-8 -*-
"""
Editor de familias para Revit MCP (0.6.0, entrega 2c, bloque C).

Documento de familia:
  POST /family/open/        template + name (Application.NewFamilyDocument) | file_path
                            (OpenDocumentFile) | family_name (doc.EditFamily). Devuelve el resumen.
  POST /family/info/        family_doc (resumen: parametros, tipos, planos, solidos, conectores, vistas);
                            sin family_doc: documentos abiertos por el MCP y, con include_templates, las .rft
  POST /family/save/        family_doc, file_path, overwrite (SaveAs)
  POST /family/load/        family_doc | file_path, overwrite_parameters (LoadFamily en el proyecto)
  POST /family/close/       family_doc, save (Close; solo documentos abiertos por el MCP y no activos)

Escritura en el documento de familia (todas con `simular`, patron escritura.ejecutar_familia:
mcp_log.jsonl del proyecto, 409 si IsModifiable, copia del .rfa si esta guardado,
una transaccion "IA: ..." en el documento de familia por llamada):
  POST /family/parameters/         parameters[] {name, data_type, group, is_instance, formula, shared_parameter_guid}
  POST /family/reference_planes/   planes[] {name, origin_mm, direction, view, is_reference}
  POST /family/dimensions/         dimensions[] {reference_planes[], parameter, view, equal, offset_mm}
  POST /family/solids/             solids[] {kind (extrusion|sweep|revolution|blend), profile, sketch_plane,
                                   start_mm, end_mm, is_void, lock_ends_to, material_parameter, ...}
  POST /family/locks/              locks[] {solid_id, face, reference_plane, view}
  POST /family/types/              types[] {type_name, values, create_if_missing}
  POST /family/connectors/         connectors[] {domain, solid_id, face, system_type, size_mm}

Las macros /family/validate/ y /family/build/ viven en familias_spec.py.

Reglas de la entrega:
  - El documento de familia se identifica por `family_doc` (titulo, o el `name` con que
    lo abrio el MCP) entre Application.Documents con IsFamilyDocument. Los que abre
    el MCP quedan en DOCUMENTOS_ABIERTOS para poder cerrarlos.
  - doc.EditFamily no se llama con una transaccion abierta en el proyecto (409), ni
    sobre familias in situ o no editables (400).
  - API 2024+: FamilyManager.AddParameter(nombre, GroupTypeId, SpecTypeId, is_instance);
    los grupos se dan como GroupTypeId ("Geometry", "Materials", "Data"...).
  - Referencias de caras: Options.ComputeReferences = True e IncludeNonVisibleObjects =
    True en una vista donde la cara sea visible.
  - Idioma: plantillas por nombre de archivo en Application.FamilyTemplatePath; vistas
    por ViewType (y nivel / direccion), nunca por "Ref. Level" ni "Front".

Compatibilidad: IronPython 2.7.
"""

from utils import (
    get_element_name, get_element_id_value, make_element_id, xyz_desde_mm, punto_a_mm,
    elevacion_interna, MM_TO_FEET, FEET_TO_MM,
)
from seguridad import requiere_token
from escritura import (
    ejecutar, ejecutar_familia, transaccion, simulacion, EscrituraRechazada, resultado_creacion,
    comprobar_alcance, describir_elemento, datos_peticion, titulo_documento, ruta_documento,
    esperar_copia_pendiente, nombre_transaccion,
)
from navegacion import _responder
from pyrevit import routes, revit, DB
import clr
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

# Documentos de familia abiertos por el MCP: titulo -> {"doc", "name", "aliases", "origen"}
DOCUMENTOS_ABIERTOS = {}

MAX_PLANTILLAS = 80
MAX_FAMILIAS_DISPONIBLES = 40
EXTENSION_FAMILIA = ".rfa"
EXTENSION_PLANTILLA = ".rft"
LONGITUD_PLANO_MM = 6000.0
DESFASE_COTA_MM = 500.0

# data_type del contrato -> ruta dentro de DB.SpecTypeId (o "family_type")
TIPOS_DATO = {
    "length": ("Length",),
    "number": ("Number",),
    "integer": ("Int", "Integer"),
    "text": ("String", "Text"),
    "yes_no": ("Boolean", "YesNo"),
    "material": ("Reference", "Material"),
    "angle": ("Angle",),
    "area": ("Area",),
    "volume": ("Volume",),
}
_ALIAS_TIPOS_DATO = {
    "longitud": "length", "numero": "number", "número": "number", "entero": "integer", "texto": "text",
    "string": "text", "bool": "yes_no", "boolean": "yes_no", "si_no": "yes_no", "sí_no": "yes_no", "yesno": "yes_no",
    "angulo": "angle", "ángulo": "angle", "superficie": "area", "volumen": "volume",
}
# Factores de las unidades del contrato a las internas por tipo de dato
FACTORES = {"length": MM_TO_FEET, "area": MM_TO_FEET * MM_TO_FEET, "volume": MM_TO_FEET ** 3,
            "angle": math.pi / 180.0}
UNIDADES = {"length": "mm", "area": "mm2", "volume": "mm3", "angle": "deg"}
# ELEM_REFERENCE_NAME (Is Reference) -> valor de FamilyInstanceReferenceType. Verificado en Revit 2027
# (0.6.2): la plantilla trae "Centro (Izquierda/Derecha)" = 1, "Centro (Frontal/Posterior)" = 4 y su
# plano horizontal = 12. La tabla de 0.6.0 (0 = no referencia, 1 = fuerte, 3 = izquierda...) estaba desplazada.
ES_REFERENCIA = {
    "left": 0, "center_left_right": 1, "right": 2, "front": 3, "center_front_back": 4, "back": 5,
    "bottom": 6, "center_elevation": 7, "top": 8, "not_a_reference": 12, "strong": 13, "weak": 14,
}
_ALIAS_REFERENCIA = {
    "no": "not_a_reference", "none": "not_a_reference", "false": "not_a_reference", "fuerte": "strong",
    "true": "strong", "debil": "weak", "débil": "weak", "izquierda": "left", "centro": "center_left_right",
    "center": "center_left_right", "derecha": "right", "delante": "front", "frente": "front", "detras": "back",
    "detrás": "back", "abajo": "bottom", "inferior": "bottom", "arriba": "top", "superior": "top",
    "centro_elevacion": "center_elevation", "centro_delante_detras": "center_front_back",
}
DIRECCIONES_PLANO = ("x", "y", "horizontal")
_ALIAS_DIRECCION = {"vertical_x": "x", "vertical_y": "y", "z": "horizontal", "horizontal": "horizontal",
                    "front_back": "x", "left_right": "y", "delante_detras": "x", "izquierda_derecha": "y"}
CARAS = {"top": (0, 0, 1), "bottom": (0, 0, -1), "right": (1, 0, 0), "left": (-1, 0, 0),
         "back": (0, 1, 0), "front": (0, -1, 0)}
_ALIAS_CARA = {"superior": "top", "inferior": "bottom", "derecha": "right", "izquierda": "left",
               "delante": "front", "frente": "front", "detras": "back", "detrás": "back", "end": "top", "start": "bottom"}
KINDS_SOLIDO = ("extrusion", "sweep", "revolution", "blend")
_ALIAS_KIND = {"extrusión": "extrusion", "extrude": "extrusion", "barrido": "sweep", "revolve": "revolution",
               "revolución": "revolution", "revolucion": "revolution", "fundido": "blend"}
DOMINIOS = {"hvac": ("Mechanical", "DuctSystemType", "CreateDuctConnector"),
            "piping": ("Plumbing", "PipeSystemType", "CreatePipeConnector"),
            "electrical": ("Electrical", "ElectricalSystemType", "CreateElectricalConnector")}
_ALIAS_DOMINIO = {"duct": "hvac", "conducto": "hvac", "mecanico": "hvac", "mecánico": "hvac", "pipe": "piping",
                  "tuberia": "piping", "tubería": "piping", "electrico": "electrical", "eléctrico": "electrical",
                  "electricidad": "electrical"}


# ---------------------------------------------------------------------------
# Utilidades pequenas
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


def _es_verdadero(valor, por_defecto=False):
    if valor is None:
        return por_defecto
    if isinstance(valor, _cadena):
        return valor.strip().lower() in ("1", "true", "si", "sí", "yes")
    return bool(valor)


def _numero(valor, etiqueta, status=400, extra=None):
    if isinstance(valor, bool):
        raise EscrituraRechazada(u"{} must be a number".format(etiqueta), status, extra)
    try:
        return float(valor)
    except (TypeError, ValueError):
        raise EscrituraRechazada(u"{} must be a number; got {!r}".format(etiqueta, valor), status, extra)


def _lista(valor, etiqueta):
    if valor is None:
        return []
    if isinstance(valor, dict) or isinstance(valor, _cadena):
        return [valor]
    if not isinstance(valor, (list, tuple)):
        raise EscrituraRechazada(u"{} must be a list".format(etiqueta), 400)
    return list(valor)


def _normalizar(texto):
    """Minusculas sin tildes, para comparar nombres de archivo y alias."""
    import unicodedata

    texto = _texto_seguro(texto).strip().lower()
    try:
        texto = unicodedata.normalize("NFD", texto)
        texto = u"".join(c for c in texto if unicodedata.category(c) != "Mn")
    except Exception:
        pass
    return texto


def _mm(valor_pies):
    return round(float(valor_pies) * FEET_TO_MM, 2)


def _es_invalido(eid):
    try:
        return eid is None or eid == DB.ElementId.InvalidElementId
    except Exception:
        return True


def _xyz(punto, etiqueta):
    if not isinstance(punto, dict):
        raise EscrituraRechazada(u"{} must be an object {{x, y, z}} in mm".format(etiqueta), 400)
    try:
        return xyz_desde_mm(punto)
    except ValueError as error:
        raise EscrituraRechazada(u"{}: {}".format(etiqueta, error), 400)


def _vector(valor, etiqueta):
    """XYZ unitario a partir de {"x","y","z"} o de un nombre de cara/eje."""
    if isinstance(valor, dict):
        try:
            v = DB.XYZ(float(valor.get("x", 0)), float(valor.get("y", 0)), float(valor.get("z", 0)))
        except (TypeError, ValueError):
            raise EscrituraRechazada(u"{} must be {{x, y, z}}".format(etiqueta), 400)
        if v.GetLength() < 1e-9:
            raise EscrituraRechazada(u"{} cannot be a zero vector".format(etiqueta), 400)
        return v.Normalize()
    clave = _normalizar(valor)
    clave = _ALIAS_CARA.get(clave, clave)
    if clave in CARAS:
        c = CARAS[clave]
        return DB.XYZ(c[0], c[1], c[2])
    if clave in ("x", "y", "z"):
        return DB.XYZ(1 if clave == "x" else 0, 1 if clave == "y" else 0, 1 if clave == "z" else 0)
    raise EscrituraRechazada(u"{}: unknown direction '{}' (use top, bottom, left, right, front, back or {{x, y, z}})".format(
        etiqueta, valor), 400)


def _vector_mm(v):
    return {"x": round(v.X, 4), "y": round(v.Y, 4), "z": round(v.Z, 4)}


# ---------------------------------------------------------------------------
# Documentos de familia
# ---------------------------------------------------------------------------
def aplicacion(doc):
    try:
        return doc.Application
    except Exception:
        return None


def documentos_familia(doc):
    """Documentos de familia abiertos en Revit (Application.Documents con IsFamilyDocument)."""
    app = aplicacion(doc)
    familias = []
    try:
        documentos = list(app.Documents) if app is not None else []
    except Exception:
        documentos = []
    for documento in documentos:
        try:
            if documento.IsFamilyDocument:
                familias.append(documento)
        except Exception:
            continue
    return familias


def _mismo_documento(a, b):
    if a is None or b is None:
        return False
    if a is b:
        return True
    try:
        return a.Equals(b)
    except Exception:
        pass
    try:
        return titulo_documento(a) == titulo_documento(b) and ruta_documento(a) == ruta_documento(b)
    except Exception:
        return False


def _limpiar_cerrados(doc):
    """Retira del registro los documentos que ya no estan abiertos en Revit."""
    abiertos = documentos_familia(doc)
    for titulo in list(DOCUMENTOS_ABIERTOS.keys()):
        entrada = DOCUMENTOS_ABIERTOS[titulo]
        if not any(_mismo_documento(entrada["doc"], d) for d in abiertos):
            del DOCUMENTOS_ABIERTOS[titulo]


def registrar_documento(doc_familia, name=None, origen=None):
    """Guarda un documento abierto por el MCP (titulo y alias con los que se puede pedir)."""
    titulo = titulo_documento(doc_familia)
    entrada = DOCUMENTOS_ABIERTOS.get(titulo) or {"doc": doc_familia, "aliases": set(), "name": None, "origen": origen}
    entrada["doc"] = doc_familia
    if name:
        entrada["name"] = _texto_seguro(name)
        entrada["aliases"].add(_texto_seguro(name))
    entrada["aliases"].add(titulo)
    if origen:
        entrada["origen"] = origen
    DOCUMENTOS_ABIERTOS[titulo] = entrada
    return titulo


def _actualizar_titulo(doc_familia, titulo_anterior):
    """Tras SaveAs el titulo cambia: se reindexa conservando los alias."""
    nuevo = titulo_documento(doc_familia)
    entrada = DOCUMENTOS_ABIERTOS.pop(titulo_anterior, None)
    if entrada is None:
        entrada = {"doc": doc_familia, "aliases": set(), "name": None, "origen": None}
    entrada["aliases"].add(titulo_anterior)
    entrada["aliases"].add(nuevo)
    entrada["doc"] = doc_familia
    DOCUMENTOS_ABIERTOS[nuevo] = entrada
    return nuevo


def nombres_disponibles(doc):
    nombres = []
    for documento in documentos_familia(doc):
        nombres.append(titulo_documento(documento))
    for entrada in DOCUMENTOS_ABIERTOS.values():
        for alias in entrada["aliases"]:
            if alias not in nombres:
                nombres.append(alias)
    return nombres


def resolver_documento(doc, data, clave="family_doc"):
    """Documento de familia por titulo (o alias del MCP). 400 sin `family_doc`, 404 si no esta abierto."""
    pedido = _texto_seguro(data.get(clave) if isinstance(data, dict) else None).strip()
    if not pedido:
        raise EscrituraRechazada(u"{} is required (title of an open family document; see family_info)".format(clave),
                                 400, {"available_family_docs": nombres_disponibles(doc)})
    _limpiar_cerrados(doc)
    candidatos = [pedido]
    if pedido.lower().endswith(EXTENSION_FAMILIA):
        candidatos.append(pedido[:-len(EXTENSION_FAMILIA)])
    else:
        candidatos.append(pedido + EXTENSION_FAMILIA)
    for documento in documentos_familia(doc):
        if titulo_documento(documento) in candidatos:
            return documento
    for entrada in DOCUMENTOS_ABIERTOS.values():
        if any(c in entrada["aliases"] for c in candidatos):
            return entrada["doc"]
    normalizados = [_normalizar(c) for c in candidatos]
    for documento in documentos_familia(doc):
        if _normalizar(titulo_documento(documento)) in normalizados:
            return documento
    raise EscrituraRechazada(u"Family document '{}' is not open".format(pedido), 404,
                             {"available_family_docs": nombres_disponibles(doc)})


def _resolver(doc):
    return lambda data: resolver_documento(doc, data)


def abierto_por_mcp(doc_familia):
    titulo = titulo_documento(doc_familia)
    entrada = DOCUMENTOS_ABIERTOS.get(titulo)
    return entrada is not None and _mismo_documento(entrada["doc"], doc_familia)


# ---------------------------------------------------------------------------
# Plantillas .rft
# ---------------------------------------------------------------------------
def carpeta_plantillas(doc):
    app = aplicacion(doc)
    try:
        return _texto_seguro(app.FamilyTemplatePath) if app is not None else u""
    except Exception:
        return u""


def listar_plantillas(doc, contiene=None, maximo=MAX_PLANTILLAS):
    """[.rft] de Application.FamilyTemplatePath (y sus subcarpetas), con la ruta completa."""
    carpeta = carpeta_plantillas(doc)
    plantillas = []
    truncado = False
    if not carpeta or not os.path.isdir(carpeta):
        return carpeta, plantillas, truncado
    filtro = _normalizar(contiene) if contiene else None
    for raiz, carpetas, archivos in os.walk(carpeta):
        carpetas.sort()
        for nombre in sorted(archivos):
            if not nombre.lower().endswith(EXTENSION_PLANTILLA):
                continue
            if filtro and filtro not in _normalizar(nombre):
                continue
            if len(plantillas) >= maximo:
                truncado = True
                return carpeta, plantillas, truncado
            plantillas.append({"name": nombre, "path": os.path.join(raiz, nombre),
                               "folder": os.path.relpath(raiz, carpeta) if raiz != carpeta else u""})
    return carpeta, plantillas, truncado


def resolver_plantilla(doc, template):
    """Ruta de la plantilla: ruta absoluta existente, o nombre de archivo en FamilyTemplatePath."""
    pedido = _texto_seguro(template).strip()
    if not pedido:
        raise EscrituraRechazada(u"template is required (.rft file name in FamilyTemplatePath, or a full path)", 400)
    if os.path.isabs(pedido) or os.sep in pedido or "/" in pedido:
        if os.path.isfile(pedido):
            return pedido
        raise EscrituraRechazada(u"Family template not found: {}".format(pedido), 404,
                                 {"family_template_path": carpeta_plantillas(doc)})
    nombre = pedido if pedido.lower().endswith(EXTENSION_PLANTILLA) else pedido + EXTENSION_PLANTILLA
    carpeta, plantillas, _ = listar_plantillas(doc, maximo=5000)
    for plantilla in plantillas:
        if plantilla["name"] == nombre:
            return plantilla["path"]
    objetivo = _normalizar(nombre)
    for plantilla in plantillas:
        if _normalizar(plantilla["name"]) == objetivo:
            return plantilla["path"]
    _, disponibles, truncado = listar_plantillas(doc, contiene=None)
    raise EscrituraRechazada(
        u"Family template '{}' not found in {}".format(nombre, carpeta or u"(FamilyTemplatePath is empty)"), 404,
        {"family_template_path": carpeta, "available_templates": [p["name"] for p in disponibles],
         "available_truncated": truncado},
    )


# ---------------------------------------------------------------------------
# Vistas, niveles y planos de referencia del documento de familia
# ---------------------------------------------------------------------------
def niveles_familia(doc_familia):
    try:
        niveles = list(DB.FilteredElementCollector(doc_familia).OfClass(DB.Level).ToElements())
    except Exception:
        niveles = []
    return sorted(niveles, key=elevacion_interna)


def vistas_familia(doc_familia):
    try:
        vistas = DB.FilteredElementCollector(doc_familia).OfClass(DB.View).WhereElementIsNotElementType().ToElements()
    except Exception:
        return []
    lista = []
    for vista in vistas:
        try:
            if vista.IsTemplate:
                continue
        except Exception:
            pass
        try:
            tipo = _texto_seguro(vista.ViewType).split(".")[-1]
        except Exception:
            tipo = u"Undefined"
        if tipo in ("Internal", "ProjectBrowser", "SystemBrowser", "Undefined", "Report", "Schedule"):
            continue
        lista.append(vista)
    return lista


def _direccion_vista(vista):
    try:
        return vista.ViewDirection
    except Exception:
        return None


def _nombre_direccion(vista):
    """front/back/left/right/top de una vista de alzado o de planta por ViewDirection (no por el nombre)."""
    d = _direccion_vista(vista)
    if d is None:
        return None
    if abs(d.Z) > 0.9:
        return "top" if d.Z > 0 else "bottom"
    if abs(d.Y) >= abs(d.X):
        return "front" if d.Y < 0 else "back"
    return "left" if d.X < 0 else "right"


def describir_vista(vista):
    nivel = None
    try:
        if vista.GenLevel is not None:
            nivel = get_element_name(vista.GenLevel)
    except Exception:
        nivel = None
    try:
        tipo = _texto_seguro(vista.ViewType).split(".")[-1]
    except Exception:
        tipo = None
    return {"id": get_element_id_value(vista), "view_type": tipo, "name": get_element_name(vista),
            "level": nivel, "direction": _nombre_direccion(vista)}


def _tipo_vista(vista):
    try:
        return _texto_seguro(vista.ViewType).split(".")[-1]
    except Exception:
        return u""


def _nivel_familia(doc_familia, nombre):
    niveles = niveles_familia(doc_familia)
    if nombre:
        for nivel in niveles:
            if get_element_name(nivel) == nombre:
                return nivel
        for nivel in niveles:
            if _normalizar(get_element_name(nivel)) == _normalizar(nombre):
                return nivel
    return niveles[0] if niveles else None


def resolver_vista(doc_familia, pedido, etiqueta="view", por_defecto=None):
    """Vista por id, o por {"view_type": "FloorPlan", "level": ...} / {"view_type": "Elevation", "direction": "front"}.

    Sin `pedido`, `por_defecto` ("plan" o "elevation") elige la primera planta del primer
    nivel o el primer alzado. Nunca por el nombre visible."""
    vistas = vistas_familia(doc_familia)
    if pedido is None or pedido == u"":
        if por_defecto is None:
            raise EscrituraRechazada(u"{} is required".format(etiqueta), 400,
                                     {"available_views": [describir_vista(v) for v in vistas]})
        pedido = {"view_type": "FloorPlan"} if por_defecto == "plan" else {"view_type": "Elevation"}
    if isinstance(pedido, _cadena) and pedido.strip().lstrip("-").isdigit():
        pedido = int(pedido)
    if isinstance(pedido, (int,)) and not isinstance(pedido, bool):
        try:
            vista = doc_familia.GetElement(make_element_id(pedido))
        except Exception:
            vista = None
        if vista is None or not isinstance(vista, DB.View):
            raise EscrituraRechazada(u"{}: view {} not found in the family document".format(etiqueta, pedido), 404,
                                     {"available_views": [describir_vista(v) for v in vistas]})
        return vista
    if isinstance(pedido, _cadena):
        pedido = {"view_type": pedido}
    if not isinstance(pedido, dict):
        raise EscrituraRechazada(u"{} must be a view id or {{\"view_type\": ..., \"level\" | \"direction\": ...}}".format(etiqueta), 400)
    tipo = _normalizar(pedido.get("view_type") or "FloorPlan").replace(" ", "").replace("_", "")
    alias = {"plan": "floorplan", "planta": "floorplan", "floorplan": "floorplan", "elevation": "elevation",
             "alzado": "elevation", "3d": "threed", "threed": "threed", "section": "section", "seccion": "section",
             "ceilingplan": "ceilingplan", "techo": "ceilingplan"}
    tipo = alias.get(tipo, tipo)
    candidatas = [v for v in vistas if _normalizar(_tipo_vista(v)) == tipo]
    if not candidatas:
        raise EscrituraRechazada(u"{}: the family document has no view of type '{}'".format(etiqueta, pedido.get("view_type")),
                                 404, {"available_views": [describir_vista(v) for v in vistas]})
    if tipo == "floorplan" or tipo == "ceilingplan":
        nivel = _nivel_familia(doc_familia, pedido.get("level"))
        if nivel is not None:
            por_nivel = []
            for v in candidatas:
                try:
                    if v.GenLevel is not None and get_element_id_value(v.GenLevel) == get_element_id_value(nivel):
                        por_nivel.append(v)
                except Exception:
                    continue
            if por_nivel:
                candidatas = por_nivel
    direccion = pedido.get("direction")
    if direccion:
        clave = _ALIAS_CARA.get(_normalizar(direccion), _normalizar(direccion))
        por_direccion = [v for v in candidatas if _nombre_direccion(v) == clave]
        if not por_direccion:
            raise EscrituraRechazada(u"{}: no {} view looking '{}'".format(etiqueta, pedido.get("view_type"), direccion), 404,
                                     {"available_views": [describir_vista(v) for v in candidatas]})
        candidatas = por_direccion
    return candidatas[0]


def planos_referencia(doc_familia):
    try:
        return list(DB.FilteredElementCollector(doc_familia).OfClass(DB.ReferencePlane).ToElements())
    except Exception:
        return []


def _plano_de(ref_plane):
    try:
        return ref_plane.GetPlane()
    except Exception:
        return None


def _normal_plano(ref_plane):
    plano = _plano_de(ref_plane)
    if plano is not None:
        try:
            return plano.Normal
        except Exception:
            pass
    try:
        return ref_plane.Normal
    except Exception:
        return None


def _origen_plano(ref_plane):
    plano = _plano_de(ref_plane)
    if plano is not None:
        try:
            return plano.Origin
        except Exception:
            pass
    try:
        return ref_plane.BubbleEnd
    except Exception:
        return None


def _nombre_es_referencia(ref_plane):
    try:
        param = ref_plane.get_Parameter(DB.BuiltInParameter.ELEM_REFERENCE_NAME)
        if param is None:
            return None
        indice = param.AsInteger()
    except Exception:
        return None
    for nombre, valor in ES_REFERENCIA.items():
        if valor == indice:
            return nombre
    return indice


def describir_plano(ref_plane):
    normal = _normal_plano(ref_plane)
    origen = _origen_plano(ref_plane)
    return {
        "id": get_element_id_value(ref_plane), "name": get_element_name(ref_plane),
        "origin_mm": punto_a_mm(origen) if origen is not None else None,
        "normal": _vector_mm(normal) if normal is not None else None,
        "is_reference": _nombre_es_referencia(ref_plane),
    }


def resolver_plano(doc_familia, nombre, etiqueta="reference_plane"):
    nombre = _texto_seguro(nombre).strip()
    planos = planos_referencia(doc_familia)
    if not nombre:
        raise EscrituraRechazada(u"{} is required".format(etiqueta), 400,
                                 {"available_reference_planes": [get_element_name(p) for p in planos]})
    for plano in planos:
        if get_element_name(plano) == nombre:
            return plano
    for plano in planos:
        if _normalizar(get_element_name(plano)) == _normalizar(nombre):
            return plano
    raise EscrituraRechazada(u"{}: reference plane '{}' not found".format(etiqueta, nombre), 404,
                             {"available_reference_planes": [get_element_name(p) for p in planos]})


def _es_horizontal(normal):
    return normal is not None and abs(normal.Z) > 0.7


def _vista_para_normal(doc_familia, normal, pedido=None):
    """Vista donde se ve un plano de esa normal: planta para planos verticales, alzado para horizontales."""
    if pedido not in (None, u""):
        return resolver_vista(doc_familia, pedido)
    if _es_horizontal(normal):
        return resolver_vista(doc_familia, None, por_defecto="elevation")
    return resolver_vista(doc_familia, None, por_defecto="plan")


# ---------------------------------------------------------------------------
# Parametros de familia
# ---------------------------------------------------------------------------
def _spec(nombre_tipo):
    """(SpecTypeId, nombre_normalizado). None si no existe en esta version."""
    ruta = TIPOS_DATO.get(nombre_tipo)
    if ruta is None:
        return None
    objeto = getattr(DB, "SpecTypeId", None)
    for parte in ruta:
        objeto = getattr(objeto, parte, None) if objeto is not None else None
    return objeto


def normalizar_tipo_dato(valor):
    """("length", None) | ("family_type", "OST_...") | error 400."""
    texto = _texto_seguro(valor).strip()
    bajo = texto.lower()
    if bajo.startswith("family_type"):
        categoria = texto.split(":", 1)[1].strip() if ":" in texto else u""
        if not categoria:
            raise EscrituraRechazada(u"data_type 'family_type' needs a BuiltInCategory: \"family_type:OST_GenericModel\"", 400)
        return "family_type", categoria
    clave = _ALIAS_TIPOS_DATO.get(bajo, bajo)
    if clave not in TIPOS_DATO:
        raise EscrituraRechazada(u"data_type '{}' not supported".format(texto), 400,
                                 {"available_data_types": sorted(TIPOS_DATO.keys()) + ["family_type:<BuiltInCategory>"]})
    return clave, None


def _grupos_disponibles():
    grupos = getattr(DB, "GroupTypeId", None)
    if grupos is None:
        return []
    return sorted(n for n in dir(grupos) if not n.startswith("_") and n[0].isupper())


def resolver_grupo(nombre):
    """DB.GroupTypeId.<nombre> (Geometry, Materials, Data, IdentityData...); alias en espanol."""
    texto = _texto_seguro(nombre).strip()
    if not texto:
        raise EscrituraRechazada(u"group is required (GroupTypeId name: Geometry, Materials, Data...)", 400,
                                 {"available_groups": _grupos_disponibles()})
    grupos = getattr(DB, "GroupTypeId", None)
    if grupos is None:
        raise EscrituraRechazada(u"This Revit has no GroupTypeId (needs Revit 2022+)", 409, {"no_soportado": True})
    alias = {"geometria": "Geometry", "materiales": "Materials", "datos": "Data", "restricciones": "Constraints",
             "construccion": "Construction", "cotas": "Dimensions", "dimensiones": "Dimensions",
             "identidad": "IdentityData", "identity": "IdentityData", "texto": "Text", "graficos": "Graphics",
             "mecanica": "Mechanical", "electricidad": "Electrical", "fontaneria": "Plumbing",
             "estructura": "Structural", "visibilidad": "Visibility", "otros": "Other", "general": "General"}
    objetivo = alias.get(_normalizar(texto), texto)
    for candidato in _grupos_disponibles():
        if candidato.lower() == objetivo.lower().replace("_", "").replace(" ", ""):
            return getattr(grupos, candidato), candidato
    raise EscrituraRechazada(u"group '{}' is not a GroupTypeId".format(texto), 400, {"available_groups": _grupos_disponibles()})


def _categoria_familia(doc_familia, nombre_bic):
    bic = getattr(DB.BuiltInCategory, _texto_seguro(nombre_bic).strip(), None)
    if bic is None:
        raise EscrituraRechazada(u"'{}' is not a BuiltInCategory".format(nombre_bic), 400)
    try:
        categoria = DB.Category.GetCategory(doc_familia, bic)
    except Exception as error:
        raise EscrituraRechazada(u"Category.GetCategory failed for {}: {}".format(nombre_bic, error), 400)
    if categoria is None:
        raise EscrituraRechazada(u"Category {} is not available in this family document".format(nombre_bic), 404)
    return categoria, bic


def _parametros_familia(doc_familia):
    try:
        return list(doc_familia.FamilyManager.GetParameters())
    except Exception:
        try:
            return list(doc_familia.FamilyManager.Parameters)
        except Exception:
            return []


def parametro_familia(doc_familia, nombre):
    nombre = _texto_seguro(nombre).strip()
    for param in _parametros_familia(doc_familia):
        try:
            if _texto_seguro(param.Definition.Name) == nombre:
                return param
        except Exception:
            continue
    try:
        return doc_familia.FamilyManager.get_Parameter(nombre)
    except Exception:
        return None


def _nombre_spec(param):
    try:
        spec = param.Definition.GetDataType()
    except Exception:
        return None
    texto = _texto_seguro(spec)
    for clave in TIPOS_DATO:
        objetivo = _spec(clave)
        try:
            if objetivo is not None and (spec == objetivo or texto == _texto_seguro(objetivo)):
                return clave
        except Exception:
            continue
    try:
        if _texto_seguro(getattr(spec, "TypeId", u"")).find("familytype") >= 0 or texto == "FamilyType":
            return "family_type"
    except Exception:
        pass
    return texto.split(".")[-1].split(":")[-1] or None


def _nombre_grupo(param):
    try:
        grupo = param.Definition.GetGroupTypeId()
    except Exception:
        return None
    texto = _texto_seguro(grupo)
    grupos = getattr(DB, "GroupTypeId", None)
    if grupos is not None:
        for nombre in _grupos_disponibles():
            try:
                if getattr(grupos, nombre) == grupo:
                    return nombre
            except Exception:
                continue
    return texto.split(".")[-1].split(":")[-1] or None


def valor_tipo(doc_familia, tipo, param):
    """Valor de un parametro en un FamilyType, en unidades del contrato."""
    try:
        if not tipo.HasValue(param):
            return None
    except Exception:
        pass
    storage = _texto_seguro(getattr(param, "StorageType", None)).split(".")[-1]
    clave = _nombre_spec(param)
    try:
        if storage == "Double":
            valor = float(tipo.AsDouble(param))
            factor = FACTORES.get(clave)
            return round(valor / factor, 4) if factor else round(valor, 6)
        if storage == "Integer":
            entero = int(tipo.AsInteger(param))
            return bool(entero) if clave == "yes_no" else entero
        if storage == "String":
            return tipo.AsString(param)
        if storage == "ElementId":
            eid = tipo.AsElementId(param)
            if _es_invalido(eid):
                return None
            elemento = doc_familia.GetElement(eid)
            return get_element_name(elemento) if elemento is not None else get_element_id_value(eid)
    except Exception:
        pass
    try:
        return tipo.AsValueString(param)
    except Exception:
        return None


def describir_parametro(doc_familia, param):
    try:
        nombre = _texto_seguro(param.Definition.Name)
    except Exception:
        nombre = None
    compartido = False
    guid = None
    try:
        compartido = bool(param.IsShared)
        if compartido:
            guid = _texto_seguro(param.GUID)
    except Exception:
        pass
    try:
        formula = param.Formula
    except Exception:
        formula = None
    try:
        es_ejemplar = bool(param.IsInstance)
    except Exception:
        es_ejemplar = None
    return {
        "id": get_element_id_value(param.Id) if getattr(param, "Id", None) is not None else None,
        "name": nombre, "data_type": _nombre_spec(param), "group": _nombre_grupo(param),
        "is_instance": es_ejemplar, "formula": formula if formula else None,
        "is_shared": compartido, "guid": guid,
        "storage_type": _texto_seguro(getattr(param, "StorageType", None)).split(".")[-1] or None,
        "unit": UNIDADES.get(_nombre_spec(param)),
    }


def asegurar_tipo(doc_familia, nombre=None):
    """Dentro de una transaccion: deja un tipo actual en la familia. Devuelve el nombre del tipo creado o None.

    Un documento nuevo desde plantilla no tiene tipos (FamilyManager.Types vacio, CurrentType None) y
    Revit rechaza SetFormula ("There is no valid family type"). Si ya hay tipos, se hace actual el
    primero; si no, se crea uno con `nombre` (el de la familia, como hace Revit al cargarla sin tipos)."""
    gestor = doc_familia.FamilyManager
    if gestor.CurrentType is not None:
        return None
    existentes = tipos_familia(doc_familia)
    if existentes:
        gestor.CurrentType = existentes[0]
        return None
    nombre = _texto_seguro(nombre).strip() or titulo_documento(doc_familia) or u"Tipo 1"
    try:
        gestor.NewType(nombre)
    except Exception as error:
        raise EscrituraRechazada(u"NewType('{}') failed: {}".format(nombre, error), 500)
    return nombre


def tipos_familia(doc_familia):
    try:
        return list(doc_familia.FamilyManager.Types)
    except Exception:
        return []


def describir_tipo(doc_familia, tipo, parametros):
    valores = {}
    for param in parametros:
        try:
            valores[_texto_seguro(param.Definition.Name)] = valor_tipo(doc_familia, tipo, param)
        except Exception:
            continue
    actual = False
    try:
        actual = doc_familia.FamilyManager.CurrentType is not None and doc_familia.FamilyManager.CurrentType.Name == tipo.Name
    except Exception:
        pass
    return {"name": _texto_seguro(tipo.Name), "is_current": actual, "values": valores}


# ---------------------------------------------------------------------------
# Solidos, cotas y conectores (lectura)
# ---------------------------------------------------------------------------
def formas(doc_familia):
    try:
        return list(DB.FilteredElementCollector(doc_familia).OfClass(DB.GenericForm).ToElements())
    except Exception:
        return []


def _clase(elem):
    try:
        return _texto_seguro(elem.GetType().Name)
    except Exception:
        return type(elem).__name__


def _kind_forma(elem):
    clase = _clase(elem).lower()
    for kind in KINDS_SOLIDO:
        if kind[:5] in clase:
            return kind
    return clase


def _solidos_de(elem, vista=None, con_referencias=False):
    opciones = DB.Options()
    if con_referencias:
        try:
            opciones.ComputeReferences = True
            opciones.IncludeNonVisibleObjects = True
        except Exception:
            pass
    if vista is not None:
        try:
            opciones.View = vista
        except Exception:
            pass
    try:
        geometria = elem.get_Geometry(opciones)
    except Exception:
        return []
    solidos = []
    for objeto in geometria or []:
        if isinstance(objeto, DB.Solid):
            solidos.append(objeto)
        elif isinstance(objeto, DB.GeometryInstance):
            try:
                for interno in objeto.GetInstanceGeometry():
                    if isinstance(interno, DB.Solid):
                        solidos.append(interno)
            except Exception:
                continue
    return solidos


def volumen_m3(elem, vista=None):
    total = 0.0
    for solido in _solidos_de(elem, vista):
        try:
            total += float(solido.Volume)
        except Exception:
            continue
    return round(total * (0.3048 ** 3), 6)


def _valor_mm_bip(elem, nombre_bip):
    bip = getattr(DB.BuiltInParameter, nombre_bip, None)
    if bip is None:
        return None
    try:
        param = elem.get_Parameter(bip)
        if param is None or not param.HasValue:
            return None
        return _mm(param.AsDouble())
    except Exception:
        return None


def _parametro_material_asociado(doc_familia, elem):
    bip = getattr(DB.BuiltInParameter, "MATERIAL_ID_PARAM", None)
    if bip is None:
        return None
    try:
        param = elem.get_Parameter(bip)
        if param is None:
            return None
        asociado = doc_familia.FamilyManager.GetAssociatedFamilyParameter(param)
        return _texto_seguro(asociado.Definition.Name) if asociado is not None else None
    except Exception:
        return None


def describir_forma(doc_familia, elem):
    kind = _kind_forma(elem)
    try:
        es_solido = bool(elem.IsSolid)
    except Exception:
        es_solido = True
    datos = {
        "id": get_element_id_value(elem), "kind": kind, "class": _clase(elem), "is_void": not es_solido,
        "volume_m3": volumen_m3(elem), "bbox_mm": describir_elemento(doc_familia, elem)["bbox_mm"],
        "material_parameter": _parametro_material_asociado(doc_familia, elem),
    }
    if kind == "extrusion":
        datos["start_mm"] = _valor_mm_bip(elem, "EXTRUSION_START_PARAM")
        datos["end_mm"] = _valor_mm_bip(elem, "EXTRUSION_END_PARAM")
    return datos


def cotas_familia(doc_familia):
    try:
        cotas = list(DB.FilteredElementCollector(doc_familia).OfClass(DB.Dimension).ToElements())
    except Exception:
        return []
    lista = []
    for cota in cotas:
        etiqueta = None
        try:
            if cota.FamilyLabel is not None:
                etiqueta = _texto_seguro(cota.FamilyLabel.Definition.Name)
        except Exception:
            etiqueta = None
        try:
            iguales = bool(cota.AreSegmentsEqual)
        except Exception:
            iguales = None
        referencias = []
        try:
            for ref in cota.References:
                elemento = doc_familia.GetElement(ref.ElementId)
                referencias.append(get_element_name(elemento) if elemento is not None else get_element_id_value(ref.ElementId))
        except Exception:
            pass
        lista.append({"id": get_element_id_value(cota), "label": etiqueta, "equal": iguales, "references": referencias})
    return lista


def conectores_familia(doc_familia):
    clase = getattr(DB, "ConnectorElement", None)
    if clase is None:
        return []
    try:
        conectores = list(DB.FilteredElementCollector(doc_familia).OfClass(clase).ToElements())
    except Exception:
        return []
    lista = []
    for conector in conectores:
        datos = {"id": get_element_id_value(conector)}
        for clave, atributo in (("domain", "Domain"), ("system_type", "SystemClassification"), ("shape", "Shape")):
            try:
                datos[clave] = _texto_seguro(getattr(conector, atributo)).split(".")[-1]
            except Exception:
                datos[clave] = None
        for clave, atributo in (("radius_mm", "Radius"), ("width_mm", "Width"), ("height_mm", "Height")):
            try:
                datos[clave] = _mm(getattr(conector, atributo))
            except Exception:
                datos[clave] = None
        lista.append(datos)
    return lista


def categoria_familia(doc_familia):
    try:
        categoria = doc_familia.OwnerFamily.FamilyCategory
    except Exception:
        return None, None
    if categoria is None:
        return None, None
    nombre = None
    try:
        nombre = _texto_seguro(categoria.Name)
    except Exception:
        pass
    bic = None
    try:
        identificador = get_element_id_value(categoria.Id)
        for candidato in dir(DB.BuiltInCategory):
            if not candidato.startswith("OST_"):
                continue
            try:
                if int(getattr(DB.BuiltInCategory, candidato)) == identificador:
                    bic = candidato
                    break
            except Exception:
                continue
    except Exception:
        pass
    return nombre, bic


def resumen_familia(doc_familia, completo=True):
    """Resumen del documento: parametros, tipos, planos, solidos, cotas, conectores, vistas, niveles."""
    parametros = _parametros_familia(doc_familia)
    nombre_categoria, bic = categoria_familia(doc_familia)
    resumen = {
        "family_doc": titulo_documento(doc_familia),
        "file_path": ruta_documento(doc_familia) or None,
        "opened_by_mcp": abierto_por_mcp(doc_familia),
        "category": bic, "categoria": nombre_categoria,
        "parameters": [describir_parametro(doc_familia, p) for p in parametros],
        "types": [describir_tipo(doc_familia, t, parametros) for t in tipos_familia(doc_familia)],
        "reference_planes": [describir_plano(p) for p in planos_referencia(doc_familia)],
        "views": [describir_vista(v) for v in vistas_familia(doc_familia)],
        "levels": [{"id": get_element_id_value(n), "name": get_element_name(n), "elevation_mm": _mm(elevacion_interna(n))}
                   for n in niveles_familia(doc_familia)],
    }
    if completo:
        resumen["solids"] = [describir_forma(doc_familia, f) for f in formas(doc_familia)]
        resumen["dimensions"] = cotas_familia(doc_familia)
        resumen["connectors"] = conectores_familia(doc_familia)
    resumen["counts"] = {"parameters": len(resumen["parameters"]), "types": len(resumen["types"]),
                         "reference_planes": len(resumen["reference_planes"]),
                         "solids": len(resumen.get("solids", [])), "connectors": len(resumen.get("connectors", []))}
    try:
        resumen["is_modified"] = bool(doc_familia.IsModified)
    except Exception:
        resumen["is_modified"] = None
    return resumen


# ---------------------------------------------------------------------------
# Abrir, guardar, cargar y cerrar
# ---------------------------------------------------------------------------
def _familia_proyecto(doc, nombre):
    """DB.Family del proyecto por nombre; None si no existe. Lista de nombres para el 404."""
    try:
        familias = list(DB.FilteredElementCollector(doc).OfClass(DB.Family).ToElements())
    except Exception:
        familias = []
    for familia in familias:
        if get_element_name(familia) == nombre:
            return familia, familias
    for familia in familias:
        if _normalizar(get_element_name(familia)) == _normalizar(nombre):
            return familia, familias
    return None, familias


def planificar_apertura(doc, data):
    """Que se va a abrir: {"modo": new|open|edit, ...}. Valida sin abrir nada."""
    template = data.get("template")
    file_path = data.get("file_path")
    family_name = data.get("family_name")
    name = _texto_seguro(data.get("name")).strip()
    if template:
        if not name:
            raise EscrituraRechazada(u"name is required with template", 400)
        ruta = resolver_plantilla(doc, template)
        return {"modo": "new", "template": ruta, "name": name}
    if file_path:
        ruta = _texto_seguro(file_path).strip()
        if not ruta.lower().endswith(EXTENSION_FAMILIA):
            raise EscrituraRechazada(u"file_path must be a .rfa file", 400)
        if not os.path.isfile(ruta):
            raise EscrituraRechazada(u"Family file not found: {}".format(ruta), 404)
        return {"modo": "open", "file_path": ruta, "name": name or os.path.splitext(os.path.basename(ruta))[0]}
    if family_name:
        try:
            if bool(doc.IsModifiable):
                raise EscrituraRechazada(u"doc.EditFamily cannot run with an open transaction in the project", 409,
                                         {"open_transaction": True})
        except EscrituraRechazada:
            raise
        except Exception:
            pass
        familia, familias = _familia_proyecto(doc, _texto_seguro(family_name).strip())
        if familia is None:
            raise EscrituraRechazada(
                u"Family '{}' not found in the project (system families cannot be edited)".format(family_name), 404,
                {"available_families": sorted(set(get_element_name(f) for f in familias))[:MAX_FAMILIAS_DISPONIBLES]})
        try:
            if bool(familia.IsInPlace):
                raise EscrituraRechazada(u"Family '{}' is in-place; it cannot be edited with EditFamily".format(family_name), 400)
        except EscrituraRechazada:
            raise
        except Exception:
            pass
        try:
            if not bool(familia.IsEditable):
                raise EscrituraRechazada(u"Family '{}' is not editable (system family)".format(family_name), 400)
        except EscrituraRechazada:
            raise
        except Exception:
            pass
        return {"modo": "edit", "family_id": get_element_id_value(familia), "family": familia,
                "name": name or get_element_name(familia)}
    raise EscrituraRechazada(u"Give template + name (new family), file_path (.rfa) or family_name (loaded family)", 400,
                             {"available_family_docs": nombres_disponibles(doc)})


def abrir_documento(doc, plan):
    app = aplicacion(doc)
    if plan["modo"] == "new":
        try:
            doc_familia = app.NewFamilyDocument(plan["template"])
        except Exception as error:
            raise EscrituraRechazada(u"NewFamilyDocument failed with '{}': {}".format(plan["template"], error), 500)
    elif plan["modo"] == "open":
        try:
            doc_familia = app.OpenDocumentFile(plan["file_path"])
        except Exception as error:
            raise EscrituraRechazada(u"OpenDocumentFile failed with '{}': {}".format(plan["file_path"], error), 500)
    else:
        try:
            doc_familia = doc.EditFamily(plan["family"])
        except Exception as error:
            raise EscrituraRechazada(u"EditFamily failed: {}".format(error), 500)
    if doc_familia is None:
        raise EscrituraRechazada(u"Revit returned no family document", 500)
    try:
        if not doc_familia.IsFamilyDocument:
            raise EscrituraRechazada(u"The opened document is not a family document", 500)
    except EscrituraRechazada:
        raise
    except Exception:
        pass
    registrar_documento(doc_familia, plan["name"], origen=plan["modo"])
    if plan["modo"] == "new":
        # tipo inicial con el nombre de la familia: sin tipos, SetFormula y la flexion fallan
        try:
            with transaccion(doc_familia, u"Tipo de familia {}".format(plan["name"])):
                asegurar_tipo(doc_familia, plan["name"])
        except Exception as error:
            logger.warning(u"No se pudo crear el tipo inicial de '%s': %s", plan["name"], error)
    return doc_familia


def cargar_familia_en_proyecto(doc, doc_familia, opciones, nombre):
    """doc_familia.LoadFamily(doc, opciones) SIN transaccion abierta en el proyecto. Devuelve (familia, avisos).

    Revit 2027: "The document must not be modifiable before calling LoadFamily. Any open transaction
    must be closed prior the call" (LoadFamily abre la suya). Se envuelve en un TransactionGroup
    "IA: Cargar familia <nombre>" (un grupo no hace modificable el documento) para que el Deshacer
    del proyecto muestre una sola entrada IA:. Si Revit rechaza la carga dentro del grupo, se
    revierte el grupo y se reintenta una vez sin el."""
    esperar_copia_pendiente()
    avisos = []
    grupo = None
    try:
        grupo = DB.TransactionGroup(doc, nombre_transaccion(u"Cargar familia {}".format(nombre)))
        grupo.Start()
    except Exception as error:
        grupo = None
        avisos.append(u"TransactionGroup no disponible: {}".format(error))
    try:
        familia = doc_familia.LoadFamily(doc, opciones)
    except Exception as error:
        if grupo is None:
            raise EscrituraRechazada(u"LoadFamily(project) failed: {}".format(error), 500)
        try:
            grupo.RollBack()
        except Exception:
            pass
        grupo = None
        avisos.append(u"LoadFamily dentro del TransactionGroup fallo ({}); cargada sin grupo".format(error))
        try:
            familia = doc_familia.LoadFamily(doc, opciones)
        except Exception as error:
            raise EscrituraRechazada(u"LoadFamily(project) failed: {}".format(error), 500, {"avisos": avisos})
    if grupo is not None:
        try:
            grupo.Assimilate()
        except Exception as error:
            avisos.append(u"No se pudo cerrar el TransactionGroup: {}".format(error))
    return familia, avisos


class _OpcionesCarga(DB.IFamilyLoadOptions):
    """IFamilyLoadOptions: sobrescribir (o no) los valores de parametros de una familia ya cargada.

    IronPython pasa el argumento `out` como StrongBox: se fija `.Value`."""

    def __init__(self, sobrescribir):
        self.sobrescribir = bool(sobrescribir)

    def _fijar(self, caja):
        try:
            caja.Value = self.sobrescribir
        except Exception:
            pass

    def OnFamilyFound(self, familyInUse, overwriteParameterValues):
        self._fijar(overwriteParameterValues)
        return True

    def OnSharedFamilyFound(self, sharedFamily, familyInUse, source, overwriteParameterValues):
        try:
            source.Value = DB.FamilySource.Family
        except Exception:
            pass
        self._fijar(overwriteParameterValues)
        return True


def _simbolos_de_familia(doc, familia):
    try:
        ids = list(familia.GetFamilySymbolIds())
    except Exception:
        ids = []
    simbolos = []
    for eid in ids:
        simbolo = doc.GetElement(eid)
        if simbolo is not None:
            simbolos.append(simbolo)
    if simbolos:
        return simbolos
    # reserva: recorrer los FamilySymbol cuya Family es esta
    try:
        objetivo = get_element_id_value(familia)
        for simbolo in DB.FilteredElementCollector(doc).OfClass(DB.FamilySymbol).ToElements():
            try:
                if simbolo.Family is not None and get_element_id_value(simbolo.Family) == objetivo:
                    simbolos.append(simbolo)
            except Exception:
                continue
    except Exception:
        pass
    return simbolos


def register_familias_routes(api):
    """Register the family editor routes (0.6.0) with the API."""

    @api.route("/family/info/", methods=["POST"])
    @requiere_token
    def family_info(doc, request):
        """Resumen de un documento de familia; sin family_doc, los documentos abiertos (y las plantillas)."""

        def cuerpo(data):
            if data.get("family_doc"):
                doc_familia = resolver_documento(doc, data)
                resumen = resumen_familia(doc_familia)
                resumen["status"] = "success"
                return resumen
            _limpiar_cerrados(doc)
            abiertos = []
            for documento in documentos_familia(doc):
                titulo = titulo_documento(documento)
                entrada = DOCUMENTOS_ABIERTOS.get(titulo)
                abiertos.append({"family_doc": titulo, "file_path": ruta_documento(documento) or None,
                                 "opened_by_mcp": entrada is not None,
                                 "name": entrada["name"] if entrada else None,
                                 "aliases": sorted(entrada["aliases"]) if entrada else [titulo],
                                 "is_active": _mismo_documento(documento, doc)})
            respuesta = {"status": "success", "open_family_docs": abiertos, "count": len(abiertos),
                         "family_template_path": carpeta_plantillas(doc)}
            if _es_verdadero(data.get("include_templates")):
                carpeta, plantillas, truncado = listar_plantillas(doc, data.get("contains"))
                respuesta["templates"] = plantillas
                respuesta["templates_truncated"] = truncado
            return respuesta

        return _responder(doc, request, cuerpo)

    @api.route("/family/open/", methods=["POST"])
    @requiere_token
    def family_open(doc, request):
        """Abre un documento de familia: nuevo desde plantilla, desde .rfa o EditFamily de una cargada. Acepta `simular`."""

        def cuerpo(ctx):
            plan = planificar_apertura(doc, ctx["data"])
            haria = {"accion": {"new": "nueva_familia", "open": "abrir_rfa", "edit": "editar_familia"}[plan["modo"]],
                     "name": plan["name"]}
            for clave in ("template", "file_path", "family_id"):
                if clave in plan:
                    haria[clave] = plan[clave]
            if ctx["simular"]:
                return simulacion([haria], plan=haria)
            doc_familia = abrir_documento(doc, plan)
            resumen = resumen_familia(doc_familia)
            resumen["mode"] = plan["modo"]
            resumen["name"] = plan["name"]
            resumen["message"] = u"Family document '{}' is open; use family_doc=\"{}\" in the next calls".format(
                resumen["family_doc"], resumen["family_doc"])
            return resumen

        # abrir no escribe en el proyecto: sin copia ni transaccion, pero con registro en el log
        return ejecutar_familia(doc, "/family/open/", request, cuerpo)

    @api.route("/family/save/", methods=["POST"])
    @requiere_token
    def family_save(doc, request):
        """SaveAs del documento de familia (SaveAsOptions.OverwriteExistingFile). 409 si existe y no overwrite. Acepta `simular`."""

        def cuerpo(ctx):
            data = ctx["data"]
            doc_familia = ctx["doc_familia"]
            ruta = _texto_seguro(data.get("file_path")).strip()
            if not ruta:
                raise EscrituraRechazada(u"file_path is required (.rfa)", 400)
            if not ruta.lower().endswith(EXTENSION_FAMILIA):
                ruta = ruta + EXTENSION_FAMILIA
            sobrescribir = _es_verdadero(data.get("overwrite"))
            existe = os.path.isfile(ruta)
            if existe and not sobrescribir:
                raise EscrituraRechazada(u"File already exists: {} (use overwrite=true)".format(ruta), 409,
                                         {"file_path": ruta, "exists": True})
            carpeta = os.path.dirname(ruta)
            if carpeta and not os.path.isdir(carpeta):
                raise EscrituraRechazada(u"Folder does not exist: {}".format(carpeta), 404)
            haria = [{"accion": "guardar_familia", "file_path": ruta, "overwrite": sobrescribir, "exists": existe}]
            if ctx["simular"]:
                return simulacion(haria)
            titulo_anterior = titulo_documento(doc_familia)
            opciones = DB.SaveAsOptions()
            try:
                opciones.OverwriteExistingFile = sobrescribir
            except Exception:
                pass
            try:
                doc_familia.SaveAs(ruta, opciones)
            except Exception as error:
                raise EscrituraRechazada(u"SaveAs failed: {}".format(error), 500, {"file_path": ruta})
            nuevo = _actualizar_titulo(doc_familia, titulo_anterior)
            try:
                tamano = int(os.path.getsize(ruta) / 1024)
            except Exception:
                tamano = None
            return {"file_path": ruta, "size_kb": tamano, "overwritten": existe, "family_doc": nuevo,
                    "previous_family_doc": titulo_anterior,
                    "verificacion": {"coincide": os.path.isfile(ruta),
                                     "detalle": None if os.path.isfile(ruta) else u"The file is not on disk after SaveAs"},
                    "ok": os.path.isfile(ruta),
                    "message": u"Saved as {}; the document is now family_doc=\"{}\"".format(ruta, nuevo)}

        return ejecutar_familia(doc, "/family/save/", request, cuerpo, resolver=_resolver(doc))

    @api.route("/family/load/", methods=["POST"])
    @requiere_token
    def family_load_into_project(doc, request):
        """Carga la familia (documento abierto o .rfa) en el proyecto. 409 si ya esta cargada salvo overwrite_parameters. Acepta `simular`."""

        def cuerpo(ctx):
            data = ctx["data"]
            sobrescribir = _es_verdadero(data.get("overwrite_parameters"))
            doc_familia = None
            ruta = None
            if data.get("family_doc"):
                doc_familia = resolver_documento(doc, data)
                try:
                    nombre = get_element_name(doc_familia.OwnerFamily)
                except Exception:
                    nombre = titulo_documento(doc_familia)
                if not nombre or nombre == "Unnamed":
                    nombre = titulo_documento(doc_familia)
            elif data.get("file_path"):
                ruta = _texto_seguro(data.get("file_path")).strip()
                if not ruta.lower().endswith(EXTENSION_FAMILIA):
                    raise EscrituraRechazada(u"file_path must be a .rfa file", 400)
                if not os.path.isfile(ruta):
                    raise EscrituraRechazada(u"Family file not found: {}".format(ruta), 404)
                nombre = os.path.splitext(os.path.basename(ruta))[0]
            else:
                raise EscrituraRechazada(u"family_doc or file_path is required", 400,
                                         {"available_family_docs": nombres_disponibles(doc)})
            existente, _ = _familia_proyecto(doc, nombre)
            if existente is not None and not sobrescribir:
                raise EscrituraRechazada(
                    u"Family '{}' is already loaded in the project; use overwrite_parameters=true to reload it".format(nombre),
                    409, {"already_loaded": True, "family_id": get_element_id_value(existente),
                          "types": [get_element_name(s) for s in _simbolos_de_familia(doc, existente)]})
            haria = [{"accion": "cargar_en_proyecto", "family": nombre, "source": "family_doc" if doc_familia is not None else "file_path",
                      "file_path": ruta, "already_loaded": existente is not None, "overwrite_parameters": sobrescribir}]
            if ctx["simular"]:
                return simulacion(haria, plan=haria[0])
            antes = set(get_element_id_value(s) for s in _simbolos_de_familia(doc, existente)) if existente is not None else set()
            opciones = _OpcionesCarga(sobrescribir)
            avisos = []
            if doc_familia is not None:
                familia, avisos = cargar_familia_en_proyecto(doc, doc_familia, opciones, nombre)
            else:
                # LoadFamily(ruta) si va dentro de una transaccion del proyecto
                with transaccion(doc, u"Cargar familia {}".format(nombre)):
                    referencia = clr.Reference[DB.Family]()
                    try:
                        cargada = doc.LoadFamily(ruta, opciones, referencia)
                    except Exception as error:
                        raise EscrituraRechazada(u"LoadFamily failed: {}".format(error), 500)
                    familia = referencia.Value
                    if familia is None and cargada is not True:
                        familia, _ = _familia_proyecto(doc, nombre)
            if familia is None:
                familia, _ = _familia_proyecto(doc, nombre)
            if familia is None:
                return {"ok": False, "family": nombre,
                        "verificacion": {"coincide": False, "detalle": u"No family named '{}' is in the project after loading".format(nombre)}}
            simbolos = _simbolos_de_familia(doc, familia)
            nuevos = [get_element_id_value(s) for s in simbolos if get_element_id_value(s) not in antes]
            resultado = resultado_creacion(doc, nuevos)
            resultado.update({
                "family": get_element_name(familia), "family_id": get_element_id_value(familia),
                "types": [{"type_id": get_element_id_value(s), "type": get_element_name(s)} for s in simbolos],
                "reloaded": existente is not None, "overwrite_parameters": sobrescribir,
                "family_doc": titulo_documento(doc_familia) if doc_familia is not None else None,
                "message": u"Family '{}' loaded with {} type(s)".format(get_element_name(familia), len(simbolos)),
                "avisos": avisos,
            })
            if not simbolos:
                resultado["ok"] = False
                resultado["verificacion"] = {"coincide": False, "detalle": u"The loaded family has no types"}
            return resultado

        return ejecutar(doc, "/family/load/", request, cuerpo)

    @api.route("/family/close/", methods=["POST"])
    @requiere_token
    def family_close(doc, request):
        """Cierra un documento de familia abierto por el MCP (Close(save)); nunca el documento activo. Acepta `simular`."""

        def cuerpo(ctx):
            data = ctx["data"]
            doc_familia = ctx["doc_familia"]
            guardar = _es_verdadero(data.get("save"))
            titulo = titulo_documento(doc_familia)
            if not abierto_por_mcp(doc_familia):
                raise EscrituraRechazada(u"'{}' was not opened by the MCP; close it in Revit".format(titulo), 400,
                                         {"opened_by_mcp": [t for t in DOCUMENTOS_ABIERTOS]})
            if _mismo_documento(doc_familia, doc) or _mismo_documento(doc_familia, getattr(revit, "doc", None)):
                raise EscrituraRechazada(u"'{}' is the active document; activate the project in Revit first".format(titulo), 409)
            if guardar and not ruta_documento(doc_familia):
                raise EscrituraRechazada(u"'{}' has never been saved: use family_save first (or save=false)".format(titulo), 400)
            haria = [{"accion": "cerrar_familia", "family_doc": titulo, "save": guardar, "file_path": ruta_documento(doc_familia) or None}]
            if ctx["simular"]:
                return simulacion(haria)
            try:
                doc_familia.Close(guardar)
            except Exception as error:
                raise EscrituraRechazada(u"Close failed: {}".format(error), 500)
            DOCUMENTOS_ABIERTOS.pop(titulo, None)
            sigue = any(titulo_documento(d) == titulo for d in documentos_familia(doc))
            return {"family_doc": titulo, "closed": not sigue, "saved": guardar, "ok": not sigue,
                    "verificacion": {"coincide": not sigue,
                                     "detalle": None if not sigue else u"'{}' is still open after Close".format(titulo)},
                    "open_family_docs": [titulo_documento(d) for d in documentos_familia(doc)]}

        return ejecutar_familia(doc, "/family/close/", request, cuerpo, resolver=_resolver(doc))

    # el resto de rutas (parametros, planos, cotas, solidos, bloqueos, tipos, conectores)
    from familias_edicion import register_edicion_routes

    register_edicion_routes(api)
    from familias_spec import register_spec_routes

    register_spec_routes(api)
