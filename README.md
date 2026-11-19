# TrackFlow — AI-Powered Logistics Operations Platform

[![4Geeks Academy](https://img.shields.io/badge/4Geeks-Academy-blue)](https://4geeksacademy.com)
[![AI Engineering](https://img.shields.io/badge/track-AI%20Engineering-green)](https://4geeksacademy.com/es/programas-de-carrera/ingenieria-ia)
[![Project Status](https://img.shields.io/badge/status-active-brightgreen)]()
[![Python 3.12+](https://img.shields.io/badge/Python-3.12+-blue)]()
[![Node.js 18+](https://img.shields.io/badge/Node.js-18+-green)]()

**AI Engineering capstone project for 4Geeks Academy — Building an intelligent logistics operations platform for TrackFlow, a binational logistics operator serving the US and Spain.**

> _Lee este documento en español: [README.es.md](./README.es.md)_

---

## 📋 Project Overview

**TrackFlow** is a binational logistics operator (founded 2009) with operations in Los Angeles, USA and Zaragoza, Spain. The company provides specialized services for e-commerce brands in fashion, electronics, and cosmetics:

- 🏭 **Warehouse Management** — Inventory storage and fulfillment
- 🚚 **Last-Mile Delivery** — Final delivery to end customers
- ↩️ **Reverse Logistics** — Returns processing and management

**Current Project Scope:** Build an intelligent operations platform combining:
1. **Public Corporate Website** — Lead capture and business communication
2. **RAG-Based Support Agent** — Incident tracking and resolution
3. **MCP Server** — Model Context Protocol integration for AI tools
4. **Telemetry Pipeline** — Operations analytics and performance tracking

---

## 🎯 Key Business Objectives

- ✅ Present TrackFlow as a serious binational operator in US and Spain
- ✅ Clearly explain three core services with technical accuracy
- ✅ Capture qualified B2B leads through structured intake forms
- ✅ Reduce commercial qualification time with better lead quality
- ✅ Implement AI-driven incident tracking and support
- ✅ Provide real-time operations telemetry and insights

---

## 🏗️ Repository Structure

### Root Level — Core Documentation

```
├── README.md                          # This file — Project overview
├── README.es.md                       # Spanish documentation
├── CONTEXT.md                         # Company context and requirements
├── CONTEXT.incidents.md              # Incident tracking system context
├── MCP-INSTALL.md                    # MCP server installation guide
├── AGENTS.md                         # Agent patterns and architecture
├── TESTING.md                        # Testing procedures and guidelines
├── docker-compose.yml                # Local development environment
├── pyproject.toml                    # Python project configuration
└── package.json                      # Node.js dependencies and scripts
```

### 📁 `/services` — Backend APIs & Workers

Microservices architecture for core business logic:

```
services/
├── api/                              # Operations API (Python/FastAPI)
│   ├── routes/                       # API endpoints (incidents, operations)
│   ├── models/                       # SQLAlchemy ORM models
│   ├── database.py                   # Database configuration
│   └── tests/                        # API integration tests
│
├── user-api/                         # User Management API (Python/FastAPI)
│   ├── app/                          # FastAPI application
│   ├── models/                       # SQLModel definitions
│   └── tests/                        # Authentication & user tests
│
├── rag_agent/                        # RAG-Based Support Agent (LangGraph)
│   ├── graph.py                      # Agent workflow orchestration
│   ├── tools/                        # Tools for incident lookup & inventory
│   │   ├── incident_lookup.py        # Query incidents from API
│   │   ├── inventory_lookup.py       # Query stock from user-api
│   │   └── README.md                 # Tool documentation
│   ├── tracing.py                    # Debug tracing & schema
│   └── tests/                        # Agent graph & tool tests
│
├── telemetry/                        # Telemetry Pipeline (Pandas/SQL)
│   ├── analysis.py                   # Metrics calculation
│   ├── ingest.py                     # Event ingestion from APIs
│   └── tests/                        # Analytics tests
│
└── job_runner/                       # Background Job Processor
    ├── tasks.py                      # Celery tasks
    └── tests/                        # Job execution tests
```

### 🎨 `/uis` — User Interfaces

Frontend applications and web interfaces:

```
uis/
├── landing/                          # Public corporate website
│   ├── index.html                    # Landing page (Tailwind CSS)
│   ├── css/                          # Stylesheets
│   ├── js/                           # Form validation, interactions
│   └── assets/                       # Images, icons, branding
│
├── operations-dashboard/             # Internal operations UI
│   ├── src/                          # React/Next.js application
│   ├── components/                   # Reusable components
│   └── pages/                        # Dashboard pages
│
└── docs/                             # Documentation portals
    └── api/                          # API documentation
```

### 🤖 `/mcps` — Model Context Protocol Servers

MCP servers for AI tool integration:

```
mcps/
├── README.md                         # MCP servers overview
├── trackflow-server/                 # TrackFlow MCP Server
│   ├── main.py                       # FastMCP application
│   ├── INSTALL.md                    # Installation guide (11.2KB)
│   ├── README.md                     # Server documentation
│   ├── requirements.txt              # Python dependencies
│   │   ├── fastmcp ≥4.1.0           # MCP server framework
│   │   ├── httpx ≥0.28.0            # HTTP client
│   │   ├── PyJWT[crypto] ≥2.9.0     # JWT authentication
│   │   └── python-dotenv ≥1.0.1     # Environment config
│   └── .env.example                  # Configuration template
│
└── Dockerfile                        # Container image for MCP server
```

### 📊 `/data` — Data & Analytics

Data pipeline and analytical assets:

```
data/
├── raw/                              # Raw data sources
│   └── api_events/                   # Event logs from services
│
├── processed/                        # Processed datasets
│   └── telemetry/                    # Aggregated metrics
│
├── pipelines/                        # ETL/ELT processes
│   ├── ingest.py                     # Data ingestion
│   └── transform.py                  # Transformations
│
└── eval/                             # Evaluation datasets
    └── agent_traces/                 # LangGraph execution traces
```

### 💡 `/skills` — Reusable Agent Skills

Modular skills for AI agents:

```
skills/
├── code-review/                      # Code review automation
│   └── SKILL.md                      # Skill definition
│
├── data-analysis/                    # Data analysis capabilities
│   ├── scripts/                      # Analysis tools
│   └── resources/                    # Reference data
│
├── research/                         # Information research
│   ├── templates/                    # Query templates
│   └── examples/                     # Example outputs
│
└── workflow-progress-sync/           # Checkpoint management
    └── SKILL.md                      # Skill definition
```

### 🔧 `/infra` — Infrastructure & Deployment

Infrastructure as Code and deployment configs:

```
infra/
├── docker/                           # Docker configurations
│   ├── Dockerfile.api                # Operations API container
│   ├── Dockerfile.user-api           # User API container
│   └── Dockerfile.mcp                # MCP server container
│
├── kubernetes/                       # Kubernetes manifests (future)
│   └── deployments/
│
└── scripts/                          # Infrastructure scripts
    └── setup.sh                      # Environment setup
```

### 📚 `/docs` — Project Documentation

Comprehensive documentation:

```
docs/
├── ARCHITECTURE.md                   # System architecture
├── API.md                            # API specifications
├── DATABASE.md                       # Schema & models
├── DEPLOYMENT.md                     # Deployment procedures
├── TROUBLESHOOTING.md                # Common issues & solutions
└── TESTING.md                        # Testing guide
```

### 🧠 `/agents` — Agent Patterns & Utilities

Agent configurations and utilities:

```
agents/
├── tools/                            # Tool definitions
│   ├── README.md                     # Tools documentation
│   ├── incidents.md                  # Incident management tools
│   └── inventory.md                  # Inventory query tools
│
└── workflows/                        # Agent workflow examples
    └── support_agent.md              # Support workflow
```

### 📦 `/packages` — Shared Dependencies

Monorepo shared packages:

```
packages/
└── shared/                           # @repo/shared-types
    ├── package.json
    ├── types/                        # TypeScript type definitions
    └── utils/                        # Shared utilities
```

### 🛠️ `/internal` — Internal Tools

CLI tools and internal utilities:

```
internal/
├── cli/                              # Command-line tools
├── migration/                        # Database migrations
└── scripts/                          # Maintenance scripts
```

### 📝 `/memory-bank` — Session Management

Agent memory and progress tracking:

```
memory-bank/
├── projectbrief.md                   # Business requirements
├── techContext.md                    # Technical stack & decisions
├── progress.md                       # Development progress log
└── checkpoints/                      # Session checkpoints
```

---

## 🚀 Quick Start

### Prerequisites

- **Python 3.12+**
- **Node.js 18+**
- **Docker & Docker Compose** (optional)
- **PostgreSQL 14+** (for user-api)
- **Git**

### Local Development Setup

```bash
# 1. Clone the repository
git clone <repository-url>
cd sp-aie-shernandez1689-project-logistic

# 2. Set up Python environment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 3. Install Python dependencies
pip install -r requirements-dev.txt

# 4. Install Node dependencies
npm install

# 5. Configure environment variables
cp .env.example .env
# Edit .env with your configuration

# 6. Start services with Docker Compose
docker-compose up -d

# 7. Run migrations
python -m alembic upgrade head

# 8. Start development server
make dev  # or python -m uvicorn services.api.main:app --reload
```

### Running the Landing Page

```bash
cd uis/landing
# Serve with Python
python -m http.server 8080

# Or open directly in browser
open index.html
```

### Starting the MCP Server

```bash
cd mcps/trackflow-server
export JWT_SECRET_KEY="dev-secret-key"
pip install -r requirements.txt
python main.py

# Server runs on http://localhost:8100
# See MCP-INSTALL.md for complete setup guide
```

---

## 🏗️ Technology Stack

### Backend
- **FastAPI** — Web framework for APIs
- **SQLAlchemy** — ORM for database models
- **SQLModel** — Combining SQLAlchemy + Pydantic
- **LangGraph** — Agent workflow orchestration
- **FastMCP** — Model Context Protocol implementation
- **PyJWT** — Token-based authentication
- **Pandas** — Data analysis and ETL

### Frontend
- **HTML5** — Semantic markup
- **Tailwind CSS** — Utility-first styling
- **JavaScript (Vanilla)** — Form validation & interactivity
- **Schema.org** — Structured data for SEO

### Database
- **PostgreSQL** — Relational database
- **TinyDB** — JSON document store (incidents)
- **SQLite** — Testing & local development

### Infrastructure
- **Docker & Docker Compose** — Containerization
- **Uvicorn** — ASGI application server
- **Starlette** — ASGI middleware

### AI/ML
- **LangChain** — LLM orchestration
- **LangGraph** — Agent state machines
- **FastMCP** — Protocol for AI tool integration

---

## 📊 Current Implementation Status

### ✅ Completed Milestones

- **Milestone 1 — Web** — Corporate landing page with lead capture form
- **Milestone 2 — Programming** — Business logic for incident tracking
- **Milestone 5 — Backend** — Operations API (incidents, inventory)
- **Milestone 6 — Telemetry** — Analytics pipeline and dashboard
- **Milestone 7 — RAG & Memory** — Support agent with knowledge base
- **Milestone 8 — Agents** — Multi-tool incident resolution agent
- **Milestone 9 — Workflows** — Job orchestration framework
- **MCP Server** — Full OAuth integration with Bearer token auth

### 🔄 In Progress

- **Advanced Agent Workflows** — Multi-step incident resolution
- **Real-time Dashboard** — Live operations monitoring
- **Enhanced Telemetry** — Performance metrics and anomaly detection

---

## 🔐 Authentication & Authorization

### JWT Bearer Tokens

All protected endpoints require Bearer token authentication:

```bash
# Example request
curl -H "Authorization: Bearer <token>" \
  http://localhost:8100/api/incidents
```

**Token Claims Required:**
- `exp` — Expiration time (required)
- `sub` — Subject/User ID (required)
- `iss` — Issuer (must match MCP_ISSUER)
- `aud` — Audience (must match MCP_RESOURCE_ID)
- `scope` — Space-separated permissions

### Available Scopes

```
read:incidents       # Read incident data
write:incidents      # Create/update incidents
read:inventory       # Read inventory/stock data
write:inventory      # Modify inventory
```

---

## 🧪 Testing

### Run All Tests

```bash
# Python tests
pytest tests/ -v

# JavaScript tests
npm test

# Specific test suite
pytest tests/pipelines/test_agent_graph_routing.py -v
```

### Test Coverage

```bash
# Generate coverage report
pytest --cov=services --cov-report=html
open htmlcov/index.html
```

### Integration Tests

```bash
# Test API endpoints
pytest tests/services/test_api.py -v

# Test agent workflows
pytest tests/pipelines/ -v

# Test MCP server
curl http://localhost:8100/.well-known/oauth-protected-resource | jq .
```

---

## 📖 Documentation

| Document | Purpose |
|----------|---------|
| [CONTEXT.md](./CONTEXT.md) | Business requirements and user stories |
| [MCP-INSTALL.md](./MCP-INSTALL.md) | MCP server setup (5-15 min) |
| [mcps/trackflow-server/INSTALL.md](./mcps/trackflow-server/INSTALL.md) | Complete MCP reference guide |
| [AGENTS.md](./AGENTS.md) | Agent patterns and tool definitions |
| [TESTING.md](./TESTING.md) | Testing procedures |
| [docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md) | System design |
| [services/rag_agent/tools/README.md](./services/rag_agent/tools/README.md) | RAG tools documentation |

---

## 🛠️ Development Workflow

### Common Commands

```bash
# Start all services
docker-compose up

# Stop services
docker-compose down

# View logs
docker-compose logs -f [service-name]

# Run migrations
alembic upgrade head

# Format code
black services/ uis/
isort services/

# Lint
pylint services/
eslint uis/

# Type checking
mypy services/
```

### Git Workflow

```bash
# Create feature branch
git checkout -b feature/description

# Commit changes with message
git commit -m "type: description"

# Push and create pull request
git push origin feature/description
```

### Commit Message Convention

```
<type>: <description>

<body>

Fixes #<issue-number>
Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>
```

Types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`

---

## 📈 Deployment

### Docker Deployment

```bash
# Build all images
docker-compose build

# Push to registry
docker tag trackflow-api:latest myregistry.azurecr.io/trackflow-api:latest
docker push myregistry.azurecr.io/trackflow-api:latest
```

### Environment Configuration

Create `.env.production` with production values:

```env
DATABASE_URL=postgresql://user:password@host:5432/trackflow
JWT_SECRET_KEY=production-secret-key-min-32-chars
MCP_RESOURCE_ID=https://mcp.trackflow.prod
LOG_LEVEL=INFO
```

---

## 🆘 Troubleshooting

### Common Issues

**Port already in use:**
```bash
# Find process using port
lsof -i :8000
kill -9 <PID>
```

**Database connection refused:**
```bash
# Check PostgreSQL is running
docker-compose logs postgres
docker-compose restart postgres
```

**Module not found errors:**
```bash
# Reinstall dependencies
pip install -r requirements.txt --force-reinstall
```

**JWT authentication fails:**
```bash
# Check JWT_SECRET_KEY is set
echo $JWT_SECRET_KEY
# Verify token claims
python -c "import jwt; print(jwt.decode(token, key, algorithms=['HS256']))"
```

See [TESTING.md](./TESTING.md) and [docs/](./docs/) for more detailed troubleshooting.

---

## 📞 Support & Contributions

### Getting Help

1. Check [TESTING.md](./TESTING.md) for common issues
2. Review [docs/TROUBLESHOOTING.md](./docs/TROUBLESHOOTING.md)
3. Check GitHub issues and discussions
4. Review agent skills in `/skills/`

### Contributing

1. Create feature branch from `main`
2. Make changes with descriptive commits
3. Write/update tests
4. Submit pull request with context
5. Wait for CI/CD and code review

---

## 📄 License

This project is part of the 4Geeks Academy AI Engineering Career Program.

**Educational use only** — Property of 4Geeks Academy and TrackFlow (fictional company).

---

## 👥 Project Team

**4Geeks Academy AI Engineering Program**

- **Instructor Lead:** [@marcogonzalo](https://www.linkedin.com/in/marcogonzalo)
- **Program Director:** [@alezanchezr](https://x.com/alesanchezr)
- **Student:** Hernández (sp-aie-shernandez1689-project-logistic)

---

## 🎓 Learning Objectives

This project covers:

✅ **Full-stack AI Engineering** — Frontend to backend to AI agents  
✅ **Monorepo Architecture** — Managing multiple services and applications  
✅ **API Design** — RESTful APIs with authentication and validation  
✅ **Database Design** — Relational and document models  
✅ **RAG Systems** — Building semantic search and AI agents  
✅ **LLM Integration** — Using language models for business logic  
✅ **Data Pipelines** — ETL and analytics  
✅ **Testing & Quality** — Comprehensive test coverage  
✅ **DevOps** — Docker, deployment, monitoring  
✅ **Documentation** — API docs, user guides, architecture  

---

## 📚 Additional Resources

- [4Geeks Academy — AI Engineering](https://4geeksacademy.com/es/programas-de-carrera/ingenieria-ia)
- [Model Context Protocol (MCP) Spec](https://spec.modelcontextprotocol.io/)
- [LangGraph Documentation](https://langchain-ai.github.io/langgraph/)
- [FastAPI Docs](https://fastapi.tiangolo.com/)
- [Tailwind CSS Docs](https://tailwindcss.com/docs)
- [SQLAlchemy ORM](https://docs.sqlalchemy.org/)

---

**Last Updated:** November 2026 | **Status:** Active Development | **Version:** 1.0.0
