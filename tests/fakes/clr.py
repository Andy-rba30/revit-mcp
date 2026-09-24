# -*- coding: utf-8 -*-
"""Simulacion de `clr` (IronPython)."""


def AddReference(nombre):
    return None


class _Ref(object):
    def __init__(self, tipo):
        self.Value = None


class Reference(object):
    def __getitem__(self, tipo):
        return _Ref


Reference = Reference()
