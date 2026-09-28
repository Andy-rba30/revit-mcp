# Prompt de validación de la entrega 2b (revit-mcp 0.5.0) para el agente local

Copia este texto tal cual en el agente que tiene conectado el MCP de Revit (Claude Desktop, Claude Code, Cursor...).
Requisitos previos, como en `VALIDACION_CONS.md`: Revit abierto **en español** (`/language ESP`) **con una copia del
modelo de prueba** (nunca el original), la extensión de 0.5.0 cargada (recarga pyRevit: `revit_mcp/` corre dentro
de Revit) con Routes activo en 48884, el puente 0.5.0 arrancado con `uv run python main.py --combined` y el
cliente MCP reiniciado (para que cargue la lista nueva de **52** herramientas). Todo se hace sobre Modelo_Copia y al
final se cierra Revit **sin guardar**, así que nada de lo que se cree queda en el archivo.

---

**Validar la extensión MCP de Revit 0.5.0 (estructuras metálicas y modelo analítico)**

Trabaja solo sobre Modelo_Copia, nunca sobre el modelo original. No cambies el código de la extensión, el script de
arranque ni los archivos de macros. Usa las herramientas MCP **por su nombre** (no llames a las rutas HTTP salvo
donde se indique), ejecuta los pasos en orden, anota la respuesta **literal** (JSON completo o, si es muy largo, las
primeras 40 líneas) de cada uno y **el tiempo total de cada paso** (desde que decides llamar hasta que tienes la
respuesta; anota también `ms` y `ms_puente` de la respuesta). Reglas:

- **No marques OK lo que no viste**: lo que requiere mirar la interfaz de Revit (menú Deshacer, geometría) lo
  comprueba el usuario; tú lo dejas como `manual`.
- **No deduzcas causas que no comprobaste**: si algo falla, pega el error tal cual y no expliques por qué.
- **No muestres el token** (`%LOCALAPPDATA%\RevitMcp\token`) en ningún mensaje ni en el informe.
- No corrijas nada por tu cuenta ni reintentes con otros valores; si un paso falla, sigue con el siguiente.
- No uses nombres visibles en inglés: toma los nombres de niveles, rejillas, tipos y familias de
  `get_revit_model_info(include=["levels"])`, `query_elements(category="OST_Grids")`, `list_steel_profiles` y
  `list_types`. Los perfiles se nombran como `"Familia: Tipo"` tal como los devuelve `list_steel_profiles`
  (`family` y `type`).
- No guardes el modelo en ningún momento (nada de `maintain_model(action="save")`).

## Preparación

1. En `C:\IA\pyrevit-ext\mcp-server-for-revit-python.extension` y en
   `C:\Users\Andy Bayona Antón\Proyectos\revit-mcp`: `git status --short` (si hay algún archivo con `M` que no sea
   `uv.lock`, detente y pregúntame; si es `uv.lock`, `git restore uv.lock`), `git fetch origin`, `git checkout main`,
   `git pull origin main` y `git log --oneline -3`. Entre esos commits debe estar el merge de la entrega 2b (0.5.0)
   y `revit_mcp\__init__.py` debe decir `0.5.0`.
2. Anota la fecha de modificación y el tamaño de `C:\Users\Andy Bayona Antón\Desktop\Modelo_Copia.rvt`
   (`Get-Item ... | Select-Object LastWriteTime, Length`).
3. Cierra Revit y ábrelo en español:
   `powershell -ExecutionPolicy Bypass -File "C:\Users\Andy Bayona Antón\open_interactive.ps1" -CommandLine '"C:\Program Files\Autodesk\Revit 2027\Revit.exe" /language ESP "C:\Users\Andy Bayona Antón\Desktop\Modelo_Copia.rvt"'`.
4. Sondea `GET http://127.0.0.1:48884/revit_mcp/ping/` sin token cada 2 s hasta 200 (máximo 3 minutos), relee el
   token, arranca el puente con `uv run python main.py --combined`, ejecuta `python pruebas\sync_schemas.py` y
   reinicia el cliente MCP.

## Paso previo: perfiles de acero

P1. `list_steel_profiles(loaded_only=true)` → esperado: `loaded[]` con `family`, `type`, `category`
    (`OST_StructuralFraming` / `OST_StructuralColumns`), `shape`, `standard`, `dimensions_mm`, `instances`,
    `steel_by`, y `no_disponibles` (anótalo si no está vacío). Si hay al menos un perfil de pilar
    (`OST_StructuralColumns`) y uno de viga (`OST_StructuralFraming`), guarda `"family: type"` del primero de cada
    uno como `PILAR` y `VIGA` y salta a los pasos.
P2. Si falta alguno: `list_steel_profiles(loaded_only=false)` → esperado: `library[]` con `family`, `path`,
    `catalog_path`, `types[]`, `shapes`, `standards`, y `library_paths`. Elige una familia de pilar y otra de viga
    (por ejemplo `HEB` / `IPE` de la biblioteca métrica) y anota dos tipos de su `types[]`.
P3. `load_steel_profile(family_name="<familia>", type_names=["<tipo>"], simular=true)` → esperado: `simulado: true`,
    `haria[0].accion` = `cargar_tipo` (con catálogo) o `cargar_familia`, sin `copia`. Repite sin `simular` →
    esperado: `ok: true`, `creados[]` con el tipo cargado (`categoria` en español), entrada `IA: Cargar perfil
    <familia>` en Deshacer. Hazlo para la viga y para el pilar; repite `list_steel_profiles(loaded_only=true)` y
    guarda `PILAR` y `VIGA`. Si la carga falla, pega el error, marca los pasos 8 a 22 como "no probado" y sigue
    con los que no necesiten perfiles.

## Pasos

1. Lista las herramientas que te ofrece el servidor → esperado: **52** nombres, entre ellos `list_steel_profiles`,
   `steel_quantities`, `load_steel_profile`, `create_steel_frame`, `create_bracing`, `create_truss`,
   `set_structural_properties`, `create_steel_connection`, `add_plate_or_stiffener`, `split_beam`,
   `analytical_status`, `fix_analytical_alignment`; y **ninguno** de `get_structural_properties`,
   `list_connection_types`, `join_steel_elements`, `export_structural_model`, `set_parameter`. Pega la lista.
2. `get_revit_status` → esperado: `status: active`, `document_title` con el nombre de la copia.
3. `get_revit_model_info(include=["levels"])` → esperado: `levels.levels[]` con `elevation_mm`. Guarda el nivel más
   bajo como `NIVEL` y el siguiente como `NIVEL2`.
4. En una consola, `python pruebas\probar_revit.py --fase 2b` → esperado: `Resultado: 14/14 pruebas correctas` y
   salida 0. Pega el bloque de cada prueba 2b.1 a 2b.5 con su `[OK]`, `[FALLO]` o `NO_APLICA`. Si 2b.3b dice que
   ningún elemento tiene modelo analítico, anótalo (Revit 2023+ no lo crea salvo con la automatización analítica).
5. `query_elements(category="OST_Grids", page_size=50)` → esperado: `elements[]` con `nombre`. Anota dos rejillas
   con X constante (verticales en planta) como `GX1`, `GX2` y dos con Y constante como `GY1`, `GY2`. Si no las
   distingues o no hay rejillas rectas, crea una rejilla auxiliar con
   `create_grid_and_levels(x_spacings_mm=[6000], y_spacings_mm=[5000], x_names="MCP2B1", y_names="MCPY1",
   origin_mm={"x": 200000, "y": 200000, "z": 0}, extension_mm=1000)` y usa esos nombres (bórrala al final).
6. `create_steel_frame(column_type=PILAR, beam_type=VIGA, grids_x=[GX1, GX2], grids_y=[GY1, GY2], levels=[NIVEL],
   mark_prefix="MCP2B-", simular=true)` → esperado: `simulado: true`, `plan.counts` = `{columns: 4, beams: 4,
   total: 8}`, `plan.intersections` con 4 etiquetas (`"GY1-GX1"`...), `plan.columns[].top_level` = `NIVEL2`,
   `plan.beams[]` con `start_mm`/`end_mm`, `haria` con 8 entradas y sin `copia`. Anota `plan.warnings`.
7. El paso 6 sin `simular` → esperado: `ok: true`, `count: 8`, `creados.columns` (4, con `label`, `top_level`,
   `point_mm`, `mark` `MCP2B-01`...) y `creados.beams` (4, con `from`, `to`, `mark`), `creados_ids` con 8 ids,
   `copia`, y **una sola** entrada `IA: Portico metalico` en Deshacer (manual). Guarda `PILARES` (4 ids) y
   `VIGAS` (4 ids). Anota `ms` y `ms_puente`.
8. `describe_element(element_id=VIGAS[0], include_structural=true)` → esperado: bloque `structural` con
   `structural_material`, `structural_material_type` (`Steel`), `is_steel: true`, `releases.start`/`end` (con
   `source` `BuiltInParameter` o `AnalyticalMember`, o `null` si no hay ninguno), `y_justification`,
   `z_justification`, `y_offset_mm`, `z_offset_mm`, `section_rotation_deg`, `start_extension_mm`,
   `end_extension_mm`, `analyze_as`, y las listas `no_disponibles` y `no_aplica`. **Pega las dos listas
   completas**: son la corrección de `miembros_por_verificar_revit.md`.
9. `set_structural_properties(element_ids=VIGAS, start_release="pinned", end_release={"FX": true, "MZ": true},
   y_justification="center", z_offset_mm=-50, simular=true)` → esperado: `simulado: true`, `haria[]` con
   `property`, `builtin`, `antes`, `despues` (por ejemplo `-50 mm`), `fallidos` y `no_disponibles`, sin `copia`.
10. El paso 9 sin `simular` → esperado: `ok: true`, `count: 4`, `changes[]` con `coincide: true`, `despues` por
    elemento, entrada `IA: Propiedades estructurales (4 elementos)`. Pega `fallidos` y `no_disponibles` aunque estén
    vacíos. Después `describe_element(element_id=VIGAS[0], include_structural=true)` → esperado:
    `releases.start.type` = `pinned`, `releases.end.FX` y `MZ` `true`, `z_offset_mm: -50`.
11. `create_bracing(bays=[{"start_point_mm": <point_mm del pilar GY1-GX1>, "end_point_mm": <point_mm del pilar
    GY1-GX2>, "level_bottom": NIVEL, "level_top": NIVEL2, "pattern": "X"}], brace_type=VIGA, simular=true)` →
    esperado: `count: 2`, `plan.bays[0].height_mm` = diferencia de cotas entre `NIVEL2` y `NIVEL`, `haria[]` con
    `start_mm`/`end_mm` (z = elevación interna de los niveles). Sin `simular` → esperado: `ok: true`,
    `creados[0].braces` con 2 elementos de categoría `Armazón estructural`, entrada `IA: Crear 2 arriostres`.
    Guarda sus ids como `ARRIOSTRES`.
12. `create_truss(trusses=[{"truss_type": "x", "start_point_mm": {"x": 0, "y": 0}, "end_point_mm": {"x": 12000,
    "y": 0}, "level": NIVEL2}])` → esperado: `404` con `available_types` (los tipos de cercha cargados) o `404` con
    `available_types: []` si no hay ninguno. Si hay alguno, repite con ese `truss_type` y `simular=true` →
    esperado: `haria[0].element_type` = `truss`; sin `simular` → `creados[0].categoria` (cercha) y entrada
    `IA: Crear 1 cerchas`; guarda el id como `CERCHA`. Si no hay tipos, "no aplica".
13. `list_types(category="connections")` → esperado: `409` con `no_soportado: true` y `motivo` (`api` o
    `sin_tipos`) si el módulo Steel Connections no está instalado o no hay tipos cargados; o `types[]` y
    `approval_types[]` si lo está. En el segundo caso, `create_steel_connection(connections=[{"element_ids":
    [PILARES[0], VIGAS[0]], "connection_type": "<types[0].tipo>"}], simular=true)` → esperado: `haria[0].accion`
    = `conectar`; sin `simular` → `creados[0]` con `connection_type`, entrada `IA: Crear 1 conexiones`; guarda el
    id como `CONEXION`. Después `create_steel_connection(connections=[{"element_ids": [PILARES[1], VIGAS[1]],
    "connection_type": "<types[0].tipo>"}], approve=true)` → esperado: `400` con `available_approval_types`; repite
    con `approval_status="<un nombre de esa lista>"` → esperado: `creados[0].approval_status` con ese nombre.
14. `list_types(category="OST_GenericModel", loaded_only=false)` → busca una familia de modelo genérico
    (preferiblemente basada en cara). Si hay una, `add_plate_or_stiffener(host_id=VIGAS[0], family_name="<su
    familia>", type_name="<su tipo>", positions=[0.5], face="top", simular=true)` → esperado: `haria[0]` con
    `hosted_on_face`, `placement_type`, `point_mm` (z en la cara superior de la viga si es alojada en cara, o
    sobre el eje si es de punto). Sin `simular` → esperado: `creados[0]` con `position_mm` y `point_mm`, entrada
    `IA: Colocar 1 <familia> en <id>`; guarda el id como `PLACA`. Si responde `400` "No top face with a reference",
    pégalo (es el punto a verificar de `get_Geometry` con `ComputeReferences`) y prueba con `face="web"`. Si no
    hay familias genéricas, "no aplica".
15. `split_beam(element_id=VIGAS[1], at_mm=[2000], simular=true)` → esperado: `haria[0].segments` con 2 tramos y
    `avisos` (uniones y conexiones). Sin `simular` → esperado: `ok: true`, `creados[0]` (tramo nuevo con
    `start_mm`/`end_mm`), `original.despues.length_mm` = 2000 (si Revit reajustó los extremos por la unión
    automática, anota la diferencia), entrada `IA: Dividir viga <id>`. Guarda el id nuevo como `TRAMO`.
16. `join_geometry(element_ids=[PILARES[0], VIGAS[0]], coping=true, simular=true)` → esperado: `haria[0]` con
    `antes.joined` y `coping: true`. Sin `simular` → esperado: `ok: true`, `pairs[0].despues.joined: true`,
    `coping.applied` (o `coping.failed` con el error literal de `AddCoping`: es un miembro por verificar), entrada
    `IA: Unir geometria en cadena (2 elementos)`.
17. `analytical_status(element_ids=PILARES + VIGAS + ARRIOSTRES)` → esperado: `elements[]` por id con
    `analytical_member_id`, `nodes.start/end` (`point_mm`, `connected`, `touching`, `is_connected`),
    `loose_nodes`, y el resumen `members`, `sin_analitico`, `loose_nodes_total`, `analytical_members_in_model`.
    Si `members` es 0 anótalo (sin modelo analítico no hay nodos que comprobar) y marca el paso 18 como
    "no aplica".
18. `fix_analytical_alignment(element_ids=VIGAS, tolerance_mm=50, simular=true)` → esperado: `haria[]` con los
    movimientos (`from_mm`, `to_mm`, `distance_mm`, `target_element_id`) o vacío, `sin_objetivo[]` con
    `nearest_mm`, `plan.counts`. Si hay movimientos, ejecuta sin `simular` → esperado: `ok: true`, `antes`/`despues`
    por nodo, entrada `IA: Alinear analitico`; repite `analytical_status` sobre esas vigas → `loose_nodes_total`
    menor. Si no hay movimientos, anota `count: 0` (correcto: nada que alinear).
19. `steel_quantities(group_by="type", element_ids=PILARES + VIGAS)` → esperado: `groups[]` con `count`,
    `length_mm` (8 elementos; los pilares por `INSTANCE_LENGTH_PARAM` o caja envolvente), `weight_kg` > 0 con
    `methods` (`volumen x densidad` o `masa lineal`), o los ids en `sin_peso` con `motivo`. Pega `metodo` y
    `sin_peso` completos. Después `steel_quantities(group_by="level")` sin ids → esperado: todo el acero del
    modelo agrupado por nivel, `totals` y `truncated`.
20. `export(format="csv_nodes_members", file_path="<carpeta de la copia>\miembros_0.5.csv",
    element_ids=PILARES + VIGAS)` → esperado: `rows: 8`, `columns[]`, `sin_analitico[]`; el archivo existe y su
    primera línea es la cabecera. Pega las 3 primeras líneas del CSV.
21. `export(format="ifc_structural", file_path="<carpeta de la copia>\estructura_0.5.ifc")` → esperado:
    `file_path`, `file_size_kb` > 0, `export_base_quantities: true`, `filter_view` (la vista activa si muestra el
    modelo analítico, o `null` con `filter_view_reason`). Anota `ms`.
22. `read_log(last_n=25)` → esperado: entradas de `/load_steel_profile/`, `/create_steel_frame/`,
    `/set_structural_properties/`, `/create_bracing/`, `/split_beam/`, `/join_geometry/` (y `/fix_analytical/`,
    `/create_steel_connection/`, `/add_plate/`, `/create_truss/` si se ejecutaron) con `ok: true` y las simuladas
    con `simulado: true`; ninguna de `/steel_profiles/`, `/steel_quantities/`, `/analytical_status/`,
    `/export_structural/` ni `/describe/`.
23. Detente y pídeme que revise el desplegable de Deshacer de Revit. Escríbeme la lista de entradas `IA: ...` que
    deberían aparecer según las escrituras reales que hiciste (`IA: Cargar perfil ...`, `IA: Portico metalico`,
    `IA: Propiedades estructurales (4 elementos)`, `IA: Crear 2 arriostres`, `IA: Dividir viga ...`,
    `IA: Unir geometria en cadena (2 elementos)`...) y **ninguna** entrada por elemento. Espera mi respuesta.
24. Limpieza: `delete_elements(element_ids=[...])` con `PILARES`, `VIGAS`, `ARRIOSTRES`, `TRAMO` y, si existen,
    `CERCHA`, `CONEXION`, `PLACA` y la rejilla auxiliar del paso 5 → esperado: `ok: true`. Cierra Revit sin guardar
    (`taskkill /IM Revit.exe /F`) y comprueba que la fecha y el tamaño de Modelo_Copia.rvt son los de la
    preparación 2.

## Informe

Entrega una tabla con una fila por paso (preparación, P1-P3 y 1 a 24):

| Paso | Herramienta | Tiempo total del paso | `ms` / `ms_puente` | Resultado (OK / FALLO / no probado / no aplica / manual) | Respuesta literal (recortada a 40 líneas) | Observaciones |
|---|---|---|---|---|---|---|

Y debajo:

- **Miembros de la API que fallaron** (nombre del miembro, versión de Revit, mensaje de error literal), para
  actualizar `herramientas-dev/miembros_por_verificar_revit.md`: en la 0.5.0 están `por verificar`
  `Family.StructuralMaterialType`, `StructuralAsset.Density` (y sus unidades), `GetStructuralSection`, los
  `BuiltInParameter` de sección y de propiedades estructurales (`no_disponibles` / `no_aplica` del paso 8),
  `LoadFamilySymbol` / `LoadFamily` dentro de transacción, `Truss.Create`, `SketchPlane.Create(nivel)`,
  `AnalyticalToPhysicalAssociationManager`, `AnalyticalMember.GetCurve/SetCurve/SetReleaseType`,
  `StructuralConnectionHandler.Create`, `NewFamilyInstance(Reference, ...)`, `AddCoping`, `CopyElement` +
  `LocationCurve.Curve` en vigas y `View.AreAnalyticalModelCategoriesHidden`.
- **Lo que quedó `no_soportado`** (`409`: conexiones de acero, modelo analítico) con el `motivo` literal.
- **Nombres visibles** que aparecieron en las respuestas (categorías de `creados`, `parameter_label`, materiales),
  para confirmar que se conservan las tildes.
- **Menú Deshacer** (paso 23): la lista que confirmó el usuario.
- Versión de Revit e idioma, y el resultado total de `probar_revit.py --fase 2b` (`N/14`) con los `NO_APLICA`.
