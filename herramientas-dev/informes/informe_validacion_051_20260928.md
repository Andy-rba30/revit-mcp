# Informe de validación de la 0.5.1 (28/09/2026)

Validación hecha con el prompt `herramientas-dev/VALIDACION_051.md`: correcciones de los fallos de la 2b.

**Entorno:**
- Revit 2027 (build 27.2.0.39) en español, sobre `Modelo_Copia.rvt`.
- `main` en `200d9b6` (merge de la 0.5.1), con `__version__ = "0.5.1"`.
- 52 herramientas sincronizadas.

**Resultado:**
- `probar_revit.py --fase 2b`: **14/14**, salida 0.
- Los cinco fallos de la validación de la 0.5.0 quedan resueltos.

## Pasos

| Paso | Herramienta | Resultado | Detalle |
|---|---|---|---|
| 1 | `probar_revit.py --fase 2b` | OK | 14/14. 2b.3b: los 8 elementos sin modelo analítico. 2b.4a: `409` `no_soportado` `sin_modelo_analitico` (2b.4b no aplica) |
| 2 | `create_grid_and_levels` + `create_steel_frame` | OK | 4 rejillas auxiliares, 4 pilares y 4 vigas; `verificacion.coincide`; 203/514 ms |
| 3 | `set_structural_properties` | OK | Solo liberaciones: `409` `sin_modelo_analitico` con la explicación. Con `z_offset_mm=-50`: `200`, `coincide: true` y liberaciones en `fallidos` |
| 4 | `list_types(category="connections")` | OK | `409` `no_soportado` `sin_tipos`, porque el modelo no tiene tipos de conexión; ya no sale "Unnamed". `create_steel_connection` no aplica |
| 5 | `split_beam(at_mm=[2000])` | OK + manual | `metodo: FamilyInstance.Split`, `verificacion.coincide: true`. El original (`segment` 0) mide 2000 mm y el tramo nuevo 4000 mm; 212/506 ms |
| 6 | `join_geometry(coping=true)` | OK + manual | La simulación eligió la viga como recortada contra el pilar. En la ejecución real, `coping.applied` y `despues.coped: true`, sin fallos |
| 7 | `join_geometry` sin `coping` | OK | `200` (antes `500`), `ok: false`, `fallidos` con "The elements cannot be joined" y `nota` que recomienda `coping=true` |
| 8 | `export` al Escritorio | OK | CSV de 8 filas e IFC de 1171 KB en `C:\Users\Andy Bayona Antón\Desktop` |
| 9 | `export` a `C:\IA\salidas` | OK | CSV e IFC creados; la carpeta se crea sola |
| 10 | `read_log(last_n=15)` | OK | Entradas de escritura; las simuladas con `simulado: true` |
| 11 | Menú Deshacer | manual, confirmado | Ver abajo |
| 12 | Limpieza | OK | 13 elementos borrados, Revit cerrado sin guardar. `Modelo_Copia.rvt` quedó igual (15/09/2026 16:21:01, 41316352 bytes). Exportaciones borradas |

## Comprobaciones manuales del usuario

- **Menú Deshacer (paso 11).** Confirmado con una captura de Revit. Las entradas, de arriba abajo:
  1. `IA: Recortar acero en cadena (2 elementos)`
  2. `IA: Dividir viga 615582`
  3. `IA: Propiedades estructurales (4 elementos)`
  4. `IA: Portico metalico`
  5. `IA: Rejilla y niveles`

  Una entrada por operación y ninguna por elemento.
- **Viga partida (paso 5)** y **recorte de la viga contra el pilar (paso 6).** El usuario los revisó en la interfaz
  de Revit y los dio por buenos al cerrar la validación.

## Miembros de la API

| Miembro | Resultado |
|---|---|
| `FamilyInstance.Split` | Funciona; no hizo falta la alternativa con `CopyElement` |
| `FamilyInstance.AddCoping`, `GetCopingIds` | Funcionan |
| `JoinGeometryUtils.JoinGeometry` entre perfiles de acero | No admitido por Revit; ahora va a `fallidos` |
| `System.IO.Directory`, `File.WriteAllText`, `Document.Export` (IFC) | Funcionan en el Escritorio con "ó" y en `C:\IA\salidas` |
| `StructuralConnectionHandler.Create` | Sin probar: el modelo no tiene tipos de conexión |
| `StructuralFramingUtils.DisallowJoinAtEnd` | Sin probar: se usó `Split` |

## Pendiente

Lo que no se pudo probar con este modelo: crear conexiones de acero, cerchas, modelo analítico con liberaciones y
`fix_analytical_alignment`, y carga de perfiles desde la biblioteca. Queda en
`miembros_por_verificar_revit.md` hasta validarlo con un modelo que tenga esos elementos.
