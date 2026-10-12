# -*- coding: utf-8 -*-
"""Coordinate tools — shared coordinates for georeferenced links (Civil 3D, survey)"""

from mcp.server.mcpserver import Context
from .utils import format_response


def register_coordinates_tools(mcp, revit_get, revit_post, revit_image=None):
    """Register coordinate tools with the MCP server."""
    _ = revit_image  # Acknowledge unused parameter

    @mcp.tool()
    async def get_project_coordinates(units: str = "m", ctx: Context = None) -> str:
        """Read the model's shared coordinates (project position, survey point, base point).

        Use it before linking a georeferenced CAD file (for example a Civil 3D
        surface in UTM). If `shared_coordinates_set` is false, the internal
        origin still has real-world coordinates 0,0,0 and any georeferenced
        file linked "origin to origin" will land hundreds of kilometres away
        (Revit warns from 33 km). Fix it with set_project_coordinates.

        Returns, in `units`: `internal_origin` (east, north, elevation and the
        angle to true north of the internal origin in the shared system),
        `project_base_point` and `survey_point` (internal and shared position),
        `site` (latitude, longitude, GIS coordinate system id when assigned, in
        the same codes Civil 3D uses, e.g. "UTM84-18S") and the project
        locations (`active_location`, `locations`).

        Args:
            units: "m" (default), "mm" or "ft" for the returned lengths
            ctx: MCP context for logging
        """
        response = await revit_get("/project_coordinates/", ctx, params={"units": units})
        return format_response(response)

    @mcp.tool()
    async def set_project_coordinates(
        east: float,
        north: float,
        elevation: float = 0.0,
        angle_degrees: float = 0.0,
        units: str = "m",
        point: dict = None,
        ctx: Context = None,
    ) -> str:
        """Give a model point real-world coordinates ("Specify Coordinates at a Point").

        This is how a Revit model gets georeferenced: the internal origin stays
        where it is and the shared coordinate system (survey point) is offset
        so that `point` has the coordinates `east`, `north`, `elevation`.
        Do it ONCE per model, before linking a georeferenced DWG with
        link_file(placement="shared"); afterwards the DWG's world coordinates
        are read as shared coordinates and the file lands next to the internal
        origin instead of 500 km away.

        Typical values: the centre of the Civil 3D surface (tool
        `extension_superficie` of the Civil 3D MCP returns `origen_sugerido`)
        or the known coordinates of a grid intersection. Keep `angle_degrees`
        at 0 when the DWG is aligned to grid north (the usual case).

        Args:
            east: Real-world easting (X) of `point`, in `units`
            north: Real-world northing (Y) of `point`, in `units`
            elevation: Real-world elevation (Z) of `point`, in `units` (default 0)
            angle_degrees: Angle from project north to true north, counter-clockwise (default 0)
            units: Units of east/north/elevation: "m" (default), "mm" or "ft"
            point: Internal point {"x", "y", "z"} in mm that receives those coordinates
                (default: the internal origin 0,0,0)
            ctx: MCP context for logging
        """
        data = {
            "east": east,
            "north": north,
            "elevation": elevation,
            "angle_degrees": angle_degrees,
            "units": units,
        }
        if point is not None:
            data["point"] = point
        response = await revit_post("/set_project_coordinates/", data, ctx)
        return format_response(response)
