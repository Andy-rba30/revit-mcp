# Instrucciones para el agente que usa el MCP de Revit

Este texto se envía como `instructions` del servidor MCP (`main.py`) y se
mantiene aquí para poder leerlo y revisarlo. Habla al agente en segunda
persona.

## 1. Precedencia de herramientas

1. Usa siempre la herramienta específica que cubre la acción (`create_*`,
   `set_parameter`, `delete_elements`, `change_element_type`,
   `set_project_location`, `purge_unused`...). Cada una valida, hace copia de
   seguridad, registra la acción, verifica el resultado y aparece en Revit
   como una entrada de deshacer `IA: <acción>`.
2. `execute_revit_code` es el último recurso: sólo cuando **ninguna**
   herramienta cubre la acción. Es obligatorio:
   - rellenar `description` (pocas palabras; nombra la entrada de deshacer);
   - explicar al usuario, **antes** de ejecutarlo, qué hará el código y qué
     elementos toca;
   - no borrar colecciones con `doc.Delete(<colección>)` (se rechaza salvo
     `forzar=true`); para borrar usa `delete_elements` con la lista de ids.
3. Las herramientas de lectura (`get_*`, `list_*`, `find_elements`,
   `read_log`) no cambian nada: úsalas sin pedir permiso.

## 2. Flujo obligatorio para cualquier cambio en el modelo

1. `get_revit_status` → confirma que Revit responde y qué documento está
   abierto.
2. `get_revit_model_info` → unidades, si es de trabajo compartido, ruta y
   fecha del último guardado, puntos base.
3. Lee lo que vas a tocar: `find_elements`, `get_element_properties`,
   `list_levels`, `list_families`, `list_element_types`, `list_worksets`
   según el caso. Nunca actúes sobre ids que no hayas leído en esta sesión.
4. Propón un plan en una tabla: elemento (id, categoría, tipo), cambio
   (antes → después). Para creaciones: tipo, nivel, coordenadas en mm.
5. Ejecuta la herramienta con `simular=true` y muestra `haria` al usuario.
6. Pide confirmación explícita al usuario.
7. Ejecuta la herramienta real (`simular=false`).
8. Comprueba la respuesta: `ok`, `verificacion.coincide`, `antes`/`despues`
   o `creados`/`eliminados`. Si `ok` es `false`, informa del `detalle` y
   **no reintentes por tu cuenta**: pregunta al usuario.
9. Si se crearon elementos, llama a `list_warnings` y comenta las
   advertencias nuevas.

Todas las herramientas de escritura devuelven además `copia` (ruta de la
copia de seguridad del **último guardado** en `backups\`, no del estado en
memoria) y `ms`. El registro completo está en `mcp_log.jsonl` junto al `.rvt`
(léelo con `read_log`).

## 3. Reglas de dominio

- **Nunca** llames a `delete_elements` sin haber listado antes los ids y su
  categoría (`find_elements` o `get_element_properties`) y sin mostrarlos al
  usuario. Más de 200 elementos por llamada exige `forzar=true` y una
  confirmación aparte.
- **No borres niveles ni rejillas** que tengan elementos alojados: comprueba
  antes con `find_elements(level_name=...)` (y `find_elements(category=
  "OST_Grids")` para las rejillas). Borrar un nivel borra en cascada todo lo
  que depende de él.
- En modelos de **trabajo compartido** (`is_workshared: true`): llama a
  `list_worksets` y no toques subproyectos ni elementos con otro
  `propietario`; un `409` significa que el elemento está prestado a otra
  persona. No hay copia de seguridad local: se confía en las del central.
- **No guardes el documento** (`save_document`) sin que el usuario lo pida
  expresamente, y **nunca sincronices con central** desde el MCP.
- Unidades: todas las herramientas reciben **milímetros** (los ángulos en
  grados). Los CSV de Civil 3D para `create_toposolid` se asumen en metros
  salvo `units`.
- Revisa `list_warnings` después de crear elementos y `list_links` /
  `get_project_location` antes de cambiar coordenadas.
- Con `purge_unused` ejecuta siempre `simular=true` primero y muestra la
  lista completa; purgar es irreversible tras guardar.
- No abras transacciones en `execute_revit_code`: el manejador ya abre una.

## 4. Glosario español ↔ API de Revit

| Español | Revit / API |
|---|---|
| Nivel | `Level` |
| Rejilla | `Grid` |
| Muro | `Wall` |
| Suelo | `Floor` |
| Cubierta | `Roof` |
| Pilar | `Column` (`StructuralType.Column`, categoría `OST_StructuralColumns`) |
| Viga | `Structural Framing` (`OST_StructuralFraming`, `StructuralType.Beam`) |
| Zapata | `Structural Foundation` (`OST_StructuralFoundation`; corrida = `WallFoundation`) |
| Habitación | `Room` |
| Plano | `Sheet` (`ViewSheet`) |
| Tabla de planificación | `Schedule` (`ViewSchedule`) |
| Familia / Tipo / Ejemplar | `Family` / `Type` (`FamilySymbol`, `ElementType`) / `Instance` |
| Parámetro de tipo / de ejemplar | `Type parameter` (`set_type_parameter`) / `Instance parameter` (`set_parameter`) |
| Subproyecto | `Workset` |
| Fase | `Phase` |
| Opción de diseño | `Design Option` |
| Punto base del proyecto | `Project Base Point` |
| Punto de reconocimiento | `Survey Point` |
| Coordenadas compartidas | `Shared Coordinates` (`AcquireCoordinates`) |
| Norte verdadero | `True North` (`ProjectPosition.Angle`) |
| Toposólido | `Toposolid` (Revit 2024+) |
| Vínculo | `Link` (`RevitLinkInstance`, `ImportInstance`) |
| Advertencia | `Warning` (`doc.GetWarnings()`) |
| Unir geometría | `Join Geometry` (`JoinGeometryUtils`) |
| Hueco | `Opening` (`doc.Create.NewOpening`) |
| Purgar sin usar | `Purge Unused` |
| Deshacer | `Undo` (entradas `IA: ...`) |

## 5. Errores típicos y qué hacer

| Mensaje | Causa | Acción |
|---|---|---|
| `Multiple targets could match` | Revit 2027: `DB.ElementId(int)` es ambiguo | Usa `DB.ElementId(System.Int64(id))` (en `execute_revit_code` ya tienes `System`). |
| `Transaction error` / `Starting a transaction from an external application running outside of API context is not allowed` | Se abrió una transacción dentro del código | El manejador ya abre una: no anides `DB.Transaction`. |
| `open_transaction: true` | Quedó una transacción abierta que no se pudo cerrar | Pide al usuario que la revise en Revit antes de seguir; no ejecutes nada más. |
| `409` "Hay una transacción abierta de otra operación" | Revit está en medio de otra edición (`doc.IsModifiable`) | Espera a que el usuario termine y repite. |
| `409` "de solo lectura" / "no editable" / "owned by" | Documento de solo lectura o elemento/subproyecto de otro usuario | No se puede escribir; informa al usuario. |
| `503` "No active Revit document" | No hay documento activo | Pide al usuario que abra un proyecto. |
| `401` "token ausente o incorrecto" | Revit se reinició o el conector no arrancó | El puente reintenta una vez; si persiste, recarga pyRevit. |
| `421 Invalid Host header` | Cabecera `Host` no permitida en el puente (protección DNS rebinding) | Conecta al puente por `127.0.0.1` o `localhost`. |
| `400` "límite es 200 por llamada" | Más de 200 elementos | Divide la llamada o pide `forzar=true` con confirmación. |
| `400` "description is required" | `execute_revit_code` sin descripción | Añade `description`. |
| `400` "deletes a collection" | `doc.Delete(<colección>)` en el código | Usa `delete_elements` con ids listados. |
| `"Revit no está abierto o el conector no ha iniciado"` | No existe el archivo del token | Abre Revit con la extensión cargada. |
| `"Revit no respondió en N s"` | Tiempo de espera agotado (30 s lectura, 120 s escritura, 600 s operaciones largas) | La operación puede seguir en curso: comprueba con `read_log` / `get_revit_status` antes de repetir. |
| `ok: false` con `verificacion.coincide: false` | El valor releído no coincide con lo pedido | No reintentes; muestra `detalle` al usuario. |
