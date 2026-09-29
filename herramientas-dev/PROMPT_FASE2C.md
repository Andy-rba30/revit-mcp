# Prompt de la entrega 2c (revit-mcp 0.6.0): editor de familias

Eres un agente de programación con acceso al repositorio `revit-mcp`. Está en `main` la versión **0.5.1**, validada
en un Revit 2027 en español: 52 herramientas MCP, estructuras metálicas y modelo analítico (`acero.py`,
`analitico.py`), 329 pruebas en CPython que GitHub ejecuta en cada PR. Esta entrega es el **Bloque C** de
`herramientas-dev/PROMPT_FASE2.md` (editor de familias), adaptado a las reglas que la consolidación y la 2b dejaron
vigentes. Donde este documento y el bloque C original difieran, manda este documento.

Antes de escribir código lee: `CONTRATO.md`, `INSTRUCCIONES_AGENTE.md`, `herramientas-dev/PROMPT_FASE2.md`
(Bloque 0, "Reglas vigentes" y "Entrega 2c"), `herramientas-dev/PROMPT_FASE2B.md` (reglas de la consolidación),
`herramientas-dev/VALIDACION_2B.md` y `VALIDACION_051.md` (formato de validación),
`herramientas-dev/miembros_por_verificar_revit.md`, `revit_mcp/escritura.py`, `revit_mcp/acero.py` (patrón de macro
y lotes), `revit_mcp/utils.py`, `tools/__init__.py`, `tools/escritura_tools.py`, `tools/lectura_tools.py`,
`tools/macro_tools.py`, `tests/fakes/` y `tests/test_acero_escritura.py`.

Rama `claude/lucid-cerf-eldde5` desde `main`, versión **0.6.0** en `pyproject.toml`, `revit_mcp/__init__.py` y
`uv.lock` (solo la entrada de la versión: no se hace commit del resto del `uv.lock` si solo cambió por
`--with pytest`). Un commit por bloque. `uv run --with pytest python -m pytest -q` antes de cada commit; ninguna
prueba existente puede dejar de pasar.

## Reglas que manda la consolidación (además de las de PROMPT_FASE2.md)

- **Pocas herramientas, de alto nivel.** El bloque C original listaba 18; aquí son **14 nuevas** y ninguna
  ampliación. Al terminar, el servidor MCP expone 66. `family_new` se absorbe en `family_open` (`template` + `name`
  \| `file_path` \| `family_name`); `family_create_extrusion` / `sweep` / `revolve` / `blend` en
  `family_create_solids(kind=...)`; `family_add_parameter`, `family_add_reference_plane`,
  `family_add_dimension_with_label`, `family_lock_face_to_plane`, `family_set_type_values` y `family_add_connector`
  pasan a lotes (`parameters[]`, `planes[]`, `dimensions[]`, `locks[]`, `types[]`, `connectors[]`). Cada
  descripción con menos de 60 palabras y un ejemplo. `family_info` en `tools/lectura_tools.py`, los lotes y el
  ciclo de vida en `tools/escritura_tools.py`, `family_validate` y `build_family_from_spec` (macros) en
  `tools/macro_tools.py`.
- **Documento de familia.** Toda escritura va a un documento de familia identificado por `family_doc` (título
  entre `Application.Documents` con `IsFamilyDocument`, o el `name` con que lo abrió el MCP). Se añade a
  `escritura.py` la variante `ejecutar_familia`: registro en el `mcp_log.jsonl` del proyecto, `409` si el documento
  de familia tiene una transacción abierta, copia del `.rfa` **solo si está guardado** (nunca se copia un `.rfa`
  sin guardar), una `transaccion(doc_familia, "IA: ...")` por llamada. Los documentos abiertos por el MCP quedan
  en un diccionario del módulo y solo esos se pueden cerrar (nunca el documento activo). `doc.EditFamily` no se
  llama con una transacción abierta en el proyecto (`409`), ni sobre familias in situ o no editables (`400`).
- **Lotes.** Cada ruta de edición recibe una lista, valida todo antes de abrir la transacción (`400`/`404`/`409`
  con `index`), aplica en una transacción y responde `creados[]` o `antes`/`despues`; `comprobar_alcance` sobre el
  número de elementos (200 salvo `forzar`).
- **Macros.** `family_validate` (un `TransactionGroup` por caso, `RollBack` con `restore`) y
  `build_family_from_spec` (valida el `spec` completo antes de abrir nada; pasos en orden; ante un fallo cierra
  sin guardar y devuelve el paso). Todo en `revit_mcp/familias.py`, `familias_edicion.py` y `familias_spec.py`.
- **Idioma.** Plantillas por nombre de archivo en `Application.FamilyTemplatePath` (`404` con la lista si no está);
  vistas por `ViewType` y nivel o `ViewDirection`, nunca por "Ref. Level" ni "Front"; categorías por
  `BuiltInCategory`; grupos por `GroupTypeId`; tipos de dato por `SpecTypeId`.
- **API 2024+.** `FamilyManager.AddParameter(nombre, GroupTypeId, SpecTypeId, is_instance)` y las sobrecargas con
  `Category` (`family_type:<OST_...>`) y `ExternalDefinition` (compartido por GUID).
- **Referencias de caras.** `Options.ComputeReferences = True`, `IncludeNonVisibleObjects = True` y `Options.View`
  en una vista donde la cara y el plano sean visibles; `Regenerate` antes de bloquear.
- **IronPython 2.7** en `revit_mcp/`; `try/except` en lo que cambie entre Revit 2024 y 2027.
- **Tiempos.** `TIMEOUT_LARGO` solo en `build_family_from_spec`, `family_validate` y `family_load_into_project`.

## Bloques

1. Fakes (`tests/fakes`: documento de familia, `FamilyManager`, `FamilyCreate`, `GroupTypeId`, `SpecTypeId`
   anidado, `ReferencePlane`, `GenericForm`, `ConnectorElement`, `SaveAs`, `Close`, `LoadFamily` en el proyecto),
   `escritura.ejecutar_familia` y el ciclo de vida: `/family/info/`, `/family/open/`, `/family/save/`,
   `/family/load/`, `/family/close/`.
2. Lotes: `/family/parameters/`, `/family/reference_planes/`, `/family/dimensions/`, `/family/types/`,
   `/family/connectors/`.
3. Sólidos y bloqueos: `/family/solids/` (`kind` extrusion \| sweep \| revolution \| blend, `is_void`,
   `lock_ends_to`, `lock_faces`, `material_parameter`) y `/family/locks/`.
4. Macros: `/family/validate/` y `/family/build/` con la validación del `spec` (plantilla inexistente, planos no
   definidos, fórmulas con parámetros no definidos, tipos con parámetros desconocidos: errores claros con
   `spec_error`, `section` e `index`).
5. Puente (14 herramientas, 66 en total, `test_herramientas.py`), `INSTRUCCIONES_AGENTE.md` (flujo de familia,
   reglas, glosario, errores), `CONTRATO.md` (rutas, formato del `spec`, los dos ejemplos completos: placa base con
   cuatro agujeros y perfil W paramétrico, `curl` por ruta), `README.md`, `LLM.txt`,
   `herramientas-dev/miembros_por_verificar_revit.md` (sección 2c), `pruebas/probar_revit.py --fase 2c`,
   `herramientas-dev/VALIDACION_2C.md` y la versión 0.6.0.

## Pruebas

- `tests/test_familias.py`: una prueba de extremo a extremo por ruta (401 sin token, `simular` sin transacción,
  400/404/409 controlados, `creados` / `antes`-`despues`), resolución de vistas por `ViewType`, plantillas sin
  tildes, `EditFamily` con transacción abierta e in situ, guardar / cargar / cerrar.
- `tests/test_familias_spec.py`: la validación del `spec`, `family_validate` con un caso que deja un sólido sin
  volumen y otro que Revit rechaza, `build_family_from_spec` completo y el cierre sin guardar si un paso falla.
- `pruebas/probar_revit.py --fase 2c`: (2c.1) `build_family_from_spec` con la placa base crea el `.rfa`;
  (2c.2) `family_validate` pasa en sus dos tipos; (2c.3) la familia se carga en el proyecto. Sin nombres visibles
  en inglés: la plantilla se elige de `family_info(include_templates=true)`.

## Entrega

- `CONTRATO.md`, `README.md`, `LLM.txt`, `INSTRUCCIONES_AGENTE.md` con el bloque, la tabla de las 66 herramientas
  con "Sustituye a" y un `curl` por ruta nueva.
- Versión 0.6.0.
- `herramientas-dev/miembros_por_verificar_revit.md` con la sección 2c.
- `herramientas-dev/VALIDACION_2C.md` con el formato de `VALIDACION_2B.md`.
- Resumen final: herramientas añadidas (y las del bloque original absorbidas en cuál), miembros de la API por
  verificar por versión, lo que quedó como `no_soportado` y su motivo.
