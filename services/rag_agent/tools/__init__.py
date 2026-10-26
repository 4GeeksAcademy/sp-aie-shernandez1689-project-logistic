"""Herramientas (tools) del agente RAG de TrackFlow.

Cada tool es un módulo autocontenido con un contrato tipado de
entrada/salida (Pydantic) y una función de consulta pura que el grafo
(`services/rag_agent/graph.py`) invoca como un nodo.

  - incident_lookup: consulta de tickets de soporte/incidencias contra el
    gestor de incidencias real (services/api). Solo lectura.
"""