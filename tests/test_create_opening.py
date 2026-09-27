# -*- coding: utf-8 -*-
"""Pruebas en CPython de /create_opening/ (revit_mcp/estructural.py) en muros.

Reproducen lo visto en Revit 2027 el 2026-09-27: NewOpening acepta cualquier
rectangulo y, si no corta el muro, Revit lo borra al confirmar con un aviso
("Rectangular opening doesn't cut its host") y la transaccion CONFIRMADA. La
ruta debe (1) rechazar con 400, antes de tocar el modelo, los rectangulos que
no pueden cortar el muro; (2) devolver `en_muro` con la situacion del hueco en
mm; y (3) si Revit lo borra igualmente, explicarlo con `avisos_revit` en
`verificacion.detalle`.

El muro simulado es el 165465 del modelo de prueba: 21350 mm de largo, oblicuo
(0.3 grados), cota 2925..5925 mm, 140 mm de espesor.
"""
import pytest
from pyrevit import DB, routes

import seguridad
import utils

TOKEN = "b" * 64
PIES = 1.0 / 304.8
INICIO = (6386.19, 18110.87, 2925.0)
FIN = (27735.90, 17998.25, 2925.0)


class Cat(object):
    def __init__(self, nombre):
        self.Name = nombre


def _xyz_mm(x, y, z):
    return DB.XYZ(x * PIES, y * PIES, z * PIES)


class MuroFalso(DB.Wall):
    def __init__(self, identificador=165465, inicio=INICIO, fin=FIN, z_min=2925.0, z_max=5925.0, espesor=140.0):
        self.Id = DB.ElementId(identificador)
        self.Category = Cat("Muros")
        self.LevelId = DB.ElementId.InvalidElementId
        self.Pinned = False
        self.Location = DB.LocationCurve(DB.Line(_xyz_mm(*inicio), _xyz_mm(*fin)))
        self.Width = espesor * PIES
        self._bb = DB.BoundingBoxXYZ(
            _xyz_mm(min(inicio[0], fin[0]), min(inicio[1], fin[1]), z_min),
            _xyz_mm(max(inicio[0], fin[0]), max(inicio[1], fin[1]), z_max),
        )

    def GetTypeId(self):
        return DB.ElementId.InvalidElementId

    def get_BoundingBox(self, vista):
        return self._bb

    def get_Parameter(self, bip):
        return None


class HuecoFalso(object):
    def __init__(self, identificador, a, b):
        self.Id = DB.ElementId(identificador)
        self.IsRectBoundary = True
        self.BoundaryRect = [a, b]
        self.Category = Cat("Rectangular Straight Wall Opening")
        self.LevelId = DB.ElementId.InvalidElementId
        self.Pinned = False

    def GetTypeId(self):
        return DB.ElementId.InvalidElementId

    def get_BoundingBox(self, vista):
        return None

    def get_Parameter(self, bip):
        return None


class CreacionFalsa(object):
    """doc.Create: NewOpening registra el hueco en el documento como hace Revit."""

    def __init__(self, doc):
        self.doc = doc
        self.llamadas = []
        self.siguiente_id = 615578

    def NewOpening(self, host, a, b):
        self.llamadas.append((host, a, b))
        hueco = HuecoFalso(self.siguiente_id, a, b)
        self.doc.elementos[self.siguiente_id] = hueco
        self.siguiente_id += 11
        return hueco


class DocFalso(object):
    def __init__(self, ruta):
        self.PathName = ruta
        self.Title = "Modelo_Copia"
        self.IsWorkshared = False
        self.IsReadOnly = False
        self.IsModifiable = False
        self.elementos = {}
        self.Create = CreacionFalsa(self)

    def GetElement(self, elem_id):
        return self.elementos.get(elem_id.Value)


@pytest.fixture
def api():
    seguridad.establecer_token(TOKEN)
    DB.Transaction.creadas = []
    DB.Transaction.resultado_commit = DB.TransactionStatus.Committed
    DB.Transaction.fallos = []
    DB.Transaction.al_confirmar = None
    utils.ULTIMOS_AVISOS = []
    api = routes.API("revit_mcp")
    from estructural import register_estructural_routes

    register_estructural_routes(api)
    yield api
    DB.Transaction.fallos = []
    DB.Transaction.al_confirmar = None


@pytest.fixture
def doc(tmp_path):
    rvt = tmp_path / "Modelo_Copia.rvt"
    rvt.write_bytes(b"RVT")
    documento = DocFalso(str(rvt))
    documento.elementos[165465] = MuroFalso()
    return documento


def _crear(api, doc, cuerpo):
    manejador = api.rutas[("/create_opening/", "POST")]
    datos = dict(cuerpo)
    datos["token"] = TOKEN
    return manejador(doc=doc, request=routes.Request(path="/create_opening/", data=datos))


def _sobre_el_muro(fraccion, z, desvio_mm=0.0):
    """Punto (mm) sobre la linea de ubicacion del muro a `fraccion` de su longitud,
    desplazado `desvio_mm` en perpendicular."""
    dx, dy = FIN[0] - INICIO[0], FIN[1] - INICIO[1]
    longitud = (dx ** 2 + dy ** 2) ** 0.5
    nx, ny = -dy / longitud, dx / longitud
    return {
        "x": round(INICIO[0] + fraccion * dx + desvio_mm * nx, 2),
        "y": round(INICIO[1] + fraccion * dy + desvio_mm * ny, 2),
        "z": z,
    }


# ---------------------------------------------------------------------------
# Rechazos antes de tocar el modelo
# ---------------------------------------------------------------------------
def test_hueco_bajo_el_muro_se_rechaza_y_explica_que_z_es_absoluta(api, doc):
    # Segundo intento del agente el 2026-09-27: z=900..2100 con el muro en 2925..5925
    r = _crear(api, doc, {"host_id": 165465, "points": [_sobre_el_muro(0.3, 900), _sobre_el_muro(0.7, 2100)]})
    assert r.status == 400, r.data
    assert "ABSOLUTE" in r.data["error"]
    assert "2925.0..5925.0" in r.data["error"]
    assert "z 3825.0 and z 5025.0" in r.data["error"]
    assert r.data["en_muro"]["muro"]["z_min_mm"] == 2925.0
    assert r.data["en_muro"]["hueco"]["z_desde_mm"] == 900.0
    assert DB.Transaction.creadas == []
    assert doc.Create.llamadas == []


def test_hueco_fuera_de_la_longitud_se_rechaza(api, doc):
    r = _crear(api, doc, {"host_id": 165465, "points": [_sobre_el_muro(1.2, 3825), _sobre_el_muro(1.5, 5025)]})
    assert r.status == 400, r.data
    assert "outside the wall along its length" in r.data["error"]
    assert r.data["en_muro"]["muro"]["longitud_mm"] == pytest.approx(21350.0, abs=1.0)
    assert r.data["en_muro"]["hueco"]["desde_mm"] > 21350.0
    assert doc.Create.llamadas == []


def test_esquinas_fuera_del_plano_del_muro_se_rechazan(api, doc):
    r = _crear(api, doc, {"host_id": 165465, "points": [
        _sobre_el_muro(0.3, 3825, desvio_mm=500.0), _sobre_el_muro(0.7, 5025, desvio_mm=500.0)]})
    assert r.status == 400, r.data
    assert "away from the wall's plane" in r.data["error"]
    assert "location_mm" in r.data["error"]
    assert r.data["en_muro"]["hueco"]["fuera_del_plano_mm"] == pytest.approx(500.0, abs=1.0)
    assert doc.Create.llamadas == []


def test_esquinas_sobre_la_cara_del_muro_se_aceptan(api, doc):
    # A medio espesor (70 mm) del eje: la cara del muro, tolerada
    r = _crear(api, doc, {"host_id": 165465, "points": [
        _sobre_el_muro(0.3, 3825, desvio_mm=70.0), _sobre_el_muro(0.7, 5025, desvio_mm=70.0)], "simular": True})
    assert r.status == 200, r.data


def test_rectangulo_degenerado_se_rechaza(api, doc):
    r = _crear(api, doc, {"host_id": 165465, "points": [_sobre_el_muro(0.3, 3825), _sobre_el_muro(0.7, 3825)]})
    assert r.status == 400, r.data
    assert "must differ" in r.data["error"]
    assert doc.Create.llamadas == []


def test_dos_puntos_exactos_y_host_existente(api, doc):
    r = _crear(api, doc, {"host_id": 165465, "points": [_sobre_el_muro(0.3, 3825)]})
    assert r.status == 400
    r = _crear(api, doc, {"host_id": 1, "points": [_sobre_el_muro(0.3, 3825), _sobre_el_muro(0.7, 5025)]})
    assert r.status == 404


# ---------------------------------------------------------------------------
# Simulacion y creacion
# ---------------------------------------------------------------------------
def test_simular_devuelve_la_situacion_del_hueco_en_el_muro(api, doc):
    r = _crear(api, doc, {"host_id": 165465, "simular": True,
                          "points": [_sobre_el_muro(0.3, 3825), _sobre_el_muro(0.7, 5025)]})
    assert r.status == 200, r.data
    assert r.data["simulado"] is True
    haria = r.data["haria"][0]
    assert haria["element_type"] == "wall_opening"
    hueco = haria["en_muro"]["hueco"]
    assert hueco["desde_mm"] == pytest.approx(0.3 * 21350.0, abs=2.0)
    assert hueco["hasta_mm"] == pytest.approx(0.7 * 21350.0, abs=2.0)
    assert hueco["z_desde_mm"] == 3825.0 and hueco["z_hasta_mm"] == 5025.0
    assert hueco["fuera_del_plano_mm"] < 1.0
    assert "sobresale" not in hueco
    assert haria["en_muro"]["muro"]["inicio_mm"]["x"] == pytest.approx(INICIO[0], abs=0.1)
    assert DB.Transaction.creadas == []
    assert doc.Create.llamadas == []


def test_crea_el_hueco_y_devuelve_el_rectangulo_de_revit(api, doc):
    a, b = _sobre_el_muro(0.3, 3825), _sobre_el_muro(0.7, 5025)
    r = _crear(api, doc, {"host_id": 165465, "points": [a, b]})
    assert r.status == 200, r.data
    assert r.data["ok"] is True and r.data["verificacion"]["coincide"] is True
    assert r.data["opening_id"] == 615578
    assert r.data["creados"][0]["id"] == 615578
    assert r.data["rectangulo_revit_mm"][0]["z"] == 3825.0
    assert r.data["rectangulo_revit_mm"][1]["x"] == pytest.approx(b["x"], abs=0.1)
    assert r.data["en_muro"]["hueco"]["z_hasta_mm"] == 5025.0
    assert r.data["host"]["id"] == 165465
    assert "avisos_revit" not in r.data
    assert r.data["message"] == "Created opening 615578 in host 165465"
    assert len(DB.Transaction.creadas) == 1
    assert DB.Transaction.creadas[0].nombre == u"IA: Crear hueco en 165465"
    assert DB.Transaction.creadas[0].estado == DB.TransactionStatus.Committed


def test_hueco_que_sobresale_por_arriba_se_crea_pero_se_avisa(api, doc):
    r = _crear(api, doc, {"host_id": 165465, "points": [_sobre_el_muro(0.3, 5000), _sobre_el_muro(0.7, 6500)]})
    assert r.status == 200, r.data
    assert r.data["ok"] is True
    assert r.data["en_muro"]["hueco"]["sobresale"] == ["height"]
    assert "partially" in r.data["en_muro"]["hueco"]["nota"]


def test_si_revit_borra_el_hueco_al_confirmar_se_explica_con_su_aviso(api, doc):
    # Lo que paso en Revit: NewOpening devuelve el hueco, Commit -> Committed, el
    # preprocesador ve el aviso y Revit resuelve borrando el elemento.
    DB.Transaction.fallos = [DB.FailureMessageFalso(
        "Rectangular opening doesn't cut its host.", DB.FailureSeverity.Warning, elementos=[615578])]
    DB.Transaction.al_confirmar = lambda t: doc.elementos.pop(615578, None)

    r = _crear(api, doc, {"host_id": 165465, "points": [_sobre_el_muro(0.3, 3825), _sobre_el_muro(0.7, 5025)]})
    assert r.status == 200, r.data
    assert r.data["ok"] is False
    assert r.data["count"] == 0 and r.data["creados"] == []
    assert r.data["opening_id"] == 615578
    detalle = r.data["verificacion"]["detalle"]
    assert "615578" in detalle and "Rectangular opening doesn't cut its host." in detalle
    assert r.data["avisos_revit"] == [{"texto": "Rectangular opening doesn't cut its host.", "elementos": [615578]}]
    assert r.data["rectangulo_revit_mm"][0]["z"] == 3825.0  # leido antes de confirmar
    assert "removed it when committing" in r.data["message"]
    assert DB.Transaction.creadas[0].estado == DB.TransactionStatus.Committed


def test_si_revit_borra_el_hueco_sin_avisar_se_dice(api, doc):
    DB.Transaction.al_confirmar = lambda t: doc.elementos.pop(615578, None)
    r = _crear(api, doc, {"host_id": 165465, "points": [_sobre_el_muro(0.3, 3825), _sobre_el_muro(0.7, 5025)]})
    assert r.status == 200 and r.data["ok"] is False
    assert "no dejo ningun aviso" in r.data["verificacion"]["detalle"]
    assert "avisos_revit" not in r.data


def test_error_de_revit_revierte_y_responde_500_con_el_motivo(api, doc):
    DB.Transaction.fallos = [DB.FailureMessageFalso(
        "Can't cut instance of Opening out of Wall.", DB.FailureSeverity.Error, elementos=[615578])]
    r = _crear(api, doc, {"host_id": 165465, "points": [_sobre_el_muro(0.3, 3825), _sobre_el_muro(0.7, 5025)]})
    assert r.status == 500, r.data
    assert "Can't cut instance of Opening out of Wall." in r.data["error"]
    assert DB.Transaction.creadas[0].estado == DB.TransactionStatus.RolledBack
