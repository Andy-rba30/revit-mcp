# -*- coding: UTF-8 -*-
"""
Tipos Module for Revit MCP
Cambio de tipo de elementos y parametros de tipo.

  POST /change_type/         element_ids*, type_name* (o type_id) -> Element.ChangeTypeId
  POST /set_type_parameter/  type_id* o element_id*, parameter_name*, value*

Ambas pasan por escritura.ejecutar (copia, log, simular, IA:, verificacion).
"""

from utils import (
    get_element_name, get_element_id_value, make_element_id, buscar_tipo_por_nombre,
    etiqueta_tipo, nombre_familia,
)
from seguridad import requiere_token
from escritura import (
    ejecutar, transaccion, simulacion, EscrituraRechazada, comprobar_alcance,
    describir_elemento,
)
from parameters import valor_parametro, convertir_valor, coincide_valor, nombres_parametros
from pyrevit import routes, revit, DB
import logging

logger = logging.getLogger(__name__)


def _tipos_validos(doc, elem):
    tipos = []
    try:
        for tipo_id in elem.GetValidTypes():
            tipo = doc.GetElement(tipo_id)
            if tipo is not None:
                tipos.append(tipo)
    except Exception:
        pass
    if tipos:
        return tipos
    # Reserva: tipos de la misma categoria
    try:
        return list(
            DB.FilteredElementCollector(doc)
            .OfCategoryId(elem.Category.Id)
            .WhereElementIsElementType()
            .ToElements()
        )
    except Exception:
        return []


def _tipo_actual(doc, elem):
    try:
        tipo_id = elem.GetTypeId()
        if tipo_id and tipo_id != DB.ElementId.InvalidElementId:
            return doc.GetElement(tipo_id)
    except Exception:
        pass
    return None


def _ejemplares_del_tipo(doc, tipo):
    """Cuantos ejemplares usan el tipo (para avisar antes de tocar un tipo)."""
    try:
        categoria = tipo.Category
        if categoria is None:
            return None
        cuenta = 0
        for inst in DB.FilteredElementCollector(doc).OfCategoryId(categoria.Id).WhereElementIsNotElementType():
            try:
                if inst.GetTypeId() == tipo.Id:
                    cuenta += 1
            except Exception:
                continue
        return cuenta
    except Exception:
        return None


def register_tipos_routes(api):
    """Register type-related write routes with the API."""

    @api.route("/change_type/", methods=["POST"])
    @requiere_token
    def change_element_type(doc, request):
        """Cambia el tipo de varios elementos (Element.ChangeTypeId). Acepta `simular`."""

        def cuerpo(ctx):
            data = ctx["data"]
            element_ids = data.get("element_ids", [])
            type_name = data.get("type_name")
            type_id = data.get("type_id")
            if not element_ids:
                raise EscrituraRechazada("element_ids is required and must not be empty", 400)
            if not type_name and type_id is None:
                raise EscrituraRechazada("type_name (or type_id) is required", 400)
            comprobar_alcance(data, len(element_ids), "elementos a cambiar de tipo")

            planes = []
            for eid in element_ids:
                elem = doc.GetElement(make_element_id(eid))
                if elem is None:
                    raise EscrituraRechazada("Element {} not found".format(eid), 404)
                validos = _tipos_validos(doc, elem)
                if type_id is not None:
                    candidatos = [t for t in validos if get_element_id_value(t) == int(type_id)]
                else:
                    candidatos = buscar_tipo_por_nombre(validos, type_name)
                if not candidatos:
                    raise EscrituraRechazada(
                        "Type '{}' is not valid for element {} ({})".format(
                            type_name or type_id, eid, get_element_name(elem.Category) if elem.Category else "?"
                        ),
                        404,
                        {"available_types": sorted(set(etiqueta_tipo(t) for t in validos))[:40]},
                    )
                if len(candidatos) > 1:
                    raise EscrituraRechazada(
                        "Type name '{}' is ambiguous for element {}; use 'Family: Type' or type_id".format(type_name, eid),
                        400,
                        {"matches": [{"type_id": get_element_id_value(t), "name": etiqueta_tipo(t)} for t in candidatos]},
                    )
                nuevo = candidatos[0]
                actual = _tipo_actual(doc, elem)
                planes.append({
                    "id": get_element_id_value(elem),
                    "elem": elem,
                    "nuevo": nuevo,
                    "antes": {"type_id": get_element_id_value(actual) if actual else None,
                              "type": etiqueta_tipo(actual) if actual else None},
                    "despues_esperado": {"type_id": get_element_id_value(nuevo), "type": etiqueta_tipo(nuevo)},
                })

            if ctx["simular"]:
                return simulacion(
                    [{"accion": "cambiar_tipo", "element_id": p["id"], "antes": p["antes"],
                      "despues": p["despues_esperado"]} for p in planes],
                    count=len(planes),
                )

            with transaccion(doc, "Cambiar tipo a {}".format(etiqueta_tipo(planes[0]["nuevo"]))):
                for plan in planes:
                    nuevo = plan["nuevo"]
                    try:
                        if hasattr(nuevo, "IsActive") and not nuevo.IsActive:
                            nuevo.Activate()
                    except Exception:
                        pass
                    plan["elem"].ChangeTypeId(nuevo.Id)

            antes = []
            despues = []
            desajustes = []
            for plan in planes:
                actual = _tipo_actual(doc, plan["elem"])
                real = {"type_id": get_element_id_value(actual) if actual else None,
                        "type": etiqueta_tipo(actual) if actual else None}
                antes.append(dict(element_id=plan["id"], **plan["antes"]))
                despues.append(dict(element_id=plan["id"], **real))
                if real["type_id"] != plan["despues_esperado"]["type_id"]:
                    desajustes.append("element {} has type {} instead of {}".format(
                        plan["id"], real["type"], plan["despues_esperado"]["type"]))
            resultado = {
                "count": len(planes),
                "antes": antes,
                "despues": despues,
                "elementos": [describir_elemento(doc, p["elem"]) for p in planes],
                "ok": not desajustes,
                "message": "Changed type of {} element{}".format(len(planes), "s" if len(planes) != 1 else ""),
            }
            resultado["verificacion"] = (
                {"coincide": True} if not desajustes else {"coincide": False, "detalle": "; ".join(desajustes)}
            )
            return resultado

        return ejecutar(doc, "/change_type/", request, cuerpo)

    @api.route("/set_type_parameter/", methods=["POST"])
    @requiere_token
    def set_type_parameter(doc, request):
        """Fija un parametro de TIPO (afecta a todos los ejemplares). Acepta `simular`."""

        def cuerpo(ctx):
            data = ctx["data"]
            type_id = data.get("type_id")
            element_id = data.get("element_id")
            parameter_name = data.get("parameter_name")
            value = data.get("value")
            if type_id is None and element_id is None:
                raise EscrituraRechazada("type_id or element_id is required", 400)
            if not parameter_name:
                raise EscrituraRechazada("parameter_name is required", 400)
            if value is None:
                raise EscrituraRechazada("value is required", 400)

            if type_id is not None:
                tipo = doc.GetElement(make_element_id(type_id))
                if tipo is None:
                    raise EscrituraRechazada("Type {} not found".format(type_id), 404)
                if not isinstance(tipo, DB.ElementType):
                    raise EscrituraRechazada("Element {} is not a type; pass element_id instead".format(type_id), 400)
            else:
                elem = doc.GetElement(make_element_id(element_id))
                if elem is None:
                    raise EscrituraRechazada("Element {} not found".format(element_id), 404)
                tipo = _tipo_actual(doc, elem)
                if tipo is None:
                    raise EscrituraRechazada("Element {} has no type".format(element_id), 400)

            param = tipo.LookupParameter(parameter_name)
            if param is None:
                raise EscrituraRechazada(
                    "Type parameter '{}' not found on type {}".format(parameter_name, etiqueta_tipo(tipo)),
                    404,
                    {"available_parameters": nombres_parametros(tipo)},
                )
            if param.IsReadOnly:
                raise EscrituraRechazada("Type parameter '{}' is read-only".format(parameter_name), 400)
            try:
                convertido = convertir_valor(param, value)
            except ValueError as error:
                raise EscrituraRechazada(str(error), 400)

            antes = valor_parametro(param, doc)
            ejemplares = _ejemplares_del_tipo(doc, tipo)
            identificador = get_element_id_value(tipo)
            if ctx["simular"]:
                return simulacion([{
                    "accion": "set_type_parameter",
                    "type_id": identificador,
                    "type": etiqueta_tipo(tipo),
                    "parameter_name": parameter_name,
                    "antes": antes,
                    "despues": value,
                    "afecta_ejemplares": ejemplares,
                }])

            with transaccion(doc, "Parametro de tipo {} en {}".format(parameter_name, get_element_name(tipo))):
                aceptado = param.Set(convertido)

            despues = valor_parametro(param, doc)
            coincide = coincide_valor(param, value)
            resultado = {
                "type_id": identificador,
                "type": etiqueta_tipo(tipo),
                "family": nombre_familia(tipo),
                "parameter_name": parameter_name,
                "antes": antes,
                "despues": despues,
                "afecta_ejemplares": ejemplares,
                "ok": bool(coincide),
                "message": "Set type parameter '{}' from '{}' to '{}' on {} ({} instances)".format(
                    parameter_name, antes, despues, etiqueta_tipo(tipo), ejemplares
                ),
            }
            resultado["verificacion"] = (
                {"coincide": True} if coincide else
                {"coincide": False, "detalle": "Set returned {} but the value read back ({!r}) differs from {!r}".format(
                    aceptado, despues, value)}
            )
            return resultado

        return ejecutar(doc, "/set_type_parameter/", request, cuerpo)

    logger.info("Tipos routes registered successfully")
