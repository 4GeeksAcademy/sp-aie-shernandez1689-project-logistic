# `agents/tools` folder

This folder groups **reusable tools** that agents in the monorepo can use (for example: connectors to internal APIs, database wrappers, scraping utilities, validators, service clients, etc.).

- **Main purpose**: avoid duplicated code across agents and standardize how agents interact with company systems.
- **Recommendation**: document each tool you add (what it does, inputs/outputs, permissions/security, limits, and usage examples) and link to it from the agents that use it.

## Tools

| Tool | Location | Description |
|------|----------|-------------|
| `incident_lookup` | [`services/rag_agent/tools/`](../../services/rag_agent/tools/README.md) | Read-only support-ticket query against the real incident manager (in-process, typed contract, timeout + honest fallback). Used by the RAG agent graph. |
| `inventory_lookup` | [`services/rag_agent/tools/`](../../services/rag_agent/tools/README.md) | Read-only stock-by-product (SKU) query against the real inventory manager (`services/user-api`, SQLModel/PostgreSQL). Same typed contract as `incident_lookup`: in-process transport (no HTTP), timeout + honest fallback, no simulated data. Used by the RAG agent graph. |

> _Spanish version: [README.es.md](./README.es.md)._
