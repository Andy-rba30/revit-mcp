# -*- coding: utf-8 -*-
"""Entrega 2b (0.5.0), bloque 2: escritura de estructuras metalicas sobre el modelo simulado.

  POST /load_steel_profile/         catalogo (LoadFamilySymbol por tipo) y sin catalogo (LoadFamily), 409 si ya cargada
  POST /create_steel_frame/         MACRO: plan con simular, pilares por interseccion, vigas consecutivas, marcas, saltos
  POST /create_bracing/             cada patron con su numero de barras y puntos intermedios (elevacion interna)
  POST /create_truss/               Truss.Create sobre un SketchPlane del nivel, 404 con los tipos disponibles
  POST /set_structural_properties/  pinned / fixed / diccionario parcial (dos fases), enums, mm, grados,
                                    AnalyticalMember de reserva, fallidos y no_disponibles
  POST /create_steel_connection/    409 no_soportado sin el modulo; con el modulo, creacion y aprobacion
  POST /add_plate/                  familia alojada en cara (top, bottom, web) y de punto
  POST /split_beam/                 FamilyInstance.Split y, sin el, CopyElement con la union del extremo suelta
  POST /join_geometry/              element_ids[] en cadena y coping (los dos argumentos siguen funcionando)

Reutiliza las fixtures y elementos de tests/test_acero.py (Revit "en espanol").
"""
import io
import math
import os

import pytest
from pyrevit import DB, routes

import modelo_falso as mf
import seguridad
from test_acero import Viga, TOKEN, BIP, BIC, ST, doc as doc_base, _biblioteca  # noqa: F401 (fixture)

MM = mf.MM_TO_FEET


def _xyz(mm):
    return DB.XYZ(mm[0] * MM, mm[1] * MM, mm[2] * MM)


def _rejilla(doc, identificador, nombre, p0, p1):
    rejilla = mf.Rejilla(doc, identificador, nombre)
    rejilla.Curve = DB.Line.CreateBound(_xyz(p0), _xyz(p1))
    return rejilla


@pytest.fixture
def doc(doc_base):
    """El modelo de test_acero mas rejillas (1, 2, 3 / A, B y una curva) y un tipo de cercha."""
    d = doc_base
    _rejilla(d, 60, u"1", (0, -1000, 0), (0, 8000, 0))
    _rejilla(d, 61, u"2", (6000, -1000, 0), (6000, 8000, 0))
    _rejilla(d, 62, u"3", (12000, -1000, 0), (12000, 8000, 0))
    _rejilla(d, 63, u"A", (-1000, 0, 0), (14000, 0, 0))
    _rejilla(d, 64, u"B", (-1000, 5000, 0), (14000, 5000, 0))
    curva = mf.Rejilla(d, 65, u"C")
    curva.Curve = DB.Arc()
    mf.TipoCercha(d, 90, u"Cercha 12 m")
    return d


@pytest.fixture
def api():
    seguridad.establecer_token(TOKEN)
    DB.Transaction.creadas = []
    DB.Transaction.resultado_commit = DB.TransactionStatus.Committed
    api = routes.API("revit_mcp")
    from acero import register_acero_routes
    from subproyectos import register_subproyectos_routes

    register_acero_routes(api)
    register_subproyectos_routes(api)
    return api


def _post(api, ruta, doc, cuerpo, con_token=True):
    datos = dict(cuerpo)
    if con_token:
        datos["token"] = TOKEN
    return api.rutas[(ruta, "POST")](doc=doc, request=routes.Request(path=ruta, data=datos))


def _transacciones():
    return [t.nombre for t in DB.Transaction.creadas]


def _instancias(doc):
    return [e for e in doc.elementos.values() if isinstance(e, mf.Instancia)]


# ---------------------------------------------------------------------------
# /load_steel_profile/
# ---------------------------------------------------------------------------
def _preparar_biblioteca(doc, tmp_path):
    raiz = _biblioteca(tmp_path)
    doc.Application.bibliotecas = {u"Biblioteca métrica": raiz}
    ruta_w = os.path.join(raiz, "Estructura", "Perfiles", "W-Perfiles.rfa")
    doc.familias_cargables[ruta_w] = {"nombre": u"W-Perfiles", "categoria": u"Armazón estructural",
                                      "bic": BIC.OST_StructuralFraming, "catalogo": [u"W12X26", u"W16X31"]}
    ruta_hea = os.path.join(raiz, "Estructura", "Perfiles", "HEA.rfa")
    with io.open(ruta_hea, "wb") as archivo:
        archivo.write(b"RFA")
    doc.familias_cargables[ruta_hea] = {"nombre": u"HEA", "categoria": u"Pilares estructurales",
                                        "bic": BIC.OST_StructuralColumns, "tipos": [u"HEA200", u"HEA240"]}
    return ruta_w, ruta_hea


def test_load_steel_profile_con_catalogo(api, doc, tmp_path):
    ruta_w, _ = _preparar_biblioteca(doc, tmp_path)
    assert _post(api, "/load_steel_profile/", doc, {"family_name": u"W-Perfiles"}, con_token=False).status == 401
    assert _post(api, "/load_steel_profile/", doc, {}).status == 400
    r = _post(api, "/load_steel_profile/", doc, {"family_name": u"NoExiste"})
    assert r.status == 404 and r.data["library_paths"] == [str(tmp_path / "Biblioteca")]
    r = _post(api, "/load_steel_profile/", doc, {"family_name": u"W-Perfiles"})
    assert r.status == 400 and r.data["available_types"] == [u"W12X26", u"W16X31"] and "catalog" in r.data["error"]
    r = _post(api, "/load_steel_profile/", doc, {"family_name": u"W-Perfiles", "type_names": [u"W99X99"]})
    assert r.status == 404 and r.data["available_types"] == [u"W12X26", u"W16X31"]
    cuerpo = {"family_name": u"W-Perfiles", "type_names": [u"W16X31"]}
    r = _post(api, "/load_steel_profile/", doc, dict(cuerpo, simular=True))
    assert r.status == 200, r.data
    assert r.data["simulado"] is True and r.data["haria"] == [{"accion": "cargar_tipo", "family": u"W-Perfiles", "type": u"W16X31",
                                                                "file_path": ruta_w, "catalog": True}]
    assert r.data["plan"]["already_loaded"] is False and DB.Transaction.creadas == [] and doc.cargas == []
    r = _post(api, "/load_steel_profile/", doc, cuerpo)
    assert r.status == 200, r.data
    assert r.data["ok"] is True and r.data["count"] == 1 and r.data["creados"][0]["tipo"] == u"W16X31"
    assert r.data["creados"][0]["categoria"] == u"Armazón estructural" and r.data["types"][0]["loaded"] is True
    assert r.data["family"] == u"W-Perfiles" and r.data["family_id"] and r.data["catalog_path"].endswith("W-Perfiles.txt")
    assert _transacciones() == [u"IA: Cargar perfil W-Perfiles"] and doc.cargas == [(ruta_w, [u"W16X31"])]
    simbolo = doc.GetElement(DB.ElementId(r.data["creados"][0]["id"]))
    assert simbolo.IsActive is True and simbolo.Family.Name == u"W-Perfiles"
    # ya cargada con ese tipo: 409 salvo overwrite; un tipo nuevo del catalogo si se carga
    r = _post(api, "/load_steel_profile/", doc, cuerpo)
    assert r.status == 409 and r.data["already_loaded"] is True and r.data["types"] == [u"W16X31"]
    r = _post(api, "/load_steel_profile/", doc, {"family_name": u"W-Perfiles", "type_names": [u"W16X31", u"W12X26"]})
    assert r.status == 200 and r.data["count"] == 1 and r.data["creados"][0]["tipo"] == u"W12X26" and r.data["ya_existian"] == [u"W16X31"]
    r = _post(api, "/load_steel_profile/", doc, dict(cuerpo, overwrite=True))
    assert r.status == 200 and r.data["types"][0]["loaded"] is False and r.data["count"] == 1


def test_load_steel_profile_sin_catalogo_y_errores(api, doc, tmp_path):
    _, ruta_hea = _preparar_biblioteca(doc, tmp_path)
    r = _post(api, "/load_steel_profile/", doc, {"file_path": ruta_hea})
    assert r.status == 200, r.data
    assert r.data["count"] == 2 and sorted(t["type"] for t in r.data["types"]) == [u"HEA200", u"HEA240"]
    assert all(t["loaded"] for t in r.data["types"]) and r.data["catalog_path"] is None
    assert _transacciones() == [u"IA: Cargar perfil HEA"]
    r = _post(api, "/load_steel_profile/", doc, {"file_path": ruta_hea, "type_names": [u"HEA999"], "overwrite": True})
    assert r.status == 404 and r.data["available_types"] == [u"HEA200", u"HEA240"]
    assert DB.Transaction.creadas[-1].estado == DB.TransactionStatus.RolledBack
    assert _post(api, "/load_steel_profile/", doc, {"file_path": str(tmp_path / "no.rfa")}).status == 404
    assert _post(api, "/load_steel_profile/", doc, {"file_path": str(tmp_path / "Modelo.rvt")}).status == 400
    r = _post(api, "/load_steel_profile/", doc, {"file_path": ruta_hea, "type_names": "x"})
    assert r.status == 404 and r.data["available_types"] == [u"HEA200", u"HEA240"]     # ya cargada, sin ese tipo
    assert _post(api, "/load_steel_profile/", doc, {"file_path": ruta_hea}).status == 409             # ya cargada, sin overwrite


# ---------------------------------------------------------------------------
# /create_steel_frame/
# ---------------------------------------------------------------------------
PORTICO = {"grids_x": [u"1", u"2"], "grids_y": [u"A", u"B"], "levels": [u"Nivel 1"],
           "column_type": u"HEB200", "beam_type": u"IPE300", "mark_prefix": u"P"}


def test_create_steel_frame_simulacion_con_plan(api, doc):
    antes = len(doc.elementos)
    assert _post(api, "/create_steel_frame/", doc, PORTICO, con_token=False).status == 401
    r = _post(api, "/create_steel_frame/", doc, dict(PORTICO, simular=True))
    assert r.status == 200, r.data
    datos = r.data
    assert datos["simulado"] is True and datos["count"] == 8 and "copia" not in datos
    plan = datos["plan"]
    assert plan["counts"] == {"columns": 4, "beams": 4, "total": 8}
    assert plan["grids_x"] == [u"1", u"2"] and plan["grids_y"] == [u"A", u"B"] and plan["levels"] == [u"Nivel 1"]
    assert plan["intersections"] == [u"A-1", u"A-2", u"B-1", u"B-2"]
    assert plan["columns"][0] == {"label": u"A-1", "grid_x": u"1", "grid_y": u"A", "point_mm": {"x": 0.0, "y": 0.0, "z": 0.0},
                                  "base_level": u"Nivel 1", "top_level": u"Nivel 2", "mark": u"P01"}
    assert plan["columns"][3]["point_mm"] == {"x": 6000.0, "y": 5000.0, "z": 0.0} and plan["columns"][3]["mark"] == u"P04"
    vigas = plan["beams"]
    assert [(v["from"], v["to"], v["direction"]) for v in vigas] == [(u"A-1", u"A-2", "x"), (u"B-1", u"B-2", "x"),
                                                                     (u"A-1", u"B-1", "y"), (u"A-2", u"B-2", "y")]
    assert vigas[0]["start_mm"] == {"x": 0.0, "y": 0.0, "z": 0.0} and vigas[0]["length_mm"] == 6000.0 and vigas[0]["mark"] == u"P05"
    assert vigas[2]["length_mm"] == 5000.0 and vigas[3]["mark"] == u"P08"
    assert plan["column_type"] == u"HEB: HEB200" and plan["beam_type"] == u"IPE: IPE300"
    assert any(u"C" in aviso for aviso in plan["warnings"])          # la rejilla curva se ignora
    assert datos["haria"][0]["element_type"] == "structural_column" and datos["haria"][4]["element_type"] == "beam"
    assert DB.Transaction.creadas == [] and len(doc.elementos) == antes


def test_create_steel_frame_crea_pilares_y_vigas_en_una_transaccion(api, doc):
    r = _post(api, "/create_steel_frame/", doc, PORTICO)
    assert r.status == 200, r.data
    datos = r.data
    assert datos["ok"] is True and datos["count"] == 8 and datos["verificacion"] == {"coincide": True}
    assert _transacciones() == [u"IA: Portico metalico"]
    columnas, vigas = datos["creados"]["columns"], datos["creados"]["beams"]
    assert len(columnas) == 4 and len(vigas) == 4 and len(datos["creados_ids"]) == 8
    assert columnas[0]["label"] == u"A-1" and columnas[0]["top_level"] == u"Nivel 2" and columnas[0]["point_mm"]["z"] == 0.0
    assert columnas[0]["categoria"] == u"Pilares estructurales" and columnas[0]["mark"] == u"P01"
    assert vigas[0]["categoria"] == u"Armazón estructural" and vigas[0]["nivel"] == u"Nivel 1" and vigas[0]["from"] == u"A-1"
    assert vigas[0]["start_mm"] == {"x": 0.0, "y": 0.0, "z": 0.0} and vigas[0]["end_mm"] == {"x": 6000.0, "y": 0.0, "z": 0.0}
    assert datos["plan"]["counts"]["total"] == 8 and datos["copia"]["ruta"].endswith(".rvt")
    marcas = sorted(e.LookupParameter(u"Marca").AsString() for e in _instancias(doc))
    assert marcas == [u"P01", u"P02", u"P03", u"P04", u"P05", u"P06", u"P07", u"P08"]
    pilar = doc.GetElement(DB.ElementId(columnas[0]["id"]))
    assert pilar.get_Parameter(BIP.FAMILY_TOP_LEVEL_PARAM).AsElementId() == DB.ElementId(2)


def test_create_steel_frame_todas_las_rejillas_niveles_y_saltos(api, doc):
    base = {"column_type": u"HEB200", "beam_type": u"IPE300", "simular": True}
    r = _post(api, "/create_steel_frame/", doc, dict(base, levels=[u"Nivel 1"]))
    assert r.status == 200, r.data
    plan = r.data["plan"]
    assert plan["grids_x"] == [u"1", u"2", u"3"] and plan["grids_y"] == [u"A", u"B"]
    assert plan["counts"] == {"columns": 6, "beams": 7, "total": 13}      # 4 en x + 3 en y
    r = _post(api, "/create_steel_frame/", doc, dict(base, levels=[u"Nivel 1"], beam_directions="x",
                                                      skip_columns_at=[u"A-1"], skip_beams_at=[u"A-1/A-2"]))
    plan = r.data["plan"]
    assert plan["counts"] == {"columns": 5, "beams": 3, "total": 8}
    assert plan["skipped"] == {"columns": [{"label": u"A-1", "level": u"Nivel 1"}],
                               "beams": [{"from": u"A-1", "to": u"A-2", "level": u"Nivel 1"}]}
    assert all(v["direction"] == "x" for v in plan["beams"]) and all(v["mark"] is None for v in plan["beams"])
    # dos niveles: pilares de cada nivel al siguiente; en la cubierta no hay nivel superior
    r = _post(api, "/create_steel_frame/", doc, dict(base, grids_x=[u"1", u"2"], grids_y=[u"A", u"B"], levels=[u"Nivel 2", u"Nivel 1"]))
    plan = r.data["plan"]
    assert plan["levels"] == [u"Nivel 1", u"Nivel 2"] and plan["counts"] == {"columns": 8, "beams": 8, "total": 16}
    assert plan["columns"][0]["top_level"] == u"Nivel 2" and plan["columns"][4]["top_level"] == u"Cubierta"
    r = _post(api, "/create_steel_frame/", doc, dict(base, grids_x=[u"1", u"2"], grids_y=[u"A"], levels=[u"Cubierta"]))
    plan = r.data["plan"]
    assert plan["counts"] == {"columns": 2, "beams": 1, "total": 3} and plan["columns"][0]["top_level"] is None
    assert any(u"No hay nivel por encima" in aviso for aviso in plan["warnings"])
    # sin niveles indicados se usan todos
    r = _post(api, "/create_steel_frame/", doc, dict(base, grids_x=[u"1", u"2"], grids_y=[u"A"]))
    assert r.data["plan"]["levels"] == [u"Nivel 1", u"Nivel 2", u"Cubierta"] and r.data["plan"]["counts"]["total"] == 9


def test_create_steel_frame_errores_controlados(api, doc):
    r = _post(api, "/create_steel_frame/", doc, dict(PORTICO, column_type=u"UPN200"))
    assert r.status == 404 and u"HEB: HEB200" in r.data["available_types"]
    r = _post(api, "/create_steel_frame/", doc, dict(PORTICO, beam_type=u"IPE999"))
    assert r.status == 404 and u"IPE: IPE300" in r.data["available_types"]
    r = _post(api, "/create_steel_frame/", doc, dict(PORTICO, grids_x=[u"9"]))
    assert r.status == 404 and r.data["available_grids"] == [u"1", u"2", u"3", u"A", u"B"]   # la curva C no cuenta
    r = _post(api, "/create_steel_frame/", doc, dict(PORTICO, levels=[u"Sótano"]))
    assert r.status == 404 and r.data["available_levels"] == [u"Cubierta", u"Nivel 1", u"Nivel 2"]
    r = _post(api, "/create_steel_frame/", doc, dict(PORTICO, skip_columns_at=[u"Z-9"]))
    assert r.status == 400 and r.data["available_labels"] == [u"A-1", u"A-2", u"B-1", u"B-2"]
    assert _post(api, "/create_steel_frame/", doc, dict(PORTICO, skip_beams_at=[u"A-1"])).status == 400
    assert _post(api, "/create_steel_frame/", doc, dict(PORTICO, beam_directions="z")).status == 400
    assert _post(api, "/create_steel_frame/", doc, {"beam_type": u"IPE300"}).status == 400
    r = _post(api, "/create_steel_frame/", doc, dict(PORTICO, skip_columns_at=[u"A-1", u"A-2", u"B-1", u"B-2"],
                                                      skip_beams_at=[u"A-1/A-2", u"B-1/B-2", u"A-1/B-1", u"A-2/B-2"]))
    assert r.status == 400 and "Nothing to create" in r.data["error"]
    for identificador in (60, 61, 62, 63, 64, 65):
        doc.elementos.pop(identificador)
    r = _post(api, "/create_steel_frame/", doc, PORTICO)
    assert r.status == 400 and "no straight grids" in r.data["error"]
    assert DB.Transaction.creadas == []


def test_create_steel_frame_limite_200_salvo_forzar(api, doc):
    for i in range(30):
        _rejilla(doc, 200 + i, u"X{}".format(i), (1000 + i * 300, -1000, 0), (1000 + i * 300, 8000, 0))
    cuerpo = {"grids_y": [u"A", u"B"], "levels": [u"Nivel 1", u"Nivel 2", u"Cubierta"],
              "column_type": u"HEB200", "beam_type": u"IPE300"}
    r = _post(api, "/create_steel_frame/", doc, cuerpo)
    assert r.status == 400 and r.data["limite"] == 200 and r.data["cantidad"] > 200
    r = _post(api, "/create_steel_frame/", doc, dict(cuerpo, forzar=True, simular=True))
    assert r.status == 200 and r.data["plan"]["counts"]["total"] == r.data["count"] > 200
    assert DB.Transaction.creadas == []


# ---------------------------------------------------------------------------
# /create_bracing/
# ---------------------------------------------------------------------------
def _vano(patron):
    return {"start_point_mm": {"x": 0, "y": 0}, "end_point_mm": {"x": 6000, "y": 0},
            "level_bottom": u"Nivel 1", "level_top": u"Nivel 2", "pattern": patron}


def test_create_bracing_patrones_con_elevacion_interna(api, doc, monkeypatch):
    # el punto base desplazado: Elevation (mostrada) difiere de ProjectElevation (interna), como en test_macros
    monkeypatch.setattr(DB.Level, "desfase_base", 18450.0 * MM)
    cuerpo = {"bays": [_vano("single"), _vano("X"), _vano("V"), _vano("inverted_V"), _vano("K")], "brace_type": u"IPE200"}
    assert _post(api, "/create_bracing/", doc, cuerpo, con_token=False).status == 401
    r = _post(api, "/create_bracing/", doc, dict(cuerpo, simular=True))
    assert r.status == 200, r.data
    datos = r.data
    assert datos["simulado"] is True and datos["count"] == 9 and datos["plan"]["counts"] == {"bays": 5, "braces": 9}
    por_vano = {}
    for h in datos["haria"]:
        por_vano.setdefault(h["pattern"], []).append((h["start_mm"], h["end_mm"]))
    assert por_vano["single"] == [({"x": 0.0, "y": 0.0, "z": 0.0}, {"x": 6000.0, "y": 0.0, "z": 3500.0})]
    assert por_vano["X"] == [({"x": 0.0, "y": 0.0, "z": 0.0}, {"x": 6000.0, "y": 0.0, "z": 3500.0}),
                             ({"x": 6000.0, "y": 0.0, "z": 0.0}, {"x": 0.0, "y": 0.0, "z": 3500.0})]
    assert [e for _, e in por_vano["V"]] == [{"x": 3000.0, "y": 0.0, "z": 3500.0}] * 2
    assert [e for _, e in por_vano["inverted_V"]] == [{"x": 3000.0, "y": 0.0, "z": 0.0}] * 2
    assert [s for s, _ in por_vano["inverted_V"]] == [{"x": 0.0, "y": 0.0, "z": 3500.0}, {"x": 6000.0, "y": 0.0, "z": 3500.0}]
    assert [e for _, e in por_vano["K"]] == [{"x": 0.0, "y": 0.0, "z": 1750.0}] * 2
    assert datos["plan"]["bays"][0]["height_mm"] == 3500.0 and datos["haria"][0]["type"] == u"IPE: IPE200"
    assert DB.Transaction.creadas == []
    r = _post(api, "/create_bracing/", doc, cuerpo)
    assert r.status == 200, r.data
    assert r.data["ok"] is True and r.data["count"] == 9 and _transacciones() == [u"IA: Crear 9 arriostres"]
    assert [len(v["braces"]) for v in r.data["creados"]] == [1, 2, 2, 2, 2]
    assert r.data["creados"][2]["pattern"] == "V" and r.data["creados"][2]["braces"][0]["end_mm"] == {"x": 3000.0, "y": 0.0, "z": 3500.0}
    assert r.data["creados"][0]["braces"][0]["nivel"] == u"Nivel 1" and r.data["creados"][0]["braces"][0]["length_mm"] == round(math.hypot(6000, 3500), 1)
    arriostres = _instancias(doc)
    assert len(arriostres) == 9 and all(a.structural_type is ST.StructuralType.Brace for a in arriostres)
    assert all(a.LevelId == DB.ElementId(1) for a in arriostres)


def test_create_bracing_errores_controlados(api, doc):
    assert _post(api, "/create_bracing/", doc, {"brace_type": u"IPE200"}).status == 400
    r = _post(api, "/create_bracing/", doc, {"bays": [_vano("single")], "brace_type": u"L999"})
    assert r.status == 404 and u"IPE: IPE200" in r.data["available_types"]
    r = _post(api, "/create_bracing/", doc, {"bays": [_vano("single"), _vano("Z")], "brace_type": u"IPE200"})
    assert r.status == 400 and r.data["index"] == 1 and r.data["available_patterns"] == ["single", "X", "V", "inverted_V", "K"]
    r = _post(api, "/create_bracing/", doc, {"bays": [dict(_vano("X"), level_top=u"Sótano")], "brace_type": u"IPE200"})
    assert r.status == 404 and r.data["index"] == 0 and r.data["available_levels"]
    r = _post(api, "/create_bracing/", doc, {"bays": [dict(_vano("X"), level_top=u"Nivel 1")], "brace_type": u"IPE200"})
    assert r.status == 400 and "above" in r.data["error"]
    r = _post(api, "/create_bracing/", doc, {"bays": [dict(_vano("X"), end_point_mm={"x": 0, "y": 0})], "brace_type": u"IPE200"})
    assert r.status == 400 and "coincide" in r.data["error"]
    assert _post(api, "/create_bracing/", doc, {"bays": [{"pattern": "X"}], "brace_type": u"IPE200"}).status == 400
    assert DB.Transaction.creadas == []


# ---------------------------------------------------------------------------
# /create_truss/
# ---------------------------------------------------------------------------
def test_create_truss_sobre_sketchplane_del_nivel(api, doc):
    cuerpo = {"trusses": [{"truss_type": u"Cercha 12 m", "start_point_mm": {"x": 0, "y": 0}, "end_point_mm": {"x": 12000, "y": 0}, "level": u"Cubierta"},
                          {"truss_type": u"Cercha: Cercha 12 m", "start_point_mm": {"x": 0, "y": 5000}, "end_point_mm": {"x": 12000, "y": 5000}, "level": u"Cubierta"}]}
    assert _post(api, "/create_truss/", doc, cuerpo, con_token=False).status == 401
    r = _post(api, "/create_truss/", doc, dict(cuerpo, simular=True))
    assert r.status == 200, r.data
    assert r.data["count"] == 2 and r.data["haria"][0]["start_mm"] == {"x": 0.0, "y": 0.0, "z": 7000.0} and r.data["haria"][0]["length_mm"] == 12000.0
    assert DB.Transaction.creadas == []
    r = _post(api, "/create_truss/", doc, cuerpo)
    assert r.status == 200, r.data
    assert r.data["ok"] is True and r.data["count"] == 2 and _transacciones() == [u"IA: Crear 2 cerchas"]
    assert r.data["creados"][0]["categoria"] == u"Cerchas estructurales" and r.data["creados"][0]["truss_type"] == u"Cercha: Cercha 12 m"
    assert r.data["creados"][1]["index"] == 1 and r.data["creados"][1]["end_mm"] == {"x": 12000.0, "y": 5000.0, "z": 7000.0}
    planos = [e for e in doc.elementos.values() if isinstance(e, DB.SketchPlane)]
    assert len(planos) == 1 and planos[0].nivel_id == DB.ElementId(3)        # un SketchPlane por nivel
    cerchas = [e for e in doc.elementos.values() if isinstance(e, DB.Structure.Truss)]
    assert all(c.plano_id == planos[0].Id for c in cerchas) and cerchas[0].type_id == DB.ElementId(90)


def test_create_truss_errores_controlados(api, doc):
    assert _post(api, "/create_truss/", doc, {}).status == 400
    r = _post(api, "/create_truss/", doc, {"trusses": [{"truss_type": u"Cercha 30 m", "start_point_mm": {"x": 0, "y": 0}, "end_point_mm": {"x": 1, "y": 0}, "level": u"Cubierta"}]})
    assert r.status == 404 and r.data["available_types"] == [u"Cercha: Cercha 12 m"] and r.data["index"] == 0
    r = _post(api, "/create_truss/", doc, {"trusses": [{"truss_type": u"Cercha 12 m", "start_point_mm": {"x": 0, "y": 0}, "end_point_mm": {"x": 1, "y": 0}, "level": u"Ático"}]})
    assert r.status == 404 and "available_levels" in r.data
    r = _post(api, "/create_truss/", doc, {"trusses": [{"truss_type": u"Cercha 12 m", "start_point_mm": {"x": 0, "y": 0}, "end_point_mm": {"x": 0, "y": 0}, "level": u"Cubierta"}]})
    assert r.status == 400 and "zero-length" in r.data["error"]
    doc.elementos.pop(90)
    r = _post(api, "/create_truss/", doc, {"trusses": [{"truss_type": u"Cercha 12 m", "start_point_mm": {"x": 0, "y": 0}, "end_point_mm": {"x": 1, "y": 0}, "level": u"Cubierta"}]})
    assert r.status == 404 and r.data["available_types"] == []
    assert DB.Transaction.creadas == []


# ---------------------------------------------------------------------------
# /set_structural_properties/
# ---------------------------------------------------------------------------
def _p(doc, identificador, bip):
    return doc.elementos[identificador].get_Parameter(bip)


def test_set_structural_properties_por_parametro_en_dos_fases(api, doc):
    cuerpo = {"element_ids": [10, 11], "start_release": "pinned", "end_release": {"FX": True, "MZ": True},
              "y_justification": "left", "z_justification": 3, "y_offset_mm": 50, "section_rotation_deg": 90,
              "start_extension_mm": 100, "analyze_as": "gravity", "structural_usage": "joist"}
    assert _post(api, "/set_structural_properties/", doc, cuerpo, con_token=False).status == 401
    r = _post(api, "/set_structural_properties/", doc, dict(cuerpo, simular=True))
    assert r.status == 200, r.data
    datos = r.data
    assert datos["simulado"] is True and datos["elements"] == 2 and datos["fallidos"] == [] and datos["no_disponibles"] == []
    haria = datos["haria"]
    propiedades = sorted(set(h["property"] for h in haria))
    assert propiedades == ["analyze_as", "end_release", "section_rotation_deg", "start_extension_mm", "start_release",
                           "structural_usage", "y_justification", "y_offset_mm", "z_justification"]
    inicio = [h for h in haria if h["property"] == "start_release" and h["element_id"] == 10][0]
    assert inicio["antes"] == "0" and inicio["despues"] == "1" and inicio["builtin"] == "STRUCTURAL_START_RELEASE_TYPE"
    componentes = [h for h in haria if h["builtin"] in ("STRUCTURAL_END_RELEASE_FX", "STRUCTURAL_END_RELEASE_MZ")]
    assert len(componentes) == 4 and all(h["despues"] is True and "definido por el usuario" in h["nota"] for h in componentes)
    desfase = [h for h in haria if h["property"] == "y_offset_mm"][0]
    assert desfase["despues"] == u"50 mm" and desfase["parameter_label"] == u"Desfase Y"
    assert DB.Transaction.creadas == []

    r = _post(api, "/set_structural_properties/", doc, cuerpo)
    assert r.status == 200, r.data
    datos = r.data
    assert datos["ok"] is True and datos["verificacion"] == {"coincide": True} and datos["count"] == 2
    assert _transacciones() == [u"IA: Propiedades estructurales (2 elementos)"]
    assert datos["properties_set"] == 22 and datos["fallidos"] == []                     # 9 por elemento + 2 componentes x 2
    for identificador in (10, 11):
        assert _p(doc, identificador, BIP.STRUCTURAL_START_RELEASE_TYPE).AsInteger() == 1
        assert _p(doc, identificador, BIP.STRUCTURAL_END_RELEASE_TYPE).AsInteger() == 3
        assert _p(doc, identificador, BIP.STRUCTURAL_END_RELEASE_FX).AsInteger() == 1
        assert _p(doc, identificador, BIP.STRUCTURAL_END_RELEASE_MZ).AsInteger() == 1
        assert _p(doc, identificador, BIP.STRUCTURAL_END_RELEASE_FY).AsInteger() == 0
        assert _p(doc, identificador, BIP.Y_JUSTIFICATION).AsInteger() == 1
        assert _p(doc, identificador, BIP.Z_JUSTIFICATION).AsInteger() == 3
        assert abs(_p(doc, identificador, BIP.Y_OFFSET_VALUE).AsDouble() - 50 * MM) < 1e-9
        assert abs(_p(doc, identificador, BIP.STRUCTURAL_BEND_DIR_ANGLE).AsDouble() - math.pi / 2) < 1e-9
        assert abs(_p(doc, identificador, BIP.START_EXTENSION).AsDouble() - 100 * MM) < 1e-9
        assert _p(doc, identificador, BIP.STRUCTURAL_ANALYZES_AS).AsInteger() == 1
        assert _p(doc, identificador, BIP.INSTANCE_STRUCT_USAGE_PARAM).AsInteger() == 3
    assert datos["despues"]["10"]["start_release"] == "1" and datos["despues"]["10"]["end_release.FX"] == "1"
    assert datos["antes"]["10"]["y_offset_mm"] == u"0 mm" and datos["despues"]["10"]["y_offset_mm"] == u"50 mm"
    assert datos["despues"]["11"]["y_justification"] == "1"
    assert all(c["coincide"] for c in datos["changes"]) and datos["copia"]["ruta"].endswith(".rvt")


def test_set_structural_properties_liberaciones_en_el_miembro_analitico(api, doc):
    """2023+: el pilar 12 no tiene los parametros de liberacion; se fijan en el AnalyticalMember. El 13 los tiene."""
    miembro = mf.MiembroAnalitico(doc, 500, (0, 0, 0), (0, 0, 3500))
    doc.asociar(doc.elementos[12], miembro)
    cuerpo = {"element_ids": [12, 13], "start_release": "pinned", "end_release": {"MX": True}}
    r = _post(api, "/set_structural_properties/", doc, dict(cuerpo, simular=True))
    assert r.status == 200, r.data
    analiticos = [h for h in r.data["haria"] if h.get("source") == "AnalyticalMember"]
    assert [(h["element_id"], h["property"], h["despues"]) for h in analiticos] == [
        (12, "start_release", "pinned"), (12, "end_release", {"MX": True, "type": "user_defined"})]
    assert analiticos[0]["analytical_member_id"] == 500 and analiticos[0]["antes"]["type"] == "fixed"
    assert DB.Transaction.creadas == []
    r = _post(api, "/set_structural_properties/", doc, cuerpo)
    assert r.status == 200, r.data
    datos = r.data
    assert datos["ok"] is True and datos["fallidos"] == [] and sorted(datos["elements"]) == [12, 13]
    assert miembro.GetReleaseType(ST.AnalyticalElementSelector.StartOrBase) is ST.ReleaseType.Pinned
    assert miembro.GetReleaseType(ST.AnalyticalElementSelector.EndOrTop) is ST.ReleaseType.UserDefined
    condiciones = miembro.GetReleaseConditions(ST.AnalyticalElementSelector.EndOrTop)
    assert condiciones.Mx is True and condiciones.Fx is False and condiciones.Start is False
    assert datos["despues"]["12"]["start_release"]["type"] == "pinned" and datos["despues"]["12"]["end_release"]["MX"] is True
    assert datos["antes"]["12"]["start_release"]["type"] == "fixed"
    cambios_12 = [c for c in datos["changes"] if c["element_id"] == 12]
    assert all(c["source"] == "AnalyticalMember" and c["coincide"] for c in cambios_12)
    assert _p(doc, 13, BIP.STRUCTURAL_START_RELEASE_TYPE).AsInteger() == 1 and _p(doc, 13, BIP.STRUCTURAL_END_RELEASE_MX).AsInteger() == 1
    # sin parametros ni miembro analitico: fallido con motivo, el resto sigue
    doc.asociaciones.clear()
    r = _post(api, "/set_structural_properties/", doc, {"element_ids": [12, 13], "start_release": "fixed", "y_justification": "center"})
    assert r.status == 200, r.data
    assert r.data["fallidos"] == [{"element_id": 12, "property": "start_release", "builtin": "STRUCTURAL_START_RELEASE_TYPE",
                                   "motivo": "parameter not found and no analytical member associated"}]
    assert r.data["despues"]["12"] == {"y_justification": "2"} and r.data["despues"]["13"]["start_release"] == "0"
    # 0.5.1: solo liberaciones y ningun elemento las admite (sin modelo analitico): 409 no_soportado con la explicacion
    DB.Transaction.creadas = []
    r = _post(api, "/set_structural_properties/", doc, {"element_ids": [12], "start_release": "pinned"})
    assert r.status == 409, r.data
    assert r.data["no_soportado"] is True and r.data["motivo"] == "sin_modelo_analitico"
    assert u"modelo analítico" in r.data["error"] and r.data["fallidos"][0]["element_id"] == 12
    assert DB.Transaction.creadas == []


def test_set_structural_properties_no_disponibles_y_errores(api, doc, monkeypatch):
    assert _post(api, "/set_structural_properties/", doc, {"element_ids": [10]}).status == 400
    r = _post(api, "/set_structural_properties/", doc, {"element_ids": [10]})
    assert "start_release" in r.data["available_properties"]
    assert _post(api, "/set_structural_properties/", doc, {"y_offset_mm": 1}).status == 400
    assert _post(api, "/set_structural_properties/", doc, {"element_ids": ["x"], "y_offset_mm": 1}).status == 400
    r = _post(api, "/set_structural_properties/", doc, {"element_ids": [10], "y_justification": "diagonal"})
    assert r.status == 400 and "YJustification" in r.data["error"] and "Left" in r.data["error"]
    assert _post(api, "/set_structural_properties/", doc, {"element_ids": [10], "start_release": "loose"}).status == 400
    assert _post(api, "/set_structural_properties/", doc, {"element_ids": [10], "start_release": {"QX": True}}).status == 400
    assert _post(api, "/set_structural_properties/", doc, {"element_ids": [10], "y_offset_mm": "mucho"}).status == 400
    r = _post(api, "/set_structural_properties/", doc, {"element_ids": [999], "y_offset_mm": 10})
    assert r.status == 400 and r.data["fallidos"][0]["motivo"] == "element not found"
    r = _post(api, "/set_structural_properties/", doc, {"element_ids": list(range(1000, 1201)), "y_offset_mm": 1})
    assert r.status == 400 and r.data["limite"] == 200 and r.data["cantidad"] == 201
    # una propiedad cuyo BuiltInParameter no existe en la version: no_disponibles y el resto se fija
    monkeypatch.delattr(DB.BuiltInParameter, "Y_JUSTIFICATION")
    r = _post(api, "/set_structural_properties/", doc, {"element_ids": [10], "y_justification": "right", "z_offset_mm": 20})
    assert r.status == 200, r.data
    assert r.data["no_disponibles"] == [{"property": "y_justification", "builtin": "Y_JUSTIFICATION"}]
    assert r.data["despues"]["10"] == {"z_offset_mm": u"20 mm"} and r.data["ok"] is True
    assert len(DB.Transaction.creadas) == 1


# ---------------------------------------------------------------------------
# /create_steel_connection/
# ---------------------------------------------------------------------------
def test_create_steel_connection_409_sin_modulo(api, doc):
    r = _post(api, "/create_steel_connection/", doc, {"connections": [{"element_ids": [10, 12], "connection_type": u"x"}]})
    assert r.status == 409, r.data
    assert r.data["no_soportado"] is True and "Steel Connections" in r.data["error"] and DB.Transaction.creadas == []


def test_create_steel_connection_con_modulo(api, doc, monkeypatch):
    tipo_cls, aprobacion_cls, _ = mf.instalar_conexiones(monkeypatch)

    class TipoConexion(mf.Elemento, tipo_cls):
        pass

    class Aprobacion(mf.Elemento, aprobacion_cls):
        pass

    TipoConexion(doc, 600, nombre=u"Conexión genérica", categoria=u"Conexiones estructurales", bic=BIC.OST_StructConnections)
    Aprobacion(doc, 601, nombre=u"No aprobada", categoria=None)
    Aprobacion(doc, 602, nombre=u"Aprobada", categoria=None)
    cuerpo = {"connections": [{"element_ids": [10, 12], "connection_type": u"Conexión genérica"},
                              {"element_ids": [11], "connection_type": 600}]}
    r = _post(api, "/create_steel_connection/", doc, dict(cuerpo, simular=True))
    assert r.status == 200, r.data
    assert r.data["count"] == 2 and r.data["haria"][0] == {"accion": "conectar", "element_type": "steel_connection", "index": 0,
                                                            "element_ids": [10, 12], "connection_type": u"Conexión genérica",
                                                            "approval_status": None}
    assert DB.Transaction.creadas == []
    r = _post(api, "/create_steel_connection/", doc, cuerpo)
    assert r.status == 200, r.data
    assert r.data["ok"] is True and r.data["count"] == 2 and _transacciones() == [u"IA: Crear 2 conexiones"]
    assert r.data["creados"][0]["categoria"] == u"Conexiones estructurales" and r.data["creados"][0]["element_ids"] == [10, 12]
    assert r.data["creados"][1]["connection_type"] == u"Conexión genérica" and "approval_status" not in r.data["creados"][1]
    conexion = doc.GetElement(DB.ElementId(r.data["creados"][0]["id"]))
    assert [i.Value for i in conexion.GetConnectedElementIds()] == [10, 12] and conexion.type_id == DB.ElementId(600)
    # aprobacion: hace falta approval_status con el nombre que muestra Revit (o su id)
    r = _post(api, "/create_steel_connection/", doc, dict(cuerpo, approve=True))
    assert r.status == 400 and r.data["available_approval_types"] == [{"id": 601, "nombre": u"No aprobada"}, {"id": 602, "nombre": u"Aprobada"}]
    r = _post(api, "/create_steel_connection/", doc, {"connections": cuerpo["connections"][:1], "approve": True, "approval_status": u"Aprobada"})
    assert r.status == 200, r.data
    assert r.data["creados"][0]["approval_status"] == u"Aprobada"
    assert doc.GetElement(DB.ElementId(r.data["creados"][0]["id"])).ApprovalStatus == DB.ElementId(602)
    r = _post(api, "/create_steel_connection/", doc, {"connections": cuerpo["connections"][:1], "approve": True, "approval_status": u"Revisada"})
    assert r.status == 404
    # errores controlados
    r = _post(api, "/create_steel_connection/", doc, {"connections": [{"element_ids": [10], "connection_type": u"Soldada"}]})
    assert r.status == 404 and r.data["available_types"] == [{"id": 600, "tipo": u"Conexión genérica"}] and r.data["index"] == 0
    r = _post(api, "/create_steel_connection/", doc, {"connections": [{"element_ids": [10, 999], "connection_type": 600}]})
    assert r.status == 404 and "999" in r.data["error"]
    assert _post(api, "/create_steel_connection/", doc, {"connections": [{"connection_type": 600}]}).status == 400
    assert _post(api, "/create_steel_connection/", doc, {}).status == 400


# ---------------------------------------------------------------------------
# /add_plate/
# ---------------------------------------------------------------------------
def _caras_viga(doc, viga, z_superior_mm, z_inferior_mm, y_alma_mm):
    referencia = DB.Reference(viga)
    caras = [
        DB.PlanarFace(DB.XYZ(0, 0, 1), _xyz((0, 0, z_superior_mm)), area=9.0, referencia=referencia),
        DB.PlanarFace(DB.XYZ(0, 0, -1), _xyz((0, 0, z_inferior_mm)), area=9.0, referencia=referencia),
        DB.PlanarFace(DB.XYZ(0, 1, 0), _xyz((0, y_alma_mm, z_superior_mm)), area=6.0, referencia=referencia),
        DB.PlanarFace(DB.XYZ(1, 0, 0), _xyz((6000, 0, 0)), area=0.5, referencia=referencia),   # extremo
        DB.PlanarFace(DB.XYZ(0, 1, 0), _xyz((0, 200, 0)), area=1.0, referencia=None),           # sin referencia
    ]
    solido = DB.Solid(volumen=1.0, area=30.0)
    solido.Faces = DB._Coleccion(caras)
    viga.solidos = [solido]


def _familias_placa(doc):
    fam_cara = mf.Familia(doc, 95, nombre=u"Rigidizador", categoria=u"Modelos genéricos", bic=BIC.OST_GenericModel)
    fam_cara.FamilyPlacementType = DB.FamilyPlacementType.WorkPlaneBased
    mf.TipoFamilia(doc, 96, familia=fam_cara, nombre=u"PL10", categoria=u"Modelos genéricos", bic=BIC.OST_GenericModel)
    fam_punto = mf.Familia(doc, 97, nombre=u"Placa base", categoria=u"Modelos genéricos", bic=BIC.OST_GenericModel)
    fam_punto.FamilyPlacementType = DB.FamilyPlacementType.OneLevelBased
    mf.TipoFamilia(doc, 98, familia=fam_punto, nombre=u"PB 400x400", categoria=u"Modelos genéricos", bic=BIC.OST_GenericModel)


def test_add_plate_en_cara_y_de_punto(api, doc):
    _familias_placa(doc)
    _caras_viga(doc, doc.elementos[10], 3650, 3350, 75)
    cuerpo = {"host_id": 10, "family_name": u"Rigidizador", "type_name": u"PL10", "positions": [0.5, 1500], "face": "top"}
    assert _post(api, "/add_plate/", doc, cuerpo, con_token=False).status == 401
    r = _post(api, "/add_plate/", doc, dict(cuerpo, simular=True))
    assert r.status == 200, r.data
    assert r.data["count"] == 2 and r.data["haria"][0]["point_mm"] == {"x": 3000.0, "y": 0.0, "z": 3650.0}
    assert r.data["haria"][1]["position_mm"] == 1500.0 and r.data["haria"][1]["face"] == "top" and r.data["haria"][1]["hosted_on_face"] is True
    assert r.data["plan"]["host_length_mm"] == 6000.0 and DB.Transaction.creadas == []
    r = _post(api, "/add_plate/", doc, cuerpo)
    assert r.status == 200, r.data
    assert r.data["ok"] is True and r.data["count"] == 2 and r.data["face"] == "top" and r.data["host"]["id"] == 10
    assert _transacciones() == [u"IA: Colocar 2 Rigidizador en 10"]
    placas = _instancias(doc)
    assert len(placas) == 2 and all(p.Host is doc.elementos[10] for p in placas)
    assert all(p.referencia_cara.ElementId == DB.ElementId(10) for p in placas)
    assert sorted(round(p.Location.Point.X * 304.8) for p in placas) == [1500, 3000]
    assert all(abs(p.Location.Point.Z * 304.8 - 3650) < 0.01 for p in placas)
    assert all(abs(p.direccion.X - 1.0) < 1e-9 for p in placas)                 # refDir = eje de la viga en el plano de la cara
    # alma e inferior
    r = _post(api, "/add_plate/", doc, dict(cuerpo, positions=[0.25], face="web"))
    assert r.status == 200 and r.data["creados"][0]["point_mm"] == {"x": 1500.0, "y": 75.0, "z": 3500.0}   # proyectado al alma
    r = _post(api, "/add_plate/", doc, dict(cuerpo, positions=[6000], face="bottom"))
    assert r.status == 200 and r.data["creados"][0]["point_mm"] == {"x": 6000.0, "y": 0.0, "z": 3350.0}
    # familia de punto: sobre el eje, en el nivel del anfitrion
    r = _post(api, "/add_plate/", doc, {"host_id": 10, "family_name": u"Placa base", "type_name": u"PB 400x400", "positions": [0]})
    assert r.status == 200, r.data
    assert r.data["hosted_on_face"] is False and r.data["face"] is None and r.data["creados"][0]["point_mm"] == {"x": 0.0, "y": 0.0, "z": 3500.0}
    placa = doc.GetElement(DB.ElementId(r.data["creados"][0]["id"]))
    assert placa.LevelId == DB.ElementId(2) and placa.Host is None and placa.structural_type is ST.StructuralType.NonStructural


def test_add_plate_errores_controlados(api, doc):
    _familias_placa(doc)
    base = {"host_id": 10, "family_name": u"Rigidizador", "type_name": u"PL10"}
    r = _post(api, "/add_plate/", doc, dict(base, type_name=u"PL20"))
    assert r.status == 404 and r.data["available_types"] == [u"PL10"]
    r = _post(api, "/add_plate/", doc, dict(base, family_name=u"Cartela"))
    assert r.status == 404 and "not loaded" in r.data["error"]
    assert _post(api, "/add_plate/", doc, dict(base, face="side")).status == 400
    assert _post(api, "/add_plate/", doc, dict(base, host_id=1)).status == 400        # un nivel no es FamilyInstance
    assert _post(api, "/add_plate/", doc, dict(base, host_id=999)).status == 404
    assert _post(api, "/add_plate/", doc, {"host_id": 10, "family_name": u"Rigidizador"}).status == 400
    r = _post(api, "/add_plate/", doc, base)                                          # la viga 10 no tiene caras con referencia
    assert r.status == 400 and "face with a reference" in r.data["error"]
    _caras_viga(doc, doc.elementos[10], 3650, 3350, 75)
    r = _post(api, "/add_plate/", doc, dict(base, positions=[7000]))
    assert r.status == 400 and "beyond" in r.data["error"]
    assert _post(api, "/add_plate/", doc, dict(base, positions=[-1])).status == 400
    assert _post(api, "/add_plate/", doc, dict(base, positions="medio")).status == 400
    assert DB.Transaction.creadas == []


# ---------------------------------------------------------------------------
# /split_beam/
# ---------------------------------------------------------------------------
def test_split_beam_copia_tramos_y_recorta_el_original(api, doc):
    cuerpo = {"element_id": 10, "at_mm": [4000, 2000]}
    assert _post(api, "/split_beam/", doc, cuerpo, con_token=False).status == 401
    r = _post(api, "/split_beam/", doc, dict(cuerpo, simular=True))
    assert r.status == 200, r.data
    haria = r.data["haria"][0]
    assert haria["at_mm"] == [2000.0, 4000.0] and haria["length_mm"] == 6000.0
    assert [s["length_mm"] for s in haria["segments"]] == [2000.0, 2000.0, 2000.0]
    assert r.data["plan"]["counts"] == {"segments": 3, "new_elements": 2} and len(r.data["avisos"]) == 2
    assert DB.Transaction.creadas == [] and len(doc.elementos) == len(doc.elementos)
    antes = len(doc.elementos)
    r = _post(api, "/split_beam/", doc, cuerpo)
    assert r.status == 200, r.data
    datos = r.data
    assert datos["ok"] is True and datos["count"] == 2 and len(doc.elementos) == antes + 2
    assert _transacciones() == [u"IA: Dividir viga 10"]
    assert datos["original"]["antes"]["length_mm"] == 6000.0 and datos["original"]["despues"]["length_mm"] == 2000.0
    assert datos["original"]["despues"]["end_mm"] == {"x": 2000.0, "y": 0.0, "z": 3500.0}
    assert [c["start_mm"]["x"] for c in datos["creados"]] == [2000.0, 4000.0] and [c["segment"] for c in datos["creados"]] == [1, 2]
    assert datos["creados"][0]["tipo"] == u"IPE300" and datos["creados"][0]["nivel"] == u"Nivel 2"
    assert any(u"uniones" in aviso for aviso in datos["avisos"])
    original = doc.elementos[10]
    assert abs(original.Location.Curve.Length * 304.8 - 2000) < 1e-6
    copias = [doc.GetElement(DB.ElementId(c["id"])) for c in datos["creados"]]
    assert [round(c.Location.Curve.GetEndPoint(1).X * 304.8) for c in copias] == [4000, 6000]
    assert copias[0].LookupParameter(u"Marca").AsString() == u"V-1"        # copia de los parametros del original


def test_split_beam_errores_controlados(api, doc):
    assert _post(api, "/split_beam/", doc, {"element_id": 10}).status == 400
    r = _post(api, "/split_beam/", doc, {"element_id": 10, "at_mm": [6000]})
    assert r.status == 400 and r.data["length_mm"] == 6000.0
    assert _post(api, "/split_beam/", doc, {"element_id": 10, "at_mm": [0]}).status == 400
    assert _post(api, "/split_beam/", doc, {"element_id": 10, "at_mm": [2000, 2000.5]}).status == 400
    assert _post(api, "/split_beam/", doc, {"element_id": 10, "at_mm": ["mitad"]}).status == 400
    assert _post(api, "/split_beam/", doc, {"element_id": 1, "at_mm": [1]}).status == 400          # un nivel no tiene curva
    assert _post(api, "/split_beam/", doc, {"element_id": 999, "at_mm": [1]}).status == 404
    assert DB.Transaction.creadas == []


class _UbicacionUnida(object):
    """LocationCurve de una viga unida por su extremo final a un pilar: al mover la curva, Revit devuelve el extremo
    al pilar salvo que la union se haya desactivado (lo que paso en la validacion 2b en Revit 2027)."""

    def __init__(self, curva):
        self._curva = curva
        self.union_final = True

    @property
    def Curve(self):
        return self._curva

    @Curve.setter
    def Curve(self, curva):
        if self.union_final:
            curva = DB.Line.CreateBound(curva.GetEndPoint(0), self._curva.GetEndPoint(1))
        self._curva = curva


def _union_en_el_extremo(doc):
    viga = doc.elementos[10]
    viga.Location = _UbicacionUnida(viga.Location.Curve)
    return viga


def test_split_beam_sin_split_suelta_la_union_del_extremo(api, doc, monkeypatch):
    viga = _union_en_el_extremo(doc)

    class StructuralFramingUtils(object):
        llamadas = []

        @staticmethod
        def DisallowJoinAtEnd(elemento, extremo):
            StructuralFramingUtils.llamadas.append((get_id(elemento), extremo))
            if extremo == 1:
                elemento.Location.union_final = False

    def get_id(elemento):
        return elemento.Id.Value

    monkeypatch.setattr(DB.Structure, "StructuralFramingUtils", StructuralFramingUtils, raising=False)
    r = _post(api, "/split_beam/", doc, {"element_id": 10, "at_mm": [2000]})
    assert r.status == 200, r.data
    datos = r.data
    assert datos["ok"] is True and datos["metodo"] == "CopyElement" and datos["verificacion"] == {"coincide": True}
    assert StructuralFramingUtils.llamadas == [(10, 1)]
    assert abs(viga.Location.Curve.Length * 304.8 - 2000) < 1e-6
    assert any(u"DisallowJoinAtEnd" in aviso for aviso in datos["avisos"])


def test_split_beam_detecta_la_viga_devuelta_a_su_longitud(api, doc, monkeypatch):
    """Sin StructuralFramingUtils, el extremo vuelve al pilar: la verificacion lo dice (ok=false), como en Revit."""
    monkeypatch.delattr(DB.Structure, "StructuralFramingUtils", raising=False)
    _union_en_el_extremo(doc)
    r = _post(api, "/split_beam/", doc, {"element_id": 10, "at_mm": [2000]})
    assert r.status == 200, r.data
    assert r.data["ok"] is False and r.data["verificacion"]["coincide"] is False
    assert u"element 10 is 6000.0 mm long instead of 2000.0 mm" in r.data["verificacion"]["detalle"]
    assert any(u"No se pudo desactivar" in aviso for aviso in r.data["avisos"])


def test_split_beam_con_split_nativo_sigue_el_tramo_del_inicio(api, doc, monkeypatch):
    """FamilyInstance.Split: la pieza nueva puede ser la del inicio; el plan se sigue por geometria."""
    viga = _union_en_el_extremo(doc)
    llamadas = []

    def split(parametro):
        llamadas.append(round(parametro, 6))
        curva = viga_actual[0].Location.Curve
        a, b = curva.GetEndPoint(0), curva.GetEndPoint(1)
        corte = a.Add(b.Subtract(a).Multiply(parametro))
        nuevo_id = DB.ElementTransformUtils.CopyElement(doc, viga_actual[0].Id, DB.XYZ(0, 0, 0))[0]
        nuevo = doc.GetElement(nuevo_id)
        nuevo.Location = mf.Ubicacion(curva=DB.Line.CreateBound(a, corte))      # la nueva es la del inicio
        ubicacion = viga_actual[0].Location                                       # Revit conserva la union del final
        if isinstance(ubicacion, _UbicacionUnida):
            ubicacion._curva = DB.Line.CreateBound(corte, b)
        else:
            viga_actual[0].Location = mf.Ubicacion(curva=DB.Line.CreateBound(corte, b))
        nuevo.Split = lambda p, n=nuevo: split_de(n, p)
        return nuevo_id

    viga_actual = [viga]

    def split_de(elemento, parametro):
        viga_actual[0] = elemento
        return split(parametro)

    viga.Split = split
    r = _post(api, "/split_beam/", doc, {"element_id": 10, "at_mm": [4000, 2000]})
    assert r.status == 200, r.data
    datos = r.data
    assert datos["ok"] is True and datos["metodo"] == "FamilyInstance.Split", datos
    assert llamadas == [round(4000 / 6000.0, 6), 0.5]
    assert datos["original"]["segment"] == 2 and datos["original"]["despues"]["length_mm"] == 2000.0
    assert sorted(c["segment"] for c in datos["creados"]) == [0, 1] and len(datos["creados_ids"]) == 2
    assert all(abs(c["length_mm"] - 2000.0) < 1e-6 for c in datos["creados"])


def test_join_geometry_pareja_rechazada_no_aborta_el_lote(api, doc):
    """Revit rechaza unir dos perfiles de acero: esa pareja va a fallidos y el resto se une (antes: HTTP 500)."""
    doc.elementos[12].no_unible = True
    r = _post(api, "/join_geometry/", doc, {"element_ids": [10, 11, 12]})
    assert r.status == 200, r.data
    datos = r.data
    assert datos["ok"] is False and datos["joined_pairs"] == 1
    assert datos["fallidos"] == [{"element_id_a": 11, "element_id_b": 12,
                                  "error": "The elements cannot be joined.\nParameter name: secondElement"}]
    assert datos["pairs"][1]["failed"] is True and datos["pairs"][0]["despues"] == {"joined": True}
    assert "coping=true" in datos["nota"]
    assert 11 in doc.elementos[10].unidos


# ---------------------------------------------------------------------------
# /join_geometry/ en cadena
# ---------------------------------------------------------------------------
def test_join_geometry_en_cadena_con_coping(api, doc):
    """0.5.1: coping solo recorta (AddCoping sobre la viga, contra el pilar), sin JoinGeometry."""
    doc.elementos[10].copings = [DB.ElementId(11)]           # 10 ya recortada contra 11
    cuerpo = {"element_ids": [12, 10, 11], "coping": True}   # pilar, viga, viga
    r = _post(api, "/join_geometry/", doc, dict(cuerpo, simular=True))
    assert r.status == 200, r.data
    assert r.data["count"] == 1 and r.data["pairs"] == 2 and r.data["haria"][1]["skip"] is True
    assert r.data["haria"][0]["coped_element"] == 10 and r.data["haria"][0]["against"] == 12   # la viga, no el pilar
    assert DB.Transaction.creadas == []
    r = _post(api, "/join_geometry/", doc, cuerpo)
    assert r.status == 200, r.data
    datos = r.data
    assert datos["ok"] is True and datos["coped_pairs"] == 1 and _transacciones() == [u"IA: Recortar acero en cadena (3 elementos)"]
    assert datos["pairs"][0] == {"element_id_a": 12, "element_id_b": 10, "antes": {"coped": False}, "despues": {"coped": True},
                                 "skipped": False, "failed": False}
    assert datos["skipped_pairs"] == [{"element_id_a": 10, "element_id_b": 11, "reason": "already coped"}]
    assert datos["coping"]["applied"] == [{"element_id": 10, "against": 12}]
    assert doc.elementos[10].copings == [DB.ElementId(11), DB.ElementId(12)]
    assert 12 not in doc.elementos[10].unidos and not getattr(doc.elementos[12], "copings", None)   # sin JoinGeometry
    DB.Transaction.creadas = []
    # union normal en cadena (11-13 ya unidas)
    doc.elementos[11].unidos.append(13)
    doc.elementos[13].unidos.append(11)
    r = _post(api, "/join_geometry/", doc, {"element_ids": [10, 11, 13]})
    assert r.status == 200 and r.data["ok"] is True and r.data["joined_pairs"] == 1, r.data
    assert r.data["pairs"][0]["despues"] == {"joined": True}
    assert r.data["skipped_pairs"] == [{"element_id_a": 11, "element_id_b": 13, "reason": "already joined"}]
    # separar en cadena
    r = _post(api, "/join_geometry/", doc, {"element_ids": [10, 11, 13], "unjoin": True})
    assert r.status == 200 and r.data["unjoined_pairs"] == 2 and 11 not in doc.elementos[10].unidos
    # errores y la forma de dos argumentos sigue funcionando
    assert _post(api, "/join_geometry/", doc, {"element_ids": [10]}).status == 400
    assert _post(api, "/join_geometry/", doc, {"element_ids": [10, 11], "coping": True, "unjoin": True}).status == 400
    assert _post(api, "/join_geometry/", doc, {"element_ids": [10, 999]}).status == 404
    assert _post(api, "/join_geometry/", doc, {"element_ids": [10, 1], "coping": True}).status == 400   # un nivel no es FamilyInstance
    r = _post(api, "/join_geometry/", doc, {"element_id_a": 10, "element_id_b": 13})
    assert r.status == 200 and r.data["despues"] == {"joined": True} and r.data["accion"] == "join"
    assert _post(api, "/join_geometry/", doc, {}).status == 400
