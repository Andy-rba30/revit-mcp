# -*- coding: UTF-8 -*-
"""
Escritura segura para las rutas de Revit MCP (IronPython 2.7, dentro de Revit).

Toda ruta que modifica el modelo pasa por tres funciones de este modulo:

  preparar(doc, nombre_ruta)
      Comprueba el estado del documento (409 si hay una transaccion abierta de
      otra operacion o el documento es de solo lectura) y, si el modelo esta
      guardado en disco y NO es de trabajo compartido, copia el .rvt a
      <carpeta del rvt>\\backups\\<nombre>_<yyyyMMdd_HHmmss>.rvt (conserva las
      ultimas 10 copias y no repite la copia si ya hay una de hace menos de
      30 minutos). La copia refleja el ULTIMO GUARDADO en disco, no el estado
      en memoria; el documento nunca se guarda desde aqui.

  registrar(doc, ruta, args, ok, ms, error, resultado_resumen)
      Escribe una linea JSON en <carpeta del rvt>\\mcp_log.jsonl (o en
      %LOCALAPPDATA%\\RevitMcp\\mcp_log.jsonl si el documento no esta guardado).
      Rota el archivo a 5 MB.

  transaccion(doc, nombre)
      Gestor de contexto: abre DB.Transaction(doc, u"IA: " + nombre), aplica
      suppress_warnings, hace Commit al salir y RollBack ante excepcion. Si
      Revit revierte la transaccion por un fallo de validacion, lanza
      TransaccionRevertida para que el manejador no informe de un exito falso.

Y por la plantilla ejecutar(doc, ruta, data, cuerpo), que encadena las tres,
resuelve el parametro `simular` (con true no se abre transaccion ni se copia
nada) y anade a la respuesta la copia, el tiempo y el resultado de la
verificacion.

Compatibilidad: IronPython 2.7 (sin f-strings, sin pathlib, sin anotaciones).
Los mismos archivos se importan desde CPython 3 en las pruebas con un
paquete `pyrevit` simulado.
"""
from __future__ import print_function

import datetime
import io
import json
import logging
import os
import re
import shutil
import time
import traceback

from pyrevit import routes, DB

from utils import suppress_warnings, get_element_id_value, make_element_id, get_element_name

logger = logging.getLogger(__name__)

try:
    _texto = unicode  # IronPython 2.7
    _cadena = basestring
except NameError:  # pragma: no cover - CPython 3 en las pruebas
    _texto = str
    _cadena = str

PREFIJO_TRANSACCION = u"IA: "
LONGITUD_MAX_NOMBRE = 60
MAX_COPIAS = 10
MINUTOS_ENTRE_COPIAS = 30
LOG_MAX_BYTES = 5 * 1024 * 1024
NOMBRE_LOG = "mcp_log.jsonl"
CARPETA_COPIAS = "backups"
LIMITE_ELEMENTOS = 200
FEET_TO_MM = 304.8
MM_TO_FEET = 1.0 / 304.8

NOTA_COPIA = (
    u"La copia refleja el ultimo guardado en disco, no el estado en memoria; "
    u"el documento no se guarda desde el MCP."
)
NOTA_COMPARTIDO = (
    u"Modelo de trabajo compartido: no se copia el archivo local, se confia en "
    u"las copias de seguridad del central."
)

_PATRON_MARCA = re.compile(r"_(\d{8}_\d{6})")


# ---------------------------------------------------------------------------
# Excepciones
# ---------------------------------------------------------------------------
class EscrituraRechazada(Exception):
    """La ruta no puede ejecutarse: se responde con `status` y `mensaje`."""

    def __init__(self, mensaje, status=400, extra=None):
        Exception.__init__(self, mensaje)
        self.mensaje = mensaje
        self.status = status
        self.extra = extra or {}


class TransaccionRevertida(Exception):
    """Revit no confirmo la transaccion (Commit devolvio un estado distinto)."""


# ---------------------------------------------------------------------------
# Utilidades pequenas
# ---------------------------------------------------------------------------
def _ahora():
    return datetime.datetime.now()


def _iso(fecha):
    try:
        return fecha.strftime("%Y-%m-%dT%H:%M:%S")
    except Exception:
        return str(fecha)


def _fecha_archivo(ruta):
    try:
        return _iso(datetime.datetime.fromtimestamp(os.path.getmtime(ruta)))
    except Exception:
        return None


def _a_texto(valor):
    """Serializador de reserva para json.dumps (objetos .NET, ElementId...)."""
    try:
        return get_element_id_value(valor)
    except Exception:
        pass
    try:
        return _texto(valor)
    except Exception:
        return repr(valor)


def titulo_documento(doc):
    try:
        return _texto(doc.Title)
    except Exception:
        return u""


def ruta_documento(doc):
    """Ruta del .rvt en disco o cadena vacia si nunca se guardo."""
    try:
        return _texto(doc.PathName or u"")
    except Exception:
        return u""


def es_compartido(doc):
    try:
        return bool(doc.IsWorkshared)
    except Exception:
        return False


def carpeta_documento(doc):
    ruta = ruta_documento(doc)
    if not ruta:
        return None
    carpeta = os.path.dirname(ruta)
    return carpeta or None


def carpeta_local():
    return os.path.join(os.environ.get("LOCALAPPDATA", ""), "RevitMcp")


def carpeta_log(doc):
    return carpeta_documento(doc) or carpeta_local()


def ruta_log(doc):
    return os.path.join(carpeta_log(doc), NOMBRE_LOG)


def datos_peticion(request):
    """Cuerpo JSON de la peticion como dict (vacio si no hay cuerpo)."""
    if request is None:
        return {}
    datos = getattr(request, "data", None)
    if not datos:
        return {}
    if isinstance(datos, dict):
        return datos
    try:
        parseado = json.loads(datos)
    except Exception as error:
        raise EscrituraRechazada(u"Invalid JSON format: {}".format(error), 400)
    if not isinstance(parseado, dict):
        raise EscrituraRechazada(u"Invalid data format - expected JSON object", 400)
    return parseado


def es_simulacion(data):
    valor = data.get("simular", False) if isinstance(data, dict) else False
    if isinstance(valor, _cadena):
        return valor.strip().lower() in ("1", "true", "si", "sí", "yes")
    return bool(valor)


def es_forzado(data):
    valor = data.get("forzar", False) if isinstance(data, dict) else False
    if isinstance(valor, _cadena):
        return valor.strip().lower() in ("1", "true", "si", "sí", "yes")
    return bool(valor)


def simulacion(haria, **extra):
    """Respuesta estandar de una llamada con simular=true."""
    resultado = {"simulado": True, "haria": haria}
    resultado.update(extra)
    return resultado


def comprobar_alcance(data, cantidad, que=u"elementos"):
    """Rechaza (400) mas de LIMITE_ELEMENTOS salvo forzar=true."""
    if cantidad > LIMITE_ELEMENTOS and not es_forzado(data):
        raise EscrituraRechazada(
            u"La llamada afecta a {} {}; el limite es {} por llamada salvo "
            u"forzar=true".format(cantidad, que, LIMITE_ELEMENTOS),
            400,
            {"limite": LIMITE_ELEMENTOS, "cantidad": cantidad},
        )


# ---------------------------------------------------------------------------
# Copias de seguridad
# ---------------------------------------------------------------------------
def _copiar_archivo(origen, destino):
    try:
        from System.IO import File

        File.Copy(origen, destino, True)
        return
    except ImportError:
        pass
    shutil.copy2(origen, destino)


def _marca_de(nombre_archivo):
    """datetime de la marca yyyyMMdd_HHmmss del nombre, o None."""
    coincidencia = _PATRON_MARCA.search(nombre_archivo)
    if not coincidencia:
        return None
    try:
        return datetime.datetime.strptime(coincidencia.group(1), "%Y%m%d_%H%M%S")
    except Exception:
        return None


def _copias_existentes(carpeta, base):
    """[(marca, ruta)] de las copias de `base` ordenadas de mas nueva a mas vieja."""
    copias = []
    if not os.path.isdir(carpeta):
        return copias
    prefijo = base + "_"
    for nombre in os.listdir(carpeta):
        if not nombre.startswith(prefijo) or not nombre.lower().endswith(".rvt"):
            continue
        marca = _marca_de(nombre[len(base):])
        if marca is None:
            continue
        copias.append((marca, os.path.join(carpeta, nombre)))
    copias.sort(reverse=True)
    return copias


def _podar_copias(carpeta, base):
    for marca, ruta in _copias_existentes(carpeta, base)[MAX_COPIAS:]:
        try:
            os.remove(ruta)
        except Exception as error:
            logger.warning(u"No se pudo borrar la copia antigua %s: %s", ruta, error)


def crear_copia(doc, sufijo=None, forzar=False):
    """Copia el .rvt guardado a <carpeta>\\backups\\<nombre>_<marca>[_<sufijo>].rvt.

    Devuelve un dict con `ruta`, `refleja_guardado_de` (fecha del archivo),
    `reutilizada` (True si ya habia una copia de hace menos de 30 minutos y no
    se forzo otra) y `nota`. Devuelve None si el documento no esta guardado en
    disco o es de trabajo compartido.
    """
    origen = ruta_documento(doc)
    if not origen or not os.path.isfile(origen):
        return None
    if es_compartido(doc):
        return None

    carpeta = os.path.join(os.path.dirname(origen), CARPETA_COPIAS)
    base = os.path.splitext(os.path.basename(origen))[0]
    ahora = _ahora()

    if not forzar:
        existentes = _copias_existentes(carpeta, base)
        if existentes:
            marca, ruta = existentes[0]
            edad = ahora - marca
            if edad.days == 0 and edad.seconds < MINUTOS_ENTRE_COPIAS * 60:
                return {
                    "ruta": ruta,
                    "refleja_guardado_de": _fecha_archivo(origen),
                    "reutilizada": True,
                    "nota": NOTA_COPIA,
                }

    if not os.path.isdir(carpeta):
        os.makedirs(carpeta)
    nombre = u"{}_{}".format(base, ahora.strftime("%Y%m%d_%H%M%S"))
    if sufijo:
        limpio = re.sub(r"[^\w\-]+", "_", _texto(sufijo)).strip("_")
        if limpio:
            nombre = u"{}_{}".format(nombre, limpio)
    destino = os.path.join(carpeta, nombre + u".rvt")
    _copiar_archivo(origen, destino)
    _podar_copias(carpeta, base)
    return {
        "ruta": destino,
        "refleja_guardado_de": _fecha_archivo(origen),
        "reutilizada": False,
        "nota": NOTA_COPIA,
    }


# ---------------------------------------------------------------------------
# preparar
# ---------------------------------------------------------------------------
def preparar(doc, nombre_ruta):
    """Comprueba el documento y hace la copia. Devuelve el contexto de la escritura.

    Lanza EscrituraRechazada(409) si `doc.IsModifiable` es True (hay una
    transaccion abierta de otra operacion) o si `doc.IsReadOnly` es True, y
    EscrituraRechazada(503) si no hay documento.
    """
    if doc is None:
        raise EscrituraRechazada(u"No active Revit document", 503)

    try:
        solo_lectura = bool(doc.IsReadOnly)
    except Exception:
        solo_lectura = False
    if solo_lectura:
        raise EscrituraRechazada(
            u"El documento es de solo lectura (doc.IsReadOnly); no se puede escribir",
            409,
        )

    try:
        modificable = bool(doc.IsModifiable)
    except Exception:
        modificable = False
    if modificable:
        raise EscrituraRechazada(
            u"Hay una transaccion abierta de otra operacion (doc.IsModifiable es "
            u"True); revisala en Revit antes de continuar",
            409,
            {"open_transaction": True},
        )

    contexto = {"ruta": nombre_ruta, "copia": None, "nota": None}
    if es_compartido(doc):
        logger.info(
            u"[%s] %s", nombre_ruta, NOTA_COMPARTIDO
        )
        contexto["nota"] = NOTA_COMPARTIDO
        return contexto

    try:
        contexto["copia"] = crear_copia(doc)
    except Exception as error:
        # Una copia fallida no debe impedir el trabajo, pero se dice claramente.
        logger.warning(u"[%s] No se pudo hacer la copia de seguridad: %s", nombre_ruta, error)
        contexto["nota"] = u"No se pudo hacer la copia de seguridad: {}".format(error)
    if contexto["copia"] is None and contexto["nota"] is None and not ruta_documento(doc):
        contexto["nota"] = u"El documento no esta guardado en disco: no hay copia que hacer."
    return contexto


# ---------------------------------------------------------------------------
# registrar
# ---------------------------------------------------------------------------
def _recortar(valor, clave=None, profundidad=0):
    """Reduce listas y textos largos para el log (el codigo se guarda entero)."""
    if clave == "code":
        return valor
    if profundidad > 4:
        return _a_texto(valor)
    if isinstance(valor, dict):
        return dict((k, _recortar(v, k, profundidad + 1)) for k, v in valor.items())
    if isinstance(valor, (list, tuple)):
        if len(valor) > 50:
            recortada = [_recortar(v, None, profundidad + 1) for v in valor[:50]]
            recortada.append(u"... ({} mas)".format(len(valor) - 50))
            return recortada
        return [_recortar(v, None, profundidad + 1) for v in valor]
    if isinstance(valor, _cadena) and len(valor) > 2000:
        return valor[:2000] + u"... ({} caracteres)".format(len(valor))
    return valor


def _rotar_log(destino):
    try:
        if os.path.isfile(destino) and os.path.getsize(destino) >= LOG_MAX_BYTES:
            anterior = destino + ".1"
            if os.path.exists(anterior):
                os.remove(anterior)
            os.rename(destino, anterior)
    except Exception as error:
        logger.warning(u"No se pudo rotar el log %s: %s", destino, error)


def registrar(doc, ruta, args, ok, ms, error=None, resultado_resumen=None, simulado=False):
    """Anade una linea JSON al log de acciones. Nunca lanza."""
    linea = {
        "fecha": _iso(_ahora()),
        "documento": titulo_documento(doc),
        "ruta": ruta,
        "ok": bool(ok),
        "ms": int(ms),
        "args": _recortar(args if isinstance(args, dict) else {"args": args}),
    }
    if simulado:
        linea["simulado"] = True
    if error:
        linea["error"] = _texto(error)
    if resultado_resumen is not None:
        linea["resultado"] = _recortar(resultado_resumen)

    destino = ruta_log(doc)
    try:
        carpeta = os.path.dirname(destino)
        if carpeta and not os.path.isdir(carpeta):
            os.makedirs(carpeta)
        _rotar_log(destino)
        texto = json.dumps(linea, ensure_ascii=False, default=_a_texto)
        with io.open(destino, "a", encoding="utf-8") as archivo:
            archivo.write(_texto(texto) + u"\n")
    except Exception as fallo:
        logger.warning(u"No se pudo escribir en el log %s: %s", destino, fallo)
    return destino


def leer_log(doc, last_n=50):
    """Ultimas `last_n` lineas del log como lista de dicts (o texto si no es JSON)."""
    destino = ruta_log(doc)
    if not os.path.isfile(destino):
        return destino, []
    try:
        last_n = int(last_n)
    except Exception:
        last_n = 50
    if last_n <= 0:
        last_n = 50
    with io.open(destino, "r", encoding="utf-8") as archivo:
        lineas = archivo.readlines()
    entradas = []
    for linea in lineas[-last_n:]:
        linea = linea.strip()
        if not linea:
            continue
        try:
            entradas.append(json.loads(linea))
        except Exception:
            entradas.append({"raw": linea})
    return destino, entradas


# ---------------------------------------------------------------------------
# transaccion
# ---------------------------------------------------------------------------
def nombre_transaccion(nombre):
    """`IA: <nombre>` recortado a 60 caracteres (sin duplicar el prefijo)."""
    nombre = _texto(nombre or u"Cambio").strip()
    if nombre.startswith(PREFIJO_TRANSACCION):
        nombre = nombre[len(PREFIJO_TRANSACCION):]
    return (PREFIJO_TRANSACCION + nombre)[: LONGITUD_MAX_NOMBRE + len(PREFIJO_TRANSACCION)]


def _confirmada(estado):
    try:
        return estado == DB.TransactionStatus.Committed
    except Exception:
        return True


class transaccion(object):
    """Gestor de contexto sobre DB.Transaction con nombre `IA: <nombre>`.

        with transaccion(doc, "Crear muros") as t:
            ...  # t es la DB.Transaction ya iniciada y con suppress_warnings

    Al salir sin excepcion hace Commit; con excepcion hace RollBack y la deja
    propagar. Si Commit no devuelve Committed (el preprocesador de fallos pidio
    revertir) lanza TransaccionRevertida.
    """

    def __init__(self, doc, nombre):
        self.doc = doc
        self.nombre = nombre_transaccion(nombre)
        self.t = None
        self.estado = None

    def _activa(self):
        try:
            return self.t is not None and self.t.HasStarted() and not self.t.HasEnded()
        except Exception:
            return False

    def __enter__(self):
        self.t = DB.Transaction(self.doc, self.nombre)
        self.t.Start()
        suppress_warnings(self.t)
        return self.t

    def __exit__(self, tipo, valor, traza):
        if tipo is not None:
            if self._activa():
                try:
                    self.t.RollBack()
                except Exception as error:
                    logger.warning(u"No se pudo revertir '%s': %s", self.nombre, error)
            return False
        if self._activa():
            self.estado = self.t.Commit()
            if not _confirmada(self.estado):
                raise TransaccionRevertida(
                    u"Revit revirtio la transaccion '{}' (estado {}); el modelo no "
                    u"cambio. Suele deberse a un error de validacion de Revit.".format(
                        self.nombre, self.estado
                    )
                )
        return False


# ---------------------------------------------------------------------------
# Verificacion tras escribir
# ---------------------------------------------------------------------------
def punto_mm(xyz):
    try:
        return {
            "x": round(xyz.X * FEET_TO_MM, 1),
            "y": round(xyz.Y * FEET_TO_MM, 1),
            "z": round(xyz.Z * FEET_TO_MM, 1),
        }
    except Exception:
        return None


def bbox_mm(elem, vista=None):
    """Caja envolvente del elemento en mm, o None."""
    try:
        bb = elem.get_BoundingBox(vista)
    except Exception:
        return None
    if bb is None:
        return None
    return {"min": punto_mm(bb.Min), "max": punto_mm(bb.Max)}


def nombre_categoria(elem):
    try:
        if elem.Category:
            return _texto(elem.Category.Name)
    except Exception:
        pass
    return None


def nombre_tipo(doc, elem):
    try:
        tipo_id = elem.GetTypeId()
        if tipo_id and tipo_id != DB.ElementId.InvalidElementId:
            tipo = doc.GetElement(tipo_id)
            if tipo is not None:
                return get_element_name(tipo)
    except Exception:
        pass
    try:
        return get_element_name(elem)
    except Exception:
        return None


def nombre_nivel(doc, elem):
    nivel_id = None
    try:
        nivel_id = elem.LevelId
    except Exception:
        nivel_id = None
    if not nivel_id or nivel_id == DB.ElementId.InvalidElementId:
        for bip in ("FAMILY_LEVEL_PARAM", "LEVEL_PARAM", "SCHEDULE_LEVEL_PARAM", "FAMILY_BASE_LEVEL_PARAM"):
            try:
                parametro = elem.get_Parameter(getattr(DB.BuiltInParameter, bip))
                if parametro:
                    candidato = parametro.AsElementId()
                    if candidato and candidato != DB.ElementId.InvalidElementId:
                        nivel_id = candidato
                        break
            except Exception:
                continue
    if not nivel_id or nivel_id == DB.ElementId.InvalidElementId:
        return None
    try:
        nivel = doc.GetElement(nivel_id)
        if nivel is not None:
            return get_element_name(nivel)
    except Exception:
        pass
    return None


def describir_elemento(doc, elem_o_id):
    """{"id", "categoria", "tipo", "nivel", "bbox_mm"} o None si ya no existe."""
    elem = elem_o_id
    if elem is None:
        return None
    if not hasattr(elem, "GetTypeId"):
        try:
            elem = doc.GetElement(make_element_id(get_element_id_value(elem_o_id)))
        except Exception:
            return None
    if elem is None:
        return None
    try:
        identificador = get_element_id_value(elem)
    except Exception:
        return None
    return {
        "id": identificador,
        "categoria": nombre_categoria(elem),
        "tipo": nombre_tipo(doc, elem),
        "nivel": nombre_nivel(doc, elem),
        "bbox_mm": bbox_mm(elem),
    }


def verificar_creados(doc, ids):
    """Relee los ids creados. Devuelve (descripciones, ids que no existen)."""
    creados = []
    faltan = []
    for identificador in ids:
        descripcion = describir_elemento(doc, identificador)
        if descripcion is None:
            try:
                faltan.append(get_element_id_value(identificador))
            except Exception:
                faltan.append(_a_texto(identificador))
        else:
            creados.append(descripcion)
    return creados, faltan


def resultado_creacion(doc, ids, errores=None, extra=None):
    """Respuesta estandar de creacion: {"creados": [...], "count", "ok", ...}."""
    creados, faltan = verificar_creados(doc, ids)
    resultado = {"creados": creados, "count": len(creados), "ok": not faltan}
    if faltan:
        resultado["verificacion"] = {
            "coincide": False,
            "detalle": u"Tras el commit no se encontraron los elementos {}".format(faltan),
        }
    else:
        resultado["verificacion"] = {"coincide": True}
    if errores:
        resultado["errors"] = errores
    if extra:
        resultado.update(extra)
    return resultado


def verificar_eliminados(doc, ids_pedidos, ids_cascada):
    """Comprueba que los ids ya no existen; devuelve el dict de borrado."""
    eliminados = []
    siguen = []
    for identificador in ids_pedidos:
        try:
            existe = doc.GetElement(make_element_id(identificador)) is not None
        except Exception:
            existe = False
        if existe:
            siguen.append(identificador)
        else:
            eliminados.append(identificador)
    resultado = {
        "eliminados": eliminados,
        "en_cascada": list(ids_cascada),
        "deleted_count": len(eliminados),
        "ok": not siguen,
    }
    if siguen:
        resultado["verificacion"] = {
            "coincide": False,
            "detalle": u"Estos elementos siguen existiendo tras el borrado: {}".format(siguen),
        }
    else:
        resultado["verificacion"] = {"coincide": True}
    return resultado


# ---------------------------------------------------------------------------
# ejecutar: plantilla de toda ruta de escritura
# ---------------------------------------------------------------------------
def _es_respuesta(objeto):
    return (
        objeto is not None
        and not isinstance(objeto, dict)
        and hasattr(objeto, "status")
        and hasattr(objeto, "data")
    )


def _resumen(resultado):
    if not isinstance(resultado, dict):
        return _recortar(resultado)
    resumen = {}
    for clave, valor in resultado.items():
        if clave in ("copia", "traceback", "code_executed", "code_attempted"):
            continue
        if isinstance(valor, list) and len(valor) > 20:
            resumen[clave] = u"[{} elementos]".format(len(valor))
        else:
            resumen[clave] = _recortar(valor, clave)
    return resumen


def ejecutar(doc, ruta, data, cuerpo):
    """Ejecuta `cuerpo(contexto)` con preparar/registrar y devuelve la respuesta HTTP.

    `data` puede ser el dict ya parseado o la peticion de Routes (se parsea).
    `cuerpo` recibe el contexto ({"ruta", "simular", "copia", "nota", "data"}) y puede:
      - devolver un dict: se completa con ok/ms/copia y se responde 200
        (ok=False si el dict trae "ok": False o "error");
      - devolver directamente una respuesta de routes.make_response;
      - lanzar EscrituraRechazada(mensaje, status) para un error controlado;
      - lanzar cualquier otra excepcion: se responde 500 con traceback.
    Con data["simular"] verdadero no se llama a preparar (ni copia ni
    transaccion): el cuerpo debe limitarse a validar y devolver
    simulacion([...]).
    """
    inicio = time.time()
    if doc is None:
        return routes.make_response(data={"error": "No active Revit document"}, status=503)
    if not isinstance(data, dict):
        # Se admite la peticion de Routes directamente: se parsea aqui para que
        # un JSON invalido responda 400 en vez de una excepcion sin controlar.
        try:
            data = datos_peticion(data)
        except EscrituraRechazada as rechazo:
            return routes.make_response(data={"error": rechazo.mensaje}, status=rechazo.status)
    simulado = es_simulacion(data)
    contexto = {
        "ruta": ruta, "simular": simulado, "copia": None, "nota": None, "doc": doc, "data": data,
    }

    def _ms():
        return int((time.time() - inicio) * 1000)

    try:
        if not simulado:
            contexto.update(preparar(doc, ruta))
        resultado = cuerpo(contexto)
    except EscrituraRechazada as rechazo:
        registrar(doc, ruta, data, False, _ms(), error=rechazo.mensaje, simulado=simulado)
        cuerpo_error = {"error": rechazo.mensaje}
        cuerpo_error.update(rechazo.extra)
        return routes.make_response(data=cuerpo_error, status=rechazo.status)
    except Exception as error:
        traza = traceback.format_exc()
        logger.error(u"[%s] %s\n%s", ruta, error, traza)
        registrar(doc, ruta, data, False, _ms(), error=_texto(error), simulado=simulado)
        respuesta = {"error": _texto(error), "traceback": traza}
        if contexto.get("copia"):
            respuesta["copia"] = contexto["copia"]
        return routes.make_response(data=respuesta, status=500)

    ms = _ms()
    if _es_respuesta(resultado):
        estado = getattr(resultado, "status", 200) or 200
        datos = getattr(resultado, "data", None)
        error = None
        if isinstance(datos, dict):
            error = datos.get("error")
        registrar(
            doc, ruta, data, estado < 400 and not error, ms,
            error=error, resultado_resumen=_resumen(datos), simulado=simulado,
        )
        return resultado

    if not isinstance(resultado, dict):
        resultado = {"resultado": resultado}
    ok = bool(resultado.get("ok", True)) and not resultado.get("error")
    resultado["ok"] = ok
    resultado["ms"] = ms
    if simulado:
        resultado.setdefault("simulado", True)
    else:
        resultado["copia"] = contexto.get("copia")
        if contexto.get("nota"):
            resultado["nota_copia"] = contexto["nota"]
    registrar(
        doc, ruta, data, ok, ms,
        error=resultado.get("error"), resultado_resumen=_resumen(resultado), simulado=simulado,
    )
    return routes.make_response(data=resultado)
