# -*- coding: utf-8 -*-
"""Navigation tools (0.3.0, read-only) — describe_element, dependency_graph,
query_elements, schedule_to_json, get_view_extents, snapshot_model and
diff_snapshots."""

from mcp.server.mcpserver import Context
from .utils import format_response, TIMEOUT_LECTURA, TIMEOUT_LARGO


def register_navigation_tools(mcp, revit_get, revit_post, revit_image=None):
    """Register the deep-navigation tools with the MCP server."""
    _ = revit_get, revit_image  # Acknowledge unused parameters

    @mcp.tool()
    async def describe_element(
        element_id: int,
        depth: int = 0,
        include_geometry: bool = False,
        ctx: Context = None,
    ) -> str:
        """Describe one element in depth: identity, every instance and type
        parameter, and its relations with other elements.

        Returns categoria, familia, tipo, nivel, host, workset, phase, design
        option, bbox_mm and `parameters` {instance[], type[]} where each
        parameter has name (as Revit shows it), value (contract units: mm,
        mm2, mm3, degrees), display, unit, is_read_only, is_shared, guid and
        `builtin` (the BuiltInParameter name, valid in any language). Also
        hosted_elements[] (FamilyInstance.Host inverse + HostObject.FindInserts),
        joined_elements[] (JoinGeometryUtils), dependents[] (GetDependentElements,
        model categories only) and referenced_by[] (dimensions and tags in the
        ACTIVE view only).

        Args:
            element_id: Revit element id
            depth: 0 = related elements as ids; 1 = with categoria/tipo/nivel;
                2 = also their own hosted_ids and joined_ids
            include_geometry: Add `geometry` (location in mm, solids with volume_m3 / area_m2)
            ctx: MCP context for logging
        """
        data = {"element_id": element_id, "depth": depth, "include_geometry": include_geometry}
        response = await revit_post("/describe/", data, ctx, timeout=TIMEOUT_LECTURA)
        return format_response(response)

    @mcp.tool()
    async def dependency_graph(
        element_id: int,
        max_nodes: int = 100,
        max_depth: int = 2,
        ctx: Context = None,
    ) -> str:
        """Nodes and edges around an element, ready to draw: edges of kind
        `hosts` (host -> hosted element), `joins` (joined geometry, undirected)
        and `depends` (element -> dependent of a model category, i.e. what
        would be deleted with it). Breadth-first from element_id.

        Args:
            element_id: Root element id
            max_nodes: Maximum nodes (default 100, max 500); `truncated` says if the limit was hit
            max_depth: How many hops from the root (default 2, max 6)
            ctx: MCP context for logging
        """
        data = {"element_id": element_id, "max_nodes": max_nodes, "max_depth": max_depth}
        response = await revit_post("/dependency_graph/", data, ctx, timeout=TIMEOUT_LECTURA)
        return format_response(response)

    @mcp.tool()
    async def query_elements(
        category: str = None,
        family: str = None,
        type_name: str = None,
        level: str = None,
        view_id: int = None,
        workset: str = None,
        phase: str = None,
        filters: list[dict] = None,
        bbox_min_mm: dict = None,
        bbox_max_mm: dict = None,
        sort_by: str = None,
        page: int = 1,
        page_size: int = 100,
        fields: list[str] = None,
        name_contains: str = None,
        ids_only: bool = False,
        ctx: Context = None,
    ) -> str:
        """Paginated element query with parameter filters. Give at least one
        criterion. Category, view, workset, bounding box and the filters on
        BuiltInParameter or shared parameters are evaluated natively in Revit
        (ElementParameterFilter); the rest in Python.

        Each result has id, categoria, tipo, familia, nivel, bbox_mm and,
        with `fields`, the requested parameter values in contract units.

        Args:
            category: BuiltInCategory name ("OST_Walls") or alias ("walls", "beams"); a list is accepted
            family: Exact family name (case-insensitive)
            type_name: Exact type name or "Family: Type"
            level: Exact level name
            view_id: Only elements visible in that view (not a template)
            workset: Workset name (workshared models only)
            phase: Phase name the elements were created in
            filters: List of {"parameter", "op", "value"}. parameter = name as
                Revit shows it, its English alias ("Mark", "Comments",
                "Length") or the BuiltInParameter name ("ALL_MODEL_MARK").
                op = "=", "!=", ">", "<", ">=", "<=", "contains", "starts",
                "empty", "not_empty" or "exists". Numeric values in mm, mm2,
                mm3 or degrees; text comparisons ignore case.
            bbox_min_mm: Min corner {"x","y","z"} in mm of a box the element's bbox must intersect
            bbox_max_mm: Max corner {"x","y","z"} in mm (goes with bbox_min_mm)
            sort_by: "id", "categoria", "tipo", "nivel", "nombre", "familia" or a
                parameter name; prefix "-" for descending
            page: 1-based page number
            page_size: Results per page (default 100, max 500)
            fields: Parameter names to include per element (contract units)
            name_contains: Case-insensitive substring of the element, type or family name
            ids_only: Return only ids (faster for large sets)
            ctx: MCP context for logging
        """
        data = {"page": page, "page_size": page_size, "ids_only": ids_only}
        for clave, valor in (
            ("category", category), ("family", family), ("type_name", type_name), ("level", level),
            ("view_id", view_id), ("workset", workset), ("phase", phase), ("filters", filters),
            ("bbox_min_mm", bbox_min_mm), ("bbox_max_mm", bbox_max_mm), ("sort_by", sort_by),
            ("fields", fields), ("name_contains", name_contains),
        ):
            if valor is not None:
                data[clave] = valor
        response = await revit_post("/query/", data, ctx, timeout=TIMEOUT_LECTURA)
        return format_response(response)

    @mcp.tool()
    async def schedule_to_json(
        name: str = None,
        view_id: int = None,
        start_row: int = 0,
        max_rows: int = 500,
        ctx: Context = None,
    ) -> str:
        """Read a schedule (tabla de planificación) as headers and rows, exactly
        as Revit displays them (GetTableData / GetCellText on the Body section).

        Give `name` (exact schedule name; spaces and accents are fine, it goes
        in the JSON body) or `view_id`. Returns headers[], rows[][] (strings),
        row_count, total_rows, truncated and fields[] (name, heading, hidden).

        Args:
            name: Schedule view name
            view_id: Schedule view id (alternative to name)
            start_row: First body row to return (default 0)
            max_rows: Rows returned (default 500, max 5000)
            ctx: MCP context for logging
        """
        data = {"start_row": start_row, "max_rows": max_rows}
        if name is not None:
            data["name"] = name
        if view_id is not None:
            data["view_id"] = view_id
        response = await revit_post("/schedule/", data, ctx, timeout=TIMEOUT_LECTURA)
        return format_response(response)

    @mcp.tool()
    async def get_view_extents(
        view_id: int = None,
        view_name: str = None,
        ctx: Context = None,
    ) -> str:
        """Crop box (view and model coordinates in mm), view range (top, cut,
        bottom, view depth with level and offset_mm), scale, level,
        discipline, detail level, view template, sheet number and phase of a
        view; section box for 3D views.

        Args:
            view_id: View id (from list_revit_views / get_current_view_info)
            view_name: Exact view name (alternative to view_id)
            ctx: MCP context for logging
        """
        data = {}
        if view_id is not None:
            data["view_id"] = view_id
        if view_name is not None:
            data["view_name"] = view_name
        response = await revit_post("/view_extents/", data, ctx, timeout=TIMEOUT_LECTURA)
        return format_response(response)

    @mcp.tool()
    async def snapshot_model(
        name: str,
        categories: list[str] = None,
        parameters: list[str] = None,
        include_parameters: bool = True,
        max_elements: int = 20000,
        overwrite: bool = False,
        ctx: Context = None,
    ) -> str:
        """Save a snapshot of the model to <rvt folder>\\snapshots\\<name>.json
        (or %LOCALAPPDATA%\\RevitMcp\\snapshots if the model is unsaved): per
        element id, UniqueId, categoria, tipo, nivel, bbox_mm, a hash of its
        parameters and (by default) the parameter values, so diff_snapshots
        can say what changed. No transaction; the model is not modified.

        Args:
            name: Snapshot name (file name without .json)
            categories: BuiltInCategory names or aliases to include; default =
                every model-category element plus levels and grids
            parameters: Only these parameters go into the hash/values (default: all instance parameters)
            include_parameters: Store the parameter values (needed to list the changed parameters)
            max_elements: Element limit (default 20000); the response warns if truncated
            overwrite: Replace an existing snapshot with the same name (409 otherwise)
            ctx: MCP context for logging
        """
        data = {"name": name, "include_parameters": include_parameters, "max_elements": max_elements,
                "overwrite": overwrite}
        if categories is not None:
            data["categories"] = categories
        if parameters is not None:
            data["parameters"] = parameters
        response = await revit_post("/snapshot/", data, ctx, timeout=TIMEOUT_LARGO)
        return format_response(response)

    @mcp.tool()
    async def diff_snapshots(
        a: str,
        b: str = None,
        max_items: int = 500,
        ctx: Context = None,
    ) -> str:
        """Compare two snapshots by UniqueId: added[], removed[] and modified[]
        (with the parameters that changed, type/level changes and bbox moves).
        Omit `b` (or pass "actual") to compare snapshot `a` against the model as
        it is now.

        Args:
            a: Older snapshot name (or full path of its .json)
            b: Newer snapshot name; omitted = the live model
            max_items: Maximum entries per list (default 500)
            ctx: MCP context for logging
        """
        data = {"a": a, "max_items": max_items}
        if b is not None:
            data["b"] = b
        response = await revit_post("/diff_snapshots/", data, ctx, timeout=TIMEOUT_LARGO)
        return format_response(response)
