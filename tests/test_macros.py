# -*- coding: utf-8 -*-
"""Pruebas de extremo a extremo (CPython, pyrevit simulado) del Bloque D de la fase 2a:

  POST /grid_levels/, /sheet_set/ e /import_civil/ (LandXML, CSV y DWG), mas los
  manejadores de la fase 1 cuyo codigo interno reutilizan (create_grid,
  create_level, create_sheet, create_toposolid, link_file) para comprobar que
  la extraccion de helpers no cambio su comportamiento.
"""
import io
import os

import pytest
from pyrevit import DB, routes

import modelo_falso as mf
import seguridad

TOKEN = "e" * 64
BIC = DB.BuiltInCategory

LANDXML = u"""<?xml version="1.0" encoding="UTF-8"?>
<LandXML xmlns="http://www.landxml.org/schema/LandXML-1.2" version="1.2">
  <Units><Metric linearUnit="meter" areaUnit="squareMeter"/></Units>
  <Surfaces>
    <Surface name="Terreno">
      <Definition surfType="TIN">
        <Pnts>
          <P id="1">4500000.5 300000.25 812.3</P>
          <P id="2">4500010 300000 813</P>
          <P id="3">4500000 300010 811.5</P>
          <P id="4">mal</P>
        </Pnts>
      </Definition>
    </Surface>
  </Surfaces>
</LandXML>
"""


class TipoToposolido(mf.Elemento, DB.ToposolidType):
    pass


@pytest.fixture
def doc(tmp_path, monkeypatch):
    mf.activar_spec(monkeypatch)
    rvt = tmp_path / "Modelo.rvt"
    rvt.write_bytes(b"RVT")
    doc = mf.Doc(str(rvt))
    n1 = mf.Nivel(doc, 1, u"Nivel 1", 0)
    n2 = mf.Nivel(doc, 2, u"Nivel 2", 3000)
    mf.Rejilla(doc, 60, u"A")
    cajetin = mf.TipoFamilia(doc, 70, nombre=u"A1 métrico", categoria=u"Cajetines", bic=BIC.OST_TitleBlocks)
    cajetin.IsActive = False
    TipoToposolido(doc, 80, nombre=u"Terreno", categoria=u"Toposólido", bic=BIC.OST_Toposolid)
    vista1 = mf.VistaPlanta(doc, 100, u"Planta Nivel 1", nivel=n1)
    mf.VistaPlanta(doc, 102, u"Planta Nivel 2", nivel=n2)
    mf.Tabla(doc, 300, u"Tabla de muros", [u"Marca"], [[u"Marca"], [u"M-1"]])
    plano = mf.Plano(doc, 500, u"E-100", u"Existente")
    viewport = DB.Viewport()
    viewport.ViewId = DB.ElementId(102)
    viewport.SheetId = DB.ElementId(500)
    doc.agregar(viewport)
    doc.ActiveView = vista1
    return doc


@pytest.fixture
def api():
    seguridad.establecer_token(TOKEN)
    DB.Transaction.creadas = []
    DB.Transaction.resultado_commit = DB.TransactionStatus.Committed
    api = routes.API("revit_mcp")
    from macros import register_macros_routes
    from structure import register_structure_routes
    from building import register_building_routes
    from documentation import register_documentation_routes
    from topografia import register_topografia_routes
    from interop import register_interop_routes

    register_macros_routes(api)
    register_structure_routes(api)
    register_building_routes(api)
    register_documentation_routes(api)
    register_topografia_routes(api)
    register_interop_routes(api)
    return api


def _post(api, ruta, doc, cuerpo, con_token=True):
    datos = dict(cuerpo)
    if con_token:
        datos["token"] = TOKEN
    return api.rutas[(ruta, "POST")](doc=doc, request=routes.Request(path=ruta, data=datos))


def _nombres_transacciones():
    return [t.nombre for t in DB.Transaction.creadas]


def _archivo(tmp_path, nombre, texto):
    ruta = tmp_path / nombre
    with io.open(str(ruta), "w", encoding="utf-8") as archivo:
        archivo.write(texto)
    return str(ruta)


# ---------------------------------------------------------------------------
# create_grid_and_levels
# ---------------------------------------------------------------------------
def test_grid_levels_sin_token_y_simulacion_con_plan(api, doc):
    cuerpo = {"x_spacings_mm": [6000, 6000], "y_spacings_mm": [5000], "y_names": "B",
              "levels": [{"name": u"Nivel 3", "elevation_mm": 6000}, {"name": u"Cimentación", "elevation_mm": -1500}],
              "origin_mm": {"x": 1000, "y": 2000, "z": 0}}
    assert _post(api, "/grid_levels/", doc, cuerpo, con_token=False).status == 401
    respuesta = _post(api, "/grid_levels/", doc, dict(cuerpo, simular=True))
    assert respuesta.status == 200, respuesta.data
    datos = respuesta.data
    assert datos["simulado"] is True and datos["count"] == 7 and "copia" not in datos
    plan = datos["plan"]
    assert [g["name"] for g in plan["grids_x"]] == [u"1", u"2", u"3"]
    assert [g["x_mm"] for g in plan["grids_x"]] == [1000.0, 7000.0, 13000.0]
    assert plan["grids_x"][0]["start_mm"] == {"x": 1000.0, "y": 0.0, "z": 0.0}       # 2000 - 2000 de extension
    assert plan["grids_x"][0]["end_mm"] == {"x": 1000.0, "y": 9000.0, "z": 0.0}       # 2000 + 5000 + 2000
    assert [g["name"] for g in plan["grids_y"]] == [u"B", u"C"]
    assert plan["grids_y"][1]["y_mm"] == 7000.0
    assert plan["grids_y"][0]["end_mm"]["x"] == 15000.0                               # 1000 + 12000 + 2000
    assert [n["name"] for n in plan["levels"]] == [u"Cimentación", u"Nivel 3"]         # ordenados por cota
    assert plan["counts"] == {"grids_x": 3, "grids_y": 2, "grids": 5, "levels": 2, "total": 7}
    assert len(datos["haria"]) == 7 and datos["haria"][0]["element_type"] == "grid"
    assert DB.Transaction.creadas == []
    assert len([e for e in doc.elementos.values() if isinstance(e, DB.Grid)]) == 1


def test_grid_levels_crea_en_una_transaccion(api, doc):
    cuerpo = {"x_spacings_mm": [6000, 6000], "y_spacings_mm": [5000], "y_names": ["B", "C"],
              "levels": [{"name": u"Nivel 3", "elevation_mm": 6000}]}
    respuesta = _post(api, "/grid_levels/", doc, cuerpo)
    assert respuesta.status == 200, respuesta.data
    datos = respuesta.data
    assert datos["ok"] is True and datos["count"] == 6 and datos["errors"] == [] if "errors" in datos else True
    assert datos["verificacion"] == {"coincide": True}
    assert _nombres_transacciones() == [u"IA: Rejilla y niveles"]
    assert sorted(g["name"] for g in datos["grids"]) == [u"1", u"2", u"3", u"B", u"C"]
    assert [n["name"] for n in datos["levels"]] == [u"Nivel 3"]
    assert datos["levels"][0]["elevation_mm"] == 6000.0 and datos["levels"][0]["categoria"] == u"Niveles"
    assert datos["copia"]["ruta"].endswith(".rvt")
    rejillas = [e for e in doc.elementos.values() if isinstance(e, DB.Grid)]
    assert sorted(g.Name for g in rejillas) == [u"1", u"2", u"3", u"A", u"B", u"C"]
    assert [e for e in doc.elementos.values() if isinstance(e, DB.Level) and e.Name == u"Nivel 3"]


def test_grid_levels_solo_niveles_y_secuencias(api, doc):
    respuesta = _post(api, "/grid_levels/", doc, {"levels": [{"elevation_mm": 9000}], "simular": True})
    assert respuesta.status == 200 and respuesta.data["plan"]["levels"] == [{"name": None, "elevation_mm": 9000.0}]
    respuesta = _post(api, "/grid_levels/", doc, {"x_spacings_mm": [1000, 1000], "x_names": "P1", "simular": True})
    assert [g["name"] for g in respuesta.data["plan"]["grids_x"]] == [u"P1", u"P2", u"P3"]
    respuesta = _post(api, "/grid_levels/", doc, {"y_spacings_mm": [1000, 1000], "y_names": "Z", "simular": True})
    assert [g["name"] for g in respuesta.data["plan"]["grids_y"]] == [u"Z", u"AA", u"AB"]
    respuesta = _post(api, "/grid_levels/", doc, {"x_spacings_mm": [1000], "x_names": "07", "simular": True})
    assert [g["name"] for g in respuesta.data["plan"]["grids_x"]] == [u"07", u"08"]
    # sin la otra direccion, la longitud por defecto es 10000
    assert respuesta.data["plan"]["grids_x"][0]["end_mm"]["y"] == 12000.0


def test_grid_levels_errores_controlados(api, doc):
    r = _post(api, "/grid_levels/", doc, {})
    assert r.status == 400 and "Nothing to create" in r.data["error"]
    r = _post(api, "/grid_levels/", doc, {"y_spacings_mm": [5000]})                   # "A" ya existe
    assert r.status == 400 and r.data["existing"] == [u"A"]
    r = _post(api, "/grid_levels/", doc, {"x_spacings_mm": [5000], "x_names": ["1", "1", "2"]})
    assert r.status == 400 and "names given" in r.data["error"]
    r = _post(api, "/grid_levels/", doc, {"x_spacings_mm": [5000], "x_names": "1", "y_spacings_mm": [5000], "y_names": "1"})
    assert r.status == 400 and r.data["repeated"] == [u"1", u"2"]
    r = _post(api, "/grid_levels/", doc, {"x_spacings_mm": [5000, -1]})
    assert r.status == 400 and "greater than 0" in r.data["error"]
    r = _post(api, "/grid_levels/", doc, {"x_spacings_mm": "5000"})
    assert r.status == 400
    r = _post(api, "/grid_levels/", doc, {"x_spacings_mm": [5000], "x_names": "#"})
    assert r.status == 400
    r = _post(api, "/grid_levels/", doc, {"levels": [{"name": u"Nivel 1", "elevation_mm": 0}]})
    assert r.status == 400 and r.data["available_levels"] == [u"Nivel 1", u"Nivel 2"]
    r = _post(api, "/grid_levels/", doc, {"levels": [{"name": u"Nivel 3"}]})
    assert r.status == 400 and "elevation_mm" in r.data["error"]
    r = _post(api, "/grid_levels/", doc, {"levels": [{"name": u"N", "elevation_mm": 1}, {"name": u"N", "elevation_mm": 2}]})
    assert r.status == 400
    doc.IsModifiable = True
    r = _post(api, "/grid_levels/", doc, {"levels": [{"name": u"Nivel 3", "elevation_mm": 6000}]})
    assert r.status == 409 and r.data["open_transaction"] is True
    doc.IsModifiable = False
    assert DB.Transaction.creadas == []


def test_grid_levels_limite_200_salvo_forzar(api, doc):
    cuerpo = {"x_spacings_mm": [1000] * 250, "x_names": "1"}
    r = _post(api, "/grid_levels/", doc, cuerpo)
    assert r.status == 400 and r.data["limite"] == 200 and r.data["cantidad"] == 251
    r = _post(api, "/grid_levels/", doc, dict(cuerpo, forzar=True, simular=True))
    assert r.status == 200 and r.data["count"] == 251


# ---------------------------------------------------------------------------
# create_sheet_set
# ---------------------------------------------------------------------------
def test_sheet_set_simulacion_y_vistas_ya_colocadas(api, doc):
    cuerpo = {"sheets": [
        {"number": "E-101", "name": u"Planta Nivel 1", "title_block": u"A1 métrico",
         "views": [{"view_name": u"Planta Nivel 1", "position_mm": {"x": 400, "y": 300}}, {"view_name": u"Planta Nivel 2"}]},
        {"number": "E-102", "name": u"Tablas", "views": [u"Tabla de muros", {"view_id": 100}]},
    ]}
    assert _post(api, "/sheet_set/", doc, cuerpo, con_token=False).status == 401
    respuesta = _post(api, "/sheet_set/", doc, dict(cuerpo, simular=True))
    assert respuesta.status == 200, respuesta.data
    datos = respuesta.data
    assert datos["simulado"] is True and DB.Transaction.creadas == []
    plan = datos["plan"]
    assert plan["counts"] == {"sheets": 2, "views": 2, "skipped": 2}
    primero, segundo = plan["sheets"]
    assert primero["title_block"] == u"A1 métrico" and [v["view"] for v in primero["views"]] == [u"Planta Nivel 1"]
    assert primero["skipped"][0]["view"] == u"Planta Nivel 2" and "E-100" in primero["skipped"][0]["reason"]
    assert [v["view"] for v in segundo["views"]] == [u"Tabla de muros"] and segundo["views"][0]["schedule"] is True
    assert segundo["skipped"][0]["view_id"] == 100 and "misma peticion" in segundo["skipped"][0]["reason"]
    assert datos["haria"][0]["accion"] == "crear" and datos["haria"][0]["number"] == "E-101"


def test_sheet_set_crea_planos_y_coloca_vistas(api, doc):
    cuerpo = {"sheets": [
        {"number": "E-101", "name": u"Planta Nivel 1", "views": [{"view_name": u"Planta Nivel 1", "position_mm": {"x": 400, "y": 300}}]},
        {"number": "E-102", "name": u"Tablas", "views": [u"Tabla de muros", u"Planta Nivel 2"]},
    ]}
    respuesta = _post(api, "/sheet_set/", doc, cuerpo)
    assert respuesta.status == 200, respuesta.data
    datos = respuesta.data
    assert datos["ok"] is True and datos["count"] == 4                     # 2 planos + viewport + tabla
    assert _nombres_transacciones() == [u"IA: Crear 2 planos"]
    planos = datos["sheets"]
    assert [p["number"] for p in planos] == ["E-101", "E-102"] and all(p["id"] for p in planos)
    assert planos[0]["views_placed"][0]["view"] == u"Planta Nivel 1"
    assert planos[0]["views_placed"][0]["position_mm"] == {"x": 400.0, "y": 300.0}
    assert planos[1]["views_placed"][0]["view"] == u"Tabla de muros"
    assert planos[1]["skipped"][0]["view"] == u"Planta Nivel 2"
    assert "1 skipped" in datos["message"]
    assert doc.elementos[70].IsActive is True                               # el cajetin se activo
    viewports = [e for e in doc.elementos.values() if isinstance(e, DB.Viewport)]
    assert sorted(v.ViewId.Value for v in viewports) == [100, 102]
    assert [e for e in doc.elementos.values() if isinstance(e, DB.ScheduleSheetInstance)][0].ScheduleId.Value == 300
    creados = dict((c["id"], c) for c in datos["creados"])
    assert creados[planos[0]["id"]]["categoria"] == u"Planos"


def test_sheet_set_errores_controlados(api, doc):
    assert _post(api, "/sheet_set/", doc, {}).status == 400
    assert _post(api, "/sheet_set/", doc, {"sheets": "E-101"}).status == 400
    r = _post(api, "/sheet_set/", doc, {"sheets": [{"number": "E-100"}]})
    assert r.status == 400 and "already exists" in r.data["error"]
    r = _post(api, "/sheet_set/", doc, {"sheets": [{"number": "E-101"}, {"number": "E-101"}]})
    assert r.status == 400 and "repeated" in r.data["error"]
    r = _post(api, "/sheet_set/", doc, {"sheets": [{"number": "E-101", "views": [u"No existe", {"view_id": 999}]}]})
    assert r.status == 404 and r.data["missing_views"] == [u"No existe", u"id 999"]
    r = _post(api, "/sheet_set/", doc, {"sheets": [{"number": "E-101", "title_block": u"A0"}]})
    assert r.status == 404
    r = _post(api, "/sheet_set/", doc, {"sheets": [{"number": "E-101", "views": [{"position_mm": {"x": 1}}]}]})
    assert r.status == 400
    r = _post(api, "/sheet_set/", doc, {"sheets": [{"number": "E-101", "views": [{"view_name": u"Planta Nivel 1", "position_mm": "centro"}]}]})
    assert r.status == 400
    doc.elementos.pop(70)
    r = _post(api, "/sheet_set/", doc, {"sheets": [{"number": "E-101"}]})
    assert r.status == 404 and "title block" in r.data["error"]
    assert DB.Transaction.creadas == []


# ---------------------------------------------------------------------------
# import_from_civil
# ---------------------------------------------------------------------------
def test_import_civil_landxml_crea_toposolido(api, doc, tmp_path):
    ruta = _archivo(tmp_path, "terreno.xml", LANDXML)
    cuerpo = {"file_path": ruta, "level": u"Nivel 1"}
    assert _post(api, "/import_civil/", doc, cuerpo, con_token=False).status == 401
    respuesta = _post(api, "/import_civil/", doc, dict(cuerpo, simular=True))
    assert respuesta.status == 200, respuesta.data
    haria = respuesta.data["haria"][0]
    assert haria["element_type"] == "toposolid" and haria["points"] == 3 and haria["type"] == u"Terreno"
    assert haria["format"].startswith("LandXML Surface/Definition/Pnts")
    assert haria["extent_mm"]["x"] == [300000000.0, 300010000.0]                    # este -> x en mm
    assert haria["extent_mm"]["y"] == [4500000000.0, 4500010000.0]                  # norte -> y
    assert respuesta.data["warnings"] == [u"punto saltado: 'mal'"]
    assert respuesta.data["plan"]["counts"] == {"points": 3, "elements": 1}
    assert DB.Transaction.creadas == []

    respuesta = _post(api, "/import_civil/", doc, dict(cuerpo, origin_offset_mm={"x": -300000000, "y": -4500000000, "z": 0}))
    assert respuesta.status == 200, respuesta.data
    datos = respuesta.data
    assert datos["ok"] is True and datos["count"] == 1 and datos["creados"][0]["categoria"] == u"Toposólido"
    assert _nombres_transacciones() == [u"IA: Importar topografia terreno.xml"]
    topo = doc.GetElement(DB.ElementId(datos["toposolid_id"]))
    assert isinstance(topo, DB.Toposolid) and len(topo.puntos) == 3
    assert abs(topo.puntos[0].X * 304.8 - 250.0) < 1e-6                             # 300000.25 m - desfase
    assert abs(topo.puntos[0].Y * 304.8 - 500.0) < 1e-6
    assert topo.LevelId == DB.ElementId(1) and topo.type_id == DB.ElementId(80)
    assert datos["source"]["origin_offset_mm"] == {"x": -300000000.0, "y": -4500000000.0, "z": 0.0}


def test_import_civil_csv_pnezd(api, doc, tmp_path):
    ruta = _archivo(tmp_path, "puntos.csv", u"P,N,E,Z,D\n1,100,200,5,BASE\n2,110,200,6,\n3,100,210,5.5,\n")
    respuesta = _post(api, "/import_civil/", doc, {"file_path": ruta, "level": u"Nivel 2", "type_name": u"Terreno"})
    assert respuesta.status == 200, respuesta.data
    assert respuesta.data["source"]["format"].startswith("P,N,E,Z") and respuesta.data["source"]["points"] == 3
    topo = doc.GetElement(DB.ElementId(respuesta.data["toposolid_id"]))
    assert abs(topo.puntos[0].X * 304.8 - 200000.0) < 1e-6 and topo.LevelId == DB.ElementId(2)
    respuesta = _post(api, "/import_civil/", doc, {"file_path": ruta, "level": u"Nivel 2", "units": "mm", "simular": True})
    assert respuesta.data["haria"][0]["extent_mm"]["x"] == [200.0, 210.0]


def test_import_civil_errores_controlados(api, doc, tmp_path, monkeypatch):
    ruta = _archivo(tmp_path, "terreno.xml", LANDXML)
    assert _post(api, "/import_civil/", doc, {"level": u"Nivel 1"}).status == 400
    assert _post(api, "/import_civil/", doc, {"file_path": str(tmp_path / "no.xml"), "level": u"Nivel 1"}).status == 404
    assert _post(api, "/import_civil/", doc, {"file_path": ruta}).status == 400
    r = _post(api, "/import_civil/", doc, {"file_path": ruta, "level": u"Sótano"})
    assert r.status == 404 and r.data["available_levels"] == [u"Nivel 1", u"Nivel 2"]
    r = _post(api, "/import_civil/", doc, {"file_path": ruta, "level": u"Nivel 1", "type_name": u"Roca"})
    assert r.status == 404 and r.data["available_types"] == [u"Terreno"]
    r = _post(api, "/import_civil/", doc, {"file_path": _archivo(tmp_path, "otro.ifc", u"x"), "level": u"Nivel 1"})
    assert r.status == 400 and "Unsupported" in r.data["error"]
    r = _post(api, "/import_civil/", doc, {"file_path": _archivo(tmp_path, "vacio.xml", u"<LandXML/>"), "level": u"Nivel 1"})
    assert r.status == 400 and "Pnts" in r.data["error"]
    r = _post(api, "/import_civil/", doc, {"file_path": _archivo(tmp_path, "dos.csv", u"1,2,3\n4,5,6\n"), "level": u"Nivel 1"})
    assert r.status == 400 and "3 points" in r.data["error"]
    monkeypatch.delattr(DB, "Toposolid")
    r = _post(api, "/import_civil/", doc, {"file_path": ruta, "level": u"Nivel 1"})
    assert r.status == 400 and "2024" in r.data["error"]
    assert DB.Transaction.creadas == []


def test_import_civil_dwg_vincula_y_adquiere_coordenadas(api, doc, tmp_path):
    ruta = _archivo(tmp_path, "topografia.dwg", u"dwg")
    cuerpo = {"file_path": ruta, "level": u"Nivel 1", "use_shared_coordinates": True}
    respuesta = _post(api, "/import_civil/", doc, dict(cuerpo, simular=True))
    assert respuesta.status == 200, respuesta.data
    haria = respuesta.data["haria"]
    assert haria[0]["element_type"] == "cad_link" and haria[0]["placement"] == "center"
    assert haria[0]["view"] == u"Planta Nivel 1" and haria[0]["acquire_coordinates"] is True
    assert haria[1]["accion"] == "adquirir_coordenadas" and haria[1]["antes"]["true_north_deg"] == 0.0
    assert respuesta.data["plan"]["steps"] == ["vincular", "adquirir_coordenadas"]
    assert doc.vinculados == [] and DB.Transaction.creadas == []

    respuesta = _post(api, "/import_civil/", doc, dict(cuerpo, origin_offset_mm={"x": 1000, "y": 0, "z": 0}))
    assert respuesta.status == 200, respuesta.data
    datos = respuesta.data
    assert datos["ok"] is True and datos["count"] == 1 and datos["link_id"] == datos["creados"][0]["id"]
    assert _nombres_transacciones() == [u"IA: Importar civil topografia.dwg"]
    vinculo = doc.vinculados[0]
    assert vinculo.placement is DB.ImportPlacement.Centered and vinculo.vista_id == DB.ElementId(100)
    assert abs(vinculo.Location.Point.X * 304.8 - 1000.0) < 1e-6
    assert doc.coordenadas_adquiridas == [datos["link_id"]]
    assert datos["coordinates"]["antes"]["true_north_deg"] == 0.0
    assert abs(datos["coordinates"]["despues"]["true_north_deg"] - 5.7296) < 1e-3
    assert "acquired" in datos["message"]


def test_import_civil_dwg_409_si_ya_hay_coordenadas_compartidas(api, doc, tmp_path):
    ruta = _archivo(tmp_path, "topografia.dwg", u"dwg")
    doc.posicion = mf.PosicionProyecto(500.0, 0.0, 0.0, 0.0)
    cuerpo = {"file_path": ruta, "level": u"Nivel 1", "use_shared_coordinates": True}
    r = _post(api, "/import_civil/", doc, cuerpo)
    assert r.status == 409 and r.data["shared_coordinates_set"] is True and doc.vinculados == []
    r = _post(api, "/import_civil/", doc, dict(cuerpo, forzar=True))
    assert r.status == 200 and len(doc.vinculados) == 1
    # sin coordenadas compartidas no hay 409 aunque el proyecto ya las tenga
    r = _post(api, "/import_civil/", doc, {"file_path": ruta, "level": u"Nivel 2", "placement": "origin"})
    assert r.status == 200 and doc.vinculados[1].placement is DB.ImportPlacement.Origin
    assert doc.vinculados[1].vista_id == DB.ElementId(102) and "coordinates" not in r.data
    r = _post(api, "/import_civil/", doc, {"file_path": ruta, "level": u"Nivel 1", "placement": "arriba"})
    assert r.status == 400


# ---------------------------------------------------------------------------
# Los manejadores de la fase 1 siguen funcionando con los helpers extraidos
# ---------------------------------------------------------------------------
def test_create_grid_y_create_level_siguen_creando(api, doc):
    r = _post(api, "/create_grid/", doc, {"grids": [{"name": "Q", "start_point": {"x": 0, "y": 0, "z": 0}, "end_point": {"x": 0, "y": 9000, "z": 0}}]})
    assert r.status == 200 and r.data["ok"] is True and r.data["creados"][0]["name"] == "Q"
    r = _post(api, "/create_level/", doc, {"levels": [{"name": u"Nivel 3", "elevation": 6000}]})
    assert r.status == 200 and r.data["ok"] is True
    assert r.data["creados"][0]["elevation_mm"] == 6000.0 and r.data["creados"][0]["name"] == u"Nivel 3"


def test_create_sheet_sigue_creando(api, doc):
    r = _post(api, "/create_sheet/", doc, {"sheet_number": "E-101", "sheet_name": u"Planta", "title_block_name": u"A1 métrico"})
    assert r.status == 200 and r.data["ok"] is True
    assert r.data["created"]["sheet_number"] == "E-101" and r.data["created"]["title_block"] == u"A1 métrico"
    assert _post(api, "/create_sheet/", doc, {"sheet_number": "E-100"}).status == 400
    assert _post(api, "/create_sheet/", doc, {"title_block_name": u"A0"}).status == 404


def test_create_toposolid_y_link_file_siguen_creando(api, doc, tmp_path):
    r = _post(api, "/create_toposolid/", doc, {"level_name": u"Nivel 1", "points": [
        {"x": 0, "y": 0, "z": 0}, {"x": 1000, "y": 0, "z": 100}, {"x": 0, "y": 1000, "z": 50}]})
    assert r.status == 200 and r.data["ok"] is True and isinstance(doc.GetElement(DB.ElementId(r.data["toposolid_id"])), DB.Toposolid)
    ruta = str(tmp_path / "plano.dwg")
    with io.open(ruta, "w", encoding="utf-8") as archivo:
        archivo.write(u"dwg")
    r = _post(api, "/link_file/", doc, {"file_path": ruta, "mode": "link"})
    assert r.status == 200 and r.data["ok"] is True and r.data["mode"] == "link"
    assert doc.vinculados[0].placement is DB.ImportPlacement.Origin


# ---------------------------------------------------------------------------
# Correcciones de la revision de la 2a
# ---------------------------------------------------------------------------
def test_opciones_cad_dgn_usa_dgnimportoptions():
    """Document.Link con DWGImportOptions no admite .dgn: hace falta DGNImportOptions."""
    from interop import opciones_cad
    opciones = opciones_cad(".dgn", "center")
    assert isinstance(opciones, DB.DGNImportOptions) and opciones.Placement is DB.ImportPlacement.Centered
    assert isinstance(opciones_cad(".dwg", "origin"), DB.DWGImportOptions)
    assert isinstance(opciones_cad(".dxf"), DB.DWGImportOptions)


def test_import_civil_dwg_409_si_un_punto_base_esta_fijado(api, doc, tmp_path, monkeypatch):
    """Misma proteccion que set_project_location: adquirir coordenadas mueve los puntos base."""
    import macros

    class _Punto(object):
        Pinned = True
        Clipped = False

    monkeypatch.setattr(macros, "puntos_base", lambda d: [("survey point", _Punto())])
    ruta = _archivo(tmp_path, "topografia.dwg", u"dwg")
    cuerpo = {"file_path": ruta, "level": u"Nivel 1", "use_shared_coordinates": True}
    r = _post(api, "/import_civil/", doc, dict(cuerpo, simular=True))
    assert r.status == 409 and r.data["pinned"] is True and r.data["clipped"] is False
    assert "survey point is pinned" in r.data["error"] and doc.vinculados == [] and DB.Transaction.creadas == []
    # sin adquirir coordenadas el punto base no importa
    assert _post(api, "/import_civil/", doc, {"file_path": ruta, "level": u"Nivel 1"}).status == 200
    # con forzar se adquiere igualmente y se regenera antes
    r = _post(api, "/import_civil/", doc, dict(cuerpo, forzar=True))
    assert r.status == 200 and doc.coordenadas_adquiridas == [r.data["link_id"]] and doc.regeneraciones >= 1


def test_comprobar_puntos_base_libres():
    from coordenadas import comprobar_puntos_base_libres
    from escritura import EscrituraRechazada

    class _Punto(object):
        def __init__(self, pinned, clipped):
            self.Pinned, self.Clipped = pinned, clipped

    comprobar_puntos_base_libres([("project base point", _Punto(False, False)), ("survey point", None)])
    with pytest.raises(EscrituraRechazada) as info:
        comprobar_puntos_base_libres([("project base point", _Punto(True, True))])
    assert info.value.status == 409 and "pinned and clipped" in str(info.value)


def test_sheet_set_una_leyenda_puede_ir_en_varios_planos(api, doc):
    leyenda = mf.VistaPlanta(doc, 105, u"Leyenda general")
    leyenda.ViewType = DB.ViewType.Legend
    viewport = DB.Viewport()
    viewport.ViewId = DB.ElementId(105)
    viewport.SheetId = DB.ElementId(500)
    doc.agregar(viewport)
    cuerpo = {"sheets": [
        {"number": "E-101", "views": [u"Leyenda general", u"Planta Nivel 1"]},
        {"number": "E-102", "views": [u"Leyenda general", u"Planta Nivel 1"]},
    ]}
    r = _post(api, "/sheet_set/", doc, dict(cuerpo, simular=True))
    assert r.status == 200, r.data
    plan = r.data["plan"]
    assert [v["view"] for v in plan["sheets"][0]["views"]] == [u"Leyenda general", u"Planta Nivel 1"]
    assert [v["view"] for v in plan["sheets"][1]["views"]] == [u"Leyenda general"]
    assert [s["view"] for s in plan["sheets"][1]["skipped"]] == [u"Planta Nivel 1"]
    r = _post(api, "/sheet_set/", doc, cuerpo)
    assert r.status == 200, r.data
    assert [v["view"] for v in r.data["sheets"][1]["views_placed"]] == [u"Leyenda general"]
