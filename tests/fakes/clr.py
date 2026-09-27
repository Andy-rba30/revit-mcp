# -*- coding: utf-8 -*-
"""Simulacion de `clr` (IronPython)."""


def AddReference(nombre):
    return None


class _Ref(object):
    """clr.Reference[T]() : argumento de salida; el manejador lee .Value."""

    def __init__(self, tipo=None):
        self.Value = None


class Reference(object):
    def __getitem__(self, tipo):
        return _Ref


Reference = Reference()
