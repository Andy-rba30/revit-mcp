# -*- coding: utf-8 -*-
"""Pruebas de extremo a extremo (CPython, pyrevit simulado) del Bloque A de la fase 2a:

  POST /describe/, /dependency_graph/, /query/, /schedule/, /view_extents/,
  GET /warnings/?group_by=description y POST /find_elements/ como alias de /query/.

El modelo simulado es un Revit "en espanol": los parametros se llaman
"Marca", "Comentarios" o "Longitud" y se resuelven por BuiltInParameter o por
su alias ingles ("Mark", "Comments", "Length"), nunca por el nombre visible en
ingles.
"""
import pytest
from pyrevit import DB, routes

import modelo_falso as mf
import seguridad

TOKEN = "c" * 64
BIP = DB.BuiltInParameter
BIC = DB.BuiltInCategory


@pytest.fixture
def doc(tmp_path, monkeypatch):
    mf.activar_spec(monkeypatch)
    rvt = tmp_path / "Modelo.rvt"
    rvt.write_bytes(b"RVT")
    doc = mf.Doc(str(rvt))

    n1 = mf.Nivel(doc, 1, u"Nivel 1", 0)
    mf.Nivel(doc, 2, u"Nivel 2", 3000)

    tipo = mf.TipoMuro(
        doc, 50, nombre=u"Genérico - 200 mm", categoria=u"Muros", bic=BIC.OST_Walls,
        parametros=[
            mf.texto(u"Comentarios de tipo", u"tipo", bip=BIP.ALL_MODEL_TYPE_COMMENTS),
            mf.longitud_mm(u"Anchura", 200),
        ],
    )
    tipo.FamilyName = u"Muro básico"

    def muro(identificador, marca, comentarios, longitud, nivel_id, caja, extra=()):
        parametros = [
            mf.texto(u"Marca", marca, bip=BIP.ALL_MODEL_MARK),
            mf.texto(u"Comentarios", comentarios, bip=BIP.ALL_MODEL_INSTANCE_COMMENTS),
            mf.longitud_mm(u"Longitud", longitud, bip=BIP.CURVE_ELEM_LENGTH),
            mf.longitud_mm(u"Altura desconectada", 3000, bip=BIP.WALL_USER_HEIGHT_PARAM),
            mf.referencia(u"Restricción de base", nivel_id, bip=BIP.LEVEL_PARAM),
            mf.entero(u"Estructural", 1 if identificador == 10 else 0),
            mf.texto(u"Código", u"C-{}".format(identificador), compartido=True,
                     guid="11111111-2222-3333-4444-{:012d}".format(identificador), elem_id=777),
        ] + list(extra)
        return mf.Muro(
            doc, identificador, nombre=u"Genérico - 200 mm", categoria=u"Muros", bic=BIC.OST_Walls,
            tipo_id=50, nivel_id=nivel_id, caja=caja, parametros=parametros,
        )

    m10 = muro(10, u"M-1", u"exterior", 5000, 1, mf.caja_mm(0, 0, 0, 5000, 200, 3000))
    m11 = muro(11, u"M-2", u"", 3000, 1, mf.caja_mm(6000, 0, 0, 9000, 200, 3000))
    m12 = muro(12, u"X-3", None, 8000, 2, mf.caja_mm(0, 5000, 3000, 8000, 5200, 6000))
    m10.Location = mf.Ubicacion(curva=DB.Line.CreateBound(DB.XYZ(0, 0, 0), DB.XYZ(5000 * mf.MM_TO_FEET, 0, 0)))
    m10.solidos = [DB.Solid(volumen=1.0, area=2.0, centro=DB.XYZ(1, 2, 3))]

    puerta = mf.Puerta(
        doc, 20, host=m10, nombre=u"0915 x 2134", categoria=u"Puertas", bic=BIC.OST_Doors, nivel_id=1,
        caja=mf.caja_mm(1000, 0, 0, 1915, 200, 2134),
        parametros=[mf.texto(u"Marca", u"P-1", bip=BIP.ALL_MODEL_MARK)],
    )
    suelo = mf.Suelo(doc, 30, nombre=u"Suelo 300", categoria=u"Suelos", bic=BIC.OST_Floors, nivel_id=1,
                     caja=mf.caja_mm(0, 0, -300, 9000, 6000, 0),
                     parametros=[mf.texto(u"Marca", u"S-1", bip=BIP.ALL_MODEL_MARK)])
    m10.insertos = [20]
    m10.dependientes = [20, 200]      # 200 es una cota (anotacion): no cuenta como dependiente de modelo
    m10.unidos = [30]
    suelo.unidos = [10]
    suelo.dependientes = [10]         # el muro "depende" del suelo en este modelo de juguete

    vista = mf.VistaPlanta(doc, 100, u"Planta Nivel 1", nivel=n1)
    vista.CropBoxActive = True
    vista.CropBoxVisible = False
    vista.CropBox = mf.caja_mm(-1000, -1000, -3000, 20000, 15000, 3000)
    vista.rango = DB.PlanViewRange(
        {DB.PlanViewPlane.CutPlane: DB.ElementId(1), DB.PlanViewPlane.TopClipPlane: DB.ElementId(1),
         DB.PlanViewPlane.BottomClipPlane: DB.ElementId(1), DB.PlanViewPlane.ViewDepthPlane: DB.PlanViewRange.Unlimited},
        {DB.PlanViewPlane.CutPlane: 1200 * mf.MM_TO_FEET, DB.PlanViewPlane.TopClipPlane: 2300 * mf.MM_TO_FEET},
    )
    doc.ActiveView = vista
    for elemento in (m10, m11, puerta, suelo):
        elemento.en_vistas.add(100)
    mf.Cota(doc, 200, [10, 11], 100)
    mf.Etiqueta(doc, 201, [10], 100)
    mf.Vista3D(doc, 101, u"{3D}")

    mf.Tabla(
        doc, 300, u"Tabla de muros",
        [u"Familia y tipo", u"Longitud", u"Marca"],
        [[u"Familia y tipo", u"Longitud", u"Marca"], [u"Genérico - 200 mm", u"5000", u"M-1"], [u"Genérico - 200 mm", u"3000", u"M-2"]],
    )

    fase = mf.Fase(doc, 400, nombre=u"Nueva construcción", categoria=u"Fases", bic=None)
    doc.Phases.append(fase)
    m10.CreatedPhaseId = DB.ElementId(400)
    m11.CreatedPhaseId = DB.ElementId(400)
    m12.CreatedPhaseId = DB.ElementId(401)

    solapados = DB.BuiltInFailures.OverlapFailures.WallsOverlap
    doc.avisos = [
        mf.Aviso(u"Los muros resaltados se solapan.", [10, 11], solapados),
        mf.Aviso(u"Los muros resaltados se solapan.", [11, 12], solapados),
        mf.Aviso(u"Aviso sin identificador conocido", [12], None),
    ]
    return doc


@pytest.fixture
def api():
    seguridad.establecer_token(TOKEN)
    DB.Transaction.creadas = []
    api = routes.API("revit_mcp")
    from navegacion import register_navegacion_routes
    from consulta import register_consulta_routes

    register_navegacion_routes(api)
    register_consulta_routes(api)
    return api


def _post(api, ruta, doc, cuerpo, con_token=True):
    datos = dict(cuerpo)
    if con_token:
        datos["token"] = TOKEN
    return api.rutas[(ruta, "POST")](doc=doc, request=routes.Request(path=ruta, data=datos))


def _get(api, ruta, doc, params=None, con_token=True):
    consulta = dict(params or {})
    if con_token:
        consulta["token"] = TOKEN
    return api.rutas[(ruta, "GET")](doc=doc, request=routes.Request(path=ruta, method="GET", query_params=consulta))


def _ids(respuesta):
    return sorted(respuesta.data["ids"])


# ---------------------------------------------------------------------------
# Seguridad y errores comunes
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("ruta,cuerpo", [
    ("/describe/", {"element_id": 10}),
    ("/dependency_graph/", {"element_id": 10}),
    ("/query/", {"category": "walls"}),
    ("/schedule/", {"name": u"Tabla de muros"}),
    ("/view_extents/", {"view_id": 100}),
])
def test_rutas_sin_token_401(api, doc, ruta, cuerpo):
    assert _post(api, ruta, doc, cuerpo, con_token=False).status == 401
    assert _post(api, ruta, None, cuerpo).status == 503
    assert DB.Transaction.creadas == []


def test_describe_errores_controlados(api, doc):
    assert _post(api, "/describe/", doc, {"element_id": 999}).status == 404
    assert _post(api, "/describe/", doc, {"element_id": "abc"}).status == 400
    assert _post(api, "/describe/", doc, {}).status == 400


# ---------------------------------------------------------------------------
# describe_element
# ---------------------------------------------------------------------------
def test_describe_muro_parametros_bbox_y_relaciones(api, doc):
    respuesta = _post(api, "/describe/", doc, {"element_id": 10})
    assert respuesta.status == 200, respuesta.data
    datos = respuesta.data
    assert datos["status"] == "success"
    assert datos["categoria"] == u"Muros"
    assert datos["tipo"] == u"Genérico - 200 mm"
    assert datos["familia"] == u"Muro básico"
    assert datos["nivel"] == u"Nivel 1"
    assert datos["unique_id"] == u"uid-10"
    assert datos["bbox_mm"] == {"min": {"x": 0.0, "y": 0.0, "z": 0.0}, "max": {"x": 5000.0, "y": 200.0, "z": 3000.0}}

    instancia = dict((p["name"], p) for p in datos["parameters"]["instance"])
    marca = instancia[u"Marca"]
    assert marca["value"] == u"M-1" and marca["builtin"] == "ALL_MODEL_MARK"
    assert marca["is_instance"] is True and marca["is_type_parameter"] is False
    assert marca["is_shared"] is False and marca["guid"] is None
    longitud = instancia[u"Longitud"]
    assert longitud["value"] == 5000.0 and longitud["unit"] == "mm" and longitud["display"] == u"5000 mm"
    nivel = instancia[u"Restricción de base"]
    assert nivel["value"] == u"Nivel 1"
    codigo = instancia[u"Código"]
    assert codigo["is_shared"] is True and codigo["guid"].endswith("000000000010")
    tipo = dict((p["name"], p) for p in datos["parameters"]["type"])
    assert tipo[u"Anchura"]["value"] == 200.0 and tipo[u"Anchura"]["is_type_parameter"] is True

    assert datos["hosted_elements"] == [{"id": 20}]
    assert datos["joined_elements"] == [{"id": 30}]
    assert datos["dependents"] == []          # la cota 200 no es de categoria de modelo
    assert datos["counts"] == {"hosted": 1, "joined": 1, "dependents": 0}
    assert sorted((r["id"], r["kind"]) for r in datos["referenced_by"]) == [(200, "dimension"), (201, "tag")]
    assert datos["active_view"] == {"id": 100, "name": u"Planta Nivel 1"}
    assert datos["host"] is None and datos["host_id"] is None
    assert "geometry" not in datos


def test_describe_puerta_con_host_y_depth(api, doc):
    respuesta = _post(api, "/describe/", doc, {"element_id": 20, "depth": 1})
    assert respuesta.status == 200, respuesta.data
    assert respuesta.data["host_id"] == 10
    assert respuesta.data["host"]["categoria"] == u"Muros"

    respuesta = _post(api, "/describe/", doc, {"element_id": 10, "depth": 2})
    alojado = respuesta.data["hosted_elements"][0]
    assert alojado["categoria"] == u"Puertas" and alojado["tipo"] == u"0915 x 2134"
    assert alojado["hosted_ids"] == [] and alojado["joined_ids"] == []
    unido = respuesta.data["joined_elements"][0]
    assert unido["categoria"] == u"Suelos" and unido["joined_ids"] == [10]
    assert respuesta.data["depth"] == 2

    # depth fuera de rango se recorta a 0..2
    assert _post(api, "/describe/", doc, {"element_id": 10, "depth": 9}).data["depth"] == 2


def test_describe_con_geometria(api, doc):
    respuesta = _post(api, "/describe/", doc, {"element_id": 10, "include_geometry": True})
    assert respuesta.status == 200, respuesta.data
    geometria = respuesta.data["geometry"]
    assert geometria["location"]["tipo"] == "curva"
    assert geometria["location"]["length_mm"] == 5000.0
    assert geometria["solid_count"] == 1
    assert abs(geometria["volume_m3"] - 0.0283) < 1e-4
    assert abs(geometria["area_m2"] - 0.186) < 1e-3


# ---------------------------------------------------------------------------
# dependency_graph
# ---------------------------------------------------------------------------
def test_dependency_graph_desde_el_muro(api, doc):
    respuesta = _post(api, "/dependency_graph/", doc, {"element_id": 10})
    assert respuesta.status == 200, respuesta.data
    datos = respuesta.data
    assert datos["root_id"] == 10
    ids = [n["id"] for n in datos["nodes"]]
    assert ids[0] == 10 and set(ids) == set([10, 20, 30])
    aristas = set((a["from"], a["to"], a["kind"]) for a in datos["edges"])
    assert (10, 20, "hosts") in aristas
    assert (10, 30, "joins") in aristas
    assert (30, 10, "depends") in aristas
    assert datos["truncated"] is False
    profundidades = dict((n["id"], n["depth"]) for n in datos["nodes"])
    assert profundidades == {10: 0, 20: 1, 30: 1}


def test_dependency_graph_desde_la_puerta_y_limite(api, doc):
    respuesta = _post(api, "/dependency_graph/", doc, {"element_id": 20})
    aristas = set((a["from"], a["to"], a["kind"]) for a in respuesta.data["edges"])
    assert (10, 20, "hosts") in aristas
    assert set(n["id"] for n in respuesta.data["nodes"]) == set([20, 10, 30])

    respuesta = _post(api, "/dependency_graph/", doc, {"element_id": 10, "max_nodes": 1})
    assert respuesta.data["node_count"] == 1
    assert respuesta.data["truncated"] is True
    assert respuesta.data["edges"] == []
    assert respuesta.data["edges_dropped"] >= 2

    respuesta = _post(api, "/dependency_graph/", doc, {"element_id": 10, "max_nodes": 9999})
    assert respuesta.data["max_nodes"] == 500
    assert _post(api, "/dependency_graph/", doc, {"element_id": 999}).status == 404


# ---------------------------------------------------------------------------
# query_elements: criterios, paginacion y orden
# ---------------------------------------------------------------------------
def test_query_categoria_y_paginacion(api, doc):
    respuesta = _post(api, "/query/", doc, {"category": "walls", "page_size": 2})
    assert respuesta.status == 200, respuesta.data
    datos = respuesta.data
    assert datos["total_matched"] == 3 and datos["count"] == 2 and datos["pages"] == 2
    assert datos["page"] == 1 and datos["truncated"] is True
    assert "category" in datos["native"]
    assert datos["elements"][0]["familia"] == u"Muro básico"
    primera = set(datos["ids"])
    segunda = _post(api, "/query/", doc, {"category": "OST_Walls", "page_size": 2, "page": 2})
    assert segunda.data["count"] == 1 and segunda.data["truncated"] is False
    assert not primera & set(segunda.data["ids"])
    assert primera | set(segunda.data["ids"]) == set([10, 11, 12])
    assert DB.Transaction.creadas == []


def test_query_errores_controlados(api, doc):
    assert _post(api, "/query/", doc, {}).status == 400
    assert _post(api, "/query/", doc, {"category": "OST_NoExiste"}).status == 400
    assert _post(api, "/query/", doc, {"category": "walls", "filters": [{"parameter": "Marca", "op": "~"}]}).status == 400
    assert _post(api, "/query/", doc, {"category": "walls", "filters": [{"parameter": "Marca"}]}).status == 400
    assert _post(api, "/query/", doc, {"category": "walls", "filters": [{"parameter": "Longitud", "op": ">", "value": "mucho"}]}).status == 400
    assert _post(api, "/query/", doc, {"category": "walls", "filters": "Marca"}).status == 400
    respuesta = _post(api, "/query/", doc, {"category": "walls", "level": u"Sótano"})
    assert respuesta.status == 404 and respuesta.data["available_levels"] == [u"Nivel 1", u"Nivel 2"]
    assert _post(api, "/query/", doc, {"view_id": 10}).status == 400
    assert _post(api, "/query/", doc, {"view_id": 999}).status == 404
    assert _post(api, "/query/", doc, {"category": "walls", "bbox_min_mm": {"x": 0, "y": 0, "z": 0}}).status == 400
    assert _post(api, "/query/", doc, {"category": "walls", "workset": "Estructura"}).status == 400
    respuesta = _post(api, "/query/", doc, {"category": "walls", "phase": "Existente"})
    assert respuesta.status == 404 and respuesta.data["available_phases"] == [u"Nueva construcción"]


def test_query_page_size_se_recorta_a_500(api, doc):
    respuesta = _post(api, "/query/", doc, {"category": "walls", "page_size": 1000})
    assert respuesta.data["page_size"] == 500
    assert any("500" in aviso for aviso in respuesta.data["warnings"])


@pytest.mark.parametrize("filtro,esperados,nativo", [
    ({"parameter": u"Marca", "op": "=", "value": "M-1"}, [10], True),
    ({"parameter": u"Marca", "op": "=", "value": "m-1"}, [10], True),          # sin distinguir mayusculas
    ({"parameter": "Mark", "op": "=", "value": "M-1"}, [10], True),            # alias ingles
    ({"parameter": "ALL_MODEL_MARK", "op": "=", "value": "M-1"}, [10], True),  # BuiltInParameter
    ({"parameter": u"Marca", "op": "!=", "value": "M-1"}, [11, 12], False),
    ({"parameter": u"Marca", "op": "contains", "value": "m-"}, [10, 11], True),
    ({"parameter": u"Marca", "op": "starts", "value": "X"}, [12], True),
    ({"parameter": u"Longitud", "op": ">", "value": 4000}, [10, 12], True),
    ({"parameter": "Length", "op": "<", "value": 4000}, [11], True),
    ({"parameter": u"Longitud", "op": ">=", "value": 8000}, [12], True),
    ({"parameter": u"Longitud", "op": "<=", "value": 5000}, [10, 11], True),
    ({"parameter": u"Longitud", "op": "=", "value": 5000}, [10], True),        # mm, no pies
    ({"parameter": u"Longitud", "op": "=", "value": "5000"}, [10], True),
    ({"parameter": u"Comentarios", "op": "empty"}, [11, 12], False),
    ({"parameter": "Comments", "op": "not_empty"}, [10], False),
    ({"parameter": u"Marca", "op": "exists"}, [10, 11, 12], False),
    ({"parameter": u"Estructural", "op": "=", "value": True}, [10], False),    # parametro de proyecto: Python
    ({"parameter": u"Estructural", "op": "=", "value": 0}, [11, 12], False),
    ({"parameter": u"Anchura", "op": "=", "value": 200}, [10, 11, 12], False), # parametro de tipo: Python
    ({"parameter": u"Restricción de base", "op": "=", "value": u"Nivel 2"}, [12], False),
    ({"parameter": u"Restricción de base", "op": "=", "value": 1}, [10, 11], True),  # ElementId nativo
    ({"parameter": u"Código", "op": "=", "value": "C-11"}, [11], True),        # compartido: nativo por Id
    ({"parameter": u"No existe", "op": "exists"}, [], False),
])
def test_query_filtros_por_operador(api, doc, filtro, esperados, nativo):
    respuesta = _post(api, "/query/", doc, {"category": "walls", "filters": [filtro]})
    assert respuesta.status == 200, respuesta.data
    assert _ids(respuesta) == esperados
    if nativo:
        assert any(f.startswith(u"filter " + filtro["parameter"]) for f in respuesta.data["native"]), respuesta.data["native"]
        assert respuesta.data["python_filters"] == []
    else:
        assert respuesta.data["python_filters"] == [{"parameter": filtro["parameter"], "op": filtro["op"]}]


def test_query_varios_filtros_y_alias_de_op(api, doc):
    respuesta = _post(api, "/query/", doc, {
        "category": "walls",
        "filters": [{"parameter": "Mark", "op": "startswith", "value": "M"},
                    {"parameter": "Length", "op": "gt", "value": 4000}],
    })
    assert _ids(respuesta) == [10]


def test_query_nivel_vista_bbox_familia_tipo_nombre(api, doc):
    assert _ids(_post(api, "/query/", doc, {"category": "walls", "level": u"Nivel 1"})) == [10, 11]
    assert _ids(_post(api, "/query/", doc, {"level": u"Nivel 2"})) == [12]
    assert _ids(_post(api, "/query/", doc, {"view_id": 100, "category": "walls"})) == [10, 11]
    assert _ids(_post(api, "/query/", doc, {"view_id": 100})) == [10, 11, 20, 30, 200, 201]   # cota y etiqueta incluidas
    respuesta = _post(api, "/query/", doc, {"category": "walls", "bbox_min_mm": {"x": 0, "y": 0, "z": 0},
                                            "bbox_max_mm": {"x": 1000, "y": 1000, "z": 1000}})
    assert _ids(respuesta) == [10] and "bbox" in respuesta.data["native"]
    assert _ids(_post(api, "/query/", doc, {"family": u"muro básico"})) == [10, 11, 12]
    assert _ids(_post(api, "/query/", doc, {"type_name": u"Muro básico: Genérico - 200 mm"})) == [10, 11, 12]
    assert _ids(_post(api, "/query/", doc, {"name_contains": "0915"})) == [20]
    assert _ids(_post(api, "/query/", doc, {"category": ["walls", "doors"]})) == [10, 11, 12, 20]


def test_query_fase_y_subproyecto(api, doc):
    assert _ids(_post(api, "/query/", doc, {"category": "walls", "phase": u"Nueva construcción"})) == [10, 11]
    doc.IsWorkshared = True
    doc.worksets = [mf.Workset(1, u"Estructura"), mf.Workset(2, u"Arquitectura")]
    doc.elementos[10].WorksetId = mf._IdWorkset(1)
    doc.elementos[11].WorksetId = mf._IdWorkset(2)
    respuesta = _post(api, "/query/", doc, {"category": "walls", "workset": u"Estructura"})
    assert _ids(respuesta) == [10] and "workset" in respuesta.data["native"]
    respuesta = _post(api, "/query/", doc, {"category": "walls", "workset": u"MEP"})
    assert respuesta.status == 404 and respuesta.data["available_worksets"] == [u"Arquitectura", u"Estructura"]


def test_query_orden_campos_e_ids_only(api, doc):
    respuesta = _post(api, "/query/", doc, {"category": "walls", "sort_by": "-Longitud"})
    assert respuesta.data["ids"] == [12, 10, 11]
    respuesta = _post(api, "/query/", doc, {"category": "walls", "sort_by": "Mark"})
    assert respuesta.data["ids"] == [10, 11, 12]
    respuesta = _post(api, "/query/", doc, {"category": "walls", "sort_by": "-id"})
    assert respuesta.data["ids"] == [12, 11, 10]
    respuesta = _post(api, "/query/", doc, {"category": "walls", "sort_by": "nivel"})
    assert respuesta.data["ids"][-1] == 12
    respuesta = _post(api, "/query/", doc, {"category": "walls", "sort_by": "id", "fields": ["Mark", "Longitud", u"Anchura", "Nada"]})
    assert respuesta.data["elements"][0]["fields"] == {"Mark": u"M-1", "Longitud": 5000.0, u"Anchura": 200.0, "Nada": None}
    respuesta = _post(api, "/query/", doc, {"category": "walls", "ids_only": True})
    assert respuesta.data["elements"] == [{"id": 10}, {"id": 11}, {"id": 12}]


def test_query_sin_criterios_nativos_avisa(api, doc):
    respuesta = _post(api, "/query/", doc, {"filters": [{"parameter": "Mark", "op": "starts", "value": "P"}]})
    assert _ids(respuesta) == [20]
    assert any("todos los elementos" in aviso for aviso in respuesta.data["warnings"])


# ---------------------------------------------------------------------------
# find_elements como alias
# ---------------------------------------------------------------------------
def test_find_elements_conserva_sus_campos(api, doc):
    respuesta = _post(api, "/find_elements/", doc, {"category": "OST_Walls", "parameter_name": "Comments", "parameter_value": "exterior"})
    assert respuesta.status == 200, respuesta.data
    assert set(respuesta.data.keys()) == set(["status", "elements", "ids", "count", "total_matched", "scanned", "truncated", "filters"])
    assert respuesta.data["ids"] == [10]
    assert respuesta.data["filters"]["parameter_name"] == "Comments" and respuesta.data["filters"]["max"] == 100
    assert set(respuesta.data["elements"][0].keys()) >= set(["id", "categoria", "tipo", "nivel", "bbox_mm"])
    respuesta = _post(api, "/find_elements/", doc, {"category": "walls", "parameter_name": "Mark"})
    assert sorted(respuesta.data["ids"]) == [10, 11, 12]
    respuesta = _post(api, "/find_elements/", doc, {"level_name": u"Nivel 1", "max": 2, "ids_only": True})
    assert respuesta.data["count"] == 2 and respuesta.data["truncated"] is True and respuesta.data["total_matched"] == 4
    assert respuesta.data["elements"][0].keys() == {"id"}
    assert _post(api, "/find_elements/", doc, {}).status == 400
    assert _post(api, "/find_elements/", doc, {"category": "OST_Nada"}).status == 400
    assert _post(api, "/find_elements/", doc, {"category": "walls"}, con_token=False).status == 401


# ---------------------------------------------------------------------------
# /warnings/ agrupadas
# ---------------------------------------------------------------------------
def test_warnings_group_by_description(api, doc):
    respuesta = _get(api, "/warnings/", doc, {"group_by": "description"})
    assert respuesta.status == 200, respuesta.data
    datos = respuesta.data
    assert datos["group_by"] == "description" and datos["total"] == 3 and datos["group_count"] == 2
    primero = datos["groups"][0]
    assert primero["count"] == 2 and primero["element_ids"] == [10, 11, 12]
    assert primero["failure"] == "OverlapFailures.WallsOverlap"
    assert "join_geometry" in primero["sugerencia"]
    assert primero["failure_definition_guid"].endswith("walls-overlap")
    assert primero["descripcion"] == u"Los muros resaltados se solapan."
    segundo = datos["groups"][1]
    assert segundo["count"] == 1 and segundo["failure"] is None
    assert segundo["sugerencia"].startswith("Sin sugerencia")
    assert _get(api, "/warnings/", doc, {"group_by": "level"}).status == 400
    assert _get(api, "/warnings/", doc, {"group_by": "description"}, con_token=False).status == 401


def test_warnings_sin_group_by_no_cambia(api, doc):
    respuesta = _get(api, "/warnings/", doc, {"max": "2"})
    assert respuesta.status == 200
    assert set(respuesta.data.keys()) == set(["status", "warnings", "count", "total", "truncated"])
    assert respuesta.data["count"] == 2 and respuesta.data["total"] == 3 and respuesta.data["truncated"] is True
    assert respuesta.data["warnings"][0]["element_ids"] == [10, 11]


# ---------------------------------------------------------------------------
# schedule_to_json
# ---------------------------------------------------------------------------
def test_schedule_to_json_por_nombre_y_por_id(api, doc):
    respuesta = _post(api, "/schedule/", doc, {"name": u"Tabla de muros"})
    assert respuesta.status == 200, respuesta.data
    datos = respuesta.data
    assert datos["schedule"] == {"id": 300, "name": u"Tabla de muros", "title": u"Tabla de muros"}
    assert datos["headers"] == [u"Familia y tipo", u"Longitud", u"Marca"] and datos["headers_from"] == "body row 0"
    assert datos["rows"] == [[u"Genérico - 200 mm", u"5000", u"M-1"], [u"Genérico - 200 mm", u"3000", u"M-2"]]
    assert datos["row_count"] == 2 and datos["total_rows"] == 2 and datos["truncated"] is False
    assert [c["name"] for c in datos["fields"]] == [u"Familia y tipo", u"Longitud", u"Marca"]
    assert _post(api, "/schedule/", doc, {"view_id": 300}).data["rows"] == datos["rows"]

    respuesta = _post(api, "/schedule/", doc, {"view_id": 300, "start_row": 1, "max_rows": 1})
    assert respuesta.data["headers_from"] == "field headings"
    assert respuesta.data["rows"] == [[u"Genérico - 200 mm", u"5000", u"M-1"]] and respuesta.data["truncated"] is True


def test_schedule_to_json_errores(api, doc):
    respuesta = _post(api, "/schedule/", doc, {"name": u"Tabla de puertas"})
    assert respuesta.status == 404 and respuesta.data["available_schedules"] == [u"Tabla de muros"]
    assert _post(api, "/schedule/", doc, {"view_id": 100}).status == 400
    assert _post(api, "/schedule/", doc, {}).status == 400


# ---------------------------------------------------------------------------
# get_view_extents
# ---------------------------------------------------------------------------
def test_view_extents_de_una_planta(api, doc):
    respuesta = _post(api, "/view_extents/", doc, {"view_id": 100})
    assert respuesta.status == 200, respuesta.data
    datos = respuesta.data
    assert datos["name"] == u"Planta Nivel 1" and datos["view_type"] == "FloorPlan"
    assert datos["scale"] == 100 and datos["level"] == u"Nivel 1"
    assert datos["discipline"] == "Architectural" and datos["detail_level"] == "Medium"
    assert datos["view_template"] is None and datos["is_template"] is False
    assert datos["crop"]["active"] is True and datos["crop"]["visible"] is False
    assert datos["crop"]["box"]["min_mm"] == {"x": -1000.0, "y": -1000.0, "z": -3000.0}
    assert datos["crop"]["box"]["max_model_mm"] == {"x": 20000.0, "y": 15000.0, "z": 3000.0}
    assert datos["view_range"]["cut"] == {"level": u"Nivel 1", "offset_mm": 1200.0}
    assert datos["view_range"]["top"] == {"level": u"Nivel 1", "offset_mm": 2300.0}
    assert datos["view_range"]["view_depth"] == {"level": "unlimited", "offset_mm": 0.0}
    assert datos["section_box"] is None
    assert _post(api, "/view_extents/", doc, {"view_name": u"Planta Nivel 1"}).data["view_id"] == 100


def test_view_extents_3d_y_errores(api, doc):
    respuesta = _post(api, "/view_extents/", doc, {"view_id": 101})
    assert respuesta.status == 200
    assert respuesta.data["view_range"] is None and respuesta.data["section_box"] == {"active": False, "box": None}
    assert _post(api, "/view_extents/", doc, {"view_id": 10}).status == 400
    assert _post(api, "/view_extents/", doc, {"view_name": u"No existe"}).status == 404
    assert _post(api, "/view_extents/", doc, {}).status == 400
