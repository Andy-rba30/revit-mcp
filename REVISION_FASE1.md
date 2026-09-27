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

## Validación en Revit 2027 (2026-09-27) y correcciones 0.2.2

Ejecutado por el agente local sobre `Modelo_Copia` (Revit en español) con la rama `claude/revision-fase1-revit`
en 0.2.1. Resultado y qué se corrigió en 0.2.2:

| # | Prueba | Resultado en Revit | Causa | Corrección en 0.2.2 |
|---|---|---|---|---|
| 1 | `probar_revit.py` (9 pruebas) | 7/9: fallan 6 y 7 con `404 Parameter 'Comments' not found` | Revit en español llama `Comentarios` al parámetro; `LookupParameter` solo entiende el nombre localizado y el guion usaba `Comments` | `set_parameter`, `modify_element` y `set_type_parameter` aceptan alias ingleses y nombres de `BuiltInParameter` (`resolver_parametro`); `get_element_properties` lista `builtin`; el guion elige el parámetro de comentarios por `builtin` |
| 2 | `set_parameter` `Altura desconectada` = 3000 | Correcto: `1.20` → `3.00` (m) y la caja sube 3000 mm | — (corrección 5 de 0.2.1 validada) | La respuesta añade `antes_valor`/`despues_valor` + `unidad` (3000, mm) y `parameter_name_revit`, para no tener que deducirlo de la caja envolvente |
| 3 | `delete_elements` muro + puerta | Borra y lista 4 alojados en cascada, `ok: true` | La puerta elegida (153543) no estaba alojada en el muro 151574 (cajas envolventes a 12 m): la corrección 1 de 0.2.1 sigue **sin validar en Revit** | Prueba CPython del caso (muro y puerta en la misma lista) y `location_mm`/`host_id` en `get_element_properties` para elegir bien la puerta |
| 4 | `create_opening` en 165465 | `ok: false`, `count: 0`, `detalle: no se encontraron los elementos ['615578']` en los dos intentos | **Fallo de la verificación, no de Revit**: `describir_elemento` no aceptaba ids enteros (`get_element_id_value(int)` lanzaba), así que `verificar_creados` daba por desaparecido TODO elemento creado (las comillas en `['615578']` son la huella: el id cayó al texto de reserva). Afectaba a las 25 rutas de creación (`create_wall`, `place_family`, `create_room`, `create_sheet`, etiquetas, cotas, conductos...) y a `creados` de `execute_code`. El hueco 615578 probablemente existe en el modelo | `get_element_id_value` acepta enteros; pruebas de `describir_elemento`/`resultado_creacion` con ids enteros |
| 4b | `create_opening` segundo intento (z = 900..2100) | Igual que 4 | El muro está en el nivel `Techo Garita` (+2925): el hueco pedido quedaba por debajo del muro. La `z` es absoluta (como `bbox_mm`), no un desfase desde la base. Revit crea el hueco y, si no corta el muro, lo borra al confirmar con el aviso `Rectangular opening doesn't cut its host` (sin error, transacción confirmada), y `_FailureSwallower` borraba los avisos sin guardarlos | `create_opening` sitúa el rectángulo respecto al muro (`_comprobar_hueco_en_muro`) y responde 400 con los números si está fuera del plano, de la longitud o de la altura; devuelve `en_muro` y `rectangulo_revit_mm`. `_FailureSwallower` guarda los avisos y toda ruta de escritura los devuelve en `avisos_revit`; `verificacion.detalle` los cita si el elemento desapareció |
| — | `execute_revit_code` para leer los extremos del muro | Dos llamadas falladas (`ElementId(int)` ambiguo en 2027; `from utils import` no resuelve) | El agente no tiene cómo obtener la curva del muro sin código | `get_element_properties` devuelve `location_mm` (start/end/longitud); `make_element_id` y `get_element_id_value` en el espacio de nombres de `execute_revit_code` |

Sin cambios: `purge_unused` (5) y `set_project_location` (6) siguen pendientes de validar.

### Qué validar en Revit con 0.2.2 (copia del modelo)

1. `python pruebas\probar_revit.py` completo: debe dar 9/9 (las pruebas 6 y 7 con `Comentarios`).
2. `get_element_properties(165465)`: comprobar `location_mm` (start/end), `bbox_mm` y `builtin` en los parámetros.
3. `create_opening` en 165465 con `simular=true` y `z` absoluta (por ejemplo 3825 y 5025): debe devolver `en_muro`
   sin `sobresale`; luego real: `ok: true`, `creados[0].id` y `rectangulo_revit_mm`, y confirmar el hueco en Revit.
4. `create_opening` con `z` = 900 y 2100 en el mismo muro: debe responder `400` explicando que el muro va de 2925 a
   5925 y qué `z` usar, sin crear nada.
5. `delete_elements` con un muro y una puerta cuyo `host_id` (en `get_element_properties`) sea ese muro.
6. Comprobar en Revit si el hueco 615578 (o 615589) del primer intento sigue en el modelo: si está, confirma que
   el fallo era de la verificación y no de Revit.
