# -*- coding: utf-8 -*-
"""Pruebas de integracion en CPython de rutas refactorizadas a escritura.py.

Se registran los manejadores reales (revit_mcp/*.py) en una API de pyRevit
simulada y se invocan como lo hace Routes: con el token en el cuerpo, el
documento simulado y `request.data` como dict. Comprueban que cada ruta:
  - responde 401 sin token (requiere_token sigue por encima de ejecutar),
  - con simular=true no abre transaccion ni cambia nada,
  - abre una transaccion "IA: ..." y devuelve antes/despues o eliminados,
  - registra la accion en mcp_log.jsonl y devuelve la copia de seguridad.
"""
import io
import json
import os

import pytest
from pyrevit import DB, routes

import escritura
import seguridad

TOKEN = "a" * 64


class ParametroFalso(object):
    def __init__(self, nombre, valor, tipo="String", solo_lectura=False):
        self.Definition = type("Def", (), {"Name": nombre})()
        self._valor = valor
        self.StorageType = getattr(DB.StorageType, tipo)
        self.IsReadOnly = solo_lectura
        self.HasValue = valor is not None

    def AsString(self):
        return self._valor

    def AsInteger(self):
        return int(self._valor)

    def AsDouble(self):
        return float(self._valor)

    def AsValueString(self):
        return None

    def Set(self, valor):
        self._valor = valor
        self.HasValue = True
        return True


class ElementoFalso(object):
    def __init__(self, doc, identificador, categoria="Walls", parametros=None):
        self.doc = doc
        self.Id = DB.ElementId(identificador)
        self.Category = type("Cat", (), {"Name": categoria})()
        self.LevelId = DB.ElementId.InvalidElementId
        self.Pinned = False
        self._parametros = parametros or {}
        self.Parameters = list(self._parametros.values())

    def GetTypeId(self):
        return DB.ElementId.InvalidElementId

    def LookupParameter(self, nombre):
        return self._parametros.get(nombre)

    def get_BoundingBox(self, vista):
        return None

    def get_Parameter(self, bip):
        return None


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

    def Delete(self, elem_id):
        self.elementos.pop(elem_id.Value, None)
        return [elem_id]


@pytest.fixture
def api():
    seguridad.establecer_token(TOKEN)
    DB.Transaction.creadas = []
    DB.Transaction.resultado_commit = DB.TransactionStatus.Committed
    api = routes.API("revit_mcp")
    from parameters import register_parameter_routes
    from editing import register_editing_routes

    register_parameter_routes(api)
    register_editing_routes(api)
    return api


@pytest.fixture
def doc(tmp_path):
    rvt = tmp_path / "Modelo.rvt"
    rvt.write_bytes(b"RVT")
    doc = DocFalso(str(rvt))
    doc.elementos[10] = ElementoFalso(
        doc, 10, "Walls", {"Mark": ParametroFalso("Mark", "M-1"), "Comments": ParametroFalso("Comments", "")}
    )
    doc.elementos[11] = ElementoFalso(doc, 11, "Doors")
    return doc


def _log(tmp_path):
    with io.open(str(tmp_path / "mcp_log.jsonl"), encoding="utf-8") as archivo:
        return [json.loads(l) for l in archivo.read().splitlines() if l.strip()]


def _llamar(api, ruta, doc, cuerpo, con_token=True):
    manejador = api.rutas[(ruta, "POST")]
    datos = dict(cuerpo)
    if con_token:
        datos["token"] = TOKEN
    return manejador(doc=doc, request=routes.Request(path=ruta, data=datos))


# ---------------------------------------------------------------------------
def test_set_parameter_sin_token_401(api, doc):
    respuesta = _llamar(api, "/set_parameter/", doc, {"element_id": 10, "parameter_name": "Mark", "value": "x"}, con_token=False)
    assert respuesta.status == 401


def test_set_parameter_simular_no_cambia_nada(api, doc, tmp_path):
    respuesta = _llamar(
        api, "/set_parameter/", doc,
        {"element_id": 10, "parameter_name": "Mark", "value": "M-2", "simular": True},
    )
    assert respuesta.status == 200, respuesta.data
    assert respuesta.data["simulado"] is True
    haria = respuesta.data["haria"][0]
    assert haria["antes"] == "M-1" and haria["despues"] == "M-2"
    assert doc.elementos[10].LookupParameter("Mark").AsString() == "M-1"
    assert DB.Transaction.creadas == []
    assert not (tmp_path / "backups").exists()
    assert _log(tmp_path)[-1]["simulado"] is True


def test_set_parameter_real_antes_despues_log_y_copia(api, doc, tmp_path):
    respuesta = _llamar(
        api, "/set_parameter/", doc, {"element_id": 10, "parameter_name": "Mark", "value": "M-2"}
    )
    assert respuesta.status == 200, respuesta.data
    datos = respuesta.data
    assert datos["ok"] is True
    assert datos["antes"] == "M-1" and datos["despues"] == "M-2"
    assert datos["verificacion"]["coincide"] is True
    assert datos["copia"]["ruta"].endswith(".rvt")
    assert os.path.isfile(datos["copia"]["ruta"])
    assert len(DB.Transaction.creadas) == 1
    assert DB.Transaction.creadas[0].nombre == u"IA: Parametro Mark de 10"
    assert DB.Transaction.creadas[0].estado == DB.TransactionStatus.Committed
    entrada = _log(tmp_path)[-1]
    assert entrada["ruta"] == "/set_parameter/" and entrada["ok"] is True
    assert entrada["args"]["value"] == "M-2"
    assert "token" not in entrada["args"]


def test_set_parameter_detecta_desajuste(api, doc, tmp_path):
    # Un parametro cuyo Set no aplica el valor: ok=false y explicacion
    param = doc.elementos[10].LookupParameter("Comments")
    param.Set = lambda valor: True
    respuesta = _llamar(
        api, "/set_parameter/", doc, {"element_id": 10, "parameter_name": "Comments", "value": "hola"}
    )
    assert respuesta.status == 200
    assert respuesta.data["ok"] is False
    assert respuesta.data["verificacion"]["coincide"] is False
    assert "no retry" in respuesta.data["verificacion"]["detalle"]


def test_set_parameter_errores_controlados(api, doc):
    r = _llamar(api, "/set_parameter/", doc, {"element_id": 99, "parameter_name": "Mark", "value": "x"})
    assert r.status == 404
    r = _llamar(api, "/set_parameter/", doc, {"element_id": 10, "parameter_name": "NoExiste", "value": "x"})
    assert r.status == 404 and "available_parameters" in r.data
    r = _llamar(api, "/set_parameter/", doc, {"element_id": 10, "parameter_name": "Mark"})
    assert r.status == 400
    doc.IsModifiable = True
    r = _llamar(api, "/set_parameter/", doc, {"element_id": 10, "parameter_name": "Mark", "value": "x"})
    assert r.status == 409 and r.data["open_transaction"] is True


def test_delete_elements_simular_lista_categorias(api, doc):
    respuesta = _llamar(api, "/delete_elements/", doc, {"element_ids": [10, 11], "simular": True})
    assert respuesta.status == 200, respuesta.data
    haria = respuesta.data["haria"]
    assert [h["categoria"] for h in haria] == ["Walls", "Doors"]
    assert all(h["accion"] == "borrar" for h in haria)
    assert 10 in doc.elementos and 11 in doc.elementos


def test_delete_elements_verifica_eliminados(api, doc):
    respuesta = _llamar(api, "/delete_elements/", doc, {"element_ids": [10]})
    assert respuesta.status == 200, respuesta.data
    assert respuesta.data["eliminados"] == [10]
    assert respuesta.data["en_cascada"] == []
    assert respuesta.data["ok"] is True
    assert respuesta.data["antes"][0]["categoria"] == "Walls"
    assert DB.Transaction.creadas[0].nombre == u"IA: Borrar elementos"
    assert 10 not in doc.elementos


def test_delete_elements_limite_200_y_forzar(api, doc):
    muchos = list(range(1000, 1300))
    for i in muchos:
        doc.elementos[i] = ElementoFalso(doc, i)
    r = _llamar(api, "/delete_elements/", doc, {"element_ids": muchos})
    assert r.status == 400 and r.data["limite"] == 200
    assert 1000 in doc.elementos
    r = _llamar(api, "/delete_elements/", doc, {"element_ids": muchos, "forzar": True})
    assert r.status == 200 and r.data["deleted_count"] == 300


def test_modify_element_antes_despues(api, doc):
    respuesta = _llamar(
        api, "/modify_element/", doc,
        {"element_id": 10, "parameters": {"Mark": "M-9", "NoExiste": "x"}},
    )
    assert respuesta.status == 200, respuesta.data
    assert respuesta.data["antes"] == {"Mark": "M-1"}
    assert respuesta.data["despues"] == {"Mark": "M-9"}
    assert respuesta.data["failed"][0]["parameter"] == "NoExiste"
    assert respuesta.data["ok"] is True
    assert DB.Transaction.creadas[0].nombre.startswith(u"IA: Modificar elemento")


def test_excepcion_en_transaccion_revierte_y_500(api, doc):
    def explota(valor):
        raise RuntimeError("Revit dice no")

    doc.elementos[10].LookupParameter("Mark").Set = explota
    respuesta = _llamar(api, "/set_parameter/", doc, {"element_id": 10, "parameter_name": "Mark", "value": "x"})
    assert respuesta.status == 500
    assert "Revit dice no" in respuesta.data["error"]
    assert DB.Transaction.creadas[0].estado == DB.TransactionStatus.RolledBack
    assert doc.IsModifiable is False
