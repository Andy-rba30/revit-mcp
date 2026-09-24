# -*- coding: utf-8 -*-
"""Type tools — change element types and set type parameters"""

from mcp.server.mcpserver import Context
from .utils import format_response, TIMEOUT_ESCRITURA


def register_type_tools(mcp, revit_get, revit_post, revit_image=None):
    """Register type-related tools with the MCP server."""
    _ = revit_image  # Acknowledge unused parameter

    @mcp.tool()
    async def change_element_type(
        element_ids: list[int],
        type_name: str = None,
        type_id: int = None,
        simular: bool = False,
        forzar: bool = False,
        ctx: Context = None,
    ) -> str:
        """Change the type of one or more elements (Element.ChangeTypeId).

        The new type must be valid for each element (same category/family
        kind); use list_element_types to see the candidates. Returns
        antes/despues per element and verifies the type read back.

        Args:
            element_ids: Elements to change
            type_name: Target type name, or "Family: Type" when ambiguous
            type_id: Target type id (alternative to type_name)
            simular: If true, only validate and return {"simulado": true, "haria": [...]} without changing the model
            forzar: Required (true) to affect more than 200 elements in one call
            ctx: MCP context for logging
        """
        data = {"element_ids": element_ids, "simular": simular, "forzar": forzar}
        if type_name is not None:
            data["type_name"] = type_name
        if type_id is not None:
            data["type_id"] = type_id
        response = await revit_post("/change_type/", data, ctx, timeout=TIMEOUT_ESCRITURA)
        return format_response(response)

    @mcp.tool()
    async def set_type_parameter(
        parameter_name: str,
        value: str,
        type_id: int = None,
        element_id: int = None,
        simular: bool = False,
        ctx: Context = None,
    ) -> str:
        """Set a TYPE parameter (affects every instance of that type).

        Pass either type_id (from list_element_types / get_element_properties)
        or element_id (its type is used). The response says how many
        instances are affected (afecta_ejemplares) and returns antes/despues.
        For instance parameters use set_parameter.

        Args:
            parameter_name: Type parameter name (e.g. "Width", "b", "Fire Rating")
            value: New value as a string (converted to the parameter's storage type)
            type_id: Element type id
            element_id: Any instance of the type (alternative to type_id)
            simular: If true, only validate and return {"simulado": true, "haria": [...]} without changing the model
            ctx: MCP context for logging
        """
        data = {"parameter_name": parameter_name, "value": value, "simular": simular}
        if type_id is not None:
            data["type_id"] = type_id
        if element_id is not None:
            data["element_id"] = element_id
        response = await revit_post("/set_type_parameter/", data, ctx, timeout=TIMEOUT_ESCRITURA)
        return format_response(response)
