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
python main.py
```

The server listens on `http://0.0.0.0:8100` (SSE transport).

## Installation & Setup

### Prerequisites

- **Python**: 3.12+ (required by dependencies)
- **pip** or **uv**: Package manager
- **Environment variables**: JWT_SECRET_KEY and backend API endpoints

### Step-by-step installation

#### 1. Navigate to MCP server directory

```bash
cd mcps/trackflow-server
```

#### 2. Create environment file

```bash
cp .env.example .env
```

Edit `.env` with your configuration:

```env
# REQUIRED: Shared HS256 secret (must match services/user-api/.env)
JWT_SECRET_KEY=your-secret-key-here

# API endpoints (defaults shown)
API_BASE_URL=http://127.0.0.1:8000           # Operations API
INVENTORY_API_BASE_URL=http://127.0.0.1:8001 # User API

# MCP config (defaults shown)
MCP_RESOURCE_ID=https://mcp.trackflow.dev
MCP_ISSUER=http://127.0.0.1:8001

# Server binding (defaults shown)
HOST=0.0.0.0
PORT=8100
```

#### 3. Install dependencies

**Option A: Using pip** (standalone)

```bash
pip install -r requirements.txt
```

**Option B: Using uv** (from monorepo root)

```bash
# From monorepo root
uv pip install --directory mcps/trackflow-server -r mcps/trackflow-server/requirements.txt
```

**Option C: Using uv sync** (if monorepo has pyproject.toml)

```bash
# From monorepo root (if configured with workspaces)
uv sync
```

#### 4. Verify installation

```bash
python -c "import fastmcp; print(f'✓ fastmcp {fastmcp.__version__}')"
python -c "import httpx; print(f'✓ httpx {httpx.__version__}')"
python -c "import jwt; print(f'✓ PyJWT installed')"
```

#### 5. Start the server

```bash
# Option A: Direct Python
export JWT_SECRET_KEY="your-secret-key"
python main.py

# Option B: Module execution
export JWT_SECRET_KEY="your-secret-key"
python -m mcps.trackflow_server.main
```

You should see:

```
2026-11-19 20:04:15,528 [INFO] trackflow-mcp: Starting TrackFlow MCP Server on 0.0.0.0:8100
INFO:     Uvicorn running on http://0.0.0.0:8100
```

#### 6. Verify server is running

```bash
# Check OAuth metadata endpoint (public, no auth required)
curl http://localhost:8100/.well-known/oauth-protected-resource | jq .

# Expected output:
# {
#   "resource": "https://mcp.trackflow.dev",
#   "authorization_servers": [...],
#   "bearer_methods_supported": ["header"],
#   "scopes_supported": ["read:incidents", "write:incidents", "read:inventory"]
# }
```

### Dependencies

#### Core dependencies (in `requirements.txt`)

| Package      | Version    | Purpose                                 |
| ------------ | ---------- | --------------------------------------- |
| `fastmcp`    | `>=4.1.0`  | MCP server framework with SSE transport |
| `httpx`      | `>=0.28.0` | Async HTTP client for backend APIs      |
| `python-dotenv` | `>=1.0.1` | Environment variable management         |
| `PyJWT[crypto]` | `>=2.9.0` | JWT token encoding/decoding (HS256)     |

#### Transitive dependencies (installed with above)

- **uvicorn**: ASGI server for HTTP handling
- **starlette**: Web framework (used by FastMCP)
- **pydantic**: Data validation
- **cryptography**: For JWT crypto operations

#### Verification of installed versions

```bash
pip show fastmcp httpx python-dotenv PyJWT | grep -E "Name|Version"
```

Expected output:

```
Name: fastmcp
Version: 4.2.2
---
Name: httpx
Version: 0.28.1
---
Name: python-dotenv
Version: 1.0.1
---
Name: PyJWT
Version: 2.9.0
```

### Environment configuration

Required variables:

| Variable            | Type   | Example                        | Notes                                      |
| ------------------- | ------ | ------------------------------ | ------------------------------------------ |
| `JWT_SECRET_KEY`    | string | `abc123...` (min 32 bytes)     | **REQUIRED** — Must match `services/user-api` |
| `API_BASE_URL`      | URL    | `http://127.0.0.1:8000`        | Operations API endpoint                    |
| `INVENTORY_API_BASE_URL` | URL | `http://127.0.0.1:8001`        | User API endpoint                          |
| `MCP_RESOURCE_ID`   | URL    | `https://mcp.trackflow.dev`    | OAuth resource identifier                  |
| `MCP_ISSUER`        | URL    | `http://127.0.0.1:8001`        | OAuth authorization server                 |
| `HOST`              | string | `0.0.0.0`                      | Listen address (0.0.0.0 = all interfaces)  |
| `PORT`              | int    | `8100`                         | Listen port                                |

### Troubleshooting installation

#### Error: `ModuleNotFoundError: No module named 'fastmcp'`

**Solution**:
```bash
pip install -r requirements.txt
# Or reinstall specific package:
pip install 'fastmcp>=4.1.0'
```

#### Error: `ModuleNotFoundError: No module named 'jwt'`

**Solution**:
```bash
pip install 'PyJWT[crypto]>=2.9.0'
```

#### Error: `No module named 'uvicorn'`

**Solution**: This is a transitive dependency. Reinstall fastmcp:
```bash
pip install --force-reinstall 'fastmcp>=4.1.0'
```

#### Error: `OSError: [Errno 98] Address already in use`

**Solution**: Port 8100 is in use. Either:

```bash
# Option 1: Use a different port
export PORT=8101
python main.py

# Option 2: Kill the existing process
lsof -i :8100           # Find process
kill -9 <PID>           # Kill it
```

#### Error: `ValueError: Invalid token: Invalid audience`

**Solution**: The JWT token must have matching `aud` claim:

```bash
# When creating tokens, ensure:
# - "aud" claim = "https://mcp.trackflow.dev" (default MCP_RESOURCE_ID)
# Or set MCP_RESOURCE_ID to match your token's "aud"
export MCP_RESOURCE_ID="your-aud-claim"
python main.py
```

### Development setup

For development with hot-reload:

```bash
# Install with dev tools
pip install -r requirements.txt
pip install watchdog[watchmedo]

# Run with auto-reload on file changes
watchmedo auto-restart -d mcps/trackflow-server -p '*.py' -- python mcps/trackflow-server/main.py
```

### Docker setup (optional)

Create `mcps/trackflow-server/Dockerfile`:

```dockerfile
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV HOST=0.0.0.0
ENV PORT=8100
EXPOSE 8100

CMD ["python", "main.py"]
```

Build and run:

```bash
docker build -t trackflow-mcp .
docker run -p 8100:8100 \
  -e JWT_SECRET_KEY="your-secret" \
  -e API_BASE_URL="http://host.docker.internal:8000" \
  trackflow-mcp
```

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
