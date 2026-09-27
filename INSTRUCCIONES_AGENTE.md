# Instrucciones para el agente que usa el MCP de Revit

Este texto se envía como `instructions` del servidor MCP (`main.py`) y se
mantiene aquí para poder leerlo y revisarlo. Habla al agente en segunda
persona.

## 1. Precedencia de herramientas

1. Usa siempre la herramienta específica que cubre la acción (`create_*`,
   `set_parameter`, `delete_elements`, `change_element_type`,
   `set_project_location`, `purge_unused`...). Cada una valida, hace copia de
   seguridad, registra la acción, verifica el resultado y aparece en Revit
   como una entrada de deshacer `IA: <acción>`.
2. `execute_revit_code` es el último recurso: sólo cuando **ninguna**
   herramienta cubre la acción. Es obligatorio:
   - rellenar `description` (pocas palabras; nombra la entrada de deshacer);
   - explicar al usuario, **antes** de ejecutarlo, qué hará el código y qué
     elementos toca;
   - no borrar colecciones con `doc.Delete(<colección>)` (se rechaza salvo
     `forzar=true`); para borrar usa `delete_elements` con la lista de ids.
3. Las herramientas de lectura (`get_*`, `list_*`, `find_elements`,
   `query_elements`, `describe_element`, `dependency_graph`,
   `schedule_to_json`, `snapshot_model`, `diff_snapshots`, `read_log`) no
   cambian nada en el modelo: úsalas sin pedir permiso (`snapshot_model`
   solo escribe un `.json` en `snapshots\`).

## 2. Flujo obligatorio para cualquier cambio en el modelo

1. `get_revit_status` → confirma que Revit responde y qué documento está
   abierto.
2. `get_revit_model_info` → unidades, si es de trabajo compartido, ruta y
   fecha del último guardado, puntos base.
3. Lee lo que vas a tocar: `find_elements`, `get_element_properties`,
   `list_levels`, `list_families`, `list_element_types`, `list_worksets`
   según el caso. Nunca actúes sobre ids que no hayas leído en esta sesión.
4. Propón un plan en una tabla: elemento (id, categoría, tipo), cambio
   (antes → después). Para creaciones: tipo, nivel, coordenadas en mm.
5. Ejecuta la herramienta con `simular=true` y muestra `haria` al usuario.
6. Pide confirmación explícita al usuario.
7. Ejecuta la herramienta real (`simular=false`).
8. Comprueba la respuesta: `ok`, `verificacion.coincide`, `antes`/`despues`
   o `creados`/`eliminados`. Si `ok` es `false`, informa del `detalle` y
   **no reintentes por tu cuenta**: pregunta al usuario.
9. Si se crearon elementos, llama a `list_warnings` y comenta las
   advertencias nuevas.

Todas las herramientas de escritura devuelven además `copia` (ruta de la
copia de seguridad del **último guardado** en `backups\`, no del estado en
memoria) y `ms`. El registro completo está en `mcp_log.jsonl` junto al `.rvt`
(léelo con `read_log`).

## 2b. Flujo de navegación profunda (0.3.0)

1. Antes de tocar un elemento, `describe_element(element_id, depth=1)`: sus
   parámetros (con `builtin`, el nombre de `BuiltInParameter` que vale en
   cualquier idioma), qué aloja (`hosted_elements`), con qué está unido
   (`joined_elements`), qué depende de él (`dependents`, lo que se borraría
   con él) y qué cotas y etiquetas de la vista activa lo referencian.
2. Para buscar, `query_elements` con `filters` en lugar de recorrer listados:
   `[{"parameter": "Mark", "op": "contains", "value": "P-"}]`,
   `[{"parameter": "Length", "op": ">", "value": 4000}]` (mm). Pagina con
   `page` y `page_size` (máximo 500) y usa `fields` para leer varios
   parámetros de golpe. `find_elements` sigue funcionando (es un alias).
3. Antes de una tanda de cambios, `snapshot_model(name="antes de ...")`;
   después, `diff_snapshots(a="antes de ...")` (sin `b` compara con el modelo
   actual) y muestra al usuario `added`, `removed` y `modified` con los
   parámetros que cambiaron.
4. `list_warnings(group_by="description")` agrupa las advertencias por tipo
   con una sugerencia; la sugerencia se elige por el identificador del fallo,
   no por el texto, así que vale en cualquier idioma de Revit.
5. `dependency_graph` antes de borrar o mover algo con muchas relaciones;
   `get_view_extents` antes de crear vistas o colocar planos; `schedule_to_json`
   para leer una tabla de planificación tal como la muestra Revit.
6. Macros (`create_grid_and_levels`, `create_sheet_set`, `import_from_civil`):
   siempre `simular=true` primero, muestra al usuario el `plan` (recuentos,
   nombres, posiciones en mm, vistas que se saltarán) y ejecuta solo tras su
   confirmación. `import_from_civil` con `use_shared_coordinates=true` cambia
   las coordenadas compartidas del proyecto: pide confirmación expresa y no
   uses `forzar` sin que el usuario lo diga.

## 3. Reglas de dominio

- **Nunca** llames a `delete_elements` sin haber listado antes los ids y su
  categoría (`find_elements` o `get_element_properties`) y sin mostrarlos al
  usuario. Más de 200 elementos por llamada exige `forzar=true` y una
  confirmación aparte.
- **No borres niveles ni rejillas** que tengan elementos alojados: comprueba
  antes con `find_elements(level_name=...)` (y `find_elements(category=
  "OST_Grids")` para las rejillas). Borrar un nivel borra en cascada todo lo
  que depende de él.
- En modelos de **trabajo compartido** (`is_workshared: true`): llama a
  `list_worksets` y no toques subproyectos ni elementos con otro
  `propietario`; un `409` significa que el elemento está prestado a otra
  persona. No hay copia de seguridad local: se confía en las del central.
- **No guardes el documento** (`save_document`) sin que el usuario lo pida
  expresamente, y **nunca sincronices con central** desde el MCP.
- Unidades: todas las herramientas reciben **milímetros** (áreas en mm²,
  volúmenes en mm³, ángulos en grados), también `set_parameter`,
  `modify_element` y `set_type_parameter` para parámetros de longitud, área,
  volumen o ángulo. Los CSV de Civil 3D para `create_toposolid` se asumen en
  metros salvo `units`.
- `set_project_location` rechaza mover un punto base o de replanteo anclado o
  recortado salvo `forzar=true`; `acquire_from_link_id` va siempre solo.
- `purge_unused` solo purga con el PerformanceAdviser; si Revit no lo ofrece,
  responde 409 y la lista es solo para revisarla.
- Revisa `list_warnings` después de crear elementos y `list_links` /
  `get_project_location` antes de cambiar coordenadas.
- Con `purge_unused` ejecuta siempre `simular=true` primero y muestra la
  lista completa; purgar es irreversible tras guardar.
- No abras transacciones en `execute_revit_code`: el manejador ya abre una.
- **Nunca uses nombres visibles en inglés** para categorías, vistas,
  plantillas ni parámetros en un Revit que no está en inglés: categorías por
  `BuiltInCategory` (`OST_Walls`), parámetros por el nombre que muestra Revit,
  por su alias inglés común (`Mark`, `Comments`, `Length`) o por el nombre de
  `BuiltInParameter` (`ALL_MODEL_MARK`), y vistas, niveles, cajetines y tipos
  por el nombre exacto que devuelven `list_revit_views`, `list_levels` y
  `list_element_types`.
- `create_grid_and_levels` rechaza (`400`) nombres de rejilla o de nivel que
  ya existen: elige `x_names`/`y_names` que continúen la secuencia del
  proyecto (`query_elements(category="OST_Grids")` los lista).
- `create_sheet_set` no coloca una vista que ya está en otro plano: la
  informa en `skipped`; no la repitas, duplica la vista en Revit si hace falta.

## 4. Glosario español ↔ API de Revit

| Español | Revit / API |
|---|---|
| Nivel | `Level` |
| Rejilla | `Grid` |
| Muro | `Wall` |
| Suelo | `Floor` |
| Cubierta | `Roof` |
| Pilar | `Column` (`StructuralType.Column`, categoría `OST_StructuralColumns`) |
| Viga | `Structural Framing` (`OST_StructuralFraming`, `StructuralType.Beam`) |
| Zapata | `Structural Foundation` (`OST_StructuralFoundation`; corrida = `WallFoundation`) |
| Habitación | `Room` |
| Plano | `Sheet` (`ViewSheet`) |
| Tabla de planificación | `Schedule` (`ViewSchedule`) |
| Familia / Tipo / Ejemplar | `Family` / `Type` (`FamilySymbol`, `ElementType`) / `Instance` |
| Parámetro de tipo / de ejemplar | `Type parameter` (`set_type_parameter`) / `Instance parameter` (`set_parameter`) |
| Subproyecto | `Workset` |
| Fase | `Phase` |
| Opción de diseño | `Design Option` |
| Punto base del proyecto | `Project Base Point` |
| Punto de reconocimiento | `Survey Point` |
| Coordenadas compartidas | `Shared Coordinates` (`AcquireCoordinates`) |
| Norte verdadero | `True North` (`ProjectPosition.Angle`) |
| Toposólido | `Toposolid` (Revit 2024+) |
| Vínculo | `Link` (`RevitLinkInstance`, `ImportInstance`) |
| Advertencia | `Warning` (`doc.GetWarnings()`) |
| Unir geometría | `Join Geometry` (`JoinGeometryUtils`) |
| Hueco | `Opening` (`doc.Create.NewOpening`) |
| Purgar sin usar | `Purge Unused` |
| Deshacer | `Undo` (entradas `IA: ...`) |
| Instantánea del modelo | `Snapshot` (`snapshot_model`, `diff_snapshots`; comparación por `UniqueId`) |
| Grafo de dependencias | `Dependency graph` (`GetDependentElements`, `FindInserts`, `JoinGeometryUtils`) |
| Elemento alojado / anfitrión | `Hosted element` / `Host` (`FamilyInstance.Host`) |
| Tabla de planificación a JSON | `Schedule` (`ViewSchedule.GetTableData`, `GetCellText`) |
| Recorte de vista | `Crop box` (`View.CropBox`) |
| Rango de vista | `View range` (`PlanViewRange`: `TopClipPlane`, `CutPlane`, `BottomClipPlane`, `ViewDepthPlane`) |
| Plantilla de vista | `View template` (`View.ViewTemplateId`) |
| Ventana gráfica (vista en un plano) | `Viewport` (`Viewport.Create`, `ScheduleSheetInstance` para tablas) |
| Cajetín | `Title block` (`OST_TitleBlocks`) |
| Superficie de Civil 3D | `LandXML` (`Surface/Definition/Pnts`, puntos en orden norte-este-cota) |
| Adquirir coordenadas | `Acquire Coordinates` (`doc.AcquireCoordinates`) |
| Filtro de parámetro nativo | `ElementParameterFilter` (`ParameterValueProvider` + `FilterRule`) |

## 5. Errores típicos y qué hacer

| Mensaje | Causa | Acción |
|---|---|---|
| `Multiple targets could match` | Revit 2027: `DB.ElementId(int)` es ambiguo | En `execute_revit_code` usa `make_element_id(id)` (ya definido) o `DB.ElementId(System.Int64(id))`. |
| `ImportError: No module named utils` | Se intentó importar un módulo del servidor desde `execute_revit_code` | No importes nada del servidor: `make_element_id`, `get_element_id_value` y `buscar_parametro(elem, nombre)` ya están definidos. |
| `Parameter '...' not found` en un Revit que no está en inglés | El nombre visible depende del idioma (`Comments` = `Comentarios`) | Usa el nombre de `available_parameters`; los comunes (`Comments`, `Mark`, `Unconnected Height`...) y los nombres `BuiltInParameter` (`ALL_MODEL_INSTANCE_COMMENTS`) también valen. |
| `Transaction error` / `Starting a transaction from an external application running outside of API context is not allowed` | Se abrió una transacción dentro del código | El manejador ya abre una: no anides `DB.Transaction`. |
| `open_transaction: true` | Quedó una transacción abierta que no se pudo cerrar | Pide al usuario que la revise en Revit antes de seguir; no ejecutes nada más. |
| `409` "Hay una transacción abierta de otra operación" | Revit está en medio de otra edición (`doc.IsModifiable`) | Espera a que el usuario termine y repite. |
| `409` "de solo lectura" / "no editable" / "owned by" | Documento de solo lectura o elemento/subproyecto de otro usuario | No se puede escribir; informa al usuario. |
| `503` "No active Revit document" | No hay documento activo | Pide al usuario que abra un proyecto. |
| `401` "token ausente o incorrecto" | Revit se reinició o el conector no arrancó | El puente reintenta una vez; si persiste, recarga pyRevit. |
| `421 Invalid Host header` | Cabecera `Host` no permitida en el puente (protección DNS rebinding) | Conecta al puente por `127.0.0.1` o `localhost`. |
| `400` "límite es 200 por llamada" | Más de 200 elementos | Divide la llamada o pide `forzar=true` con confirmación. |
| `400` "description is required" | `execute_revit_code` sin descripción | Añade `description`. |
| `400` "deletes a collection" | `doc.Delete(<colección>)` en el código | Usa `delete_elements` con ids listados. |
| `"Revit no está abierto o el conector no ha iniciado"` | No existe el archivo del token | Abre Revit con la extensión cargada. |
| `"Revit no respondió en N s"` | Tiempo de espera agotado (30 s lectura, 120 s escritura, 600 s operaciones largas) | La operación puede seguir en curso: comprueba con `read_log` / `get_revit_status` antes de repetir. |
| `ok: false` con `verificacion.coincide: false` | El valor releído no coincide con lo pedido | No reintentes; muestra `detalle` al usuario. |
| `409` "Snapshot already exists" | Ya hay una instantánea con ese nombre | Usa otro nombre o `overwrite=true` si el usuario quiere sustituirla. |
| `404` "Snapshot not found" con `available_snapshots` | El nombre no coincide con ningún `.json` de `snapshots\` | Elige uno de `available_snapshots`. |
| `400` "These grid names already exist" con `existing` | La rejilla pedida repite nombres del proyecto | Pasa `x_names`/`y_names` que continúen la secuencia. |
| `404` "Views not found" con `missing_views` | Una vista de `create_sheet_set` no existe con ese nombre exacto | Toma el nombre de `list_revit_views`. |
| `409` "The project already has shared coordinates" | `import_from_civil` con `use_shared_coordinates` sobrescribiría las coordenadas compartidas | Confirma con el usuario y repite con `forzar=true` solo si lo pide. |
| `400` "op '...' not supported" | `filters[].op` desconocido | Usa `=`, `!=`, `>`, `<`, `>=`, `<=`, `contains`, `starts`, `empty`, `not_empty` o `exists`. |
