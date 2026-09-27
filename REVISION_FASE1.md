# Revisión de la fase 1 (0.2.0 → 0.2.1) antes de ejecutarla en Revit

Revisión de código hecha el 2026-09-27 sobre la rama de fase 1 (68 herramientas), sin Revit disponible. Las 113
pruebas de CPython con el `pyrevit` simulado siguen pasando. Todo lo de abajo está pendiente de la validación en
Revit con `pruebas\probar_revit.py` sobre una copia del modelo.

## Corregido en esta rama

| # | Dónde | Qué fallaba | Qué se hizo |
|---|---|---|---|
| 1 | `editing.py` `delete_elements` | Borraba id a id; si uno arrastraba a otro de la misma lista (muro y puerta, nivel y alojados), el segundo `Delete` lanzaba y se revertía todo con un 500 | Se salta los ids que ya no existen antes de borrarlos |
| 2 | `mantenimiento.py` `purge_unused` | Sin PerformanceAdviser tomaba como "sin uso" todo tipo de familia sin ejemplar (tipos de etiqueta, perfiles de barandilla, familias anidadas) y los borraba en cascada; sin límite de elementos; `ExecuteRules` dentro de la transacción | Sin PerformanceAdviser responde 409 (la lista solo se consulta con `simular`); límite de 200 salvo `forzar`; una transacción por ronda y el adviser fuera de ella |
| 3 | `estructural.py` `create_opening` | Recombinaba las esquinas como (mín, mín, mín)/(máx, máx, máx): fuera del plano en muros oblicuos | Exactamente 2 puntos y se pasan tal cual a `NewOpening` |
| 4 | `model_info.py`, `status.py` | Las rutas no pedían `doc` y pyRevit las ejecutaba fuera del contexto de la API (hilo HTTP) con llamadas nuevas a unidades y punto base | `doc` en la firma |
| 5 | `parameters.py` (`set_parameter`, `modify_element`, `set_type_parameter`) | Los `Double` se guardaban en crudo (pies) mientras todo lo demás recibe mm | `factor_a_interno`: mm→pies, mm²→pies², mm³→pies³, grados→radianes según `Definition.GetDataType()`; documentado en las herramientas |
| 6 | `coordenadas.py` `set_project_location` | Movía puntos anclados (falla) o recortados (cambia las coordenadas compartidas de todo el modelo) sin aviso; `acquire_from_link_id` pisaba a los demás cambios | 409 si el punto está anclado o recortado salvo `forzar`; `acquire_from_link_id` exclusivo |
| 7 | `utils.py` `elementos_por_nombre`, `building.py` | Niveles y tipos con tildes o ñ nunca coincidían (se comparaba contra el nombre pasado a ASCII); `str(name)` fallaba con tildes | Se indexa también el nombre real (unicode); sin `str()` |
| 8 | `building.py`, `estructural.py` | `create_line`, `create_surface`, `create_level`, `create_column`, `create_foundation` sin límite por llamada | `comprobar_alcance` (200 salvo `forzar`) |
| 9 | `document.py` `save_document` | `SaveAs` en modelo compartido (exige `WorksharingSaveAsSettings`); `SaveAs` sobre la misma ruta | 400 en compartido con `file_path`; misma ruta = guardado normal |
| 10 | `topografia.py` | CSV separado por espacios: varios espacios seguidos desalineaban las columnas | `split()` cuando el separador es espacio |
| 11 | `escritura.py` | Copia reutilizada informaba la fecha del `.rvt` actual, no de la copia | Fecha de la copia |
| 12 | `utils.py`, `escritura.py` | Al revertir por un fallo de Revit el error no decía por qué (los mensajes ya se habían borrado) | `_FailureSwallower` guarda las descripciones y `TransaccionRevertida` las incluye |

## Visto y no cambiado (decidir más adelante)

- `set_type_parameter` no limita por `afecta_ejemplares` (cambiar un tipo con miles de ejemplares no pide `forzar`).
- `find_elements` con `level_name` sin `category` filtra por `ElementLevelFilter` y luego comprueba por `nombre_nivel`; los resultados pueden diferir según se pase `category` o no.
- La copia del `.rvt` se hace en el hilo principal de Revit al primer cambio de cada 30 minutos: en modelos grandes congela Revit unos segundos.
- `Request.query_params` de pyRevit Routes: el token de los GET depende de que la versión instalada lo exponga; si no, todos los GET responden 401. Se comprueba en la prueba 2 de `probar_revit.py`.

## Qué validar en Revit (copia del modelo, nunca el original)

1. `python pruebas\probar_revit.py` completo (9 pruebas).
2. `set_parameter` sobre un parámetro de longitud (por ejemplo `Comments` no vale; usar `Unconnected Height` de un muro) con `value` en mm y comprobar en Revit que el valor coincide.
3. `delete_elements` con un muro y una puerta alojada en él en la misma lista.
4. `create_opening` en un muro oblicuo (no paralelo a los ejes).
5. `purge_unused` con `simular=true`: debe devolver método `PerformanceAdviser`; si devuelve 409, anotar la versión de Revit.
6. `set_project_location` con `simular=true` y luego `true_north_deg` solo.

## Ejecución en Revit de la 0.2.1 (2026-09-27) y correcciones de la 0.2.2

Resultado en un Revit en español: 7/9 en `probar_revit.py`; `set_parameter` de longitud, `delete_elements`
con muro y puerta alojada, y lectura de puntos por `execute_code` correctos. Fallos encontrados:

| # | Dónde | Qué fallaba | Qué se hizo |
|---|---|---|---|
| 13 | `utils.py` `get_element_id_value`, `escritura.py` `resultado_creacion` | **Toda** creación respondía `ok:false` y "no se encontraron los elementos" aunque Revit los hubiera creado: las rutas guardan ids enteros y `get_element_id_value(int)` lanzaba, así que `describir_elemento` devolvía `None` (visto con `create_opening`) | `get_element_id_value` acepta enteros |
| 14 | `set_parameter`, `modify_element`, `set_type_parameter`, `find_elements` y los `LookupParameter` con nombre inglés fijo | En un Revit que no está en inglés `Comments` es `Comentarios`: 404 (pruebas 6 y 7), y `Name`/`Number`/`Area`/`Length`... salían vacíos | `utils.buscar_por_nombre`: nombre visible, luego nombre `BuiltInParameter` (`ALL_MODEL_INSTANCE_COMMENTS`), luego alias ingleses de los parámetros comunes; `set_parameter` devuelve `parameter_label` (el nombre que muestra Revit) |
| 15 | `nombres_parametros` | `available_parameters` repetía nombres (`Categoría` dos veces) y cortaba a 30 | Sin repetidos, hasta 60 |
| 16 | `utils.py` `get_element_name`, `sanitize_string`, `normalize_string`; `parameters.py` `_safe_str` | Los nombres con tildes o ñ salían como `Gen?rico - Alba?iler?a`, y las comparaciones con un nombre con tildes pasado por el usuario (vistas, tipos, cajetines) nunca coincidían | Se conserva el texto unicode; la capa JSON ya lo escapa (`é`) |
| 17 | `estructural.py` `create_opening` | Un hueco con z tomada como desfase desde el nivel (900–2100 en un muro de 2925–5925) se creaba sin cortar nada | 400 si el hueco no solapa en altura con el muro; el mensaje ya no dice "Created" cuando la verificación falla; docstring: z es cota absoluta y los 2 puntos van sobre la línea de ubicación |
| 18 | `code_execution.py` | `DB.ElementId(165465)` falla en Revit 2027 y `from utils import make_element_id` da `ImportError` | `make_element_id`, `get_element_id_value` y `buscar_parametro` ya definidos en el espacio del código; pista para el `ImportError` |
| 19 | `pruebas/probar_revit.py` | Buscaba el valor original por el nombre pedido (`Comments`) en `element_properties` | Toma el valor original y el nombre visible de la respuesta simulada |

Pendiente en Revit: repetir `probar_revit.py` (se esperan 9/9) y `create_opening` con z dentro del muro. Los
huecos 615578 y 615589 de la prueba anterior probablemente sí se crearon: revisar el modelo o deshacerlos
(entradas `IA: Crear hueco en 165465`).

## Validación en Revit de la 0.2.2 (2026-09-27)

`probar_revit.py` 9/9. `create_opening` dentro del muro: `ok:true`, hueco 615549 verificado; fuera del muro: 400
con las cotas reales del muro. Parámetros por nombre inglés y `BuiltInParameter`: `parameter_label` correcto.
`available_parameters` sin repetidos. Tildes correctas en `list_levels` y `element_types`. Helpers de
`execute_code` correctos. Los huecos 615578 y 615589 no existían (el modelo se reabrió sin guardar).

| # | Dónde | Qué fallaba | Qué se hizo |
|---|---|---|---|
| 20 | `parameters.py` `set_parameter` con `simular` | En un Double, `despues` mostraba el valor interno en pies (`9.84251968504` para 3000 mm) | `despues` en las unidades en que se recibió (`3000 mm`) |
