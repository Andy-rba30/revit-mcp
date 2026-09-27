# -*- coding: UTF-8 -*-
"""
Document Module for Revit MCP
Save / persistence operations for the active document.

Note: Save / SaveAs must NOT run inside a Transaction — this handler
deliberately does not open one. Si pasa por escritura.ejecutar para que,
antes de sobrescribir el archivo, quede la copia del ultimo guardado en
backups\\ y la accion en mcp_log.jsonl. Acepta `simular`.
"""

from pyrevit import routes, revit, DB
from seguridad import requiere_token
from escritura import ejecutar, simulacion, EscrituraRechazada, ruta_documento, _fecha_archivo, es_compartido
import os
import logging

logger = logging.getLogger(__name__)


def _estado_archivo(ruta):
    if not ruta or not os.path.isfile(ruta):
        return {"path": ruta or "", "exists": False}
    return {
        "path": ruta,
        "exists": True,
        "last_saved": _fecha_archivo(ruta),
        "size_kb": int(os.path.getsize(ruta) / 1024),
    }


def register_document_routes(api):
    """Register document persistence routes with the API."""

    @api.route("/save_document/", methods=["POST"])
    @requiere_token
    def save_document(doc, request):
        """
        Save the active document. If a file_path is given (or the document has
        never been saved, e.g. it was started from a template), performs SaveAs;
        otherwise saves in place.

        Payload (all optional):
        {
            "file_path": "C:\\\\path\\\\to\\\\Model.rvt",
            "overwrite": true,
            "simular": false
        }
        """

        def cuerpo(ctx):
            data = ctx["data"]
            file_path = data.get("file_path")
            overwrite = bool(data.get("overwrite", True))
            path_on_disk = ruta_documento(doc)

            # "Guardar como" sobre la misma ruta es un guardado normal (Revit rechaza
            # SaveAs sobre el archivo que tiene abierto).
            if file_path and path_on_disk:
                try:
                    if os.path.normcase(os.path.abspath(file_path)) == os.path.normcase(os.path.abspath(path_on_disk)):
                        file_path = None
                except Exception:
                    pass

            if file_path and es_compartido(doc):
                # SaveAs en un modelo de trabajo compartido exige WorksharingSaveAsSettings y
                # crea o desliga un central: se deja para hacerlo a mano en Revit.
                raise EscrituraRechazada(
                    "Workshared model: 'Save As' from the MCP is not supported (it would create a new "
                    "central or detach the model). Save in place without file_path, or do it in Revit.",
                    400,
                )

            if file_path:
                operacion = "save_as"
                destino = file_path
                if os.path.exists(file_path) and not overwrite:
                    raise EscrituraRechazada(
                        "File already exists and overwrite is false: {}".format(file_path), 400
                    )
            elif path_on_disk:
                operacion = "save"
                destino = path_on_disk
            else:
                raise EscrituraRechazada(
                    "Document has never been saved — provide a file_path to save it "
                    "(e.g. C:\\\\Models\\\\ESB.rvt).",
                    400,
                )

            antes = _estado_archivo(destino)
            if ctx["simular"]:
                return simulacion([{
                    "accion": operacion, "file_path": destino, "overwrite": overwrite, "antes": antes,
                }])

            if operacion == "save_as":
                try:
                    parent = os.path.dirname(file_path)
                    if parent and not os.path.exists(parent):
                        os.makedirs(parent)
                except Exception as mk_err:
                    logger.warning("Could not create directory: {}".format(str(mk_err)))
                save_opts = DB.SaveAsOptions()
                save_opts.OverwriteExistingFile = overwrite
                model_path = DB.ModelPathUtils.ConvertUserVisiblePathToModelPath(file_path)
                doc.SaveAs(model_path, save_opts)
            else:
                doc.Save()

            despues = _estado_archivo(ruta_documento(doc) or destino)
            coincide = despues["exists"] and (
                antes.get("last_saved") != despues.get("last_saved") or not antes["exists"]
            )
            resultado = {
                "status": "success",
                "operation": operacion,
                "file_path": despues["path"],
                "antes": antes,
                "despues": despues,
                "ok": coincide,
                "message": "Document saved to {}".format(despues["path"]),
            }
            resultado["verificacion"] = (
                {"coincide": True} if coincide else
                {"coincide": False, "detalle": "The file on disk did not change after Save ({})".format(despues)}
            )
            return resultado

        return ejecutar(doc, "/save_document/", request, cuerpo)

    logger.info("Document routes registered successfully")
