# -*- coding: UTF-8 -*-
"""
Subproyectos y geometria Module for Revit MCP

  POST /set_workset/     element_ids*, workset_name*  -> parametro ELEM_PARTITION_PARAM
  POST /join_geometry/   element_id_a*, element_id_b*, unjoin -> JoinGeometryUtils

Ambas pasan por escritura.ejecutar (copia, log, simular, IA:, antes/despues).
"""

from utils import get_element_name, get_element_id_value, make_element_id
from seguridad import requiere_token
from escritura import ejecutar, transaccion, simulacion, EscrituraRechazada, comprobar_alcance, describir_elemento
from consulta import listar_worksets
from pyrevit import routes, revit, DB
import logging

logger = logging.getLogger(__name__)


def _workset_de(doc, elem):
    try:
        p = elem.get_Parameter(DB.BuiltInParameter.ELEM_PARTITION_PARAM)
        if p is None:
            return None, None
        identificador = p.AsInteger()
        nombre = None
        try:
            ws = doc.GetWorksetTable().GetWorkset(elem.WorksetId)
            nombre = ws.Name if ws is not None else None
        except Exception:
            nombre = p.AsValueString()
        return identificador, nombre
    except Exception:
        return None, None


def register_subproyectos_routes(api):
    """Register workset and join-geometry routes with the API."""

    @api.route("/set_workset/", methods=["POST"])
    @requiere_token
    def set_workset(doc, request):
        """Cambia el subproyecto de los elementos. Acepta `simular` y `forzar`."""

        def cuerpo(ctx):
            data = ctx["data"]
            element_ids = data.get("element_ids", [])
            workset_name = data.get("workset_name")
            if not element_ids:
                raise EscrituraRechazada("element_ids is required and must not be empty", 400)
            if not workset_name:
                raise EscrituraRechazada("workset_name is required", 400)
            if not doc.IsWorkshared:
                raise EscrituraRechazada("The document is not workshared: it has no worksets", 400)
            comprobar_alcance(data, len(element_ids), "elementos a cambiar de subproyecto")

            worksets = listar_worksets(doc)
            objetivo = None
            for ws in worksets:
                if ws["nombre"] == workset_name:
                    objetivo = ws
                    break
            if objetivo is None:
                raise EscrituraRechazada(
                    "Workset '{}' not found".format(workset_name), 404,
                    {"available_worksets": [w["nombre"] for w in worksets]},
                )
            if objetivo["propietario"] and not objetivo["editable"]:
                raise EscrituraRechazada(
                    "Workset '{}' is owned by {} and not editable by you".format(workset_name, objetivo["propietario"]),
                    409,
                )

            planes = []
            for eid in element_ids:
                elem = doc.GetElement(make_element_id(eid))
                if elem is None:
                    raise EscrituraRechazada("Element {} not found".format(eid), 404)
                p = elem.get_Parameter(DB.BuiltInParameter.ELEM_PARTITION_PARAM)
                if p is None:
                    raise EscrituraRechazada("Element {} has no workset parameter".format(eid), 400)
                if p.IsReadOnly:
                    raise EscrituraRechazada(
                        "Element {} workset is read-only (owned by another user or not editable)".format(eid), 409
                    )
                ws_id, ws_nombre = _workset_de(doc, elem)
                planes.append({"id": get_element_id_value(elem), "elem": elem, "param": p,
                               "antes": {"workset_id": ws_id, "workset": ws_nombre}})

            if ctx["simular"]:
                return simulacion(
                    [{"accion": "set_workset", "element_id": p["id"], "antes": p["antes"],
                      "despues": {"workset_id": objetivo["id"], "workset": workset_name}} for p in planes],
                    count=len(planes),
                )

            with transaccion(doc, "Subproyecto {} en {} elementos".format(workset_name, len(planes))):
                for plan in planes:
                    plan["param"].Set(objetivo["id"])

            antes = []
            despues = []
            desajustes = []
            for plan in planes:
                ws_id, ws_nombre = _workset_de(doc, plan["elem"])
                antes.append(dict(element_id=plan["id"], **plan["antes"]))
                despues.append({"element_id": plan["id"], "workset_id": ws_id, "workset": ws_nombre})
                if ws_id != objetivo["id"]:
                    desajustes.append("element {} is in workset {} instead of {}".format(plan["id"], ws_nombre, workset_name))
            resultado = {
                "count": len(planes), "workset": workset_name, "workset_id": objetivo["id"],
                "antes": antes, "despues": despues, "ok": not desajustes,
                "message": "Moved {} element(s) to workset '{}'".format(len(planes), workset_name),
            }
            resultado["verificacion"] = (
                {"coincide": True} if not desajustes else {"coincide": False, "detalle": "; ".join(desajustes)}
            )
            return resultado

        return ejecutar(doc, "/set_workset/", request, cuerpo)

    @api.route("/join_geometry/", methods=["POST"])
    @requiere_token
    def join_geometry(doc, request):
        """Une (o separa con unjoin=true) la geometria de dos elementos. Acepta `simular`."""

        def cuerpo(ctx):
            data = ctx["data"]
            id_a = data.get("element_id_a")
            id_b = data.get("element_id_b")
            unjoin = bool(data.get("unjoin", False))
            if id_a is None or id_b is None:
                raise EscrituraRechazada("element_id_a and element_id_b are required", 400)
            a = doc.GetElement(make_element_id(id_a))
            b = doc.GetElement(make_element_id(id_b))
            if a is None:
                raise EscrituraRechazada("Element {} not found".format(id_a), 404)
            if b is None:
                raise EscrituraRechazada("Element {} not found".format(id_b), 404)
            if get_element_id_value(a) == get_element_id_value(b):
                raise EscrituraRechazada("element_id_a and element_id_b must differ", 400)

            try:
                unidos_antes = bool(DB.JoinGeometryUtils.AreElementsJoined(doc, a, b))
            except Exception as error:
                raise EscrituraRechazada("Cannot check join state: {}".format(error), 400)
            accion = "unjoin" if unjoin else "join"
            if unjoin and not unidos_antes:
                raise EscrituraRechazada("Elements {} and {} are not joined".format(id_a, id_b), 400)
            if not unjoin and unidos_antes:
                return {"accion": accion, "antes": {"joined": True}, "despues": {"joined": True},
                        "ok": True, "message": "Elements were already joined; nothing to do",
                        "verificacion": {"coincide": True}}

            descripcion = {"accion": accion, "element_a": describir_elemento(doc, a),
                           "element_b": describir_elemento(doc, b), "antes": {"joined": unidos_antes}}
            if ctx["simular"]:
                return simulacion([descripcion])

            with transaccion(doc, "{} geometria {} y {}".format("Separar" if unjoin else "Unir", id_a, id_b)):
                if unjoin:
                    DB.JoinGeometryUtils.UnjoinGeometry(doc, a, b)
                else:
                    DB.JoinGeometryUtils.JoinGeometry(doc, a, b)

            unidos_despues = bool(DB.JoinGeometryUtils.AreElementsJoined(doc, a, b))
            coincide = unidos_despues == (not unjoin)
            resultado = {
                "accion": accion,
                "element_a": descripcion["element_a"],
                "element_b": descripcion["element_b"],
                "antes": {"joined": unidos_antes},
                "despues": {"joined": unidos_despues},
                "ok": coincide,
                "message": "{} geometry of {} and {}".format("Unjoined" if unjoin else "Joined", id_a, id_b),
            }
            if not unjoin and unidos_despues:
                try:
                    resultado["cutting"] = get_element_id_value(a) if DB.JoinGeometryUtils.IsCuttingElementInJoin(doc, a, b) else get_element_id_value(b)
                except Exception:
                    pass
            resultado["verificacion"] = (
                {"coincide": True} if coincide else
                {"coincide": False, "detalle": "AreElementsJoined returned {} after {}".format(unidos_despues, accion)}
            )
            return resultado

        return ejecutar(doc, "/join_geometry/", request, cuerpo)

    logger.info("Subproyectos routes registered successfully")
