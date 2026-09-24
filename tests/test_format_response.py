# -*- coding: utf-8 -*-
"""format_response devuelve JSON valido para dict y lista, y texto para errores."""
import json

from tools.utils import format_response


def test_dict_devuelve_json_valido():
    texto = format_response({"creados": [{"id": 1, "categoria": u"Muros"}], "ok": True, "count": 1})
    datos = json.loads(texto)
    assert datos["ok"] is True
    assert datos["creados"][0]["categoria"] == u"Muros"
    # ensure_ascii=False: los acentos van tal cual, no como \\uXXXX
    texto = format_response({"nota": u"Habitación"})
    assert u"Habitación" in texto and "\\u" not in texto


def test_lista_devuelve_json_valido():
    texto = format_response([{"id": 1}, {"id": 2}])
    assert json.loads(texto) == [{"id": 1}, {"id": 2}]


def test_respuesta_con_status_success_no_se_convierte_en_texto():
    texto = format_response({"status": "success", "message": "Created 2 element(s)", "creados": []})
    datos = json.loads(texto)
    assert datos["message"] == "Created 2 element(s)"


def test_error_devuelve_texto():
    texto = format_response({"error": "Element 5 not found", "http_status": 404, "available_parameters": ["Mark"]})
    assert texto.startswith("=== ERROR DETAILS ===")
    assert "HTTP: 404" in texto
    assert "Element 5 not found" in texto
    assert "Mark" in texto


def test_status_error_devuelve_texto_con_traceback():
    texto = format_response({"status": "error", "error": "boom", "traceback": "Traceback..."})
    assert "=== ERROR DETAILS ===" in texto
    assert "=== TRACEBACK ===" in texto


def test_get_revit_status_conserva_texto():
    texto = format_response({"status": "active", "health": "healthy", "revit_available": True,
                             "document_title": "Casa", "api_name": "revit_mcp"})
    assert texto.startswith("=== REVIT STATUS ===")
    assert "Document: Casa" in texto


def test_texto_del_puente_se_devuelve_tal_cual():
    assert format_response("Revit no está abierto") == "Revit no está abierto"
