# Prompt de la entrega 2b (revit-mcp 0.5.0): estructuras metálicas y modelo analítico

Eres un agente de programación con acceso al repositorio `revit-mcp`. Está en `main` la versión **0.4.3**, validada
en un Revit 2027 en español: 40 herramientas MCP, lotes en una transacción (`/set_parameters/`,
`/create_elements/`), macros propias (`/macros/`), copia diferida del `.rvt`, 279 pruebas en CPython que GitHub
ejecuta en cada PR (`.github/workflows/pruebas.yml`). Esta entrega es el **Bloque B** de
`herramientas-dev/PROMPT_FASE2.md`, adaptado a las reglas que la consolidación dejó vigentes. Donde este documento
y el bloque B original difieran, manda este documento.

Antes de escribir código lee: `CONTRATO.md`, `INSTRUCCIONES_AGENTE.md`, `herramientas-dev/PROMPT_FASE2.md`
(Bloque 0, "Reglas vigentes" y Bloque B), `herramientas-dev/PROMPT_CONSOLIDACION.md` (por qué hay pocas
herramientas y lotes), `herramientas-dev/VALIDACION_CONS.md` y `VALIDACION_PENDIENTES.md` (formato de validación),
`herramientas-dev/miembros_por_verificar_revit.md`, `revit_mcp/escritura.py`, `revit_mcp/lotes.py`,
`revit_mcp/estructural.py`, `revit_mcp/structure.py`, `revit_mcp/navegacion.py`, `revit_mcp/utils.py`,
`tools/__init__.py`, `tools/escritura_tools.py`, `tools/lectura_tools.py`, `tests/fakes/` y `tests/test_lotes.py`.

Rama `feature/fase2b-acero` desde `main`, versión **0.5.0** en `pyproject.toml` y `revit_mcp/__init__.py`. Un
commit por bloque. `uv run --with pytest python -m pytest -q` antes de cada commit; ninguna prueba existente puede
dejar de pasar. No hagas commit de `uv.lock` si solo cambió por `--with pytest`.

## Reglas que manda la consolidación (además de las de PROMPT_FASE2.md)

- **Pocas herramientas, de alto nivel.** El bloque B original listaba 15; aquí son **11 nuevas** y 4 ampliaciones
  de herramientas existentes. Al terminar, el servidor MCP expone 51. Cada descripción con menos de 60 palabras y
  un ejemplo. Toda herramienta nueva se registra en `tools/escritura_tools.py` o `tools/lectura_tools.py` (no
  crees módulos de herramientas nuevos) y en la tabla de CONTRATO.md con su columna "Sustituye a".
- **Lotes.** Toda escritura que pueda aplicarse a varios elementos recibe `element_ids[]` o una lista de
  operaciones, valida todo antes de abrir la transacción (400 con el índice), aplica en una transacción y responde
  con `fallidos[]` sin abortar el lote por un elemento (como `/set_parameters/`). `comprobar_alcance` sobre el total
  con `forzar` por encima de 200.
- **Macros.** `create_steel_frame` es una macro como las del bloque D: valida, `plan` con `simular`, una
  transacción, `creados` agrupado. Todo en `revit_mcp/acero.py` y `revit_mcp/analitico.py`; nada nuevo en
  `macros.py`.
- **Escritura.** Patrón de la fase 1 sin excepciones: `ejecutar(doc, "/ruta/", request, cuerpo)`,
  `EscrituraRechazada`, `simulacion(...)`, `with transaccion(doc, "...")`, `resultado_creacion` o `antes/despues`.
  La copia diferida ya la hace `ejecutar`; no la toques.
- **Idioma.** Nunca nombres visibles en inglés: `BuiltInParameter` para todo lo estructural, `BuiltInCategory` para
  categorías, `StructuralMaterialType`/`StructuralType` para tipos. Los nombres que se devuelven conservan tildes.
- **IronPython 2.7** en `revit_mcp/` (lo comprueba `tests/test_compatibilidad_ironpython.py`). `try/except` para
  cualquier miembro que cambie entre Revit 2024 y 2027, con la variante en la tabla de miembros.
- **Revit ocupado.** `TIMEOUT_LARGO` solo en `create_steel_frame`, `export_structural_model` (dentro de `export`) y
  `fix_analytical_alignment`; el resto `TIMEOUT_ESCRITURA` o `TIMEOUT_LECTURA`.

## Bloque 0. Estado de partida

1. Cada miembro nuevo de la API que uses entra en `herramientas-dev/miembros_por_verificar_revit.md` como
   `por verificar`, en una sección "Entrega 2b (0.5.0)". Partes con estos ya conocidos: `NewFamilyInstance(XYZ,
   FamilySymbol, Level, StructuralType.Column)` y `NewFamilyInstance(Line, FamilySymbol, Level, StructuralType.Beam)`
   (verificados en fase 1 por `/create_structural_column/` y `/create_structural_framing/`), `FAMILY_TOP_LEVEL_PARAM`
   (por verificar), `AnalyticalMember`/`AnalyticalToPhysicalAssociationManager` (por verificar, existen en 2024-2027),
   `StructuralConnectionHandler` (por verificar; depende de que las conexiones de acero estén instaladas).
2. No toques el código de 0.4.3 salvo para las ampliaciones que se indican. Si encuentras un fallo real, va en un
   commit propio al principio.
3. Cada manejador nuevo necesita al menos una prueba de extremo a extremo con el `pyrevit` simulado de
   `tests/fakes` (401 sin token, `simular` sin transacción, 400/404 controlados, `creados` o `antes/despues`).
   Amplía `tests/fakes/pyrevit/DB.py` y `modelo_falso.py` con lo que haga falta (`FamilySymbol` estructural con
   `StructuralMaterialType`, `AnalyticalMember`, `Truss`, `StructuralConnectionHandler`).

## Bloque 1. Lectura

| Herramienta | Ruta | Parámetros | Devuelve / notas |
|---|---|---|---|
| `list_steel_profiles` | POST `/steel_profiles/` | `standard` (`AISC`, `EN`, `todos`), `shape` (`W`, `HSS`, `L`, `C`, `WT`, `Pipe`), `loaded_only` (true) | Cargados: `FamilySymbol` de `OST_StructuralFraming` y `OST_StructuralColumns` cuyo `StructuralMaterialType` es `Steel`, con familia, tipo, categoría, `is_active`, dimensiones principales (los `BuiltInParameter` de sección que existan: `STRUCTURAL_SECTION_COMMON_HEIGHT`, `_WIDTH`, `_WEB_THICKNESS`, `_FLANGE_THICKNESS`, en mm). Con `loaded_only=false`, además los `.rfa` de `Application.GetLibraryPaths()` que tengan un `.txt` de catálogo al lado, sin suponer nombres de carpeta en inglés: familia, tipos del catálogo y ruta. |
| `steel_quantities` | POST `/steel_quantities/` | `group_by` (`type`, `level`, `family`, `mark`), `element_ids[]` (vacío = todo el acero) | Por grupo: recuento, longitud total en mm (`INSTANCE_LENGTH_PARAM` o la curva de ubicación) y peso total en kg = volumen (`HOST_VOLUME_COMPUTED`) × densidad del activo estructural del material (`Material.StructuralAssetId` → `PropertySetElement.GetStructuralAsset().Density`). `metodo` dice cómo se calculó; `sin_peso[]` con los ids cuyo peso no se pudo calcular y por qué. Si el tipo tiene un parámetro de masa lineal, úsalo como alternativa y dilo. |
| Ampliación de `describe_element` | POST `/describe/` | nuevo `include_structural` | Bloque `structural` con: uso estructural (`INSTANCE_STRUCT_USAGE_PARAM`), material estructural, liberaciones de inicio y fin (`STRUCTURAL_START_RELEASE_*`, `STRUCTURAL_END_RELEASE_*` con `FX/FY/FZ/MX/MY/MZ`), justificaciones Y/Z (`Y_JUSTIFICATION`, `Z_JUSTIFICATION`), desfases Y/Z en mm (`Y_OFFSET_VALUE`, `Z_OFFSET_VALUE`), rotación de sección en grados (`STRUCTURAL_BEND_DIR_ANGLE`), extensiones de inicio y fin en mm (`START_EXTENSION`, `END_EXTENSION`) y `analyze_as` (`STRUCTURAL_ANALYZES_AS`). Cada `BuiltInParameter` que no exista en la versión se omite y se anota en `no_disponibles[]`. Sustituye al `get_structural_properties` del bloque original. |
| Ampliación de `list_types` | POST `/element_types/` | `category="connections"` | Tipos de conexión de acero disponibles (`StructuralConnectionHandlerType`); `409` `no_soportado` si el módulo de conexiones no está instalado. Sustituye a `list_connection_types`. |

## Bloque 2. Escritura (todas con `simular`)

| Herramienta | Ruta | Parámetros | Acción |
|---|---|---|---|
| `load_steel_profile` | POST `/load_steel_profile/` | `file_path` o `family_name` (buscada en la biblioteca), `type_names[]` | Con catálogo `.txt`: `doc.LoadFamilySymbol(ruta, nombre_tipo)` por cada tipo pedido (`IFamilyLoadOptions` no sirve para elegir tipos); sin catálogo: `doc.LoadFamily` y activar los tipos. Si la familia ya está cargada, `409` salvo `overwrite=true`. `creados` con los símbolos cargados. |
| `create_steel_frame` | POST `/create_steel_frame/` | `grids_x[]`, `grids_y[]` (vacío = todas), `levels[]` (vacío = todos), `column_type*`, `beam_type*`, `beam_directions` (`x`, `y`, `both`), `column_orientation_deg`, `skip_columns_at[]` (`"A-1"`), `skip_beams_at[]`, `mark_prefix` | **Macro.** Pilares en cada intersección de rejillas con `crear_pilar` de `estructural.py` (reutilízalo; `FAMILY_TOP_LEVEL_PARAM` al nivel siguiente) y vigas entre pilares consecutivos de cada nivel con `crear_viga` de `structure.py`. Una transacción `IA: Portico metalico`. `comprobar_alcance` sobre el total. Con `simular`, `plan` con `counts` (`columns`, `beams`, `total`) y la lista de posiciones. `creados` agrupado en `columns` y `beams`. Marcas correlativas con `mark_prefix` (`ALL_MODEL_MARK`). |
| `create_bracing` | POST `/create_bracing/` | `bays[]` de `{start_point_mm*, end_point_mm*, level_bottom*, level_top*, pattern}` (`single`, `X`, `V`, `inverted_V`, `K`), `brace_type*` | **Lote.** Arriostres con `NewFamilyInstance(Line, symbol, level, StructuralType.Brace)`; los puntos intermedios se calculan a partir de los extremos y la altura entre niveles (elevación **interna**, `utils.elevacion_interna`). Valida todos los vanos antes; `creados` por vano. |
| `create_truss` | POST `/create_truss/` | `trusses[]` de `{truss_type*, start_point_mm*, end_point_mm*, level*}` | **Lote.** `DB.Structure.Truss.Create(doc, trussTypeId, sketchPlaneId, curve)` con un `SketchPlane` en el nivel. Si el tipo no existe, `404` con los disponibles. |
| `set_structural_properties` | POST `/set_structural_properties/` | `element_ids*` y cualquiera de: `start_release`/`end_release` (`{"FX": true, ...}` o `pinned`/`fixed`), `y_justification`, `z_justification`, `y_offset_mm`, `z_offset_mm`, `section_rotation_deg`, `start_extension_mm`, `end_extension_mm`, `analyze_as`, `structural_usage` | **Lote** sobre `/set_parameters/`: traduce cada propiedad a su `BuiltInParameter` y reutiliza `lotes.resolver_parametros`/aplicación en una transacción `IA: Propiedades estructurales (<n> elementos)`. `antes/despues` por elemento, `fallidos[]` con motivo, `no_disponibles[]` por versión. Sustituye a la versión por elemento del bloque original. |
| `create_steel_connection` | POST `/create_steel_connection/` | `connections[]` de `{element_ids*, connection_type*}`, `approve` | **Lote.** `StructuralConnectionHandler.Create(doc, ids, typeId)`; con `approve`, `ApprovalStatus`. Si las conexiones de acero no están instaladas, `409` `no_soportado` con la explicación y sin transacción. |
| `add_plate_or_stiffener` | POST `/add_plate/` | `host_id*`, `family_name*`, `type_name*`, `positions[]` (mm desde el inicio o fracción 0-1), `face` (`top`, `bottom`, `web`) | Familia alojada en cara: `NewFamilyInstance(reference, point, refDir, symbol)` con la referencia de la cara obtenida de `get_Geometry` con `ComputeReferences=True`; familia de punto: el punto sobre la curva. Un host, varias posiciones, una transacción. |
| `split_beam` | POST `/split_beam/` | `element_id*`, `at_mm[]` | `ElementTransformUtils.CopyElement` por tramo y ajuste de `LocationCurve`; el original se recorta al primer tramo. `avisos` dice que se pierden uniones y conexiones del original. `creados` con los tramos nuevos. |
| Ampliación de `join_geometry` | POST `/join_geometry/` | `element_ids[]` (cadena) y `coping` | `JoinGeometryUtils.JoinGeometry` por parejas consecutivas; con `coping=true`, `FamilyInstance.AddCoping`. Sustituye a `join_steel_elements`. Los dos argumentos actuales (`element_id_a`, `element_id_b`) siguen funcionando. |

## Bloque 3. Modelo analítico

Las cuatro versiones (2024-2027) tienen `AnalyticalMember` y `AnalyticalToPhysicalAssociationManager`; no hace
falta la ruta antigua de `AnalyticalModel`.

| Herramienta | Ruta | Parámetros | Acción |
|---|---|---|---|
| `analytical_status` | POST `/analytical_status/` | `element_ids[]` (vacío = todo el acero, con `max` 500) | Por elemento: `AnalyticalMember` asociado (`GetAssociatedAnalyticalElementId`), nodos extremos en mm (`GetCurve().GetEndPoint`), elementos analíticos conectados en cada extremo (miembros cuyo extremo está a menos de `tolerance_mm`, 10 por defecto), `is_connected` y `loose_nodes[]`. Resumen: `members`, `loose_nodes_total`. |
| `fix_analytical_alignment` | POST `/fix_analytical/` | `element_ids*`, `tolerance_mm` (50) | **Lote.** Para cada nodo suelto con un nodo ajeno dentro de la tolerancia, `AnalyticalMember.SetCurve` con el extremo movido a ese nodo, en una transacción `IA: Alinear analitico`. `antes/despues` por nodo movido, `fallidos[]`. Con `simular`, la lista de movimientos previstos. |
| Ampliación de `export` | POST `/export_document/` o nueva ruta `/export_structural/` | `format="ifc_structural"` o `"csv_nodes_members"`, `file_path*` | IFC: reutiliza la exportación IFC de 0.4.x con `ExportBaseQuantities` y la vista analítica activa si existe. CSV: una fila por miembro con id, tipo, perfil, material, nodo i (x, y, z mm), nodo j y liberaciones. Sustituye a `export_structural_model`. |

## Bloque 4. Instrucciones del agente y pruebas

1. `INSTRUCCIONES_AGENTE.md`: el flujo de estructura metálica del prompt original (niveles y rejillas con
   `query_elements(category="OST_Grids")` → `list_steel_profiles(loaded_only=true)` → `load_steel_profile` si falta
   → `create_steel_frame(simular=true)` y mostrar el recuento → confirmar → ejecutar → `set_structural_properties` →
   `analytical_status` y `fix_analytical_alignment` → `steel_quantities`), la regla "un lote, no una llamada por
   elemento", y el glosario adicional (arriostre = `Brace`, cercha = `Truss`, rigidizador = `Stiffener`, placa base
   = `Base plate`, liberación = `Release`, justificación = `Justification`, modelo analítico = `Analytical model`,
   nodo = `Analytical node`).
2. `pruebas/probar_revit.py --fase 2b`, sin depender de nombres visibles en inglés: (2b.1) `list_steel_profiles`
   devuelve al menos un perfil cargado o dice que no hay acero; (2b.2) `create_steel_frame(simular=true)` devuelve
   `plan.counts.total` y no crea nada; (2b.3) `create_steel_frame` real sobre 2×2 rejillas y 1 nivel crea 4 pilares
   y 4 vigas, `analytical_status` sobre ellos no reporta nodos sueltos, y se borran después; (2b.4)
   `set_structural_properties` con `start_release=pinned` sobre las 4 vigas, `describe_element(include_structural)`
   lo refleja; (2b.5) `steel_quantities(group_by="type")` devuelve peso > 0 para los perfiles creados o los lista en
   `sin_peso` con motivo. Si el modelo no tiene perfiles de acero cargados, las pruebas 2b.2 a 2b.5 se marcan
   `NO_APLICA` con el motivo, no como fallo.
3. `tests/`: una prueba de extremo a extremo por ruta nueva y por ampliación; el parseo de `start_release`
   (`pinned`, `fixed`, diccionario parcial); `create_bracing` con cada `pattern` (número de barras y puntos
   intermedios en mm, con un nivel cuya `Elevation` mostrada difiera de la interna, como en `test_macros.py`);
   `steel_quantities` con un material sin activo estructural (va a `sin_peso`); `analytical_status` con dos
   miembros cuyos nodos distan menos y más que la tolerancia; `create_steel_connection` sin el módulo (409).

## Entrega

- `CONTRATO.md`, `README.md`, `LLM.txt`, `INSTRUCCIONES_AGENTE.md` con el bloque, la tabla de las 51 herramientas
  con "Sustituye a" y un `curl` por ruta de escritura nueva.
- Versión 0.5.0 en `pyproject.toml` y `revit_mcp/__init__.py`.
- `herramientas-dev/miembros_por_verificar_revit.md` con la sección 2b.
- `herramientas-dev/VALIDACION_2B.md`: prompt de validación para el agente local con el formato de
  `VALIDACION_CONS.md` (requisitos: Revit en español con `/language ESP`, copia del modelo, extensión 0.5.0 con
  pyRevit recargado, puente y cliente MCP reiniciados; pasos numerados con los argumentos exactos, respuesta
  esperada, tiempo por paso; reglas "no marques OK lo que no viste", "no deduzcas causas que no comprobaste", "no
  muestres el token"; informe con tabla OK/FALLO, respuestas literales, miembros de la API que fallaron y menú
  Deshacer). Incluye un paso previo para cargar un perfil de acero si el modelo no tiene ninguno.
- Resumen final con: herramientas añadidas y ampliadas (y las del bloque original que se absorbieron en cuál),
  miembros de la API por verificar por versión, lo que quedó como `no_soportado` y su motivo.
