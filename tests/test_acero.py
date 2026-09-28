# -*- coding: utf-8 -*-
"""Entrega 2b (0.5.0), bloque B: estructuras metalicas sobre el modelo simulado.

Bloque 1 (lectura):
  POST /steel_profiles/    perfiles cargados (material por Family.StructuralMaterialType o por la
                           clase Metal del activo), forma, norma, dimensiones; biblioteca (.rfa + .txt)
  POST /steel_quantities/  peso por volumen x densidad, masa lineal de reserva, sin_peso con motivo
  POST /describe/          include_structural: liberaciones, justificaciones, desfases, no_disponibles
  POST /element_types/     category="connections": 409 no_soportado sin el modulo, tipos con el modulo

El modelo simulado es un Revit "en espanol": nombres visibles con tildes y todo lo
estructural resuelto por BuiltInParameter.
"""
import io
import os

import pytest
from pyrevit import DB, routes

import modelo_falso as mf
import seguridad

TOKEN = "5" * 64
BIP = DB.BuiltInParameter
BIC = DB.BuiltInCategory
ST = DB.Structure
FT3_POR_M3 = 1.0 / 0.028316846592


class Viga(mf.Elemento, DB.FamilyInstance):
    """Viga o pilar estructural con parametros de liberaciones, justificacion, desfases..."""

    def __init__(self, doc, identificador, symbol, inicio_mm, fin_mm, nivel_id, marca=u"", volumen_m3=None,
                 material_id=None, con_liberaciones=True, extra=()):
        mf.Elemento.__init__(
            self, doc, identificador, nombre=None, categoria=symbol.Category.Name, bic=symbol.Category.BuiltInCategory,
            tipo_id=symbol.Id.Value, nivel_id=nivel_id,
        )
        self.symbol = symbol
        self.Host = None
        self.SuperComponent = None
        self.StructuralUsage = ST.StructuralInstanceUsage.Girder
        self.StructuralMaterialType = ST.StructuralMaterialType.Steel
        inicio = DB.XYZ(inicio_mm[0] * mf.MM_TO_FEET, inicio_mm[1] * mf.MM_TO_FEET, inicio_mm[2] * mf.MM_TO_FEET)
        fin = DB.XYZ(fin_mm[0] * mf.MM_TO_FEET, fin_mm[1] * mf.MM_TO_FEET, fin_mm[2] * mf.MM_TO_FEET)
        self.Location = mf.Ubicacion(curva=DB.Line.CreateBound(inicio, fin))
        longitud = inicio.DistanceTo(fin) * 304.8
        parametros = [
            mf.texto(u"Marca", marca, bip=BIP.ALL_MODEL_MARK),
            mf.texto(u"Comentarios", u"", bip=BIP.ALL_MODEL_INSTANCE_COMMENTS),
            mf.longitud_mm(u"Longitud", longitud, bip=BIP.INSTANCE_LENGTH_PARAM, solo_lectura=True),
            mf.entero(u"Justificación Y", 2, bip=BIP.Y_JUSTIFICATION),
            mf.entero(u"Justificación Z", 1, bip=BIP.Z_JUSTIFICATION),
            mf.longitud_mm(u"Desfase Y", 0, bip=BIP.Y_OFFSET_VALUE),
            mf.longitud_mm(u"Desfase Z", 0, bip=BIP.Z_OFFSET_VALUE),
            mf.Parametro(u"Rotación de sección", 0.0, "Double", bip=BIP.STRUCTURAL_BEND_DIR_ANGLE, spec=mf.SpecTypeId.Angle),
            mf.longitud_mm(u"Extensión inicial", 0, bip=BIP.START_EXTENSION),
            mf.longitud_mm(u"Extensión final", 0, bip=BIP.END_EXTENSION),
            mf.entero(u"Analizar como", 1, bip=BIP.STRUCTURAL_ANALYZES_AS),
            mf.entero(u"Uso estructural", 2, bip=BIP.INSTANCE_STRUCT_USAGE_PARAM),
        ]
        if volumen_m3 is not None:
            parametros.append(mf.Parametro(u"Volumen", volumen_m3 * FT3_POR_M3, "Double",
                                           bip=BIP.HOST_VOLUME_COMPUTED, solo_lectura=True))
        if material_id is not None:
            parametros.append(mf.referencia(u"Material estructural", material_id, bip=BIP.STRUCTURAL_MATERIAL_PARAM))
        if con_liberaciones:
            parametros.extend(parametros_liberaciones())
        parametros.extend(extra)
        self.Parameters = parametros


class _Componente(mf.Parametro):
    """Fx..Mz de una liberacion: solo editable cuando el tipo de liberacion es 'definido por el usuario' (3)."""

    def __init__(self, nombre, tipo_param, bip):
        mf.Parametro.__init__(self, nombre, 0, "Integer", bip=bip)
        self.tipo_param = tipo_param

    @property
    def IsReadOnly(self):
        return self.tipo_param.AsInteger() != 3

    @IsReadOnly.setter
    def IsReadOnly(self, valor):
        pass


def parametros_liberaciones(inicio=0, fin=1):
    """Tipo de liberacion inicial (0 = empotrado) y final (1 = articulado) con sus componentes."""
    lista = []
    for prefijo, etiqueta, valor in (("START", u"inicial", inicio), ("END", u"final", fin)):
        tipo = mf.entero(u"Liberación {}".format(etiqueta), valor, bip=getattr(BIP, "STRUCTURAL_{}_RELEASE_TYPE".format(prefijo)))
        lista.append(tipo)
        for clave in ("FX", "FY", "FZ", "MX", "MY", "MZ"):
            lista.append(_Componente(u"{} {}".format(etiqueta.capitalize(), clave.capitalize()), tipo,
                                     getattr(BIP, "STRUCTURAL_{}_RELEASE_{}".format(prefijo, clave))))
    return lista


def _tipo_acero(doc, identificador, familia, nombre, h=None, b=None, tw=None, tf=None, material_id=None,
                masa_lineal_kg_m=None, forma=None):
    parametros = []
    for valor, bip in ((h, BIP.STRUCTURAL_SECTION_COMMON_HEIGHT), (b, BIP.STRUCTURAL_SECTION_COMMON_WIDTH),
                       (tw, BIP.STRUCTURAL_SECTION_COMMON_WEB_THICKNESS), (tf, BIP.STRUCTURAL_SECTION_COMMON_FLANGE_THICKNESS)):
        if valor is not None:
            parametros.append(mf.longitud_mm(u"dim", valor, bip=bip))
    if material_id is not None:
        parametros.append(mf.referencia(u"Material estructural", material_id, bip=BIP.STRUCTURAL_MATERIAL_PARAM))
    if masa_lineal_kg_m is not None:
        parametros.append(mf.Parametro(u"Peso nominal", masa_lineal_kg_m * 0.3048, "Double",
                                       bip=BIP.STRUCTURAL_SECTION_NOMINAL_WEIGHT))
    tipo = mf.TipoFamilia(doc, identificador, familia=familia, nombre=nombre, categoria=familia.Category.Name,
                          bic=familia.bic, parametros=parametros)
    if forma is not None:
        tipo.seccion = ST.StructuralSection(forma)
    return tipo


@pytest.fixture
def doc(tmp_path, monkeypatch):
    mf.activar_spec(monkeypatch)
    rvt = tmp_path / "Modelo.rvt"
    rvt.write_bytes(b"RVT")
    doc = mf.Doc(str(rvt))
    mf.Nivel(doc, 1, u"Nivel 1", 0)
    mf.Nivel(doc, 2, u"Nivel 2", 3500)
    mf.Nivel(doc, 3, u"Cubierta", 7000)
    # materiales: uno con activo estructural (acero, 7850 kg/m3) y otro sin activo
    mf.ActivoEstructural(doc, 40, u"Acero S275 (activo)", 7850)
    mf.Material(doc, 41, u"Acero S275", activo_id=40)
    mf.Material(doc, 42, u"Acero sin activo")
    mf.ActivoEstructural(doc, 43, u"Hormigón (activo)", 2400, clase=DB.StructuralAssetClass.Concrete)
    mf.Material(doc, 44, u"Hormigón HA-25", activo_id=43)
    # familias: IPE (acero por StructuralMaterialType), HEB (acero, material sin activo, con masa lineal),
    # W (StructuralMaterialType sin definir -> acero por la clase Metal del activo), hormigon (descartada)
    fam_ipe = mf.Familia(doc, 70, nombre=u"IPE", categoria=u"Armazón estructural", bic=BIC.OST_StructuralFraming)
    fam_ipe.StructuralMaterialType = ST.StructuralMaterialType.Steel
    _tipo_acero(doc, 71, fam_ipe, u"IPE300", 300, 150, 7.1, 10.7, material_id=41, forma=ST.StructuralSectionShape.IWideFlange)
    _tipo_acero(doc, 72, fam_ipe, u"IPE200", 200, 100, 5.6, 8.5, material_id=41, forma=ST.StructuralSectionShape.IWideFlange)
    fam_heb = mf.Familia(doc, 73, nombre=u"HEB", categoria=u"Pilares estructurales", bic=BIC.OST_StructuralColumns)
    fam_heb.StructuralMaterialType = ST.StructuralMaterialType.Steel
    _tipo_acero(doc, 74, fam_heb, u"HEB200", 200, 200, 9, 15, material_id=42, masa_lineal_kg_m=61.3)
    fam_w = mf.Familia(doc, 75, nombre=u"W-Wide Flange", categoria=u"Armazón estructural", bic=BIC.OST_StructuralFraming)
    _tipo_acero(doc, 76, fam_w, u"W12X26", material_id=41)
    fam_hss = mf.Familia(doc, 77, nombre=u"HSS-Hollow", categoria=u"Armazón estructural", bic=BIC.OST_StructuralFraming)
    fam_hss.StructuralMaterialType = ST.StructuralMaterialType.Steel
    _tipo_acero(doc, 78, fam_hss, u"HSS6X6X1/4", material_id=42, forma=ST.StructuralSectionShape.RectangleHSS)
    fam_hormigon = mf.Familia(doc, 79, nombre=u"Viga hormigón", categoria=u"Armazón estructural", bic=BIC.OST_StructuralFraming)
    fam_hormigon.StructuralMaterialType = ST.StructuralMaterialType.Concrete
    _tipo_acero(doc, 80, fam_hormigon, u"30x50", material_id=44)
    # elementos: dos IPE300 (volumen y material con activo), un HEB200 (material sin activo -> masa lineal),
    # un HSS (sin volumen, sin activo, sin masa lineal -> sin_peso) y una viga de hormigon
    Viga(doc, 10, doc.elementos[71], (0, 0, 3500), (6000, 0, 3500), 2, u"V-1", volumen_m3=0.0322, material_id=41)
    Viga(doc, 11, doc.elementos[71], (0, 5000, 3500), (6000, 5000, 3500), 2, u"V-2", volumen_m3=0.0322, material_id=41)
    Viga(doc, 12, doc.elementos[74], (0, 0, 0), (0, 0, 3500), 1, u"P-1", volumen_m3=0.0273, material_id=42, con_liberaciones=False)
    Viga(doc, 13, doc.elementos[78], (6000, 0, 3500), (6000, 5000, 3500), 2, u"V-3", material_id=42)
    Viga(doc, 14, doc.elementos[80], (0, 0, 7000), (6000, 0, 7000), 3, u"H-1", volumen_m3=0.9, material_id=44)
    return doc


@pytest.fixture
def api():
    seguridad.establecer_token(TOKEN)
    DB.Transaction.creadas = []
    DB.Transaction.resultado_commit = DB.TransactionStatus.Committed
    api = routes.API("revit_mcp")
    from acero import register_acero_routes
    from navegacion import register_navegacion_routes
    from consulta import register_consulta_routes

    register_acero_routes(api)
    register_navegacion_routes(api)
    register_consulta_routes(api)
    return api


def _post(api, ruta, doc, cuerpo, con_token=True):
    datos = dict(cuerpo)
    if con_token:
        datos["token"] = TOKEN
    return api.rutas[(ruta, "POST")](doc=doc, request=routes.Request(path=ruta, data=datos))


def _biblioteca(tmp_path):
    """Carpeta de biblioteca con un .rfa de perfiles W con catalogo, una puerta con catalogo y un .rfa sin catalogo."""
    carpeta = tmp_path / "Biblioteca" / "Estructura" / "Perfiles"
    carpeta.mkdir(parents=True)
    (carpeta / "W-Perfiles.rfa").write_bytes(b"RFA")
    with io.open(str(carpeta / "W-Perfiles.txt"), "w", encoding="utf-8") as archivo:
        archivo.write(u",h##LENGTH##MILLIMETERS,b##LENGTH##MILLIMETERS\nW12X26,310,165\nW16X31,403,140\n")
    (carpeta / "IPE.rfa").write_bytes(b"RFA")
    puertas = tmp_path / "Biblioteca" / "Puertas"
    puertas.mkdir()
    (puertas / "Puerta simple.rfa").write_bytes(b"RFA")
    with io.open(str(puertas / "Puerta simple.txt"), "w", encoding="utf-8") as archivo:
        archivo.write(u",Anchura##LENGTH##MILLIMETERS\n0915 x 2134,915\n")
    return str(tmp_path / "Biblioteca")


# ---------------------------------------------------------------------------
# /steel_profiles/
# ---------------------------------------------------------------------------
def test_steel_profiles_cargados_forma_norma_y_dimensiones(api, doc):
    assert _post(api, "/steel_profiles/", doc, {}, con_token=False).status == 401
    assert _post(api, "/steel_profiles/", None, {}).status == 503
    r = _post(api, "/steel_profiles/", doc, {})
    assert r.status == 200, r.data
    datos = r.data
    perfiles = dict((p["type"], p) for p in datos["loaded"])
    # el hormigon queda fuera; W12X26 entra por la clase Metal de su activo aunque la familia no defina el material
    assert sorted(perfiles) == [u"HEB200", u"HSS6X6X1/4", u"IPE200", u"IPE300", u"W12X26"]
    assert datos["count"] == 5 and datos["not_steel"] == 1 and datos["unknown_material"] == 0
    ipe = perfiles[u"IPE300"]
    assert ipe["family"] == u"IPE" and ipe["category"] == "OST_StructuralFraming" and ipe["categoria"] == u"Armazón estructural"
    assert ipe["shape"] == "W" and ipe["shape_source"].startswith("StructuralSectionShape") and ipe["standard"] == "EN"
    assert ipe["dimensions_mm"] == {"height": 300.0, "width": 150.0, "web_thickness": 7.1, "flange_thickness": 10.7}
    assert ipe["instances"] == 2 and ipe["material"] == u"Acero S275" and ipe["material_type"] == "Steel"
    assert ipe["steel_by"] == u"Family.StructuralMaterialType = Steel"
    w = perfiles[u"W12X26"]
    assert w["shape"] == "W" and w["shape_source"] == u"designacion del tipo" and w["standard"] == "AISC"
    assert w["steel_by"] == u"StructuralAssetClass = Metal" and w["dimensions_mm"] == {} and w["instances"] == 0
    assert perfiles[u"HSS6X6X1/4"]["shape"] == "HSS" and perfiles[u"HSS6X6X1/4"]["standard"] == "AISC"
    assert perfiles[u"HEB200"]["category"] == "OST_StructuralColumns" and perfiles[u"HEB200"]["instances"] == 1
    assert datos["no_disponibles"] == [] and "library" not in datos
    assert DB.Transaction.creadas == []


def test_steel_profiles_filtros_y_biblioteca(api, doc, tmp_path, monkeypatch):
    r = _post(api, "/steel_profiles/", doc, {"shape": "HSS"})
    assert r.status == 200 and [p["type"] for p in r.data["loaded"]] == [u"HSS6X6X1/4"]
    r = _post(api, "/steel_profiles/", doc, {"standard": "EN"})
    assert sorted(p["type"] for p in r.data["loaded"]) == [u"HEB200", u"IPE200", u"IPE300"]
    r = _post(api, "/steel_profiles/", doc, {"standard": "AISC", "shape": "W"})
    assert [p["type"] for p in r.data["loaded"]] == [u"W12X26"]
    assert _post(api, "/steel_profiles/", doc, {"standard": "DIN"}).status == 400
    assert _post(api, "/steel_profiles/", doc, {"shape": "Z"}).status == 400
    # biblioteca: solo los .rfa con catalogo cuyos tipos tienen designacion de perfil
    doc.Application.bibliotecas = {u"Biblioteca métrica": _biblioteca(tmp_path)}
    r = _post(api, "/steel_profiles/", doc, {"loaded_only": False})
    assert r.status == 200, r.data
    assert r.data["library_count"] == 1 and r.data["library_scanned_files"] == 3 and r.data["library_truncated"] is False
    familia = r.data["library"][0]
    assert familia["family"] == u"W-Perfiles" and familia["types"] == [u"W12X26", u"W16X31"] and familia["types_total"] == 2
    assert familia["shapes"] == ["W"] and familia["standards"] == ["AISC"] and familia["is_loaded"] is False
    assert familia["path"].endswith("W-Perfiles.rfa") and familia["catalog_path"].endswith("W-Perfiles.txt")
    assert r.data["library_paths"] == [str(tmp_path / "Biblioteca")]
    r = _post(api, "/steel_profiles/", doc, {"loaded_only": False, "shape": "L"})
    assert r.data["library"] == [] and r.data["loaded"] == []
    # si un BuiltInParameter de seccion no existe en la version, se omite y se anota
    monkeypatch.delattr(DB.BuiltInParameter, "STRUCTURAL_SECTION_COMMON_WEB_THICKNESS")
    r = _post(api, "/steel_profiles/", doc, {"shape": "W", "standard": "EN"})
    assert r.data["no_disponibles"] == ["STRUCTURAL_SECTION_COMMON_WEB_THICKNESS"]
    assert "web_thickness" not in r.data["loaded"][0]["dimensions_mm"]


def test_steel_profiles_sin_acero_avisa(api, doc):
    for identificador in (71, 72, 74, 76, 78):
        doc.elementos.pop(identificador)
    r = _post(api, "/steel_profiles/", doc, {})
    assert r.status == 200 and r.data["loaded"] == [] and "load_steel_profile" in r.data["nota"]


# ---------------------------------------------------------------------------
# /steel_quantities/
# ---------------------------------------------------------------------------
def test_steel_quantities_por_tipo_con_densidad_masa_lineal_y_sin_peso(api, doc):
    assert _post(api, "/steel_quantities/", doc, {}, con_token=False).status == 401
    r = _post(api, "/steel_quantities/", doc, {"group_by": "type"})
    assert r.status == 200, r.data
    datos = r.data
    grupos = dict((g["group"], g) for g in datos["groups"])
    # la viga de hormigon no es acero: no entra
    assert sorted(grupos) == [u"HEB: HEB200", u"HSS-Hollow: HSS6X6X1/4", u"IPE: IPE300"]
    ipe = grupos[u"IPE: IPE300"]
    assert ipe["count"] == 2 and ipe["length_mm"] == 12000.0 and ipe["element_ids"] == [10, 11]
    assert abs(ipe["weight_kg"] - 2 * 0.0322 * 7850) < 0.5 and ipe["methods"] == {"volumen x densidad": 2}
    heb = grupos[u"HEB: HEB200"]
    assert heb["count"] == 1 and heb["length_mm"] == 3500.0
    assert abs(heb["weight_kg"] - 61.3 * 3.5) < 0.01 and list(heb["methods"]) == ["masa lineal (STRUCTURAL_SECTION_NOMINAL_WEIGHT) x longitud"]
    hss = grupos[u"HSS-Hollow: HSS6X6X1/4"]
    assert hss["weight_kg"] == 0 and hss["with_weight"] == 0 and hss["length_mm"] == 5000.0
    assert len(datos["sin_peso"]) == 1 and datos["sin_peso"][0]["element_id"] == 13
    motivo = datos["sin_peso"][0]["motivo"]
    assert "HOST_VOLUME_COMPUTED" in motivo and "no tiene activo estructural" in motivo and "masa lineal" in motivo
    assert datos["totals"]["count"] == 4 and datos["totals"]["with_weight"] == 3 and datos["totals"]["without_weight"] == 1
    assert abs(datos["totals"]["weight_kg"] - (2 * 0.0322 * 7850 + 61.3 * 3.5)) < 0.5
    assert datos["scanned"] == 5 and datos["truncated"] is False and "densidad" in datos["metodo"]
    assert DB.Transaction.creadas == []


def test_steel_quantities_agrupaciones_ids_y_errores(api, doc):
    r = _post(api, "/steel_quantities/", doc, {"group_by": "level"})
    assert sorted(g["group"] for g in r.data["groups"]) == [u"Nivel 1", u"Nivel 2"]
    r = _post(api, "/steel_quantities/", doc, {"group_by": "mark", "element_ids": [10, 12, 999]})
    assert r.status == 200, r.data
    assert sorted(g["group"] for g in r.data["groups"]) == [u"P-1", u"V-1"] and r.data["not_found"] == [999]
    r = _post(api, "/steel_quantities/", doc, {"group_by": "family", "element_ids": [14]})
    assert r.data["groups"][0]["group"] == u"Viga hormigón" and r.data["not_steel"] == [14]
    assert abs(r.data["groups"][0]["weight_kg"] - 0.9 * 2400) < 0.5     # pedido explicitamente, se pesa igual
    r = _post(api, "/steel_quantities/", doc, {"group_by": "peso"})
    assert r.status == 400 and r.data["available_group_by"] == ["type", "level", "family", "mark"]
    assert _post(api, "/steel_quantities/", doc, {"element_ids": "10"}).status == 400
    r = _post(api, "/steel_quantities/", doc, {"max": 2})
    assert r.data["truncated"] is True and r.data["totals"]["count"] == 2


# ---------------------------------------------------------------------------
# /describe/ con include_structural
# ---------------------------------------------------------------------------
def test_describe_include_structural_liberaciones_por_parametro(api, doc, monkeypatch):
    r = _post(api, "/describe/", doc, {"element_id": 10})
    assert r.status == 200 and "structural" not in r.data
    r = _post(api, "/describe/", doc, {"element_id": 10, "include_structural": True})
    assert r.status == 200, r.data
    bloque = r.data["structural"]
    assert bloque["releases"]["start"]["type"] == "fixed" and bloque["releases"]["start"]["source"] == "BuiltInParameter"
    assert bloque["releases"]["end"]["type"] == "pinned" and bloque["releases"]["end"]["type_index"] == 1
    assert bloque["releases"]["start"]["FX"] is False and set(bloque["releases"]["end"]) >= set(["FX", "FY", "FZ", "MX", "MY", "MZ"])
    assert bloque["y_justification"]["index"] == 2 and bloque["z_justification"]["index"] == 1
    assert bloque["y_offset_mm"] == 0.0 and bloque["z_offset_mm"] == 0.0 and bloque["section_rotation_deg"] == 0.0
    assert bloque["start_extension_mm"] == 0.0 and bloque["end_extension_mm"] == 0.0
    assert bloque["analyze_as"]["index"] == 1 and bloque["structural_usage"] == {"value": "2", "index": 2, "enum": "Girder"}
    assert bloque["structural_material"] == u"Acero S275" and bloque["structural_material_type"] == "Steel"
    assert bloque["is_steel"] is True and bloque["no_disponibles"] == [] and bloque["no_aplica"] == []
    # un BuiltInParameter que no existe en esta version se omite y se anota
    monkeypatch.delattr(DB.BuiltInParameter, "STRUCTURAL_ANALYZES_AS")
    r = _post(api, "/describe/", doc, {"element_id": 10, "include_structural": True})
    bloque = r.data["structural"]
    assert "analyze_as" not in bloque and bloque["no_disponibles"] == ["STRUCTURAL_ANALYZES_AS"]


def test_describe_include_structural_liberaciones_del_miembro_analitico(api, doc):
    """2023+: el pilar no tiene los parametros de liberacion; se leen del AnalyticalMember asociado."""
    miembro = mf.MiembroAnalitico(doc, 500, (0, 0, 0), (0, 0, 3500))
    miembro.SetReleaseType(ST.AnalyticalElementSelector.StartOrBase, ST.ReleaseType.Pinned)
    miembro.SetReleaseConditions(ST.ReleaseConditions(False, mx=True, my=True))
    doc.asociar(doc.elementos[12], miembro)
    r = _post(api, "/describe/", doc, {"element_id": 12, "include_structural": True})
    assert r.status == 200, r.data
    bloque = r.data["structural"]
    assert bloque["releases"]["start"] == {"type": "pinned", "source": "AnalyticalMember", "FX": False, "FY": False,
                                            "FZ": False, "MX": False, "MY": False, "MZ": False}
    assert bloque["releases"]["end"]["type"] == "fixed" and bloque["releases"]["end"]["MX"] is True
    assert bloque["analytical_member_id"] == 500
    assert "STRUCTURAL_START_RELEASE_TYPE" in bloque["no_aplica"] and bloque["no_disponibles"] == []
    # sin miembro analitico ni parametros: liberaciones None
    doc.asociaciones.clear()
    r = _post(api, "/describe/", doc, {"element_id": 12, "include_structural": True})
    assert r.data["structural"]["releases"] == {"start": None, "end": None}


# ---------------------------------------------------------------------------
# /element_types/ category="connections"
# ---------------------------------------------------------------------------
def test_element_types_connections_409_sin_modulo_y_tipos_con_modulo(api, doc, monkeypatch):
    r = _post(api, "/element_types/", doc, {"category": "connections"})
    assert r.status == 409, r.data
    assert r.data["no_soportado"] is True and "StructuralConnectionHandler" in r.data["error"]
    assert r.data["motivo"] == "api"
    _, aprobacion_cls, _ = mf.instalar_conexiones(monkeypatch)
    # con el modulo pero sin tipos cargados tambien es no_soportado
    r = _post(api, "/element_types/", doc, {"category": "conexiones"})
    assert r.status == 409 and r.data["no_soportado"] is True and r.data["motivo"] == "sin_tipos"

    class TipoConexion(mf.Elemento, DB.Structure.StructuralConnectionHandlerType):
        pass

    class Aprobacion(mf.Elemento, aprobacion_cls):
        pass

    TipoConexion(doc, 600, nombre=u"Conexión genérica", categoria=u"Conexiones estructurales", bic=BIC.OST_StructConnections)
    Aprobacion(doc, 601, nombre=u"No aprobada", categoria=None)
    Aprobacion(doc, 602, nombre=u"Aprobada", categoria=None)
    r = _post(api, "/element_types/", doc, {"category": "connections"})
    assert r.status == 200, r.data
    assert r.data["types"] == [{"id": 600, "familia": None, "tipo": u"Conexión genérica", "parametros": {}, "ejemplares": 0}]
    assert r.data["approval_types"] == [{"id": 601, "nombre": u"No aprobada"}, {"id": 602, "nombre": u"Aprobada"}]
    assert r.data["class"] == "StructuralConnectionHandlerType" and r.data["count"] == 1
    # las demas categorias siguen como antes
    r = _post(api, "/element_types/", doc, {"category": "OST_StructuralFraming"})
    assert r.status == 200 and r.data["count"] == 5
