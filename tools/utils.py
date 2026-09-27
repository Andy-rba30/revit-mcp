# -*- coding: utf-8 -*-
"""Utilidades de las herramientas MCP: formato de respuesta, tiempos de espera y
`ms_puente` (tiempo total de la llamada HTTP visto desde el puente)."""

import json
import time

# Tiempos de espera (segundos) que cada herramienta pasa a revit_get/revit_post.
# main.py los reexporta y documenta; las herramientas los importan de aqui para
# no crear una importacion circular (main -> tools -> main).
TIMEOUT_LECTURA = 30.0      # consultas: status, listados, propiedades...
TIMEOUT_ESCRITURA = 120.0   # set_parameters, create_elements, transform_elements y demas cambios
TIMEOUT_LARGO = 600.0       # export, check_clashes, analyze_model(materials), link_file,
                            # load_family, maintain_model, execute_revit_code,
                            # create_elements(toposolid), snapshot_model, diff_snapshots,
                            # import_from_civil y run_macro (salvo timeout_s propio).
                            # No es un sustituto de un limite de elementos: cada
                            # lote y macro aplica comprobar_alcance (200 salvo forzar).


class Cronometro(object):
    """Mide el tiempo total que una herramienta pasa llamando a Revit (una o varias rutas)."""

    def __init__(self):
        self.inicio = time.perf_counter()

    def ms(self):
        return int((time.perf_counter() - self.inicio) * 1000)


async def con_tiempo(coro):
    """Espera la llamada a Revit y anade `ms_puente` (ms desde el puente) a la respuesta dict.

    `ms` (dentro de la respuesta) es el tiempo que Revit tardo en el manejador;
    `ms_puente` incluye ademas la red y la serializacion JSON en ambos sentidos.
    """
    crono = Cronometro()
    respuesta = await coro
    if isinstance(respuesta, dict):
        respuesta["ms_puente"] = crono.ms()
    return respuesta


def _texto_estado(response):
    """Formato de texto de get_revit_status (se conserva a proposito)."""
    status_parts = ["=== REVIT STATUS ==="]
    status_parts.append("Status: {}".format(response.get("status", "Unknown")))
    status_parts.append("Health: {}".format(response.get("health", "Unknown")))
    if "api_name" in response:
        status_parts.append("API: {}".format(response["api_name"]))
    if "document_title" in response:
        status_parts.append("Document: {}".format(response["document_title"]))
    if "revit_available" in response:
        status_parts.append("Revit Available: {}".format(response["revit_available"]))
    known_fields = {"status", "health", "api_name", "document_title", "revit_available"}
    other_fields = set(response.keys()) - known_fields
    if other_fields:
        status_parts.append("")
        for field in sorted(other_fields):
            status_parts.append("{}: {}".format(field.replace("_", " ").title(), response[field]))
    return "\n".join(status_parts)


def _texto_error(response):
    """Formato de texto de los errores (=== ERROR DETAILS ===)."""
    error_msg = response.get("error", "Unknown error occurred")
    traceback_info = response.get("traceback", "")
    details = response.get("details", "")
    status = response.get("status", "unknown")

    error_parts = ["=== ERROR DETAILS ==="]
    error_parts.append("Status: {}".format(status))
    if "http_status" in response:
        error_parts.append("HTTP: {}".format(response["http_status"]))
    error_parts.append("Error: {}".format(error_msg))
    if details:
        error_parts.append("Details: {}".format(details))
    if traceback_info:  # Code execution error with traceback
        error_parts.append("\n=== TRACEBACK ===")
        error_parts.append(traceback_info)

    debug_fields = ["code_attempted", "endpoint", "request_data", "response_code", "hints",
                    "open_transaction", "available_parameters", "available_levels",
                    "available_views", "available_families", "available_macros", "limite",
                    "cantidad", "copia", "index", "kind", "fallidos", "faltan", "sobran"]
    for field in debug_fields:
        if field in response:
            error_parts.append("{}: {}".format(
                field.replace("_", " ").title(), _json(response[field])))

    excluidos = set(debug_fields) | {"error", "traceback", "details", "status", "http_status"}
    response_keys = set(response.keys()) - excluidos
    if response_keys:
        error_parts.append("\n=== ADDITIONAL RESPONSE DATA ===")
        for key in sorted(response_keys):
            error_parts.append("{}: {}".format(key, _json(response[key])))
    return "\n".join(error_parts)


def _json(valor):
    return json.dumps(valor, ensure_ascii=False, default=str)


def format_response(response, ms_puente=None):
    """Formatea la respuesta de revit_get/revit_post para devolverla al agente.

    - dict o list -> JSON (`json.dumps(..., ensure_ascii=False)`), para que el
      agente reciba datos estructurados y no representaciones de Python;
    - dict de error (clave "error" o status error/failed/failure/exception) ->
      texto "=== ERROR DETAILS ===";
    - respuesta de get_revit_status (status "active" + "health") -> texto
      "=== REVIT STATUS ===";
    - cualquier otra cosa (texto del puente, p. ej. "Revit no está abierto")
      -> str.

    `ms_puente` (opcional): tiempo total de la llamada HTTP medido desde el
    puente; se anade al dict al lado del `ms` que devuelve Revit para separar
    el tiempo de Revit del de red y serializacion.
    """
    if isinstance(response, dict):
        if ms_puente is not None:
            response["ms_puente"] = int(ms_puente)
        status = str(response.get("status", "") or "").lower()
        has_error = (bool(response.get("error")) or
                     status in ("error", "failed", "failure", "exception"))
        if has_error:
            return _texto_error(response)
        if status == "active" and "health" in response:
            return _texto_estado(response)
        return json.dumps(response, ensure_ascii=False, indent=2, default=str)
    if isinstance(response, list):
        return json.dumps(response, ensure_ascii=False, indent=2, default=str)
    return str(response)


def es_error(response):
    """True si la respuesta es un texto del puente o un dict con `error`."""
    if not isinstance(response, dict):
        return True
    return bool(response.get("error"))
