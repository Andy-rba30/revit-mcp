# -*- coding: utf-8 -*-
"""Bloque 1 de la consolidacion (0.4.0): tools/ pasa de 78 a 40 herramientas.

- Se registran exactamente las 40 de tools.HERRAMIENTAS en un MCPServer real y
  ninguna de las 51 retiradas.
- Llamar a un nombre retirado responde con la herramienta sustituta (ToolError,
  que el SDK devuelve como resultado is_error), no con "Unknown tool".
- Cada descripcion es corta (< 60 palabras antes de Args) y trae un ejemplo.
- Las herramientas que combinan varias rutas despachan a las rutas de 0.3.x
  correctas (puente simulado que registra las llamadas) y anaden ms_puente.
"""
import asyncio
import io
import json
import os
import re

import pytest

import tools
from tools import HERRAMIENTAS, HERRAMIENTAS_RETIRADAS, mensaje_retirada, instalar_retiradas, register_tools
from tools.utils import format_response


# ---------------------------------------------------------------------------
# Puente simulado
# ---------------------------------------------------------------------------
class Puente(object):
    """revit_get / revit_post / revit_image simulados: registran las llamadas y responden
    con lo que haya en `respuestas[ruta]` (dict, texto o funcion(data))."""

    def __init__(self, respuestas=None):
        self.respuestas = dict(respuestas or {})
        self.llamadas = []

    def _responder(self, metodo, ruta, data=None, params=None, timeout=None):
        self.llamadas.append((metodo, ruta, data if data is not None else params, timeout))
        valor = self.respuestas.get(ruta, {"status": "success", "ruta": ruta})
        if callable(valor):
            valor = valor(data if data is not None else params)
        return json.loads(json.dumps(valor)) if isinstance(valor, (dict, list)) else valor

    async def get(self, ruta, ctx=None, timeout=None, params=None):
        return self._responder("GET", ruta, params=params, timeout=timeout)

    async def post(self, ruta, data, ctx=None, timeout=None):
        return self._responder("POST", ruta, data=data, timeout=timeout)

    async def image(self, ruta, ctx=None):
        self.llamadas.append(("IMAGE", ruta, None, None))
        return "IMAGEN:" + ruta

    def rutas(self):
        return [ruta for _, ruta, _, _ in self.llamadas]


class _Captura(object):
    """mcp simulado: guarda las funciones decoradas con @mcp.tool()."""

    def __init__(self):
        self.funciones = {}

    def tool(self):
        def decorador(funcion):
            self.funciones[funcion.__name__] = funcion
            return funcion
        return decorador


def _herramientas(puente):
    captura = _Captura()
    register_tools(captura, puente.get, puente.post, puente.image)
    return captura.funciones


def _llamar(funciones, nombre, **kwargs):
    texto = asyncio.run(funciones[nombre](**kwargs))
    try:
        return json.loads(texto)
    except (TypeError, ValueError):
        return texto


@pytest.fixture
def servidor():
    from mcp.server.mcpserver import MCPServer

    puente = Puente()
    mcp = MCPServer("prueba")
    register_tools(mcp, puente.get, puente.post, puente.image)
    return mcp, puente


# ---------------------------------------------------------------------------
# Registro y nombres retirados
# ---------------------------------------------------------------------------
def test_se_registran_todas_y_ninguna_retirada(servidor):
    mcp, _ = servidor
    nombres = sorted(t.name for t in mcp._tool_manager.list_tools())
    assert nombres == sorted(HERRAMIENTAS)
    assert len(HERRAMIENTAS) == 42
    assert not set(nombres) & set(HERRAMIENTAS_RETIRADAS)
    assert len(HERRAMIENTAS_RETIRADAS) == 51
    # cada sustituta empieza por una herramienta registrada
    for viejo, sustituta in HERRAMIENTAS_RETIRADAS.items():
        primera = re.match(r"[a-z_]+", sustituta).group(0)
        assert primera in HERRAMIENTAS, (viejo, sustituta)


def test_nombre_retirado_responde_con_la_sustituta(servidor):
    from mcp.server.mcpserver.exceptions import ToolError

    mcp, _ = servidor
    assert getattr(mcp._tool_manager.call_tool, "_retiradas_objetivo", None) == "_tool_manager.call_tool"
    with pytest.raises(ToolError) as info:
        asyncio.run(mcp._tool_manager.call_tool("set_parameter", {"element_id": 1}, None))
    assert "set_parameters(" in str(info.value) and "0.4.0" in str(info.value)
    with pytest.raises(ToolError) as info:
        asyncio.run(mcp._tool_manager.call_tool("get_revit_view", {}, None))
    assert "capture_view" in str(info.value)
    # un nombre que nunca existio sigue siendo "Unknown tool"
    with pytest.raises(ToolError) as info:
        asyncio.run(mcp._tool_manager.call_tool("no_existe", {}, None))
    assert "Unknown tool" in str(info.value)
    assert mensaje_retirada("find_elements").startswith("La herramienta 'find_elements' se retiro en 0.4.0")
    # instalar dos veces no envuelve dos veces
    assert instalar_retiradas(mcp) == "_tool_manager.call_tool"


def test_query_elements_acepta_lista_de_categorias_en_el_esquema(servidor):
    """0.4.1: la descripcion decia que category acepta una lista, pero el tipo era
    str y la validacion del servidor MCP la rechazaba antes de llegar a Revit."""
    mcp, puente = servidor
    asyncio.run(mcp._tool_manager.call_tool(
        "query_elements", {"category": ["OST_Walls", "OST_Floors"], "page_size": 5}, None))
    _, ruta, datos, _ = puente.llamadas[-1]
    assert ruta == "/query/" and datos["category"] == ["OST_Walls", "OST_Floors"]


def test_las_herramientas_registradas_siguen_llamandose(servidor):
    mcp, puente = servidor
    puente.respuestas["/status/"] = {"status": "active", "health": "healthy", "document_title": "Casa"}
    # la envoltura deja pasar los nombres registrados al ToolManager original
    resultado = asyncio.run(mcp._tool_manager.call_tool("get_revit_status", {}, None))
    assert puente.rutas() == ["/status/"]
    assert "Document: Casa" in str(resultado)


def test_descripciones_cortas_con_ejemplo(servidor):
    mcp, _ = servidor
    for herramienta in mcp._tool_manager.list_tools():
        resumen = herramienta.description.split("Args:")[0]
        palabras = len(resumen.split())
        assert 0 < palabras < 60, (herramienta.name, palabras)
        assert "Example:" in resumen or "example" in resumen.lower(), herramienta.name


# ---------------------------------------------------------------------------
# format_response con ms_puente
# ---------------------------------------------------------------------------
def test_format_response_anade_ms_puente():
    datos = json.loads(format_response({"ok": True, "ms": 12}, ms_puente=34))
    assert datos["ms"] == 12 and datos["ms_puente"] == 34
    assert format_response("Revit no está abierto", ms_puente=5) == "Revit no está abierto"
    texto = format_response({"error": "x", "http_status": 400, "fallidos": [{"a": 1}]}, ms_puente=7)
    assert "Fallidos" in texto and "=== ERROR DETAILS ===" in texto


# ---------------------------------------------------------------------------
# Despacho de las herramientas combinadas
# ---------------------------------------------------------------------------
def test_get_revit_model_info_include():
    puente = Puente({"/model_info/": {"file": {"path": "C:\\m.rvt"}, "status": "success"},
                     "/list_levels/": {"levels": [{"name": u"Nivel 1"}]},
                     "/project_location/": {"true_north_deg": 3.0}})
    f = _herramientas(puente)
    datos = _llamar(f, "get_revit_model_info", include=["levels", "location", "raro"])
    assert puente.rutas() == ["/model_info/", "/list_levels/", "/project_location/"]
    assert datos["levels"]["levels"][0]["name"] == u"Nivel 1" and datos["location"]["true_north_deg"] == 3.0
    assert "raro" in datos["warnings"][0] and "ms_puente" in datos
    puente.llamadas = []
    _llamar(f, "get_revit_model_info")
    assert puente.rutas() == ["/model_info/"]


def test_list_views_tipo_activa_y_on_sheet():
    vistas = {"views_by_type": {"floor_plans": [u"Planta 1", u"Planta 2"], "sections": [u"Sección A"], "other": []},
              "total_exportable_views": 3, "status": "success"}
    consulta = {"elements": [
        {"id": 1, "nombre": u"Planta 1", "fields": {u"Número de plano": u"E-101"}},
        {"id": 2, "nombre": u"Planta 2", "fields": {u"Número de plano": u"---"}},
        {"id": 3, "nombre": u"Sección A", "fields": {u"Número de plano": u""}},
    ], "truncated": False}
    puente = Puente({"/list_views/": vistas, "/query/": consulta, "/current_view_info/": {"view_info": {"view_id": 9}}})
    f = _herramientas(puente)
    datos = _llamar(f, "list_views", view_type="floor_plan")
    assert datos["views_by_type"] == {"floor_plans": [u"Planta 1", u"Planta 2"]} and datos["total_exportable_views"] == 2
    datos = _llamar(f, "list_views", on_sheet=False)
    assert datos["views_by_type"] == {"floor_plans": [u"Planta 2"], "sections": [u"Sección A"], "other": []}
    datos = _llamar(f, "list_views", on_sheet=True)
    assert datos["views_by_type"]["floor_plans"] == [u"Planta 1"] and datos["sheet_numbers"] == {u"Planta 1": u"E-101"}
    assert puente.llamadas[-1][2]["fields"] == ["VIEWER_SHEET_NUMBER"] and puente.llamadas[-1][2]["category"] == "OST_Views"
    puente.llamadas = []
    datos = _llamar(f, "list_views", current_only=True)
    assert puente.rutas() == ["/current_view_info/"] and datos["view_info"]["view_id"] == 9
    assert "desconocido" in _llamar(f, "list_views", view_type="rara")


def test_describe_view_sin_id_usa_la_vista_activa():
    puente = Puente({"/current_view_info/": {"view_info": {"view_id": 100, "view_name": u"Planta", "view_family_type": u"Plano de planta"}},
                     "/view_extents/": lambda data: {"view_id": data.get("view_id"), "scale": 100}})
    f = _herramientas(puente)
    datos = _llamar(f, "describe_view")
    assert puente.rutas() == ["/current_view_info/", "/view_extents/"]
    assert datos["view_id"] == 100 and datos["is_active"] is True and datos["view_family_type"] == u"Plano de planta"
    puente.llamadas = []
    datos = _llamar(f, "describe_view", view_id=5)
    assert puente.rutas() == ["/view_extents/"] and "is_active" not in datos


def test_capture_view_devuelve_la_imagen():
    puente = Puente()
    f = _herramientas(puente)
    assert asyncio.run(f["capture_view"](view_name=u"Planta 1")) == u"IMAGEN:/get_view/Planta 1"


def test_query_elements_vista_activa_y_seleccion():
    puente = Puente({"/current_view_info/": {"view_info": {"view_id": 77}}, "/query/": lambda d: {"data": d},
                     "/selected_elements/": {"elements": [], "count": 0}})
    f = _herramientas(puente)
    datos = _llamar(f, "query_elements", category="walls", current_view=True)
    assert puente.rutas() == ["/current_view_info/", "/query/"] and datos["data"]["view_id"] == 77
    assert datos["data"]["category"] == "walls" and "level" not in datos["data"]
    puente.llamadas = []
    datos = _llamar(f, "query_elements", selected=True)
    assert puente.rutas() == ["/selected_elements/"] and datos["count"] == 0
    puente.llamadas = []
    _llamar(f, "query_elements", view_id=3, current_view=True)
    assert puente.rutas() == ["/query/"]


def test_describe_element_uno_o_varios():
    puente = Puente({"/describe/": lambda d: {"error": "not found"} if d["element_id"] == 99 else {"id": d["element_id"], "depth": d["depth"]}})
    f = _herramientas(puente)
    datos = _llamar(f, "describe_element", element_id=10, depth=1)
    assert datos["id"] == 10 and datos["depth"] == 1 and "ms_puente" in datos
    datos = _llamar(f, "describe_element", element_ids=[10, 99, 11], include_geometry=True)
    assert sorted(datos["elements"]) == ["10", "11"] and datos["count"] == 2 and "99" in datos["errors"]
    assert puente.llamadas[-1][2]["include_geometry"] is True
    texto = _llamar(f, "describe_element", element_ids=list(range(21)))
    assert "at most 20" in texto
    assert "required" in _llamar(f, "describe_element")


def test_list_types_tres_caminos():
    puente = Puente({"/element_types/": {"types": [{"tipo": "A", "ejemplares": 0}, {"tipo": "B", "ejemplares": 2}], "count": 2},
                     "/list_category_parameters/": {"parameters": ["Marca"]},
                     "/list_families/": {"families": [{"type_name": "X", "is_active": False}, {"type_name": "Y", "is_active": True}], "count": 2},
                     "/list_family_categories/": {"categories": {u"Puertas": 3}}})
    f = _herramientas(puente)
    datos = _llamar(f, "list_types", category="OST_Walls", with_parameters=True, loaded_only=True)
    assert puente.rutas() == ["/element_types/", "/list_category_parameters/"]
    assert [t["tipo"] for t in datos["types"]] == ["B"] and datos["category_parameters"]["parameters"] == ["Marca"]
    assert puente.llamadas[1][2] == {"category_name": "OST_Walls"}
    puente.llamadas = []
    datos = _llamar(f, "list_types", contains="HEB", loaded_only=True)
    assert puente.rutas() == ["/list_families/"] and [x["type_name"] for x in datos["families"]] == ["Y"]
    assert puente.llamadas[0][2] == {"contains": "HEB", "limit": "200"}
    puente.llamadas = []
    datos = _llamar(f, "list_types")
    assert puente.rutas() == ["/list_family_categories/"] and datos["categories"] == {u"Puertas": 3}


def test_analyze_model_incluye_bloques():
    puente = Puente({"/model_statistics/": {"total_elements": 5}, "/material_quantities/": lambda d: {"materials": d}})
    f = _herramientas(puente)
    datos = _llamar(f, "analyze_model")
    assert puente.rutas() == ["/model_statistics/"] and datos["total_elements"] == 5
    puente.llamadas = []
    datos = _llamar(f, "analyze_model", include=["statistics", "materials"], categories=["walls"])
    assert puente.rutas() == ["/model_statistics/", "/material_quantities/"]
    assert datos["statistics"]["total_elements"] == 5 and datos["materials"]["materials"]["categories"] == ["walls"]
    assert puente.llamadas[1][3] == 600.0


def test_set_parameters_formas_de_llamada():
    puente = Puente({"/set_parameters/": lambda d: {"data": d}})
    f = _herramientas(puente)
    datos = _llamar(f, "set_parameters", element_ids=[1, 2], parameters={"Comments": "x"}, type_parameters=True)
    assert datos["data"]["changes"] == [{"element_ids": [1, 2], "parameters": {"Comments": "x"}}]
    assert datos["data"]["type_parameters"] is True and datos["data"]["simular"] is False
    datos = _llamar(f, "set_parameters", element_id=5, parameter_name="Mark", value="M-1", simular=True)
    assert datos["data"]["changes"] == [{"element_id": 5, "parameter_name": "Mark", "value": "M-1"}]
    assert datos["data"]["simular"] is True
    assert "changes is required" in _llamar(f, "set_parameters")


def test_create_elements_y_transform_array():
    puente = Puente({"/create_elements/": lambda d: {"data": d}, "/transform_elements/": lambda d: {"data": d}})
    f = _herramientas(puente)
    datos = _llamar(f, "create_elements", elements=[{"kind": "wall"}], simular=True)
    assert datos["data"]["elements"] == [{"kind": "wall"}] and puente.llamadas[-1][3] == 120.0
    _llamar(f, "create_elements", elements=[{"kind": "toposolid"}])
    assert puente.llamadas[-1][3] == 600.0
    datos = _llamar(f, "transform_elements", element_ids=[1], operation="array", vector={"x": 1000, "y": 0, "z": 0}, count=3)
    assert datos["data"]["count"] == 3 and datos["data"]["operation"] == "array" and "angle" not in datos["data"]


def test_annotate_despacha_por_kind():
    puente = Puente({"/create_dimensions/": lambda d: {"data": d}, "/tag_elements/": lambda d: {"data": d},
                     "/tag_walls/": lambda d: {"data": d}})
    f = _herramientas(puente)
    datos = _llamar(f, "annotate", kind="dimension", element_ids=[1, 2], dimension_type="aligned")
    assert puente.rutas() == ["/create_dimensions/"] and datos["data"]["dimension_type"] == "aligned"
    datos = _llamar(f, "annotate", kind="tag", element_ids=[1], add_leader=True, offset={"x": 10, "y": 0})
    assert puente.rutas()[-1] == "/tag_elements/" and datos["data"]["add_leader"] is True and datos["data"]["offset"] == {"x": 10, "y": 0}
    datos = _llamar(f, "annotate", kind="tag", category="OST_Walls", tag_type_name="Etiqueta muro")
    assert puente.rutas()[-1] == "/tag_walls/" and datos["data"] == {"use_leader": False, "tag_type_name": "Etiqueta muro", "simular": False}
    assert "element_ids is required" in _llamar(f, "annotate", kind="dimension")
    assert "needs element_ids" in _llamar(f, "annotate", kind="tag")
    assert "not supported" in _llamar(f, "annotate", kind="texto")


def test_color_elements_y_maintain_model():
    puente = Puente({"/color_splash/": lambda d: {"data": d}, "/clear_colors/": lambda d: {"data": d},
                     "/purge_unused/": lambda d: {"data": d}, "/backup/": lambda d: {"data": d},
                     "/save_document/": lambda d: {"data": d}})
    f = _herramientas(puente)
    datos = _llamar(f, "color_elements", category_name="Walls", parameter_name="Mark", custom_colors=["#FF0000"])
    assert puente.rutas()[-1] == "/color_splash/" and datos["data"]["custom_colors"] == ["#FF0000"]
    datos = _llamar(f, "color_elements", category_name="Walls", clear=True)
    assert puente.rutas()[-1] == "/clear_colors/" and datos["data"] == {"category_name": "Walls", "simular": False}
    assert "parameter_name is required" in _llamar(f, "color_elements", category_name="Walls")
    datos = _llamar(f, "maintain_model", action="purge", simular=True, forzar=True)
    assert puente.rutas()[-1] == "/purge_unused/" and datos["data"] == {"max_rounds": 3, "simular": True, "forzar": True}
    datos = _llamar(f, "maintain_model", action="backup", suffix="antes")
    assert puente.rutas()[-1] == "/backup/" and datos["data"] == {"simular": False, "suffix": "antes"}
    datos = _llamar(f, "maintain_model", action="save", file_path="C:\\m.rvt")
    assert puente.rutas()[-1] == "/save_document/" and datos["data"]["file_path"] == "C:\\m.rvt"
    assert "not supported" in _llamar(f, "maintain_model", action="rezar")


def test_export_formatos(tmp_path):
    habitaciones = {"rooms": [{"name": u"Salón", "number": "101", "area_sqm": 20.5}, {"name": u"Baño", "number": "102", "area_sqm": None}], "count": 2}
    puente = Puente({"/export_document/": lambda d: {"data": d}, "/export_ifc/": lambda d: {"data": d}, "/room_data/": habitaciones})
    f = _herramientas(puente)
    datos = _llamar(f, "export", format="PDF", view_name="E-101")
    assert puente.rutas()[-1] == "/export_document/" and datos["data"]["format"] == "pdf"
    assert "file_path is required" in _llamar(f, "export", format="ifc")
    datos = _llamar(f, "export", format="ifc", file_path="C:\\m.ifc", ifc_version="IFC4")
    assert puente.rutas()[-1] == "/export_ifc/" and datos["data"]["ifc_version"] == "IFC4"
    datos = _llamar(f, "export", format="rooms_json")
    assert puente.rutas()[-1] == "/room_data/" and datos["count"] == 2
    datos = _llamar(f, "export", format="rooms_csv")
    assert datos["rows"] == 2 and datos["csv"].splitlines()[0] == "name,number,area_sqm" and u"Salón,101,20.5" in datos["csv"]
    ruta = str(tmp_path / "habitaciones.csv")
    datos = _llamar(f, "export", format="rooms_csv", file_path=ruta)
    assert datos["file_path"] == ruta and os.path.isfile(ruta)
    with io.open(ruta, encoding="utf-8") as archivo:
        assert u"Baño,102," in archivo.read()
    assert "not supported" in _llamar(f, "export", format="xls")


def test_macros_de_usuario_y_codigo():
    puente = Puente({"/macros/": {"macros": []}, "/macros/run/": lambda d: {"data": d}, "/execute_code/": lambda d: {"data": d}})
    f = _herramientas(puente)
    assert _llamar(f, "list_macros")["macros"] == []
    datos = _llamar(f, "run_macro", name="numerar_planos", args={"prefix": "E-"}, simular=True, timeout_s=30)
    assert datos["data"] == {"name": "numerar_planos", "args": {"prefix": "E-"}, "simular": True, "forzar": False}
    assert puente.llamadas[-1][3] == 30.0
    _llamar(f, "run_macro", name="x")
    assert puente.llamadas[-1][3] == 600.0 and puente.llamadas[-1][2]["args"] == {}
    datos = _llamar(f, "execute_revit_code", code="print(1)", description="prueba")
    assert datos["data"]["description"] == "prueba"


def test_set_project_location_acepta_forzar_y_snapshot_include_bbox():
    puente = Puente({"/set_project_location/": lambda d: {"data": d}, "/snapshot/": lambda d: {"data": d}})
    f = _herramientas(puente)
    datos = _llamar(f, "set_project_location", true_north_deg=1.5, forzar=True)
    assert datos["data"] == {"simular": False, "forzar": True, "true_north_deg": 1.5}
    datos = _llamar(f, "snapshot_model", name="a", include_bbox=False)
    assert datos["data"]["include_bbox"] is False and datos["data"]["include_parameters"] is True


# ---------------------------------------------------------------------------
# 0.5.0 (entrega 2b): estructuras metalicas
# ---------------------------------------------------------------------------
def test_describe_element_include_structural_y_list_types_connections():
    puente = Puente({"/describe/": lambda d: {"data": d}, "/element_types/": lambda d: {"types": [], "count": 0, "category": d["category"]},
                     "/list_category_parameters/": {"parameters": []}})
    f = _herramientas(puente)
    datos = _llamar(f, "describe_element", element_id=10, include_structural=True)
    assert datos["data"]["include_structural"] is True
    datos = _llamar(f, "describe_element", element_id=10)
    assert "include_structural" not in datos["data"]
    puente.llamadas = []
    datos = _llamar(f, "list_types", category="connections", with_parameters=True)
    # con connections no se piden los parametros de categoria (no hay BuiltInCategory)
    assert puente.rutas() == ["/element_types/"] and datos["category"] == "connections"
    puente.llamadas = []
    _llamar(f, "list_types", category="OST_Walls", with_parameters=True)
    assert puente.rutas() == ["/element_types/", "/list_category_parameters/"]


def test_list_steel_profiles_y_steel_quantities():
    puente = Puente({"/steel_profiles/": lambda d: {"data": d}, "/steel_quantities/": lambda d: {"data": d}})
    f = _herramientas(puente)
    datos = _llamar(f, "list_steel_profiles")
    assert datos["data"] == {"standard": "todos", "loaded_only": True} and puente.llamadas[-1][3] == 30.0
    datos = _llamar(f, "list_steel_profiles", standard="EN", shape="W", loaded_only=False)
    assert datos["data"] == {"standard": "EN", "loaded_only": False, "shape": "W"} and puente.llamadas[-1][3] == 600.0
    assert "not supported" in _llamar(f, "list_steel_profiles", standard="DIN")
    assert "not supported" in _llamar(f, "list_steel_profiles", shape="Z")
    datos = _llamar(f, "steel_quantities", group_by="level", element_ids=[1, 2])
    assert datos["data"] == {"group_by": "level", "max": 2000, "element_ids": [1, 2]}
    datos = _llamar(f, "steel_quantities")
    assert datos["data"] == {"group_by": "type", "max": 2000}
    assert "not supported" in _llamar(f, "steel_quantities", group_by="peso")
