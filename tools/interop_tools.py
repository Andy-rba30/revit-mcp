# -*- coding: utf-8 -*-
"""Interop tools — IFC export and external file linking"""

from mcp.server.mcpserver import Context
from .utils import format_response


def register_interop_tools(mcp, revit_get, revit_post, revit_image=None):
    """Register interop tools with the MCP server."""
    _ = revit_image  # Acknowledge unused parameter

    @mcp.tool()
    async def export_ifc(
        file_path: str,
        ifc_version: str = "IFC2x3",
        export_base_quantities: bool = True,
        view_name: str = None,
        ctx: Context = None,
    ) -> str:
        """Export the Revit model to IFC format.

        Creates an IFC file at the specified path. Optionally filter by view
        to export only visible elements. Supports IFC2x3 and IFC4.

        Args:
            file_path: Output file path (must end in .ifc)
            ifc_version: "IFC2x3" (default) or "IFC4"
            export_base_quantities: Include IFC base quantities (default: true)
            view_name: Export only elements visible in this view (optional)
            ctx: MCP context for logging
        """
        data = {
            "file_path": file_path,
            "ifc_version": ifc_version,
            "export_base_quantities": export_base_quantities,
        }
        if view_name is not None:
            data["view_name"] = view_name
        response = await revit_post("/export_ifc/", data, ctx)
        return format_response(response)

    @mcp.tool()
    async def link_file(
        file_path: str,
        mode: str = "link",
        placement: str = "origin",
        position: dict = None,
        ctx: Context = None,
    ) -> str:
        """Link or import an external file into the Revit model.

        Supports DWG, DXF, DGN (CAD files) and RVT (Revit links).
        Linked files maintain a live connection; imported files are embedded.

        Georeferenced CAD files (a Civil 3D surface or site plan in UTM or any
        survey system) must NOT be placed "origin": their coordinates are
        hundreds of kilometres from Revit's internal origin and Revit warns
        (33 km limit) and may silently fall back to center-to-center. Instead:
        1. get_project_coordinates: if `shared_coordinates_set` is false,
        2. set_project_coordinates(east, north, elevation) with the real-world
           coordinates of the internal origin (e.g. the surface centre), then
        3. link_file(..., placement="shared").
        The response reports how far the file landed from the internal origin
        and warns when it is beyond 16 km.

        Args:
            file_path: Path to the file (DWG, DXF, DGN, SAT, SKP, 3DM or RVT)
            mode: "link" (default, maintains connection) or "import" (embeds copy)
            placement: "origin" (default, Auto - Origin to Internal Origin),
                "shared" (Auto - By Shared Coordinates; requires shared coordinates,
                see set_project_coordinates), "center" (Auto - Center to Center) or
                "site" (Auto - Origin to Project Base Point)
            position: Optional placement offset {"x", "y", "z"} in mm
            ctx: MCP context for logging
        """
        data = {"file_path": file_path, "mode": mode, "placement": placement}
        if position is not None:
            data["position"] = position
        response = await revit_post("/link_file/", data, ctx)
        return format_response(response)
