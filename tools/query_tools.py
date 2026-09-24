# -*- coding: utf-8 -*-
"""Query tools (read-only) — warnings, worksets, phases, links, coordinates,
element search, geometry, element types and the MCP action log."""

from mcp.server.mcpserver import Context
from .utils import format_response


def register_query_tools(mcp, revit_get, revit_post, revit_image=None):
    """Register the read-only query tools with the MCP server."""
    _ = revit_image  # Acknowledge unused parameter

    @mcp.tool()
    async def list_warnings(max: int = 100, ctx: Context = None) -> str:
        """List the model warnings (doc.GetWarnings): description, severity and element ids.

        Call it after creating elements to check that nothing overlaps or is
        unjoined. Returns `total` and `truncated` when there are more than `max`.

        Args:
            max: Maximum warnings returned (default 100)
            ctx: MCP context for logging
        """
        response = await revit_get("/warnings/", ctx, params={"max": str(max)})
        return format_response(response)

    @mcp.tool()
    async def list_worksets(ctx: Context = None) -> str:
        """List the worksets (subproyectos) of a workshared model.

        Returns id, name, owner, editable, open and active for each user
        workset, plus `is_workshared`. Never change elements in a workset
        owned by someone else.

        Args:
            ctx: MCP context for logging
        """
        response = await revit_get("/worksets/", ctx)
        return format_response(response)

    @mcp.tool()
    async def list_phases_and_options(ctx: Context = None) -> str:
        """List the project phases (in order) and design options with id, name,
        whether the option is primary (es_principal), its option set and which
        option is active.

        Args:
            ctx: MCP context for logging
        """
        response = await revit_get("/phases_options/", ctx)
        return format_response(response)

    @mcp.tool()
    async def list_links(ctx: Context = None) -> str:
        """List the linked/imported files: RVT and IFC links and CAD (DWG/DXF/DGN...)
        imports, with id, name, path, loaded status and position
        (origen / interno / compartido) plus the origin offset in mm.

        Args:
            ctx: MCP context for logging
        """
        response = await revit_get("/links/", ctx)
        return format_response(response)

    @mcp.tool()
    async def get_project_location(ctx: Context = None) -> str:
        """Get the project base point, survey point (in mm, internal and shared
        coordinates), the true north angle in degrees and the active shared
        coordinate system (project location / site).

        Args:
            ctx: MCP context for logging
        """
        response = await revit_get("/project_location/", ctx)
        return format_response(response)

    @mcp.tool()
    async def find_elements(
        category: str = None,
        name_contains: str = None,
        type_name: str = None,
        level_name: str = None,
        parameter_name: str = None,
        parameter_value: str = None,
        max: int = 100,
        ids_only: bool = False,
        ctx: Context = None,
    ) -> str:
        """Find elements by category, name, type, level and/or parameter value.

        Returns ids plus a summary per element (category, type, level, bbox_mm).
        Use it BEFORE delete_elements / modify_element / transform_elements to
        list exactly what will be touched, and with level_name to check whether
        a level still hosts elements before deleting it. At least one filter is
        required.

        Args:
            category: BuiltInCategory name ("OST_Walls") or alias ("walls",
                "beams", "ducts", "structural columns")
            name_contains: Case-insensitive substring of the element, type or
                family name
            type_name: Exact type name (e.g. "Generic - 200mm")
            level_name: Exact level name the element belongs to
            parameter_name: Parameter that must exist on the element (or its type)
            parameter_value: Value (as displayed) that parameter must have
            max: Maximum results (default 100); `total_matched` says how many exist
            ids_only: Return only ids (faster for large sets)
            ctx: MCP context for logging
        """
        data = {
            "category": category,
            "name_contains": name_contains,
            "type_name": type_name,
            "level_name": level_name,
            "parameter_name": parameter_name,
            "parameter_value": parameter_value,
            "max": max,
            "ids_only": ids_only,
        }
        response = await revit_post("/find_elements/", data, ctx)
        return format_response(response)

    @mcp.tool()
    async def get_element_geometry(
        element_id: int, detail: str = "bbox", ctx: Context = None
    ) -> str:
        """Get the geometry of an element: bounding box in mm (always), and
        optionally its location curve/point or its solids.

        Args:
            element_id: Revit element id
            detail: "bbox" (default), "curves" (location line: start/end/length,
                or point + rotation) or "solids" (volume m3, area m2, faces,
                centroid per solid)
            ctx: MCP context for logging
        """
        data = {"element_id": element_id, "detail": detail}
        response = await revit_post("/element_geometry/", data, ctx)
        return format_response(response)

    @mcp.tool()
    async def list_element_types(
        category: str, family_name: str = None, max: int = 200, ctx: Context = None
    ) -> str:
        """List the element types available in a category (walls, floors,
        structural columns, doors...): id, family, type name, main type
        parameters (Width, b, h, Structural Material...) and how many
        instances use each type. Use it to pick `type_name` for create_* tools
        or change_element_type.

        Args:
            category: BuiltInCategory name ("OST_StructuralColumns") or alias
                ("structural columns", "walls", "floors")
            family_name: Only types of this family (exact, case-insensitive)
            max: Maximum types returned (default 200)
            ctx: MCP context for logging
        """
        data = {"category": category, "family_name": family_name, "max": max}
        response = await revit_post("/element_types/", data, ctx)
        return format_response(response)

    @mcp.tool()
    async def read_log(last_n: int = 50, ctx: Context = None) -> str:
        """Read the last entries of mcp_log.jsonl, the persistent log of every
        write made through this MCP (route, args, ok, ms, error, result summary).
        It lives next to the .rvt (or in %LOCALAPPDATA%\\RevitMcp if the model
        is unsaved).

        Args:
            last_n: Number of entries from the end (default 50)
            ctx: MCP context for logging
        """
        response = await revit_get("/log/", ctx, params={"last_n": str(last_n)})
        return format_response(response)
