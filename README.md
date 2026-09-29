# Revit MCP Server

MCP server for Autodesk Revit 2024/2025/2026/2027 via pyRevit — **66 tools** (40 consolidated from 78 in 0.4.0, 12 for steel structures and the analytical model in 0.5.0, and 14 for the family editor in 0.6.0) for building design, structure, coordinates, editing, analysis, clash detection, MEP, interop, documentation, model persistence, deep navigation (element relations, parameter queries, schedules, snapshots), project macros (grids + levels, sheet sets, Civil 3D import, steel frames), **steel structures** (profiles, quantities, bracing, trusses, releases, connections, plates, analytical model), **the family editor** (new families from a template, parameters, reference planes, labelled dimensions, solids and voids, locks, types, connectors, flexing, save and load, or a whole family from a spec) and **the user's own macros**, with **batch routes** (many parameters or many elements in ONE transaction) and a safe-write layer (backups, action log, dry-run `simular`, verification and `IA:` undo entries). Version **0.6.0**.

Why 0.4.0: in the 0.3.1 validation Revit answered each call in under a second and simple tasks still took minutes, because the time went to the agent layer: 78 tools in context, one call per element and repetitive tasks driven step by step. 0.4.0 attacks that without changing the existing HTTP routes: the consolidation lives in `tools/`, and only two batch routes and the user-macro routes are new.

Works with any MCP client: Claude Desktop, Claude Code, Cursor, Windsurf, Copilot, or any other MCP-compatible application.

## How It Works

```
AI Client ──stdio/SSE/HTTP──> MCP Server (Python/FastMCP) ──HTTP :48884──> pyRevit Routes ──> Revit API
```

The MCP server runs on your machine and communicates with Revit through pyRevit's Routes API. Any MCP-compatible AI client can connect to it.

## Prerequisites

| Requirement | Details |
|-------------|---------|
| **Windows 10/11** | Revit is Windows-only |
| **Autodesk Revit** | 2024, 2025, 2026, or 2027 |
| **pyRevit** | Installed and loaded in Revit |
| **uv** | Python package manager ([install](https://docs.astral.sh/uv/getting-started/installation/)) |
| **A project open in Revit** | Tools require an active document |

## Install pyRevit (if not already installed)

pyRevit is a free add-in that lets scripts run inside Revit. This MCP server needs it to communicate with Revit.

1. Go to https://github.com/pyrevitlabs/pyRevit/releases
2. Download the latest **.exe installer** (e.g. `pyRevit_CLI_x.x.x.x_admin_signed.exe`)
3. Run the installer — accept all defaults, click **Next** through each screen
4. Open (or restart) Revit — you should see a **pyRevit** tab in the ribbon at the top
5. In the pyRevit tab, click **Settings** (gear icon)
6. In the Settings window, go to the **Routes** section on the left
7. Check the box to **Enable Routes Server**
8. Click **Save Settings** and let pyRevit reload

To verify: open a browser and go to `http://localhost:48884/` — you should see a response (not a "connection refused" error).

## Quick Start

### Step 1: Clone and install

```bash
git clone https://github.com/Demolinator/revit-mcp-server.git
cd revit-mcp-server
uv sync
```

### Step 2: Install the pyRevit extension

The `revit_mcp/` folder and `startup.py` need to run inside Revit via pyRevit.

**Option A — Install from pyRevit (recommended):**

1. In Revit, go to pyRevit tab > Extensions
2. Find "MCP Server for Revit Python" > Install
3. Wait for pyRevit to reload

**Option B — Manual install:**

1. Copy the entire repo folder to `%APPDATA%\pyRevit\Extensions\`
2. Rename the folder to `mcp-server-for-revit-python.extension`
3. In Revit, go to pyRevit tab > Settings > Custom Extensions
4. Add the path to the `.extension` folder
5. Reload pyRevit (or restart Revit)

### Step 3: Activate pyRevit Routes

1. In Revit, go to pyRevit tab > Settings
2. Navigate to Routes > activate **Routes Server**
3. pyRevit will start listening on `http://localhost:48884/`

### Step 4: Verify connection

Every route requires the **session token** that the extension writes to
`%LOCALAPPDATA%\RevitMcp\token` each time Revit starts (see [Security](#security)).
From PowerShell:

```powershell
$token = Get-Content "$env:LOCALAPPDATA\RevitMcp\token"
Invoke-RestMethod "http://127.0.0.1:48884/revit_mcp/status/?token=$token"
```

You should see:

```json
{
  "status": "active",
  "health": "healthy",
  "revit_available": true,
  "document_title": "your_project_name",
  "api_name": "revit_mcp"
}
```

Without the token (for example opening `http://localhost:48884/revit_mcp/status/`
in a browser) the answer is `401 {"error": "token ausente o incorrecto"}`.

### Step 5: Start the MCP server

```bash
uv run main.py
```

That's it. Your AI client can now connect. The bridge reads the token file by
itself and sends it with every request; if Revit is not running it answers
"Revit no está abierto o el conector no ha iniciado" instead of failing.

To run the smoke tests (Revit open, bridge started with `--combined` or
`--streamable-http`):

```bash
python pruebas\probar_revit.py
```

Expected: `401` without token, `200` with token, `200` for `execute_code`,
`421` from the bridge when the `Host` header is forged, every read route in
under 5 s, `set_parameter` with `simular=true` leaving the value untouched, a
real `set_parameter` returning `antes`/`despues` plus a log line and a backup,
`400` for `execute_code` without `description`, and the last undo entry named
`IA: ...`. Details in [CONTRATO.md](CONTRATO.md).

`python pruebas\probar_revit.py --fase 2a` adds the four 0.3.0 checks
(describe_element, paginated query_elements, snapshot/diff around a new level
and a simulated create_grid_and_levels); expected `13/13`.
`--fase cons` adds the 0.4.0 checks (a 20-element `set_parameters` batch
against 20 `set_parameter` calls, a mixed `create_elements` dry run, the
`comentarios_por_nivel` example macro, a snapshot with `timings` and a retired
tool name answered by the bridge); expected `15/15`.
`--fase 2b` adds the 0.5.0 steel checks (loaded steel profiles, a simulated
and a real `create_steel_frame` on a 2x2 auxiliary grid with
`analytical_status` on the result, `set_structural_properties` reflected by
`describe_element(include_structural=true)` and `steel_quantities`); expected
`14/14`, with 2b.2-2b.5 marked `NO_APLICA` when the model has no steel profiles
loaded. `--fase 2c` adds the 0.6.0 family editor checks (the base plate example
of CONTRATO.md built with `build_family_from_spec` into a `.rfa`, `family_validate`
passing on its two types, and the family loaded into the project and removed
again); expected `12/12`.

Without Revit, `uv run pytest` runs the CPython tests in `tests/` (JSON
formatting, simulated transactions, log rotation, write routes against a fake
`pyrevit`, CSV reader, the IronPython 2.7 compatibility guard and, since
0.3.0, end-to-end tests of every navigation, snapshot and macro route
against the fake model in `tests/fakes/modelo_falso.py`).

## Connecting Your AI Client

### Claude Desktop / Claude Code

Add to your MCP config:

```json
{
  "mcpServers": {
    "revit": {
      "command": "uv",
      "args": ["run", "main.py"],
      "cwd": "/path/to/revit-mcp-server"
    }
  }
}
```

### Cursor / Windsurf / Other MCP Clients

Use HTTP transport:

```bash
uv run main.py --streamable-http
```

Then configure your client to connect to `http://localhost:8000/mcp`.

### Transport Modes

| Flag | Transport | Endpoints | Use Case |
|------|-----------|-----------|----------|
| *(none)* | stdio | stdin/stdout | Claude Desktop / Claude Code |
| `--sse` | SSE | `/sse`, `/messages/` | Legacy clients |
| `--streamable-http` | HTTP | `/mcp` | HTTP-based clients |
| `--combined` | Both | All above | Maximum compatibility |

### Testing with MCP Inspector

```bash
mcp dev main.py
```

Then open `http://127.0.0.1:6274` in your browser.

## Supported Tools (66)

Rule: **a usual task takes one or two calls.** Every write tool accepts
`simular` (dry run: validates and returns `haria`, and `plan` in batches and
macros, without touching the model) and returns `ok`, `verificacion`, `copia`
(backup of the last save), `ms` (time in Revit) and `ms_puente` (total time
seen from the bridge). Calling one of the 51 names retired in 0.4.0 answers
with the replacement tool and its arguments (see the full table with the
"Sustituye a" column in [CONTRATO.md](CONTRATO.md#herramientas-mcp-040)).

### Read (20)

| Tool | Description | Replaces |
|------|-------------|----------|
| `get_revit_status` | Revit answers and which document is open | — |
| `get_revit_model_info` | Model summary + `file` block; `include=["levels","worksets","phases","links","location"]` adds those blocks | `list_levels`, `list_worksets`, `list_phases_and_options`, `list_links`, `get_project_location` |
| `list_views` | Views by type; `current_only`, `view_type`, `on_sheet` | `list_revit_views`, `get_current_view_info` |
| `describe_view` | Crop, view range, scale, level, template, sheet of a view (active view without arguments) | `get_view_extents`, `get_current_view_info` |
| `capture_view` | A view as a PNG image | `get_revit_view` |
| `query_elements` | Paginated query with `filters`; `current_view=true`, `selected=true` | `find_elements`, `get_current_view_elements`, `ai_element_filter`, `get_selected_elements` |
| `describe_element` | Everything about one element, or up to 20 with `element_ids`; `include_structural` adds usage, material, releases, justifications, offsets, rotation, extensions and `analyze_as` (0.5.0) | `get_element_properties`, `get_element_geometry`, `get_structural_properties` (original block B) |
| `dependency_graph` | `hosts` / `joins` / `depends` edges around an element | — |
| `list_types` | Types of a category, family types by text, or the family categories; `with_parameters`, `loaded_only`; `category="connections"` lists the steel connection types (409 `no_soportado` without the module) (0.5.0) | `list_element_types`, `list_families`, `list_family_categories`, `list_category_parameters`, `list_connection_types` (original block B) |
| `schedule_to_json` | A schedule as Revit shows it | — |
| `list_warnings` | Warnings, grouped by failure type with `group_by` | — |
| `analyze_model` | `include=["statistics","materials"]` | `analyze_model_statistics`, `get_material_quantities` |
| `check_clashes` | Hard clashes between category sets | — |
| `snapshot_model` | Snapshot to `snapshots\<name>.json` with `timings` per stage and `include_bbox` | — |
| `diff_snapshots` | Added / removed / modified between snapshots or against the live model | — |
| `read_log` | Last entries of `mcp_log.jsonl` | — |
| `list_steel_profiles` | Steel profiles loaded (family, type, shape W/HSS/L/C/WT/Pipe, standard AISC/EN, section dimensions in mm) and, with `loaded_only=false`, the library `.rfa` files with a type catalog (0.5.0) | new |
| `steel_quantities` | Count, length (mm) and weight (kg = volume x structural asset density, or nominal weight x length) by type / level / family / mark; `sin_peso` with the reason (0.5.0) | new |
| `analytical_status` | Associated `AnalyticalMember`, end nodes in mm, connected members per node, `loose_nodes` (0.5.0) | new |
| `family_info` | Summary of an open family document (parameters, types with values in mm, reference planes, views by `ViewType`, solids, dimensions, connectors); without `family_doc`, the open family documents and, with `include_templates`, the `.rft` templates (0.6.0) | new |

### Write (39)

| Tool | Description | Replaces |
|------|-------------|----------|
| `set_parameters` | Many parameters on many elements in ONE transaction; `type_parameters`; read-only ones reported in `fallidos` | `set_parameter`, `set_type_parameter`, `modify_element` |
| `create_elements` | Elements of mixed `kind` (wall, floor, roof, ceiling, level, grid, column, beam, foundation, opening, toposolid, room, room_separation, detail_line, duct, pipe, family_instance) in ONE transaction; all validated first, one failure rolls back everything | `create_line_based_element`, `create_surface_based_element`, `create_level`, `create_grid`, `create_structural_column`, `create_structural_framing`, `create_foundation`, `create_opening`, `create_toposolid`, `create_room`, `create_room_separation`, `create_detail_line`, `create_duct`, `create_pipe`, `place_family` |
| `transform_elements` | Move, copy, rotate, mirror or `array` (`count` copies) | — |
| `delete_elements` | Delete by id, cascaded elements reported | — |
| `change_element_type` | `ChangeTypeId` on several elements | — |
| `join_geometry` | Join / unjoin two elements, or a chain (`element_ids`, consecutive pairs) with `coping` (0.5.0) | `join_steel_elements` (original block B) |
| `set_workset` | Move elements to a workset | — |
| `set_project_location` | Base/survey point, true north, acquire coordinates (`forzar` for pinned points) | — |
| `create_view` | Plans, sections, elevations, 3D | — |
| `set_active_view` | Switch the active view | — |
| `create_sheet_set` | Sheets with views placed (one sheet = one element) | `create_sheet` |
| `create_schedule` | Schedule for a category | — |
| `annotate` | `kind="dimension"` or `kind="tag"` (`category="OST_Walls"` tags every wall in view) | `create_dimensions`, `tag_walls`, `tag_elements` |
| `color_elements` | Colour by parameter, `clear=true` resets | `color_splash`, `clear_colors` |
| `export` | `format` = pdf / png / jpg / dwg / ifc / rooms_json / rooms_csv / ifc_structural / csv_nodes_members (0.5.0) | `export_document`, `export_ifc`, `export_room_data`, `export_structural_model` (original block B) |
| `link_file` | Link or import DWG/DXF/DGN/RVT | — |
| `load_family` | Load an `.rfa` | — |
| `create_mep_system` | Group ducts/pipes into a system | — |
| `maintain_model` | `action` = purge / backup / save | `purge_unused`, `create_backup`, `save_document` |
| `load_steel_profile` | Load steel profile types: `LoadFamilySymbol` per catalog type or `LoadFamily`; 409 if already loaded unless `overwrite` (0.5.0) | new |
| `create_steel_frame` | **Macro**: a column at every grid intersection per level and beams between consecutive columns in ONE transaction (`IA: Portico metalico`); `plan.counts` with `simular`, `skip_columns_at`, `skip_beams_at`, `mark_prefix` (0.5.0) | new |
| `create_bracing` | Braces per bay (`single`, `X`, `V`, `inverted_V`, `K`) in ONE transaction, heights from the levels' internal elevation (0.5.0) | new |
| `create_truss` | Trusses (`Truss.Create` on a sketch plane at the level) in ONE transaction; 404 with the available types (0.5.0) | new |
| `set_structural_properties` | Releases (`pinned`, `fixed`, `{"FX": true, ...}`), Y/Z justification and offsets, section rotation, extensions, `analyze_as`, structural usage by `BuiltInParameter` in ONE transaction; releases fall back to the `AnalyticalMember` (2023+); `fallidos`, `no_disponibles` (0.5.0) | new (batch version of the original block B tool) |
| `create_steel_connection` | `StructuralConnectionHandler.Create` per connection; 409 `no_soportado` without Steel Connections for Revit (0.5.0) | new |
| `add_plate_or_stiffener` | Face-based family on the top / bottom / web face (references from `get_Geometry`) or point-based family along a beam or column (0.5.0) | new |
| `split_beam` | Split a straight beam: the original keeps the first segment, each further segment is a copy; `avisos` about lost joins and connections (0.5.0) | new |
| `fix_analytical_alignment` | Snap loose analytical nodes to the nearest foreign node within `tolerance_mm` (`AnalyticalMember.SetCurve`) in ONE transaction (0.5.0) | new |
| `family_open` | Open a family document: new from a `.rft` template (`template` + `name`), from a `.rfa`, or `EditFamily` of a loaded family (never system or in-place families); returns the summary and the `family_doc` to use next (0.6.0) | new (absorbs `family_new`) |
| `family_add_parameters` | Family parameters in ONE transaction: `data_type` (length, number, integer, text, yes_no, material, angle, area, volume, `family_type:<BuiltInCategory>`), `group` as `GroupTypeId`, `formula`, `shared_parameter_guid` (0.6.0) | new (batch) |
| `family_add_reference_planes` | Named reference planes (`direction` x / y / horizontal, `is_reference` left / right / front / back / top / bottom / strong / weak) in ONE transaction (0.6.0) | new (batch) |
| `family_add_dimensions` | Dimensions between parallel reference planes labelled with a parameter (`FamilyLabel`) or with equal segments, in ONE transaction (0.6.0) | new (batch) |
| `family_create_solids` | Extrusions, sweeps, revolutions and blends (solids or voids) with `lock_ends_to`, `lock_faces` and `material_parameter`, in ONE transaction (0.6.0) | new (absorbs `family_create_extrusion` / `sweep` / `revolve` / `blend`) |
| `family_lock_faces` | `NewAlignment` between a face of a solid (top, bottom, left...) and a reference plane, in ONE transaction (0.6.0) | new (batch of `family_lock_face_to_plane`) |
| `family_set_type_values` | Create types and set their values (mm, degrees, booleans, materials by name) with `antes`/`despues` and `fallidos`, in ONE transaction (0.6.0) | new (batch) |
| `family_add_connectors` | Duct, pipe or electrical connectors on a face of a solid (`system_type`, `size_mm`); no structural connector exists (0.6.0) | new (batch) |
| `family_save` | `SaveAs` to a `.rfa` (409 if it exists unless `overwrite`); the document title becomes the file name (0.6.0) | new |
| `family_load_into_project` | Load the open family document (or a `.rfa`) into the project with `IFamilyLoadOptions`; 409 if already loaded unless `overwrite_parameters` (0.6.0) | new |
| `family_close` | `Close(save)` of a document opened by the MCP, never the active one (0.6.0) | new |

### Macros (6)

| Tool | Description |
|------|-------------|
| `create_grid_and_levels` | Full grid with sequential names and levels in one transaction; `plan` with `simular` |
| `import_from_civil` | LandXML / CSV → toposolid; DWG → link and, optionally, shared coordinates |
| `list_macros` | Catalogue of the user's own macros (`%LOCALAPPDATA%\RevitMcp\macros\<name>\macro.json` + `macro.py`, or `REVIT_MCP_MACROS`) with their arguments, and the invalid ones with their error |
| `run_macro` | Run one with its arguments; a `writes` macro runs inside `IA: Macro <name>` with backup, log and verification, `simular` returns its `plan` |
| `family_validate` | Flex the family: one `TransactionGroup` per case (type + values + `Regenerate`), every solid must keep a volume, Revit regeneration errors reported per case, rolled back with `restore` (0.6.0) | new |
| `build_family_from_spec` | Whole family from a spec: validated completely before opening anything, then new family → parameters → planes → dimensions → solids → connectors → types → validate → save → load; any failure closes without saving (0.6.0) | new |

### Last resort (1)

| Tool | Description |
|------|-------------|
| `execute_revit_code` | IronPython code inside Revit (`description` required; full code logged; `elementos_modificados` in the response) |

### Your own macros (0.4.0)

Repetitive work becomes a deterministic plugin the AI only chooses when and
how to call. One folder per macro under `%LOCALAPPDATA%\RevitMcp\macros\`:
`macro.json` (name, description, `args` with `type` int / float / str / bool /
list / element_id / element_ids / level / view, `required`, `default`; `writes`;
`timeout_s`) and `macro.py` with `run(doc, uidoc, args, api)` and an optional
`plan(doc, args, api)`. `api` carries the server helpers (`make_element_id`,
`buscar_por_nombre`, `xyz_desde_mm`, `resolver_nivel`, `log`...). The macro
never opens a transaction: the server wraps a `writes` macro in
`IA: Macro <name>` with backup, `mcp_log.jsonl`, `simular` (its `plan`) and
verification of what `run` returns; files are reloaded when their `mtime`
changes, so you edit without restarting Revit. Two examples with tests live in
`herramientas-dev/macros-ejemplo/` (`numerar_planos`, `comentarios_por_nivel`).
Existing C# add-ins or pyRevit buttons can be wrapped with `PostCommand` in a
`writes: false` macro (no arguments, no verification); see CONTRATO.md.

### Steel structures and the analytical model (0.5.0)

Delivery 2b (block B of `herramientas-dev/PROMPT_FASE2.md`, adapted by
`PROMPT_FASE2B.md`): 12 new tools and 4 extensions, all in
`revit_mcp/acero.py` and `revit_mcp/analitico.py`, registered in
`tools/lectura_tools.py` and `tools/escritura_tools.py`. Everything structural
goes through `BuiltInParameter`, `BuiltInCategory` and
`StructuralMaterialType` / `StructuralAssetClass`, so it works in a Spanish
Revit; the profile standard (AISC / EN) and shape (W, HSS, L, C, WT, Pipe) are
deduced from the type designation, which is the same in every language.
Every write tool that can touch several elements is a **batch** (one list, all
validated first, one transaction, `fallidos[]`), and `create_steel_frame` is a
**macro** (`plan.counts` with `simular`, one `IA: Portico metalico` undo
entry). The agent flow is in `INSTRUCCIONES_AGENTE.md` (section 2d): levels and
grids → `list_steel_profiles` → `load_steel_profile` if needed →
`create_steel_frame(simular=true)` → confirm → run →
`set_structural_properties` → `analytical_status` /
`fix_analytical_alignment` → `steel_quantities`. Steel connections need the
Steel Connections for Revit module (409 `no_soportado` otherwise), and the
analytical routes need Revit 2023+ (`AnalyticalMember`). Validation prompt:
`herramientas-dev/VALIDACION_2B.md`.

### Family editor (0.6.0)

Delivery 2c (block C of `herramientas-dev/PROMPT_FASE2.md`): 14 tools in
`revit_mcp/familias.py`, `familias_edicion.py` and `familias_spec.py`. They write
to a **family document** identified by `family_doc` (the title returned by
`family_open`, or the `name` given to it) through `escritura.ejecutar_familia`:
logged in the project's `mcp_log.jsonl`, `409` when the family document has an
open transaction, the `.rfa` backed up only when it is already saved, and one
`IA: ...` transaction in the family document per call. Templates are found by
file name in `Application.FamilyTemplatePath` (`family_info(include_templates=true)`
lists them as that Revit names them), views by `ViewType` plus level or
direction, categories by `BuiltInCategory` and parameter groups by `GroupTypeId`,
so nothing depends on English display names. `build_family_from_spec` validates
the whole spec (template, referenced planes, formulas, types) before opening
anything and closes without saving if any step fails; `family_validate` flexes
the family in a rolled-back `TransactionGroup` per case. The agent flow is in
`INSTRUCCIONES_AGENTE.md` (section 2e); the spec format and two complete
examples (a base plate with four holes and a parametric W profile) are in
CONTRATO.md. Validation prompt: `herramientas-dev/VALIDACION_2C.md`.

## Seguridad de escritura

Las 19 herramientas de escritura, las 4 macros y todas las rutas de escritura
de 0.3.x pasan por `revit_mcp/escritura.py` (detalle en
[CONTRATO.md](CONTRATO.md#escritura-segura)):

- **Comprobación previa.** `409` si Revit tiene una transacción abierta de
  otra operación (`doc.IsModifiable`) o el documento es de solo lectura.
- **Copia de seguridad.** Antes de escribir, si el modelo está guardado y no
  es de trabajo compartido, se copia el `.rvt` a
  `<carpeta>\backups\<nombre>_<yyyyMMdd_HHmmss>.rvt` (una copia cada 30 min
  como mucho, se conservan 10). La copia refleja el **último guardado**, no el
  estado en memoria; el MCP nunca guarda por su cuenta.
  `maintain_model(action="backup")` hace una copia a demanda. Desde 0.4.0 la
  copia corre en un hilo aparte mientras el manejador valida y la transacción
  la espera justo antes de `Commit` (`copia.ms`, `copia.espera_ms`); nunca se
  confirma una escritura sin la copia terminada.
- **Registro.** Cada acción deja una línea JSON en `mcp_log.jsonl` junto al
  `.rvt` (ruta, argumentos, `ok`, `ms`, error, resumen; código completo en
  `execute_revit_code`). Rota a 5 MB. Se lee con `read_log`.
- **Simulación.** `simular=true` valida y devuelve `{"simulado": true,
  "haria": [...]}` sin abrir transacción.
- **Verificación.** Tras escribir se relee el resultado: `creados` (id,
  categoría, tipo, nivel, bbox), `antes`/`despues` o
  `eliminados`/`en_cascada`. Si no coincide con lo pedido, `ok: false` con
  `verificacion.detalle`; nunca se reintenta solo.
- **Deshacer.** Todas las transacciones se llaman `IA: <acción>`, así se
  distinguen en el historial de Revit.
- **Límites.** Más de 200 elementos por llamada exige `forzar=true`
  (`delete_elements`, `transform_elements`, `change_element_type`,
  `set_workset`, las macros sobre el total que crean o `plan.count`,
  `set_parameters` sobre elementos × parámetros y `create_elements` sobre
  `elements`). `execute_revit_code` exige `description` y rechaza
  `doc.Delete(<colección>)` salvo `forzar`.
- **Instrucciones al agente.** `INSTRUCCIONES_AGENTE.md` se envía como
  `instructions` del servidor: precedencia de herramientas, flujo obligatorio
  (leer → plan → `simular` → confirmar → ejecutar → verificar →
  `list_warnings`), reglas de dominio y glosario español ↔ API.

## Security

Full details (routes, parameters, expected errors) live in [CONTRATO.md](CONTRATO.md).

- **Session token.** `startup.py` generates a random 64-hex token on every
  Revit start, stores it in `%LOCALAPPDATA%\RevitMcp\token` (ACL restricted to
  the current user when possible) and keeps it in memory. Every route is wrapped
  with `@requiere_token` (`revit_mcp/seguridad.py`): POST expects `"token"` in
  the JSON body, GET expects `?token=`. Missing or wrong token → `401`.
  `main.py` reads the file, caches it, and on a `401` re-reads it once and
  retries (Revit restarted → new token).
- **Loopback only.** pyRevit Routes listens on all interfaces, so block inbound
  connections to port 48884 with this firewall rule (admin console):

  ```bat
  netsh advfirewall firewall add rule name="Block pyRevit Routes" dir=in action=block protocol=TCP localport=48884 profile=any
  ```

- **DNS rebinding.** pyRevit Routes does not expose the `Origin` header to
  handlers, so it cannot be checked inside Revit. The MCP bridge is created with
  `host="127.0.0.1"`, which in mcp 2.2 enables DNS-rebinding protection: any
  request whose `Host` is not `127.0.0.1`, `localhost` or `[::1]` gets `421`.

## Undo

`execute_revit_code` runs the code inside a Revit `TransactionGroup` named
`IA: <description>` (the tool's `description` argument, or the first `#`
comment line of the code, max 60 characters). The whole order becomes a single
undo entry in Revit. On error the group is rolled back; if a transaction opened
by the code cannot be closed, the response says so (`open_transaction: true`)
so you can check it in Revit.

## Architecture

Two runtimes communicate over HTTP:

| Component | Runtime | Location | Purpose |
|-----------|---------|----------|---------|
| `main.py` + `tools/` | Python 3.11+ (CPython) | Your machine | MCP protocol, tool definitions |
| `startup.py` + `revit_mcp/` | IronPython 2.7 (inside Revit) | Revit process | pyRevit route handlers, Revit API |

## Multi-Version Revit Support

This server supports Revit 2024, 2025, 2026, and 2027 through centralized helper functions that handle the ElementId API differences across versions:

- `get_element_id_value()` — Extracts integer IDs using `.Value` (2024+) with `.IntegerValue` fallback
- `make_element_id()` — Creates ElementIds using `System.Int64` (2024+) with `int` fallback

No configuration needed — version detection is automatic via try/except at runtime.

> **Revit 2027 note:** Revit 2027 runs on **.NET 10** (vs .NET 8 in 2025/2026). This MCP server is pyRevit-based, so .NET compatibility is handled by pyRevit itself — ensure you run a **pyRevit build with Revit 2027 support**. None of the 66 tools use APIs removed in 2027 (AXM/FormIt import, `Mechanical.Zone` members, legacy rebar creation, or the dropped `EnergyDataSettings` properties). `create_elements(kind="toposolid")` and `import_from_civil` (LandXML/CSV) need Revit 2024+ (`DB.Toposolid`); `kind="ceiling"` uses `DB.Ceiling.Create` (2022+) and falls back to a floor. The API members added in 0.3.0, 0.4.0, 0.5.0 and 0.6.0 that are still pending a check inside Revit are listed in `herramientas-dev/miembros_por_verificar_revit.md`; the 0.5.0 steel routes read releases from the `AnalyticalMember` (2023+) when the physical element has no release parameters, and report any `BuiltInParameter` missing in the running version in `no_disponibles`; the 0.6.0 family editor uses the 2022+ `FamilyManager.AddParameter(name, GroupTypeId, SpecTypeId, isInstance)` overload.

## Unit Handling

All tools accept **millimeters (mm)**. The server converts to Revit's internal feet.

| From | To mm |
|------|-------|
| meters | x 1000 |
| feet | x 304.8 |
| inches | x 25.4 |

## Creating Your Own Tools

For repetitive work you do not need a new tool: write a **macro** (see
"Your own macros" above) and the agent finds it with `list_macros`. A new MCP
tool is for a capability the routes do not offer yet, and requires 2 files + 2
registration lines:

1. **Route handler** in `revit_mcp/new_module.py` (IronPython 2.7) — put
   `@requiere_token` (from `seguridad`) right below `@api.route(...)` so the
   route requires the session token. A write route wraps its body in
   `escritura.ejecutar(doc, "/ruta/", request, cuerpo)`, honours
   `ctx["simular"]`, opens `with escritura.transaccion(doc, "Acción")` and
   returns `escritura.resultado_creacion(...)` or `antes`/`despues`
2. **Tool definition** in `tools/lectura_tools.py`, `tools/escritura_tools.py`
   or `tools/macro_tools.py` (Python 3.11+) with a `simular: bool = False`
   parameter, a `timeout=` from `tools/utils.py`, a description under 60
   words with an example, and `format_response(..., ms_puente=crono.ms())`
3. **Register routes** in `startup.py`
4. **Add the name** to `tools.HERRAMIENTAS` (and to `HERRAMIENTAS_RETIRADAS`
   if it replaces an old one); `tests/test_herramientas.py` checks both lists

Run `uv run pytest`: `tests/test_compatibilidad_ironpython.py` rejects
Python-3-only syntax in `revit_mcp/` and imports every module with a fake
`pyrevit`.

See `LLM.txt` for full context that helps AI assistants understand the codebase.

## Contributing

Contributions are welcome! Feel free to submit pull requests or open issues.

## Author

**Talal Ahmed**

## License

MIT
