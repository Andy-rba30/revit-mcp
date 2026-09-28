# Validar la 0.4.3: cota entre muros y filtro por fase

Prompt para el agente local. Repite los dos puntos que quedaron abiertos en `VALIDACION_PENDIENTES.md` (0.4.2): la
cota entre dos muros (pasos 20 y 21, corregida en 0.4.3) y el filtro por fase con la fase real de un muro. Se trabaja
sobre Modelo_Copia y al final se cierra Revit sin guardar.

---

**Validar la extensión MCP de Revit 0.4.3 (cota entre muros y filtro por fase)**

Trabaja solo sobre Modelo_Copia, nunca sobre el modelo original. No cambies el código de la extensión, el script de
arranque ni los archivos de macros. Pega la respuesta literal de cada paso. Si algo falla, informa del fallo tal cual,
sin arreglarlo ni reintentar con otros valores, y sigue con el siguiente paso. No deduzcas causas que no hayas
comprobado: si algo falla, pega el error y no expliques por qué. No guardes el modelo en ningún momento.

## Preparación

1. En `C:\IA\pyrevit-ext\mcp-server-for-revit-python.extension` y en
   `C:\Users\Andy Bayona Antón\Proyectos\revit-mcp`: `git status --short` (si hay algún archivo con `M` que no sea
   `uv.lock`, detente y pregúntame; si es `uv.lock`, `git restore uv.lock`), `git fetch origin`, `git checkout main`,
   `git pull origin main` y `git log --oneline -3`. Entre esos commits debe estar
   `2f9e1a9 Merge PR #11: 0.4.3, cotas entre muros con la cara exterior`, y `revit_mcp\__init__.py` debe decir `0.4.3`.
2. Anota la fecha de modificación y el tamaño de `C:\Users\Andy Bayona Antón\Desktop\Modelo_Copia.rvt`.
3. Cierra Revit y ábrelo en español como en la validación anterior:
   `powershell -ExecutionPolicy Bypass -File "C:\Users\Andy Bayona Antón\open_interactive.ps1" -CommandLine '"C:\Program Files\Autodesk\Revit 2027\Revit.exe" /language ESP "C:\Users\Andy Bayona Antón\Desktop\Modelo_Copia.rvt"'`.
4. Sondea `GET http://127.0.0.1:48884/revit_mcp/ping/` sin token cada 2 s hasta 200 (máximo 3 minutos), relee el
   token, arranca el puente con `uv run python main.py --combined` y ejecuta `python pruebas\sync_schemas.py`.

## Pasos

1. `set_active_view(view_name="Techo Garita")`. Se espera `ok: true`.
2. `annotate(kind="dimension", element_ids=[165465, 165592], simular=true)`. Se espera `haria[0].accion` "acotar" con
   `references` 2.
3. El paso 2 sin simular. Se espera `ok: true` y en `creados` una cota con `value`. Guarda su id como COTA.
4. `describe_element(element_id=165465)`. Se espera COTA en `referenced_by`.
5. `delete_elements(element_ids=[COTA])`. Se espera `ok: true`.
6. `query_elements(category="OST_Walls", phase="Fase 1", ids_only=true, page_size=500)`. Se espera que 165465 esté
   en `ids`. Anota `total_matched`.
7. `query_elements(category="OST_Walls", phase="Fase 3", ids_only=true, page_size=500)`. Anota `total_matched`
   (en la 0.4.2 fue 0).
8. `get_revit_model_info()`. Anota el recuento total de muros si aparece, para compararlo con la suma de los pasos 6 y 7.
9. Cierra Revit sin guardar (`taskkill /IM Revit.exe /F`) y comprueba que la fecha y el tamaño de Modelo_Copia.rvt
   son los de la preparación 2.

## Informe final

Tabla con una fila por paso (preparación y 1 a 9): Paso, Herramienta, Esperado, Obtenido, OK/FALLO y Observaciones.
Debajo, todo lo que no coincida con lo esperado, con la respuesta literal.
