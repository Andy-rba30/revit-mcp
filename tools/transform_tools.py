# -*- coding: utf-8 -*-
"""Transform tools — move, copy, rotate, and mirror elements"""

from mcp.server.mcpserver import Context
from .utils import format_response, TIMEOUT_ESCRITURA


def register_transform_tools(mcp, revit_get, revit_post, revit_image=None):
    """Register transform tools with the MCP server."""
    _ = revit_image  # Acknowledge unused parameter

    @mcp.tool()
    async def transform_elements(
        element_ids: list[int],
        operation: str,
        vector: dict = None,
        axis_point: dict = None,
        angle: float = None,
        mirror_plane: dict = None,
        simular: bool = False,
        forzar: bool = False,
        ctx: Context = None,
    ) -> str:
        """Move, copy, rotate, or mirror elements in the Revit model.

        Performs geometric transformations on one or more elements.
        All coordinates and distances are in millimeters, angles in degrees.

        Args:
            element_ids: List of element IDs to transform
            operation: Transform type — "move", "copy", "rotate", or "mirror"
            vector: Translation vector {"x", "y", "z"} in mm (required for move/copy)
            axis_point: Rotation center {"x", "y", "z"} in mm (required for rotate)
            angle: Rotation angle in degrees (required for rotate)
            mirror_plane: Mirror definition (required for mirror):
                - origin (dict): {"x", "y", "z"} point on the mirror plane in mm
                - normal (dict): {"x", "y", "z"} plane normal direction
            simular: If true, only validate and return {"simulado": true, "haria": [...]} without changing the model
            forzar: Required (true) to affect more than 200 elements in one call
            ctx: MCP context for logging
        """
        data = {
            "element_ids": element_ids,
            "operation": operation,
        }
        if vector is not None:
            data["vector"] = vector
        if axis_point is not None:
            data["axis_point"] = axis_point
        if angle is not None:
            data["angle"] = angle
        if mirror_plane is not None:
            data["mirror_plane"] = mirror_plane
        data["simular"] = simular
        data["forzar"] = forzar
        response = await revit_post("/transform_elements/", data, ctx, timeout=TIMEOUT_ESCRITURA)
        return format_response(response)
