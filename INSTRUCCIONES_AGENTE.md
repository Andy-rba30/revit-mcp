# Instrucciones para el agente que usa el MCP de Revit

Este texto se envía como `instructions` del servidor MCP (`main.py`) y se
mantiene aquí para poder leerlo y revisarlo. Habla al agente en segunda
persona. Versión del conector: **0.5.0** (52 herramientas: las 40 de 0.4.0 más
las 12 de estructuras metálicas y modelo analítico; las 51 retiradas en 0.4.0
responden con el nombre de su sustituta si las llamas).

## 1. Precedencia de herramientas

1. **Antes de encadenar varias herramientas para una tarea repetitiva, mira
   `list_macros`.** Si hay una macro del usuario que la cubre (renumerar
   planos, rellenar comentarios por nivel...), usa `run_macro(name, args,
   simular=true)`, muestra su `plan` y ejecútala tras la confirmación. Una
   macro es un plugin determinista del usuario: tú solo eliges cuándo y con
   qué argumentos.
2. **Una tarea habitual se resuelve en una o dos llamadas.** Los cambios de
   parámetros van todos en `set_parameters(changes=[...])` (una transacción
   por llamada, hasta 200 pares elemento × parámetro) y las creaciones en
   `create_elements(elements=[{"kind": ...}, ...])` (una transacción, mezcla
   de kinds). No hagas 20 llamadas de un elemento cada una.
3. Usa siempre la herramienta específica que cubre la acción
   (`create_elements`, `set_parameters`, `delete_elements`,
   `change_element_type`, `transform_elements`, `annotate`, `set_project_location`,
   `maintain_model`...). Cada una valida, hace copia de seguridad, registra
   la acción, verifica el resultado y aparece en Revit como una entrada de
   deshacer `IA: <acción>`.
4. `execute_revit_code` es el último recurso: sólo cuando **ninguna**
   herramienta ni macro cubre la acción. Es obligatorio:
   - rellenar `description` (pocas palabras; nombra la entrada de deshacer);
   - explicar al usuario, **antes** de ejecutarlo, qué hará el código y qué
     elementos toca;
   - no borrar colecciones con `doc.Delete(<colección>)` (se rechaza salvo
     `forzar=true`); para borrar usa `delete_elements` con la lista de ids.
5. Las herramientas de lectura (`get_revit_status`, `get_revit_model_info`,
   `list_views`, `describe_view`, `capture_view`, `query_elements`,
   `describe_element`, `dependency_graph`, `list_types`, `schedule_to_json`,
   `list_warnings`, `analyze_model`, `check_clashes`, `snapshot_model`,
   `diff_snapshots`, `read_log`, `list_macros`, `list_steel_profiles`,
   `steel_quantities`, `analytical_status`) no cambian nada en el modelo:
   úsalas sin pedir permiso (`snapshot_model` solo escribe un `.json` en
   `snapshots\`; `export` con `csv_nodes_members` solo escribe el CSV).

## 2. Flujo obligatorio para cualquier cambio en el modelo

1. `get_revit_status` → confirma que Revit responde y qué documento está
   abierto.
2. `get_revit_model_info(include=["levels", "location"])` → unidades, si es
   de trabajo compartido, ruta y fecha del último guardado, niveles, puntos
   base. Añade `"worksets"`, `"phases"` o `"links"` cuando los necesites.
3. Lee lo que vas a tocar: `query_elements` (filtros, vista activa con
   `current_view=true`, selección con `selected=true`), `describe_element`
   (hasta 20 ids por llamada), `list_types` (tipos de una categoría, familias
   cargadas, categorías). Nunca actúes sobre ids que no hayas leído en esta
   sesión.
4. Propón un plan en una tabla: elemento (id, categoría, tipo), cambio
   (antes → después). Para creaciones: `kind`, tipo, nivel, coordenadas en mm.
5. Ejecuta la herramienta con `simular=true` y muestra `haria` (y `plan` en
   lotes y macros) al usuario.
6. Pide confirmación explícita al usuario.
7. Ejecuta la herramienta real (`simular=false`).
8. Comprueba la respuesta: `ok`, `verificacion.coincide`, `antes`/`despues`
   o `creados`/`eliminados`, y `fallidos` en `set_parameters` (un parámetro
   de solo lectura o inexistente no aborta el lote: se informa). Si `ok` es
   `false`, informa del `detalle` y **no reintentes por tu cuenta**: pregunta
   al usuario.
9. Si se crearon elementos, llama a `list_warnings` y comenta las
   advertencias nuevas.

Todas las herramientas de escritura devuelven además `copia` (ruta de la
copia de seguridad del **último guardado** en `backups\`, no del estado en
memoria; desde 0.4.0 se copia en un hilo aparte y `copia.ms` /
`copia.espera_ms` dicen cuánto tardó y cuánto esperó la transacción), `ms`
(tiempo en Revit) y `ms_puente` (tiempo total visto desde el puente: red y
serialización incluidas). El registro completo está en `mcp_log.jsonl` junto
al `.rvt` (léelo con `read_log`).

## 2b. Lotes (0.4.0)

- `set_parameters(changes=[{"element_ids": [...], "parameters": {nombre: valor}}])`
  cambia varios parámetros de varios elementos en una transacción. Con
  `type_parameters=true` (global o por cambio) toca los parámetros de **tipo**
  (afecta a todos los ejemplares del tipo; se fija una vez por tipo). La
  respuesta trae `antes`/`despues` por elemento y parámetro, `changes[]`
  con `parameter_label` (nombre visible) e `is_type_parameter`, y `fallidos[]`
  con el motivo. Sustituye a `set_parameter`, `modify_element` y
  `set_type_parameter`.
- `create_elements(elements=[{"kind": "wall", ...}, {"kind": "level", ...}])`
  crea elementos de kinds mezclados en una transacción: `wall`, `floor`, `roof`,
  `ceiling`, `level`, `grid`, `column`, `beam`, `foundation`, `opening`,
  `toposolid`, `room`, `room_separation`, `detail_line`, `duct`, `pipe`,
  `family_instance`. Cada elemento lleva los mismos argumentos que la ruta de
  creación de 0.3.x de su kind (ver CONTRATO.md). Se valida **todo** antes de
  abrir la transacción (`400` con `index` y `kind` del primero que falla) y si
  uno falla dentro de Revit se revierte todo (`500` con `index`, `kind` y
  `revit_error`). `creados` viene agrupado por kind. Un elemento que depende
  de otro del mismo lote (un muro sobre un nivel nuevo) va en dos llamadas:
  el nivel primero.
- `transform_elements(operation="array", vector=..., count=n)` crea `n-1`
  copias desplazadas en una transacción.

## 2c. Flujo de navegación profunda (0.3.0)

1. Antes de tocar un elemento, `describe_element(element_id, depth=1)`: sus
   parámetros (con `builtin`, el nombre de `BuiltInParameter` que vale en
   cualquier idioma), qué aloja (`hosted_elements`), con qué está unido
   (`joined_elements`), qué depende de él (`dependents`, lo que se borraría
   con él) y qué cotas y etiquetas de la vista activa lo referencian. Con
   `element_ids=[...]` describes hasta 20 en una llamada.
2. Para buscar, `query_elements` con `filters` en lugar de recorrer listados:
   `[{"parameter": "Mark", "op": "contains", "value": "P-"}]`,
   `[{"parameter": "Length", "op": ">", "value": 4000}]` (mm). Pagina con
   `page` y `page_size` (máximo 500) y usa `fields` para leer varios
   parámetros de golpe. Lo que antes pedías a `ai_element_filter` en lenguaje
   natural se traduce a `filters`: "muros de más de 4 m en la vista actual" =
   `query_elements(category="OST_Walls", current_view=true, filters=[{"parameter":
   "Length", "op": ">", "value": 4000}])`; "puertas con marca que empiece por
   P" = `filters=[{"parameter": "Mark", "op": "starts", "value": "P"}]`;
   "elementos sin comentarios" = `[{"parameter": "Comments", "op": "empty"}]`;
   una caja en mm = `bbox_min_mm` + `bbox_max_mm`.
3. Antes de una tanda de cambios, `snapshot_model(name="antes de ...")`;
   después, `diff_snapshots(a="antes de ...")` (sin `b` compara con el modelo
   actual) y muestra al usuario `added`, `removed` y `modified` con los
   parámetros que cambiaron. `timings` dice cuánto tardó cada etapa; con
   `include_bbox=false` va más rápido si no te importan los movimientos.
4. `list_warnings(group_by="description")` agrupa las advertencias por tipo
   con una sugerencia; la sugerencia se elige por el identificador del fallo,
   no por el texto, así que vale en cualquier idioma de Revit.
5. `dependency_graph` antes de borrar o mover algo con muchas relaciones;
   `describe_view` (sin argumentos = la vista activa) antes de crear vistas o
   colocar planos; `schedule_to_json` para leer una tabla de planificación tal
   como la muestra Revit; `list_views(on_sheet=false)` para saber qué vistas
   no están aún en ningún plano.
6. Macros de proyecto (`create_grid_and_levels`, `create_sheet_set`,
   `import_from_civil`) y macros del usuario (`run_macro`): siempre
   `simular=true` primero, muestra al usuario el `plan` (recuentos, nombres,
   posiciones en mm, vistas que se saltarán) y ejecuta solo tras su
   confirmación. `import_from_civil` con `use_shared_coordinates=true` cambia
   las coordenadas compartidas del proyecto: pide confirmación expresa y no
   uses `forzar` sin que el usuario lo diga.

## 2d. Flujo de estructura metálica (0.5.0)

1. Niveles con `get_revit_model_info(include=["levels"])` y rejillas con
   `query_elements(category="OST_Grids")`: toma los nombres exactos que
   devuelven (nunca supongas "1, 2, 3 / A, B, C"). Si no hay rejillas rectas,
   créalas con `create_grid_and_levels`.
2. `list_steel_profiles(loaded_only=true)`: perfiles de acero cargados con su
   forma (`W`, `HSS`, `L`, `C`, `WT`, `Pipe`), norma deducida (`AISC`, `EN`) y
   dimensiones en mm. Si falta el perfil, `list_steel_profiles(loaded_only=false)`
   lista los `.rfa` con catálogo de la biblioteca y `load_steel_profile(family_name=...,
   type_names=[...])` carga solo los tipos que hacen falta (con catálogo `.txt`
   hay que nombrar los tipos; la familia ya cargada responde `409` salvo
   `overwrite=true`).
3. `create_steel_frame(column_type, beam_type, grids_x, grids_y, levels,
   simular=true)`: muestra al usuario `plan.counts` (`columns`, `beams`,
   `total`), las intersecciones (`"A-1"`) y los `warnings` (rejillas curvas,
   nivel sin nivel superior). `skip_columns_at=["A-1"]` y
   `skip_beams_at=["A-1/A-2"]` quitan pilares y vanos; `mark_prefix` numera.
4. Tras su confirmación, ejecuta sin `simular`: una sola entrada
   `IA: Portico metalico`; `creados.columns` y `creados.beams` traen ids, nivel
   superior y marcas. Arriostres con `create_bracing` (patrón por vano) y
   cerchas con `create_truss`, siempre en lote.
5. `set_structural_properties(element_ids=[...], start_release="pinned",
   end_release={"FX": true, "MZ": true}, y_justification="center", ...)` para
   liberaciones, justificaciones, desfases (mm), rotación (grados),
   extensiones y `analyze_as`: todo en una llamada por lote de elementos.
   `fallidos` dice qué par elemento × propiedad no se pudo fijar y
   `no_disponibles` qué `BuiltInParameter` no existe en esa versión de Revit
   (las liberaciones van al `AnalyticalMember` cuando el elemento físico no
   las tiene, Revit 2023+). Sin modelo analítico (`analytical_status`:
   `members: 0`) las liberaciones no se pueden fijar: si solo pides
   liberaciones, responde `409` `no_soportado` con `motivo:
   sin_modelo_analitico`. Díselo al usuario; no lo rodees con
   `execute_revit_code`. Comprueba con `describe_element(element_id,
   include_structural=true)`.
6. `analytical_status(element_ids=[...])`: `members`, `sin_analitico` y
   `loose_nodes_total`; con nodos sueltos, `fix_analytical_alignment(element_ids,
   tolerance_mm=50, simular=true)`, muestra los movimientos (`from_mm`,
   `to_mm`, `distance_mm`) y ejecuta tras confirmar. Un nodo sin nada a menos
   de la tolerancia queda en `sin_objetivo`: no subas la tolerancia sin decirlo.
7. `steel_quantities(group_by="type")` como comprobación: recuento, longitud
   y peso por grupo; los ids de `sin_peso` traen el motivo (material sin
   activo estructural, tipo sin masa lineal). `export(format="csv_nodes_members",
   file_path=...)` o `export(format="ifc_structural", file_path=...)` para
   entregar el modelo analítico.
8. **Un lote, no una llamada por elemento**: `set_structural_properties`,
   `create_bracing`, `create_truss`, `create_steel_connection`,
   `fix_analytical_alignment` y `join_geometry(element_ids=[...])` reciben
   listas y aplican todo en una transacción con `fallidos[]`; no repitas la
   herramienta por cada viga.
9. Conexiones de acero: `list_types(category="connections")` lista los tipos
   (y `approval_types`); `create_steel_connection(connections=[{"element_ids":
   [...], "connection_type": ...}])`. Un `409` `no_soportado` significa que el
   módulo Steel Connections for Revit no está instalado o no hay tipos
   cargados: díselo al usuario, no lo rodees con `execute_revit_code`.
10. `add_plate_or_stiffener` coloca una familia alojada en cara (`top`,
    `bottom`, `web`) o de punto sobre una viga o pilar; `split_beam` divide
    una viga recta (con `FamilyInstance.Split`, que conserva las uniones de
    los extremos; mira `metodo`, `original.segment` y `verificacion`).
    Revit no une la geometría de perfiles de acero: para vigas y pilares
    metálicos usa `join_geometry(element_ids=[...], coping=true)`, que solo
    recorta la viga contra el pilar o la otra viga, sea cual sea el orden.

## 3. Reglas de dominio

- **Nunca** llames a `delete_elements` sin haber listado antes los ids y su
  categoría (`query_elements` o `describe_element`) y sin mostrarlos al
  usuario. Más de 200 elementos por llamada exige `forzar=true` y una
  confirmación aparte.
- **No borres niveles ni rejillas** que tengan elementos alojados: comprueba
  antes con `query_elements(level=...)` (y `query_elements(category=
  "OST_Grids")` para las rejillas). Borrar un nivel borra en cascada todo lo
  que depende de él.
- En modelos de **trabajo compartido** (`is_workshared: true`): llama a
  `get_revit_model_info(include=["worksets"])` y no toques subproyectos ni
  elementos con otro `propietario`; un `409` significa que el elemento está
  prestado a otra persona. No hay copia de seguridad local: se confía en las
  del central.
- **No guardes el documento** (`maintain_model(action="save")`) sin que el
  usuario lo pida expresamente, y **nunca sincronices con central** desde el
  MCP.
- Unidades: todas las herramientas reciben **milímetros** (áreas en mm²,
  volúmenes en mm³, ángulos en grados), también `set_parameters` para
  parámetros de longitud, área, volumen o ángulo. Los CSV de Civil 3D para
  `create_elements(kind="toposolid")` e `import_from_civil` se asumen en
  metros salvo `units`.
- `set_project_location` rechaza mover un punto base o de replanteo anclado o
  recortado salvo `forzar=true`; `acquire_from_link_id` va siempre solo.
- `maintain_model(action="purge")` solo purga con el PerformanceAdviser; si
  Revit no lo ofrece, responde 409 y la lista es solo para revisarla. Ejecuta
  siempre `simular=true` primero y muestra la lista completa; purgar es
  irreversible tras guardar.
- Revisa `list_warnings` después de crear elementos y
  `get_revit_model_info(include=["links", "location"])` antes de cambiar
  coordenadas.
- No abras transacciones en `execute_revit_code` ni en las macros: el
  servidor ya abre una.
- **Nunca uses nombres visibles en inglés** para categorías, vistas,
  plantillas ni parámetros en un Revit que no está en inglés: categorías por
  `BuiltInCategory` (`OST_Walls`), parámetros por el nombre que muestra Revit,
  por su alias inglés común (`Mark`, `Comments`, `Length`) o por el nombre de
  `BuiltInParameter` (`ALL_MODEL_MARK`), y vistas, niveles, cajetines y tipos
  por el nombre exacto que devuelven `list_views`,
  `get_revit_model_info(include=["levels"])` y `list_types`.
- `create_grid_and_levels` rechaza (`400`) nombres de rejilla o de nivel que
  ya existen: elige `x_names`/`y_names` que continúen la secuencia del
  proyecto (`query_elements(category="OST_Grids")` los lista).
- `create_sheet_set` no coloca una vista que ya está en otro plano: la
  informa en `skipped`; no la repitas, duplica la vista en Revit si hace falta.
- Una macro con `writes: false` no puede cambiar el modelo (Revit rechaza
  cualquier escritura fuera de transacción); una con `writes: true` corre
  dentro de `IA: Macro <nombre>` con copia, registro y verificación. Si
  `list_macros` marca una macro como inválida, dile al usuario el `error` del
  manifiesto en vez de intentar arreglarla tú.

## 4. Glosario español ↔ API de Revit

| Español | Revit / API |
|---|---|
| Nivel | `Level` |
| Rejilla | `Grid` |
| Muro | `Wall` |
| Suelo | `Floor` |
| Cubierta | `Roof` |
| Techo | `Ceiling` (`Ceiling.Create`, Revit 2022+) |
| Pilar | `Column` (`StructuralType.Column`, categoría `OST_StructuralColumns`) |
| Viga | `Structural Framing` (`OST_StructuralFraming`, `StructuralType.Beam`) |
| Zapata | `Structural Foundation` (`OST_StructuralFoundation`; corrida = `WallFoundation`) |
| Habitación | `Room` |
| Plano | `Sheet` (`ViewSheet`) |
| Tabla de planificación | `Schedule` (`ViewSchedule`) |
| Familia / Tipo / Ejemplar | `Family` / `Type` (`FamilySymbol`, `ElementType`) / `Instance` |
| Parámetro de tipo / de ejemplar | `Type parameter` (`set_parameters(type_parameters=true)`) / `Instance parameter` (`set_parameters`) |
| Lote | `changes[]` / `elements[]` en una sola `Transaction` |
| Macro del usuario | `macro.json` + `macro.py` en `%LOCALAPPDATA%\RevitMcp\macros\<nombre>\` (`run_macro`) |
| Matriz | `Array` (`transform_elements(operation="array")`, `CopyElement` repetido) |
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
| Perfil de acero | `FamilySymbol` de `OST_StructuralFraming` / `OST_StructuralColumns` con `StructuralMaterialType.Steel` (`list_steel_profiles`, `load_steel_profile`) |
| Catálogo de tipos | `Type catalog` (`.txt` junto al `.rfa`; `LoadFamilySymbol` por tipo) |
| Pórtico metálico | `create_steel_frame` (`NewFamilyInstance` con `StructuralType.Column` / `Beam`) |
| Arriostre | `Brace` (`StructuralType.Brace`, `create_bracing`) |
| Cercha | `Truss` (`Truss.Create`, `TrussType`, `create_truss`) |
| Rigidizador | `Stiffener` (familia alojada en cara, `add_plate_or_stiffener(face="web")`) |
| Placa base | `Base plate` (`add_plate_or_stiffener`, familia de punto o de cara) |
| Liberación | `Release` (`STRUCTURAL_START_RELEASE_*` / `STRUCTURAL_END_RELEASE_*`, `AnalyticalMember.SetReleaseType`) |
| Justificación | `Justification` (`Y_JUSTIFICATION`, `Z_JUSTIFICATION`) |
| Desfase / rotación de sección / extensión | `Y_OFFSET_VALUE`, `Z_OFFSET_VALUE` / `STRUCTURAL_BEND_DIR_ANGLE` / `START_EXTENSION`, `END_EXTENSION` |
| Uso estructural | `Structural usage` (`INSTANCE_STRUCT_USAGE_PARAM`, `StructuralInstanceUsage`) |
| Conexión de acero | `Steel connection` (`StructuralConnectionHandler`, tipos `StructuralConnectionHandlerType`) |
| Modelo analítico | `Analytical model` (`AnalyticalMember`, `AnalyticalToPhysicalAssociationManager`) |
| Nodo | `Analytical node` (extremos de `AnalyticalMember.GetCurve()`; `fix_analytical_alignment` los mueve con `SetCurve`) |
| Recorte de viga (coping) | `Coping` (`FamilyInstance.AddCoping`, `join_geometry(coping=true)`) |
| Peso del acero | volumen (`HOST_VOLUME_COMPUTED`) × densidad del activo estructural (`StructuralAsset.Density`) |

## 5. Errores típicos y qué hacer

| Mensaje | Causa | Acción |
|---|---|---|
| `La herramienta 'X' se retiro en 0.4.0. Usa en su lugar: ...` | Llamaste a una de las 51 herramientas retiradas (`set_parameter`, `find_elements`, `list_levels`, `create_line_based_element`...) | Usa la sustituta que indica el mensaje con los argumentos que propone. |
| `Multiple targets could match` | Revit 2027: `DB.ElementId(int)` es ambiguo | En `execute_revit_code` usa `make_element_id(id)` (ya definido) o `DB.ElementId(System.Int64(id))`. |
| `ImportError: No module named utils` | Se intentó importar un módulo del servidor desde `execute_revit_code` | No importes nada del servidor: `make_element_id`, `get_element_id_value` y `buscar_parametro(elem, nombre)` ya están definidos. |
| `Parameter '...' not found` en un Revit que no está en inglés | El nombre visible depende del idioma (`Comments` = `Comentarios`) | Usa el nombre de `available_parameters`; los comunes (`Comments`, `Mark`, `Unconnected Height`...) y los nombres `BuiltInParameter` (`ALL_MODEL_INSTANCE_COMMENTS`) también valen. |
| `fallidos[]` con `motivo: read-only` / `parameter not found` / `element not found` en `set_parameters` | Ese par elemento × parámetro no se pudo fijar; el resto del lote sí | Informa al usuario; no repitas el lote entero, solo lo que falta si procede. |
| `400` `elements[i]: ...` con `index` y `kind` en `create_elements` | El elemento `i` no pasó la validación previa (nivel o tipo inexistente, puntos iguales, nombre repetido) | Nada se creó; corrige ese elemento y repite. |
| `500` `elements[i] (kind) failed in Revit ... rolled back` | Revit rechazó un elemento dentro de la transacción | Nada se creó; mira `revit_error`, corrige y repite (o crea ese elemento aparte). |
| `Transaction error` / `Starting a transaction from an external application running outside of API context is not allowed` | Se abrió una transacción dentro del código o de una macro | El servidor ya abre una: no anides `DB.Transaction`. |
| `open_transaction: true` | Quedó una transacción abierta que no se pudo cerrar | Pide al usuario que la revise en Revit antes de seguir; no ejecutes nada más. |
| `409` "Hay una transacción abierta de otra operación" | Revit está en medio de otra edición (`doc.IsModifiable`) | Espera a que el usuario termine y repite. |
| `409` "de solo lectura" / "no editable" / "owned by" | Documento de solo lectura o elemento/subproyecto de otro usuario | No se puede escribir; informa al usuario. |
| `503` "No active Revit document" | No hay documento activo | Pide al usuario que abra un proyecto. |
| `401` "token ausente o incorrecto" | Revit se reinició o el conector no arrancó | El puente reintenta una vez; si persiste, recarga pyRevit. |
| `421 Invalid Host header` | Cabecera `Host` no permitida en el puente (protección DNS rebinding) | Conecta al puente por `127.0.0.1` o `localhost`. |
| `400` "límite es 200 por llamada" | Más de 200 elementos (o pares elemento × parámetro, o `plan.count` de una macro) | Divide la llamada o pide `forzar=true` con confirmación. |
| `400` "description is required" | `execute_revit_code` sin descripción | Añade `description`. |
| `400` "deletes a collection" | `doc.Delete(<colección>)` en el código | Usa `delete_elements` con ids listados. |
| `404` "Macro '...' not found" con `available_macros` | No hay carpeta con ese nombre en la carpeta de macros | Elige una de `available_macros` (o dile al usuario dónde debe dejar la macro: `carpeta`). |
| `400` "Arguments of macro ... do not match macro.json" con `faltan` / `sobran` / `invalidos` | Los `args` no cuadran con el manifiesto | Corrige los argumentos según `args_spec`. |
| `400` "macro.py ... could not be loaded" | Error de sintaxis o de importación en la macro del usuario | Muestra el error al usuario; no la reescribas tú. |
| `"Revit no está abierto o el conector no ha iniciado"` | No existe el archivo del token | Abre Revit con la extensión cargada. |
| `"Revit no respondió en N s"` | Tiempo de espera agotado (30 s lectura, 120 s escritura, 600 s operaciones largas y macros) | La operación puede seguir en curso: comprueba con `read_log` / `get_revit_status` antes de repetir. |
| `ok: false` con `verificacion.coincide: false` | El valor releído no coincide con lo pedido | No reintentes; muestra `detalle` al usuario. |
| `409` "Snapshot already exists" | Ya hay una instantánea con ese nombre | Usa otro nombre o `overwrite=true` si el usuario quiere sustituirla. |
| `404` "Snapshot not found" con `available_snapshots` | El nombre no coincide con ningún `.json` de `snapshots\` | Elige uno de `available_snapshots`. |
| `400` "These grid names already exist" con `existing` | La rejilla pedida repite nombres del proyecto | Pasa `x_names`/`y_names` que continúen la secuencia. |
| `404` "Views not found" con `missing_views` | Una vista de `create_sheet_set` no existe con ese nombre exacto | Toma el nombre de `list_views`. |
| `409` "The project already has shared coordinates" | `import_from_civil` con `use_shared_coordinates` sobrescribiría las coordenadas compartidas | Confirma con el usuario y repite con `forzar=true` solo si lo pide. |
| `400` "op '...' not supported" | `filters[].op` desconocido | Usa `=`, `!=`, `>`, `<`, `>=`, `<=`, `contains`, `starts`, `empty`, `not_empty` o `exists`. |
| `409` con `no_soportado: true` en `list_types(category="connections")` o `create_steel_connection` | La API no expone `StructuralConnectionHandler` (falta Steel Connections for Revit) o no hay tipos de conexión cargados | Informa al usuario; no crees conexiones con `execute_revit_code`. |
| `409` con `no_soportado: true` en `analytical_status` / `fix_analytical_alignment` | La API no expone `AnalyticalToPhysicalAssociationManager` (Revit anterior a 2023) | Informa; el conector no lee el modelo analítico antiguo. |
| `nota` "No hay perfiles de acero cargados" en `list_steel_profiles` | El proyecto no tiene familias de acero | `list_steel_profiles(loaded_only=false)` y `load_steel_profile` con los tipos que hagan falta. |
| `400` "has a type catalog: give type_names" con `available_types` | La familia tiene catálogo `.txt` y `LoadFamilySymbol` carga un tipo cada vez | Repite con `type_names=[...]` elegidos de `available_types`. |
| `409` "already loaded" en `load_steel_profile` | La familia (y los tipos pedidos) ya están en el proyecto | No hace falta cargar; usa `overwrite=true` solo si el usuario quiere recargarla. |
| `404` "grids not found" / `400` "unknown intersections" con `available_grids` / `available_labels` | Nombres de rejilla o etiquetas (`"A-1"`) que no existen | Usa los nombres de `query_elements(category="OST_Grids")` y las etiquetas de `available_labels`. |
| `fallidos[]` con `motivo: parameter not found and no analytical member associated` en `set_structural_properties` | El elemento no tiene ese parámetro (un pilar sin extensiones) ni miembro analítico para las liberaciones | Informa; el resto del lote sí se aplicó. |
| `409` `no_soportado` con `motivo: sin_modelo_analitico` en `set_structural_properties` | Solo se pidieron liberaciones y ningún elemento tiene miembro analítico (Revit 2023+) | Informa: el usuario tiene que crear el modelo analítico en Revit. |
| `fallidos[]` con "The elements cannot be joined" en `join_geometry` (y `nota`) | Revit no une la geometría de esa pareja (dos perfiles de acero) | Repite con `coping=true` si son vigas y pilares metálicos. |
| `500` al exportar con `ruta_recibida`, `caracteres_no_ascii`, `posible_ruta_mal_leida` y `sugerencia` | Revit no pudo crear la carpeta o escribir el archivo | Enseña el diagnóstico al usuario y prueba una carpeta sin tildes (`C:\IA\salidas`); si ahí funciona, el Escritorio está protegido o la ruta llega mal leída. |
| `no_disponibles[]` en `set_structural_properties` / `describe_element(include_structural)` | Ese `BuiltInParameter` no existe en la versión de Revit | Informa; anótalo en `herramientas-dev/miembros_por_verificar_revit.md`. |
| `400` "approve=true needs approval_status" con `available_approval_types` | El estado de aprobación se elige por su nombre visible (depende del idioma) o su id | Repite con `approval_status` tomado de la lista. |
| `sin_peso[]` en `steel_quantities` | Material sin activo estructural o tipo sin masa lineal | Informa el motivo; no inventes densidades. |
| `sin_objetivo[]` en `fix_analytical_alignment` | Nodo suelto sin ningún nodo ajeno a menos de `tolerance_mm` | Muéstralo con `nearest_mm`; sube la tolerancia solo si el usuario lo pide. |
