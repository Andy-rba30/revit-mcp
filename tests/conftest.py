# -*- coding: utf-8 -*-
"""Configuracion de pytest: hace importables tools/ (CPython) y revit_mcp/ (IronPython
simulado) desde CPython 3.

- `tests/fakes` aporta `pyrevit`, `System`, `clr` y `StringIO` simulados.
- `revit_mcp/` se anade a sys.path porque sus modulos usan importaciones
  implicitas relativas de Python 2 (`from utils import ...`).
"""
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAKES = os.path.join(RAIZ, "tests", "fakes")
REVIT_MCP = os.path.join(RAIZ, "revit_mcp")

for ruta in (RAIZ, FAKES, REVIT_MCP):
    if ruta not in sys.path:
        sys.path.insert(0, ruta)
