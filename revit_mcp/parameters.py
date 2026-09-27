# -*- coding: UTF-8 -*-
"""
Parameters Module for Revit MCP
Handles reading element properties and setting parameter values.

set_parameter pasa por escritura.ejecutar: copia, log, `simular`, transaccion
"IA: ..." y verificacion antes/despues (ok=false si lo releido no coincide).
Las funciones valor_parametro / asignar_parametro / coincide_valor las reutilizan
editing.py (modify_element) y tipos.py (set_type_parameter).
"""

from utils import get_element_name, get_element_id_value, make_element_id
from seguridad import requiere_token
from escritura import ejecutar, transaccion, simulacion, EscrituraRechazada, bbox_mm, nombre_nivel, ubicacion_mm, MM_TO_FEET
from pyrevit import routes, revit, DB
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


def _safe_str(value):
    """Convert a value to a JSON-safe ASCII string, replacing problematic chars.
    IronPython 2.7 compatible — handles both str (bytes) and unicode."""
    try:
        if value is None:
            return ""
        # Try unicode first (IronPython 2.7 has unicode type)
        try:
            s = unicode(value)
        except Exception:
            try:
                s = str(value)
            except Exception:
                return ""
        # Strip any char with ordinal >= 128
        result = []
        for ch in s:
            try:
                o = ord(ch)
                if o < 128:
                    result.append(chr(o))
                else:
                    result.append("?")
            except Exception:
                result.append("?")
        return "".join(result)
    except Exception:
        return ""


def _get_param_group_name(param):
    """Get the parameter group name safely across Revit versions."""
    try:
        # Revit 2024+
        if hasattr(param.Definition, "GetGroupTypeId"):
            group_id = param.Definition.GetGroupTypeId()
            return _safe_str(DB.LabelUtils.GetLabelForGroup(group_id))
        # Older Revit
        if hasattr(param.Definition, "ParameterGroup"):
            return _safe_str(param.Definition.ParameterGroup)
    except Exception:
        pass
    return "Other"


def _get_param_value_display(param, doc):
    """Get a display-friendly parameter value."""
    try:
        if not param.HasValue:
            return ""
        if param.StorageType == DB.StorageType.String:
            return _safe_str(param.AsString() or "")
        elif param.StorageType == DB.StorageType.Integer:
            display = param.AsValueString()
            if display:
                return _safe_str(display)
            return str(param.AsInteger())
        elif param.StorageType == DB.StorageType.Double:
            display = param.AsValueString()
            if display:
                return _safe_str(display)
            return str(round(param.AsDouble(), 6))
        elif param.StorageType == DB.StorageType.ElementId:
            eid = param.AsElementId()
            if eid and eid != DB.ElementId.InvalidElementId:
                elem = doc.GetElement(eid)
                if elem:
                    return _safe_str(get_element_name(elem))
            return ""
    except Exception:
        return ""
    return ""


def valor_parametro(param, doc):
    """Valor legible del parametro (alias publico de _get_param_value_display)."""
    return _get_param_value_display(param, doc)


def valor_bruto(param):
    """Valor interno del parametro segun su StorageType (None si no tiene)."""
    try:
        if not param.HasValue:
            return None
        if param.StorageType == DB.StorageType.String:
            return param.AsString()
        if param.StorageType == DB.StorageType.Integer:
            return param.AsInteger()
        if param.StorageType == DB.StorageType.Double:
            return param.AsDouble()
        if param.StorageType == DB.StorageType.ElementId:
            eid = param.AsElementId()
            if eid is None:
                return None
            return get_element_id_value(eid)
    except Exception:
        return None
    return None


def factor_a_interno(param):
    """Factor que pasa el valor recibido a las unidades internas de Revit segun el
    tipo de dato del parametro: longitudes en mm -> pies, areas en mm2 -> pies2,
    volumenes en mm3 -> pies3, angulos en grados -> radianes. None si el
    parametro no es de esos tipos (o la API no expone GetDataType), y entonces el
    valor se guarda tal cual."""
    try:
        spec = param.Definition.GetDataType()
    except Exception:
        return None
    try:
        tipos = DB.SpecTypeId
        if spec == tipos.Length:
            return MM_TO_FEET
        if spec == tipos.Area:
            return MM_TO_FEET * MM_TO_FEET
        if spec == tipos.Volume:
            return MM_TO_FEET * MM_TO_FEET * MM_TO_FEET
        if spec == tipos.Angle:
            return math.pi / 180.0
    except Exception:
        return None
    return None


def unidad_contrato(param):
    """Unidad del contrato de un parametro Double: 'mm', 'mm2', 'mm3' o 'grados';
    None si Revit no lo declara como longitud, area, volumen o angulo."""
    try:
        spec = param.Definition.GetDataType()
        tipos = DB.SpecTypeId
        if spec == tipos.Length:
            return "mm"
        if spec == tipos.Area:
            return "mm2"
        if spec == tipos.Volume:
            return "mm3"
        if spec == tipos.Angle:
            return "grados"
    except Exception:
        return None
    return None


def valor_en_contrato(param):
    """(valor numerico en las unidades del contrato, unidad) de un parametro
    Double o Integer; (None, None) si no tiene valor o no es numerico.

    `valor_parametro` devuelve lo que Revit muestra ("3.00" en un proyecto en
    metros); esto devuelve 3000.0 y "mm", que es lo que el agente envio."""
    try:
        if param.StorageType == DB.StorageType.Double:
            bruto = valor_bruto(param)
            if bruto is None:
                return None, None
            factor = factor_a_interno(param)
            valor = float(bruto) / factor if factor else float(bruto)
            return round(valor, 4), unidad_contrato(param)
        if param.StorageType == DB.StorageType.Integer:
            return valor_bruto(param), None
    except Exception:
        pass
    return None, None


def convertir_valor(param, value):
    """Convierte `value` al tipo que espera el parametro. Lanza ValueError.

    Los Double se reciben en las unidades del contrato (mm, mm2, mm3, grados) y
    se convierten a las internas de Revit con factor_a_interno."""
    try:
        if param.StorageType == DB.StorageType.String:
            return _texto(value) if not isinstance(value, _cadena) else value
        if param.StorageType == DB.StorageType.Integer:
            if isinstance(value, _cadena):
                bajo = value.strip().lower()
                if bajo in ("true", "yes", "si", "sí"):
                    return 1
                if bajo in ("false", "no"):
                    return 0
            return int(float(value))
        if param.StorageType == DB.StorageType.Double:
            numero = float(value)
            factor = factor_a_interno(param)
            return numero * factor if factor else numero
        if param.StorageType == DB.StorageType.ElementId:
            return make_element_id(value)
    except (TypeError, ValueError) as error:
        raise ValueError(
            "value {!r} is not valid for a {} parameter: {}".format(value, param.StorageType, error)
        )
    raise ValueError("unsupported storage type {}".format(param.StorageType))


def asignar_parametro(param, value):
    """param.Set(...) con el valor convertido; devuelve True si Revit acepto."""
    convertido = convertir_valor(param, value)
    return param.Set(convertido)


def coincide_valor(param, value):
    """True si el valor releido del parametro coincide con `value`."""
    try:
        convertido = convertir_valor(param, value)
    except ValueError:
        return False
    actual = valor_bruto(param)
    try:
        if param.StorageType == DB.StorageType.Double:
            return actual is not None and abs(float(actual) - float(convertido)) < 1e-6
        if param.StorageType == DB.StorageType.Integer:
            return actual == int(convertido)
        if param.StorageType == DB.StorageType.ElementId:
            return actual == get_element_id_value(convertido)
        return (actual or u"") == (convertido or u"")
    except Exception:
        return False


def contexto_elemento(doc, elem):
    """bbox_mm, level, workset, phase_created, phase_demolished, design_option,
    host_id y pinned de un elemento (None cuando no aplica)."""
    contexto = {
        "bbox_mm": bbox_mm(elem),
        "location_mm": ubicacion_mm(elem),
        "level": nombre_nivel(doc, elem),
        "workset": None,
        "phase_created": None,
        "phase_demolished": None,
        "design_option": None,
        "host_id": None,
        "pinned": None,
    }
    try:
        if doc.IsWorkshared:
            workset = doc.GetWorksetTable().GetWorkset(elem.WorksetId)
            if workset is not None:
                contexto["workset"] = _safe_str(workset.Name)
    except Exception:
        pass
    for clave, atributo in (("phase_created", "CreatedPhaseId"), ("phase_demolished", "DemolishedPhaseId")):
        try:
            fase_id = getattr(elem, atributo)
            if fase_id and fase_id != DB.ElementId.InvalidElementId:
                fase = doc.GetElement(fase_id)
                if fase is not None:
                    contexto[clave] = _safe_str(get_element_name(fase))
        except Exception:
            pass
    try:
        opcion = elem.DesignOption
        if opcion is not None:
            contexto["design_option"] = _safe_str(get_element_name(opcion))
    except Exception:
        pass
    try:
        host = getattr(elem, "Host", None)
        if host is not None:
            contexto["host_id"] = get_element_id_value(host)
    except Exception:
        pass
    try:
        contexto["pinned"] = bool(elem.Pinned)
    except Exception:
        pass
    return contexto


# Nombres ingleses habituales -> BuiltInParameter, para que un agente pueda pedir
# "Comments" o "Unconnected Height" aunque Revit este en espanol ("Comentarios",
# "Altura desconectada"). LookupParameter solo entiende el nombre en el idioma
# de Revit; el BuiltInParameter es el mismo en todos. Los nombres que no existan
# en la version de Revit se ignoran (getattr).
ALIAS_BUILTIN = {
    "comments": ("ALL_MODEL_INSTANCE_COMMENTS",),
    "type comments": ("ALL_MODEL_TYPE_COMMENTS",),
    "mark": ("ALL_MODEL_MARK",),
    "type mark": ("ALL_MODEL_TYPE_MARK",),
    "description": ("ALL_MODEL_DESCRIPTION",),
    "unconnected height": ("WALL_USER_HEIGHT_PARAM",),
    "base offset": ("WALL_BASE_OFFSET", "FAMILY_BASE_LEVEL_OFFSET_PARAM"),
    "top offset": ("WALL_TOP_OFFSET", "FAMILY_TOP_LEVEL_OFFSET_PARAM"),
    "base constraint": ("WALL_BASE_CONSTRAINT", "FAMILY_BASE_LEVEL_PARAM"),
    "top constraint": ("WALL_HEIGHT_TYPE", "FAMILY_TOP_LEVEL_PARAM"),
    "level": ("FAMILY_LEVEL_PARAM", "LEVEL_PARAM", "SCHEDULE_LEVEL_PARAM"),
    "elevation": ("LEVEL_ELEV", "INSTANCE_ELEVATION_PARAM"),
    "sill height": ("INSTANCE_SILL_HEIGHT_PARAM",),
    "head height": ("INSTANCE_HEAD_HEIGHT_PARAM",),
    "phase created": ("PHASE_CREATED",),
    "phase demolished": ("PHASE_DEMOLISHED",),
    "workset": ("ELEM_PARTITION_PARAM",),
    "room bounding": ("WALL_ATTR_ROOM_BOUND",),
    "structural": ("WALL_STRUCTURAL_SIGNIFICANT",),
    "name": ("DATUM_TEXT", "VIEW_NAME", "ROOM_NAME"),
    "number": ("ROOM_NUMBER",),
    "department": ("ROOM_DEPARTMENT",),
    "occupancy": ("ROOM_OCCUPANCY",),
    "length": ("CURVE_ELEM_LENGTH",),
    "area": ("HOST_AREA_COMPUTED", "ROOM_AREA"),
    "volume": ("HOST_VOLUME_COMPUTED", "ROOM_VOLUME"),
}

NOTA_NOMBRES = (
    "Parameter names are the ones Revit shows in its language (e.g. 'Comentarios' on a "
    "Spanish Revit); English built-in names such as Comments, Mark, Description or "
    "Unconnected Height and BuiltInParameter names such as ALL_MODEL_MARK are accepted "
    "as aliases (get_element_properties lists each parameter's `builtin` name)."
)


def _parametro_builtin(elem, nombre):
    """Parametro por nombre de BuiltInParameter (ALL_MODEL_MARK) o alias ingles
    (Comments, Mark, Unconnected Height...); None si el elemento no lo tiene."""
    if not nombre:
        return None
    limpio = nombre.strip()
    candidatos = []
    if "_" in limpio and limpio.upper() == limpio:
        candidatos.append(limpio)
    candidatos.extend(ALIAS_BUILTIN.get(limpio.lower(), ()))
    for candidato in candidatos:
        bip = getattr(DB.BuiltInParameter, candidato, None)
        if bip is None:
            continue
        try:
            param = elem.get_Parameter(bip)
        except Exception:
            param = None
        if param:
            return param
    return None


def resolver_parametro(doc, elem, parameter_name, incluir_tipo=True):
    """(parametro, es_de_tipo) por nombre en el idioma de Revit, nombre de
    BuiltInParameter o alias ingles; primero en el ejemplar y, si incluir_tipo,
    en su tipo. (None, False) si no existe."""
    param = elem.LookupParameter(parameter_name) or _parametro_builtin(elem, parameter_name)
    if param:
        return param, False
    if not incluir_tipo:
        return None, False
    try:
        type_id = elem.GetTypeId()
        if type_id and type_id != DB.ElementId.InvalidElementId:
            elem_type = doc.GetElement(type_id)
            if elem_type:
                param = elem_type.LookupParameter(parameter_name) or _parametro_builtin(elem_type, parameter_name)
                if param:
                    return param, True
    except Exception:
        pass
    return None, False


def buscar_parametro(doc, elem, parameter_name, incluir_tipo=True):
    """Parametro de ejemplar (o de tipo si incluir_tipo) por nombre; None si no."""
    return resolver_parametro(doc, elem, parameter_name, incluir_tipo)[0]


def nombre_definicion(param):
    """Nombre del parametro tal como lo muestra Revit (Definition.Name), o None."""
    try:
        return _safe_str(param.Definition.Name)
    except Exception:
        return None


def nombre_builtin(param):
    """Nombre del BuiltInParameter del parametro (ALL_MODEL_MARK...), o None si
    es compartido/de proyecto o la API no lo expone."""
    try:
        bip = param.Definition.BuiltInParameter
    except Exception:
        return None
    if bip is None:
        return None
    try:
        invalido = getattr(DB.BuiltInParameter, "INVALID", None)
        if invalido is not None and bip == invalido:
            return None
        texto = str(bip)
    except Exception:
        return None
    return texto if texto and texto != "INVALID" else None


def nombres_parametros(elem, maximo=30):
    available = []
    for p in elem.Parameters:
        try:
            available.append(p.Definition.Name)
        except Exception:
            continue
    available.sort()
    return available[:maximo]


def register_parameter_routes(api):
    """Register all parameter routes with the API"""

    @api.route("/element_properties/<element_id>", methods=["GET"])
    @requiere_token
    def get_element_properties_handler(doc, element_id):
        """Get all properties and parameters of an element."""
        try:
            if not doc:
                return routes.make_response(
                    data={"error": "No active Revit document"}, status=503
                )

            elem_id = make_element_id(int(element_id))
            elem = doc.GetElement(elem_id)
            if not elem:
                return routes.make_response(
                    data={"error": "Element not found."},
                    status=404,
                )

            category = ""
            try:
                if elem.Category:
                    category = _safe_str(elem.Category.Name)
            except Exception:
                pass

            family = ""
            type_name = ""
            try:
                type_id = elem.GetTypeId()
                if type_id and type_id != DB.ElementId.InvalidElementId:
                    elem_type = doc.GetElement(type_id)
                    if elem_type:
                        type_name = _safe_str(get_element_name(elem_type))
                        if hasattr(elem_type, "Family") and elem_type.Family:
                            family = _safe_str(get_element_name(elem_type.Family))
                        elif hasattr(elem_type, "FamilyName"):
                            family = _safe_str(elem_type.FamilyName)
            except Exception:
                pass

            # Collect instance parameters
            parameters = []
            seen_names = set()

            for param in elem.GetOrderedParameters():
                try:
                    param_name = _safe_str(param.Definition.Name)
                    if param_name in seen_names:
                        continue
                    seen_names.add(param_name)

                    entrada = {
                        "name": param_name,
                        "value": _safe_str(_get_param_value_display(param, doc)),
                        "storage_type": str(param.StorageType),
                        "read_only": param.IsReadOnly,
                        "group": _safe_str(_get_param_group_name(param)),
                        "is_instance": True,
                        "is_type_parameter": False,
                    }
                    builtin = nombre_builtin(param)
                    if builtin:
                        entrada["builtin"] = builtin
                    parameters.append(entrada)
                except Exception:
                    continue

            # Collect type parameters
            try:
                type_id = elem.GetTypeId()
                if type_id and type_id != DB.ElementId.InvalidElementId:
                    elem_type = doc.GetElement(type_id)
                    if elem_type:
                        for param in elem_type.GetOrderedParameters():
                            try:
                                param_name = _safe_str(param.Definition.Name)
                                if param_name in seen_names:
                                    continue
                                seen_names.add(param_name)

                                entrada = {
                                    "name": param_name,
                                    "value": _safe_str(_get_param_value_display(param, doc)),
                                    "storage_type": str(param.StorageType),
                                    "read_only": param.IsReadOnly,
                                    "group": _safe_str(_get_param_group_name(param)),
                                    "is_instance": False,
                                    "is_type_parameter": True,
                                }
                                builtin = nombre_builtin(param)
                                if builtin:
                                    entrada["builtin"] = builtin
                                parameters.append(entrada)
                            except Exception:
                                continue
            except Exception:
                pass

            contexto = contexto_elemento(doc, elem)
            datos = {
                "status": "success",
                "element_id": int(element_id),
                "category": category,
                "family": family,
                "type": type_name,
                "type_id": None,
                "parameters": parameters,
                "parameter_count": len(parameters),
                "message": "Found {} parameters on element {}".format(
                    len(parameters), element_id
                ),
            }
            try:
                type_id = elem.GetTypeId()
                if type_id and type_id != DB.ElementId.InvalidElementId:
                    datos["type_id"] = get_element_id_value(type_id)
            except Exception:
                pass
            datos.update(contexto)
            return routes.make_response(data=datos)

        except Exception as e:
            logger.error("Failed to get element properties: {}".format(str(e)))
            error_trace = traceback.format_exc()
            return routes.make_response(
                data={"error": str(e), "traceback": error_trace}, status=500
            )

    @api.route("/set_parameter/", methods=["POST"])
    @requiere_token
    def set_parameter_handler(doc, request):
        """Set a single parameter value on an element. Accepts `simular`."""

        def cuerpo(ctx):
            data = ctx["data"]
            element_id = data.get("element_id")
            parameter_name = data.get("parameter_name")
            value = data.get("value")

            if element_id is None:
                raise EscrituraRechazada("element_id is required", 400)
            if not parameter_name:
                raise EscrituraRechazada("parameter_name is required", 400)
            if value is None:
                raise EscrituraRechazada("value is required", 400)

            elem = doc.GetElement(make_element_id(element_id))
            if not elem:
                raise EscrituraRechazada("Element {} not found".format(element_id), 404)

            param, es_de_tipo = resolver_parametro(doc, elem, parameter_name)
            if not param:
                raise EscrituraRechazada(
                    "Parameter '{}' not found on element {}. {}".format(parameter_name, element_id, NOTA_NOMBRES),
                    404,
                    {"available_parameters": nombres_parametros(elem)},
                )
            nombre_revit = nombre_definicion(param)
            if param.IsReadOnly:
                raise EscrituraRechazada(
                    "Parameter '{}' ({}) is read-only and cannot be modified.".format(parameter_name, nombre_revit), 400
                )
            try:
                convertido = convertir_valor(param, value)
            except ValueError as error:
                raise EscrituraRechazada(str(error), 400)

            antes = valor_parametro(param, doc)
            antes_valor, unidad = valor_en_contrato(param)
            if ctx["simular"]:
                haria = {
                    "accion": "set_parameter",
                    "element_id": int(element_id),
                    "parameter_name": parameter_name,
                    "parameter_name_revit": nombre_revit,
                    "is_type_parameter": es_de_tipo,
                    "storage_type": str(param.StorageType),
                    "antes": antes,
                    "despues": value,
                }
                if param.StorageType == DB.StorageType.Double:
                    # Lo que se guardara en Revit (pies, pies2, pies3 o radianes)
                    haria["valor_interno_revit"] = convertido
                    if unidad:
                        haria["unidad"] = unidad
                return simulacion([haria])

            with transaccion(doc, "Parametro {} de {}".format(parameter_name, element_id)):
                aceptado = param.Set(convertido)

            despues = valor_parametro(param, doc)
            despues_valor, _ = valor_en_contrato(param)
            coincide = coincide_valor(param, value)
            en_unidades = u" ({} {})".format(despues_valor, unidad) if unidad and despues_valor is not None else u""
            resultado = {
                "element_id": int(element_id),
                "parameter_name": parameter_name,
                "parameter_name_revit": nombre_revit,
                "is_type_parameter": es_de_tipo,
                "antes": antes,
                "despues": despues,
                "old_value": antes,
                "new_value": despues,
                "ok": bool(coincide),
                "message": u"Set '{}' from '{}' to '{}'{} on element {}".format(
                    parameter_name, antes, despues, en_unidades, element_id
                ),
            }
            if antes_valor is not None or despues_valor is not None:
                # Valor numerico en las unidades del contrato (mm...), ademas del
                # texto que muestra Revit en las unidades del proyecto ("3.00" m).
                resultado["antes_valor"] = antes_valor
                resultado["despues_valor"] = despues_valor
                if unidad:
                    resultado["unidad"] = unidad
            if coincide:
                resultado["verificacion"] = {"coincide": True}
            else:
                resultado["verificacion"] = {
                    "coincide": False,
                    "detalle": "Revit accepted the call (Set returned {}) but the value read back "
                    "({!r}) does not match the requested value ({!r}); no retry was attempted.".format(
                        aceptado, despues, value
                    ),
                }
            return resultado

        return ejecutar(doc, "/set_parameter/", request, cuerpo)

    logger.info("Parameter routes registered successfully")
