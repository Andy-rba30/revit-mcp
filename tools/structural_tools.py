# -*- coding: utf-8 -*-
"""Structural tools — columns, foundations, openings and toposolids"""

from mcp.server.mcpserver import Context
from .utils import format_response, TIMEOUT_ESCRITURA, TIMEOUT_LARGO


def register_structural_tools(mcp, revit_get, revit_post, revit_image=None):
    """Register structural creation tools with the MCP server."""
    _ = revit_image  # Acknowledge unused parameter

    @mcp.tool()
    async def create_structural_column(
        columns: list[dict],
        simular: bool = False,
        ctx: Context = None,
    ) -> str:
        """Create structural columns (pilares) with doc.Create.NewFamilyInstance
        and StructuralType.Column. Supports batch creation.

        Args:
            columns: List of column definitions, each with:
                - point (dict): {"x", "y", "z"} in mm; z is an offset from base_level (required)
                - base_level (str): Base level name (required)
                - top_level (str): Top level name (optional; must be above base_level)
                - top_offset (float): Offset from top level in mm (optional)
                - type_name (str): Structural column type, or "Family: Type" (optional, first available)
                - rotation (float): Rotation in degrees around the vertical axis (optional)
            simular: If true, only validate and return {"simulado": true, "haria": [...]} without changing the model
            ctx: MCP context for logging
        """
        data = {"columns": columns, "simular": simular}
        response = await revit_post("/create_column/", data, ctx, timeout=TIMEOUT_ESCRITURA)
        return format_response(response)

    @mcp.tool()
    async def create_foundation(
        foundations: list[dict],
        simular: bool = False,
        ctx: Context = None,
    ) -> str:
        """Create foundations (cimentaciones): isolated footings, continuous
        wall foundations (WallFoundation) or foundation slabs. Supports batch.

        Args:
            foundations: List of definitions; each one is ONE of:
                - Isolated footing (zapata aislada): {"point": {"x","y","z"} mm,
                  "level": level name, "type_name": foundation family type, "rotation": deg}
                - Wall foundation (zapata corrida): {"wall_id": id, "type_name": WallFoundationType}
                  or {"curve": {"start_point": {...}, "end_point": {...}} mm, "type_name": ...}
                  — the wall under the curve is located (within 100 mm); WallFoundation
                  always needs a host wall
                - Foundation slab (losa): {"boundary": [{"x","y","z"}, ...] mm closed polygon,
                  "level": level name, "type_name": floor type flagged as foundation slab}
            simular: If true, only validate and return {"simulado": true, "haria": [...]} without changing the model
            ctx: MCP context for logging
        """
        data = {"foundations": foundations, "simular": simular}
        response = await revit_post("/create_foundation/", data, ctx, timeout=TIMEOUT_ESCRITURA)
        return format_response(response)

    @mcp.tool()
    async def create_opening(
        host_id: int,
        points: list[dict],
        simular: bool = False,
        ctx: Context = None,
    ) -> str:
        """Create an opening (hueco) in a wall, floor, roof or ceiling with
        doc.Create.NewOpening.

        Wall openings: give EXACTLY 2 opposite corners of the rectangle, in
        model coordinates (mm), on the wall's location line — take `start`/`end`
        from get_element_properties(host_id) → location_mm and interpolate
        along it. `z` is the ABSOLUTE elevation in mm (the same frame as
        bbox_mm), NOT an offset from the wall base: a wall on a level at
        +2925 mm needs z 3825..5025 for an opening 900..2100 mm above its base.
        Corners off the wall's plane, or a rectangle outside the wall's length
        or height, are rejected with 400 before touching the model (Revit would
        otherwise create the opening and delete it at commit with the warning
        "Rectangular opening doesn't cut its host"). The response includes
        `en_muro` (where the rectangle sits along the wall and in z, in mm) and
        `rectangulo_revit_mm` (the rectangle Revit registered).

        Args:
            host_id: Id of the host wall/floor/roof/ceiling
            points: In a wall: 2 opposite corners of the rectangular opening
                {"x","y","z"} in mm, absolute model coordinates on the wall's
                plane. In a floor/roof/ceiling: 3+ points of the closed
                polygon of the opening, in mm.
            simular: If true, only validate (including the wall geometry check) and return {"simulado": true, "haria": [...]} without changing the model
            ctx: MCP context for logging
        """
        data = {"host_id": host_id, "points": points, "simular": simular}
        response = await revit_post("/create_opening/", data, ctx, timeout=TIMEOUT_ESCRITURA)
        return format_response(response)

    @mcp.tool()
    async def create_toposolid(
        level_name: str,
        points: list[dict] = None,
        csv_path: str = None,
        type_name: str = None,
        units: str = "m",
        boundary: list[dict] = None,
        simular: bool = False,
        ctx: Context = None,
    ) -> str:
        """Create a toposolid (Revit 2024+, DB.Toposolid.Create) from survey
        points given inline (mm) or read from a Civil 3D CSV.

        CSV formats: "P,N,E,Z" (point number, northing, easting, elevation) or
        "X,Y,Z"; the column order is detected from the header or, without a
        header, from the number of columns (4+ = P,N,E,Z; 3 = X,Y,Z). For
        P,N,E,Z the model x = E and y = N. CSV values are ASSUMED TO BE IN
        METERS unless `units` says "mm", "cm" or "ft". Inline `points` are
        always in mm. Check the extent in the simulation before creating.

        Args:
            level_name: Level that hosts the toposolid (required)
            points: Inline points [{"x","y","z"}] in mm (alternative to csv_path)
            csv_path: Full path to a CSV on the Revit machine
            type_name: Toposolid type name (optional, first available)
            units: Units of the CSV values: "m" (default), "mm", "cm" or "ft"
            boundary: Optional closed polygon [{"x","y","z"}] in mm limiting the toposolid
            simular: If true, only validate and return {"simulado": true, "haria": [...]} without changing the model
            ctx: MCP context for logging
        """
        data = {"level_name": level_name, "units": units, "simular": simular}
        if points is not None:
            data["points"] = points
        if csv_path is not None:
            data["csv_path"] = csv_path
        if type_name is not None:
            data["type_name"] = type_name
        if boundary is not None:
            data["boundary"] = boundary
        response = await revit_post("/create_toposolid/", data, ctx, timeout=TIMEOUT_LARGO)
        return format_response(response)
