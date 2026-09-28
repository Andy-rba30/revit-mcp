# -*- coding: utf-8 -*-
"""Bloque 2 de la consolidacion (0.4.0): lotes en una sola transaccion.

  POST /set_parameters/   lote de parametros (ejemplar y tipo), solo lectura informado,
                          simular sin transaccion, 401, limite 200 (elementos x parametros)
  POST /create_elements/  mezcla de kind, validacion previa con indice, reversion total, plan
  POST /transform_elements/ operation=array (0.4.0)

Sobre el modelo simulado de tests/fakes/modelo_falso.py (Revit "en espanol") con
las fabricas doc.Create y DB.*.Create anadidas en 0.4.0.
"""
import pytest
from pyrevit import DB, routes

import modelo_falso as mf
import seguridad

TOKEN = "f" * 64
BIP = DB.BuiltInParameter
BIC = DB.BuiltInCategory


class TipoSuelo(mf.Elemento, DB.FloorType):
    IsFoundationSlab = False


class TipoCubierta(mf.Elemento, DB.RoofType):
    pass


class TipoTecho(mf.Elemento, DB.CeilingType):
    pass


class TipoToposolido(mf.Elemento, DB.ToposolidType):
    pass


class TipoZapataCorrida(mf.Elemento, DB.WallFoundationType):
    pass


class TipoConducto(mf.Elemento, DB.Mechanical.DuctType):
    pass


class TipoSistemaMecanico(mf.Elemento, DB.Mechanical.MechanicalSystemType):
    pass


class TipoTuberia(mf.Elemento, DB.Plumbing.PipeType):
    pass


class TipoSistemaTuberias(mf.Elemento, DB.Plumbing.PipingSystemType):
    pass


def _muro(doc, identificador, marca, nivel_id, caja, x0, x1):
    muro = mf.Muro(
        doc, identificador, nombre=u"Genérico - 200 mm", categoria=u"Muros", bic=BIC.OST_Walls,
        tipo_id=50, nivel_id=nivel_id, caja=caja,
        parametros=[
            mf.texto(u"Marca", marca, bip=BIP.ALL_MODEL_MARK),
            mf.texto(u"Comentarios", u"", bip=BIP.ALL_MODEL_INSTANCE_COMMENTS),
            mf.longitud_mm(u"Longitud", x1 - x0, bip=BIP.CURVE_ELEM_LENGTH, solo_lectura=True),
            mf.longitud_mm(u"Altura desconectada", 3000, bip=BIP.WALL_USER_HEIGHT_PARAM),
        ],
    )
    muro.Location = mf.Ubicacion(curva=DB.Line.CreateBound(
        DB.XYZ(x0 * mf.MM_TO_FEET, 0, 0), DB.XYZ(x1 * mf.MM_TO_FEET, 0, 0)))
    return muro


@pytest.fixture
def doc(tmp_path, monkeypatch):
    mf.activar_spec(monkeypatch)
    rvt = tmp_path / "Modelo.rvt"
    rvt.write_bytes(b"RVT")
    doc = mf.Doc(str(rvt))
    n1 = mf.Nivel(doc, 1, u"Nivel 1", 0)
    mf.Nivel(doc, 2, u"Nivel 2", 3000)
    tipo_muro = mf.TipoMuro(doc, 50, nombre=u"Genérico - 200 mm", categoria=u"Muros", bic=BIC.OST_Walls,
                            parametros=[mf.texto(u"Comentarios de tipo", u"", bip=BIP.ALL_MODEL_TYPE_COMMENTS),
                                        mf.longitud_mm(u"Anchura", 200)])
    tipo_muro.FamilyName = u"Muro básico"
    _muro(doc, 10, u"M-1", 1, mf.caja_mm(0, 0, 0, 5000, 200, 3000), 0, 5000)
    _muro(doc, 11, u"M-2", 1, mf.caja_mm(6000, 0, 0, 9000, 200, 3000), 6000, 9000)
    mf.Rejilla(doc, 60, u"A")
    fam_viga = mf.Familia(doc, 70, nombre=u"IPE", categoria=u"Armazón estructural", bic=BIC.OST_StructuralFraming)
    mf.TipoFamilia(doc, 71, familia=fam_viga, nombre=u"IPE300", categoria=u"Armazón estructural", bic=BIC.OST_StructuralFraming)
    fam_pilar = mf.Familia(doc, 72, nombre=u"HEB", categoria=u"Pilares estructurales", bic=BIC.OST_StructuralColumns)
    mf.TipoFamilia(doc, 73, familia=fam_pilar, nombre=u"HEB300", categoria=u"Pilares estructurales", bic=BIC.OST_StructuralColumns)
    fam_zapata = mf.Familia(doc, 74, nombre=u"Zapata", categoria=u"Cimentación estructural", bic=BIC.OST_StructuralFoundation)
    mf.TipoFamilia(doc, 75, familia=fam_zapata, nombre=u"1500x1500", categoria=u"Cimentación estructural", bic=BIC.OST_StructuralFoundation)
    fam_puerta = mf.Familia(doc, 76, nombre=u"Puerta simple", categoria=u"Puertas", bic=BIC.OST_Doors)
    mf.TipoFamilia(doc, 77, familia=fam_puerta, nombre=u"0915 x 2134", categoria=u"Puertas", bic=BIC.OST_Doors)
    fam_generica = mf.Familia(doc, 78, nombre=u"Caja", categoria=u"Modelos genéricos", bic=BIC.OST_GenericModel)
    mf.TipoFamilia(doc, 79, familia=fam_generica, nombre=u"Caja 1", categoria=u"Modelos genéricos", bic=BIC.OST_GenericModel)
    TipoSuelo(doc, 80, nombre=u"Suelo 300", categoria=u"Suelos", bic=BIC.OST_Floors)
    losa = TipoSuelo(doc, 81, nombre=u"Losa 400", categoria=u"Suelos", bic=BIC.OST_Floors)
    losa.IsFoundationSlab = True
    TipoCubierta(doc, 82, nombre=u"Cubierta 250", categoria=u"Cubiertas", bic=BIC.OST_Roofs)
    TipoTecho(doc, 83, nombre=u"Techo 600x600", categoria=u"Techos", bic=BIC.OST_Ceilings)
    TipoToposolido(doc, 84, nombre=u"Terreno", categoria=u"Sólido topográfico", bic=BIC.OST_Toposolid)
    TipoZapataCorrida(doc, 85, nombre=u"Corrida 600", categoria=u"Cimentación estructural", bic=BIC.OST_StructuralFoundation)
    TipoConducto(doc, 86, nombre=u"Rectangular", categoria=u"Conductos", bic=BIC.OST_DuctCurves)
    TipoSistemaMecanico(doc, 87, nombre=u"Impulsión", categoria=u"Sistemas", bic=None)
    TipoTuberia(doc, 88, nombre=u"Cobre", categoria=u"Tuberías", bic=BIC.OST_PipeCurves)
    TipoSistemaTuberias(doc, 89, nombre=u"ACS", categoria=u"Sistemas", bic=None)
    fase = mf.Fase(doc, 400, nombre=u"Nueva construcción", categoria=u"Fases", bic=None)
    doc.Phases.append(fase)
    vista = mf.VistaPlanta(doc, 100, u"Planta Nivel 1", nivel=n1)
    doc.ActiveView = vista
    return doc


@pytest.fixture
def api():
    seguridad.establecer_token(TOKEN)
    DB.Transaction.creadas = []
    DB.Transaction.resultado_commit = DB.TransactionStatus.Committed
    api = routes.API("revit_mcp")
    from lotes import register_lotes_routes
    from transforms import register_transform_routes
    from building import register_building_routes

    register_lotes_routes(api)
    register_transform_routes(api)
    register_building_routes(api)
    return api


def _post(api, ruta, doc, cuerpo, con_token=True):
    datos = dict(cuerpo)
    if con_token:
        datos["token"] = TOKEN
    return api.rutas[(ruta, "POST")](doc=doc, request=routes.Request(path=ruta, data=datos))


def _transacciones():
    return [t.nombre for t in DB.Transaction.creadas]


def _elementos_de(doc, clase):
    return [e for e in doc.elementos.values() if isinstance(e, clase)]


# ---------------------------------------------------------------------------
# /set_parameters/
# ---------------------------------------------------------------------------
def test_set_parameters_sin_token_y_simulacion(api, doc, tmp_path):
    cuerpo = {"changes": [{"element_ids": [10, 11], "parameters": {"Comments": u"Revisado", "Mark": u"M-9"}}]}
    assert _post(api, "/set_parameters/", doc, cuerpo, con_token=False).status == 401
    r = _post(api, "/set_parameters/", doc, dict(cuerpo, simular=True))
    assert r.status == 200, r.data
    datos = r.data
    assert datos["simulado"] is True and datos["count"] == 4 and datos["elements"] == 2 and "copia" not in datos
    haria = datos["haria"]
    assert [(h["element_id"], h["parameter_label"], h["antes"], h["despues"]) for h in haria] == [
        (10, u"Comentarios", u"", u"Revisado"), (10, u"Marca", u"M-1", u"M-9"),
        (11, u"Comentarios", u"", u"Revisado"), (11, u"Marca", u"M-2", u"M-9"),
    ]
    assert all(h["is_type_parameter"] is False for h in haria) and datos["fallidos"] == []
    assert DB.Transaction.creadas == []
    assert doc.elementos[10].LookupParameter(u"Marca").AsString() == u"M-1"
    assert not (tmp_path / "backups").exists()


def test_set_parameters_lote_en_una_transaccion(api, doc):
    cuerpo = {"changes": [
        {"element_ids": [10, 11], "parameters": {"Comments": u"Revisado", "Unconnected Height": 3500}},
        {"element_id": 10, "parameters": {"Mark": u"M-10"}},
    ]}
    r = _post(api, "/set_parameters/", doc, cuerpo)
    assert r.status == 200, r.data
    datos = r.data
    assert datos["ok"] is True and datos["verificacion"] == {"coincide": True}
    assert datos["count"] == 2 and datos["elements"] == [10, 11] and datos["parameters_set"] == 5
    assert _transacciones() == [u"IA: Parametros (2 elementos)"]
    assert datos["antes"]["10"] == {u"Comentarios": u"", u"Altura desconectada": u"3000 mm", u"Marca": u"M-1"}
    assert datos["despues"]["10"][u"Comentarios"] == u"Revisado" and datos["despues"]["10"][u"Marca"] == u"M-10"
    assert datos["despues"]["11"][u"Comentarios"] == u"Revisado"
    # el Double se recibio en mm y se guardo en pies
    assert abs(doc.elementos[11].LookupParameter(u"Altura desconectada").AsDouble() - 3500 * mf.MM_TO_FEET) < 1e-9
    assert doc.elementos[10].LookupParameter(u"Marca").AsString() == u"M-10"
    assert all(c["coincide"] for c in datos["changes"]) and datos["changes"][0]["parameter_name"] == "Comments"
    assert datos["copia"]["ruta"].endswith(".rvt") and datos["fallidos"] == []


def test_set_parameters_informa_solo_lectura_e_inexistentes_sin_abortar(api, doc):
    cuerpo = {"changes": [
        {"element_ids": [10, 99], "parameters": {"Length": 4000, "NoExiste": u"x", "Comments": u"ok"}},
        {"element_id": 11, "parameter_name": "Unconnected Height", "value": "alto"},
    ]}
    r = _post(api, "/set_parameters/", doc, cuerpo)
    assert r.status == 200, r.data
    datos = r.data
    motivos = dict(((f["element_id"], f["parameter_name"]), f["motivo"]) for f in datos["fallidos"])
    assert motivos[(10, "Length")] == "read-only"
    assert motivos[(10, "NoExiste")] == "parameter not found"
    assert motivos[(99, "Length")] == "element not found" and motivos[(99, "Comments")] == "element not found"
    assert "not valid" in motivos[(11, "Unconnected Height")]
    assert [f for f in datos["fallidos"] if f["parameter_name"] == "NoExiste"][0]["available_parameters"]
    assert datos["parameters_set"] == 1 and datos["despues"]["10"] == {u"Comentarios": u"ok"}
    assert datos["ok"] is True and "1 failed" not in datos["message"] and "4 failed" not in datos["message"]
    assert _transacciones() == [u"IA: Parametros (1 elementos)"]
    # si todo falla: 400 con los fallidos y sin transaccion
    DB.Transaction.creadas = []
    r = _post(api, "/set_parameters/", doc, {"changes": [{"element_id": 10, "parameters": {"Length": 1}}]})
    assert r.status == 400 and r.data["fallidos"][0]["motivo"] == "read-only" and DB.Transaction.creadas == []


def test_set_parameters_de_tipo_se_fija_una_vez_por_tipo(api, doc):
    cuerpo = {"changes": [{"element_ids": [10, 11], "parameters": {"Type Comments": u"Tipo revisado", "Anchura": 250}}],
              "type_parameters": True}
    r = _post(api, "/set_parameters/", doc, dict(cuerpo, simular=True))
    assert r.status == 200 and all(h["is_type_parameter"] for h in r.data["haria"])
    assert r.data["haria"][1]["despues"] == u"250 mm"
    r = _post(api, "/set_parameters/", doc, cuerpo)
    assert r.status == 200, r.data
    datos = r.data
    assert datos["ok"] is True and datos["parameters_set"] == 4
    assert all(c["is_type_parameter"] and c["type_id"] == 50 for c in datos["changes"])
    assert datos["despues"]["10"][u"Comentarios de tipo"] == u"Tipo revisado"
    assert datos["despues"]["11"][u"Comentarios de tipo"] == u"Tipo revisado"
    assert abs(doc.elementos[50].LookupParameter(u"Anchura").AsDouble() - 250 * mf.MM_TO_FEET) < 1e-9
    # un parametro de tipo alcanzado desde el ejemplar (como set_parameter) tambien se marca
    r = _post(api, "/set_parameters/", doc, {"changes": [{"element_id": 10, "parameters": {"Type Comments": u"otra"}}]})
    assert r.status == 200 and r.data["changes"][0]["is_type_parameter"] is True and r.data["changes"][0]["type_id"] == 50


def test_set_parameters_compatibilidad_errores_y_limite(api, doc):
    r = _post(api, "/set_parameters/", doc, {"element_id": 10, "parameter_name": "Mark", "value": u"M-3"})
    assert r.status == 200 and r.data["despues"]["10"] == {u"Marca": u"M-3"}
    assert _post(api, "/set_parameters/", doc, {}).status == 400
    assert _post(api, "/set_parameters/", doc, {"changes": []}).status == 400
    assert _post(api, "/set_parameters/", doc, {"changes": [{"parameters": {"Mark": "x"}}]}).status == 400
    assert _post(api, "/set_parameters/", doc, {"changes": [{"element_id": 10}]}).status == 400
    assert _post(api, "/set_parameters/", doc, {"changes": [{"element_id": 10, "parameter_name": "Mark"}]}).status == 400
    assert _post(api, "/set_parameters/", doc, {"changes": [{"element_id": "x", "parameters": {"Mark": "a"}}]}).status == 400
    muchos = dict(("P{}".format(i), "v") for i in range(100))
    muchos["Mark"] = u"M-lote"
    r = _post(api, "/set_parameters/", doc, {"changes": [{"element_ids": [10, 11], "parameters": muchos}]})
    assert r.status == 400 and r.data["limite"] == 200 and r.data["cantidad"] == 202
    r = _post(api, "/set_parameters/", doc, {"changes": [{"element_ids": [10, 11], "parameters": muchos}], "forzar": True, "simular": True})
    assert r.status == 200 and len(r.data["fallidos"]) == 200 and r.data["count"] == 2 and r.data["elements"] == 2
    doc.IsModifiable = True
    r = _post(api, "/set_parameters/", doc, {"changes": [{"element_id": 10, "parameters": {"Mark": "a"}}]})
    assert r.status == 409 and r.data["open_transaction"] is True


# ---------------------------------------------------------------------------
# /create_elements/
# ---------------------------------------------------------------------------
LOTE = [
    {"kind": "wall", "start_point": {"x": 0, "y": 5000, "z": 0}, "end_point": {"x": 4000, "y": 5000, "z": 0},
     "level_name": u"Nivel 1", "height": 2800, "type_name": u"Genérico - 200 mm"},
    {"kind": "level", "name": u"Nivel 3", "elevation_mm": 6000},
    {"kind": "grid", "name": u"B", "start_point": {"x": 0, "y": 0, "z": 0}, "end_point": {"x": 0, "y": 9000, "z": 0}},
    {"kind": "column", "point": {"x": 0, "y": 0, "z": 0}, "base_level": u"Nivel 1", "top_level": u"Nivel 2", "type_name": u"HEB300"},
    {"kind": "beam", "start_point": {"x": 0, "y": 0, "z": 0}, "end_point": {"x": 6000, "y": 0, "z": 0}, "level_name": u"Nivel 2", "type_name": u"IPE300"},
    {"kind": "floor", "level_name": u"Nivel 1", "boundary": [{"x": 0, "y": 0, "z": 0}, {"x": 4000, "y": 0, "z": 0}, {"x": 4000, "y": 4000, "z": 0}, {"x": 0, "y": 4000, "z": 0}]},
    {"kind": "family_instance", "family_name": u"Caja", "type_name": u"Caja 1", "location": {"x": 1000, "y": 1000, "z": 0}, "level_name": u"Nivel 1", "properties": {"Comments": u"colocada"}},
]


def test_create_elements_sin_token_simulacion_con_plan(api, doc, tmp_path):
    antes = len(doc.elementos)
    assert _post(api, "/create_elements/", doc, {"elements": LOTE}, con_token=False).status == 401
    r = _post(api, "/create_elements/", doc, {"elements": LOTE, "simular": True})
    assert r.status == 200, r.data
    datos = r.data
    assert datos["simulado"] is True and datos["count"] == 7 and "copia" not in datos
    assert datos["plan"] == {"counts": {"wall": 1, "level": 1, "grid": 1, "column": 1, "beam": 1, "floor": 1, "family_instance": 1}, "total": 7}
    haria = datos["haria"]
    assert [h["kind"] for h in haria] == ["wall", "level", "grid", "column", "beam", "floor", "family_instance"]
    assert [h["index"] for h in haria] == list(range(7))
    assert haria[0]["height_mm"] == 2800.0 and haria[0]["type"] == u"Genérico - 200 mm"
    assert haria[3]["top_level"] == u"Nivel 2" and haria[4]["start_mm"]["z"] == 3000.0   # z = desfase desde Nivel 2
    assert haria[6]["accion"] == "colocar" and haria[6]["properties"] == {"Comments": u"colocada"}
    assert DB.Transaction.creadas == [] and len(doc.elementos) == antes


def test_create_elements_crea_todo_en_una_transaccion(api, doc):
    antes = len(doc.elementos)
    r = _post(api, "/create_elements/", doc, {"elements": LOTE})
    assert r.status == 200, r.data
    datos = r.data
    assert datos["ok"] is True and datos["count"] == 7 and datos["verificacion"] == {"coincide": True}
    assert _transacciones() == [u"IA: Crear 7 elementos"]
    assert len(doc.elementos) == antes + 7 and len(datos["creados_ids"]) == 7
    creados = datos["creados"]
    assert sorted(creados.keys()) == ["beam", "column", "family_instance", "floor", "grid", "level", "wall"]
    assert creados["wall"][0]["categoria"] == u"Muros" and creados["wall"][0]["index"] == 0
    assert creados["level"][0]["name"] == u"Nivel 3" and creados["level"][0]["elevation_mm"] == 6000.0
    assert creados["grid"][0]["name"] == u"B"
    assert creados["column"][0]["top_level"] == u"Nivel 2" and creados["column"][0]["point_mm"] == {"x": 0.0, "y": 0.0, "z": 0.0}
    assert creados["beam"][0]["nivel"] == u"Nivel 2"
    assert creados["floor"][0]["categoria"] == u"Suelos"
    assert creados["family_instance"][0]["properties_set"] == ["Comments"] and creados["family_instance"][0]["nivel"] == u"Nivel 1"
    assert datos["plan"]["counts"]["wall"] == 1 and "1 wall" in datos["message"]
    assert datos["copia"]["ruta"].endswith(".rvt")
    # las fabricas recibieron lo esperado
    muro = _elementos_de(doc, DB.Wall)[-1]
    assert abs(muro.altura - 2800 * mf.MM_TO_FEET) < 1e-9 and muro.type_id == DB.ElementId(50)
    columna = [e for e in doc.elementos.values() if isinstance(e, mf.Instancia) and e.symbol.Id.Value == 73][0]
    assert columna.get_Parameter(BIP.FAMILY_TOP_LEVEL_PARAM).AsElementId() == DB.ElementId(2)


def test_create_elements_valida_todo_antes_con_indice(api, doc):
    lote = [LOTE[0], {"kind": "wall", "start_point": {"x": 0, "y": 0, "z": 0}, "end_point": {"x": 0, "y": 0, "z": 0}}]
    r = _post(api, "/create_elements/", doc, {"elements": lote})
    assert r.status == 400 and r.data["index"] == 1 and r.data["kind"] == "wall"
    assert r.data["error"].startswith("elements[1]") and "zero-length" in r.data["error"]
    assert DB.Transaction.creadas == []
    r = _post(api, "/create_elements/", doc, {"elements": [{"kind": "room", "level_name": u"Sótano"}]})
    assert r.status == 404 and r.data["index"] == 0 and r.data["available_levels"] == [u"Nivel 1", u"Nivel 2"]
    r = _post(api, "/create_elements/", doc, {"elements": [{"kind": "puente"}]})
    assert r.status == 400 and "not supported" in r.data["error"] and "wall" in r.data["available_kinds"]
    r = _post(api, "/create_elements/", doc, {"elements": [{"start_point": {}}]})
    assert r.status == 400 and "kind is required" in r.data["error"]
    assert _post(api, "/create_elements/", doc, {}).status == 400
    assert _post(api, "/create_elements/", doc, {"elements": "wall"}).status == 400
    r = _post(api, "/create_elements/", doc, {"elements": [{"kind": "level", "name": u"N", "elevation_mm": 1}, {"kind": "level", "name": u"N", "elevation_mm": 2}]})
    assert r.status == 400 and r.data["index"] == 1 and "repeated" in r.data["error"]
    r = _post(api, "/create_elements/", doc, {"elements": [{"kind": "grid", "name": u"A", "start_point": {"x": 0, "y": 0, "z": 0}, "end_point": {"x": 1, "y": 0, "z": 0}}]})
    assert r.status == 400 and "already exists" in r.data["error"]
    r = _post(api, "/create_elements/", doc, {"elements": [{"kind": "opening", "host_id": 10, "points": [{"x": 1000, "y": 0, "z": 9000}, {"x": 2000, "y": 0, "z": 9500}]}]})
    assert r.status == 400 and r.data["index"] == 0 and "does not overlap" in r.data["error"]
    r = _post(api, "/create_elements/", doc, {"elements": [{"kind": "level", "elevation_mm": 1}] * 201})
    assert r.status == 400 and r.data["limite"] == 200 and r.data["cantidad"] == 201
    assert DB.Transaction.creadas == []


def test_create_elements_revierte_todo_si_uno_falla_en_revit(api, doc, monkeypatch):
    original = DB.Wall.Create

    def explota(doc_, curva, tipo_id, nivel_id, altura, desfase, flip, estructural):
        raise Exception("Revit: Wall cannot be created here")

    monkeypatch.setattr(DB.Wall, "Create", staticmethod(explota))
    lote = [LOTE[1], LOTE[0], LOTE[2]]
    r = _post(api, "/create_elements/", doc, {"elements": lote})
    assert r.status == 500, r.data
    assert r.data["index"] == 1 and r.data["kind"] == "wall" and r.data["rolled_back"] is True
    assert "Wall cannot be created here" in r.data["revit_error"] and "rolled back" in r.data["error"]
    assert DB.Transaction.creadas[0].estado == DB.TransactionStatus.RolledBack
    assert doc.IsModifiable is False
    monkeypatch.setattr(DB.Wall, "Create", staticmethod(original))


def test_create_elements_resto_de_kinds(api, doc):
    lote = [
        {"kind": "roof", "level_name": u"Nivel 2", "boundary": [{"x": 0, "y": 0, "z": 0}, {"x": 4000, "y": 0, "z": 0}, {"x": 4000, "y": 4000, "z": 0}, {"x": 0, "y": 4000, "z": 0}]},
        {"kind": "ceiling", "level_name": u"Nivel 1", "type_name": u"Techo 600x600", "boundary": [{"x": 0, "y": 0, "z": 0}, {"x": 4000, "y": 0, "z": 0}, {"x": 4000, "y": 4000, "z": 0}, {"x": 0, "y": 4000, "z": 0}]},
        {"kind": "foundation", "point": {"x": 0, "y": 0, "z": 0}, "level": u"Nivel 1", "type_name": u"1500x1500"},
        {"kind": "foundation", "wall_id": 10, "type_name": u"Corrida 600"},
        {"kind": "foundation", "boundary": [{"x": 0, "y": 0, "z": 0}, {"x": 8000, "y": 0, "z": 0}, {"x": 8000, "y": 8000, "z": 0}, {"x": 0, "y": 8000, "z": 0}], "level": u"Nivel 1", "type_name": u"Losa 400"},
        {"kind": "opening", "host_id": 10, "points": [{"x": 1000, "y": 0, "z": 900}, {"x": 2200, "y": 0, "z": 2100}]},
        {"kind": "toposolid", "level_name": u"Nivel 1", "points": [{"x": 0, "y": 0, "z": 0}, {"x": 1000, "y": 0, "z": 100}, {"x": 0, "y": 1000, "z": 50}]},
        {"kind": "room", "level_name": u"Nivel 1", "location": {"x": 2500, "y": 2000}, "name": u"Salón", "number": u"101"},
        {"kind": "room_separation", "lines": [{"start_point": {"x": 0, "y": 2000, "z": 0}, "end_point": {"x": 5000, "y": 2000, "z": 0}}, {"start_point": {"x": 0, "y": 3000, "z": 0}, "end_point": {"x": 5000, "y": 3000, "z": 0}}]},
        {"kind": "detail_line", "start_point": {"x": 0, "y": 0, "z": 0}, "end_point": {"x": 3000, "y": 0, "z": 0}},
        {"kind": "duct", "start_point": {"x": 0, "y": 0, "z": 2800}, "end_point": {"x": 6000, "y": 0, "z": 2800}, "width": 400, "height": 250, "level_name": u"Nivel 1"},
        {"kind": "pipe", "start_point": {"x": 0, "y": 500, "z": 2600}, "end_point": {"x": 6000, "y": 500, "z": 2600}, "diameter": 50},
        {"kind": "family_instance", "family_name": u"Puerta simple", "location": {"x": 2500, "y": 0, "z": 0}, "level_name": u"Nivel 1"},
    ]
    r = _post(api, "/create_elements/", doc, {"elements": lote, "simular": True})
    assert r.status == 200, r.data
    haria = r.data["haria"]
    assert haria[1]["element_type"] == "ceiling" and "nota" not in haria[1]
    assert haria[3]["element_type"] == "wall_foundation" and haria[3]["wall_id"] == 10
    assert haria[6]["element_type"] == "toposolid" and haria[6]["points"] == 3
    assert haria[8]["lines"] == 2 and haria[10]["dimensions_mm"] == {"width": 400, "height": 250}
    assert haria[12]["host_wall_id"] == 10
    r = _post(api, "/create_elements/", doc, {"elements": lote})
    assert r.status == 200, r.data
    datos = r.data
    assert datos["ok"] is True and datos["count"] == 14                     # 13 + la segunda separacion
    assert _transacciones() == [u"IA: Crear 13 elementos"]
    creados = datos["creados"]
    assert len(creados["foundation"]) == 3 and len(creados["room_separation"]) == 2
    assert creados["roof"][0]["categoria"] == u"Cubiertas" and creados["ceiling"][0]["categoria"] == u"Techos"
    assert creados["room"][0]["name"] == u"Salón" and creados["room"][0]["number"] == u"101"
    assert creados["duct"][0]["categoria"] == u"Conductos" and creados["pipe"][0]["categoria"] == u"Tuberías"
    assert creados["opening"][0]["categoria"] == u"Huecos"
    assert creados["family_instance"][0]["categoria"] == u"Puertas"
    puerta = [e for e in doc.elementos.values() if isinstance(e, mf.Instancia) and e.symbol.Id.Value == 77][0]
    assert puerta.Host is doc.elementos[10]
    assert isinstance(_elementos_de(doc, DB.Ceiling)[0], DB.Ceiling) and _elementos_de(doc, DB.WallFoundation)[0].wall_id == DB.ElementId(10)


def test_create_surface_ceiling_sin_ceiling_create_crea_suelo(api, doc, monkeypatch):
    """Sin DB.Ceiling.Create (Revit < 2022) el techo se crea como suelo, como en 0.3.x, y se avisa."""
    monkeypatch.delattr(DB.Ceiling, "Create")
    contorno = [{"x": 0, "y": 0, "z": 0}, {"x": 4000, "y": 0, "z": 0}, {"x": 4000, "y": 4000, "z": 0}, {"x": 0, "y": 4000, "z": 0}]
    r = _post(api, "/create_surface/", doc, {"elements": [{"element_type": "ceiling", "boundary": contorno}], "simular": True})
    assert r.status == 200 and "como suelo" in r.data["haria"][0]["nota"] and r.data["haria"][0]["type"] == u"Suelo 300"
    r = _post(api, "/create_surface/", doc, {"elements": [{"element_type": "ceiling", "boundary": contorno}]})
    assert r.status == 200 and r.data["creados"][0]["categoria"] == u"Suelos"


# ---------------------------------------------------------------------------
# /transform_elements/ array (0.4.0)
# ---------------------------------------------------------------------------
def test_transform_array_crea_count_menos_una_copias(api, doc):
    cuerpo = {"element_ids": [10, 11], "operation": "array", "vector": {"x": 0, "y": 3000, "z": 0}, "count": 3}
    r = _post(api, "/transform_elements/", doc, dict(cuerpo, simular=True))
    assert r.status == 200 and r.data["haria"][0]["count"] == 3 and r.data["haria"][0]["copies"] == 2
    assert DB.Transaction.creadas == []
    antes = len(doc.elementos)
    r = _post(api, "/transform_elements/", doc, cuerpo)
    assert r.status == 200, r.data
    assert r.data["ok"] is True and len(r.data["new_element_ids"]) == 4 and len(doc.elementos) == antes + 4
    assert _transacciones() == [u"IA: Matriz de 2 elementos"]
    ys = sorted(round(c["bbox_mm"]["min"]["y"]) for c in r.data["creados"])
    assert ys == [3000, 3000, 6000, 6000]
    assert _post(api, "/transform_elements/", doc, dict(cuerpo, count=1)).status == 400
    assert _post(api, "/transform_elements/", doc, dict(cuerpo, count="tres")).status == 400
    r = _post(api, "/transform_elements/", doc, dict(cuerpo, count=102))
    assert r.status == 400 and r.data["cantidad"] == 202


def test_set_parameters_de_tipo_acepta_el_id_del_tipo(api, doc):
    """Como set_type_parameter(type_id=...): con type_parameters el id puede ser ya el del tipo."""
    r = _post(api, "/set_parameters/", doc, {"changes": [{"element_ids": [50], "parameters": {"Type Comments": u"desde el tipo"}}],
                                            "type_parameters": True})
    assert r.status == 200, r.data
    assert r.data["fallidos"] == [] and r.data["changes"][0]["is_type_parameter"] is True
    assert r.data["changes"][0]["type_id"] == 50 and r.data["despues"]["50"][u"Comentarios de tipo"] == u"desde el tipo"
