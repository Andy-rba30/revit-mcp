# -*- coding: UTF-8 -*-
"""
Edicion del documento de familia (0.6.0, entrega 2c): parametros, planos de
referencia, cotas con etiqueta, solidos y vaciados, bloqueos, tipos y conectores.

Todas las rutas son LOTES: reciben una lista, validan todo antes de abrir la
transaccion (400/404 con `index`), aplican en UNA transaccion del documento de
familia (`IA: ...`) y responden `creados[]` o `antes`/`despues`. Con `simular`
devuelven `haria[]` sin tocar nada. Las funciones `planificar_*` / `aplicar_*`
las reutiliza familias_spec.build_family_from_spec.

Unidades: milimetros y grados hacia fuera; pies y radianes dentro.
Compatibilidad: IronPython 2.7.
"""

from utils import get_element_name, get_element_id_value, make_element_id, punto_a_mm, MM_TO_FEET, FEET_TO_MM
from seguridad import requiere_token
from escritura import ejecutar_familia, transaccion, simulacion, EscrituraRechazada, comprobar_alcance, describir_elemento
from pyrevit import DB
import familias as F
import math
import logging

logger = logging.getLogger(__name__)

try:
    _texto = unicode  # IronPython 2.7
    _cadena = basestring
except NameError:  # pragma: no cover - CPython 3 en las pruebas
    _texto = str
    _cadena = str

_t = F._texto_seguro


def _resolver(doc):
    return lambda data: F.resolver_documento(doc, data)


def _indice(etiqueta, i, mensaje):
    return u"{}[{}]: {}".format(etiqueta, i, mensaje)


def _rechazo(error, etiqueta, i):
    """Reetiqueta un EscrituraRechazada con el indice del lote."""
    raise EscrituraRechazada(_indice(etiqueta, i, error.mensaje), error.status, dict(error.extra, index=i))


# ---------------------------------------------------------------------------
# Parametros
# ---------------------------------------------------------------------------
def _definicion_compartida(doc_familia, guid):
    """ExternalDefinition del archivo de parametros compartidos activo con ese GUID."""
    app = F.aplicacion(doc_familia)
    try:
        archivo = app.OpenSharedParameterFile()
    except Exception as error:
        raise EscrituraRechazada(u"OpenSharedParameterFile failed: {}".format(error), 500)
    if archivo is None:
        raise EscrituraRechazada(u"No shared parameter file is set in Revit (Application.OpenSharedParameterFile is null)", 409,
                                 {"no_soportado": True, "motivo": "sin_archivo_compartido"})
    objetivo = _t(guid).strip().lower().strip("{}")
    disponibles = []
    for grupo in archivo.Groups:
        for definicion in grupo.Definitions:
            try:
                propio = _t(definicion.GUID).strip().lower().strip("{}")
            except Exception:
                continue
            disponibles.append({"name": _t(definicion.Name), "guid": propio, "group": _t(grupo.Name)})
            if propio == objetivo:
                return definicion
    raise EscrituraRechazada(u"Shared parameter with GUID {} not found in the shared parameter file".format(guid), 404,
                             {"available_shared_parameters": disponibles[:60]})


def _nombres_formula(formula):
    """Nombres candidatos a parametro dentro de una formula (lo que no es numero, unidad ni operador)."""
    import re

    trozos = re.split(r"[+\-*/()<>=,^]|\band\b|\bor\b|\bif\b|\bthen\b|\belse\b|\bnot\b", formula)
    nombres = []
    for trozo in trozos:
        candidato = trozo.strip().strip('"')
        if not candidato:
            continue
        if re.match(r"^[0-9.]+\s*(mm|cm|m|ft|in|\"|'|°|deg|%)?$", candidato):
            continue
        if candidato.lower() in ("true", "false", "pi", "abs", "sqrt", "exp", "log", "round", "roundup", "rounddown",
                                 "sin", "cos", "tan", "asin", "acos", "atan", "max", "min"):
            continue
        nombres.append(candidato)
    return nombres


def planificar_parametros(doc_familia, lista, etiqueta="parameters", definidos_extra=()):
    """Valida la lista de parametros a crear. Devuelve [plan] con spec/grupo resueltos."""
    if not lista:
        raise EscrituraRechazada(u"{} is required (list of {{name, data_type, group, ...}})".format(etiqueta), 400)
    existentes = set(_t(p.Definition.Name) for p in F._parametros_familia(doc_familia))
    conocidos = set(existentes) | set(definidos_extra)
    planes = []
    for i, item in enumerate(lista):
        if not isinstance(item, dict):
            raise EscrituraRechazada(_indice(etiqueta, i, u"must be an object"), 400, {"index": i})
        nombre = _t(item.get("name")).strip()
        if not nombre:
            raise EscrituraRechazada(_indice(etiqueta, i, u"name is required"), 400, {"index": i})
        if nombre in existentes:
            raise EscrituraRechazada(_indice(etiqueta, i, u"parameter '{}' already exists in the family".format(nombre)), 409,
                                     {"index": i, "existing": sorted(existentes)})
        if any(p["name"] == nombre for p in planes):
            raise EscrituraRechazada(_indice(etiqueta, i, u"parameter '{}' is repeated in the list".format(nombre)), 400, {"index": i})
        plan = {"index": i, "name": nombre, "is_instance": F._es_verdadero(item.get("is_instance")),
                "formula": _t(item.get("formula")).strip() or None, "shared_guid": None, "spec": None,
                "category": None, "categoria_bic": None}
        guid = item.get("shared_parameter_guid")
        if guid:
            try:
                plan["definicion"] = _definicion_compartida(doc_familia, guid)
            except EscrituraRechazada as error:
                _rechazo(error, etiqueta, i)
            plan["shared_guid"] = _t(guid)
            plan["data_type"] = None
        else:
            try:
                clave, categoria = F.normalizar_tipo_dato(item.get("data_type"))
            except EscrituraRechazada as error:
                _rechazo(error, etiqueta, i)
            plan["data_type"] = clave
            if clave == "family_type":
                try:
                    plan["category"], plan["categoria_bic"] = F._categoria_familia(doc_familia, categoria)
                except EscrituraRechazada as error:
                    _rechazo(error, etiqueta, i)
                plan["data_type"] = u"family_type:{}".format(categoria)
            else:
                plan["spec"] = F._spec(clave)
                if plan["spec"] is None:
                    raise EscrituraRechazada(_indice(etiqueta, i, u"SpecTypeId for '{}' is not available in this Revit".format(clave)),
                                             409, {"index": i, "no_soportado": True})
        try:
            plan["grupo"], plan["group"] = F.resolver_grupo(item.get("group"))
        except EscrituraRechazada as error:
            _rechazo(error, etiqueta, i)
        if plan["formula"]:
            desconocidos = [n for n in _nombres_formula(plan["formula"]) if n not in conocidos and n != nombre]
            if desconocidos:
                raise EscrituraRechazada(
                    _indice(etiqueta, i, u"formula uses undefined parameters: {}".format(u", ".join(desconocidos))), 400,
                    {"index": i, "undefined": desconocidos, "defined": sorted(conocidos)})
        conocidos.add(nombre)
        planes.append(plan)
    return planes


def haria_parametro(plan):
    return {"accion": "crear_parametro", "index": plan["index"], "name": plan["name"], "data_type": plan["data_type"],
            "group": plan["group"], "is_instance": plan["is_instance"], "formula": plan["formula"],
            "shared_parameter_guid": plan["shared_guid"]}


def aplicar_parametros(doc_familia, planes):
    """Dentro de una transaccion: AddParameter (+ SetFormula). Devuelve [FamilyParameter]."""
    gestor = doc_familia.FamilyManager
    creados = []
    for plan in planes:
        try:
            if plan.get("definicion") is not None:
                param = gestor.AddParameter(plan["definicion"], plan["grupo"], plan["is_instance"])
            elif plan["category"] is not None:
                param = gestor.AddParameter(plan["name"], plan["grupo"], plan["category"], plan["is_instance"])
            else:
                param = gestor.AddParameter(plan["name"], plan["grupo"], plan["spec"], plan["is_instance"])
        except Exception as error:
            raise EscrituraRechazada(u"AddParameter('{}') failed: {}".format(plan["name"], error), 500, {"index": plan["index"]})
        creados.append(param)
    # las formulas despues de crear todos, porque pueden referirse a los del mismo lote; Revit
    # exige un tipo actual para SetFormula ("There is no valid family type")
    if any(plan["formula"] for plan in planes):
        try:
            nombre = get_element_name(doc_familia.OwnerFamily)
        except Exception:
            nombre = None
        F.asegurar_tipo(doc_familia, nombre if nombre and nombre != "Unnamed" else None)
    for plan, param in zip(planes, creados):
        if plan["formula"]:
            try:
                gestor.SetFormula(param, plan["formula"])
            except Exception as error:
                raise EscrituraRechazada(u"SetFormula('{}', '{}') failed: {}".format(plan["name"], plan["formula"], error), 500,
                                         {"index": plan["index"]})
    return creados


# ---------------------------------------------------------------------------
# Planos de referencia
# ---------------------------------------------------------------------------
def _es_referencia(valor):
    """Indice de ELEM_REFERENCE_NAME a partir del nombre del contrato (o bool). None = no tocar."""
    if valor is None or valor == u"":
        return None, None
    if isinstance(valor, bool):
        clave = "strong" if valor else "not_a_reference"
    elif isinstance(valor, (int, float)) and not isinstance(valor, bool):
        indice = int(valor)
        for nombre, v in F.ES_REFERENCIA.items():
            if v == indice:
                return indice, nombre
        raise EscrituraRechazada(u"is_reference index {} out of range".format(indice), 400,
                                 {"available_is_reference": sorted(F.ES_REFERENCIA.keys())})
    else:
        clave = F._normalizar(valor).replace(" ", "_").replace("(", "").replace(")", "").replace("/", "_")
        clave = F._ALIAS_REFERENCIA.get(clave, clave)
    if clave not in F.ES_REFERENCIA:
        raise EscrituraRechazada(u"is_reference '{}' not supported".format(valor), 400,
                                 {"available_is_reference": sorted(F.ES_REFERENCIA.keys())})
    return F.ES_REFERENCIA[clave], clave


def planificar_planos(doc_familia, lista, etiqueta="planes"):
    if not lista:
        raise EscrituraRechazada(u"{} is required (list of {{name, origin_mm, direction, view, is_reference}})".format(etiqueta), 400)
    existentes = set(get_element_name(p) for p in F.planos_referencia(doc_familia))
    planes = []
    largo = F.LONGITUD_PLANO_MM * MM_TO_FEET / 2.0
    for i, item in enumerate(lista):
        if not isinstance(item, dict):
            raise EscrituraRechazada(_indice(etiqueta, i, u"must be an object"), 400, {"index": i})
        nombre = _t(item.get("name")).strip()
        if not nombre:
            raise EscrituraRechazada(_indice(etiqueta, i, u"name is required"), 400, {"index": i})
        if nombre in existentes or any(p["name"] == nombre for p in planes):
            raise EscrituraRechazada(_indice(etiqueta, i, u"reference plane '{}' already exists".format(nombre)), 409,
                                     {"index": i, "existing": sorted(existentes)})
        try:
            origen = F._xyz(item.get("origin_mm") or {"x": 0, "y": 0, "z": 0}, "origin_mm")
        except EscrituraRechazada as error:
            _rechazo(error, etiqueta, i)
        direccion = F._normalizar(item.get("direction") or "x")
        direccion = F._ALIAS_DIRECCION.get(direccion, direccion)
        if direccion not in F.DIRECCIONES_PLANO:
            raise EscrituraRechazada(_indice(etiqueta, i, u"direction must be x, y or horizontal"), 400, {"index": i})
        # linea y vector de corte: la normal del plano es dir x cut
        if direccion == "x":        # plano paralelo a X (normal Y): se dibuja en planta
            a, b, cut = DB.XYZ(origen.X - largo, origen.Y, origen.Z), DB.XYZ(origen.X + largo, origen.Y, origen.Z), DB.XYZ(0, 0, 1)
        elif direccion == "y":      # plano paralelo a Y (normal X): se dibuja en planta
            a, b, cut = DB.XYZ(origen.X, origen.Y - largo, origen.Z), DB.XYZ(origen.X, origen.Y + largo, origen.Z), DB.XYZ(0, 0, 1)
        else:                       # plano horizontal (normal Z): se dibuja en un alzado
            a, b, cut = DB.XYZ(origen.X - largo, origen.Y, origen.Z), DB.XYZ(origen.X + largo, origen.Y, origen.Z), DB.XYZ(0, 1, 0)
        normal = b.Subtract(a).Normalize().CrossProduct(cut).Normalize()
        try:
            vista = F._vista_para_normal(doc_familia, normal, item.get("view"))
        except EscrituraRechazada as error:
            _rechazo(error, etiqueta, i)
        try:
            indice_ref, nombre_ref = _es_referencia(item.get("is_reference"))
        except EscrituraRechazada as error:
            _rechazo(error, etiqueta, i)
        planes.append({"index": i, "name": nombre, "origin": origen, "direction": direccion, "bubble": a, "free": b,
                       "cut": cut, "normal": normal, "view": vista, "is_reference": indice_ref, "is_reference_name": nombre_ref})
    return planes


def haria_plano(plan):
    return {"accion": "crear_plano_referencia", "index": plan["index"], "name": plan["name"],
            "origin_mm": punto_a_mm(plan["origin"]), "direction": plan["direction"], "normal": F._vector_mm(plan["normal"]),
            "view": F.describir_vista(plan["view"]), "is_reference": plan["is_reference_name"]}


def aplicar_planos(doc_familia, planes):
    creados = []
    for plan in planes:
        try:
            ref_plane = doc_familia.FamilyCreate.NewReferencePlane(plan["bubble"], plan["free"], plan["cut"], plan["view"])
        except Exception as error:
            raise EscrituraRechazada(u"NewReferencePlane('{}') failed: {}".format(plan["name"], error), 500, {"index": plan["index"]})
        try:
            ref_plane.Name = plan["name"]
        except Exception as error:
            raise EscrituraRechazada(u"Could not name the reference plane '{}': {}".format(plan["name"], error), 500,
                                     {"index": plan["index"]})
        if plan["is_reference"] is not None:
            bip = getattr(DB.BuiltInParameter, "ELEM_REFERENCE_NAME", None)
            param = ref_plane.get_Parameter(bip) if bip is not None else None
            if param is None:
                plan["aviso"] = u"ELEM_REFERENCE_NAME not available: is_reference not set"
            else:
                try:
                    param.Set(int(plan["is_reference"]))
                except Exception as error:
                    plan["aviso"] = u"is_reference not set: {}".format(error)
        creados.append(ref_plane)
    return creados


# ---------------------------------------------------------------------------
# Cotas con etiqueta
# ---------------------------------------------------------------------------
def _referencia_plano(ref_plane):
    try:
        return ref_plane.GetReference()
    except Exception:
        return DB.Reference(ref_plane)


def planificar_cotas(doc_familia, lista, etiqueta="dimensions", parametros_extra=()):
    if not lista:
        raise EscrituraRechazada(u"{} is required (list of {{reference_planes[], parameter, view, equal}})".format(etiqueta), 400)
    planes = []
    for i, item in enumerate(lista):
        if not isinstance(item, dict):
            raise EscrituraRechazada(_indice(etiqueta, i, u"must be an object"), 400, {"index": i})
        nombres = item.get("reference_planes") or item.get("reference_plane_names") or []
        if isinstance(nombres, _cadena):
            nombres = [nombres]
        if not isinstance(nombres, (list, tuple)) or len(nombres) < 2:
            raise EscrituraRechazada(_indice(etiqueta, i, u"reference_planes needs at least two reference plane names"), 400, {"index": i})
        ref_planes = []
        for nombre in nombres:
            try:
                ref_planes.append(F.resolver_plano(doc_familia, nombre, "reference_planes"))
            except EscrituraRechazada as error:
                _rechazo(error, etiqueta, i)
        normal = F._normal_plano(ref_planes[0])
        if normal is None:
            raise EscrituraRechazada(_indice(etiqueta, i, u"reference plane '{}' has no plane".format(nombres[0])), 400, {"index": i})
        for ref_plane, nombre in zip(ref_planes[1:], nombres[1:]):
            otra = F._normal_plano(ref_plane)
            if otra is None or abs(abs(otra.DotProduct(normal)) - 1.0) > 0.01:
                raise EscrituraRechazada(_indice(etiqueta, i, u"reference planes must be parallel; '{}' is not parallel to '{}'".format(
                    nombre, nombres[0])), 400, {"index": i})
        igual = F._es_verdadero(item.get("equal"))
        if igual and len(ref_planes) < 3:
            raise EscrituraRechazada(_indice(etiqueta, i, u"equal=true needs at least three reference planes"), 400, {"index": i})
        nombre_param = _t(item.get("parameter") or item.get("parameter_name")).strip() or None
        param = None
        if nombre_param:
            param = F.parametro_familia(doc_familia, nombre_param)
            if param is None and nombre_param not in parametros_extra:
                raise EscrituraRechazada(_indice(etiqueta, i, u"parameter '{}' not found in the family".format(nombre_param)), 404,
                                         {"index": i, "available_parameters": [_t(p.Definition.Name) for p in F._parametros_familia(doc_familia)]})
        if not nombre_param and not igual:
            raise EscrituraRechazada(_indice(etiqueta, i, u"parameter (label) or equal=true is required"), 400, {"index": i})
        try:
            vista = F._vista_para_normal(doc_familia, normal, item.get("view"))
        except EscrituraRechazada as error:
            _rechazo(error, etiqueta, i)
        desfase = F._numero(item.get("offset_mm", F.DESFASE_COTA_MM), "offset_mm") * MM_TO_FEET
        # linea de cota: paralela a la normal, entre el primer y el ultimo plano, desplazada dentro de la vista
        origenes = [F._origen_plano(p) for p in ref_planes]
        base = origenes[0]
        posiciones = [o.Subtract(base).DotProduct(normal) for o in origenes]
        orden = sorted(range(len(ref_planes)), key=lambda k: posiciones[k])
        ref_planes = [ref_planes[k] for k in orden]
        nombres_ordenados = [nombres[k] for k in orden]
        minimo, maximo = min(posiciones), max(posiciones)
        direccion_vista = F._direccion_vista(vista)
        if direccion_vista is None:
            direccion_vista = DB.XYZ(0, 0, 1)
        lateral = direccion_vista.CrossProduct(normal)
        if lateral.GetLength() < 1e-6:
            lateral = DB.XYZ(0, 0, 1) if abs(normal.Z) < 0.9 else DB.XYZ(0, 1, 0)
        lateral = lateral.Normalize().Multiply(desfase)
        p0 = base.Add(normal.Multiply(minimo)).Add(lateral)
        p1 = base.Add(normal.Multiply(maximo)).Add(lateral)
        if p0.DistanceTo(p1) < 1e-6:
            raise EscrituraRechazada(_indice(etiqueta, i, u"the reference planes are coincident"), 400, {"index": i})
        planes.append({"index": i, "ref_planes": ref_planes, "names": nombres_ordenados, "parameter": nombre_param, "param": param,
                       "equal": igual, "view": vista, "line": DB.Line.CreateBound(p0, p1),
                       "length_mm": round((maximo - minimo) * FEET_TO_MM, 2)})
    return planes


def haria_cota(plan):
    return {"accion": "crear_cota", "index": plan["index"], "reference_planes": plan["names"], "parameter": plan["parameter"],
            "equal": plan["equal"], "view": F.describir_vista(plan["view"]), "length_mm": plan["length_mm"],
            "line_mm": {"start": punto_a_mm(plan["line"].GetEndPoint(0)), "end": punto_a_mm(plan["line"].GetEndPoint(1))}}


def aplicar_cotas(doc_familia, planes):
    creadas = []
    for plan in planes:
        referencias = DB.ReferenceArray()
        for ref_plane in plan["ref_planes"]:
            referencias.Append(_referencia_plano(ref_plane))
        try:
            cota = doc_familia.FamilyCreate.NewDimension(plan["view"], plan["line"], referencias)
        except Exception as error:
            raise EscrituraRechazada(u"NewDimension({}) failed: {}".format(plan["names"], error), 500, {"index": plan["index"]})
        param = plan["param"] or (F.parametro_familia(doc_familia, plan["parameter"]) if plan["parameter"] else None)
        if plan["parameter"]:
            if param is None:
                raise EscrituraRechazada(u"parameter '{}' not found when labelling".format(plan["parameter"]), 404, {"index": plan["index"]})
            try:
                cota.FamilyLabel = param
            except Exception as error:
                raise EscrituraRechazada(u"FamilyLabel = '{}' failed: {}".format(plan["parameter"], error), 500, {"index": plan["index"]})
        if plan["equal"]:
            try:
                cota.AreSegmentsEqual = True
            except Exception as error:
                raise EscrituraRechazada(u"AreSegmentsEqual failed: {}".format(error), 500, {"index": plan["index"]})
        creadas.append(cota)
    return creadas


# ---------------------------------------------------------------------------
# Planos de boceto, perfiles y solidos
# ---------------------------------------------------------------------------
def _plano_boceto(doc_familia, pedido, etiqueta="sketch_plane"):
    """(SketchPlane, Plane-like (Origin, Normal), descripcion) desde un nombre de plano de referencia,
    {"reference_plane": nombre} o {"view_type": "FloorPlan", "level": ...} (plano del nivel)."""
    if pedido is None or pedido == u"":
        pedido = {"view_type": "FloorPlan"}
    if isinstance(pedido, _cadena):
        pedido = {"reference_plane": pedido}
    if not isinstance(pedido, dict):
        raise EscrituraRechazada(u"{} must be a reference plane name or {{\"view_type\"/\"level\"}}".format(etiqueta), 400)
    if pedido.get("reference_plane"):
        ref_plane = F.resolver_plano(doc_familia, pedido.get("reference_plane"), etiqueta)
        origen, normal = F._origen_plano(ref_plane), F._normal_plano(ref_plane)
        return {"kind": "reference_plane", "ref_plane": ref_plane, "origin": origen, "normal": normal,
                "name": get_element_name(ref_plane), "view": F._vista_para_normal(doc_familia, normal, pedido.get("view"))}
    nivel = F._nivel_familia(doc_familia, pedido.get("level"))
    if nivel is None:
        raise EscrituraRechazada(u"{}: the family document has no levels".format(etiqueta), 404)
    from utils import elevacion_interna

    z = elevacion_interna(nivel)
    vista = F.resolver_vista(doc_familia, dict(pedido, view_type=pedido.get("view_type") or "FloorPlan"), etiqueta)
    return {"kind": "level", "level": nivel, "origin": DB.XYZ(0, 0, z), "normal": DB.XYZ(0, 0, 1),
            "name": get_element_name(nivel), "view": vista}


def crear_plano_boceto(doc_familia, plano):
    """SketchPlane.Create dentro de una transaccion (por referencia del plano o por id del nivel)."""
    try:
        if plano["kind"] == "reference_plane":
            return DB.SketchPlane.Create(doc_familia, _referencia_plano(plano["ref_plane"]))
        return DB.SketchPlane.Create(doc_familia, plano["level"].Id)
    except Exception as error:
        try:
            geom = DB.Plane.CreateByNormalAndOrigin(plano["normal"], plano["origin"])
            return DB.SketchPlane.Create(doc_familia, geom)
        except Exception as error2:
            raise EscrituraRechazada(u"SketchPlane.Create failed on '{}': {} / {}".format(plano["name"], error, error2), 500)


def _en_plano(punto, plano):
    """Proyecta el punto sobre el plano (Origin, Normal)."""
    d = punto.Subtract(plano["origin"]).DotProduct(plano["normal"])
    return punto.Subtract(plano["normal"].Multiply(d))


def _ejes_plano(plano):
    normal = plano["normal"]
    aux = DB.XYZ(0, 0, 1) if abs(normal.Z) < 0.9 else DB.XYZ(1, 0, 0)
    x = aux.CrossProduct(normal).Normalize()
    y = normal.CrossProduct(x).Normalize()
    return x, y


def _lazo(definicion, plano, etiqueta):
    """CurveArray cerrado a partir de una lista de puntos {x,y,z} mm, {"circle": {...}} o {"rect": {...}}."""
    lazo = DB.CurveArray()
    if isinstance(definicion, dict) and definicion.get("circle"):
        circulo = definicion["circle"]
        centro = _en_plano(F._xyz(circulo.get("center_mm") or circulo.get("center") or {"x": 0, "y": 0, "z": 0}, etiqueta + ".circle.center_mm"), plano)
        radio = F._numero(circulo.get("radius_mm", circulo.get("radius")), etiqueta + ".circle.radius_mm") * MM_TO_FEET
        if radio <= 0:
            raise EscrituraRechazada(u"{}: circle radius must be positive".format(etiqueta), 400)
        x, y = _ejes_plano(plano)
        try:
            lazo.Append(DB.Arc.Create(centro, radio, 0.0, math.pi, x, y))
            lazo.Append(DB.Arc.Create(centro, radio, math.pi, 2 * math.pi, x, y))
        except Exception as error:
            raise EscrituraRechazada(u"{}: Arc.Create failed: {}".format(etiqueta, error), 500)
        return lazo, {"circle": {"center_mm": punto_a_mm(centro), "radius_mm": round(radio * FEET_TO_MM, 2)}}
    if isinstance(definicion, dict) and definicion.get("rect"):
        rect = definicion["rect"]
        a = F._xyz(rect.get("min_mm") or rect.get("min"), etiqueta + ".rect.min_mm")
        b = F._xyz(rect.get("max_mm") or rect.get("max"), etiqueta + ".rect.max_mm")
        x, y = _ejes_plano(plano)
        base = _en_plano(a, plano)
        u = b.Subtract(a).DotProduct(x)
        v = b.Subtract(a).DotProduct(y)
        if abs(u) < 1e-9 or abs(v) < 1e-9:
            raise EscrituraRechazada(u"{}: rect has no area on the sketch plane".format(etiqueta), 400)
        puntos = [base, base.Add(x.Multiply(u)), base.Add(x.Multiply(u)).Add(y.Multiply(v)), base.Add(y.Multiply(v))]
    else:
        if isinstance(definicion, dict) and "points" in definicion:
            definicion = definicion["points"]
        if not isinstance(definicion, (list, tuple)) or len(definicion) < 3:
            raise EscrituraRechazada(u"{}: a loop needs at least 3 points (or circle / rect)".format(etiqueta), 400)
        puntos = []
        for k, p in enumerate(definicion):
            if isinstance(p, (list, tuple)):
                p = {"x": p[0], "y": p[1], "z": p[2] if len(p) > 2 else None}
                if p["z"] is None:
                    p["z"] = 0
            puntos.append(_en_plano(F._xyz(p, u"{}[{}]".format(etiqueta, k)), plano))
        if puntos[0].DistanceTo(puntos[-1]) < 1e-9:
            puntos = puntos[:-1]
        if len(puntos) < 3:
            raise EscrituraRechazada(u"{}: a loop needs at least 3 distinct points".format(etiqueta), 400)
    for k in range(len(puntos)):
        a, b = puntos[k], puntos[(k + 1) % len(puntos)]
        if a.DistanceTo(b) < 1e-9:
            raise EscrituraRechazada(u"{}: points {} and {} coincide".format(etiqueta, k, (k + 1) % len(puntos)), 400)
        lazo.Append(DB.Line.CreateBound(a, b))
    return lazo, {"points_mm": [punto_a_mm(p) for p in puntos]}


def _perfil(definicion, plano, etiqueta):
    """CurveArrArray con uno o varios lazos. `definicion` = lazo, o lista de lazos."""
    lazos = definicion
    if isinstance(definicion, dict):
        lazos = definicion.get("loops") if definicion.get("loops") else [definicion]
    elif isinstance(definicion, (list, tuple)) and definicion and isinstance(definicion[0], (list, tuple)) and \
            definicion[0] and isinstance(definicion[0][0], (dict, list, tuple)):
        lazos = definicion          # lista de lazos
    else:
        lazos = [definicion]        # un lazo de puntos
    if not lazos:
        raise EscrituraRechazada(u"{}: profile is required".format(etiqueta), 400)
    perfil = DB.CurveArrArray()
    descripcion = []
    for k, lazo in enumerate(lazos):
        curvas, desc = _lazo(lazo, plano, u"{}[{}]".format(etiqueta, k))
        perfil.Append(curvas)
        descripcion.append(desc)
    return perfil, descripcion


def _cara_de_forma(doc_familia, forma, cara_pedida, vista=None):
    """(PlanarFace, Reference) de la forma cuya normal es la de `cara_pedida` (top, bottom... o {x,y,z})."""
    direccion = F._vector(cara_pedida, "face")
    candidatas = []
    for solido in F._solidos_de(forma, vista, con_referencias=True):
        try:
            caras = list(solido.Faces)
        except Exception:
            continue
        for cara in caras:
            try:
                normal = cara.FaceNormal
            except Exception:
                try:
                    normal = cara.ComputeNormal(DB.UV(0.5, 0.5))
                except Exception:
                    continue
            referencia = getattr(cara, "Reference", None)
            if referencia is None:
                continue
            alineacion = normal.DotProduct(direccion)
            if alineacion > 0.95:
                try:
                    area = float(cara.Area)
                except Exception:
                    area = 0.0
                try:
                    avance = cara.Origin.DotProduct(direccion)
                except Exception:
                    avance = 0.0
                candidatas.append((avance, area, cara, referencia))
    if not candidatas:
        raise EscrituraRechazada(u"No '{}' face with a reference on solid {} (ComputeReferences in view {})".format(
            cara_pedida, get_element_id_value(forma), F.describir_vista(vista)["id"] if vista is not None else None), 400)
    candidatas.sort(key=lambda c: (-c[0], -c[1]))
    return candidatas[0][2], candidatas[0][3]


def planificar_solidos(doc_familia, lista, etiqueta="solids", parametros_extra=()):
    if not lista:
        raise EscrituraRechazada(u"{} is required (list of {{kind, profile, sketch_plane, ...}})".format(etiqueta), 400)
    planes = []
    for i, item in enumerate(lista):
        if not isinstance(item, dict):
            raise EscrituraRechazada(_indice(etiqueta, i, u"must be an object"), 400, {"index": i})
        kind = F._normalizar(item.get("kind") or "extrusion")
        kind = F._ALIAS_KIND.get(kind, kind)
        if kind not in F.KINDS_SOLIDO:
            raise EscrituraRechazada(_indice(etiqueta, i, u"kind must be extrusion, sweep, revolution or blend"), 400, {"index": i})
        plan = {"index": i, "kind": kind, "name": _t(item.get("name")).strip() or u"{} {}".format(kind, i),
                "is_void": F._es_verdadero(item.get("is_void")), "material_parameter": None, "lock_ends": None, "lock_faces": []}
        try:
            plan["plane"] = _plano_boceto(doc_familia, item.get("sketch_plane"), "sketch_plane")
        except EscrituraRechazada as error:
            _rechazo(error, etiqueta, i)
        plano = plan["plane"]
        try:
            if kind == "extrusion":
                plan["start"] = F._numero(item.get("start_mm", 0), "start_mm") * MM_TO_FEET
                plan["end"] = F._numero(item.get("end_mm"), "end_mm") * MM_TO_FEET if item.get("end_mm") is not None else None
                if plan["end"] is None:
                    raise EscrituraRechazada(u"end_mm is required", 400)
                if abs(plan["end"] - plan["start"]) < 1e-9:
                    raise EscrituraRechazada(u"end_mm must differ from start_mm", 400)
                plan["profile"], plan["profile_desc"] = _perfil(item.get("profile"), plano, "profile")
            elif kind == "blend":
                plan["end"] = F._numero(item.get("end_mm", item.get("height_mm")), "end_mm") * MM_TO_FEET
                if plan["end"] <= 0:
                    raise EscrituraRechazada(u"end_mm (blend height) must be positive", 400)
                plan["base"], plan["base_desc"] = _lazo(item.get("base_profile") or item.get("profile"), plano, "base_profile")
                plan["top"], plan["top_desc"] = _lazo(item.get("top_profile"), plano, "top_profile")
            elif kind == "revolution":
                plan["profile"], plan["profile_desc"] = _perfil(item.get("profile"), plano, "profile")
                eje = item.get("axis") or {}
                a = _en_plano(F._xyz(eje.get("start_mm") or eje.get("start"), "axis.start_mm"), plano)
                b = _en_plano(F._xyz(eje.get("end_mm") or eje.get("end"), "axis.end_mm"), plano)
                if a.DistanceTo(b) < 1e-9:
                    raise EscrituraRechazada(u"axis start and end coincide", 400)
                plan["axis"] = DB.Line.CreateBound(a, b)
                plan["start_angle"] = math.radians(F._numero(item.get("start_angle_deg", 0), "start_angle_deg"))
                plan["end_angle"] = math.radians(F._numero(item.get("end_angle_deg", 360), "end_angle_deg"))
                if abs(plan["end_angle"] - plan["start_angle"]) < 1e-9:
                    raise EscrituraRechazada(u"end_angle_deg must differ from start_angle_deg", 400)
            else:  # sweep
                plan["path"], plan["path_desc"] = _camino(item.get("path"), plano, "path")
                # el perfil vive en el plano perpendicular a la trayectoria en su inicio
                plan["profile_plane"] = {"origin": plan["path"].GetEndPoint(0), "normal": plan["path"].direccion}
                plan["profile"], plan["profile_desc"] = _perfil(item.get("profile"), plan["profile_plane"], "profile")
        except EscrituraRechazada as error:
            _rechazo(error, etiqueta, i)
        material = _t(item.get("material_parameter")).strip()
        if material:
            if F.parametro_familia(doc_familia, material) is None and material not in parametros_extra:
                raise EscrituraRechazada(_indice(etiqueta, i, u"material_parameter '{}' is not a family parameter".format(material)), 404,
                                         {"index": i, "available_parameters": [_t(p.Definition.Name) for p in F._parametros_familia(doc_familia)]})
            plan["material_parameter"] = material
        bloqueos = item.get("lock_ends_to")
        if bloqueos:
            if isinstance(bloqueos, (list, tuple)):
                if len(bloqueos) != 2:
                    raise EscrituraRechazada(_indice(etiqueta, i, u"lock_ends_to must be [start_plane, end_plane] or {{start, end}}"), 400, {"index": i})
                bloqueos = {"start": bloqueos[0], "end": bloqueos[1]}
            if not isinstance(bloqueos, dict):
                raise EscrituraRechazada(_indice(etiqueta, i, u"lock_ends_to must be [start_plane, end_plane] or {{start, end}}"), 400, {"index": i})
            if kind != "extrusion":
                raise EscrituraRechazada(_indice(etiqueta, i, u"lock_ends_to only applies to extrusions"), 400, {"index": i})
            plan["lock_ends"] = {}
            for extremo in ("start", "end"):
                nombre = bloqueos.get(extremo)
                if not nombre:
                    continue
                try:
                    F.resolver_plano(doc_familia, nombre, "lock_ends_to." + extremo)
                except EscrituraRechazada as error:
                    _rechazo(error, etiqueta, i)
                plan["lock_ends"][extremo] = nombre
        for k, bloqueo in enumerate(item.get("lock_faces") or []):
            if not isinstance(bloqueo, dict) or not bloqueo.get("face") or not bloqueo.get("reference_plane"):
                raise EscrituraRechazada(_indice(etiqueta, i, u"lock_faces[{}] needs face and reference_plane".format(k)), 400, {"index": i})
            try:
                F._vector(bloqueo["face"], "lock_faces.face")
                F.resolver_plano(doc_familia, bloqueo["reference_plane"], "lock_faces.reference_plane")
            except EscrituraRechazada as error:
                _rechazo(error, etiqueta, i)
            plan["lock_faces"].append({"face": bloqueo["face"], "reference_plane": bloqueo["reference_plane"], "view": bloqueo.get("view")})
        planes.append(plan)
    return planes


def _camino(definicion, plano, etiqueta):
    """CurveArrArray con la trayectoria de un barrido (lista de puntos abierta) sobre el plano."""
    if isinstance(definicion, dict) and "points" in definicion:
        definicion = definicion["points"]
    if not isinstance(definicion, (list, tuple)) or len(definicion) < 2:
        raise EscrituraRechazada(u"{}: a path needs at least 2 points".format(etiqueta), 400)
    puntos = []
    for k, p in enumerate(definicion):
        if isinstance(p, (list, tuple)):
            p = {"x": p[0], "y": p[1], "z": p[2] if len(p) > 2 else 0}
        puntos.append(_en_plano(F._xyz(p, u"{}[{}]".format(etiqueta, k)), plano))
    curvas = DB.CurveArray()
    for k in range(len(puntos) - 1):
        if puntos[k].DistanceTo(puntos[k + 1]) < 1e-9:
            raise EscrituraRechazada(u"{}: points {} and {} coincide".format(etiqueta, k, k + 1), 400)
        curvas.Append(DB.Line.CreateBound(puntos[k], puntos[k + 1]))
    camino = DB.CurveArrArray()
    camino.Append(curvas)
    camino.GetEndPoint = lambda indice: puntos[0] if indice == 0 else puntos[-1]
    camino.direccion = puntos[1].Subtract(puntos[0]).Normalize()
    return camino, {"points_mm": [punto_a_mm(p) for p in puntos]}


def haria_solido(plan):
    datos = {"accion": ("crear_vaciado" if plan["is_void"] else "crear_solido"), "index": plan["index"], "kind": plan["kind"],
             "name": plan["name"], "sketch_plane": plan["plane"]["name"], "material_parameter": plan["material_parameter"],
             "lock_ends_to": plan["lock_ends"], "lock_faces": plan["lock_faces"]}
    if plan["kind"] == "extrusion":
        datos.update({"start_mm": round(plan["start"] * FEET_TO_MM, 2), "end_mm": round(plan["end"] * FEET_TO_MM, 2),
                      "profile": plan["profile_desc"]})
    elif plan["kind"] == "blend":
        datos.update({"end_mm": round(plan["end"] * FEET_TO_MM, 2), "base_profile": plan["base_desc"], "top_profile": plan["top_desc"]})
    elif plan["kind"] == "revolution":
        datos.update({"profile": plan["profile_desc"], "start_angle_deg": round(math.degrees(plan["start_angle"]), 2),
                      "end_angle_deg": round(math.degrees(plan["end_angle"]), 2),
                      "axis_mm": {"start": punto_a_mm(plan["axis"].GetEndPoint(0)), "end": punto_a_mm(plan["axis"].GetEndPoint(1))}})
    else:
        datos.update({"path": plan["path_desc"], "profile": plan["profile_desc"]})
    return datos


def _fijar_mm(elem, nombre_bip, valor_pies):
    bip = getattr(DB.BuiltInParameter, nombre_bip, None)
    if bip is None:
        return u"{} not available".format(nombre_bip)
    try:
        param = elem.get_Parameter(bip)
        if param is None:
            return u"{} not found on the solid".format(nombre_bip)
        param.Set(float(valor_pies))
    except Exception as error:
        return u"{}: {}".format(nombre_bip, error)
    return None


def _asociar_material(doc_familia, forma, nombre_param):
    param_familia = F.parametro_familia(doc_familia, nombre_param)
    if param_familia is None:
        return u"material parameter '{}' not found".format(nombre_param)
    bip = getattr(DB.BuiltInParameter, "MATERIAL_ID_PARAM", None)
    param = forma.get_Parameter(bip) if bip is not None else None
    if param is None:
        return u"MATERIAL_ID_PARAM not found on the solid"
    try:
        doc_familia.FamilyManager.AssociateElementParameterToFamilyParameter(param, param_familia)
    except Exception as error:
        return u"AssociateElementParameterToFamilyParameter failed: {}".format(error)
    return None


def _alinear(doc_familia, vista, referencia_cara, ref_plane):
    try:
        return doc_familia.FamilyCreate.NewAlignment(vista, referencia_cara, _referencia_plano(ref_plane))
    except Exception as error:
        raise EscrituraRechazada(u"NewAlignment to '{}' failed: {}".format(get_element_name(ref_plane), error), 500)


def _bloquear_extremos(doc_familia, forma, plan, avisos):
    """Bloquea las caras inicial y final de la extrusion a los planos pedidos (NewAlignment)."""
    normal = plan["plane"]["normal"]
    for extremo, nombre in (plan["lock_ends"] or {}).items():
        if not nombre:
            continue
        ref_plane = F.resolver_plano(doc_familia, nombre, "lock_ends_to." + extremo)
        direccion = normal.Multiply(-1.0) if extremo == "start" else normal
        if plan["end"] < plan["start"]:
            direccion = direccion.Multiply(-1.0)
        vista = F._vista_para_normal(doc_familia, normal)
        try:
            cara, referencia = _cara_de_forma(doc_familia, forma, F._vector_mm(direccion), vista)
        except EscrituraRechazada as error:
            avisos.append(u"{} ({}): {}".format(plan["name"], extremo, error.mensaje))
            continue
        _alinear(doc_familia, vista, referencia, ref_plane)


def _bloquear_caras(doc_familia, forma, bloqueos, avisos):
    creados = []
    for bloqueo in bloqueos:
        ref_plane = F.resolver_plano(doc_familia, bloqueo["reference_plane"], "lock_faces.reference_plane")
        normal = F._normal_plano(ref_plane)
        vista = F._vista_para_normal(doc_familia, normal, bloqueo.get("view"))
        cara, referencia = _cara_de_forma(doc_familia, forma, bloqueo["face"], vista)
        creados.append(_alinear(doc_familia, vista, referencia, ref_plane))
    return creados


def aplicar_solidos(doc_familia, planes, avisos):
    """Dentro de una transaccion. Devuelve [forma] (regenera entre solidos para poder bloquear caras)."""
    creados = []
    for plan in planes:
        boceto = crear_plano_boceto(doc_familia, plan["plane"])
        es_solido = not plan["is_void"]
        fabrica = doc_familia.FamilyCreate
        try:
            if plan["kind"] == "extrusion":
                forma = fabrica.NewExtrusion(es_solido, plan["profile"], boceto, plan["end"] - plan["start"])
                if abs(plan["start"]) > 1e-9:
                    for nombre_bip, valor in (("EXTRUSION_END_PARAM", plan["end"]), ("EXTRUSION_START_PARAM", plan["start"])):
                        motivo = _fijar_mm(forma, nombre_bip, valor)
                        if motivo:
                            avisos.append(u"{}: {}".format(plan["name"], motivo))
            elif plan["kind"] == "blend":
                forma = fabrica.NewBlend(es_solido, plan["top"], plan["base"], boceto)
                try:
                    forma.TopOffset = plan["end"]
                except Exception as error:
                    avisos.append(u"{}: TopOffset not set: {}".format(plan["name"], error))
            elif plan["kind"] == "revolution":
                forma = fabrica.NewRevolution(es_solido, plan["profile"], boceto, plan["axis"], plan["start_angle"], plan["end_angle"])
            else:
                perfil = F.aplicacion(doc_familia).Create.NewCurveLoopsProfile(plan["profile"])
                forma = fabrica.NewSweep(es_solido, plan["path"], boceto, perfil, 0, DB.ProfilePlaneLocation.Start)
        except EscrituraRechazada:
            raise
        except Exception as error:
            raise EscrituraRechazada(u"{} '{}' failed in Revit: {}".format(plan["kind"], plan["name"], error), 500, {"index": plan["index"]})
        if plan["material_parameter"]:
            motivo = _asociar_material(doc_familia, forma, plan["material_parameter"])
            if motivo:
                avisos.append(u"{}: {}".format(plan["name"], motivo))
        if plan["lock_ends"] or plan["lock_faces"]:
            try:
                doc_familia.Regenerate()
            except Exception as error:
                raise EscrituraRechazada(u"Regenerate after '{}' failed: {}".format(plan["name"], error), 500, {"index": plan["index"]})
            if plan["lock_ends"]:
                _bloquear_extremos(doc_familia, forma, plan, avisos)
            if plan["lock_faces"]:
                _bloquear_caras(doc_familia, forma, plan["lock_faces"], avisos)
        creados.append(forma)
    return creados


# ---------------------------------------------------------------------------
# Bloqueos sueltos, tipos y conectores
# ---------------------------------------------------------------------------
def _forma_por_id(doc_familia, valor, etiqueta="solid_id"):
    try:
        forma = doc_familia.GetElement(make_element_id(valor))
    except Exception:
        raise EscrituraRechazada(u"{} must be an integer id".format(etiqueta), 400)
    if forma is None or not isinstance(forma, DB.GenericForm):
        raise EscrituraRechazada(u"{}: solid {} not found in the family document".format(etiqueta, valor), 404,
                                 {"available_solids": [F.describir_forma(doc_familia, f)["id"] for f in F.formas(doc_familia)]})
    return forma


def planificar_bloqueos(doc_familia, lista, etiqueta="locks"):
    if not lista:
        raise EscrituraRechazada(u"{} is required (list of {{solid_id, face, reference_plane, view}})".format(etiqueta), 400)
    planes = []
    for i, item in enumerate(lista):
        if not isinstance(item, dict):
            raise EscrituraRechazada(_indice(etiqueta, i, u"must be an object"), 400, {"index": i})
        try:
            forma = _forma_por_id(doc_familia, item.get("solid_id"))
            F._vector(item.get("face") or "top", "face")
            ref_plane = F.resolver_plano(doc_familia, item.get("reference_plane") or item.get("reference_plane_name"), "reference_plane")
            vista = F._vista_para_normal(doc_familia, F._normal_plano(ref_plane), item.get("view"))
        except EscrituraRechazada as error:
            _rechazo(error, etiqueta, i)
        planes.append({"index": i, "solid": forma, "face": item.get("face") or "top", "ref_plane": ref_plane, "view": vista})
    return planes


def haria_bloqueo(plan):
    return {"accion": "bloquear_cara", "index": plan["index"], "solid_id": get_element_id_value(plan["solid"]), "face": plan["face"],
            "reference_plane": get_element_name(plan["ref_plane"]), "view": F.describir_vista(plan["view"])}


def _valor_para_tipo(doc_familia, param, valor):
    """Convierte el valor del contrato al que espera FamilyManager.Set. (valor, texto_visible)."""
    clave = F._nombre_spec(param)
    storage = _t(getattr(param, "StorageType", None)).split(".")[-1]
    if storage == "Double":
        numero = F._numero(valor, u"value of '{}'".format(_t(param.Definition.Name)))
        factor = F.FACTORES.get(clave)
        return (numero * factor if factor else numero), u"{} {}".format(valor, F.UNIDADES.get(clave, u"")).strip()
    if storage == "Integer":
        if isinstance(valor, bool) or clave == "yes_no":
            return (1 if F._es_verdadero(valor) else 0), F._es_verdadero(valor)
        return int(F._numero(valor, u"value of '{}'".format(_t(param.Definition.Name)))), int(float(valor))
    if storage == "String":
        return _t(valor), _t(valor)
    if storage == "ElementId":
        if isinstance(valor, (int, float)) and not isinstance(valor, bool):
            return make_element_id(int(valor)), int(valor)
        nombre = _t(valor).strip()
        clases = [DB.Material] if clave == "material" else [DB.FamilySymbol, DB.ElementType]
        for clase in clases:
            try:
                for elemento in DB.FilteredElementCollector(doc_familia).OfClass(clase).ToElements():
                    if get_element_name(elemento) == nombre:
                        return elemento.Id, nombre
            except Exception:
                continue
        raise EscrituraRechazada(u"'{}' not found for parameter '{}' ({})".format(nombre, _t(param.Definition.Name), clave or storage), 404)
    return valor, valor


def planificar_tipos(doc_familia, lista, etiqueta="types", parametros_extra=()):
    if not lista:
        raise EscrituraRechazada(u"{} is required (list of {{type_name, values, create_if_missing}})".format(etiqueta), 400)
    existentes = dict((_t(t.Name), t) for t in F.tipos_familia(doc_familia))
    planes = []
    for i, item in enumerate(lista):
        if not isinstance(item, dict):
            raise EscrituraRechazada(_indice(etiqueta, i, u"must be an object"), 400, {"index": i})
        nombre = _t(item.get("type_name") or item.get("name")).strip()
        if not nombre:
            raise EscrituraRechazada(_indice(etiqueta, i, u"type_name is required"), 400, {"index": i})
        crear = F._es_verdadero(item.get("create_if_missing"), True)
        tipo = existentes.get(nombre)
        if tipo is None and not crear:
            raise EscrituraRechazada(_indice(etiqueta, i, u"type '{}' does not exist (create_if_missing=false)".format(nombre)), 404,
                                     {"index": i, "available_types": sorted(existentes.keys())})
        valores = item.get("values") or {}
        if not isinstance(valores, dict):
            raise EscrituraRechazada(_indice(etiqueta, i, u"values must be an object {{parameter: value}}"), 400, {"index": i})
        cambios = []
        for nombre_param, valor in valores.items():
            param = F.parametro_familia(doc_familia, nombre_param)
            if param is None:
                if nombre_param in parametros_extra:
                    cambios.append({"parameter": nombre_param, "param": None, "value": valor, "internal": None, "display": valor})
                    continue
                raise EscrituraRechazada(_indice(etiqueta, i, u"parameter '{}' not found in the family".format(nombre_param)), 404,
                                         {"index": i, "available_parameters": [_t(p.Definition.Name) for p in F._parametros_familia(doc_familia)]})
            formula = _formula_de(param)
            if formula:
                raise EscrituraRechazada(_indice(etiqueta, i, u"parameter '{}' is determined by a formula".format(nombre_param)), 400,
                                         {"index": i, "formula": formula})
            try:
                interno, visible = _valor_para_tipo(doc_familia, param, valor)
            except EscrituraRechazada as error:
                _rechazo(error, etiqueta, i)
            cambios.append({"parameter": nombre_param, "param": param, "value": valor, "internal": interno, "display": visible})
        planes.append({"index": i, "type_name": nombre, "tipo": tipo, "create": tipo is None, "changes": cambios})
    return planes


def haria_tipo(plan):
    return {"accion": "crear_tipo" if plan["create"] else "fijar_tipo", "index": plan["index"], "type_name": plan["type_name"],
            "values": dict((c["parameter"], c["display"]) for c in plan["changes"])}


def _formula_de(param):
    """Formula del parametro o None. Revit 2027 (validacion 2c, 0.6.3) no la delato por IsDeterminedByFormula
    y FamilyManager.Set ignoro el valor sin lanzar: se mira tambien FamilyParameter.Formula."""
    try:
        if bool(param.IsDeterminedByFormula):
            return _t(param.Formula) or u"?"
    except Exception:
        pass
    try:
        formula = param.Formula
    except Exception:
        return None
    formula = _t(formula).strip() if formula is not None else u""
    return formula or None


def aplicar_tipos(doc_familia, planes):
    """Dentro de una transaccion: NewType si falta, CurrentType y Set. Devuelve [(plan, tipo, antes, despues, fallidos)]."""
    gestor = doc_familia.FamilyManager
    resultados = []
    for plan in planes:
        tipo = plan["tipo"]
        if tipo is None:
            try:
                tipo = gestor.NewType(plan["type_name"])
            except Exception as error:
                raise EscrituraRechazada(u"NewType('{}') failed: {}".format(plan["type_name"], error), 500, {"index": plan["index"]})
        try:
            gestor.CurrentType = tipo
        except Exception as error:
            raise EscrituraRechazada(u"CurrentType = '{}' failed: {}".format(plan["type_name"], error), 500, {"index": plan["index"]})
        antes, despues, fallidos = {}, {}, []
        for cambio in plan["changes"]:
            param = cambio["param"] or F.parametro_familia(doc_familia, cambio["parameter"])
            if param is None:
                fallidos.append({"parameter": cambio["parameter"], "motivo": "parameter not found"})
                continue
            if cambio["internal"] is None:
                try:
                    cambio["internal"], cambio["display"] = _valor_para_tipo(doc_familia, param, cambio["value"])
                except EscrituraRechazada as error:
                    fallidos.append({"parameter": cambio["parameter"], "motivo": error.mensaje})
                    continue
            antes[cambio["parameter"]] = F.valor_tipo(doc_familia, tipo, param)
            try:
                gestor.Set(param, cambio["internal"])
            except Exception as error:
                fallidos.append({"parameter": cambio["parameter"], "motivo": _t(error)})
                continue
            despues[cambio["parameter"]] = F.valor_tipo(doc_familia, tipo, param)
        resultados.append((plan, tipo, antes, despues, fallidos))
    return resultados


def _sistema(dominio, nombre):
    """(enum, nombre) del tipo de sistema del dominio; alias insensible a mayusculas."""
    espacio, clase, _ = F.DOMINIOS[dominio]
    enumerado = getattr(getattr(DB, espacio, None), clase, None)
    if enumerado is None:
        raise EscrituraRechazada(u"DB.{}.{} is not available in this Revit".format(espacio, clase), 409, {"no_soportado": True})
    disponibles = sorted(n for n in dir(enumerado) if not n.startswith("_") and n[0].isupper())
    if not nombre:
        raise EscrituraRechazada(u"system_type is required", 400, {"available_system_types": disponibles})
    objetivo = F._normalizar(nombre).replace("_", "").replace(" ", "")
    for candidato in disponibles:
        if candidato.lower() == objetivo:
            return getattr(enumerado, candidato), candidato
    raise EscrituraRechazada(u"system_type '{}' not in {}.{}".format(nombre, espacio, clase), 400,
                             {"available_system_types": disponibles})


def planificar_conectores(doc_familia, lista, etiqueta="connectors"):
    if not lista:
        raise EscrituraRechazada(u"{} is required (list of {{domain, solid_id, face, system_type, size_mm}})".format(etiqueta), 400)
    if getattr(DB, "ConnectorElement", None) is None:
        raise EscrituraRechazada(u"DB.ConnectorElement is not available", 409, {"no_soportado": True})
    planes = []
    for i, item in enumerate(lista):
        if not isinstance(item, dict):
            raise EscrituraRechazada(_indice(etiqueta, i, u"must be an object"), 400, {"index": i})
        dominio = F._normalizar(item.get("domain"))
        dominio = F._ALIAS_DOMINIO.get(dominio, dominio)
        if dominio not in F.DOMINIOS:
            raise EscrituraRechazada(_indice(etiqueta, i, u"domain must be hvac, piping or electrical (there is no structural connector)"),
                                     400, {"index": i})
        try:
            forma = _forma_por_id(doc_familia, item.get("solid_id") if item.get("solid_id") is not None else item.get("face_of_solid_id"), "solid_id")
            F._vector(item.get("face") or "top", "face")
            sistema, nombre_sistema = _sistema(dominio, item.get("system_type"))
        except EscrituraRechazada as error:
            _rechazo(error, etiqueta, i)
        tamano = item.get("size_mm")
        radio = ancho = alto = None
        if isinstance(tamano, dict):
            if tamano.get("diameter") is not None:
                radio = F._numero(tamano.get("diameter"), "size_mm.diameter") / 2.0
            elif tamano.get("radius") is not None:
                radio = F._numero(tamano.get("radius"), "size_mm.radius")
            else:
                ancho = F._numero(tamano.get("width"), "size_mm.width") if tamano.get("width") is not None else None
                alto = F._numero(tamano.get("height"), "size_mm.height") if tamano.get("height") is not None else None
        elif tamano is not None:
            radio = F._numero(tamano, "size_mm") / 2.0
        if dominio != "electrical" and radio is None and ancho is None:
            raise EscrituraRechazada(_indice(etiqueta, i, u"size_mm (diameter, or {{width, height}}) is required"), 400, {"index": i})
        planes.append({"index": i, "domain": dominio, "solid": forma, "face": item.get("face") or "top", "system": sistema,
                       "system_type": nombre_sistema, "radius_mm": radio, "width_mm": ancho, "height_mm": alto,
                       "view": item.get("view")})
    return planes


def haria_conector(plan):
    return {"accion": "crear_conector", "index": plan["index"], "domain": plan["domain"], "solid_id": get_element_id_value(plan["solid"]),
            "face": plan["face"], "system_type": plan["system_type"], "radius_mm": plan["radius_mm"], "width_mm": plan["width_mm"],
            "height_mm": plan["height_mm"]}


def aplicar_conectores(doc_familia, planes, avisos):
    creados = []
    for plan in planes:
        vista = F._vista_para_normal(doc_familia, F._vector(plan["face"], "face"), plan.get("view"))
        cara, referencia = _cara_de_forma(doc_familia, plan["solid"], plan["face"], vista)
        _, _, metodo = F.DOMINIOS[plan["domain"]]
        try:
            conector = getattr(DB.ConnectorElement, metodo)(doc_familia, plan["system"], referencia)
        except Exception as error:
            raise EscrituraRechazada(u"ConnectorElement.{} failed: {}".format(metodo, error), 500, {"index": plan["index"]})
        for clave, atributo, bip in (("radius_mm", "Radius", "CONNECTOR_RADIUS"), ("width_mm", "Width", "CONNECTOR_WIDTH"),
                                     ("height_mm", "Height", "CONNECTOR_HEIGHT")):
            valor = plan.get(clave)
            if valor is None:
                continue
            if clave != "radius_mm":
                try:
                    conector.Shape = DB.ConnectorProfileType.Rectangular
                except Exception:
                    pass
            try:
                setattr(conector, atributo, float(valor) * MM_TO_FEET)
            except Exception:
                motivo = _fijar_mm(conector, bip, float(valor) * MM_TO_FEET)
                if motivo:
                    avisos.append(u"connector {}: {} not set ({})".format(plan["index"], clave, motivo))
        creados.append(conector)
    return creados


# ---------------------------------------------------------------------------
# Rutas
# ---------------------------------------------------------------------------
def _resultado_creados(doc_familia, elementos, extra_por_elemento=None):
    creados = []
    for k, elemento in enumerate(elementos):
        descripcion = describir_elemento(doc_familia, elemento) or {"id": None}
        descripcion["name"] = get_element_name(elemento)
        if extra_por_elemento:
            descripcion.update(extra_por_elemento[k])
        creados.append(descripcion)
    faltan = [c for c in creados if c.get("id") is None or doc_familia.GetElement(make_element_id(c["id"])) is None]
    return {"creados": creados, "count": len(creados), "ok": not faltan,
            "verificacion": {"coincide": not faltan} if not faltan else
            {"coincide": False, "detalle": u"{} element(s) not found after Commit".format(len(faltan))}}


def register_edicion_routes(api):
    """Rutas de edicion del documento de familia (0.6.0)."""

    @api.route("/family/parameters/", methods=["POST"])
    @requiere_token
    def family_add_parameters(doc, request):
        """LOTE: FamilyManager.AddParameter (+ SetFormula, parametros compartidos por GUID). Acepta `simular`."""

        def cuerpo(ctx):
            doc_familia = ctx["doc_familia"]
            planes = planificar_parametros(doc_familia, F._lista(ctx["data"].get("parameters"), "parameters"))
            comprobar_alcance(ctx["data"], len(planes), "parametros a crear")
            haria = [haria_parametro(p) for p in planes]
            if ctx["simular"]:
                return simulacion(haria, count=len(haria))
            with transaccion(doc_familia, u"Parametros de familia ({})".format(len(planes))):
                creados = aplicar_parametros(doc_familia, planes)
            descritos = [F.describir_parametro(doc_familia, p) for p in creados]
            return {"creados": descritos, "count": len(descritos), "ok": len(descritos) == len(planes),
                    "verificacion": {"coincide": len(descritos) == len(planes)},
                    "parameters": [F.describir_parametro(doc_familia, p) for p in F._parametros_familia(doc_familia)]}

        return ejecutar_familia(doc, "/family/parameters/", request, cuerpo, resolver=_resolver(doc))

    @api.route("/family/reference_planes/", methods=["POST"])
    @requiere_token
    def family_add_reference_planes(doc, request):
        """LOTE: NewReferencePlane con nombre y ELEM_REFERENCE_NAME (is_reference). Acepta `simular`."""

        def cuerpo(ctx):
            doc_familia = ctx["doc_familia"]
            planes = planificar_planos(doc_familia, F._lista(ctx["data"].get("planes") or ctx["data"].get("reference_planes"), "planes"))
            comprobar_alcance(ctx["data"], len(planes), "planos de referencia a crear")
            haria = [haria_plano(p) for p in planes]
            if ctx["simular"]:
                return simulacion(haria, count=len(haria))
            with transaccion(doc_familia, u"Planos de referencia ({})".format(len(planes))):
                creados = aplicar_planos(doc_familia, planes)
            resultado = _resultado_creados(doc_familia, creados, [F.describir_plano(p) for p in creados])
            resultado["avisos"] = [p["aviso"] for p in planes if p.get("aviso")]
            return resultado

        return ejecutar_familia(doc, "/family/reference_planes/", request, cuerpo, resolver=_resolver(doc))

    @api.route("/family/dimensions/", methods=["POST"])
    @requiere_token
    def family_add_dimensions(doc, request):
        """LOTE: NewDimension entre planos de referencia con FamilyLabel (y AreSegmentsEqual). Acepta `simular`."""

        def cuerpo(ctx):
            doc_familia = ctx["doc_familia"]
            planes = planificar_cotas(doc_familia, F._lista(ctx["data"].get("dimensions"), "dimensions"))
            comprobar_alcance(ctx["data"], len(planes), "cotas a crear")
            haria = [haria_cota(p) for p in planes]
            if ctx["simular"]:
                return simulacion(haria, count=len(haria))
            with transaccion(doc_familia, u"Cotas con etiqueta ({})".format(len(planes))):
                creadas = aplicar_cotas(doc_familia, planes)
            extras = [{"label": p["parameter"], "equal": p["equal"], "reference_planes": p["names"]} for p in planes]
            return _resultado_creados(doc_familia, creadas, extras)

        return ejecutar_familia(doc, "/family/dimensions/", request, cuerpo, resolver=_resolver(doc))

    @api.route("/family/solids/", methods=["POST"])
    @requiere_token
    def family_create_solids(doc, request):
        """LOTE: NewExtrusion / NewSweep / NewRevolution / NewBlend (solidos o vaciados), bloqueos y material. Acepta `simular`."""

        def cuerpo(ctx):
            doc_familia = ctx["doc_familia"]
            planes = planificar_solidos(doc_familia, F._lista(ctx["data"].get("solids"), "solids"))
            comprobar_alcance(ctx["data"], len(planes), "solidos a crear")
            haria = [haria_solido(p) for p in planes]
            if ctx["simular"]:
                return simulacion(haria, count=len(haria))
            avisos = []
            with transaccion(doc_familia, u"Solidos de familia ({})".format(len(planes))):
                creados = aplicar_solidos(doc_familia, planes, avisos)
            extras = [dict(F.describir_forma(doc_familia, f), name=p["name"]) for f, p in zip(creados, planes)]
            resultado = _resultado_creados(doc_familia, creados, extras)
            resultado["avisos"] = avisos
            return resultado

        return ejecutar_familia(doc, "/family/solids/", request, cuerpo, resolver=_resolver(doc))

    @api.route("/family/locks/", methods=["POST"])
    @requiere_token
    def family_lock_faces(doc, request):
        """LOTE: NewAlignment entre una cara de un solido (top, bottom, left...) y un plano de referencia. Acepta `simular`."""

        def cuerpo(ctx):
            doc_familia = ctx["doc_familia"]
            planes = planificar_bloqueos(doc_familia, F._lista(ctx["data"].get("locks"), "locks"))
            comprobar_alcance(ctx["data"], len(planes), "bloqueos a crear")
            haria = [haria_bloqueo(p) for p in planes]
            if ctx["simular"]:
                return simulacion(haria, count=len(haria))
            creados = []
            with transaccion(doc_familia, u"Bloquear caras ({})".format(len(planes))):
                for plan in planes:
                    cara, referencia = _cara_de_forma(doc_familia, plan["solid"], plan["face"], plan["view"])
                    creados.append(_alinear(doc_familia, plan["view"], referencia, plan["ref_plane"]))
            return {"locks": [dict(haria_bloqueo(p), id=get_element_id_value(c) if c is not None else None) for p, c in zip(planes, creados)],
                    "count": len(creados), "ok": True, "verificacion": {"coincide": True}}

        return ejecutar_familia(doc, "/family/locks/", request, cuerpo, resolver=_resolver(doc))

    @api.route("/family/types/", methods=["POST"])
    @requiere_token
    def family_set_type_values(doc, request):
        """LOTE: NewType si falta, CurrentType y FamilyManager.Set con valores en unidades del contrato. Acepta `simular`."""

        def cuerpo(ctx):
            doc_familia = ctx["doc_familia"]
            planes = planificar_tipos(doc_familia, F._lista(ctx["data"].get("types"), "types"))
            comprobar_alcance(ctx["data"], sum(max(len(p["changes"]), 1) for p in planes), "tipos x parametros")
            haria = [haria_tipo(p) for p in planes]
            if ctx["simular"]:
                return simulacion(haria, count=len(haria))
            with transaccion(doc_familia, u"Tipos de familia ({})".format(len(planes))):
                resultados = aplicar_tipos(doc_familia, planes)
            tipos, fallidos, antes, despues = [], [], {}, {}
            coincide = True
            for plan, tipo, a, d, fallo in resultados:
                for cambio in plan["changes"]:
                    nombre = cambio["parameter"]
                    # se compara con el valor pedido en unidades del contrato ("display" es texto, p. ej. "30 mm")
                    pedido = cambio["value"]
                    if nombre in d and pedido is not None and not isinstance(pedido, _cadena):
                        try:
                            if abs(float(d[nombre]) - float(int(pedido) if isinstance(pedido, bool) else pedido)) > 1e-3:
                                coincide = False
                                fallo.append({"parameter": nombre, "motivo": u"Revit kept {} after Set({})".format(d[nombre], pedido)})
                        except (TypeError, ValueError):
                            pass
                tipos.append({"type_name": plan["type_name"], "created": plan["create"], "antes": a, "despues": d,
                              "fallidos": fallo, "coincide": not fallo})
                antes[plan["type_name"]], despues[plan["type_name"]] = a, d
                fallidos.extend(dict(f, type_name=plan["type_name"]) for f in fallo)
            parametros = F._parametros_familia(doc_familia)
            return {"types": tipos, "count": len(tipos), "antes": antes, "despues": despues, "fallidos": fallidos,
                    "ok": coincide and not fallidos, "verificacion": {"coincide": coincide},
                    "family_types": [F.describir_tipo(doc_familia, t, parametros) for t in F.tipos_familia(doc_familia)]}

        return ejecutar_familia(doc, "/family/types/", request, cuerpo, resolver=_resolver(doc))

    @api.route("/family/connectors/", methods=["POST"])
    @requiere_token
    def family_add_connectors(doc, request):
        """LOTE: ConnectorElement.CreateDuct/Pipe/ElectricalConnector sobre una cara de un solido. Acepta `simular`."""

        def cuerpo(ctx):
            doc_familia = ctx["doc_familia"]
            planes = planificar_conectores(doc_familia, F._lista(ctx["data"].get("connectors"), "connectors"))
            comprobar_alcance(ctx["data"], len(planes), "conectores a crear")
            haria = [haria_conector(p) for p in planes]
            if ctx["simular"]:
                return simulacion(haria, count=len(haria))
            avisos = []
            with transaccion(doc_familia, u"Conectores de familia ({})".format(len(planes))):
                creados = aplicar_conectores(doc_familia, planes, avisos)
            resultado = _resultado_creados(doc_familia, creados, F.conectores_familia(doc_familia)[-len(creados):] if creados else [])
            resultado["avisos"] = avisos
            return resultado

        return ejecutar_familia(doc, "/family/connectors/", request, cuerpo, resolver=_resolver(doc))
