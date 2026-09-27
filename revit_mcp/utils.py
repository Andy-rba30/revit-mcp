# -*- coding: utf-8 -*-
from pyrevit import DB
import traceback
import logging

logger = logging.getLogger(__name__)


# Descripciones de los ultimos fallos de severidad Error que hicieron revertir una
# transaccion (los rellena _FailureSwallower; los lee escritura.transaccion).
ULTIMOS_ERRORES = []

# Avisos (severidad Warning) que Revit emitio al confirmar las transacciones de
# la peticion en curso: [{"texto", "elementos"}]. Se acumulan porque Revit puede
# llamar varias veces al preprocesador; escritura.ejecutar los vacia al empezar
# cada peticion y los devuelve como `avisos_revit`. Son la unica pista de por
# que un elemento recien creado desaparece al confirmar: Revit resuelve avisos
# como "Rectangular opening doesn't cut its host" borrando el elemento, sin
# error y con la transaccion confirmada.
ULTIMOS_AVISOS = []


def _describir_fallo(f):
    """{"texto", "elementos"} de un FailureMessageAccessor; nunca lanza."""
    try:
        texto = sanitize_string(f.GetDescriptionText())
    except Exception:
        texto = "?"
    elementos = []
    try:
        for eid in f.GetFailingElementIds():
            try:
                elementos.append(get_element_id_value(eid))
            except Exception:
                continue
    except Exception:
        pass
    return {"texto": texto, "elementos": elementos}


def _es_error(f):
    try:
        severidad = f.GetSeverity()
    except Exception:
        return False
    if severidad == DB.FailureSeverity.Error:
        return True
    corrupcion = getattr(DB.FailureSeverity, "DocumentCorruption", None)
    return corrupcion is not None and severidad == corrupcion


class _FailureSwallower(DB.IFailuresPreprocessor):
    """Resolve Revit failures during a transaction without ever showing a modal
    dialog. Warnings are recorded in ULTIMOS_AVISOS and then deleted (the
    operation proceeds); if any error-severity failure is present, the
    transaction is rolled back. Either way the headless Routes server keeps
    running instead of hanging on a dialog."""

    def PreprocessFailures(self, failuresAccessor):
        global ULTIMOS_ERRORES
        try:
            # Record every message BEFORE deleting the warnings: once deleted
            # their text is gone and a vanished element can't be explained.
            errores = []
            for f in failuresAccessor.GetFailureMessages():
                fallo = _describir_fallo(f)
                if _es_error(f):
                    errores.append(fallo["texto"])
                elif fallo not in ULTIMOS_AVISOS:
                    ULTIMOS_AVISOS.append(fallo)
            # Delete all warnings so they don't block (operation continues).
            failuresAccessor.DeleteAllWarnings()
            # If any genuine errors remain, roll back rather than go modal. The
            # description is kept so the caller can say WHY Revit refused.
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
    """Safely normalize string values to ASCII-safe output."""
    if text is None:
        return "Unnamed"
    try:
        return str(text).strip().encode('ascii', 'replace').decode('ascii')
    except Exception:
        return "Unnamed"


def sanitize_string(text):
    """Sanitize a string to be ASCII-safe for JSON serialization."""
    if text is None:
        return "Unnamed"
    try:
        return str(text).encode('ascii', 'replace').decode('ascii')
    except Exception:
        return "Unnamed"


def get_element_name(element):
    """
    Get the name of a Revit element.
    Useful for both FamilySymbol and other elements.
    Returns ASCII-safe string for JSON serialization.
    """
    try:
        name = element.Name
    except AttributeError:
        name = DB.Element.Name.__get__(element)
    return sanitize_string(name)


try:
    _ENTEROS = (int, long)  # IronPython 2.7
except NameError:  # pragma: no cover - CPython 3 en las pruebas
    _ENTEROS = (int,)


def get_element_id_value(element_or_id):
    """
    Extract an integer element ID from an Element, an ElementId or a plain int.
    Accepts a full Revit Element, a raw ElementId (duck typing) or an int/long
    that already is the id (returned as is): the write layer stores ids as
    ints and verifies them afterwards through this function.
    Compatible with Revit 2024, 2025, 2026, and 2027.
    Returns a plain Python int for JSON serialization.
    Raises ValueError if the ID cannot be extracted or input is None.
    """
    if element_or_id is None:
        raise ValueError("Cannot extract ElementId from None")
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
                fam_name = sanitize_string(symbol.Family.Name)
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
    try:
        return unicode(name)  # IronPython 2.7
    except NameError:  # pragma: no cover - CPython 3 en las pruebas
        return str(name)
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
        return sanitize_string(tipo.FamilyName)
    except Exception:
        return None


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
