# -*- coding: utf-8 -*-
"""Guarda de compatibilidad IronPython 2.7 para revit_mcp/*.py y startup.py.

No hay IronPython en CI, asi que se analiza el arbol sintactico con CPython 3 y
se rechazan las construcciones que IronPython 2.7 no acepta: f-strings,
anotaciones, `nonlocal`, `yield from`, argumentos solo por nombre,
`raise ... from`, `super()` sin argumentos, `pathlib`, `open(encoding=)`,
`os.makedirs(exist_ok=)`, `{**d}`, `[*a]` y `print(...)` con varios argumentos
sin `from __future__ import print_function`.

Ademas comprueba que cada modulo se importa en CPython 3 con el `pyrevit`
simulado de tests/fakes (nombres, importaciones y sintaxis basica).
"""
import ast
import importlib
import io
import os

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CARPETA = os.path.join(RAIZ, "revit_mcp")

ARCHIVOS = sorted(
    os.path.join(CARPETA, nombre)
    for nombre in os.listdir(CARPETA)
    if nombre.endswith(".py")
) + [os.path.join(RAIZ, "startup.py")]


def _problemas(ruta):
    with io.open(ruta, "r", encoding="utf-8") as archivo:
        fuente = archivo.read()
    arbol = ast.parse(fuente, filename=ruta)
    tiene_print_function = any(
        isinstance(nodo, ast.ImportFrom)
        and nodo.module == "__future__"
        and any(alias.name == "print_function" for alias in nodo.names)
        for nodo in arbol.body
    )
    problemas = []

    def marcar(nodo, texto):
        problemas.append("{}:{}: {}".format(os.path.basename(ruta), getattr(nodo, "lineno", "?"), texto))

    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.JoinedStr):
            marcar(nodo, "f-string")
        elif isinstance(nodo, ast.AnnAssign):
            marcar(nodo, "anotacion de variable")
        elif isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if isinstance(nodo, ast.AsyncFunctionDef):
                marcar(nodo, "async def")
            if nodo.returns is not None:
                marcar(nodo, "anotacion de retorno")
            args = nodo.args
            for arg in list(args.args) + list(getattr(args, "posonlyargs", [])):
                if arg.annotation is not None:
                    marcar(nodo, "anotacion de argumento")
            if args.kwonlyargs:
                marcar(nodo, "argumentos solo por nombre")
            if getattr(args, "posonlyargs", []):
                marcar(nodo, "argumentos solo posicionales")
        elif isinstance(nodo, ast.Nonlocal):
            marcar(nodo, "nonlocal")
        elif isinstance(nodo, ast.YieldFrom):
            marcar(nodo, "yield from")
        elif isinstance(nodo, ast.Await):
            marcar(nodo, "await")
        elif isinstance(nodo, ast.Raise) and nodo.cause is not None:
            marcar(nodo, "raise ... from")
        elif isinstance(nodo, ast.Dict) and any(clave is None for clave in nodo.keys):
            marcar(nodo, "{**dict}")
        elif isinstance(nodo, (ast.List, ast.Tuple, ast.Set)) and any(
            isinstance(elemento, ast.Starred) for elemento in nodo.elts
        ):
            marcar(nodo, "desempaquetado * en literal")
        elif isinstance(nodo, (ast.Import, ast.ImportFrom)):
            nombres = [alias.name for alias in nodo.names]
            modulo = getattr(nodo, "module", None) or ""
            if "pathlib" in nombres or modulo.startswith("pathlib"):
                marcar(nodo, "pathlib")
        elif isinstance(nodo, ast.Call):
            funcion = nodo.func
            nombre = funcion.id if isinstance(funcion, ast.Name) else None
            atributo = funcion.attr if isinstance(funcion, ast.Attribute) else None
            palabras = [kw.arg for kw in nodo.keywords]
            if nombre == "open" and "encoding" in palabras:
                marcar(nodo, "open(encoding=) (usar io.open)")
            if atributo == "makedirs" and "exist_ok" in palabras:
                marcar(nodo, "os.makedirs(exist_ok=)")
            if nombre == "super" and not nodo.args:
                marcar(nodo, "super() sin argumentos")
            if nombre == "print" and not tiene_print_function:
                if len(nodo.args) != 1 or nodo.keywords:
                    marcar(nodo, "print(...) con varios argumentos sin print_function")
    return problemas


@pytest.mark.parametrize("ruta", ARCHIVOS, ids=[os.path.basename(r) for r in ARCHIVOS])
def test_sintaxis_compatible_con_ironpython_27(ruta):
    problemas = _problemas(ruta)
    assert not problemas, "\n".join(problemas)


# Modulos con importaciones exclusivas de Python 2 que no se pueden simular
# sin sombrear la biblioteca estandar de CPython (urllib.unquote).
SIN_IMPORTAR = {"views"}

MODULOS = sorted(
    os.path.splitext(nombre)[0]
    for nombre in os.listdir(CARPETA)
    if nombre.endswith(".py") and nombre != "__init__.py"
)


@pytest.mark.parametrize("modulo", [m for m in MODULOS if m not in SIN_IMPORTAR])
def test_modulo_se_importa_con_pyrevit_simulado(modulo):
    # Se importa como paquete (revit_mcp.<modulo>), igual que hace startup.py,
    # para que funcionen tanto `from utils import` (implicita relativa en
    # Python 2; aqui resuelve por sys.path) como `from .utils import`.
    importado = importlib.import_module("revit_mcp." + modulo)
    assert importado is not None
    registro = [nombre for nombre in dir(importado) if nombre.startswith("register_")]
    if modulo not in ("utils", "seguridad", "escritura"):
        assert registro, "{} no define register_*_routes".format(modulo)
