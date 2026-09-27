# Prompt de la entrega de consolidación (revit-mcp 0.4.0): menos herramientas, lotes y macros propias

Eres un agente de programación con acceso al repositorio `revit-mcp`. La fase 1 (0.2.2) y la entrega 2a (0.3.1)
están en `main` y **validadas en un Revit 2027 en español** (`pruebas/probar_revit.py --fase 2a` 13/13, 211 pruebas
en CPython). Antes de escribir código lee `CONTRATO.md`, `INSTRUCCIONES_AGENTE.md`, `herramientas-dev/PROMPT_FASE2.md`
(reglas vigentes), `herramientas-dev/VALIDACION_2A.md` (resultado), `tools/__init__.py`, `tools/utils.py`,
`revit_mcp/escritura.py`, `revit_mcp/navegacion.py`, `revit_mcp/macros.py` y `tests/`.

## Por qué esta entrega

En la validación, Revit respondió en menos de un segundo por llamada (`set_parameter` 391 ms, `create_opening`
904 ms) y aun así las tareas sencillas tardaban minutos. El tiempo se va en la capa del agente: 78 herramientas en
contexto (elige peor y gasta tokens en cada turno), una llamada por elemento (cambiar 20 comentarios son 20 idas y
vueltas) y tareas repetitivas que se le piden paso a paso en vez de ejecutarlas de una vez. Esta entrega ataca eso
**sin cambiar las rutas HTTP existentes de `revit_mcp/`**: la consolidación ocurre en `tools/`, y solo se añaden
rutas nuevas donde hace falta una transacción única (lotes) o una capacidad nueva (macros propias).

Rama `feature/consolidacion` desde `main`, versión **0.4.0** (rompe compatibilidad de nombres de herramientas). Las
entregas 2b y 2c pasan a 0.5.0 y 0.6.0. Un commit por bloque. `uv run --with pytest python -m pytest -q` antes de
cada commit; ninguna prueba existente puede dejar de pasar salvo las que comprueban nombres de herramientas
eliminadas, que se actualizan. No hagas commit de `uv.lock` si solo cambió por `--with pytest`.

## Bloque 1. De 78 a unas 38 herramientas MCP

Regla: **una tarea habitual se resuelve en una o dos llamadas.** Cada herramienta que quede debe tener una
descripción corta (menos de 60 palabras) que diga cuándo usarla y con un ejemplo de argumentos. Las rutas HTTP que
usaban las herramientas eliminadas siguen existiendo; solo desaparece la herramienta MCP.

| Queda (38) | Absorbe | Cómo |
|---|---|---|
| `get_revit_status` | — | igual |
| `get_revit_model_info` | `get_project_location`, `list_worksets`, `list_phases_and_options`, `list_links`, `list_levels` | `include=["levels","worksets","phases","links","location"]`; por defecto solo el bloque `file` y el resumen actual |
| `list_views` | `list_revit_views`, `get_current_view_info` | `current_only`, `view_type`, `on_sheet` |
| `describe_view` | `get_view_extents` | + los campos de `current_view_info`; `view_id` opcional (activa) |
| `capture_view` | `get_revit_view` | imagen; igual |
| `query_elements` | `find_elements`, `get_current_view_elements`, `ai_element_filter` | `view_id` (activa por defecto si `current_view=true`), `fields`; `ai_element_filter` era `query` + descripción en lenguaje natural: se documenta en INSTRUCCIONES_AGENTE cómo traducirlo a `filters` |
| `describe_element` | `get_element_properties`, `get_element_geometry` | `include_geometry`, `element_ids[]` (hasta 20 en una llamada, respuesta por id) |
| `dependency_graph` | — | igual |
| `list_types` | `list_element_types`, `list_families`, `list_family_categories`, `list_category_parameters` | `category`, `family`, `with_parameters`, `loaded_only` |
| `schedule_to_json` | — | igual |
| `list_warnings` | — | igual (`group_by`) |
| `set_parameters` | `set_parameter`, `set_type_parameter`, `modify_element` | **lote**, ver Bloque 2 |
| `create_elements` | `create_line_based_element`, `create_surface_based_element`, `create_level`, `create_grid`, `create_structural_column`, `create_structural_framing`, `create_foundation`, `create_opening`, `create_toposolid`, `create_room`, `create_room_separation`, `create_detail_line`, `create_duct`, `create_pipe`, `place_family` | **lote**, ver Bloque 2 |
| `transform_elements` | — | igual (`move`, `copy`, `rotate`, `mirror`, `array`) |
| `delete_elements` | — | igual |
| `change_element_type` | — | igual, acepta `element_ids[]` |
| `join_geometry` | — | igual |
| `set_workset` | — | igual |
| `set_project_location` | — | igual |
| `create_view` | — | igual |
| `set_active_view` | — | igual |
| `create_sheet_set` | `create_sheet` | un plano es `sheets` con un elemento |
| `create_schedule` | — | igual |
| `annotate` | `create_dimensions`, `tag_walls`, `tag_elements` | `kind` = `dimension` / `tag`; `tag_walls` es `tag` con `category=OST_Walls` |
| `color_elements` | `color_splash`, `clear_colors` | `clear=true` limpia |
| `export` | `export_document`, `export_ifc`, `export_room_data` | `format` = `pdf` / `dwg` / `ifc` / `rooms_csv` / `rooms_json` |
| `link_file` | — | igual |
| `load_family` | — | igual |
| `analyze_model` | `analyze_model_statistics`, `get_material_quantities` | `include=["statistics","materials"]` |
| `check_clashes` | — | igual |
| `snapshot_model` | — | igual, ver Bloque 4 |
| `diff_snapshots` | — | igual |
| `create_mep_system` | — | igual (los conductos y tuberías van por `create_elements`) |
| `maintain_model` | `purge_unused`, `create_backup`, `save_document` | `action` = `purge` / `backup` / `save`, todas con `simular` donde aplique |
| `read_log` | — | igual |
| `create_grid_and_levels` | — | igual |
| `import_from_civil` | — | igual |
| `run_macro` (+ `list_macros`) | nuevo | ver Bloque 3 |
| `execute_revit_code` | — | igual, último recurso |

Son 38 con `list_macros` (39 si `capture_view` se mantiene separada por devolver imagen; decide y dilo). En
`tools/__init__.py` deja una tabla `HERRAMIENTAS_RETIRADAS = {"nombre_viejo": "nombre_nuevo(argumentos)"}` y haz que
el servidor MCP, si un cliente llama a un nombre retirado, responda con un error que diga por qué herramienta
sustituirlo (registra las viejas como herramientas ocultas que solo devuelven ese mensaje, o intercepta la llamada;
elige lo que permita `mcp` 2.x y documéntalo).

## Bloque 2. Lotes en una sola transacción

Dos rutas nuevas en `revit_mcp/lotes.py`, con el patrón de escritura de la fase 1 (`ejecutar`, `simular`,
`comprobar_alcance`, `EscrituraRechazada`, `transaccion`, `resultado_creacion` / `antes/despues`):

| Ruta | Cuerpo | Comportamiento |
|---|---|---|
| POST `/set_parameters/` | `changes[]` de `{element_ids[], parameters: {nombre: valor}}` o `{element_id, parameter_name, value}` (compatibilidad), `type_parameters` (bool o por cambio), `simular`, `forzar` | Todo en **una** transacción `IA: Parametros (<n> elementos)`. Resuelve cada nombre con `utils.buscar_por_nombre` una sola vez por parámetro y elemento tipo. Devuelve `antes/despues` por elemento y parámetro, `parameter_label`, `verificacion.coincide` global y `fallidos[]` con motivo (no aborta el lote por un parámetro de solo lectura: lo informa). `comprobar_alcance` sobre `elementos × parámetros`. Convierte Double con `convertir_valor` (mm, mm², mm³, grados). |
| POST `/create_elements/` | `elements[]` de `{kind, ...argumentos del creador}`, `simular`, `forzar` | `kind` ∈ `wall`, `floor`, `roof`, `ceiling`, `level`, `grid`, `column`, `beam`, `foundation`, `opening`, `toposolid`, `room`, `room_separation`, `detail_line`, `duct`, `pipe`, `family_instance`. Reutiliza las funciones internas de creación de `building.py`, `structure.py`, `structural.py`, `topografia.py`, `room.py`, `mep.py`, `placement.py` (extrae helpers si hoy están dentro del manejador, como hizo la 2a con `crear_rejilla` y `crear_nivel`). Una transacción `IA: Crear <n> elementos`. Valida **todos** los elementos antes de abrir la transacción (400 con el índice del que falla). Con `simular`, `plan` con recuento por `kind`. `creados` agrupado por `kind`. Si uno falla dentro de la transacción, se revierte todo y la respuesta dice cuál (índice y error de Revit). |

`set_parameter` y las rutas de creación antiguas siguen existiendo (las usan `probar_revit.py` y los clientes viejos).

## Bloque 3. Macros propias del usuario (`run_macro`)

Objetivo: lo repetitivo se ejecuta como un plugin determinista del usuario, y la IA solo elige cuándo y con qué
argumentos.

- Carpeta de macros: `%LOCALAPPDATA%\RevitMcp\macros\<nombre>\` con `macro.json` (manifiesto) y `macro.py`. Ruta
  configurable con la variable de entorno `REVIT_MCP_MACROS`.
- `macro.json`: `{"name", "description", "version", "args": {<nombre>: {"type": "int|float|str|bool|list|element_id|element_ids|level|view", "required", "default", "description"}}, "writes": true|false, "timeout_s"}`.
- `macro.py` define `def run(doc, uidoc, args, api)` donde `api` expone los helpers del servidor ya probados:
  `make_element_id`, `get_element_id_value`, `buscar_por_nombre`, `buscar_parametro`, `xyz_desde_mm`, `punto_a_mm`,
  `convertir_valor`, `resolver_nivel`, `resolver_vista`, `log(texto)`. La macro **no** abre transacciones: si
  `writes` es verdadero, el servidor la envuelve en `transaccion(doc, "Macro <nombre>")` con el patrón de escritura
  completo (copia, registro, `simular` con un `plan` que la macro puede devolver desde `def plan(doc, args, api)`
  opcional, y `resultado_creacion` sobre los ids que devuelva).
- GET `/macros/` lista los manifiestos válidos y los inválidos con su error. POST `/macros/run/` con
  `{"name", "args", "simular", "forzar"}` valida los `args` contra el manifiesto (400 con lo que falta o sobra),
  ejecuta y devuelve `output`, `result` (lo que retorne `run`), `creados`/`eliminados`/`antes/despues` según el
  caso y `ms`. Cada macro se carga con `imp`/`execfile` compatible con IronPython 2.7 y se recarga si cambió el
  archivo (compara `mtime`), para que el usuario edite sin reiniciar Revit.
- Add-ins en C# o pyRevit existentes: documenta en `CONTRATO.md` la forma de envolverlos en una macro Python
  (`__revit__.PostCommand(RevitCommandId.LookupCommandId("<id>"))` no admite argumentos ni transacción propia; solo
  para comandos sin parámetros y marcado `writes: false` porque el servidor no puede verificar lo que hace).
- Dos macros de ejemplo en `herramientas-dev/macros-ejemplo/`: `numerar_planos` (renumera planos con prefijo y paso)
  y `comentarios_por_nivel` (rellena `Comentarios` con el nombre del nivel en los elementos de una categoría),
  con sus pruebas en CPython.
- La herramienta MCP `list_macros` devuelve el catálogo con la descripción y los argumentos; `run_macro(name, args,
  simular)` ejecuta. INSTRUCCIONES_AGENTE.md: **antes de encadenar varias herramientas para una tarea repetitiva,
  mirar si hay una macro que la cubra.**

## Bloque 4. Rendimiento medible

- `snapshot_model` tardó 8,2 s con 2746 elementos bloqueando Revit. Mide por etapas (`ms` de recolección, bbox,
  parámetros, hash, escritura) y devuélvelas en `timings`. Objetivo: menos de 3 s en ese modelo con
  `include_parameters=true`. Pistas: un solo `FilteredElementCollector` con `WherePasses(ElementMulticategoryFilter)`,
  `get_BoundingBox(None)` solo si `include_bbox`, leer parámetros con `GetOrderedParameters()` una vez y hashear la
  cadena concatenada, no cada valor.
- La copia del `.rvt` en la primera escritura de la sesión: mide y devuelve `copia.ms`. Si supera 2 s, añade
  `escritura.copia_diferida()`: copiar en un hilo aparte con `System.IO.File.Copy` **antes** de abrir la transacción
  y esperar a que termine solo si la transacción llega a `Commit` (no cambies la garantía: nunca escribir sin copia
  terminada).
- En el puente (`tools/utils.py`), añade `ms_puente` (tiempo total de la llamada HTTP visto desde el puente) al lado
  del `ms` de Revit en `format_response`, para separar tiempo de Revit y tiempo de red/serialización.
- Nueva sección en `probar_revit.py --fase cons`: `set_parameters` con 20 elementos en una llamada (comparar `ms`
  con 20 llamadas a `set_parameter`), `create_elements` con 3 tipos mezclados y `simular`, `run_macro` de
  `comentarios_por_nivel` con `simular` y real, `snapshot_model` con `timings`, y una llamada a un nombre retirado
  que debe devolver el mensaje de sustitución.

## Reglas que siguen vigentes

Las de `PROMPT_FASE2.md`: IronPython 2.7 en `revit_mcp/`, patrón `ejecutar`, `comprobar_alcance` (200 salvo
`forzar`), mm hacia fuera, nunca nombres visibles en inglés (`buscar_por_nombre`, `BuiltInCategory`), tildes
conservadas, `try/except` para miembros que cambian entre Revit 2024 y 2027, y toda API nueva que uses entra en
`herramientas-dev/miembros_por_verificar_revit.md` como `por verificar`.

## Entrega

- `CONTRATO.md`, `README.md`, `LLM.txt`, `INSTRUCCIONES_AGENTE.md`: tabla de las 38 herramientas con la columna
  "sustituye a", ejemplos `curl` de `/set_parameters/`, `/create_elements/` y `/macros/run/`, y el formato de
  `macro.json`.
- Versión 0.4.0 en `pyproject.toml` y `revit_mcp/__init__.py`.
- Pruebas en CPython: cada herramienta de la tabla registrada y ninguna retirada (test sobre `tools/__init__.py`
  con un `mcp` simulado); `/set_parameters/` (lote, solo lectura informado, `simular` sin transacción, 401, límite);
  `/create_elements/` (mezcla de `kind`, validación previa con índice, reversión total, `plan`); `/macros/` (manifiesto
  inválido, args que faltan, `writes` con y sin `simular`, recarga por `mtime`); `timings` en snapshot.
- Resumen final con: herramientas retiradas y su sustituta, rutas nuevas, miembros de la API por verificar, y el
  **prompt de validación para el agente local** con el formato de `VALIDACION_2A.md` (pasos numerados con los
  argumentos exactos escritos en el prompt, respuesta esperada, informe OK/FALLO con respuestas literales),
  indicando que use las herramientas MCP por su nombre y que mida el tiempo total de cada paso.
