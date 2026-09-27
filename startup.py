# -*- coding: UTF-8 -*-
"""
Revit MCP Extension Startup
Registers all MCP routes and initializes the API

Al cargar la extension se genera un token de sesion aleatorio, se escribe en
%LOCALAPPDATA%\\RevitMcp\\token y se guarda en memoria (revit_mcp/seguridad.py).
Todas las rutas exigen ese token (ver CONTRATO.md).
"""
from __future__ import print_function

from pyrevit import routes
import logging
import os

import clr
import System

logger = logging.getLogger(__name__)

# Initialize the main API
api = routes.API("revit_mcp")


# ---------------------------------------------------------------------------
# Token de sesion
# ---------------------------------------------------------------------------
def _generar_token():
    """Devuelve 32 bytes aleatorios en hexadecimal (64 caracteres, minusculas)."""
    try:
        # En .NET Framework no hace falta; en .NET Core el tipo vive en este
        # ensamblado. Si ya esta cargado o no existe, se ignora el error.
        clr.AddReference("System.Security.Cryptography.Algorithms")
    except Exception:
        pass
    try:
        from System.Security.Cryptography import RandomNumberGenerator

        generador = RandomNumberGenerator.Create()
        try:
            buffer = System.Array[System.Byte](32)
        except Exception:
            buffer = System.Array.CreateInstance(System.Byte, 32)
        generador.GetBytes(buffer)
        return "".join("{0:02x}".format(int(b)) for b in buffer)
    except Exception as error:
        logger.warning(
            u"RandomNumberGenerator no disponible (%s); se usa os.urandom",
            str(error),
        )
        return "".join("{0:02x}".format(ord(c)) for c in os.urandom(32))


def _restringir_acl(ruta):
    """Deja el archivo solo accesible para el usuario actual (sin herencia)."""
    # En .NET Core / .NET 8 estos tipos viven en ensamblados aparte; en .NET
    # Framework ya estan cargados y AddReference simplemente no hace falta.
    for ensamblado in (
        "System.Security.AccessControl",
        "System.Security.Principal.Windows",
        "System.IO.FileSystem.AccessControl",
    ):
        try:
            clr.AddReference(ensamblado)
        except Exception:
            pass

    from System.IO import File, FileInfo
    from System.Security.AccessControl import (
        AccessControlType,
        FileSecurity,
        FileSystemAccessRule,
        FileSystemRights,
    )
    from System.Security.Principal import WindowsIdentity

    usuario = WindowsIdentity.GetCurrent().User
    seguridad = FileSecurity()
    # True, False: corta la herencia y NO copia las reglas heredadas.
    seguridad.SetAccessRuleProtection(True, False)
    seguridad.AddAccessRule(
        FileSystemAccessRule(usuario, FileSystemRights.FullControl, AccessControlType.Allow)
    )
    try:
        # .NET Framework: metodo de instancia en System.IO.File
        File.SetAccessControl(ruta, seguridad)
    except AttributeError:
        # .NET Core / .NET 8: metodo de extension en System.IO.FileSystem.AccessControl
        from System.IO import FileSystemAclExtensions

        FileSystemAclExtensions.SetAccessControl(FileInfo(ruta), seguridad)


def inicializar_token():
    """Genera el token de esta sesion, lo escribe en disco y lo deja en memoria."""
    from revit_mcp import seguridad

    token = _generar_token()
    seguridad.establecer_token(token)

    try:
        carpeta = os.path.join(os.environ["LOCALAPPDATA"], "RevitMcp")
        if not os.path.isdir(carpeta):
            os.makedirs(carpeta)
        ruta = os.path.join(carpeta, "token")
        with open(ruta, "w") as archivo:
            archivo.write(token)
        logger.info("Token de sesion escrito en %s", ruta)
    except Exception as error:
        logger.error(
            u"No se pudo escribir el token en %%LOCALAPPDATA%%\\RevitMcp\\token: %s. "
            u"El puente MCP no podra autenticarse hasta reiniciar Revit.",
            str(error),
        )
        return token

    try:
        _restringir_acl(ruta)
    except Exception as error:
        logger.warning(
            u"No se pudo restringir la ACL de %s (%s); el archivo queda con los "
            u"permisos por defecto de %%LOCALAPPDATA%%.",
            ruta,
            str(error),
        )
    return token


def register_routes():
    """Register all MCP route modules"""
    try:
        # Import and register status routes
        from revit_mcp.status import register_status_routes

        register_status_routes(api)

        from revit_mcp.model_info import register_model_info_routes

        register_model_info_routes(api)

        from revit_mcp.views import register_views_routes

        register_views_routes(api)

        from revit_mcp.placement import register_placement_routes

        register_placement_routes(api)

        from revit_mcp.colors import register_color_routes

        register_color_routes(api)

        from revit_mcp.code_execution import register_code_execution_routes

        register_code_execution_routes(api)

        from revit_mcp.building import register_building_routes

        register_building_routes(api)

        from revit_mcp.editing import register_editing_routes

        register_editing_routes(api)

        from revit_mcp.structure import register_structure_routes

        register_structure_routes(api)

        from revit_mcp.annotation import register_annotation_routes

        register_annotation_routes(api)

        from revit_mcp.analysis import register_analysis_routes

        register_analysis_routes(api)

        from revit_mcp.documentation import register_documentation_routes

        register_documentation_routes(api)

        from revit_mcp.rooms import register_room_routes

        register_room_routes(api)

        from revit_mcp.view_management import register_view_management_routes

        register_view_management_routes(api)

        from revit_mcp.tags import register_tag_routes

        register_tag_routes(api)

        from revit_mcp.transforms import register_transform_routes

        register_transform_routes(api)

        from revit_mcp.mep import register_mep_routes

        register_mep_routes(api)

        from revit_mcp.parameters import register_parameter_routes

        register_parameter_routes(api)

        from revit_mcp.interop import register_interop_routes

        register_interop_routes(api)

        from revit_mcp.detail import register_detail_routes

        register_detail_routes(api)

        from revit_mcp.clash import register_clash_routes

        register_clash_routes(api)

        from revit_mcp.document import register_document_routes

        register_document_routes(api)

        from revit_mcp.consulta import register_consulta_routes

        register_consulta_routes(api)

        from revit_mcp.coordenadas import register_coordenadas_routes

        register_coordenadas_routes(api)

        from revit_mcp.tipos import register_tipos_routes

        register_tipos_routes(api)

        from revit_mcp.estructural import register_estructural_routes

        register_estructural_routes(api)

        from revit_mcp.topografia import register_topografia_routes

        register_topografia_routes(api)

        from revit_mcp.subproyectos import register_subproyectos_routes

        register_subproyectos_routes(api)

        from revit_mcp.mantenimiento import register_mantenimiento_routes

        register_mantenimiento_routes(api)

        from revit_mcp.navegacion import register_navegacion_routes

        register_navegacion_routes(api)

        from revit_mcp.instantaneas import register_instantaneas_routes

        register_instantaneas_routes(api)

        from revit_mcp.macros import register_macros_routes

        register_macros_routes(api)

        from revit_mcp.lotes import register_lotes_routes

        register_lotes_routes(api)

        logger.info("All MCP routes registered successfully")

    except Exception as e:
        logger.error("Failed to register MCP routes: %s", str(e))
        raise


# Generate the session token first so every route is protected from the
# very first request, then register all routes when the extension loads
inicializar_token()
register_routes()
