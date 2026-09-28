# -*- coding: utf-8 -*-
"""Herramientas de lectura (0.4.0, 16): ninguna cambia el modelo (snapshot_model
solo escribe un .json en snapshots\\). Varias combinan dos rutas existentes en
una sola llamada MCP (get_revit_model_info con `include`, list_views con
`on_sheet`, describe_view sin `view_id`, query_elements con `current_view` o
`selected`, describe_element con `element_ids`, list_types, analyze_model).
`ms_puente` es el tiempo total de esas llamadas visto desde el puente."""

from mcp.server.mcpserver import Context
from .utils import format_response, es_error, Cronometro, TIMEOUT_LECTURA, TIMEOUT_LARGO

MAX_DESCRIBE = 20
MAX_PAGINAS_VISTAS = 20

# Bloques opcionales de get_revit_model_info -> ruta que los aporta.
_BLOQUES_MODELO = {
    "levels": "/list_levels/",
    "worksets": "/worksets/",
    "phases": "/phases_options/",
    "links": "/links/",
    "location": "/project_location/",
}

# view_type de list_views -> clave de views_by_type de /list_views/.
_TIPOS_VISTA = {
    "floor_plans": "floor_plans", "floor_plan": "floor_plans", "plan": "floor_plans", "planta": "floor_plans",
    "ceiling_plans": "ceiling_plans", "ceiling_plan": "ceiling_plans", "techo": "ceiling_plans",
    "elevations": "elevations", "elevation": "elevations", "alzado": "elevations",
    "sections": "sections", "section": "sections", "seccion": "sections",
    "3d_views": "3d_views", "3d": "3d_views", "threed": "3d_views",
    "drafting_views": "drafting_views", "drafting": "drafting_views", "drafting_view": "drafting_views",
    "schedules": "schedules", "schedule": "schedules", "tabla": "schedules",
    "other": "other", "otras": "other",
}
_SIN_PLANO = ("", "---", "-", None)


def _texto_error(mensaje, **extra):
    datos = {"error": mensaje, "status": "error"}
    datos.update(extra)
    return datos


def register_lectura_tools(mcp, revit_get, revit_post, revit_image=None):
    """Registra las 16 herramientas de lectura."""

    @mcp.tool()
    async def get_revit_status(ctx: Context = None) -> str:
        """Check that Revit answers and which document is open. Call it first in
        every session. No arguments. Example: get_revit_status()."""
        crono = Cronometro()
        response = await revit_get("/status/", ctx, timeout=10.0)
        return format_response(response, ms_puente=crono.ms())

    @mcp.tool()
    async def get_revit_model_info(include: list[str] = None, ctx: Context = None) -> str:
        """Model summary (project, counts, warnings, views, sheets, rooms and the `file`
        block: path, is_workshared, units, base points, true north). Add blocks with
        `include`: "levels", "worksets", "phases", "links", "location".
        Example: get_revit_model_info(include=["levels", "location"]).

        Args:
            include: Extra blocks; each one is another read route merged under its name
        """
        crono = Cronometro()
        response = await revit_get("/model_info/", ctx)
        if es_error(response):
            return format_response(response, ms_puente=crono.ms())
        desconocidos = []
        for bloque in include or []:
            clave = str(bloque).strip().lower()
            ruta = _BLOQUES_MODELO.get(clave)
            if ruta is None:
                desconocidos.append(bloque)
                continue
            response[clave] = await revit_get(ruta, ctx)
        if desconocidos:
            response["warnings"] = ["include desconocido: {} (usa {})".format(
                ", ".join(str(d) for d in desconocidos), ", ".join(sorted(_BLOQUES_MODELO)))]
        return format_response(response, ms_puente=crono.ms())

    @mcp.tool()
    async def list_views(
        current_only: bool = False,
        view_type: str = None,
        on_sheet: bool = None,
        ctx: Context = None,
    ) -> str:
        """List the views of the model grouped by type, or only the active view.
        Example: list_views(view_type="floor_plans", on_sheet=false) lists the plans
        not placed on any sheet; list_views(current_only=true) returns the active view.

        Args:
            current_only: Only the active view (name, id, type, scale, template, discipline)
            view_type: floor_plans, ceiling_plans, elevations, sections, 3d_views, drafting_views, schedules, other
            on_sheet: true = only views already on a sheet; false = only views not on any sheet
        """
        crono = Cronometro()
        if current_only:
            response = await revit_get("/current_view_info/", ctx)
            return format_response(response, ms_puente=crono.ms())
        response = await revit_get("/list_views/", ctx)
        if es_error(response):
            return format_response(response, ms_puente=crono.ms())
        por_tipo = response.get("views_by_type") or {}
        if view_type:
            clave = _TIPOS_VISTA.get(str(view_type).strip().lower())
            if clave is None:
                return format_response(_texto_error(
                    "view_type '{}' desconocido".format(view_type),
                    available_view_types=sorted(set(_TIPOS_VISTA.values()))), ms_puente=crono.ms())
            por_tipo = {clave: list(por_tipo.get(clave) or [])}
            response["view_type"] = clave
        if on_sheet is not None:
            numeros = {}
            pagina = 1
            while pagina <= MAX_PAGINAS_VISTAS:
                consulta = await revit_post("/query/", {
                    "category": "OST_Views", "fields": ["VIEWER_SHEET_NUMBER"],
                    "page": pagina, "page_size": 500,
                }, ctx, timeout=TIMEOUT_LECTURA)
                if es_error(consulta):
                    return format_response(consulta, ms_puente=crono.ms())
                for elemento in consulta.get("elements") or []:
                    campos = elemento.get("fields") or {}
                    valor = None
                    for clave_campo in campos:
                        valor = campos[clave_campo]
                        break
                    nombre = elemento.get("nombre")
                    if nombre:
                        numeros[nombre] = valor
                if not consulta.get("truncated"):
                    break
                pagina += 1
            colocada = lambda nombre: (numeros.get(nombre) not in _SIN_PLANO)  # noqa: E731
            por_tipo = dict(
                (clave, [n for n in nombres if colocada(n) == bool(on_sheet)])
                for clave, nombres in por_tipo.items()
            )
            response["sheet_numbers"] = dict((n, numeros[n]) for n in numeros if numeros[n] not in _SIN_PLANO)
            response["on_sheet"] = bool(on_sheet)
        response["views_by_type"] = por_tipo
        response["total_exportable_views"] = sum(len(v) for v in por_tipo.values())
        return format_response(response, ms_puente=crono.ms())

    @mcp.tool()
    async def describe_view(view_id: int = None, view_name: str = None, ctx: Context = None) -> str:
        """Crop box, view range, scale, level, discipline, template, section box and sheet
        of one view; without arguments, the active view (plus its type and family).
        Example: describe_view(view_id=12345) or describe_view().

        Args:
            view_id: View id (from list_views); omitted = active view
            view_name: Exact view name (alternative to view_id)
        """
        crono = Cronometro()
        activa = None
        if view_id is None and not view_name:
            info = await revit_get("/current_view_info/", ctx)
            if es_error(info):
                return format_response(info, ms_puente=crono.ms())
            activa = info.get("view_info") or {}
            view_id = activa.get("view_id")
            if view_id is None:
                return format_response(_texto_error("No active view"), ms_puente=crono.ms())
        data = {"view_id": view_id} if view_id is not None else {"view_name": view_name}
        response = await revit_post("/view_extents/", data, ctx, timeout=TIMEOUT_LECTURA)
        if isinstance(response, dict) and activa:
            response["is_active"] = True
            for clave in ("view_name", "view_type", "view_family_type", "detail_level", "crop_box_active"):
                if clave in activa and clave not in response:
                    response[clave] = activa[clave]
        return format_response(response, ms_puente=crono.ms())

    @mcp.tool()
    async def capture_view(view_name: str, ctx: Context = None):
        """Export one view as a PNG image (the image itself, not JSON). Use the exact
        name from list_views. Example: capture_view(view_name="Planta Nivel 1")."""
        response = await revit_image("/get_view/{}".format(view_name), ctx)
        return response

    @mcp.tool()
    async def query_elements(
        category: str | list[str] = None,
        family: str = None,
        type_name: str = None,
        level: str = None,
        view_id: int = None,
        current_view: bool = False,
        selected: bool = False,
        workset: str = None,
        phase: str = None,
        filters: list[dict] = None,
        bbox_min_mm: dict = None,
        bbox_max_mm: dict = None,
        sort_by: str = None,
        page: int = 1,
        page_size: int = 100,
        fields: list[str] = None,
        name_contains: str = None,
        ids_only: bool = False,
        ctx: Context = None,
    ) -> str:
        """Find elements: paginated query by category, family, type, level, view, workset,
        phase, bounding box and parameter `filters`. Give at least one criterion.
        Example: query_elements(category="OST_Walls", filters=[{"parameter": "Length",
        "op": ">", "value": 4000}], fields=["Mark", "Length"], sort_by="-Length").

        Args:
            category: BuiltInCategory ("OST_Walls") or alias ("walls"); a list is accepted
            family: Exact family name
            type_name: Exact type name or "Family: Type"
            level: Exact level name (404 with available_levels if it does not exist)
            view_id: Only elements visible in that view
            current_view: Only elements visible in the active view (resolves its id first)
            selected: Only the elements selected in the Revit window (ignores the other criteria)
            workset: Workset name
            phase: Phase name the elements were created in
            filters: [{"parameter": name | alias | BuiltInParameter, "op": "=", "!=", ">", "<", ">=", "<=", "contains", "starts", "empty", "not_empty", "exists", "value": mm/mm2/mm3/deg or text}]
            bbox_min_mm: Min corner {"x","y","z"} in mm of a box the element must intersect
            bbox_max_mm: Max corner {"x","y","z"} in mm
            sort_by: id, categoria, tipo, nivel, nombre, familia or a parameter; "-" = descending
            page: 1-based page
            page_size: Results per page (max 500)
            fields: Parameter values to include per element (contract units)
            name_contains: Case-insensitive substring of element, type or family name
            ids_only: Only ids (faster)
        """
        crono = Cronometro()
        if selected:
            response = await revit_get("/selected_elements/", ctx)
            return format_response(response, ms_puente=crono.ms())
        if current_view and view_id is None:
            info = await revit_get("/current_view_info/", ctx)
            if es_error(info):
                return format_response(info, ms_puente=crono.ms())
            view_id = (info.get("view_info") or {}).get("view_id")
            if view_id is None:
                return format_response(_texto_error("No active view"), ms_puente=crono.ms())
        data = {"page": page, "page_size": page_size, "ids_only": ids_only}
        for clave, valor in (
            ("category", category), ("family", family), ("type_name", type_name), ("level", level),
            ("view_id", view_id), ("workset", workset), ("phase", phase), ("filters", filters),
            ("bbox_min_mm", bbox_min_mm), ("bbox_max_mm", bbox_max_mm), ("sort_by", sort_by),
            ("fields", fields), ("name_contains", name_contains),
        ):
            if valor is not None:
                data[clave] = valor
        response = await revit_post("/query/", data, ctx, timeout=TIMEOUT_LECTURA)
        return format_response(response, ms_puente=crono.ms())

    @mcp.tool()
    async def describe_element(
        element_id: int = None,
        element_ids: list[int] = None,
        depth: int = 0,
        include_geometry: bool = False,
        ctx: Context = None,
    ) -> str:
        """Everything about one element (or up to 20, answered per id): category, type,
        level, host, every instance and type parameter (mm, `builtin` name), hosted,
        joined, dependent elements, dimensions/tags of the active view and bbox.
        Example: describe_element(element_id=1234, depth=1, include_geometry=true).

        Args:
            element_id: One element id
            element_ids: Up to 20 ids in one call; the answer is {"elements": {id: ...}}
            depth: 0 = related elements as ids; 1 = with category/type/level; 2 = also their relations
            include_geometry: Add `geometry` (location in mm, solids with volume_m3 / area_m2)
        """
        crono = Cronometro()
        ids = list(element_ids or [])
        if element_id is not None:
            ids.insert(0, element_id)
        if not ids:
            return format_response(_texto_error("element_id or element_ids is required"), ms_puente=crono.ms())
        if len(ids) > MAX_DESCRIBE:
            return format_response(_texto_error(
                "describe_element accepts at most {} ids per call; got {}".format(MAX_DESCRIBE, len(ids)),
                limite=MAX_DESCRIBE, cantidad=len(ids)), ms_puente=crono.ms())
        resultados = {}
        errores = {}
        for identificador in ids:
            data = {"element_id": identificador, "depth": depth, "include_geometry": include_geometry}
            response = await revit_post("/describe/", data, ctx, timeout=TIMEOUT_LECTURA)
            if es_error(response):
                errores[str(identificador)] = response if isinstance(response, dict) else {"error": str(response)}
            else:
                resultados[str(identificador)] = response
        if element_ids is None:
            unico = resultados.get(str(ids[0]))
            if unico is not None:
                return format_response(unico, ms_puente=crono.ms())
            return format_response(errores.get(str(ids[0])), ms_puente=crono.ms())
        return format_response({
            "elements": resultados, "count": len(resultados), "errors": errores, "requested": len(ids),
        }, ms_puente=crono.ms())

    @mcp.tool()
    async def dependency_graph(
        element_id: int,
        max_nodes: int = 100,
        max_depth: int = 2,
        ctx: Context = None,
    ) -> str:
        """Nodes and edges around an element (`hosts`, `joins`, `depends`: what would be
        deleted with it), breadth-first. Use before deleting or moving something with many
        relations. Example: dependency_graph(element_id=1234, max_nodes=50).

        Args:
            element_id: Root element id
            max_nodes: Maximum nodes (max 500); `truncated` says if the limit was hit
            max_depth: Hops from the root (max 6)
        """
        crono = Cronometro()
        data = {"element_id": element_id, "max_nodes": max_nodes, "max_depth": max_depth}
        response = await revit_post("/dependency_graph/", data, ctx, timeout=TIMEOUT_LECTURA)
        return format_response(response, ms_puente=crono.ms())

    @mcp.tool()
    async def list_types(
        category: str = None,
        family: str = None,
        contains: str = None,
        with_parameters: bool = False,
        loaded_only: bool = False,
        max: int = 200,
        ctx: Context = None,
    ) -> str:
        """Types available to create or change elements. With `category`: its types with
        main type parameters and instance counts. With only `family`/`contains`: family
        types matching the text. Without arguments: the family categories with counts.
        Example: list_types(category="OST_StructuralColumns", with_parameters=true).

        Args:
            category: BuiltInCategory ("OST_Walls") or alias ("walls", "beams")
            family: Only types of this family (with category: exact; alone: substring)
            contains: Substring of "family type" when no category is given
            with_parameters: Also list the parameters of the category's elements (`category_parameters`)
            loaded_only: Only types in use: ejemplares > 0 (with category) or is_active (family list)
            max: Maximum types returned
        """
        crono = Cronometro()
        if category:
            data = {"category": category, "family_name": family, "max": max}
            response = await revit_post("/element_types/", data, ctx, timeout=TIMEOUT_LECTURA)
            if es_error(response):
                return format_response(response, ms_puente=crono.ms())
            if loaded_only:
                tipos = [t for t in (response.get("types") or []) if (t.get("ejemplares") or 0) > 0]
                response["types"] = tipos
                response["count"] = len(tipos)
                response["loaded_only"] = True
            if with_parameters:
                response["category_parameters"] = await revit_post(
                    "/list_category_parameters/", {"category_name": category}, ctx, timeout=TIMEOUT_LECTURA)
            return format_response(response, ms_puente=crono.ms())
        if family or contains:
            params = {"contains": family or contains, "limit": str(max)}
            response = await revit_get("/list_families/", ctx, params=params)
            if isinstance(response, dict) and loaded_only and not es_error(response):
                familias = [f for f in (response.get("families") or []) if f.get("is_active")]
                response["families"] = familias
                response["count"] = len(familias)
                response["loaded_only"] = True
            return format_response(response, ms_puente=crono.ms())
        response = await revit_get("/list_family_categories/", ctx)
        return format_response(response, ms_puente=crono.ms())

    @mcp.tool()
    async def schedule_to_json(
        name: str = None,
        view_id: int = None,
        start_row: int = 0,
        max_rows: int = 500,
        ctx: Context = None,
    ) -> str:
        """Read a schedule as headers and rows exactly as Revit shows them. Give the exact
        schedule name (from list_views schedules) or its id.
        Example: schedule_to_json(name="Tabla de muros", max_rows=100).

        Args:
            name: Schedule view name
            view_id: Schedule view id (alternative to name)
            start_row: First body row (default 0)
            max_rows: Rows returned (max 5000)
        """
        crono = Cronometro()
        data = {"start_row": start_row, "max_rows": max_rows}
        if name is not None:
            data["name"] = name
        if view_id is not None:
            data["view_id"] = view_id
        response = await revit_post("/schedule/", data, ctx, timeout=TIMEOUT_LECTURA)
        return format_response(response, ms_puente=crono.ms())

    @mcp.tool()
    async def list_warnings(max: int = 100, group_by: str = None, ctx: Context = None) -> str:
        """Model warnings (description, severity, element ids). Call it after creating
        elements. With group_by="description" they come grouped by failure type with a
        suggestion each (language independent). Example: list_warnings(group_by="description").

        Args:
            max: Maximum warnings (or groups) returned
            group_by: "description" to group by failure type
        """
        crono = Cronometro()
        params = {"max": str(max)}
        if group_by:
            params["group_by"] = group_by
        response = await revit_get("/warnings/", ctx, params=params)
        return format_response(response, ms_puente=crono.ms())

    @mcp.tool()
    async def analyze_model(
        include: list[str] = None,
        categories: list[str] = None,
        ctx: Context = None,
    ) -> str:
        """Model statistics (element counts by category) and/or material quantities
        (areas and volumes for takeoffs). Default: statistics only.
        Example: analyze_model(include=["statistics", "materials"], categories=["OST_Walls"]).

        Args:
            include: "statistics" and/or "materials"
            categories: Categories for the material takeoff (default: all)
        """
        crono = Cronometro()
        bloques = [str(b).strip().lower() for b in (include or ["statistics"])]
        response = {"include": bloques}
        desconocidos = []
        for bloque in bloques:
            if bloque == "statistics":
                response["statistics"] = await revit_get("/model_statistics/", ctx)
            elif bloque == "materials":
                response["materials"] = await revit_post(
                    "/material_quantities/", {"categories": categories}, ctx, timeout=TIMEOUT_LARGO)
            else:
                desconocidos.append(bloque)
        if desconocidos:
            response["warnings"] = ["include desconocido: {} (usa statistics, materials)".format(", ".join(desconocidos))]
        if len(bloques) == 1 and bloques[0] in response and isinstance(response[bloques[0]], (dict, str)):
            unico = response[bloques[0]]
            if isinstance(unico, dict) and not es_error(unico):
                unico["include"] = bloques
                return format_response(unico, ms_puente=crono.ms())
            return format_response(unico, ms_puente=crono.ms())
        return format_response(response, ms_puente=crono.ms())

    @mcp.tool()
    async def check_clashes(
        set_a_categories: list[str] = None,
        set_b_categories: list[str] = None,
        max_clashes: int = 200,
        ctx: Context = None,
    ) -> str:
        """Hard clashes (solids that overlap) between two category sets, e.g. structure
        vs MEP; one set alone is checked against itself; none = default physical scope.
        Example: check_clashes(set_a_categories=["beams"], set_b_categories=["ducts", "pipes"]).

        Args:
            set_a_categories: First set (BuiltInCategory names or aliases)
            set_b_categories: Second set to check against
            max_clashes: Maximum pairs returned
        """
        crono = Cronometro()
        data = {"set_a_categories": set_a_categories, "set_b_categories": set_b_categories,
                "max_clashes": max_clashes}
        response = await revit_post("/clash_check/", data, ctx, timeout=TIMEOUT_LARGO)
        return format_response(response, ms_puente=crono.ms())

    @mcp.tool()
    async def snapshot_model(
        name: str,
        categories: list[str] = None,
        parameters: list[str] = None,
        include_parameters: bool = True,
        include_bbox: bool = True,
        max_elements: int = 20000,
        overwrite: bool = False,
        ctx: Context = None,
    ) -> str:
        """Save a snapshot of the model (id, UniqueId, category, type, level, bbox and
        parameters per element) to snapshots\\<name>.json, with `timings` per stage.
        No transaction. Take one before a batch of changes, then diff_snapshots.
        Example: snapshot_model(name="antes de estructura", categories=["walls"]).

        Args:
            name: Snapshot name (a name, not a path)
            categories: Categories to include; default = model elements plus levels and grids
            parameters: Only these parameters in the hash/values (default: all instance parameters)
            include_parameters: Store the parameter values (needed to list what changed)
            include_bbox: Store the bounding box (skip it to go faster)
            max_elements: Element limit; the response warns if truncated
            overwrite: Replace an existing snapshot (409 otherwise)
        """
        crono = Cronometro()
        data = {"name": name, "include_parameters": include_parameters, "include_bbox": include_bbox,
                "max_elements": max_elements, "overwrite": overwrite}
        if categories is not None:
            data["categories"] = categories
        if parameters is not None:
            data["parameters"] = parameters
        response = await revit_post("/snapshot/", data, ctx, timeout=TIMEOUT_LARGO)
        return format_response(response, ms_puente=crono.ms())

    @mcp.tool()
    async def diff_snapshots(a: str, b: str = None, max_items: int = 500, ctx: Context = None) -> str:
        """Compare two snapshots by UniqueId: added, removed and modified elements with the
        parameters that changed. Omit `b` to compare against the model as it is now.
        Example: diff_snapshots(a="antes de estructura").

        Args:
            a: Older snapshot name
            b: Newer snapshot name; omitted = the live model
            max_items: Maximum entries per list
        """
        crono = Cronometro()
        data = {"a": a, "max_items": max_items}
        if b is not None:
            data["b"] = b
        response = await revit_post("/diff_snapshots/", data, ctx, timeout=TIMEOUT_LARGO)
        return format_response(response, ms_puente=crono.ms())

    @mcp.tool()
    async def read_log(last_n: int = 50, ctx: Context = None) -> str:
        """Last entries of mcp_log.jsonl, the log of every write made through this MCP
        (route, args, ok, ms, error). Check it before repeating a call that timed out.
        Example: read_log(last_n=20).

        Args:
            last_n: Entries from the end
        """
        crono = Cronometro()
        response = await revit_get("/log/", ctx, params={"last_n": str(last_n)})
        return format_response(response, ms_puente=crono.ms())
