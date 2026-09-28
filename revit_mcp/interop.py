# -*- coding: UTF-8 -*-
"""
Interop Module for Revit MCP
Handles IFC export and external file linking/importing.

link_file pasa por escritura.ejecutar (copia, log, simular, IA:, creados).
export_ifc no modifica el modelo: solo usa la transaccion "IA: Exportar IFC".
"""

from utils import get_element_name, get_element_id_value, suppress_warnings
from seguridad import requiere_token
from escritura import ejecutar, transaccion, simulacion, EscrituraRechazada, resultado_creacion, nombre_transaccion
from pyrevit import routes, revit, DB
import clr
import json
import os
import traceback
import logging

logger = logging.getLogger(__name__)

MM_TO_FEET = 1.0 / 304.8

# Colocacion de un CAD vinculado: nombre pedido -> miembro de DB.ImportPlacement
PLACEMENTS = {"origin": "Origin", "center": "Centered", "centered": "Centered", "shared": "Shared", "site": "Site"}


def opciones_cad(file_ext, placement="origin"):
    """Opciones de importacion segun la extension; DWG/DXF/DGN con la colocacion pedida."""
    if file_ext in (".sat", ".3dm"):
        return DB.SATImportOptions()
    if file_ext == ".skp":
        return DB.SKPImportOptions()
    # Document.Link(string, DWGImportOptions, ...) solo admite DWG/DXF; un .dgn
    # necesita la sobrecarga con DGNImportOptions (Placement es de BaseImportOptions).
    options = DB.DGNImportOptions() if file_ext == ".dgn" else DB.DWGImportOptions()
    nombre = PLACEMENTS.get((placement or "origin").lower(), "Origin")
    try:
        options.Placement = getattr(DB.ImportPlacement, nombre)
    except Exception:
        options.Placement = DB.ImportPlacement.Origin
    return options


def vincular_cad(doc, file_path, mode, view, placement="origin"):
    """doc.Link (mode "link") o doc.Import de un CAD en la vista dada.

    Debe llamarse dentro de una transaccion. Devuelve el id (int) del
    ImportInstance o None si Revit no lo devolvio. Lo reutiliza
    macros.import_from_civil."""
    file_ext = os.path.splitext(file_path)[1].lower()
    options = opciones_cad(file_ext, placement)
    idref = clr.Reference[DB.ElementId]()
    if mode == "link":
        doc.Link(file_path, options, view, idref)
    else:
        doc.Import(file_path, options, view, idref)
    try:
        if idref.Value and idref.Value != DB.ElementId.InvalidElementId:
            return get_element_id_value(idref.Value)
    except Exception:
        pass
    return None


def opciones_ifc(ifc_version="IFC2x3", export_base_quantities=True, view_id=None):
    """IFCExportOptions con la version, las cantidades base y, si se da, la vista de filtro.

    Lo reutiliza analitico.exportar_estructural (0.5.0, format="ifc_structural")."""
    ifc_options = DB.IFCExportOptions()
    if ifc_version == "IFC4":
        ifc_options.FileVersion = DB.IFCVersion.IFC4
    else:
        ifc_options.FileVersion = DB.IFCVersion.IFC2x3
    ifc_options.ExportBaseQuantities = bool(export_base_quantities)
    if view_id is not None:
        ifc_options.FilterViewId = view_id
    return ifc_options


def exportar_ifc(doc, file_path, ifc_options):
    """doc.Export(carpeta, nombre, opciones) y tamano del archivo en KB. Dentro de una transaccion."""
    output_dir = os.path.dirname(file_path)
    file_name = os.path.basename(file_path)
    doc.Export(output_dir or ".", file_name, ifc_options)
    file_size_kb = 0
    try:
        if os.path.exists(file_path):
            file_size_kb = int(os.path.getsize(file_path) / 1024)
    except Exception:
        pass
    return file_size_kb


def buscar_vista_por_nombre(doc, view_name):
    """Vista (no plantilla) con ese nombre exacto, o None."""
    views = (
        DB.FilteredElementCollector(doc)
        .OfClass(DB.View)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    for v in views:
        try:
            if get_element_name(v) == view_name and not v.IsTemplate:
                return v
        except Exception:
            continue
    return None


def register_interop_routes(api):
    """Register all interop routes with the API"""

    @api.route("/export_ifc/", methods=["POST"])
    @requiere_token
    def export_ifc_handler(doc, request):
        """Export the model to IFC format."""
        try:
            if not doc:
                return routes.make_response(
                    data={"error": "No active Revit document"}, status=503
                )

            data = json.loads(request.data) if isinstance(request.data, str) else request.data

            file_path = data.get("file_path")
            if not file_path:
                return routes.make_response(
                    data={"error": "file_path is required (must end in .ifc)"},
                    status=400,
                )

            if not file_path.lower().endswith(".ifc"):
                return routes.make_response(
                    data={"error": "file_path must end in .ifc"},
                    status=400,
                )

            ifc_version = data.get("ifc_version", "IFC2x3")
            export_base_quantities = data.get("export_base_quantities", True)
            view_name = data.get("view_name")

            # Ensure output directory exists
            output_dir = os.path.dirname(file_path)

            if output_dir and not os.path.exists(output_dir):
                try:
                    os.makedirs(output_dir)
                except Exception as dir_err:
                    return routes.make_response(
                        data={"error": "Cannot create output directory: {}".format(str(dir_err))},
                        status=500,
                    )

            # Filter by view if specified
            target_view = buscar_vista_por_nombre(doc, view_name) if view_name else None
            ifc_options = opciones_ifc(ifc_version, export_base_quantities,
                                       target_view.Id if target_view is not None else None)

            t = DB.Transaction(doc, nombre_transaccion("Exportar IFC"))
            t.Start()
            suppress_warnings(t)

            try:
                file_size_kb = exportar_ifc(doc, file_path, ifc_options)
                t.Commit()
            except Exception as tx_error:
                if t.HasStarted() and not t.HasEnded():
                    t.RollBack()
                raise tx_error

            return routes.make_response(
                data={
                    "status": "success",
                    "file_path": file_path,
                    "file_size_kb": file_size_kb,
                    "ifc_version": ifc_version,
                    "message": "Exported IFC to '{}' ({} KB)".format(file_path, file_size_kb),
                }
            )

        except Exception as e:
            logger.error("Failed to export IFC: {}".format(str(e)))
            error_trace = traceback.format_exc()
            return routes.make_response(
                data={"error": str(e), "traceback": error_trace}, status=500
            )

    @api.route("/link_file/", methods=["POST"])
    @requiere_token
    def link_file_handler(doc, request):
        """Link or import an external file. Accepts `simular`."""

        def cuerpo(ctx):
            data = ctx["data"]
            file_path = data.get("file_path")
            if not file_path:
                raise EscrituraRechazada("file_path is required", 400)
            if not os.path.exists(file_path):
                raise EscrituraRechazada("File not found at the specified path: {}".format(file_path), 404)

            mode = data.get("mode", "link")
            file_ext = os.path.splitext(file_path)[1].lower()
            file_name = os.path.basename(file_path)
            file_type = file_ext.lstrip(".").upper()
            if file_ext not in (".rvt", ".dwg", ".dxf", ".dgn", ".sat", ".skp", ".3dm"):
                raise EscrituraRechazada(
                    "Unsupported file type '{}'. Supported: DWG, DXF, DGN, SAT, SKP, 3DM, RVT".format(file_ext), 400
                )
            # SAT/SKP/3DM are import-only (not linkable); force import.
            if file_ext == ".rvt":
                mode = "link"
            elif not ((mode == "link") and file_ext in (".dwg", ".dxf", ".dgn")):
                mode = "import"

            if ctx["simular"]:
                return simulacion([{
                    "accion": "vincular" if mode == "link" else "importar",
                    "file_name": file_name, "file_type": file_type, "file_path": file_path,
                    "size_kb": int(os.path.getsize(file_path) / 1024),
                    "view": get_element_name(doc.ActiveView) if file_ext != ".rvt" else None,
                }])

            result_id = None
            with transaccion(doc, "{} {}".format("Vincular" if mode == "link" else "Importar", file_name)):
                if file_ext == ".rvt":
                    model_path = DB.ModelPathUtils.ConvertUserVisiblePathToModelPath(file_path)
                    link_options = DB.RevitLinkOptions(False)  # not relative
                    link_result = DB.RevitLinkType.Create(doc, model_path, link_options)
                    if link_result and link_result.ElementId:
                        result_id = get_element_id_value(link_result.ElementId)
                        link_instance = DB.RevitLinkInstance.Create(doc, link_result.ElementId)
                        if link_instance:
                            result_id = get_element_id_value(link_instance)
                else:
                    result_id = vincular_cad(doc, file_path, mode, doc.ActiveView)

            resultado = resultado_creacion(doc, [result_id] if result_id is not None else [])
            resultado.update({
                "element_id": result_id,
                "file_name": file_name,
                "file_type": file_type,
                "mode": mode,
                "message": "{}ed file '{}'".format(mode.capitalize(), file_name),
            })
            if result_id is None:
                resultado["ok"] = False
                resultado["verificacion"] = {
                    "coincide": False,
                    "detalle": "Revit did not return the id of the created link/import",
                }
            return resultado

        return ejecutar(doc, "/link_file/", request, cuerpo)

    logger.info("Interop routes registered successfully")
