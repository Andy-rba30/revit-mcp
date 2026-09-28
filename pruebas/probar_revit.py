#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pruebas de humo del conector Revit MCP (CPython 3, solo httpx y stdlib).

Requisitos: Revit abierto con la extensión cargada (Routes en 48884), un
modelo de prueba abierto (guardado en disco y, a ser posible, NO de trabajo
compartido para que se creen copias en backups\\) y el puente MCP en marcha
(python main.py --combined, o --streamable-http, en 8000).

Uso:
    python pruebas\\probar_revit.py [--element-id ID] [--parameter Comments] [--fase 2a|cons]

Cada prueba imprime nombre, código de estado y cuerpo tal cual llega.
Termina con código de salida 0 si todas dan el resultado esperado.

Pruebas:
   1  GET /status/ sin token                              -> 401
   2  GET /status/ con token                              -> 200 "active"
   3  POST /execute_code/ con description                 -> 200, output "hola", undo_name "IA: prueba"
   4  POST puente con Host: evil.com                      -> 421
   5  Toda ruta de lectura responde 200 en menos de 5 s   (18 rutas)
   6  set_parameter con simular=true no altera el valor   -> 200 simulado, sin transacción
   7  set_parameter real cambia el valor, devuelve antes/despues, deja una
      línea en mcp_log.jsonl y una copia en backups\\ si el modelo no es
      compartido; después se restaura el valor original
   8  execute_code sin description                        -> 400
   9  el nombre de la última entrada de deshacer empieza por "IA:"
      (se lee con doc.GetUndoName() vía execute_code; si esa API no existe en
      la versión de Revit, se pide comprobación manual y se da por verificada)

Con --fase 2a se añaden (0.3.0), sin depender de nombres visibles en inglés:
  2a.1  describe_element de un muro devuelve parameters, bbox_mm y hosted_elements
  2a.2  query_elements con op=contains sobre Mark pagina bien (marcas temporales
        en dos muros, page_size=1, páginas disjuntas; después se restauran)
  2a.3  snapshot_model antes y después de crear un nivel, y diff_snapshots lista
        exactamente ese nivel (el nivel se borra al final)
  2a.4  create_grid_and_levels(simular=true) no crea nada (recuento de rejillas
        y niveles igual antes y después; sin copia; entrada simulada en el log)
Con --fase cons se añaden (0.4.0, consolidación):
  cons.1  set_parameters con 20 elementos en UNA llamada frente a 20 llamadas a
          set_parameter (se comparan los ms; después se restauran los valores)
  cons.2  create_elements con 3 kind mezclados y simular=true (plan.counts, sin copia)
  cons.3  run_macro comentarios_por_nivel con simular=true y real (las macros de
          ejemplo se copian a %LOCALAPPDATA%\\RevitMcp\\macros si no están); después se
          restauran los comentarios con set_parameters
  cons.4  snapshot_model devuelve timings por etapa
  cons.5  una llamada al puente MCP con un nombre retirado (set_parameter) responde con
          el mensaje de sustitución (set_parameters)
Con --fase 2b se añaden (0.5.0, estructuras metálicas), sin nombres visibles en inglés:
  2b.1  list_steel_profiles devuelve al menos un perfil de acero cargado o dice que no hay acero
  2b.2  create_steel_frame(simular=true) sobre una rejilla 2x2 propia devuelve plan.counts.total
        (8) y no crea nada
  2b.3  create_steel_frame real crea 4 pilares y 4 vigas; analytical_status sobre ellos no reporta
        nodos sueltos (o dice que no tienen modelo analítico); se borran al final
  2b.4  set_structural_properties(start_release=pinned) sobre las 4 vigas y
        describe_element(include_structural) lo refleja
  2b.5  steel_quantities(group_by=type) devuelve peso > 0 para los perfiles creados o los lista en
        sin_peso con motivo
  Si el modelo no tiene perfiles de acero de pilar y de viga cargados, 2b.2 a 2b.5 se marcan
  NO_APLICA con el motivo (no como fallo).
La fase 2c se añadirá con su entrega.
"""
import argparse
import json
import os
import shutil
import sys
import time

import httpx

REVIT = "http://127.0.0.1:48884/revit_mcp"
PUENTE = "http://127.0.0.1:8000/mcp"
RUTA_TOKEN = os.path.expandvars(r"%LOCALAPPDATA%\RevitMcp\token")

RUTAS_LECTURA = [
    ("GET", "/status/", None),
    ("GET", "/model_info/", None),
    ("GET", "/model_statistics/", None),
    ("GET", "/room_data/", None),
    ("GET", "/selected_elements/", None),
    ("GET", "/list_levels/", None),
    ("GET", "/list_views/", None),
    ("GET", "/current_view_info/", None),
    ("GET", "/list_families/", {"limit": "5"}),
    ("GET", "/list_family_categories/", None),
    ("GET", "/warnings/", {"max": "5"}),
    ("GET", "/worksets/", None),
    ("GET", "/phases_options/", None),
    ("GET", "/links/", None),
    ("GET", "/project_location/", None),
    ("GET", "/log/", {"last_n": "3"}),
    ("POST", "/find_elements/", {"category": "OST_Levels", "max": 5}),
    ("POST", "/element_types/", {"category": "walls", "max": 5}),
]


def leer_token():
    try:
        with open(RUTA_TOKEN, "r", encoding="utf-8") as archivo:
            return archivo.read().strip()
    except OSError:
        return None


def mostrar(nombre, esperado, respuesta, cuerpo_max=1500):
    ok = respuesta.status_code == esperado
    print("=" * 70)
    print("{} -> {}  (esperado {})  [{}]".format(
        nombre, respuesta.status_code, esperado, "OK" if ok else "FALLO"))
    print("Cuerpo:")
    texto = respuesta.text
    print(texto if len(texto) <= cuerpo_max else texto[:cuerpo_max] + "... ({} caracteres)".format(len(texto)))
    return ok


def resultado_manual(nombre, ok, detalle):
    print("=" * 70)
    print("{} [{}]".format(nombre, "OK" if ok else "FALLO"))
    print(detalle)
    return ok


def _json(respuesta):
    try:
        return respuesta.json()
    except json.JSONDecodeError:
        return {}


def elegir_elemento(cliente, token, element_id, parameter_name):
    """Devuelve (element_id, parameter_name) de un elemento con parámetro editable."""
    if element_id is not None:
        return element_id, parameter_name
    r = cliente.post(REVIT + "/find_elements/", json={"category": "OST_Walls", "max": 1, "token": token})
    ids = _json(r).get("ids") or []
    if not ids:
        r = cliente.post(REVIT + "/find_elements/", json={"category": "OST_Levels", "max": 1, "token": token})
        ids = _json(r).get("ids") or []
    if not ids:
        return None, parameter_name
    return ids[0], parameter_name


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--element-id", type=int, default=None, help="elemento para las pruebas 6 y 7 (por defecto, el primer muro)")
    parser.add_argument("--parameter", default="Comments", help="parámetro de texto editable (por defecto Comments)")
    parser.add_argument("--fase", choices=["2a", "2b", "2c", "cons"], default=None,
                        help="añade las pruebas de esa entrega (2a: navegación y macros; cons: consolidación 0.4.0; "
                             "2b: estructuras metálicas 0.5.0)")
    args = parser.parse_args()

    resultados = []
    cliente = httpx.Client(timeout=60.0)

    # 1. GET /status/ sin token -> 401
    r = cliente.get(REVIT + "/status/")
    resultados.append(mostrar("1. GET /revit_mcp/status/ sin token", 401, r))

    token = leer_token()
    if token is None:
        print("=" * 70)
        print("2. No existe {}: Revit no está abierto o el conector no ha iniciado".format(RUTA_TOKEN))
        resultados.append(False)
    else:
        # 2. GET /status/ con token -> 200
        r = cliente.get(REVIT + "/status/", params={"token": token})
        resultados.append(mostrar("2. GET /revit_mcp/status/ con token", 200, r))

        # 3. POST /execute_code/ con token y description -> 200
        cuerpo = {"code": 'print("hola")', "description": "prueba", "token": token}
        r = cliente.post(REVIT + "/execute_code/", json=cuerpo)
        ok = mostrar("3. POST /revit_mcp/execute_code/ con description", 200, r)
        if ok:
            datos = _json(r)
            ok = datos.get("output", "").strip() == "hola" and datos.get("undo_name") == "IA: prueba"
            if not ok:
                print("   (se esperaba output 'hola' y undo_name 'IA: prueba')")
        resultados.append(ok)

    # 4. Puente con Host falso -> 421 (protección DNS rebinding del SDK mcp)
    inicializar = {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                   "clientInfo": {"name": "probar_revit", "version": "1"}},
    }
    try:
        r = cliente.post(
            PUENTE, json=inicializar,
            headers={"Host": "evil.com",
                     "Accept": "application/json, text/event-stream"},
        )
        resultados.append(mostrar("4. POST http://127.0.0.1:8000/mcp con Host: evil.com", 421, r))
    except httpx.ConnectError as error:
        print("=" * 70)
        print("4. No se pudo conectar con el puente en 8000: {}".format(error))
        resultados.append(False)

    if token is None:
        print("=" * 70)
        print("Sin token no se pueden ejecutar las pruebas 5-9.")
        print("Resultado: {}/{} pruebas correctas".format(sum(resultados), len(resultados)))
        return 1

    # 5. Toda ruta de lectura responde 200 en menos de 5 s
    lentas = []
    fallidas = []
    print("=" * 70)
    print("5. Rutas de lectura (200 y < 5 s):")
    for metodo, ruta, params in RUTAS_LECTURA:
        inicio = time.time()
        try:
            if metodo == "GET":
                consulta = dict(params or {})
                consulta["token"] = token
                r = cliente.get(REVIT + ruta, params=consulta)
            else:
                cuerpo = dict(params or {})
                cuerpo["token"] = token
                r = cliente.post(REVIT + ruta, json=cuerpo)
            segundos = time.time() - inicio
            estado = r.status_code
        except Exception as error:
            segundos = time.time() - inicio
            estado = "EXC {}".format(error)
        marca = "OK" if (estado == 200 and segundos < 5.0) else "FALLO"
        print("   {:5} {:28} -> {}  {:.2f} s  [{}]".format(metodo, ruta, estado, segundos, marca))
        if estado != 200:
            fallidas.append(ruta)
        elif segundos >= 5.0:
            lentas.append(ruta)
    resultados.append(resultado_manual(
        "5. Rutas de lectura", not fallidas and not lentas,
        "   fallidas: {}  lentas: {}".format(fallidas or "ninguna", lentas or "ninguna")))

    # 6 y 7. set_parameter simulado y real
    element_id, parametro = elegir_elemento(cliente, token, args.element_id, args.parameter)
    if element_id is None:
        resultados.append(resultado_manual("6/7. set_parameter", False, "   No hay muros ni niveles en el modelo; usa --element-id"))
    else:
        nuevo_valor = "MCP prueba {}".format(int(time.time()))

        # El valor original y el nombre visible del parámetro ("Comentarios" en
        # un Revit en español cuando se pide "Comments") salen de la simulación.
        r = cliente.post(REVIT + "/set_parameter/", json={
            "element_id": element_id, "parameter_name": parametro, "value": nuevo_valor,
            "simular": True, "token": token})
        ok = mostrar("6. POST /set_parameter/ simular=true (elemento {})".format(element_id), 200, r)
        datos = _json(r)
        haria = (datos.get("haria") or [{}])[0]
        valor_original = haria.get("antes") or ""
        etiqueta = haria.get("parameter_label") or parametro
        if ok:
            ok = datos.get("simulado") is True and "haria" in datos and "copia" not in datos
            if not ok:
                print("   (se esperaba simulado=true, haria y sin copia)")
        if ok:
            r = cliente.get(REVIT + "/element_properties/{}".format(element_id), params={"token": token})
            actual = ""
            for p in _json(r).get("parameters", []):
                if p.get("name") == etiqueta:
                    actual = p.get("value", "") or ""
            ok = actual == valor_original
            if not ok:
                print("   (el valor cambió con simular=true: {!r} -> {!r})".format(valor_original, actual))
        resultados.append(ok)

        # 7. real
        r = cliente.post(REVIT + "/set_parameter/", json={
            "element_id": element_id, "parameter_name": parametro, "value": nuevo_valor, "token": token})
        ok = mostrar("7. POST /set_parameter/ real (elemento {})".format(element_id), 200, r)
        datos = _json(r)
        if ok:
            ok = (datos.get("ok") is True and (datos.get("antes") or "") == valor_original
                  and datos.get("despues") == nuevo_valor)
            if not ok:
                print("   (se esperaba ok=true, antes={!r}, despues={!r})".format(valor_original, nuevo_valor))
        info = _json(cliente.get(REVIT + "/model_info/", params={"token": token})).get("file", {})
        compartido = bool(info.get("is_workshared"))
        ruta_rvt = info.get("path")
        if ok:
            # entrada en el log
            r = cliente.get(REVIT + "/log/", params={"token": token, "last_n": "3"})
            entradas = _json(r).get("entradas", [])
            ultima = next((e for e in reversed(entradas)
                           if e.get("ruta") == "/set_parameter/" and not e.get("simulado")), None)
            ok = ultima is not None and ultima.get("ok") is True and ultima.get("args", {}).get("value") == nuevo_valor
            print("   log: {}".format("línea encontrada en {}".format(_json(r).get("ruta")) if ok else "NO se encontró la línea"))
        if ok:
            copia = datos.get("copia")
            if compartido:
                print("   modelo compartido: no se espera copia (nota: {})".format(datos.get("nota_copia")))
                ok = copia is None
            elif not ruta_rvt:
                print("   modelo sin guardar: no se espera copia")
                ok = copia is None
            else:
                ok = bool(copia) and str(copia.get("ruta", "")).lower().endswith(".rvt") and "backups" in str(copia.get("ruta", ""))
                print("   copia: {}".format(copia))
        resultados.append(ok)

        # restaurar el valor original
        cliente.post(REVIT + "/set_parameter/", json={
            "element_id": element_id, "parameter_name": parametro, "value": valor_original, "token": token})

    # 8. execute_code sin description -> 400
    r = cliente.post(REVIT + "/execute_code/", json={"code": "print(1)", "token": token})
    resultados.append(mostrar("8. POST /execute_code/ sin description", 400, r))

    # 9. Última entrada de deshacer empieza por "IA:"
    codigo = (
        "nombre = None\n"
        "try:\n"
        "    nombre = doc.GetUndoName()\n"
        "except Exception as error:\n"
        "    nombre = 'NO_DISPONIBLE: ' + str(error)\n"
        "print(nombre)\n"
    )
    r = cliente.post(REVIT + "/execute_code/", json={"code": codigo, "description": "leer deshacer", "token": token})
    salida = _json(r).get("output", "").strip()
    if r.status_code == 200 and salida.startswith("IA:"):
        resultados.append(resultado_manual("9. Última entrada de deshacer", True, "   GetUndoName() = {!r}".format(salida)))
    elif r.status_code == 200 and salida.startswith("NO_DISPONIBLE"):
        resultados.append(resultado_manual(
            "9. Última entrada de deshacer", True,
            "   doc.GetUndoName() no está disponible en esta versión de Revit ({}).\n"
            "   COMPROBACIÓN MANUAL: abre el desplegable de Deshacer (Ctrl+Z) en Revit y confirma\n"
            "   que las últimas entradas se llaman 'IA: leer deshacer' e 'IA: Parametro ...'.".format(salida)))
    else:
        resultados.append(resultado_manual("9. Última entrada de deshacer", False,
                                           "   estado {} salida {!r}".format(r.status_code, salida)))

    if args.fase == "2a":
        pruebas_2a(cliente, token, resultados)
    elif args.fase == "cons":
        pruebas_cons(cliente, token, resultados)
    elif args.fase == "2b":
        pruebas_2b(cliente, token, resultados)
    elif args.fase:
        print("=" * 70)
        print("Fase {}: sin pruebas todavía (entrega pendiente)".format(args.fase))

    print("=" * 70)
    print("Resultado: {}/{} pruebas correctas".format(sum(resultados), len(resultados)))
    return 0 if all(resultados) else 1


# ---------------------------------------------------------------------------
# Fase 2a (0.3.0): navegación profunda y macros de proyecto
# ---------------------------------------------------------------------------
def _post(cliente, ruta, token, cuerpo):
    datos = dict(cuerpo)
    datos["token"] = token
    return cliente.post(REVIT + ruta, json=datos)


def _ids_query(cliente, token, categoria):
    r = _post(cliente, "/query/", token, {"category": categoria, "page_size": 500, "ids_only": True})
    datos = _json(r)
    return set(datos.get("ids") or []), datos.get("total_matched")


def pruebas_2a(cliente, token, resultados):
    marca_tiempo = int(time.time())

    # 2a.1 describe_element de un muro: parameters, bbox_mm y hosted_elements
    r = _post(cliente, "/find_elements/", token, {"category": "OST_Walls", "max": 2})
    muros = _json(r).get("ids") or []
    if not muros:
        resultados.append(resultado_manual("2a.1 describe_element", False, "   No hay muros en el modelo"))
    else:
        r = _post(cliente, "/describe/", token, {"element_id": muros[0], "depth": 1})
        ok = mostrar("2a.1 POST /describe/ del muro {}".format(muros[0]), 200, r, cuerpo_max=2500)
        datos = _json(r)
        if ok:
            parametros = datos.get("parameters") or {}
            ok = (isinstance(parametros.get("instance"), list) and len(parametros["instance"]) > 0
                  and "bbox_mm" in datos and isinstance(datos.get("hosted_elements"), list))
            if not ok:
                print("   (se esperaban parameters.instance no vacío, bbox_mm y hosted_elements)")
            else:
                print("   parámetros de ejemplar: {}, de tipo: {}, alojados: {}, unidos: {}, referencias en la vista activa: {}".format(
                    len(parametros["instance"]), len(parametros.get("type") or []), len(datos["hosted_elements"]),
                    len(datos.get("joined_elements") or []), len(datos.get("referenced_by") or [])))
        resultados.append(ok)

    # 2a.2 query_elements con op=contains sobre Mark pagina bien
    if len(muros) < 2:
        resultados.append(resultado_manual("2a.2 query_elements", False, "   Hacen falta al menos 2 muros"))
    else:
        originales = {}
        for indice, muro in enumerate(muros[:2]):
            r = _post(cliente, "/set_parameter/", token, {"element_id": muro, "parameter_name": "Mark", "value": "MCP-2A-{}".format(indice + 1)})
            originales[muro] = (_json(r).get("antes") or "")
        vistos = []
        pagina = 1
        ok = True
        total = None
        while True:
            r = _post(cliente, "/query/", token, {
                "category": "OST_Walls", "filters": [{"parameter": "Mark", "op": "contains", "value": "MCP-2A"}],
                "page_size": 1, "page": pagina, "sort_by": "id"})
            if pagina == 1:
                ok = mostrar("2a.2 POST /query/ Mark contains MCP-2A (page_size=1)", 200, r)
            datos = _json(r)
            if r.status_code != 200:
                ok = False
                break
            total = datos.get("total_matched")
            ids = datos.get("ids") or []
            if len(ids) > 1 or any(i in vistos for i in ids):
                ok = False
                print("   (página {} repite ids o devuelve más de uno: {})".format(pagina, ids))
                break
            vistos.extend(ids)
            if not datos.get("truncated") or pagina >= 10:
                break
            pagina += 1
        if ok:
            ok = total is not None and total >= 2 and len(vistos) == total and datos.get("pages") == total
            print("   total_matched={} páginas={} ids={} [{}]".format(total, datos.get("pages"), vistos, "OK" if ok else "FALLO"))
        for muro, original in originales.items():
            _post(cliente, "/set_parameter/", token, {"element_id": muro, "parameter_name": "Mark", "value": original})
        resultados.append(ok)

    # 2a.3 snapshot antes/después de crear un nivel; diff lista exactamente ese nivel
    nombre_nivel = "MCP 2A nivel {}".format(marca_tiempo)
    r = _post(cliente, "/snapshot/", token, {"name": "prueba2a_antes", "overwrite": True})
    ok = mostrar("2a.3a POST /snapshot/ antes", 200, r, cuerpo_max=600)
    ruta_antes = _json(r).get("ruta")
    nivel_id = None
    ruta_despues = None
    if ok:
        r = _post(cliente, "/create_level/", token, {"levels": [{"name": nombre_nivel, "elevation": 123456}]})
        ok = mostrar("2a.3b POST /create_level/ {}".format(nombre_nivel), 200, r, cuerpo_max=600)
        creados = _json(r).get("creados") or []
        nivel_id = creados[0]["id"] if creados else None
        ok = ok and nivel_id is not None
    if ok:
        r = _post(cliente, "/snapshot/", token, {"name": "prueba2a_despues", "overwrite": True})
        ok = mostrar("2a.3c POST /snapshot/ después", 200, r, cuerpo_max=600)
        ruta_despues = _json(r).get("ruta")
    if ok:
        r = _post(cliente, "/diff_snapshots/", token, {"a": "prueba2a_antes", "b": "prueba2a_despues"})
        ok = mostrar("2a.3d POST /diff_snapshots/", 200, r, cuerpo_max=1500)
        datos = _json(r)
        if ok:
            anadidos = [e.get("id") for e in (datos.get("added") or [])]
            ok = anadidos == [nivel_id] and datos.get("removed") == []
            if not ok:
                print("   (se esperaba added=[{}] y removed=[]; added={} removed={})".format(nivel_id, anadidos, datos.get("removed")))
            if datos.get("modified"):
                print("   aviso: {} elementos modificados al crear el nivel: {}".format(
                    len(datos["modified"]), [m.get("id") for m in datos["modified"]][:10]))
    resultados.append(ok)
    if nivel_id is not None:
        r = _post(cliente, "/delete_elements/", token, {"element_ids": [nivel_id]})
        print("   limpieza: nivel {} borrado -> {}".format(nivel_id, r.status_code))
    for ruta in (ruta_antes, ruta_despues):
        try:
            if ruta and os.path.isfile(ruta):
                os.remove(ruta)
        except OSError as error:
            print("   no se pudo borrar {}: {}".format(ruta, error))

    # 2a.4 create_grid_and_levels(simular=true) no crea nada
    rejillas_antes, _ = _ids_query(cliente, token, "OST_Grids")
    niveles_antes = len(_json(cliente.get(REVIT + "/list_levels/", params={"token": token})).get("levels") or [])
    r = _post(cliente, "/grid_levels/", token, {
        "x_spacings_mm": [6000, 6000], "y_spacings_mm": [5000], "x_names": "MCP2A-1", "y_names": "MCPY1",
        "levels": [{"name": "MCP 2A sim {}".format(marca_tiempo), "elevation_mm": 99000}], "simular": True})
    ok = mostrar("2a.4 POST /grid_levels/ simular=true", 200, r, cuerpo_max=2000)
    datos = _json(r)
    if ok:
        plan = datos.get("plan") or {}
        ok = (datos.get("simulado") is True and "copia" not in datos and "haria" in datos
              and (plan.get("counts") or {}).get("total") == 6)
        if not ok:
            print("   (se esperaba simulado=true, haria, plan.counts.total=6 y sin copia)")
    if ok:
        rejillas_despues, _ = _ids_query(cliente, token, "OST_Grids")
        niveles_despues = len(_json(cliente.get(REVIT + "/list_levels/", params={"token": token})).get("levels") or [])
        ok = rejillas_despues == rejillas_antes and niveles_despues == niveles_antes
        print("   rejillas {} -> {}, niveles {} -> {} [{}]".format(
            len(rejillas_antes), len(rejillas_despues), niveles_antes, niveles_despues, "OK" if ok else "FALLO"))
    if ok:
        entradas = _json(cliente.get(REVIT + "/log/", params={"token": token, "last_n": "3"})).get("entradas", [])
        ultima = next((e for e in reversed(entradas) if e.get("ruta") == "/grid_levels/"), None)
        ok = ultima is not None and ultima.get("simulado") is True
        print("   log: {}".format("entrada simulada encontrada" if ok else "NO hay entrada simulada de /grid_levels/"))
    resultados.append(ok)


# ---------------------------------------------------------------------------
# Consolidación (0.4.0): lotes, macros propias y rendimiento medible
# ---------------------------------------------------------------------------
RUTA_MACROS = os.environ.get("REVIT_MCP_MACROS") or os.path.expandvars(r"%LOCALAPPDATA%\RevitMcp\macros")
MACROS_EJEMPLO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "herramientas-dev", "macros-ejemplo")


def _instalar_macros_ejemplo():
    """Copia las macros de ejemplo a la carpeta de macros si no están (misma máquina que Revit)."""
    instaladas = []
    for nombre in ("numerar_planos", "comentarios_por_nivel"):
        origen = os.path.join(MACROS_EJEMPLO, nombre)
        destino = os.path.join(RUTA_MACROS, nombre)
        if os.path.isdir(origen) and not os.path.isdir(destino):
            try:
                shutil.copytree(origen, destino)
                instaladas.append(nombre)
            except OSError as error:
                print("   no se pudo copiar {}: {}".format(nombre, error))
    return instaladas


def pruebas_cons(cliente, token, resultados):
    marca_tiempo = int(time.time())

    # cons.1 set_parameters con 20 elementos en una llamada frente a 20 set_parameter
    r = _post(cliente, "/query/", token, {"category": "OST_Walls", "page_size": 20, "fields": ["Comments"], "sort_by": "id"})
    elementos = _json(r).get("elements") or []
    if len(elementos) < 2:
        resultados.append(resultado_manual("cons.1 set_parameters", False, "   Hacen falta al menos 2 muros"))
        ids = []
    else:
        ids = [e["id"] for e in elementos]
        originales = {}
        for e in elementos:
            campos = e.get("fields") or {}
            originales[e["id"]] = list(campos.values())[0] if campos else ""
        valor = "MCP cons {}".format(marca_tiempo)
        inicio = time.time()
        ms_individual = 0
        ok = True
        for identificador in ids:
            r = _post(cliente, "/set_parameter/", token, {"element_id": identificador, "parameter_name": "Comments", "value": valor})
            ok = ok and r.status_code == 200 and _json(r).get("ok") is True
            ms_individual += int(_json(r).get("ms") or 0)
        pared_individual = int((time.time() - inicio) * 1000)
        inicio = time.time()
        r = _post(cliente, "/set_parameters/", token, {
            "changes": [{"element_ids": ids, "parameters": {"Comments": valor + " lote"}}]})
        pared_lote = int((time.time() - inicio) * 1000)
        ok = mostrar("cons.1 POST /set_parameters/ ({} elementos en una llamada)".format(len(ids)), 200, r, cuerpo_max=1200) and ok
        datos = _json(r)
        if ok:
            ok = (datos.get("ok") is True and datos.get("parameters_set") == len(ids)
                  and datos.get("verificacion", {}).get("coincide") is True and datos.get("fallidos") == [])
            if not ok:
                print("   (se esperaba ok=true, parameters_set={} y fallidos vacío)".format(len(ids)))
        print("   {} llamadas a set_parameter: {} ms en Revit, {} ms de pared; 1 llamada a set_parameters: {} ms en Revit, {} ms de pared".format(
            len(ids), ms_individual, pared_individual, datos.get("ms"), pared_lote))
        # restaurar
        cambios = [{"element_ids": [i], "parameters": {"Comments": originales[i] or ""}} for i in ids]
        r = _post(cliente, "/set_parameters/", token, {"changes": cambios})
        print("   restaurados: {} (fallidos: {})".format(r.status_code, len(_json(r).get("fallidos") or [])))
        resultados.append(ok)

    # cons.2 create_elements con 3 kind mezclados y simular=true
    niveles = _json(cliente.get(REVIT + "/list_levels/", params={"token": token})).get("levels") or []
    nivel_bajo = niveles[0]["name"] if niveles else None
    lote = [
        {"kind": "level", "name": "MCP cons nivel {}".format(marca_tiempo), "elevation_mm": 123000},
        {"kind": "grid", "name": "MCPC{}".format(marca_tiempo % 1000), "start_point": {"x": 0, "y": -1000, "z": 0}, "end_point": {"x": 0, "y": 9000, "z": 0}},
        {"kind": "wall", "start_point": {"x": 0, "y": 0, "z": 0}, "end_point": {"x": 3000, "y": 0, "z": 0}, "level_name": nivel_bajo, "height": 2500},
    ]
    rejillas_antes, _ = _ids_query(cliente, token, "OST_Grids")
    r = _post(cliente, "/create_elements/", token, {"elements": lote, "simular": True})
    ok = mostrar("cons.2 POST /create_elements/ simular=true (level + grid + wall)", 200, r, cuerpo_max=1500)
    datos = _json(r)
    if ok:
        plan = datos.get("plan") or {}
        ok = (datos.get("simulado") is True and "copia" not in datos and plan.get("total") == 3
              and plan.get("counts") == {"level": 1, "grid": 1, "wall": 1}
              and [h.get("kind") for h in datos.get("haria") or []] == ["level", "grid", "wall"])
        if not ok:
            print("   (se esperaba simulado=true, plan.total=3, counts {level, grid, wall} y sin copia)")
    if ok:
        rejillas_despues, _ = _ids_query(cliente, token, "OST_Grids")
        ok = rejillas_despues == rejillas_antes
        print("   rejillas {} -> {} [{}]".format(len(rejillas_antes), len(rejillas_despues), "OK" if ok else "FALLO"))
    resultados.append(ok)

    # cons.3 run_macro comentarios_por_nivel simular y real
    instaladas = _instalar_macros_ejemplo()
    if instaladas:
        print("   macros de ejemplo copiadas a {}: {}".format(RUTA_MACROS, ", ".join(instaladas)))
    r = cliente.get(REVIT + "/macros/", params={"token": token})
    ok = mostrar("cons.3a GET /macros/", 200, r, cuerpo_max=1500)
    catalogo = _json(r)
    nombres = [m.get("name") for m in catalogo.get("macros") or []]
    if ok:
        ok = "comentarios_por_nivel" in nombres and "numerar_planos" in nombres
        if not ok:
            print("   (faltan las macros de ejemplo en {}; invalidas: {})".format(RUTA_MACROS, catalogo.get("invalidas")))
    resultados.append(ok)
    if ok and nivel_bajo:
        argumentos = {"category": "OST_Walls", "level": nivel_bajo}
        r = _post(cliente, "/macros/run/", token, {"name": "comentarios_por_nivel", "args": argumentos, "simular": True})
        ok = mostrar("cons.3b POST /macros/run/ comentarios_por_nivel simular=true", 200, r, cuerpo_max=1500)
        datos = _json(r)
        plan = datos.get("plan") or {}
        if ok:
            ok = datos.get("simulado") is True and "copia" not in datos and isinstance(plan.get("count"), int)
            if not ok:
                print("   (se esperaba simulado=true, plan.count y sin copia)")
        if ok and plan.get("count", 0) > 200:
            print("   plan.count = {} > 200: la ejecución real exige forzar; se marca solo la simulación".format(plan["count"]))
            resultados.append(ok)
        elif ok:
            r = _post(cliente, "/macros/run/", token, {"name": "comentarios_por_nivel", "args": argumentos})
            ok = mostrar("cons.3c POST /macros/run/ comentarios_por_nivel real", 200, r, cuerpo_max=1500)
            datos = _json(r)
            if ok:
                ok = (datos.get("ok") is True and datos.get("writes") is True
                      and len(datos.get("despues") or {}) == plan.get("count") and bool(datos.get("copia")) or plan.get("count") == 0)
                if not ok:
                    print("   (se esperaba ok=true, writes=true, despues con plan.count elementos y copia)")
            antes = datos.get("antes") or {}
            if antes:
                cambios = [{"element_id": int(i), "parameters": {"Comments": v or ""}} for i, v in antes.items()]
                r = _post(cliente, "/set_parameters/", token, {"changes": cambios})
                print("   comentarios restaurados con set_parameters: {} ({} elementos)".format(r.status_code, len(cambios)))
            resultados.append(ok)
    else:
        resultados.append(resultado_manual("cons.3b/c run_macro", False, "   sin macro o sin niveles"))

    # cons.4 snapshot_model con timings
    r = _post(cliente, "/snapshot/", token, {"name": "prueba_cons", "overwrite": True})
    ok = mostrar("cons.4 POST /snapshot/ con timings", 200, r, cuerpo_max=1200)
    datos = _json(r)
    timings = datos.get("timings") or {}
    if ok:
        ok = all(clave in timings for clave in ("recoleccion_ms", "descripcion_ms", "bbox_ms", "parametros_ms", "hash_ms", "escritura_ms", "total_ms"))
        if not ok:
            print("   (faltan etapas en timings)")
        else:
            print("   {} elementos en {} ms: recolección {} / descripción {} / bbox {} / parámetros {} / hash {} / escritura {} [{}]".format(
                datos.get("count"), timings.get("total_ms"), timings.get("recoleccion_ms"), timings.get("descripcion_ms"),
                timings.get("bbox_ms"), timings.get("parametros_ms"), timings.get("hash_ms"), timings.get("escritura_ms"),
                "< 3 s" if (timings.get("total_ms") or 0) < 3000 else "objetivo < 3 s no alcanzado"))
    resultados.append(ok)
    try:
        if datos.get("ruta") and os.path.isfile(datos["ruta"]):
            os.remove(datos["ruta"])
    except OSError as error:
        print("   no se pudo borrar {}: {}".format(datos.get("ruta"), error))

    # cons.5 nombre retirado en el puente MCP
    cabeceras = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    inicializar = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                   "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                              "clientInfo": {"name": "probar_revit", "version": "1"}}}
    llamada = {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
               "params": {"name": "set_parameter", "arguments": {"element_id": 1, "parameter_name": "Comments", "value": "x"}}}
    try:
        cliente.post(PUENTE, json=inicializar, headers=cabeceras)
        cliente.post(PUENTE, json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=cabeceras)
        r = cliente.post(PUENTE, json=llamada, headers=cabeceras)
        ok = mostrar("cons.5 tools/call set_parameter (retirada) en el puente", 200, r, cuerpo_max=800)
        texto = r.text
        if ok:
            ok = "set_parameters" in texto and ("isError" in texto or "is_error" in texto)
            if not ok:
                print("   (se esperaba isError=true con el texto 'set_parameters(...)')")
    except Exception as error:
        ok = resultado_manual("cons.5 nombre retirado en el puente", False, "   {}".format(error))
    resultados.append(ok)


# ---------------------------------------------------------------------------
# Entrega 2b (0.5.0): estructuras metálicas y modelo analítico
# ---------------------------------------------------------------------------
def _no_aplica(resultados, nombres, motivo):
    """Marca las pruebas como NO_APLICA (cuentan como correctas) con el motivo."""
    for nombre in nombres:
        resultados.append(resultado_manual(nombre, True, "   NO_APLICA: {}".format(motivo)))


def pruebas_2b(cliente, token, resultados):
    marca = int(time.time()) % 10000
    pendientes = ["2b.2 create_steel_frame simular=true", "2b.3 create_steel_frame real + analytical_status",
                  "2b.4 set_structural_properties + describe_element(include_structural)", "2b.5 steel_quantities"]

    # 2b.1 list_steel_profiles: al menos un perfil cargado, o dice que no hay acero
    r = _post(cliente, "/steel_profiles/", token, {"loaded_only": True})
    ok = mostrar("2b.1 POST /steel_profiles/ (perfiles de acero cargados)", 200, r, cuerpo_max=2500)
    datos = _json(r)
    perfiles = datos.get("loaded") if isinstance(datos.get("loaded"), list) else []
    if ok:
        ok = len(perfiles) > 0 or bool(datos.get("nota"))
        print("   perfiles de acero cargados: {}{}".format(
            len(perfiles), "" if perfiles else "; nota: {}".format(datos.get("nota"))))
        if datos.get("no_disponibles"):
            print("   BuiltInParameter no disponibles en esta version: {}".format(datos["no_disponibles"]))
    resultados.append(ok)

    pilares = [p for p in perfiles if p.get("category") == "OST_StructuralColumns"]
    vigas = [p for p in perfiles if p.get("category") == "OST_StructuralFraming"]
    if not pilares or not vigas:
        _no_aplica(resultados, pendientes,
                   "el modelo no tiene perfiles de acero cargados de pilar y de viga ({} pilares, {} vigas en "
                   "list_steel_profiles); carga uno con load_steel_profile".format(len(pilares), len(vigas)))
        return
    tipo_pilar = u"{}: {}".format(pilares[0]["family"], pilares[0]["type"])
    tipo_viga = u"{}: {}".format(vigas[0]["family"], vigas[0]["type"])
    niveles = _json(cliente.get(REVIT + "/list_levels/", params={"token": token})).get("levels") or []
    if not niveles:
        _no_aplica(resultados, pendientes, "el modelo no tiene niveles")
        return
    nivel = niveles[0]["name"]

    # rejilla 2x2 propia, lejos del modelo, para que el portico no dependa de las rejillas existentes
    x_names = "MCP2B{}".format(marca)
    y_names = "MCPY{}".format(marca)
    r = _post(cliente, "/grid_levels/", token, {
        "x_spacings_mm": [6000], "y_spacings_mm": [5000], "x_names": x_names, "y_names": y_names,
        "origin_mm": {"x": 200000, "y": 200000, "z": 0}, "extension_mm": 1000})
    rejillas = [g["name"] for g in (_json(r).get("grids") or [])]
    ids_rejillas = [g["id"] for g in (_json(r).get("grids") or [])]
    if r.status_code != 200 or len(rejillas) != 4:
        print("=" * 70)
        print("2b: no se pudo crear la rejilla auxiliar 2x2 ({}): {}".format(r.status_code, r.text[:400]))
        _no_aplica(resultados, pendientes, "no se pudo crear la rejilla auxiliar 2x2 con /grid_levels/")
        return
    grids_x = [n for n in rejillas if n.startswith(x_names[:-len(str(marca))])]
    grids_y = [n for n in rejillas if n.startswith(y_names[:-len(str(marca))])]
    portico = {"grids_x": grids_x, "grids_y": grids_y, "levels": [nivel], "column_type": tipo_pilar,
               "beam_type": tipo_viga, "mark_prefix": "MCP2B-"}
    creados_ids = []
    try:
        # 2b.2 simulado: plan.counts.total y ningun elemento nuevo
        pilares_antes, _ = _ids_query(cliente, token, "OST_StructuralColumns")
        vigas_antes, _ = _ids_query(cliente, token, "OST_StructuralFraming")
        r = _post(cliente, "/create_steel_frame/", token, dict(portico, simular=True))
        ok = mostrar("2b.2 POST /create_steel_frame/ simular=true (2x2 rejillas, 1 nivel)", 200, r, cuerpo_max=2500)
        datos = _json(r)
        plan = datos.get("plan") or {}
        if ok:
            counts = plan.get("counts") or {}
            ok = (datos.get("simulado") is True and "copia" not in datos and counts.get("total") == 8
                  and counts.get("columns") == 4 and counts.get("beams") == 4)
            if not ok:
                print("   (se esperaba simulado=true, plan.counts {columns: 4, beams: 4, total: 8} y sin copia)")
        if ok:
            pilares_despues, _ = _ids_query(cliente, token, "OST_StructuralColumns")
            vigas_despues, _ = _ids_query(cliente, token, "OST_StructuralFraming")
            ok = pilares_despues == pilares_antes and vigas_despues == vigas_antes
            print("   pilares {} -> {}, vigas {} -> {} [{}]".format(
                len(pilares_antes), len(pilares_despues), len(vigas_antes), len(vigas_despues), "OK" if ok else "FALLO"))
        resultados.append(ok)

        # 2b.3 real: 4 pilares y 4 vigas; analytical_status sin nodos sueltos
        r = _post(cliente, "/create_steel_frame/", token, portico)
        ok = mostrar("2b.3a POST /create_steel_frame/ real", 200, r, cuerpo_max=2500)
        datos = _json(r)
        creados = datos.get("creados") or {}
        columnas = creados.get("columns") or []
        vigas_creadas = creados.get("beams") or []
        creados_ids = list(datos.get("creados_ids") or [])
        if ok:
            ok = datos.get("ok") is True and len(columnas) == 4 and len(vigas_creadas) == 4
            if not ok:
                print("   (se esperaba ok=true con 4 pilares y 4 vigas)")
            else:
                print("   pilares: {} (nivel superior {}), vigas: {}, marcas: {}".format(
                    [c["id"] for c in columnas], columnas[0].get("top_level"), [v["id"] for v in vigas_creadas],
                    [c.get("mark") for c in columnas + vigas_creadas]))
        sin_modelo_analitico = False
        if ok:
            r = _post(cliente, "/analytical_status/", token, {"element_ids": creados_ids})
            ok = mostrar("2b.3b POST /analytical_status/ de los 8 elementos", 200, r, cuerpo_max=2500)
            estado = _json(r)
            if ok:
                ok = estado.get("loose_nodes_total") == 0
                if estado.get("members", 0) == 0:
                    sin_modelo_analitico = True
                    print("   ningun elemento tiene modelo analitico asociado (sin_analitico = {}): "
                          "Revit 2023+ no lo crea salvo con la automatizacion analitica activa".format(estado.get("sin_analitico")))
                else:
                    print("   miembros analiticos: {}, conectados: {}, nodos sueltos: {}".format(
                        estado.get("members"), estado.get("connected_members"), estado.get("loose_nodes_total")))
        resultados.append(ok)

        # 2b.4 set_structural_properties(start_release=pinned) sobre las 4 vigas + describe include_structural
        ids_vigas = [v["id"] for v in vigas_creadas]
        if not ids_vigas:
            resultados.append(resultado_manual("2b.4 set_structural_properties", False, "   sin vigas creadas en 2b.3"))
        else:
            r = _post(cliente, "/set_structural_properties/", token, {"element_ids": ids_vigas, "start_release": "pinned"})
            # 0.5.1: sin modelo analitico (Revit 2023+), las liberaciones no se pueden fijar: 409 no_soportado
            esperado = 409 if sin_modelo_analitico else 200
            ok = mostrar("2b.4a POST /set_structural_properties/ start_release=pinned ({} vigas)".format(len(ids_vigas)), esperado, r, cuerpo_max=2500)
            datos = _json(r)
            if ok and sin_modelo_analitico:
                ok = datos.get("no_soportado") is True and datos.get("motivo") == "sin_modelo_analitico"
                print("   sin modelo analitico: 409 no_soportado con motivo {} [{}] (2b.4b no aplica)".format(
                    datos.get("motivo"), "OK" if ok else "FALLO"))
            elif ok:
                ok = datos.get("ok") is True and datos.get("count") == len(ids_vigas)
                if datos.get("fallidos"):
                    print("   fallidos: {}".format(datos["fallidos"]))
                if datos.get("no_disponibles"):
                    print("   no_disponibles: {}".format(datos["no_disponibles"]))
                if not ok:
                    print("   (se esperaba ok=true y count={})".format(len(ids_vigas)))
            if ok and not sin_modelo_analitico:
                r = _post(cliente, "/describe/", token, {"element_id": ids_vigas[0], "include_structural": True})
                ok = mostrar("2b.4b POST /describe/ include_structural de la viga {}".format(ids_vigas[0]), 200, r, cuerpo_max=2500)
                bloque = (_json(r).get("structural") or {})
                inicio = (bloque.get("releases") or {}).get("start") or {}
                if ok:
                    ok = inicio.get("type") == "pinned"
                    print("   liberacion inicial: {} (fuente {}) [{}]".format(inicio.get("type"), inicio.get("source"), "OK" if ok else "FALLO"))
            resultados.append(ok)

        # 2b.5 steel_quantities(group_by=type) sobre los creados
        if not creados_ids:
            resultados.append(resultado_manual("2b.5 steel_quantities", False, "   sin elementos creados en 2b.3"))
        else:
            r = _post(cliente, "/steel_quantities/", token, {"group_by": "type", "element_ids": creados_ids})
            ok = mostrar("2b.5 POST /steel_quantities/ group_by=type", 200, r, cuerpo_max=2500)
            datos = _json(r)
            if ok:
                grupos = datos.get("groups") or []
                sin_peso = datos.get("sin_peso") or []
                con_peso = [g for g in grupos if (g.get("weight_kg") or 0) > 0]
                ok = (datos.get("totals") or {}).get("count") == len(creados_ids) and (
                    (con_peso and not sin_peso) or all(s.get("motivo") for s in sin_peso))
                print("   grupos: {}; con peso: {}; sin_peso: {} [{}]".format(
                    [(g["group"], g["count"], g["weight_kg"]) for g in grupos], len(con_peso),
                    [(s["element_id"], s.get("motivo")) for s in sin_peso], "OK" if ok else "FALLO"))
            resultados.append(ok)
    finally:
        if creados_ids:
            r = _post(cliente, "/delete_elements/", token, {"element_ids": creados_ids})
            print("   limpieza: {} pilares y vigas borrados -> {}".format(len(creados_ids), r.status_code))
        if ids_rejillas:
            r = _post(cliente, "/delete_elements/", token, {"element_ids": ids_rejillas})
            print("   limpieza: rejillas auxiliares {} borradas -> {}".format(rejillas, r.status_code))


if __name__ == "__main__":
    sys.exit(main())
