# -*- coding: utf-8 -*-
"""0.6.5: coordenadas compartidas por valor (Civil 3D -> Revit sin el aviso "muy lejos del origen").

  POST /set_project_location/ con `shared_coordinates` (ProjectLocation.SetProjectPosition),
  POST /link_file/ con `placement` y `extents`, y las herramientas MCP que los exponen.
"""
import io
import math

import pytest
from pyrevit import DB, routes

import modelo_falso as mf
import seguridad
from utils import MM_TO_FEET

from test_herramientas import Puente, _herramientas, _llamar

TOKEN = "f" * 64
M_A_PIES = 1000.0 * MM_TO_FEET


@pytest.fixture
def doc(tmp_path, monkeypatch):
    mf.activar_spec(monkeypatch)
    rvt = tmp_path / "Modelo.rvt"
    rvt.write_bytes(b"RVT")
    doc = mf.Doc(str(rvt))
    n1 = mf.Nivel(doc, 1, u"Nivel 1", 0)
    doc.ActiveView = mf.VistaPlanta(doc, 100, u"Planta Nivel 1", nivel=n1)
    return doc


@pytest.fixture
def api():
    seguridad.establecer_token(TOKEN)
    DB.Transaction.creadas = []
    DB.Transaction.resultado_commit = DB.TransactionStatus.Committed
    api = routes.API("revit_mcp")
    from coordenadas import register_coordenadas_routes
    from interop import register_interop_routes
    register_coordenadas_routes(api)
    register_interop_routes(api)
    return api


def _post(api, ruta, doc, cuerpo):
    datos = dict(cuerpo, token=TOKEN)
    return api.rutas[(ruta, "POST")](doc=doc, request=routes.Request(path=ruta, data=datos))


def _get(api, ruta, doc):
    return api.rutas[(ruta, "GET")](doc=doc, request=routes.Request(path=ruta, data=None, query_params={"token": TOKEN}))


def _dwg(tmp_path):
    ruta = tmp_path / "topografia.dwg"
    with io.open(str(ruta), "w", encoding="utf-8") as archivo:
        archivo.write(u"dwg")
    return str(ruta)


UTM = {"east": 500000, "north": 8500500, "elevation": 2420}


# ---------------------------------------------------------------- set_project_location(shared_coordinates)
def test_shared_coordinates_fija_la_posicion_del_origen_interno(api, doc):
    r = _get(api, "/project_location/", doc)
    assert r.status == 200 and r.data["shared_coordinates_set"] is False

    r = _post(api, "/set_project_location/", doc, {"shared_coordinates": UTM, "units": "m"})
    assert r.status == 200, r.data
    assert r.data["ok"] is True and r.data["verificacion"] == {"coincide": True}
    accion = r.data["acciones"][0]
    assert accion["accion"] == "fijar_coordenadas_compartidas" and accion["punto_mm"] == {"x": 0.0, "y": 0.0, "z": 0.0}
    assert accion["antes"]["east"] == 0.0 and accion["units"] == "m"
    assert abs(doc.posicion.EastWest - 500000 * M_A_PIES) < 1e-6
    assert abs(doc.posicion.NorthSouth - 8500500 * M_A_PIES) < 1e-6
    assert abs(doc.posicion.Elevation - 2420 * M_A_PIES) < 1e-6 and doc.posicion.Angle == 0.0
    assert r.data["project_location"] == {"name": u"Interno", "switched": False}
    assert "placement=\"shared\"" in r.data["message"]
    assert [t.nombre for t in DB.Transaction.creadas] == [u"IA: Coordenadas del proyecto"]

    r = _get(api, "/project_location/", doc)
    assert r.data["shared_coordinates_set"] is True
    assert r.data["active_project_location"]["origin_offset_mm"]["east_west"] == 500000000.0


def test_shared_coordinates_en_otro_punto_y_con_norte(api, doc):
    r = _post(api, "/set_project_location/", doc, {
        "shared_coordinates": {"east": 500, "north": 8500, "elevation": 2.4}, "units": "mm",
        "at_point_mm": {"x": 1000, "y": 0, "z": 0}, "true_north_deg": 3.0})
    assert r.status == 200, r.data
    assert r.data["acciones"][0]["punto_mm"] == {"x": 1000.0, "y": 0.0, "z": 0.0}
    assert abs(doc.posicion.Angle - math.radians(3.0)) < 1e-9
    assert abs(r.data["despues"]["true_north_deg"] - 3.0) < 1e-6


def test_shared_coordinates_409_si_ya_hay_y_forzar(api, doc):
    doc.posicion = mf.PosicionProyecto(10.0, 0.0, 0.0, 0.0)
    r = _post(api, "/set_project_location/", doc, {"shared_coordinates": UTM})
    assert r.status == 409 and r.data["shared_coordinates_set"] is True
    assert abs(doc.posicion.EastWest - 10.0) < 1e-9, "no toca nada"
    r = _post(api, "/set_project_location/", doc, {"shared_coordinates": UTM, "forzar": True})
    assert r.status == 200 and abs(doc.posicion.EastWest - 500000 * M_A_PIES) < 1e-6


def test_shared_coordinates_simulacion_y_errores(api, doc):
    r = _post(api, "/set_project_location/", doc, {"shared_coordinates": UTM, "simular": True})
    assert r.status == 200 and r.data["simulado"] is True
    assert r.data["haria"][0]["accion"] == "fijar_coordenadas_compartidas" and doc.posicion.EastWest == 0.0
    assert DB.Transaction.creadas == []
    assert _post(api, "/set_project_location/", doc, {"shared_coordinates": UTM, "units": "in"}).status == 400
    assert _post(api, "/set_project_location/", doc, {"shared_coordinates": {"east": 1}}).status == 400
    assert _post(api, "/set_project_location/", doc, {"shared_coordinates": {"east": "a", "north": 2}}).status == 400
    r = _post(api, "/set_project_location/", doc, {"shared_coordinates": UTM, "acquire_from_link_id": 5})
    assert r.status == 400 and "alone" in r.data["error"]
    assert doc.posicion.EastWest == 0.0


# ---------------------------------------------------------------- link_file(placement) y extents
def test_link_file_shared_exige_coordenadas_y_las_usa(api, doc, tmp_path):
    ruta = _dwg(tmp_path)
    r = _post(api, "/link_file/", doc, {"file_path": ruta, "placement": "arriba"})
    assert r.status == 400 and "placement must be one of" in r.data["error"]
    r = _post(api, "/link_file/", doc, {"file_path": ruta, "placement": "shared"})
    assert r.status == 409 and r.data["shared_coordinates_set"] is False
    assert "set_project_location" in r.data["error"] and "extension_superficie" in r.data["error"]
    assert doc.vinculados == []

    assert _post(api, "/set_project_location/", doc, {"shared_coordinates": UTM}).status == 200
    r = _post(api, "/link_file/", doc, {"file_path": ruta, "placement": "shared", "simular": True})
    assert r.status == 200 and r.data["haria"][0]["placement"] == "shared" and doc.vinculados == []
    r = _post(api, "/link_file/", doc, {"file_path": ruta, "placement": "shared"})
    assert r.status == 200, r.data
    assert r.data["ok"] is True and r.data["placement"] == "shared"
    assert doc.vinculados[0].placement is DB.ImportPlacement.Shared
    assert r.data["extents"] is None, "el vinculo simulado no tiene caja"
    assert r.data["message"] == "Linked file 'topografia.dwg' (placement: shared)"


def test_link_file_por_defecto_origen_y_extents_avisan_lejos(api, doc, tmp_path, monkeypatch):
    ruta = _dwg(tmp_path)
    original = mf.Doc.Link

    def link_lejos(self, ruta_, opciones, vista, idref):
        original(self, ruta_, opciones, vista, idref)
        # Superficie en UTM vinculada origen con origen: a ~8 500 km del origen interno
        self.vinculados[-1].caja = DB.BoundingBoxXYZ(
            DB.XYZ(500000 * M_A_PIES, 8500000 * M_A_PIES, 2400 * M_A_PIES),
            DB.XYZ(501000 * M_A_PIES, 8501000 * M_A_PIES, 2500 * M_A_PIES))

    monkeypatch.setattr(mf.Doc, "Link", link_lejos)
    r = _post(api, "/link_file/", doc, {"file_path": ruta})
    assert r.status == 200, r.data
    assert r.data["placement"] == "origin" and doc.vinculados[0].placement is DB.ImportPlacement.Origin
    extents = r.data["extents"]
    assert extents["beyond_revit_limit"] is True and extents["far_from_origin"] is True
    assert extents["max_distance_m"] > 8000000
    assert extents["center_internal_mm"]["x"] == 500500000.0
    assert extents["center_shared_m"]["east"] == 500500.0, "sin coordenadas compartidas, compartida = interna"
    assert "33 km" in extents["hint"] and "placement='shared'" in extents["hint"]
    assert "WARNING" in r.data["message"] and "Farthest corner" in r.data["message"]


def test_extents_cerca_del_origen_sin_aviso(api, doc, tmp_path, monkeypatch):
    ruta = _dwg(tmp_path)
    original = mf.Doc.Link

    def link_cerca(self, ruta_, opciones, vista, idref):
        original(self, ruta_, opciones, vista, idref)
        self.vinculados[-1].caja = DB.BoundingBoxXYZ(DB.XYZ(-300 * M_A_PIES, -200 * M_A_PIES, 0),
                                                      DB.XYZ(300 * M_A_PIES, 200 * M_A_PIES, 40 * M_A_PIES))

    monkeypatch.setattr(mf.Doc, "Link", link_cerca)
    _post(api, "/set_project_location/", doc, {"shared_coordinates": UTM})
    r = _post(api, "/link_file/", doc, {"file_path": ruta, "placement": "shared"})
    extents = r.data["extents"]
    assert extents["far_from_origin"] is False and extents["beyond_revit_limit"] is False and "hint" not in extents
    assert abs(extents["max_distance_m"] - math.sqrt(300 ** 2 + 200 ** 2 + 40 ** 2)) < 0.1
    assert extents["center_shared_m"]["east"] == 500000.0 and extents["center_shared_m"]["elevation"] == 2440.0
    assert "WARNING" not in r.data["message"]


# ---------------------------------------------------------------- herramientas MCP (puente)
def test_herramientas_envian_shared_coordinates_y_placement():
    puente = Puente({"/set_project_location/": lambda d: {"data": d}, "/link_file/": lambda d: {"data": d}})
    f = _herramientas(puente)
    datos = _llamar(f, "set_project_location", shared_coordinates=UTM, units="m")
    assert datos["data"] == {"simular": False, "forzar": False, "shared_coordinates": UTM, "units": "m"}
    datos = _llamar(f, "link_file", file_path="C:\\\\t.dwg", placement="shared")
    assert datos["data"] == {"file_path": "C:\\\\t.dwg", "mode": "link", "simular": False, "placement": "shared"}
    datos = _llamar(f, "link_file", file_path="C:\\\\t.dwg")
    assert "placement" not in datos["data"], "sin placement el puente no manda nada y la ruta usa origin"
