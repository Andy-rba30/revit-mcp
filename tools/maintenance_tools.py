# -*- coding: utf-8 -*-
"""Maintenance tools — purge unused and on-demand backups"""

from mcp.server.mcpserver import Context
from .utils import format_response, TIMEOUT_LARGO


def register_maintenance_tools(mcp, revit_get, revit_post, revit_image=None):
    """Register maintenance tools with the MCP server."""
    _ = revit_image  # Acknowledge unused parameter

    @mcp.tool()
    async def purge_unused(
        max_rounds: int = 3,
        simular: bool = False,
        ctx: Context = None,
    ) -> str:
        """Purge unused families and types (like Manage > Purge Unused).

        Uses the PerformanceAdviser rule "Project contains unused families and
        types"; if it is not available, only FamilySymbols without instances
        are removed. Several rounds are run because deleting a type can leave
        its family unused. ALWAYS run with simular=true first: it lists every
        element that would be deleted.

        Args:
            max_rounds: Maximum purge rounds (default 3, max 10)
            simular: If true, only list what would be purged without changing the model
            ctx: MCP context for logging
        """
        data = {"max_rounds": max_rounds, "simular": simular}
        response = await revit_post("/purge_unused/", data, ctx, timeout=TIMEOUT_LARGO)
        return format_response(response)

    @mcp.tool()
    async def create_backup(
        suffix: str = None,
        simular: bool = False,
        ctx: Context = None,
    ) -> str:
        """Copy the saved .rvt to <folder>\\backups\\<name>_<yyyyMMdd_HHmmss>[_suffix].rvt
        on demand (the same copy every write makes automatically, but forced now).

        The copy reflects the LAST SAVE on disk, not the state in memory; the
        document is never saved by the MCP. Not available for unsaved or
        workshared models.

        Args:
            suffix: Optional label appended to the file name (e.g. "antes_de_purgar")
            simular: If true, only report what would be copied
            ctx: MCP context for logging
        """
        data = {"simular": simular}
        if suffix:
            data["suffix"] = suffix
        response = await revit_post("/backup/", data, ctx, timeout=TIMEOUT_LARGO)
        return format_response(response)
