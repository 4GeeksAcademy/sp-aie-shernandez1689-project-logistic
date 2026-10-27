# TrackFlow MCP Server

MCP (Model Context Protocol) server that exposes TrackFlow's internal APIs as discoverable tools for AI agents.

## Features

- **OAuth 2.1 authentication** via [MCP Auth](https://mcp-auth.dev/) (`mcpauth`) — Bearer JWT validation with OAuth Protected Resource Metadata endpoint
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
           │  mcpauth BearerAuthMiddleware │
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
