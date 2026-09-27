# Revit MCP Server

MCP server for Autodesk Revit 2024/2025/2026/2027 via pyRevit — **68 tools** for building design, structure, coordinates, editing, analysis, clash detection, MEP, interop, documentation and model persistence, with a safe-write layer (backups, action log, dry-run `simular`, verification and `IA:` undo entries). Version **0.2.2**.

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

Without Revit, `uv run pytest` runs the CPython tests in `tests/` (JSON
formatting, simulated transactions, log rotation, write routes against a fake
`pyrevit`, CSV reader and the IronPython 2.7 compatibility guard).

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

## Supported Tools (68)

Every write tool accepts `simular` (dry run: validates and returns `haria`
without touching the model) and returns `ok`, `verificacion`, `copia` (backup
of the last save) and `ms`. See [Seguridad de escritura](#seguridad-de-escritura).

### Create (20)

| Tool | Description |
|------|-------------|
| `create_level` | Create new levels with elevations |
| `create_line_based_element` | Create walls, beams, and other line-based elements |
| `create_surface_based_element` | Create floors, roofs, and surface elements |
| `place_family` | Place a family instance at specified location |
| `create_grid` | Create column grid lines |
| `create_structural_framing` | Create structural beams and framing |
| `create_structural_column` | Create structural columns between levels (`StructuralType.Column`) |
| `create_foundation` | Isolated footings, wall foundations (`WallFoundation`) and foundation slabs |
| `create_opening` | Openings in walls (2 corners) or floors/roofs/ceilings (polygon) |
| `create_toposolid` | Toposolid (Revit 2024+) from points in mm or a Civil 3D CSV (P,N,E,Z / X,Y,Z) |
| `create_sheet` | Create new drawing sheets |
| `create_schedule` | Create schedules with custom fields |
| `create_room` | Create rooms at specified levels |
| `create_room_separation` | Create room separation boundary lines |
| `create_duct` | Create ducts between two points (MEP) |
| `create_pipe` | Create pipes between two points (MEP) |
| `create_mep_system` | Create mechanical or piping systems |
| `create_detail_line` | Create view-specific detail lines |
| `create_view` | Create floor plans, sections, elevations, 3D views |
| `create_dimensions` | Create dimension annotations |

### Query (21)

| Tool | Description |
|------|-------------|
| `get_revit_status` | Check if the API is active and responding |
| `get_revit_model_info` | Model information + `file` block (workshared, path, last saved, units, base points, true north) |
| `list_levels` | Get all levels with elevations |
| `list_families` | Family types filtered by `contains`, `category` and `limit` |
| `list_family_categories` | Get all family categories |
| `list_element_types` | Types of a category with main type parameters and instance counts |
| `find_elements` | Search by category, name, type, level and parameter value (ids + summary) |
| `get_element_properties` | Parameters plus bbox, level, workset, phase, design option, host |
| `get_element_geometry` | Bounding box in mm, location curves or solids (volume, area) |
| `get_revit_view` | Export a view as an image |
| `list_revit_views` | List all exportable views |
| `get_current_view_info` | Get active view details |
| `get_current_view_elements` | Get elements in current view |
| `get_selected_elements` | Get currently selected elements |
| `list_category_parameters` | List parameters for a category |
| `list_warnings` | Model warnings with severity and element ids |
| `list_worksets` | Worksets: id, name, owner, editable, open |
| `list_phases_and_options` | Phases and design options |
| `list_links` | RVT/IFC links and CAD imports with path, loaded status and position |
| `get_project_location` | Project base point, survey point, true north, shared coordinates |
| `read_log` | Last entries of `mcp_log.jsonl` |

### Modify (16)

| Tool | Description |
|------|-------------|
| `delete_elements` | Delete elements (verifies `eliminados`/`en_cascada`; >200 needs `forzar`) |
| `modify_element` | Modify element parameter values (`antes`/`despues`) |
| `set_parameter` | Set a single instance (or type) parameter on an element |
| `set_type_parameter` | Set a type parameter (reports affected instances) |
| `change_element_type` | Change the type of elements (`ChangeTypeId`) |
| `transform_elements` | Move, copy, rotate, or mirror elements (>200 needs `forzar`) |
| `set_workset` | Move elements to another workset |
| `join_geometry` | Join / unjoin the geometry of two elements |
| `set_project_location` | Move base/survey point, rotate true north, acquire coordinates from a link |
| `color_splash` | Color elements by parameter values |
| `clear_colors` | Reset element colors |
| `tag_walls` | Tag all walls in current view |
| `tag_elements` | Tag specific elements with annotation symbols |
| `set_active_view` | Switch the active view in Revit |
| `purge_unused` | Purge unused families and types (run with `simular` first) |
| `create_backup` | On-demand copy of the saved `.rvt` to `backups\` |

### Analyze (5)

| Tool | Description |
|------|-------------|
| `ai_element_filter` | Filter elements by category and parameters |
| `export_room_data` | Export room areas, volumes, boundaries |
| `get_material_quantities` | Material takeoff data |
| `check_clashes` | Detect hard clashes (interferences) between disciplines, e.g. structure vs MEP |
| `analyze_model_statistics` | Element counts and model stats |

### Document, Interop & Persistence (5)

| Tool | Description |
|------|-------------|
| `export_document` | Export views to PDF or image |
| `export_ifc` | Export model to IFC format (IFC2x3/IFC4) |
| `link_file` | Link or import DWG, DXF, DGN, SAT, SKP, 3DM, or RVT files |
| `load_family` | Load a Revit family (`.rfa`) from disk so its types can be placed |
| `save_document` | Save / Save-As the model to disk (only when the user asks) |

### Advanced (1)

| Tool | Description |
|------|-------------|
| `execute_revit_code` | Execute IronPython code in Revit context (`description` required; full code logged; `elementos_modificados` in the response) |

## Seguridad de escritura

Las 40 herramientas de escritura pasan por `revit_mcp/escritura.py`
(detalle en [CONTRATO.md](CONTRATO.md#escritura-segura)):

- **Comprobación previa.** `409` si Revit tiene una transacción abierta de
  otra operación (`doc.IsModifiable`) o el documento es de solo lectura.
- **Copia de seguridad.** Antes de escribir, si el modelo está guardado y no
  es de trabajo compartido, se copia el `.rvt` a
  `<carpeta>\backups\<nombre>_<yyyyMMdd_HHmmss>.rvt` (una copia cada 30 min
  como mucho, se conservan 10). La copia refleja el **último guardado**, no el
  estado en memoria; el MCP nunca guarda por su cuenta. `create_backup` hace
  una copia a demanda.
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
  `set_workset`). `execute_revit_code` exige `description` y rechaza
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

> **Revit 2027 note:** Revit 2027 runs on **.NET 10** (vs .NET 8 in 2025/2026). This MCP server is pyRevit-based, so .NET compatibility is handled by pyRevit itself — ensure you run a **pyRevit build with Revit 2027 support**. None of the 68 tools use APIs removed in 2027 (AXM/FormIt import, `Mechanical.Zone` members, legacy rebar creation, or the dropped `EnergyDataSettings` properties). `create_toposolid` needs Revit 2024+ (`DB.Toposolid`).

## Unit Handling

All tools accept **millimeters (mm)**. The server converts to Revit's internal feet.

| From | To mm |
|------|-------|
| meters | x 1000 |
| feet | x 304.8 |
| inches | x 25.4 |

## Creating Your Own Tools

Adding a new tool requires 2 files + 2 registration lines:

1. **Route handler** in `revit_mcp/new_module.py` (IronPython 2.7) — put
   `@requiere_token` (from `seguridad`) right below `@api.route(...)` so the
   route requires the session token. A write route wraps its body in
   `escritura.ejecutar(doc, "/ruta/", request, cuerpo)`, honours
   `ctx["simular"]`, opens `with escritura.transaccion(doc, "Acción")` and
   returns `escritura.resultado_creacion(...)` or `antes`/`despues`
2. **Tool definition** in `tools/new_tools.py` (Python 3.11+) with a
   `simular: bool = False` parameter and a `timeout=` from `tools/utils.py`
3. **Register routes** in `startup.py`
4. **Register tools** in `tools/__init__.py`

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
