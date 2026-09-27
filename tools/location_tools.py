# -*- coding: utf-8 -*-
"""Location tools — project coordinates, worksets and join geometry"""

from mcp.server.mcpserver import Context
from .utils import format_response, TIMEOUT_ESCRITURA


def register_location_tools(mcp, revit_get, revit_post, revit_image=None):
    """Register coordinate / workset / join tools with the MCP server."""
    _ = revit_image  # Acknowledge unused parameter

    @mcp.tool()
    async def set_project_location(
        base_point_mm: dict = None,
        survey_point_mm: dict = None,
        true_north_deg: float = None,
        acquire_from_link_id: int = None,
        simular: bool = False,
        ctx: Context = None,
    ) -> str:
        """Move the project base point or the survey point, rotate true north,
        or acquire shared coordinates from a linked model (doc.AcquireCoordinates).

        Read get_project_location first; the response returns the full
        location antes/despues and verifies the requested values. Moving the
        survey point while clipped changes the shared coordinate system.

        Args:
            base_point_mm: New internal position of the project base point {"x","y","z"} in mm
            survey_point_mm: New internal position of the survey point {"x","y","z"} in mm
            true_north_deg: New true north angle in degrees (counter-clockwise from project north)
            acquire_from_link_id: Revit link instance id whose shared coordinates are acquired
            simular: If true, only validate and return {"simulado": true, "haria": [...]} without changing the model
            ctx: MCP context for logging
        """
        data = {"simular": simular}
        if base_point_mm is not None:
            data["base_point_mm"] = base_point_mm
        if survey_point_mm is not None:
            data["survey_point_mm"] = survey_point_mm
        if true_north_deg is not None:
            data["true_north_deg"] = true_north_deg
        if acquire_from_link_id is not None:
            data["acquire_from_link_id"] = acquire_from_link_id
        response = await revit_post("/set_project_location/", data, ctx, timeout=TIMEOUT_ESCRITURA)
        return format_response(response)

    @mcp.tool()
    async def set_workset(
        element_ids: list[int],
        workset_name: str,
        simular: bool = False,
        forzar: bool = False,
        ctx: Context = None,
    ) -> str:
        """Move elements to another workset (subproyecto) of a workshared model
        via the ELEM_PARTITION_PARAM parameter.

        Check list_worksets first: the target workset must be editable by you
        and the elements must not be borrowed by someone else (409 otherwise).

        Args:
            element_ids: Elements to move
            workset_name: Target user workset name
            simular: If true, only validate and return {"simulado": true, "haria": [...]} without changing the model
            forzar: Required (true) to affect more than 200 elements in one call
            ctx: MCP context for logging
        """
        data = {"element_ids": element_ids, "workset_name": workset_name,
                "simular": simular, "forzar": forzar}
        response = await revit_post("/set_workset/", data, ctx, timeout=TIMEOUT_ESCRITURA)
        return format_response(response)

    @mcp.tool()
    async def join_geometry(
        element_id_a: int,
        element_id_b: int,
        unjoin: bool = False,
        simular: bool = False,
        ctx: Context = None,
    ) -> str:
        """Join (or unjoin) the geometry of two elements with
        JoinGeometryUtils.JoinGeometry / UnjoinGeometry, e.g. a wall and a
        floor, a column and a beam. Returns joined antes/despues.

        Args:
            element_id_a: First element id
            element_id_b: Second element id
            unjoin: True to separate elements that are currently joined
            simular: If true, only validate and return {"simulado": true, "haria": [...]} without changing the model
            ctx: MCP context for logging
        """
        data = {"element_id_a": element_id_a, "element_id_b": element_id_b,
                "unjoin": unjoin, "simular": simular}
        response = await revit_post("/join_geometry/", data, ctx, timeout=TIMEOUT_ESCRITURA)
        return format_response(response)
