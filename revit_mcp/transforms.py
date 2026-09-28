# -*- coding: UTF-8 -*-
"""
Transforms Module for Revit MCP
Handles move, copy, rotate, mirror and (0.4.0) array operations on elements.

Pasa por escritura.ejecutar: copia, log, `simular`, transaccion "IA: ...",
limite de 200 elementos salvo `forzar` y verificacion antes/despues (bbox).
"""

from utils import get_element_id_value, make_element_id, xyz_desde_mm, punto_a_mm, MM_TO_FEET
from seguridad import requiere_token
from escritura import (
    ejecutar, transaccion, simulacion, EscrituraRechazada, comprobar_alcance,
    describir_elemento, resultado_creacion, bbox_mm,
)
from pyrevit import routes, revit, DB
import math
import logging

logger = logging.getLogger(__name__)


def _centro(caja):
    if not caja or not caja.get("min") or not caja.get("max"):
        return None
    return dict(
        (eje, (caja["min"][eje] + caja["max"][eje]) / 2.0) for eje in ("x", "y", "z")
    )


def register_transform_routes(api):
    """Register all transform routes with the API"""

    @api.route("/transform_elements/", methods=["POST"])
    @requiere_token
    def transform_elements_handler(doc, request):
        """Move, copy, rotate, or mirror elements. Accepts `simular` and `forzar`."""

        def cuerpo(ctx):
            data = ctx["data"]
            element_ids = data.get("element_ids", [])
            operation = data.get("operation")

            if not element_ids:
                raise EscrituraRechazada("element_ids is required and must not be empty", 400)
            if not operation:
                raise EscrituraRechazada("operation is required (move, copy, rotate, mirror, array)", 400)
            if operation not in ("move", "copy", "rotate", "mirror", "array"):
                raise EscrituraRechazada(
                    "Invalid operation '{}'. Use: move, copy, rotate, mirror, array".format(operation), 400
                )
            count = 1
            if operation == "array":
                # 0.4.0: matriz lineal = count-1 copias a vector, 2*vector, ... en una transaccion
                try:
                    count = int(data.get("count") or 0)
                except (TypeError, ValueError):
                    raise EscrituraRechazada("count must be an integer (total copies, >= 2)", 400)
                if count < 2:
                    raise EscrituraRechazada("array needs count >= 2 (total copies including the original)", 400)
                comprobar_alcance(data, len(element_ids) * (count - 1), "elementos a crear en la matriz")
            else:
                comprobar_alcance(data, len(element_ids), "elementos a transformar")

            # Resolve the operation parameters first
            translation = None
            axis_line = None
            angle_rad = None
            plane = None
            detalle = {"operation": operation}
            if operation in ("move", "copy", "array"):
                vector = data.get("vector")
                if not vector:
                    raise EscrituraRechazada("vector is required for {} operation".format(operation), 400)
                translation = xyz_desde_mm(vector)
                detalle["vector_mm"] = punto_a_mm(translation)
                if operation == "array":
                    detalle["count"] = count
                    detalle["copies"] = count - 1
            elif operation == "rotate":
                axis_point = data.get("axis_point")
                angle = data.get("angle")
                if not axis_point:
                    raise EscrituraRechazada("axis_point is required for rotate operation", 400)
                if angle is None:
                    raise EscrituraRechazada("angle is required for rotate operation", 400)
                center = xyz_desde_mm(axis_point)
                axis_line = DB.Line.CreateBound(center, DB.XYZ(center.X, center.Y, center.Z + 1.0))
                angle_rad = float(angle) * math.pi / 180.0
                detalle["axis_point_mm"] = punto_a_mm(center)
                detalle["angle_deg"] = float(angle)
            else:
                mirror_plane = data.get("mirror_plane")
                if not mirror_plane:
                    raise EscrituraRechazada("mirror_plane is required for mirror operation", 400)
                origin = mirror_plane.get("origin", {})
                normal = mirror_plane.get("normal", {})
                plane_origin = xyz_desde_mm(origin)
                plane_normal = DB.XYZ(
                    float(normal.get("x", 0)), float(normal.get("y", 1)), float(normal.get("z", 0))
                ).Normalize()
                plane = DB.Plane.CreateByNormalAndOrigin(plane_normal, plane_origin)
                detalle["mirror_origin_mm"] = punto_a_mm(plane_origin)
                detalle["mirror_normal"] = {"x": plane_normal.X, "y": plane_normal.Y, "z": plane_normal.Z}

            # Validate and collect elements
            elem_id_list = []
            antes = []
            for eid in element_ids:
                elem_id = make_element_id(eid)
                elem = doc.GetElement(elem_id)
                if not elem:
                    raise EscrituraRechazada("Element {} not found".format(eid), 404)
                if hasattr(elem, "Pinned") and elem.Pinned:
                    raise EscrituraRechazada(
                        "Element {} is pinned — unpin it first using modify_element before transforming.".format(eid),
                        400,
                    )
                elem_id_list.append(elem_id)
                antes.append(describir_elemento(doc, elem))

            if ctx["simular"]:
                haria = []
                for descripcion in antes:
                    accion = dict(detalle)
                    accion["accion"] = operation
                    accion["elemento"] = descripcion
                    haria.append(accion)
                return simulacion(haria, count=len(haria))

            nombres = {"move": "Mover", "copy": "Copiar", "rotate": "Girar", "mirror": "Simetria de",
                       "array": "Matriz de"}
            new_element_ids = []
            with transaccion(doc, "{} {} elementos".format(nombres[operation], len(elem_id_list))):
                for eid in elem_id_list:
                    if operation == "move":
                        DB.ElementTransformUtils.MoveElement(doc, eid, translation)
                    elif operation == "copy":
                        copied = DB.ElementTransformUtils.CopyElement(doc, eid, translation)
                        if copied:
                            for cid in copied:
                                new_element_ids.append(get_element_id_value(cid))
                    elif operation == "array":
                        for paso in range(1, count):
                            desplazamiento = DB.XYZ(translation.X * paso, translation.Y * paso, translation.Z * paso)
                            copied = DB.ElementTransformUtils.CopyElement(doc, eid, desplazamiento)
                            if copied:
                                for cid in copied:
                                    new_element_ids.append(get_element_id_value(cid))
                    elif operation == "rotate":
                        DB.ElementTransformUtils.RotateElement(doc, eid, axis_line, angle_rad)
                    else:
                        DB.ElementTransformUtils.MirrorElement(doc, eid, plane)

            # Verificacion: releer bbox de cada elemento
            despues = [describir_elemento(doc, eid) for eid in elem_id_list]
            desajustes = []
            if operation == "move":
                esperado = detalle["vector_mm"]
                for a, d in zip(antes, despues):
                    ca = _centro(a.get("bbox_mm")) if a else None
                    cd = _centro(d.get("bbox_mm")) if d else None
                    if not ca or not cd:
                        continue
                    for eje in ("x", "y", "z"):
                        if abs((cd[eje] - ca[eje]) - esperado[eje]) > 1.0:
                            desajustes.append(
                                "element {}: moved {} on {} instead of {}".format(
                                    a["id"], round(cd[eje] - ca[eje], 1), eje, esperado[eje]
                                )
                            )
                            break
            elif operation in ("rotate", "mirror"):
                for a, d in zip(antes, despues):
                    if d is None:
                        desajustes.append("element {} no longer exists".format(a["id"]))

            resultado = {
                "operation": operation,
                "count": len(elem_id_list),
                "antes": antes,
                "despues": despues,
                "ok": not desajustes,
                "message": "{} {} element{}".format(
                    {"move": "Moved", "copy": "Copied", "rotate": "Rotated", "mirror": "Mirrored",
                     "array": "Arrayed"}[operation],
                    len(elem_id_list),
                    "s" if len(elem_id_list) != 1 else "",
                ),
            }
            resultado.update(detalle)
            if operation in ("copy", "array"):
                esperadas = len(elem_id_list) * (count - 1 if operation == "array" else 1)
                creados = resultado_creacion(doc, new_element_ids)
                resultado["new_element_ids"] = new_element_ids
                resultado["creados"] = creados["creados"]
                resultado["ok"] = creados["ok"] and len(new_element_ids) == esperadas
                resultado["verificacion"] = creados["verificacion"]
                if len(new_element_ids) != esperadas:
                    resultado["verificacion"] = {
                        "coincide": False,
                        "detalle": "Requested {} copies, Revit returned {}".format(
                            esperadas, len(new_element_ids)
                        ),
                    }
            elif desajustes:
                resultado["verificacion"] = {"coincide": False, "detalle": "; ".join(desajustes)}
            else:
                resultado["verificacion"] = {"coincide": True}
            return resultado

        return ejecutar(doc, "/transform_elements/", request, cuerpo)

    logger.info("Transform routes registered successfully")
