# -*- coding: utf-8 -*-
"""0.6.1: textos con tildes que llegan mal decodificados desde el puente.

httpx >= 0.28 envia el cuerpo JSON en UTF-8 sin escapar (ensure_ascii=False) y
el servidor de pyRevit (IronPython 2.7) lo decodifica byte a byte, de modo que
"Modelo genérico métrico.rft" llegaba como "Modelo genÃ©rico mÃ©trico.rft" y
/family/open/ y /family/build/ respondian 404 (validacion 2c, paso 5).
"""
import asyncio
import json

import pytest
from pyrevit import routes

import seguridad

TOKEN = "u" * 64


def _roto(texto, codificacion="latin-1"):
    """Lo que ve el manejador cuando el servidor lee UTF-8 como Latin-1/cp1252."""
    return texto.encode("utf-8").decode(codificacion)


@pytest.mark.parametrize("texto", [
    u"Modelo genérico métrico.rft",
    u"Muro básico",
    u"Diámetro perno + 2 mm",
    u"Centro (delante/detrás)",
    u"Conexión estructural métrica.rft",
])
def test_repara_texto_decodificado_como_latin1(texto):
    assert seguridad.reparar_utf8(_roto(texto)) == texto


def test_repara_texto_decodificado_como_cp1252():
    # "Ñ" = C3 91 y 0x91 en cp1252 es U+2018; Latin-1 no basta.
    assert seguridad.reparar_utf8(_roto(u"AÑADIR Ñu", "cp1252")) == u"AÑADIR Ñu"


@pytest.mark.parametrize("texto", [
    u"Modelo genérico métrico.rft", u"Muro básico", u"ASCII puro", u"", u"50 €", u"Ã sola",
])
def test_texto_correcto_no_cambia(texto):
    assert seguridad.reparar_utf8(texto) == texto


def test_repara_claves_listas_y_deja_otros_tipos():
    datos = {_roto(u"Diámetro"): [_roto(u"Detrás"), 3, None, {_roto(u"Ñ"): True}], "n": 1.5}
    assert seguridad.reparar_utf8(datos) == {u"Diámetro": [u"Detrás", 3, None, {u"Ñ": True}], "n": 1.5}


def test_la_ruta_protegida_recibe_el_texto_reparado():
    recibido = {}

    @seguridad.requiere_token
    def manejador(doc, request):
        recibido.update(datos=request.data, consulta=request.query_params)
        return routes.make_response(data={"ok": True})

    seguridad.establecer_token(TOKEN)
    peticion = routes.Request(
        path="/family/open/",
        data={"template": _roto(u"Modelo genérico métrico.rft"), "name": u"Placa base 2C", "token": TOKEN},
        query_params={"contains": _roto(u"genérico")},
    )
    assert manejador(doc=None, request=peticion).status == 200
    assert recibido["datos"] == {"template": u"Modelo genérico métrico.rft", "name": u"Placa base 2C"}
    assert recibido["consulta"] == {"contains": u"genérico"}


def test_cuerpo_json_en_texto_tambien_se_repara():
    recibido = {}

    @seguridad.requiere_token
    def manejador(request):
        recibido.update(request.data)
        return routes.make_response(data={"ok": True})

    seguridad.establecer_token(TOKEN)
    cuerpo = json.dumps({"family_name": u"Muro básico", "token": TOKEN}, ensure_ascii=False)
    assert manejador(request=routes.Request(path="/family/open/", data=_roto(cuerpo))).status == 200
    assert recibido == {"family_name": u"Muro básico"}


def test_family_open_con_plantilla_mal_decodificada(tmp_path, monkeypatch):
    """Reproduce el fallo del paso 5 de VALIDACION_2C sobre el modelo simulado."""
    from test_familias import PLANTILLA, _post
    import familias
    import modelo_falso as mf
    from pyrevit import DB

    mf.activar_spec(monkeypatch)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    rvt = tmp_path / "Modelo.rvt"
    rvt.write_bytes(b"RVT")
    doc = mf.Doc(str(rvt))
    carpeta = tmp_path / "Plantillas"
    carpeta.mkdir()
    (carpeta / PLANTILLA).write_bytes(b"RFT")
    doc.Application.FamilyTemplatePath = str(carpeta)
    familias.DOCUMENTOS_ABIERTOS.clear()
    DB.Transaction.creadas = []
    api = routes.API("revit_mcp")
    familias.register_familias_routes(api)
    seguridad.establecer_token("6" * 64)

    r = _post(api, "/family/open/", doc, {"template": _roto(PLANTILLA), "name": u"Placa base 2C", "simular": True})
    assert r.status == 200, r.data
    assert r.data["haria"][0]["template"].endswith(PLANTILLA)


def test_el_puente_envia_json_solo_ascii(monkeypatch):
    httpx = pytest.importorskip("httpx")
    pytest.importorskip("mcp")
    import main

    enviados = []

    def responder(peticion):
        enviados.append(peticion)
        return httpx.Response(200, json={"ok": True})

    cliente = httpx.AsyncClient(base_url="http://revit", transport=httpx.MockTransport(responder))
    monkeypatch.setattr(main, "_get_client", lambda: cliente)
    asyncio.run(main._enviar("POST", "/family/open/", TOKEN, data={"template": u"Modelo genérico métrico.rft"}))
    cuerpo = enviados[0].content
    assert cuerpo.isascii()
    assert b"\\u00e9" in cuerpo
    assert enviados[0].headers["content-type"] == "application/json"
    assert json.loads(cuerpo.decode("ascii")) == {"template": u"Modelo genérico métrico.rft", "token": TOKEN}
