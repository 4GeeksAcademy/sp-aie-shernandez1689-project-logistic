"""
TrackFlow MCP Server — OAuth-protected MCP server for incident management
and inventory querying.

Requirements
------------
- fastmcp >= 4.1.0    (MCP server framework)
- httpx >= 0.28.0     (API client for internal services)
- PyJWT[crypto]       (JWT decoding — shared HS256 secret)
- python-dotenv       (local development convenience)

Usage
-----
    export JWT_SECRET_KEY="<shared-secret>"
    python -m mcps.trackflow-server.main

The server listens on ``0.0.0.0:8100`` by default (override via HOST / PORT).

Error codes
-----------
All error responses use a consistent JSON body with ``error`` and
``error_description`` fields, aligned with RFC 6750 / RFC 6749.

HTTP 401 (Autenticación fallida):
  ``missing_auth_header`` — No Bearer token present.
  ``invalid_token``       — Token expirado, malformado, o firma inválida.
  ``invalid_issuer``      — El issuer del token no coincide con el esperado.

HTTP 403 (Autorización insuficiente):
  ``insufficient_scope``  — El token es válido pero no tiene el scope
                             requerido por la tool.

HTTP 422 (Error de validación):
  ``invalid_input``       — Parámetros de la tool inválidos (tipo, rango,
                             formato).

HTTP 500 (Error interno):
  ``internal_error``      — Error inesperado del servidor. Contactar al
                             administrador.

Tool-call logging format
------------------------
Every tool invocation is logged at INFO level:
  TOOL_CALL  tool=<name>  client=<sub>  status=ok|denied|error  ...
"""

from __future__ import annotations

import json as _json
import logging
import os
import time
from collections.abc import AsyncIterator
from contextvars import ContextVar
from typing import Any

import httpx
import jwt
from dotenv import load_dotenv
from fastmcp import FastMCP
from fastmcp.server.lifespan import lifespan as lifespan_decorator
from starlette.applications import Starlette
from starlette.responses import JSONResponse


# =====================================================================
# Structured error codes (RFC 6750 compliant)
# =====================================================================


class ErrorCode:
    """An OAuth/HTTP error code with its HTTP status and description."""

    __slots__ = ("status", "code", "description")

    def __init__(self, status: int, code: str, description: str) -> None:
        self.status = status
        self.code = code
        self.description = description


class Errors:
    """Catalog of all error codes returned by the MCP server."""

    # 401 — Authentication
    MISSING_AUTH_HEADER = ErrorCode(
        401, "missing_auth_header",
        "Missing `Authorization` header. Please provide a valid Bearer token.",
    )
    INVALID_TOKEN = ErrorCode(
        401, "invalid_token",
        "The access token is expired, malformed, or has an invalid signature.",
    )
    INVALID_ISSUER = ErrorCode(
        401, "invalid_issuer",
        "The token issuer claim does not match the expected authorization "
        "server.",
    )

    # 403 — Authorization
    INSUFFICIENT_SCOPE = ErrorCode(
        403, "insufficient_scope",
        "The access token does not have the required scope for this "
        "operation.",
    )

    # 422 — Validation
    INVALID_INPUT = ErrorCode(
        422, "invalid_input",
        "One or more tool parameters are invalid (type, range, or format).",
    )

    # 500 — Internal
    INTERNAL_ERROR = ErrorCode(
        500, "internal_error",
        "An unexpected internal error occurred. Contact the administrator.",
    )


# =====================================================================
# AuthInfo
# =====================================================================


class AuthInfo:
    """Authenticated user / client information carried by a verified token."""

    __slots__ = ("token", "issuer", "client_id", "subject", "audience",
                 "scopes", "claims")

    def __init__(
        self,
        token: str,
        issuer: str,
        client_id: str | None,
        subject: str,
        audience: str,
        scopes: list[str] | None = None,
        claims: dict[str, Any] | None = None,
    ) -> None:
        self.token = token
        self.issuer = issuer
        self.client_id = client_id
        self.subject = subject
        self.audience = audience
        self.scopes = scopes if scopes is not None else []
        self.claims = claims if claims is not None else {}

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("trackflow-mcp")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

load_dotenv()  # also reads .env from CWD / parent dirs

JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "")
JWT_ALGORITHM = "HS256"
API_BASE_URL = os.getenv("API_BASE_URL", "http://127.0.0.1:8000")
INVENTORY_API_BASE_URL = os.getenv(
    "INVENTORY_API_BASE_URL", "http://127.0.0.1:8001"
)

# MCP resource identifier (RFC 8707) — the "aud" claim expected in tokens.
RESOURCE_ID = os.getenv(
    "MCP_RESOURCE_ID",
    "https://mcp.trackflow.dev",
)

# Authorization server identifier — the "iss" claim expected in tokens.
ISSUER = os.getenv(
    "MCP_ISSUER",
    "http://127.0.0.1:8001",
)

HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8100"))

# ---------------------------------------------------------------------------
# Auth context — populated after successful Bearer verification
# ---------------------------------------------------------------------------

auth_context: ContextVar[AuthInfo | None] = ContextVar(
    "mcp_auth_context", default=None
)
client_ip_context: ContextVar[str] = ContextVar(
    "mcp_client_ip", default=""
)


# ---------------------------------------------------------------------------
# JWT verification function (mcpauth-compatible)
# ---------------------------------------------------------------------------

def _verify_token(token: str) -> AuthInfo:
    """Decode and validate a JWT using the shared secret (HS256).

    Reuses the same HS256 key that ``services/user-api`` uses to
    issue tokens.
    """
    try:
        payload: dict[str, Any] = jwt.decode(
            token,
            JWT_SECRET_KEY,
            algorithms=[JWT_ALGORITHM],
            options={"require": ["exp", "sub"]},
        )
    except jwt.ExpiredSignatureError:
        raise ValueError("Token expired")
    except jwt.InvalidTokenError as e:
        raise ValueError(f"Invalid token: {e}")

    # Parse scopes from token — MCP Auth typically sends them as a
    # space-separated string in the "scope" claim.
    raw_scopes = payload.get("scope", "")
    scopes: list[str] = raw_scopes.split() if isinstance(raw_scopes, str) else (
        list(raw_scopes) if isinstance(raw_scopes, (list, tuple)) else []
    )

    return AuthInfo(
        token=token,
        issuer=str(payload.get("iss", ISSUER)),
        client_id=payload.get("azp"),
        subject=str(payload.get("sub", "")),
        audience=payload.get("aud", RESOURCE_ID),
        scopes=scopes,
        claims=payload,
    )


# ---------------------------------------------------------------------------
# FastMCP server instance
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# MCP Server lifecycle — httpx client management
# ---------------------------------------------------------------------------

_client: httpx.AsyncClient | None = None


@lifespan_decorator
async def trackflow_lifespan(server: FastMCP) -> AsyncIterator[dict[str, Any]]:
    """Startup/shutdown lifecycle for the MCP server.

    - Initialises the shared ``httpx.AsyncClient`` for internal API calls.
    - Validates that JWT_SECRET_KEY is configured.
    """
    global _client
    logger.info("TrackFlow MCP Server starting up")
    if not JWT_SECRET_KEY:
        logger.error(
            "JWT_SECRET_KEY is not set — authentication will reject all tokens!"
        )
    _client = httpx.AsyncClient(timeout=30.0)
    try:
        yield {}
    finally:
        await _client.aclose()
        _client = None
        logger.info("TrackFlow MCP Server shut down")


def _get_client() -> httpx.AsyncClient:
    assert _client is not None, "httpx client not initalised"
    return _client


mcp = FastMCP(
    "TrackFlow MCP Server",
    instructions="""\
TrackFlow MCP Server — tools for managing incidents and querying inventory.

**Incident tools** (require ``write:incidents`` or ``read:incidents``):
- ``create_incident`` — create a new incident ticket
- ``update_incident_status`` — transition an incident through its lifecycle
- ``get_incident`` — retrieve a single incident by ID
- ``list_incidents`` — list/filter incidents

**Inventory tools** (read-only, require ``read:inventory``):
- ``get_product`` — retrieve a single SKU with current stock
- ``list_products`` — list all products / filter by warehouse
""",
    version="1.0.0",
    lifespan=trackflow_lifespan,
)


def _build_http_app() -> Starlette:
    """Build the Starlette ASGI app with middleware and metadata endpoint.

    Architecture
    ------------
    A top-level ``Starlette`` app dispatches requests:

    * ``/.well-known/oauth-protected-resource`` → direct response (no auth)
    * Everything else                            → MCP SSE sub-app wrapped
                                                   with Bearer-auth middleware

    This avoids the ``BaseHTTPMiddleware`` / SSE streaming incompatibility
    while keeping the metadata endpoint publicly accessible.
    """
    from starlette.applications import Starlette
    from starlette.responses import Response
    from starlette.routing import Mount, Route
    from starlette.types import ASGIApp, Receive, Scope, Send

    # ------------------------------------------------------------------
    # Pure ASGI Bearer-auth wrapper (no BaseHTTPMiddleware).
    # Works with SSE / streaming responses correctly.
    # ------------------------------------------------------------------
    mcp_sse_app: ASGIApp = mcp.http_app(transport="sse")

    async def _auth_asgi_middleware(
        scope: Scope, receive: Receive, send: Send
    ) -> None:
        """ASGI middleware that validates Bearer tokens before passing
        through to the MCP SSE app."""
        if scope["type"] != "http":
            await mcp_sse_app(scope, receive, send)
            return

        from starlette.datastructures import Headers

        headers = Headers(scope=scope)

        # Capture client IP for audit logging
        client_ip = (
            headers.get("x-forwarded-for", "").split(",")[0].strip()
            or scope.get("client")
            and (scope["client"][0] if isinstance(scope.get("client"), (list, tuple)) else "")
            or ""
        )
        client_ip_context.set(client_ip)

        auth_header = headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            await _send_401(send, "missing_auth_header",
                            "Missing `Authorization` header. Please provide a "
                            "valid bearer token.")
            return

        token = auth_header.removeprefix("Bearer ")
        try:
            auth_info = _verify_token(token)
        except ValueError as e:
            await _send_401(send, "invalid_token", str(e))
            return

        # Validate issuer
        if auth_info.issuer != ISSUER:
            await _send_401(send, "invalid_issuer",
                            f"Token issuer '{auth_info.issuer}' does not match "
                            f"expected issuer '{ISSUER}'.")
            return

        # Store in context var so tools can forward the token
        auth_context.set(auth_info)

        await mcp_sse_app(scope, receive, send)

    async def _send_401(
        send_func: Send, code: str, description: str
    ) -> None:
        """Send a 401 Unauthorized JSON response.

        Called directly with ``await`` from within the ASGI middleware,
        so it runs synchronously with the request lifecycle.
        """
        body_bytes = _json.dumps({
            "error": code,
            "error_description": description,
            "cause": None,
        }).encode()
        await send_func({
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body_bytes)).encode()),
            ],
        })
        await send_func({
            "type": "http.response.body",
            "body": body_bytes,
            "more_body": False,
        })

    top_level = Starlette(
        routes=[
            Route(
                "/.well-known/oauth-protected-resource",
                endpoint=_metadata_route,
                methods=["GET"],
            ),
            Mount("/", app=_auth_asgi_middleware),
        ]
    )
    return top_level


def _protected_resource_metadata() -> dict[str, Any]:
    """Serve the OAuth Protected Resource Metadata document."""
    return {
        "resource": RESOURCE_ID,
        "authorization_servers": [
            {
                "issuer": ISSUER,
                "authorization_endpoint": f"{ISSUER}/auth/login",
                "token_endpoint": f"{ISSUER}/auth/token",
            }
        ],
        "bearer_methods_supported": ["header"],
        "scopes_supported": [
            "read:incidents",
            "write:incidents",
            "read:inventory",
        ],
    }


async def _metadata_route(request: Any) -> JSONResponse:
    """ASGI endpoint for ``/.well-known/oauth-protected-resource``."""
    return JSONResponse(_protected_resource_metadata())


# =====================================================================
# TOOLS — Incidents Manager
# =====================================================================
# Maps to:
#   POST   /api/incidents          — create
#   GET    /api/incidents          — list
#   GET    /api/incidents/{id}     — get by id
#   PATCH  /api/incidents/{id}/status  — status transition (lifecycle)
# =====================================================================


@mcp.tool(
    name="create_incident",
    description="""\
Create a new incident ticket in the TrackFlow Incidents Manager.

Required scope: write:incidents

Parameters
----------
title : str
    Short title (1-120 chars).
description : str
    Full description of the incident.
category : str
    One of: lost_parcel, delivery_failure, inventory_discrepancy,
    carrier_issue, returns_issue, warehouse_incident,
    system_failure, client_complaint, other.
origin : str
    Who reported it: customer, branch, internal.
branch : str
    Affected branch: central, la_warehouse, la_office,
    zaragoza_warehouse, zaragoza_office.

Returns
-------
dict
    The created incident object with id, status, timestamps, etc.
""",
)
async def create_incident(
    title: str,
    description: str,
    category: str,
    origin: str,
    branch: str,
) -> dict[str, Any]:
    """Create a new incident ticket."""
    _require_scope("write:incidents")
    _t0 = time.monotonic_ns()
    _subject = _auth_subject()
    try:
        client = _get_client()
        resp = await client.post(
            f"{API_BASE_URL}/api/incidents",
            json={
                "title": title,
                "description": description,
                "category": category,
                "origin": origin,
                "branch": branch,
            },
            headers=_auth_headers(),
        )
        if resp.status_code >= 400:
            detail = await _safe_error_detail(resp)
            raise _denied(Errors.INVALID_INPUT,
                          upstream_status=resp.status_code,
                          upstream_body=detail)
        result = resp.json()
        _ToolLogger.log("create_incident", "ok",
                        subject=_subject,
                        duration_ms=_elapsed_ms(_t0))
        return result
    except MCPError:
        raise
    except Exception as exc:
        _ToolLogger.log("create_incident", "error",
                        subject=_subject,
                        duration_ms=_elapsed_ms(_t0),
                        detail=str(exc))
        raise _denied(Errors.INTERNAL_ERROR, detail=str(exc)) from exc


@mcp.tool(
    name="get_incident",
    description="""\
Retrieve a single incident by its ID.

Required scope: read:incidents

Parameters
----------
incident_id : str
    The UUID of the incident (e.g. "a1b2c3d4-...").

Returns
-------
dict
    The incident object with all fields.
""",
)
async def get_incident(incident_id: str) -> dict[str, Any]:
    """Get incident by ID."""
    _require_scope("read:incidents")
    _t0 = time.monotonic_ns()
    _subject = _auth_subject()
    try:
        client = _get_client()
        resp = await client.get(
            f"{API_BASE_URL}/api/incidents/{incident_id}",
            headers=_auth_headers(),
        )
        if resp.status_code == 404:
            _ToolLogger.log("get_incident", "not_found",
                            subject=_subject,
                            duration_ms=_elapsed_ms(_t0),
                            detail=f"incident_id={incident_id}")
            return {"error": f"Incident {incident_id} not found"}
        if resp.status_code >= 400:
            detail = await _safe_error_detail(resp)
            raise _denied(Errors.INVALID_INPUT,
                          upstream_status=resp.status_code,
                          upstream_body=detail)
        result = resp.json()
        _ToolLogger.log("get_incident", "ok",
                        subject=_subject,
                        duration_ms=_elapsed_ms(_t0))
        return result
    except MCPError:
        raise
    except Exception as exc:
        _ToolLogger.log("get_incident", "error",
                        subject=_subject,
                        duration_ms=_elapsed_ms(_t0),
                        detail=str(exc))
        raise _denied(Errors.INTERNAL_ERROR, detail=str(exc)) from exc


@mcp.tool(
    name="list_incidents",
    description="""\
List incidents, optionally filtered by status, origin, branch, or category.

Required scope: read:incidents

Parameters
----------
status : str | None
    Filter by status: open, in_progress, resolved, discarded.
origin : str | None
    Filter by origin: customer, branch, internal.
branch : str | None
    Filter by branch: central, la_warehouse, la_office,
    zaragoza_warehouse, zaragoza_office.
category : str | None
    Filter by category: lost_parcel, delivery_failure, ...

Returns
-------
list[dict]
    List of matching incidents (id, title, status, category, origin, branch, created_at).
""",
)
async def list_incidents(
    status: str | None = None,
    origin: str | None = None,
    branch: str | None = None,
    category: str | None = None,
) -> list[dict[str, Any]]:
    """List incidents with optional filters."""
    _require_scope("read:incidents")
    _t0 = time.monotonic_ns()
    _subject = _auth_subject()
    try:
        params: dict[str, str] = {}
        if status:
            params["status"] = status
        if origin:
            params["origin"] = origin
        if branch:
            params["branch"] = branch
        if category:
            params["category"] = category

        client = _get_client()
        resp = await client.get(
            f"{API_BASE_URL}/api/incidents",
            params=params,
            headers=_auth_headers(),
        )
        if resp.status_code >= 400:
            detail = await _safe_error_detail(resp)
            raise _denied(Errors.INVALID_INPUT,
                          upstream_status=resp.status_code,
                          upstream_body=detail)
        result = resp.json()
        _ToolLogger.log("list_incidents", "ok",
                        subject=_subject,
                        duration_ms=_elapsed_ms(_t0))
        return result
    except MCPError:
        raise
    except Exception as exc:
        _ToolLogger.log("list_incidents", "error",
                        subject=_subject,
                        duration_ms=_elapsed_ms(_t0),
                        detail=str(exc))
        raise _denied(Errors.INTERNAL_ERROR, detail=str(exc)) from exc


@mcp.tool(
    name="update_incident_status",
    description="""\
Transition an incident through its lifecycle status.

Required scope: write:incidents

This endpoint enforces the valid state machine:
  OPEN       -> in_progress | discarded
  IN_PROGRESS -> resolved    | discarded
  RESOLVED   -> (terminal)
  DISCARDED  -> (terminal)

Parameters
----------
incident_id : str
    The UUID of the incident.
new_status : str
    The target status: open, in_progress, resolved, discarded.

Returns
-------
dict
    The updated incident with new status and updated_at timestamp.
""",
)
async def update_incident_status(
    incident_id: str,
    new_status: str,
) -> dict[str, Any]:
    """Transition incident to a new status.

    Calls ``PATCH /api/incidents/{id}/status`` — the dedicated lifecycle
    endpoint, **not** a generic resource PATCH.
    """
    _require_scope("write:incidents")
    _t0 = time.monotonic_ns()
    _subject = _auth_subject()
    try:
        client = _get_client()
        resp = await client.patch(
            f"{API_BASE_URL}/api/incidents/{incident_id}/status",
            json={"status": new_status},
            headers=_auth_headers(),
        )
        if resp.status_code >= 400:
            detail = await _safe_error_detail(resp)
            raise _denied(Errors.INVALID_INPUT,
                          upstream_status=resp.status_code,
                          upstream_body=detail)
        result = resp.json()
        _ToolLogger.log("update_incident_status", "ok",
                        subject=_subject,
                        duration_ms=_elapsed_ms(_t0))
        return result
    except MCPError:
        raise
    except Exception as exc:
        _ToolLogger.log("update_incident_status", "error",
                        subject=_subject,
                        duration_ms=_elapsed_ms(_t0),
                        detail=str(exc))
        raise _denied(Errors.INTERNAL_ERROR, detail=str(exc)) from exc


# =====================================================================
# TOOLS — Inventory (read-only)
# =====================================================================
# Maps to:
#   GET  /inventory/products      — list all SKUs
#   GET  /inventory/products/{id} — get single SKU with stock
#
# Any attempt to write to inventory MUST be rejected explicitly.
# =====================================================================


@mcp.tool(
    name="get_product",
    description="""\
Retrieve a single product/SKU with its current stock level.

Required scope: read:inventory

**This is a READ-ONLY tool.** Any modification request will be
explicitly rejected by the server.

Parameters
----------
product_id : int
    Numeric SKU/Product ID (e.g. 1, 42).

Returns
-------
dict
    Product details including id, name, sku, client_name, category,
    warehouse, and current_stock.
""",
)
async def get_product(product_id: int) -> dict[str, Any]:
    """Get product by ID (read-only)."""
    _require_scope("read:inventory")
    _t0 = time.monotonic_ns()
    _subject = _auth_subject()
    try:
        client = _get_client()
        resp = await client.get(
            f"{INVENTORY_API_BASE_URL}/inventory/products/{product_id}",
            headers=_auth_headers(),
        )
        if resp.status_code == 404:
            _ToolLogger.log("get_product", "not_found",
                            subject=_subject,
                            duration_ms=_elapsed_ms(_t0),
                            detail=f"product_id={product_id}")
            return {"error": f"Product {product_id} not found"}
        if resp.status_code >= 400:
            detail = await _safe_error_detail(resp)
            raise _denied(Errors.INVALID_INPUT,
                          upstream_status=resp.status_code,
                          upstream_body=detail)
        result = resp.json()
        _ToolLogger.log("get_product", "ok",
                        subject=_subject,
                        duration_ms=_elapsed_ms(_t0))
        return result
    except MCPError:
        raise
    except Exception as exc:
        _ToolLogger.log("get_product", "error",
                        subject=_subject,
                        duration_ms=_elapsed_ms(_t0),
                        detail=str(exc))
        raise _denied(Errors.INTERNAL_ERROR, detail=str(exc)) from exc


@mcp.tool(
    name="list_products",
    description="""\
List all products/SKUs with current stock levels.

Required scope: read:inventory

**This is a READ-ONLY tool.** Any modification request will be
explicitly rejected by the server. No write operations (create,
update, delete) are available through this tool.

Parameters
----------
warehouse : str | None
    Optional filter by warehouse: "LA" or "ZGZ".

Returns
-------
list[dict]
    List of products with id, name, sku, client_name, category,
    warehouse, and current_stock.
""",
)
async def list_products(
    warehouse: str | None = None,
) -> list[dict[str, Any]]:
    """List products, optionally filtered by warehouse (read-only)."""
    _require_scope("read:inventory")
    _t0 = time.monotonic_ns()
    _subject = _auth_subject()
    try:
        params: dict[str, str] = {}
        if warehouse:
            params["warehouse"] = warehouse

        client = _get_client()
        resp = await client.get(
            f"{INVENTORY_API_BASE_URL}/inventory/products",
            params=params,
            headers=_auth_headers(),
        )
        if resp.status_code >= 400:
            detail = await _safe_error_detail(resp)
            raise _denied(Errors.INVALID_INPUT,
                          upstream_status=resp.status_code,
                          upstream_body=detail)
        result = resp.json()
        _ToolLogger.log("list_products", "ok",
                        subject=_subject,
                        duration_ms=_elapsed_ms(_t0))
        return result
    except MCPError:
        raise
    except Exception as exc:
        _ToolLogger.log("list_products", "error",
                        subject=_subject,
                        duration_ms=_elapsed_ms(_t0),
                        detail=str(exc))
        raise _denied(Errors.INTERNAL_ERROR, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _auth_headers() -> dict[str, str]:
    """Build auth headers from the current request's Bearer token.

    Because the middleware has already verified the token, we forward
    it to internal APIs so they can re-validate it.
    """
    info = auth_context.get()
    if info is not None:
        return {"Authorization": f"Bearer {info.token}"}
    return {}


class MCPError(Exception):
    """Exception that FastMCP converts to an MCP JSON-RPC error response.

    The ``status`` field maps to the JSON-RPC error code range we reserve:
    - -32000 … -32099  (server errors, we use -32000 = auth denied,
        -32001 = forbidden, -32002 = validation)
    - ``message`` is the human-readable error title.
    - ``data`` carries the structured ``{error, error_description}`` body.
    """

    def __init__(
        self,
        status: int,
        message: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        self.status = status
        self.message = message
        self.data = data or {}
        super().__init__(self.message)


_MCP_RPC_AUTH_ERROR = -32000
_MCP_RPC_FORBIDDEN = -32001
_MCP_RPC_VALIDATION = -32002
_MCP_RPC_INTERNAL = -32003


def _denied(
    err: ErrorCode,
    **extra: Any,
) -> MCPError:
    """Build an MCPError from a canned ErrorCode with optional extras."""
    return MCPError(
        status={
            401: _MCP_RPC_AUTH_ERROR,
            403: _MCP_RPC_FORBIDDEN,
            422: _MCP_RPC_VALIDATION,
            500: _MCP_RPC_INTERNAL,
        }.get(err.status, _MCP_RPC_INTERNAL),
        message=err.code,
        data={"error": err.code, "error_description": err.description,
              **extra},
    )


def _require_scope(*required: str) -> None:
    """Check that the authenticated client has *at least one* of the
    required scopes.

    Raises ``MCPError`` (→ JSON-RPC error) if the token lacks all of them.
    Must be called from within a tool handler (where ``auth_context`` is
    set by the ASGI middleware).
    """
    info = auth_context.get()
    if info is None:
        raise _denied(Errors.MISSING_AUTH_HEADER)
    if not required:
        return  # no scope requirement = allowed
    if not any(s in info.scopes for s in required):
        raise _denied(
            Errors.INSUFFICIENT_SCOPE,
            required_scopes=list(required),
            token_scopes=info.scopes,
        )


class _ToolLogger:
    """Structured logger for tool-call audit trail."""

    @staticmethod
    def log(
        tool_name: str,
        status: str,
        *,
        subject: str | None = None,
        duration_ms: int | None = None,
        detail: str | None = None,
    ) -> None:
        """Emit a structured INFO log line for a tool invocation."""
        ip = client_ip_context.get() or "-"
        parts = [
            f"TOOL_CALL  tool={tool_name}",
            f"client={subject or 'unknown'}",
            f"ip={ip}",
            f"status={status}",
        ]
        if duration_ms is not None:
            parts.append(f"duration_ms={duration_ms}")
        if detail:
            parts.append(f"detail={detail}")
        logger.info("  ".join(parts))


def _auth_subject() -> str:
    """Return the ``sub`` claim from the current request context,
    or ``"unknown"`` if no auth context exists."""
    info = auth_context.get()
    return info.subject if info else "unknown"


def _elapsed_ms(start_ns: int) -> int:
    """Return elapsed wall-clock time in milliseconds since *start_ns*."""
    return (time.monotonic_ns() - start_ns) // 1_000_000


async def _safe_error_detail(resp: httpx.Response) -> str:
    """Safely extract a short error detail from an HTTP error response."""
    try:
        body = resp.json()
        if isinstance(body, dict):
            return str(body.get("detail") or body.get("error") or
                       body.get("message") or str(body))
        return str(body)
    except Exception:
        return resp.text[:500] if resp.text else f"HTTP {resp.status_code}"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Run the MCP server via HTTP (SSE transport) with OAuth middleware."""
    import uvicorn

    app = _build_http_app()
    logger.info(
        "Starting TrackFlow MCP Server on %s:%s  (resource=%s, issuer=%s)",
        HOST,
        PORT,
        RESOURCE_ID,
        ISSUER,
    )
    uvicorn.run(
        app,
        host=HOST,
        port=PORT,
        log_level="info",
        lifespan="on",
    )


if __name__ == "__main__":
    main()
