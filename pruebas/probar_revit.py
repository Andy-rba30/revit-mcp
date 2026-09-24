#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pruebas de humo del conector Revit MCP (CPython 3, solo httpx y stdlib).

Requisitos: Revit abierto con la extensión cargada (Routes en 48884), un
modelo de prueba abierto (guardado en disco y, a ser posible, NO de trabajo
compartido para que se creen copias en backups\\) y el puente MCP en marcha
(python main.py --combined, o --streamable-http, en 8000).

Uso:
    python pruebas\\probar_revit.py [--element-id ID] [--parameter Comments]

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
"""
import argparse
import json
import os
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
        r = cliente.get(REVIT + "/element_properties/{}".format(element_id), params={"token": token})
        propiedades = _json(r)
        valor_original = ""
        for p in propiedades.get("parameters", []):
            if p.get("name") == parametro:
                valor_original = p.get("value", "")
                break
        nuevo_valor = "MCP prueba {}".format(int(time.time()))

        r = cliente.post(REVIT + "/set_parameter/", json={
            "element_id": element_id, "parameter_name": parametro, "value": nuevo_valor,
            "simular": True, "token": token})
        ok = mostrar("6. POST /set_parameter/ simular=true (elemento {})".format(element_id), 200, r)
        datos = _json(r)
        if ok:
            ok = datos.get("simulado") is True and "haria" in datos and "copia" not in datos
            if not ok:
                print("   (se esperaba simulado=true, haria y sin copia)")
        if ok:
            r = cliente.get(REVIT + "/element_properties/{}".format(element_id), params={"token": token})
            actual = ""
            for p in _json(r).get("parameters", []):
                if p.get("name") == parametro:
                    actual = p.get("value", "")
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
            ok = (datos.get("ok") is True and datos.get("antes") == valor_original
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

    print("=" * 70)
    print("Resultado: {}/{} pruebas correctas".format(sum(resultados), len(resultados)))
    return 0 if all(resultados) else 1


if __name__ == "__main__":
    sys.exit(main())
