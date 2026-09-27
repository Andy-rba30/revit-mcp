# -*- coding: UTF-8 -*-
"""
Mantenimiento Module for Revit MCP

  POST /purge_unused/  max_rounds -> purga tipos y familias sin uso
  POST /backup/        suffix     -> copia del .rvt a demanda (misma copia que preparar)

purge_unused usa la regla del PerformanceAdviser "Project contains unused
families and types" (id e8c63650-70b7-435a-9010-ec97660c1bda); si no esta
disponible, itera los FamilySymbol sin ejemplares. Con simular=true solo lista.
"""

from utils import get_element_name, get_element_id_value, make_element_id, nombre_familia, etiqueta_tipo
from seguridad import requiere_token
from escritura import (
    ejecutar, transaccion, simulacion, EscrituraRechazada, crear_copia, registrar,
    es_simulacion, ruta_documento, es_compartido, datos_peticion, comprobar_alcance,
)
from pyrevit import routes, revit, DB
from System.Collections.Generic import List
import time
import logging

logger = logging.getLogger(__name__)

REGLA_SIN_USO = "e8c63650-70b7-435a-9010-ec97660c1bda"
MAX_LISTADOS = 500


def _ids_sin_uso_adviser(doc):
    """Ids que la regla del PerformanceAdviser marca como sin uso, o None."""
    try:
        import System

        adviser = DB.PerformanceAdviser.GetPerformanceAdviser()
        regla = None
        for rule_id in adviser.GetAllRuleIds():
            try:
                if str(rule_id.Guid).lower() == REGLA_SIN_USO:
                    regla = rule_id
                    break
            except Exception:
                continue
        if regla is None:
            return None
        reglas = List[DB.PerformanceAdviserRuleId]()
        reglas.Add(regla)
        ids = set()
        for fallo in adviser.ExecuteRules(doc, reglas):
            try:
                for eid in fallo.GetFailingElements():
                    ids.add(get_element_id_value(eid))
            except Exception:
                continue
        return ids
    except Exception as error:
        logger.warning("PerformanceAdviser no disponible: %s", str(error))
        return None


def _ids_simbolos_sin_uso(doc):
    """Reserva: FamilySymbol sin ejemplares (no toca tipos de sistema)."""
    usados = set()
    for inst in DB.FilteredElementCollector(doc).OfClass(DB.FamilyInstance).WhereElementIsNotElementType():
        try:
            usados.add(get_element_id_value(inst.GetTypeId()))
        except Exception:
            continue
    sin_uso = set()
    for simbolo in DB.FilteredElementCollector(doc).OfClass(DB.FamilySymbol).ToElements():
        try:
            identificador = get_element_id_value(simbolo)
            if identificador not in usados:
                sin_uso.add(identificador)
        except Exception:
            continue
    return sin_uso


def _describir(doc, ids):
    lista = []
    for identificador in sorted(ids)[:MAX_LISTADOS]:
        elem = doc.GetElement(make_element_id(identificador))
        if elem is None:
            continue
        datos = {"id": identificador, "nombre": get_element_name(elem), "clase": type(elem).__name__}
        try:
            datos["categoria"] = get_element_name(elem.Category) if elem.Category else None
        except Exception:
            pass
        familia = nombre_familia(elem)
        if familia:
            datos["familia"] = familia
        lista.append(datos)
    return lista


def register_mantenimiento_routes(api):
    """Register purge and backup routes with the API."""

    @api.route("/purge_unused/", methods=["POST"])
    @requiere_token
    def purge_unused(doc, request):
        """Purga tipos y familias sin uso. Acepta `simular` (solo lista)."""

        def cuerpo(ctx):
            data = ctx["data"]
            try:
                max_rounds = int(data.get("max_rounds", 3) or 3)
            except (TypeError, ValueError):
                max_rounds = 3
            max_rounds = max(1, min(max_rounds, 10))

            ids = _ids_sin_uso_adviser(doc)
            metodo = "PerformanceAdviser"
            if ids is None:
                ids = _ids_simbolos_sin_uso(doc)
                metodo = "FamilySymbol sin ejemplares (reserva)"
            candidatos = _describir(doc, ids)
            if ctx["simular"]:
                return simulacion(
                    [{"accion": "purgar", "metodo": metodo, "count": len(ids), "elementos": candidatos,
                      "max_rounds": max_rounds}],
                    count=len(ids),
                )
            if not ids:
                return {"ok": True, "eliminados": [], "count": 0, "rounds": 0, "metodo": metodo,
                        "message": "Nothing to purge"}
            if metodo != "PerformanceAdviser":
                # La reserva "FamilySymbol sin FamilyInstance" no distingue tipos de etiqueta,
                # perfiles de barandillas ni familias anidadas: borrarlos arrastra las etiquetas
                # y barandillas que los usan. Solo sirve para listar candidatos.
                raise EscrituraRechazada(
                    "PerformanceAdviser is not available in this Revit, and the fallback list "
                    "(family types without instances) is not safe to delete automatically: "
                    "it includes tag types, railing profiles and nested families. Use simular=true "
                    "to review the list and purge from Revit (Manage > Purge Unused).",
                    409,
                    {"metodo": metodo, "count": len(ids)},
                )
            comprobar_alcance(data, len(ids), "tipos a purgar")

            eliminados = []
            rondas = 0
            pendientes = ids
            while pendientes and rondas < max_rounds:
                rondas += 1
                borrados_ronda = 0
                # Una transaccion por ronda: ExecuteRules del PerformanceAdviser se llama
                # entre rondas, fuera de la transaccion.
                with transaccion(doc, "Purgar sin uso (ronda {})".format(rondas)):
                    for identificador in sorted(pendientes):
                        elem_id = make_element_id(identificador)
                        if doc.GetElement(elem_id) is None:
                            continue
                        try:
                            resultado = doc.Delete(elem_id)
                            for borrado in resultado:
                                eliminados.append(get_element_id_value(borrado))
                            borrados_ronda += 1
                        except Exception as error:
                            logger.debug("No se pudo purgar %s: %s", identificador, str(error))
                if borrados_ronda == 0:
                    break
                pendientes = _ids_sin_uso_adviser(doc)
                if pendientes is None:
                    break

            eliminados = sorted(set(eliminados))
            restantes = _ids_sin_uso_adviser(doc)
            return {
                "ok": True,
                "metodo": metodo,
                "rounds": rondas,
                "candidatos": candidatos,
                "eliminados": eliminados[:MAX_LISTADOS],
                "count": len(eliminados),
                "sin_uso_restantes": len(restantes or []),
                "message": "Purged {} element(s) in {} round(s)".format(len(eliminados), rondas),
                "verificacion": {"coincide": True, "sin_uso_restantes": len(restantes or [])},
            }

        return ejecutar(doc, "/purge_unused/", request, cuerpo)

    @api.route("/backup/", methods=["POST"])
    @requiere_token
    def create_backup(doc, request):
        """Copia del .rvt guardado a demanda (backups\\<nombre>_<marca>_<suffix>.rvt)."""
        inicio = time.time()
        if doc is None:
            return routes.make_response(data={"error": "No active Revit document"}, status=503)
        try:
            data = datos_peticion(request)
        except EscrituraRechazada as rechazo:
            return routes.make_response(data={"error": rechazo.mensaje}, status=rechazo.status)
        sufijo = data.get("suffix")
        simulado = es_simulacion(data)
        origen = ruta_documento(doc)

        def _responder(cuerpo, status=200):
            ms = int((time.time() - inicio) * 1000)
            registrar(doc, "/backup/", data, status < 400, ms, error=cuerpo.get("error"),
                      resultado_resumen=cuerpo, simulado=simulado)
            cuerpo["ms"] = ms
            return routes.make_response(data=cuerpo, status=status)

        if not origen:
            return _responder({"error": "The document has never been saved: there is no file to copy. "
                                        "Save it first (save_document)."}, 400)
        if es_compartido(doc):
            return _responder({"error": "Workshared model: no local copy is made, rely on the central "
                                        "model backups."}, 400)
        if simulado:
            return _responder(simulacion([{"accion": "backup", "origen": origen, "suffix": sufijo}]))
        try:
            copia = crear_copia(doc, sufijo=sufijo, forzar=True)
        except Exception as error:
            return _responder({"error": "Backup failed: {}".format(error)}, 500)
        if copia is None:
            return _responder({"error": "Backup not possible (file missing on disk?)"}, 400)
        return _responder({"ok": True, "copia": copia, "message": "Backup written to {}".format(copia["ruta"])})

    logger.info("Mantenimiento routes registered successfully")
