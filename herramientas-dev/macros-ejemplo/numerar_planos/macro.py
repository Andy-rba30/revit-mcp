# -*- coding: UTF-8 -*-
"""Macro de ejemplo (0.4.0): renumera los planos con prefijo y paso.

Se ejecuta dentro de la transaccion "IA: Macro numerar_planos" que abre el
servidor (writes: true); aqui no se abre ninguna. IronPython 2.7: sin
f-strings ni anotaciones.
"""
from pyrevit import DB


def _planos(doc, args, api):
    ids = args.get("sheet_ids") or []
    if ids:
        planos = [doc.GetElement(api.make_element_id(i)) for i in ids]
        planos = [p for p in planos if isinstance(p, DB.ViewSheet)]
    else:
        planos = list(DB.FilteredElementCollector(doc).OfClass(DB.ViewSheet).WhereElementIsNotElementType())
        planos.sort(key=lambda p: (u"{}".format(p.SheetNumber), api.get_element_id_value(p)))
    return planos


def _numeros(planos, args):
    numero = int(args.get("start") or 1)
    paso = int(args.get("step") or 1)
    cifras = int(args.get("digits") or 2)
    numeros = []
    for _ in planos:
        numeros.append(u"{}{}".format(args["prefix"], u"{}".format(numero).zfill(cifras)))
        numero += paso
    return numeros


def plan(doc, args, api):
    """Lo que haria: un cambio por plano (antes -> despues). `count` alimenta comprobar_alcance."""
    planos = _planos(doc, args, api)
    numeros = _numeros(planos, args)
    return {
        "count": len(planos),
        "cambios": [
            {"id": api.get_element_id_value(p), "antes": u"{}".format(p.SheetNumber), "despues": n}
            for p, n in zip(planos, numeros)
        ],
    }


def run(doc, uidoc, args, api):
    planos = _planos(doc, args, api)
    numeros = _numeros(planos, args)
    antes = {}
    despues = {}
    # Dos pasadas: Revit rechaza un numero de plano repetido, y al desplazar
    # la secuencia (01 -> 02 cuando 02 existe) chocaria con el siguiente.
    for plano in planos:
        identificador = api.get_element_id_value(plano)
        antes[identificador] = u"{}".format(plano.SheetNumber)
        plano.SheetNumber = u"tmp-{}".format(identificador)
    for plano, numero in zip(planos, numeros):
        identificador = api.get_element_id_value(plano)
        plano.SheetNumber = numero
        despues[identificador] = u"{}".format(plano.SheetNumber)
        api.log(u"{}: {} -> {}".format(identificador, antes[identificador], numero))
    return {"antes": antes, "despues": despues, "count": len(planos)}
