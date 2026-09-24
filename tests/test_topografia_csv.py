# -*- coding: utf-8 -*-
"""leer_csv_puntos: detecta P,N,E,Z o X,Y,Z (con o sin cabecera) y convierte unidades."""
import io

import pytest

from topografia import leer_csv_puntos


def _csv(tmp_path, texto, nombre="puntos.csv"):
    ruta = tmp_path / nombre
    with io.open(str(ruta), "w", encoding="utf-8") as archivo:
        archivo.write(texto)
    return str(ruta)


def test_pnez_con_cabecera_en_metros(tmp_path):
    ruta = _csv(tmp_path, "P,N,E,Z,D\n1,4500000.5,300000.25,812.3,BASE\n2,4500010,300010,813,\n")
    puntos, formato, avisos = leer_csv_puntos(ruta, "m")
    assert formato.startswith("P,N,E,Z")
    assert puntos[0] == {"x": 300000.25 * 1000.0, "y": 4500000.5 * 1000.0, "z": 812.3 * 1000.0}
    assert len(puntos) == 2 and avisos == []


def test_xyz_sin_cabecera_en_mm(tmp_path):
    ruta = _csv(tmp_path, "10;20;30\n40;50;60\n")
    puntos, formato, avisos = leer_csv_puntos(ruta, "mm")
    assert formato.startswith("X,Y,Z")
    assert puntos == [{"x": 10.0, "y": 20.0, "z": 30.0}, {"x": 40.0, "y": 50.0, "z": 60.0}]


def test_pnez_sin_cabecera_por_numero_de_columnas(tmp_path):
    ruta = _csv(tmp_path, "1,100,200,5\n2,101,201,6\nmalo,x,y,z\n")
    puntos, formato, avisos = leer_csv_puntos(ruta, "m")
    assert "P,N,E,Z" in formato
    assert puntos[0] == {"x": 200000.0, "y": 100000.0, "z": 5000.0}
    assert len(avisos) == 1 and "line 3" in avisos[0]


def test_cabecera_con_nombres_largos(tmp_path):
    ruta = _csv(tmp_path, "Point,Northing,Easting,Elevation\n7,1,2,3\n")
    puntos, formato, _ = leer_csv_puntos(ruta, "ft")
    assert puntos[0] == {"x": 2 * 304.8, "y": 1 * 304.8, "z": 3 * 304.8}


def test_errores(tmp_path):
    with pytest.raises(ValueError):
        leer_csv_puntos(_csv(tmp_path, "a,b\n1,2\n"), "m")
    with pytest.raises(ValueError):
        leer_csv_puntos(_csv(tmp_path, "1,2\n"), "m")
    with pytest.raises(ValueError):
        leer_csv_puntos(_csv(tmp_path, "1,2,3\n"), "yardas")
