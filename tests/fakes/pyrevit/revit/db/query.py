# -*- coding: utf-8 -*-
"""Simulacion de pyrevit.revit.db.query."""


def get_linked_model_instances(doc):
    from pyrevit import DB

    return DB.FilteredElementCollector(doc)


def get_rvt_link_instance_name(instancia):
    return "Link"
