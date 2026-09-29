# Prompt de validación de la entrega 2c (revit-mcp 0.6.0) para el agente local

Copia este texto tal cual en el agente que tiene conectado el MCP de Revit (Claude Desktop, Claude Code, Cursor...).
Requisitos previos, como en `VALIDACION_2B.md`: Revit abierto **en español** (`/language ESP`) **con una copia del
modelo de prueba** (nunca el original), la extensión de 0.6.0 cargada (recarga pyRevit: `revit_mcp/` corre dentro
de Revit) con Routes activo en 48884, el puente 0.6.0 arrancado con `uv run python main.py --combined` y el
cliente MCP reiniciado (para que cargue la lista nueva de **66** herramientas). Todo se hace sobre Modelo_Copia y
al final se cierra Revit **sin guardar**; las familias que se crean se guardan en `C:\IA\salidas\familias\` y se
borran al terminar.

---

**Validar la extensión MCP de Revit 0.6.0 (editor de familias)**

Trabaja solo sobre Modelo_Copia, nunca sobre el modelo original. No cambies el código de la extensión, el script de
arranque ni los archivos de macros. Usa las herramientas MCP **por su nombre** (no llames a las rutas HTTP salvo
donde se indique), ejecuta los pasos en orden, anota la respuesta **literal** (JSON completo o, si es muy largo, las
primeras 40 líneas) de cada uno y **el tiempo total de cada paso** (desde que decides llamar hasta que tienes la
respuesta; anota también `ms` y `ms_puente` de la respuesta). Reglas:

- **No marques OK lo que no viste**: lo que requiere mirar la interfaz de Revit (el editor de familias abierto,
  la geometría, el menú Deshacer) lo comprueba el usuario; tú lo dejas como `manual`.
- **No deduzcas causas que no comprobaste**: si algo falla, pega el error tal cual y no expliques por qué.
- **No muestres el token** (`%LOCALAPPDATA%\RevitMcp\token`) en ningún mensaje ni en el informe.
- No corrijas nada por tu cuenta ni reintentes con otros valores salvo donde el paso lo pide; si un paso falla,
  sigue con el siguiente.
- No uses nombres visibles en inglés: la plantilla se toma de `family_info(include_templates=true)`, las vistas se
  piden por `view_type`, los grupos son `GroupTypeId` (`Geometry`, `Materials`...) y la categoría un
  `BuiltInCategory`.
- No guardes el modelo en ningún momento (nada de `maintain_model(action="save")`). Guardar familias (`.rfa`) sí
  está permitido, en `C:\IA\salidas\familias\`.

## Preparación

1. En `C:\IA\pyrevit-ext\mcp-server-for-revit-python.extension` y en
   `C:\Users\Andy Bayona Antón\Proyectos\revit-mcp`: `git status --short` (si hay algún archivo con `M` que no sea
   `uv.lock`, detente y pregúntame; si es `uv.lock`, `git restore uv.lock`), `git fetch origin`, `git checkout main`,
   `git pull origin main` y `git log --oneline -3`. Entre esos commits debe estar el merge de la entrega 2c (0.6.0)
   y `revit_mcp\__init__.py` debe decir `0.6.4` (0.6.4: 400 al fijar un parámetro con fórmula aunque Revit no lo marque; 0.6.1: nombres con tildes que llegaban como `genÃ©rico`;
   0.6.2: tipo inicial para `SetFormula`, `LoadFamily` fuera de transacción y tabla de `is_reference`; 0.6.3: la
   validación ya no cuenta como vacíos los vaciados, que Revit da con volumen negativo). Si no, detente y avísame.
2. Anota la fecha de modificación y el tamaño de `C:\Users\Andy Bayona Antón\Desktop\Modelo_Copia.rvt`
   (`Get-Item ... | Select-Object LastWriteTime, Length`). Crea la carpeta `C:\IA\salidas\familias` si no existe.
3. Cierra Revit y ábrelo en español:
   `powershell -ExecutionPolicy Bypass -File "C:\Users\Andy Bayona Antón\open_interactive.ps1" -CommandLine '"C:\Program Files\Autodesk\Revit 2027\Revit.exe" /language ESP "C:\Users\Andy Bayona Antón\Desktop\Modelo_Copia.rvt"'`.
4. Sondea `GET http://127.0.0.1:48884/revit_mcp/ping/` sin token cada 2 s hasta 200 (máximo 3 minutos), relee el
   token, arranca el puente con `uv run python main.py --combined`, ejecuta `python pruebas\sync_schemas.py` y
   reinicia el cliente MCP.

## Pasos

1. Lista las herramientas que te ofrece el servidor → esperado: **66** nombres, entre ellos `family_info`,
   `family_open`, `family_add_parameters`, `family_add_reference_planes`, `family_add_dimensions`,
   `family_create_solids`, `family_lock_faces`, `family_set_type_values`, `family_add_connectors`, `family_save`,
   `family_load_into_project`, `family_close`, `family_validate`, `build_family_from_spec`; y **ninguno** de
   `family_new`, `family_add_parameter`, `family_create_extrusion`, `family_lock_face_to_plane`. Pega la lista.
2. `get_revit_status` → esperado: `status: active`, `document_title` con el nombre de la copia.
3. `family_info()` sin argumentos → esperado: `open_family_docs: []` (o los documentos de familia que el usuario
   tenga abiertos, con `opened_by_mcp: false`), `family_template_path` (carpeta de plantillas de ese Revit).
   Después `family_info(include_templates=true, contains="generico")` → esperado: `templates[]` con `name`,
   `path`, `folder`. Guarda como `PLANTILLA` el `name` de la plantilla genérica métrica **no** basada en cara
   (en español, "Modelo genérico métrico.rft"; toma el nombre exacto de la respuesta). Pega los nombres.
4. En una consola, `python pruebas\probar_revit.py --fase 2c` → esperado: `Resultado: 12/12 pruebas correctas`
   y salida 0. Pega el bloque de cada prueba 2c.1 a 2c.3 con su `[OK]` / `[FALLO]` / `NO_APLICA`, la plantilla que
   eligió el script y los `avisos` de 2c.1b. Si el script no encuentra plantilla, repite con
   `--template "PLANTILLA"`.
5. `family_open(template=PLANTILLA, name="Placa base 2C", simular=true)` → esperado: `simulado: true`,
   `haria[0].accion` = `nueva_familia` y `haria[0].template` con la ruta completa; sin `copia`. Sin `simular` →
   esperado: `ok: true`, `mode: new`, `family_doc` (anótalo como `FD`; en una familia nueva suele ser `Familia1`),
   `category` (`OST_GenericModel`), `categoria` en español, `reference_planes[]` con los planos de la plantilla
   (nombres en español; **pega `name`, `normal` e `is_reference` de cada uno**: confirman `ELEM_REFERENCE_NAME`;
   esperado "Centro (Izquierda/Derecha)" `center_left_right`, "Centro (Frontal/Posterior)" `center_front_back` y
   el plano horizontal `not_a_reference`),
   `views[]` con `view_type`, `level` y `direction` (**pega la lista**: confirma la lectura de `ViewDirection`),
   `levels[]` y `types[]` con **un** tipo llamado `Placa base 2C` (Revit crea la familia sin tipos y el MCP le da
   uno: sin él, `SetFormula` falla). Pídeme que compruebe en Revit (manual) que se abrió el editor de familias.
6. `family_add_parameters(family_doc=FD, parameters=[{"name": "Ancho", "data_type": "length", "group":
   "Geometry"}, {"name": "Largo", "data_type": "length", "group": "Geometry"}, {"name": "Espesor", "data_type":
   "length", "group": "Geometry"}, {"name": "Diámetro perno", "data_type": "length", "group": "Geometry"},
   {"name": "Diámetro agujero", "data_type": "length", "group": "Geometry", "formula": "Diámetro perno + 2 mm"},
   {"name": "Material placa", "data_type": "material", "group": "Materials"}, {"name": "Visible", "data_type":
   "yes_no", "group": "Graphics", "is_instance": true}], simular=true)` → esperado: `simulado: true`, `haria` con
   7 entradas `crear_parametro`, sin `copia`. Sin `simular` → esperado: `ok: true`, `count: 7`, `creados[]` con
   `data_type`, `group`, `formula` (`Diámetro perno + 2 mm` en el quinto), `nota_copia` (documento sin guardar),
   entrada `IA: Parametros de familia (7)` en el Deshacer del **editor de familias** (manual). Si `SetFormula`
   falla por el texto de la fórmula, pega el error: es el punto a verificar del idioma de las unidades.
7. `family_add_parameters(family_doc=FD, parameters=[{"name": "Ancho", "data_type": "length", "group":
   "Geometry"}])` → esperado: `409` con `existing`. Después `family_add_parameters(family_doc=FD,
   parameters=[{"name": "Mitad", "data_type": "length", "group": "Geometry", "formula": "Ancho / 2 + Alto"}])` →
   esperado: `400` con `undefined: ["Alto"]` e `index: 0`.
8. `family_add_reference_planes(family_doc=FD, planes=[{"name": "Izquierda", "origin_mm": {"x": -150, "y": 0,
   "z": 0}, "direction": "y", "is_reference": "left"}, {"name": "Derecha", "origin_mm": {"x": 150, "y": 0, "z": 0},
   "direction": "y", "is_reference": "right"}, {"name": "Delante", "origin_mm": {"x": 0, "y": -150, "z": 0},
   "direction": "x", "is_reference": "front"}, {"name": "Detrás", "origin_mm": {"x": 0, "y": 150, "z": 0},
   "direction": "x", "is_reference": "back"}, {"name": "Cara superior", "origin_mm": {"x": 0, "y": 0, "z": 20},
   "direction": "horizontal", "is_reference": "top"}], simular=true)` → esperado: `haria[]` con `normal`
   (`{x: 1}` para `y`, `{y: -1}` para `x`, `{z: 1}` para `horizontal`) y `view` (planta para los cuatro primeros,
   alzado para el último). Sin `simular` → esperado: `ok: true`, `count: 5`, `creados[]` con `origin_mm`,
   `normal`, `is_reference` (**pega `is_reference` de los cinco**: confirma la tabla de índices), `avisos: []`,
   entrada `IA: Planos de referencia (5)`.
9. `family_add_dimensions(family_doc=FD, dimensions=[{"reference_planes": ["Izquierda", "Derecha"],
   "parameter": "Ancho"}, {"reference_planes": ["Delante", "Detrás"], "parameter": "Largo"}], simular=true)` →
   esperado: `haria[]` con `length_mm: 300`, `view.view_type: FloorPlan`, `line_mm`. Sin `simular` → esperado:
   `ok: true`, `count: 2`, `creados[]` con `label` (`Ancho`, `Largo`), entrada `IA: Cotas con etiqueta (2)`.
   Pídeme que compruebe (manual) que las dos cotas aparecen en la planta con sus etiquetas.
10. `family_add_dimensions(family_doc=FD, dimensions=[{"reference_planes": ["Izquierda", "Delante"],
    "parameter": "Ancho"}])` → esperado: `400` "must be parallel".
11. `family_create_solids(family_doc=FD, solids=[{"name": "placa", "kind": "extrusion", "sketch_plane":
    {"view_type": "FloorPlan"}, "profile": {"rect": {"min_mm": {"x": -150, "y": -150, "z": 0}, "max_mm": {"x": 150,
    "y": 150, "z": 0}}}, "start_mm": 0, "end_mm": 20, "material_parameter": "Material placa", "lock_ends_to":
    {"end": "Cara superior"}, "lock_faces": [{"face": "left", "reference_plane": "Izquierda"}, {"face": "right",
    "reference_plane": "Derecha"}, {"face": "front", "reference_plane": "Delante"}, {"face": "back",
    "reference_plane": "Detrás"}]}, {"name": "agujero 1", "kind": "extrusion", "is_void": true, "sketch_plane":
    {"view_type": "FloorPlan"}, "profile": {"circle": {"center_mm": {"x": 100, "y": 100, "z": 0}, "radius_mm":
    11}}, "start_mm": -5, "end_mm": 30}], simular=true)` → esperado: `haria[0].accion` = `crear_solido` con
    `profile[0].points_mm` (4 puntos) y `haria[1].accion` = `crear_vaciado` con `profile[0].circle`. Sin
    `simular` → esperado: `ok: true`, `count: 2`, `creados[0]` con `kind: extrusion`, `is_void: false`,
    `volume_m3` > 0, `start_mm: 0`, `end_mm: 20`, `material_parameter: "Material placa"`; `creados[1]` con
    `is_void: true`, `start_mm: -5`, `end_mm: 30`; **pega `avisos` completos** (un bloqueo o el material que no se
    pudo aplicar salen ahí: son `NewAlignment` y `AssociateElementParameterToFamilyParameter`, por verificar);
    entrada `IA: Solidos de familia (2)`. Guarda `creados[0].id` como `PLACA`. Pídeme que compruebe (manual) que
    la placa y el agujero se ven en 3D y que los candados de las caras están puestos.
12. `family_lock_faces(family_doc=FD, locks=[{"solid_id": PLACA, "face": "bottom", "reference_plane":
    "<nombre del plano de referencia horizontal de la plantilla, tomado del paso 5>"}], simular=true)` →
    esperado: `haria[0]` con `face`, `reference_plane`, `view` (alzado). Sin `simular` → esperado: `ok: true`,
    `count: 1`, entrada `IA: Bloquear caras (1)`. Si responde `400` "No 'bottom' face with a reference", pégalo
    (es `get_Geometry` con `ComputeReferences` en el documento de familia, por verificar). Si la plantilla no
    tiene plano horizontal, "no aplica".
13. `family_set_type_values(family_doc=FD, types=[{"type_name": "PL300x300x20", "values": {"Ancho": 300, "Largo":
    300, "Espesor": 20, "Diámetro perno": 20}}, {"type_name": "PL400x400x25", "values": {"Ancho": 400, "Largo":
    400, "Espesor": 25, "Diámetro perno": 24}}], simular=true)` → esperado: `haria[]` con `accion: crear_tipo` y
    `values` con `"300 mm"`. Sin `simular` → esperado: `ok: true`, `count: 2`, `types[]` con `created: true`,
    `antes` (nulos) y `despues` (`Ancho: 300`...), `fallidos: []`, `family_types[]` con los dos tipos y el inicial
    (`Placa base 2C`), entrada `IA: Tipos de familia (2)`. Después `family_set_type_values(family_doc=FD,
    types=[{"type_name": "PL300x300x20", "values": {"Diámetro agujero": 30}}])` → esperado: `400` "determined by
    a formula".
14. `family_info(family_doc=FD)` → esperado: `counts` = 7 parámetros, 3 tipos, 7 planos (5 propios + los de la
    plantilla; anota el número real), 2 sólidos; `types[]` con `values` en mm (`PL300x300x20.Ancho: 300`,
    `Diámetro agujero: 22` por la fórmula), `dimensions[]` con `label`, `solids[]` con `material_parameter`.
    Pega `types` completo.
15. `family_validate(family_doc=FD, simular=true)` → esperado: `haria[]` con un caso por tipo (3) y `solids[]`
    actuales. Sin `simular` → esperado: `ok: true`, `passed: 3`, `failed_cases: []`, `cases[].solids[]` con
    `volume_m3` > 0 en la placa; `restored: true`. Después `family_validate(family_doc=FD, flex_cases=[{"name":
    "espesor 2", "type": "PL300x300x20", "values": {"Espesor": 2}}, {"name": "ancho 0", "type": "PL300x300x20",
    "values": {"Ancho": 0}}])` → esperado: `200`; **pega `cases` completo** (`ok`, `motivo`, `empty_solids`,
    `revit_errors`): el caso `ancho 0` debería fallar (Revit no puede regenerar) y `espesor 2` pasar. Pídeme que
    compruebe (manual) que el tipo actual de la familia no cambió (los grupos se revirtieron) y que en el Deshacer
    del editor no quedan entradas `IA: Validar ...`.
16. `family_save(family_doc=FD, file_path="C:\IA\salidas\familias\Placa base 2C.rfa", simular=true)` →
    esperado: `haria[0]` con `exists: false`. Sin `simular` → esperado: `ok: true`, `family_doc: "Placa base 2C"`,
    `previous_family_doc` = `FD`, `size_kb` > 0, el archivo existe. A partir de aquí usa `FD2 = "Placa base 2C"`.
    Repite sin `overwrite` → esperado: `409` con `exists: true`. Repite con `overwrite=true` → esperado:
    `ok: true`, `overwritten: true`, `copia.ruta` termina en `.rfa` (copia del `.rfa` guardado en
    `C:\IA\salidas\familias\backups\`).
17. `family_load_into_project(family_doc=FD2, simular=true)` → esperado: `haria[0]` con `accion:
    cargar_en_proyecto`, `already_loaded: false`. Sin `simular` → esperado: `ok: true`, `family: "Placa base 2C"`,
    `family_id`, `types[]` con 3 tipos, `creados[]` con `categoria` en español, entrada `IA: Cargar familia Placa
    base 2C` en el Deshacer **del proyecto** (manual), `avisos: []` (si dice "cargada sin grupo", pégalo: Revit no
    admitió el `TransactionGroup` y la entrada del Deshacer tendrá el nombre de Revit). Guarda `family_id` como `FAMILIA`. Repite sin
    `overwrite_parameters` → esperado: `409` con `already_loaded: true` y `types`. Repite con
    `overwrite_parameters=true` → esperado: `ok: true`, `reloaded: true`. Si `LoadFamily` falla, pega el error
    literal (es el punto a verificar de `IFamilyLoadOptions` en IronPython).
18. `list_types(category="OST_GenericModel", family="Placa base 2C")` → esperado: los 3 tipos de la familia con
    `familia: "Placa base 2C"` (la familia se creó con la categoría de la plantilla; si el paso 5 dio otra
    `category`, usa esa).
19. `family_close(family_doc=FD2, simular=true)` → esperado: `haria[0]` con `save: false` y `file_path`. Sin
    `simular` → esperado: `ok: true`, `closed: true`, `open_family_docs` sin `Placa base 2C`. Después
    `family_info(family_doc=FD2)` → esperado: `404` con `available_family_docs`. Pídeme que compruebe (manual)
    que el editor de familias se cerró y el proyecto sigue abierto.
20. `family_open(family_name="Placa base 2C")` (EditFamily de la familia cargada) → esperado: `ok: true`,
    `mode: edit`, `family_doc` (anótalo como `FD3`), `parameters[]` con los 7 parámetros y `types[]` con los 3
    tipos. Después `family_close(family_doc=FD3)` → esperado: `closed: true`. Y `family_open(family_name="<una
    familia del sistema, por ejemplo el nombre de un tipo de muro de list_types(category="OST_Walls")>")` →
    esperado: `404` con `available_families`.
21. `build_family_from_spec(spec=<el ejemplo 1 de CONTRATO.md con "name": "Placa base spec 2C" y "template":
    PLANTILLA>, save_path="C:\IA\salidas\familias\Placa base spec 2C.rfa", load_into_project=true, simular=true)` →
    esperado: `simulado: true`, `plan.counts` = `{parameters: 6, reference_planes: 5, dimensions: 2, solids: 1,
    voids: 4, connectors: 0, types: 2, flex_cases: 2}`, `plan.steps` = `[open, category, parameters,
    reference_planes, dimensions, solids, types, validate]`, sin `copia`; `family_info()` no muestra ningún
    documento nuevo. Después cambia en el `spec` la fórmula por `"Diámetro tornillo + 2 mm"` y repite con
    `simular=true` → esperado: `400` con `spec_error: true`, `section: "parameters"`, `index: 4`, `undefined:
    ["Diámetro tornillo"]`, y nada abierto. Y con la fórmula correcta pero `"template": "Metric Generic
    Model.rft"` → esperado: `404` con `section: "template"` y `available_templates`.
22. El paso 21 con el `spec` correcto y **sin** `simular` → esperado: `ok: true`, `steps[]` con `open`,
    `category` (`categoria: "Conexiones estructurales"` o el nombre en español de `OST_StructConnections`),
    `parameters` (6), `reference_planes` (5), `dimensions` (2), `solids` (5), `types` (2), `validate` (`passed:
    2`), `save`, `load`; `family_doc: "Placa base spec 2C"`, `file_path`, `family_id` (guárdalo como `FAMILIA2`),
    `loaded_types[]` (3), `summary.counts`, **`avisos` completo**. Anota `ms` y `ms_puente`. Si responde `500`
    con `failed_step`, pega `failed_step`, `steps` y el `error` literal, y comprueba con `family_info()` que
    `closed_without_saving` se cumplió (ningún documento nuevo abierto) y que el `.rfa` no existe.
23. `family_close(family_doc="Placa base spec 2C")` → esperado: `closed: true`.
24. `read_log(last_n=30)` → esperado: entradas de `/family/open/`, `/family/parameters/`,
    `/family/reference_planes/`, `/family/dimensions/`, `/family/solids/`, `/family/locks/`, `/family/types/`,
    `/family/validate/`, `/family/save/`, `/family/load/`, `/family/close/`, `/family/build/` con `ok: true` y
    las simuladas con `simulado: true`; las de `/family/info/` no aparecen (lectura).
25. Detente y pídeme que revise el desplegable de Deshacer **del proyecto**. Escríbeme la lista de entradas
    `IA: ...` que deberían aparecer (`IA: Cargar familia Placa base 2C` una o dos veces según el paso 17,
    `IA: Cargar familia Placa base spec 2C`) y **ninguna** de las del editor de familias (esas vivían en cada
    documento de familia, ya cerrado). Espera mi respuesta.
26. Limpieza: `delete_elements(element_ids=[FAMILIA, FAMILIA2])` → esperado: `ok: true` (borrar la `Family`
    quita sus tipos del proyecto). Cierra Revit sin guardar (`taskkill /IM Revit.exe /F`), comprueba que la fecha y
    el tamaño de Modelo_Copia.rvt son los de la preparación 2 y borra `C:\IA\salidas\familias\`.

## Informe

Entrega una tabla con una fila por paso (preparación y 1 a 26):

| Paso | Herramienta | Tiempo total del paso | `ms` / `ms_puente` | Resultado (OK / FALLO / no probado / no aplica / manual) | Respuesta literal (recortada a 40 líneas) | Observaciones |
|---|---|---|---|---|---|---|

Y debajo:

- **Miembros de la API que fallaron** (nombre del miembro, versión de Revit, mensaje de error literal), para
  actualizar `herramientas-dev/miembros_por_verificar_revit.md`: en la 0.6.0 están `por verificar`
  `Application.NewFamilyDocument`, `FamilyTemplatePath`, `doc.EditFamily`, `FamilyManager.AddParameter` (las tres
  sobrecargas), `GroupTypeId`, `SpecTypeId.Int/String/Boolean/Reference`, `SetFormula` con unidades en la
  fórmula, `NewReferencePlane` + `ELEM_REFERENCE_NAME` (tabla de índices), `View.ViewDirection` (signo de
  `direction`), `NewDimension` + `FamilyLabel`, `SketchPlane.Create` por referencia y por nivel, `NewExtrusion` +
  `EXTRUSION_START/END_PARAM`, `Arc.Create`, `get_Geometry` con `ComputeReferences` en la familia, `NewAlignment`,
  `AssociateElementParameterToFamilyParameter`, `NewType` / `CurrentType` / `Set`, `TransactionGroup` +
  `Regenerate` en la familia, `SaveAs` + `OverwriteExistingFile`, `IFamilyLoadOptions` en IronPython +
  `LoadFamily(proyecto)`, `Document.Close`.
- **Lo que quedó `no_soportado`** (`409`) con el `motivo` literal (parámetros compartidos sin archivo activo,
  `GroupTypeId` ausente...).
- **Nombres visibles** que aparecieron (categorías, planos de la plantilla, vistas, `categoria` del `spec`), para
  confirmar que se conservan las tildes.
- **Menú Deshacer** (paso 25): la lista que confirmó el usuario.
- Versión de Revit e idioma, y el resultado total de `probar_revit.py --fase 2c` (`N/12`).
