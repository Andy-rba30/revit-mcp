# -*- coding: utf-8 -*-
"""Bloque 4 de la consolidacion (0.4.0): rendimiento medible.

- Copia diferida del .rvt (escritura.CopiaDiferida): se copia en un hilo aparte
  mientras el cuerpo valida y `transaccion` espera a que termine justo antes de
  Commit; la respuesta trae copia.ms, copia.espera_ms y copia.estado. Las rutas
  sin transaccion (RUTAS_COPIA_SINCRONA) siguen copiando antes del cuerpo.
- `timings` de /snapshot/ por etapa e include_bbox.
"""
import json
import os
import threading
import time

import pytest
from pyrevit import DB, routes

import escritura
import modelo_falso as mf
import seguridad

TOKEN = "7" * 64
BIP = DB.BuiltInParameter
BIC = DB.BuiltInCategory


class DocFalso(object):
    def __init__(self, ruta):
        self.PathName = ruta
        self.Title = "Modelo"
        self.IsWorkshared = False
        self.IsReadOnly = False
        self.IsModifiable = False
        self.elementos = {}

    def GetElement(self, elem_id):
        return self.elementos.get(elem_id.Value)


@pytest.fixture(autouse=True)
def _limpio():
    DB.Transaction.creadas = []
    DB.Transaction.resultado_commit = DB.TransactionStatus.Committed
    escritura._retirar_copia_pendiente()
    yield
    escritura._retirar_copia_pendiente()


@pytest.fixture
def doc(tmp_path):
    rvt = tmp_path / "Modelo.rvt"
    rvt.write_bytes(b"RVT" * 1000)
    return DocFalso(str(rvt))


class _CopiaLenta(object):
    """Sustituye _copiar_archivo: tarda `segundos` y apunta cuando empieza y termina."""

    def __init__(self, segundos):
        self.segundos = segundos
        self.inicio = None
        self.fin = None
        self.hilo = None

    def __call__(self, origen, destino):
        self.inicio = time.time()
        self.hilo = threading.current_thread().name
        time.sleep(self.segundos)
        import shutil
        shutil.copy2(origen, destino)
        self.fin = time.time()


# ---------------------------------------------------------------------------
def test_copia_diferida_solapa_con_el_cuerpo_y_espera_antes_de_commit(doc, tmp_path, monkeypatch):
    lenta = _CopiaLenta(0.3)
    monkeypatch.setattr(escritura, "_copiar_archivo", lenta)
    marcas = {}

    def cuerpo(contexto):
        marcas["cuerpo_inicio"] = time.time()
        copia = contexto["copia"]
        assert copia["diferida"] is True and copia["estado"] == "pendiente" and copia["ruta"].endswith(".rvt")
        assert not os.path.isfile(copia["ruta"])           # todavia copiando
        with escritura.transaccion(doc, "Lote") as t:
            marcas["dentro"] = time.time()
            assert not t.HasEnded()
        # al salir del with, Commit espero a la copia
        marcas["commit"] = time.time()
        assert os.path.isfile(copia["ruta"])
        assert copia["estado"] == "terminada"
        return {"antes": 1, "despues": 2}

    respuesta = escritura.ejecutar(doc, "/set_parameters/", {"changes": []}, cuerpo)
    assert respuesta.status == 200, respuesta.data
    copia = respuesta.data["copia"]
    assert copia["diferida"] is True and copia["estado"] == "terminada" and copia["reutilizada"] is False
    assert copia["ms"] >= 250 and copia["espera_ms"] > 0 and "error" not in copia
    assert lenta.hilo != threading.current_thread().name          # se copio en otro hilo
    assert lenta.inicio <= marcas["cuerpo_inicio"] + 0.05           # empezo antes (o a la vez) que el cuerpo
    assert marcas["dentro"] < lenta.fin <= marcas["commit"] + 0.01  # Commit no paso hasta que termino
    assert DB.Transaction.creadas[0].estado == DB.TransactionStatus.Committed
    assert escritura._COPIA_PENDIENTE is None
    assert len(os.listdir(str(tmp_path / "backups"))) == 1


def test_copia_diferida_reutilizada_y_sin_transaccion_se_espera_al_final(doc, tmp_path, monkeypatch):
    lenta = _CopiaLenta(0.1)
    monkeypatch.setattr(escritura, "_copiar_archivo", lenta)

    def cuerpo(contexto):
        return {"ok": True}   # sin transaccion: ejecutar espera al final igualmente

    respuesta = escritura.ejecutar(doc, "/join_geometry/", {}, cuerpo)
    assert respuesta.status == 200 and respuesta.data["copia"]["estado"] == "terminada"
    assert respuesta.data["copia"]["ms"] >= 80 and os.path.isfile(respuesta.data["copia"]["ruta"])
    # segunda escritura en menos de 30 minutos: copia reutilizada, sin hilo
    respuesta = escritura.ejecutar(doc, "/join_geometry/", {}, cuerpo)
    copia = respuesta.data["copia"]
    assert copia["reutilizada"] is True and copia["diferida"] is True and copia["ms"] == 0 and copia["estado"] == "terminada"


def test_rutas_sin_transaccion_copian_antes_del_cuerpo(doc, tmp_path, monkeypatch):
    lenta = _CopiaLenta(0.05)
    monkeypatch.setattr(escritura, "_copiar_archivo", lenta)

    def cuerpo(contexto):
        copia = contexto["copia"]
        assert "diferida" not in copia and os.path.isfile(copia["ruta"])
        assert copia["reutilizada"] or copia["ms"] >= 40
        return {"loaded": True}

    for ruta in escritura.RUTAS_COPIA_SINCRONA:
        respuesta = escritura.ejecutar(doc, ruta, {}, cuerpo)
        assert respuesta.status == 200, (ruta, respuesta.data)
        assert respuesta.data["copia"]["ruta"].endswith(".rvt")


def test_copia_fallida_no_impide_escribir_pero_se_dice(doc, tmp_path, monkeypatch):
    def rompe(origen, destino):
        raise IOError("disco lleno")

    monkeypatch.setattr(escritura, "_copiar_archivo", rompe)

    def cuerpo(contexto):
        with escritura.transaccion(doc, "Cambio"):
            pass
        return {"antes": 1, "despues": 2}

    respuesta = escritura.ejecutar(doc, "/set_parameters/", {}, cuerpo)
    assert respuesta.status == 200, respuesta.data
    assert respuesta.data["copia"]["estado"] == "error" and "disco lleno" in respuesta.data["copia"]["error"]
    assert "disco lleno" in respuesta.data["nota_copia"]
    assert DB.Transaction.creadas[0].estado == DB.TransactionStatus.Committed


def test_error_en_el_cuerpo_espera_la_copia_y_la_devuelve(doc, tmp_path, monkeypatch):
    lenta = _CopiaLenta(0.05)
    monkeypatch.setattr(escritura, "_copiar_archivo", lenta)

    def rechaza(contexto):
        raise escritura.EscrituraRechazada("no", 400)

    respuesta = escritura.ejecutar(doc, "/delete_elements/", {}, rechaza)
    assert respuesta.status == 400 and respuesta.data["copia"]["estado"] == "terminada"
    assert escritura._COPIA_PENDIENTE is None

    def explota(contexto):
        raise RuntimeError("boom")

    respuesta = escritura.ejecutar(doc, "/delete_elements/", {}, explota)
    assert respuesta.status == 500 and respuesta.data["copia"]["estado"] == "terminada"
    assert escritura._COPIA_PENDIENTE is None


def test_simular_no_copia_ni_diferida(doc, tmp_path):
    respuesta = escritura.ejecutar(doc, "/set_parameters/", {"simular": True}, lambda c: escritura.simulacion([]))
    assert respuesta.status == 200 and "copia" not in respuesta.data
    assert not (tmp_path / "backups").exists() and escritura._COPIA_PENDIENTE is None


def test_crear_copia_sincrona_trae_ms(doc, tmp_path):
    copia = escritura.crear_copia(doc, sufijo="a mano", forzar=True)
    assert copia["ms"] >= 0 and copia["ruta"].endswith("_a_mano.rvt") and copia["reutilizada"] is False
    info, trabajo = escritura.planificar_copia(doc)
    assert info["reutilizada"] is True and trabajo is None
    assert escritura.planificar_copia(DocFalso("")) == (None, None)


# ---------------------------------------------------------------------------
# /snapshot/ con timings
# ---------------------------------------------------------------------------
@pytest.fixture
def modelo(tmp_path, monkeypatch):
    mf.activar_spec(monkeypatch)
    rvt = tmp_path / "Modelo.rvt"
    rvt.write_bytes(b"RVT")
    doc = mf.Doc(str(rvt))
    mf.Nivel(doc, 1, u"Nivel 1", 0)
    tipo = mf.TipoMuro(doc, 50, nombre=u"Genérico - 200 mm", categoria=u"Muros", bic=BIC.OST_Walls)
    tipo.FamilyName = u"Muro básico"
    for i in range(10, 40):
        mf.Muro(doc, i, nombre=u"Genérico - 200 mm", categoria=u"Muros", bic=BIC.OST_Walls, tipo_id=50, nivel_id=1,
                caja=mf.caja_mm(0, 0, 0, 5000, 200, 3000),
                parametros=[mf.texto(u"Marca", u"M-{}".format(i), bip=BIP.ALL_MODEL_MARK),
                            mf.longitud_mm(u"Longitud", 5000, bip=BIP.CURVE_ELEM_LENGTH),
                            mf.referencia(u"Restricción de base", 1, bip=BIP.LEVEL_PARAM)])
    return doc


@pytest.fixture
def api():
    seguridad.establecer_token(TOKEN)
    api = routes.API("revit_mcp")
    from instantaneas import register_instantaneas_routes

    register_instantaneas_routes(api)
    return api


def _post(api, ruta, doc, cuerpo):
    datos = dict(cuerpo, token=TOKEN)
    return api.rutas[(ruta, "POST")](doc=doc, request=routes.Request(path=ruta, data=datos))


def test_snapshot_devuelve_timings_por_etapa(api, modelo, tmp_path):
    r = _post(api, "/snapshot/", modelo, {"name": "t"})
    assert r.status == 200, r.data
    timings = r.data["timings"]
    for clave in ("recoleccion_ms", "descripcion_ms", "bbox_ms", "parametros_ms", "hash_ms", "escritura_ms", "total_ms"):
        assert clave in timings and timings[clave] >= 0, clave
    assert timings["elements"] == 31 and r.data["ms"] == timings["total_ms"]
    # tipo y nivel se resuelven una vez por id, no una por elemento: 30 muros comparten tipo 50 y nivel 1
    assert timings["name_lookups"] <= 3
    assert r.data["include_bbox"] is True
    with open(r.data["ruta"], encoding="utf-8") as archivo:
        guardado = json.load(archivo)
    muro = guardado["elements"]["uid-10"]
    assert muro["tipo"] == u"Genérico - 200 mm" and muro["nivel"] == u"Nivel 1" and muro["bbox_mm"]["max"]["x"] == 5000.0
    assert muro["params"][u"Restricción de base"] == u"Nivel 1" and muro["params"][u"Longitud"] == 5000.0
    assert len(muro["hash"]) == 32


def test_snapshot_include_bbox_false_y_diff_lo_respeta(api, modelo):
    r = _post(api, "/snapshot/", modelo, {"name": "sin_bbox", "include_bbox": False})
    assert r.status == 200 and r.data["include_bbox"] is False and r.data["timings"]["bbox_ms"] == 0
    with open(r.data["ruta"], encoding="utf-8") as archivo:
        guardado = json.load(archivo)
    assert guardado["elements"]["uid-10"]["bbox_mm"] is None
    modelo.elementos[10].mover(DB.XYZ(1, 0, 0))
    modelo.elementos[11].LookupParameter(u"Marca").Set(u"M-x")
    r = _post(api, "/diff_snapshots/", modelo, {"a": "sin_bbox"})
    assert r.status == 200 and r.data["counts"]["modified"] == 1       # el movimiento no cuenta sin bbox
    assert r.data["modified"][0]["id"] == 11
