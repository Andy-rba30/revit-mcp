# Prompt de validación de la 0.5.1 (correcciones de la 2b) para el agente local

La validación de la 0.5.0 (28/09/2026, Revit 2027 en español) dejó 13/14 en `probar_revit.py --fase 2b` y cinco
pasos en FALLO. La 0.5.1 los corrige y este prompt **solo repite esos pasos**, más los necesarios para
prepararlos:

| Paso 0.5.0 | Fallo | Qué cambia en la 0.5.1 |
|---|---|---|
| 4 (2b.4a) y 10 | Las liberaciones no se pueden fijar: no hay parámetro en la viga física ni modelo analítico | Si solo se piden liberaciones y no hay miembro analítico: `409` `no_soportado` con `motivo: sin_modelo_analitico` (la prueba 2b.4a lo espera así) |
| 13 | `AttributeError: 'ElementId' object has no attribute 'Id'`; el tipo salía "Unnamed" | `GetDefaultConnectionHandlerType` devuelve un `ElementId`: se convierte con `doc.GetElement` |
| 15 | La viga original seguía midiendo 6000 mm en vez de 2000 mm | `FamilyInstance.Split`; sin él, `DisallowJoinAtEnd` antes de mover el extremo. `verificacion` comprueba cada tramo |
| 16 | HTTP 500 "The elements cannot be joined" | Con `coping=true` solo se recorta (`AddCoping` sobre la viga, sin `JoinGeometry`); sin `coping`, la pareja rechazada va a `fallidos` |
| 20 y 21 | "Access to the path '...\Desktop' is denied" | Carpeta y CSV con `System.IO`; si falla, `500` con un diagnóstico (`ruta_recibida`, `caracteres_no_ascii`, `posible_ruta_mal_leida`, `sugerencia`) |

Requisitos previos, como en `VALIDACION_2B.md`: Revit abierto **en español** con **una copia** del modelo de
prueba, la extensión 0.5.1 cargada, Routes activo en 48884, el puente arrancado con
`uv run python main.py --combined` y el cliente MCP reiniciado. Siguen siendo **52** herramientas. Al final se
cierra Revit **sin guardar**.

---

**Validar las correcciones de la extensión MCP de Revit 0.5.1**

Trabaja solo sobre Modelo_Copia. No cambies el código de la extensión, el script de arranque ni los archivos de
macros. Usa las herramientas MCP **por su nombre**. Ejecuta los pasos en orden y anota de cada uno la respuesta
**literal** (JSON completo o, si es muy largo, las primeras 40 líneas), el tiempo total del paso, y `ms` y
`ms_puente`. Reglas:

- **No marques OK lo que no viste**: lo que requiere mirar la interfaz de Revit lo comprueba el usuario; tú lo
  dejas como `manual`.
- **No deduzcas causas que no comprobaste**: si algo falla, pega el error tal cual.
- **No muestres el token** en ningún mensaje ni en el informe.
- No corrijas nada por tu cuenta ni reintentes con otros valores salvo donde el paso lo pide; si un paso falla,
  sigue con el siguiente.
- Toma los nombres de niveles, rejillas, tipos y familias de las herramientas (`get_revit_model_info`,
  `query_elements`, `list_steel_profiles`, `list_types`). No uses nombres visibles en inglés.
- No guardes el modelo en ningún momento.

## Preparación

1. En `C:\IA\pyrevit-ext\mcp-server-for-revit-python.extension` y en
   `C:\Users\Andy Bayona Antón\Proyectos\revit-mcp`:
   - `git status --short`. Si hay algún archivo con `M` que no sea `uv.lock`, detente y pregúntame; si es
     `uv.lock`, `git restore uv.lock`.
   - `git fetch origin`, `git checkout main`, `git pull origin main` y `git log --oneline -3`.
   - Entre esos commits debe estar el merge de la 0.5.1, y `revit_mcp\__init__.py` debe decir `0.5.1`. Si no,
     detente y avísame.
2. Anota la fecha de modificación y el tamaño de `C:\Users\Andy Bayona Antón\Desktop\Modelo_Copia.rvt`.
3. Cierra Revit y ábrelo en español:
   `powershell -ExecutionPolicy Bypass -File "C:\Users\Andy Bayona Antón\open_interactive.ps1" -CommandLine '"C:\Program Files\Autodesk\Revit 2027\Revit.exe" /language ESP "C:\Users\Andy Bayona Antón\Desktop\Modelo_Copia.rvt"'`.
4. Arranca el servidor y el puente:
   - sondea `GET http://127.0.0.1:48884/revit_mcp/ping/` sin token cada 2 s hasta que responda 200 (máximo
     3 minutos);
   - relee el token;
   - arranca el puente con `uv run python main.py --combined`;
   - ejecuta `python pruebas\sync_schemas.py` y reinicia el cliente MCP.
5. `list_steel_profiles(loaded_only=true)`: guarda como `PILAR` y `VIGA` el primer perfil de
   `OST_StructuralColumns` y el primero de `OST_StructuralFraming` (`"family: type"`).
6. `get_revit_model_info(include=["levels"])`: guarda el nivel más bajo como `NIVEL` y el siguiente como `NIVEL2`.

## Pasos

1. En una consola, `python pruebas\probar_revit.py --fase 2b`.
   - Esperado: `Resultado: 14/14 pruebas correctas` y salida 0.
   - 2b.4a debe dar `409` con "sin modelo analitico: 409 no_soportado con motivo sin_modelo_analitico [OK]", si
     2b.3b dice que ningún elemento tiene modelo analítico.
   - Pega el bloque de cada prueba 2b.1 a 2b.5.
2. Crea la rejilla auxiliar y el pórtico, como en los pasos 5 a 7 de la 0.5.0:
   - `create_grid_and_levels(x_spacings_mm=[6000], y_spacings_mm=[5000], x_names="MCP2B1", y_names="MCPY1",
     origin_mm={"x": 200000, "y": 200000, "z": 0}, extension_mm=1000)`;
   - `create_steel_frame(column_type=PILAR, beam_type=VIGA, grids_x=["MCP2B1", "MCP2B2"],
     grids_y=["MCPY1", "MCPY2"], levels=[NIVEL], mark_prefix="MCP051-")` sin `simular`.
   - Esperado: `ok: true`, 4 pilares y 4 vigas. Guarda `REJILLAS` (los 4 ids creados), `PILARES` (4 ids) y
     `VIGAS` (4 ids).
3. **Liberaciones** (antes, pasos 4 y 10): `set_structural_properties(element_ids=VIGAS, start_release="pinned")`.
   - Esperado: `409` con `no_soportado: true`, `motivo: "sin_modelo_analitico"`, un `error` en español que
     explica que las liberaciones viven en el modelo analítico, y `fallidos[]`.
   - Después, `set_structural_properties(element_ids=VIGAS, start_release="pinned", z_offset_mm=-50)`. Esperado:
     `200`, `ok: true`, `changes[]` con `z_offset_mm` y `coincide: true`, y las liberaciones en `fallidos`.
4. **Conexiones** (antes, paso 13): `list_types(category="connections")`.
   - Esperado: `types[]` con un nombre de tipo **distinto de "Unnamed"**, o `409` `no_soportado` con `motivo`.
   - Si hay tipos: `create_steel_connection(connections=[{"element_ids": [PILARES[0], VIGAS[0]],
     "connection_type": "<types[0].tipo>"}], simular=true)` y después sin `simular`.
   - Esperado: `ok: true`, `creados[0]` con `connection_type` y la entrada `IA: Crear 1 conexiones`. Guarda el id
     como `CONEXION`.
   - Si falla, pega el error literal (es `StructuralConnectionHandler.Create`, por verificar).
5. **Dividir viga** (antes, paso 15): `split_beam(element_id=VIGAS[1], at_mm=[2000], simular=true)` y después
   sin `simular`.
   - Esperado: `ok: true`, `metodo` (`FamilyInstance.Split` o `CopyElement`), `verificacion.coincide: true`,
     `original.segment`, y dos tramos de 2000 y 4000 mm entre `original.despues` y `creados[]`.
   - Guarda el id nuevo como `TRAMO`. Anota `metodo` y `avisos` completos.
   - Pídeme que compruebe en Revit (manual) que la viga se ve partida en dos y que los dos tramos llegan a los
     pilares.
6. **Recorte** (antes, paso 16): `join_geometry(element_ids=[PILARES[0], VIGAS[0]], coping=true, simular=true)`.
   - Esperado: `haria[0].coped_element` = `VIGAS[0]` y `against` = `PILARES[0]` (la viga se recorta aunque el
     pilar vaya primero).
   - Sin `simular`. Esperado: `200`, `ok: true`, `coping.applied` con la viga y el pilar,
     `pairs[0].despues.coped: true`, y la entrada `IA: Recortar acero en cadena (2 elementos)`.
   - Si `coping.failed` trae un error, pégalo literal (es `AddCoping`, por verificar).
   - Pídeme que compruebe en Revit (manual) si se ve el recorte.
7. **Unión sin recorte** (comprobación del arreglo del 500): `join_geometry(element_ids=[PILARES[1], VIGAS[1]])`.
   - Esperado: `200` (no `500`), `ok: false`, `fallidos[0].error` con "The elements cannot be joined" y una
     `nota` que recomienda `coping=true`.
8. **Exportar al Escritorio** (antes, pasos 20 y 21):
   - `export(format="csv_nodes_members", file_path="C:\Users\Andy Bayona Antón\Desktop\miembros_051.csv",
     element_ids=PILARES + VIGAS)`.
   - Esperado: `200` con `rows: 8` y el archivo creado. Si responde `500`, pega **completos** `ruta_recibida`,
     `caracteres_no_ascii`, `posible_ruta_mal_leida` y `sugerencia`.
   - Repite con `format="ifc_structural"` y `file_path="C:\Users\Andy Bayona Antón\Desktop\estructura_051.ifc"`.
9. **Exportar a una carpeta sin tildes**: los dos exports del paso 8 con `file_path="C:\IA\salidas\miembros_051.csv"`
   y `"C:\IA\salidas\estructura_051.ifc"`.
   - Esperado: `200`, archivos creados (la carpeta se crea si no existe) y `file_size_kb` > 0 en el IFC.
   - Pega las 3 primeras líneas del CSV.
   - Si el paso 8 falló y este funciona, dilo tal cual; no deduzcas la causa.
10. `read_log(last_n=15)`. Esperado: entradas de `/set_structural_properties/`, `/split_beam/`,
    `/join_geometry/` (y `/create_steel_connection/` si se ejecutó); las simuladas con `simulado: true`.
11. Detente y pídeme que revise el desplegable de Deshacer de Revit. Escríbeme la lista de entradas `IA: ...` que
    deberían aparecer según las escrituras reales que hiciste. Espera mi respuesta.
12. Limpieza y cierre:
    - `delete_elements` con `PILARES`, `VIGAS`, `TRAMO`, `REJILLAS` y, si existe, `CONEXION`. Esperado: `ok: true`.
    - Cierra Revit sin guardar (`taskkill /IM Revit.exe /F`).
    - Comprueba que la fecha y el tamaño de Modelo_Copia.rvt son los de la preparación 2.
    - Borra los archivos exportados del Escritorio y de `C:\IA\salidas`.

## Informe

Entrega una tabla con una fila por paso (preparación y 1 a 12):

| Paso | Herramienta | Tiempo total del paso | `ms` / `ms_puente` | Resultado (OK / FALLO / no probado / no aplica / manual) | Respuesta literal (recortada a 40 líneas) | Observaciones |
|---|---|---|---|---|---|---|

Debajo de la tabla:

- **Miembros de la API**, para `herramientas-dev/miembros_por_verificar_revit.md`: `FamilyInstance.Split` (o, si
  `metodo` fue `CopyElement`, `StructuralFramingUtils.DisallowJoinAtEnd`), `FamilyInstance.AddCoping` y
  `GetCopingIds`, `StructuralConnectionHandler.Create`, `System.IO.Directory`/`File.WriteAllText` y
  `Document.Export` del IFC. De cada uno: si funcionó, o el mensaje de error literal.
- **Exportación**: resultado en el Escritorio y en `C:\IA\salidas`, con el diagnóstico completo si hubo `500`.
- **Menú Deshacer** (paso 11): la lista que confirmó el usuario.
- El resultado total de `probar_revit.py --fase 2b` (`N/14`).

Entrégame el informe en esta conversación.
