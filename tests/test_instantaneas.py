# -*- coding: utf-8 -*-
"""Pruebas de extremo a extremo (CPython, pyrevit simulado) de POST /snapshot/ y
POST /diff_snapshots/: archivo en <carpeta del rvt>/snapshots/<name>.json,
limite configurable, 409 sin overwrite, y diff por UniqueId con anadidos,
eliminados y modificados (parametros que cambiaron, bbox movida)."""
import io
import json
import os

import pytest
from pyrevit import DB, routes

import modelo_falso as mf
import seguridad

TOKEN = "d" * 64
BIP = DB.BuiltInParameter
BIC = DB.BuiltInCategory


def _muro(doc, identificador, marca, longitud, nivel_id, caja):
    return mf.Muro(
        doc, identificador, nombre=u"Genérico - 200 mm", categoria=u"Muros", bic=BIC.OST_Walls,
        tipo_id=50, nivel_id=nivel_id, caja=caja,
        parametros=[
            mf.texto(u"Marca", marca, bip=BIP.ALL_MODEL_MARK),
            mf.texto(u"Comentarios", u"", bip=BIP.ALL_MODEL_INSTANCE_COMMENTS),
            mf.longitud_mm(u"Longitud", longitud, bip=BIP.CURVE_ELEM_LENGTH),
            mf.referencia(u"Restricción de base", nivel_id, bip=BIP.LEVEL_PARAM),
        ],
    )


@pytest.fixture
def doc(tmp_path, monkeypatch):
    mf.activar_spec(monkeypatch)
    rvt = tmp_path / "Modelo.rvt"
    rvt.write_bytes(b"RVT")
    doc = mf.Doc(str(rvt))
    n1 = mf.Nivel(doc, 1, u"Nivel 1", 0)
    mf.Nivel(doc, 2, u"Nivel 2", 3000)
    tipo = mf.TipoMuro(doc, 50, nombre=u"Genérico - 200 mm", categoria=u"Muros", bic=BIC.OST_Walls)
    tipo.FamilyName = u"Muro básico"
    _muro(doc, 10, u"M-1", 5000, 1, mf.caja_mm(0, 0, 0, 5000, 200, 3000))
    _muro(doc, 11, u"M-2", 3000, 1, mf.caja_mm(6000, 0, 0, 9000, 200, 3000))
    mf.Puerta(doc, 20, host=doc.elementos[10], nombre=u"0915 x 2134", categoria=u"Puertas", bic=BIC.OST_Doors,
              nivel_id=1, caja=mf.caja_mm(1000, 0, 0, 1915, 200, 2134),
              parametros=[mf.texto(u"Marca", u"P-1", bip=BIP.ALL_MODEL_MARK)])
    vista = mf.VistaPlanta(doc, 100, u"Planta Nivel 1", nivel=n1)
    doc.ActiveView = vista
    mf.Cota(doc, 200, [10, 11], 100)          # anotacion: fuera de la instantanea por defecto
    return doc


@pytest.fixture
def api():
    seguridad.establecer_token(TOKEN)
    DB.Transaction.creadas = []
    api = routes.API("revit_mcp")
    from instantaneas import register_instantaneas_routes

    register_instantaneas_routes(api)
    return api


def _post(api, ruta, doc, cuerpo, con_token=True):
    datos = dict(cuerpo)
    if con_token:
        datos["token"] = TOKEN
    return api.rutas[(ruta, "POST")](doc=doc, request=routes.Request(path=ruta, data=datos))


def _leer(ruta):
    with io.open(ruta, encoding="utf-8") as archivo:
        return json.load(archivo)


# ---------------------------------------------------------------------------
def test_snapshot_sin_token_y_errores(api, doc):
    assert _post(api, "/snapshot/", doc, {"name": "a"}, con_token=False).status == 401
    assert _post(api, "/diff_snapshots/", doc, {"a": "a"}, con_token=False).status == 401
    assert _post(api, "/snapshot/", None, {"name": "a"}).status == 503
    assert _post(api, "/snapshot/", doc, {}).status == 400
    assert _post(api, "/snapshot/", doc, {"name": "   "}).status == 400
    assert _post(api, "/snapshot/", doc, {"name": "a", "categories": ["OST_Nada"]}).status == 400
    assert _post(api, "/diff_snapshots/", doc, {}).status == 400
    respuesta = _post(api, "/diff_snapshots/", doc, {"a": "no-existe"})
    assert respuesta.status == 404 and respuesta.data["available_snapshots"] == []
    assert DB.Transaction.creadas == []


def test_snapshot_guarda_json_junto_al_rvt(api, doc, tmp_path):
    respuesta = _post(api, "/snapshot/", doc, {"name": u"antes de obra"})
    assert respuesta.status == 200, respuesta.data
    datos = respuesta.data
    assert datos["ruta"] == str(tmp_path / "snapshots" / "antes de obra.json")
    assert os.path.isfile(datos["ruta"])
    assert datos["count"] == 5 and datos["total_elements"] == 5 and datos["truncated"] is False
    assert datos["categories"] == ["model", "OST_Levels", "OST_Grids"]
    assert "elements" not in datos and datos["name"] == u"antes de obra"
    archivo = _leer(datos["ruta"])
    assert set(archivo["elements"].keys()) == set([u"uid-1", u"uid-2", u"uid-10", u"uid-11", u"uid-20"])
    muro = archivo["elements"][u"uid-10"]
    assert muro["id"] == 10 and muro["categoria"] == u"Muros" and muro["nivel"] == u"Nivel 1"
    assert muro["params"][u"Marca"] == u"M-1" and muro["params"][u"Longitud"] == 5000.0
    assert muro["params"][u"Restricción de base"] == u"Nivel 1"
    assert len(muro["hash"]) == 32
    assert muro["bbox_mm"]["max"]["x"] == 5000.0
    assert DB.Transaction.creadas == []

    # 409 si ya existe, salvo overwrite
    respuesta = _post(api, "/snapshot/", doc, {"name": u"antes de obra"})
    assert respuesta.status == 409 and respuesta.data["ruta"].endswith("antes de obra.json")
    assert _post(api, "/snapshot/", doc, {"name": u"antes de obra", "overwrite": True}).status == 200


def test_snapshot_categorias_parametros_y_limite(api, doc, tmp_path):
    respuesta = _post(api, "/snapshot/", doc, {"name": "muros", "categories": ["walls"], "parameters": ["Mark"]})
    assert respuesta.status == 200, respuesta.data
    assert respuesta.data["count"] == 2 and respuesta.data["categories"] == ["OST_Walls"]
    archivo = _leer(respuesta.data["ruta"])
    assert archivo["elements"][u"uid-10"]["params"] == {u"Marca": u"M-1"}

    respuesta = _post(api, "/snapshot/", doc, {"name": "corto", "max_elements": 2})
    assert respuesta.status == 200
    assert respuesta.data["count"] == 2 and respuesta.data["truncated"] is True
    assert "max_elements" in respuesta.data["warning"]

    respuesta = _post(api, "/snapshot/", doc, {"name": "sin_params", "include_parameters": False})
    archivo = _leer(respuesta.data["ruta"])
    assert "params" not in archivo["elements"][u"uid-10"]


def test_snapshot_modelo_sin_guardar_va_a_localappdata(api, tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    doc = mf.Doc("")
    mf.Nivel(doc, 1, u"Nivel 1", 0)
    respuesta = _post(api, "/snapshot/", doc, {"name": "sin guardar"})
    assert respuesta.status == 200, respuesta.data
    assert respuesta.data["ruta"] == str(tmp_path / "RevitMcp" / "snapshots" / "sin guardar.json")


def test_diff_lista_anadidos_eliminados_y_modificados(api, doc):
    assert _post(api, "/snapshot/", doc, {"name": "antes"}).status == 200
    # cambios: nivel nuevo, marca cambiada, muro movido, puerta borrada
    nivel = DB.Level.Create(doc, 6000 / 304.8)
    nivel.Name = u"Nivel 3"
    doc.elementos[10].LookupParameter(u"Marca").Set(u"M-9")
    doc.elementos[11].mover(DB.XYZ(500 / 304.8, 0, 0))
    doc.Delete(DB.ElementId(20))

    respuesta = _post(api, "/diff_snapshots/", doc, {"a": "antes"})       # b omitido = modelo actual
    assert respuesta.status == 200, respuesta.data
    datos = respuesta.data
    assert datos["counts"] == {"added": 1, "removed": 1, "modified": 2, "unchanged": 2}
    assert [a["tipo"] for a in datos["added"]] == [u"Nivel 3"]
    assert datos["added"][0]["categoria"] == u"Niveles" and "params" not in datos["added"][0]
    assert [r["id"] for r in datos["removed"]] == [20]
    modificados = dict((m["id"], m) for m in datos["modified"])
    assert modificados[10]["cambios"] == [{"parametro": u"Marca", "antes": u"M-1", "despues": u"M-9"}]
    assert modificados[10]["bbox_movido"] is False
    assert modificados[11]["cambios"] == [] and modificados[11]["bbox_movido"] is True
    assert modificados[11]["bbox_antes_mm"]["min"]["x"] == 6000.0
    assert modificados[11]["bbox_despues_mm"]["min"]["x"] == 6500.0
    assert datos["a"]["name"] == "antes" and datos["b"]["ruta"] is None and datos["b"]["name"] == "actual"
    assert datos["truncated"] is False and "warning" not in datos

    # el mismo resultado comparando dos archivos
    assert _post(api, "/snapshot/", doc, {"name": "despues"}).status == 200
    respuesta = _post(api, "/diff_snapshots/", doc, {"a": "antes", "b": "despues"})
    assert respuesta.status == 200
    assert respuesta.data["counts"] == datos["counts"]
    assert respuesta.data["b"]["ruta"].endswith("despues.json")
    assert DB.Transaction.creadas == []


def test_diff_sin_cambios_y_max_items(api, doc):
    _post(api, "/snapshot/", doc, {"name": "a"})
    _post(api, "/snapshot/", doc, {"name": "b"})
    respuesta = _post(api, "/diff_snapshots/", doc, {"a": "a", "b": "b"})
    assert respuesta.data["counts"] == {"added": 0, "removed": 0, "modified": 0, "unchanged": 5}
    doc.Delete(DB.ElementId(10))
    doc.Delete(DB.ElementId(11))
    respuesta = _post(api, "/diff_snapshots/", doc, {"a": "a", "max_items": 1})
    assert respuesta.data["counts"]["removed"] == 2 and len(respuesta.data["removed"]) == 1
    assert respuesta.data["truncated"] is True


def test_diff_sin_parametros_guardados_explica_el_hash(api, doc):
    _post(api, "/snapshot/", doc, {"name": "sin", "include_parameters": False})
    doc.elementos[10].LookupParameter(u"Marca").Set(u"M-9")
    respuesta = _post(api, "/diff_snapshots/", doc, {"a": "sin"})
    modificado = respuesta.data["modified"][0]
    assert modificado["id"] == 10
    assert modificado["cambios"][0]["parametro"] is None and "hash" in modificado["cambios"][0]["detalle"]


def test_diff_avisa_si_las_categorias_no_coinciden(api, doc):
    _post(api, "/snapshot/", doc, {"name": "todo"})
    _post(api, "/snapshot/", doc, {"name": "muros", "categories": ["walls"]})
    respuesta = _post(api, "/diff_snapshots/", doc, {"a": "todo", "b": "muros"})
    assert respuesta.status == 200
    assert respuesta.data["counts"]["removed"] == 3
    assert "categorias" in respuesta.data["warning"]


def test_snapshot_name_no_puede_ser_una_ruta(api, doc, tmp_path):
    """0.3.0: `name` es un nombre; con separadores o ruta absoluta se rechaza (400) y no se escribe nada."""
    fuera = str(tmp_path / "fuera.json")
    for nombre in (fuera, u"../fuera", u"sub\\fuera", u"sub/fuera"):
        r = _post(api, "/snapshot/", doc, {"name": nombre})
        assert r.status == 400 and "not a path" in r.data["error"], (nombre, r.data)
        r = _post(api, "/diff_snapshots/", doc, {"a": nombre})
        assert r.status == 400, (nombre, r.data)
    assert not os.path.exists(fuera)
    assert not os.path.isdir(os.path.join(os.path.dirname(doc.PathName), "snapshots"))
