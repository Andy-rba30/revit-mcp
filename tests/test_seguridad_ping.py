# -*- coding: utf-8 -*-
"""0.3.3: GET /ping/ sin token y avisos de 401 limitados a uno por ruta y minuto."""
import logging

import pytest
from pyrevit import routes

import seguridad
from status import register_status_routes

TOKEN = "e" * 64


@pytest.fixture
def api():
    seguridad.establecer_token(TOKEN)
    seguridad._rechazos.clear()
    api = routes.API("revit_mcp")
    register_status_routes(api)
    return api


def test_ping_sin_token_responde_200(api):
    r = api.rutas[("/ping/", "GET")]()
    assert r.status == 200 and r.data == {"ok": True, "api_name": "revit_mcp"}
    # /status/ sigue exigiendo token
    r = api.rutas[("/status/", "GET")](doc=None, request=routes.Request(path="/status/", data=None, query_params={}))
    assert r.status == 401


def test_401_se_avisa_una_vez_por_minuto(api, caplog, monkeypatch):
    reloj = [1000.0]
    monkeypatch.setattr(seguridad.time, "time", lambda: reloj[0])
    caplog.set_level(logging.DEBUG, logger="seguridad")
    ruta = api.rutas[("/status/", "GET")]
    for _ in range(5):
        assert ruta(doc=None, request=routes.Request(path="/status/", data=None, query_params={"token": "malo"})).status == 401
    avisos = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(avisos) == 1 and "(1 en el ultimo minuto" in avisos[0].getMessage()
    assert len([r for r in caplog.records if r.levelno == logging.DEBUG]) == 4
    reloj[0] += 61
    assert ruta(doc=None, request=routes.Request(path="/status/", data=None, query_params={"token": "malo"})).status == 401
    avisos = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(avisos) == 2 and "(5 en el ultimo minuto" in avisos[1].getMessage()
    # otra ruta tiene su propio contador
    assert seguridad.registrar_rechazo("/otra/") == (True, 1)
