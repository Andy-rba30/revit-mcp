# -*- coding: utf-8 -*-
"""Entrega 2c (0.6.0), bloque C: editor de familias sobre el modelo simulado.

  POST /family/info/, /family/open/, /family/save/, /family/load/, /family/close/   ciclo de vida
  POST /family/parameters/, /family/reference_planes/, /family/dimensions/            lotes
  POST /family/solids/, /family/locks/, /family/types/, /family/connectors/           lotes
  POST /family/validate/, /family/build/                                              macros (familias_spec)

Cada ruta: 401 sin token, `simular` sin transaccion, 400/404 controlados y `creados` / `antes`-`despues`.
El "Revit" simulado esta en espanol (plantilla "Modelo genérico métrico.rft", planos "Centro (...)").
"""
import copy
import io
import os

import pytest
from pyrevit import DB, routes

import modelo_falso as mf
import seguridad

TOKEN = "6" * 64
PLANTILLA = u"Modelo genérico métrico.rft"


@pytest.fixture
def doc(tmp_path, monkeypatch):
    mf.activar_spec(monkeypatch)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))     # log de un documento sin guardar
    rvt = tmp_path / "Modelo.rvt"
    rvt.write_bytes(b"RVT")
    d = mf.Doc(str(rvt))
    carpeta = tmp_path / "Plantillas"
    (carpeta / "Estructura").mkdir(parents=True)
    (carpeta / PLANTILLA).write_bytes(b"RFT")
    (carpeta / u"Modelo genérico métrico basado en cara.rft").write_bytes(b"RFT")
    (carpeta / "Estructura" / u"Conexión estructural métrica.rft").write_bytes(b"RFT")
    d.Application.FamilyTemplatePath = str(carpeta)
    # una familia cargable (editable), otra in situ y un material en el proyecto
    d.agregar(mf.Familia(None, 0, nombre=u"Rigidizador", categoria=u"Rigidizadores estructurales",
                         bic=DB.BuiltInCategory.OST_StructuralStiffener), 300)
    insitu = mf.Familia(None, 0, nombre=u"In situ 1", categoria=u"Modelos genéricos", bic=DB.BuiltInCategory.OST_GenericModel)
    insitu.IsInPlace = True
    d.agregar(insitu, 301)
    return d


@pytest.fixture
def api(doc):
    import familias

    familias.DOCUMENTOS_ABIERTOS.clear()
    seguridad.establecer_token(TOKEN)
    DB.Transaction.creadas = []
    DB.Transaction.resultado_commit = DB.TransactionStatus.Committed
    api = routes.API("revit_mcp")
    familias.register_familias_routes(api)
    return api


def _post(api, ruta, doc, cuerpo, con_token=True):
    datos = dict(cuerpo)
    if con_token:
        datos["token"] = TOKEN
    return api.rutas[(ruta, "POST")](doc=doc, request=routes.Request(path=ruta, data=datos))


def _transacciones():
    return [t.nombre for t in DB.Transaction.creadas if not isinstance(t, DB.TransactionGroup)]


def _abrir(api, doc, nombre=u"Placa base"):
    r = _post(api, "/family/open/", doc, {"template": PLANTILLA, "name": nombre})
    assert r.status == 200, r.data
    DB.Transaction.creadas = []     # la del tipo inicial se comprueba en test_family_open_nueva_desde_plantilla
    return r.data["family_doc"], doc.Application.Documents[-1]


def _parametros_base(api, doc, fd):
    r = _post(api, "/family/parameters/", doc, {"family_doc": fd, "parameters": [
        {"name": u"Ancho", "data_type": "length", "group": "Geometry"},
        {"name": u"Largo", "data_type": "length", "group": "Geometry"},
        {"name": u"Espesor", "data_type": "length", "group": "Geometry"},
        {"name": u"Material placa", "data_type": "material", "group": "Materials"},
        {"name": u"Diámetro perno", "data_type": "length", "group": "Geometry"},
        {"name": u"Diámetro agujero", "data_type": "length", "group": "Geometry", "formula": u"Diámetro perno + 2 mm"},
    ]})
    assert r.status == 200, r.data
    return r.data


def _planos_base(api, doc, fd):
    r = _post(api, "/family/reference_planes/", doc, {"family_doc": fd, "planes": [
        {"name": u"Izquierda", "origin_mm": {"x": -150, "y": 0, "z": 0}, "direction": "y", "is_reference": "left"},
        {"name": u"Derecha", "origin_mm": {"x": 150, "y": 0, "z": 0}, "direction": "y", "is_reference": "right"},
        {"name": u"Delante", "origin_mm": {"x": 0, "y": -150, "z": 0}, "direction": "x", "is_reference": "front"},
        {"name": u"Detrás", "origin_mm": {"x": 0, "y": 150, "z": 0}, "direction": "x", "is_reference": "back"},
        {"name": u"Cara superior", "origin_mm": {"x": 0, "y": 0, "z": 20}, "direction": "horizontal", "is_reference": "top"},
    ]})
    assert r.status == 200, r.data
    return r.data


# ---------------------------------------------------------------------------
# info y open
# ---------------------------------------------------------------------------
def test_family_info_sin_documento_lista_abiertos_y_plantillas(api, doc):
    assert _post(api, "/family/info/", doc, {}, con_token=False).status == 401
    r = _post(api, "/family/info/", doc, {"include_templates": True})
    assert r.status == 200, r.data
    assert r.data["open_family_docs"] == [] and r.data["count"] == 0
    nombres = [p["name"] for p in r.data["templates"]]
    assert PLANTILLA in nombres and u"Conexión estructural métrica.rft" in nombres
    assert [p for p in r.data["templates"] if p["name"].startswith(u"Conexión")][0]["folder"] == "Estructura"
    r = _post(api, "/family/info/", doc, {"include_templates": True, "contains": "conexion"})
    assert [p["name"] for p in r.data["templates"]] == [u"Conexión estructural métrica.rft"]
    r = _post(api, "/family/info/", doc, {"family_doc": u"Nada"})
    assert r.status == 404 and r.data["available_family_docs"] == []


def test_family_open_nueva_desde_plantilla(api, doc):
    assert _post(api, "/family/open/", doc, {"template": PLANTILLA, "name": "x"}, con_token=False).status == 401
    r = _post(api, "/family/open/", doc, {})
    assert r.status == 400 and "template" in r.data["error"]
    r = _post(api, "/family/open/", doc, {"template": PLANTILLA})
    assert r.status == 400 and "name" in r.data["error"]
    r = _post(api, "/family/open/", doc, {"template": u"Metric Generic Model.rft", "name": u"Placa"})
    assert r.status == 404 and PLANTILLA in r.data["available_templates"] and r.data["family_template_path"]
    r = _post(api, "/family/open/", doc, {"template": u"modelo generico metrico", "name": u"Placa", "simular": True})
    assert r.status == 200, r.data
    assert r.data["simulado"] is True and r.data["haria"][0]["accion"] == "nueva_familia" and r.data["haria"][0]["template"].endswith(PLANTILLA)
    assert doc.Application.Documents == [] and "copia" not in r.data
    r = _post(api, "/family/open/", doc, {"template": PLANTILLA, "name": u"Placa base"})
    assert r.status == 200, r.data
    datos = r.data
    assert datos["ok"] is True and datos["family_doc"] == u"Familia1" and datos["mode"] == "new" and datos["opened_by_mcp"] is True
    assert datos["category"] == "OST_GenericModel" and datos["categoria"] == u"Modelos genéricos"
    assert [p["name"] for p in datos["reference_planes"]] == [u"Centro (izquierda/derecha)", u"Centro (delante/detrás)"]
    assert datos["reference_planes"][0]["normal"] == {"x": 1.0, "y": 0.0, "z": 0.0}
    # ELEM_REFERENCE_NAME de la plantilla como en Revit 2027 (1 y 4 en FamilyInstanceReferenceType)
    assert [p["is_reference"] for p in datos["reference_planes"]] == ["center_left_right", "center_front_back"]
    tipos = dict((v["view_type"], v) for v in datos["views"])
    assert tipos["FloorPlan"]["level"] == u"Nivel de referencia" and tipos["Elevation"]["direction"] in ("front", "left")
    # Revit crea la familia sin tipos; el MCP le da uno con el nombre pedido (sin el, SetFormula falla)
    assert datos["levels"][0]["elevation_mm"] == 0 and [t["name"] for t in datos["types"]] == [u"Placa base"]
    assert doc.Application.Documents[-1].FamilyManager.CurrentType.Name == u"Placa base"
    assert datos["copia"] is None
    assert _transacciones() == [u"IA: Tipo de familia Placa base"]
    # se puede pedir por el titulo o por el nombre dado
    for pedido in (u"Familia1", u"Placa base", u"Familia1.rfa"):
        r = _post(api, "/family/info/", doc, {"family_doc": pedido})
        assert r.status == 200 and r.data["family_doc"] == u"Familia1", pedido
    r = _post(api, "/family/info/", doc, {})
    assert r.data["open_family_docs"][0]["name"] == u"Placa base" and u"Placa base" in r.data["open_family_docs"][0]["aliases"]


def test_family_open_desde_rfa_y_edit_family(api, doc, tmp_path):
    r = _post(api, "/family/open/", doc, {"file_path": str(tmp_path / "no.rfa")})
    assert r.status == 404
    r = _post(api, "/family/open/", doc, {"file_path": str(tmp_path / "Modelo.rvt")})
    assert r.status == 400
    rfa = tmp_path / u"Pilar HEB.rfa"
    rfa.write_bytes(b"RFA")
    r = _post(api, "/family/open/", doc, {"file_path": str(rfa)})
    assert r.status == 200 and r.data["family_doc"] == u"Pilar HEB" and r.data["file_path"] == str(rfa) and r.data["mode"] == "open"
    # EditFamily: 404 con las familias del proyecto, 400 in situ, 409 con transaccion abierta
    r = _post(api, "/family/open/", doc, {"family_name": u"Muro basico"})
    assert r.status == 404 and r.data["available_families"] == [u"In situ 1", u"Rigidizador"]
    r = _post(api, "/family/open/", doc, {"family_name": u"In situ 1"})
    assert r.status == 400 and "in-place" in r.data["error"]
    doc.IsModifiable = True
    r = _post(api, "/family/open/", doc, {"family_name": u"Rigidizador"})
    assert r.status == 409 and r.data["open_transaction"] is True
    doc.IsModifiable = False

    def edit_family(familia):
        return mf.DocFamilia(doc.Application, titulo=familia.Name)

    doc.EditFamily = edit_family
    r = _post(api, "/family/open/", doc, {"family_name": u"Rigidizador"})
    assert r.status == 200, r.data
    assert r.data["family_doc"] == u"Rigidizador" and r.data["mode"] == "edit"


# ---------------------------------------------------------------------------
# parametros
# ---------------------------------------------------------------------------
def test_family_parameters_lote(api, doc):
    fd, familia = _abrir(api, doc)
    assert _post(api, "/family/parameters/", doc, {"family_doc": fd, "parameters": [{"name": "A"}]}, con_token=False).status == 401
    assert _post(api, "/family/parameters/", doc, {"parameters": [{"name": "A"}]}).status == 400
    assert _post(api, "/family/parameters/", doc, {"family_doc": fd}).status == 400
    r = _post(api, "/family/parameters/", doc, {"family_doc": fd, "parameters": [{"name": u"Ancho", "data_type": "metros", "group": "Geometry"}]})
    assert r.status == 400 and "length" in r.data["available_data_types"] and r.data["index"] == 0
    r = _post(api, "/family/parameters/", doc, {"family_doc": fd, "parameters": [{"name": u"Ancho", "data_type": "length", "group": "Nada"}]})
    assert r.status == 400 and "Geometry" in r.data["available_groups"]
    r = _post(api, "/family/parameters/", doc, {"family_doc": fd, "parameters": [
        {"name": u"Ancho", "data_type": "length", "group": "Geometry"},
        {"name": u"Mitad", "data_type": "length", "group": "Geometry", "formula": u"Ancho / 2 + Alto"}]})
    assert r.status == 400 and r.data["undefined"] == [u"Alto"] and r.data["index"] == 1
    cuerpo = {"family_doc": fd, "parameters": [
        {"name": u"Ancho", "data_type": "length", "group": "Geometry"},
        {"name": u"Mitad", "data_type": "length", "group": "geometria", "formula": u"Ancho / 2"},
        {"name": u"Visible", "data_type": "yes_no", "group": "Graphics", "is_instance": True},
        {"name": u"Perno", "data_type": u"family_type:OST_StructConnections", "group": "Data"}]}
    r = _post(api, "/family/parameters/", doc, dict(cuerpo, simular=True))
    assert r.status == 200, r.data
    assert r.data["simulado"] is True and [h["accion"] for h in r.data["haria"]] == ["crear_parametro"] * 4
    assert r.data["haria"][1]["group"] == "Geometry" and r.data["haria"][3]["data_type"] == u"family_type:OST_StructConnections"
    assert _transacciones() == [] and familia.FamilyManager.Parameters == []
    r = _post(api, "/family/parameters/", doc, cuerpo)
    assert r.status == 200, r.data
    assert r.data["ok"] is True and r.data["count"] == 4 and r.data["family_doc"] == fd
    assert _transacciones() == [u"IA: Parametros de familia (4)"]
    creados = dict((p["name"], p) for p in r.data["creados"])
    assert creados[u"Mitad"]["formula"] == u"Ancho / 2" and creados[u"Mitad"]["data_type"] == "length" and creados[u"Mitad"]["unit"] == "mm"
    assert creados[u"Visible"]["is_instance"] is True and creados[u"Visible"]["data_type"] == "yes_no"
    assert creados[u"Perno"]["storage_type"] == "ElementId" and creados[u"Ancho"]["group"] == "Geometry"
    r = _post(api, "/family/parameters/", doc, {"family_doc": fd, "parameters": [{"name": u"Ancho", "data_type": "length", "group": "Geometry"}]})
    assert r.status == 409 and u"Ancho" in r.data["existing"]


def test_family_parameters_compartido_por_guid(api, doc):
    fd, familia = _abrir(api, doc)
    r = _post(api, "/family/parameters/", doc, {"family_doc": fd, "parameters": [
        {"name": u"x", "shared_parameter_guid": "abc", "group": "Data"}]})
    assert r.status == 409 and r.data["motivo"] == "sin_archivo_compartido"
    doc.Application.archivo_compartidos = DB.DefinitionFile([DB.DefinitionGroup(u"Grupo", [
        DB.ExternalDefinition(u"Código de fabricante", "11111111-2222-3333-4444-555555555555", mf.SpecTypeId.String.Text)])])
    r = _post(api, "/family/parameters/", doc, {"family_doc": fd, "parameters": [
        {"name": u"x", "shared_parameter_guid": "{99999999-2222-3333-4444-555555555555}", "group": "Data"}]})
    assert r.status == 404 and r.data["available_shared_parameters"][0]["name"] == u"Código de fabricante"
    r = _post(api, "/family/parameters/", doc, {"family_doc": fd, "parameters": [
        {"name": u"x", "shared_parameter_guid": "{11111111-2222-3333-4444-555555555555}", "group": "Data", "is_instance": True}]})
    assert r.status == 200, r.data
    assert r.data["creados"][0]["name"] == u"Código de fabricante" and r.data["creados"][0]["is_shared"] is True
    assert r.data["creados"][0]["guid"] == "11111111-2222-3333-4444-555555555555" and r.data["creados"][0]["data_type"] == "text"


# ---------------------------------------------------------------------------
# planos de referencia y cotas
# ---------------------------------------------------------------------------
def test_family_reference_planes_lote(api, doc):
    fd, familia = _abrir(api, doc)
    assert _post(api, "/family/reference_planes/", doc, {"family_doc": fd, "planes": [{"name": "P"}]}, con_token=False).status == 401
    r = _post(api, "/family/reference_planes/", doc, {"family_doc": fd, "planes": [{"name": u"Centro (izquierda/derecha)"}]})
    assert r.status == 409 and r.data["index"] == 0
    r = _post(api, "/family/reference_planes/", doc, {"family_doc": fd, "planes": [{"name": u"P", "direction": "diagonal"}]})
    assert r.status == 400 and "direction" in r.data["error"]
    r = _post(api, "/family/reference_planes/", doc, {"family_doc": fd, "planes": [{"name": u"P", "is_reference": "lateral"}]})
    assert r.status == 400 and "left" in r.data["available_is_reference"]
    cuerpo = {"family_doc": fd, "planes": [
        {"name": u"Izquierda", "origin_mm": {"x": -150, "y": 0, "z": 0}, "direction": "y", "is_reference": "left"},
        {"name": u"Delante", "origin_mm": {"x": 0, "y": -150, "z": 0}, "direction": "x", "is_reference": True},
        {"name": u"Cara superior", "origin_mm": {"x": 0, "y": 0, "z": 20}, "direction": "horizontal", "is_reference": "top"}]}
    r = _post(api, "/family/reference_planes/", doc, dict(cuerpo, simular=True))
    assert r.status == 200, r.data
    haria = r.data["haria"]
    assert haria[0]["normal"] == {"x": 1.0, "y": 0.0, "z": 0.0} and haria[0]["view"]["view_type"] == "FloorPlan"
    assert haria[1]["normal"] == {"x": 0.0, "y": -1.0, "z": 0.0} and haria[1]["is_reference"] == "strong"
    assert haria[2]["normal"] == {"x": 0.0, "y": 0.0, "z": 1.0} and haria[2]["view"]["view_type"] == "Elevation"
    assert _transacciones() == [] and len([e for e in familia.elementos.values() if isinstance(e, DB.ReferencePlane)]) == 2
    r = _post(api, "/family/reference_planes/", doc, cuerpo)
    assert r.status == 200, r.data
    assert r.data["ok"] is True and r.data["count"] == 3 and _transacciones() == [u"IA: Planos de referencia (3)"]
    creados = dict((c["name"], c) for c in r.data["creados"])
    assert creados[u"Izquierda"]["origin_mm"] == {"x": -150.0, "y": -3000.0, "z": 0.0} and creados[u"Izquierda"]["is_reference"] == "left"
    assert creados[u"Cara superior"]["is_reference"] == "top" and creados[u"Cara superior"]["origin_mm"]["z"] == 20.0
    assert creados[u"Delante"]["categoria"] == u"Planos de referencia"
    plano = familia.GetElement(DB.ElementId(creados[u"Izquierda"]["id"]))
    assert plano.get_Parameter(DB.BuiltInParameter.ELEM_REFERENCE_NAME).AsInteger() == 0      # Left
    plano = familia.GetElement(DB.ElementId(creados[u"Cara superior"]["id"]))
    assert plano.get_Parameter(DB.BuiltInParameter.ELEM_REFERENCE_NAME).AsInteger() == 8      # Top
    plano = familia.GetElement(DB.ElementId(creados[u"Delante"]["id"]))
    assert plano.get_Parameter(DB.BuiltInParameter.ELEM_REFERENCE_NAME).AsInteger() == 13     # StrongReference


def test_family_dimensions_con_etiqueta_e_iguales(api, doc):
    fd, familia = _abrir(api, doc)
    _parametros_base(api, doc, fd)
    _planos_base(api, doc, fd)
    ruta = "/family/dimensions/"
    assert _post(api, ruta, doc, {"family_doc": fd, "dimensions": []}, con_token=False).status == 401
    r = _post(api, ruta, doc, {"family_doc": fd, "dimensions": [{"reference_planes": [u"Izquierda"], "parameter": u"Ancho"}]})
    assert r.status == 400 and "two" in r.data["error"]
    r = _post(api, ruta, doc, {"family_doc": fd, "dimensions": [{"reference_planes": [u"Izquierda", u"Nada"], "parameter": u"Ancho"}]})
    assert r.status == 404 and u"Derecha" in r.data["available_reference_planes"]
    r = _post(api, ruta, doc, {"family_doc": fd, "dimensions": [{"reference_planes": [u"Izquierda", u"Delante"], "parameter": u"Ancho"}]})
    assert r.status == 400 and "parallel" in r.data["error"]
    r = _post(api, ruta, doc, {"family_doc": fd, "dimensions": [{"reference_planes": [u"Izquierda", u"Derecha"], "parameter": u"Alto"}]})
    assert r.status == 404 and u"Ancho" in r.data["available_parameters"]
    r = _post(api, ruta, doc, {"family_doc": fd, "dimensions": [{"reference_planes": [u"Izquierda", u"Derecha"]}]})
    assert r.status == 400 and "equal" in r.data["error"]
    r = _post(api, ruta, doc, {"family_doc": fd, "dimensions": [{"reference_planes": [u"Izquierda", u"Derecha"], "equal": True}]})
    assert r.status == 400 and "three" in r.data["error"]
    cuerpo = {"family_doc": fd, "dimensions": [
        {"reference_planes": [u"Derecha", u"Izquierda"], "parameter": u"Ancho"},
        {"reference_planes": [u"Delante", u"Detrás"], "parameter": u"Largo"},
        {"reference_planes": [u"Izquierda", u"Centro (izquierda/derecha)", u"Derecha"], "equal": True},
        {"reference_planes": [u"Nivel de referencia", u"Cara superior"], "parameter": u"Espesor"}]}
    # el nivel no es un plano de referencia: 404
    r = _post(api, ruta, doc, cuerpo)
    assert r.status == 404 and r.data["index"] == 3
    cuerpo["dimensions"] = cuerpo["dimensions"][:3]
    r = _post(api, ruta, doc, dict(cuerpo, simular=True))
    assert r.status == 200, r.data
    haria = r.data["haria"]
    assert haria[0]["reference_planes"] == [u"Izquierda", u"Derecha"] and haria[0]["length_mm"] == 300.0
    assert haria[0]["view"]["view_type"] == "FloorPlan" and haria[0]["line_mm"]["start"]["x"] == -150.0 and haria[0]["line_mm"]["end"]["x"] == 150.0
    assert haria[2]["equal"] is True and haria[2]["parameter"] is None
    assert not [t for t in _transacciones() if t.startswith(u"IA: Cotas")]
    r = _post(api, ruta, doc, cuerpo)
    assert r.status == 200, r.data
    assert r.data["ok"] is True and r.data["count"] == 3 and _transacciones()[-1] == u"IA: Cotas con etiqueta (3)"
    assert r.data["creados"][0]["label"] == u"Ancho" and r.data["creados"][2]["equal"] is True
    cota = familia.GetElement(DB.ElementId(r.data["creados"][0]["id"]))
    assert cota.FamilyLabel.Definition.Name == u"Ancho" and len(cota.References) == 2
    assert familia.GetElement(DB.ElementId(r.data["creados"][2]["id"])).AreSegmentsEqual is True
    r = _post(api, "/family/info/", doc, {"family_doc": fd})
    assert [d["label"] for d in r.data["dimensions"]] == [u"Ancho", u"Largo", None]


# ---------------------------------------------------------------------------
# solidos, bloqueos, tipos y conectores
# ---------------------------------------------------------------------------
PLACA = {"name": u"placa", "kind": "extrusion", "sketch_plane": {"view_type": "FloorPlan"},
         "profile": {"rect": {"min_mm": {"x": -150, "y": -150, "z": 0}, "max_mm": {"x": 150, "y": 150, "z": 0}}},
         "start_mm": 0, "end_mm": 20, "material_parameter": u"Material placa",
         "lock_ends_to": {"end": u"Cara superior"},
         "lock_faces": [{"face": "left", "reference_plane": u"Izquierda"}, {"face": "right", "reference_plane": u"Derecha"}]}
AGUJERO = {"name": u"agujero 1", "kind": "extrusion", "is_void": True, "sketch_plane": {"view_type": "FloorPlan"},
           "profile": {"circle": {"center_mm": {"x": 100, "y": 100, "z": 0}, "radius_mm": 11}}, "start_mm": -5, "end_mm": 30}


def test_family_solids_extrusion_vaciado_y_bloqueos(api, doc):
    fd, familia = _abrir(api, doc)
    _parametros_base(api, doc, fd)
    _planos_base(api, doc, fd)
    ruta = "/family/solids/"
    assert _post(api, ruta, doc, {"family_doc": fd, "solids": [PLACA]}, con_token=False).status == 401
    r = _post(api, ruta, doc, {"family_doc": fd, "solids": [dict(PLACA, kind="cono")]})
    assert r.status == 400 and "kind" in r.data["error"]
    r = _post(api, ruta, doc, {"family_doc": fd, "solids": [dict(PLACA, end_mm=None)]})
    assert r.status == 400 and "end_mm" in r.data["error"] and r.data["index"] == 0
    r = _post(api, ruta, doc, {"family_doc": fd, "solids": [dict(PLACA, material_parameter=u"Material tapa")]})
    assert r.status == 404 and u"Material placa" in r.data["available_parameters"]
    r = _post(api, ruta, doc, {"family_doc": fd, "solids": [dict(PLACA, lock_ends_to={"end": u"Tapa"})]})
    assert r.status == 404 and "Tapa" in r.data["error"]
    r = _post(api, ruta, doc, {"family_doc": fd, "solids": [dict(PLACA, profile=[{"x": 0, "y": 0, "z": 0}, {"x": 10, "y": 0, "z": 0}])]})
    assert r.status == 400 and "3 points" in r.data["error"]
    cuerpo = {"family_doc": fd, "solids": [PLACA, AGUJERO]}
    r = _post(api, ruta, doc, dict(cuerpo, simular=True))
    assert r.status == 200, r.data
    haria = r.data["haria"]
    assert haria[0]["accion"] == "crear_solido" and haria[0]["end_mm"] == 20.0 and haria[0]["sketch_plane"] == u"Nivel de referencia"
    assert haria[0]["profile"][0]["points_mm"][0] == {"x": -150.0, "y": -150.0, "z": 0.0} and len(haria[0]["profile"][0]["points_mm"]) == 4
    assert haria[1]["accion"] == "crear_vaciado" and haria[1]["profile"][0]["circle"]["radius_mm"] == 11.0 and haria[1]["start_mm"] == -5.0
    assert not [t for t in _transacciones() if t.startswith(u"IA: Solidos")]
    assert [e for e in familia.elementos.values() if isinstance(e, DB.GenericForm)] == []
    r = _post(api, ruta, doc, cuerpo)
    assert r.status == 200, r.data
    assert r.data["ok"] is True and r.data["count"] == 2 and r.data["avisos"] == []
    assert _transacciones()[-1] == u"IA: Solidos de familia (2)"
    placa, agujero = r.data["creados"]
    assert placa["kind"] == "extrusion" and placa["is_void"] is False and placa["material_parameter"] == u"Material placa"
    assert placa["start_mm"] == 0.0 and placa["end_mm"] == 20.0 and placa["volume_m3"] > 0
    assert agujero["is_void"] is True and agujero["start_mm"] == -5.0 and agujero["end_mm"] == 30.0
    # bloqueos: cara superior al plano "Cara superior", izquierda y derecha a sus planos
    assert len(familia.alineaciones) == 3
    nombres = [familia.GetElement(a.referencias[1].ElementId).Name for a in familia.alineaciones]
    assert nombres == [u"Cara superior", u"Izquierda", u"Derecha"]
    assert familia.alineaciones[0].referencias[0].cara == "top" and familia.alineaciones[1].referencias[0].cara == "left"
    forma = familia.GetElement(DB.ElementId(placa["id"]))
    assert forma.get_Parameter(DB.BuiltInParameter.MATERIAL_ID_PARAM).asociado.Definition.Name == u"Material placa"
    # bloqueo suelto de otra cara
    r = _post(api, "/family/locks/", doc, {"family_doc": fd, "locks": [{"solid_id": placa["id"], "face": "front", "reference_plane": u"Delante"}]})
    assert r.status == 200, r.data
    assert r.data["count"] == 1 and _transacciones()[-1] == u"IA: Bloquear caras (1)" and len(familia.alineaciones) == 4
    r = _post(api, "/family/locks/", doc, {"family_doc": fd, "locks": [{"solid_id": 999, "face": "front", "reference_plane": u"Delante"}]})
    assert r.status == 404 and r.data["available_solids"] == [placa["id"], agujero["id"]]
    r = _post(api, "/family/info/", doc, {"family_doc": fd})
    assert r.data["counts"]["solids"] == 2 and r.data["solids"][1]["is_void"] is True


def test_family_solids_barrido_revolucion_y_fundido(api, doc):
    fd, familia = _abrir(api, doc)
    cuerpo = {"family_doc": fd, "solids": [
        {"name": u"tubo", "kind": "sweep", "sketch_plane": {"view_type": "FloorPlan"},
         "path": [{"x": 0, "y": 0, "z": 0}, {"x": 1000, "y": 0, "z": 0}, {"x": 1000, "y": 500, "z": 0}],
         "profile": {"circle": {"center_mm": {"x": 0, "y": 0, "z": 0}, "radius_mm": 25}}},
        {"name": u"eje", "kind": "revolution", "sketch_plane": {"view_type": "FloorPlan"},
         "profile": [{"x": 0, "y": 0, "z": 0}, {"x": 50, "y": 0, "z": 0}, {"x": 50, "y": 300, "z": 0}, {"x": 0, "y": 300, "z": 0}],
         "axis": {"start_mm": {"x": 0, "y": 0, "z": 0}, "end_mm": {"x": 0, "y": 300, "z": 0}}, "end_angle_deg": 180},
        {"name": u"tolva", "kind": "blend", "sketch_plane": {"view_type": "FloorPlan"}, "end_mm": 400,
         "base_profile": {"rect": {"min_mm": {"x": 0, "y": 0, "z": 0}, "max_mm": {"x": 400, "y": 400, "z": 0}}},
         "top_profile": {"rect": {"min_mm": {"x": 100, "y": 100, "z": 0}, "max_mm": {"x": 300, "y": 300, "z": 0}}}}]}
    r = _post(api, "/family/solids/", doc, dict(cuerpo, simular=True))
    assert r.status == 200, r.data
    assert r.data["haria"][0]["path"]["points_mm"][2] == {"x": 1000.0, "y": 500.0, "z": 0.0}
    assert r.data["haria"][1]["end_angle_deg"] == 180.0 and r.data["haria"][2]["end_mm"] == 400.0
    r = _post(api, "/family/solids/", doc, cuerpo)
    assert r.status == 200, r.data
    assert [c["kind"] for c in r.data["creados"]] == ["sweep", "revolution", "blend"] and r.data["ok"] is True
    llamadas = [l[0] for l in familia.FamilyCreate.llamadas]
    assert llamadas[-3:] == ["NewSweep", "NewRevolution", "NewBlend"]
    assert familia.GetElement(DB.ElementId(r.data["creados"][2]["id"])).TopOffset == pytest.approx(400 * mf.MM_TO_FEET)
    r = _post(api, "/family/solids/", doc, {"family_doc": fd, "solids": [dict(cuerpo["solids"][1], axis={"start_mm": {"x": 0, "y": 0}, "end_mm": {"x": 0, "y": 0}})]})
    assert r.status == 400 and "axis" in r.data["error"]


def test_family_types_lote_con_unidades(api, doc):
    fd, familia = _abrir(api, doc)
    _parametros_base(api, doc, fd)
    ruta = "/family/types/"
    assert _post(api, ruta, doc, {"family_doc": fd, "types": [{"type_name": "A"}]}, con_token=False).status == 401
    r = _post(api, ruta, doc, {"family_doc": fd, "types": [{"type_name": u"PL300", "values": {u"Alto": 1}}]})
    assert r.status == 404 and u"Ancho" in r.data["available_parameters"]
    r = _post(api, ruta, doc, {"family_doc": fd, "types": [{"type_name": u"PL300", "values": {u"Diámetro agujero": 1}}]})
    assert r.status == 400 and "formula" in r.data["error"]
    r = _post(api, ruta, doc, {"family_doc": fd, "types": [{"type_name": u"PL300", "values": {u"Ancho": 300}, "create_if_missing": False}]})
    assert r.status == 404 and r.data["available_types"] == [u"Placa base"]
    r = _post(api, ruta, doc, {"family_doc": fd, "types": [{"type_name": u"PL300", "values": {u"Ancho": "trescientos"}}]})
    assert r.status == 400 and "number" in r.data["error"]
    cuerpo = {"family_doc": fd, "types": [
        {"type_name": u"PL300x300x20", "values": {u"Ancho": 300, u"Largo": 300, u"Espesor": 20, u"Diámetro perno": 20}},
        {"type_name": u"PL400x400x25", "values": {u"Ancho": 400, u"Largo": 400, u"Espesor": 25, u"Diámetro perno": 24}}]}
    r = _post(api, ruta, doc, dict(cuerpo, simular=True))
    assert r.status == 200 and r.data["haria"][0]["accion"] == "crear_tipo" and r.data["haria"][0]["values"][u"Ancho"] == u"300 mm"
    assert _transacciones() == [u"IA: Parametros de familia (6)"] and len(familia.FamilyManager.Types) == 1
    r = _post(api, ruta, doc, cuerpo)
    assert r.status == 200, r.data
    assert r.data["ok"] is True and r.data["count"] == 2 and r.data["fallidos"] == [] and _transacciones()[-1] == u"IA: Tipos de familia (2)"
    assert r.data["types"][0]["created"] is True and r.data["despues"][u"PL300x300x20"][u"Ancho"] == 300.0
    assert r.data["antes"][u"PL300x300x20"][u"Ancho"] is None
    tipo = [t for t in familia.FamilyManager.Types if t.Name == u"PL400x400x25"][0]
    assert tipo.valores[u"Ancho"] == pytest.approx(400 * mf.MM_TO_FEET)
    assert [t["name"] for t in r.data["family_types"]] == [u"Placa base", u"PL300x300x20", u"PL400x400x25"]
    # cambiar un tipo existente: antes/despues
    r = _post(api, ruta, doc, {"family_doc": fd, "types": [{"type_name": u"PL300x300x20", "values": {u"Espesor": 22}}]})
    assert r.data["types"][0]["created"] is False and r.data["antes"][u"PL300x300x20"] == {u"Espesor": 20.0}
    assert r.data["despues"][u"PL300x300x20"] == {u"Espesor": 22.0}


def test_family_connectors_lote(api, doc):
    fd, familia = _abrir(api, doc)
    r = _post(api, "/family/solids/", doc, {"family_doc": fd, "solids": [dict(PLACA, material_parameter=None, lock_ends_to=None, lock_faces=[])]})
    assert r.status == 200, r.data
    solido = r.data["creados"][0]["id"]
    ruta = "/family/connectors/"
    assert _post(api, ruta, doc, {"family_doc": fd, "connectors": [{}]}, con_token=False).status == 401
    r = _post(api, ruta, doc, {"family_doc": fd, "connectors": [{"domain": "structural", "solid_id": solido}]})
    assert r.status == 400 and "no structural" in r.data["error"]
    r = _post(api, ruta, doc, {"family_doc": fd, "connectors": [{"domain": "hvac", "solid_id": solido, "system_type": "Vapor"}]})
    assert r.status == 400 and "SupplyAir" in r.data["available_system_types"]
    r = _post(api, ruta, doc, {"family_doc": fd, "connectors": [{"domain": "hvac", "solid_id": solido, "system_type": "supply_air"}]})
    assert r.status == 400 and "size_mm" in r.data["error"]
    cuerpo = {"family_doc": fd, "connectors": [
        {"domain": "hvac", "solid_id": solido, "face": "top", "system_type": "supply_air", "size_mm": 200},
        {"domain": "piping", "solid_id": solido, "face": "left", "system_type": "DomesticColdWater", "size_mm": {"diameter": 50}},
        {"domain": "electrical", "solid_id": solido, "face": "right", "system_type": "PowerCircuit"},
        {"domain": "hvac", "solid_id": solido, "face": "bottom", "system_type": "ReturnAir", "size_mm": {"width": 300, "height": 200}}]}
    r = _post(api, ruta, doc, dict(cuerpo, simular=True))
    assert r.status == 200, r.data
    assert r.data["haria"][0]["radius_mm"] == 100.0 and r.data["haria"][0]["system_type"] == "SupplyAir" and r.data["haria"][3]["width_mm"] == 300.0
    assert _transacciones() == [u"IA: Solidos de familia (1)"]
    r = _post(api, ruta, doc, cuerpo)
    assert r.status == 200, r.data
    assert r.data["ok"] is True and r.data["count"] == 4 and _transacciones()[-1] == u"IA: Conectores de familia (4)"
    creados = r.data["creados"]
    assert creados[0]["domain"] == "DomainHvac" and creados[0]["radius_mm"] == 100.0 and creados[0]["system_type"] == "SupplyAir"
    assert creados[1]["domain"] == "DomainPiping" and creados[1]["radius_mm"] == 25.0
    assert creados[2]["domain"] == "DomainElectrical" and creados[3]["shape"] == "Rectangular" and creados[3]["height_mm"] == 200.0
    conector = familia.GetElement(DB.ElementId(creados[0]["id"]))
    assert conector.referencia.cara == "top"


# ---------------------------------------------------------------------------
# save, load, close
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
def _familia_completa(api, doc):
    fd, familia = _abrir(api, doc)
    _parametros_base(api, doc, fd)
    _planos_base(api, doc, fd)
    r = _post(api, "/family/solids/", doc, {"family_doc": fd, "solids": [PLACA, AGUJERO]})
    assert r.status == 200, r.data
    r = _post(api, "/family/types/", doc, {"family_doc": fd, "types": [
        {"type_name": u"PL300x300x20", "values": {u"Ancho": 300, u"Largo": 300, u"Espesor": 20, u"Diámetro perno": 20}},
        {"type_name": u"PL400x400x25", "values": {u"Ancho": 400, u"Largo": 400, u"Espesor": 25, u"Diámetro perno": 24}}]})
    assert r.status == 200, r.data
    return fd, familia


def test_family_save_load_close(api, doc, tmp_path):
    fd, familia = _familia_completa(api, doc)
    destino = str(tmp_path / u"Placa base.rfa")
    r = _post(api, "/family/save/", doc, {"family_doc": fd, "file_path": destino}, con_token=False)
    assert r.status == 401
    r = _post(api, "/family/save/", doc, {"family_doc": fd})
    assert r.status == 400
    r = _post(api, "/family/save/", doc, {"family_doc": fd, "file_path": str(tmp_path / "no" / "x.rfa")})
    assert r.status == 404
    r = _post(api, "/family/save/", doc, {"family_doc": fd, "file_path": destino, "simular": True})
    assert r.status == 200 and r.data["haria"][0] == {"accion": "guardar_familia", "file_path": destino, "overwrite": False, "exists": False}
    assert not os.path.exists(destino)
    r = _post(api, "/family/save/", doc, {"family_doc": fd, "file_path": destino})
    assert r.status == 200, r.data
    assert r.data["ok"] is True and r.data["family_doc"] == u"Placa base" and r.data["previous_family_doc"] == u"Familia1"
    assert os.path.isfile(destino) and r.data["verificacion"]["coincide"] is True
    r = _post(api, "/family/save/", doc, {"family_doc": u"Placa base", "file_path": destino})
    assert r.status == 409 and r.data["exists"] is True
    r = _post(api, "/family/save/", doc, {"family_doc": u"Familia1", "file_path": destino, "overwrite": True})
    assert r.status == 200 and r.data["overwritten"] is True and r.data["copia"]["ruta"].endswith(".rfa")
    # cargar en el proyecto: LoadFamily SIN transaccion del proyecto (Revit la rechaza), dentro de un
    # TransactionGroup "IA: Cargar familia ..."; 409 la segunda vez salvo overwrite_parameters
    r = _post(api, "/family/load/", doc, {"family_doc": u"Placa base", "simular": True})
    assert r.status == 200 and r.data["haria"][0]["accion"] == "cargar_en_proyecto" and r.data["haria"][0]["already_loaded"] is False
    r = _post(api, "/family/load/", doc, {"family_doc": u"Placa base"})
    assert r.status == 200, r.data
    assert r.data["ok"] is True and r.data["family"] == u"Placa base" and [t["type"] for t in r.data["types"]] == [u"Placa base", u"PL300x300x20", u"PL400x400x25"]
    assert r.data["count"] == 3 and r.data["creados"][0]["categoria"] == u"Modelos genéricos" and r.data["avisos"] == []
    carga = doc.transacciones[-1]
    assert isinstance(carga, DB.TransactionGroup) and carga.nombre == u"IA: Cargar familia Placa base"
    assert carga.estado == DB.TransactionStatus.Committed and doc.IsModifiable is False
    r = _post(api, "/family/load/", doc, {"family_doc": u"Placa base"})
    assert r.status == 409 and r.data["already_loaded"] is True and len(r.data["types"]) == 3
    r = _post(api, "/family/load/", doc, {"family_doc": u"Placa base", "overwrite_parameters": True})
    assert r.status == 200 and r.data["reloaded"] is True and familia.cargas_en_proyecto[-1][1] is True
    r = _post(api, "/family/load/", doc, {"file_path": str(tmp_path / "otra.rfa")})
    assert r.status == 404
    # cerrar: 400 si no lo abrio el MCP, 409 si es el activo
    otro = mf.DocFamilia(doc.Application, titulo=u"Abierta a mano")
    r = _post(api, "/family/close/", doc, {"family_doc": u"Abierta a mano"})
    assert r.status == 400 and "Revit" in r.data["error"]
    r = _post(api, "/family/close/", doc, {"family_doc": u"Placa base", "simular": True})
    assert r.status == 200 and r.data["haria"][0]["file_path"] == destino
    r = _post(api, "/family/close/", doc, {"family_doc": u"Placa base", "save": True})
    assert r.status == 200, r.data
    assert r.data["closed"] is True and r.data["ok"] is True and familia.cerrado is True and r.data["open_family_docs"] == [u"Abierta a mano"]
    r = _post(api, "/family/info/", doc, {"family_doc": u"Placa base"})
    assert r.status == 404
    otro.Close()


def test_family_close_rechaza_el_documento_activo(api, doc):
    fd, familia = _abrir(api, doc)
    r = _post(api, "/family/close/", doc, {"family_doc": fd, "save": True})
    assert r.status == 400 and "never been saved" in r.data["error"]
    # el documento de familia es el activo: Routes lo pasa como `doc`
    r = _post(api, "/family/close/", familia, {"family_doc": fd})
    assert r.status == 409 and "active" in r.data["error"]
    familia.IsModifiable = True
    r = _post(api, "/family/close/", doc, {"family_doc": fd})
    assert r.status == 409 and r.data["open_transaction"] is True
    familia.IsModifiable = False




# ---------------------------------------------------------------------------
# 0.6.2: comportamiento medido en Revit 2027 (validacion 2c)
# ---------------------------------------------------------------------------
def test_formula_en_familia_sin_tipos_crea_uno(api, doc):
    """Un .rfa guardado sin tipos: SetFormula fallaba con "There is no valid family type"."""
    fd, familia = _abrir(api, doc)
    familia.FamilyManager.Types[:] = []
    familia.FamilyManager.CurrentType = None
    r = _post(api, "/family/parameters/", doc, {"family_doc": fd, "parameters": [
        {"name": u"Diámetro perno", "data_type": "length", "group": "Geometry"},
        {"name": u"Diámetro agujero", "data_type": "length", "group": "Geometry", "formula": u"Diámetro perno + 2 mm"}]})
    assert r.status == 200, r.data
    assert r.data["creados"][1]["formula"] == u"Diámetro perno + 2 mm"
    assert [t.Name for t in familia.FamilyManager.Types] == [familia.OwnerFamily.Name]
    # sin formulas no se crea ningun tipo
    familia.FamilyManager.Types[:] = []
    familia.FamilyManager.CurrentType = None
    r = _post(api, "/family/parameters/", doc, {"family_doc": fd, "parameters": [{"name": u"Ancho", "data_type": "length", "group": "Geometry"}]})
    assert r.status == 200 and familia.FamilyManager.Types == []


def test_load_reintenta_sin_grupo_si_revit_lo_rechaza(api, doc, monkeypatch):
    fd, familia = _familia_completa(api, doc)
    original = mf.DocFamilia.LoadFamily
    llamadas = []

    def load_family(self, proyecto, opciones=None):
        llamadas.append(proyecto.transacciones[-1].estado)
        if len(llamadas) == 1:
            raise Exception("The document must not be modifiable before calling LoadFamily.")
        return original(self, proyecto, opciones)

    monkeypatch.setattr(mf.DocFamilia, "LoadFamily", load_family)
    r = _post(api, "/family/load/", doc, {"family_doc": fd})
    assert r.status == 200, r.data
    assert r.data["ok"] is True and len(llamadas) == 2 and "cargada sin grupo" in r.data["avisos"][0]
    grupo = [t for t in doc.transacciones if isinstance(t, DB.TransactionGroup)][-1]
    assert grupo.estado == DB.TransactionStatus.RolledBack


def test_types_rechaza_formula_aunque_revit_no_la_marque(api, doc):
    """Revit 2027 (validacion 2c con 0.6.3, paso 13): IsDeterminedByFormula no delato la formula y
    FamilyManager.Set dejo 22 mm sin lanzar; la ruta respondio 200 con coincide: true."""
    fd, familia = _familia_completa(api, doc)
    param = familia.FamilyManager.get_Parameter(u"Diámetro agujero")
    param.IsDeterminedByFormula = False
    r = _post(api, "/family/types/", doc, {"family_doc": fd, "types": [{"type_name": u"PL300x300x20", "values": {u"Diámetro agujero": 30}}]})
    assert r.status == 400 and "formula" in r.data["error"] and r.data["formula"] == u"Diámetro perno + 2 mm"


def test_types_detecta_set_ignorado(api, doc, monkeypatch):
    fd, familia = _familia_completa(api, doc)
    monkeypatch.setattr(familia.FamilyManager, "Set", lambda param, valor: True)   # Revit no cambia nada
    r = _post(api, "/family/types/", doc, {"family_doc": fd, "types": [{"type_name": u"PL300x300x20", "values": {u"Ancho": 350}}]})
    assert r.status == 200
    assert r.data["ok"] is False and r.data["verificacion"]["coincide"] is False
    assert r.data["fallidos"][0]["parameter"] == u"Ancho" and "350" in r.data["fallidos"][0]["motivo"]
