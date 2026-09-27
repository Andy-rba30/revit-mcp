# -*- coding: utf-8 -*-
"""Simulacion de pyrevit.routes: API, Response y make_response."""


class Response(object):
    def __init__(self, status=200, data=None, headers=None):
        self.status = status
        self.data = data
        self.headers = headers or {}

    def __repr__(self):
        return "Response(status={}, data={!r})".format(self.status, self.data)


def make_response(data=None, status=200, headers=None):
    return Response(status=status, data=data, headers=headers)


class Request(object):
    def __init__(self, path="/", method="POST", data=None, params=None, query_params=None):
        self.path = path
        self.method = method
        self.data = data
        self.params = params or {}
        self.query_params = query_params or {}


class API(object):
    """Guarda los manejadores registrados en `rutas[(patron, metodo)]`."""

    def __init__(self, name):
        self.name = name
        self.rutas = {}

    def route(self, pattern, methods=None):
        metodos = tuple(methods or ["GET"])

        def decorador(funcion):
            for metodo in metodos:
                self.rutas[(pattern, metodo)] = funcion
            return funcion

        return decorador
