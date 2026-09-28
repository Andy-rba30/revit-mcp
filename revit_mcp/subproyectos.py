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


def _estan_unidos(doc, a, b):
    try:
        return bool(DB.JoinGeometryUtils.AreElementsJoined(doc, a, b))
    except Exception as error:
        raise EscrituraRechazada("Cannot check join state of {} and {}: {}".format(
            get_element_id_value(a), get_element_id_value(b), error), 400)


def _es_viga(elem):
    """True si el elemento es de Armazon estructural (vigas y arriostres)."""
    bic = getattr(DB.BuiltInCategory, "OST_StructuralFraming", None)
    categoria = getattr(elem, "Category", None)
    if categoria is None or bic is None:
        return False
    try:
        if categoria.BuiltInCategory == bic:
            return True
    except Exception:
        pass
    try:
        return get_element_id_value(categoria.Id) == int(bic)
    except Exception:
        return False


def _recortada(elem, contra):
    """True/False si `elem` ya tiene un recorte contra `contra` (FamilyInstance.GetCopingIds); None si no se sabe."""
    try:
        ids = [get_element_id_value(i) for i in elem.GetCopingIds()]
    except Exception:
        return None
    return get_element_id_value(contra) in ids


def _orden_recorte(a, b):
    """(recortado, contra): se recorta la viga contra el pilar, sea cual sea el orden de element_ids."""
    if _es_viga(b) and not _es_viga(a):
        return b, a
    return a, b


def unir_en_cadena(doc, ctx):
    """JoinGeometryUtils por parejas consecutivas de element_ids[]; con coping=true, FamilyInstance.AddCoping.

    0.5.1: con coping=true solo se recorta (AddCoping sobre la viga, contra el pilar o la otra viga). Revit no une
    la geometria de perfiles de acero ("The elements cannot be joined", validacion 2b en Revit 2027): en acero la
    union es el recorte. Una pareja que Revit rechaza va a `fallidos` y no aborta el lote. Cada pareja ya unida
    (o ya separada con unjoin, o ya recortada) se informa en `skipped_pairs`."""
    data = ctx["data"]
    ids = data.get("element_ids")
    if isinstance(ids, (int, float)) and not isinstance(ids, bool):
        ids = [ids]
    if not isinstance(ids, (list, tuple)) or len(ids) < 2:
        raise EscrituraRechazada("element_ids needs at least 2 elements (they are joined in consecutive pairs)", 400)
    unjoin = bool(data.get("unjoin", False))
    coping = bool(data.get("coping", False))
    if coping and unjoin:
        raise EscrituraRechazada("coping cannot be combined with unjoin", 400)
    comprobar_alcance(data, len(ids), "elementos a unir")
    elementos = []
    for identificador in ids:
        try:
            elem = doc.GetElement(make_element_id(identificador))
        except ValueError as error:
            raise EscrituraRechazada(str(error), 400)
        if elem is None:
            raise EscrituraRechazada("Element {} not found".format(identificador), 404)
        elementos.append(elem)
    parejas = []
    for a, b in zip(elementos, elementos[1:]):
        if get_element_id_value(a) == get_element_id_value(b):
            raise EscrituraRechazada("Consecutive element_ids must differ ({})".format(get_element_id_value(a)), 400)
        if coping and not (isinstance(a, DB.FamilyInstance) and isinstance(b, DB.FamilyInstance)):
            raise EscrituraRechazada(
                "coping needs family instances (steel beams/columns): {} and {}".format(
                    get_element_id_value(a), get_element_id_value(b)), 400,
            )
        pareja = {"a": a, "b": b, "id_a": get_element_id_value(a), "id_b": get_element_id_value(b)}
        if coping:
            recortado, contra = _orden_recorte(a, b)
            ya = _recortada(recortado, contra)
            pareja.update({"recortado": recortado, "contra": contra, "antes": {"coped": ya}, "skip": ya is True})
        else:
            unidos = _estan_unidos(doc, a, b)
            pareja.update({"antes": {"joined": unidos}, "skip": (unidos if not unjoin else not unidos)})
        parejas.append(pareja)
    accion = "cope" if coping else ("unjoin" if unjoin else "join")
    haria = []
    for p in parejas:
        entrada = {"accion": accion, "element_id_a": p["id_a"], "element_id_b": p["id_b"], "antes": p["antes"],
                   "coping": coping, "skip": p["skip"]}
        if coping:
            entrada.update({"coped_element": get_element_id_value(p["recortado"]), "against": get_element_id_value(p["contra"])})
        haria.append(entrada)
    if ctx["simular"]:
        return simulacion(haria, count=len([p for p in parejas if not p["skip"]]), pairs=len(parejas))
    copings = []
    fallos_coping = []
    fallidos = []
    titulo = u"Recortar acero en cadena ({} elementos)" if coping else (
        u"{} geometria en cadena".format("Separar" if unjoin else "Unir") + u" ({} elementos)")
    with transaccion(doc, titulo.format(len(elementos))):
        for pareja in parejas:
            if pareja["skip"]:
                continue
            if coping:
                id_recortado = get_element_id_value(pareja["recortado"])
                id_contra = get_element_id_value(pareja["contra"])
                try:
                    pareja["recortado"].AddCoping(pareja["contra"])
                    copings.append({"element_id": id_recortado, "against": id_contra})
                except Exception as error:
                    pareja["fallo"] = True
                    fallos_coping.append({"element_id": id_recortado, "against": id_contra, "error": str(error)})
                continue
            try:
                if unjoin:
                    DB.JoinGeometryUtils.UnjoinGeometry(doc, pareja["a"], pareja["b"])
                else:
                    DB.JoinGeometryUtils.JoinGeometry(doc, pareja["a"], pareja["b"])
            except Exception as error:
                pareja["fallo"] = True
                fallidos.append({"element_id_a": pareja["id_a"], "element_id_b": pareja["id_b"], "error": str(error)})
    resultados = []
    desajustes = []
    for pareja in parejas:
        if coping:
            despues = {"coped": _recortada(pareja["recortado"], pareja["contra"])}
            if not pareja["skip"] and not pareja.get("fallo") and despues["coped"] is False:
                desajustes.append("{} against {}: no coping after AddCoping".format(
                    get_element_id_value(pareja["recortado"]), get_element_id_value(pareja["contra"])))
        else:
            despues = {"joined": _estan_unidos(doc, pareja["a"], pareja["b"])}
            if not pareja.get("fallo") and despues["joined"] != (not unjoin):
                desajustes.append("{} and {}: joined={} after {}".format(pareja["id_a"], pareja["id_b"], despues["joined"], accion))
        resultados.append({"element_id_a": pareja["id_a"], "element_id_b": pareja["id_b"],
                           "antes": pareja["antes"], "despues": despues, "skipped": pareja["skip"],
                           "failed": bool(pareja.get("fallo"))})
    hechas = len([p for p in parejas if not p["skip"] and not p.get("fallo")])
    motivo_salto = "already coped" if coping else ("already joined" if not unjoin else "not joined")
    resultado = {
        "accion": accion, "element_ids": [get_element_id_value(e) for e in elementos], "pairs": resultados,
        ("coped_pairs" if coping else ("unjoined_pairs" if unjoin else "joined_pairs")): hechas,
        "skipped_pairs": [{"element_id_a": p["id_a"], "element_id_b": p["id_b"], "reason": motivo_salto}
                          for p in parejas if p["skip"]],
        "fallidos": fallidos,
        "coping": {"requested": coping, "applied": copings, "failed": fallos_coping},
        "ok": not desajustes and not fallidos and not fallos_coping,
        "message": "{} {} pair(s) of {} elements{}".format(
            "Coped" if coping else ("Unjoined" if unjoin else "Joined"), hechas, len(elementos),
            ", {} failed".format(len(fallidos) + len(fallos_coping)) if (fallidos or fallos_coping) else ""),
    }
    if fallidos and not unjoin:
        resultado["nota"] = (u"Revit no une la geometría de perfiles de acero entre sí: para vigas y pilares metálicos "
                             u"usa coping=true (recorte).")
    resultado["verificacion"] = ({"coincide": True} if not desajustes else
                                 {"coincide": False, "detalle": "; ".join(desajustes)})
    return resultado


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
        """Une (o separa con unjoin=true) la geometria de dos elementos, o de una cadena
        `element_ids` por parejas consecutivas (0.5.0, con `coping`). Acepta `simular`."""

        def cuerpo(ctx):
            data = ctx["data"]
            if data.get("element_ids") is not None:
                return unir_en_cadena(doc, ctx)
            id_a = data.get("element_id_a")
            id_b = data.get("element_id_b")
            unjoin = bool(data.get("unjoin", False))
            if id_a is None or id_b is None:
                raise EscrituraRechazada("element_id_a and element_id_b are required (or element_ids for a chain)", 400)
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
