#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pruebas de humo del conector Revit MCP (CPython 3, solo httpx y stdlib).

Requisitos: Revit abierto con la extensión cargada (Routes en 48884) y el
puente MCP en marcha (python main.py --combined, o --streamable-http, en 8000).

Uso:
    python pruebas\\probar_revit.py

Cada prueba imprime nombre, código de estado y cuerpo tal cual llega.
Termina con código de salida 0 si todas dan el resultado esperado.
"""
import json
import os
import sys

import httpx

REVIT = "http://127.0.0.1:48884/revit_mcp"
PUENTE = "http://127.0.0.1:8000/mcp"
RUTA_TOKEN = os.path.expandvars(r"%LOCALAPPDATA%\RevitMcp\token")


def leer_token():
    try:
        with open(RUTA_TOKEN, "r", encoding="utf-8") as archivo:
            return archivo.read().strip()
    except OSError:
        return None


def mostrar(nombre, esperado, respuesta):
    ok = respuesta.status_code == esperado
    print("=" * 70)
    print("{} -> {}  (esperado {})  [{}]".format(
        nombre, respuesta.status_code, esperado, "OK" if ok else "FALLO"))
    print("Cuerpo:")
    print(respuesta.text)
    return ok


def main():
    resultados = []
    cliente = httpx.Client(timeout=30.0)

    # 1. GET /status/ sin token -> 401
    r = cliente.get(REVIT + "/status/")
    resultados.append(mostrar("1. GET /revit_mcp/status/ sin token", 401, r))

    # 2. GET /status/ con token -> 200
    token = leer_token()
    if token is None:
        print("=" * 70)
        print("2. No existe {}: Revit no está abierto o el conector no ha iniciado".format(RUTA_TOKEN))
        resultados.append(False)
    else:
        r = cliente.get(REVIT + "/status/", params={"token": token})
        resultados.append(mostrar("2. GET /revit_mcp/status/ con token", 200, r))

        # 3. POST /execute_code/ con token en el cuerpo -> 200
        cuerpo = {"code": 'print("hola")', "description": "prueba", "token": token}
        r = cliente.post(REVIT + "/execute_code/", json=cuerpo)
        ok = mostrar("3. POST /revit_mcp/execute_code/ con token", 200, r)
        if ok:
            try:
                ok = r.json().get("output", "").strip() == "hola"
                if not ok:
                    print("   (la salida capturada no es 'hola')")
            except json.JSONDecodeError:
                ok = False
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

    print("=" * 70)
    print("Resultado: {}/{} pruebas correctas".format(sum(resultados), len(resultados)))
    return 0 if all(resultados) else 1


if __name__ == "__main__":
    sys.exit(main())
