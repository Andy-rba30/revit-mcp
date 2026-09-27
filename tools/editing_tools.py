# -*- coding: utf-8 -*-
"""Editing tools — delete, modify, and select elements"""

from mcp.server.mcpserver import Context
from .utils import format_response, TIMEOUT_ESCRITURA


def register_editing_tools(mcp, revit_get, revit_post, revit_image=None):
    """Register editing tools with the MCP server."""
    _ = revit_image  # Acknowledge unused parameter

    @mcp.tool()
    async def delete_elements(
        element_ids: list[int],
        simular: bool = False,
        forzar: bool = False,
        ctx: Context = None,
    ) -> str:
        """Delete one or more elements from the Revit model.

        Removes elements by their IDs. If a deleted element hosts other elements
        (e.g., a wall with doors), the hosted elements are also deleted (cascade).
        All deletions happen in a single transaction — if any fails, none are deleted.

        Args:
            element_ids: List of Revit element IDs to delete
            simular: If true, only validate and return {"simulado": true, "haria": [...]} without changing the model
            forzar: Required (true) to affect more than 200 elements in one call
            ctx: MCP context for logging
        """
        data = {"element_ids": element_ids}
        data["simular"] = simular
        data["forzar"] = forzar
        response = await revit_post("/delete_elements/", data, ctx, timeout=TIMEOUT_ESCRITURA)
        return format_response(response)

    @mcp.tool()
    async def modify_element(
        element_id: int,
        parameters: dict,
        simular: bool = False,
        ctx: Context = None,
    ) -> str:
        """Modify parameter values on a Revit element.

        Changes one or more instance parameters on the specified element.
        Returns old and new values for confirmation.

        Args:
            element_id: Revit element ID to modify
            parameters: Dictionary of parameter name to new value pairs
                (lengths in mm, areas in mm², volumes in mm³, angles in degrees)
                e.g., {"Mark": "EW-01", "Comments": "Updated via MCP"}
            simular: If true, only validate and return {"simulado": true, "haria": [...]} without changing the model
            ctx: MCP context for logging
        """
        data = {"element_id": element_id, "parameters": parameters}
        data["simular"] = simular
        response = await revit_post("/modify_element/", data, ctx, timeout=TIMEOUT_ESCRITURA)
        return format_response(response)

    @mcp.tool()
    async def get_selected_elements(
        ctx: Context = None,
    ) -> str:
        """Get details of elements currently selected in the Revit UI.

        Returns IDs, categories, types, and key parameters of all elements
        the user has selected in Revit. Returns an empty list if nothing
        is selected (not an error).

        Args:
            ctx: MCP context for logging
        """
        response = await revit_get("/selected_elements/", ctx)
        return format_response(response)
