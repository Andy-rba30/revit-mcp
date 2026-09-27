# -*- coding: utf-8 -*-
"""Project macro tools (0.3.0) — create_grid_and_levels, create_sheet_set and
import_from_civil. Every macro validates first, answers with a `plan` when
simular=true and applies the 200-element limit (forzar) to everything it creates."""

from mcp.server.mcpserver import Context
from .utils import format_response, TIMEOUT_ESCRITURA, TIMEOUT_LARGO


def register_macro_tools(mcp, revit_get, revit_post, revit_image=None):
    """Register the project macros with the MCP server."""
    _ = revit_get, revit_image  # Acknowledge unused parameters

    @mcp.tool()
    async def create_grid_and_levels(
        x_spacings_mm: list[float] = None,
        y_spacings_mm: list[float] = None,
        x_names: str | list[str] = None,
        y_names: str | list[str] = None,
        levels: list[dict] = None,
        origin_mm: dict = None,
        extension_mm: float = 2000,
        simular: bool = False,
        forzar: bool = False,
        ctx: Context = None,
    ) -> str:
        """Create a complete structural grid (sequential names: 1, 2, 3 along X
        and A, B, C along Y) and the building levels in ONE transaction
        ("IA: Rejilla y niveles"). Run it with simular=true first: the answer
        carries `plan` with every grid position and level.

        Args:
            x_spacings_mm: Spacings in mm between consecutive grids along X
                (n spacings = n+1 vertical grid lines, parallel to Y)
            y_spacings_mm: Spacings in mm between consecutive grids along Y
                (horizontal grid lines, parallel to X)
            x_names: Full list of names for the X grids, or the first name to
                continue from ("1", "P1"); default "1", "2", "3"...
            y_names: Full list of names for the Y grids, or the first name to
                continue from ("A"); default "A", "B", "C"... (then AA, AB)
            levels: [{"name": "Nivel 1", "elevation_mm": 0}, ...]; names must not exist yet
            origin_mm: {"x", "y", "z"} of grid 1/A (default 0, 0, 0)
            extension_mm: How far the grid lines run past the last grid on each side (default 2000)
            simular: If true, only validate and return {"simulado": true, "haria": [...], "plan": {...}}
            forzar: Required (true) to create more than 200 elements in one call
            ctx: MCP context for logging
        """
        data = {"extension_mm": extension_mm, "simular": simular, "forzar": forzar}
        for clave, valor in (("x_spacings_mm", x_spacings_mm), ("y_spacings_mm", y_spacings_mm),
                             ("x_names", x_names), ("y_names", y_names), ("levels", levels), ("origin_mm", origin_mm)):
            if valor is not None:
                data[clave] = valor
        response = await revit_post("/grid_levels/", data, ctx, timeout=TIMEOUT_ESCRITURA)
        return format_response(response)

    @mcp.tool()
    async def create_sheet_set(
        sheets: list[dict],
        simular: bool = False,
        forzar: bool = False,
        ctx: Context = None,
    ) -> str:
        """Create several sheets and place views on them in one transaction.
        Each view is checked with Viewport.CanAddViewToSheet before
        Viewport.Create (schedules use ScheduleSheetInstance). A view that is
        already on another sheet is reported in `skipped` and not placed; a
        view name that does not exist answers 404 with `missing_views`.

        Args:
            sheets: [{"number": "E-101", "name": "Planta cimentación",
                "title_block": "A1 métrico" (type name, optional: first loaded),
                "views": [{"view_name": "Planta Nivel 1", "position_mm": {"x": 400, "y": 300}}, ...]}]
                position_mm is the viewport centre on the sheet in mm; omitted
                = centre of the title block. A view can also be given by "view_id".
            simular: If true, only validate and return {"simulado": true, "haria": [...], "plan": {...}}
            forzar: Required (true) to create more than 200 sheets+views in one call
            ctx: MCP context for logging
        """
        data = {"sheets": sheets, "simular": simular, "forzar": forzar}
        response = await revit_post("/sheet_set/", data, ctx, timeout=TIMEOUT_ESCRITURA)
        return format_response(response)

    @mcp.tool()
    async def import_from_civil(
        file_path: str,
        level: str,
        use_shared_coordinates: bool = False,
        origin_offset_mm: dict = None,
        units: str = None,
        type_name: str = None,
        placement: str = None,
        simular: bool = False,
        forzar: bool = False,
        ctx: Context = None,
    ) -> str:
        """Import survey data from Civil 3D: a LandXML surface
        (Surface/Definition/Pnts, or CgPoints) or a CSV (P,N,E,Z / X,Y,Z)
        becomes a toposolid (Revit 2024+) on `level`; a DWG/DXF/DGN is linked
        in the floor plan of `level` (or the active view) and, with
        use_shared_coordinates, the project acquires its coordinates
        (doc.AcquireCoordinates). Acquiring is refused (409) when the project
        already has shared coordinates unless forzar=true.

        Args:
            file_path: Full path on the Revit machine (.xml/.landxml, .csv/.txt or .dwg/.dxf/.dgn)
            level: Level name (as Revit shows it) for the toposolid or the placement plan view
            use_shared_coordinates: DWG only: acquire the shared coordinates from the link
            origin_offset_mm: {"x","y","z"} added to every point (or applied to the link)
            units: CSV/LandXML units: "m" (CSV default), "mm", "cm" or "ft"; LandXML reads its <Units> when omitted
            type_name: Toposolid type (optional, first available)
            placement: DWG placement: "origin", "center" (default when acquiring coordinates) or "shared"
            simular: If true, only validate and return {"simulado": true, "haria": [...], "plan": {...}}
            forzar: Required (true) to overwrite existing shared coordinates
            ctx: MCP context for logging
        """
        data = {"file_path": file_path, "level": level, "use_shared_coordinates": use_shared_coordinates,
                "simular": simular, "forzar": forzar}
        for clave, valor in (("origin_offset_mm", origin_offset_mm), ("units", units), ("type_name", type_name),
                             ("placement", placement)):
            if valor is not None:
                data[clave] = valor
        response = await revit_post("/import_civil/", data, ctx, timeout=TIMEOUT_LARGO)
        return format_response(response)
