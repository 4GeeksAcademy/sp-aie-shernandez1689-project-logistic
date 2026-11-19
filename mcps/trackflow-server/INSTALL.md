# TrackFlow MCP Server — Installation Guide

**Complete documentation for installing and running the TrackFlow MCP (Model Context Protocol) server.**

---

## Table of Contents

1. [System Requirements](#system-requirements)
2. [Installation Methods](#installation-methods)
3. [Dependency Details](#dependency-details)
4. [Configuration](#configuration)
5. [Verification](#verification)
6. [Troubleshooting](#troubleshooting)

---

## System Requirements

### Minimum requirements

- **Python**: 3.12 or later
- **pip** or **uv**: Package installer
- **RAM**: 512 MB
- **Disk**: 100 MB free
- **Network**: TCP port 8100 available (or configurable)

### Python version check

```bash
python --version
# Should output: Python 3.12.x or later
```

If Python 3.12+ is not available:
- Install from [python.org](https://www.python.org)
- Or use a version manager: `pyenv install 3.12` or `asdf install python 3.12`

---

## Installation Methods

### Method 1: Standalone installation with pip (Recommended for local dev)

**Best for**: Local development, testing, or single-server deployment.

```bash
# 1. Navigate to MCP server directory
cd mcps/trackflow-server

# 2. Create virtual environment (optional but recommended)
python -m venv venv
source venv/bin/activate       # On Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Verify installation
pip list | grep -E "fastmcp|httpx|PyJWT"
```

**Output should show:**
```
fastmcp                   4.2.2
httpx                     0.28.1
PyJWT                     2.9.0
python-dotenv             1.0.1
```

---

### Method 2: Monorepo installation with uv (Recommended for full project)

**Best for**: Working with the entire TrackFlow monorepo.

#### Install uv (if not already installed)

```bash
# Using pip
pip install uv

# Or on macOS using Homebrew
brew install uv

# Or on Windows using Cargo (if you have Rust)
cargo install uv

# Verify installation
uv --version
```

#### Install MCP server dependencies

```bash
# From monorepo root
uv pip install --directory mcps/trackflow-server -r mcps/trackflow-server/requirements.txt

# Or use uv sync if monorepo has workspace configuration
uv sync
```

---

### Method 3: Docker containerization

**Best for**: Production deployment, isolated environments, CI/CD.

#### Build Docker image

```bash
cd mcps/trackflow-server

docker build -t trackflow-mcp:latest .
```

#### Run Docker container

```bash
docker run -d \
  --name trackflow-mcp \
  -p 8100:8100 \
  -e JWT_SECRET_KEY="your-secret-key" \
  -e API_BASE_URL="http://host.docker.internal:8000" \
  -e INVENTORY_API_BASE_URL="http://host.docker.internal:8001" \
  trackflow-mcp:latest
```

---

## Dependency Details

### Primary Dependencies

All dependencies are listed in [`requirements.txt`](requirements.txt):

```
fastmcp>=4.1.0
httpx>=0.28.0
python-dotenv>=1.0.1
PyJWT[crypto]>=2.9.0
```

### Detailed Package Information

#### 1. **fastmcp** ≥4.1.0

- **Purpose**: MCP server framework with HTTP/SSE transport
- **Why needed**: Provides the core MCP protocol implementation
- **Installation**: `pip install 'fastmcp>=4.1.0'`
- **GitHub**: https://github.com/jlouis/fastmcp
- **Latest version**: 4.2.2

```bash
python -c "import fastmcp; print(fastmcp.__version__)"
```

#### 2. **httpx** ≥0.28.0

- **Purpose**: Async HTTP client library
- **Why needed**: Makes requests to `services/api` (incidents) and `services/user-api` (inventory)
- **Installation**: `pip install 'httpx>=0.28.0'`
- **GitHub**: https://github.com/encode/httpx
- **Latest version**: 0.28.1

```bash
python -c "import httpx; print(httpx.__version__)"
```

#### 3. **python-dotenv** ≥1.0.1

- **Purpose**: Environment variable management
- **Why needed**: Loads `.env` file for `JWT_SECRET_KEY`, API URLs, etc.
- **Installation**: `pip install 'python-dotenv>=1.0.1'`
- **GitHub**: https://github.com/theskumar/python-dotenv
- **Latest version**: 1.0.1

```bash
python -c "import dotenv; print(dotenv.__version__)"
```

#### 4. **PyJWT[crypto]** ≥2.9.0

- **Purpose**: JWT encoding/decoding (HS256)
- **Why needed**: Validates Bearer tokens from MCP clients
- **Installation**: `pip install 'PyJWT[crypto]>=2.9.0'`
- **GitHub**: https://github.com/jpadilla/pyjwt
- **Latest version**: 2.9.0

```bash
python -c "import jwt; print(jwt.__version__)"
```

### Transitive Dependencies

These are automatically installed when you install the primary dependencies:

| Package              | Purpose                           | Installed by |
| -------------------- | --------------------------------- | ------------ |
| `uvicorn`            | ASGI server                       | `fastmcp`    |
| `starlette`          | Web framework                     | `fastmcp`    |
| `pydantic`           | Data validation                   | `fastmcp`    |
| `cryptography`       | JWT crypto operations             | `PyJWT[crypto]` |
| `sse-starlette`      | Server-sent events support        | `fastmcp`    |
| `typing-extensions`  | Type hints for older Python       | Multiple     |

---

## Configuration

### Environment file setup

#### 1. Copy template

```bash
cd mcps/trackflow-server
cp .env.example .env
```

#### 2. Edit `.env` with your values

```bash
# REQUIRED: JWT secret (min 32 characters for production)
# This must match the secret in services/user-api/.env
JWT_SECRET_KEY=your-32-or-more-character-secret-key

# API endpoints (defaults for local dev)
API_BASE_URL=http://127.0.0.1:8000
INVENTORY_API_BASE_URL=http://127.0.0.1:8001

# MCP OAuth configuration
MCP_RESOURCE_ID=https://mcp.trackflow.dev
MCP_ISSUER=http://127.0.0.1:8001

# Server binding
HOST=0.0.0.0
PORT=8100
```

### Configuration reference

| Variable                   | Type   | Default                     | Required | Notes                            |
| -------------------------- | ------ | --------------------------- | -------- | -------------------------------- |
| `JWT_SECRET_KEY`           | string | (none)                      | ✅ YES   | Min 32 chars; must match user-api |
| `API_BASE_URL`             | URL    | `http://127.0.0.1:8000`     | ❌ No    | Operations API endpoint          |
| `INVENTORY_API_BASE_URL`   | URL    | `http://127.0.0.1:8001`     | ❌ No    | User API endpoint                |
| `MCP_RESOURCE_ID`          | URL    | `https://mcp.trackflow.dev` | ❌ No    | OAuth resource ID (aud claim)    |
| `MCP_ISSUER`               | URL    | `http://127.0.0.1:8001`     | ❌ No    | OAuth issuer (iss claim)         |
| `HOST`                     | string | `0.0.0.0`                   | ❌ No    | Bind to all interfaces           |
| `PORT`                     | int    | `8100`                      | ❌ No    | TCP port                         |

---

## Verification

### Installation verification

```bash
# Check Python version
python --version

# Check pip installed packages
pip list | grep -E "fastmcp|httpx|PyJWT|python-dotenv"

# Verify individual imports
python -c "import fastmcp; import httpx; import jwt; print('✓ All dependencies installed')"
```

### Server startup verification

```bash
cd mcps/trackflow-server

# Set secret (required for startup)
export JWT_SECRET_KEY="test-secret-key-dev"

# Start server
python main.py
```

**Expected output:**
```
2026-11-19 20:04:15,528 [INFO] trackflow-mcp: Starting TrackFlow MCP Server on 0.0.0.0:8100
INFO:     Uvicorn running on http://0.0.0.0:8100 (Press CTRL+C to quit)
```

### API verification

In another terminal:

```bash
# 1. Check OAuth metadata (public endpoint)
curl -s http://localhost:8100/.well-known/oauth-protected-resource | jq .

# Expected response:
# {
#   "resource": "https://mcp.trackflow.dev",
#   "authorization_servers": [
#     {
#       "issuer": "http://127.0.0.1:8001",
#       "authorization_endpoint": "http://127.0.0.1:8001/auth/login",
#       "token_endpoint": "http://127.0.0.1:8001/auth/token"
#     }
#   ],
#   "bearer_methods_supported": ["header"],
#   "scopes_supported": ["read:incidents", "write:incidents", "read:inventory"]
# }
```

---

## Troubleshooting

### "ModuleNotFoundError: No module named 'fastmcp'"

**Cause**: Dependencies not installed.

**Solution**:
```bash
pip install -r requirements.txt
# Or reinstall specifically:
pip install 'fastmcp>=4.1.0'
```

### "ModuleNotFoundError: No module named 'jwt'"

**Cause**: PyJWT not installed.

**Solution**:
```bash
pip install 'PyJWT[crypto]>=2.9.0'
```

### "OSError: [Errno 98] Address already in use"

**Cause**: Another process is using port 8100.

**Solution**:
```bash
# Find and kill the process
lsof -i :8100
kill -9 <PID>

# Or use a different port
export PORT=8101
python main.py
```

### "ValueError: Invalid token: Invalid audience"

**Cause**: JWT token's `aud` claim doesn't match `MCP_RESOURCE_ID`.

**Solution**:
```bash
# Option 1: Use default resource ID
# (token must have aud: "https://mcp.trackflow.dev")

# Option 2: Override resource ID
export MCP_RESOURCE_ID="https://your-domain"
python main.py

# Option 3: Check token claims
python -c "import jwt; print(jwt.decode('your-token', 'secret', algorithms=['HS256']))"
```

### "Connection refused: API_BASE_URL"

**Cause**: Backend APIs not running.

**Solution**:
```bash
# Start the operations API in another terminal
cd services/api
python -m uvicorn app.main:app --port 8000

# Start the user API in another terminal
cd services/user-api
python -m uvicorn app.main:app --port 8001

# Then start MCP server
cd mcps/trackflow-server
python main.py
```

### Python version incompatibility

**Cause**: Python < 3.12 installed.

**Solution**:

```bash
# Check current version
python --version

# Install Python 3.12+ using:
# Option 1: Direct from python.org
# Option 2: Using homebrew (macOS)
brew install python@3.12
alias python=python3.12

# Option 3: Using pyenv
pyenv install 3.12.0
pyenv shell 3.12.0
```

### "pip: command not found"

**Cause**: pip not installed or Python not in PATH.

**Solution**:
```bash
# Try using python module directly
python -m pip install -r requirements.txt

# Or use uv instead
uv pip install -r requirements.txt

# Or install pip
python -m ensurepip --upgrade
```

### Virtual environment issues

**Problem**: Installed packages not found even after `pip install`.

**Solution**:
```bash
# Activate virtual environment
source venv/bin/activate       # On Linux/macOS
venv\Scripts\activate          # On Windows

# If using conda
conda activate trackflow-mcp

# Verify activation (should show venv name in prompt)
which python

# Then try pip install again
pip install -r requirements.txt
```

---

## Next steps after installation

1. **Set up environment variables** in `.env` file
2. **Start the backend APIs** (services/api and services/user-api)
3. **Start the MCP server**: `python main.py`
4. **Test with an MCP client** (e.g., using the MCP CLI or Cursor/Claude integration)
5. **Review the main README.md** for tool documentation and architecture

---

## Support & Additional Resources

- **MCP Specification**: https://spec.modelcontextprotocol.io/
- **FastMCP Documentation**: https://github.com/jlouis/fastmcp
- **TrackFlow Project Context**: See [`../../CONTEXT.md`](../../CONTEXT.md)
- **Backend API Docs**: See [`../../services/api/README.md`](../../services/api/README.md)

