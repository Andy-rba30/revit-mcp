# -*- coding: utf-8 -*-
"""Pruebas en CPython (sin Revit) de revit_mcp/escritura.py.

Usa el paquete `pyrevit` simulado de tests/fakes: DB.Transaction registra
Start/Commit/RollBack y `routes.make_response` devuelve un objeto Response.
"""
import io
import json
import os

import pytest
from pyrevit import DB

import escritura


class DocFalso(object):
    """Documento con lo minimo que consulta escritura.py."""

    def __init__(self, ruta="", compartido=False, solo_lectura=False):
        self.PathName = ruta
        self.Title = os.path.splitext(os.path.basename(ruta))[0] if ruta else "Proyecto1"
        self.IsWorkshared = compartido
        self.IsReadOnly = solo_lectura
        self.IsModifiable = False
        self.transacciones = []
        self.elementos = {}

    def GetElement(self, elem_id):
        return self.elementos.get(elem_id.Value)


@pytest.fixture(autouse=True)
def _reiniciar_transacciones():
    DB.Transaction.creadas = []
    DB.Transaction.resultado_commit = DB.TransactionStatus.Committed
    yield
    DB.Transaction.resultado_commit = DB.TransactionStatus.Committed


@pytest.fixture
def doc_guardado(tmp_path):
    rvt = tmp_path / "Modelo.rvt"
    rvt.write_bytes(b"RVT" * 100)
    return DocFalso(str(rvt))


# ---------------------------------------------------------------------------
# transaccion
# ---------------------------------------------------------------------------
def test_transaccion_confirma_y_usa_prefijo_ia(doc_guardado):
    with escritura.transaccion(doc_guardado, "Crear muros") as t:
        assert t.HasStarted()
        assert doc_guardado.IsModifiable is True
    assert t.nombre == u"IA: Crear muros"
    assert t.estado == DB.TransactionStatus.Committed
    assert doc_guardado.IsModifiable is False


def test_transaccion_no_duplica_prefijo_y_recorta(doc_guardado):
    nombre_largo = "IA: " + "x" * 100
    with escritura.transaccion(doc_guardado, nombre_largo) as t:
        pass
    assert t.nombre.startswith(u"IA: ")
    assert t.nombre.count(u"IA: ") == 1
    assert len(t.nombre) == len(u"IA: ") + escritura.LONGITUD_MAX_NOMBRE


def test_transaccion_revierte_ante_excepcion(doc_guardado):
    with pytest.raises(ValueError):
        with escritura.transaccion(doc_guardado, "Borrar") as t:
            raise ValueError("fallo del cuerpo")
    assert t.estado == DB.TransactionStatus.RolledBack
    assert t.HasEnded()
    assert doc_guardado.IsModifiable is False


def test_transaccion_lanza_si_revit_revierte(doc_guardado):
    DB.Transaction.resultado_commit = DB.TransactionStatus.RolledBack
    with pytest.raises(escritura.TransaccionRevertida):
        with escritura.transaccion(doc_guardado, "Cambio invalido"):
            pass


# ---------------------------------------------------------------------------
# preparar y copias
# ---------------------------------------------------------------------------
def test_preparar_rechaza_transaccion_abierta(doc_guardado):
    doc_guardado.IsModifiable = True
    with pytest.raises(escritura.EscrituraRechazada) as info:
        escritura.preparar(doc_guardado, "/set_parameter/")
    assert info.value.status == 409
    assert info.value.extra.get("open_transaction") is True


def test_preparar_rechaza_solo_lectura(doc_guardado):
    doc_guardado.IsReadOnly = True
    with pytest.raises(escritura.EscrituraRechazada) as info:
        escritura.preparar(doc_guardado, "/set_parameter/")
    assert info.value.status == 409


def test_preparar_sin_documento():
    with pytest.raises(escritura.EscrituraRechazada) as info:
        escritura.preparar(None, "/x/")
    assert info.value.status == 503


def test_preparar_copia_y_reutiliza(doc_guardado, tmp_path):
    contexto = escritura.preparar(doc_guardado, "/create_line/")
    copia = contexto["copia"]
    assert copia is not None
    assert copia["reutilizada"] is False
    assert os.path.isfile(copia["ruta"])
    assert os.path.dirname(copia["ruta"]) == str(tmp_path / "backups")
    assert os.path.basename(copia["ruta"]).startswith("Modelo_")
    assert copia["refleja_guardado_de"]
    assert "ultimo guardado" in copia["nota"]

    # Segunda llamada en menos de 30 minutos: misma copia, no se crea otra
    segundo = escritura.preparar(doc_guardado, "/create_line/")
    assert segundo["copia"]["reutilizada"] is True
    assert segundo["copia"]["ruta"] == copia["ruta"]
    assert len(os.listdir(str(tmp_path / "backups"))) == 1


def test_crear_copia_forzada_con_sufijo_y_poda(doc_guardado, tmp_path, monkeypatch):
    marcas = iter(range(1, 20))

    class _Reloj(object):
        @staticmethod
        def ahora():
            import datetime

            return datetime.datetime(2026, 1, 1, 10, 0, next(marcas))

    monkeypatch.setattr(escritura, "_ahora", _Reloj.ahora)
    rutas = []
    for _ in range(escritura.MAX_COPIAS + 3):
        rutas.append(escritura.crear_copia(doc_guardado, sufijo="antes de purgar", forzar=True)["ruta"])
    assert rutas[-1].endswith("_antes_de_purgar.rvt")
    existentes = os.listdir(str(tmp_path / "backups"))
    assert len(existentes) == escritura.MAX_COPIAS
    # Se conservan las mas nuevas
    assert os.path.basename(rutas[-1]) in existentes
    assert os.path.basename(rutas[0]) not in existentes


def test_preparar_no_copia_modelo_compartido(tmp_path):
    rvt = tmp_path / "Central_local.rvt"
    rvt.write_bytes(b"x")
    doc = DocFalso(str(rvt), compartido=True)
    contexto = escritura.preparar(doc, "/create_grid/")
    assert contexto["copia"] is None
    assert "central" in contexto["nota"]
    assert not (tmp_path / "backups").exists()


def test_preparar_documento_sin_guardar():
    doc = DocFalso("")
    contexto = escritura.preparar(doc, "/create_grid/")
    assert contexto["copia"] is None
    assert "no esta guardado" in contexto["nota"]


# ---------------------------------------------------------------------------
# registrar / rotacion del log
# ---------------------------------------------------------------------------
def test_registrar_escribe_json_junto_al_rvt(doc_guardado, tmp_path):
    destino = escritura.registrar(
        doc_guardado, "/set_parameter/", {"element_id": 1, "value": u"Ñandú"},
        True, 12, resultado_resumen={"antes": "a", "despues": u"Ñandú"},
    )
    assert destino == str(tmp_path / "mcp_log.jsonl")
    with io.open(destino, "r", encoding="utf-8") as archivo:
        lineas = archivo.read().splitlines()
    assert len(lineas) == 1
    entrada = json.loads(lineas[0])
    assert entrada["ruta"] == "/set_parameter/"
    assert entrada["ok"] is True
    assert entrada["ms"] == 12
    assert entrada["args"]["value"] == u"Ñandú"
    assert entrada["resultado"]["despues"] == u"Ñandú"
    assert entrada["documento"] == "Modelo"
    assert "simulado" not in entrada


def test_registrar_documento_sin_guardar_usa_localappdata(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    doc = DocFalso("")
    destino = escritura.registrar(doc, "/x/", {}, False, 1, error="boom", simulado=True)
    assert destino == str(tmp_path / "RevitMcp" / "mcp_log.jsonl")
    entrada = json.loads(io.open(destino, encoding="utf-8").read())
    assert entrada["error"] == "boom"
    assert entrada["simulado"] is True


def test_registrar_guarda_codigo_completo_y_recorta_listas(doc_guardado):
    codigo = "x = 1\n" * 1000
    destino = escritura.registrar(
        doc_guardado, "/execute_code/",
        {"code": codigo, "ids": list(range(500))}, True, 3,
    )
    entrada = json.loads(io.open(destino, encoding="utf-8").read())
    assert entrada["args"]["code"] == codigo
    assert len(entrada["args"]["ids"]) == 51
    assert entrada["args"]["ids"][-1].endswith("mas)")


def test_log_rota_a_5mb(doc_guardado, tmp_path, monkeypatch):
    monkeypatch.setattr(escritura, "LOG_MAX_BYTES", 600)
    for i in range(20):
        escritura.registrar(doc_guardado, "/x/", {"i": i, "relleno": "r" * 100}, True, 1)
    principal = tmp_path / "mcp_log.jsonl"
    rotado = tmp_path / "mcp_log.jsonl.1"
    assert principal.exists()
    assert rotado.exists()
    assert principal.stat().st_size < 600 + 400
    ruta, entradas = escritura.leer_log(doc_guardado, last_n=3)
    assert ruta == str(principal)
    # Tras la rotacion el archivo principal solo tiene las entradas mas nuevas
    assert 0 < len(entradas) <= 3
    assert entradas[-1]["args"]["i"] == 19


# ---------------------------------------------------------------------------
# ejecutar
# ---------------------------------------------------------------------------
def test_ejecutar_exito_incluye_copia_ms_y_registra(doc_guardado, tmp_path):
    def cuerpo(contexto):
        assert contexto["simular"] is False
        assert contexto["copia"] is not None
        with escritura.transaccion(doc_guardado, "Set parameter"):
            pass
        return {"antes": "a", "despues": "b"}

    respuesta = escritura.ejecutar(doc_guardado, "/set_parameter/", {"element_id": 1}, cuerpo)
    assert respuesta.status == 200
    assert respuesta.data["ok"] is True
    assert respuesta.data["copia"]["ruta"].endswith(".rvt")
    assert "ms" in respuesta.data
    assert DB.Transaction.creadas[0].nombre == u"IA: Set parameter"
    entrada = json.loads(io.open(str(tmp_path / "mcp_log.jsonl"), encoding="utf-8").read())
    assert entrada["ok"] is True and entrada["resultado"]["despues"] == "b"


def test_ejecutar_simular_no_prepara_ni_abre_transaccion(doc_guardado, tmp_path):
    def cuerpo(contexto):
        assert contexto["simular"] is True
        return escritura.simulacion([{"accion": "set", "parametro": "Mark"}])

    respuesta = escritura.ejecutar(doc_guardado, "/set_parameter/", {"simular": True}, cuerpo)
    assert respuesta.status == 200
    assert respuesta.data["simulado"] is True
    assert respuesta.data["haria"][0]["parametro"] == "Mark"
    assert "copia" not in respuesta.data
    assert DB.Transaction.creadas == []
    assert not (tmp_path / "backups").exists()
    entrada = json.loads(io.open(str(tmp_path / "mcp_log.jsonl"), encoding="utf-8").read())
    assert entrada["simulado"] is True


def test_ejecutar_rechazo_controlado(doc_guardado):
    def cuerpo(contexto):
        raise escritura.EscrituraRechazada(u"Element 5 not found", 404, {"element_id": 5})

    respuesta = escritura.ejecutar(doc_guardado, "/delete_elements/", {}, cuerpo)
    assert respuesta.status == 404
    assert respuesta.data["error"] == u"Element 5 not found"
    assert respuesta.data["element_id"] == 5


def test_ejecutar_excepcion_devuelve_500_con_traceback(doc_guardado):
    def cuerpo(contexto):
        with escritura.transaccion(doc_guardado, "Fallo"):
            raise RuntimeError("explota")

    respuesta = escritura.ejecutar(doc_guardado, "/x/", {}, cuerpo)
    assert respuesta.status == 500
    assert "explota" in respuesta.data["error"]
    assert "Traceback" in respuesta.data["traceback"]
    assert DB.Transaction.creadas[0].estado == DB.TransactionStatus.RolledBack


def test_ejecutar_409_si_hay_transaccion_abierta(doc_guardado):
    doc_guardado.IsModifiable = True
    llamado = []
    respuesta = escritura.ejecutar(doc_guardado, "/x/", {}, lambda ctx: llamado.append(1))
    assert respuesta.status == 409
    assert llamado == []


def test_ejecutar_sin_documento():
    respuesta = escritura.ejecutar(None, "/x/", {}, lambda ctx: {})
    assert respuesta.status == 503


def test_ejecutar_respeta_respuesta_directa(doc_guardado):
    from pyrevit import routes

    respuesta = escritura.ejecutar(
        doc_guardado, "/x/", {}, lambda ctx: routes.make_response(data={"error": "mal"}, status=400)
    )
    assert respuesta.status == 400


def test_ejecutar_ok_false_cuando_la_verificacion_no_coincide(doc_guardado):
    def cuerpo(contexto):
        return {"ok": False, "verificacion": {"coincide": False, "detalle": "despues != pedido"}}

    respuesta = escritura.ejecutar(doc_guardado, "/x/", {}, cuerpo)
    assert respuesta.status == 200
    assert respuesta.data["ok"] is False


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def test_comprobar_alcance_limite_200():
    with pytest.raises(escritura.EscrituraRechazada) as info:
        escritura.comprobar_alcance({}, 201)
    assert info.value.status == 400
    assert info.value.extra["limite"] == 200
    escritura.comprobar_alcance({}, 200)
    escritura.comprobar_alcance({"forzar": True}, 5000)


def test_datos_peticion_y_banderas():
    class Peticion(object):
        data = '{"a": 1, "simular": "true", "forzar": "no"}'

    datos = escritura.datos_peticion(Peticion())
    assert datos["a"] == 1
    assert escritura.es_simulacion(datos) is True
    assert escritura.es_forzado(datos) is False
    assert escritura.datos_peticion(None) == {}
    with pytest.raises(escritura.EscrituraRechazada):
        Peticion.data = "no es json"
        escritura.datos_peticion(Peticion())


def test_verificar_eliminados(doc_guardado):
    class Elem(object):
        pass

    doc_guardado.elementos = {7: Elem()}
    resultado = escritura.verificar_eliminados(doc_guardado, [5, 7], [9])
    assert resultado["eliminados"] == [5]
    assert resultado["en_cascada"] == [9]
    assert resultado["ok"] is False
    assert "7" in resultado["verificacion"]["detalle"]
