"""Servicio de memoria persistente para agentes TrackFlow.

Arquitectura
------------
La memoria del agente se divide en dos stores independientes:

1. **KV Store (Redis)** — hechos episódicos y semánticos estructurados:
   - ``agent_memory:fact:<key>`` — hechos memorizados (preferencias de usuario,
     resoluciones aprobadas, persona→rol mappings).
   - ``agent_memory:episode:<session_id>`` — resúmenes episódicos por sesión
     (lo que se resolvió en cada conversación, no el log completo).
   - ``agent_memory:user:<user_id>:prefs`` — preferencias expresadas por el
     usuario durante la conversación.
   - TTL configurable para episodios (default 30 días).

2. **Vector Store (Qdrant, colección ``trackflow_agent_memory``)** — memoria
   semántica de largo plazo: hechos aprobados, relaciones conceptuales y
   patrones aprendidos. Permite búsqueda por similitud semántica cuando el
   agente necesita recordar algo que no sabe cómo se llama exactamente.

Reglas de negocio (de BRIEFING + CONTEXT)
------------------------------------------
✅ MEMORIZABLE:
  - Mapeos persona→rol (ej. "Ana → Head of Warehouse Operations")
  - Preferencias expresadas por el usuario durante la conversación
  - Hechos aprobados explícitamente por el usuario (ej. "confirma que ...")
  - Resúmenes de interacciones previas (qué se preguntó/resolvió)
  - Decisiones operativas aprobadas (ej. umbrales de stock acordados)

❌ PROHIBIDO:
  - Datos personales del consumidor final (CONTEXT-TELE.md §3)
  - Estados crudos de incidencias o inventario (leer via tools/MCP)
  - Contenido de documentos RAG (trackflow_knowledge es read-only)
  - Conversaciones completas — solo resúmenes aprobados por el grafo
  - Tokens, contraseñas o credenciales de cualquier sistema
  - Información no verificada o no aprobada por el usuario

La interfaz es explícita: el agente debe llamar a write()/read()/search()
— nunca acumula estado inyectándolo al system prompt.
"""

from __future__ import annotations

from .kv import MemoryKVStore
from .vector import MemoryVectorStore
from .service import MemoryService

__all__ = [
    "MemoryKVStore",
    "MemoryVectorStore",
    "MemoryService",
]
