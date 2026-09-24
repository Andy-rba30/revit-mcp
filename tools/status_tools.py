# -*- coding: utf-8 -*-
"""Status and model information tools"""

from mcp.server.mcpserver import Context
from .utils import format_response


def register_status_tools(mcp, revit_get):
    """Register status-related tools"""

    @mcp.tool()
    async def get_revit_status(ctx: Context) -> str:
        """Check if the Revit MCP API is active and responding"""
        response = await revit_get("/status/", ctx, timeout=10.0)
        return format_response(response)

    @mcp.tool()
    async def get_revit_model_info(ctx: Context) -> str:
        """Get comprehensive information about the current Revit model.

        Includes project info, element counts, warnings, levels, rooms, views,
        sheets, links and a `file` block with is_workshared, path, last_saved
        (file date on disk), units (project unit system), project_base_point_mm,
        survey_point_mm and true_north_deg.
        """
        response = await revit_get("/model_info/", ctx)
        return format_response(response)
