import os
import sys
import json
import logging
import httpx
import anyio
from mcp.server.mcpserver import MCPServer, Image, Context
import base64
from typing import Optional, Dict, Any, Union

# Instrucciones para el agente (precedencia, flujo obligatorio, reglas de
# dominio, glosario y errores típicos). Viven en INSTRUCCIONES_AGENTE.md, junto
# a este archivo, y se envían al cliente como `instructions` del servidor.
RUTA_INSTRUCCIONES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "INSTRUCCIONES_AGENTE.md")


def leer_instrucciones() -> str:
    try:
        with open(RUTA_INSTRUCCIONES, "r", encoding="utf-8") as archivo:
            return archivo.read()
    except OSError as error:
        logging.getLogger(__name__).warning("No se pudo leer %s: %s", RUTA_INSTRUCCIONES, error)
        return (
            "Usa las herramientas específicas antes que execute_revit_code; ejecuta "
            "los cambios con simular=true, confirma con el usuario y comprueba "
            "antes/despues. Todas las unidades en milímetros."
        )


# Create a generic MCP server for interacting with Revit
# Use stateless_http=True and json_response=True for better compatibility
mcp = MCPServer("Revit MCP Server", instructions=leer_instrucciones())

# Configuration
REVIT_HOST = os.environ.get("REVIT_HOST", "localhost")
REVIT_PORT = 48884  # Default pyRevit Routes port
BASE_URL = f"http://{REVIT_HOST}:{REVIT_PORT}/revit_mcp"

# Token de sesión que startup.py (dentro de Revit) escribe en cada arranque.
# Se envía en cada petición: POST -> clave "token" en el cuerpo JSON,
# GET -> parámetro ?token=. Ver CONTRATO.md.
RUTA_TOKEN = os.path.expandvars(r"%LOCALAPPDATA%\RevitMcp\token")
MENSAJE_SIN_TOKEN = "Revit no está abierto o el conector no ha iniciado"
MENSAJE_TOKEN_CAMBIO = "el token cambió: Revit se reinició, reintenta en unos segundos"

_token_cache: Optional[str] = None

# Tiempos de espera por herramienta (segundos). Cada tools/*_tools.py pasa el
# suyo en revit_get/revit_post(timeout=...); los valores viven en tools/utils.py
# para evitar la importación circular main -> tools -> main.
#
#   TIMEOUT_LECTURA   30 s  consultas (status, listados, propiedades, vistas...)
#   TIMEOUT_ESCRITURA 120 s create_*, transform_elements, color_splash y demás
#                           cambios en el modelo
#   TIMEOUT_LARGO     600 s export_ifc, export_document, check_clashes,
#                           get_material_quantities, link_file, load_family,
#                           save_document, execute_revit_code
from tools.utils import TIMEOUT_LECTURA, TIMEOUT_ESCRITURA, TIMEOUT_LARGO  # noqa: E402,F401

# httpx registra cada URL a nivel INFO y en GET la URL lleva ?token=...;
# se sube el umbral para que el token no acabe en el log del puente.
logging.getLogger("httpx").setLevel(logging.WARNING)


def leer_token(forzar: bool = False) -> Optional[str]:
    """Devuelve el token de %LOCALAPPDATA%\\RevitMcp\\token (cacheado).

    Devuelve None, sin lanzar, si el archivo no existe o está vacío: eso
    significa que Revit no está abierto o la extensión aún no ha arrancado.
    """
    global _token_cache
    if _token_cache and not forzar:
        return _token_cache
    try:
        with open(RUTA_TOKEN, "r", encoding="utf-8") as archivo:
            valor = archivo.read().strip()
    except OSError:
        _token_cache = None
        return None
    _token_cache = valor or None
    return _token_cache


def olvidar_token() -> None:
    """Borra la caché; la siguiente llamada relee el archivo."""
    global _token_cache
    _token_cache = None


# Shared HTTP client with keep-alive connection pooling. Reusing a single
# AsyncClient across all tool calls avoids the per-request TCP/handshake cost
# of creating a new client each time — meaningful when a session fires dozens
# of calls at the local Routes server.
_http_client: Optional[httpx.AsyncClient] = None


def _get_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            base_url=BASE_URL,
            limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
        )
    return _http_client


async def revit_get(endpoint: str, ctx: Context = None, **kwargs) -> Union[Dict, str]:
    """Simple GET request to Revit API"""
    return await _revit_call("GET", endpoint, ctx=ctx, **kwargs)


async def revit_post(endpoint: str, data: Dict[str, Any], ctx: Context = None, **kwargs) -> Union[Dict, str]:
    """Simple POST request to Revit API"""
    return await _revit_call("POST", endpoint, data=data, ctx=ctx, **kwargs)


async def revit_image(endpoint: str, ctx: Context = None) -> Union[Image, str]:
    """GET request that returns an Image object"""
    response = await _enviar_con_token("GET", endpoint, timeout=60.0)
    if isinstance(response, str):
        return response
    if response.status_code == 200:
        try:
            data = response.json()
            image_bytes = base64.b64decode(data["image_data"])
            return Image(data=image_bytes, format="png")
        except Exception as e:
            return f"Error: {e}"
    return f"Error: {response.status_code} - {response.text}"


async def _enviar(method: str, endpoint: str, token: str, data: Dict = None,
                  params: Dict = None, timeout: float = TIMEOUT_LECTURA) -> httpx.Response:
    """Una petición HTTP a Revit con el token incluido (POST: cuerpo; GET: query)."""
    client = _get_client()
    if method == "GET":
        query = dict(params or {})
        query["token"] = token
        return await client.get(endpoint, params=query, timeout=timeout)
    cuerpo = dict(data or {})
    cuerpo["token"] = token
    # JSON solo ASCII (tildes como \u00e9): httpx >= 0.28 manda UTF-8 crudo con
    # json=, y el servidor de pyRevit lo lee como Latin-1 ("genÃ©rico").
    return await client.post(
        endpoint,
        content=json.dumps(cuerpo, ensure_ascii=True).encode("ascii"),
        headers={"Content-Type": "application/json"},
        timeout=timeout,
    )


async def _enviar_con_token(method: str, endpoint: str, data: Dict = None,
                            params: Dict = None, timeout: float = TIMEOUT_LECTURA
                            ) -> Union[httpx.Response, str]:
    """Envía la petición con el token; ante 401 relee el archivo y reintenta una vez.

    Devuelve la respuesta httpx, o un texto explicativo (nunca lanza) cuando no
    hay token, Revit no responde o el token cambió porque Revit se reinició.
    """
    token = leer_token()
    if token is None:
        return MENSAJE_SIN_TOKEN
    for intento in (1, 2):
        try:
            response = await _enviar(method, endpoint, token, data, params, timeout)
        except httpx.ConnectError:
            return MENSAJE_SIN_TOKEN
        except Exception as e:
            return f"Error: {e}"
        if response.status_code != 401:
            return response
        if intento == 1:
            olvidar_token()
            token = leer_token(forzar=True)
            if token is None:
                return MENSAJE_SIN_TOKEN
    return MENSAJE_TOKEN_CAMBIO


async def _revit_call(method: str, endpoint: str, data: Dict = None, ctx: Context = None,
                     timeout: float = TIMEOUT_LECTURA, params: Dict = None) -> Union[Dict, str]:
    """Internal function handling all HTTP calls.

    `timeout` lo fija cada herramienta (tools/*_tools.py) según la tabla de
    tiempos de espera de arriba. Las respuestas de error (400/404/409/500) se
    devuelven como dict con `error` y `http_status` para que el agente reciba
    los detalles (parámetros disponibles, límite, transacción abierta...) en
    lugar de un texto plano.
    """
    try:
        response = await _enviar_con_token(method, endpoint, data=data, params=params, timeout=timeout)
    except httpx.TimeoutException:
        return {
            "error": f"Revit no respondió en {timeout:.0f} s a {endpoint}",
            "status": "error",
            "endpoint": endpoint,
            "details": "La operación puede seguir en curso en Revit; comprueba antes de repetirla.",
        }
    if isinstance(response, str):
        return response
    try:
        if response.status_code == 200:
            return response.json()
        try:
            cuerpo = response.json()
        except Exception:
            cuerpo = None
        if isinstance(cuerpo, dict):
            cuerpo.setdefault("error", f"HTTP {response.status_code}")
            cuerpo["http_status"] = response.status_code
            return cuerpo
        return f"Error: {response.status_code} - {response.text}"
    except Exception as e:
        return f"Error: {e}"


# Register all tools BEFORE the main block
from tools import register_tools
register_tools(mcp, revit_get, revit_post, revit_image)


async def run_combined_async():
    """Run server with both SSE and streamable-http endpoints.

    This allows clients to connect via either:
    - SSE: GET /sse, POST /messages/
    - Streamable-HTTP: POST/GET /mcp
    """
    import uvicorn

    # Get the streamable-http app first - it has the proper lifespan
    # that initializes the session manager's task group.
    # host="127.0.0.1" activa en el SDK mcp 2.2 la protección contra DNS
    # rebinding (allowed_hosts 127.0.0.1:*, localhost:*, [::1]:* y los
    # allowed_origins equivalentes; cualquier otro Host/Origin recibe 421).
    # Estas fábricas no aceptan port=: el puerto lo fija uvicorn más abajo.
    http_app = mcp.streamable_http_app(host="127.0.0.1", stateless_http=True, json_response=True)

    # Get SSE routes (SSE doesn't need special lifespan - it creates
    # task groups per-request in connect_sse())
    sse_app = mcp.sse_app(host="127.0.0.1")

    # Add SSE routes to the http app (preserving its lifespan)
    for route in sse_app.routes:
        http_app.routes.append(route)

    config = uvicorn.Config(
        http_app,
        host="127.0.0.1",
        port=8000,
        log_level="info",
    )
    server = uvicorn.Server(config)
    await server.serve()


if __name__ == "__main__":
    if "--sse" in sys.argv:
        mcp.run(transport="sse", host="127.0.0.1", port=8000)
    elif "--http" in sys.argv or "--streamable-http" in sys.argv:
        mcp.run(transport="streamable-http", host="127.0.0.1", port=8000, stateless_http=True, json_response=True)
    elif "--combined" in sys.argv:
        # Run both SSE and streamable-http transports simultaneously
        print("Starting combined server with SSE (/sse, /messages/) and streamable-http (/mcp) endpoints...")
        anyio.run(run_combined_async)
        sys.exit(0)
    else:
        mcp.run(transport="stdio")
