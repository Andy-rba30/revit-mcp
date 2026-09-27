# -*- coding: utf-8 -*-
"""Fallos vistos al ejecutar pruebas/probar_revit.py (0.2.1) en un Revit en espanol.

- resultado_creacion daba ok=false en toda creacion: get_element_id_value no
  aceptaba el id entero que guardan las rutas y describir_elemento devolvia None.
- set_parameter con "Comments" respondia 404 porque Revit lo llama "Comentarios".
- available_parameters repetia nombres ("Categoria" dos veces).
- Los nombres con tildes salian como "Gen?rico - Alba?iler?a".
- create_opening aceptaba un hueco entero por debajo del muro (z tomada como
  desfase desde el nivel).
"""
import pytest
from pyrevit import DB, routes

import escritura
import seguridad
from utils import get_element_id_value, get_element_name, buscar_por_nombre, normalize_string

TOKEN = "b" * 64


class Parametro(object):
    def __init__(self, nombre, valor):
        self.Definition = type("Def", (), {"Name": nombre})()
        self._valor = valor
        self.StorageType = DB.StorageType.String
        self.IsReadOnly = False
        self.HasValue = True

    def AsString(self):
        return self._valor

    def AsValueString(self):
        return None

    def Set(self, valor):
        self._valor = valor
        return True


class Caja(object):
    def __init__(self, zmin_mm, zmax_mm):
        self.Min = DB.XYZ(0, 0, zmin_mm / 304.8)
        self.Max = DB.XYZ(100, 1, zmax_mm / 304.8)


class Muro(DB.Wall):
    """Muro de un Revit en espanol: los parametros tienen nombre local."""

    def __init__(self, identificador, nombre=u"Genérico - Albañilería 140 mm", caja=None):
        self.Id = DB.ElementId(identificador)
        self.Name = nombre
        self.Category = type("Cat", (), {"Name": u"Muros"})()
        self.LevelId = DB.ElementId.InvalidElementId
        self.Pinned = False
        self._caja = caja
        comentarios = Parametro(u"Comentarios", u"")
        self._por_nombre = {u"Comentarios": comentarios}
        self._integrados = {"ALL_MODEL_INSTANCE_COMMENTS": comentarios}
        # Revit lista algunos parametros dos veces con el mismo nombre visible
        self.Parameters = [comentarios, Parametro(u"Categoría", None), Parametro(u"Categoría", None)]

    def GetTypeId(self):
        return DB.ElementId.InvalidElementId

    def LookupParameter(self, nombre):
        return self._por_nombre.get(nombre)

    def get_Parameter(self, bip):
        return self._integrados.get(str(bip))

    def get_BoundingBox(self, vista):
        return self._caja


class Doc(object):
    def __init__(self, ruta):
        self.PathName = ruta
        self.Title = "Modelo"
        self.IsWorkshared = False
        self.IsReadOnly = False
        self.IsModifiable = False
        self.elementos = {}

    def GetElement(self, elem_id):
        return self.elementos.get(elem_id.Value)


@pytest.fixture
def doc(tmp_path):
    rvt = tmp_path / "Modelo.rvt"
    rvt.write_bytes(b"RVT")
    doc = Doc(str(rvt))
    doc.elementos[165465] = Muro(165465, caja=Caja(2925, 5925))
    return doc


@pytest.fixture
def api():
    seguridad.establecer_token(TOKEN)
    DB.Transaction.creadas = []
    DB.Transaction.resultado_commit = DB.TransactionStatus.Committed
    api = routes.API("revit_mcp")
    from parameters import register_parameter_routes
    from estructural import register_estructural_routes

    register_parameter_routes(api)
    register_estructural_routes(api)
    return api


def _llamar(api, ruta, doc, cuerpo):
    datos = dict(cuerpo)
    datos["token"] = TOKEN
    return api.rutas[(ruta, "POST")](doc=doc, request=routes.Request(path=ruta, data=datos))


# ---------------------------------------------------------------------------
def test_get_element_id_value_acepta_enteros():
    assert get_element_id_value(615589) == 615589
    assert get_element_id_value(DB.ElementId(7)) == 7
    with pytest.raises(ValueError):
        get_element_id_value(True)


def test_resultado_creacion_encuentra_ids_enteros(doc):
    resultado = escritura.resultado_creacion(doc, [165465])
    assert resultado["ok"] is True
    assert resultado["count"] == 1
    assert resultado["verificacion"] == {"coincide": True}
    assert resultado["creados"][0]["id"] == 165465


def test_resultado_creacion_sigue_detectando_los_que_faltan(doc):
    resultado = escritura.resultado_creacion(doc, [165465, 999])
    assert resultado["ok"] is False
    assert "999" in resultado["verificacion"]["detalle"]


def test_nombres_con_tildes_se_conservan(doc):
    muro = doc.elementos[165465]
    assert get_element_name(muro) == u"Genérico - Albañilería 140 mm"
    assert escritura.describir_elemento(doc, 165465)["tipo"] == u"Genérico - Albañilería 140 mm"
    assert normalize_string(u"  Sótano ") == u"Sótano"


def test_buscar_por_nombre_local_ingles_y_builtin(doc):
    muro = doc.elementos[165465]
    esperado = muro._por_nombre[u"Comentarios"]
    assert buscar_por_nombre(muro, u"Comentarios") is esperado
    assert buscar_por_nombre(muro, "Comments") is esperado
    assert buscar_por_nombre(muro, "comments") is esperado
    assert buscar_por_nombre(muro, "ALL_MODEL_INSTANCE_COMMENTS") is esperado
    assert buscar_por_nombre(muro, "No existe") is None
    assert buscar_por_nombre(muro, "NO_EXISTE_PARAM") is None


def test_set_parameter_comments_en_revit_en_espanol(api, doc):
    respuesta = _llamar(api, "/set_parameter/", doc,
                        {"element_id": 165465, "parameter_name": "Comments", "value": "MCP prueba"})
    assert respuesta.status == 200, respuesta.data
    assert respuesta.data["ok"] is True
    assert respuesta.data["parameter_label"] == u"Comentarios"
    assert respuesta.data["is_type_parameter"] is False
    assert doc.elementos[165465]._por_nombre[u"Comentarios"].AsString() == "MCP prueba"


def test_set_parameter_simular_devuelve_nombre_visible(api, doc):
    respuesta = _llamar(api, "/set_parameter/", doc,
                        {"element_id": 165465, "parameter_name": "Comments", "value": "x", "simular": True})
    assert respuesta.status == 200
    assert respuesta.data["haria"][0]["parameter_label"] == u"Comentarios"


def test_available_parameters_sin_repetidos(api, doc):
    respuesta = _llamar(api, "/set_parameter/", doc,
                        {"element_id": 165465, "parameter_name": "No existe", "value": "x"})
    assert respuesta.status == 404
    disponibles = respuesta.data["available_parameters"]
    assert disponibles == sorted(set(disponibles))
    assert disponibles.count(u"Categoría") == 1


def test_create_opening_rechaza_hueco_por_debajo_del_muro(api, doc):
    # El muro va de z=2925 a z=5925; z=900..2100 es un desfase desde el nivel, no una cota
    respuesta = _llamar(api, "/create_opening/", doc, {
        "host_id": 165465,
        "points": [{"x": 12791.1, "y": 18077.08, "z": 900.0}, {"x": 21330.99, "y": 18032.04, "z": 2100.0}],
    })
    assert respuesta.status == 400
    assert "does not overlap" in respuesta.data["error"]
    assert "2925" in respuesta.data["error"]
    assert DB.Transaction.creadas == []


def test_create_opening_dentro_del_muro_pasa_la_validacion(api, doc):
    respuesta = _llamar(api, "/create_opening/", doc, {
        "host_id": 165465, "simular": True,
        "points": [{"x": 12791.1, "y": 18077.08, "z": 3825.0}, {"x": 21330.99, "y": 18032.04, "z": 5025.0}],
    })
    assert respuesta.status == 200, respuesta.data
    assert respuesta.data["simulado"] is True


def test_execute_code_trae_helpers_de_ids():
    import code_execution
    from StringIO import StringIO

    espacio = code_execution._espacio(None, StringIO())
    assert espacio["make_element_id"](165465).Value == 165465
    assert espacio["get_element_id_value"](DB.ElementId(5)) == 5
    assert espacio["buscar_parametro"] is buscar_por_nombre
    pistas = code_execution._pistas("ImportError", "No module named utils")
    assert any("make_element_id" in p for p in pistas)
