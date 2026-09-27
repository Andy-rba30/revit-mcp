# Prompt fase 2 (revit-mcp): navegación profunda, estructuras metálicas y creación de familias

Eres un agente de programación con acceso al repositorio `revit-mcp`. La fase 1 está terminada y **validada dentro
de Revit** (versión 0.2.2, 68 herramientas, 125 pruebas en CPython, `pruebas/probar_revit.py` 9/9 en un Revit en
español). Llega a `main` con el PR Andy-rba30/revit-mcp#1.

Antes de escribir código, lee en su estado actual: `CONTRATO.md`, `INSTRUCCIONES_AGENTE.md`, `REVISION_FASE1.md`,
`revit_mcp/escritura.py`, `revit_mcp/utils.py`, `revit_mcp/consulta.py`, `revit_mcp/estructural.py`,
`tools/utils.py` y `tests/`. Construye sobre ellos.

La fase 2 se hace en **tres entregas separadas**, cada una en su rama desde `main` (con el PR #1 ya fusionado), con
su PR y su validación en Revit antes de empezar la siguiente:

| Entrega | Rama | Contenido | Versión |
|---|---|---|---|
| 2a | `feature/fase2a-navegacion` | Bloque 0 + Bloque A + Bloque D | 0.3.0 |
| 2b | `feature/fase2b-acero` | Bloque B (acero y modelo analítico) | 0.4.0 |
| 2c | `feature/fase2c-familias` | Bloque C (editor de familias) | 0.5.0 |

**En esta ejecución haz solo la entrega que te indique el usuario.** Si no la indica, haz la 2a. Un commit por
bloque o sub-bloque.

## Bloque 0. Estado de partida y reglas de verificación

1. Crea `herramientas-dev/miembros_por_verificar_revit.md` con una tabla (tipo, miembro, versión mínima, ruta que lo
   usa, estado). Parte de este estado real de la fase 1:
   - **Verificados en Revit:** `doc.Create.NewOpening` en muro; `request.query_params` en rutas GET;
     `TransactionStatus`; `ElementId(System.Int64)`; `ElementId.Value`; `LookupParameter` y `get_Parameter`
     con `BuiltInParameter`; `Definition.GetDataType`; `SpecTypeId.Length`.
   - **No existe:** `doc.GetUndoName()`. La prueba 9 de `probar_revit.py` sigue siendo manual.
   - **Por verificar:** `PerformanceAdviser` en `purge_unused`; `DB.Toposolid.Create`; `WallFoundation.Create`;
     `NewOpening` en suelo o cubierta; `BasePoint.GetProjectBasePoint/GetSurveyPoint`; `ProjectPosition.Angle`;
     `doc.AcquireCoordinates`; `MoveElement` sobre puntos base; `Workset.Id.IntegerValue`;
     `GetElementOverrides(...).ProjectionLineColor.IsValid`.
   - Añade cada miembro de la API que uses en esta fase, con estado `por verificar`.
2. No toques el código de la fase 1 salvo que el usuario te pase errores reales de Revit. En ese caso, corrígelos en
   un commit propio antes de empezar.
3. Cada manejador nuevo necesita al menos una prueba de extremo a extremo en CPython con el `pyrevit` simulado de
   `tests/fakes`, como `tests/test_rutas_escritura.py` y `tests/test_correcciones_021.py`. Amplía los simuladores
   cuando haga falta. Ejecuta `uv run --with pytest python -m pytest -q` antes de cada commit; ninguna prueba
   existente puede dejar de pasar. No hagas commit de `uv.lock` si solo cambió por `--with pytest`.

### Reglas vigentes

- **Compatibilidad:** manejadores en `revit_mcp/` compatibles con IronPython 2.7 (sin f-strings, sin `pathlib`, sin
  anotaciones, sin argumentos solo por nombre). Lo comprueba `tests/test_compatibilidad_ironpython.py`.
- **Estructura de cada herramienta:** manejador con `@api.route` + `@requiere_token`, registro en `startup.py`,
  definición en `tools/` y registro en `tools/__init__.py`.
- **Escritura:** toda escritura sigue el patrón de la fase 1:
  - `ejecutar(doc, "/ruta/", request, cuerpo)`, que aporta copia, `mcp_log.jsonl`, `simular`, 409 si hay otra
    transacción, y `ms`.
  - Dentro de `cuerpo`: `EscrituraRechazada(mensaje, estado)` para los 400/404/409, `simulacion([...])` cuando
    `ctx["simular"]` es verdadero, `with transaccion(doc, "...")` (el prefijo `IA:` se añade solo) y
    `resultado_creacion(doc, ids)`, `verificar_eliminados` o `antes/despues`.
  - `comprobar_alcance` con límite de 200 elementos por llamada salvo `forzar`.
  - No llames a `preparar` ni a `registrar` a mano.
- **Unidades:** milímetros hacia fuera, pies internos dentro (`xyz_desde_mm`, `punto_a_mm`, `MM_TO_FEET`). Los
  Double de parámetros van en mm, mm², mm³ o grados (`convertir_valor`).
- **Idioma de Revit (obligatorio, lo aprendimos en la validación):**
  - Nunca busques parámetros, categorías, vistas, plantillas ni grupos por su nombre inglés visible.
  - Parámetros: `utils.buscar_por_nombre` (nombre visible, `BuiltInParameter` o alias inglés). Si añades alias,
    amplía `ALIAS_PARAMETROS`.
  - Categorías: `BuiltInCategory`.
  - Grupos de parámetros: `GroupTypeId`.
  - Tipos de dato: `SpecTypeId`.
  - Vistas de familia: por `ViewType` y nivel asociado, no por "Ref. Level".
  - Plantillas `.rft`: por nombre de archivo dentro de `Application.FamilyTemplatePath`. En un Revit en español se
    llaman distinto ("Modelo genérico métrico basado en cara.rft"). Si no se encuentra, lista las disponibles.
- **Nombres:** los nombres que se devuelven conservan las tildes (`get_element_name`). Nunca los pases a ASCII.
- **Convenciones:** herramientas con nombre en inglés y documentación en español. `execute_revit_code` sigue siendo
  el último recurso.
- **Versiones de Revit (2024-2027):**
  - Si un miembro cambia entre versiones, resuelve con `try/except` en tiempo de ejecución, como `utils.py`.
  - Las cuatro versiones tienen `AnalyticalMember`; no hace falta la ruta de `AnalyticalModel`.
  - `ForgeTypeId` está en todas.
- **Hilo de Revit:** Routes ejecuta cada llamada en el hilo de Revit, que queda bloqueado mientras dura.
  - No subas tiempos de espera a 600 s como sustituto de un límite.
  - Toda macro valida y responde con un `plan` cuando `simular=true`.
  - Toda macro aplica `comprobar_alcance` al total de elementos que va a crear.
  - Tiempo de espera largo (`TIMEOUT_LARGO`) solo en `snapshot_model`, `diff_snapshots`, `create_steel_frame`,
    `build_family_from_spec`, `family_validate`, `export_structural_model` e `import_from_civil`.

## Entrega 2a

### Bloque A. Navegación profunda

Antes de crear rutas nuevas, revisa las que ya existen: `element_properties`, `element_geometry`, `find_elements`,
`warnings`, `element_types` y `list_category_parameters`. Donde una herramienta nueva solaparía con una existente,
**amplía la existente**, sin cambiar sus campos actuales, y documéntalo.

| Herramienta | Ruta | Parámetros | Devuelve / notas |
|---|---|---|---|
| `describe_element` | POST `/describe/` | `element_id*`, `depth` (0-2), `include_geometry` | Ver el detalle debajo de la tabla. |
| `dependency_graph` | POST `/dependency_graph/` | `element_id*`, `max_nodes` (máx 500) | Nodos y aristas (`hosts`, `joins`, `depends`) listos para dibujar. |
| `query_elements` | POST `/query/` | `category`, `family`, `type_name`, `level`, `view_id`, `workset`, `phase`, `filters[]` (`{"parameter", "op", "value"}`), `bbox_min_mm`, `bbox_max_mm`, `sort_by`, `page`, `page_size` (máx 500) | Ver el detalle debajo de la tabla. |
| `schedule_to_json` | **POST** `/schedule/` | `name*` o `view_id*` | Encabezados y filas de la tabla, con `GetTableData().GetSectionData(SectionType.Body)` y `GetCellText`. Va por POST y no por GET con el nombre en la ruta, porque los nombres llevan espacios y tildes. |
| `snapshot_model` | POST `/snapshot/` | `name*`, `categories[]` | Ver el detalle debajo de la tabla. |
| `diff_snapshots` | POST `/diff_snapshots/` | `a*`, `b*` | Elementos añadidos, eliminados y modificados (con los parámetros que cambiaron), comparando por `UniqueId`. |
| — (ampliación de `/warnings/`) | GET `/warnings/` | nuevo `group_by=description` | Advertencias agrupadas por descripción con recuento, ids y una sugerencia por tipo. La sugerencia se elige por el `FailureDefinitionId` (`GetFailureDefinitionId().Guid`), no por el texto, que depende del idioma. |
| `get_view_extents` | POST `/view_extents/` | `view_id*` | Recorte, rango de vista, escala, nivel, disciplina y plantilla de vista. |

**`describe_element`**
- Devuelve categoría, familia, tipo, nivel, host, subproyecto, fase y opción de diseño.
- Parámetros de ejemplar y de tipo: nombre, valor, unidad, `is_read_only`, `is_shared` y `guid`.
- `hosted_elements[]`: `FamilyInstance.Host` inverso y `HostObject.FindInserts`.
- `joined_elements[]`: `JoinGeometryUtils.GetJoinedElements`.
- `dependents[]`: `GetDependentElements`, filtrado por categorías de modelo.
- `referenced_by[]`: cotas y etiquetas, **solo** en la vista activa; recorrer todas las vistas no escala.
- `bbox_mm`.
- Con `include_geometry`: la ubicación en mm, y el volumen y el área de los sólidos.
- Con `depth>0`: un resumen de host, hosted y joined.
- Reutiliza `parameters.valor_parametro` y `escritura.describir_elemento`.

**`query_elements`**
- Valores de `op`: `=`, `!=`, `>`, `<`, `>=`, `<=`, `contains`, `starts` y `empty`.
- Los parámetros se resuelven con `buscar_por_nombre`.
- Usa `ElementParameterFilter` cuando el parámetro es `BuiltInParameter` o compartido, y filtra en Python en el
  resto.
- Los números de `value` van en unidades del contrato.
- `find_elements` queda como alias simple que llama al mismo código.

**`snapshot_model`**
- Guarda en `<carpeta del rvt>\snapshots\<name>.json` (si el modelo no está guardado, en `%LOCALAPPDATA%\RevitMcp`).
- Por elemento: id, `UniqueId`, categoría, tipo, nivel, bbox y hash de parámetros.
- Sin transacción. Límite de elementos configurable, con aviso si se trunca.

### Bloque D. Macros de proyecto

Reutiliza `create_grid`, `create_level`, `create_sheet`, `create_toposolid` y `link_file` (su código interno, no las
llamadas HTTP).

| Herramienta | Ruta | Parámetros | Acción |
|---|---|---|---|
| `create_grid_and_levels` | POST `/grid_levels/` | `x_spacings_mm[]`, `y_spacings_mm[]`, `x_names`, `y_names`, `levels[]` (`{name, elevation_mm}`), `origin_mm` | Rejilla completa con nombres correlativos (1, 2, 3 / A, B, C) y niveles, en una transacción. Con `simular`, devuelve el plan. |
| `create_sheet_set` | POST `/sheet_set/` | `sheets[]` (`{number, name, title_block, views[] {view_name, position_mm}}`) | Planos con vistas colocadas (`Viewport.CanAddViewToSheet` antes de `Viewport.Create`). Una vista que ya está en otro plano se informa y no se coloca. |
| `import_from_civil` | POST `/import_civil/` | `file_path*` (LandXML, CSV PNEZD o DWG), `level*`, `use_shared_coordinates`, `origin_offset_mm` | Ver el detalle debajo de la tabla. |

**`import_from_civil`**
- LandXML (`Surface/Definition/Pnts`) o CSV: toposólido con el código de `topografia.py`.
- DWG: `link_file` y, si `use_shared_coordinates`, `set_project_location(acquire_from_link_id)` con las mismas
  protecciones de la fase 1 (409 salvo `forzar`).

## Entrega 2b. Bloque B: estructuras metálicas

### Lectura

| Herramienta | Ruta | Parámetros | Devuelve / notas |
|---|---|---|---|
| `list_steel_profiles` | POST `/steel_profiles/` | `standard` (`AISC`, `EN`, `todos`), `shape` (`W`, `HSS`, `L`, `C`, `WT`, `Pipe`), `loaded_only` | Ver el detalle debajo de la tabla. |
| `steel_quantities` | POST `/steel_quantities/` | `group_by` (`type`, `level`, `family`, `mark`), `element_ids` | Ver el detalle debajo de la tabla. |
| `get_structural_properties` | POST `/structural_properties/` | `element_id*` | Uso estructural, material, liberaciones de inicio y fin, justificaciones Y/Z, desfases Y/Z en mm, rotación de sección, extensiones de inicio y fin en mm, y `analyze_as`. Todo por `BuiltInParameter`. Lista en la tabla de miembros los que no puedas confirmar. |

**`list_steel_profiles`**
- Perfiles cargados: `FamilySymbol` de `OST_StructuralFraming` y `OST_StructuralColumns` cuyo material estructural
  es acero (`StructuralMaterialType.Steel`).
- Con `loaded_only=false`, además los `.rfa` de las rutas de `Application.GetLibraryPaths()`, sin suponer nombres de
  carpeta en inglés: busca `.rfa` con `.txt` de catálogo al lado. Incluye familia y tipos del catálogo.

**`steel_quantities`**
- Por grupo: recuento, longitud total en mm y peso total en kg.
- Peso = volumen (`HOST_VOLUME_COMPUTED`) × densidad del activo estructural del material
  (`PropertySetElement` → `StructuralAsset.Density`). No hay un `BuiltInParameter` estándar de masa lineal.
- Si el tipo tiene un parámetro de masa lineal, úsalo como alternativa y dilo en `metodo`.
- Devuelve los ids cuyo peso no se pudo calcular.

### Escritura (todas con `simular`)

| Herramienta | Ruta | Parámetros | Acción |
|---|---|---|---|
| `load_steel_profile` | POST `/load_steel_profile/` | `file_path*` o `family_name*` (buscado en la biblioteca), `type_names[]` | Si la familia tiene catálogo `.txt`: `doc.LoadFamilySymbol(ruta, nombre_tipo)` por cada tipo pedido. `IFamilyLoadOptions` no sirve para elegir tipos. Sin catálogo: `doc.LoadFamily` y activar los tipos pedidos. |
| `create_steel_frame` | POST `/create_steel_frame/` | `grids_x[]`, `grids_y[]` (vacío = todas), `levels[]` (vacío = todos), `column_type*`, `beam_type*`, `beam_directions` (`x`, `y`, `both`), `column_orientation_deg`, `skip_columns_at[]`, `skip_beams_at[]`, `mark_prefix` | **Macro.** Ver el detalle debajo de la tabla. |
| `create_bracing` | POST `/create_bracing/` | `start_point_mm*`, `end_point_mm*`, `brace_type*`, `pattern` (`single`, `X`, `V`, `inverted_V`, `K`), `level_bottom*`, `level_top*` | Arriostres con `StructuralType.Brace`. Los puntos intermedios se calculan a partir de los extremos y la altura entre niveles. |
| `create_truss` | POST `/create_truss/` | `truss_type*`, `start_point_mm*`, `end_point_mm*`, `level*`, `height_mm` | `DB.Structure.Truss.Create(doc, trussTypeId, sketchPlaneId, curve)` con un `SketchPlane` en el nivel. Si el tipo no existe, lista los disponibles. |
| `set_structural_properties` | POST `/set_structural_properties/` | `element_ids*` y cualquiera de las propiedades de `get_structural_properties` | Devuelve `antes/despues` por elemento, con el límite de 200. |
| `create_steel_connection` | POST `/create_steel_connection/` | `element_ids*`, `connection_type*`, `approve` | `StructuralConnectionHandler.Create`. Si las conexiones de acero no están instaladas, 409 `no_soportado` con la explicación. Incluye `list_connection_types` (GET `/connection_types/`). |
| `add_plate_or_stiffener` | POST `/add_plate/` | `host_id*`, `family_name*`, `type_name*`, `positions[]` (mm desde el inicio o fracción 0-1), `face` (`top`, `bottom`, `web`) | Familia alojada en cara: `NewFamilyInstance(reference, point, refDir, symbol)`. La referencia de la cara se obtiene de `get_Geometry` con `ComputeReferences=True`. Familia de punto: el punto sobre la curva. |
| `split_beam` | POST `/split_beam/` | `element_id*`, `at_mm[]` | `ElementTransformUtils.CopyElement` y ajuste de `LocationCurve` en cada tramo. Avisa en la respuesta de que se pierden las uniones y conexiones del original. |
| `join_steel_elements` | POST `/join_steel/` | `element_ids*`, `coping` | `JoinGeometryUtils.JoinGeometry` en cadena. Con `coping=true`, `FamilyInstance.AddCoping`. Reutiliza `/join_geometry/` si ya cubre el caso. |

**`create_steel_frame`**
- Pilares: `NewFamilyInstance(point, symbol, baseLevel, StructuralType.Column)`, con `FAMILY_TOP_LEVEL_PARAM`.
- Vigas: `NewFamilyInstance(line, symbol, level, StructuralType.Beam)` entre pilares consecutivos de cada nivel.
- Todo en una transacción, con `comprobar_alcance` sobre el total.
- Con `simular`, devuelve el `plan` con el recuento de pilares y vigas.
- `creados` se agrupa en `columns` y `beams`.

### Modelo analítico

| Herramienta | Ruta | Parámetros | Acción |
|---|---|---|---|
| `analytical_status` | POST `/analytical_status/` | `element_ids` (vacío = todo, con límite) | Por elemento: `AnalyticalMember` asociado (`AnalyticalToPhysicalAssociationManager`), nodos extremos en mm, elementos conectados, `is_connected` y nodos sueltos. |
| `fix_analytical_alignment` | POST `/fix_analytical/` | `element_ids*`, `tolerance_mm` | `AnalyticalMember.SetCurve` hacia nodos cercanos dentro de la tolerancia, con `antes/despues`. |
| `export_structural_model` | POST `/export_structural/` | `format*` (`ifc_structural`, `csv_nodes_members`), `file_path*` | IFC: reutiliza `/export_ifc/` con opciones de vista analítica. CSV: id, tipo, perfil, material, nodo i, nodo j y liberaciones. |

## Entrega 2c. Bloque C: editor de familias

Reglas propias de este bloque:

- **Documento de familia:**
  - Identifícalo por `family_doc` (título) entre `app.Documents` con `IsFamilyDocument`.
  - Guarda en un diccionario del módulo los documentos que abre el MCP, para cerrarlos después.
  - `doc.EditFamily` no se puede llamar con una transacción abierta en el proyecto (409), ni sobre familias del
    sistema o in situ (`Family.IsInPlace`, `Family.IsEditable`): 400.
- **Escritura:**
  - `escritura.ejecutar` está pensado para el proyecto (copia del `.rvt`, 409 por `IsModifiable`).
  - Para el documento de familia, añade a `escritura.py` una variante que registre en `mcp_log.jsonl` del
    proyecto, compruebe `IsModifiable` sobre el documento de familia y no copie `.rfa` sin guardar.
  - Cada cambio va en su propia `transaccion(doc_familia, ...)`.
- **API 2024+:**
  - `FamilyManager.AddParameter(nombre, GroupTypeId, SpecTypeId, is_instance)`, y para `family_type` la sobrecarga
    con `Category`.
  - Los grupos del `spec` se dan como `GroupTypeId` (`"Geometry"`, `"Materials"`, `"Data"`...), no como texto visible.
- **Referencias de caras** (`NewAlignment`, cotas): obtenlas con `Options.ComputeReferences = True` y
  `IncludeNonVisibleObjects = True`, en una vista donde el plano y la cara sean visibles.

| Herramienta | Ruta | Parámetros | Acción |
|---|---|---|---|
| `family_new` | POST `/family/new/` | `template*` (archivo `.rft` o ruta), `name*` | `Application.NewFamilyDocument`. Devuelve `family_doc`, categoría, planos de referencia y vistas (id, `ViewType`, nombre). |
| `family_open` | POST `/family/open/` | `file_path*` o `family_name*` (`doc.EditFamily`) | Resumen: parámetros, tipos, planos de referencia y sólidos. |
| `family_info` | POST `/family/info/` | `family_doc*` | Ver el detalle debajo de la tabla. |
| `family_add_parameter` | POST `/family/parameter/` | `family_doc*`, `name*`, `data_type*` (`length`, `number`, `integer`, `text`, `yes_no`, `material`, `angle`, `area`, `volume`, `family_type:<BuiltInCategory>`), `group*` (`GroupTypeId`), `is_instance`, `formula`, `shared_parameter_guid` | `AddParameter` y, si se pide, `SetFormula`. Un parámetro compartido se busca por GUID en el archivo de parámetros compartidos activo. |
| `family_add_reference_plane` | POST `/family/reference_plane/` | `family_doc*`, `name*`, `origin_mm*`, `direction*`, `view` (id o `ViewType`+nivel), `is_reference` | `NewReferencePlane`, con `Name` y el parámetro `ELEM_REFERENCE_NAME` o equivalente para `IsReference`. |
| `family_add_dimension_with_label` | POST `/family/dimension/` | `family_doc*`, `reference_plane_names[]*`, `parameter_name*`, `view`, `equal` | `NewDimension` y `Dimension.FamilyLabel`. Con `equal`, `AreSegmentsEqual`. |
| `family_create_extrusion` | POST `/family/extrusion/` | `family_doc*`, `profile[]`, `sketch_plane*`, `start_mm*`, `end_mm*`, `is_void`, `lock_ends_to`, `material_parameter` | `FamilyItemFactory.NewExtrusion`, `NewAlignment` y `AssociateElementParameterToFamilyParameter`. |
| `family_create_sweep` / `revolve` / `blend` | POST `/family/sweep/`, `/family/revolve/`, `/family/blend/` | Como el prompt original | `NewSweep`, `NewRevolution`, `NewBlend`. |
| `family_lock_face_to_plane` | POST `/family/lock/` | `family_doc*`, `solid_id*`, `face`, `reference_plane_name*`, `view` | `NewAlignment` entre la cara y el plano. |
| `family_set_type_values` | POST `/family/type/` | `family_doc*`, `type_name*`, `values`, `create_if_missing` | `NewType` si falta, `CurrentType` y `Set`. Los valores van en unidades del contrato. |
| `family_add_connector` | POST `/family/connector/` | `family_doc*`, `domain*` (`hvac`, `piping`, `electrical`), `face_of_solid_id*`, `face`, `system_type`, `size_mm` | `ConnectorElement.CreateDuctConnector`, `CreatePipeConnector` o `CreateElectricalConnector`. El dominio estructural no existe; no lo ofrezcas. |
| `family_validate` | POST `/family/validate/` | `family_doc*`, `flex_cases[]`, `restore` | **Macro.** Ver el detalle debajo de la tabla. |
| `family_save` | POST `/family/save/` | `family_doc*`, `file_path*`, `overwrite` | `SaveAs` con `SaveAsOptions.OverwriteExistingFile`. Sin `overwrite`, 409 si el archivo existe. |
| `family_load_into_project` | POST `/family/load/` | `family_doc*` o `file_path*`, `overwrite_parameters` | `LoadFamily(projectDoc, IFamilyLoadOptions)`. Sin `overwrite_parameters`, 409 si la familia ya está cargada. |
| `family_close` | POST `/family/close/` | `family_doc*`, `save` | `Close(save)` solo sobre documentos abiertos por el MCP y que no sean el documento activo. |
| `build_family_from_spec` | POST `/family/build/` | `spec*`, `save_path`, `load_into_project`, `simular` | **Macro.** Ver el detalle debajo de la tabla. |

**`family_info`**
- Parámetros: nombre, tipo de dato, grupo, ejemplar o tipo, y fórmula.
- Tipos, planos de referencia, geometría con sus restricciones, y conectores.

**`family_validate`**
- Cada caso va dentro de un `TransactionGroup`: `Set`, `doc.Regenerate()`, y comprobar que cada sólido sigue
  teniendo volumen > 0. `RollBack` del grupo si `restore`.
- Los errores de regeneración **no** aparecen en `doc.GetWarnings()`. Llegan como fallos al preprocesador
  (`_FailureSwallower` y `ULTIMOS_ERRORES`) o como excepción en `Regenerate`/`Commit`.
- Devuelve los casos que fallan y el motivo.

**`build_family_from_spec`**
- Valida el `spec` completo antes de abrir nada:
  - los planos referenciados existen;
  - las fórmulas solo usan parámetros definidos;
  - los tipos usan parámetros válidos;
  - la plantilla existe en `FamilyTemplatePath`;
  - las vistas se resuelven por `ViewType`.
- Después ejecuta en orden: `family_new` → parámetros → planos → cotas con etiqueta → sólidos y vaciados → bloqueos
  → tipos → `family_validate` con un caso por tipo → guardar → cargar.
- Ante cualquier fallo, cierra sin guardar y devuelve el paso que falló.
- Con `simular`, devuelve el plan de pasos.

**Formato de `spec`**
- Como el prompt original, con estos cambios:
  - `template` es un nombre de archivo que se busca en `FamilyTemplatePath`, o una ruta.
  - `category` es un `BuiltInCategory` (`OST_StructConnections`).
  - `group` es un `GroupTypeId` (`Geometry`).
  - `view` y `sketch_plane` se dan como `{"view_type": "FloorPlan", "level": "Ref. Level"}`, donde el nivel se
    resuelve por el primer `Level` de la familia si el nombre no coincide.
  - Todo parámetro usado en `material_parameter` debe estar declarado en `parameters`. En el ejemplo original,
    "Material placa" no lo estaba.
- En `CONTRATO.md`, dos ejemplos completos: la placa base con cuatro agujeros y un perfil W paramétrico.

## Instrucciones del agente

Añade a `INSTRUCCIONES_AGENTE.md`, en la entrega correspondiente:

- **Flujo de estructura metálica:**
  1. `list_levels`, y rejillas con `query_elements(category="OST_Grids")`.
  2. `list_steel_profiles(loaded_only=true)`; si falta un perfil, `load_steel_profile`.
  3. `create_steel_frame(simular=true)` y mostrar el recuento al usuario.
  4. Tras su confirmación, ejecutar.
  5. `set_structural_properties` para las liberaciones.
  6. `analytical_status` y corregir los nodos sueltos.
  7. `steel_quantities` como comprobación.
- **Flujo de familia:**
  1. `family_info` de una familia parecida, si existe.
  2. Redactar el `spec` y mostrar al usuario un resumen (parámetros, sólidos, tipos).
  3. `build_family_from_spec(simular=true)` y, tras su confirmación, ejecutar.
  4. `family_validate` con valores extremos.
  5. Cargar en el proyecto.
- **Reglas:**
  - Nunca editar una familia del sistema o in situ.
  - Nunca sobrescribir una familia cargada sin `overwrite_parameters` explícito del usuario.
  - Nunca usar nombres visibles en inglés para categorías, vistas o plantillas.
- **Glosario adicional:** plano de referencia = `ReferencePlane`, etiqueta de cota = `FamilyLabel`,
  extrusión/barrido/revolución/fundido = `Extrusion/Sweep/Revolution/Blend`, vaciado = `Void`,
  bloquear = `Alignment/Lock`, flexionar = `Flex`, liberación = `Release`, justificación = `Justification`,
  arriostre = `Brace`, cercha = `Truss`, rigidizador = `Stiffener`, placa base = `Base plate`,
  modelo analítico = `Analytical model`, nodo = `Analytical node`.

## Pruebas

**`tests/` (CPython, sin Revit), en cada entrega:**
- Una prueba de extremo a extremo por ruta nueva: 401 sin token, `simular` sin transacción, 400/404 controlados, y
  `creados` o `antes/despues`.
- 2a: el parseo de `query_elements.filters` (cada `op`, unidades, parámetro por alias).
- 2c: la validación del `spec` (planos inexistentes, fórmulas con parámetros no definidos, tipos con parámetros
  desconocidos, plantilla inexistente: errores claros).

**`pruebas/probar_revit.py`:** añade una sección por entrega que se active con `--fase 2a|2b|2c`. Sin ese
argumento, solo las 9 pruebas actuales. Debe funcionar en un Revit en español; ninguna comprobación puede depender
de nombres visibles en inglés.
- **2a:**
  1. `describe_element` de un muro devuelve `parameters`, `bbox_mm` y `hosted_elements`.
  2. `query_elements` con `op=contains` sobre `Mark` pagina bien.
  3. `snapshot_model` antes y después de crear un nivel, y `diff_snapshots` lista exactamente ese nivel.
  4. `create_grid_and_levels(simular=true)` no crea nada.
- **2b:**
  1. `create_steel_frame(simular=true)` devuelve el recuento y no crea nada.
  2. `create_steel_frame` real crea pilares y vigas, y `analytical_status` no reporta nodos sueltos.
- **2c:**
  1. `build_family_from_spec` con el ejemplo de la placa base crea el `.rfa`.
  2. `family_validate` pasa en sus dos tipos.
  3. La familia se carga en el proyecto.

## Entrega

En cada entrega:
- `CONTRATO.md`, `README.md` y `LLM.txt` actualizados con el bloque, con un ejemplo `curl` por ruta de escritura.
- La versión de la tabla en `pyproject.toml` y en `revit_mcp/__init__.py`.
- `herramientas-dev/miembros_por_verificar_revit.md` al día.

Resumen final con:
- las herramientas añadidas y las rutas existentes ampliadas;
- los miembros de la API no verificados, por versión (2024-2027);
- lo que quedó como `no_soportado` y su motivo;
- el prompt de validación para el agente local, con el mismo formato que el de la 0.2.2: pasos numerados, respuesta
  esperada por paso, e informe final en tabla con OK/FALLO y respuestas literales.
