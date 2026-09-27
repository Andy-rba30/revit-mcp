# -*- coding: utf-8 -*-
"""Paquete `pyrevit` simulado para importar revit_mcp/*.py en CPython 3.

Solo existe para las pruebas de tests/: no toca Revit. Implementa lo justo
para que los modulos IronPython se importen y para simular transacciones,
respuestas HTTP y algunos tipos de DB (ElementId, Transaction, XYZ).
"""
from . import routes, DB, revit  # noqa: F401
