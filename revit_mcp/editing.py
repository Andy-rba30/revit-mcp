# -*- coding: UTF-8 -*-
"""
Editing Module for Revit MCP
Handles element deletion, modification, and selection retrieval.

delete_elements y modify_element pasan por escritura.ejecutar: copia, log,
`simular`, transaccion "IA: ...", verificacion (eliminados / antes-despues) y
limite de 200 elementos por llamada salvo `forzar`.
"""

from utils import get_element_name, make_element_id, get_element_id_value, buscar_por_nombre
from seguridad import requiere_token
from escritura import (
    ejecutar, transaccion, simulacion, EscrituraRechazada, comprobar_alcance,
    describir_elemento, verificar_eliminados,
)
from parameters import valor_parametro, asignar_parametro, coincide_valor
from pyrevit import routes, revit, DB
import traceback
import logging

logger = logging.getLogger(__name__)


def register_editing_routes(api):
    """Register all editing routes with the API"""

    @api.route("/delete_elements/", methods=["POST"])
    @requiere_token
    def delete_elements_handler(doc, request):
        """Delete one or more elements. Accepts `simular` and `forzar` (>200)."""

        def cuerpo(ctx):
            data = ctx["data"]
            element_ids = data.get("element_ids", [])
            if not element_ids:
                raise EscrituraRechazada("No element_ids provided", 400)
            comprobar_alcance(data, len(element_ids), "elementos a borrar")

            # Validate all elements exist before deleting
            objetivos = []
            for eid in element_ids:
                elem_id = make_element_id(eid)
                elem = doc.GetElement(elem_id)
                if not elem:
                    raise EscrituraRechazada(
                        "Element {} not found in the active model".format(eid), 404
                    )
                objetivos.append((elem_id, describir_elemento(doc, elem)))

            if ctx["simular"]:
                haria = [dict(accion="borrar", **descripcion) for _, descripcion in objetivos]
                return simulacion(haria, count=len(haria))

            pedidos = [get_element_id_value(elem_id) for elem_id, _ in objetivos]
            cascada = []
            with transaccion(doc, "Borrar elementos"):
                for elem_id, _ in objetivos:
                    # Un borrado anterior de esta misma lista puede haber arrastrado a este
                    # (muro y su puerta, nivel y lo alojado): Delete sobre un id que ya no
                    # existe lanza ArgumentException y revertiria toda la transaccion.
                    if doc.GetElement(elem_id) is None:
                        continue
                    # doc.Delete returns all deleted IDs (including cascaded)
                    result = doc.Delete(elem_id)
                    if result:
                        for del_id in result:
                            del_id_int = get_element_id_value(del_id)
                            if del_id_int not in pedidos and del_id_int not in cascada:
                                cascada.append(del_id_int)

            resultado = verificar_eliminados(doc, pedidos, cascada)
            resultado["deleted_ids"] = resultado["eliminados"]
            resultado["cascaded_ids"] = resultado["en_cascada"]
            resultado["antes"] = [descripcion for _, descripcion in objetivos]
            message = "Deleted {} element{}".format(
                len(resultado["eliminados"]), "s" if len(resultado["eliminados"]) != 1 else ""
            )
            if cascada:
                message += " ({} hosted element{} also removed)".format(
                    len(cascada), "s" if len(cascada) != 1 else ""
                )
            resultado["message"] = message
            return resultado

        return ejecutar(doc, "/delete_elements/", request, cuerpo)

    @api.route("/modify_element/", methods=["POST"])
    @requiere_token
    def modify_element_handler(doc, request):
        """Modify parameter values on a Revit element. Accepts `simular`."""

        def cuerpo(ctx):
            data = ctx["data"]
            element_id = data.get("element_id")
            parameters = data.get("parameters", {})
            if element_id is None:
                raise EscrituraRechazada("No element_id provided", 400)
            if not parameters:
                raise EscrituraRechazada("No parameters provided", 400)

            elem = doc.GetElement(make_element_id(element_id))
            if not elem:
                raise EscrituraRechazada(
                    "Element {} not found in the active model".format(element_id), 404
                )

            # Resolve every parameter first (no transaction yet)
            planes = []
            failed = []
            for param_name, new_value in parameters.items():
                param = buscar_por_nombre(elem, param_name)
                if not param:
                    available = set()
                    for p in elem.Parameters:
                        try:
                            available.add(p.Definition.Name)
                        except Exception:
                            continue
                    failed.append({
                        "parameter": param_name,
                        "reason": "not found",
                        "available_parameters": sorted(available)[:60],
                    })
                    continue
                if param.IsReadOnly:
                    failed.append({"parameter": param_name, "reason": "read-only"})
                    continue
                planes.append({
                    "parameter": param_name,
                    "param": param,
                    "new_value": new_value,
                    "antes": valor_parametro(param, doc),
                })

            if ctx["simular"]:
                haria = [
                    {
                        "accion": "set_parameter", "element_id": int(element_id),
                        "parameter": p["parameter"], "antes": p["antes"], "despues": p["new_value"],
                    }
                    for p in planes
                ]
                return simulacion(haria, count=len(haria), failed=failed)

            with transaccion(doc, "Modificar elemento {}".format(element_id)):
                for plan in planes:
                    try:
                        asignar_parametro(plan["param"], plan["new_value"])
                        plan["set"] = True
                    except Exception as set_err:
                        plan["set"] = False
                        failed.append({
                            "parameter": plan["parameter"],
                            "reason": "set failed: {}".format(str(set_err)),
                        })

            # Verificacion: releer cada parametro
            antes = {}
            despues = {}
            changes = []
            desajustes = []
            for plan in planes:
                if not plan.get("set"):
                    continue
                valor_despues = valor_parametro(plan["param"], doc)
                antes[plan["parameter"]] = plan["antes"]
                despues[plan["parameter"]] = valor_despues
                coincide = coincide_valor(plan["param"], plan["new_value"])
                changes.append({
                    "parameter": plan["parameter"],
                    "old_value": plan["antes"],
                    "new_value": valor_despues,
                    "status": "set" if coincide else "mismatch",
                })
                if not coincide:
                    desajustes.append(
                        "'{}': requested {!r}, read back {!r}".format(
                            plan["parameter"], plan["new_value"], valor_despues
                        )
                    )

            resultado = {
                "element_id": int(element_id),
                "antes": antes,
                "despues": despues,
                "changes": changes,
                "failed": failed,
                "ok": not desajustes,
                "message": "Modified {} parameter{} on element {}".format(
                    len(changes), "s" if len(changes) != 1 else "", element_id
                ),
            }
            if desajustes:
                resultado["verificacion"] = {
                    "coincide": False,
                    "detalle": "Values read back after commit differ from the request: "
                    + "; ".join(desajustes),
                }
            else:
                resultado["verificacion"] = {"coincide": True}
            return resultado

        return ejecutar(doc, "/modify_element/", request, cuerpo)

    @api.route("/selected_elements/", methods=["GET"])
    @requiere_token
    def get_selected_elements_handler(doc, uidoc):
        """Get details of elements currently selected in Revit UI."""
        try:
            if not doc:
                return routes.make_response(
                    data={"error": "No active Revit document"}, status=503
                )

            if not uidoc:
                return routes.make_response(
                    data={"error": "No active UI document"}, status=503
                )

            # Get current selection
            selection = uidoc.Selection.GetElementIds()

            elements = []
            for elem_id in selection:
                elem = doc.GetElement(elem_id)
                if not elem:
                    continue

                elem_info = {
                    "id": get_element_id_value(elem_id),
                    "category": elem.Category.Name if elem.Category else "Unknown",
                    "type": get_element_name(elem),
                }

                # Try to get level
                try:
                    level_id = elem.LevelId
                    if level_id and level_id != DB.ElementId.InvalidElementId:
                        level = doc.GetElement(level_id)
                        if level:
                            elem_info["level"] = get_element_name(level)
                except Exception:
                    pass

                # Get key parameters
                params = {}
                key_param_names = ["Mark", "Comments", "Length", "Area", "Volume", "Width", "Height"]
                for pname in key_param_names:
                    try:
                        p = buscar_por_nombre(elem, pname)
                        if p and p.HasValue:
                            if p.StorageType == DB.StorageType.String:
                                val = p.AsString()
                                if val:
                                    params[pname] = val
                            elif p.StorageType == DB.StorageType.Double:
                                params[pname] = str(round(p.AsDouble(), 4))
                            elif p.StorageType == DB.StorageType.Integer:
                                params[pname] = str(p.AsInteger())
                    except Exception:
                        continue

                if params:
                    elem_info["parameters"] = params

                elements.append(elem_info)

            count = len(elements)
            if count == 0:
                message = "No elements currently selected"
            elif count == 1:
                message = "1 element currently selected"
            else:
                message = "{} elements currently selected".format(count)

            return routes.make_response(
                data={
                    "status": "success",
                    "elements": elements,
                    "count": count,
                    "message": message,
                }
            )

        except Exception as e:
            logger.error("Failed to get selected elements: {}".format(str(e)))
            error_trace = traceback.format_exc()
            return routes.make_response(
                data={"error": str(e), "traceback": error_trace}, status=500
            )

    logger.info("Editing routes registered successfully")
