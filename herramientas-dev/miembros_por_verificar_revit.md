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
| método | `BasePoint.GetProjectBasePoint(Document)` / `BasePoint.GetSurveyPoint(Document)` | 2022 | `coordenadas._punto_base` (reserva por categoría si faltan) | por verificar |
| propiedad | `ProjectPosition.Angle` | 2011 | `coordenadas.norte_verdadero_grados`, `/set_project_location/` | por verificar |
| método | `Document.AcquireCoordinates(ElementId)` | 2018 | `/set_project_location/` (`acquire_from_link_id`) | por verificar |
| método | `ElementTransformUtils.MoveElement` sobre `BasePoint` | 2012 | `/set_project_location/` | por verificar |
| propiedad | `WorksetId.IntegerValue` | 2013 | `consulta._workset_id` (reserva `Value`) | por verificar |
| propiedad | `OverrideGraphicSettings.ProjectionLineColor` + `Color.IsValid` | 2013 | `/color_splash/`, `/clear_colors/` | por verificar |

## Entrega 2a (0.3.0): Bloque A, navegación profunda

| Tipo | Miembro | Versión mínima | Ruta que lo usa | Estado |
|---|---|---|---|---|
| método | `Element.GetDependentElements(ElementFilter)` (con `None`) | 2018 | `/describe/`, `/dependency_graph/` | verificado (0.3.0 en Revit 2027 es, `/describe/`, `/dependency_graph/`) |
| método | `HostObject.FindInserts(bool, bool, bool, bool)` | 2011 | `/describe/`, `/dependency_graph/` | verificado (0.3.0 en Revit 2027 es, `/describe/`) |
| propiedad | `FamilyInstance.Host`, `FamilyInstance.SuperComponent` | 2011 | `/describe/`, `/dependency_graph/` | verificado (0.3.0 en Revit 2027 es, `/dependency_graph/`) |
| método | `JoinGeometryUtils.GetJoinedElements(Document, Element)` | 2012 | `/describe/`, `/dependency_graph/` | verificado (0.3.0 en Revit 2027 es, `/dependency_graph/` 32 aristas) |
| propiedad | `Category.CategoryType` (`CategoryType.Model`) | 2011 | `/describe/`, `/dependency_graph/`, `/snapshot/` | verificado (0.3.0 en Revit 2027 es, `/describe/`, `/snapshot/`) |
| propiedad | `Element.UniqueId`, `Element.ViewSpecific` | 2011 | `/describe/`, `/snapshot/` | verificado (0.3.0 en Revit 2027 es, `/snapshot/` 2746 elementos) |
| propiedad | `Dimension.References` y `Reference.ElementId` | 2011 | `/describe/` (`referenced_by`) | por verificar |
| método | `IndependentTag.GetTaggedLocalElementIds()` (reserva `TaggedLocalElementId`) | 2022 | `/describe/` (`referenced_by`) | por verificar |
| método | `Element.GetOrderedParameters()` | 2015 | `/describe/` | verificado (`/element_properties/`) |
| propiedad | `Parameter.IsShared`, `Parameter.GUID` | 2011 | `/describe/`, `/query/` (proveedor por compartido) | por verificar |
| propiedad | `InternalDefinition.BuiltInParameter` | 2011 | `/describe/` (`builtin`), `/query/` (proveedor) | verificado (0.3.0 en Revit 2027 es, `builtin: ALL_MODEL_MARK` en 2a.1) |
| propiedad | `Element.CreatedPhaseId` | 2011 | `/query/` (`phase`) | por verificar |
| constructor | `FilteredElementCollector(Document, ElementId vista)` | 2011 | `/query/` (`view_id`), `/describe/` | verificado (`/current_view_elements/`) |
| clase | `ElementParameterFilter(FilterRule)` + `ParameterValueProvider(ElementId)` | 2011 | `/query/` (`filters` nativos) | verificado (0.3.0 en Revit 2027 es, `Length > 3000` nativo) |
| constructor | `FilterStringRule(provider, evaluator, string)` (3 argumentos; el 4.º `caseSensitive` desapareció en 2023) | 2023 | `/query/` | por verificar |
| clase | `FilterDoubleRule`, `FilterIntegerRule`, `FilterElementIdRule` | 2011 | `/query/` | verificado (0.3.0 en Revit 2027 es, `FilterDoubleRule` con CURVE_ELEM_LENGTH; las otras dos por verificar) |
| clase | `FilterStringEquals`, `FilterStringContains`, `FilterStringBeginsWith`, `FilterNumericEquals/Greater/GreaterOrEqual/Less/LessOrEqual` | 2011 | `/query/` | verificado (0.3.0 en Revit 2027 es, `FilterNumericGreater`; los de texto solo via 2a.2 sin confirmar la rama nativa) |
| clase | `BoundingBoxIntersectsFilter(Outline)` | 2011 | `/query/` (`bbox_min_mm`/`bbox_max_mm`) | por verificar (ya lo usaba `/ai_filter/`) |
| clase | `ElementLevelFilter(ElementId)` | 2011 | `/query/` (`level` sin categoría), `/find_elements/` | verificado (0.3.0 en Revit 2027 es, `level: Zapata B.O`) |
| clase | `ElementWorksetFilter(WorksetId)` + constructor `WorksetId(int)` | 2013 | `/query/` (`workset`) | por verificar |
| clase | `ElementMulticategoryFilter(IList<BuiltInCategory>)` | 2014 | `/query/` (`category` como lista) | por verificar (ya lo usaba `/clash_check/`) |
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
| propiedad | `Document.Phases` (`PhaseArray`) | 2011 | `/query/` (`phase` por nombre) | por verificar |
| método | `TableView.GetCellText(SectionType.Header, 0, 0)` (título de la tabla) | 2013 | `/schedule/` | por verificar (`SectionType.Body` también) |

## Entrega 2a (0.3.0): Bloque D, macros de proyecto

| Tipo | Miembro | Versión mínima | Ruta que lo usa | Estado |
|---|---|---|---|---|
| método | `Grid.Create(Document, Line)` + `Grid.Name` (rechaza nombres repetidos) | 2011 | `/grid_levels/` (`structure.crear_rejilla`), `/create_grid/` | verificado (0.3.1 en Revit 2027 es: V1 V2 V3 H1 H2, 115 ms) |
| método | `Level.Create(Document, double)` + `Level.Name` | 2011 | `/grid_levels/` (`building.crear_nivel`), `/create_level/` | verificado (0.3.0 en Revit 2027 es, 2a.3b) |
| método | `ViewSheet.Create(Document, ElementId cajetín)`, `ViewSheet.SheetNumber`, `View.Name` | 2013 | `/sheet_set/` (`documentation.crear_plano`), `/create_sheet/` | verificado (0.3.0 en Revit 2027 es, `/sheet_set/` MCP-01) |
| método | `Viewport.CanAddViewToSheet(Document, ElementId, ElementId)` y `Viewport.Create(Document, ElementId, ElementId, XYZ)` | 2014 | `/sheet_set/` | verificado (0.3.0 en Revit 2027 es, 2 vistas colocadas) |
| propiedad | `Viewport.ViewId`, `Viewport.SheetId` | 2014 | `/sheet_set/` (vistas ya colocadas) | verificado (0.3.0 en Revit 2027 es, `/sheet_set/`) |
| método | `ScheduleSheetInstance.Create(Document, ElementId, ElementId, XYZ)`, `ScheduleSheetInstance.ScheduleId`, `.OwnerViewId` | 2014 | `/sheet_set/` (tablas) | por verificar |
| método | `Element.get_BoundingBox(ViewSheet)` del cajetín para centrar la vista | 2011 | `/sheet_set/` (`position_mm` omitido) | verificado (0.3.0 en Revit 2027 es, centro 422.5 x 296) |
| método | `Document.Link(string, DWGImportOptions, View, out ElementId)` | 2011 | `/import_civil/` (`interop.vincular_cad`), `/link_file/` | por verificar |
| método | `Document.Link(string, DGNImportOptions, View, out ElementId)` (`.dgn`; la sobrecarga DWG no lo admite) | 2011 | `/import_civil/`, `/link_file/` con `.dgn` | por verificar |
| método | `Document.Regenerate()` antes de `AcquireCoordinates` sobre el vínculo recién creado | 2011 | `/import_civil/` (`use_shared_coordinates`) | por verificar |
| propiedad | `BasePoint.Pinned`, `BasePoint.Clipped` (409 si el punto base está fijado o recortado) | 2011 | `/import_civil/` (`coordenadas.comprobar_puntos_base_libres`), `/set_project_location/` | por verificar |
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

| Tipo | Miembro | Versión mínima | Ruta que lo usa | Estado |
|---|---|---|---|---|
| método | `Ceiling.Create(Document, IList<CurveLoop>, ElementId tipo, ElementId nivel)` + clase `CeilingType` | 2022 | `/create_elements/` y `/create_surface/` (kind `ceiling`; reserva: suelo como en 0.3.x) | por verificar |
| enumeración | `BuiltInParameter.CEILING_HEIGHTABOVELEVEL_PARAM` (desfase del techo) | 2011 | `building.crear_superficie` (techo con `offset`) | por verificar |
| método | `ElementTransformUtils.CopyElement` repetido con `vector * i` en una transacción, también sobre un elemento fijado (0.4.1) | 2012 | `/transform_elements/` (`operation=array`) | por verificar (0.4.0 lo rechazó antes de llamar a Revit porque el muro 151574 estaba fijado) |
| método | `Element.GetOrderedParameters()` en cada elemento de la instantánea (antes `Parameters`) | 2015 | `/snapshot/` (`instantaneas._parametros_de`) | verificado (0.4.0: `parametros_ms` 950 en 2745 elementos; `total_ms` 3612 frente a 8200 en 0.3.1) |
| método | `Element.get_BoundingBox(None)` solo con `include_bbox` | 2011 | `/snapshot/` | verificado (0.4.0: `total_ms` 2241 sin bbox frente a 3612 con bbox; `bbox_ms` 77 y 2, la diferencia está sobre todo en `escritura_ms`: 1729 y 916) |
| módulo | `threading.Thread` + `System.IO.File.Copy` desde un hilo que no es el de Revit (solo E/S de archivos) | IronPython 2.7 | `escritura.CopiaDiferida` (todas las escrituras salvo `RUTAS_COPIA_SINCRONA`) | por verificar (0.4.0: `diferida` true y `estado` terminada en los pasos 8, 13 y 22, pero las tres con `reutilizada` true y `ms` 0, así que el hilo no llegó a copiar) |
| módulo | `imp.load_source(nombre, ruta)` para cargar `macro.py` y recargarlo al cambiar el `mtime` | IronPython 2.7 | `/macros/run/` (`macros_usuario.cargar_modulo`) | verificado (0.4.0: `reloaded` true tras editar `comentarios_por_nivel/macro.py`) |
| propiedad | `ViewSheet.SheetNumber` asignada dos veces (número temporal y definitivo) para evitar duplicados | 2013 | macro de ejemplo `numerar_planos` | por verificar |
| argumento | `uidoc` inyectado por Routes en `/macros/run/` (`run(doc, uidoc, args, api)`) | pyRevit 4.8 | `/macros/run/` | verificado (0.4.0: `run_macro` se ejecuta con la firma de Routes; las macros de ejemplo no lo usan) |
| parámetro | `BuiltInParameter.VIEWER_SHEET_NUMBER` como `fields` de `/query/` sobre `OST_Views` (`---` = sin plano) | 2011 | herramienta `list_views(on_sheet=...)` | verificado (0.4.0: `list_views(view_type="floor_plans", on_sheet=false)`, 13 plantas) |
| método | `Category.Id` comparado con `int(BuiltInCategory)` para resolver `OST_...` en `/list_category_parameters/` | 2011 | herramienta `list_types(with_parameters=true)` | verificado (0.4.0: `list_types(category="OST_Walls", with_parameters=true)` con `category_parameters`) |
