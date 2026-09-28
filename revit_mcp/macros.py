# -*- coding: UTF-8 -*-
"""
Macros de proyecto para Revit MCP (0.3.0):

  POST /grid_levels/    x_spacings_mm[], y_spacings_mm[], x_names, y_names,
                        levels[] {name, elevation_mm}, origin_mm, extension_mm
  POST /sheet_set/      sheets[] {number, name, title_block, views[] {view_name | view_id, position_mm}}
  POST /import_civil/   file_path* (LandXML, CSV PNEZD o DWG), level*, use_shared_coordinates,
                        origin_offset_mm, units, type_name, placement, forzar

Reutilizan el codigo interno de create_grid (structure.crear_rejilla),
create_level (building.crear_nivel), create_sheet (documentation.elegir_cajetin
y crear_plano), create_toposolid (topografia.leer_csv_puntos, tipo_toposolido y
crear_toposolido) y link_file (interop.vincular_cad). Todas pasan por
escritura.ejecutar (copia, log, `simular` con `plan`, transaccion "IA: ..." y
`creados`) y aplican comprobar_alcance al total de elementos que van a crear.

Idioma de Revit: niveles, vistas, cajetines y tipos se buscan por el nombre
que muestra Revit (el que devuelven list_levels, list_revit_views...), nunca
por un nombre ingles fijo.
"""

from utils import (
    elevacion_interna, elevacion_mostrada,
    get_element_name, get_element_id_value, make_element_id, xyz_desde_mm, punto_a_mm,
    elementos_por_nombre, mapa_niveles, buscar_vista, MM_TO_FEET, FEET_TO_MM, leer_texto_utf8,
)
from seguridad import requiere_token
from escritura import (
    ejecutar, transaccion, simulacion, EscrituraRechazada, resultado_creacion, comprobar_alcance,
    es_forzado,
)
from structure import crear_rejilla
from building import crear_nivel
from documentation import elegir_cajetin, crear_plano
from topografia import (
    leer_csv_puntos, tipo_toposolido, crear_toposolido, comprobar_toposolido_disponible,
    MAX_PUNTOS, FACTOR_A_MM,
)
from interop import vincular_cad, PLACEMENTS
from coordenadas import ubicacion_proyecto, puntos_base, comprobar_puntos_base_libres
from pyrevit import routes, revit, DB
import os
import re
import logging

logger = logging.getLogger(__name__)

try:
    _texto = unicode  # IronPython 2.7
    _cadena = basestring
except NameError:  # pragma: no cover - CPython 3 en las pruebas
    _texto = str
    _cadena = str

LONGITUD_DEFECTO_MM = 10000.0
EXTENSION_DEFECTO_MM = 2000.0
NOMBRE_PLANO_DEFECTO = u"Unnamed Sheet"
EXTENSIONES_LANDXML = (".xml", ".landxml")
EXTENSIONES_CSV = (".csv", ".txt", ".pnezd")
EXTENSIONES_CAD = (".dwg", ".dxf", ".dgn")
# LandXML: <Metric linearUnit="meter"> / <Imperial linearUnit="foot"> -> factor a mm
UNIDADES_LANDXML = {
    "meter": 1000.0, "metre": 1000.0, "millimeter": 1.0, "millimetre": 1.0, "centimeter": 10.0,
    "centimetre": 10.0, "kilometer": 1000000.0, "kilometre": 1000000.0, "foot": 304.8, "feet": 304.8,
    "ussurveyfoot": 304.8006096, "inch": 25.4,
}


def _texto_seguro(valor):
    if valor is None:
        return u""
    try:
        return _texto(valor)
    except Exception:
        return u"?"


def _numero(valor, etiqueta):
    try:
        if isinstance(valor, bool):
            raise ValueError("bool")
        return float(valor)
    except (TypeError, ValueError):
        raise EscrituraRechazada("{} must be a number".format(etiqueta), 400)


# ---------------------------------------------------------------------------
# Rejilla y niveles
# ---------------------------------------------------------------------------
def _letras(indice):
    """0 -> A, 25 -> Z, 26 -> AA."""
    indice = int(indice)
    nombre = u""
    while True:
        nombre = chr(ord("A") + indice % 26) + nombre
        indice = indice // 26 - 1
        if indice < 0:
            break
    return nombre


def _indice_letras(texto):
    """"A" -> 0, "Z" -> 25, "AA" -> 26; None si no son solo letras."""
    valor = 0
    for caracter in texto.upper():
        if not ("A" <= caracter <= "Z"):
            return None
        valor = valor * 26 + (ord(caracter) - ord("A") + 1)
    return valor - 1


def nombres_correlativos(spec, cantidad, por_defecto):
    """`cantidad` nombres: `spec` es la lista completa, el primer nombre ("1", "A",
    "P1") desde el que continuar, o None (empieza en `por_defecto`)."""
    if cantidad <= 0:
        return []
    if isinstance(spec, (list, tuple)):
        nombres = [_texto_seguro(n).strip() for n in spec]
        if len(nombres) != cantidad:
            raise ValueError("{} names given for {} grids".format(len(nombres), cantidad))
        if any(not n for n in nombres):
            raise ValueError("grid names must not be empty")
        return nombres
    inicio = _texto_seguro(spec).strip() if spec not in (None, "") else por_defecto
    coincidencia = re.match(r"^(.*?)(\d+)$", inicio)
    if coincidencia:
        prefijo = coincidencia.group(1)
        numero = int(coincidencia.group(2))
        ancho = len(coincidencia.group(2))
        return [u"{}{}".format(prefijo, _texto(numero + i).zfill(ancho)) for i in range(cantidad)]
    indice = _indice_letras(inicio)
    if indice is None:
        raise ValueError(
            "cannot continue the sequence from '{}': use digits (1), letters (A) or a list of names".format(inicio)
        )
    return [_letras(indice + i) for i in range(cantidad)]


def _espaciados(valor, etiqueta):
    if valor in (None, ""):
        return []
    if not isinstance(valor, (list, tuple)):
        raise EscrituraRechazada("{} must be a list of spacings in mm".format(etiqueta), 400)
    espaciados = []
    for indice, crudo in enumerate(valor):
        numero = _numero(crudo, "{}[{}]".format(etiqueta, indice))
        if numero <= 0:
            raise EscrituraRechazada("{}[{}] must be greater than 0 mm".format(etiqueta, indice), 400)
        espaciados.append(numero)
    return espaciados


def planificar_rejilla_y_niveles(doc, data):
    """Valida la peticion y devuelve el plan (nombres, posiciones en mm y niveles)."""
    try:
        origen = xyz_desde_mm(data.get("origin_mm") or {"x": 0, "y": 0, "z": 0})
    except ValueError as error:
        raise EscrituraRechazada("origin_mm: {}".format(error), 400)
    extension_mm = _numero(data.get("extension_mm", EXTENSION_DEFECTO_MM), "extension_mm")
    if extension_mm < 0:
        raise EscrituraRechazada("extension_mm must be 0 or greater", 400)
    ex = _espaciados(data.get("x_spacings_mm"), "x_spacings_mm")
    ey = _espaciados(data.get("y_spacings_mm"), "y_spacings_mm")
    nx = len(ex) + 1 if ex else 0
    ny = len(ey) + 1 if ey else 0
    try:
        x_names = nombres_correlativos(data.get("x_names"), nx, "1")
        y_names = nombres_correlativos(data.get("y_names"), ny, "A")
    except ValueError as error:
        raise EscrituraRechazada("grid names: {}".format(error), 400)

    origen_mm = punto_a_mm(origen)
    total_x = sum(ex) if ex else LONGITUD_DEFECTO_MM
    total_y = sum(ey) if ey else LONGITUD_DEFECTO_MM
    grids_x = []
    acumulado = 0.0
    for indice, nombre in enumerate(x_names):
        if indice > 0:
            acumulado += ex[indice - 1]
        x = origen_mm["x"] + acumulado
        grids_x.append({
            "name": nombre, "x_mm": round(x, 1),
            "start_mm": {"x": round(x, 1), "y": round(origen_mm["y"] - extension_mm, 1), "z": origen_mm["z"]},
            "end_mm": {"x": round(x, 1), "y": round(origen_mm["y"] + total_y + extension_mm, 1), "z": origen_mm["z"]},
        })
    grids_y = []
    acumulado = 0.0
    for indice, nombre in enumerate(y_names):
        if indice > 0:
            acumulado += ey[indice - 1]
        y = origen_mm["y"] + acumulado
        grids_y.append({
            "name": nombre, "y_mm": round(y, 1),
            "start_mm": {"x": round(origen_mm["x"] - extension_mm, 1), "y": round(y, 1), "z": origen_mm["z"]},
            "end_mm": {"x": round(origen_mm["x"] + total_x + extension_mm, 1), "y": round(y, 1), "z": origen_mm["z"]},
        })

    existentes = elementos_por_nombre(
        DB.FilteredElementCollector(doc).OfClass(DB.Grid).WhereElementIsNotElementType().ToElements()
    )
    pedidos = [g["name"] for g in grids_x + grids_y]
    repetidos = sorted(set(n for n in pedidos if pedidos.count(n) > 1))
    if repetidos:
        raise EscrituraRechazada(
            "Grid names repeated in the request: {}".format(", ".join(repetidos)), 400, {"repeated": repetidos},
        )
    conflictos = sorted(n for n in pedidos if n in existentes)
    if conflictos:
        raise EscrituraRechazada(
            "These grid names already exist in the project: {}. Pass x_names/y_names to continue the "
            "sequence elsewhere".format(", ".join(conflictos)), 400,
            {"existing": conflictos, "available_start": None},
        )

    niveles_pedidos = data.get("levels") or []
    if not isinstance(niveles_pedidos, (list, tuple)):
        raise EscrituraRechazada("levels must be a list of {name, elevation_mm}", 400)
    niveles_existentes = mapa_niveles(doc)
    levels = []
    nombres_niveles = []
    for indice, nivel in enumerate(niveles_pedidos):
        if not isinstance(nivel, dict):
            raise EscrituraRechazada("levels[{}] must be an object {{name, elevation_mm}}".format(indice), 400)
        elevacion = nivel.get("elevation_mm")
        if elevacion is None:
            elevacion = nivel.get("elevation")
        if elevacion is None:
            raise EscrituraRechazada("levels[{}]: elevation_mm is required".format(indice), 400)
        elevacion = _numero(elevacion, "levels[{}].elevation_mm".format(indice))
        nombre = _texto_seguro(nivel.get("name")).strip() or None
        if nombre:
            if nombre in niveles_existentes:
                raise EscrituraRechazada(
                    u"Level '{}' already exists".format(nombre), 400,
                    {"available_levels": sorted(niveles_existentes.keys())},
                )
            if nombre in nombres_niveles:
                raise EscrituraRechazada(u"Level name '{}' repeated in the request".format(nombre), 400)
            nombres_niveles.append(nombre)
        levels.append({"name": nombre, "elevation_mm": elevacion})
    levels.sort(key=lambda n: n["elevation_mm"])

    if not grids_x and not grids_y and not levels:
        raise EscrituraRechazada("Nothing to create: give x_spacings_mm, y_spacings_mm and/or levels", 400)

    return {
        "origin_mm": origen_mm,
        "extension_mm": extension_mm,
        "grids_x": grids_x,
        "grids_y": grids_y,
        "levels": levels,
        "counts": {"grids_x": len(grids_x), "grids_y": len(grids_y), "grids": len(grids_x) + len(grids_y),
                   "levels": len(levels), "total": len(grids_x) + len(grids_y) + len(levels)},
    }


# ---------------------------------------------------------------------------
# Conjunto de planos
# ---------------------------------------------------------------------------
def vistas_colocadas(doc):
    """{view_id: numero de plano} de las vistas y tablas que ya estan en un plano."""
    colocadas = {}
    try:
        for vp in DB.FilteredElementCollector(doc).OfClass(DB.Viewport).WhereElementIsNotElementType():
            try:
                plano = doc.GetElement(vp.SheetId)
                colocadas[get_element_id_value(vp.ViewId)] = _texto_seguro(plano.SheetNumber) if plano is not None else u"?"
            except Exception:
                continue
    except Exception:
        pass
    try:
        for instancia in DB.FilteredElementCollector(doc).OfClass(DB.ScheduleSheetInstance).WhereElementIsNotElementType():
            try:
                plano = doc.GetElement(instancia.OwnerViewId)
                colocadas[get_element_id_value(instancia.ScheduleId)] = _texto_seguro(plano.SheetNumber) if plano is not None else u"?"
            except Exception:
                continue
    except Exception:
        pass
    return colocadas


def _numeros_de_plano(doc):
    numeros = set()
    try:
        for plano in DB.FilteredElementCollector(doc).OfClass(DB.ViewSheet).WhereElementIsNotElementType():
            try:
                numeros.add(_texto_seguro(plano.SheetNumber))
            except Exception:
                continue
    except Exception:
        pass
    return numeros


def _punto_plano(posicion, etiqueta):
    if posicion is None:
        return None
    if not isinstance(posicion, dict):
        raise EscrituraRechazada("{}: position_mm must be {{x, y}} in mm on the sheet".format(etiqueta), 400)
    try:
        return DB.XYZ(float(posicion.get("x", 0)) * MM_TO_FEET, float(posicion.get("y", 0)) * MM_TO_FEET, 0.0)
    except (TypeError, ValueError):
        raise EscrituraRechazada("{}: position_mm must be {{x, y}} in mm on the sheet".format(etiqueta), 400)


def planificar_planos(doc, data):
    """Valida sheets[] y devuelve el plan: cajetin, numero, nombre y vistas por plano."""
    sheets = data.get("sheets") or []
    if not isinstance(sheets, (list, tuple)) or not sheets:
        raise EscrituraRechazada("sheets is required: a list of {number, name, title_block, views[]}", 400)
    colocadas = vistas_colocadas(doc)
    numeros_existentes = _numeros_de_plano(doc)
    numeros_pedidos = set()
    vistas_pedidas = set()
    planes = []
    faltan_vistas = []
    for indice, sheet in enumerate(sheets):
        etiqueta = "sheets[{}]".format(indice)
        if not isinstance(sheet, dict):
            raise EscrituraRechazada("{} must be an object {{number, name, title_block, views[]}}".format(etiqueta), 400)
        numero = _texto_seguro(sheet.get("number") or sheet.get("sheet_number")).strip() or None
        nombre = _texto_seguro(sheet.get("name") or sheet.get("sheet_name")).strip() or NOMBRE_PLANO_DEFECTO
        if numero:
            if numero in numeros_existentes:
                raise EscrituraRechazada("{}: sheet number '{}' already exists in the project".format(etiqueta, numero), 400)
            if numero in numeros_pedidos:
                raise EscrituraRechazada("{}: sheet number '{}' repeated in the request".format(etiqueta, numero), 400)
            numeros_pedidos.add(numero)
        cajetin = elegir_cajetin(doc, _texto_seguro(sheet.get("title_block") or sheet.get("title_block_name")).strip() or None)
        vistas = []
        saltadas = []
        pedidas = sheet.get("views") or []
        if not isinstance(pedidas, (list, tuple)):
            raise EscrituraRechazada("{}: views must be a list of {{view_name, position_mm}}".format(etiqueta), 400)
        for j, pedida in enumerate(pedidas):
            if isinstance(pedida, _cadena):
                pedida = {"view_name": pedida}
            if not isinstance(pedida, dict):
                raise EscrituraRechazada("{}.views[{}] must be an object {{view_name, position_mm}}".format(etiqueta, j), 400)
            vista = None
            nombre_vista = _texto_seguro(pedida.get("view_name") or pedida.get("name")).strip()
            if pedida.get("view_id") is not None:
                try:
                    vista = doc.GetElement(make_element_id(pedida["view_id"]))
                except ValueError:
                    vista = None
                if not isinstance(vista, DB.View):
                    faltan_vistas.append(u"id {}".format(pedida["view_id"]))
                    continue
                nombre_vista = get_element_name(vista)
            elif nombre_vista:
                vista = buscar_vista(doc, nombre_vista)
                if vista is None:
                    faltan_vistas.append(nombre_vista)
                    continue
            else:
                raise EscrituraRechazada("{}.views[{}]: view_name (or view_id) is required".format(etiqueta, j), 400)
            vista_id = get_element_id_value(vista)
            punto = _punto_plano(pedida.get("position_mm"), "{}.views[{}]".format(etiqueta, j))
            if isinstance(vista, DB.ViewSheet):
                saltadas.append({"view": nombre_vista, "view_id": vista_id, "reason": u"es un plano, no se puede colocar en otro plano"})
                continue
            # Una leyenda puede estar en varios planos; el resto de vistas solo en uno.
            es_leyenda = False
            try:
                es_leyenda = vista.ViewType == DB.ViewType.Legend
            except Exception:
                pass
            if vista_id in colocadas and not es_leyenda:
                saltadas.append({"view": nombre_vista, "view_id": vista_id,
                                 "reason": u"ya esta en el plano {}".format(colocadas[vista_id])})
                continue
            if vista_id in vistas_pedidas and not es_leyenda:
                saltadas.append({"view": nombre_vista, "view_id": vista_id, "reason": u"ya se coloca en otro plano de esta misma peticion"})
                continue
            vistas_pedidas.add(vista_id)
            vistas.append({
                "view": nombre_vista, "view_id": vista_id, "vista": vista,
                "is_schedule": isinstance(vista, DB.ViewSchedule),
                "position_mm": pedida.get("position_mm"), "punto": punto,
            })
        planes.append({
            "idx": indice, "number": numero, "name": nombre, "cajetin": cajetin,
            "title_block": get_element_name(cajetin), "views": vistas, "skipped": saltadas,
        })
    if faltan_vistas:
        raise EscrituraRechazada(
            u"Views not found: {}".format(u", ".join(faltan_vistas)), 404, {"missing_views": faltan_vistas},
        )
    return planes


def _centro_cajetin(doc, plano):
    """Centro (pies) del cajetin del plano, para colocar una vista sin position_mm."""
    try:
        cajetin = (
            DB.FilteredElementCollector(doc, plano.Id)
            .OfCategory(DB.BuiltInCategory.OST_TitleBlocks)
            .WhereElementIsNotElementType()
            .FirstElement()
        )
        caja = cajetin.get_BoundingBox(plano) if cajetin is not None else None
        if caja is not None:
            return DB.XYZ((caja.Min.X + caja.Max.X) / 2.0, (caja.Min.Y + caja.Max.Y) / 2.0, 0.0)
    except Exception:
        pass
    return DB.XYZ(0.0, 0.0, 0.0)


def _resumen_plano(plan):
    return {
        "number": plan["number"], "name": plan["name"], "title_block": plan["title_block"],
        "views": [{"view": v["view"], "view_id": v["view_id"], "schedule": v["is_schedule"],
                   "position_mm": v["position_mm"]} for v in plan["views"]],
        "skipped": plan["skipped"],
    }


# ---------------------------------------------------------------------------
# Importacion desde Civil 3D
# ---------------------------------------------------------------------------
def leer_landxml(ruta, unidades=None):
    """Puntos (mm) de <Surface><Definition><Pnts><P> (o de <CgPoints>) de un LandXML.

    Cada punto LandXML es "norte este cota" (Y X Z): se guarda x = este, y = norte.
    Devuelve (puntos, formato, avisos). Lanza ValueError."""
    texto = leer_texto_utf8(ruta)
    avisos = []
    etiqueta_unidad = None
    factor = None
    if unidades:
        if unidades not in FACTOR_A_MM:
            raise ValueError("units must be one of {}".format(sorted(FACTOR_A_MM.keys())))
        factor = FACTOR_A_MM[unidades]
        etiqueta_unidad = unidades
    else:
        coincidencia = re.search(r'<(Metric|Imperial)\b[^>]*\blinearUnit="([^"]+)"', texto, re.I)
        if coincidencia:
            etiqueta_unidad = coincidencia.group(2)
            factor = UNIDADES_LANDXML.get(etiqueta_unidad.lower().replace(" ", ""))
        if factor is None:
            avisos.append(u"linearUnit {} no reconocida; se asumen metros".format(
                repr(etiqueta_unidad) if etiqueta_unidad else "ausente"))
            factor = 1000.0
            etiqueta_unidad = "meter (asumido)"
    bloques = re.findall(r"<Pnts\b[^>]*>(.*?)</Pnts>", texto, re.S | re.I)
    origen = "Surface/Definition/Pnts"
    patron = r"<P\b[^>]*>([^<]+)</P>"
    if not bloques:
        bloques = re.findall(r"<CgPoints\b[^>]*>(.*?)</CgPoints>", texto, re.S | re.I)
        origen = "CgPoints"
        patron = r"<CgPoint\b[^>]*>([^<]+)</CgPoint>"
    if not bloques:
        raise ValueError("No <Surface>/<Definition>/<Pnts> nor <CgPoints> found in the LandXML file")
    puntos = []
    for bloque in bloques:
        for coincidencia in re.finditer(patron, bloque, re.I):
            partes = coincidencia.group(1).split()
            if len(partes) < 3:
                avisos.append(u"punto saltado: '{}'".format(coincidencia.group(1).strip()[:40]))
                continue
            try:
                norte, este, cota = float(partes[0]), float(partes[1]), float(partes[2])
            except ValueError:
                avisos.append(u"punto saltado: '{}'".format(coincidencia.group(1).strip()[:40]))
                continue
            puntos.append({"x": este * factor, "y": norte * factor, "z": cota * factor})
            if len(puntos) > MAX_PUNTOS:
                raise ValueError("LandXML has more than {} points; thin it out first".format(MAX_PUNTOS))
    if not puntos:
        raise ValueError("LandXML contains no valid points")
    formato = "LandXML {} (N E Z, {} = {} mm)".format(origen, etiqueta_unidad, factor)
    return puntos, formato, avisos


def _desfase(data):
    crudo = data.get("origin_offset_mm")
    if not crudo:
        return None
    try:
        xyz = xyz_desde_mm(crudo)
    except ValueError as error:
        raise EscrituraRechazada("origin_offset_mm: {}".format(error), 400)
    if xyz.GetLength() < 1e-9:
        return None
    return xyz


def _vista_planta_de(doc, nivel):
    """Vista de planta (FloorPlan, no plantilla) asociada al nivel, o None."""
    try:
        for vista in DB.FilteredElementCollector(doc).OfClass(DB.ViewPlan).WhereElementIsNotElementType():
            try:
                if vista.IsTemplate or vista.ViewType != DB.ViewType.FloorPlan:
                    continue
                nivel_vista = vista.GenLevel
                if nivel_vista is not None and nivel_vista.Id == nivel.Id:
                    return vista
            except Exception:
                continue
    except Exception:
        pass
    return None


def _coordenadas_ya_definidas(doc):
    """True si el proyecto ya tiene coordenadas compartidas distintas de las internas."""
    try:
        posicion = doc.ActiveProjectLocation.GetProjectPosition(DB.XYZ.Zero)
        return any(abs(float(v)) > 1e-6 for v in (posicion.EastWest, posicion.NorthSouth, posicion.Elevation, posicion.Angle))
    except Exception:
        return False


def _extension(puntos_mm):
    xs = [p["x"] for p in puntos_mm]
    ys = [p["y"] for p in puntos_mm]
    zs = [p["z"] for p in puntos_mm]
    return {"x": [round(min(xs), 1), round(max(xs), 1)], "y": [round(min(ys), 1), round(max(ys), 1)],
            "z": [round(min(zs), 1), round(max(zs), 1)]}


# ---------------------------------------------------------------------------
# Rutas
# ---------------------------------------------------------------------------
def register_macros_routes(api):
    """Register the project macro routes with the API."""

    @api.route("/grid_levels/", methods=["POST"])
    @requiere_token
    def create_grid_and_levels(doc, request):
        """Rejilla completa (nombres correlativos) y niveles en una transaccion. Acepta `simular`."""

        def cuerpo(ctx):
            data = ctx["data"]
            plan = planificar_rejilla_y_niveles(doc, data)
            comprobar_alcance(data, plan["counts"]["total"], "rejillas y niveles a crear")
            haria = (
                [{"accion": "crear", "element_type": "grid", "name": g["name"], "start_mm": g["start_mm"], "end_mm": g["end_mm"]}
                 for g in plan["grids_x"] + plan["grids_y"]]
                + [{"accion": "crear", "element_type": "level", "name": n["name"], "elevation_mm": n["elevation_mm"]}
                   for n in plan["levels"]]
            )
            if ctx["simular"]:
                return simulacion(haria, plan=plan, count=plan["counts"]["total"])

            ids_grids = []
            ids_levels = []
            errors = []
            with transaccion(doc, "Rejilla y niveles"):
                for g in plan["grids_x"] + plan["grids_y"]:
                    try:
                        grid, error_nombre = crear_rejilla(doc, xyz_desde_mm(g["start_mm"]), xyz_desde_mm(g["end_mm"]), g["name"])
                        if error_nombre:
                            errors.append(u"Grid '{}': could not set name: {}".format(g["name"], error_nombre))
                        ids_grids.append(get_element_id_value(grid))
                    except Exception as error:
                        errors.append(u"Grid '{}': {}".format(g["name"], error))
                for n in plan["levels"]:
                    try:
                        nivel = crear_nivel(doc, n["elevation_mm"], n["name"])
                        ids_levels.append(get_element_id_value(nivel))
                    except Exception as error:
                        errors.append(u"Level '{}': {}".format(n["name"], error))

            resultado = resultado_creacion(doc, ids_grids + ids_levels, errors, {"plan": plan})
            grids = []
            levels = []
            for creado in resultado["creados"]:
                try:
                    elem = doc.GetElement(make_element_id(creado["id"]))
                    creado["name"] = get_element_name(elem)
                    if creado["id"] in ids_levels:
                        creado["elevation_mm"] = round(elevacion_interna(elem) * FEET_TO_MM, 1)
                        creado["elevation_shown_mm"] = round(elevacion_mostrada(elem) * FEET_TO_MM, 1)
                        levels.append(creado)
                    else:
                        grids.append(creado)
                except Exception:
                    (levels if creado["id"] in ids_levels else grids).append(creado)
            resultado["grids"] = grids
            resultado["levels"] = levels
            resultado["message"] = "Created {} grid(s) and {} level(s)".format(len(grids), len(levels))
            return resultado

        return ejecutar(doc, "/grid_levels/", request, cuerpo)

    @api.route("/sheet_set/", methods=["POST"])
    @requiere_token
    def create_sheet_set(doc, request):
        """Planos con sus vistas colocadas (Viewport.CanAddViewToSheet antes de Viewport.Create). Acepta `simular`."""

        def cuerpo(ctx):
            data = ctx["data"]
            planes = planificar_planos(doc, data)
            total = len(planes) + sum(len(p["views"]) for p in planes)
            comprobar_alcance(data, total, "planos y vistas a colocar")
            resumen = [_resumen_plano(p) for p in planes]
            if ctx["simular"]:
                return simulacion(
                    [dict(accion="crear", element_type="sheet", **r) for r in resumen],
                    plan={"sheets": resumen, "counts": {"sheets": len(planes), "views": total - len(planes),
                                                        "skipped": sum(len(p["skipped"]) for p in planes)}},
                    count=total,
                )

            ids = []
            errors = []
            detalle = []
            with transaccion(doc, "Crear {} planos".format(len(planes))):
                for plan in planes:
                    entrada = {"number": plan["number"], "name": plan["name"], "title_block": plan["title_block"],
                               "id": None, "views_placed": [], "skipped": list(plan["skipped"])}
                    detalle.append(entrada)
                    try:
                        plano = crear_plano(doc, plan["cajetin"], plan["number"], plan["name"])
                    except Exception as error:
                        errors.append(u"Sheet {}: {}".format(plan["number"] or plan["idx"], error))
                        continue
                    entrada["id"] = get_element_id_value(plano)
                    entrada["number"] = _texto_seguro(plano.SheetNumber)
                    entrada["name"] = get_element_name(plano)
                    ids.append(entrada["id"])
                    centro = None
                    for vista in plan["views"]:
                        punto = vista["punto"]
                        if punto is None:
                            if centro is None:
                                centro = _centro_cajetin(doc, plano)
                            punto = centro
                        try:
                            if vista["is_schedule"]:
                                colocada = DB.ScheduleSheetInstance.Create(doc, plano.Id, vista["vista"].Id, punto)
                            else:
                                if not DB.Viewport.CanAddViewToSheet(doc, plano.Id, vista["vista"].Id):
                                    entrada["skipped"].append({
                                        "view": vista["view"], "view_id": vista["view_id"],
                                        "reason": u"Viewport.CanAddViewToSheet devolvio False (ya colocada o no se puede colocar en un plano)",
                                    })
                                    continue
                                colocada = DB.Viewport.Create(doc, plano.Id, vista["vista"].Id, punto)
                            identificador = get_element_id_value(colocada)
                            ids.append(identificador)
                            entrada["views_placed"].append({
                                "view": vista["view"], "view_id": vista["view_id"], "viewport_id": identificador,
                                "position_mm": {"x": round(punto.X * FEET_TO_MM, 1), "y": round(punto.Y * FEET_TO_MM, 1)},
                            })
                        except Exception as error:
                            errors.append(u"Sheet {}: view '{}': {}".format(entrada["number"], vista["view"], error))

            resultado = resultado_creacion(doc, ids, errors, {"sheets": detalle, "plan": {"sheets": resumen}})
            colocadas = sum(len(e["views_placed"]) for e in detalle)
            saltadas = sum(len(e["skipped"]) for e in detalle)
            resultado["message"] = "Created {} sheet(s) with {} view(s) placed, {} skipped".format(
                len([e for e in detalle if e["id"] is not None]), colocadas, saltadas)
            return resultado

        return ejecutar(doc, "/sheet_set/", request, cuerpo)

    @api.route("/import_civil/", methods=["POST"])
    @requiere_token
    def import_from_civil(doc, request):
        """LandXML o CSV -> toposolido; DWG -> vinculo y, si se pide, coordenadas compartidas. Acepta `simular`."""

        def cuerpo(ctx):
            data = ctx["data"]
            file_path = data.get("file_path")
            if not file_path:
                raise EscrituraRechazada("file_path is required (LandXML .xml, CSV PNEZD or DWG)", 400)
            if not os.path.isfile(file_path):
                raise EscrituraRechazada("File not found: {}".format(file_path), 404)
            level_name = _texto_seguro(data.get("level") or data.get("level_name")).strip()
            if not level_name:
                raise EscrituraRechazada("level is required", 400)
            niveles = mapa_niveles(doc)
            nivel = niveles.get(level_name)
            if nivel is None:
                raise EscrituraRechazada(
                    u"Level '{}' not found".format(level_name), 404, {"available_levels": sorted(niveles.keys())},
                )
            extension = os.path.splitext(file_path)[1].lower()
            nombre_archivo = os.path.basename(file_path)
            desfase = _desfase(data)
            use_shared = bool(data.get("use_shared_coordinates", False))
            comprobar_alcance(data, 1, "elementos a crear")

            if extension in EXTENSIONES_LANDXML or extension in EXTENSIONES_CSV:
                comprobar_toposolido_disponible()
                unidades = data.get("units")
                try:
                    if extension in EXTENSIONES_LANDXML:
                        puntos_mm, formato, avisos = leer_landxml(file_path, unidades or None)
                    else:
                        puntos_mm, formato, avisos = leer_csv_puntos(file_path, (unidades or "m").lower())
                except ValueError as error:
                    raise EscrituraRechazada(str(error), 400)
                if desfase is not None:
                    dx, dy, dz = desfase.X * FEET_TO_MM, desfase.Y * FEET_TO_MM, desfase.Z * FEET_TO_MM
                    puntos_mm = [{"x": p["x"] + dx, "y": p["y"] + dy, "z": p["z"] + dz} for p in puntos_mm]
                if len(puntos_mm) < 3:
                    raise EscrituraRechazada("At least 3 points are needed for a toposolid; got {}".format(len(puntos_mm)), 400)
                tipo = tipo_toposolido(doc, data.get("type_name"))
                try:
                    puntos = [xyz_desde_mm(p) for p in puntos_mm]
                except ValueError as error:
                    raise EscrituraRechazada(str(error), 400)
                resumen = {
                    "accion": "crear", "element_type": "toposolid", "source": file_path, "format": formato,
                    "points": len(puntos), "level": level_name, "type": get_element_name(tipo),
                    "extent_mm": _extension(puntos_mm),
                    "origin_offset_mm": punto_a_mm(desfase) if desfase is not None else None,
                }
                if ctx["simular"]:
                    return simulacion([resumen], plan={"steps": ["leer {}".format(formato), "crear toposolido"],
                                                       "counts": {"points": len(puntos), "elements": 1}},
                                      warnings=avisos)
                with transaccion(doc, u"Importar topografia {}".format(nombre_archivo)):
                    topo = crear_toposolido(doc, puntos, tipo, nivel)
                    topo_id = get_element_id_value(topo)
                resultado = resultado_creacion(doc, [topo_id])
                resultado["toposolid_id"] = topo_id
                resultado["source"] = resumen
                resultado["warnings"] = avisos
                resultado["message"] = "Created toposolid {} from {} points of {}".format(topo_id, len(puntos), nombre_archivo)
                return resultado

            if extension not in EXTENSIONES_CAD:
                raise EscrituraRechazada(
                    "Unsupported file type '{}'. Use LandXML (.xml), CSV (P,N,E,Z / X,Y,Z) or DWG/DXF/DGN".format(extension), 400,
                )
            placement = _texto_seguro(data.get("placement") or ("center" if use_shared else "origin")).strip().lower()
            if placement not in PLACEMENTS:
                raise EscrituraRechazada(
                    "placement must be one of {}".format(", ".join(sorted(PLACEMENTS.keys()))), 400,
                )
            vista = _vista_planta_de(doc, nivel)
            if vista is None:
                try:
                    vista = doc.ActiveView
                except Exception:
                    vista = None
            if vista is None:
                raise EscrituraRechazada(
                    u"No floor plan view for level '{}' and no active view to place the link".format(level_name), 400,
                )
            antes = ubicacion_proyecto(doc) if use_shared else None
            if use_shared and not es_forzado(data):
                # Mismas protecciones que set_project_location: adquirir coordenadas
                # mueve los puntos base; 409 si alguno esta fijado o recortado.
                comprobar_puntos_base_libres(puntos_base(doc))
            if use_shared and _coordenadas_ya_definidas(doc) and not es_forzado(data):
                raise EscrituraRechazada(
                    "The project already has shared coordinates (project position is not zero); acquiring them "
                    "from the DWG would overwrite them. Pass forzar=true to do it anyway.", 409,
                    {"shared_coordinates_set": True, "antes": antes},
                )
            resumen = {
                "accion": "vincular", "element_type": "cad_link", "file_name": nombre_archivo, "file_path": file_path,
                "placement": placement, "view": get_element_name(vista), "level": level_name,
                "acquire_coordinates": use_shared,
                "origin_offset_mm": punto_a_mm(desfase) if desfase is not None else None,
            }
            haria = [resumen]
            if use_shared:
                haria.append({"accion": "adquirir_coordenadas", "desde": nombre_archivo, "antes": antes})
            if ctx["simular"]:
                return simulacion(haria, plan={"steps": [h["accion"] for h in haria], "counts": {"elements": 1}})
            with transaccion(doc, u"Importar civil {}".format(nombre_archivo)):
                link_id = vincular_cad(doc, file_path, "link", vista, placement)
                if link_id is None:
                    raise EscrituraRechazada("Revit did not return the id of the DWG link", 500)
                if desfase is not None:
                    DB.ElementTransformUtils.MoveElement(doc, make_element_id(link_id), desfase)
                if use_shared:
                    # El ImportInstance se acaba de crear (y mover) en esta misma
                    # transaccion: regenerar para que su posicion este resuelta.
                    try:
                        doc.Regenerate()
                    except Exception:
                        pass
                    doc.AcquireCoordinates(make_element_id(link_id))
            resultado = resultado_creacion(doc, [link_id])
            resultado["link_id"] = link_id
            resultado["source"] = resumen
            if use_shared:
                resultado["coordinates"] = {"antes": antes, "despues": ubicacion_proyecto(doc)}
            resultado["message"] = "Linked {} in view '{}'{}".format(
                nombre_archivo, get_element_name(vista), " and acquired its coordinates" if use_shared else "")
            return resultado

        return ejecutar(doc, "/import_civil/", request, cuerpo)

    logger.info("Macros routes registered successfully")
