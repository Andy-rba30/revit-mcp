# -*- coding: UTF-8 -*-
"""
Documentation Module for Revit MCP
Handles sheet creation, schedule creation, and document export.

create_sheet y create_schedule pasan por escritura.ejecutar (copia, log,
simular, IA:, creados). export_document no modifica el modelo: solo usa la
transaccion "IA: Exportar documento" que Revit exige para ExportImage.
"""

from utils import get_element_name, get_element_id_value, suppress_warnings
from seguridad import requiere_token
from escritura import ejecutar, transaccion, simulacion, EscrituraRechazada, resultado_creacion, nombre_transaccion
from pyrevit import routes, revit, DB
import json
import traceback
import logging
import os

logger = logging.getLogger(__name__)


def cajetines(doc):
    """FamilySymbol de cajetin (OST_TitleBlocks) cargados en el proyecto."""
    return list(
        DB.FilteredElementCollector(doc)
        .OfCategory(DB.BuiltInCategory.OST_TitleBlocks)
        .OfClass(DB.FamilySymbol)
        .ToElements()
    )


def elegir_cajetin(doc, title_block_name=None):
    """Cajetin por nombre de tipo (o el primero). Lanza EscrituraRechazada(404).

    Lo reutiliza macros.create_sheet_set."""
    title_blocks = cajetines(doc)
    if not title_blocks:
        raise EscrituraRechazada(
            "No title block families found — load a title block family into the project", 404
        )
    if title_block_name:
        for tb in title_blocks:
            try:
                if get_element_name(tb) == title_block_name:
                    return tb
            except Exception:
                continue
        raise EscrituraRechazada("Title block '{}' not found".format(title_block_name), 404)
    return title_blocks[0]


def crear_plano(doc, title_block, sheet_number=None, sheet_name=None):
    """DB.ViewSheet.Create con el cajetin (activandolo si hace falta), numero y nombre.

    Lo reutiliza macros.create_sheet_set."""
    if not title_block.IsActive:
        title_block.Activate()
        doc.Regenerate()
    new_sheet = DB.ViewSheet.Create(doc, title_block.Id)
    if sheet_number:
        new_sheet.SheetNumber = sheet_number
    if sheet_name:
        new_sheet.Name = sheet_name
    return new_sheet


def register_documentation_routes(api):
    """Register all documentation routes with the API"""

    @api.route("/create_sheet/", methods=["POST"])
    @requiere_token
    def create_sheet_handler(doc, request):
        """Create a drawing sheet in Revit. Accepts `simular`."""

        def cuerpo(ctx):
            data = ctx["data"]
            sheet_number = data.get("sheet_number")
            sheet_name = data.get("sheet_name", "Unnamed Sheet")
            title_block_name = data.get("title_block_name")

            target_tb = elegir_cajetin(doc, title_block_name)

            if sheet_number:
                for sheet in DB.FilteredElementCollector(doc).OfClass(DB.ViewSheet).ToElements():
                    try:
                        if sheet.SheetNumber == sheet_number:
                            raise EscrituraRechazada(
                                "Sheet number '{}' already exists in the project".format(sheet_number), 400
                            )
                    except EscrituraRechazada:
                        raise
                    except Exception:
                        continue

            tb_name = get_element_name(target_tb)
            if ctx["simular"]:
                return simulacion([{
                    "accion": "crear", "element_type": "sheet", "sheet_number": sheet_number,
                    "sheet_name": sheet_name, "title_block": tb_name,
                }])

            with transaccion(doc, "Crear plano {}".format(sheet_number or sheet_name)):
                new_sheet = crear_plano(doc, target_tb, sheet_number, sheet_name)
                sheet_id = get_element_id_value(new_sheet)

            resultado = resultado_creacion(doc, [sheet_id])
            numero_real = new_sheet.SheetNumber
            nombre_real = new_sheet.Name
            for creado in resultado["creados"]:
                creado["sheet_number"] = numero_real
                creado["sheet_name"] = nombre_real
                creado["title_block"] = tb_name
            resultado["created"] = resultado["creados"][0] if resultado["creados"] else None
            resultado["message"] = "Created sheet {} - {}".format(numero_real, nombre_real)
            if (sheet_number and numero_real != sheet_number) or (sheet_name and nombre_real != sheet_name):
                resultado["ok"] = False
                resultado["verificacion"] = {
                    "coincide": False,
                    "detalle": "Sheet created as {} - {} instead of {} - {}".format(
                        numero_real, nombre_real, sheet_number, sheet_name
                    ),
                }
            return resultado

        return ejecutar(doc, "/create_sheet/", request, cuerpo)

    @api.route("/create_schedule/", methods=["POST"])
    @requiere_token
    def create_schedule_handler(doc, request):
        """Create a schedule view in Revit. Accepts `simular`."""

        def cuerpo(ctx):
            data = ctx["data"]
            category_str = data.get("category")
            fields = data.get("fields")
            schedule_name = data.get("schedule_name")
            if not category_str:
                raise EscrituraRechazada("No category provided", 400)
            try:
                bic = getattr(DB.BuiltInCategory, category_str)
            except AttributeError:
                raise EscrituraRechazada(
                    "Invalid category '{}' — use a valid BuiltInCategory name like OST_Walls, OST_Rooms, OST_Doors".format(category_str),
                    400,
                )
            cat_id = DB.ElementId(bic)
            cat_display = category_str.replace("OST_", "").lower()
            nombre = schedule_name or "{} Schedule".format(category_str.replace("OST_", ""))

            if ctx["simular"]:
                return simulacion([{
                    "accion": "crear", "element_type": "schedule", "category": category_str,
                    "name": nombre, "fields": fields or "(first 5 schedulable fields)",
                    "nota": "Los campos solo se validan al crear la tabla (GetSchedulableFields).",
                }])

            fields_added = []
            fields_failed = []
            available = []
            with transaccion(doc, "Crear tabla {}".format(nombre)):
                schedule = DB.ViewSchedule.CreateSchedule(doc, cat_id)
                schedule.Name = nombre
                sched_def = schedule.Definition
                schedulable_fields = sched_def.GetSchedulableFields()
                for sf in schedulable_fields:
                    try:
                        available.append(sf.GetName(doc))
                    except Exception:
                        continue
                if fields:
                    for field_name in fields:
                        found = False
                        for sf in schedulable_fields:
                            try:
                                if sf.GetName(doc) == field_name:
                                    sched_def.AddField(sf)
                                    fields_added.append(field_name)
                                    found = True
                                    break
                            except Exception:
                                continue
                        if not found:
                            fields_failed.append(field_name)
                else:
                    count = 0
                    for sf in schedulable_fields:
                        if count >= 5:
                            break
                        try:
                            fname = sf.GetName(doc)
                            sched_def.AddField(sf)
                            fields_added.append(fname)
                            count += 1
                        except Exception:
                            continue
                schedule_id = get_element_id_value(schedule)

            row_count = 0
            try:
                table_data = schedule.GetTableData()
                section = table_data.GetSectionData(DB.SectionType.Body)
                row_count = section.NumberOfRows
            except Exception:
                pass

            resultado = resultado_creacion(doc, [schedule_id])
            for creado in resultado["creados"]:
                creado["name"] = schedule.Name
                creado["category"] = cat_display
                creado["fields"] = fields_added
                creado["row_count"] = row_count
            resultado["created"] = resultado["creados"][0] if resultado["creados"] else None
            resultado["message"] = "Created {} schedule with {} field{} and {} row{}".format(
                cat_display, len(fields_added), "s" if len(fields_added) != 1 else "",
                row_count, "s" if row_count != 1 else "",
            )
            if fields_failed:
                resultado["fields_not_found"] = fields_failed
                resultado["available_fields"] = sorted(available)[:30]
                resultado["ok"] = False
                resultado["verificacion"] = {
                    "coincide": False,
                    "detalle": "Schedule created but these fields do not exist: {}".format(fields_failed),
                }
            return resultado

        return ejecutar(doc, "/create_schedule/", request, cuerpo)

    @api.route("/export_document/", methods=["POST"])
    @requiere_token
    def export_document_handler(doc, request):
        """Export a view or sheet to file."""
        try:
            if not doc:
                return routes.make_response(
                    data={"error": "No active Revit document"}, status=503
                )

            data = {}
            if request and request.data:
                data = json.loads(request.data) if isinstance(request.data, str) else request.data

            view_name = data.get("view_name")
            export_format = data.get("format", "pdf")
            resolution = data.get("resolution", 300)

            supported_formats = ["pdf", "png", "jpg", "dwg"]
            if export_format.lower() not in supported_formats:
                return routes.make_response(
                    data={"error": "Format '{}' not supported — use pdf, png, jpg, or dwg".format(export_format)},
                    status=400,
                )

            # Find the view
            target_view = None
            if view_name:
                views = (
                    DB.FilteredElementCollector(doc)
                    .OfClass(DB.View)
                    .ToElements()
                )
                for v in views:
                    try:
                        if get_element_name(v) == view_name:
                            target_view = v
                            break
                    except Exception:
                        continue

                if not target_view:
                    return routes.make_response(
                        data={"error": "View '{}' not found in the project".format(view_name)},
                        status=404,
                    )
            else:
                target_view = doc.ActiveView

            if not target_view:
                return routes.make_response(
                    data={"error": "No view available for export"}, status=400
                )

            actual_view_name = get_element_name(target_view)

            # Determine export path
            export_dir = os.path.join(
                os.environ.get("USERPROFILE", os.environ.get("HOME", "C:\\")),
                "Documents",
                "RevitMCPExport",
            )
            if not os.path.exists(export_dir):
                os.makedirs(export_dir)

            fmt = export_format.lower()

            t = DB.Transaction(doc, nombre_transaccion("Exportar documento"))
            t.Start()
            suppress_warnings(t)

            try:
                file_path = ""
                file_size_kb = 0

                if fmt == "png" or fmt == "jpg":
                    # Image export
                    options = DB.ImageExportOptions()
                    options.ZoomType = DB.ZoomFitType.FitToPage
                    options.PixelSize = resolution
                    options.ExportRange = DB.ExportRange.SetOfViews

                    view_set = DB.ViewSet()
                    view_set.Insert(target_view)

                    # Use ICollection for SetViewsAndSheets
                    from System.Collections.Generic import List
                    view_ids = List[DB.ElementId]()
                    view_ids.Add(target_view.Id)
                    options.SetViewsAndSheets(view_ids)

                    if fmt == "png":
                        options.HLRandWFViewsFileType = DB.ImageFileType.PNG
                    else:
                        options.HLRandWFViewsFileType = DB.ImageFileType.JPGMedium

                    safe_name = actual_view_name.replace(" ", "_").replace("/", "_")
                    options.FilePath = os.path.join(export_dir, safe_name)

                    doc.ExportImage(options)

                    # Find the exported file
                    expected_ext = ".png" if fmt == "png" else ".jpg"
                    file_path = os.path.join(export_dir, safe_name + expected_ext)

                elif fmt == "pdf":
                    # PDF export (Revit 2022+)
                    try:
                        pdf_options = DB.PDFExportOptions()
                        pdf_options.FileName = actual_view_name.replace(" ", "_")
                        pdf_options.Combine = True

                        from System.Collections.Generic import List
                        view_ids = List[DB.ElementId]()
                        view_ids.Add(target_view.Id)

                        success = doc.Export(export_dir, view_ids, pdf_options)

                        if success:
                            file_path = os.path.join(
                                export_dir,
                                actual_view_name.replace(" ", "_") + ".pdf"
                            )
                        else:
                            # Fallback to image
                            t.RollBack()
                            return routes.make_response(
                                data={
                                    "error": "PDF export failed — ensure Revit PDF printer is configured. Try format 'png' as an alternative.",
                                },
                                status=500,
                            )
                    except Exception as pdf_err:
                        t.RollBack()
                        return routes.make_response(
                            data={
                                "error": "PDF export not available: {}. Try format 'png' as an alternative.".format(str(pdf_err)),
                            },
                            status=500,
                        )

                elif fmt == "dwg":
                    # DWG export
                    try:
                        dwg_options = DB.DWGExportOptions()

                        from System.Collections.Generic import List
                        view_ids = List[DB.ElementId]()
                        view_ids.Add(target_view.Id)

                        safe_name = actual_view_name.replace(" ", "_")
                        doc.Export(export_dir, safe_name, view_ids, dwg_options)
                        file_path = os.path.join(export_dir, safe_name + ".dwg")
                    except Exception as dwg_err:
                        t.RollBack()
                        return routes.make_response(
                            data={"error": "DWG export failed: {}".format(str(dwg_err))},
                            status=500,
                        )

                t.Commit()

                # Get file size
                try:
                    if file_path and os.path.exists(file_path):
                        file_size_kb = int(os.path.getsize(file_path) / 1024)
                except Exception:
                    pass

                return routes.make_response(
                    data={
                        "status": "success",
                        "exported": {
                            "view_name": actual_view_name,
                            "format": fmt,
                            "file_path": file_path,
                            "file_size_kb": file_size_kb,
                        },
                        "message": "Exported '{}' to {} ({} KB)".format(
                            actual_view_name, fmt.upper(), file_size_kb
                        ),
                    }
                )

            except Exception as tx_error:
                if t.HasStarted() and not t.HasEnded():
                    t.RollBack()
                raise tx_error

        except Exception as e:
            logger.error("Failed to export document: {}".format(str(e)))
            error_trace = traceback.format_exc()
            return routes.make_response(
                data={"error": str(e), "traceback": error_trace}, status=500
            )

    logger.info("Documentation routes registered successfully")
