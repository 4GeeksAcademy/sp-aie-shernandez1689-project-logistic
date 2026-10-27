# DEPRECATED — Tools directas del agente (en-proceso)

> **Estado:** DEPRECATED desde 2026-10-27
> **Reemplazo:** `services/rag_agent/mcp_client.py` (conexión al MCP Server via `langchain-mcp-adapters`)

Estos módulos se **eliminarán** en una versión futura. No deben usarse en
nuevo código.

## ¿Por qué se deprecan?

Las tools `incident_lookup.py` e `inventory_lookup.py` leían **en-proceso**
los gestores de incidencias e inventario:

- Importaban directamente `services.api.database.get_incidents_table` para
  leer la tabla TinyDB de incidencias.
- Importaban directamente `services.user-api.app.models.SKU` para leer la
  base SQLModel de inventario.

Esto creaba un **acoplamiento fuerte**: el agente necesitaba acceso directo
al filesystem y a las bases de datos de los servicios. Cualquier cambio en
los modelos o en la estructura de los servicios podía romper el agente.

## ¿Qué las reemplaza?

El agente ahora se conecta al **TrackFlow MCP Server**
(`mcps/trackflow-server`) mediante `langchain-mcp-adapters`, usando el
protocolo MCP sobre HTTP/SSE.

Esto proporciona:

- **Desacoplamiento**: el agente no necesita acceso directo a las bases de
  datos. Habla con el MCP Server, que expone las mismas operaciones que
  la API REST pero como tools MCP.
- **Autenticación**: el MCP Server valida tokens JWT (Bearer) en cada
  llamada, igual que la API REST.
- **Contrato estable**: las tools del MCP Server tienen un contrato
  explícito (FastMCP `@mcp.tool` con argumentos tipados) que no depende
  de la estructura interna de los modelos.
- **Misma semántica**: las funciones `lookup_incidents_mcp()` y
  `lookup_inventory_mcp()` en `mcp_client.py` devuelven el mismo
  formato de datos que las antiguas `lookup_incidents()` y
  `lookup_inventory()`. El resto del grafo no necesita cambios.

## Historial

| Fecha       | Cambio                                                  |
|-------------|---------------------------------------------------------|
| 2026-10-27  | Marcadas como DEPRECATED. Agente migrado a MCP.         |
