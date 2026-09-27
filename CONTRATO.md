# Contrato del conector Revit MCP

Este documento describe el servidor HTTP que la extensión de pyRevit levanta
dentro de Revit, cómo se protege, cómo escribe en el modelo y qué rutas
expone. Es la referencia para el puente `main.py` y para cualquier cliente que
quiera hablar con Revit directamente. Versión del conector: **0.2.0**.

## El servidor

| Elemento | Valor |
|----------|-------|
| Tecnología | pyRevit Routes (servidor HTTP dentro del proceso de Revit) |
| Puerto | **48884** (por defecto de pyRevit Routes) |
| Prefijo de las rutas | `http://127.0.0.1:48884/revit_mcp/...` |
| Motor | IronPython 2.7 (los manejadores viven en `revit_mcp/`) |
| Puente MCP | `main.py` (CPython 3.11+, SDK mcp 2.2) en `http://127.0.0.1:8000` |
| Herramientas MCP | 68 (40 de escritura, todas con `simular`) |

El servidor debe usarse **solo desde loopback**. pyRevit Routes escucha en
todas las interfaces, así que el usuario aplica a mano esta regla de firewall
(consola de administrador) para bloquear cualquier conexión entrante al
puerto 48884 desde otras máquinas:

```bat
netsh advfirewall firewall add rule name="Block pyRevit Routes" dir=in action=block protocol=TCP localport=48884 profile=any
```

La regla bloquea el tráfico entrante desde la red; las conexiones locales
(`127.0.0.1`) del puente y de las pruebas siguen funcionando.

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
Las garantías son las mismas para las 40 herramientas de escritura:

| Garantía | Detalle |
|----------|---------|
| **Comprobación previa** | `preparar(doc, ruta)` responde `409` si `doc.IsModifiable` es `True` (hay una transacción abierta de otra operación; cuerpo con `"open_transaction": true`) o si `doc.IsReadOnly`. Sin documento activo: `503`. |
| **Copia de seguridad** | Si el documento está guardado en disco y **no** es de trabajo compartido, antes de escribir se copia el `.rvt` con `System.IO.File.Copy` a `<carpeta del rvt>\backups\<nombre>_<yyyyMMdd_HHmmss>.rvt`. No se repite si ya hay una copia de hace menos de 30 minutos (`"reutilizada": true`) y se conservan las últimas 10. La copia refleja el **último guardado**, no el estado en memoria; la respuesta lo dice: `"copia": {"ruta", "refleja_guardado_de", "reutilizada", "nota"}`. El documento **nunca** se guarda por su cuenta. En modelos de trabajo compartido no se copia nada (`"copia": null`, `"nota_copia"` explica que se confía en las copias del central). |
| **Registro** | `registrar()` añade una línea JSON a `<carpeta del rvt>\mcp_log.jsonl` (o `%LOCALAPPDATA%\RevitMcp\mcp_log.jsonl` si el documento no está guardado) con `fecha`, `documento`, `ruta`, `ok`, `ms`, `args` (en `execute_code` el código completo), `error`, `resultado` y `simulado`. Rota a 5 MB (`mcp_log.jsonl.1`). Se lee con `GET /log/`. |
| **`simular`** | Parámetro booleano (por defecto `false`) en **todas** las rutas de escritura. Con `true` el manejador valida todo (elementos, tipos, niveles, unidades convertidas) y responde `{"simulado": true, "haria": [...]}` sin abrir transacción ni hacer copia. La llamada queda en el log con `"simulado": true`. |
| **Transacción `IA:`** | `transaccion(doc, nombre)` abre `DB.Transaction(doc, "IA: <acción>")` con `suppress_warnings`, hace `Commit` al salir y `RollBack` ante excepción. Si Revit revierte por un fallo de validación, la ruta responde `500` en vez de un éxito falso. Todas las transacciones del conector se llaman `IA: ...` (`IA: Crear muros/vigas`, `IA: Borrar elementos`, `IA: Parametro Mark de 1234`...), así el usuario distingue en el historial de deshacer lo que hizo la IA. `execute_code` usa un `TransactionGroup` con el mismo prefijo. |
| **Verificación** | Cada manejador vuelve a leer lo que cambió: creación → `"creados": [{"id", "categoria", "tipo", "nivel", "bbox_mm"}]`; parámetros → `"antes"` / `"despues"`; borrado → `"eliminados"` / `"en_cascada"`. La respuesta lleva `"ok"` y `"verificacion": {"coincide": bool, "detalle"}`. Si lo releído no coincide con lo pedido, `ok=false` con explicación y **nunca** se reintenta solo. |
| **Avisos de Revit** | El preprocesador de fallos borra los avisos (para que Revit no abra diálogos) pero antes los guarda; toda ruta de escritura devuelve `"avisos_revit": [{"texto", "elementos"}]` si Revit avisó de algo al confirmar. Los errores de Revit revierten la transacción y responden `500` con el motivo. Si un elemento creado ya no existe tras el commit, `verificacion.detalle` cita esos avisos (Revit resuelve avisos como `Rectangular opening doesn't cut its host` borrando el elemento, con la transacción confirmada). |
| **Límite de alcance** | `delete_elements`, `transform_elements`, `change_type` y `set_workset` rechazan (`400`, con `limite` y `cantidad`) más de 200 elementos por llamada salvo `forzar=true`. `execute_code` exige `description` (`400` si falta) y rechaza código con `doc.Delete(<colección>)` salvo `forzar=true`. |
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
`purge_unused`, `create_backup`.

### Estado y modelo (lectura)

| Método | Ruta | Parámetros | Respuesta |
|--------|------|------------|-----------|
| GET | `/status/` | — | `{"status": "active", "health": "healthy", "revit_available": true, "document_title": ..., "api_name": "revit_mcp"}`; `503` sin documento |
| GET | `/model_info/` | — | Proyecto, recuentos, avisos, vistas, planos, habitaciones, vínculos y bloque `file`: `is_workshared`, `path`, `last_saved`, `units`, `project_base_point_mm`, `survey_point_mm`, `true_north_deg` |
| GET | `/model_statistics/` | — | Estadísticas del modelo (elementos, categorías) |
| GET | `/room_data/` | — | Habitaciones con nivel, área y parámetros |
| GET | `/selected_elements/` | — | Elementos seleccionados en Revit |
| GET | `/list_levels/` | — | Niveles con elevación |
| GET | `/element_properties/<element_id>` | `element_id` en la ruta | Propiedades y parámetros (`is_type_parameter` y, en los integrados, `builtin` = nombre del `BuiltInParameter`, igual en todos los idiomas) más `bbox_mm`, `location_mm` (`start`/`end`/`longitud_mm` de la línea de ubicación o `point`/`rotation_deg`), `level`, `workset`, `phase_created`, `phase_demolished`, `design_option`, `host_id`, `pinned`, `type_id` |
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
| POST | `/create_level/` | `levels`: (nombre, elevación mm), `simular` | `creados[]` con `elevation_mm` verificada |
| POST | `/create_grid/` | `grids`: (nombre, inicio, fin mm), `simular` | `creados[]` con `name` |
| POST | `/create_framing/` | `elements`: vigas (puntos mm, tipo, nivel), `simular` | `creados[]` |
| POST | `/create_column/` | `columns[]`: `point` (mm), `base_level`*, `top_level`, `top_offset`, `type_name`, `rotation`, `simular` | `creados[]` con `top_level` y `point_mm` |
| POST | `/create_foundation/` | `foundations[]`: `point`+`level`+`type_name` (zapata aislada) / `wall_id` o `curve`+`type_name` (`WallFoundation`) / `boundary`+`level`+`type_name` (losa), `simular` | `creados[]` |
| POST | `/create_opening/` | `host_id`*, `points[]` (mm absolutos; en muro exactamente 2 esquinas opuestas sobre la línea de ubicación del muro, con `z` absoluta como `bbox_mm`; polígono en suelo/cubierta), `simular` | `creados[]`, `host`, `en_muro` (posición del rectángulo a lo largo del muro y en z, en mm) y `rectangulo_revit_mm`. `400` si las esquinas están fuera del plano del muro, el rectángulo es degenerado o queda fuera de su longitud o altura (Revit lo crearía y lo borraría al confirmar) |
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
| POST | `/set_parameter/` | `element_id`, `parameter_name` (nombre en el idioma de Revit, nombre de `BuiltInParameter` o alias inglés: `Comments`, `Mark`, `Description`, `Unconnected Height`, `Base Offset`, `Level`, `Phase Created`...), `value` (longitudes en mm, áreas mm², volúmenes mm³, ángulos en grados; el resto tal cual), `simular` | `antes`/`despues` (como los muestra Revit), `antes_valor`/`despues_valor` + `unidad` en los numéricos, `parameter_name_revit`, `is_type_parameter` |
| POST | `/set_type_parameter/` | `type_id` o `element_id`, `parameter_name` (mismos nombres y alias que `set_parameter`), `value` (mismas unidades), `simular` | `antes`/`despues`, `parameter_name_revit`, `afecta_ejemplares` |
| POST | `/change_type/` | `element_ids`, `type_name` (o `type_id`), `simular`, `forzar` | `antes`/`despues` (tipo) por elemento |
| POST | `/delete_elements/` | `element_ids`, `simular`, `forzar` | `eliminados[]`, `en_cascada[]`, `antes[]` (descripción previa) |
| POST | `/transform_elements/` | `element_ids`, `operation` (move/rotate/mirror/copy), `vector`, `axis_point`, `angle`, `mirror_plane`, `simular`, `forzar` | `antes`/`despues` (bbox), `creados[]` en copy |
| POST | `/set_workset/` | `element_ids`, `workset_name`, `simular`, `forzar` | `antes`/`despues` (subproyecto) |
| POST | `/join_geometry/` | `element_id_a`, `element_id_b`, `unjoin`, `simular` | `antes`/`despues` (`joined`) |
| POST | `/set_project_location/` | `base_point_mm`, `survey_point_mm`, `true_north_deg`, `acquire_from_link_id` (solo, sin los otros), `forzar`, `simular` | `antes`/`despues` (ubicación completa). `409` si el punto está anclado o recortado y no se pasa `forzar=true` (mover un punto recortado cambia las coordenadas compartidas de todo el modelo) |
| POST | `/color_splash/` | `category_name`, `parameter_name`, `use_gradient`, `custom_colors`, `simular` | Elementos coloreados, `verificacion` (muestra) |
| POST | `/clear_colors/` | `category_name`, `simular` | Colores restablecidos |
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
```

## Pruebas

Con Revit abierto (extensión cargada y Routes activo), un modelo de prueba
guardado y el puente en marcha:

```bat
cd <carpeta del repositorio>
.venv\Scripts\python main.py --combined
```

En otra consola:

```bat
python pruebas\probar_revit.py [--element-id ID] [--parameter NOMBRE]
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

Sin Revit, en CPython: `uv run pytest` ejecuta las pruebas de
`tests/` (formato JSON de `format_response`, gestor `transaccion` simulado,
rotación del log, rutas de escritura contra un `pyrevit` simulado, lector CSV
y guarda de compatibilidad IronPython 2.7).

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
