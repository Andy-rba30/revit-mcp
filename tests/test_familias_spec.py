# -*- coding: utf-8 -*-
"""Entrega 2c (0.6.0), bloque 4: macros del editor de familias (familias_spec) sobre el modelo simulado.

  POST /family/validate/   un TransactionGroup por caso (RollBack con restore), solidos sin volumen y errores de
                           regeneracion como casos fallidos
  POST /family/build/      validacion del spec completo antes de abrir nada (plantilla inexistente, planos no
                           definidos, formulas con parametros no definidos, tipos con parametros desconocidos...),
                           plan con simular, construccion real con guardado y carga, y cierre sin guardar si falla
"""
import copy
import os

import pytest
from pyrevit import DB

import modelo_falso as mf
from test_familias import (  # noqa: F401 (fixtures)
    doc, api, _post, _transacciones, _abrir, _parametros_base, _planos_base, _familia_completa, PLACA, AGUJERO, PLANTILLA,
)


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------
def test_family_validate_por_tipo_y_casos(api, doc):
    fd, familia = _familia_completa(api, doc)
    ruta = "/family/validate/"
    assert _post(api, ruta, doc, {"family_doc": fd}, con_token=False).status == 401
    r = _post(api, ruta, doc, {"family_doc": fd, "flex_cases": [{"name": "x", "type": u"PL999"}]})
    assert r.status == 404 and u"PL300x300x20" in r.data["available_types"]
    r = _post(api, ruta, doc, {"family_doc": fd, "flex_cases": [{"name": "x", "values": {u"Alto": 1}}]})
    assert r.status == 404
    grupos_antes = len([t for t in DB.Transaction.creadas if isinstance(t, DB.TransactionGroup)])
    r = _post(api, ruta, doc, {"family_doc": fd, "simular": True})
    assert r.status == 200 and r.data["simulado"] is True and [h["type"] for h in r.data["haria"]] == [u"Placa base", u"PL300x300x20", u"PL400x400x25"]
    assert len(r.data["solids"]) == 2
    r = _post(api, ruta, doc, {"family_doc": fd})
    assert r.status == 200, r.data
    assert r.data["ok"] is True and r.data["count"] == 3 and r.data["passed"] == 3 and r.data["failed_cases"] == []
    assert r.data["cases"][1]["type"] == u"PL300x300x20" and r.data["cases"][1]["solids"][0]["volume_m3"] > 0 and r.data["cases"][1]["restored"] is True
    grupos = [t for t in DB.Transaction.creadas if isinstance(t, DB.TransactionGroup)][grupos_antes:]
    assert [g.nombre for g in grupos] == [u"IA: Validar Placa base", u"IA: Validar PL300x300x20", u"IA: Validar PL400x400x25"]
    assert all(g.estado == DB.TransactionStatus.RolledBack for g in grupos)
    assert familia.FamilyManager.CurrentType.Name == u"PL400x400x25"

    # un caso extremo que deja la placa sin volumen y otro que Revit rechaza (error de regeneracion)
    def al_regenerar(d):
        espesor = d.FamilyManager.CurrentType.valores.get(u"Espesor") or 0
        for forma in d.elementos.values():
            if isinstance(forma, DB.Extrusion) and forma.IsSolid:
                forma.volumen = 1.0 if espesor > 0 else 0.0
        if (d.FamilyManager.CurrentType.valores.get(u"Ancho") or 0) < 0:
            raise Exception("Revit: Constraints are not satisfied")

    familia.al_regenerar = al_regenerar
    r = _post(api, ruta, doc, {"family_doc": fd, "flex_cases": [
        {"name": u"normal", "type": u"PL300x300x20", "values": {u"Espesor": 30}},
        {"name": u"sin espesor", "type": u"PL300x300x20", "values": {u"Espesor": 0}},
        {"name": u"ancho negativo", "type": u"PL400x400x25", "values": {u"Ancho": -100}}], "restore": True})
    assert r.status == 200, r.data
    assert r.data["ok"] is False and r.data["passed"] == 1 and r.data["failed"] == 2
    assert r.data["cases"][1]["empty_solids"] and "no volume" in r.data["cases"][1]["motivo"]
    assert "Constraints" in r.data["cases"][2]["motivo"] and r.data["cases"][2]["values"] == {u"Ancho": u"-100 mm"}
    assert [c["name"] for c in r.data["failed_cases"]] == [u"sin espesor", u"ancho negativo"]
    assert r.data["verificacion"]["coincide"] is False




# ---------------------------------------------------------------------------
# build_family_from_spec
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
def spec_placa():
    return {
        "name": u"Placa base MCP",
        "template": PLANTILLA,
        "category": "OST_StructConnections",
        "parameters": [
            {"name": u"Ancho", "data_type": "length", "group": "Geometry"},
            {"name": u"Largo", "data_type": "length", "group": "Geometry"},
            {"name": u"Espesor", "data_type": "length", "group": "Geometry"},
            {"name": u"Diámetro perno", "data_type": "length", "group": "Geometry"},
            {"name": u"Diámetro agujero", "data_type": "length", "group": "Geometry", "formula": u"Diámetro perno + 2 mm"},
            {"name": u"Material placa", "data_type": "material", "group": "Materials"},
        ],
        "reference_planes": [
            {"name": u"Izquierda", "origin_mm": {"x": -150, "y": 0, "z": 0}, "direction": "y", "is_reference": "left"},
            {"name": u"Derecha", "origin_mm": {"x": 150, "y": 0, "z": 0}, "direction": "y", "is_reference": "right"},
            {"name": u"Delante", "origin_mm": {"x": 0, "y": -150, "z": 0}, "direction": "x", "is_reference": "front"},
            {"name": u"Detrás", "origin_mm": {"x": 0, "y": 150, "z": 0}, "direction": "x", "is_reference": "back"},
            {"name": u"Cara superior", "origin_mm": {"x": 0, "y": 0, "z": 20}, "direction": "horizontal", "is_reference": "top"},
        ],
        "dimensions": [
            {"reference_planes": [u"Izquierda", u"Derecha"], "parameter": u"Ancho"},
            {"reference_planes": [u"Delante", u"Detrás"], "parameter": u"Largo"},
        ],
        "solids": [copy.deepcopy(PLACA)] + [dict(AGUJERO, name=u"agujero {}".format(i), profile={"circle": {"center_mm": {"x": x, "y": y, "z": 0}, "radius_mm": 11}})
                             for i, (x, y) in enumerate(((100, 100), (-100, 100), (100, -100), (-100, -100)), 1)],
        "types": [
            {"name": u"PL300x300x20", "values": {u"Ancho": 300, u"Largo": 300, u"Espesor": 20, u"Diámetro perno": 20}},
            {"name": u"PL400x400x25", "values": {u"Ancho": 400, u"Largo": 400, u"Espesor": 25, u"Diámetro perno": 24}},
        ],
    }


@pytest.mark.parametrize("cambio, seccion, texto", [
    (lambda s: s.update(template=u"Metric Generic Model.rft"), "template", "not found"),
    (lambda s: s.__setitem__("parameters", s["parameters"][:4] + [s["parameters"][4]]), "parameters", "undefined parameters"),
    (lambda s: s["dimensions"].append({"reference_planes": [u"Izquierda", u"Arriba"], "parameter": u"Ancho"}), "dimensions", "Arriba"),
    (lambda s: s["dimensions"].append({"reference_planes": [u"Izquierda", u"Derecha"], "parameter": u"Alto"}), "dimensions", "Alto"),
    (lambda s: s["types"].append({"name": u"PL500", "values": {u"Grosor": 5}}), "types", "Grosor"),
    (lambda s: s["solids"][0].update(lock_faces=[{"face": "left", "reference_plane": u"Borde"}]), "solids", "Borde"),
    (lambda s: s["solids"][0].update(material_parameter=u"Material tapa"), "solids", "Material tapa"),
    (lambda s: s["solids"][0].update(sketch_plane=u"Tapa"), "solids", "Tapa"),
    (lambda s: s.__setitem__("connectors", [{"domain": "hvac", "solid": u"tapa", "system_type": "SupplyAir", "size_mm": 100}]), "connectors", "tapa"),
    (lambda s: s.__setitem__("flex_cases", [{"name": "x", "type": u"PL999"}]), "flex_cases", "PL999"),
    (lambda s: s.pop("name"), "name", "name is required"),
    (lambda s: s.update(category="OST_Nada"), "category", "BuiltInCategory"),
])
def test_build_valida_el_spec_antes_de_abrir(api, doc, cambio, seccion, texto):
    spec = spec_placa()
    if seccion == "parameters":
        spec["parameters"][4] = dict(spec["parameters"][4], formula=u"Diámetro tornillo + 2 mm")
    cambio(spec)
    r = _post(api, "/family/build/", doc, {"spec": spec, "simular": True})
    assert r.status in (400, 404), r.data
    assert r.data["spec_error"] is True and r.data["section"] == seccion and texto in r.data["error"], r.data
    assert doc.Application.Documents == []


def test_build_family_from_spec_simulado_y_real(api, doc, tmp_path):
    assert _post(api, "/family/build/", doc, {"spec": spec_placa()}, con_token=False).status == 401
    assert _post(api, "/family/build/", doc, {}).status == 400
    r = _post(api, "/family/build/", doc, {"spec": spec_placa(), "simular": True})
    assert r.status == 200, r.data
    plan = r.data["plan"]
    assert plan["counts"] == {"parameters": 6, "reference_planes": 5, "dimensions": 2, "solids": 1, "voids": 4, "connectors": 0,
                              "types": 2, "flex_cases": 2}
    assert plan["steps"] == ["open", "category", "parameters", "reference_planes", "dimensions", "solids", "types", "validate"]
    assert plan["template"].endswith(PLANTILLA) and r.data["count"] == 20 and doc.Application.Documents == []
    destino = str(tmp_path / u"Placa base MCP.rfa")
    r = _post(api, "/family/build/", doc, {"spec": spec_placa(), "save_path": destino, "load_into_project": True})
    assert r.status == 200, r.data
    datos = r.data
    assert datos["ok"] is True and [p["step"] for p in datos["steps"]] == plan["steps"] + ["save", "load"]
    assert datos["family_doc"] == u"Placa base MCP" and datos["file_path"] == destino and os.path.isfile(destino)
    assert datos["validation"]["passed"] == 2 and datos["validation"]["failed"] == 0
    assert [t["type"] for t in datos["loaded_types"]] == [u"Placa base MCP", u"PL300x300x20", u"PL400x400x25"]
    assert datos["summary"]["category"] == "OST_StructConnections" and datos["summary"]["counts"]["solids"] == 5
    assert datos["summary"]["categoria"] == u"Conexiones estructurales"
    assert datos["avisos"] == []
    familia = doc.Application.Documents[-1]
    assert [t.nombre for t in familia.transacciones if not isinstance(t, DB.TransactionGroup)] == [
        u"IA: Tipo de familia Placa base MCP", u"IA: Categoria de familia", u"IA: Parametros de familia (6)", u"IA: Planos de referencia (5)", u"IA: Cotas con etiqueta (2)",
        u"IA: Solidos de familia (5)", u"IA: Tipos de familia (2)", u"IA: Flexionar PL300x300x20", u"IA: Flexionar PL400x400x25"]
    assert [t.nombre for t in familia.transacciones if isinstance(t, DB.TransactionGroup)] == [
        u"IA: Validar PL300x300x20", u"IA: Validar PL400x400x25"]
    assert isinstance(doc.transacciones[-1], DB.TransactionGroup) and doc.transacciones[-1].nombre == u"IA: Cargar familia Placa base MCP"
    assert doc._familia_por_nombre(u"Placa base MCP") is not None
    # la familia sigue abierta (close no pedido) y se puede cerrar
    r = _post(api, "/family/close/", doc, {"family_doc": u"Placa base MCP"})
    assert r.status == 200 and r.data["closed"] is True
    # segunda vez: 409 (el archivo existe) sin abrir nada
    r = _post(api, "/family/build/", doc, {"spec": spec_placa(), "save_path": destino})
    assert r.status == 409 and len(doc.Application.Documents) == 0


def test_build_cierra_sin_guardar_si_un_paso_falla(api, doc, tmp_path):
    spec = spec_placa()
    spec["types"][1]["values"][u"Espesor"] = 0      # la validacion por tipo falla en el segundo tipo

    def al_regenerar(d):
        espesor = d.FamilyManager.CurrentType.valores.get(u"Espesor") or 0
        for forma in d.elementos.values():
            if isinstance(forma, DB.Extrusion) and forma.IsSolid:
                forma.volumen = 1.0 if espesor > 0 else 0.0

    original = mf.Aplicacion.NewFamilyDocument

    def nueva(self, ruta):
        d = original(self, ruta)
        d.al_regenerar = al_regenerar
        return d

    mf.Aplicacion.NewFamilyDocument = nueva
    try:
        destino = str(tmp_path / "falla.rfa")
        r = _post(api, "/family/build/", doc, {"spec": spec, "save_path": destino})
    finally:
        mf.Aplicacion.NewFamilyDocument = original
    assert r.status == 500, r.data
    assert r.data["failed_step"] == "validate" and r.data["closed_without_saving"] is True
    assert r.data["failed_cases"][0]["type"] == u"PL400x400x25" and [p["step"] for p in r.data["steps"]][-1] == "validate"
    assert not os.path.exists(destino) and doc.Application.Documents == [] and doc._familia_por_nombre(u"Placa base MCP") is None
    import familias

    assert familias.DOCUMENTOS_ABIERTOS == {}


def test_vaciados_con_volumen_negativo_no_cuentan_como_vacios():
    """Revit 2027 da volumen negativo a los vaciados (validacion 2c con 0.6.2, paso 22)."""
    import familias_spec

    assert familias_spec._sin_volumen({"is_void": True, "volume_m3": -1.3e-05}) is False
    assert familias_spec._sin_volumen({"is_void": True, "volume_m3": 0}) is True
    assert familias_spec._sin_volumen({"is_void": False, "volume_m3": 0.0018}) is False
    assert familias_spec._sin_volumen({"is_void": False, "volume_m3": 0}) is True
    assert familias_spec._sin_volumen({"is_void": False, "volume_m3": None}) is True
