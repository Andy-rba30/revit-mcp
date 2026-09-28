# -*- coding: utf-8 -*-
"""colors.buscar_categoria (0.4.1): color_elements acepta OST_..., alias o nombre visible."""
from pyrevit import DB

from revit_mcp import colors


class _Id(object):
    def __init__(self, valor):
        self.Value = valor
        self.IntegerValue = valor


class _Categoria(object):
    def __init__(self, valor, nombre):
        self.Id = _Id(valor)
        self.Name = nombre


class _Doc(object):
    def __init__(self, categorias):
        self.Settings = type("Settings", (), {"Categories": categorias})()


def test_buscar_categoria_independiente_del_idioma():
    muros = _Categoria(int(DB.BuiltInCategory.OST_Walls), u"Muros")
    doc = _Doc([_Categoria(-1, u"Suelos"), muros])
    assert colors.buscar_categoria(doc, "OST_Walls") is muros
    assert colors.buscar_categoria(doc, "Walls") is muros
    assert colors.buscar_categoria(doc, u"Muros") is muros
    assert colors.buscar_categoria(doc, "NoExiste") is None
