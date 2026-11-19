# `mcps` folder

**Model Context Protocol (MCP) Servers — Bridge between AI agents and TrackFlow internal systems**

This folder contains implementations of the [Model Context Protocol](https://spec.modelcontextprotocol.io/) (MCP) servers that expose tools, resources, and context to AI models and agents.

---

## Overview

MCP servers act as **adapters that make business APIs discoverable and usable by AI agents**. They provide:

- **Tool discovery** — `tools/list` returns available operations with schemas
- **Type-safe invocation** — JSON schemas define input/output contracts
- **OAuth authentication** — Bearer tokens protect sensitive operations
- **Consistent error handling** — Standardized RFC 6750 / RFC 6749 error responses
- **Audit logging** — Every tool call logged with client, timestamp, duration

---

## Available MCP Servers

### 1. **TrackFlow MCP Server**

**Directory**: [`./trackflow-server/`](./trackflow-server/)

**Purpose**: Exposes incident management and inventory tools

**Status**: ✅ Production-ready

**Transport**: HTTP with Server-Sent Events (SSE)

**Listen**: `http://0.0.0.0:8100`

#### Exposed tools

| Category   | Tool                     | Scope             | Description                           |
| ---------- | ------------------------ | ----------------- | ------------------------------------- |
| **Incidents** | `create_incident`      | `write:incidents` | Create new support ticket             |
|            | `get_incident`           | `read:incidents`  | Retrieve incident by ID               |
|            | `list_incidents`         | `read:incidents`  | List incidents with filters           |
|            | `update_incident_status` | `write:incidents` | Transition lifecycle status           |
| **Inventory** | `get_product`          | `read:inventory`  | Get product/SKU with current stock    |
|            | `list_products`          | `read:inventory`  | List products, optionally filtered    |

#### Quick start

```bash
cd trackflow-server
pip install -r requirements.txt
export JWT_SECRET_KEY="dev-secret"
python main.py
```

**Full docs**: 
- [`README.md`](./trackflow-server/README.md) — Tool documentation & architecture
- [`INSTALL.md`](./trackflow-server/INSTALL.md) — Complete installation guide
- [`.env.example`](./trackflow-server/.env.example) — Configuration template

---

## Installation Guide

### Method 1: Standalone (Local development)

```bash
cd trackflow-server
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python main.py
```

### Method 2: Monorepo (Full project)

```bash
# From project root
uv pip install --directory mcps/trackflow-server -r mcps/trackflow-server/requirements.txt

# Or use uv sync if configured
uv sync
```

### Method 3: Docker (Production)

```bash
docker build -t trackflow-mcp:latest trackflow-server/
docker run -p 8100:8100 \
  -e JWT_SECRET_KEY="production-secret" \
  trackflow-mcp:latest
```

**See [`trackflow-server/INSTALL.md`](./trackflow-server/INSTALL.md) for detailed installation instructions.**

---

## Architecture

```
┌──────────────────────────────┐
│   AI Agent / LLM Client      │
│ (Claude, GPT, Custom Agent)  │
└────────┬─────────────────────┘
         │  Bearer JWT (MCP Auth)
         ▼
┌──────────────────────────────┐
│   MCP Client (SSE)           │
│ • tools/list                 │
│ • tools/call                 │
│ • Session management         │
└────────┬─────────────────────┘
         │  HTTP/SSE (port 8100)
         ▼
┌──────────────────────────────┐
│  TrackFlow MCP Server        │
│ • ASGI Bearer Auth           │
│ • Tool implementations       │
│ • Error handling             │
└────────┬─────────────────────┘
         │  Internal APIs (HTTP)
         ▼
   ┌─────┴──────┐
   │            │
   ▼            ▼
 API (:8000)  User API (:8001)
 Incidents    Inventory
```

---

## Configuration

### Required environment variables

```bash
# REQUIRED: JWT secret (min 32 chars)
export JWT_SECRET_KEY="your-secret-key-here"
```

### Optional environment variables

```bash
# Backend API endpoints
export API_BASE_URL=http://127.0.0.1:8000
export INVENTORY_API_BASE_URL=http://127.0.0.1:8001

# MCP OAuth configuration
export MCP_RESOURCE_ID=https://mcp.trackflow.dev
export MCP_ISSUER=http://127.0.0.1:8001

# Server binding
export HOST=0.0.0.0
export PORT=8100
```

### Using .env file

```bash
cd trackflow-server
cp .env.example .env
# Edit .env with your values
python main.py
```

---

## Verification

### Check dependencies

```bash
cd trackflow-server
pip list | grep -E "fastmcp|httpx|PyJWT|python-dotenv"
```

### Start server

```bash
cd trackflow-server
export JWT_SECRET_KEY="test-secret"
python main.py
```

**Expected output:**
```
2026-11-19 20:04:15,528 [INFO] trackflow-mcp: Starting TrackFlow MCP Server on 0.0.0.0:8100
INFO:     Uvicorn running on http://0.0.0.0:8100
```

### Test endpoint

```bash
curl http://localhost:8100/.well-known/oauth-protected-resource | jq .
```

**Expected response:**
```json
{
  "resource": "https://mcp.trackflow.dev",
  "authorization_servers": [...],
  "bearer_methods_supported": ["header"],
  "scopes_supported": ["read:incidents", "write:incidents", "read:inventory"]
}
```

---

## Common Workflows

### Workflow 1: List incidents

```bash
TOKEN="your-jwt-token"
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8100/mcp/tools/call \
  -X POST -d '{
    "tool": "list_incidents",
    "params": {"status": "open"}
  }'
```

### Workflow 2: Get inventory

```bash
TOKEN="your-jwt-token"
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8100/mcp/tools/call \
  -X POST -d '{
    "tool": "list_products",
    "params": {"warehouse": "zaragoza_warehouse"}
  }'
```

### Workflow 3: Create incident

```bash
TOKEN="your-jwt-token"
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8100/mcp/tools/call \
  -X POST -d '{
    "tool": "create_incident",
    "params": {
      "title": "Payment error",
      "description": "Order processing failed",
      "category": "payment_issue",
      "origin": "web",
      "branch": "los_angeles"
    }
  }'
```

---

## Troubleshooting

### "Module not found" error

```bash
pip install -r trackflow-server/requirements.txt
```

### "Address already in use"

```bash
lsof -i :8100           # Find process
kill -9 <PID>           # Kill it
# Or use different port:
export PORT=8101 && python trackflow-server/main.py
```

### "Invalid token" error

1. Set `JWT_SECRET_KEY`: `export JWT_SECRET_KEY="your-secret"`
2. Verify token's `aud` claim matches `MCP_RESOURCE_ID`
3. Verify token hasn't expired

### Backend API not responding

```bash
# Terminal 1: Operations API
cd services/api
python -m uvicorn app.main:app --port 8000

# Terminal 2: User API
cd services/user-api
python -m uvicorn app.main:app --port 8001

# Terminal 3: MCP Server
cd mcps/trackflow-server
python main.py
```

---

## Documentation Structure

Each MCP server folder contains:

```
mcps/trackflow-server/
├── README.md           # Main documentation
├── INSTALL.md          # Detailed installation guide
├── requirements.txt    # Python dependencies
├── .env.example        # Configuration template
├── main.py             # MCP server implementation
└── __init__.py         # Python module marker
```

---

## Development

### Adding a new MCP server

1. Create folder: `mcps/my-new-server/`
2. Use [`trackflow-server/`](./trackflow-server/) as template
3. Create:
   - `main.py` with FastMCP server and tools
   - `requirements.txt` with dependencies
   - `README.md` with documentation
   - `.env.example` with configuration
4. Update this README

### Testing

```bash
python -c "
import asyncio
from mcp.client.sse import sse_client
from mcp import ClientSession
import jwt
from datetime import datetime, timedelta, timezone

# Generate token
secret = 'your-secret'
payload = {
    'sub': 'test-user',
    'scope': 'read:incidents write:incidents read:inventory',
    'iss': 'http://127.0.0.1:8001',
    'aud': 'https://mcp.trackflow.dev',
    'exp': datetime.now(timezone.utc) + timedelta(hours=1)
}
token = jwt.encode(payload, secret, algorithm='HS256')

async def test():
    async with sse_client('http://localhost:8100/', headers={
        'Authorization': f'Bearer {token}'
    }) as (read, write):
        async with ClientSession(read, write) as session:
            tools = await session.list_tools()
            print(f'✓ Found {len(tools.tools)} tools')

asyncio.run(test())
"
```

---

## Security

### Authentication

All requests require a valid **Bearer token** with:
- Valid signature (HS256 with `JWT_SECRET_KEY`)
- Valid expiration (`exp` claim)
- Matching issuer (`iss` matches `MCP_ISSUER`)
- Matching audience (`aud` matches `MCP_RESOURCE_ID`)

### Authorization

Each tool requires specific scopes:
- `read:incidents` — List/get incidents
- `write:incidents` — Create/update incidents
- `read:inventory` — List/get products

### Audit

Every tool call is logged:
```
TOOL_CALL  tool=create_incident  client=user-123  status=ok  duration_ms=142
```

---

## Resources

- **MCP Specification**: https://spec.modelcontextprotocol.io/
- **FastMCP**: https://github.com/jlouis/fastmcp
- **TrackFlow Context**: [`../CONTEXT.md`](../CONTEXT.md)
- **Backend API**: [`../services/api/README.md`](../services/api/README.md)
- **User API**: [`../services/user-api/README.md`](../services/user-api/README.md)

---

## Recommendation

Document the tools and resources exposed by each MCP server in its own `README.md` and `INSTALL.md` files. Use this directory to coordinate between multiple MCP servers and provide unified deployment/configuration guidance.

> _Spanish version: [README.es.md](./README.es.md)._
