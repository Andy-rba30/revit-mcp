# -*- coding: utf-8 -*-
"""Simulacion de pyrevit.revit.db (ProjectInfo y query)."""
from . import query  # noqa: F401


class ProjectInfo(object):
    def __init__(self, doc):
        self.name = getattr(doc, "Title", "")
        self.number = ""
        self.client_name = ""
