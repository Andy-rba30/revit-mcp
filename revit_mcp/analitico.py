# -*- coding: UTF-8 -*-
"""
Modelo analitico para Revit MCP (0.5.0, entrega 2b, bloque 3).

  POST /analytical_status/   element_ids[] (vacio = todo el acero, max 500), tolerance_mm (10)
                             -> por elemento: AnalyticalMember asociado, nodos extremos en mm, miembros
                                conectados en cada extremo (extremo a menos de tolerance_mm) o que pasan
                                por el nodo (`touching`), is_connected y loose_nodes[]; resumen `members`,
                                `sin_analitico`, `loose_nodes_total`. Sin transaccion.
  POST /fix_analytical/      element_ids*, tolerance_mm (50), simular, forzar
                             -> LOTE: cada nodo suelto con un nodo ajeno a menos de la tolerancia se mueve
                                a ese nodo con AnalyticalMember.SetCurve, una transaccion "IA: Alinear
                                analitico"; antes/despues por nodo, fallidos[]; con simular, los movimientos.
  POST /export_structural/   format ("ifc_structural" | "csv_nodes_members"), file_path*
                             -> IFC: la exportacion IFC de 0.4.x (interop.opciones_ifc / exportar_ifc) con
                                ExportBaseQuantities y la vista analitica activa (o `view_name`) como filtro.
                                CSV: una fila por miembro con id, tipo, perfil, material, nodo i, nodo j
                                (mm) y liberaciones. Sin transaccion salvo la de la exportacion IFC.

Las cuatro versiones de Revit (2024-2027) tienen AnalyticalMember y
AnalyticalToPhysicalAssociationManager (2023+); no hay ruta para el
AnalyticalModel antiguo. Compatibilidad: IronPython 2.7.
"""

from utils import get_element_name, get_element_id_value, make_element_id, punto_a_mm, nombre_familia, MM_TO_FEET, FEET_TO_MM
from seguridad import requiere_token
from escritura import (
    ejecutar, transaccion, simulacion, EscrituraRechazada, comprobar_alcance, describir_elemento,
    nombre_nivel, nombre_categoria, nombre_tipo,
)
from navegacion import _responder, _entero
from acero import (
    elementos_acero, gestor_asociaciones, miembro_analitico, liberaciones_analiticas, material_de,
    bloque_estructural, LIBERACIONES, MAX_ELEMENTOS_ANALITICO, _texto_seguro, _es_invalido, _tipo_de,
)
from interop import opciones_ifc, exportar_ifc, buscar_vista_por_nombre
from pyrevit import routes, revit, DB
import io
import os
import logging

logger = logging.getLogger(__name__)

try:
    _texto = unicode  # IronPython 2.7
    _cadena = basestring
except NameError:  # pragma: no cover - CPython 3 en las pruebas
    _texto = str
    _cadena = str

TOLERANCIA_ESTADO_MM = 10.0
TOLERANCIA_ALINEAR_MM = 50.0
TOLERANCIA_COINCIDENTE_MM = 1.0       # dos nodos a menos de 1 mm ya estan unidos
FORMATOS_EXPORTACION = ("ifc_structural", "csv_nodes_members")
COLUMNAS_CSV = (
    "element_id", "analytical_member_id", "categoria", "familia", "tipo", "material", "nivel",
    "xi_mm", "yi_mm", "zi_mm", "xj_mm", "yj_mm", "zj_mm", "length_mm",
    "start_release", "start_released", "end_release", "end_released",
)
NO_SOPORTADO_ANALITICO = (
    u"La API de este Revit no expone AnalyticalToPhysicalAssociationManager / AnalyticalMember (Revit 2023+); "
    u"el modelo analitico no se puede leer desde el conector."
)


# ---------------------------------------------------------------------------
# Indice de miembros analiticos
# ---------------------------------------------------------------------------
def _extremos(miembro):
    """(p0, p1) de GetCurve() del miembro analitico, o None."""
    try:
        curva = miembro.GetCurve()
        if curva is None:
            return None
        return curva.GetEndPoint(0), curva.GetEndPoint(1)
    except Exception:
        return None


def indice_miembros(doc):
    """[{id, miembro, p0, p1}] de todos los AnalyticalMember del documento (los sin curva se saltan)."""
    clase = getattr(getattr(DB, "Structure", None), "AnalyticalMember", None)
    if clase is None:
        return []
    lista = []
    try:
        coleccion = DB.FilteredElementCollector(doc).OfClass(clase).WhereElementIsNotElementType().ToElements()
    except Exception as error:
        logger.debug("No se pudieron leer los miembros analiticos: %s", error)
        return []
    for miembro in coleccion:
        extremos = _extremos(miembro)
        if extremos is None:
            continue
        lista.append({"id": get_element_id_value(miembro), "miembro": miembro, "p0": extremos[0], "p1": extremos[1]})
    return lista


def _tolerancia(data, clave, por_defecto):
    valor = data.get(clave, por_defecto)
    if valor is None:
        return por_defecto
    try:
        if isinstance(valor, bool):
            raise ValueError("bool")
        numero = float(valor)
    except (TypeError, ValueError):
        raise EscrituraRechazada("{} must be a number of millimetres".format(clave), 400)
    if numero <= 0:
        raise EscrituraRechazada("{} must be greater than 0".format(clave), 400)
    return numero


def _distancia_curva(entrada, punto):
    try:
        return entrada["miembro"].GetCurve().Distance(punto)
    except Exception:
        return None


def _vecinos_del_nodo(punto, propio_id, indice, tolerancia_ft):
    """(conectados, tocando): miembros ajenos con un extremo a menos de la tolerancia, o cuya curva pasa por el nodo."""
    conectados = []
    tocando = []
    for entrada in indice:
        if entrada["id"] == propio_id:
            continue
        if min(entrada["p0"].DistanceTo(punto), entrada["p1"].DistanceTo(punto)) <= tolerancia_ft:
            conectados.append(entrada["id"])
            continue
        distancia = _distancia_curva(entrada, punto)
        if distancia is not None and distancia <= tolerancia_ft:
            tocando.append(entrada["id"])
    return conectados, tocando


def _fisico_de(gestor, doc, analitico_id):
    """Id del elemento fisico asociado a un miembro analitico, o None."""
    try:
        eid = gestor.GetAssociatedElementId(make_element_id(analitico_id))
    except Exception:
        return None
    if _es_invalido(eid):
        return None
    return get_element_id_value(eid)


# ---------------------------------------------------------------------------
# /analytical_status/
# ---------------------------------------------------------------------------
def estado_analitico(doc, data):
    """Cuerpo de POST /analytical_status/."""
    gestor = gestor_asociaciones(doc)
    if gestor is None:
        raise EscrituraRechazada(NO_SOPORTADO_ANALITICO, 409, {"no_soportado": True})
    tolerancia_mm = _tolerancia(data, "tolerance_mm", TOLERANCIA_ESTADO_MM)
    tolerancia_ft = tolerancia_mm * MM_TO_FEET
    element_ids = data.get("element_ids") or []
    if not isinstance(element_ids, (list, tuple)):
        raise EscrituraRechazada("element_ids must be a list", 400)
    maximo = _entero(data.get("max"), MAX_ELEMENTOS_ANALITICO, minimo=1, maximo=MAX_ELEMENTOS_ANALITICO)
    elementos, info = elementos_acero(doc, element_ids, maximo)
    indice = indice_miembros(doc)
    por_id = dict((e["id"], e) for e in indice)
    resultados = []
    miembros = 0
    sin_analitico = 0
    sueltos_total = 0
    conectados_total = 0
    for elem in elementos:
        element_id = get_element_id_value(elem)
        miembro = miembro_analitico(doc, elem, gestor)
        entrada = {"element_id": element_id, "categoria": nombre_categoria(elem), "tipo": nombre_tipo(doc, elem),
                   "analytical_member_id": None, "nodes": None, "is_connected": None, "loose_nodes": []}
        if miembro is None:
            sin_analitico += 1
            entrada["nota"] = u"sin modelo analitico asociado"
            resultados.append(entrada)
            continue
        miembros += 1
        analitico_id = get_element_id_value(miembro)
        entrada["analytical_member_id"] = analitico_id
        try:
            entrada["analytical_class"] = _texto_seguro(miembro.GetType().Name)
        except Exception:
            entrada["analytical_class"] = type(miembro).__name__
        datos_indice = por_id.get(analitico_id)
        extremos = (datos_indice["p0"], datos_indice["p1"]) if datos_indice is not None else _extremos(miembro)
        if extremos is None:
            entrada["nota"] = u"el elemento analitico no tiene curva (panel u otro tipo)"
            resultados.append(entrada)
            continue
        nodos = {}
        sueltos = []
        for nombre, punto in (("start", extremos[0]), ("end", extremos[1])):
            conectados, tocando = _vecinos_del_nodo(punto, analitico_id, indice, tolerancia_ft)
            conectado = bool(conectados or tocando)
            nodos[nombre] = {"point_mm": punto_a_mm(punto), "connected": conectados, "touching": tocando,
                             "is_connected": conectado}
            if not conectado:
                sueltos.append(nombre)
        entrada["nodes"] = nodos
        entrada["loose_nodes"] = sueltos
        entrada["is_connected"] = not sueltos
        entrada["releases"] = {"start": liberaciones_analiticas(miembro, True), "end": liberaciones_analiticas(miembro, False)}
        sueltos_total += len(sueltos)
        if not sueltos:
            conectados_total += 1
        resultados.append(entrada)
    return {
        "elements": resultados,
        "count": len(resultados),
        "members": miembros,
        "connected_members": conectados_total,
        "sin_analitico": sin_analitico,
        "loose_nodes_total": sueltos_total,
        "tolerance_mm": tolerancia_mm,
        "analytical_members_in_model": len(indice),
        "not_found": info["not_found"],
        "not_steel": info["not_steel"],
        "truncated": info["truncated"],
        "max": maximo,
        "metodo": (u"AnalyticalToPhysicalAssociationManager.GetAssociatedElementId -> AnalyticalMember.GetCurve(); un nodo esta "
                   u"conectado si otro miembro tiene un extremo a menos de tolerance_mm (`connected`) o su curva pasa por el "
                   u"nodo (`touching`)"),
    }


# ---------------------------------------------------------------------------
# /fix_analytical/
# ---------------------------------------------------------------------------
def planificar_alineacion(doc, data):
    """Movimientos de nodos sueltos hacia el nodo ajeno mas cercano dentro de la tolerancia."""
    gestor = gestor_asociaciones(doc)
    if gestor is None:
        raise EscrituraRechazada(NO_SOPORTADO_ANALITICO, 409, {"no_soportado": True})
    element_ids = data.get("element_ids")
    if isinstance(element_ids, (int, float)) and not isinstance(element_ids, bool):
        element_ids = [element_ids]
    if not isinstance(element_ids, (list, tuple)) or not element_ids:
        raise EscrituraRechazada("element_ids is required and must not be empty", 400)
    tolerancia_mm = _tolerancia(data, "tolerance_mm", TOLERANCIA_ALINEAR_MM)
    tolerancia_ft = tolerancia_mm * MM_TO_FEET
    coincidente_ft = TOLERANCIA_COINCIDENTE_MM * MM_TO_FEET
    indice = indice_miembros(doc)
    movimientos = []
    sin_analitico = []
    sin_objetivo = []
    ya_unidos = 0
    for identificador in element_ids:
        try:
            elem = doc.GetElement(make_element_id(identificador))
        except ValueError as error:
            raise EscrituraRechazada(_texto_seguro(error), 400)
        if elem is None:
            raise EscrituraRechazada("Element {} not found".format(identificador), 404)
        element_id = get_element_id_value(elem)
        miembro = miembro_analitico(doc, elem, gestor)
        if miembro is None:
            sin_analitico.append(element_id)
            continue
        analitico_id = get_element_id_value(miembro)
        extremos = _extremos(miembro)
        if extremos is None:
            sin_analitico.append(element_id)
            continue
        nuevos = [extremos[0], extremos[1]]
        for posicion, (nombre, punto) in enumerate((("start", extremos[0]), ("end", extremos[1]))):
            mejor = None
            for entrada in indice:
                if entrada["id"] == analitico_id:
                    continue
                for candidato in (entrada["p0"], entrada["p1"]):
                    distancia = punto.DistanceTo(candidato)
                    if mejor is None or distancia < mejor[0]:
                        mejor = (distancia, candidato, entrada["id"])
            if mejor is None or mejor[0] > tolerancia_ft:
                sin_objetivo.append({"element_id": element_id, "analytical_member_id": analitico_id, "end": nombre,
                                     "point_mm": punto_a_mm(punto),
                                     "nearest_mm": round(mejor[0] * FEET_TO_MM, 1) if mejor is not None else None})
                continue
            if mejor[0] <= coincidente_ft:
                ya_unidos += 1
                continue
            nuevos[posicion] = mejor[1]
            movimientos.append({
                "element_id": element_id, "analytical_member_id": analitico_id, "end": nombre,
                "from_mm": punto_a_mm(punto), "to_mm": punto_a_mm(mejor[1]), "distance_mm": round(mejor[0] * FEET_TO_MM, 1),
                "target_analytical_id": mejor[2], "target_element_id": _fisico_de(gestor, doc, mejor[2]),
                "miembro": miembro, "posicion": posicion, "nuevos": nuevos,
            })
    return {"movimientos": movimientos, "sin_analitico": sin_analitico, "sin_objetivo": sin_objetivo,
            "ya_unidos": ya_unidos, "tolerance_mm": tolerancia_mm}


def _publico(movimiento):
    return dict((k, v) for k, v in movimiento.items() if k not in ("miembro", "posicion", "nuevos"))


def alinear_nodos(doc, movimientos):
    """SetCurve por miembro con sus extremos movidos (dentro de una transaccion). Devuelve fallidos."""
    fallidos = []
    por_miembro = {}
    orden = []
    for movimiento in movimientos:
        clave = movimiento["analytical_member_id"]
        if clave not in por_miembro:
            por_miembro[clave] = movimiento
            orden.append(clave)
    for clave in orden:
        movimiento = por_miembro[clave]
        nuevos = movimiento["nuevos"]
        try:
            movimiento["miembro"].SetCurve(DB.Line.CreateBound(nuevos[0], nuevos[1]))
        except Exception as error:
            for otro in movimientos:
                if otro["analytical_member_id"] == clave:
                    fallidos.append({"element_id": otro["element_id"], "analytical_member_id": clave, "end": otro["end"],
                                     "motivo": u"SetCurve: {}".format(error)})
    return fallidos


# ---------------------------------------------------------------------------
# /export_structural/
# ---------------------------------------------------------------------------
def _es_vista_analitica(vista):
    try:
        return not bool(vista.AreAnalyticalModelCategoriesHidden)
    except Exception:
        return False


def vista_analitica_activa(doc, view_name=None):
    """(vista, motivo): la vista pedida, o la activa si muestra el modelo analitico; (None, motivo) si no."""
    if view_name:
        vista = buscar_vista_por_nombre(doc, view_name)
        if vista is None:
            raise EscrituraRechazada(u"View '{}' not found".format(view_name), 404)
        return vista, u"view_name"
    try:
        vista = doc.ActiveView
    except Exception:
        vista = None
    if vista is None:
        return None, u"sin vista activa: se exporta todo el modelo"
    if _es_vista_analitica(vista):
        return vista, u"vista activa con el modelo analitico visible"
    return None, u"la vista activa no muestra el modelo analitico (AreAnalyticalModelCategoriesHidden): se exporta todo el modelo"


def _texto_csv(valor):
    if valor is None:
        return u""
    texto = _texto_seguro(valor)
    if any(c in texto for c in (u",", u'"', u"\n")):
        return u'"{}"'.format(texto.replace(u'"', u'""'))
    return texto


def _liberacion_csv(liberacion):
    if not liberacion:
        return u"", u""
    activas = [c for c in LIBERACIONES if liberacion.get(c)]
    return liberacion.get("type") or u"", u"+".join(activas)


def filas_csv(doc, elementos):
    """(filas, sin_analitico): una fila (lista de textos) por miembro; los elementos sin miembro van aparte."""
    gestor = gestor_asociaciones(doc)
    filas = []
    sin_analitico = []
    for elem in elementos:
        element_id = get_element_id_value(elem)
        miembro = miembro_analitico(doc, elem, gestor) if gestor is not None else None
        extremos = _extremos(miembro) if miembro is not None else None
        if miembro is None or extremos is None:
            sin_analitico.append(element_id)
        tipo = _tipo_de(doc, elem)
        material = material_de(doc, elem)
        liberaciones = bloque_estructural(doc, elem).get("releases") or {}
        inicio_tipo, inicio_libres = _liberacion_csv(liberaciones.get("start"))
        fin_tipo, fin_libres = _liberacion_csv(liberaciones.get("end"))
        pi = punto_a_mm(extremos[0]) if extremos else {"x": None, "y": None, "z": None}
        pj = punto_a_mm(extremos[1]) if extremos else {"x": None, "y": None, "z": None}
        longitud = round(extremos[0].DistanceTo(extremos[1]) * FEET_TO_MM, 1) if extremos else None
        filas.append([
            element_id, get_element_id_value(miembro) if miembro is not None else None,
            nombre_categoria(elem), nombre_familia(tipo) if tipo is not None else None, nombre_tipo(doc, elem),
            get_element_name(material) if material is not None else None, nombre_nivel(doc, elem),
            pi["x"], pi["y"], pi["z"], pj["x"], pj["y"], pj["z"], longitud,
            inicio_tipo, inicio_libres, fin_tipo, fin_libres,
        ])
    return filas, sin_analitico


def escribir_csv(ruta, filas):
    carpeta = os.path.dirname(ruta)
    if carpeta and not os.path.isdir(carpeta):
        os.makedirs(carpeta)
    with io.open(ruta, "w", encoding="utf-8") as archivo:
        archivo.write(u",".join(COLUMNAS_CSV) + u"\n")
        for fila in filas:
            archivo.write(u",".join(_texto_csv(v) for v in fila) + u"\n")


def exportar_estructural(doc, data):
    """Cuerpo de POST /export_structural/ (IFC dentro de su transaccion; CSV sin transaccion)."""
    formato = _texto_seguro(data.get("format")).strip().lower()
    if formato not in FORMATOS_EXPORTACION:
        raise EscrituraRechazada("format must be one of {}".format(", ".join(FORMATOS_EXPORTACION)), 400,
                                 {"available_formats": list(FORMATOS_EXPORTACION)})
    ruta = _texto_seguro(data.get("file_path")).strip()
    if not ruta:
        raise EscrituraRechazada("file_path is required", 400)
    if formato == "ifc_structural":
        if not ruta.lower().endswith(".ifc"):
            raise EscrituraRechazada("file_path must end in .ifc", 400)
        vista, motivo = vista_analitica_activa(doc, _texto_seguro(data.get("view_name")).strip() or None)
        version = _texto_seguro(data.get("ifc_version") or "IFC2x3").strip()
        opciones = opciones_ifc(version, True, vista.Id if vista is not None else None)
        carpeta = os.path.dirname(ruta)
        if carpeta and not os.path.isdir(carpeta):
            try:
                os.makedirs(carpeta)
            except Exception as error:
                raise EscrituraRechazada("Cannot create output directory: {}".format(error), 500)
        with transaccion(doc, u"Exportar IFC estructural"):
            tamano = exportar_ifc(doc, ruta, opciones)
        return {
            "format": formato, "file_path": ruta, "file_size_kb": tamano, "ifc_version": version,
            "export_base_quantities": True,
            "filter_view": {"id": get_element_id_value(vista), "name": get_element_name(vista)} if vista is not None else None,
            "filter_view_reason": motivo,
            "message": u"Exported IFC to '{}' ({} KB)".format(ruta, tamano),
        }
    if not ruta.lower().endswith(".csv"):
        raise EscrituraRechazada("file_path must end in .csv", 400)
    element_ids = data.get("element_ids") or []
    if not isinstance(element_ids, (list, tuple)):
        raise EscrituraRechazada("element_ids must be a list", 400)
    maximo = _entero(data.get("max"), MAX_ELEMENTOS_ANALITICO, minimo=1)
    elementos, info = elementos_acero(doc, element_ids, maximo)
    filas, sin_analitico = filas_csv(doc, elementos)
    try:
        escribir_csv(ruta, filas)
    except (IOError, OSError) as error:
        raise EscrituraRechazada(u"Could not write {}: {}".format(ruta, error), 500)
    return {
        "format": formato, "file_path": ruta, "rows": len(filas), "columns": list(COLUMNAS_CSV),
        "sin_analitico": sin_analitico, "not_found": info["not_found"], "truncated": info["truncated"],
        "message": u"Wrote {} member row(s) to '{}'".format(len(filas), ruta),
    }


# ---------------------------------------------------------------------------
# Rutas
# ---------------------------------------------------------------------------
def register_analitico_routes(api):
    """Register the analytical-model routes (0.5.0) with the API."""

    @api.route("/analytical_status/", methods=["POST"])
    @requiere_token
    def analytical_status(doc, request):
        """Miembro analitico, nodos extremos, conectados y sueltos por elemento (sin transaccion)."""

        def cuerpo(data):
            datos = estado_analitico(doc, data)
            datos["status"] = "success"
            return datos

        return _responder(doc, request, cuerpo)

    @api.route("/fix_analytical/", methods=["POST"])
    @requiere_token
    def fix_analytical_alignment(doc, request):
        """LOTE: mueve los nodos sueltos al nodo ajeno mas cercano dentro de tolerance_mm (SetCurve). Acepta `simular`."""

        def cuerpo(ctx):
            data = ctx["data"]
            plan = planificar_alineacion(doc, data)
            movimientos = plan["movimientos"]
            comprobar_alcance(data, len(movimientos), "nodos a mover")
            haria = [dict(_publico(m), accion="mover_nodo") for m in movimientos]
            resumen = {"counts": {"moves": len(movimientos), "members": len(set(m["analytical_member_id"] for m in movimientos)),
                                  "already_joined": plan["ya_unidos"], "without_target": len(plan["sin_objetivo"]),
                                  "without_analytical": len(plan["sin_analitico"])},
                       "tolerance_mm": plan["tolerance_mm"]}
            if ctx["simular"]:
                return simulacion(haria, plan=resumen, count=len(haria), sin_objetivo=plan["sin_objetivo"],
                                  sin_analitico=plan["sin_analitico"])
            if not movimientos:
                return {"count": 0, "moves": [], "antes": {}, "despues": {}, "fallidos": [], "plan": resumen,
                        "sin_objetivo": plan["sin_objetivo"], "sin_analitico": plan["sin_analitico"], "ok": True,
                        "verificacion": {"coincide": True}, "message": u"Nothing to align within {} mm".format(plan["tolerance_mm"])}
            with transaccion(doc, u"Alinear analitico"):
                fallidos = alinear_nodos(doc, movimientos)
            fallados = set((f["analytical_member_id"]) for f in fallidos)
            antes = {}
            despues = {}
            desajustes = []
            movidos = []
            for movimiento in movimientos:
                if movimiento["analytical_member_id"] in fallados:
                    continue
                extremos = _extremos(movimiento["miembro"])
                leido = punto_a_mm(extremos[movimiento["posicion"]]) if extremos else None
                clave = u"{}.{}".format(movimiento["element_id"], movimiento["end"])
                antes[clave] = movimiento["from_mm"]
                despues[clave] = leido
                coincide = leido is not None and all(abs(leido[e] - movimiento["to_mm"][e]) <= 0.5 for e in ("x", "y", "z"))
                publico = dict(_publico(movimiento), coincide=coincide, despues_mm=leido)
                movidos.append(publico)
                if not coincide:
                    desajustes.append(u"{}: node {} read back at {} instead of {}".format(
                        movimiento["element_id"], movimiento["end"], leido, movimiento["to_mm"]))
            return {
                "count": len(set(m["analytical_member_id"] for m in movidos)), "moves": movidos, "antes": antes, "despues": despues,
                "fallidos": fallidos, "plan": resumen, "sin_objetivo": plan["sin_objetivo"], "sin_analitico": plan["sin_analitico"],
                "ok": not desajustes,
                "verificacion": {"coincide": True} if not desajustes else {"coincide": False, "detalle": u"; ".join(desajustes)},
                "message": u"Moved {} node(s) of {} analytical member(s); {} failed".format(
                    len(movidos), len(set(m["analytical_member_id"] for m in movidos)), len(fallidos)),
            }

        return ejecutar(doc, "/fix_analytical/", request, cuerpo)

    @api.route("/export_structural/", methods=["POST"])
    @requiere_token
    def export_structural(doc, request):
        """IFC con cantidades base y la vista analitica activa, o CSV de nodos y miembros."""

        def cuerpo(data):
            datos = exportar_estructural(doc, data)
            datos["status"] = "success"
            return datos

        return _responder(doc, request, cuerpo)

    logger.info("Analitico routes registered successfully")
