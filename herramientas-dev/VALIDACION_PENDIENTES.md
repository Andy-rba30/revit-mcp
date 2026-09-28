# Validar en Revit lo pendiente tras la 0.4.1 (versión 0.4.2)

Prompt para el agente local. Cubre lo que `miembros_por_verificar_revit.md` sigue marcando `por verificar` tras
la 0.4.1: la copia diferida, los nombres en español, techos, filtros de `query_elements`, tablas en planos,
`numerar_planos`, colores, coordenadas y las cotas de `describe_element`. Todo se hace sobre Modelo_Copia y al
final se cierra Revit **sin guardar**, así que nada de lo que se cambie queda en el archivo.

---

**Validar lo pendiente de la extensión MCP de Revit 0.4.2**

Trabaja solo sobre Modelo_Copia, nunca sobre el modelo original. No cambies el código de la extensión, el script de
arranque ni los archivos de macros. Usa las herramientas MCP del servidor revit por su nombre. Pega la respuesta
literal de cada paso (JSON completo o, si es muy largo, las primeras 40 líneas). Si algo falla, informa del fallo
tal cual, sin arreglarlo ni reintentar con otros valores, y sigue con el siguiente paso salvo que se diga lo
contrario. No deduzcas causas que no hayas comprobado. No marques como comprobado nada que no hayas visto en una
respuesta: lo que requiere mirar la interfaz de Revit lo comprueba el usuario. No guardes el modelo en ningún
momento (nada de `maintain_model(action="save")`).

## Preparación

1. En `C:\IA\pyrevit-ext\mcp-server-for-revit-python.extension` y en
   `C:\Users\Andy Bayona Antón\Proyectos\revit-mcp`: `git status --short` (si hay algún archivo con `M` que no sea
   `uv.lock`, detente y pregúntame), `git fetch origin`, `git checkout main`, `git pull origin main` y
   `git log -1 --oneline`. Debe ser el merge del PR #10 (0.4.2), y `revit_mcp\__init__.py` debe decir `0.4.2`.
2. Anota la fecha de modificación y el tamaño de `C:\Users\Andy Bayona Antón\Desktop\Modelo_Copia.rvt`
   (`Get-Item ... | Select-Object LastWriteTime, Length`).
3. Lista las tres copias más recientes de `C:\Users\Andy Bayona Antón\Desktop\backups`
   (`Get-ChildItem ... | Sort-Object LastWriteTime -Descending | Select-Object -First 3 Name, LastWriteTime, Length`).
   La más reciente debe tener **más de 30 minutos**; si no, espera hasta que los tenga y dime cuánto esperaste.
4. Cierra Revit. Lee `C:\Users\Andy Bayona Antón\open_interactive.ps1` y ábrelo en español: usa el modo del
   script que ejecuta una línea de órdenes (`cmd.exe /c $CommandLine`) con
   `"C:\Program Files\Autodesk\Revit 2027\Revit.exe" /language ESP "C:\Users\Andy Bayona Antón\Desktop\Modelo_Copia.rvt"`.
   Si `Revit.exe` no está en esa ruta, búscalo en `C:\Program Files\Autodesk` y usa la que encuentres. Si el script
   no tiene ese modo, ábrelo como siempre y anota que Revit quedará en inglés.
5. Sondea `GET http://127.0.0.1:48884/revit_mcp/ping/` sin token cada 2 s hasta 200 (máximo 3 minutos), relee el
   token de `%LOCALAPPDATA%\RevitMcp\token`, arranca el puente con `uv run python main.py --combined` y ejecuta
   `python pruebas\sync_schemas.py`.

## Pasos

**A. Copia diferida (tiene que ser la primera escritura de la sesión; antes solo lecturas)**

1. `set_parameters(changes=[{"element_ids": [165465], "parameters": {"Comments": "prueba copia"}}])`. Pega el
   bloque `copia` completo. Se espera `reutilizada` false, `diferida` true, `estado` en curso o terminada y una
   `ruta` nueva en `Desktop\backups`.
2. Inmediatamente, `set_parameters(changes=[{"element_ids": [165465], "parameters": {"Comments": ""}}])`. Pega
   `copia`: se espera `reutilizada` true con la misma `ruta`, y anota `espera_ms`.
3. Repite el listado de `Desktop\backups`. Se espera un archivo nuevo con la `ruta` del paso A1 y el mismo tamaño
   que Modelo_Copia.rvt (preparación 2).

**B. Idioma y nombres en español**

4. `execute_revit_code(code="print(doc.Application.Language)\nprint(doc.Application.VersionNumber)", description="idioma revit")`.
   Se espera `Spanish`. Si no lo es, anota `IDIOMA=inglés`: en los pasos 5 a 7 los nombres saldrán en inglés y
   esa parte se marca "no probado".
5. `set_parameters(changes=[{"element_ids": [165465], "parameters": {"Comments": "x", "Type Comments": "x"}}], simular=true)`.
   Se espera `parameter_label` "Comentarios" y "Comentarios de tipo".
6. `get_revit_model_info(include=["levels", "phases", "location"])`. Guarda el nivel de 165465 (`NTN +19.00`
   en la validación anterior) como NIVEL, la última fase como FASE y el bloque `location` completo como UBICACION.
   Pega los tres.
7. `create_elements(elements=[{"kind": "level", "name": "MCP pend nivel", "elevation_mm": 123000}, {"kind": "grid", "name": "MCPP", "start_point": {"x": 0, "y": -1000, "z": 0}, "end_point": {"x": 0, "y": 9000, "z": 0}}, {"kind": "wall", "start_point": {"x": 0, "y": 0, "z": 0}, "end_point": {"x": 3000, "y": 0, "z": 0}, "level_name": NIVEL, "height": 2500}])`.
   Se espera `categoria` "Niveles", "Rejillas" y "Muros". Borra los tres con `delete_elements`.

**C. Techos**

8. `create_elements(elements=[{"kind": "ceiling", "boundary": [{"x": 0, "y": 0, "z": 0}, {"x": 4000, "y": 0, "z": 0}, {"x": 4000, "y": 4000, "z": 0}, {"x": 0, "y": 4000, "z": 0}], "level_name": NIVEL, "offset": 2700}], simular=true)`
   y después sin simular. Se espera `creados` con un techo (categoría "Techos"), sin la nota "como suelo".
9. `describe_element(element_id=<id del techo>)`. Anota el parámetro de desfase respecto al nivel (se espera 2700).
   Borra el techo con `delete_elements`.

**D. Filtros de query_elements**

10. `query_elements(category="OST_Walls", phase=FASE, page_size=5)`. Se espera `elements` y `count`, sin error.
11. `describe_element(element_id=151574)` y, con su `bbox_mm`, `query_elements(category="OST_Walls", bbox_min_mm=<min>, bbox_max_mm=<max>, page_size=10)`.
    Se espera que 151574 esté en el resultado.
12. `query_elements(category=["OST_Walls", "OST_Floors"], page_size=5)`. Se espera respuesta sin error.
13. `get_revit_model_info(include=["worksets"])`: si `is_workshared` es false, el filtro por `workset` se anota
    "no aplica (modelo no compartido)"; si es true, `query_elements(category="OST_Walls", workset=<uno de la lista>, page_size=5)`.

**E. Tablas en planos y numerar_planos**

14. `list_views(view_type="schedules")`. Guarda como TABLA una tabla que no esté en ningún plano (por ejemplo
    "Sardineles y veredas"). `schedule_to_json(name=TABLA, max_rows=5)`: anota el título que devuelve.
15. `create_sheet_set(sheets=[{"number": "MCP-99", "name": "Prueba MCP", "views": [{"view_name": TABLA}]}], simular=true)`
    y después sin simular. Se espera la tabla colocada (no en `skipped`) y el id del plano nuevo como PLANO.
16. `run_macro(name="numerar_planos", args={"prefix": "MCP-", "start": 7, "sheet_ids": [PLANO]}, simular=true)` y
    después sin simular. Se espera que PLANO pase a "MCP-07" y `ok: true`. Comprueba con
    `describe_element(element_id=PLANO)` el número de plano.
17. Borra PLANO con `delete_elements`.

**F. Colores y cotas en una planta**

18. `list_views(view_type="floor_plans")` y `set_active_view(view_name=<una planta de NIVEL>)`.
19. `color_elements(category_name="Walls", parameter_name="Comments", simular=true)` y después sin simular. Se
    espera que acepte "Walls" aunque Revit esté en español. Después `color_elements(category_name="OST_Walls", clear=true)`.
20. `query_elements(current_view=true, category="OST_Walls", page_size=20)` y elige dos muros paralelos (misma
    orientación en `bbox_mm`) como M1 y M2. `annotate(kind="dimension", element_ids=[M1, M2])`. Si responde 400,
    anota el mensaje y salta el paso 21.
21. `describe_element(element_id=M1)`. Se espera la cota del paso 20 en `referenced_by`. Bórrala con
    `delete_elements`.

**G. Coordenadas (se deshacen al cerrar sin guardar)**

22. `set_project_location(true_north_deg=<UBICACION.true_north_deg + 10>, simular=true)` y después sin simular.
    `get_revit_model_info(include=["location"])`: se espera el norte cambiado en 10 grados.
23. `set_project_location(true_north_deg=<valor original>)` y vuelve a leer: se espera el valor original.
24. `set_project_location(base_point_mm={"x": 0, "y": 0, "z": 0}, simular=true)`. Pega la respuesta (puede ser 409
    si el punto base está fijado: anótalo, no uses `forzar`).

**H. Deshacer y cierre**

25. Detente y pídeme que revise el desplegable de Deshacer de Revit. Escríbeme la lista de entradas "IA: ..."
    que deberían aparecer según las escrituras reales que hiciste. Espera mi respuesta antes de seguir.
26. Cierra Revit sin guardar (`taskkill /IM Revit.exe /F`). Comprueba que la fecha y el tamaño de
    Modelo_Copia.rvt son los de la preparación 2.

## Informe final

Tabla con una fila por paso (preparación y 1 a 26): Paso, Herramienta, Esperado, Obtenido, Resultado (OK / FALLO /
no probado / no aplica / manual) y Observaciones. Debajo: (a) idioma y versión de Revit y cómo se abrió; (b) los
dos bloques `copia` del apartado A y el listado de `backups` antes y después; (c) nombres visibles que aparecieron
(parámetros, categorías, tipos) para confirmar tildes; (d) todo lo que no coincida con lo esperado, con la respuesta
literal.
