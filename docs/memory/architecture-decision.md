# Decisión de Arquitectura: Memoria por Propuesta Explícita

## Contexto

El agente RAG de TrackFlow necesita recordar información entre conversaciones
(preferencias de usuario, hechos confirmados, decisiones). Existen dos enfoques
principales:

1. **Auto-persistencia**: el agente escribe a memoria en cada interacción,
   decidiendo autónomamente qué guardar.
2. **Propuesta explícita**: el agente evalúa si hay algo memorable, se lo
   propone al usuario, y solo persiste si el usuario confirma.

## Decisión

Elegimos **propuesta explícita**. El agente **nunca escribe directamente a memoria**.

### Flujo

```
Interacción N:
  generate_node produce respuesta + _memory_proposal (auto-evaluada)
  memorize_node incrusta pregunta al final: "📝 ¿Quieres que recuerde...?"
  
Interacción N+1 (siguiente pregunta del usuario):
  remember_node detecta si la nueva pregunta es una confirmación/rechazo
  Si es confirmación → persiste en Redis/Qdrant
  Si es rechazo → descarta la propuesta
  Si es ambigua → mantiene la propuesta pendiente
```

### ¿Por qué?

- **Consentimiento explícito**: el usuario decide qué se guarda.
- **Sin segunda llamada al LLM**: la auto-evaluación es puramente heurística
  (regex + reglas de negocio).
- **Sin agente separado**: todo ocurre dentro del mismo grafo LangGraph.
- **Sin arquitectura multi-agente**: un solo flujo de nodos síncronos.

## Estructura de Datos

### `_memory_proposal` (estado del grafo — no persiste)

```python
# Propuesta de preferencia
{"type": "preference", "payload": {"user_id": "...", "preferences": {"language": "es"}}}

# Propuesta de hecho
{"type": "fact", "payload": {"facts": [{"key": "person:role:juan", "value": "Gerente"}], "user_id": "..."}}

# Sin propuesta
{"type": "none", "payload": {}}
```

### Redis KV (`agent_memory:*`)

```redis
agent_memory:pref:{user_id}:language → "es"
agent_memory:pref:{user_id}:detail_level → "summary"
agent_memory:episode:{thread_id}      → JSON con historial de la conversación
```

### Qdrant (`trackflow_agent_memory`, 1536d, COSINE)

```json
{
  "id": "uuid",
  "vector": [0.1, 0.2, ...],
  "payload": {
    "type": "fact",
    "key": "person:role:juan_perez",
    "value": "Gerente de Logística",
    "user_id": "usr_123",
    "timestamp": "2025-01-15T10:30:00Z"
  }
}
```

## Reglas de Auto-evaluación (memorize_node)

1. **Preferencias detectadas** → proponer (p.ej., "en español", "solo resumen").
2. **Hechos aprobados en respuesta** → proponer (p.ej., "Confirmado: Juan es Gerente").
3. **Solicitud explícita** → proponer (p.ej., "recuerda que María es líder").
4. **Ninguna de las anteriores** → `{"type": "none"}` (no se propone nada).

## Ejemplos de NO Memorización

| Pregunta | Motivo |
|----------|--------|
| "¿Cuál es el estado del ticket INC-123?" | Consulta operacional puntual |
| "Necesito el stock del SKU CLT-SNK-W-42" | Consulta de inventario transaccional |
| "¿Qué dice la política de devoluciones?" | Pregunta documental RAG |
| "Gracias" / "ok" / "entendido" | Sin información nueva |
| Preguntas sobre el clima | Fuera del dominio de TrackFlow |

## Detección de Confirmación (remember_node)

### Patrones de Aceptación

`sí`, `si`, `ok`, `okay`, `de acuerdo`, `confirmo`, `adelante`, `claro`,
`sí, guárdalo`, `sí, recuérdalo`, `yes`, `sure`, `go ahead`

### Patrones de Rechazo

`no`, `no gracias`, `no guardes`, `no recuerdes`, `nope`, `not now`,
`no thanks`, `no quiero`

### Mensajes ambiguos

Mensajes de menos de 8 caracteres que no coinciden con aceptación ni rechazo
se consideran ambiguos y la propuesta se mantiene pendiente.

## Implementación

### Archivos

- `services/rag_agent/memory_nodes.py` — Nodos `remember_node` y `memorize_node`,
  helpers de extracción y detección.
- `services/rag_agent/graph.py` — Grafo LangGraph con aristas condicionales para
  los nuevos nodos.
- `services/memory/` — `MemoryService`, `MemoryKVStore` (Redis), `MemoryVectorStore` (Qdrant).

### Sync vs Async

Los nodos del grafo son síncronos (`graph.invoke`), pero `MemoryService` usa
`redis.asyncio` y `httpx.AsyncClient`. Se usa el helper `_run_async(coro)` que
detecta si hay un event loop corriendo y ejecuta la corutina adecuadamente.

### Persistencia entre runs

`_memory_proposal` sobrevive entre invocaciones gracias al `MemorySaver`
checkpointing de LangGraph (el estado se persiste tras cada transición de nodo).
`remember_node` lo limpia al procesarlo (aceptación o rechazo).

## Testing

Ver `tests/pipelines/test_memory_nodes.py` para:
- Tests unitarios de `_is_acceptance`, `_is_rejection`, `_extract_preferences`,
  `_extract_approved_facts`, `_extract_explicit_remember_request`.
- Tests de `memorize_node` con estados simulados.
- Tests de `remember_node` con propuestas pendientes simuladas.
- Tests de integración con `MemoryService` mockeado.
