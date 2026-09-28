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
_FORMATOS_ESTRUCTURALES = ("ifc_structural", "csv_nodes_members")
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
        antes/despues; copies and arrays return `creados`. Pinned elements can be
        copied or arrayed; move, rotate and mirror reject them (400, pinned_ids).
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
        element_id_a: int = None,
        element_id_b: int = None,
        element_ids: list[int] = None,
        unjoin: bool = False,
        coping: bool = False,
        simular: bool = False,
        forzar: bool = False,
        ctx: Context = None,
    ) -> str:
        """Join or unjoin the geometry of two elements, or of a chain (`element_ids`, joined
        in consecutive pairs; `coping=true` also cuts steel members with AddCoping). Returns
        joined antes/despues. Example: join_geometry(element_ids=[1234, 5678, 9012], coping=true).

        Args:
            element_id_a: First element (two-element form)
            element_id_b: Second element (two-element form)
            element_ids: Chain of 2+ elements joined pair by pair (alternative)
            unjoin: true to separate elements already joined
            coping: With element_ids: FamilyInstance.AddCoping on each pair (steel beams/columns)
            simular: Only validate
            forzar: Required above 200 elements in a chain
        """
        crono = Cronometro()
        if element_ids:
            data = {"element_ids": element_ids, "unjoin": unjoin, "coping": coping, "simular": simular, "forzar": forzar}
        else:
            if element_id_a is None or element_id_b is None:
                return format_response(_texto_error(
                    "element_id_a and element_id_b are required (or element_ids for a chain)"), ms_puente=crono.ms())
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
            category_name: BuiltInCategory ("OST_Walls"), alias ("walls") or visible name
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
        element_ids: list[int] = None,
        ctx: Context = None,
    ) -> str:
        """Export a view or sheet (pdf, png, jpg, dwg), the model to IFC, the room data
        (rooms_json, rooms_csv) or the structural model (ifc_structural: IFC with base
        quantities filtered by the active analytical view; csv_nodes_members: one row per
        analytical member with nodes and releases). Example: export(format="pdf", view_name="E-101");
        export(format="csv_nodes_members", file_path="C:\\\\Proyectos\\\\miembros.csv").

        Args:
            format: "pdf", "png", "jpg", "dwg", "ifc", "rooms_json", "rooms_csv", "ifc_structural" or "csv_nodes_members"
            file_path: Output path (required for ifc, ifc_structural and csv_nodes_members; optional for rooms_csv)
            view_name: View or sheet to export (default: active view; ifc: only its visible elements)
            resolution: DPI for png/jpg
            ifc_version: "IFC2x3" or "IFC4"
            export_base_quantities: IFC base quantities
            element_ids: csv_nodes_members only: elements to export (empty = every steel element)
        """
        crono = Cronometro()
        formato = str(format or "").strip().lower()
        if formato in _FORMATOS_ESTRUCTURALES:
            if not file_path:
                return format_response(_texto_error("file_path is required for format={}".format(formato)), ms_puente=crono.ms())
            data = {"format": formato, "file_path": file_path, "ifc_version": ifc_version}
            if view_name is not None:
                data["view_name"] = view_name
            if element_ids:
                data["element_ids"] = element_ids
            response = await revit_post("/export_structural/", data, ctx, timeout=TIMEOUT_LARGO)
            return format_response(response, ms_puente=crono.ms())
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
            "format '{}' not supported: use pdf, png, jpg, dwg, ifc, rooms_json, rooms_csv, ifc_structural or "
            "csv_nodes_members".format(format)), ms_puente=crono.ms())

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

    # ---- 0.5.0 (entrega 2b): estructuras metalicas -----------------------------
    @mcp.tool()
    async def load_steel_profile(
        file_path: str = None,
        family_name: str = None,
        type_names: list[str] = None,
        overwrite: bool = False,
        simular: bool = False,
        ctx: Context = None,
    ) -> str:
        """Load steel profile types: LoadFamilySymbol per type when the .rfa has a .txt type
        catalog (type_names required), LoadFamily otherwise. 409 if already loaded unless
        overwrite. Example: load_steel_profile(family_name="W-Wide Flange", type_names=["W12X26"]).

        Args:
            file_path: Full path of the .rfa on the Revit machine
            family_name: Alternative: <name>.rfa searched in Application.GetLibraryPaths()
            type_names: Catalog types to load (or the family types to activate)
            overwrite: Reload a family that is already loaded
            simular: Only validate; returns plan
        """
        crono = Cronometro()
        if not file_path and not family_name:
            return format_response(_texto_error("file_path or family_name is required"), ms_puente=crono.ms())
        data = {"overwrite": overwrite, "simular": simular}
        for clave, valor in (("file_path", file_path), ("family_name", family_name), ("type_names", type_names)):
            if valor is not None:
                data[clave] = valor
        response = await revit_post("/load_steel_profile/", data, ctx, timeout=TIMEOUT_LARGO)
        return format_response(response, ms_puente=crono.ms())

    @mcp.tool()
    async def create_steel_frame(
        column_type: str,
        beam_type: str,
        grids_x: list[str] = None,
        grids_y: list[str] = None,
        levels: list[str] = None,
        beam_directions: str = "both",
        column_orientation_deg: float = 0,
        skip_columns_at: list[str] = None,
        skip_beams_at: list[str] = None,
        mark_prefix: str = None,
        simular: bool = False,
        forzar: bool = False,
        ctx: Context = None,
    ) -> str:
        """MACRO: steel frame in ONE transaction: a column at every grid intersection of each
        level (top = next level) and beams between consecutive columns. Run with simular=true
        first and show `plan.counts`. Example: create_steel_frame(column_type="HEB300",
        beam_type="IPE300", levels=["Nivel 1"], mark_prefix="P").

        Args:
            column_type: Structural column type (or "Family: Type")
            beam_type: Structural framing type
            grids_x: Grid names with constant X (1, 2, 3...); empty = all
            grids_y: Grid names with constant Y (A, B, C...); empty = all
            levels: Level names; empty = all
            beam_directions: "x", "y" or "both"
            column_orientation_deg: Column rotation
            skip_columns_at: Intersections without column ("A-1")
            skip_beams_at: Bays without beam ("A-1/A-2")
            mark_prefix: Sequential marks (ALL_MODEL_MARK) with this prefix
            simular: Only validate; returns plan
            forzar: Required above 200 elements
        """
        crono = Cronometro()
        data = {"column_type": column_type, "beam_type": beam_type, "beam_directions": beam_directions,
                "column_orientation_deg": column_orientation_deg, "simular": simular, "forzar": forzar}
        for clave, valor in (("grids_x", grids_x), ("grids_y", grids_y), ("levels", levels),
                             ("skip_columns_at", skip_columns_at), ("skip_beams_at", skip_beams_at), ("mark_prefix", mark_prefix)):
            if valor is not None:
                data[clave] = valor
        response = await revit_post("/create_steel_frame/", data, ctx, timeout=TIMEOUT_LARGO)
        return format_response(response, ms_puente=crono.ms())

    @mcp.tool()
    async def create_bracing(
        bays: list[dict],
        brace_type: str,
        simular: bool = False,
        forzar: bool = False,
        ctx: Context = None,
    ) -> str:
        """Braces (StructuralType.Brace) per bay in ONE transaction; pattern single, X, V,
        inverted_V or K, heights from the levels (internal elevation), points in mm.
        Example: create_bracing(bays=[{"start_point_mm": {"x": 0, "y": 0}, "end_point_mm":
        {"x": 6000, "y": 0}, "level_bottom": "Nivel 1", "level_top": "Nivel 2", "pattern": "X"}], brace_type="L100x100x10").

        Args:
            bays: [{"start_point_mm", "end_point_mm", "level_bottom", "level_top", "pattern"}]
            brace_type: Structural framing type used as brace
            simular: Only validate; returns plan
            forzar: Required above 200 braces
        """
        crono = Cronometro()
        data = {"bays": bays, "brace_type": brace_type, "simular": simular, "forzar": forzar}
        response = await revit_post("/create_bracing/", data, ctx, timeout=TIMEOUT_ESCRITURA)
        return format_response(response, ms_puente=crono.ms())

    @mcp.tool()
    async def create_truss(
        trusses: list[dict],
        simular: bool = False,
        forzar: bool = False,
        ctx: Context = None,
    ) -> str:
        """Trusses (Truss.Create on a sketch plane at the level) in ONE transaction; 404 with
        the available types if truss_type does not exist. Example: create_truss(trusses=[
        {"truss_type": "Cercha 12 m", "start_point_mm": {"x": 0, "y": 0}, "end_point_mm":
        {"x": 12000, "y": 0}, "level": "Cubierta"}]).

        Args:
            trusses: [{"truss_type", "start_point_mm", "end_point_mm", "level"}]
            simular: Only validate; returns plan
            forzar: Required above 200 trusses
        """
        crono = Cronometro()
        data = {"trusses": trusses, "simular": simular, "forzar": forzar}
        response = await revit_post("/create_truss/", data, ctx, timeout=TIMEOUT_ESCRITURA)
        return format_response(response, ms_puente=crono.ms())

    @mcp.tool()
    async def set_structural_properties(
        element_ids: list[int],
        start_release: str | dict = None,
        end_release: str | dict = None,
        y_justification: str | int = None,
        z_justification: str | int = None,
        y_offset_mm: float = None,
        z_offset_mm: float = None,
        section_rotation_deg: float = None,
        start_extension_mm: float = None,
        end_extension_mm: float = None,
        analyze_as: str | int = None,
        structural_usage: str | int = None,
        simular: bool = False,
        forzar: bool = False,
        ctx: Context = None,
    ) -> str:
        """Structural properties of beams, braces and columns in ONE transaction, by
        BuiltInParameter (releases fall back to the AnalyticalMember in Revit 2023+). Returns
        antes/despues per element, `fallidos` and `no_disponibles` per Revit version.
        Example: set_structural_properties(element_ids=[10, 11], start_release="pinned",
        end_release={"FX": true, "MZ": true}, y_justification="center").

        Args:
            element_ids: Elements to change
            start_release: "pinned", "fixed", "bending_moment" or {"FX": bool, ... "MZ": bool}
            end_release: Same for the end
            y_justification: "origin", "left", "center", "right" or the enum index
            z_justification: "origin", "top", "center", "bottom" or the enum index
            y_offset_mm: Y offset
            z_offset_mm: Z offset
            section_rotation_deg: Cross-section rotation
            start_extension_mm: Start extension
            end_extension_mm: End extension
            analyze_as: AnalyzeAs name ("gravity", "lateral", "not_for_analysis") or index
            structural_usage: StructuralInstanceUsage name ("girder", "joist", "column"...) or index
            simular: Only validate; returns haria
            forzar: Required above 200 element x property pairs
        """
        crono = Cronometro()
        data = {"element_ids": element_ids, "simular": simular, "forzar": forzar}
        for clave, valor in (("start_release", start_release), ("end_release", end_release),
                             ("y_justification", y_justification), ("z_justification", z_justification),
                             ("y_offset_mm", y_offset_mm), ("z_offset_mm", z_offset_mm),
                             ("section_rotation_deg", section_rotation_deg), ("start_extension_mm", start_extension_mm),
                             ("end_extension_mm", end_extension_mm), ("analyze_as", analyze_as),
                             ("structural_usage", structural_usage)):
            if valor is not None:
                data[clave] = valor
        response = await revit_post("/set_structural_properties/", data, ctx, timeout=TIMEOUT_ESCRITURA)
        return format_response(response, ms_puente=crono.ms())

    @mcp.tool()
    async def create_steel_connection(
        connections: list[dict],
        approve: bool = False,
        approval_status: str = None,
        simular: bool = False,
        forzar: bool = False,
        ctx: Context = None,
    ) -> str:
        """Steel connections (StructuralConnectionHandler.Create) in ONE transaction; pick the
        type from list_types(category="connections"). 409 no_soportado without the Steel
        Connections module. Example: create_steel_connection(connections=[{"element_ids":
        [10, 12], "connection_type": "Conexión genérica"}]).

        Args:
            connections: [{"element_ids": [...], "connection_type": name or id}]
            approve: Also set the approval status (needs approval_status)
            approval_status: Approval type name as Revit shows it (list_types connections -> approval_types) or id
            simular: Only validate
            forzar: Required above 200 connections
        """
        crono = Cronometro()
        data = {"connections": connections, "approve": approve, "simular": simular, "forzar": forzar}
        if approval_status is not None:
            data["approval_status"] = approval_status
        response = await revit_post("/create_steel_connection/", data, ctx, timeout=TIMEOUT_ESCRITURA)
        return format_response(response, ms_puente=crono.ms())

    @mcp.tool()
    async def add_plate_or_stiffener(
        host_id: int,
        family_name: str,
        type_name: str,
        positions: list[float] = None,
        face: str = "top",
        simular: bool = False,
        ctx: Context = None,
    ) -> str:
        """Place plates or stiffeners along a beam or column in one transaction: a face-based
        family goes on the top, bottom or web face (references from get_Geometry); a
        point-based family on the axis. Positions in mm from the start, or fractions 0-1.
        Example: add_plate_or_stiffener(host_id=1234, family_name="Rigidizador", type_name="PL10", positions=[0.25, 0.75]).

        Args:
            host_id: Beam, brace or column
            family_name: Loaded family
            type_name: Its type
            positions: mm from the start, or fractions (<= 1); default [0.5]
            face: "top", "bottom" or "web" (face-based families)
            simular: Only validate
        """
        crono = Cronometro()
        data = {"host_id": host_id, "family_name": family_name, "type_name": type_name, "face": face, "simular": simular}
        if positions is not None:
            data["positions"] = positions
        response = await revit_post("/add_plate/", data, ctx, timeout=TIMEOUT_ESCRITURA)
        return format_response(response, ms_puente=crono.ms())

    @mcp.tool()
    async def split_beam(
        element_id: int,
        at_mm: list[float],
        simular: bool = False,
        ctx: Context = None,
    ) -> str:
        """Split a straight beam at distances from its start: the original keeps the first
        segment and each further segment is a copy (CopyElement + LocationCurve). `avisos`
        warns that joins and connections of the original are lost.
        Example: split_beam(element_id=1234, at_mm=[2000, 4000]).

        Args:
            element_id: Beam or brace with a straight location curve
            at_mm: Cut positions in mm from the start (inside the beam)
            simular: Only validate
        """
        crono = Cronometro()
        data = {"element_id": element_id, "at_mm": at_mm, "simular": simular}
        response = await revit_post("/split_beam/", data, ctx, timeout=TIMEOUT_ESCRITURA)
        return format_response(response, ms_puente=crono.ms())

    @mcp.tool()
    async def fix_analytical_alignment(
        element_ids: list[int],
        tolerance_mm: float = 50,
        simular: bool = False,
        forzar: bool = False,
        ctx: Context = None,
    ) -> str:
        """Snap the loose analytical nodes of these elements to the nearest foreign node within
        tolerance_mm (AnalyticalMember.SetCurve) in ONE transaction; antes/despues per node,
        `sin_objetivo` for nodes with nothing near. Run analytical_status first and simular=true.
        Example: fix_analytical_alignment(element_ids=[1234], tolerance_mm=50, simular=true).

        Args:
            element_ids: Physical elements whose analytical members are aligned
            tolerance_mm: Maximum distance a node is moved (default 50)
            simular: Only list the planned moves
            forzar: Required above 200 node moves
        """
        crono = Cronometro()
        data = {"element_ids": element_ids, "tolerance_mm": tolerance_mm, "simular": simular, "forzar": forzar}
        response = await revit_post("/fix_analytical/", data, ctx, timeout=TIMEOUT_LARGO)
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
