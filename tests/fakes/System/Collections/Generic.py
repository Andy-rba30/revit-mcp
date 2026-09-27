# -*- coding: utf-8 -*-
"""List[T]() simulada: una lista de Python con Add/Count."""


class _ListaTipada(list):
    def Add(self, valor):
        self.append(valor)

    @property
    def Count(self):
        return len(self)


class _List(object):
    def __getitem__(self, tipo):
        return _ListaTipada


List = _List()
