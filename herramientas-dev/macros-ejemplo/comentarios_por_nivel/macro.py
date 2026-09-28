# -*- coding: UTF-8 -*-
"""Macro de ejemplo (0.4.0): Comentarios = nombre del nivel en los elementos de una categoria.

Se ejecuta dentro de la transaccion "IA: Macro comentarios_por_nivel" que abre
el servidor (writes: true). IronPython 2.7: sin f-strings ni anotaciones.
"""
from pyrevit import DB

MUESTRA = 20


def _cambios(doc, args, api):
    """[(elemento, parametro, antes, despues)] que la macro va a tocar."""
    categoria = args["category"]
    bic = getattr(DB.BuiltInCategory, categoria, None)
    if bic is None:
        raise ValueError("category must be a BuiltInCategory name like OST_Walls, got '{}'".format(categoria))
    nivel = args.get("level")
    nombre_pedido = api.get_element_name(nivel) if nivel is not None else None
    parametro = args.get("parameter") or "Comments"
    cambios = []
    for elemento in DB.FilteredElementCollector(doc).OfCategory(bic).WhereElementIsNotElementType():
        nombre_nivel = api.nombre_nivel(elemento)
        if not nombre_nivel:
            continue
        if nombre_pedido is not None and nombre_nivel != nombre_pedido:
            continue
        param = api.buscar_por_nombre(elemento, parametro)
        if param is None or param.IsReadOnly:
            continue
        actual = param.AsString() or u""
        if actual and not args.get("overwrite"):
            continue
        if actual == nombre_nivel:
            continue
        cambios.append((elemento, param, actual, nombre_nivel))
    return cambios


def plan(doc, args, api):
    cambios = _cambios(doc, args, api)
    return {
        "count": len(cambios),
        "parameter": args.get("parameter") or "Comments",
        "muestra": [
            {"id": api.get_element_id_value(e), "antes": antes, "despues": despues}
            for e, _, antes, despues in cambios[:MUESTRA]
        ],
    }


def run(doc, uidoc, args, api):
    cambios = _cambios(doc, args, api)
    antes = {}
    despues = {}
    for elemento, param, valor_antes, nombre_nivel in cambios:
        identificador = api.get_element_id_value(elemento)
        param.Set(nombre_nivel)
        antes[identificador] = valor_antes
        despues[identificador] = param.AsString() or u""
    api.log(u"{} elementos con {} = nivel".format(len(cambios), args.get("parameter") or "Comments"))
    return {"antes": antes, "despues": despues, "count": len(cambios)}
