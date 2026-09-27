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
