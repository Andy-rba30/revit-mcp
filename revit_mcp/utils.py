# -*- coding: utf-8 -*-
from pyrevit import DB
import traceback
import logging

logger = logging.getLogger(__name__)


# Descripciones de los ultimos fallos de severidad Error que hicieron revertir una
# transaccion (los rellena _FailureSwallower; los lee escritura.transaccion).
ULTIMOS_ERRORES = []

try:
    _ENTEROS = (int, long)  # IronPython 2.7
except NameError:  # pragma: no cover - CPython 3 en las pruebas
    _ENTEROS = (int,)


class _FailureSwallower(DB.IFailuresPreprocessor):
    """Resolve Revit failures during a transaction without ever showing a modal
    dialog. Warnings are deleted (the operation proceeds); if any error-severity
    failure is present, the transaction is rolled back. Either way the headless
    Routes server keeps running instead of hanging on a dialog."""

    def PreprocessFailures(self, failuresAccessor):
        global ULTIMOS_ERRORES
        try:
            # Delete all warnings so they don't block (operation continues).
            failuresAccessor.DeleteAllWarnings()
            # If any genuine errors remain, roll back rather than go modal. The
            # description is kept so the caller can say WHY Revit refused.
            errores = []
            for f in failuresAccessor.GetFailureMessages():
                if f.GetSeverity() == DB.FailureSeverity.Error:
                    try:
                        errores.append(sanitize_string(f.GetDescriptionText()))
                    except Exception:
                        errores.append("error")
            if errores:
                ULTIMOS_ERRORES = errores
                return DB.FailureProcessingResult.ProceedWithRollBack
        except Exception:
            pass
        return DB.FailureProcessingResult.Continue


def suppress_warnings(transaction):
    """Configure a transaction so Revit failures never block on a modal dialog.

    Essential for unattended/headless operation: without this, a routine Revit
    warning (e.g. overlapping walls) OR an error (e.g. "Can't cut instance out
    of Wall") pops a modal dialog that blocks the Routes server indefinitely —
    every later request then times out until a human clicks the dialog.

    Warnings are auto-deleted (operation proceeds); errors roll the transaction
    back cleanly. Call right after transaction.Start(). Best-effort — never raises.
    """
    try:
        opts = transaction.GetFailureHandlingOptions()
        opts.SetForcedModalHandling(False)
        opts.SetClearAfterRollback(True)
        opts.SetFailuresPreprocessor(_FailureSwallower())
        transaction.SetFailureHandlingOptions(opts)
    except Exception:
        pass


def normalize_string(text):
    """Text value for output, trimmed; "Unnamed" if None or unreadable.

    Accents are kept: the JSON layer escapes them (\u00e9). Passing to ASCII
    turned "Generico" with its accent into "Gen?rico" in every response."""
    texto = sanitize_string(text)
    return texto.strip() if texto != "Unnamed" else texto


def sanitize_string(text):
    """Text value for output (unicode, accents kept); "Unnamed" if None or unreadable."""
    if text is None:
        return "Unnamed"
    texto = _a_unicode(text)
    return texto if texto is not None else "Unnamed"


def get_element_name(element):
    """
    Get the name of a Revit element (FamilySymbol or any other element).
    Keeps accents and n-tilde ("Generico - Albanileria" with its real letters):
    the JSON layer escapes them, and passing names to ASCII made every
    comparison against a user-supplied name with accents fail. "Unnamed" if
    the element has no readable name.
    """
    name = nombre_crudo(element)
    return name if name else "Unnamed"


def get_element_id_value(element_or_id):
    """
    Extract an integer element ID from an Element or ElementId.
    Accepts both a full Revit Element and a raw ElementId (duck typing).
    Compatible with Revit 2024, 2025, 2026, and 2027.
    Returns a plain Python int for JSON serialization.
    Raises ValueError if the ID cannot be extracted or input is None.
    """
    if element_or_id is None:
        raise ValueError("Cannot extract ElementId from None")
    # A plain integer id is already the value: the write routes collect ids as
    # ints and resultado_creacion re-reads them through here.
    if isinstance(element_or_id, _ENTEROS) and not isinstance(element_or_id, bool):
        return int(element_or_id)
    try:
        eid = element_or_id.Id if hasattr(element_or_id, "Id") else element_or_id
    except Exception:
        raise ValueError("Cannot extract ElementId from input: {}".format(
            type(element_or_id).__name__))
    try:
        return int(eid.Value)
    except (AttributeError, TypeError):
        pass
    try:
        return int(eid.IntegerValue)
    except (AttributeError, TypeError):
        raise ValueError("Cannot read ID value from: {}".format(
            type(element_or_id).__name__))


def make_element_id(id_value):
    """
    Create a DB.ElementId from an integer value.
    Compatible with Revit 2024, 2025, 2026, and 2027.
    Tries System.Int64 constructor first (2024+), falls back to int.
    Raises ValueError if the ElementId cannot be created or input is invalid.
    """
    if id_value is None:
        raise ValueError("Cannot create ElementId from None")
    try:
        int_val = int(id_value)
    except (TypeError, ValueError):
        raise ValueError("Cannot create ElementId from {}: not a valid integer".format(
            repr(id_value)))
    try:
        import System
        return DB.ElementId(System.Int64(int_val))
    except (TypeError, OverflowError, ImportError):
        pass
    try:
        return DB.ElementId(int_val)
    except Exception as e:
        raise ValueError("Cannot create ElementId from {}: {}".format(
            id_value, str(e)))


def find_family_symbol_safely(doc, target_family_name, target_type_name=None):
    """
    Safely find a family symbol by name.
    Uses get_element_name() for consistent string handling in IronPython.
    """
    try:
        collector = DB.FilteredElementCollector(doc).OfClass(DB.FamilySymbol)

        for symbol in collector:
            try:
                fam_name = get_element_name(symbol.Family)
            except Exception:
                continue
            if fam_name == target_family_name:
                if not target_type_name or get_element_name(symbol) == target_type_name:
                    return symbol
        return None
    except Exception as e:
        logger.error("Error finding family symbol: %s", str(e))
        return None


# ---------------------------------------------------------------------------
# Helpers compartidos por las rutas de escritura (unidades y busquedas)
# ---------------------------------------------------------------------------
MM_TO_FEET = 1.0 / 304.8
FEET_TO_MM = 304.8


def xyz_desde_mm(punto, z_defecto=0.0):
    """DB.XYZ en pies a partir de un dict {"x", "y", "z"} en milimetros.

    Lanza ValueError con un mensaje claro si falta el punto o no es numerico.
    """
    if not isinstance(punto, dict):
        raise ValueError("point must be an object {x, y, z} in mm")
    try:
        return DB.XYZ(
            float(punto.get("x", 0)) * MM_TO_FEET,
            float(punto.get("y", 0)) * MM_TO_FEET,
            float(punto.get("z", z_defecto)) * MM_TO_FEET,
        )
    except (TypeError, ValueError) as error:
        raise ValueError("invalid coordinates {}: {}".format(punto, error))


def punto_a_mm(xyz):
    """Dict {"x", "y", "z"} en milimetros (redondeado a 0.1) a partir de un XYZ."""
    return {
        "x": round(xyz.X * FEET_TO_MM, 1),
        "y": round(xyz.Y * FEET_TO_MM, 1),
        "z": round(xyz.Z * FEET_TO_MM, 1),
    }


def nombre_crudo(element):
    """Nombre del elemento sin pasar a ASCII (unicode), o None."""
    try:
        name = element.Name
    except AttributeError:
        try:
            name = DB.Element.Name.__get__(element)
        except Exception:
            return None
    except Exception:
        return None
    if name is None:
        return None
    return _a_unicode(name)


def _a_unicode(texto):
    try:
        return unicode(texto)  # IronPython 2.7
    except NameError:  # pragma: no cover - CPython 3 en las pruebas
        return str(texto)
    except Exception:
        return None


def elementos_por_nombre(elementos):
    """{nombre: elemento} ignorando los que no tienen nombre legible.

    Se registra el nombre real (unicode) y tambien su version ASCII, para que
    un nivel "Sotano" o un tipo "Muro basico" con acentos se encuentre tanto
    con el nombre exacto como con el que devuelven los listados."""
    mapa = {}
    for elemento in elementos:
        try:
            mapa[get_element_name(elemento)] = elemento
        except Exception:
            continue
        crudo = nombre_crudo(elemento)
        if crudo:
            mapa[crudo] = elemento
    return mapa


def coleccion_niveles(doc):
    return (
        DB.FilteredElementCollector(doc)
        .OfCategory(DB.BuiltInCategory.OST_Levels)
        .WhereElementIsNotElementType()
        .ToElements()
    )


def mapa_niveles(doc):
    """{nombre: DB.Level} de todos los niveles del documento."""
    return elementos_por_nombre(coleccion_niveles(doc))


def nivel_mas_bajo(mapa):
    """El nivel de menor elevacion del mapa (o None si esta vacio)."""
    if not mapa:
        return None
    return sorted(mapa.values(), key=lambda nivel: nivel.Elevation)[0]


def buscar_vista(doc, nombre, solo_planta=False):
    """Vista (no plantilla) por nombre exacto; None si no existe."""
    clase = DB.ViewPlan if solo_planta else DB.View
    vistas = (
        DB.FilteredElementCollector(doc)
        .OfClass(clase)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    for vista in vistas:
        try:
            if vista.IsTemplate:
                continue
            if get_element_name(vista) == nombre:
                return vista
        except Exception:
            continue
    return None


def nombre_familia(tipo):
    """Nombre de la familia de un tipo (FamilySymbol.Family o FamilyName), o None."""
    try:
        if hasattr(tipo, "Family") and tipo.Family:
            return get_element_name(tipo.Family)
    except Exception:
        pass
    try:
        nombre = tipo.FamilyName
    except Exception:
        return None
    return _a_unicode(nombre) if nombre else None


def buscar_tipo_por_nombre(tipos, nombre):
    """Tipos cuyo nombre es `nombre` o "Familia: Tipo" (lista, puede haber varios)."""
    if not nombre:
        return []
    coincidencias = []
    for tipo in tipos:
        try:
            nombre_tipo = get_element_name(tipo)
        except Exception:
            continue
        familia = nombre_familia(tipo)
        candidatos = [nombre_tipo]
        if familia:
            candidatos.append("{}: {}".format(familia, nombre_tipo))
            candidatos.append("{} : {}".format(familia, nombre_tipo))
        if nombre in candidatos:
            coincidencias.append(tipo)
    return coincidencias


def etiqueta_tipo(tipo):
    """'Familia: Tipo' (o solo 'Tipo' si no hay familia) para mensajes."""
    familia = nombre_familia(tipo)
    nombre = get_element_name(tipo)
    return "{}: {}".format(familia, nombre) if familia else nombre


# ---------------------------------------------------------------------------
# Parametros por nombre en cualquier idioma de Revit
# ---------------------------------------------------------------------------
# LookupParameter busca por el nombre visible, que depende del idioma de Revit
# ("Comments" en ingles, "Comentarios" en espanol). Para los parametros comunes
# se admite tambien el nombre ingles y el nombre del BuiltInParameter
# ("ALL_MODEL_INSTANCE_COMMENTS"), que no cambian con el idioma.
ALIAS_PARAMETROS = {
    "comments": ("ALL_MODEL_INSTANCE_COMMENTS",),
    "mark": ("ALL_MODEL_MARK", "DOOR_NUMBER"),
    "type comments": ("ALL_MODEL_TYPE_COMMENTS",),
    "type mark": ("ALL_MODEL_TYPE_MARK", "WINDOW_TYPE_ID"),
    "description": ("ALL_MODEL_DESCRIPTION",),
    "model": ("ALL_MODEL_MODEL",),
    "manufacturer": ("ALL_MODEL_MANUFACTURER",),
    "url": ("ALL_MODEL_URL",),
    "cost": ("ALL_MODEL_COST",),
    "length": ("CURVE_ELEM_LENGTH",),
    "unconnected height": ("WALL_USER_HEIGHT_PARAM",),
    "base offset": ("WALL_BASE_OFFSET",),
    "top offset": ("WALL_TOP_OFFSET",),
    "base constraint": ("WALL_BASE_CONSTRAINT",),
    "top constraint": ("WALL_HEIGHT_TYPE",),
    "room bounding": ("WALL_ATTR_ROOM_BOUNDING",),
    "structural": ("WALL_STRUCTURAL_SIGNIFICANT",),
    "width": ("FAMILY_WIDTH_PARAM", "GENERIC_WIDTH", "WALL_ATTR_WIDTH_PARAM", "RBS_CURVE_WIDTH_PARAM"),
    "height": ("FAMILY_HEIGHT_PARAM", "GENERIC_HEIGHT", "RBS_CURVE_HEIGHT_PARAM"),
    "diameter": ("RBS_PIPE_DIAMETER_PARAM", "RBS_CURVE_DIAMETER_PARAM"),
    "depth": ("GENERIC_DEPTH",),
    "keynote": ("KEYNOTE_PARAM",),
    "assembly code": ("UNIFORMAT_CODE",),
    "fire rating": ("DOOR_FIRE_RATING", "FIRE_RATING"),
    "function": ("FUNCTION_PARAM",),
    "structural material": ("STRUCTURAL_MATERIAL_PARAM",),
    "thickness": ("GENERIC_THICKNESS", "FLOOR_ATTR_THICKNESS_PARAM"),
    "sill height": ("INSTANCE_SILL_HEIGHT_PARAM",),
    "head height": ("INSTANCE_HEAD_HEIGHT_PARAM",),
    "level": ("FAMILY_LEVEL_PARAM", "LEVEL_PARAM", "SCHEDULE_LEVEL_PARAM"),
    "name": ("ROOM_NAME",),
    "number": ("ROOM_NUMBER",),
    "area": ("ROOM_AREA", "HOST_AREA_COMPUTED"),
    "volume": ("ROOM_VOLUME", "HOST_VOLUME_COMPUTED"),
    "phase created": ("PHASE_CREATED",),
    "phase demolished": ("PHASE_DEMOLISHED",),
    "system name": ("RBS_SYSTEM_NAME_PARAM",),
}


def _parametro_integrado(elem, nombre_bip):
    enumeracion = getattr(DB, "BuiltInParameter", None)
    bip = getattr(enumeracion, nombre_bip, None) if enumeracion is not None else None
    if bip is None:
        return None
    try:
        return elem.get_Parameter(bip)
    except Exception:
        return None


def buscar_por_nombre(elem, nombre):
    """Parametro del elemento por nombre visible, nombre ingles o BuiltInParameter; None si no.

    Orden: LookupParameter(nombre) (el nombre tal como lo muestra Revit en su
    idioma), luego el nombre del BuiltInParameter si `nombre` lo es, luego los
    alias ingleses de ALIAS_PARAMETROS.
    """
    if elem is None or not nombre:
        return None
    try:
        param = elem.LookupParameter(nombre)
    except Exception:
        param = None
    if param:
        return param
    try:
        clave = nombre.strip()
    except Exception:
        return None
    candidatos = []
    if clave.upper() == clave and "_" in clave:
        candidatos.append(clave)
    candidatos.extend(ALIAS_PARAMETROS.get(clave.lower(), ()))
    for nombre_bip in candidatos:
        param = _parametro_integrado(elem, nombre_bip)
        if param:
            return param
    return None
