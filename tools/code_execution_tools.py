# -*- coding: utf-8 -*-
"""execute_revit_code (0.4.0): ultimo recurso, solo cuando ninguna otra
herramienta (ni una macro de list_macros) cubre la accion."""

from mcp.server.mcpserver import Context
from .utils import format_response, Cronometro, TIMEOUT_LARGO


def register_code_execution_tools(mcp, revit_get, revit_post, revit_image=None):
    """Registra execute_revit_code."""
    _ = revit_get, revit_image

    @mcp.tool()
    async def execute_revit_code(
        code: str,
        description: str,
        simular: bool = False,
        forzar: bool = False,
        ctx: Context = None,
    ) -> str:
        """LAST RESORT: run IronPython 2.7 code inside Revit when no tool or macro covers
        the action. Explain to the user what it does first. `doc`, `DB`, `revit`, `clr`,
        `System`, `make_element_id`, `get_element_id_value`, `buscar_parametro` and `print`
        are defined; the code already runs inside a transaction (do not open one).
        Example: execute_revit_code(code="print(doc.Title)", description="Leer título").

        Args:
            code: IronPython 2.7 code (no f-strings); output of print is returned
            description: Required, max 60 chars: names the undo entry "IA: <description>"
            simular: Only validate (the code does not run)
            forzar: Required when the code deletes a collection with doc.Delete(<collection>)
        """
        crono = Cronometro()
        payload = {"code": code, "description": description, "simular": simular, "forzar": forzar}
        response = await revit_post("/execute_code/", payload, ctx, timeout=TIMEOUT_LARGO)
        return format_response(response, ms_puente=crono.ms())
