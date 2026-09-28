# -*- coding: utf-8 -*-
"""Bloque 3 de la consolidacion (0.4.0): macros propias del usuario.

  GET  /macros/      catalogo (manifiestos validos e invalidos con su error)
  POST /macros/run/  args que faltan/sobran/invalidos (400), 404 sin macro, `writes` con y sin
                     `simular` (transaccion "IA: Macro <nombre>", copia y log), lectura sin
                     transaccion, recarga por mtime, limite 200 sobre plan()["count"] y error
                     dentro de run() (500, transaccion revertida).

Las dos macros de ejemplo de herramientas-dev/macros-ejemplo/ se copian a una
carpeta temporal apuntada por REVIT_MCP_MACROS y se ejecutan sobre el modelo
simulado; ademas pasan la guarda de sintaxis IronPython 2.7.
"""
import io
import json
import os
import shutil
import time

import pytest
from pyrevit import DB, routes

import modelo_falso as mf
import seguridad
import macros_usuario

TOKEN = "9" * 64
BIP = DB.BuiltInParameter
BIC = DB.BuiltInCategory
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EJEMPLOS = os.path.join(RAIZ, "herramientas-dev", "macros-ejemplo")

LECTOR = u'''# -*- coding: UTF-8 -*-
from pyrevit import DB

def run(doc, uidoc, args, api):
    muros = list(DB.FilteredElementCollector(doc).OfCategory(DB.BuiltInCategory.OST_Walls).WhereElementIsNotElementType())
    api.log(u"{} muros".format(len(muros)))
    return {"muros": len(muros), "ids": [api.get_element_id_value(m) for m in muros], "version": VERSION}

VERSION = %d
'''

EXPLOTA = u'''# -*- coding: UTF-8 -*-
def plan(doc, args, api):
    return {"count": args.get("count", 1)}

def run(doc, uidoc, args, api):
    doc.elementos[10].LookupParameter(u"Comentarios").Set(u"a medias")
    raise RuntimeError("la macro fallo a mitad")
'''


def _escribir(ruta, texto):
    with io.open(ruta, "w", encoding="utf-8") as archivo:
        archivo.write(texto)


@pytest.fixture
def carpeta(tmp_path, monkeypatch):
    destino = tmp_path / "macros"
    destino.mkdir()
    for nombre in ("numerar_planos", "comentarios_por_nivel"):
        shutil.copytree(os.path.join(EJEMPLOS, nombre), str(destino / nombre))
    (destino / "rota").mkdir()
    _escribir(str(destino / "rota" / "macro.json"), u"{no es json")
    _escribir(str(destino / "rota" / "macro.py"), u"def run(doc, uidoc, args, api):\n    return 1\n")
    (destino / "sin_codigo").mkdir()
    _escribir(str(destino / "sin_codigo" / "macro.json"), u'{"name": "sin_codigo", "writes": false}')
    (destino / "lector").mkdir()
    _escribir(str(destino / "lector" / "macro.json"),
              u'{"name": "lector", "description": "cuenta muros", "version": "1", "args": {}, "writes": false, "timeout_s": 5}')
    _escribir(str(destino / "lector" / "macro.py"), LECTOR % 1)
    (destino / "explota").mkdir()
    _escribir(str(destino / "explota" / "macro.json"),
              u'{"name": "explota", "args": {"count": {"type": "int", "default": 1}}, "writes": true}')
    _escribir(str(destino / "explota" / "macro.py"), EXPLOTA)
    monkeypatch.setenv("REVIT_MCP_MACROS", str(destino))
    macros_usuario._modulos.clear()
    return destino


@pytest.fixture
def doc(tmp_path, monkeypatch):
    mf.activar_spec(monkeypatch)
    rvt = tmp_path / "Modelo.rvt"
    rvt.write_bytes(b"RVT")
    doc = mf.Doc(str(rvt))
    n1 = mf.Nivel(doc, 1, u"Nivel 1", 0)
    mf.Nivel(doc, 2, u"Nivel 2", 3000)
    for identificador, nivel_id, comentario in ((10, 1, u""), (11, 1, u"ya escrito"), (12, 2, u"")):
        mf.Muro(doc, identificador, nombre=u"Genérico - 200 mm", categoria=u"Muros", bic=BIC.OST_Walls, nivel_id=nivel_id,
                parametros=[mf.texto(u"Comentarios", comentario, bip=BIP.ALL_MODEL_INSTANCE_COMMENTS),
                            mf.texto(u"Marca", u"M-{}".format(identificador), bip=BIP.ALL_MODEL_MARK)])
    mf.Plano(doc, 500, u"A-10", u"Planta baja")
    mf.Plano(doc, 501, u"A-11", u"Planta primera")
    mf.Plano(doc, 502, u"A-12", u"Alzados")
    doc.ActiveView = mf.VistaPlanta(doc, 100, u"Planta Nivel 1", nivel=n1)
    return doc


@pytest.fixture
def api():
    seguridad.establecer_token(TOKEN)
    DB.Transaction.creadas = []
    DB.Transaction.resultado_commit = DB.TransactionStatus.Committed
    api = routes.API("revit_mcp")
    from macros_usuario import register_macros_usuario_routes

    register_macros_usuario_routes(api)
    return api


def _post(api, ruta, doc, cuerpo, con_token=True):
    datos = dict(cuerpo)
    if con_token:
        datos["token"] = TOKEN
    return api.rutas[(ruta, "POST")](doc=doc, uidoc=None, request=routes.Request(path=ruta, data=datos))


def _get(api, ruta, doc, con_token=True):
    consulta = {"token": TOKEN} if con_token else {}
    return api.rutas[(ruta, "GET")](doc=doc, request=routes.Request(path=ruta, method="GET", query_params=consulta))


def _log(tmp_path):
    with io.open(str(tmp_path / "mcp_log.jsonl"), encoding="utf-8") as archivo:
        return [json.loads(l) for l in archivo.read().splitlines() if l.strip()]


# ---------------------------------------------------------------------------
def test_catalogo_lista_validas_e_invalidas(api, doc, carpeta):
    assert _get(api, "/macros/", doc, con_token=False).status == 401
    r = _get(api, "/macros/", doc)
    assert r.status == 200, r.data
    datos = r.data
    assert datos["carpeta"] == str(carpeta) and datos["count"] == 4
    nombres = [m["name"] for m in datos["macros"]]
    assert nombres == ["comentarios_por_nivel", "explota", "lector", "numerar_planos"]
    numerar = [m for m in datos["macros"] if m["name"] == "numerar_planos"][0]
    assert numerar["writes"] is True and numerar["timeout_s"] == 120.0 and u"prefijo" in numerar["description"].lower()
    assert numerar["args"]["prefix"] == {"type": "str", "required": True, "default": None, "description": u'Prefijo del número de plano, p. ej. "E-"'}
    assert numerar["args"]["sheet_ids"]["type"] == "element_ids" and numerar["args"]["start"]["default"] == 1
    lector = [m for m in datos["macros"] if m["name"] == "lector"][0]
    assert lector["writes"] is False and lector["timeout_s"] == 5.0
    invalidas = dict((i["name"], i["error"]) for i in datos["invalidas"])
    assert "no es JSON valido" in invalidas["rota"] and invalidas["sin_codigo"] == "falta macro.py"


def test_run_errores_de_nombre_y_argumentos(api, doc, carpeta):
    assert _post(api, "/macros/run/", doc, {"name": "lector"}, con_token=False).status == 401
    assert _post(api, "/macros/run/", None, {"name": "lector"}).status == 503
    r = _post(api, "/macros/run/", doc, {})
    assert r.status == 400 and "name is required" in r.data["error"]
    r = _post(api, "/macros/run/", doc, {"name": "no_existe"})
    assert r.status == 404 and r.data["available_macros"] == ["comentarios_por_nivel", "explota", "lector", "numerar_planos"]
    r = _post(api, "/macros/run/", doc, {"name": "rota"})
    assert r.status == 400 and "invalid manifest" in r.data["error"]
    r = _post(api, "/macros/run/", doc, {"name": "numerar_planos", "args": {"step": "dos", "otro": 1}})
    assert r.status == 400, r.data
    assert r.data["faltan"] == ["prefix"] and r.data["sobran"] == ["otro"]
    assert r.data["invalidos"]["step"].startswith("int:") and "args_spec" in r.data
    r = _post(api, "/macros/run/", doc, {"name": "numerar_planos", "args": {"prefix": "E-", "sheet_ids": [500, 999]}})
    assert r.status == 400 and "element 999 not found" in r.data["invalidos"]["sheet_ids"]
    r = _post(api, "/macros/run/", doc, {"name": "comentarios_por_nivel", "args": {"category": "OST_Walls", "level": u"Sótano"}})
    assert r.status == 400 and "Nivel 1, Nivel 2" in r.data["invalidos"]["level"]
    r = _post(api, "/macros/run/", doc, {"name": "lector", "args": "prefix"})
    assert r.status == 400
    assert DB.Transaction.creadas == []


def test_numerar_planos_simular_y_real(api, doc, carpeta, tmp_path):
    cuerpo = {"name": "numerar_planos", "args": {"prefix": "E-", "start": 1, "step": 1}}
    r = _post(api, "/macros/run/", doc, dict(cuerpo, simular=True))
    assert r.status == 200, r.data
    datos = r.data
    assert datos["simulado"] is True and "copia" not in datos and DB.Transaction.creadas == []
    assert datos["haria"][0] == {"accion": "macro", "name": "numerar_planos", "args": {"prefix": "E-", "start": 1, "step": 1, "digits": 2, "sheet_ids": []},
                                 "writes": True, "undo_name": u"IA: Macro numerar_planos"}
    assert datos["plan"]["count"] == 3
    assert [(c["antes"], c["despues"]) for c in datos["plan"]["cambios"]] == [(u"A-10", u"E-01"), (u"A-11", u"E-02"), (u"A-12", u"E-03")]
    assert doc.elementos[500].SheetNumber == u"A-10"
    assert _log(tmp_path)[-1]["ruta"] == "/macros/run/" and _log(tmp_path)[-1]["simulado"] is True

    r = _post(api, "/macros/run/", doc, dict(cuerpo, args={"prefix": "E-", "start": 10, "step": 5, "digits": 3}))
    assert r.status == 200, r.data
    datos = r.data
    assert datos["ok"] is True and datos["writes"] is True and datos["name"] == "numerar_planos"
    assert [t.nombre for t in DB.Transaction.creadas] == [u"IA: Macro numerar_planos"]
    assert DB.Transaction.creadas[0].estado == DB.TransactionStatus.Committed
    assert [doc.elementos[i].SheetNumber for i in (500, 501, 502)] == [u"E-010", u"E-015", u"E-020"]
    assert datos["antes"] == {"500": u"A-10", "501": u"A-11", "502": u"A-12"}
    assert datos["despues"] == {"500": u"E-010", "501": u"E-015", "502": u"E-020"}
    assert datos["result"] == {"count": 3} and datos["output"].splitlines()[0] == u"500: A-10 -> E-010"
    assert datos["copia"]["ruta"].endswith(".rvt") and datos["ms"] >= 0
    entrada = _log(tmp_path)[-1]
    assert entrada["ok"] is True and entrada["args"]["name"] == "numerar_planos" and "token" not in entrada["args"]
    # solo algunos planos, en el orden dado
    r = _post(api, "/macros/run/", doc, {"name": "numerar_planos", "args": {"prefix": "S-", "sheet_ids": [502, 500]}})
    assert r.status == 200 and doc.elementos[502].SheetNumber == u"S-01" and doc.elementos[500].SheetNumber == u"S-02"
    assert doc.elementos[501].SheetNumber == u"E-015"


def test_comentarios_por_nivel(api, doc, carpeta):
    cuerpo = {"name": "comentarios_por_nivel", "args": {"category": "OST_Walls"}}
    r = _post(api, "/macros/run/", doc, dict(cuerpo, simular=True))
    assert r.status == 200, r.data
    plan = r.data["plan"]
    assert plan["count"] == 2 and plan["parameter"] == "Comments"
    assert sorted((m["id"], m["despues"]) for m in plan["muestra"]) == [(10, u"Nivel 1"), (12, u"Nivel 2")]
    r = _post(api, "/macros/run/", doc, cuerpo)
    assert r.status == 200, r.data
    assert r.data["despues"] == {"10": u"Nivel 1", "12": u"Nivel 2"} and r.data["antes"] == {"10": u"", "12": u""}
    assert doc.elementos[11].LookupParameter(u"Comentarios").AsString() == u"ya escrito"
    assert r.data["output"] == u"2 elementos con Comments = nivel"
    r = _post(api, "/macros/run/", doc, {"name": "comentarios_por_nivel", "args": {"category": "OST_Walls", "overwrite": True, "level": u"Nivel 1"}})
    assert r.status == 200 and r.data["despues"] == {"11": u"Nivel 1"}
    r = _post(api, "/macros/run/", doc, {"name": "comentarios_por_nivel", "args": {"category": "Muros"}})
    assert r.status == 500 and "BuiltInCategory" in r.data["error"]


def test_macro_de_lectura_sin_transaccion_ni_copia(api, doc, carpeta, tmp_path):
    r = _post(api, "/macros/run/", doc, {"name": "lector"})
    assert r.status == 200, r.data
    datos = r.data
    assert datos["writes"] is False and datos["result"] == {"muros": 3, "ids": [10, 11, 12], "version": 1}
    assert datos["output"] == u"3 muros" and "copia" not in datos and datos["ok"] is True and "ms" in datos
    assert DB.Transaction.creadas == [] and not (tmp_path / "backups").exists() and not (tmp_path / "mcp_log.jsonl").exists()
    r = _post(api, "/macros/run/", doc, {"name": "lector", "simular": True})
    assert r.status == 200 and r.data["simulado"] is True and r.data["plan"] is None and "no define plan" in r.data["nota"]


def test_recarga_por_mtime(api, doc, carpeta):
    r = _post(api, "/macros/run/", doc, {"name": "lector"})
    assert r.data["result"]["version"] == 1 and r.data["reloaded"] is False
    r = _post(api, "/macros/run/", doc, {"name": "lector"})
    assert r.data["result"]["version"] == 1 and r.data["reloaded"] is False
    ruta = str(carpeta / "lector" / "macro.py")
    _escribir(ruta, LECTOR % 2)
    marca = os.path.getmtime(ruta) + 10
    os.utime(ruta, (marca, marca))
    r = _post(api, "/macros/run/", doc, {"name": "lector"})
    assert r.data["result"]["version"] == 2 and r.data["reloaded"] is True
    # un macro.py que no importa responde 400 con el error, no 500
    _escribir(ruta, u"def run(doc, uidoc, args, api:\n    return 1\n")
    os.utime(ruta, (marca + 10, marca + 10))
    r = _post(api, "/macros/run/", doc, {"name": "lector"})
    assert r.status == 400 and "could not be loaded" in r.data["error"] and "SyntaxError" in r.data["error"]
    _escribir(ruta, u"VALOR = 1\n")
    os.utime(ruta, (marca + 20, marca + 20))
    r = _post(api, "/macros/run/", doc, {"name": "lector"})
    assert r.status == 400 and "does not define run" in r.data["error"]


def test_error_dentro_de_run_revierte_y_500(api, doc, carpeta):
    r = _post(api, "/macros/run/", doc, {"name": "explota"})
    assert r.status == 500 and "la macro fallo a mitad" in r.data["error"]
    assert DB.Transaction.creadas[0].nombre == u"IA: Macro explota"
    assert DB.Transaction.creadas[0].estado == DB.TransactionStatus.RolledBack and doc.IsModifiable is False
    assert "copia" in r.data


def test_limite_200_sobre_el_plan(api, doc, carpeta):
    r = _post(api, "/macros/run/", doc, {"name": "explota", "args": {"count": 500}, "simular": True})
    assert r.status == 400 and r.data["limite"] == 200 and r.data["cantidad"] == 500 and DB.Transaction.creadas == []
    r = _post(api, "/macros/run/", doc, {"name": "explota", "args": {"count": 500}, "simular": True, "forzar": True})
    assert r.status == 200 and r.data["plan"] == {"count": 500}


def test_carpeta_por_defecto_y_sin_carpeta(monkeypatch, tmp_path):
    monkeypatch.delenv("REVIT_MCP_MACROS", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert macros_usuario.carpeta_macros() == os.path.join(str(tmp_path), "RevitMcp", "macros")
    assert macros_usuario.catalogo() == ([], [])


def test_manifiesto_rechaza_tipos_y_nombres_raros(tmp_path):
    carpeta = tmp_path / "mala macro"
    carpeta.mkdir()
    _escribir(str(carpeta / "macro.json"), u'{"name": "mala macro", "args": {"x": {"type": "date"}}}')
    _escribir(str(carpeta / "macro.py"), u"def run(doc, uidoc, args, api):\n    return 1\n")
    with pytest.raises(ValueError) as info:
        macros_usuario.leer_manifiesto(str(carpeta))
    assert "no valido" in str(info.value)
    carpeta2 = tmp_path / "otra"
    carpeta2.mkdir()
    _escribir(str(carpeta2 / "macro.json"), u'{"name": "otra", "args": {"x": {"type": "date"}}}')
    _escribir(str(carpeta2 / "macro.py"), u"def run(doc, uidoc, args, api):\n    return 1\n")
    with pytest.raises(ValueError) as info:
        macros_usuario.leer_manifiesto(str(carpeta2))
    assert "type 'date' no admitido" in str(info.value)
    _escribir(str(carpeta2 / "macro.json"), u'{"name": "distinto"}')
    with pytest.raises(ValueError) as info:
        macros_usuario.leer_manifiesto(str(carpeta2))
    assert "no coincide con la carpeta" in str(info.value)


def test_interpretar_resultado_ids_y_diccionarios(doc):
    salida = macros_usuario.interpretar_resultado(doc, [10, 11])
    assert salida["ok"] is True and salida["count"] == 2 and salida["result"] == [10, 11]
    salida = macros_usuario.interpretar_resultado(doc, {"creados": [doc.elementos[10]], "eliminados": [999], "antes": {"a": 1}, "extra": DB.ElementId(5)})
    assert salida["count"] == 1 and salida["eliminados"] == [999] and salida["antes"] == {"a": 1} and salida["result"] == {"extra": 5}
    salida = macros_usuario.interpretar_resultado(doc, {"eliminados": [10]})
    assert salida["ok"] is False and "siguen existiendo" in salida["verificacion"]["detalle"]
    assert macros_usuario.interpretar_resultado(doc, u"hola") == {"result": u"hola"}


@pytest.mark.parametrize("nombre", ["numerar_planos", "comentarios_por_nivel"])
def test_macros_de_ejemplo_son_ironpython_27(nombre):
    from test_compatibilidad_ironpython import _problemas

    assert _problemas(os.path.join(EJEMPLOS, nombre, "macro.py")) == []


def test_macro_que_escribe_sin_plan_exige_forzar(api, doc, carpeta):
    (carpeta / "sin_plan").mkdir()
    _escribir(str(carpeta / "sin_plan" / "macro.json"), u'{"name": "sin_plan", "args": {}, "writes": true}')
    _escribir(str(carpeta / "sin_plan" / "macro.py"),
              u"def run(doc, uidoc, args, api):\n    doc.elementos[10].LookupParameter(u'Comentarios').Set(u'sin plan')\n    return {}\n")
    macros_usuario._modulos.clear()
    r = _post(api, "/macros/run/", doc, {"name": "sin_plan", "simular": True})
    assert r.status == 200 and r.data["plan"] is None and "no define plan()" in r.data["nota"]
    r = _post(api, "/macros/run/", doc, {"name": "sin_plan"})
    assert r.status == 400 and r.data["plan_required"] is True and "forzar" in r.data["error"]
    assert DB.Transaction.creadas == []
    r = _post(api, "/macros/run/", doc, {"name": "sin_plan", "forzar": True})
    assert r.status == 200, r.data
    assert doc.elementos[10].LookupParameter(u"Comentarios").AsString() == u"sin plan"
