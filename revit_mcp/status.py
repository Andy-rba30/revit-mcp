# -*- coding: UTF-8 -*-
"""
Status Module for Revit MCP
Handles API status and health check endpoints
"""

from pyrevit import routes
from seguridad import requiere_token
import logging

logger = logging.getLogger(__name__)

def register_status_routes(api):
    """Register all status-related routes with the API"""
    
    @api.route('/ping/', methods=["GET"])
    def revit_ping():
        """Sin token: solo dice que la extension esta cargada y escuchando.

        Es lo que debe sondear un cliente mientras Revit arranca: no revela
        nada del modelo y no genera avisos de 401. Despues, releer el token
        (cambia en cada arranque) y llamar a /status/."""
        return routes.make_response(data={"ok": True, "api_name": "revit_mcp"})

    @api.route('/status/', methods=["GET"])
    @requiere_token
    def revit_status(doc):
        """
        Health check endpoint that verifies Revit context availability
        
        Returns:
            dict: Health status with Revit document information
        """
        try:
            from pyrevit import revit

            # `doc` en la firma: pyRevit ejecuta la ruta en el contexto de la API
            if doc is None:
                doc = revit.doc
            if doc:
                return routes.make_response(data={
                    "status": "active",
                    "health": "healthy",
                    "revit_available": True,
                    "document_title": doc.Title if doc.Title else "Untitled",
                    "api_name": "revit_mcp"
                })
            else:
                return routes.make_response(data={
                    "status": "unhealthy", 
                    "revit_available": False,
                    "error": "No active Revit document",
                    "api_name": "revit_mcp"
                }, status=503)
                
        except Exception as e:
            logger.error("Health check failed:{}".format(str(e)))
            return routes.make_response(data={
                "status": "unhealthy",
                "revit_available": False, 
                "error": str(e),
                "api_name": "revit_mcp"
            }, status=503)
    
    logger.info("Status routes registered successfully")