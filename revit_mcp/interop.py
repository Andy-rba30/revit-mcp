# -*- coding: UTF-8 -*-
"""
Interop Module for Revit MCP
Handles IFC export and external file linking/importing
"""

from utils import get_element_name, get_element_id_value, make_element_id, suppress_warnings
from seguridad import requiere_token
from coordinates_core import (
    METERS_PER_FOOT,
    evaluate_extents,
    normalize_placement,
    shared_coordinates_set,
)
from pyrevit import routes, revit, DB
import clr
import json
import os
import traceback
import logging

logger = logging.getLogger(__name__)

MM_TO_FEET = 1.0 / 304.8


def _import_placement(name):
    """Revit ImportPlacement for a canonical placement name (coordinates_core)."""
    return {
        "origin": DB.ImportPlacement.Origin,     # Auto - Origin to Internal Origin
        "shared": DB.ImportPlacement.Shared,     # Auto - By Shared Coordinates
        "center": DB.ImportPlacement.Centered,   # Auto - Center to Center
        "site": DB.ImportPlacement.Site,         # Auto - Origin to Project Base Point
    }[name]


def _extents(doc, element_id):
    """Where the new link/import instance landed: distance of its bounding box to
    the internal origin (coordinates_core.evaluate_extents) and the shared
    coordinates of its centre, in metres. Best-effort: None when unavailable."""
    if element_id is None:
        return None
    try:
        elem = doc.GetElement(make_element_id(element_id))
        bb = elem.get_BoundingBox(None) if elem is not None else None
        if bb is None:
            return None
        result = evaluate_extents(
            (bb.Min.X, bb.Min.Y, bb.Min.Z), (bb.Max.X, bb.Max.Y, bb.Max.Z))
        center = DB.XYZ((bb.Min.X + bb.Max.X) / 2.0, (bb.Min.Y + bb.Max.Y) / 2.0,
                        (bb.Min.Z + bb.Max.Z) / 2.0)
        result["center_internal_m"] = {
            "x": round(center.X * METERS_PER_FOOT, 3),
            "y": round(center.Y * METERS_PER_FOOT, 3),
            "z": round(center.Z * METERS_PER_FOOT, 3),
        }
        pos = doc.ActiveProjectLocation.GetProjectPosition(center)
        result["center_shared_m"] = {
            "east": round(pos.EastWest * METERS_PER_FOOT, 3),
            "north": round(pos.NorthSouth * METERS_PER_FOOT, 3),
            "elevation": round(pos.Elevation * METERS_PER_FOOT, 3),
        }
        return result
    except Exception as error:
        logger.debug("Could not evaluate link extents: {}".format(str(error)))
        return None


def _extents_text(extents):
    """Human-readable summary of _extents() for the tool message."""
    if not extents:
        return ""
    text = ". Farthest corner {} m from the internal origin".format(extents["max_distance_m"])
    center = extents.get("center_shared_m")
    if center:
        text += "; centre at shared E={} N={} elevation={} m".format(
            center["east"], center["north"], center["elevation"])
    if extents.get("hint"):
        text += ". WARNING: " + extents["hint"]
    return text


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
            file_name = os.path.basename(file_path)

            if output_dir and not os.path.exists(output_dir):
                try:
                    os.makedirs(output_dir)
                except Exception as dir_err:
                    return routes.make_response(
                        data={"error": "Cannot create output directory: {}".format(str(dir_err))},
                        status=500,
                    )

            # Set up IFC export options
            ifc_options = DB.IFCExportOptions()

            # Set IFC version
            if ifc_version == "IFC4":
                ifc_options.FileVersion = DB.IFCVersion.IFC4
            else:
                ifc_options.FileVersion = DB.IFCVersion.IFC2x3

            ifc_options.ExportBaseQuantities = export_base_quantities

            # Filter by view if specified
            if view_name:
                views = (
                    DB.FilteredElementCollector(doc)
                    .OfClass(DB.View)
                    .WhereElementIsNotElementType()
                    .ToElements()
                )
                target_view = None
                for v in views:
                    if get_element_name(v) == view_name and not v.IsTemplate:
                        target_view = v
                        break
                if target_view:
                    ifc_options.FilterViewId = target_view.Id

            t = DB.Transaction(doc, "Export IFC via MCP")
            t.Start()
            suppress_warnings(t)

            try:
                doc.Export(output_dir or ".", file_name, ifc_options)
                t.Commit()
            except Exception as tx_error:
                if t.HasStarted() and not t.HasEnded():
                    t.RollBack()
                raise tx_error

            # Get file size
            file_size_kb = 0
            try:
                if os.path.exists(file_path):
                    file_size_kb = int(os.path.getsize(file_path) / 1024)
            except Exception:
                pass

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
        """Link or import an external file."""
        try:
            if not doc:
                return routes.make_response(
                    data={"error": "No active Revit document"}, status=503
                )

            data = json.loads(request.data) if isinstance(request.data, str) else request.data

            file_path = data.get("file_path")
            if not file_path:
                return routes.make_response(
                    data={"error": "file_path is required"},
                    status=400,
                )

            if not os.path.exists(file_path):
                return routes.make_response(
                    data={"error": "File not found at the specified path."},
                    status=500,
                )

            mode = data.get("mode", "link")
            position = data.get("position")
            try:
                placement = normalize_placement(data.get("placement"))
            except ValueError as placement_error:
                return routes.make_response(data={"error": str(placement_error)}, status=400)
            placement_enum = _import_placement(placement)

            file_ext = os.path.splitext(file_path)[1].lower()
            file_name = os.path.basename(file_path)
            file_type = file_ext.lstrip(".").upper()

            # "By Shared Coordinates" reads the CAD file's world coordinates as
            # Revit shared coordinates. With the project position still at
            # 0,0,0 that is the same as "Origin to Origin", so a georeferenced
            # file would land hundreds of kilometres away: refuse and explain.
            if placement == "shared" and file_ext != ".rvt":
                pos = doc.ActiveProjectLocation.GetProjectPosition(DB.XYZ.Zero)
                if not shared_coordinates_set(pos.EastWest, pos.NorthSouth, pos.Elevation, pos.Angle):
                    return routes.make_response(
                        data={"error": (
                            "placement 'shared' needs shared coordinates, but the model's project "
                            "position is still 0,0,0 (the shared system coincides with the internal "
                            "one). Call set_project_coordinates with the real-world coordinates of "
                            "the internal origin (for example the centre of the Civil 3D surface, "
                            "'origen_sugerido' from extension_superficie) and link again.")},
                        status=400,
                    )

            t = DB.Transaction(doc, "Link/Import File via MCP")
            t.Start()
            suppress_warnings(t)

            try:
                result_id = None

                if file_ext == ".rvt":
                    # Revit link
                    model_path = DB.ModelPathUtils.ConvertUserVisiblePathToModelPath(file_path)
                    link_options = DB.RevitLinkOptions(False)  # not relative
                    link_result = DB.RevitLinkType.Create(doc, model_path, link_options)
                    if link_result and link_result.ElementId:
                        result_id = get_element_id_value(link_result.ElementId)
                        # Place instance with the requested placement (3-argument
                        # overload since Revit 2018; older API gets origin to origin)
                        try:
                            link_instance = DB.RevitLinkInstance.Create(
                                doc, link_result.ElementId, placement_enum)
                        except TypeError:
                            link_instance = DB.RevitLinkInstance.Create(doc, link_result.ElementId)
                        if link_instance:
                            result_id = get_element_id_value(link_instance)

                elif file_ext in (".dwg", ".dxf", ".dgn", ".sat", ".skp", ".3dm"):
                    # CAD / 3D solid geometry. Use the correct options per format,
                    # and capture the created element via an out-param Reference
                    # (the previous None placeholder never captured the result).
                    active_view = doc.ActiveView
                    if file_ext == ".sat":
                        options = DB.SATImportOptions()
                    elif file_ext == ".skp":
                        options = DB.SKPImportOptions()
                    elif file_ext == ".3dm":
                        # Rhino import also uses SAT-style options in Revit's importer
                        options = DB.SATImportOptions()
                    else:
                        options = DB.DWGImportOptions()
                    # Placement lives on BaseImportOptions, so every format takes it
                    options.Placement = placement_enum

                    idref = clr.Reference[DB.ElementId]()
                    # SAT/SKP/3DM are import-only (not linkable); force import.
                    do_link = (mode == "link") and file_ext in (".dwg", ".dxf", ".dgn")
                    if do_link:
                        doc.Link(file_path, options, active_view, idref)
                        mode = "link"
                    else:
                        doc.Import(file_path, options, active_view, idref)
                        mode = "import"

                    try:
                        if idref.Value and idref.Value != DB.ElementId.InvalidElementId:
                            result_id = get_element_id_value(idref.Value)
                    except Exception:
                        result_id = None

                else:
                    t.RollBack()
                    return routes.make_response(
                        data={"error": "Unsupported file type '{}'. Supported: DWG, DXF, DGN, SAT, SKP, 3DM, RVT".format(file_ext)},
                        status=400,
                    )

                t.Commit()

                extents = _extents(doc, result_id)
                return routes.make_response(
                    data={
                        "status": "success",
                        "element_id": result_id,
                        "file_name": file_name,
                        "file_type": file_type,
                        "mode": mode,
                        "placement": placement,
                        "extents": extents,
                        "message": "{}ed file '{}' (placement: {}){}".format(
                            mode.capitalize(), file_name, placement, _extents_text(extents)
                        ),
                    }
                )

            except Exception as tx_error:
                if t.HasStarted() and not t.HasEnded():
                    t.RollBack()
                raise tx_error

        except Exception as e:
            logger.error("Failed to link file: {}".format(str(e)))
            error_trace = traceback.format_exc()
            return routes.make_response(
                data={"error": str(e), "traceback": error_trace}, status=500
            )

    logger.info("Interop routes registered successfully")
