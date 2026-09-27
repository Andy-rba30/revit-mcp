# Prompt de validación de la entrega 2a (revit-mcp 0.3.0) para el agente local

Copia este texto tal cual en el agente que tiene conectado el MCP de Revit (Claude Desktop, Claude Code, Cursor...).
Requisitos previos, como en la validación de la 0.2.2: Revit abierto **con una copia del modelo de prueba** (nunca
el original), la extensión cargada con Routes activo en 48884, el puente arrancado con
`.venv\Scripts\python main.py --combined`, y un Revit en español (o en cualquier idioma que no sea inglés).

---

Eres el agente de validación de la versión 0.3.0 del conector Revit MCP. Trabajas sobre una **copia** del modelo.
Ejecuta los pasos en orden, anota la respuesta **literal** (JSON completo o, si es muy largo, las primeras 40
líneas) de cada uno, no corrijas nada por tu cuenta y, si un paso falla, sigue con el siguiente. No uses nombres
visibles en inglés: toma los nombres de niveles, vistas, cajetines y tipos de las respuestas de `list_levels`,
`list_revit_views` y `list_element_types`. Al final entrega el informe con el formato del apartado "Informe".

## Pasos

1. `get_revit_status` → esperado: `status: active`, `document_title` con el nombre de la copia.
2. `get_revit_model_info` → esperado: bloque `file` con `path` (la copia), `is_workshared` y `units`.
3. En una consola, `python pruebas\probar_revit.py --fase 2a` → esperado: `Resultado: 13/13 pruebas correctas`
   y salida 0. Pega el bloque de cada prueba 2a.1 a 2a.4 con su `[OK]` o `[FALLO]`. Si 2a.3 avisa de
   "elementos modificados al crear el nivel", pega los ids.
4. `find_elements(category="OST_Walls", max=3)` → esperado: `200` con `elements`, `ids`, `count`,
   `total_matched`, `scanned`, `truncated` y `filters` (los mismos campos que en 0.2.2). Guarda el primer id como
   `MURO`.
5. `describe_element(element_id=MURO, depth=1, include_geometry=true)` → esperado: `categoria` en el idioma de
   Revit ("Muros"), `parameters.instance` con un parámetro cuyo `builtin` sea `ALL_MODEL_MARK` y otro de longitud
   con `unit: "mm"` y `value` numérico en mm (compáralo con la longitud que muestra Revit), `parameters.type` no
   vacío, `hosted_elements` (con `categoria` gracias a `depth=1`), `joined_elements`, `dependents`,
   `referenced_by` (cotas/etiquetas de la vista activa) y `geometry.volume_m3 > 0`. Anota si `is_shared`/`guid`
   aparecen en algún parámetro compartido del modelo.
6. `dependency_graph(element_id=MURO, max_nodes=50)` → esperado: `nodes[0].id == MURO`, aristas `hosts` hacia sus
   puertas/ventanas (si las tiene) y `joins` hacia lo unido; `truncated: false` con menos de 50 nodos.
7. `query_elements(category="OST_Walls", filters=[{"parameter": "Length", "op": ">", "value": 3000}],
   sort_by="-Length", page_size=5, fields=["Length", "Mark"])` → esperado: `elements[].fields.Length` en mm,
   todos > 3000 y en orden descendente; `native` incluye `category` y `filter Length > (BuiltInParameter
   CURVE_ELEM_LENGTH)`; `python_filters` vacío.
8. `query_elements(category="OST_Walls", filters=[{"parameter": "Comments", "op": "empty"}])` → esperado:
   `python_filters` con `Comments/empty` y `total_matched` igual al número de muros sin comentarios.
9. `query_elements(level="<nombre exacto de un nivel>", page_size=10)` → esperado: todos los `elements[].nivel`
   iguales a ese nivel. Después `query_elements(level="NoExiste")` → esperado: `404` con `available_levels`.
10. `query_elements(view_id=<id de la vista activa, de get_current_view_info>, category="OST_Walls")` →
    esperado: solo los muros visibles en esa vista.
11. `query_elements(category="OST_Walls", filters=[{"parameter": "Mark", "op": "~", "value": "x"}])` →
    esperado: `400` con la lista de `op` admitidos.
12. `list_warnings(group_by="description")` → esperado: `groups[]` con `count`, `element_ids`,
    `failure_definition_guid` y `sugerencia`. Anota qué `failure` aparecen (por ejemplo
    `OverlapFailures.WallsOverlap`) y cuáles salen con `failure: null` (para completar la tabla de sugerencias).
    Después `list_warnings(max=5)` sin `group_by` → esperado: la respuesta de siempre (`warnings`, `count`,
    `total`, `truncated`).
13. `list_revit_views` y elige una tabla de planificación de `schedules`; `schedule_to_json(name="<ese nombre>")`
    → esperado: `headers` iguales a las cabeceras que muestra Revit, `rows` con los textos tal cual (unidades
    incluidas), `row_count` y `total_rows`. Si `headers_from` es `field headings`, anótalo: significa que la fila 0
    del cuerpo no eran los encabezados.
14. `get_view_extents(view_id=<id de la vista activa>)` → esperado: `scale`, `level`, `discipline`, `crop.active`,
    `view_range.cut.level` y `view_range.cut.offset_mm` iguales a los del cuadro "Rango de vista" de Revit,
    `view_template` (nombre o `null`).
15. `snapshot_model(name="validacion 2a")` → esperado: `ruta` dentro de `snapshots\` junto al `.rvt`, `count`
    > 0, `truncated: false`. Repite la misma llamada → esperado: `409` "Snapshot already exists". Con
    `overwrite=true` → `200`.
16. `set_parameter(element_id=MURO, parameter_name="Comments", value="validacion 2a")` y después
    `diff_snapshots(a="validacion 2a")` → esperado: `modified` con el muro y `cambios[]` que incluye
    `Comentarios` (nombre visible) con `antes`/`despues`; `added` y `removed` vacíos. Restaura el comentario con
    `set_parameter` al valor `antes`.
17. `create_grid_and_levels(x_spacings_mm=[6000, 6000], y_spacings_mm=[5000], x_names="V1", y_names="H1",
    levels=[{"name": "MCP validacion", "elevation_mm": 99000}], simular=true)` → esperado: `simulado: true`,
    `plan.grids_x` = V1, V2, V3, `plan.grids_y` = H1, H2, `plan.counts.total: 6`, sin `copia`. Si responde `400`
    porque esos nombres ya existen, cambia el prefijo y anótalo.
18. Ejecuta el paso 17 sin `simular` → esperado: `ok: true`, `count: 6`, `grids[]` con esos nombres, `levels[]`
    con `elevation_mm: 99000`, `copia` con la ruta del `.rvt` copiado y una entrada de deshacer
    `IA: Rejilla y niveles` en Revit. Después borra los 6 elementos con `delete_elements(element_ids=[...])`.
19. `create_sheet_set(sheets=[{"number": "MCP-01", "name": "Validación 2a", "views": [{"view_name": "<una vista
    de planta que NO esté en ningún plano>"}, {"view_name": "<una vista que SÍ esté en un plano>"}]}],
    simular=true)` → esperado: `plan.sheets[0].views` con la primera y `skipped` con la segunda ("ya está en el
    plano ..."). Ejecuta sin `simular` → esperado: `sheets[0].id`, `views_placed` con un `viewport_id` y la vista
    visible en el plano en Revit. Después borra el plano con `delete_elements`.
20. Con el CSV `pruebas\puntos_validacion.csv` (créalo con tres líneas `P,N,E,Z`: `1,100,200,5`, `2,110,200,6`,
    `3,100,210,5.5`) → `import_from_civil(file_path="<ruta absoluta>", level="<nivel más bajo>", simular=true)`
    → esperado: `haria[0].points: 3`, `format` empezando por `P,N,E,Z`, `extent_mm.x` = [200000, 210000]. Ejecuta
    sin `simular` → esperado: `creados[0].categoria` = la categoría de toposólido en tu idioma. Si Revit es 2024+ y
    responde `400` por `Toposolid`, anótalo. Borra el toposólido después.
21. Solo si tienes un DWG de topografía a mano: `import_from_civil(file_path="<dwg>", level="<nivel>",
    use_shared_coordinates=true, simular=true)` → esperado: `haria` con `vincular` (placement `center`) y
    `adquirir_coordenadas` con `antes`. Si el proyecto ya tiene coordenadas compartidas, el real responde `409`
    con `shared_coordinates_set: true`: no pases `forzar`. Si no tienes DWG, marca el paso como "no probado".
22. `read_log(last_n=15)` → esperado: entradas de `/grid_levels/`, `/sheet_set/` e `/import_civil/` con `ok: true`
    y las simuladas con `simulado: true`; ninguna de `/snapshot/`, `/query/` ni `/describe/` (son de lectura).
23. En Revit, abre el desplegable de Deshacer → esperado: entradas `IA: Rejilla y niveles`, `IA: Crear 1 planos`,
    `IA: Importar topografia puntos_validacion.csv` y las `IA: Parametro ...` del paso 16.

## Informe

Entrega una tabla con una fila por paso:

| Paso | Herramienta | Resultado (OK / FALLO / no probado) | Respuesta literal (recortada a 40 líneas) | Observaciones |
|---|---|---|---|---|

Y debajo:

- **Miembros de la API que fallaron** (nombre del miembro, versión de Revit, mensaje de error literal), para
  actualizar `herramientas-dev/miembros_por_verificar_revit.md`.
- **Nombres visibles** que aparecieron en las respuestas (categorías, parámetros, cabeceras de la tabla), para
  confirmar que se conservan las tildes.
- **Fallos `failure: null`** del paso 12 con su `descripcion` y `failure_definition_guid`.
- Versión de Revit e idioma, y el resultado total de `probar_revit.py --fase 2a` (`N/13`).
