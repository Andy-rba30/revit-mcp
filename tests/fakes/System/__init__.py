# -*- coding: utf-8 -*-
"""Simulacion de los tipos .NET que usan los modulos de revit_mcp."""
Int64 = int
Int32 = int
Byte = int
Double = float
String = str


class Array(object):
    @staticmethod
    def CreateInstance(tipo, n):
        return [tipo() for _ in range(n)]


class IO(object):
    class File(object):
        @staticmethod
        def Copy(origen, destino, sobrescribir=False):
            import shutil

            shutil.copy2(origen, destino)

        @staticmethod
        def Exists(ruta):
            import os

            return os.path.exists(ruta)


class Environment(object):
    UserName = "prueba"
