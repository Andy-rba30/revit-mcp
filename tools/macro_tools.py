# -*- coding: utf-8 -*-
"""Macros (0.6.0, 6): las dos macros de proyecto de 0.3.0 (create_grid_and_levels,
import_from_civil; create_sheet_set esta en escritura_tools), las macros
propias del usuario (list_macros, run_macro: carpeta %LOCALAPPDATA%\\RevitMcp\\macros
o REVIT_MCP_MACROS, con macro.json y macro.py) y, desde 0.6.0, las dos macros
del editor de familias (family_validate, build_family_from_spec). Toda macro
valida y responde con `plan` cuando simular=true y aplica el limite de 200
elementos (forzar)."""

from mcp.server.mcpserver import Context
from .utils import format_response, Cronometro, TIMEOUT_LECTURA, TIMEOUT_ESCRITURA, TIMEOUT_LARGO


async def _timeout_del_manifiesto(revit_get, name, ctx):
    """timeout_s declarado en macro.json de `name` (via GET /macros/), o TIMEOUT_LARGO."""
    try:
        catalogo = await revit_get("/macros/", ctx, timeout=TIMEOUT_LECTURA)
        for macro in (catalogo or {}).get("macros") or []:
            if macro.get("name") == name and macro.get("timeout_s"):
                return max(float(macro["timeout_s"]), 1.0)
    except Exception:
        pass
    return TIMEOUT_LARGO


def register_macro_tools(mcp, revit_get, revit_post, revit_image=None):
    """Registra las 6 macros."""

    @mcp.tool()
    async def create_grid_and_levels(
        x_spacings_mm: list[float] = None,
        y_spacings_mm: list[float] = None,
        x_names: str | list[str] = None,
        y_names: str | list[str] = None,
        levels: list[dict] = None,
        origin_mm: dict = None,
        extension_mm: float = 2000,
        simular: bool = False,
        forzar: bool = False,
        ctx: Context = None,
    ) -> str:
        """Complete structural grid (sequential names 1, 2, 3 / A, B, C) and levels in ONE
        transaction. Run with simular=true first and show `plan`. Names must not exist yet.
        Example: create_grid_and_levels(x_spacings_mm=[6000, 6000], y_spacings_mm=[5000],
        x_names="1", y_names="A", levels=[{"name": "Nivel 1", "elevation_mm": 0}]).

        Args:
            x_spacings_mm: Spacings between consecutive grids along X (n spacings = n+1 grids)
            y_spacings_mm: Spacings along Y
            x_names: Full list, or the first name to continue from ("1", "P1")
            y_names: Full list, or the first name to continue from ("A")
            levels: [{"name", "elevation_mm"}] (internal origin)
            origin_mm: {"x","y","z"} of grid 1/A
            extension_mm: Overrun past the last grid on each side
            simular: Only validate; returns plan
            forzar: Required above 200 elements
        """
        crono = Cronometro()
        data = {"extension_mm": extension_mm, "simular": simular, "forzar": forzar}
        for clave, valor in (("x_spacings_mm", x_spacings_mm), ("y_spacings_mm", y_spacings_mm),
                             ("x_names", x_names), ("y_names", y_names), ("levels", levels), ("origin_mm", origin_mm)):
            if valor is not None:
                data[clave] = valor
        response = await revit_post("/grid_levels/", data, ctx, timeout=TIMEOUT_ESCRITURA)
        return format_response(response, ms_puente=crono.ms())

    @mcp.tool()
    async def import_from_civil(
        file_path: str,
        level: str,
        use_shared_coordinates: bool = False,
        origin_offset_mm: dict = None,
        units: str = None,
        type_name: str = None,
        placement: str = None,
        simular: bool = False,
        forzar: bool = False,
        ctx: Context = None,
    ) -> str:
        """Import Civil 3D data: a LandXML surface or CSV (P,N,E,Z / X,Y,Z) becomes a
        toposolid on `level`; a DWG/DXF/DGN is linked in the level's plan and, with
        use_shared_coordinates, the project acquires its coordinates (409 unless forzar
        when it already has some). Example: import_from_civil(file_path="C:\\\\Proyectos\\\\terreno.xml",
        level="Terreno", simular=true).

        Args:
            file_path: Full path (.xml/.landxml, .csv/.txt or .dwg/.dxf/.dgn)
            level: Level name for the toposolid or the placement plan
            use_shared_coordinates: DWG only: acquire the shared coordinates from the link
            origin_offset_mm: {"x","y","z"} added to every point / applied to the link
            units: CSV/LandXML units ("m" default for CSV, "mm", "cm", "ft")
            type_name: Toposolid type
            placement: DWG placement: "origin", "center" or "shared"
            simular: Only validate; returns plan
            forzar: Overwrite existing shared coordinates
        """
        crono = Cronometro()
        data = {"file_path": file_path, "level": level, "use_shared_coordinates": use_shared_coordinates,
                "simular": simular, "forzar": forzar}
        for clave, valor in (("origin_offset_mm", origin_offset_mm), ("units", units), ("type_name", type_name),
                             ("placement", placement)):
            if valor is not None:
                data[clave] = valor
        response = await revit_post("/import_civil/", data, ctx, timeout=TIMEOUT_LARGO)
        return format_response(response, ms_puente=crono.ms())

    @mcp.tool()
    async def list_macros(ctx: Context = None) -> str:
        """Catalogue of the user's own macros (deterministic plugins in
        %LOCALAPPDATA%\\RevitMcp\\macros\\<name>\\macro.json + macro.py): description,
        arguments with types and defaults, whether it writes, and the invalid ones with
        their error. Check it BEFORE chaining several tools for a repetitive task.
        Example: list_macros()."""
        crono = Cronometro()
        response = await revit_get("/macros/", ctx, timeout=TIMEOUT_LECTURA)
        return format_response(response, ms_puente=crono.ms())

    @mcp.tool()
    async def run_macro(
        name: str,
        args: dict = None,
        simular: bool = False,
        forzar: bool = False,
        timeout_s: float = None,
        ctx: Context = None,
    ) -> str:
        """Run one of the user's macros (see list_macros) with its arguments. A macro that
        writes runs inside one transaction "IA: Macro <name>" with backup, log and
        verification; simular=true returns its `plan` without touching the model.
        Example: run_macro(name="comentarios_por_nivel", args={"category": "OST_Walls"}, simular=true).

        Args:
            name: Macro name (folder name)
            args: Arguments as declared in macro.json (400 lists what is missing or unknown)
            simular: Only validate and return the macro's plan
            forzar: Required above 200 elements when the macro reports its scope
            timeout_s: Bridge timeout; default: the macro's `timeout_s` from its manifest (list_macros), else 600 s
        """
        crono = Cronometro()
        data = {"name": name, "args": args or {}, "simular": simular, "forzar": forzar}
        espera = float(timeout_s) if timeout_s else await _timeout_del_manifiesto(revit_get, name, ctx)
        response = await revit_post("/macros/run/", data, ctx, timeout=espera)
        return format_response(response, ms_puente=crono.ms())

    @mcp.tool()
    async def family_validate(
        family_doc: str,
        flex_cases: list[dict] = None,
        restore: bool = True,
        simular: bool = False,
        forzar: bool = False,
        ctx: Context = None,
    ) -> str:
        """MACRO (0.6.0): flex the family. Each case runs in a TransactionGroup "IA: Validar
        <case>" (switch type, set values, Regenerate) and checks every solid keeps a volume;
        Revit regeneration errors are reported per case. Without flex_cases, one case per
        type. restore=true (default) rolls every case back.
        Example: family_validate(family_doc="Placa base", flex_cases=[{"name": "extremo",
        "type": "PL300x300x20", "values": {"Espesor": 2}}]).

        Args:
            family_doc: Open family document
            flex_cases: [{"name", "type", "values": {parameter: value}}] (mm, degrees)
            restore: Roll back each case (false keeps the last case's values)
            simular: Only list the cases and the current solids
            forzar: Required above 200 cases
        """
        crono = Cronometro()
        data = {"family_doc": family_doc, "restore": restore, "simular": simular, "forzar": forzar}
        if flex_cases is not None:
            data["flex_cases"] = flex_cases
        response = await revit_post("/family/validate/", data, ctx, timeout=TIMEOUT_LARGO)
        return format_response(response, ms_puente=crono.ms())

    @mcp.tool()
    async def build_family_from_spec(
        spec: dict,
        save_path: str = None,
        load_into_project: bool = False,
        overwrite: bool = False,
        overwrite_parameters: bool = False,
        close: bool = False,
        simular: bool = False,
        forzar: bool = False,
        ctx: Context = None,
    ) -> str:
        """MACRO (0.6.0): build a whole family from a spec (name, template, category,
        parameters, reference_planes, dimensions, solids, connectors, types; see CONTRATO.md).
        The spec is validated before opening anything, then every step runs in order and a
        failure closes without saving (`failed_step`). Run simular=true first and show `plan`.
        Example: build_family_from_spec(spec={...}, save_path="C:\\Familias\\Placa.rfa", simular=true).

        Args:
            spec: The family specification (object)
            save_path: .rfa to save to (required with load_into_project)
            load_into_project: Load into the active project after saving
            overwrite: Replace an existing .rfa
            overwrite_parameters: Reload a family already loaded in the project
            close: Close the family document at the end
            simular: Only validate the spec; returns plan (counts and steps)
            forzar: Required above 200 elements in the spec
        """
        crono = Cronometro()
        if not isinstance(spec, dict):
            return format_response({"error": "spec must be an object", "status": "error"}, ms_puente=crono.ms())
        data = {"spec": spec, "load_into_project": load_into_project, "overwrite": overwrite,
                "overwrite_parameters": overwrite_parameters, "close": close, "simular": simular, "forzar": forzar}
        if save_path:
            data["save_path"] = save_path
        response = await revit_post("/family/build/", data, ctx, timeout=TIMEOUT_LARGO)
        return format_response(response, ms_puente=crono.ms())
