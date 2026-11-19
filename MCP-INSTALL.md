# TrackFlow MCP Server — Installation Quick Reference

**Complete installation documentation for the TrackFlow MCP (Model Context Protocol) Server**

---

## Quick Start (5 minutes)

```bash
# 1. Navigate to MCP server
cd mcps/trackflow-server

# 2. Install dependencies
pip install -r requirements.txt

# 3. Set environment variable
export JWT_SECRET_KEY="dev-secret-key-32-chars-minimum"

# 4. Start server
python main.py

# 5. Verify (in another terminal)
curl http://localhost:8100/.well-known/oauth-protected-resource | jq .
```

**Expected output**: JSON with scopes and resource metadata

---

## Full Installation Guide

### Prerequisites

- **Python 3.12+** — Check with `python --version`
- **pip or uv** — Package manager
- **Internet connection** — To download dependencies
- **Port 8100** — Available (or override with `PORT` env var)

### Installation Methods

#### Method 1: Standalone (Recommended for local dev)

```bash
# Navigate to MCP server directory
cd mcps/trackflow-server

# Optional: Create virtual environment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Verify installation
python -c "import fastmcp; print('✓ fastmcp installed')"
```

#### Method 2: Monorepo (Full project)

```bash
# From monorepo root, install MCP server dependencies
uv pip install --directory mcps/trackflow-server -r mcps/trackflow-server/requirements.txt

# Or use uv sync if configured
uv sync
```

#### Method 3: Docker (Production)

```bash
# Build image
docker build -t trackflow-mcp:latest mcps/trackflow-server/

# Run container
docker run -p 8100:8100 \
  -e JWT_SECRET_KEY="production-secret" \
  trackflow-mcp:latest
```

---

## Configuration

### Create .env file

```bash
cd mcps/trackflow-server
cp .env.example .env
```

Edit `.env`:

```env
# REQUIRED: JWT secret (min 32 characters for production)
# This must match the secret in services/user-api/.env
JWT_SECRET_KEY=your-32-or-more-character-secret-key

# OPTIONAL: Backend API endpoints (defaults shown)
API_BASE_URL=http://127.0.0.1:8000
INVENTORY_API_BASE_URL=http://127.0.0.1:8001

# OPTIONAL: MCP OAuth configuration
MCP_RESOURCE_ID=https://mcp.trackflow.dev
MCP_ISSUER=http://127.0.0.1:8001

# OPTIONAL: Server binding
HOST=0.0.0.0
PORT=8100
```

---

## Dependency Details

| Package      | Version  | Purpose                        |
| ------------ | -------- | ------------------------------ |
| **fastmcp**  | ≥4.1.0   | MCP server framework (SSE)     |
| **httpx**    | ≥0.28.0  | HTTP client for backend APIs   |
| **PyJWT[crypto]** | ≥2.9.0 | JWT validation (HS256)         |
| **python-dotenv** | ≥1.0.1 | Environment variable loading   |

### Auto-installed transitive dependencies

- **uvicorn** — ASGI server
- **starlette** — Web framework
- **pydantic** — Data validation
- **cryptography** — JWT encryption

### Installation command

```bash
pip install -r requirements.txt
```

**Verification:**

```bash
pip show fastmcp httpx PyJWT python-dotenv | grep Version
```

---

## Starting the Server

### Standalone

```bash
cd mcps/trackflow-server
export JWT_SECRET_KEY="your-secret-key"
python main.py
```

### With environment file

```bash
cd mcps/trackflow-server
# .env file is automatically loaded by python-dotenv
python main.py
```

### Module execution

```bash
export JWT_SECRET_KEY="your-secret-key"
python -m mcps.trackflow_server.main
```

### Expected startup output

```
2026-11-19 20:04:15,528 [INFO] trackflow-mcp: Starting TrackFlow MCP Server on 0.0.0.0:8100 (resource=https://mcp.trackflow.dev, issuer=http://127.0.0.1:8001)
INFO:     Started server process [PID]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8100 (Press CTRL+C to quit)
```

---

## Verification

### Check dependencies installed

```bash
cd mcps/trackflow-server
python -c "import fastmcp; import httpx; import jwt; print('✓ All dependencies OK')"
```

### Verify server startup

```bash
# Terminal 1: Start server
cd mcps/trackflow-server
export JWT_SECRET_KEY="test-secret"
python main.py

# Terminal 2: Test endpoint
curl http://localhost:8100/.well-known/oauth-protected-resource | jq .
```

**Expected response:**

```json
{
  "resource": "https://mcp.trackflow.dev",
  "authorization_servers": [
    {
      "issuer": "http://127.0.0.1:8001",
      "authorization_endpoint": "http://127.0.0.1:8001/auth/login",
      "token_endpoint": "http://127.0.0.1:8001/auth/token"
    }
  ],
  "bearer_methods_supported": ["header"],
  "scopes_supported": ["read:incidents", "write:incidents", "read:inventory"]
}
```

---

## Troubleshooting

### "ModuleNotFoundError: No module named 'fastmcp'"

**Cause**: Dependencies not installed

**Fix**:
```bash
cd mcps/trackflow-server
pip install -r requirements.txt
```

### "OSError: [Errno 98] Address already in use"

**Cause**: Port 8100 already in use

**Fix**:
```bash
# Option 1: Use different port
export PORT=8101
python main.py

# Option 2: Kill existing process
lsof -i :8100
kill -9 <PID>
```

### "ValueError: Invalid token: Invalid audience"

**Cause**: JWT token's `aud` claim doesn't match configuration

**Fix**:
```bash
# Ensure token has correct aud claim:
export MCP_RESOURCE_ID="https://mcp.trackflow.dev"

# Or generate token with matching claim:
# token = jwt.encode({
#   "aud": "https://mcp.trackflow.dev",
#   ...
# }, secret, algorithm="HS256")
```

### "Connection refused: API_BASE_URL"

**Cause**: Backend APIs not running

**Fix**: Start backend services

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

### Python version < 3.12

**Cause**: Old Python version installed

**Fix**:

```bash
# Check Python version
python --version

# Install Python 3.12+ from python.org, or:
# macOS Homebrew:
brew install python@3.12
alias python=python3.12

# Linux (Ubuntu):
sudo apt-get install python3.12
sudo update-alternatives --install /usr/bin/python python /usr/bin/python3.12 1

# Using pyenv:
pyenv install 3.12.0
pyenv shell 3.12.0
```

---

## Available Tools

The MCP server exposes 6 tools:

### Incidents (requires authentication)

| Tool                     | Scope             |
| ------------------------ | ----------------- |
| `create_incident`        | `write:incidents` |
| `get_incident`           | `read:incidents`  |
| `list_incidents`         | `read:incidents`  |
| `update_incident_status` | `write:incidents` |

### Inventory (read-only)

| Tool            | Scope             |
| --------------- | ----------------- |
| `get_product`   | `read:inventory`  |
| `list_products` | `read:inventory`  |

---

## Documentation Files

- **Main README**: [`mcps/trackflow-server/README.md`](mcps/trackflow-server/README.md)
- **Full Installation Guide**: [`mcps/trackflow-server/INSTALL.md`](mcps/trackflow-server/INSTALL.md)
- **MCP Directory Overview**: [`mcps/README.md`](mcps/README.md)
- **Configuration Template**: [`mcps/trackflow-server/.env.example`](mcps/trackflow-server/.env.example)

---

## Next Steps

1. ✅ Install dependencies
2. ✅ Configure `.env` file
3. ✅ Start the server
4. 📖 Read [`mcps/trackflow-server/README.md`](mcps/trackflow-server/README.md) for tool documentation
5. 🧪 Test tools using Bearer tokens
6. 📚 Review [`mcps/trackflow-server/INSTALL.md`](mcps/trackflow-server/INSTALL.md) for advanced topics

---

## Support

- **MCP Spec**: https://spec.modelcontextprotocol.io/
- **FastMCP Docs**: https://github.com/jlouis/fastmcp
- **TrackFlow Context**: [`CONTEXT.md`](CONTEXT.md)

