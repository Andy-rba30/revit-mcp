# -*- coding: utf-8 -*-
"""Entrega 2b (0.5.0), bloque 3: modelo analitico sobre el modelo simulado.

  POST /analytical_status/   miembro asociado, nodos, conectados / tocando, sueltos; dos miembros cuyos
                             nodos distan menos y mas que la tolerancia; elementos sin modelo analitico
  POST /fix_analytical/      movimientos previstos con simular, SetCurve en una transaccion, antes/despues,
                             sin_objetivo, fallidos cuando SetCurve falla
  POST /export_structural/   csv_nodes_members (una fila por miembro con nodos y liberaciones) e
                             ifc_structural (ExportBaseQuantities y la vista analitica activa como filtro)

Reutiliza el modelo de tests/test_acero.py: vigas 10, 11 y 13, pilar 12 (acero) y viga 14 (hormigon).
"""
import io

import pytest
from pyrevit import DB, routes

import modelo_falso as mf
import seguridad
from test_acero import Viga, TOKEN, BIP, BIC, ST, doc as doc_base  # noqa: F401 (fixture)


@pytest.fixture
def doc(doc_base):
    """Miembros analiticos: m10 (viga 10) unido al pilar m12 y a m13; m11 (viga 11) suelto: su nodo final
    esta a 30 mm del de m13 y el inicial no tiene nada cerca; m15 (viga 15) toca m10 a mitad de vano."""
    d = doc_base
    Viga(d, 15, d.elementos[72], (3000, 0, 3500), (3000, -2000, 3500), 2, u"V-5", volumen_m3=0.01, material_id=41)
    m10 = mf.MiembroAnalitico(d, 510, (0, 0, 3500), (6000, 0, 3500))
    m11 = mf.MiembroAnalitico(d, 511, (0, 5000, 3500), (6000, 5030, 3500))
    m12 = mf.MiembroAnalitico(d, 512, (0, 0, 0), (0, 0, 3500))
    m13 = mf.MiembroAnalitico(d, 513, (6000, 0, 3500), (6000, 5000, 3500))
    m15 = mf.MiembroAnalitico(d, 515, (3000, 0, 3500), (3000, -2000, 3500))
    m10.SetReleaseType(ST.AnalyticalElementSelector.EndOrTop, ST.ReleaseType.Pinned)
    for fisico, analitico in ((10, m10), (11, m11), (12, m12), (13, m13), (15, m15)):
        d.asociar(d.elementos[fisico], analitico)
    return d


@pytest.fixture
def api():
    seguridad.establecer_token(TOKEN)
    DB.Transaction.creadas = []
    DB.Transaction.resultado_commit = DB.TransactionStatus.Committed
    api = routes.API("revit_mcp")
    from analitico import register_analitico_routes
    from interop import register_interop_routes

    register_analitico_routes(api)
    register_interop_routes(api)
    return api


def _post(api, ruta, doc, cuerpo, con_token=True):
    datos = dict(cuerpo)
    if con_token:
        datos["token"] = TOKEN
    return api.rutas[(ruta, "POST")](doc=doc, request=routes.Request(path=ruta, data=datos))


def _transacciones():
    return [t.nombre for t in DB.Transaction.creadas]


def _por_id(datos):
    return dict((e["element_id"], e) for e in datos["elements"])


# ---------------------------------------------------------------------------
# /analytical_status/
# ---------------------------------------------------------------------------
def test_analytical_status_nodos_conectados_tocando_y_sueltos(api, doc):
    assert _post(api, "/analytical_status/", doc, {}, con_token=False).status == 401
    assert _post(api, "/analytical_status/", None, {}).status == 503
    r = _post(api, "/analytical_status/", doc, {"element_ids": [10, 11, 12, 13, 14, 15, 999]})
    assert r.status == 200, r.data
    datos = r.data
    assert datos["count"] == 6 and datos["members"] == 5 and datos["sin_analitico"] == 1 and datos["not_found"] == [999]
    assert datos["tolerance_mm"] == 10.0 and datos["analytical_members_in_model"] == 5 and datos["not_steel"] == [14]
    por_id = _por_id(datos)
    viga = por_id[10]
    assert viga["analytical_member_id"] == 510 and viga["is_connected"] is True and viga["loose_nodes"] == []
    assert viga["nodes"]["start"] == {"point_mm": {"x": 0.0, "y": 0.0, "z": 3500.0}, "connected": [512], "touching": [], "is_connected": True}
    assert viga["nodes"]["end"]["connected"] == [513] and viga["nodes"]["end"]["point_mm"]["x"] == 6000.0
    assert viga["releases"]["end"]["type"] == "pinned" and viga["releases"]["start"]["type"] == "fixed"
    # m13: su nodo final esta a 30 mm del de m11: suelto con la tolerancia de 10 mm
    assert por_id[13]["loose_nodes"] == ["end"] and por_id[13]["nodes"]["start"]["connected"] == [510]
    assert por_id[13]["is_connected"] is False
    # m11: los dos nodos sueltos
    assert por_id[11]["loose_nodes"] == ["start", "end"]
    # m12: la base del pilar no toca nada; la cabeza toca m10
    assert por_id[12]["loose_nodes"] == ["start"] and por_id[12]["nodes"]["end"]["connected"] == [510]
    # m15 arranca a mitad de m10: `touching`, no `connected`
    assert por_id[15]["nodes"]["start"] == {"point_mm": {"x": 3000.0, "y": 0.0, "z": 3500.0}, "connected": [], "touching": [510], "is_connected": True}
    assert por_id[15]["loose_nodes"] == ["end"]
    assert por_id[14] == {"element_id": 14, "categoria": u"Armazón estructural", "tipo": u"30x50", "analytical_member_id": None,
                          "nodes": None, "is_connected": None, "loose_nodes": [], "nota": u"sin modelo analitico asociado"}
    assert datos["loose_nodes_total"] == 5 and datos["connected_members"] == 1
    assert DB.Transaction.creadas == []
    # con 50 mm de tolerancia m11 y m13 quedan conectados por el nodo final
    r = _post(api, "/analytical_status/", doc, {"element_ids": [11, 13], "tolerance_mm": 50})
    por_id = _por_id(r.data)
    assert por_id[13]["is_connected"] is True and por_id[13]["nodes"]["end"]["connected"] == [511]
    assert por_id[11]["loose_nodes"] == ["start"] and por_id[11]["nodes"]["end"]["connected"] == [513]
    assert r.data["loose_nodes_total"] == 1


def test_analytical_status_todo_el_acero_y_errores(api, doc):
    r = _post(api, "/analytical_status/", doc, {})
    assert r.status == 200, r.data
    assert sorted(e["element_id"] for e in r.data["elements"]) == [10, 11, 12, 13, 15]     # la viga de hormigon no entra
    assert r.data["members"] == 5 and r.data["sin_analitico"] == 0
    r = _post(api, "/analytical_status/", doc, {"max": 2})
    assert r.data["count"] == 2 and r.data["truncated"] is True
    assert _post(api, "/analytical_status/", doc, {"tolerance_mm": "cerca"}).status == 400
    assert _post(api, "/analytical_status/", doc, {"tolerance_mm": 0}).status == 400
    assert _post(api, "/analytical_status/", doc, {"element_ids": 10}).status == 400
    doc.asociaciones.clear()
    r = _post(api, "/analytical_status/", doc, {"element_ids": [10]})
    assert r.data["sin_analitico"] == 1 and r.data["members"] == 0


def test_analytical_status_409_sin_api_analitica(api, doc, monkeypatch):
    monkeypatch.delattr(DB.Structure, "AnalyticalToPhysicalAssociationManager")
    r = _post(api, "/analytical_status/", doc, {"element_ids": [10]})
    assert r.status == 409 and r.data["no_soportado"] is True
    r = _post(api, "/fix_analytical/", doc, {"element_ids": [10]})
    assert r.status == 409 and r.data["no_soportado"] is True and DB.Transaction.creadas == []


# ---------------------------------------------------------------------------
# /fix_analytical/
# ---------------------------------------------------------------------------
def test_fix_analytical_mueve_el_nodo_suelto_dentro_de_la_tolerancia(api, doc):
    cuerpo = {"element_ids": [11, 14], "tolerance_mm": 50}
    assert _post(api, "/fix_analytical/", doc, cuerpo, con_token=False).status == 401
    r = _post(api, "/fix_analytical/", doc, dict(cuerpo, simular=True))
    assert r.status == 200, r.data
    datos = r.data
    assert datos["simulado"] is True and datos["count"] == 1 and "copia" not in datos
    assert datos["haria"] == [{"accion": "mover_nodo", "element_id": 11, "analytical_member_id": 511, "end": "end",
                               "from_mm": {"x": 6000.0, "y": 5030.0, "z": 3500.0}, "to_mm": {"x": 6000.0, "y": 5000.0, "z": 3500.0},
                               "distance_mm": 30.0, "target_analytical_id": 513, "target_element_id": 13}]
    assert datos["sin_objetivo"] == [{"element_id": 11, "analytical_member_id": 511, "end": "start",
                                      "point_mm": {"x": 0.0, "y": 5000.0, "z": 3500.0}, "nearest_mm": 5000.0}]
    assert datos["sin_analitico"] == [14]
    assert datos["plan"]["counts"] == {"moves": 1, "members": 1, "already_joined": 0, "without_target": 1, "without_analytical": 1}
    assert DB.Transaction.creadas == []
    r = _post(api, "/fix_analytical/", doc, cuerpo)
    assert r.status == 200, r.data
    datos = r.data
    assert datos["ok"] is True and datos["count"] == 1 and datos["fallidos"] == [] and datos["verificacion"] == {"coincide": True}
    assert _transacciones() == [u"IA: Alinear analitico"]
    assert datos["antes"] == {"11.end": {"x": 6000.0, "y": 5030.0, "z": 3500.0}}
    assert datos["despues"] == {"11.end": {"x": 6000.0, "y": 5000.0, "z": 3500.0}}
    assert datos["moves"][0]["coincide"] is True and datos["copia"]["ruta"].endswith(".rvt")
    curva = doc.elementos[511].GetCurve()
    assert abs(curva.GetEndPoint(1).Y * 304.8 - 5000.0) < 1e-6 and abs(curva.GetEndPoint(0).Y * 304.8 - 5000.0) < 1e-6
    # ahora analytical_status ya no ve suelto ese nodo, y repetir no mueve nada
    r = _post(api, "/analytical_status/", doc, {"element_ids": [11, 13]})
    assert r.data["loose_nodes_total"] == 1 and _por_id(r.data)[13]["is_connected"] is True
    r = _post(api, "/fix_analytical/", doc, cuerpo)
    assert r.status == 200 and r.data["count"] == 0 and r.data["plan"]["counts"]["already_joined"] == 1
    assert len(DB.Transaction.creadas) == 1                                    # sin movimientos no se abre transaccion


def test_fix_analytical_errores_y_fallidos(api, doc, monkeypatch):
    assert _post(api, "/fix_analytical/", doc, {}).status == 400
    assert _post(api, "/fix_analytical/", doc, {"element_ids": [999]}).status == 404
    assert _post(api, "/fix_analytical/", doc, {"element_ids": [11], "tolerance_mm": -1}).status == 400
    # con la tolerancia por defecto de 10 mm no hay nada que mover
    r = _post(api, "/fix_analytical/", doc, {"element_ids": [11], "tolerance_mm": 10})
    assert r.status == 200 and r.data["count"] == 0 and len(r.data["sin_objetivo"]) == 2
    # SetCurve falla: fallidos con motivo, la respuesta no dice exito falso
    monkeypatch.setattr(doc.elementos[511], "SetCurve", lambda curva: (_ for _ in ()).throw(Exception("Revit: analytical curve locked")))
    r = _post(api, "/fix_analytical/", doc, {"element_ids": [11], "tolerance_mm": 50})
    assert r.status == 200, r.data
    assert r.data["count"] == 0 and r.data["fallidos"] == [{"element_id": 11, "analytical_member_id": 511, "end": "end",
                                                            "motivo": u"SetCurve: Revit: analytical curve locked"}]
    assert r.data["moves"] == [] and r.data["ok"] is True
    ids = list(range(2000, 2201))
    r = _post(api, "/fix_analytical/", doc, {"element_ids": ids})
    assert r.status == 404                                                        # el primero no existe


# ---------------------------------------------------------------------------
# /export_structural/
# ---------------------------------------------------------------------------
def test_export_structural_csv_nodos_y_miembros(api, doc, tmp_path):
    ruta = str(tmp_path / "salida" / "miembros.csv")
    assert _post(api, "/export_structural/", doc, {"format": "csv_nodes_members", "file_path": ruta}, con_token=False).status == 401
    r = _post(api, "/export_structural/", doc, {"format": "csv_nodes_members", "file_path": ruta})
    assert r.status == 200, r.data
    assert r.data["rows"] == 5 and r.data["sin_analitico"] == [] and r.data["file_path"] == ruta
    assert r.data["columns"][:2] == ["element_id", "analytical_member_id"] and DB.Transaction.creadas == []
    with io.open(ruta, encoding="utf-8") as archivo:
        lineas = archivo.read().splitlines()
    assert lineas[0] == ",".join(r.data["columns"])
    filas = dict((l.split(",")[0], l.split(",")) for l in lineas[1:])
    fila = filas["10"]
    assert fila[1] == "510" and fila[2] == u"Armazón estructural" and fila[3] == u"IPE" and fila[4] == u"IPE300"
    assert fila[5] == u"Acero S275" and fila[6] == u"Nivel 2"
    assert fila[7:13] == ["0.0", "0.0", "3500.0", "6000.0", "0.0", "3500.0"] and fila[13] == "6000.0"
    assert fila[14:18] == ["fixed", "", "pinned", ""]          # liberaciones por parametro de la viga 10
    assert filas["12"][14] == "fixed" and filas["12"][1] == "512"
    # element_ids explicitos: el hormigon sin miembro analitico sale en sin_analitico y con los nodos vacios
    r = _post(api, "/export_structural/", doc, {"format": "csv_nodes_members", "file_path": ruta, "element_ids": [10, 14, 999]})
    assert r.status == 200 and r.data["rows"] == 2 and r.data["sin_analitico"] == [14] and r.data["not_found"] == [999]
    with io.open(ruta, encoding="utf-8") as archivo:
        fila_14 = [l for l in archivo.read().splitlines() if l.startswith("14,")][0].split(",")
    assert fila_14[1] == "" and fila_14[7:14] == [""] * 7 and fila_14[5] == u"Hormigón HA-25"


def test_export_structural_ifc_con_vista_analitica_activa(api, doc, tmp_path):
    ruta = str(tmp_path / "ifc" / "estructura.ifc")
    cuerpo = {"format": "ifc_structural", "file_path": ruta, "ifc_version": "IFC4"}
    # con una planta normal activa (modelo analitico oculto) se exporta todo el modelo, sin filtro
    doc.ActiveView = mf.VistaPlanta(doc, 122, u"Planta Nivel 2")
    r = _post(api, "/export_structural/", doc, cuerpo)
    assert r.status == 200, r.data
    assert r.data["filter_view"] is None and "no muestra" in r.data["filter_view_reason"]
    assert r.data["export_base_quantities"] is True and r.data["ifc_version"] == "IFC4" and r.data["file_size_kb"] == 0
    assert _transacciones() == [u"IA: Exportar IFC estructural"]
    ruta_escrita, opciones = doc.exportaciones[-1]
    assert ruta_escrita == ruta and opciones.ExportBaseQuantities is True and opciones.FileVersion is DB.IFCVersion.IFC4
    assert opciones.FilterViewId == DB.ElementId.InvalidElementId
    # con una vista 3D analitica activa, se usa como filtro
    vista = mf.Vista3D(doc, 120, u"Analítico 3D")
    vista.AreAnalyticalModelCategoriesHidden = False
    doc.ActiveView = vista
    r = _post(api, "/export_structural/", doc, cuerpo)
    assert r.status == 200 and r.data["filter_view"] == {"id": 120, "name": u"Analítico 3D"}
    assert doc.exportaciones[-1][1].FilterViewId == DB.ElementId(120)
    # view_name explicito
    mf.VistaPlanta(doc, 121, u"Planta estructura")
    r = _post(api, "/export_structural/", doc, dict(cuerpo, view_name=u"Planta estructura"))
    assert r.status == 200 and r.data["filter_view"]["id"] == 121 and r.data["filter_view_reason"] == "view_name"
    assert _post(api, "/export_structural/", doc, dict(cuerpo, view_name=u"No existe")).status == 404
    # la exportacion IFC de 0.4.x sigue funcionando con los helpers extraidos
    r = _post(api, "/export_ifc/", doc, {"file_path": str(tmp_path / "ifc" / "todo.ifc"), "view_name": u"Analítico 3D"})
    assert r.status == 200 and r.data["status"] == "success" and doc.exportaciones[-1][1].FilterViewId == DB.ElementId(120)
    assert doc.exportaciones[-1][1].FileVersion is DB.IFCVersion.IFC2x3


def test_export_structural_errores_controlados(api, doc, tmp_path):
    assert _post(api, "/export_structural/", doc, {"format": "csv_nodes_members"}).status == 400
    r = _post(api, "/export_structural/", doc, {"format": "dxf", "file_path": "x.dxf"})
    assert r.status == 400 and r.data["available_formats"] == ["ifc_structural", "csv_nodes_members"]
    assert _post(api, "/export_structural/", doc, {"format": "ifc_structural", "file_path": str(tmp_path / "a.txt")}).status == 400
    assert _post(api, "/export_structural/", doc, {"format": "csv_nodes_members", "file_path": str(tmp_path / "a.ifc")}).status == 400
    assert _post(api, "/export_structural/", doc, {"format": "csv_nodes_members", "file_path": str(tmp_path / "a.csv"), "element_ids": 10}).status == 400
    assert DB.Transaction.creadas == []


def test_export_structural_ruta_con_tildes_mal_leida_y_diagnostico(api, doc, tmp_path):
    """0.5.1: validacion 2b en Revit 2027: 'Access ... denied' al exportar al Escritorio de 'Andy Bayona Antón'."""
    carpeta = tmp_path / u"Andy Bayona Antón"
    carpeta.mkdir()
    # la ruta llega con el UTF-8 leido como latin-1 ("AntÃ³n"): se usa la carpeta reparada si existe
    mal_leida = str(carpeta / u"miembros.csv").encode("utf-8").decode("latin-1")
    r = _post(api, "/export_structural/", doc, {"format": "csv_nodes_members", "file_path": mal_leida})
    assert r.status == 200, r.data
    assert r.data["file_path"] == str(carpeta / u"miembros.csv") and (carpeta / u"miembros.csv").exists()
    # una carpeta que no se puede crear: 500 con el diagnostico (caracteres no ASCII y la sugerencia)
    (tmp_path / "archivo").write_text("x")
    imposible = str(tmp_path / "archivo" / u"Antón" / "m.csv")
    r = _post(api, "/export_structural/", doc, {"format": "csv_nodes_members", "file_path": imposible})
    assert r.status == 500, r.data
    assert r.data["ruta_recibida"] == imposible and u"ó U+00F3" in r.data["caracteres_no_ascii"]
    assert r.data["posible_ruta_mal_leida"] is False and u"Acceso controlado a carpetas" in r.data["sugerencia"]
