# Contrato del conector Revit MCP

Este documento describe el servidor HTTP que la extensión de pyRevit levanta
dentro de Revit, cómo se protege, cómo escribe en el modelo y qué rutas
expone. Es la referencia para el puente `main.py` y para cualquier cliente que
quiera hablar con Revit directamente. Versión del conector: **0.4.0**.

## El servidor

| Elemento | Valor |
|----------|-------|
| Tecnología | pyRevit Routes (servidor HTTP dentro del proceso de Revit) |
| Puerto | **48884** (por defecto de pyRevit Routes) |
| Prefijo de las rutas | `http://127.0.0.1:48884/revit_mcp/...` |
| Motor | IronPython 2.7 (los manejadores viven en `revit_mcp/`) |
| Puente MCP | `main.py` (CPython 3.11+, SDK mcp 2.2) en `http://127.0.0.1:8000` |
| Herramientas MCP | **40** (16 de lectura, 19 de escritura con `simular`, 4 macros, `execute_revit_code`); 0.4.0 consolida las 78 de 0.3.x en 40 sin cambiar las rutas HTTP, añade los lotes `/set_parameters/` y `/create_elements/` (una transacción por llamada) y las macros propias del usuario (`/macros/`, `/macros/run/`) |

El servidor debe usarse **solo desde loopback**. pyRevit Routes escucha en
todas las interfaces, así que el usuario aplica a mano esta regla de firewall
(consola de administrador) para bloquear cualquier conexión entrante al
puerto 48884 desde otras máquinas:

```bat
netsh advfirewall firewall add rule name="Block pyRevit Routes" dir=in action=block protocol=TCP localport=48884 profile=any
```

La regla bloquea el tráfico entrante desde la red; las conexiones locales
(`127.0.0.1`) del puente y de las pruebas siguen funcionando.

## Herramientas MCP (0.4.0)

La consolidación ocurre en el puente (`tools/`): cada herramienta que queda
despacha a las rutas HTTP de 0.3.x (que siguen todas existiendo) o a las tres
rutas nuevas de 0.4.0. Regla de diseño: **una tarea habitual se resuelve en
una o dos llamadas**. Cada herramienta tiene una descripción de menos de 60
palabras con un ejemplo de argumentos. Si un cliente llama a uno de los 51
nombres retirados, el servidor MCP responde con un resultado `is_error` que
dice por qué herramienta sustituirlo (`tools.HERRAMIENTAS_RETIRADAS`;
`tools.instalar_retiradas` envuelve `ToolManager.call_tool` del SDK `mcp` 2.x,
porque el SDK no permite ocultar herramientas de `tools/list` y 51 entradas
más en contexto es justo lo que se elimina).

| Herramienta | Sustituye a | Ruta(s) HTTP | Cómo |
|---|---|---|---|
| `get_revit_status` | — | `GET /status/` | igual |
| `get_revit_model_info` | `list_levels`, `list_worksets`, `list_phases_and_options`, `list_links`, `get_project_location` | `GET /model_info/` + `/list_levels/`, `/worksets/`, `/phases_options/`, `/links/`, `/project_location/` | `include=["levels","worksets","phases","links","location"]`; por defecto solo `/model_info/` |
| `list_views` | `list_revit_views`, `get_current_view_info` | `GET /list_views/`, `GET /current_view_info/`, `POST /query/` (`on_sheet`) | `current_only`, `view_type`, `on_sheet` (vistas en/fuera de plano, por `VIEWER_SHEET_NUMBER`) |
| `describe_view` | `get_view_extents`, `get_current_view_info` | `POST /view_extents/` (+ `GET /current_view_info/` sin `view_id`) | `view_id` opcional (activa) |
| `capture_view` | `get_revit_view` | `GET /get_view/<view_name>` | devuelve la imagen PNG (por eso se mantiene aparte: 40 herramientas, 39 sin ella) |
| `query_elements` | `find_elements`, `get_current_view_elements`, `ai_element_filter`, `get_selected_elements` | `POST /query/`, `GET /current_view_info/` (`current_view`), `GET /selected_elements/` (`selected`) | `current_view=true` resuelve la vista activa; `selected=true` devuelve la selección; `ai_element_filter` se traduce a `filters` (INSTRUCCIONES_AGENTE 2c) |
| `describe_element` | `get_element_properties`, `get_element_geometry` | `POST /describe/` (una por id) | `element_ids[]` hasta 20, respuesta por id; `include_geometry` |
| `dependency_graph` | — | `POST /dependency_graph/` | igual |
| `list_types` | `list_element_types`, `list_families`, `list_family_categories`, `list_category_parameters` | `POST /element_types/`, `GET /list_families/`, `GET /list_family_categories/`, `POST /list_category_parameters/` | `category` → tipos; solo `family`/`contains` → familias; nada → categorías; `with_parameters`; `loaded_only` (tipos con ejemplares o `is_active`) |
| `schedule_to_json` | — | `POST /schedule/` | igual |
| `list_warnings` | — | `GET /warnings/` | igual (`group_by`) |
| `set_parameters` | `set_parameter`, `set_type_parameter`, `modify_element` | **`POST /set_parameters/`** (nueva) | lote en una transacción; `type_parameters=true` acepta en `element_ids` el id del ejemplar o directamente el del tipo (como `set_type_parameter(type_id)`) |
| `create_elements` | `create_line_based_element`, `create_surface_based_element`, `create_level`, `create_grid`, `create_structural_column`, `create_structural_framing`, `create_foundation`, `create_opening`, `create_toposolid`, `create_room`, `create_room_separation`, `create_detail_line`, `create_duct`, `create_pipe`, `place_family` | **`POST /create_elements/`** (nueva) | lote en una transacción, `kind` |
| `transform_elements` | — | `POST /transform_elements/` | igual + `operation="array"` con `count` (0.4.0) |
| `delete_elements` | — | `POST /delete_elements/` | igual |
| `change_element_type` | — | `POST /change_type/` | igual, `element_ids[]` |
| `join_geometry` | — | `POST /join_geometry/` | igual |
| `set_workset` | — | `POST /set_workset/` | igual |
| `set_project_location` | — | `POST /set_project_location/` | igual (+ `forzar`, que la herramienta de 0.3.x no exponía) |
| `create_view` | — | `POST /create_view/` | igual |
| `set_active_view` | — | `POST /set_active_view/` | igual |
| `create_sheet_set` | `create_sheet` | `POST /sheet_set/` | un plano es `sheets` con un elemento |
| `create_schedule` | — | `POST /create_schedule/` | igual |
| `annotate` | `create_dimensions`, `tag_walls`, `tag_elements` | `POST /create_dimensions/`, `/tag_elements/`, `/tag_walls/` | `kind="dimension"` / `"tag"`; `tag_walls` = `kind="tag"` con `category="OST_Walls"` y sin ids |
| `color_elements` | `color_splash`, `clear_colors` | `POST /color_splash/`, `/clear_colors/` | `clear=true` limpia |
| `export` | `export_document`, `export_ifc`, `export_room_data` | `POST /export_document/`, `/export_ifc/`, `GET /room_data/` | `format` = `pdf` / `png` / `jpg` / `dwg` / `ifc` / `rooms_json` / `rooms_csv` (el CSV lo escribe el puente en `file_path`) |
| `link_file` | — | `POST /link_file/` | igual |
| `load_family` | — | `POST /load_family/` | igual |
| `analyze_model` | `analyze_model_statistics`, `get_material_quantities` | `GET /model_statistics/`, `POST /material_quantities/` | `include=["statistics","materials"]` |
| `check_clashes` | — | `POST /clash_check/` | igual |
| `snapshot_model` | — | `POST /snapshot/` | igual + `include_bbox` y `timings` (0.4.0) |
| `diff_snapshots` | — | `POST /diff_snapshots/` | igual |
| `create_mep_system` | — | `POST /create_mep_system/` | igual (conductos y tuberías van por `create_elements`) |
| `maintain_model` | `purge_unused`, `create_backup`, `save_document` | `POST /purge_unused/`, `/backup/`, `/save_document/` | `action` = `purge` / `backup` / `save` |
| `read_log` | — | `GET /log/` | igual |
| `create_grid_and_levels` | — | `POST /grid_levels/` | igual |
| `import_from_civil` | — | `POST /import_civil/` | igual |
| `list_macros` | nuevo | **`GET /macros/`** (nueva) | catálogo de macros del usuario |
| `run_macro` | nuevo | **`POST /macros/run/`** (nueva) | ejecuta una macro del usuario |
| `execute_revit_code` | — | `POST /execute_code/` | igual, último recurso |

Todas las de escritura devuelven `ms` (tiempo en Revit) y, desde 0.4.0,
`ms_puente` (tiempo total de la llamada visto desde el puente, red y JSON
incluidos); las que combinan varias rutas suman todas en `ms_puente`.

## Seguridad

| Regla | Si falla |
|-------|----------|
| Toda ruta exige el **token de sesión**: en POST como clave `"token"` del cuerpo JSON; en GET como parámetro de consulta `?token=...`. El decorador `requiere_token` (`revit_mcp/seguridad.py`) lo comprueba con longitud igual y comparación byte a byte, y elimina la clave del cuerpo antes de llamar al manejador. | `401` con cuerpo `{"error": "token ausente o incorrecto"}` |
| La cabecera **Origin no se puede comprobar en Routes**: `base.Request` de pyRevit no expone las cabeceras HTTP (`_headers` queda vacío; solo llegan `data`, `params` y `query_params`). La protección contra DNS rebinding la aporta el **puente** `main.py`: con el SDK mcp 2.2 y `host="127.0.0.1"`, el servidor MCP solo acepta `Host` `127.0.0.1:*`, `localhost:*`, `[::1]:*` y los `Origin` equivalentes. | El puente responde `421 Invalid Host header` a un `Host` falso y `403` a un `Origin` no permitido; la petición nunca llega a Revit. |
| El token vive en `%LOCALAPPDATA%\RevitMcp\token` (64 caracteres hexadecimales). `startup.py` lo genera con `RandomNumberGenerator` **en cada arranque de Revit**, lo escribe ahí, intenta restringir la ACL del archivo al usuario actual y lo guarda en memoria. | Si la ACL no se puede aplicar, se registra en el log de pyRevit y el archivo queda con los permisos por defecto de `%LOCALAPPDATA%`. Si Revit se reinicia, el token cambia: el puente relee el archivo tras un `401` y reintenta una vez; si vuelve `401` devuelve "el token cambió: Revit se reinició, reintenta en unos segundos". Si el archivo no existe, el puente devuelve "Revit no está abierto o el conector no ha iniciado". |

Comprobación manual desde PowerShell:

```powershell
$token = Get-Content "$env:LOCALAPPDATA\RevitMcp\token"
Invoke-RestMethod "http://127.0.0.1:48884/revit_mcp/status/?token=$token"
```

## Escritura segura

Toda ruta que modifica el modelo pasa por `revit_mcp/escritura.py`
(`ejecutar` → `preparar` → `transaccion` → verificación → `registrar`).
Las garantías son las mismas para las 19 herramientas de escritura de 0.4.0,
las 4 macros y todas las rutas de escritura de 0.3.x que siguen existiendo:

| Garantía | Detalle |
|----------|---------|
| **Comprobación previa** | `preparar(doc, ruta)` responde `409` si `doc.IsModifiable` es `True` (hay una transacción abierta de otra operación; cuerpo con `"open_transaction": true`) o si `doc.IsReadOnly`. Sin documento activo: `503`. |
| **Copia de seguridad** | Si el documento está guardado en disco y **no** es de trabajo compartido, antes de escribir se copia el `.rvt` con `System.IO.File.Copy` a `<carpeta del rvt>\backups\<nombre>_<yyyyMMdd_HHmmss>.rvt`. No se repite si ya hay una copia de hace menos de 30 minutos (`"reutilizada": true`) y se conservan las últimas 10. La copia refleja el **último guardado**, no el estado en memoria; la respuesta lo dice: `"copia": {"ruta", "refleja_guardado_de", "reutilizada", "nota", "ms"}`. El documento **nunca** se guarda por su cuenta. En modelos de trabajo compartido no se copia nada (`"copia": null`, `"nota_copia"` explica que se confía en las copias del central). |
| **Copia diferida (0.4.0)** | `escritura.CopiaDiferida`: el plan de la copia (ruta, reutilización) se decide en el hilo de Revit y el `File.Copy` corre en un hilo aparte mientras el manejador valida; `transaccion` (y `execute_code`) esperan a que termine **justo antes de `Commit`**, y `ejecutar` la espera al final en cualquier caso. La garantía no cambia: nunca se confirma una escritura sin la copia terminada. La respuesta añade `copia.diferida`, `copia.ms` (duración de la copia), `copia.espera_ms` (lo que esperó la transacción) y `copia.estado` (`terminada` / `error`, con `error` y `nota_copia` si falló: una copia fallida no impide el trabajo, pero se dice). Las rutas que escriben sin `escritura.transaccion` (`/load_family/`, `/save_document/`, `/set_active_view/`; `RUTAS_COPIA_SINCRONA`) copian de forma síncrona antes del cuerpo. |
| **Registro** | `registrar()` añade una línea JSON a `<carpeta del rvt>\mcp_log.jsonl` (o `%LOCALAPPDATA%\RevitMcp\mcp_log.jsonl` si el documento no está guardado) con `fecha`, `documento`, `ruta`, `ok`, `ms`, `args` (en `execute_code` el código completo), `error`, `resultado` y `simulado`. Rota a 5 MB (`mcp_log.jsonl.1`). Se lee con `GET /log/`. |
| **`simular`** | Parámetro booleano (por defecto `false`) en **todas** las rutas de escritura. Con `true` el manejador valida todo (elementos, tipos, niveles, unidades convertidas) y responde `{"simulado": true, "haria": [...]}` sin abrir transacción ni hacer copia. La llamada queda en el log con `"simulado": true`. |
| **Transacción `IA:`** | `transaccion(doc, nombre)` abre `DB.Transaction(doc, "IA: <acción>")` con `suppress_warnings`, hace `Commit` al salir y `RollBack` ante excepción. Si Revit revierte por un fallo de validación, la ruta responde `500` en vez de un éxito falso. Todas las transacciones del conector se llaman `IA: ...` (`IA: Crear muros/vigas`, `IA: Borrar elementos`, `IA: Parametro Mark de 1234`...), así el usuario distingue en el historial de deshacer lo que hizo la IA. `execute_code` usa un `TransactionGroup` con el mismo prefijo. |
| **Verificación** | Cada manejador vuelve a leer lo que cambió: creación → `"creados": [{"id", "categoria", "tipo", "nivel", "bbox_mm"}]`; parámetros → `"antes"` / `"despues"`; borrado → `"eliminados"` / `"en_cascada"`. La respuesta lleva `"ok"` y `"verificacion": {"coincide": bool, "detalle"}`. Si lo releído no coincide con lo pedido, `ok=false` con explicación y **nunca** se reintenta solo. |
| **Límite de alcance** | `delete_elements`, `transform_elements` (en `array`, `elementos × (count-1)`), `change_type`, `set_workset`, las macros de 0.3.0 (`/grid_levels/`, `/sheet_set/`, `/import_civil/`, sobre el total de elementos que crean), los lotes de 0.4.0 (`/set_parameters/` sobre `elementos × parámetros`, `/create_elements/` sobre `elements`) y las macros del usuario (sobre `plan()["count"]`) rechazan (`400`, con `limite` y `cantidad`) más de 200 por llamada salvo `forzar=true`. `execute_code` exige `description` (`400` si falta) y rechaza código con `doc.Delete(<colección>)` salvo `forzar=true`. |
| **Respuesta** | Éxito: `200` con los datos, `ok`, `ms`, `copia` (o `simulado`/`haria`). Error controlado: `400`/`404`/`409` con `{"error", ...detalles}`. Excepción: `500` con `error`, `traceback` y `copia` si ya se había hecho. |

Comandos de ejemplo (PowerShell; `$token` como arriba):

```powershell
$body = @{ element_id = 1234; parameter_name = "Comments"; value = "Revisado"; simular = $true; token = $token } | ConvertTo-Json
Invoke-RestMethod -Method Post -ContentType "application/json" -Body $body "http://127.0.0.1:48884/revit_mcp/set_parameter/"
```

## Rutas

Todas las rutas cuelgan de `/revit_mcp`. Los POST reciben JSON
(`Content-Type: application/json`) y **todos** llevan además la clave `token`
en el cuerpo; los GET llevan `?token=`. La respuesta es JSON: en éxito un
objeto con los datos (las rutas de escritura añaden `ok`, `ms`, `copia`,
`verificacion`), y en error `{"error": "..."}` con estado `400` (datos
incorrectos), `404` (no encontrado), `409` (transacción abierta, solo lectura,
elemento prestado), `503` (sin documento activo) o `500` (excepción). Sin
token: `401`. El puente convierte los errores en dicts con `http_status` y
las respuestas en JSON para el agente (`tools/utils.py::format_response`).

Tiempos de espera del puente: 30 s lectura; 120 s escritura (`create_*`,
`transform_elements`, `color_splash`...); 600 s `export_ifc`,
`export_document`, `check_clashes`, `get_material_quantities`, `link_file`,
`load_family`, `save_document`, `execute_revit_code`, `create_toposolid`,
`purge_unused`, `create_backup` y, desde 0.3.0, `snapshot_model`,
`diff_snapshots` e `import_from_civil`. El tiempo de espera largo no sustituye
al límite de elementos: Routes ejecuta cada llamada en el hilo de Revit, que
queda bloqueado mientras dura, y por eso toda macro aplica `comprobar_alcance`
al total de elementos que va a crear.

### Estado y modelo (lectura)

| Método | Ruta | Parámetros | Respuesta |
|--------|------|------------|-----------|
| GET | `/ping/` | — (sin token) | `{"ok": true}` en cuanto la extensión escucha. Para esperar a que Revit arranque: sondear `/ping/`, releer el token (cambia en cada arranque) y entonces llamar a `/status/`. Cada 401 se registra en pyRevit una vez por ruta y minuto, con el recuento |
| GET | `/status/` | — | `{"status": "active", "health": "healthy", "revit_available": true, "document_title": ..., "api_name": "revit_mcp"}`; `503` sin documento |
| GET | `/model_info/` | — | Proyecto, recuentos, avisos, vistas, planos, habitaciones, vínculos y bloque `file`: `is_workshared`, `path`, `last_saved`, `units`, `project_base_point_mm`, `survey_point_mm`, `true_north_deg` |
| GET | `/model_statistics/` | — | Estadísticas del modelo (elementos, categorías) |
| GET | `/room_data/` | — | Habitaciones con nivel, área y parámetros |
| GET | `/selected_elements/` | — | Elementos seleccionados en Revit |
| GET | `/list_levels/` | — | Niveles con `elevation` (pies, mostrada), `elevation_mm` (origen interno) y `elevation_shown_mm` (la que muestra Revit) |
| GET | `/element_properties/<element_id>` | `element_id` en la ruta | Propiedades y parámetros (`is_type_parameter` por parámetro) más `bbox_mm`, `level`, `workset`, `phase_created`, `phase_demolished`, `design_option`, `host_id`, `pinned`, `type_id` |
| GET | `/warnings/` | `max` (100) | `warnings[]`: `descripcion`, `severidad`, `element_ids[]`; `total`, `truncated` |
| GET | `/worksets/` | — | `is_workshared`, `worksets[]`: `id`, `nombre`, `propietario`, `editable`, `abierto`, `activo` |
| GET | `/phases_options/` | — | `phases[]` (`id`, `nombre`, `orden`) y `design_options[]` (`id`, `nombre`, `es_principal`, `conjunto`, `activa`) |
| GET | `/links/` | — | `links[]`: `id`, `nombre`, `tipo` (RVT/IFC/DWG...), `ruta`, `cargado`, `posicion` (`origen`/`interno`/`compartido`), `origen_mm` |
| GET | `/project_location/` | — | `project_base_point`, `survey_point` (posición interna y compartida en mm), `true_north_deg`, `active_project_location`, `project_locations[]` |
| GET | `/log/` | `last_n` (50) | `ruta` del log y `entradas[]` (últimas líneas de `mcp_log.jsonl`) |
| POST | `/find_elements/` | `category`, `name_contains`, `type_name`, `level_name`, `parameter_name`, `parameter_value`, `max` (100), `ids_only` | `elements[]` (`id`, `categoria`, `tipo`, `nivel`, `bbox_mm`), `ids[]`, `total_matched`, `truncated` |
| POST | `/element_geometry/` | `element_id`*, `detail` (`bbox`/`curves`/`solids`) | `bbox_mm`, `center_mm`, `size_mm` y, según `detail`, `location` (curva o punto) o `solids[]` (`volume_m3`, `area_m2`, `faces`, `centroid_mm`) |
| POST | `/element_types/` | `category`*, `family_name`, `max` (200) | `types[]`: `id`, `familia`, `tipo`, `parametros` (principales de tipo), `ejemplares` |

### Vistas

| Método | Ruta | Parámetros | Respuesta |
|--------|------|------------|-----------|
| GET | `/list_views/` | — | Vistas y planos del modelo |
| GET | `/current_view_info/` | — | Datos de la vista activa |
| GET | `/current_view_elements/` | — | Elementos visibles en la vista activa |
| GET | `/get_view/<view_name>` | `view_name` en la ruta (URL-encoded) | `{"image_data": <PNG en base64>, "content_type": "image/png"}` |
| POST | `/create_view/` | `view_type`, `name`, `level_name`, `section_box`, `simular` | `creados[]` (vista), `view_id`, `name` verificado |
| POST | `/set_active_view/` | `view_name`, `simular` | `antes`/`despues` (vista activa); sin transacción |

### Familias y colocación

| Método | Ruta | Parámetros | Respuesta |
|--------|------|------------|-----------|
| GET | `/list_families/` | `contains`, `category`, `limit` (50) (query) | `families[]` (`family_name`, `type_name`, `category`, `is_active`, `type_id`), `total_matched`, `truncated` |
| GET | `/list_family_categories/` | — | Categorías de familia |
| POST | `/place_family/` | `family_name`, `type_name`, `location` (mm), `rotation`, `level_name`, `properties`, `simular` | `creados[]`, `requested_location`/`actual_location`, `host_wall_id` |
| POST | `/load_family/` | `file_path` (.rfa), `simular` | `family_id`, `types[]` (sin transacción: `LoadFamily` abre la suya) |

### Creación de elementos

| Método | Ruta | Parámetros | Respuesta |
|--------|------|------------|-----------|
| POST | `/create_line/` | `elements`: muros/vigas (puntos en mm, tipo, nivel), `simular` | `creados[]`, `errors[]` por elemento |
| POST | `/create_surface/` | `elements`: suelos/cubiertas (contornos en mm, tipo, nivel), `simular` | `creados[]`, `errors[]` |
| POST | `/create_level/` | `levels`: (nombre, elevación mm respecto al origen interno, el marco de las XYZ), `simular` | `creados[]` con `elevation_mm` verificada (origen interno) y `elevation_shown_mm` (la que muestra Revit según la Base de elevación del tipo de nivel: con el punto base desplazado difieren) |
| POST | `/create_grid/` | `grids`: (nombre, inicio, fin mm), `simular` | `creados[]` con `name` |
| POST | `/create_framing/` | `elements`: vigas (puntos mm, tipo, nivel), `simular` | `creados[]` |
| POST | `/create_column/` | `columns[]`: `point` (mm), `base_level`*, `top_level`, `top_offset`, `type_name`, `rotation`, `simular` | `creados[]` con `top_level` y `point_mm` |
| POST | `/create_foundation/` | `foundations[]`: `point`+`level`+`type_name` (zapata aislada) / `wall_id` o `curve`+`type_name` (`WallFoundation`) / `boundary`+`level`+`type_name` (losa), `simular` | `creados[]` |
| POST | `/create_opening/` | `host_id`*, `points[]` (mm; en muro exactamente 2 esquinas opuestas sobre la cara del muro, se usan tal cual; polígono en suelo/cubierta), `simular` | `creados[]`, `host` |
| POST | `/create_toposolid/` | `points[]` (mm) o `csv_path` (P,N,E,Z / X,Y,Z; metros salvo `units`), `level_name`*, `type_name`, `boundary`, `simular` | `creados[]`, `source` (formato detectado, extensión) |
| POST | `/create_room/` | `level_name`, `location`, `name`, `number`, `simular` | `creados[]`, `area` |
| POST | `/create_room_separation/` | `lines`, `view_name`, `level_name`, `simular` | `creados[]`, `line_ids` |
| POST | `/create_detail_line/` | `start_point`, `end_point`, `view_name`, `line_style`, `simular` | `creados[]` |
| POST | `/create_duct/` | `start_point`, `end_point`, `duct_type`, `system_type`, `level_name`, `diameter` o `width`+`height`, `simular` | `creados[]`, `dimensions_mm` |
| POST | `/create_pipe/` | `start_point`, `end_point`, `pipe_type`, `system_type`, `level_name`, `diameter`, `simular` | `creados[]`, `diameter_mm` |
| POST | `/create_mep_system/` | `system_type`, `system_name`, `element_ids`, `simular` | `system_id`, `antes`/`despues` (nombre) |

### Modificación

| Método | Ruta | Parámetros | Respuesta |
|--------|------|------------|-----------|
| POST | `/modify_element/` | `element_id`, `parameters` (dict nombre → valor; longitudes en mm, áreas mm², volúmenes mm³, ángulos en grados), `simular` | `antes`/`despues` por parámetro, `failed[]` |
| POST | `/set_parameter/` | `element_id`, `parameter_name`, `value` (longitudes en mm, áreas mm², volúmenes mm³, ángulos en grados; el resto tal cual), `simular` | `antes`/`despues`, `is_type_parameter` |
| POST | `/set_type_parameter/` | `type_id` o `element_id`, `parameter_name`, `value` (mismas unidades que `set_parameter`), `simular` | `antes`/`despues`, `afecta_ejemplares` |
| POST | `/change_type/` | `element_ids`, `type_name` (o `type_id`), `simular`, `forzar` | `antes`/`despues` (tipo) por elemento |
| POST | `/delete_elements/` | `element_ids`, `simular`, `forzar` | `eliminados[]`, `en_cascada[]`, `antes[]` (descripción previa) |
| POST | `/transform_elements/` | `element_ids`, `operation` (move/rotate/mirror/copy), `vector`, `axis_point`, `angle`, `mirror_plane`, `simular`, `forzar` | `antes`/`despues` (bbox), `creados[]` en copy; un elemento fijado (`Pinned`) se puede copiar o repetir en matriz (0.4.1), pero move/rotate/mirror responden `400` con `pinned_ids` |
| POST | `/set_workset/` | `element_ids`, `workset_name`, `simular`, `forzar` | `antes`/`despues` (subproyecto) |
| POST | `/join_geometry/` | `element_id_a`, `element_id_b`, `unjoin`, `simular` | `antes`/`despues` (`joined`) |
| POST | `/set_project_location/` | `base_point_mm`, `survey_point_mm`, `true_north_deg`, `acquire_from_link_id` (solo, sin los otros), `forzar`, `simular` | `antes`/`despues` (ubicación completa). `409` si el punto está anclado o recortado y no se pasa `forzar=true` (mover un punto recortado cambia las coordenadas compartidas de todo el modelo) |
| POST | `/color_splash/` | `category_name` (`OST_Walls`, alias `walls` o nombre visible; 0.4.2), `parameter_name`, `use_gradient`, `custom_colors`, `simular` | Elementos coloreados, `verificacion` (muestra) |
| POST | `/clear_colors/` | `category_name` (como en `/color_splash/`), `simular` | Colores restablecidos |
| POST | `/list_category_parameters/` | `category_name` | Parámetros de la categoría (lectura) |

### Anotación y documentación

| Método | Ruta | Parámetros | Respuesta |
|--------|------|------------|-----------|
| POST | `/create_dimensions/` | `element_ids`, `dimension_type`, `simular` | `creados[]` (cota con `value`) |
| POST | `/tag_walls/` | `use_leader`, `tag_type_name`, `simular` | `creados[]` (etiquetas), `walls_already_tagged` |
| POST | `/tag_elements/` | `element_ids`, `view_name`, `add_leader`, `orientation`, `offset`, `tag_type_name`, `simular` | `creados[]`, `skipped[]` |
| POST | `/create_sheet/` | `sheet_number`, `sheet_name`, `title_block_name`, `simular` | `creados[]` (plano) |
| POST | `/create_schedule/` | `category`, `fields`, `schedule_name`, `simular` | `creados[]` (tabla), `fields_not_found` |
| POST | `/export_document/` | `view_name`, `format` (pdf/png/jpg/dwg), `resolution` | Ruta del archivo exportado (transacción `IA: Exportar documento`) |

### Análisis, interoperabilidad, documento y mantenimiento

| Método | Ruta | Parámetros | Respuesta |
|--------|------|------------|-----------|
| POST | `/ai_filter/` | `category`, `type_name`, `visible_in_view`, `bounding_box_min`, `bounding_box_max`, `max_elements` | Elementos filtrados |
| POST | `/material_quantities/` | `categories` | Cantidades de material |
| POST | `/clash_check/` | `set_a_categories`, `set_b_categories`, `max_clashes` | Interferencias detectadas |
| POST | `/export_ifc/` | `file_path`, `ifc_version`, `export_base_quantities`, `view_name` | Ruta del IFC (transacción `IA: Exportar IFC`) |
| POST | `/link_file/` | `file_path`, `mode`, `position`, `simular` | `creados[]` (vínculo o importación) |
| POST | `/save_document/` | `file_path`, `overwrite`, `simular` | `antes`/`despues` del archivo (fecha, tamaño); sin transacción; la copia previa refleja el guardado anterior |
| POST | `/purge_unused/` | `max_rounds` (3), `forzar`, `simular` | `candidatos[]`, `eliminados[]`, `rounds`; con `simular` solo lista. Solo purga con el PerformanceAdviser; si no está disponible responde `409` y la lista de reserva (tipos sin ejemplares) es solo informativa. Más de 200 tipos exige `forzar=true`. Una transacción `IA:` por ronda |
| POST | `/backup/` | `suffix`, `simular` | `copia` forzada (`backups\<nombre>_<marca>_<suffix>.rvt`); `400` en modelos compartidos o sin guardar |

### Navegación profunda (0.3.0, lectura)

Ninguna abre transacción. Los parámetros se resuelven como en `set_parameter`
(nombre visible, alias inglés o `BuiltInParameter`), las categorías por
`BuiltInCategory` y los valores numéricos van en mm, mm², mm³ o grados.

| Método | Ruta | Parámetros | Respuesta |
|--------|------|------------|-----------|
| POST | `/describe/` | `element_id`*, `depth` (0-2), `include_geometry` | `categoria`, `familia`, `tipo`, `type_id`, `nivel`, `host`/`host_id`, `workset`, `phase_created`, `phase_demolished`, `design_option`, `pinned`, `bbox_mm`, `parameters` {`instance[]`, `type[]`: `name`, `value` (mm, mm², mm³, grados), `display`, `unit`, `is_read_only`, `is_shared`, `guid`, `builtin`}, `hosted_elements[]` (`FamilyInstance.Host` inverso y `HostObject.FindInserts`), `joined_elements[]` (`JoinGeometryUtils`), `dependents[]` (`GetDependentElements`, solo categorías de modelo), `referenced_by[]` (cotas y etiquetas **solo de la vista activa**), `counts`; con `include_geometry`: `geometry` (`location`, `solids[]`, `volume_m3`, `area_m2`). Con `depth` 1 los relacionados llevan categoría, tipo y nivel; con 2, además sus propios `hosted_ids` y `joined_ids` |
| POST | `/dependency_graph/` | `element_id`*, `max_nodes` (100, máx 500), `max_depth` (2, máx 6) | `nodes[]` (`id`, `categoria`, `tipo`, `nivel`, `bbox_mm`, `depth`), `edges[]` (`from`, `to`, `kind` = `hosts` / `joins` / `depends`), `node_count`, `edge_count`, `truncated` |
| POST | `/query/` | `category` (nombre o lista), `family`, `type_name`, `level`, `view_id`, `workset`, `phase`, `filters[]` (`{"parameter", "op", "value"}`; `op` = `=`, `!=`, `>`, `<`, `>=`, `<=`, `contains`, `starts`, `empty`, `not_empty`, `exists`), `bbox_min_mm` + `bbox_max_mm`, `sort_by` (`-` = descendente), `page`, `page_size` (100, máx 500), `fields[]`, `name_contains`, `ids_only` | `elements[]` (`id`, `categoria`, `tipo`, `familia`, `nivel`, `bbox_mm`, `fields`), `ids[]`, `count`, `total_matched`, `scanned`, `page`, `pages`, `truncated`, `native[]` (criterios evaluados en Revit), `python_filters[]`, `warnings[]`. Categoría, vista, subproyecto, caja envolvente y los `filters` con `=`, `>`, `<`, `>=`, `<=`, `contains` o `starts` sobre un `BuiltInParameter` o un parámetro compartido de ejemplar usan `ElementParameterFilter`; el resto (`!=`, `empty`, `exists`, parámetros de proyecto o de tipo, familia, tipo, fase) se evalúa en Python. Los textos no distinguen mayúsculas. Al menos un criterio |
| POST | `/find_elements/` | como en 0.2.0 | Alias de `/query/`: conserva `elements`, `ids`, `count`, `total_matched`, `scanned`, `truncated` y `filters`. `max` se limita a 500 y un nivel inexistente responde `404` con `available_levels` |
| GET | `/warnings/` | `max` (100), **nuevo** `group_by=description` | Con `group_by`: `groups[]` (`descripcion`, `failure_definition_guid`, `failure` (miembro de `BuiltInFailures`), `severidad`, `count`, `element_ids[]`, `elements_total`, `sugerencia`), `group_count`, `total`. El grupo y la sugerencia se eligen por el `FailureDefinitionId` (`GetFailureDefinitionId().Guid`), no por el texto. Sin `group_by`, la respuesta de 0.2.0 sin cambios |
| POST | `/schedule/` | `name`* o `view_id`*, `start_row` (0), `max_rows` (500, máx 5000) | `schedule` (`id`, `name`, `title`), `headers[]`, `headers_from`, `rows[][]` (textos como los muestra Revit, `GetTableData().GetSectionData(SectionType.Body)` y `GetCellText`), `row_count`, `total_rows` (filas de datos, sin la fila de encabezados), `truncated`, `fields[]` (`name`, `heading`, `hidden`), `is_itemized`, `show_grand_total`. Va por POST y no por GET con el nombre en la ruta porque los nombres llevan espacios y tildes |
| POST | `/snapshot/` (`name` es un nombre, no una ruta: siempre se guarda en `snapshots\` junto al `.rvt`) | `name`*, `categories[]`, `parameters[]`, `include_parameters` (true), `max_elements` (20000), `overwrite` | Escribe `<carpeta del rvt>\snapshots\<name>.json` (o `%LOCALAPPDATA%\RevitMcp\snapshots\` si el modelo no está guardado) con, por elemento, `id`, `unique_id`, `categoria`, `tipo`, `nivel`, `bbox_mm`, `hash` de parámetros y `params`. Por defecto entran los elementos de categoría de modelo no específicos de vista más niveles y rejillas. Responde `ruta`, `count`, `total_elements`, `truncated` (+ `warning`), `categories`, `ms`. Sin transacción; `409` si el archivo existe y no se pasa `overwrite` |
| POST | `/diff_snapshots/` | `a`*, `b` (otra instantánea; vacío o `actual` = el modelo ahora), `max_items` (500) | Compara por `UniqueId`: `added[]`, `removed[]`, `modified[]` (`cambios[]` {`parametro`, `antes`, `despues`}, `bbox_movido`, `bbox_antes_mm`, `bbox_despues_mm`), `counts`, `truncated`, `a` y `b` (metadatos); `warning` si no cubren las mismas categorías |
| POST | `/view_extents/` | `view_id`* (o `view_name`) | `view_type`, `is_template`, `scale`, `level`, `discipline`, `detail_level`, `view_template`, `crop` (`active`, `visible`, `box`: `min_mm`/`max_mm` en coordenadas de vista y `min_model_mm`/`max_model_mm`), `view_range` (`top`, `cut`, `bottom`, `view_depth`, `underlay_bottom`: `level`, `offset_mm`), `section_box` (vistas 3D), `sheet_number`, `phase` |

### Macros de proyecto (0.3.0)

Pasan por `escritura.ejecutar` como cualquier escritura: con `simular` validan
todo y responden `haria` más `plan`; aplican `comprobar_alcance` (200 salvo
`forzar`) al total de elementos que van a crear y reutilizan el código interno
de `create_grid`, `create_level`, `create_sheet`, `create_toposolid` y
`link_file`.

| Método | Ruta | Parámetros | Respuesta |
|--------|------|------------|-----------|
| POST | `/grid_levels/` | `x_spacings_mm[]`, `y_spacings_mm[]`, `x_names`, `y_names` (lista completa o primer nombre desde el que continuar: `1`, `A`, `P1`), `levels[]` (`{name, elevation_mm}`), `origin_mm`, `extension_mm` (2000), `simular`, `forzar` | Rejilla completa con nombres correlativos (1, 2, 3 / A, B, C) y niveles en una sola transacción `IA: Rejilla y niveles`. `plan` (`grids_x[]`, `grids_y[]` con `x_mm`/`y_mm`, `start_mm`, `end_mm`; `levels[]`; `counts`), `creados[]` más `grids[]` y `levels[]` (`elevation_mm`). `400` si un nombre de rejilla o de nivel ya existe (`existing`, `repeated`) |
| POST | `/sheet_set/` | `sheets[]` (`{number, name, title_block, views[] {view_name | view_id, position_mm {x, y}}}`), `simular`, `forzar` | Planos con vistas colocadas: `Viewport.CanAddViewToSheet` antes de `Viewport.Create` (tablas con `ScheduleSheetInstance.Create`; sin `position_mm`, el centro del cajetín). Una vista que ya está en otro plano se informa en `skipped` y no se coloca. `sheets[]` (`id`, `number`, `name`, `title_block`, `views_placed[]`, `skipped[]`), `creados[]`, `plan`. `404` con `missing_views` si una vista no existe; `400` si el número de plano ya existe |
| POST | `/import_civil/` | `file_path`* (LandXML `.xml`/`.landxml`, CSV `P,N,E,Z` / `X,Y,Z`, o `.dwg`/`.dxf`/`.dgn`), `level`*, `use_shared_coordinates`, `origin_offset_mm`, `units` (CSV: `m` por defecto; LandXML lee `<Units>`), `type_name`, `placement` (`origin`, `center` (por defecto al adquirir coordenadas), `shared`), `simular`, `forzar` | LandXML (`Surface/Definition/Pnts` o `CgPoints`, puntos en orden norte-este-cota) o CSV → toposólido (`creados[]`, `toposolid_id`, `source` con `format`, `points`, `extent_mm`; `400` si Revit no tiene `Toposolid`). DWG → vínculo en la planta del nivel (o la vista activa) (`creados[]`, `link_id`) y, con `use_shared_coordinates`, `doc.AcquireCoordinates` con `coordinates.antes/despues`; `409` (`shared_coordinates_set`) si el proyecto ya tiene coordenadas compartidas, y `409` (`pinned`/`clipped`) si el punto base o el de replanteo están fijados o recortados, salvo `forzar=true`. `.dgn` se vincula con `DGNImportOptions` |

### Lotes en una sola transacción (0.4.0)

Motivo: en la validación de la 2a Revit respondía en menos de un segundo por
llamada (`set_parameter` 391 ms) y aun así cambiar 20 comentarios eran 20
idas y vueltas y 20 entradas de deshacer. Las rutas de 0.3.x
(`/set_parameter/`, `/create_line/`...) siguen existiendo; estas dos las
reúnen en una transacción y reutilizan sus helpers internos (`planificar_*` /
`crear_*` de `building.py`, `structure.py`, `estructural.py`, `topografia.py`,
`rooms.py`, `detail.py`, `mep.py` y `placement.py`).

| Método | Ruta | Parámetros | Respuesta |
|--------|------|------------|-----------|
| POST | `/set_parameters/` | `changes[]` de `{element_ids[] \| element_id, parameters: {nombre: valor}, type_parameters}` o `{element_id, parameter_name, value}` (compatibilidad con `set_parameter`), `type_parameters` (global), `simular`, `forzar` | Todo en **una** transacción `IA: Parametros (<n> elementos)`. Cada nombre se resuelve con `utils.buscar_por_nombre` (visible, alias inglés o `BuiltInParameter`) una sola vez por elemento, o por tipo con `type_parameters` (un parámetro de tipo compartido por varios elementos se fija una vez); si el parámetro no está en el ejemplar se busca en su tipo (como `set_parameter`, `is_type_parameter: true`). Los Double van en mm, mm², mm³ o grados (`convertir_valor`). Devuelve `count` (elementos), `elements[]`, `parameters_set`, `changes[]` (`element_id`, `parameter_name`, `parameter_label`, `is_type_parameter`, `type_id`, `antes`, `despues`, `coincide`), `antes` / `despues` (`{element_id: {etiqueta: valor}}`), `fallidos[]` (`element_id`, `parameter_name`, `motivo`: `read-only`, `parameter not found` (+ `available_parameters`), `element not found`, valor inválido; **no abortan el lote**), `ok` y `verificacion.coincide` global. `400` si todos los pares fallan (con `fallidos`), si faltan `changes` o si supera 200 pares `elementos × parámetros` sin `forzar`. Con `simular`: `haria[]` con `antes`/`despues` por par, `elements` y `fallidos` |
| POST | `/create_elements/` | `elements[]` de `{kind, ...argumentos del creador de 0.3.x}`, `simular`, `forzar` | `kind` ∈ `wall`, `floor`, `roof`, `ceiling`, `level`, `grid`, `column`, `beam`, `foundation`, `opening`, `toposolid`, `room`, `room_separation`, `detail_line`, `duct`, `pipe`, `family_instance` (alias: `muro`, `structural_column`, `place_family`...). Argumentos por kind: `wall` y `beam` = un elemento de `/create_line/` (`beam` también acepta los de `/create_framing/`); `floor`/`roof`/`ceiling` = `/create_surface/` (techo con `DB.Ceiling.Create`, Revit 2022+, y `CeilingType`; sin ellos se crea como suelo y `haria.nota` lo dice); `level` = `{name, elevation_mm}`; `grid` = `/create_grid/`; `column` = `/create_column/`; `foundation` = `/create_foundation/`; `opening` = `/create_opening/`; `toposolid` = `/create_toposolid/`; `room` = `/create_room/`; `room_separation` = `/create_room_separation/` (crea una línea por segmento); `detail_line` = `/create_detail_line/`; `duct` / `pipe` = `/create_duct/` / `/create_pipe/`; `family_instance` = `/place_family/`. Se validan **todos** antes de abrir la transacción: `400` (o `404`) con `error` = `elements[i]: ...`, `index` y `kind` del primero que falla (nivel o tipo inexistente, puntos iguales, nombre de nivel o rejilla repetido en el lote o en el proyecto, hueco fuera del muro...). Una transacción `IA: Crear <n> elementos`; si Revit rechaza uno, se revierte todo y responde `500` con `index`, `kind`, `revit_error` y `rolled_back: true`. Éxito: `creados` agrupado por `kind` (cada uno con `id`, `categoria`, `tipo`, `nivel`, `bbox_mm`, `index` y extras: `name` / `elevation_mm` en niveles, `name` en rejillas, `top_level` / `point_mm` en pilares, `properties_set` en familias, `name` / `number` / `area` en habitaciones), `creados_ids[]`, `count`, `plan.counts`. Con `simular`: `haria[]` (con `index` y `kind`) y `plan` = `{counts: {kind: n}, total}`. Un elemento que depende de otro del mismo lote (muro sobre un nivel nuevo) va en dos llamadas |

### Macros propias del usuario (0.4.0)

Objetivo: lo repetitivo se ejecuta como un plugin determinista del usuario y
la IA solo elige cuándo y con qué argumentos. Carpeta
`%LOCALAPPDATA%\RevitMcp\macros\<nombre>\` (configurable con la variable
de entorno `REVIT_MCP_MACROS`), una subcarpeta por macro con `macro.json` y
`macro.py`. Dos macros de ejemplo con pruebas en
`herramientas-dev/macros-ejemplo/` (`numerar_planos`, `comentarios_por_nivel`).

**`macro.json`** (el `name` debe coincidir con la carpeta):

```json
{
  "name": "comentarios_por_nivel",
  "description": "Rellena Comentarios con el nombre del nivel en los elementos de una categoría",
  "version": "1.0",
  "args": {
    "category":  {"type": "str",   "required": true,  "description": "BuiltInCategory (OST_Walls)"},
    "parameter": {"type": "str",   "required": false, "default": "Comments"},
    "overwrite": {"type": "bool",  "required": false, "default": false},
    "level":     {"type": "level", "required": false, "default": null, "description": "Solo este nivel"}
  },
  "writes": true,
  "timeout_s": 120
}
```

`type` ∈ `int`, `float`, `str`, `bool`, `list`, `element_id`, `element_ids`
(se comprueba que existen), `level` y `view` (la macro los recibe ya
resueltos como `DB.Level` / `DB.View`). `timeout_s` es informativo (Routes no
puede abortar una macro en curso); el puente usa `run_macro(timeout_s=...)`.

**`macro.py`** define `def run(doc, uidoc, args, api)` (obligatoria) y, si
quiere, `def plan(doc, args, api)` que devuelve lo que haría (para `simular`;

> Una macro con `writes: true` **debe** definir `plan()` devolviendo `{"count": n}`: sin él, `POST /macros/run/` responde `400` (`plan_required`) salvo `forzar=true`, porque el límite de 200 elementos no se puede comprobar antes de escribir. Con `simular` se devuelve una `nota` en lugar del `plan`.
`plan["count"]` alimenta `comprobar_alcance`). `api` expone los helpers ya
probados del servidor: `make_element_id`, `get_element_id_value`,
`get_element_name`, `buscar_por_nombre(elem, nombre)`, `buscar_parametro(elem,
nombre)` (también de tipo), `xyz_desde_mm`, `punto_a_mm`, `convertir_valor`,
`resolver_nivel(nombre)`, `resolver_vista(nombre_o_id)`, `nombre_nivel(elem)`,
`log(texto)` (va a `output`), `DB`, `MM_TO_FEET`, `FEET_TO_MM`. **La macro no
abre transacciones**: con `writes: true` el servidor la envuelve en
`escritura.ejecutar` + `transaccion(doc, "Macro <nombre>")` con el patrón
completo (copia, `mcp_log.jsonl`, `simular` con el `plan`, `comprobar_alcance`
sobre `plan["count"]`, y `resultado_creacion` / `verificar_eliminados` /
`antes`-`despues` sobre lo que devuelva `run`: una lista de ids → `creados`;
un dict con `creados`, `eliminados`, `antes`, `despues` → cada uno verificado,
el resto en `result`). Con `writes: false` se ejecuta sin copia ni
transacción (Revit rechaza cualquier escritura fuera de transacción). Cada
`macro.py` se carga con `imp.load_source` (IronPython 2.7) y se recarga si
cambió el `mtime`, para editar sin reiniciar Revit; debe ser IronPython 2.7
(sin f-strings ni anotaciones).

| Método | Ruta | Parámetros | Respuesta |
|--------|------|------------|-----------|
| GET | `/macros/` | — | `carpeta`, `macros[]` (`name`, `description`, `version`, `args`, `writes`, `timeout_s`, `carpeta`), `invalidas[]` (`name`, `carpeta`, `error`: `falta macro.py`, `macro.json no es JSON valido`, `type 'x' no admitido`, `name no coincide con la carpeta`...), `count` |
| POST | `/macros/run/` | `name`*, `args`, `simular`, `forzar` | `404` con `available_macros` si no existe; `400` si el manifiesto es inválido, si `macro.py` no importa (`could not be loaded`, con `traceback`) o no define `run`, o si los `args` no cuadran (`faltan[]`, `sobran[]`, `invalidos{}`, `args_spec`). Éxito: `name`, `args` (tal como llegaron), `writes`, `output` (líneas de `api.log`), `result`, `reloaded`, `ms` y, con `writes`, `plan`, `creados` / `eliminados` / `antes` / `despues` según lo devuelto, `ok`, `copia`, transacción `IA: Macro <nombre>`. Con `simular` y `writes`: `haria` = `[{accion: macro, name, args, undo_name}]`, `plan` (o `nota` si la macro no define `plan()`), `output`. Si `run` lanza una excepción: `500` con el error y la transacción revertida |

### Otras ampliaciones de 0.4.0 en rutas existentes

| Ruta | Cambio |
|------|--------|
| `POST /transform_elements/` | `operation="array"` con `count` (total incluyendo el original, ≥ 2): `count-1` copias a `vector`, `2·vector`, ... en una transacción `IA: Matriz de <n> elementos`; `creados[]` y `new_element_ids`; límite 200 sobre `elementos × (count-1)` |
| `POST /snapshot/` | `include_bbox` (por defecto `true`; con `false` no llama a `get_BoundingBox`) y `timings` en la respuesta: `recoleccion_ms`, `descripcion_ms`, `bbox_ms`, `parametros_ms`, `hash_ms`, `escritura_ms`, `total_ms`, `elements`, `name_lookups` (nombres de tipo, nivel y elementos referenciados se cachean por id). Los parámetros se leen una vez con `GetOrderedParameters()` y se hashea la cadena concatenada. `ms` = `total_ms` |
| `POST /query/` | cada elemento trae `nombre` (nombre propio del elemento: vistas, niveles, rejillas...) además de `tipo` |
| `POST /list_category_parameters/` | `category_name` acepta `BuiltInCategory` (`OST_Walls`) o alias (`walls`) además del nombre visible |
| `POST /create_surface/` | `element_type: "ceiling"` crea un techo con `DB.Ceiling.Create` y `CeilingType` (Revit 2022+); sin ellos, un suelo como en 0.3.x, y `haria.nota` lo dice |

### Ejecución de código

| Método | Ruta | Parámetros | Respuesta |
|--------|------|------------|-----------|
| POST | `/execute_code/` | `code` (IronPython 2.7), `description` (**obligatoria**, ≤ 60 caracteres), `simular`, `forzar` | Éxito: `{"status": "success", "description", "undo_name": "IA: ...", "output", "code_executed", "elementos_modificados": {"creados", "eliminados", "por_categoria", "advertencias": {"antes", "despues", "nuevas"}}, "ok", "ms", "copia"}`. Error (`500`): `{"status": "error", "error", "error_type", "traceback", "code_attempted", "undo_name", "partial_output"?, "hints"?, "open_transaction"?}`. `400` si falta `code` o `description`, o si borra colecciones sin `forzar`. |

`elementos_modificados` se calcula comparando los ids de elementos y las
advertencias del modelo antes y después del código (Routes no expone
`DocumentChangedEventArgs`); los cambios en elementos existentes no se
detectan.

## Ejemplos `curl` por ruta de escritura

`TOKEN` es el contenido de `%LOCALAPPDATA%\RevitMcp\token` y `R` es
`http://127.0.0.1:48884/revit_mcp`. Todos admiten `"simular": true` para ver
`haria` sin tocar el modelo.

```bash
# Muro entre dos puntos (mm) en el nivel "Nivel 1"
curl -X POST $R/create_line/ -H "Content-Type: application/json" -d '{"token":"TOKEN","elements":[{"element_type":"wall","start_point":{"x":0,"y":0,"z":0},"end_point":{"x":5000,"y":0,"z":0},"level_name":"Nivel 1","height":3000,"type_name":"Generic - 200mm"}]}'
# Suelo por contorno cerrado
curl -X POST $R/create_surface/ -H "Content-Type: application/json" -d '{"token":"TOKEN","elements":[{"element_type":"floor","level_name":"Nivel 1","boundary":[{"x":0,"y":0,"z":0},{"x":5000,"y":0,"z":0},{"x":5000,"y":4000,"z":0},{"x":0,"y":4000,"z":0}]}]}'
# Nivel nuevo
curl -X POST $R/create_level/ -H "Content-Type: application/json" -d '{"token":"TOKEN","levels":[{"name":"Nivel 2","elevation":3200}]}'
# Rejilla
curl -X POST $R/create_grid/ -H "Content-Type: application/json" -d '{"token":"TOKEN","grids":[{"name":"A","start_point":{"x":0,"y":-1000,"z":0},"end_point":{"x":0,"y":20000,"z":0}}]}'
# Viga
curl -X POST $R/create_framing/ -H "Content-Type: application/json" -d '{"token":"TOKEN","elements":[{"start_point":{"x":0,"y":0,"z":0},"end_point":{"x":6000,"y":0,"z":0},"level_name":"Nivel 2","type_name":"IPE300"}]}'
# Pilar estructural entre niveles
curl -X POST $R/create_column/ -H "Content-Type: application/json" -d '{"token":"TOKEN","columns":[{"point":{"x":0,"y":0,"z":0},"base_level":"Nivel 1","top_level":"Nivel 2","type_name":"HEB300","rotation":90}]}'
# Zapata aislada / corrida / losa
curl -X POST $R/create_foundation/ -H "Content-Type: application/json" -d '{"token":"TOKEN","foundations":[{"point":{"x":0,"y":0,"z":0},"level":"Cimentacion","type_name":"Zapata 1500x1500"},{"wall_id":1234,"type_name":"Zapata corrida 600"},{"boundary":[{"x":0,"y":0,"z":0},{"x":8000,"y":0,"z":0},{"x":8000,"y":8000,"z":0},{"x":0,"y":8000,"z":0}],"level":"Cimentacion","type_name":"Losa 400"}]}'
# Hueco en un muro (2 esquinas) y en un suelo (polígono)
curl -X POST $R/create_opening/ -H "Content-Type: application/json" -d '{"token":"TOKEN","host_id":1234,"points":[{"x":1000,"y":0,"z":900},{"x":2200,"y":0,"z":2100}]}'
curl -X POST $R/create_opening/ -H "Content-Type: application/json" -d '{"token":"TOKEN","host_id":5678,"points":[{"x":1000,"y":1000,"z":0},{"x":2000,"y":1000,"z":0},{"x":2000,"y":2000,"z":0},{"x":1000,"y":2000,"z":0}]}'
# Toposólido desde un CSV de Civil 3D (P,N,E,Z en metros)
curl -X POST $R/create_toposolid/ -H "Content-Type: application/json" -d '{"token":"TOKEN","csv_path":"C:\\\\Proyectos\\\\puntos.csv","level_name":"Terreno","units":"m","simular":true}'
# Habitación y separación de habitaciones
curl -X POST $R/create_room/ -H "Content-Type: application/json" -d '{"token":"TOKEN","level_name":"Nivel 1","location":{"x":2500,"y":2000},"name":"Salon","number":"101"}'
curl -X POST $R/create_room_separation/ -H "Content-Type: application/json" -d '{"token":"TOKEN","lines":[{"start_point":{"x":0,"y":2000,"z":0},"end_point":{"x":5000,"y":2000,"z":0}}]}'
# Línea de detalle en la vista activa
curl -X POST $R/create_detail_line/ -H "Content-Type: application/json" -d '{"token":"TOKEN","start_point":{"x":0,"y":0,"z":0},"end_point":{"x":3000,"y":0,"z":0}}'
# Conducto, tubería y sistema MEP
curl -X POST $R/create_duct/ -H "Content-Type: application/json" -d '{"token":"TOKEN","start_point":{"x":0,"y":0,"z":2800},"end_point":{"x":6000,"y":0,"z":2800},"width":400,"height":250}'
curl -X POST $R/create_pipe/ -H "Content-Type: application/json" -d '{"token":"TOKEN","start_point":{"x":0,"y":500,"z":2600},"end_point":{"x":6000,"y":500,"z":2600},"diameter":50}'
curl -X POST $R/create_mep_system/ -H "Content-Type: application/json" -d '{"token":"TOKEN","system_type":"mechanical","system_name":"Impulsion P1","element_ids":[4001,4002]}'
# Vista de planta y vista activa
curl -X POST $R/create_view/ -H "Content-Type: application/json" -d '{"token":"TOKEN","view_type":"floor_plan","name":"P1 - Estructura","level_name":"Nivel 1"}'
curl -X POST $R/set_active_view/ -H "Content-Type: application/json" -d '{"token":"TOKEN","view_name":"P1 - Estructura"}'
# Colocar y cargar familias
curl -X POST $R/place_family/ -H "Content-Type: application/json" -d '{"token":"TOKEN","family_name":"Puerta simple","type_name":"0915 x 2134","location":{"x":2500,"y":0,"z":0},"level_name":"Nivel 1"}'
curl -X POST $R/load_family/ -H "Content-Type: application/json" -d '{"token":"TOKEN","file_path":"C:\\\\Familias\\\\Pilar HEB.rfa"}'
# Parámetros de ejemplar y de tipo, cambio de tipo
curl -X POST $R/set_parameter/ -H "Content-Type: application/json" -d '{"token":"TOKEN","element_id":1234,"parameter_name":"Comments","value":"Revisado"}'
curl -X POST $R/modify_element/ -H "Content-Type: application/json" -d '{"token":"TOKEN","element_id":1234,"parameters":{"Mark":"M-01","Comments":"Revisado"}}'
curl -X POST $R/set_type_parameter/ -H "Content-Type: application/json" -d '{"token":"TOKEN","element_id":1234,"parameter_name":"Fire Rating","value":"EI 60","simular":true}'
curl -X POST $R/change_type/ -H "Content-Type: application/json" -d '{"token":"TOKEN","element_ids":[1234,1235],"type_name":"Basic Wall: Generic - 300mm"}'
# Borrar, transformar
curl -X POST $R/delete_elements/ -H "Content-Type: application/json" -d '{"token":"TOKEN","element_ids":[1234],"simular":true}'
curl -X POST $R/transform_elements/ -H "Content-Type: application/json" -d '{"token":"TOKEN","element_ids":[1234],"operation":"move","vector":{"x":0,"y":500,"z":0}}'
# Subproyectos, unir geometría, coordenadas
curl -X POST $R/set_workset/ -H "Content-Type: application/json" -d '{"token":"TOKEN","element_ids":[1234],"workset_name":"Estructura"}'
curl -X POST $R/join_geometry/ -H "Content-Type: application/json" -d '{"token":"TOKEN","element_id_a":1234,"element_id_b":5678}'
curl -X POST $R/set_project_location/ -H "Content-Type: application/json" -d '{"token":"TOKEN","true_north_deg":12.5,"simular":true}'
curl -X POST $R/set_project_location/ -H "Content-Type: application/json" -d '{"token":"TOKEN","acquire_from_link_id":9001}'
# Colores
curl -X POST $R/color_splash/ -H "Content-Type: application/json" -d '{"token":"TOKEN","category_name":"Walls","parameter_name":"Mark"}'
curl -X POST $R/clear_colors/ -H "Content-Type: application/json" -d '{"token":"TOKEN","category_name":"Walls"}'
# Anotación y documentación
curl -X POST $R/create_dimensions/ -H "Content-Type: application/json" -d '{"token":"TOKEN","element_ids":[1234,1235]}'
curl -X POST $R/tag_walls/ -H "Content-Type: application/json" -d '{"token":"TOKEN","use_leader":false}'
curl -X POST $R/tag_elements/ -H "Content-Type: application/json" -d '{"token":"TOKEN","element_ids":[1234],"add_leader":true}'
curl -X POST $R/create_sheet/ -H "Content-Type: application/json" -d '{"token":"TOKEN","sheet_number":"E-101","sheet_name":"Planta cimentacion"}'
curl -X POST $R/create_schedule/ -H "Content-Type: application/json" -d '{"token":"TOKEN","category":"OST_Walls","fields":["Type","Length","Area"],"schedule_name":"Muros"}'
# Vínculo, guardado, purga, copia
curl -X POST $R/link_file/ -H "Content-Type: application/json" -d '{"token":"TOKEN","file_path":"C:\\\\Proyectos\\\\topografia.dwg","mode":"link"}'
curl -X POST $R/save_document/ -H "Content-Type: application/json" -d '{"token":"TOKEN"}'
curl -X POST $R/purge_unused/ -H "Content-Type: application/json" -d '{"token":"TOKEN","simular":true}'
curl -X POST $R/backup/ -H "Content-Type: application/json" -d '{"token":"TOKEN","suffix":"antes_de_purgar"}'
# Código (description obligatoria)
curl -X POST $R/execute_code/ -H "Content-Type: application/json" -d '{"token":"TOKEN","description":"Contar muros","code":"print(len(list(DB.FilteredElementCollector(doc).OfCategory(DB.BuiltInCategory.OST_Walls).WhereElementIsNotElementType())))"}'
# 0.3.0: rejilla 3x2 (1, 2, 3 / A, B) y dos niveles en una transacción; primero con simular
curl -X POST $R/grid_levels/ -H "Content-Type: application/json" -d '{"token":"TOKEN","x_spacings_mm":[6000,6000],"y_spacings_mm":[5000],"levels":[{"name":"Nivel 1","elevation_mm":0},{"name":"Nivel 2","elevation_mm":3500}],"origin_mm":{"x":0,"y":0,"z":0},"simular":true}'
# 0.3.0: dos planos con vistas colocadas (posición en mm sobre el plano)
curl -X POST $R/sheet_set/ -H "Content-Type: application/json" -d '{"token":"TOKEN","sheets":[{"number":"E-101","name":"Planta cimentación","title_block":"A1 métrico","views":[{"view_name":"Cimentación","position_mm":{"x":420,"y":297}}]},{"number":"E-102","name":"Tablas","views":["Tabla de pilares"]}]}'
# 0.3.0: topografía desde LandXML o CSV, y DWG con coordenadas compartidas
curl -X POST $R/import_civil/ -H "Content-Type: application/json" -d '{"token":"TOKEN","file_path":"C:\\Proyectos\\terreno.xml","level":"Terreno","simular":true}'
curl -X POST $R/import_civil/ -H "Content-Type: application/json" -d '{"token":"TOKEN","file_path":"C:\\Proyectos\\topografia.dwg","level":"Terreno","use_shared_coordinates":true,"placement":"center"}'
# 0.3.0: instantáneas (escriben snapshots\<name>.json, no tocan el modelo) y consulta con filtros
curl -X POST $R/snapshot/ -H "Content-Type: application/json" -d '{"token":"TOKEN","name":"antes de estructura","overwrite":true}'
curl -X POST $R/diff_snapshots/ -H "Content-Type: application/json" -d '{"token":"TOKEN","a":"antes de estructura"}'
curl -X POST $R/query/ -H "Content-Type: application/json" -d '{"token":"TOKEN","category":"OST_Walls","filters":[{"parameter":"Mark","op":"contains","value":"M-"},{"parameter":"Length","op":">","value":4000}],"sort_by":"-Length","page_size":50,"fields":["Mark","Length"]}'
# 0.4.0: 20 comentarios y 20 marcas en UNA transaccion (Comments/Mark por alias ingles; mm en longitudes)
curl -X POST $R/set_parameters/ -H "Content-Type: application/json" -d '{"token":"TOKEN","changes":[{"element_ids":[1234,1235,1236],"parameters":{"Comments":"Revisado","Unconnected Height":3500}},{"element_id":1234,"parameters":{"Mark":"M-01"}}],"simular":true}'
curl -X POST $R/set_parameters/ -H "Content-Type: application/json" -d '{"token":"TOKEN","changes":[{"element_ids":[1234,1235],"parameters":{"Type Comments":"Tipo revisado"}}],"type_parameters":true}'
# 0.4.0: nivel + rejilla + muro + pilar de kinds mezclados en UNA transaccion (todo validado antes; un fallo revierte todo)
curl -X POST $R/create_elements/ -H "Content-Type: application/json" -d '{"token":"TOKEN","elements":[{"kind":"level","name":"Nivel 3","elevation_mm":6400},{"kind":"grid","name":"D","start_point":{"x":18000,"y":-2000,"z":0},"end_point":{"x":18000,"y":20000,"z":0}},{"kind":"wall","start_point":{"x":0,"y":0,"z":0},"end_point":{"x":5000,"y":0,"z":0},"level_name":"Nivel 1","height":3000,"type_name":"Genérico - 200 mm"},{"kind":"column","point":{"x":0,"y":0,"z":0},"base_level":"Nivel 1","top_level":"Nivel 2","type_name":"HEB300"}],"simular":true}'
# 0.4.0: macro del usuario (catalogo y ejecucion con plan)
curl "$R/macros/?token=TOKEN"
curl -X POST $R/macros/run/ -H "Content-Type: application/json" -d '{"token":"TOKEN","name":"comentarios_por_nivel","args":{"category":"OST_Walls","level":"Nivel 1"},"simular":true}'
curl -X POST $R/macros/run/ -H "Content-Type: application/json" -d '{"token":"TOKEN","name":"numerar_planos","args":{"prefix":"E-","start":1,"step":1,"digits":2}}'
# 0.4.0: matriz de 4 copias y snapshot con timings sin bbox
curl -X POST $R/transform_elements/ -H "Content-Type: application/json" -d '{"token":"TOKEN","element_ids":[1234],"operation":"array","vector":{"x":3000,"y":0,"z":0},"count":4}'
curl -X POST $R/snapshot/ -H "Content-Type: application/json" -d '{"token":"TOKEN","name":"rapido","include_bbox":false,"overwrite":true}'
```

### Add-ins en C# y comandos de pyRevit existentes

Un comando ya instalado (add-in de C# con `IExternalCommand` o un botón de
pyRevit) se puede envolver en una macro del usuario para que el agente lo
lance por su nombre:

```python
# macro.py de la macro "lanzar_mi_comando" (writes: false, args: {})
from Autodesk.Revit.UI import RevitCommandId

def run(doc, uidoc, args, api):
    comando = RevitCommandId.LookupCommandId("MiEmpresa.MiAddin.MiComando")  # id del add-in
    __revit__.PostCommand(comando)
    api.log(u"comando enviado a Revit; se ejecuta al terminar esta peticion")
    return {"posted": True}
```

Limitaciones que hay que asumir: `PostCommand` no admite argumentos ni
transacción propia (el comando corre después, con su propia transacción, y el
servidor no puede verificar lo que hace ni deshacerlo como `IA: ...`); por
eso la macro debe ir con `writes: false` y solo vale para comandos sin
parámetros. Un comando que necesita argumentos se reescribe como macro Python
con `run(doc, uidoc, args, api)` y `writes: true`.

## Pruebas

Con Revit abierto (extensión cargada y Routes activo), un modelo de prueba
guardado y el puente en marcha:

```bat
cd <carpeta del repositorio>
.venv\Scripts\python main.py --combined
```

En otra consola:

```bat
python pruebas\probar_revit.py [--element-id ID] [--parameter Comments]
```

El script ejecuta y muestra literalmente (nombre, código de estado y cuerpo):

| Prueba | Petición | Esperado |
|--------|----------|----------|
| 1 | `GET .../status/` sin token | `401` `{"error": "token ausente o incorrecto"}` |
| 2 | `GET .../status/?token=<token>` | `200` con `"status": "active"` |
| 3 | `POST .../execute_code/` con `{"code": "print(\"hola\")", "description": "prueba", "token": ...}` | `200` con `"output": "hola\n"` y `"undo_name": "IA: prueba"` |
| 4 | `POST http://127.0.0.1:8000/mcp` con cabecera `Host: evil.com` | `421 Invalid Host header` |
| 5 | Las 18 rutas de lectura | `200` en menos de 5 s cada una |
| 6 | `POST .../set_parameter/` con `simular: true` | `200` con `simulado: true`, sin `copia`; el valor no cambia |
| 7 | `POST .../set_parameter/` real | `200` con `ok: true`, `antes`/`despues`; línea nueva en `mcp_log.jsonl`; copia en `backups\` si el modelo no es compartido; el valor original se restaura |
| 8 | `POST .../execute_code/` sin `description` | `400` |
| 9 | `doc.GetUndoName()` vía `execute_code` | empieza por `IA:` (si la API no existe, comprobación manual del desplegable Deshacer) |

Termina con `Resultado: 9/9 pruebas correctas` y código de salida 0.

Con `--fase 2a` (0.3.0) se añaden cuatro pruebas más, ninguna dependiente de
nombres visibles en inglés (`Mark` se resuelve por `BuiltInParameter`):

| Prueba | Petición | Esperado |
|--------|----------|----------|
| 2a.1 | `POST .../describe/` del primer muro con `depth: 1` | `200` con `parameters.instance` no vacío, `bbox_mm` y `hosted_elements` (lista) |
| 2a.2 | Marcas temporales `MCP-2A-1` y `MCP-2A-2` en dos muros; `POST .../query/` con `filters: [{"parameter": "Mark", "op": "contains", "value": "MCP-2A"}]`, `page_size: 1` | Páginas disjuntas de un elemento, `pages` = `total_matched` ≥ 2; después se restauran las marcas |
| 2a.3 | `POST .../snapshot/` (`prueba2a_antes`), `POST .../create_level/`, `POST .../snapshot/` (`prueba2a_despues`), `POST .../diff_snapshots/` | `added` contiene exactamente el nivel creado y `removed` está vacío; el nivel se borra y los dos `.json` se eliminan |
| 2a.4 | `POST .../grid_levels/` con `simular: true` | `200` con `simulado: true`, `plan.counts.total: 6`, sin `copia`; el número de rejillas y niveles no cambia y la última entrada de `/grid_levels/` en el log es simulada |

Termina con `Resultado: 13/13 pruebas correctas`.

Con `--fase cons` (0.4.0) se añaden las pruebas de la consolidación; el script
copia las macros de ejemplo a `%LOCALAPPDATA%\RevitMcp\macros` (o
`REVIT_MCP_MACROS`) si no están:

| Prueba | Petición | Esperado |
|--------|----------|----------|
| cons.1 | 20 `POST .../set_parameter/` frente a un `POST .../set_parameters/` con los mismos 20 muros | `200`, `ok: true`, `parameters_set: 20`, `fallidos: []`; se imprimen los `ms` de Revit y de pared de ambos; los comentarios se restauran con otro lote |
| cons.2 | `POST .../create_elements/` con `level` + `grid` + `wall` y `simular: true` | `simulado: true`, `plan.total: 3`, `plan.counts {level, grid, wall}`, sin `copia`; el número de rejillas no cambia |
| cons.3 | `GET .../macros/`, `POST .../macros/run/` `comentarios_por_nivel` (`level` = nivel más bajo) con `simular` y real | catálogo con las dos macros de ejemplo; `plan.count`; `ok: true`, `writes: true`, `despues` con `plan.count` elementos y `copia`; se restauran con `set_parameters` |
| cons.4 | `POST .../snapshot/` `prueba_cons` | `timings` con `recoleccion_ms`, `descripcion_ms`, `bbox_ms`, `parametros_ms`, `hash_ms`, `escritura_ms`, `total_ms`; se imprime si baja de 3 s |
| cons.5 | `tools/call` `set_parameter` al puente `http://127.0.0.1:8000/mcp` | resultado `isError` con el texto `set_parameters(...)` |

Termina con `Resultado: 15/15 pruebas correctas` (14/14 si `plan.count` de la
macro supera 200 y solo se simula).

Sin Revit, en CPython: `uv run pytest` ejecuta las pruebas de
`tests/` (formato JSON de `format_response`, gestor `transaccion` simulado,
rotación del log, rutas de escritura contra un `pyrevit` simulado, lector CSV,
guarda de compatibilidad IronPython 2.7 y, desde 0.3.0, una prueba de extremo
a extremo por ruta de navegación, instantáneas y macros sobre el modelo
simulado de `tests/fakes/modelo_falso.py`: `test_navegacion.py`,
`test_instantaneas.py` y `test_macros.py`; desde 0.4.0, `test_herramientas.py`
(las 40 herramientas registradas en un `MCPServer` real, ninguna retirada, un
nombre retirado responde con su sustituta, despacho de cada herramienta
combinada), `test_lotes.py` (`/set_parameters/`, `/create_elements/`, `array`),
`test_macros_usuario.py` (`/macros/`, `/macros/run/`, macros de ejemplo,
recarga por `mtime`) y `test_rendimiento.py` (copia diferida y `timings`)).

## Deshacer

Cada llamada a `/execute_code/` (herramienta MCP `execute_revit_code`) se
ejecuta dentro de un `DB.TransactionGroup` llamado **`IA: <description>`**
(`description` es obligatoria y se recorta a 60 caracteres).

- Si el código termina bien, el grupo se asimila (`Assimilate`) y toda la
  orden aparece en Revit como **una sola entrada de deshacer** (Ctrl+Z la
  revierte entera y el historial muestra `IA: <descripción>`).
- Si el código lanza una excepción, se revierten las `Transaction` que el
  código dejó abiertas y la transacción interna, y después el grupo
  (`RollBack`): el modelo queda como estaba.
- Si tras eso `doc.IsModifiable` sigue siendo `True`, quedó una `Transaction`
  abierta que no se pudo cerrar: el grupo **no** se revierte y la respuesta lo
  indica (`"open_transaction": true`) para que el usuario la revise en Revit.
- Sin documento activo el código se ejecuta sin grupo ni transacción.

Las demás rutas que modifican el modelo usan `escritura.transaccion`, cuyo
nombre siempre empieza por `IA:` (por ejemplo `IA: Crear rejillas`,
`IA: Borrar elementos`, `IA: Parametro Mark de 1234`).
