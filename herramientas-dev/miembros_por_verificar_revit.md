# Miembros de la API de Revit por verificar

Registro de los miembros de la API de Revit (y de la biblioteca de IronPython) que usa el conector y de si se han
comprobado ya dentro de Revit. Se actualiza en cada entrega: todo miembro nuevo entra como `por verificar` y pasa
a `verificado` cuando `pruebas/probar_revit.py` (o una prueba manual anotada en `REVISION_FASE1.md`) lo ejecuta
en un Revit real. Un miembro `no existe` se retira del código o se deja con `try/except` y ruta alternativa.

Estados: `verificado` (ejecutado en Revit 2027 en español, 0.2.2 o 0.3.0), `no existe`, `por verificar`.

Pendiente tras la validación de la 0.3.0 (27/09/2026): `Grid.Create` (la herramienta MCP rechazaba `x_names` como texto; corregido en 0.3.1), `ScheduleSheetInstance.Create`, todo lo de DWG en `/import_civil/` (sin archivo de prueba), LandXML, `View3D.GetSectionBox`, filtros por `workset`, `phase` y `bbox`.

## Estado de partida (fase 1, versión 0.2.2)

| Tipo | Miembro | Versión mínima | Ruta que lo usa | Estado |
|---|---|---|---|---|
| método | `Document.Create.NewOpening(Wall, XYZ, XYZ)` | 2011 | `/create_opening/` (muro) | verificado |
| propiedad | `Request.query_params` (pyRevit Routes) | pyRevit 4.8 | todas las rutas GET (`?token=`) | verificado |
| enumeración | `TransactionStatus` (`Committed`) | 2011 | `escritura.transaccion` | verificado |
| constructor | `ElementId(System.Int64)` | 2024 | `utils.make_element_id` | verificado |
| propiedad | `ElementId.Value` | 2024 | `utils.get_element_id_value` | verificado |
| método | `Element.LookupParameter(str)` | 2015 | `utils.buscar_por_nombre` | verificado |
| método | `Element.get_Parameter(BuiltInParameter)` | 2011 | `utils.buscar_por_nombre`, `escritura.nombre_nivel` | verificado |
| método | `Definition.GetDataType()` | 2022 | `parameters.factor_a_interno` | verificado |
| propiedad | `SpecTypeId.Length` (y `Area`, `Volume`, `Angle`) | 2021 | `parameters.factor_a_interno` | verificado |
| método | `Document.GetUndoName()` | — | `pruebas/probar_revit.py` prueba 9 | no existe (la prueba 9 sigue siendo manual) |
| clase | `PerformanceAdviser` (`GetPerformanceAdviser`, `GetAllRuleIds`, `ExecuteRules`) | 2012 | `/purge_unused/` | verificado (0.4.0, `maintain_model(action="purge", simular=true)`: 371 candidatos) |
| método | `Toposolid.Create(Document, IList<XYZ>, ElementId, ElementId)` (y la sobrecarga con `CurveLoop`) | 2024 | `/create_toposolid/` | verificado (0.3.0 en Revit 2027 es, `/import_civil/` CSV) |
| método | `WallFoundation.Create(Document, ElementId, ElementId)` | 2014 | `/create_foundation/` (corrida) | por verificar |
| método | `Document.Create.NewOpening(Element, CurveArray, bool)` | 2011 | `/create_opening/` (suelo, cubierta, techo) | por verificar |
| método | `BasePoint.GetProjectBasePoint(Document)` / `BasePoint.GetSurveyPoint(Document)` | 2022 | `coordenadas._punto_base` (reserva por categoría si faltan) | verificado (0.4.2 en Revit 2027 es: `get_revit_model_info(include=["location"])` con los dos puntos) |
| propiedad | `ProjectPosition.Angle` | 2011 | `coordenadas.norte_verdadero_grados`, `/set_project_location/` | verificado (0.4.2 en Revit 2027 es: norte -123.0931 → -113.0931 → -123.0931) |
| método | `Document.AcquireCoordinates(ElementId)` | 2018 | `/set_project_location/` (`acquire_from_link_id`) | por verificar |
| método | `ElementTransformUtils.MoveElement` sobre `BasePoint` | 2012 | `/set_project_location/` | por verificar |
| propiedad | `WorksetId.IntegerValue` | 2013 | `consulta._workset_id` (reserva `Value`) | no aplica en Modelo_Copia (no es de trabajo compartido) |
| propiedad | `OverrideGraphicSettings.ProjectionLineColor` + `Color.IsValid` | 2013 | `/color_splash/`, `/clear_colors/` | verificado (0.4.2 en Revit 2027 es: 68 muros coloreados y limpiados, `con_color` 0) |

## Entrega 2a (0.3.0): Bloque A, navegación profunda

| Tipo | Miembro | Versión mínima | Ruta que lo usa | Estado |
|---|---|---|---|---|
| método | `Element.GetDependentElements(ElementFilter)` (con `None`) | 2018 | `/describe/`, `/dependency_graph/` | verificado (0.3.0 en Revit 2027 es, `/describe/`, `/dependency_graph/`) |
| método | `HostObject.FindInserts(bool, bool, bool, bool)` | 2011 | `/describe/`, `/dependency_graph/` | verificado (0.3.0 en Revit 2027 es, `/describe/`) |
| propiedad | `FamilyInstance.Host`, `FamilyInstance.SuperComponent` | 2011 | `/describe/`, `/dependency_graph/` | verificado (0.3.0 en Revit 2027 es, `/dependency_graph/`) |
| método | `JoinGeometryUtils.GetJoinedElements(Document, Element)` | 2012 | `/describe/`, `/dependency_graph/` | verificado (0.3.0 en Revit 2027 es, `/dependency_graph/` 32 aristas) |
| propiedad | `Category.CategoryType` (`CategoryType.Model`) | 2011 | `/describe/`, `/dependency_graph/`, `/snapshot/` | verificado (0.3.0 en Revit 2027 es, `/describe/`, `/snapshot/`) |
| propiedad | `Element.UniqueId`, `Element.ViewSpecific` | 2011 | `/describe/`, `/snapshot/` | verificado (0.3.0 en Revit 2027 es, `/snapshot/` 2746 elementos) |
| propiedad | `Dimension.References` y `Reference.ElementId` | 2011 | `/describe/` (`referenced_by`) | verificado (0.4.3 en Revit 2027 es: la cota 615549 aparece en `referenced_by` del muro 165465) |
| método | `IndependentTag.GetTaggedLocalElementIds()` (reserva `TaggedLocalElementId`) | 2022 | `/describe/` (`referenced_by`) | por verificar |
| método | `Element.GetOrderedParameters()` | 2015 | `/describe/` | verificado (`/element_properties/`) |
| propiedad | `Parameter.IsShared`, `Parameter.GUID` | 2011 | `/describe/`, `/query/` (proveedor por compartido) | por verificar |
| propiedad | `InternalDefinition.BuiltInParameter` | 2011 | `/describe/` (`builtin`), `/query/` (proveedor) | verificado (0.3.0 en Revit 2027 es, `builtin: ALL_MODEL_MARK` en 2a.1) |
| propiedad | `Element.CreatedPhaseId` | 2011 | `/query/` (`phase`) | verificado (0.4.3 en Revit 2027 es: `phase="Fase 1"` 68 muros, `"Fase 3"` 0; 68 = total de muros del modelo) |
| constructor | `FilteredElementCollector(Document, ElementId vista)` | 2011 | `/query/` (`view_id`), `/describe/` | verificado (`/current_view_elements/`) |
| clase | `ElementParameterFilter(FilterRule)` + `ParameterValueProvider(ElementId)` | 2011 | `/query/` (`filters` nativos) | verificado (0.3.0 en Revit 2027 es, `Length > 3000` nativo) |
| constructor | `FilterStringRule(provider, evaluator, string)` (3 argumentos; el 4.º `caseSensitive` desapareció en 2023) | 2023 | `/query/` | por verificar |
| clase | `FilterDoubleRule`, `FilterIntegerRule`, `FilterElementIdRule` | 2011 | `/query/` | verificado (0.3.0 en Revit 2027 es, `FilterDoubleRule` con CURVE_ELEM_LENGTH; las otras dos por verificar) |
| clase | `FilterStringEquals`, `FilterStringContains`, `FilterStringBeginsWith`, `FilterNumericEquals/Greater/GreaterOrEqual/Less/LessOrEqual` | 2011 | `/query/` | verificado (0.3.0 en Revit 2027 es, `FilterNumericGreater`; los de texto solo via 2a.2 sin confirmar la rama nativa) |
| clase | `BoundingBoxIntersectsFilter(Outline)` | 2011 | `/query/` (`bbox_min_mm`/`bbox_max_mm`) | verificado (0.4.2 en Revit 2027 es: la caja del muro 151574 devuelve 151574 y 152894) |
| clase | `ElementLevelFilter(ElementId)` | 2011 | `/query/` (`level` sin categoría), `/find_elements/` | verificado (0.3.0 en Revit 2027 es, `level: Zapata B.O`) |
| clase | `ElementWorksetFilter(WorksetId)` + constructor `WorksetId(int)` | 2013 | `/query/` (`workset`) | no aplica en Modelo_Copia (no es de trabajo compartido) |
| clase | `ElementMulticategoryFilter(IList<BuiltInCategory>)` | 2014 | `/query/` (`category` como lista) | verificado (0.4.2 en Revit 2027 es: `["OST_Walls", "OST_Floors"]`, 84 elementos) |
| método | `FailureMessage.GetFailureDefinitionId().Guid` | 2011 | `/warnings/?group_by=description` | verificado (0.3.0 en Revit 2027 es, 15 grupos, 339 avisos) |
| propiedades | `BuiltInFailures.OverlapFailures.WallsOverlap`, `.DuplicateInstances`, `.WallRoomSeparationOverlap`, `.RoomSeparationLinesOverlap`, `.FloorsOverlap`; `RoomFailures.RoomNotEnclosed`, `.RoomNotInPlaced`, `.RoomsInSameRegion`; `JoinElementsFailures.CannotKeepJoined`, `.CannotKeepJoinedWarning`; `InaccurateFailures.InaccurateLine`, `.InaccurateWall`, `.InaccurateBeamOrBrace`, `.InaccurateGrid`, `.InaccurateRefPlane`; `GeneralFailures.DuplicateValue`; `WallFailures.WallNotAttached`; `AreaFailures.AreaNotEnclosed` | 2012 | `/warnings/?group_by=description` (los nombres que no existan se ignoran; anotar cuáles) | verificado (0.3.0 en Revit 2027 es, 5 resueltos; 9 GUID sin miembro anadidos a `SUGERENCIAS_POR_GUID`) |
| método | `ViewSchedule.GetTableData()`, `TableData.GetSectionData(SectionType.Body)`, `TableSectionData.NumberOfRows/NumberOfColumns` | 2014 | `/schedule/` | verificado (`/create_schedule/` lee `NumberOfRows`) |
| método | `ViewSchedule.GetCellText(SectionType, int, int)` (fila 0 del cuerpo = encabezados si `ShowHeaders`) | 2014 | `/schedule/` | verificado (0.3.0 en Revit 2027 es, `/schedule/` Sardineles y veredas, fila 0 = encabezados) |
| método | `ScheduleDefinition.GetFieldCount()`, `GetField(int)`, `ShowHeaders`, `IsItemized`, `ShowGrandTotal`; `ScheduleField.GetName()`, `ColumnHeading`, `IsHidden` | 2014 | `/schedule/` | verificado (0.3.0 en Revit 2027 es, `/schedule/`) |
| propiedad | `View.CropBox` (`BoundingBoxXYZ.Transform`), `CropBoxActive`, `CropBoxVisible`, `Scale`, `Discipline`, `DetailLevel`, `ViewTemplateId`, `ViewPlan.GenLevel` | 2013 | `/view_extents/` | verificado (0.3.0 en Revit 2027 es, `/view_extents/` planta NPT +30.40) |
| método | `ViewPlan.GetViewRange()`, `PlanViewRange.GetLevelId/GetOffset(PlanViewPlane)`, constantes `PlanViewRange.Unlimited/Current/LevelAbove/LevelBelow` | 2014 | `/view_extents/` | verificado (0.3.0 en Revit 2027 es, corte +1200, superior +2300) |
| método | `View3D.IsSectionBoxActive`, `View3D.GetSectionBox()` | 2011 | `/view_extents/` | por verificar |
| enumeración | `BuiltInParameter.VIEWER_SHEET_NUMBER`, `BuiltInParameter.VIEW_PHASE` | 2011 | `/view_extents/` (`sheet_number`, `phase`) | verificado (0.3.0 en Revit 2027 es, `---`, `Fase 1`) |
| módulo | `hashlib.md5` (biblioteca de IronPython 2.7; reserva `zlib.crc32`) | IronPython 2.7 | `/snapshot/` (hash de parámetros) | verificado (0.3.0 en Revit 2027 es, `/snapshot/` y `/diff_snapshots/`) |
| expresión | `int(BuiltInCategory)` comparado con `get_element_id_value(Category.Id)` | 2011 | `/snapshot/` (niveles y rejillas por defecto) | verificado (0.3.0 en Revit 2027 es, diff detecta el nivel nuevo en 2a.3) |
| método | `FilteredElementCollector(doc).WhereElementIsNotElementType()` sin más filtros (recorrido completo) | 2011 | `/snapshot/` por defecto, `/query/` sin criterios nativos | verificado (`/model_statistics/`) |
| constructor | `ElementId(BuiltInParameter)` para `ParameterValueProvider` | 2011 | `/query/` (`navegacion.regla_nativa`) | verificado (0.3.0 en Revit 2027 es, `/query/` nativo) |
| propiedad | `Solid.Volume`, `Solid.SurfaceArea`, `Solid.ComputeCentroid()` | 2011 | `/describe/` (`include_geometry`) | verificado (0.3.0 en Revit 2027 es, `geometry` en 2a.1) |
| propiedad | `Document.Phases` (`PhaseArray`) | 2011 | `/query/` (`phase` por nombre) | verificado (0.4.2 en Revit 2027 es: `include=["phases"]`, 5 fases; `phase` por nombre sin error) |
| método | `TableView.GetCellText(SectionType.Header, 0, 0)` (título de la tabla) | 2013 | `/schedule/` | verificado (0.4.2 en Revit 2027 es: título "Sardineles y veredas") |

## Entrega 2a (0.3.0): Bloque D, macros de proyecto

| Tipo | Miembro | Versión mínima | Ruta que lo usa | Estado |
|---|---|---|---|---|
| método | `Grid.Create(Document, Line)` + `Grid.Name` (rechaza nombres repetidos) | 2011 | `/grid_levels/` (`structure.crear_rejilla`), `/create_grid/` | verificado (0.3.1 en Revit 2027 es: V1 V2 V3 H1 H2, 115 ms) |
| método | `Level.Create(Document, double)` + `Level.Name` | 2011 | `/grid_levels/` (`building.crear_nivel`), `/create_level/` | verificado (0.3.0 en Revit 2027 es, 2a.3b) |
| método | `ViewSheet.Create(Document, ElementId cajetín)`, `ViewSheet.SheetNumber`, `View.Name` | 2013 | `/sheet_set/` (`documentation.crear_plano`), `/create_sheet/` | verificado (0.3.0 en Revit 2027 es, `/sheet_set/` MCP-01) |
| método | `Viewport.CanAddViewToSheet(Document, ElementId, ElementId)` y `Viewport.Create(Document, ElementId, ElementId, XYZ)` | 2014 | `/sheet_set/` | verificado (0.3.0 en Revit 2027 es, 2 vistas colocadas) |
| propiedad | `Viewport.ViewId`, `Viewport.SheetId` | 2014 | `/sheet_set/` (vistas ya colocadas) | verificado (0.3.0 en Revit 2027 es, `/sheet_set/`) |
| método | `ScheduleSheetInstance.Create(Document, ElementId, ElementId, XYZ)`, `ScheduleSheetInstance.ScheduleId`, `.OwnerViewId` | 2014 | `/sheet_set/` (tablas) | verificado (0.4.2 en Revit 2027 es: tabla colocada en el plano MCP-99) |
| método | `Element.get_BoundingBox(ViewSheet)` del cajetín para centrar la vista | 2011 | `/sheet_set/` (`position_mm` omitido) | verificado (0.3.0 en Revit 2027 es, centro 422.5 x 296) |
| método | `Document.Link(string, DWGImportOptions, View, out ElementId)` | 2011 | `/import_civil/` (`interop.vincular_cad`), `/link_file/` | por verificar |
| método | `Document.Link(string, DGNImportOptions, View, out ElementId)` (`.dgn`; la sobrecarga DWG no lo admite) | 2011 | `/import_civil/`, `/link_file/` con `.dgn` | por verificar |
| método | `Document.Regenerate()` antes de `AcquireCoordinates` sobre el vínculo recién creado | 2011 | `/import_civil/` (`use_shared_coordinates`) | por verificar |
| propiedad | `BasePoint.Pinned`, `BasePoint.Clipped` (409 si el punto base está fijado o recortado) | 2011 | `/import_civil/` (`coordenadas.comprobar_puntos_base_libres`), `/set_project_location/` | verificado (0.4.2 en Revit 2027 es: lectura: punto base `fijado` false, punto de replanteo `recortado` true) |
| propiedad | `DWGImportOptions.Placement` = `ImportPlacement.Origin` / `Centered` / `Shared` / `Site` | 2011 | `/import_civil/` (`placement`), `/link_file/` | por verificar |
| método | `Document.AcquireCoordinates(ElementId)` sobre un `ImportInstance` (DWG vinculado) | 2018 | `/import_civil/` (`use_shared_coordinates`) | por verificar (el caso RVT ya estaba pendiente) |
| propiedad | `ProjectLocation.GetProjectPosition(XYZ)` → `EastWest`, `NorthSouth`, `Elevation`, `Angle` | 2011 | `/import_civil/` (409 si ya hay coordenadas compartidas) | por verificar |
| método | `ElementTransformUtils.MoveElement` sobre un `ImportInstance` | 2012 | `/import_civil/` (`origin_offset_mm` con DWG) | por verificar |
| propiedad | `ViewPlan.GenLevel` para elegir la planta del nivel | 2011 | `/import_civil/` (vista de colocación) | verificado (0.3.0 en Revit 2027 es, `/view_extents/` level) |
| formato | LandXML: `<Units><Metric linearUnit>` y `<P>` en orden norte-este-cota (Y X Z) | LandXML 1.2 | `/import_civil/` (`macros.leer_landxml`) | por verificar con un archivo real de Civil 3D |
| propiedad | `Level.ProjectElevation` (origen interno; `Level.Elevation` es la mostrada según la Base de elevación del tipo) | 2014 | `utils.elevacion_interna`: `/create_level/`, `/grid_levels/`, `/list_levels/`, pilares, zapatas, vigas y MEP | verificado (0.3.2 en Revit 2027 es: 99000 interno / 117450 mostrado; `/list_levels/` Zapata B.O -4600 / 13850) |

## Consolidación (0.4.0): lotes, macros propias y rendimiento

Validada en Revit el 28/09/2026 con `herramientas-dev/VALIDACION_CONS.md`: `probar_revit.py --fase cons` 15/15 y 29
de 31 pasos como se esperaba. Los dos que no: el paso 25 (la matriz se rechazaba porque el muro estaba fijado;
corregido en 0.4.1, `copy` y `array` admiten elementos fijados) y el paso 27 (el modelo no tiene familias de etiqueta
de muro, así que `annotate(kind="tag")` responde 400; es lo correcto). En esa sesión `parameter_label` y las categorías
de `creados` salieron en inglés (`Comments`, `Walls`), no en español como en la 0.2.2; falta confirmar el idioma con
el que se abrió Revit.

Validación de la 0.4.1 (28/09/2026, Revit 2027 `English_USA`): Revit se abre en inglés (el script de arranque usa la
asociación de archivos de Windows), así que las etiquetas en español siguen sin probarse en esta entrega. La matriz y
la copia de un muro fijado funcionan y las copias salen sin fijar; `move` responde 400 sin mover el muro. `read_log`
dio 500 con `UnicodeDecodeError` en `io.open(..., encoding="utf-8").readlines()`: corregido con
`utils.leer_texto_utf8`, que lee en binario y decodifica en bloque.

0.4.2 (sin probar aún en Revit): `color_elements` acepta `OST_Walls` o el alias `walls` además del nombre visible
(antes `"Walls"` daba 404 en un Revit en español) y `query_elements` admite `category` como lista en el esquema MCP.
Lo que queda por verificar se prueba con `herramientas-dev/VALIDACION_PENDIENTES.md`.

Validación de pendientes (0.4.2, 28/09/2026, Revit 2027 en español con `/language ESP`): 22 pasos OK, el menú Deshacer OK (manual),
1 no aplica (subproyectos) y un fallo, la cota entre muros (corregida en 0.4.3; el paso 21 quedó sin probar). Confirmados en español los nombres visibles
(`Comentarios`, `Muros`, `Techos`, `Planos`...), el menú Deshacer (13 entradas `IA: ...`, sin entradas por elemento) y la
copia diferida.

Validación de la 0.4.3 (28/09/2026, `VALIDACION_043.md`, Revit 2027 en español): 9 de 9. La cota entre muros
funciona y aparece en `referenced_by`; el filtro por fase devuelve los 68 muros en "Fase 1" y 0 en "Fase 3".

| Tipo | Miembro | Versión mínima | Ruta que lo usa | Estado |
|---|---|---|---|---|
| método | `Ceiling.Create(Document, IList<CurveLoop>, ElementId tipo, ElementId nivel)` + clase `CeilingType` | 2022 | `/create_elements/` y `/create_surface/` (kind `ceiling`; reserva: suelo como en 0.3.x) | verificado (0.4.2 en Revit 2027 es: techo "Simple", categoría "Techos", sin reserva como suelo) |
| enumeración | `BuiltInParameter.CEILING_HEIGHTABOVELEVEL_PARAM` (desfase del techo) | 2011 | `building.crear_superficie` (techo con `offset`) | verificado (0.4.2 en Revit 2027 es: "Desfase de altura desde nivel" = 2700) |
| método | `ElementTransformUtils.CopyElement` repetido con `vector * i` en una transacción, también sobre un elemento fijado (0.4.1) | 2012 | `/transform_elements/` (`operation=array`) | verificado (0.4.1 en Revit 2027: `array` count 3 y `copy` sobre el muro fijado 151574; las copias salen con `pinned` false) |
| método | `Element.GetOrderedParameters()` en cada elemento de la instantánea (antes `Parameters`) | 2015 | `/snapshot/` (`instantaneas._parametros_de`) | verificado (0.4.0: `parametros_ms` 950 en 2745 elementos; `total_ms` 3612 frente a 8200 en 0.3.1) |
| método | `Element.get_BoundingBox(None)` solo con `include_bbox` | 2011 | `/snapshot/` | verificado (0.4.0: `total_ms` 2241 sin bbox frente a 3612 con bbox; `bbox_ms` 77 y 2, la diferencia está sobre todo en `escritura_ms`: 1729 y 916) |
| módulo | `threading.Thread` + `System.IO.File.Copy` desde un hilo que no es el de Revit (solo E/S de archivos) | IronPython 2.7 | `escritura.CopiaDiferida` (todas las escrituras salvo `RUTAS_COPIA_SINCRONA`) | verificado (0.4.2 en Revit 2027 es: primera escritura de la sesión: `reutilizada` false, `ms` 196, copia de 41.316.352 bytes, igual que el .rvt) |
| módulo | `imp.load_source(nombre, ruta)` para cargar `macro.py` y recargarlo al cambiar el `mtime` | IronPython 2.7 | `/macros/run/` (`macros_usuario.cargar_modulo`) | verificado (0.4.0: `reloaded` true tras editar `comentarios_por_nivel/macro.py`) |
| propiedad | `ViewSheet.SheetNumber` asignada dos veces (número temporal y definitivo) para evitar duplicados | 2013 | macro de ejemplo `numerar_planos` | verificado (0.4.2 en Revit 2027 es: `numerar_planos`: MCP-99 → MCP-07) |
| argumento | `uidoc` inyectado por Routes en `/macros/run/` (`run(doc, uidoc, args, api)`) | pyRevit 4.8 | `/macros/run/` | verificado (0.4.0: `run_macro` se ejecuta con la firma de Routes; las macros de ejemplo no lo usan) |
| parámetro | `BuiltInParameter.VIEWER_SHEET_NUMBER` como `fields` de `/query/` sobre `OST_Views` (`---` = sin plano) | 2011 | herramienta `list_views(on_sheet=...)` | verificado (0.4.0: `list_views(view_type="floor_plans", on_sheet=false)`, 13 plantas) |
| método | `Category.Id` comparado con `int(BuiltInCategory)` para resolver `OST_...` en `/list_category_parameters/` | 2011 | herramienta `list_types(with_parameters=true)` | verificado (0.4.0: `list_types(category="OST_Walls", with_parameters=true)` con `category_parameters`) |
| módulo | `io.open(ruta, "r", encoding="utf-8")` en IronPython 2.7 | IronPython 2.7 | antes: `/log/` (`read_log`), `/diff_snapshots/`, manifiestos de macros, CSV y LandXML | no fiable: falla con `UnicodeDecodeError` si un carácter de dos bytes cae entre dos trozos (0.4.1, `read_log` con la ruta `Antón`); sustituido por `utils.leer_texto_utf8` (bytes + `decode` en bloque), por verificar en Revit |
| método | `HostObjectUtils.GetSideFaces(Wall, ShellLayerType.Exterior)` como referencia de cota | 2011 | `/create_dimensions/` (`annotate(kind="dimension")`, `annotation._referencia`) | verificado (0.4.3 en Revit 2027 es: cota entre los muros 165465 y 165592, valor 6.50) |

## Entrega 2b (0.5.0): estructuras metálicas y modelo analítico

Sin probar aún en Revit (28/09/2026): todo lo de esta sección entra como `por verificar` salvo lo ya
verificado en la fase 1. Se prueba con `herramientas-dev/VALIDACION_2B.md` y `pruebas/probar_revit.py --fase 2b`.
Regla de la entrega: cada miembro que cambie entre Revit 2024 y 2027 va con `try/except` y la ruta
alternativa se anota en la columna "Ruta que lo usa".

Nota sobre la 0.2.x: `/load_family/` (`placement.py`) llama a `Document.LoadFamily` **sin** transacción porque
el comentario de la fase 1 dice que LoadFamily abre la suya; en la documentación de la API LoadFamily modifica
el documento y necesita una transacción abierta. Las rutas nuevas (`/load_steel_profile/`) la abren
(`IA: Cargar perfil <familia>`). Queda por comprobar en Revit cuál de las dos formas es la correcta; si
`/load_family/` falla con "outside of transaction", es un fallo real de 0.2.x que corregir en su commit.

| Tipo | Miembro | Versión mínima | Ruta que lo usa | Estado |
|---|---|---|---|---|
| método | `Document.Create.NewFamilyInstance(XYZ, FamilySymbol, Level, StructuralType.Column)` | 2011 | `/create_steel_frame/` (`estructural.crear_pilar`) | verificado (fase 1, `/create_column/`) |
| método | `Document.Create.NewFamilyInstance(Line, FamilySymbol, Level, StructuralType.Beam)` | 2011 | `/create_steel_frame/` (`structure.crear_viga`) | verificado (fase 1, `/create_framing/`) |
| parámetro | `BuiltInParameter.FAMILY_TOP_LEVEL_PARAM` (nivel superior del pilar) | 2011 | `/create_steel_frame/`, `/create_column/` | verificado (0.5.0, validación 2b en Revit 2027 es, 28/09/2026, pasos 6-7: `top_level` "Zapata T.O") |
| método | `Document.Create.NewFamilyInstance(Line, FamilySymbol, Level, StructuralType.Brace)` | 2011 | `/create_bracing/` | verificado (0.5.0, validación 2b en Revit 2027 es, 28/09/2026, paso 11: arriostre en X, 2 barras de 6007,5 mm) |
| propiedad | `Family.StructuralMaterialType` (`StructuralMaterialType.Steel`) | 2013 | `/steel_profiles/`, `/steel_quantities/`, `describe include_structural` (`acero.es_acero`; reserva: clase `Metal` del activo estructural del material) | verificado (0.5.0, validación 2b en Revit 2027 es, 28/09/2026, P1: `steel_by: Family.StructuralMaterialType = Steel`) |
| propiedad | `FamilyInstance.StructuralMaterialType`, `FamilyInstance.StructuralUsage` | 2013 | `describe include_structural` (`acero.bloque_estructural`) | verificado (0.5.0, validación 2b en Revit 2027 es, 28/09/2026, paso 8: `structural_material_type: Steel`) |
| propiedad | `Material.StructuralAssetId` → `PropertySetElement.GetStructuralAsset()` → `StructuralAsset.Density`, `StructuralAsset.StructuralAssetClass` | 2013 | `/steel_quantities/` (peso), `acero.es_acero` (reserva) | verificado (0.5.0, validación 2b en Revit 2027 es, 28/09/2026, paso 19: peso por volumen × densidad en los 8 elementos, `sin_peso: []`) |
| método | `UnitUtils.ConvertFromInternalUnits(valor, UnitTypeId.KilogramsPerCubicMeter)` y `UnitTypeId.KilogramsPerMeter` | 2021 | `/steel_quantities/` (densidad y masa lineal; reserva: interno kg/ft³ × 35,31 y kg/ft × 3,28) | verificado (0.5.0, validación 2b en Revit 2027 es, 28/09/2026, paso 19: 4 pilares 305x305x97UC de 300 mm = 116,29 kg, coherente con 97 kg/m) |
| parámetro | `BuiltInParameter.HOST_VOLUME_COMPUTED`, `INSTANCE_LENGTH_PARAM` en vigas y pilares | 2011 | `/steel_quantities/` (reserva: `Location.Curve.Length`, caja envolvente) | verificado (0.5.0, validación 2b en Revit 2027 es, 28/09/2026, paso 19) |
| parámetro | `BuiltInParameter.STRUCTURAL_SECTION_COMMON_HEIGHT`, `_WIDTH`, `_WEB_THICKNESS`, `_FLANGE_THICKNESS` (tipo) | 2017 | `/steel_profiles/` (`dimensions_mm`; los que no existan van a `no_disponibles`) | parcial (validación 2b en Revit 2027, P1): `_WEB_THICKNESS` y `_FLANGE_THICKNESS` no existen y van a `no_disponibles`; `_HEIGHT` y `_WIDTH` sin error |
| parámetro | `BuiltInParameter.STRUCTURAL_SECTION_NOMINAL_WEIGHT` (masa lineal del tipo) | 2017 | `/steel_quantities/` (reserva sin activo estructural) | por verificar |
| método | `FamilySymbol.GetStructuralSection()` → `StructuralSection.StructuralSectionShape` | 2017 | `/steel_profiles/` (`shape`; reserva: designación del tipo) | por verificar |
| método | `Application.GetLibraryPaths()` (`IDictionary<string,string>`; se leen `.Values`) | 2011 | `/steel_profiles/` con `loaded_only=false`, `/load_steel_profile/` con `family_name` | por verificar (no probado en la validación 2b: ya había perfiles cargados, P2 y P3 saltados) |
| método | `Document.LoadFamilySymbol(string ruta, string tipo, out FamilySymbol)` (catálogo `.txt`) dentro de `IA: Cargar perfil` | 2011 | `/load_steel_profile/` | por verificar (no probado en la validación 2b: P3 saltado) |
| método | `Document.LoadFamily(string ruta, out Family)` dentro de una transacción | 2011 | `/load_steel_profile/` (sin catálogo) | por verificar (no probado en la validación 2b: P3 saltado; ver la nota sobre `/load_family/`) |
| método | `Family.GetFamilySymbolIds()` | 2015 | `/load_steel_profile/` (tipos de la familia cargada) | por verificar |
| propiedad | `Grid.Curve` (`Line`; los `Arc` se ignoran con aviso) | 2011 | `/create_steel_frame/` (`acero.rejillas_clasificadas`) | verificado (0.5.0, validación 2b en Revit 2027 es, 28/09/2026, pasos 5-7: 4 intersecciones de rejillas rectas) |
| parámetro | `BuiltInParameter.ALL_MODEL_MARK` fijado en pilares y vigas (`mark_prefix`) | 2011 | `/create_steel_frame/` | verificado (0.5.0, validación 2b en Revit 2027 es, 28/09/2026, paso 7: marcas `MCP2B-01`…`MCP2B-08`) |
| método | `SketchPlane.Create(Document, ElementId nivel)` | 2014 | `/create_truss/` | por verificar (no probado en la validación 2b: el modelo no tiene familias de cercha) |
| método | `Structure.Truss.Create(Document, ElementId trussTypeId, ElementId sketchPlaneId, Curve)` + clase `Structure.TrussType` | 2015 | `/create_truss/` (404 con `available_types` si no hay tipos) | por verificar (validación 2b: sin familias de cercha, `404` con `available_types: []` como se esperaba) |
| parámetro | `BuiltInParameter.INSTANCE_STRUCT_USAGE_PARAM`, `Y_JUSTIFICATION`, `Z_JUSTIFICATION`, `Y_OFFSET_VALUE`, `Z_OFFSET_VALUE`, `STRUCTURAL_BEND_DIR_ANGLE`, `START_EXTENSION`, `END_EXTENSION`, `STRUCTURAL_ANALYZES_AS` | 2011-2013 | `/set_structural_properties/`, `describe include_structural` (los que no existan → `no_disponibles`; los que el elemento no tenga → `no_aplica` / `fallidos`) | verificado (0.5.0, validación 2b en Revit 2027 es, 28/09/2026, pasos 8-10: `z_offset_mm` −50 y `y_justification` "Centro" releídos; `STRUCTURAL_ANALYZES_AS` no aplica a vigas y va a `no_aplica`) |
| parámetro | `BuiltInParameter.STRUCTURAL_START_RELEASE_TYPE`, `STRUCTURAL_END_RELEASE_TYPE` y `STRUCTURAL_START/END_RELEASE_FX/FY/FZ/MX/MY/MZ` en el elemento físico | 2011 | `/set_structural_properties/`, `describe include_structural` | no existe en el elemento físico en Revit 2027 (validación 2b, pasos 8-10: `no_aplica`); las liberaciones viven en el `AnalyticalMember`. 0.5.1: si solo se piden liberaciones y no hay miembro analítico, `409` `no_soportado` (`sin_modelo_analitico`) |
| enumeración | `int(Structure.YJustification.Left)`, `Structure.ZJustification`, `Structure.StructuralInstanceUsage`, `Structure.AnalyzeAs` (nombre → índice) | 2011 | `/set_structural_properties/` (`acero._indice_enum`) | verificado (0.5.0, validación 2b en Revit 2027 es, 28/09/2026, paso 10: `y_justification="center"` → índice 1, "Centro") |
| método | `Structure.AnalyticalToPhysicalAssociationManager.GetAnalyticalToPhysicalAssociationManager(doc).GetAssociatedElementId(ElementId)` (en los dos sentidos) | 2023 | `/analytical_status/`, `/fix_analytical/`, `/export_structural/`, liberaciones de reserva | verificado (0.5.0, validación 2b en Revit 2027 es, 28/09/2026, paso 17: responde; el modelo no tiene miembros analíticos asociados, `members: 0`) |
| método | `Structure.AnalyticalMember.GetCurve()`, `SetCurve(Curve)` | 2023 | `/analytical_status/`, `/fix_analytical/` | por verificar (no probado en la validación 2b: sin modelo analítico) |
| método | `AnalyticalMember.GetReleaseType/SetReleaseType(AnalyticalElementSelector, ReleaseType)`, `GetReleaseConditions/SetReleaseConditions(ReleaseConditions)`, constructor `ReleaseConditions(bool start, fx, fy, fz, mx, my, mz)` | 2023 | `/set_structural_properties/` (reserva), `describe include_structural`, CSV de `/export_structural/` | por verificar (no probado en la validación 2b: sin modelo analítico) |
| método | `Curve.Distance(XYZ)` (nodo sobre otro miembro: `touching`) | 2011 | `/analytical_status/` | por verificar (no probado en la validación 2b: sin modelo analítico) |
| clase | `Structure.StructuralConnectionHandler.Create(Document, IList<ElementId>, ElementId tipo)`, `StructuralConnectionHandlerType` (+ `GetDefaultConnectionHandlerType(doc)`), `StructuralConnectionApprovalType.GetAllStructuralConnectionApprovalTypes(doc)`, `StructuralConnectionHandler.ApprovalStatus` | 2017 / 2019 | `/create_steel_connection/`, `/element_types/` con `category="connections"` (409 `no_soportado` si faltan las clases o no hay tipos) | corregido en 0.5.1: con el `ElementId` de `GetDefaultConnectionHandlerType` convertido, un proyecto sin tipos de conexión responde `409` `sin_tipos` en lugar del tipo "Unnamed" (validación 0.5.1, paso 4). `Create` y `ApprovalStatus`, por verificar (el modelo de prueba no tiene tipos de conexión) |
| método | `Options.ComputeReferences = True` → `Face.Reference`, `PlanarFace.FaceNormal`, `Face.Area`, `Face.Project(XYZ).XYZPoint`; `GeometryInstance.GetSymbolGeometry()` + `Transform` para las referencias de una instancia | 2011 | `/add_plate/` (`acero._caras_con_referencia`) | por verificar (las referencias de `GetInstanceGeometry` suelen ser nulas: se usa la geometría del símbolo) |
| método | `Document.Create.NewFamilyInstance(Reference, XYZ, XYZ refDir, FamilySymbol)` (familia alojada en cara) | 2011 | `/add_plate/` | por verificar |
| método | `Document.Create.NewFamilyInstance(XYZ, FamilySymbol, Level, StructuralType.NonStructural)` y la sobrecarga sin nivel `(XYZ, FamilySymbol, StructuralType)` | 2011 | `/add_plate/` (familia de punto) | verificado (0.5.0, validación 2b en Revit 2027 es, 28/09/2026, paso 14: "Pergola Slat" colocada sobre la viga 615580) |
| propiedad | `Family.FamilyPlacementType` (`WorkPlaneBased` = alojada en cara) | 2011 | `/add_plate/` | por verificar |
| método | `ElementTransformUtils.CopyElement(doc, id, XYZ.Zero)` + `LocationCurve.Curve = Line` en una viga (original y copias) | 2012 | `/split_beam/` | fallo (validación 2b, paso 15): la copia sale bien (4000 mm), pero la unión del extremo con el pilar devuelve la viga original a 6000 mm. 0.5.1: `FamilyInstance.Split` y, sin él, `StructuralFramingUtils.DisallowJoinAtEnd` antes de mover; resuelto en la validación 0.5.1 con `Split` |
| método | `FamilyInstance.AddCoping(FamilyInstance)` | 2011 | `/join_geometry/` con `coping=true` | verificado (0.5.1, validación en Revit 2027 es, 28/09/2026, paso 6: la viga 615580 recortada contra el pilar 615576, `coping.failed: []`) |
| propiedad | `View.AreAnalyticalModelCategoriesHidden` (vista analítica activa como filtro del IFC) | 2011 | `/export_structural/` (`ifc_structural`) | sin error (validación 0.5.1, pasos 8-9: el IFC se exportó; no se anotó qué `filter_view` eligió) |
| método | `Document.Export(carpeta, nombre, IFCExportOptions)` con `FilterViewId` = vista analítica y `ExportBaseQuantities` | 2011 | `/export_structural/` (`interop.exportar_ifc`, extraído de `/export_ifc/`) | verificado (0.5.1, validación en Revit 2027 es, 28/09/2026, pasos 8-9: IFC de 1171 KB en el Escritorio y en `C:\IA\salidas`) |
| método | `JoinGeometryUtils.JoinGeometry(doc, pilar, viga)` entre perfiles de acero | 2011 | `/join_geometry/` | no admitido (validación 2b, paso 16: "The elements cannot be joined. Parameter name: secondElement"). 0.5.1: con `coping=true` no se llama y, sin él, la pareja va a `fallidos` |
| método | `FamilyInstance.Split(double parámetro normalizado)` → `ElementId` del trozo nuevo | 2019 | `/split_beam/` (0.5.1) | verificado (0.5.1, validación en Revit 2027 es, 28/09/2026, paso 5: tramos de 2000 y 4000 mm, `verificacion.coincide: true`; el original conserva el primer tramo, `original.segment: 0`) |
| método | `Structure.StructuralFramingUtils.DisallowJoinAtEnd(FamilyInstance, int)` | 2011 | `/split_beam/` sin `Split` (0.5.1) | no probado (0.5.1: se usó `FamilyInstance.Split`, así que la ruta alternativa no se ejecutó) |
| método | `FamilyInstance.GetCopingIds()` | 2011 | `/join_geometry/` con `coping=true` (`antes`/`despues` `coped`, 0.5.1) | verificado (0.5.1, validación en Revit 2027 es, 28/09/2026, paso 6: `antes.coped: false` y `despues.coped: true`) |
| método | `System.IO.Directory.Exists/CreateDirectory` y `File.WriteAllText(ruta, texto, UTF8Encoding(False))` | IronPython 2.7 | `/export_structural/` (0.5.1; en 0.5.0 `os.path.isdir` dio False para el Escritorio del usuario y `os.makedirs` falló con "Access ... denied", pasos 20-21) | verificado (0.5.1, validación en Revit 2027 es, 28/09/2026, pasos 8-9: CSV de 8 filas en el Escritorio de "Andy Bayona Antón" y en `C:\IA\salidas`, creando la carpeta; sin `500`) |
