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
import time

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

# Los rechazos se registran como WARNING (pyRevit abre su ventana de salida con
# cada uno) solo la primera vez por ruta y despues una vez por minuto con el
# recuento: un cliente que sondea /status/ con un token viejo mientras Revit
# arranca generaba decenas de avisos seguidos. El resto va a DEBUG.
INTERVALO_AVISO_401_S = 60.0
_rechazos = {}


def registrar_rechazo(ruta):
    """Devuelve (avisar, rechazos_desde_el_ultimo_aviso) y actualiza el recuento."""
    ahora = time.time()
    ultimo, pendientes = _rechazos.get(ruta, (None, 0))
    pendientes += 1
    if ultimo is None or ahora - ultimo >= INTERVALO_AVISO_401_S:
        _rechazos[ruta] = (ahora, 0)
        return True, pendientes
    _rechazos[ruta] = (ultimo, pendientes)
    return False, pendientes

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


# Texto recibido con tildes mal decodificado ("Modelo genÃ©rico" en vez de
# "Modelo genérico"): el cuerpo llega en UTF-8 pero el servidor de pyRevit lo
# interpreta byte a byte (Latin-1 / cp1252) antes de que lo vea el manejador.
# httpx >= 0.28 envia el JSON con ensure_ascii=False, asi que cualquier nombre
# con tildes o eñes llegaba roto (plantillas .rft, familias, parametros...).
try:
    _TIPOS_TEXTO = (basestring,)  # noqa: F821 (IronPython 2.7)
except NameError:  # pragma: no cover
    _TIPOS_TEXTO = (str,)


def _reparar_texto(texto):
    """Deshace la doble decodificacion UTF-8 -> Latin-1/cp1252 si la hubo.

    Solo cambia el texto si, al volver a sus bytes originales, estos forman
    UTF-8 valido; un texto ya correcto ("genérico") no lo cumple y se deja igual.
    """
    if not isinstance(texto, _TIPOS_TEXTO) or all(ord(c) < 128 for c in texto):
        return texto
    try:
        crudo = bytearray()
        for caracter in texto:
            codigo = ord(caracter)
            if codigo < 256:
                crudo.append(codigo)
            else:
                crudo.extend(bytearray(caracter.encode("cp1252")))
        reparado = crudo.decode("utf-8")
    except Exception:
        return texto
    return reparado


def reparar_utf8(valor):
    """Aplica _reparar_texto a textos, claves y valores de dicts y listas."""
    if isinstance(valor, dict):
        return dict((_reparar_texto(clave), reparar_utf8(dato)) for clave, dato in valor.items())
    if isinstance(valor, list):
        return [reparar_utf8(dato) for dato in valor]
    return _reparar_texto(valor)


def _reparar_peticion(request):
    """Repara en su sitio request.data y request.query_params."""
    if request is None:
        return
    for atributo in ("data", "query_params"):
        try:
            valor = getattr(request, atributo, None)
            if isinstance(valor, (dict, list)) or isinstance(valor, _TIPOS_TEXTO):
                setattr(request, atributo, reparar_utf8(valor))
        except Exception as error:
            logger.debug(u"No se pudo reparar request.%s: %s", atributo, error)


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
        _reparar_peticion(request)
        recibido = _extraer_token(request)
        if not _tokens_iguales(recibido, token_actual()):
            ruta = getattr(request, "path", "?")
            avisar, cuantos = registrar_rechazo(ruta)
            if avisar:
                logger.warning(
                    u"Peticion rechazada (401) en %s: token ausente o incorrecto (%d en el ultimo minuto; "
                    u"si Revit acaba de arrancar, el cliente debe releer %%LOCALAPPDATA%%\\RevitMcp\\token)",
                    ruta, cuantos,
                )
            else:
                logger.debug(u"Peticion rechazada (401) en %s (%d sin avisar)", ruta, cuantos)
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
