# Contrato del conector Revit MCP

Este documento describe el servidor HTTP que la extensión de pyRevit levanta
dentro de Revit, cómo se protege y qué rutas expone. Es la referencia para el
puente `main.py` y para cualquier cliente que quiera hablar con Revit
directamente.

## El servidor

| Elemento | Valor |
|----------|-------|
| Tecnología | pyRevit Routes (servidor HTTP dentro del proceso de Revit) |
| Puerto | **48884** (por defecto de pyRevit Routes) |
| Prefijo de las rutas | `http://127.0.0.1:48884/revit_mcp/...` |
| Motor | IronPython 2.7 (los manejadores viven en `revit_mcp/`) |
| Puente MCP | `main.py` (CPython 3.11+, SDK mcp 2.2) en `http://127.0.0.1:8000` |

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

## Rutas

Todas las rutas cuelgan de `/revit_mcp`. Los POST reciben JSON
(`Content-Type: application/json`) y **todos** llevan además la clave `token`
en el cuerpo; los GET llevan `?token=`. Salvo que se indique otra cosa, la
respuesta es JSON: en éxito un objeto con los datos (a menudo con
`"status": "success"` o `"message"`), y en error `{"error": "..."}` con
estado `400` (datos incorrectos), `404` (no encontrado), `503` (sin documento
activo) o `500` (excepción). Sin token: `401`.

### Estado y modelo

| Método | Ruta | Parámetros | Respuesta |
|--------|------|------------|-----------|
| GET | `/status/` | — | `{"status": "active", "health": "healthy", "revit_available": true, "document_title": ..., "api_name": "revit_mcp"}`; `503` sin documento |
| GET | `/model_info/` | — | Información del proyecto, recuentos por categoría, avisos, vistas, planos, habitaciones, vínculos |
| GET | `/model_statistics/` | — | Estadísticas del modelo (elementos, categorías, avisos) |
| GET | `/room_data/` | — | Lista de habitaciones con nivel, área y parámetros |
| GET | `/selected_elements/` | — | Elementos seleccionados en Revit |
| GET | `/list_levels/` | — | Niveles con elevación |
| GET | `/element_properties/<element_id>` | `element_id` en la ruta | Propiedades y parámetros del elemento |

### Vistas

| Método | Ruta | Parámetros | Respuesta |
|--------|------|------------|-----------|
| GET | `/list_views/` | — | Vistas y planos del modelo |
| GET | `/current_view_info/` | — | Datos de la vista activa |
| GET | `/current_view_elements/` | — | Elementos visibles en la vista activa |
| GET | `/get_view/<view_name>` | `view_name` en la ruta (URL-encoded) | `{"image_data": <PNG en base64>, "content_type": "image/png"}` |
| POST | `/create_view/` | `view_type`, `name`, `level_name`, `section_box` | Vista creada |
| POST | `/set_active_view/` | `view_name` | Confirmación |

### Familias y colocación

| Método | Ruta | Parámetros | Respuesta |
|--------|------|------------|-----------|
| GET | `/list_families/` | `contains`, `limit` (query; el puente los envía, el manejador hoy los ignora) | Hasta 50 tipos de familia |
| GET | `/list_family_categories/` | — | Categorías de familia |
| POST | `/place_family/` | `family_name`, `type_name`, `location` (mm), `rotation`, `level_name`, `properties` | Instancia colocada |
| POST | `/load_family/` | `file_path` (.rfa) | Familia cargada |

### Creación de elementos

| Método | Ruta | Parámetros | Respuesta |
|--------|------|------------|-----------|
| POST | `/create_line/` | `elements`: lista de muros/vigas (puntos en mm, tipo, nivel) | Elementos creados y errores por elemento |
| POST | `/create_surface/` | `elements`: lista de suelos/cubiertas (contornos en mm, tipo, nivel) | Elementos creados y errores por elemento |
| POST | `/create_level/` | `levels`: lista de niveles (nombre, elevación) | Niveles creados |
| POST | `/create_grid/` | `grids`: lista de rejillas (nombre, inicio, fin) | Rejillas creadas |
| POST | `/create_framing/` | `elements`: lista de vigas/pilares estructurales | Elementos creados |
| POST | `/create_room/` | `level_name`, `location`, `name`, `number` | Habitación creada |
| POST | `/create_room_separation/` | `lines`, `view_name`, `level_name` | Líneas de separación creadas |
| POST | `/create_detail_line/` | `start_point`, `end_point`, `view_name`, `line_style` | Línea de detalle creada |
| POST | `/create_duct/` | `start_point`, `end_point`, `duct_type`, `system_type`, `level_name`, `diameter` o `width`+`height` | Conducto creado |
| POST | `/create_pipe/` | `start_point`, `end_point`, `pipe_type`, `system_type`, `level_name`, `diameter` | Tubería creada |
| POST | `/create_mep_system/` | `system_type`, `system_name`, `element_ids` | Sistema MEP creado |

### Modificación

| Método | Ruta | Parámetros | Respuesta |
|--------|------|------------|-----------|
| POST | `/modify_element/` | `element_id`, `parameters` (dict nombre → valor) | Parámetros modificados |
| POST | `/set_parameter/` | `element_id`, `parameter_name`, `value` | Parámetro modificado |
| POST | `/delete_elements/` | `element_ids` | Ids eliminados |
| POST | `/transform_elements/` | `element_ids`, `operation` (move/rotate/mirror/copy), `vector`, `axis_point`, `angle`, `mirror_plane` | Resultado por elemento |
| POST | `/color_splash/` | `category_name`, `parameter_name`, `use_gradient`, `custom_colors` | Elementos coloreados |
| POST | `/clear_colors/` | `category_name` | Colores restablecidos |
| POST | `/list_category_parameters/` | `category_name` | Parámetros de la categoría |

### Anotación y documentación

| Método | Ruta | Parámetros | Respuesta |
|--------|------|------------|-----------|
| POST | `/create_dimensions/` | `element_ids`, `dimension_type` | Cotas creadas |
| POST | `/tag_walls/` | `use_leader`, `tag_type_name` (opcionales) | Muros etiquetados |
| POST | `/tag_elements/` | `element_ids`, `view_name`, `add_leader`, `orientation`, `offset`, `tag_type_name` | Etiquetas creadas |
| POST | `/create_sheet/` | `sheet_number`, `sheet_name`, `title_block_name` | Plano creado |
| POST | `/create_schedule/` | `category`, `fields`, `schedule_name` | Tabla de planificación creada |
| POST | `/export_document/` | `view_name`, `format` (pdf/png/jpg/dwg), `resolution` | Ruta del archivo exportado |

### Análisis, interoperabilidad y documento

| Método | Ruta | Parámetros | Respuesta |
|--------|------|------------|-----------|
| POST | `/ai_filter/` | `category`, `type_name`, `visible_in_view`, `bounding_box_min`, `bounding_box_max`, `max_elements` | Elementos filtrados |
| POST | `/material_quantities/` | `categories` | Cantidades de material |
| POST | `/clash_check/` | `set_a_categories`, `set_b_categories`, `max_clashes` | Interferencias detectadas |
| POST | `/export_ifc/` | `file_path`, `ifc_version`, `export_base_quantities`, `view_name` | Ruta del IFC |
| POST | `/link_file/` | `file_path`, `mode`, `position` | Vínculo creado |
| POST | `/save_document/` | `file_path`, `overwrite` | Documento guardado |

### Ejecución de código

| Método | Ruta | Parámetros | Respuesta |
|--------|------|------------|-----------|
| POST | `/execute_code/` | `code` (IronPython 2.7), `description` (pocas palabras; opcional) | Éxito: `{"status": "success", "description", "undo_name": "IA: ...", "output", "code_executed"}`. Error (`500`): `{"status": "error", "error", "error_type", "traceback", "code_attempted", "undo_name", "partial_output"?, "hints"?, "open_transaction"?}`. `400` si falta `code`. |

## Pruebas

Con Revit abierto (extensión cargada y Routes activo) y el puente en marcha:

```bat
cd <carpeta del repositorio>
.venv\Scripts\python main.py --combined
```

En otra consola:

```bat
python pruebas\probar_revit.py
```

El script ejecuta y muestra literalmente (nombre, código de estado y cuerpo):

| Prueba | Petición | Esperado |
|--------|----------|----------|
| 1 | `GET http://127.0.0.1:48884/revit_mcp/status/` sin token | `401` `{"error": "token ausente o incorrecto"}` |
| 2 | `GET .../status/?token=<token del archivo>` | `200` con `"status": "active"` |
| 3 | `POST .../execute_code/` con `{"code": "print(\"hola\")", "description": "prueba", "token": ...}` | `200` con `"output": "hola\n"` y `"undo_name": "IA: prueba"` |
| 4 | `POST http://127.0.0.1:8000/mcp` con cabecera `Host: evil.com` | `421 Invalid Host header` |

Termina con `Resultado: 4/4 pruebas correctas` y código de salida 0.

## Deshacer

Cada llamada a `/execute_code/` (herramienta MCP `execute_revit_code`) se
ejecuta dentro de un `DB.TransactionGroup` llamado **`IA: <descripción>`**,
donde `<descripción>` es, por este orden: el campo `description` de la
petición; si está vacío, la primera línea del código cuando es un comentario
`#` (sin el `#`); si no, `Code execution`. Se recorta a 60 caracteres.

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

Las demás rutas que modifican el modelo siguen usando su propia
`Transaction` con nombre propio (por ejemplo `Create Grids via MCP`).
