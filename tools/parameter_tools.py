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
        box in mm (bbox_mm), location_mm (start/end of the location line for
        walls, beams and lines, or point + rotation_deg for family instances),
        level, workset, phase_created, phase_demolished, design_option,
        host_id (if hosted), pinned, and a complete list of both instance and
        type parameters with their values, storage types, read-only status,
        is_type_parameter and, for built-in parameters, `builtin` (the
        BuiltInParameter name, e.g. ALL_MODEL_INSTANCE_COMMENTS — identical in
        every Revit language, unlike `name`).

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
        and new values for confirmation: `antes`/`despues` as Revit displays
        them (project units, e.g. "3.00" in a model in metres) plus, for
        numeric parameters, `antes_valor`/`despues_valor` in the contract
        units (`unidad`: mm, mm2, mm3, grados) and `parameter_name_revit`, the
        name Revit actually used.

        Args:
            element_id: Target element ID
            parameter_name: Parameter name as Revit shows it in its language
                ("Comentarios" on a Spanish Revit), a BuiltInParameter name
                (ALL_MODEL_MARK) or a common English alias ("Comments", "Mark",
                "Description", "Unconnected Height", "Base Offset", "Level",
                "Phase Created"...). get_element_properties lists the names and
                the `builtin` of every parameter.
            value: New value as a string — automatically converted to the correct type.
                Lengths in mm, areas in mm², volumes in mm³, angles in degrees
                (the server converts to Revit's internal feet/radians)
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
