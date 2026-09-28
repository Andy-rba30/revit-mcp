# -*- coding: utf-8 -*-
"""Herramientas de escritura (0.4.0, 19). Todas aceptan `simular` (valida y
devuelve `haria` sin tocar el modelo) y devuelven `ok`, `verificacion`, `copia`
(copia del ultimo guardado), `ms` (Revit) y `ms_puente` (puente). Los lotes
(set_parameters, create_elements) van a las rutas nuevas /set_parameters/ y
/create_elements/ (una transaccion por llamada); el resto despacha a las rutas
de 0.3.x, y annotate, color_elements, export y maintain_model eligen la ruta
segun `kind`, `clear`, `format` o `action`."""

import csv
import io

from mcp.server.mcpserver import Context
from .utils import format_response, es_error, Cronometro, TIMEOUT_LECTURA, TIMEOUT_ESCRITURA, TIMEOUT_LARGO

_KIND_COTA = ("dimension", "dimensions", "cota", "cotas", "dim")
_KIND_ETIQUETA = ("tag", "tags", "etiqueta", "etiquetas")
_MUROS = ("ost_walls", "walls", "muros", "wall", "muro")
_FORMATOS_DOCUMENTO = ("pdf", "png", "jpg", "jpeg", "dwg")
_ACCIONES = ("purge", "backup", "save")
_KINDS_LARGOS = ("toposolid",)


def _texto_error(mensaje, **extra):
    datos = {"error": mensaje, "status": "error"}
    datos.update(extra)
    return datos


def _csv_habitaciones(habitaciones):
    """CSV (texto) con una fila por habitacion y una columna por clave."""
    columnas = []
    for habitacion in habitaciones:
        for clave in habitacion:
            if clave not in columnas:
                columnas.append(clave)
    buffer = io.StringIO()
    escritor = csv.writer(buffer, lineterminator="\n")
    escritor.writerow(columnas)
    for habitacion in habitaciones:
        escritor.writerow(["" if habitacion.get(c) is None else habitacion.get(c) for c in columnas])
    return buffer.getvalue()


def register_escritura_tools(mcp, revit_get, revit_post, revit_image=None):
    """Registra las 19 herramientas de escritura."""

    @mcp.tool()
    async def set_parameters(
        changes: list[dict] = None,
        element_ids: list[int] = None,
        parameters: dict = None,
        element_id: int = None,
        parameter_name: str = None,
        value: str | float | int | bool = None,
        type_parameters: bool = False,
        simular: bool = False,
        forzar: bool = False,
        ctx: Context = None,
    ) -> str:
        """Set instance or type parameters on many elements in ONE transaction. Lengths in
        mm, areas mm2, volumes mm3, angles deg; names as Revit shows them, English alias
        or BuiltInParameter. Read-only parameters are reported in `fallidos`, not fatal.
        Example: set_parameters(changes=[{"element_ids": [10, 11], "parameters":
        {"Comments": "Revisado", "Mark": "M-01"}}]).

        Args:
            changes: [{"element_ids": [...] | "element_id": id, "parameters": {name: value}, "type_parameters": bool}]
            element_ids: Shortcut: these ids get `parameters` (one change)
            parameters: {name: value} for element_ids
            element_id: Compatibility with set_parameter: one element
            parameter_name: Compatibility with set_parameter: one parameter
            value: Compatibility with set_parameter: its value
            type_parameters: true = set the parameters on the elements' TYPES (affects every instance)
            simular: Only validate; returns haria with antes/despues
            forzar: Required above 200 element x parameter pairs
        """
        crono = Cronometro()
        cambios = list(changes or [])
        if element_ids and parameters:
            cambios.append({"element_ids": element_ids, "parameters": parameters})
        if element_id is not None and parameter_name:
            cambios.append({"element_id": element_id, "parameter_name": parameter_name, "value": value})
        if not cambios:
            return format_response(_texto_error(
                "changes is required: [{\"element_ids\": [...], \"parameters\": {name: value}}]"), ms_puente=crono.ms())
        data = {"changes": cambios, "type_parameters": type_parameters, "simular": simular, "forzar": forzar}
        response = await revit_post("/set_parameters/", data, ctx, timeout=TIMEOUT_ESCRITURA)
        return format_response(response, ms_puente=crono.ms())

    @mcp.tool()
    async def create_elements(
        elements: list[dict],
        simular: bool = False,
        forzar: bool = False,
        ctx: Context = None,
    ) -> str:
        """Create elements of mixed kinds in ONE transaction (all validated first; if one
        fails nothing is created). Coordinates in mm. kind = wall, floor, roof, ceiling,
        level, grid, column, beam, foundation, opening, toposolid, room, room_separation,
        detail_line, duct, pipe, family_instance. Example: create_elements(elements=[{"kind":
        "wall", "start_point": {"x": 0, "y": 0, "z": 0}, "end_point": {"x": 5000, "y": 0, "z": 0}}]).

        Args:
            elements: [{"kind": ..., ...arguments of that kind (same as the 0.3 create_* routes)}]
            simular: Only validate; returns haria and plan (counts per kind)
            forzar: Required above 200 elements
        """
        crono = Cronometro()
        data = {"elements": elements, "simular": simular, "forzar": forzar}
        largo = any(isinstance(e, dict) and str(e.get("kind", "")).lower() in _KINDS_LARGOS for e in elements or [])
        response = await revit_post("/create_elements/", data, ctx,
                                    timeout=TIMEOUT_LARGO if largo else TIMEOUT_ESCRITURA)
        return format_response(response, ms_puente=crono.ms())

    @mcp.tool()
    async def transform_elements(
        element_ids: list[int],
        operation: str,
        vector: dict = None,
        axis_point: dict = None,
        angle: float = None,
        mirror_plane: dict = None,
        count: int = None,
        simular: bool = False,
        forzar: bool = False,
        ctx: Context = None,
    ) -> str:
        """Move, copy, rotate, mirror or array elements (mm, degrees). Returns bbox
        antes/despues; copies and arrays return `creados`.
        Example: transform_elements(element_ids=[1234], operation="array",
        vector={"x": 3000, "y": 0, "z": 0}, count=4).

        Args:
            element_ids: Elements to transform
            operation: "move", "copy", "rotate", "mirror" or "array"
            vector: {"x","y","z"} in mm (move, copy, array = step between copies)
            axis_point: Rotation centre {"x","y","z"} in mm (rotate)
            angle: Degrees (rotate)
            mirror_plane: {"origin": {"x","y","z"}, "normal": {"x","y","z"}} (mirror)
            count: Total copies in an array (>= 2)
            simular: Only validate
            forzar: Required above 200 elements
        """
        crono = Cronometro()
        data = {"element_ids": element_ids, "operation": operation, "simular": simular, "forzar": forzar}
        for clave, valor in (("vector", vector), ("axis_point", axis_point), ("angle", angle),
                             ("mirror_plane", mirror_plane), ("count", count)):
            if valor is not None:
                data[clave] = valor
        response = await revit_post("/transform_elements/", data, ctx, timeout=TIMEOUT_ESCRITURA)
        return format_response(response, ms_puente=crono.ms())

    @mcp.tool()
    async def delete_elements(
        element_ids: list[int],
        simular: bool = False,
        forzar: bool = False,
        ctx: Context = None,
    ) -> str:
        """Delete elements by id in one transaction; hosted elements go with them
        (`en_cascada`). List the ids with query_elements first and show them to the user.
        Example: delete_elements(element_ids=[1234, 1235], simular=true).

        Args:
            element_ids: Ids to delete
            simular: Only list what would be deleted
            forzar: Required above 200 elements
        """
        crono = Cronometro()
        data = {"element_ids": element_ids, "simular": simular, "forzar": forzar}
        response = await revit_post("/delete_elements/", data, ctx, timeout=TIMEOUT_ESCRITURA)
        return format_response(response, ms_puente=crono.ms())

    @mcp.tool()
    async def change_element_type(
        element_ids: list[int],
        type_name: str = None,
        type_id: int = None,
        simular: bool = False,
        forzar: bool = False,
        ctx: Context = None,
    ) -> str:
        """Change the type of one or more elements (Element.ChangeTypeId); pick the type
        from list_types. Returns antes/despues per element.
        Example: change_element_type(element_ids=[1234], type_name="Muro básico: Genérico - 300 mm").

        Args:
            element_ids: Elements to change
            type_name: Target type, or "Family: Type" when ambiguous
            type_id: Target type id (alternative)
            simular: Only validate
            forzar: Required above 200 elements
        """
        crono = Cronometro()
        data = {"element_ids": element_ids, "simular": simular, "forzar": forzar}
        if type_name is not None:
            data["type_name"] = type_name
        if type_id is not None:
            data["type_id"] = type_id
        response = await revit_post("/change_type/", data, ctx, timeout=TIMEOUT_ESCRITURA)
        return format_response(response, ms_puente=crono.ms())

    @mcp.tool()
    async def join_geometry(
        element_id_a: int,
        element_id_b: int,
        unjoin: bool = False,
        simular: bool = False,
        ctx: Context = None,
    ) -> str:
        """Join or unjoin the geometry of two elements (wall and floor, column and beam).
        Returns joined antes/despues. Example: join_geometry(element_id_a=1234, element_id_b=5678).

        Args:
            element_id_a: First element
            element_id_b: Second element
            unjoin: true to separate elements already joined
            simular: Only validate
        """
        crono = Cronometro()
        data = {"element_id_a": element_id_a, "element_id_b": element_id_b, "unjoin": unjoin, "simular": simular}
        response = await revit_post("/join_geometry/", data, ctx, timeout=TIMEOUT_ESCRITURA)
        return format_response(response, ms_puente=crono.ms())

    @mcp.tool()
    async def set_workset(
        element_ids: list[int],
        workset_name: str,
        simular: bool = False,
        forzar: bool = False,
        ctx: Context = None,
    ) -> str:
        """Move elements to another workset of a workshared model. Check the worksets with
        get_revit_model_info(include=["worksets"]) first; 409 if an element is borrowed.
        Example: set_workset(element_ids=[1234], workset_name="Estructura").

        Args:
            element_ids: Elements to move
            workset_name: Target user workset
            simular: Only validate
            forzar: Required above 200 elements
        """
        crono = Cronometro()
        data = {"element_ids": element_ids, "workset_name": workset_name, "simular": simular, "forzar": forzar}
        response = await revit_post("/set_workset/", data, ctx, timeout=TIMEOUT_ESCRITURA)
        return format_response(response, ms_puente=crono.ms())

    @mcp.tool()
    async def set_project_location(
        base_point_mm: dict = None,
        survey_point_mm: dict = None,
        true_north_deg: float = None,
        acquire_from_link_id: int = None,
        simular: bool = False,
        forzar: bool = False,
        ctx: Context = None,
    ) -> str:
        """Move the project base point or survey point, rotate true north, or acquire
        shared coordinates from a link. Read get_revit_model_info(include=["location"])
        first; 409 if a point is pinned or clipped unless forzar.
        Example: set_project_location(true_north_deg=12.5, simular=true).

        Args:
            base_point_mm: New internal position {"x","y","z"} of the project base point
            survey_point_mm: New internal position of the survey point
            true_north_deg: New true north angle (counter-clockwise from project north)
            acquire_from_link_id: Link instance id to acquire coordinates from (alone)
            simular: Only validate
            forzar: Move a pinned or clipped point anyway
        """
        crono = Cronometro()
        data = {"simular": simular, "forzar": forzar}
        for clave, valor in (("base_point_mm", base_point_mm), ("survey_point_mm", survey_point_mm),
                             ("true_north_deg", true_north_deg), ("acquire_from_link_id", acquire_from_link_id)):
            if valor is not None:
                data[clave] = valor
        response = await revit_post("/set_project_location/", data, ctx, timeout=TIMEOUT_ESCRITURA)
        return format_response(response, ms_puente=crono.ms())

    @mcp.tool()
    async def create_view(
        view_type: str,
        name: str,
        level_name: str = None,
        section_box: dict = None,
        simular: bool = False,
        ctx: Context = None,
    ) -> str:
        """Create a floor plan, ceiling plan, section, elevation or 3D view. Plans need
        level_name; sections need section_box (origin, direction, up, width, height, depth in mm).
        Example: create_view(view_type="floor_plan", name="P1 - Estructura", level_name="Nivel 1").

        Args:
            view_type: "floor_plan", "ceiling_plan", "section", "elevation" or "3d"
            name: View name
            level_name: Level for plans
            section_box: Cut definition for sections
            simular: Only validate
        """
        crono = Cronometro()
        data = {"view_type": view_type, "name": name, "simular": simular}
        if level_name is not None:
            data["level_name"] = level_name
        if section_box is not None:
            data["section_box"] = section_box
        response = await revit_post("/create_view/", data, ctx, timeout=TIMEOUT_ESCRITURA)
        return format_response(response, ms_puente=crono.ms())

    @mcp.tool()
    async def set_active_view(view_name: str, simular: bool = False, ctx: Context = None) -> str:
        """Switch the active view in Revit (exact name from list_views).
        Example: set_active_view(view_name="Planta Nivel 1").

        Args:
            view_name: View to activate
            simular: Only validate
        """
        crono = Cronometro()
        response = await revit_post("/set_active_view/", {"view_name": view_name, "simular": simular}, ctx,
                                    timeout=TIMEOUT_ESCRITURA)
        return format_response(response, ms_puente=crono.ms())

    @mcp.tool()
    async def create_sheet_set(
        sheets: list[dict],
        simular: bool = False,
        forzar: bool = False,
        ctx: Context = None,
    ) -> str:
        """Create one or more sheets with views placed, in one transaction. A single sheet is
        `sheets` with one element. Views already on a sheet are reported in `skipped`.
        Example: create_sheet_set(sheets=[{"number": "E-101", "name": "Planta cimentación",
        "title_block": "A1 métrico", "views": [{"view_name": "Cimentación"}]}]).

        Args:
            sheets: [{"number", "name", "title_block" (optional), "views": [{"view_name" | "view_id", "position_mm": {"x","y"}}]}]
            simular: Only validate; returns plan
            forzar: Required above 200 sheets + views
        """
        crono = Cronometro()
        data = {"sheets": sheets, "simular": simular, "forzar": forzar}
        response = await revit_post("/sheet_set/", data, ctx, timeout=TIMEOUT_ESCRITURA)
        return format_response(response, ms_puente=crono.ms())

    @mcp.tool()
    async def create_schedule(
        category: str,
        fields: list[str] = None,
        schedule_name: str = None,
        simular: bool = False,
        ctx: Context = None,
    ) -> str:
        """Create a schedule (quantity table) for a category with the given parameter columns.
        Example: create_schedule(category="OST_Walls", fields=["Type", "Length", "Area"],
        schedule_name="Muros").

        Args:
            category: BuiltInCategory name
            fields: Parameter names as columns (optional default set)
            schedule_name: Name of the schedule view
            simular: Only validate
        """
        crono = Cronometro()
        data = {"category": category, "fields": fields, "schedule_name": schedule_name, "simular": simular}
        response = await revit_post("/create_schedule/", data, ctx, timeout=TIMEOUT_ESCRITURA)
        return format_response(response, ms_puente=crono.ms())

    @mcp.tool()
    async def annotate(
        kind: str,
        element_ids: list[int] = None,
        category: str = None,
        view_name: str = None,
        dimension_type: str = "linear",
        tag_type_name: str = None,
        add_leader: bool = False,
        orientation: str = "horizontal",
        offset: dict = None,
        simular: bool = False,
        ctx: Context = None,
    ) -> str:
        """Dimension or tag elements. kind="dimension" dimensions element_ids in the active
        view; kind="tag" tags element_ids (any taggable category), or with
        category="OST_Walls" and no ids tags every untagged wall of the active view.
        Example: annotate(kind="tag", element_ids=[1234, 1235], add_leader=true).

        Args:
            kind: "dimension" or "tag"
            element_ids: Elements to dimension or tag
            category: With kind="tag" and no element_ids: "OST_Walls" tags all walls in view
            view_name: View for the tags (default: active view)
            dimension_type: "linear", "aligned" or "angular"
            tag_type_name: Tag type (default: first suitable)
            add_leader: Leader line
            orientation: "horizontal" or "vertical"
            offset: Tag offset {"x","y"} in mm
            simular: Only validate
        """
        crono = Cronometro()
        clase = str(kind or "").strip().lower()
        if clase in _KIND_COTA:
            if not element_ids:
                return format_response(_texto_error("element_ids is required for kind=dimension"), ms_puente=crono.ms())
            data = {"element_ids": element_ids, "dimension_type": dimension_type, "simular": simular}
            response = await revit_post("/create_dimensions/", data, ctx, timeout=TIMEOUT_ESCRITURA)
            return format_response(response, ms_puente=crono.ms())
        if clase in _KIND_ETIQUETA:
            if element_ids:
                data = {"element_ids": element_ids, "add_leader": add_leader, "orientation": orientation,
                        "simular": simular}
                for clave, valor in (("view_name", view_name), ("tag_type_name", tag_type_name), ("offset", offset)):
                    if valor is not None:
                        data[clave] = valor
                response = await revit_post("/tag_elements/", data, ctx, timeout=TIMEOUT_ESCRITURA)
                return format_response(response, ms_puente=crono.ms())
            if str(category or "").strip().lower() in _MUROS:
                data = {"use_leader": add_leader, "tag_type_name": tag_type_name, "simular": simular}
                response = await revit_post("/tag_walls/", data, ctx, timeout=TIMEOUT_ESCRITURA)
                return format_response(response, ms_puente=crono.ms())
            return format_response(_texto_error(
                "kind=tag needs element_ids, or category=\"OST_Walls\" to tag every wall of the active view"),
                ms_puente=crono.ms())
        return format_response(_texto_error(
            "kind '{}' not supported: use \"dimension\" or \"tag\"".format(kind)), ms_puente=crono.ms())

    @mcp.tool()
    async def color_elements(
        category_name: str,
        parameter_name: str = None,
        use_gradient: bool = False,
        custom_colors: list[str] = None,
        clear: bool = False,
        simular: bool = False,
        ctx: Context = None,
    ) -> str:
        """Colour the elements of a category in the active view by the value of a parameter,
        or clear those overrides with clear=true.
        Example: color_elements(category_name="Walls", parameter_name="Mark");
        color_elements(category_name="Walls", clear=true).

        Args:
            category_name: Category to colour (as the route expects it)
            parameter_name: Parameter whose values pick the colours (required unless clear)
            use_gradient: Gradient instead of distinct colours
            custom_colors: Hex colours to use
            clear: Remove the colour overrides instead
            simular: Only validate
        """
        crono = Cronometro()
        if clear:
            response = await revit_post("/clear_colors/", {"category_name": category_name, "simular": simular},
                                        ctx, timeout=TIMEOUT_ESCRITURA)
            return format_response(response, ms_puente=crono.ms())
        if not parameter_name:
            return format_response(_texto_error("parameter_name is required (or clear=true)"), ms_puente=crono.ms())
        data = {"category_name": category_name, "parameter_name": parameter_name, "use_gradient": use_gradient,
                "simular": simular}
        if custom_colors:
            data["custom_colors"] = custom_colors
        response = await revit_post("/color_splash/", data, ctx, timeout=TIMEOUT_ESCRITURA)
        return format_response(response, ms_puente=crono.ms())

    @mcp.tool()
    async def export(
        format: str,
        file_path: str = None,
        view_name: str = None,
        resolution: int = 300,
        ifc_version: str = "IFC2x3",
        export_base_quantities: bool = True,
        ctx: Context = None,
    ) -> str:
        """Export a view or sheet (pdf, png, jpg, dwg), the model to IFC, or the room data
        (rooms_json, rooms_csv). Example: export(format="pdf", view_name="E-101");
        export(format="ifc", file_path="C:\\\\Proyectos\\\\modelo.ifc");
        export(format="rooms_csv", file_path="C:\\\\Proyectos\\\\habitaciones.csv").

        Args:
            format: "pdf", "png", "jpg", "dwg", "ifc", "rooms_json" or "rooms_csv"
            file_path: Output path (required for ifc; optional for rooms_csv, written by the bridge)
            view_name: View or sheet to export (default: active view; ifc: only its visible elements)
            resolution: DPI for png/jpg
            ifc_version: "IFC2x3" or "IFC4"
            export_base_quantities: IFC base quantities
        """
        crono = Cronometro()
        formato = str(format or "").strip().lower()
        if formato in _FORMATOS_DOCUMENTO:
            data = {"view_name": view_name, "format": formato, "resolution": resolution}
            response = await revit_post("/export_document/", data, ctx, timeout=TIMEOUT_LARGO)
            return format_response(response, ms_puente=crono.ms())
        if formato == "ifc":
            if not file_path:
                return format_response(_texto_error("file_path is required for format=ifc"), ms_puente=crono.ms())
            data = {"file_path": file_path, "ifc_version": ifc_version,
                    "export_base_quantities": export_base_quantities}
            if view_name is not None:
                data["view_name"] = view_name
            response = await revit_post("/export_ifc/", data, ctx, timeout=TIMEOUT_LARGO)
            return format_response(response, ms_puente=crono.ms())
        if formato in ("rooms_json", "rooms", "rooms_csv"):
            response = await revit_get("/room_data/", ctx)
            if formato != "rooms_csv" or es_error(response):
                return format_response(response, ms_puente=crono.ms())
            habitaciones = response.get("rooms") or []
            texto = _csv_habitaciones(habitaciones)
            resultado = {"format": "rooms_csv", "rows": len(habitaciones), "status": "success"}
            if file_path:
                try:
                    with io.open(file_path, "w", encoding="utf-8", newline="") as archivo:
                        archivo.write(texto)
                    resultado["file_path"] = file_path
                except OSError as error:
                    return format_response(_texto_error("could not write {}: {}".format(file_path, error)),
                                           ms_puente=crono.ms())
            else:
                resultado["csv"] = texto
            return format_response(resultado, ms_puente=crono.ms())
        return format_response(_texto_error(
            "format '{}' not supported: use pdf, png, jpg, dwg, ifc, rooms_json or rooms_csv".format(format)),
            ms_puente=crono.ms())

    @mcp.tool()
    async def link_file(
        file_path: str,
        mode: str = "link",
        position: dict = None,
        simular: bool = False,
        ctx: Context = None,
    ) -> str:
        """Link or import an external file (DWG, DXF, DGN, RVT...) into the model.
        Example: link_file(file_path="C:\\\\Proyectos\\\\topografia.dwg", mode="link").

        Args:
            file_path: Full path on the Revit machine
            mode: "link" (keeps the connection) or "import" (embeds a copy)
            position: Placement offset {"x","y","z"} in mm
            simular: Only validate
        """
        crono = Cronometro()
        data = {"file_path": file_path, "mode": mode, "simular": simular}
        if position is not None:
            data["position"] = position
        response = await revit_post("/link_file/", data, ctx, timeout=TIMEOUT_LARGO)
        return format_response(response, ms_puente=crono.ms())

    @mcp.tool()
    async def load_family(file_path: str, simular: bool = False, ctx: Context = None) -> str:
        """Load a family (.rfa) from disk so its types can be placed with create_elements
        (kind="family_instance"). Example: load_family(file_path="C:\\\\Familias\\\\Pilar HEB.rfa").

        Args:
            file_path: Full path to the .rfa on the Revit machine
            simular: Only validate
        """
        crono = Cronometro()
        response = await revit_post("/load_family/", {"file_path": file_path, "simular": simular}, ctx,
                                    timeout=TIMEOUT_LARGO)
        return format_response(response, ms_puente=crono.ms())

    @mcp.tool()
    async def create_mep_system(
        system_type: str,
        system_name: str,
        element_ids: list[int] = None,
        simular: bool = False,
        ctx: Context = None,
    ) -> str:
        """Group ducts or pipes into a named mechanical or piping system (ducts and pipes
        are created with create_elements). Example: create_mep_system(system_type="mechanical",
        system_name="Impulsión P1", element_ids=[4001, 4002]).

        Args:
            system_type: "mechanical" or "piping"
            system_name: System name
            element_ids: Duct/pipe ids to add
            simular: Only validate
        """
        crono = Cronometro()
        data = {"system_type": system_type, "system_name": system_name, "simular": simular}
        if element_ids is not None:
            data["element_ids"] = element_ids
        response = await revit_post("/create_mep_system/", data, ctx, timeout=TIMEOUT_ESCRITURA)
        return format_response(response, ms_puente=crono.ms())

    @mcp.tool()
    async def maintain_model(
        action: str,
        max_rounds: int = 3,
        suffix: str = None,
        file_path: str = None,
        overwrite: bool = True,
        simular: bool = False,
        forzar: bool = False,
        ctx: Context = None,
    ) -> str:
        """Purge unused families and types, make an on-demand backup of the saved .rvt, or
        save the document (only when the user asks). Always purge with simular=true first.
        Example: maintain_model(action="purge", simular=true); maintain_model(action="backup",
        suffix="antes_de_purgar"); maintain_model(action="save").

        Args:
            action: "purge", "backup" or "save"
            max_rounds: Purge rounds (purge)
            suffix: Label appended to the backup file name (backup)
            file_path: Save-As path (save; required the first time a template model is saved)
            overwrite: Overwrite an existing file (save)
            simular: Only validate / list what would be purged
            forzar: Purge more than 200 types (purge)
        """
        crono = Cronometro()
        accion = str(action or "").strip().lower()
        if accion == "purge":
            data = {"max_rounds": max_rounds, "simular": simular, "forzar": forzar}
            response = await revit_post("/purge_unused/", data, ctx, timeout=TIMEOUT_LARGO)
        elif accion == "backup":
            data = {"simular": simular}
            if suffix:
                data["suffix"] = suffix
            response = await revit_post("/backup/", data, ctx, timeout=TIMEOUT_LARGO)
        elif accion == "save":
            data = {"file_path": file_path, "overwrite": overwrite, "simular": simular}
            response = await revit_post("/save_document/", data, ctx, timeout=TIMEOUT_LARGO)
        else:
            response = _texto_error("action '{}' not supported: use {}".format(action, ", ".join(_ACCIONES)))
        return format_response(response, ms_puente=crono.ms())
