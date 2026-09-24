# -*- coding: UTF-8 -*-
"""
Seguridad para las rutas HTTP de Revit MCP (IronPython 2.7, dentro de Revit).

Token de sesion
---------------
startup.py genera un token aleatorio en cada arranque de Revit, lo escribe en
%LOCALAPPDATA%\\RevitMcp\\token y lo deja en memoria en este modulo mediante
establecer_token(). El puente MCP (main.py) lee ese mismo archivo y envia el
token en cada peticion:

  * POST: clave "token" dentro del cuerpo JSON.
  * GET : parametro de consulta ?token=...

El decorador requiere_token comprueba el token antes de ejecutar el manejador y
responde 401 {"error": "token ausente o incorrecto"} si falta o no coincide.

Limitacion conocida: pyRevit Routes NO expone las cabeceras HTTP al manejador
(base.Request._headers queda vacio; server.py solo pasa path, method, data,
params y query_params). Por eso no es posible comprobar la cabecera Origin
aqui. La proteccion contra DNS rebinding queda en el puente main.py, que con
el SDK mcp 2.2 responde 421 a cualquier Host/Origin que no sea loopback.
"""
from __future__ import print_function

import inspect
import json
import logging
import os

from pyrevit import routes

logger = logging.getLogger(__name__)

# IronPython 2.7 solo tiene getargspec (es lo que usa pyRevit); el alias permite
# probar este modulo en CPython 3.
try:
    _getargspec = inspect.getargspec
except AttributeError:  # pragma: no cover
    _getargspec = inspect.getfullargspec

# Carpeta y archivo donde startup.py deja el token de la sesion actual.
CARPETA_TOKEN = os.path.join(os.environ.get("LOCALAPPDATA", ""), "RevitMcp")
RUTA_TOKEN = os.path.join(CARPETA_TOKEN, "token")

MENSAJE_401 = u"token ausente o incorrecto"

# Token en memoria: se rellena desde startup.py para no leer el archivo en cada
# peticion. Si aun no esta, token_actual() lo lee del archivo una sola vez.
_token = None


def establecer_token(token):
    """Guarda en memoria el token generado por startup.py."""
    global _token
    _token = token


def token_actual():
    """Devuelve el token de la sesion (memoria primero, archivo despues)."""
    global _token
    if _token:
        return _token
    try:
        with open(RUTA_TOKEN, "r") as archivo:
            valor = archivo.read().strip()
        if valor:
            _token = valor
    except Exception as error:
        logger.warning(
            u"No se pudo leer el token de %s: %s", RUTA_TOKEN, str(error)
        )
    return _token


def _tokens_iguales(recibido, esperado):
    """Comparacion en tiempo constante: misma longitud y byte a byte."""
    if not recibido or not esperado:
        return False
    try:
        recibido = str(recibido)
        esperado = str(esperado)
    except Exception:
        return False
    if len(recibido) != len(esperado):
        return False
    diferencia = 0
    for a, b in zip(recibido, esperado):
        diferencia |= ord(a) ^ ord(b)
    return diferencia == 0


def _extraer_token(request):
    """Saca el token de la peticion y lo elimina del cuerpo.

    Orden: clave "token" en request.data (dict JSON) -> se elimina del dict para
    que el manejador nunca la vea; si no, clave "token" en request.query_params.
    Si el cuerpo llego como texto JSON (sin Content-Type application/json) se
    parsea aqui y se deja como dict, que es lo que ya aceptan los manejadores.
    """
    if request is None:
        return None

    datos = getattr(request, "data", None)
    if isinstance(datos, str) and datos.strip().startswith("{"):
        try:
            parseado = json.loads(datos)
            if isinstance(parseado, dict):
                request.data = parseado
                datos = parseado
        except Exception:
            pass

    if isinstance(datos, dict) and "token" in datos:
        return datos.pop("token")

    consulta = getattr(request, "query_params", None) or {}
    valor = consulta.get("token") if isinstance(consulta, dict) else None
    if isinstance(valor, str):
        return valor
    return None


def respuesta_401():
    """Respuesta estandar cuando el token falta o no coincide."""
    return routes.make_response(data={"error": MENSAJE_401}, status=401)


def requiere_token(funcion):
    """Decorador para manejadores de pyRevit Routes.

    Debe ir POR DENTRO de @api.route (es decir, justo encima del def), para que
    Routes registre la funcion ya protegida:

        @api.route("/ruta/", methods=["POST"])
        @requiere_token
        def manejador(doc, request):
            ...

    pyRevit inyecta los argumentos por nombre (inspect.getargspec + filter_kwargs
    en routes/server/handler.py), asi que la funcion envuelta conserva
    exactamente los mismos nombres de parametros del manejador original. Si el
    manejador no declara "request" se anade solo en la envoltura para poder
    leer el token, y no se le pasa al manejador.
    """
    argumentos = list(_getargspec(funcion)[0])
    firma = list(argumentos)
    if "request" not in firma:
        firma.append("request")

    def _ejecutar(request, kwargs):
        recibido = _extraer_token(request)
        if not _tokens_iguales(recibido, token_actual()):
            logger.warning(
                u"Peticion rechazada (401) en %s: token ausente o incorrecto",
                getattr(request, "path", "?"),
            )
            return respuesta_401()
        return funcion(**kwargs)

    # Se genera una funcion con la misma firma para que getargspec la vea igual.
    llamada = ", ".join('"%s": %s' % (nombre, nombre) for nombre in argumentos)
    codigo = (
        "def %s(%s):\n"
        "    return _ejecutar(request, {%s})\n"
        % (funcion.__name__, ", ".join(firma), llamada)
    )
    espacio = {"_ejecutar": _ejecutar}
    exec(codigo, espacio)
    envoltura = espacio[funcion.__name__]
    envoltura.__doc__ = funcion.__doc__
    envoltura.__module__ = funcion.__module__
    envoltura.__wrapped__ = funcion
    return envoltura
