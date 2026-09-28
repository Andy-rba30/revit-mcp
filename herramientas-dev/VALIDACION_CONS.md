# Prompt de validación de la consolidación (revit-mcp 0.4.0) para el agente local

Copia este texto tal cual en el agente que tiene conectado el MCP de Revit (Claude Desktop, Claude Code, Cursor...).
Requisitos previos, como en las validaciones anteriores: Revit abierto **con una copia del modelo de prueba** (nunca
el original), la extensión de 0.4.0 cargada (recarga pyRevit: `revit_mcp/` corre dentro de Revit) con Routes activo
en 48884, el puente 0.4.0 arrancado con `.venv\Scripts\python main.py --combined`, el cliente MCP reiniciado (para que
cargue la lista nueva de 40 herramientas) y un Revit en español (o en cualquier idioma que no sea inglés).

---

Eres el agente de validación de la versión 0.4.0 del conector Revit MCP. Trabajas sobre una **copia** del modelo.
Usa las herramientas MCP **por su nombre** (no llames a las rutas HTTP salvo donde se indique), ejecuta los pasos en
orden, anota la respuesta **literal** (JSON completo o, si es muy largo, las primeras 40 líneas) de cada uno y **el
tiempo total de cada paso** (desde que decides llamar hasta que tienes la respuesta; anota también `ms` y `ms_puente`
de la respuesta), no corrijas nada por tu cuenta y, si un paso falla, sigue con el siguiente. No uses nombres visibles
en inglés: toma los nombres de niveles, vistas, cajetines y tipos de `get_revit_model_info(include=["levels"])`,
`list_views` y `list_types`. Al final entrega el informe con el formato del apartado "Informe".

## Pasos

1. Lista las herramientas que te ofrece el servidor → esperado: **40** nombres, entre ellos `set_parameters`,
   `create_elements`, `list_macros`, `run_macro`, `list_types`, `annotate`, `export`, `maintain_model`; y **ninguno**
   de `set_parameter`, `find_elements`, `list_levels`, `create_line_based_element`, `place_family`. Pega la lista.
2. Llama a `set_parameter(element_id=1, parameter_name="Comments", value="x")` (nombre retirado) → esperado: un
   resultado de error cuyo texto empieza por `La herramienta 'set_parameter' se retiro en 0.4.0. Usa en su lugar:
   set_parameters(...)`. Repite con `find_elements(category="OST_Walls")` → esperado: texto con `query_elements(`.
3. `get_revit_status` → esperado: `status: active`, `document_title` con el nombre de la copia.
4. `get_revit_model_info(include=["levels", "location", "worksets"])` → esperado: bloque `file` (`path` de la copia,
   `is_workshared`, `units`), `levels.levels[]` con `elevation_mm`, `location.true_north_deg`,
   `worksets.is_workshared`, y `ms_puente`. Guarda el nombre del nivel más bajo como `NIVEL`.
5. En una consola, `python pruebas\probar_revit.py --fase cons` → esperado: `Resultado: 15/15 pruebas correctas`
   (14/14 si cons.3 solo se simuló porque `plan.count` superaba 200) y salida 0. Pega el bloque de cons.1 con los
   cuatro tiempos (20 `set_parameter` frente a 1 `set_parameters`, ms de Revit y de pared) y el de cons.4 con
   `timings`.
6. `query_elements(category="OST_Walls", page_size=20, fields=["Comments", "Mark"], sort_by="id")` → esperado:
   `elements[]` con `id`, `nombre`, `tipo`, `nivel`, `fields`, y `ms_puente`. Guarda los 20 ids como `MUROS` y sus
   comentarios actuales.
7. `set_parameters(changes=[{"element_ids": MUROS, "parameters": {"Comments": "validacion 0.4.0"}}], simular=true)`
   → esperado: `simulado: true`, `haria` con 20 entradas (`parameter_label` = `Comentarios` en un Revit en español,
   `antes`, `despues`), `elements: 20`, `fallidos: []`, sin `copia`.
8. El paso 7 sin `simular` → esperado: `ok: true`, `count: 20`, `parameters_set: 20`, `verificacion.coincide: true`,
   `despues` con 20 entradas, `copia` con `diferida: true`, `ms` y `espera_ms`, y **una sola** entrada
   `IA: Parametros (20 elementos)` en el desplegable de Deshacer de Revit. Anota `ms` y `ms_puente`.
9. `set_parameters(changes=[{"element_ids": MUROS[:2], "parameters": {"Length": 1, "NoExiste": "x", "Comments":
   "validacion 0.4.0 b"}}])` → esperado: `200` con `fallidos[]` de motivo `read-only` (Length) y `parameter not found`
   (NoExiste, con `available_parameters`) para cada muro, `parameters_set: 2` y `ok: true`: un parámetro de solo
   lectura no aborta el lote.
10. `set_parameters(changes=[{"element_ids": MUROS[:2], "parameters": {"Type Comments": "tipo 0.4.0"}}],
    type_parameters=true, simular=true)` → esperado: `haria[].is_type_parameter: true`. Ejecuta sin `simular` →
    esperado: `changes[].type_id` igual en los dos muros si comparten tipo y `despues` con `Comentarios de tipo`.
    Restaura el valor anterior (`antes`) con otro `set_parameters(type_parameters=true)`.
11. Restaura los comentarios del paso 6 con **un** `set_parameters` (un `change` por muro con su valor original;
    los vacíos como `""`) → esperado: `ok: true`, `parameters_set: 20`.
12. `create_elements(elements=[{"kind": "level", "name": "MCP 0.4 nivel", "elevation_mm": 123000}, {"kind": "grid",
    "name": "MCP4", "start_point": {"x": 0, "y": -1000, "z": 0}, "end_point": {"x": 0, "y": 9000, "z": 0}},
    {"kind": "wall", "start_point": {"x": 0, "y": 0, "z": 0}, "end_point": {"x": 3000, "y": 0, "z": 0},
    "level_name": NIVEL, "height": 2500}], simular=true)` → esperado: `simulado: true`, `plan.total: 3`,
    `plan.counts {level: 1, grid: 1, wall: 1}`, `haria[]` con `index` 0, 1, 2 y `kind`, sin `copia`. Si responde `400`
    porque `MCP4` ya existe, cambia el nombre y anótalo.
13. El paso 12 sin `simular` → esperado: `ok: true`, `count: 3`, `creados.level[0].name` = `MCP 0.4 nivel` con
    `elevation_mm: 123000`, `creados.grid[0].name` = `MCP4`, `creados.wall[0].categoria` = `Muros`, `creados_ids` con
    3 ids, **una sola** entrada `IA: Crear 3 elementos` en Deshacer. Anota `ms`. Después borra los 3 con
    `delete_elements(element_ids=creados_ids)`.
14. `create_elements(elements=[{"kind": "level", "name": "MCP 0.4 otro", "elevation_mm": 124000}, {"kind": "wall",
    "start_point": {"x": 0, "y": 0, "z": 0}, "end_point": {"x": 0, "y": 0, "z": 0}, "level_name": NIVEL}])` →
    esperado: `400` con `index: 1`, `kind: wall` y `zero-length` en el error, y **ningún** nivel creado
    (compruébalo con `get_revit_model_info(include=["levels"])`).
15. `create_elements(elements=[{"kind": "column", "point": {"x": 0, "y": 0, "z": 0}, "base_level": NIVEL,
    "type_name": "<un tipo de pilar de list_types(category=\"OST_StructuralColumns\")>"}, {"kind": "family_instance",
    "family_name": "<una familia de list_types(contains=\"...\")>", "type_name": "<su tipo>", "location": {"x": 1000,
    "y": 1000, "z": 0}, "level_name": NIVEL}], simular=true)` → esperado: `haria[0].element_type` =
    `structural_column`, `haria[1].accion` = `colocar`. Si no hay pilares o familias cargadas, anótalo como "no
    probado".
16. `describe_element(element_ids=[MUROS[0], MUROS[1], 999999999])` → esperado: `elements` con dos entradas (por id,
    con `parameters.instance`), `errors` con `999999999` y `count: 2`.
17. `list_types(category="OST_Walls", with_parameters=true)` → esperado: `types[]` con `ejemplares` y
    `category_parameters` (no `error`: la ruta ya acepta `OST_Walls`). Después `list_types()` → esperado: categorías
    con recuento; y `list_types(contains="<parte del nombre de una familia>", loaded_only=true)` → esperado: solo
    tipos con `is_active: true`.
18. `list_views(view_type="floor_plans", on_sheet=false)` → esperado: solo plantas que no están en ningún plano
    (`sheet_numbers` con las colocadas). Después `describe_view()` sin argumentos → esperado: la vista activa
    (`is_active: true`, `view_name`, `scale`, `view_range` si es planta).
19. `query_elements(current_view=true, category="OST_Walls", page_size=5)` → esperado: solo muros visibles en la
    vista activa. `query_elements(selected=true)` con algo seleccionado en Revit → esperado: `elements[]` y `count`.
20. `list_macros` → esperado: `carpeta` (`%LOCALAPPDATA%\RevitMcp\macros` o `REVIT_MCP_MACROS`), `macros[]` con
    `comentarios_por_nivel` y `numerar_planos` (el paso 5 las copió) con `args`, `writes: true`, `timeout_s`, y
    `invalidas: []`. Si no están, copia a mano `herramientas-dev\macros-ejemplo\*` a esa carpeta y repite.
21. `run_macro(name="comentarios_por_nivel", args={"category": "OST_Walls", "level": NIVEL}, simular=true)` →
    esperado: `simulado: true`, `plan.count`, `plan.muestra[]` (`id`, `antes`, `despues` = NIVEL), `haria[0].undo_name`
    = `IA: Macro comentarios_por_nivel`, sin `copia`.
22. Si `plan.count` ≤ 200, el paso 21 sin `simular` → esperado: `ok: true`, `writes: true`, `despues` con
    `plan.count` entradas, `output` = `N elementos con Comments = nivel`, `copia`, entrada `IA: Macro
    comentarios_por_nivel` en Deshacer. Restaura con `set_parameters` usando `antes` (un `change` por id).
23. `run_macro(name="comentarios_por_nivel", args={"category": "Muros"})` → esperado: `500` (o resultado de error)
    con `BuiltInCategory` en el texto y **ningún** cambio (la transacción se revierte). Después
    `run_macro(name="numerar_planos", args={"step": "dos"})` → esperado: `400` con `faltan: ["prefix"]` e
    `invalidos.step`.
24. Edita `%LOCALAPPDATA%\RevitMcp\macros\comentarios_por_nivel\macro.py` y cambia el texto del `api.log` (por ejemplo
    añade ` (editada)`); repite el paso 21 → esperado: `reloaded: true` en la respuesta y el `output`... no aplica en
    simular; ejecuta `run_macro(..., simular=true)` y anota `reloaded`. Deshaz la edición.
25. `transform_elements(element_ids=[MUROS[0]], operation="array", vector={"x": 0, "y": 5000, "z": 0}, count=3,
    simular=true)` → esperado: `haria[0].count: 3`, `copies: 2`. Ejecuta sin `simular` → esperado: `ok: true`,
    `new_element_ids` con 2 ids, entrada `IA: Matriz de 1 elementos`. Borra las 2 copias con `delete_elements`.
26. `snapshot_model(name="validacion 0.4.0", overwrite=true)` → esperado: `timings` con `recoleccion_ms`,
    `descripcion_ms`, `bbox_ms`, `parametros_ms`, `hash_ms`, `escritura_ms`, `total_ms`, `name_lookups`; anota
    `total_ms` y `count` (en la 0.3.1 fueron 8,2 s y 2746 elementos; objetivo < 3 s). Repite con
    `include_bbox=false` → esperado: `bbox_ms: 0` y `total_ms` menor.
27. `annotate(kind="tag", category="OST_Walls", simular=true)` → esperado: la simulación de `tag_walls` (`haria` con
    los muros sin etiqueta de la vista activa). `annotate(kind="dimension", element_ids=[MUROS[0], MUROS[1]],
    simular=true)` → esperado: `haria[0].accion` = `acotar` o un `400` explicando que la vista no admite cotas.
28. `export(format="rooms_csv", file_path="<carpeta de la copia>\habitaciones_0.4.csv")` → esperado: `rows` y
    `file_path`; el archivo existe. `export(format="ifc")` sin `file_path` → esperado: error `file_path is required`.
29. `maintain_model(action="backup", suffix="validacion_0_4")` → esperado: `copia.ruta` acabado en
    `_validacion_0_4.rvt` y `copia.ms`. `maintain_model(action="purge", simular=true)` → esperado: `candidatos[]` (o
    `409` si no hay PerformanceAdviser). `maintain_model(action="rezar")` → esperado: error `not supported`.
30. `read_log(last_n=20)` → esperado: entradas de `/set_parameters/`, `/create_elements/`, `/macros/run/` y
    `/transform_elements/` con `ok: true` (las simuladas con `simulado: true`); ninguna de `/query/`, `/describe/`,
    `/macros/` ni `/snapshot/`.
31. En Revit, abre el desplegable de Deshacer → esperado: `IA: Parametros (20 elementos)`, `IA: Parametros (2
    elementos)`, `IA: Crear 3 elementos`, `IA: Macro comentarios_por_nivel`, `IA: Matriz de 1 elementos`, y **ninguna**
    entrada por elemento de los pasos 8 y 13.

## Informe

Entrega una tabla con una fila por paso:

| Paso | Herramienta | Tiempo total del paso | `ms` / `ms_puente` | Resultado (OK / FALLO / no probado) | Respuesta literal (recortada a 40 líneas) | Observaciones |
|---|---|---|---|---|---|---|

Y debajo:

- **Tiempos comparados**: 20 `set_parameter` frente a 1 `set_parameters` (paso 5, cons.1: ms de Revit y de pared de
  cada lado) y `snapshot_model` con y sin `include_bbox` (paso 26), con `count`.
- **Miembros de la API que fallaron** (nombre del miembro, versión de Revit, mensaje de error literal), para
  actualizar `herramientas-dev/miembros_por_verificar_revit.md` (en 0.4.0 están `por verificar`: `Ceiling.Create`,
  `CopyElement` repetido en `array`, `GetOrderedParameters` en la instantánea, `threading` + `File.Copy` en la copia
  diferida, `imp.load_source` y la recarga por `mtime`, `ViewSheet.SheetNumber` asignado dos veces, `uidoc` en
  `/macros/run/`, `VIEWER_SHEET_NUMBER` como `fields`).
- **Copia diferida**: `copia.ms` y `copia.espera_ms` de los pasos 8, 13 y 22 (si `espera_ms` es siempre 0 la copia
  terminó antes del Commit; si `estado` es `error`, el mensaje).
- **Nombres visibles** que aparecieron en las respuestas (`parameter_label`, categorías en `creados`), para confirmar
  que se conservan las tildes.
- Versión de Revit e idioma, y el resultado total de `probar_revit.py --fase cons` (`N/15`).
