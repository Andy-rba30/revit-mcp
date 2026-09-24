# -*- coding: utf-8 -*-
"""Parameter tools — read element properties and set parameter values"""

from mcp.server.mcpserver import Context
from .utils import format_response, TIMEOUT_ESCRITURA


def register_parameter_tools(mcp, revit_get, revit_post, revit_image=None):
    """Register parameter tools with the MCP server."""
    _ = revit_image  # Acknowledge unused parameter

    @mcp.tool()
    async def get_element_properties(
        element_id: int,
        include_type_params: bool = True,
        ctx: Context = None,
    ) -> str:
        """Get all properties and parameters of a Revit element.

        Returns the element's category, family, type (and type_id), bounding
        box in mm (bbox_mm), level, workset, phase_created, phase_demolished,
        design_option, host_id (if hosted), pinned, and a complete list of both
        instance and type parameters with their values, storage types,
        read-only status and is_type_parameter.

        Args:
            element_id: Revit element ID to inspect
            include_type_params: Include type parameters in addition to instance
                parameters (default: true)
            ctx: MCP context for logging
        """
        response = await revit_get(
            "/element_properties/{}".format(element_id), ctx
        )
        return format_response(response)

    @mcp.tool()
    async def set_parameter(
        element_id: int,
        parameter_name: str,
        value: str,
        simular: bool = False,
        ctx: Context = None,
    ) -> str:
        """Set a single parameter value on a Revit element.

        Automatically detects the parameter's storage type (String, Integer,
        Double, ElementId) and converts the value accordingly. Returns old
        and new values for confirmation.

        Args:
            element_id: Target element ID
            parameter_name: Name of the parameter to set (e.g., "Comments", "Mark")
            value: New value as a string — automatically converted to the correct type
            simular: If true, only validate and return {"simulado": true, "haria": [...]} without changing the model
            ctx: MCP context for logging
        """
        data = {
            "element_id": element_id,
            "parameter_name": parameter_name,
            "value": value,
        }
        data["simular"] = simular
        response = await revit_post("/set_parameter/", data, ctx, timeout=TIMEOUT_ESCRITURA)
        return format_response(response)
