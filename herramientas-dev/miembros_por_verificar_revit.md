# Miembros de la API de Revit por verificar

Registro de los miembros de la API de Revit (y de la biblioteca de IronPython) que usa el conector y de si se han
comprobado ya dentro de Revit. Se actualiza en cada entrega: todo miembro nuevo entra como `por verificar` y pasa
a `verificado` cuando `pruebas/probar_revit.py` (o una prueba manual anotada en `REVISION_FASE1.md`) lo ejecuta
en un Revit real. Un miembro `no existe` se retira del código o se deja con `try/except` y ruta alternativa.

Estados: `verificado` (ejecutado en Revit 2027 en español, 0.2.2), `no existe`, `por verificar`.

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
| clase | `PerformanceAdviser` (`GetPerformanceAdviser`, `GetAllRuleIds`, `ExecuteRules`) | 2012 | `/purge_unused/` | por verificar |
| método | `Toposolid.Create(Document, IList<XYZ>, ElementId, ElementId)` (y la sobrecarga con `CurveLoop`) | 2024 | `/create_toposolid/` | por verificar |
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
| método | `Element.GetDependentElements(ElementFilter)` (con `None`) | 2018 | `/describe/`, `/dependency_graph/` | por verificar |
| método | `HostObject.FindInserts(bool, bool, bool, bool)` | 2011 | `/describe/`, `/dependency_graph/` | por verificar |
| propiedad | `FamilyInstance.Host`, `FamilyInstance.SuperComponent` | 2011 | `/describe/`, `/dependency_graph/` | por verificar |
| método | `JoinGeometryUtils.GetJoinedElements(Document, Element)` | 2012 | `/describe/`, `/dependency_graph/` | por verificar |
| propiedad | `Category.CategoryType` (`CategoryType.Model`) | 2011 | `/describe/`, `/dependency_graph/`, `/snapshot/` | por verificar |
| propiedad | `Element.UniqueId`, `Element.ViewSpecific` | 2011 | `/describe/`, `/snapshot/` | por verificar |
| propiedad | `Dimension.References` y `Reference.ElementId` | 2011 | `/describe/` (`referenced_by`) | por verificar |
| método | `IndependentTag.GetTaggedLocalElementIds()` (reserva `TaggedLocalElementId`) | 2022 | `/describe/` (`referenced_by`) | por verificar |
| método | `Element.GetOrderedParameters()` | 2015 | `/describe/` | verificado (`/element_properties/`) |
| propiedad | `Parameter.IsShared`, `Parameter.GUID` | 2011 | `/describe/`, `/query/` (proveedor por compartido) | por verificar |
| propiedad | `InternalDefinition.BuiltInParameter` | 2011 | `/describe/` (`builtin`), `/query/` (proveedor) | por verificar |
| propiedad | `Element.CreatedPhaseId` | 2011 | `/query/` (`phase`) | por verificar |
| constructor | `FilteredElementCollector(Document, ElementId vista)` | 2011 | `/query/` (`view_id`), `/describe/` | verificado (`/current_view_elements/`) |
| clase | `ElementParameterFilter(FilterRule)` + `ParameterValueProvider(ElementId)` | 2011 | `/query/` (`filters` nativos) | por verificar |
| constructor | `FilterStringRule(provider, evaluator, string)` (3 argumentos; el 4.º `caseSensitive` desapareció en 2023) | 2023 | `/query/` | por verificar |
| clase | `FilterDoubleRule`, `FilterIntegerRule`, `FilterElementIdRule` | 2011 | `/query/` | por verificar |
| clase | `FilterStringEquals`, `FilterStringContains`, `FilterStringBeginsWith`, `FilterNumericEquals/Greater/GreaterOrEqual/Less/LessOrEqual` | 2011 | `/query/` | por verificar |
| clase | `BoundingBoxIntersectsFilter(Outline)` | 2011 | `/query/` (`bbox_min_mm`/`bbox_max_mm`) | por verificar (ya lo usaba `/ai_filter/`) |
| clase | `ElementLevelFilter(ElementId)` | 2011 | `/query/` (`level` sin categoría), `/find_elements/` | por verificar |
| clase | `ElementWorksetFilter(WorksetId)` + constructor `WorksetId(int)` | 2013 | `/query/` (`workset`) | por verificar |
| clase | `ElementMulticategoryFilter(IList<BuiltInCategory>)` | 2014 | `/query/` (`category` como lista) | por verificar (ya lo usaba `/clash_check/`) |
| método | `FailureMessage.GetFailureDefinitionId().Guid` | 2011 | `/warnings/?group_by=description` | por verificar |
| propiedades | `BuiltInFailures.OverlapFailures.WallsOverlap`, `.DuplicateInstances`, `.WallRoomSeparationOverlap`, `.RoomSeparationLinesOverlap`, `.FloorsOverlap`; `RoomFailures.RoomNotEnclosed`, `.RoomNotInPlaced`, `.RoomsInSameRegion`; `JoinElementsFailures.CannotKeepJoined`, `.CannotKeepJoinedWarning`; `InaccurateFailures.InaccurateLine`, `.InaccurateWall`, `.InaccurateBeamOrBrace`, `.InaccurateGrid`, `.InaccurateRefPlane`; `GeneralFailures.DuplicateValue`; `WallFailures.WallNotAttached`; `AreaFailures.AreaNotEnclosed` | 2012 | `/warnings/?group_by=description` (los nombres que no existan se ignoran; anotar cuáles) | por verificar |
| método | `ViewSchedule.GetTableData()`, `TableData.GetSectionData(SectionType.Body)`, `TableSectionData.NumberOfRows/NumberOfColumns` | 2014 | `/schedule/` | verificado (`/create_schedule/` lee `NumberOfRows`) |
| método | `ViewSchedule.GetCellText(SectionType, int, int)` (fila 0 del cuerpo = encabezados si `ShowHeaders`) | 2014 | `/schedule/` | por verificar |
| método | `ScheduleDefinition.GetFieldCount()`, `GetField(int)`, `ShowHeaders`, `IsItemized`, `ShowGrandTotal`; `ScheduleField.GetName()`, `ColumnHeading`, `IsHidden` | 2014 | `/schedule/` | por verificar |
| propiedad | `View.CropBox` (`BoundingBoxXYZ.Transform`), `CropBoxActive`, `CropBoxVisible`, `Scale`, `Discipline`, `DetailLevel`, `ViewTemplateId`, `ViewPlan.GenLevel` | 2013 | `/view_extents/` | por verificar (`Scale`, `CropBoxActive`, `Discipline` ya en `/current_view_info/`) |
| método | `ViewPlan.GetViewRange()`, `PlanViewRange.GetLevelId/GetOffset(PlanViewPlane)`, constantes `PlanViewRange.Unlimited/Current/LevelAbove/LevelBelow` | 2014 | `/view_extents/` | por verificar |
| método | `View3D.IsSectionBoxActive`, `View3D.GetSectionBox()` | 2011 | `/view_extents/` | por verificar |
| enumeración | `BuiltInParameter.VIEWER_SHEET_NUMBER`, `BuiltInParameter.VIEW_PHASE` | 2011 | `/view_extents/` (`sheet_number`, `phase`) | por verificar |
| módulo | `hashlib.md5` (biblioteca de IronPython 2.7; reserva `zlib.crc32`) | IronPython 2.7 | `/snapshot/` (hash de parámetros) | por verificar |
| expresión | `int(BuiltInCategory)` comparado con `get_element_id_value(Category.Id)` | 2011 | `/snapshot/` (niveles y rejillas por defecto) | por verificar (`placement._necesita_muro` ya usa `int(bic)`) |
| método | `FilteredElementCollector(doc).WhereElementIsNotElementType()` sin más filtros (recorrido completo) | 2011 | `/snapshot/` por defecto, `/query/` sin criterios nativos | verificado (`/model_statistics/`) |
