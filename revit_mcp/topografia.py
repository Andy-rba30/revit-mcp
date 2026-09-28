# -*- coding: UTF-8 -*-
"""
Topografia Module for Revit MCP
Toposolidos (Revit 2024+) a partir de puntos o de un CSV de Civil 3D.

  POST /create_toposolid/  points[] {x, y, z} (mm) o csv_path, level_name*, type_name,
                           units ("m" por defecto para CSV; "mm" o "ft"), boundary[]

Formatos de CSV admitidos (se detecta el orden de columnas por la cabecera o
por el numero de columnas): "P,N,E,Z" (numero de punto, norte, este, cota) o
"X,Y,Z". Para P,N,E,Z se toma x = E (este), y = N (norte). Se asume que el CSV
esta en metros salvo que `units` diga otra cosa. Pasa por escritura.ejecutar.
"""

from utils import get_element_name, get_element_id_value, make_element_id, xyz_desde_mm, punto_a_mm, mapa_niveles, elementos_por_nombre
from seguridad import requiere_token
from escritura import ejecutar, transaccion, simulacion, EscrituraRechazada, resultado_creacion
from pyrevit import routes, revit, DB
from System.Collections.Generic import List
import io
import os
import logging

logger = logging.getLogger(__name__)

FACTOR_A_MM = {"mm": 1.0, "m": 1000.0, "ft": 304.8, "cm": 10.0}
MAX_PUNTOS = 50000

_ALIAS = {
    "p": "p", "point": "p", "punto": "p", "id": "p", "n": "n", "north": "n", "northing": "n", "norte": "n",
    "e": "e", "east": "e", "easting": "e", "este": "e", "z": "z", "elev": "z", "elevation": "z", "cota": "z",
    "x": "x", "y": "y", "desc": "d", "description": "d", "code": "d", "descripcion": "d",
}


def _separador(linea):
    for sep in (",", ";", "\t"):
        if sep in linea:
            return sep
    return " "


def _es_numero(texto):
    try:
        float(texto)
        return True
    except (TypeError, ValueError):
        return False


def leer_csv_puntos(ruta, unidades="m"):
    """Lee un CSV P,N,E,Z / X,Y,Z y devuelve (puntos_mm, formato_detectado, avisos)."""
    if unidades not in FACTOR_A_MM:
        raise ValueError("units must be one of {}".format(sorted(FACTOR_A_MM.keys())))
    factor = FACTOR_A_MM[unidades]
    with io.open(ruta, "r", encoding="utf-8-sig") as archivo:
        lineas = [l.strip() for l in archivo.readlines()]
    lineas = [l for l in lineas if l and not l.startswith("#")]
    if not lineas:
        raise ValueError("CSV is empty")
    sep = _separador(lineas[0])
    primera = [c.strip() for c in lineas[0].split(sep)]
    columnas = None
    inicio = 0
    if not all(_es_numero(c) for c in primera if c):
        # Cabecera: mapear por nombre
        columnas = []
        for c in primera:
            columnas.append(_ALIAS.get(c.strip().strip('"').lower(), None))
        inicio = 1
        if "x" in columnas and "y" in columnas and "z" in columnas:
            formato = "X,Y,Z (cabecera)"
        elif "n" in columnas and "e" in columnas and "z" in columnas:
            formato = "P,N,E,Z (cabecera)"
        else:
            raise ValueError("CSV header {} not recognised; expected P,N,E,Z or X,Y,Z".format(primera))
    else:
        n = len([c for c in primera if c])
        if n >= 4:
            columnas = ["p", "n", "e", "z"] + ["d"] * (n - 4)
            formato = "P,N,E,Z (sin cabecera, {} columnas)".format(n)
        elif n == 3:
            columnas = ["x", "y", "z"]
            formato = "X,Y,Z (sin cabecera)"
        else:
            raise ValueError("CSV rows need 3 (X,Y,Z) or 4+ (P,N,E,Z) columns; got {}".format(n))
    puntos = []
    avisos = []
    for numero, linea in enumerate(lineas[inicio:], inicio + 1):
        celdas = [c.strip().strip('"') for c in (linea.split() if sep == " " else linea.split(sep))]
        valores = {}
        for clave, celda in zip(columnas, celdas):
            if clave in ("x", "y", "z", "n", "e"):
                valores[clave] = celda
        try:
            if "x" in valores:
                x, y, z = float(valores["x"]), float(valores["y"]), float(valores["z"])
            else:
                x, y, z = float(valores["e"]), float(valores["n"]), float(valores["z"])
        except (KeyError, ValueError):
            avisos.append("line {}: skipped ({})".format(numero, linea[:60]))
            continue
        puntos.append({"x": x * factor, "y": y * factor, "z": z * factor})
        if len(puntos) > MAX_PUNTOS:
            raise ValueError("CSV has more than {} points; thin it out first".format(MAX_PUNTOS))
    if not puntos:
        raise ValueError("CSV contains no valid points")
    return puntos, formato, avisos


def comprobar_toposolido_disponible():
    """400 si esta version de Revit no tiene DB.Toposolid (necesita 2024+)."""
    if not hasattr(DB, "Toposolid"):
        raise EscrituraRechazada(
            "DB.Toposolid is not available in this Revit version (needs 2024+)", 400
        )


def tipo_toposolido(doc, type_name=None):
    """ToposolidType por nombre (o el primero). Lanza EscrituraRechazada(404).

    Lo reutiliza macros.import_from_civil."""
    tipos = elementos_por_nombre(DB.FilteredElementCollector(doc).OfClass(DB.ToposolidType).ToElements())
    if not tipos:
        raise EscrituraRechazada("No toposolid types in the project", 404)
    if type_name:
        tipo = tipos.get(type_name)
        if tipo is None:
            raise EscrituraRechazada(
                "Toposolid type '{}' not found".format(type_name), 404,
                {"available_types": sorted(tipos.keys())},
            )
        return tipo
    return list(tipos.values())[0]


def crear_toposolido(doc, puntos, tipo, nivel, contorno=None):
    """DB.Toposolid.Create con los XYZ (pies) y, si se da, el contorno cerrado (XYZ).

    Debe llamarse dentro de una transaccion. Lo reutiliza macros.import_from_civil."""
    lista = List[DB.XYZ]()
    for p in puntos:
        lista.Add(p)
    if contorno:
        loop = DB.CurveLoop()
        for i in range(len(contorno)):
            loop.Append(DB.Line.CreateBound(contorno[i], contorno[(i + 1) % len(contorno)]))
        loops = List[DB.CurveLoop]()
        loops.Add(loop)
        return DB.Toposolid.Create(doc, loops, lista, tipo.Id, nivel.Id)
    return DB.Toposolid.Create(doc, lista, tipo.Id, nivel.Id)


def planificar_toposolido(doc, data):
    """Valida {points | csv_path, level_name, type_name, units, boundary}. Lanza EscrituraRechazada.

    Devuelve el plan: puntos (XYZ), tipo, nivel, contorno, resumen (para `haria`)
    y avisos. Lo reutiliza lotes.create_elements (kind toposolid)."""
    comprobar_toposolido_disponible()
    level_name = data.get("level_name") or data.get("level")
    if not level_name:
        raise EscrituraRechazada("level_name is required", 400)
    level_map = mapa_niveles(doc)
    nivel = level_map.get(level_name)
    if nivel is None:
        raise EscrituraRechazada(
            "Level '{}' not found".format(level_name), 404,
            {"available_levels": sorted(level_map.keys())},
        )

    unidades = (data.get("units") or "m").lower()
    puntos_mm = data.get("points") or []
    csv_path = data.get("csv_path")
    formato = "points (mm)"
    avisos = []
    if csv_path:
        if not os.path.isfile(csv_path):
            raise EscrituraRechazada("CSV file not found: {}".format(csv_path), 404)
        try:
            puntos_mm, formato, avisos = leer_csv_puntos(csv_path, unidades)
        except ValueError as error:
            raise EscrituraRechazada(str(error), 400)
    if not puntos_mm or len(puntos_mm) < 3:
        raise EscrituraRechazada("At least 3 points are required (points[] in mm or csv_path)", 400)
    if len(puntos_mm) > MAX_PUNTOS:
        raise EscrituraRechazada("Too many points ({}); max {}".format(len(puntos_mm), MAX_PUNTOS), 400)

    try:
        puntos = [xyz_desde_mm(p) for p in puntos_mm]
    except ValueError as error:
        raise EscrituraRechazada(str(error), 400)

    tipo = tipo_toposolido(doc, data.get("type_name"))

    boundary = data.get("boundary") or []
    contorno = None
    if boundary:
        try:
            esquinas = [xyz_desde_mm(p) for p in boundary]
        except ValueError as error:
            raise EscrituraRechazada("boundary: {}".format(error), 400)
        if len(esquinas) < 3:
            raise EscrituraRechazada("boundary needs at least 3 points", 400)
        if esquinas[0].DistanceTo(esquinas[-1]) < 0.001:
            esquinas = esquinas[:-1]
        contorno = esquinas

    xs = [p["x"] for p in puntos_mm]
    ys = [p["y"] for p in puntos_mm]
    zs = [p["z"] for p in puntos_mm]
    resumen = {
        "accion": "crear", "element_type": "toposolid", "type": get_element_name(tipo),
        "level": level_name, "points": len(puntos), "source": csv_path or "points",
        "format": formato, "units_assumed": unidades if csv_path else "mm",
        "extent_mm": {"x": [min(xs), max(xs)], "y": [min(ys), max(ys)], "z": [min(zs), max(zs)]},
        "boundary_points": len(contorno) if contorno else 0,
    }
    return {"kind": "toposolid", "puntos": puntos, "tipo": tipo, "nivel": nivel, "contorno": contorno,
            "haria": resumen, "warnings": avisos}


def register_topografia_routes(api):
    """Register toposolid routes with the API."""

    @api.route("/create_toposolid/", methods=["POST"])
    @requiere_token
    def create_toposolid(doc, request):
        """Toposolido (DB.Toposolid.Create, Revit 2024+). Acepta `simular`."""

        def cuerpo(ctx):
            data = ctx["data"]
            plan = planificar_toposolido(doc, data)
            resumen = plan["haria"]
            if ctx["simular"]:
                return simulacion([resumen], warnings=plan["warnings"])

            with transaccion(doc, "Crear toposolido {}".format(get_element_name(plan["tipo"]))):
                topo = crear_toposolido(doc, plan["puntos"], plan["tipo"], plan["nivel"], plan["contorno"])
                topo_id = get_element_id_value(topo)

            resultado = resultado_creacion(doc, [topo_id])
            resultado["toposolid_id"] = topo_id
            resultado["source"] = resumen
            resultado["warnings"] = plan["warnings"]
            resultado["message"] = "Created toposolid {} from {} points".format(topo_id, len(plan["puntos"]))
            return resultado

        return ejecutar(doc, "/create_toposolid/", request, cuerpo)

    logger.info("Topografia routes registered successfully")
