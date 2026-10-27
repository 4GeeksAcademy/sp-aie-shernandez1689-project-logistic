# TrackFlow MCP Server

MCP (Model Context Protocol) server that exposes TrackFlow's internal APIs as discoverable tools for AI agents.

## Features

- **OAuth 2.1 authentication** via pure ASGI Bearer-auth middleware — validates JWT tokens (HS256) at the protocol level, compatible with any OAuth 2.1 / OIDC provider; exposes OAuth Protected Resource Metadata per RFC 9728
- **Incident management** — create, read, list, and status-transition incidents via the dedicated lifecycle endpoint
- **Read-only inventory** — query products and stock levels; any write attempt is explicitly rejected

## Quick start

```bash
# 1. Copy environment config
cp .env.example .env
# 2. Edit .env — especially JWT_SECRET_KEY (must match services/user-api/.env)
# 3. Install
pip install -r requirements.txt
# 4. Launch
python -m mcps.trackflow-server.main
```

The server listens on `http://0.0.0.0:8100` (SSE transport).

## Architecture

```
                    ┌──────────────────────┐
                    │   AI Agent / Client   │
                    └──────┬───────────────┘
                           │  Bearer JWT (MCP)
                           ▼
           ┌───────────────────────────────┐
           │  ASGI Bearer-auth middleware  │
           │  (validates iss, aud, exp)    │
           └──────────┬────────────────────┘
                      │  authenticated
                      ▼
           ┌───────────────────────────────┐
           │    FastMCP (MCP Server)        │
           │  tools:   create_incident     │
           │           get_incident        │
           │           list_incidents      │
           │           update_incident_    │
           │             status            │
           │           get_product         │
           │           list_products       │
           └──────────┬────────────────────┘
                      │  internal API calls
                      ▼
        ┌──────────────┴──────────────┐
        │  Operations API (:8000)     │
        │  User API       (:8001)     │
        └─────────────────────────────┘
```

### OAuth Protected Resource Metadata

The server exposes an [RFC 9728](https://www.rfc-editor.org/rfc/rfc9728) Protected Resource Metadata document at:

```
GET /.well-known/oauth-protected-resource
```

This allows MCP clients to discover the authorization server, supported scopes, and bearer methods automatically.

## Tools

All tools are fully documented with descriptions, input schemas, and output schemas. Any MCP client can discover them via the standard `tools/list` request.

### Incidents

| Tool                    | Scope             | Description                                  |
| ----------------------- | ----------------- | -------------------------------------------- |
| `create_incident`       | `write:incidents` | Create a new incident ticket                 |
| `get_incident`          | `read:incidents`  | Retrieve a single incident by ID             |
| `list_incidents`        | `read:incidents`  | List incidents with optional filters         |
| `update_incident_status`| `write:incidents` | Transition status via the lifecycle endpoint |

### Inventory (read-only)

| Tool             | Scope              | Description                                   |
| ---------------- | ------------------ | --------------------------------------------- |
| `get_product`    | `read:inventory`   | Get a single product/SKU with current stock   |
| `list_products`  | `read:inventory`   | List all products, optionally by warehouse    |

> **Read-only enforcement**: Inventory tools only issue `GET` requests. Any attempt to create, update, or delete inventory data is **explicitly rejected** by the server — the tools don't expose write operations at all.

## Configuration

| Variable                  | Default                        | Description                                |
| ------------------------- | ------------------------------ | ------------------------------------------ |
| `JWT_SECRET_KEY`          | *(required)*                   | HS256 key shared with `services/user-api`  |
| `API_BASE_URL`            | `http://127.0.0.1:8000`        | Operations API (incidents)                 |
| `INVENTORY_API_BASE_URL`  | `http://127.0.0.1:8001`        | User API (inventory)                       |
| `MCP_RESOURCE_ID`         | `https://mcp.trackflow.dev`    | Expected `aud` claim in JWT                |
| `MCP_ISSUER`              | `http://127.0.0.1:8001`        | Expected `iss` claim in JWT                |
| `HOST`                    | `0.0.0.0`                      | Listen address                             |
| `PORT`                    | `8100`                         | Listen port                                |

## Status lifecycle

```
OPEN ──► IN_PROGRESS ──► RESOLVED
  │                        │
  └────► DISCARDED     DISCARDED
```

Transitions are enforced server-side by the dedicated `PATCH /api/incidents/{id}/status` endpoint, **not** by a generic `PATCH` on the incident resource.

## Error codes

All error responses use a consistent JSON body with ``error`` and
``error_description`` fields, aligned with RFC 6750 / RFC 6749.

| HTTP | Error code             | Meaning                                              |
| ---- | ---------------------- | ---------------------------------------------------- |
| 401  | `missing_auth_header`  | No Bearer token was sent in the `Authorization` header. |
| 401  | `invalid_token`        | Token expirado, malformado, o firma inválida.        |
| 401  | `invalid_issuer`       | The `iss` claim doesn't match `MCP_ISSUER`.          |
| 403  | `insufficient_scope`   | Token is valid but lacks the required scope for the tool. |
| 422  | `invalid_input`        | Tool parameters are invalid (type, range, format).   |
| 500  | `internal_error`       | Unexpected server error — contact the administrator. |

Example error body:

```json
{
  "error": "insufficient_scope",
  "error_description": "The access token does not have the required scope for this operation.",
  "required_scopes": ["write:incidents"],
  "token_scopes": ["read:incidents"]
}
```

## Security model

### Authentication (OAuth via MCP Auth)

1. **Transport-level**: The server only accepts HTTPS connections in production. The ASGI middleware rejects any request without a valid `Authorization: Bearer <token>` header.
2. **Token validation**: Every request is verified against the shared `JWT_SECRET_KEY` (HS256). The server checks `exp`, `sub`, `iss`, and `aud` claims per RFC 6750.
3. **Public endpoint**: The metadata endpoint (`/.well-known/oauth-protected-resource`) is the **only** public path — no auth required.

### Authorization (mínimo privilegio)

Each tool defines the **minimum set of scopes** it needs:

| Tool                    | Required scope        | Privilege |
| ----------------------- | --------------------- | --------- |
| `create_incident`       | `write:incidents`     | Write     |
| `update_incident_status`| `write:incidents`     | Write     |
| `get_incident`          | `read:incidents`      | Read      |
| `list_incidents`        | `read:incidents`      | Read      |
| `get_product`           | `read:inventory`      | Read      |
| `list_products`         | `read:inventory`      | Read      |

A client with **any one** of the required scopes may invoke the tool (OR semantics). Tools return `403 insufficient_scope` if none of the required scopes are present in the token's `scope` claim.

### Audit logging

Every tool invocation produces a structured log line at `INFO` level:

```
TOOL_CALL  tool=create_incident  client=user1  ip=192.168.1.10  \
  status=ok  duration_ms=127
TOOL_CALL  tool=get_product  client=user1  ip=192.168.1.10  \
  status=not_found  duration_ms=34  detail=product_id=42
TOOL_CALL  tool=list_incidents  client=user2  ip=10.0.0.5  \
  status=denied  duration_ms=2  detail=insufficient_scope
```

Fields:
- `tool` — name of the invoked MCP tool
- `client` — JWT `sub` claim (client identity)
- `ip` — client IP address (from `X-Forwarded-For` or direct socket)
- `status` — `ok`, `denied`, `not_found`, or `error`
- `duration_ms` — wall-clock execution time in milliseconds
- `detail` — additional context (error message, resource ID, etc.)

## Development

### Running tests

```bash
# Unit tests (requires shared Python env)
cd ../../ && python -m pytest tests/ -v -k "mcp"

# Integration test (standalone)
JWT_SECRET_KEY="your-secret" python -c "
from mcps.trackflow-server.main import _build_http_app
app = _build_http_app()
import uvicorn
uvicorn.run(app, host='127.0.0.1', port=8100)
"
```
