# -*- coding: utf-8 -*-
"""Registro de las herramientas MCP (0.6.0): 66 herramientas en cuatro modulos.

  lectura_tools        20 herramientas de lectura (estado, modelo, vistas, consulta,
                       tipos, tablas, avisos, analisis, colisiones, instantaneas, log;
                       0.5.0: perfiles de acero, cantidades de acero, estado analitico;
                       0.6.0: family_info)
  escritura_tools      39 herramientas de escritura (lotes, transformar, borrar,
                       tipos, uniones, subproyectos, coordenadas, vistas, planos,
                       tablas, anotar, colores, exportar, vincular, familias, MEP,
                       mantenimiento; 0.5.0: cargar perfiles, portico metalico,
                       arriostres, cerchas, propiedades estructurales, conexiones,
                       placas, dividir viga, alinear el modelo analitico; 0.6.0: editor
                       de familias: abrir, parametros, planos, cotas, solidos, bloqueos,
                       tipos, conectores, guardar, cargar, cerrar)
  macro_tools           6 macros (rejilla y niveles, Civil 3D, macros propias;
                       0.6.0: family_validate, build_family_from_spec)
  code_execution_tools  1 (execute_revit_code, ultimo recurso)

La consolidacion de 0.4.0 (de 78 a 40 herramientas) ocurre aqui, en el puente:
las rutas HTTP de revit_mcp/ que usaban las herramientas retiradas siguen
existiendo. HERRAMIENTAS_RETIRADAS dice por que herramienta sustituir cada
nombre viejo; instalar_retiradas() hace que el servidor MCP responda con ese
mensaje (resultado is_error, sin traza) si un cliente llama a un nombre
retirado, en vez del "Unknown tool" generico del SDK.
"""

import logging

logger = logging.getLogger(__name__)

# Las 66 herramientas de 0.6.0: las 40 de 0.4.0 (39 sin capture_view, que se
# mantiene aparte porque devuelve una imagen y no un texto JSON), las 12 de la
# entrega 2b (estructuras metalicas y modelo analitico) y las 14 de la entrega
# 2c (editor de familias).
HERRAMIENTAS = (
    # lectura (16 + 2 de 0.5.0 + 1 de 0.6.0)
    "get_revit_status", "get_revit_model_info", "list_views", "describe_view", "capture_view",
    "query_elements", "describe_element", "dependency_graph", "list_types", "schedule_to_json",
    "list_warnings", "analyze_model", "check_clashes", "snapshot_model", "diff_snapshots", "read_log",
    "list_steel_profiles", "steel_quantities", "analytical_status",
    "family_info",
    # escritura (19 + 8 de 0.5.0 + 11 de 0.6.0)
    "set_parameters", "create_elements", "transform_elements", "delete_elements",
    "change_element_type", "join_geometry", "set_workset", "set_project_location", "create_view",
    "set_active_view", "create_sheet_set", "create_schedule", "annotate", "color_elements", "export",
    "link_file", "load_family", "create_mep_system", "maintain_model",
    "load_steel_profile", "create_steel_frame", "create_bracing", "create_truss", "set_structural_properties",
    "create_steel_connection", "add_plate_or_stiffener", "split_beam", "fix_analytical_alignment",
    "family_open", "family_add_parameters", "family_add_reference_planes", "family_add_dimensions",
    "family_create_solids", "family_lock_faces", "family_set_type_values", "family_add_connectors",
    "family_save", "family_load_into_project", "family_close",
    # macros (4 + 2 de 0.6.0)
    "create_grid_and_levels", "import_from_civil", "list_macros", "run_macro",
    "family_validate", "build_family_from_spec",
    # ultimo recurso (1)
    "execute_revit_code",
)

# nombre_viejo -> "nombre_nuevo(argumentos)" (51 herramientas retiradas en 0.4.0).
HERRAMIENTAS_RETIRADAS = {
    # get_revit_model_info(include=[...])
    "list_levels": "get_revit_model_info(include=[\"levels\"])",
    "list_worksets": "get_revit_model_info(include=[\"worksets\"])",
    "list_phases_and_options": "get_revit_model_info(include=[\"phases\"])",
    "list_links": "get_revit_model_info(include=[\"links\"])",
    "get_project_location": "get_revit_model_info(include=[\"location\"])",
    # vistas
    "list_revit_views": "list_views(view_type=..., on_sheet=...)",
    "get_current_view_info": "list_views(current_only=true) o describe_view() sin view_id",
    "get_view_extents": "describe_view(view_id=...)",
    "get_revit_view": "capture_view(view_name=...)",
    # consulta
    "find_elements": "query_elements(category=..., level=..., name_contains=..., filters=[...])",
    "get_current_view_elements": "query_elements(current_view=true)",
    "ai_element_filter": "query_elements(category=..., type_name=..., current_view=..., bbox_min_mm=..., bbox_max_mm=...)",
    "get_selected_elements": "query_elements(selected=true)",
    "get_element_properties": "describe_element(element_id=...) o describe_element(element_ids=[...])",
    "get_element_geometry": "describe_element(element_id=..., include_geometry=true)",
    # tipos
    "list_element_types": "list_types(category=..., family=...)",
    "list_families": "list_types(contains=...) o list_types(family=...)",
    "list_family_categories": "list_types() sin argumentos",
    "list_category_parameters": "list_types(category=..., with_parameters=true)",
    # parametros (lote en una transaccion)
    "set_parameter": "set_parameters(changes=[{\"element_ids\": [...], \"parameters\": {nombre: valor}}])",
    "set_type_parameter": "set_parameters(element_ids=[type_id o element_id], parameters={...}, type_parameters=true)",
    "modify_element": "set_parameters(changes=[{\"element_id\": id, \"parameters\": {...}}])",
    # creacion (lote en una transaccion)
    "create_line_based_element": "create_elements(elements=[{\"kind\": \"wall\"|\"beam\", ...}])",
    "create_surface_based_element": "create_elements(elements=[{\"kind\": \"floor\"|\"roof\"|\"ceiling\", ...}])",
    "create_level": "create_elements(elements=[{\"kind\": \"level\", \"name\": ..., \"elevation_mm\": ...}])",
    "create_grid": "create_elements(elements=[{\"kind\": \"grid\", ...}]) o create_grid_and_levels",
    "create_structural_column": "create_elements(elements=[{\"kind\": \"column\", ...}])",
    "create_structural_framing": "create_elements(elements=[{\"kind\": \"beam\", ...}])",
    "create_foundation": "create_elements(elements=[{\"kind\": \"foundation\", ...}])",
    "create_opening": "create_elements(elements=[{\"kind\": \"opening\", \"host_id\": ..., \"points\": [...]}])",
    "create_toposolid": "create_elements(elements=[{\"kind\": \"toposolid\", ...}]) o import_from_civil",
    "create_room": "create_elements(elements=[{\"kind\": \"room\", ...}])",
    "create_room_separation": "create_elements(elements=[{\"kind\": \"room_separation\", \"lines\": [...]}])",
    "create_detail_line": "create_elements(elements=[{\"kind\": \"detail_line\", ...}])",
    "create_duct": "create_elements(elements=[{\"kind\": \"duct\", ...}])",
    "create_pipe": "create_elements(elements=[{\"kind\": \"pipe\", ...}])",
    "place_family": "create_elements(elements=[{\"kind\": \"family_instance\", \"family_name\": ..., \"location\": {...}}])",
    # documentacion y anotacion
    "create_sheet": "create_sheet_set(sheets=[{\"number\": ..., \"name\": ..., \"title_block\": ...}])",
    "create_dimensions": "annotate(kind=\"dimension\", element_ids=[...])",
    "tag_walls": "annotate(kind=\"tag\", category=\"OST_Walls\")",
    "tag_elements": "annotate(kind=\"tag\", element_ids=[...])",
    # colores
    "color_splash": "color_elements(category_name=..., parameter_name=...)",
    "clear_colors": "color_elements(category_name=..., clear=true)",
    # exportacion y analisis
    "export_document": "export(format=\"pdf\"|\"png\"|\"jpg\"|\"dwg\", view_name=...)",
    "export_ifc": "export(format=\"ifc\", file_path=...)",
    "export_room_data": "export(format=\"rooms_json\") o export(format=\"rooms_csv\", file_path=...)",
    "analyze_model_statistics": "analyze_model(include=[\"statistics\"])",
    "get_material_quantities": "analyze_model(include=[\"materials\"], categories=[...])",
    # mantenimiento
    "purge_unused": "maintain_model(action=\"purge\", simular=true)",
    "create_backup": "maintain_model(action=\"backup\", suffix=...)",
    "save_document": "maintain_model(action=\"save\")",
}


def mensaje_retirada(nombre):
    """Texto que recibe el cliente al llamar a una herramienta retirada."""
    sustituta = HERRAMIENTAS_RETIRADAS.get(nombre)
    if sustituta is None:
        return "Unknown tool: {}".format(nombre)
    return (
        "La herramienta '{}' se retiro en 0.4.0. Usa en su lugar: {}. "
        "La ruta HTTP sigue existiendo en revit_mcp/; solo cambio el nombre de la "
        "herramienta MCP (ver CONTRATO.md, tabla de herramientas)."
    ).format(nombre, sustituta)


def instalar_retiradas(mcp_server):
    """Hace que un nombre retirado responda con su sustituta en vez de "Unknown tool".

    Con `mcp` 2.x el servidor resuelve cada llamada en
    `MCPServer._tool_manager.call_tool(name, ...)`, que lanza
    `ToolError("Unknown tool: ...")` si el nombre no esta registrado; ese
    ToolError llega al cliente como resultado `is_error` con el texto. Aqui se
    envuelve ese metodo para que, ante un nombre de HERRAMIENTAS_RETIRADAS que
    no este registrado, el ToolError diga por que herramienta sustituirlo. No
    se registran herramientas ocultas: el SDK no tiene forma de excluir una
    herramienta de `tools/list`, y 51 entradas mas en contexto es justo lo que
    esta entrega elimina. Si el SDK cambia y no expone `_tool_manager`, se
    envuelve `MCPServer.call_tool` (misma firma). Devuelve el nombre del
    metodo envuelto, o None si no fue posible (y se avisa en el log).
    """
    try:
        from mcp.server.mcpserver.exceptions import ToolError
    except ImportError:  # pragma: no cover - SDK distinto
        ToolError = Exception

    manager = getattr(mcp_server, "_tool_manager", None)
    objetivo = manager if manager is not None and hasattr(manager, "call_tool") else mcp_server
    original = getattr(objetivo, "call_tool", None)
    if original is None:
        logger.warning("No se pudo instalar la interceptacion de herramientas retiradas")
        return None
    if getattr(original, "_retiradas_instaladas", False):
        return getattr(original, "_retiradas_objetivo", None)

    def _registrada(nombre):
        get_tool = getattr(objetivo, "get_tool", None)
        if get_tool is None:
            return False
        try:
            return get_tool(nombre) is not None
        except Exception:
            return False

    async def call_tool(name, *args, **kwargs):
        if name in HERRAMIENTAS_RETIRADAS and not _registrada(name):
            raise ToolError(mensaje_retirada(name))
        return await original(name, *args, **kwargs)

    etiqueta = "_tool_manager.call_tool" if objetivo is manager else "call_tool"
    call_tool._retiradas_instaladas = True
    call_tool._retiradas_objetivo = etiqueta
    setattr(objetivo, "call_tool", call_tool)
    return etiqueta


def register_tools(mcp_server, revit_get_func, revit_post_func, revit_image_func):
    """Registra las 66 herramientas y la interceptacion de nombres retirados."""
    from .lectura_tools import register_lectura_tools
    from .escritura_tools import register_escritura_tools
    from .macro_tools import register_macro_tools
    from .code_execution_tools import register_code_execution_tools

    register_lectura_tools(mcp_server, revit_get_func, revit_post_func, revit_image_func)
    register_escritura_tools(mcp_server, revit_get_func, revit_post_func, revit_image_func)
    register_macro_tools(mcp_server, revit_get_func, revit_post_func, revit_image_func)
    register_code_execution_tools(mcp_server, revit_get_func, revit_post_func, revit_image_func)
    instalar_retiradas(mcp_server)
