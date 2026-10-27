"""Herramientas (tools) del agente RAG de TrackFlow (DEPRECATED).

ESTADO: DEPRECADO desde 2026-10-27
----------------------------------
El agente ya NO usa estas tools directas en-proceso. Los nodos
``incident_lookup`` e ``inventory_lookup`` del grafo LangGraph se
conectan ahora al TrackFlow MCP Server via ``langchain-mcp-adapters``.

Ver:
  - ``services/rag_agent/mcp_client.py``: nuevo cliente MCP.
  - ``services/rag_agent/tools/DEPRECATED.md``: explicación detallada.

Los módulos siguientes se conservan por compatibilidad con los tests
existentes hasta que se actualicen, pero **no deben usarse en nuevo
código**:

  - incident_lookup: consulta de tickets (en-proceso).
  - inventory_lookup: consulta de stock (en-proceso).
"""