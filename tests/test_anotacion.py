# -*- coding: utf-8 -*-
"""annotation._referencia (0.4.3): la cota entre muros usa la cara exterior del muro."""
from pyrevit import DB

import annotation


def test_referencia_de_un_muro_es_su_cara_exterior(monkeypatch):
    """Validacion 0.4.2, paso 20: annotate(kind="dimension") sobre dos muros paralelos
    respondia 400 porque un muro no tiene la referencia "Center" ni `Reference` en su
    geometria. Ahora se toma HostObjectUtils.GetSideFaces(muro, Exterior)[0]."""
    llamadas = []

    class HostObjectUtils(object):
        @staticmethod
        def GetSideFaces(muro, capa):
            llamadas.append((muro, capa))
            return ["cara exterior"]

    monkeypatch.setattr(DB, "HostObjectUtils", HostObjectUtils, raising=False)
    monkeypatch.setattr(DB, "ShellLayerType", type("ShellLayerType", (), {"Exterior": "Exterior"}), raising=False)
    muro = DB.Wall()
    assert annotation._referencia(muro, None) == "cara exterior"
    assert llamadas == [(muro, "Exterior")]
