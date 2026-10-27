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
  remember_node clasifica la intención con _classify_proposal_intent()
    accept       → persiste en Redis/Qdrant (si solo "sí" → finaliza)
                    (si "sí, y dime X..." → continúa al intent)
    reject       → descarta (idem mensaje combinado)
    edit         → descarta y notifica (nunca asume)
    topic_change → descarta automáticamente (nunca asume aprobación)
    ambiguous    → descarta por seguridad (nunca asume)
```

## Reglas de Negocio Clave (v2)

### 1. Clasificador Estructurado

Reemplaza los antiguos booleanos `_is_acceptance`/`_is_rejection` por una
etiqueta explícita con 5 valores: `accept | reject | edit | topic_change | ambiguous`.
Usa el texto COMPLETO del mensaje, no solo el primer token.

### 2. Una Propuesta a la Vez

Si `memorize_node` encuentra una `_memory_proposal` pendiente
(`type != none`), **NO genera una segunda** (audita `event: "skipped"`).

### 3. Cambio de Tema = Descarte Automático

Nunca se asume aprobación por silencio o ambigüedad.

### 4. Log de Auditoría

Cada decisión se registra en `data/audit/memory_proposals.jsonl` (JSONL).
Eventos: `proposed | accepted | rejected | edited | discarded | skipped | error | none`.

### 5. Mensaje Combinado

Si el usuario acepta/rechaza y además pregunta algo nuevo
(ej. "sí, y dime el stock"), la propuesta se resuelve y la conversación
continúa. `_is_pure_confirmation()` distingue confirmación pura de combinada.

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

### `_memory_intent_classification` (estado del grafo — no persiste)

Producido por `remember_node` tras clasificar la intención del usuario.
Valores: `"accept" | "reject" | "edit" | "topic_change" | "ambiguous" | None`.

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
5. **Si ya hay una propuesta pendiente** → saltar sin generar segunda.

## Ejemplos de NO Memorización

| Pregunta | Motivo |
|----------|--------|
| "¿Cuál es el estado del ticket INC-123?" | Consulta operacional puntual |
| "Necesito el stock del SKU CLT-SNK-W-42" | Consulta de inventario transaccional |
| "¿Qué dice la política de devoluciones?" | Pregunta documental RAG |
| "Gracias" / "ok" / "entendido" | Sin información nueva |
| Preguntas sobre el clima | Fuera del dominio de TrackFlow |

## Clasificador de Intención (`_classify_proposal_intent`)

### Patrones de Aceptación

`sí`, `si`, `ok`, `okay`, `de acuerdo`, `confirmo`, `adelante`, `claro`,
`sí, guárdalo`, `sí, recuérdalo`, `yes`, `sure`, `go ahead`

También detecta primer token: `sí`, `si`, `ok`, `okay`, `yes`, `sure`,
`claro`, `adelante`.

### Patrones de Rechazo

`no`, `no gracias`, `no guardes`, `no recuerdes`, `nope`, `not now`,
`no thanks`, `no quiero`

También detecta primer token: `no`, `nope`.

### Patrones de Edición

`en realidad`, `cambia`, `corrige`, `mejor di`, `edita`, `modifica`,
`no es eso`, `rectifica`, `realmente`

### Cambio de Tema

Mensajes de ≥8 caracteres que contienen términos operacionales (ticket,
stock, inventario, sku, política, devolución, reporte, dashboard, etc.).

### Ambigüedad

Mensajes vacíos, de solo espacios, o muy cortos (<8 caracteres) sin
coincidencia con patrones claros → `ambiguous`.

## Routing (`route_after_remember`)

```
state._memory_intent_classification
  ├── accept/reject + _is_pure_confirmation  → finalize
  ├── accept/reject + no _is_pure_confirmation → intent (combinado)
  ├── edit/topic_change/ambiguous → intent (descartar y continuar)
  └── None (sin propuesta) → intent (normal)
```

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
`remember_node` lo limpia al procesarlo (aceptación, rechazo, edición o descarte).

## Testing

Ver `tests/pipelines/test_memory_helpers.py` para:
- Tests unitarios de `_classify_proposal_intent` (cobertura 5 etiquetas, 90 tests).
- Tests unitarios de `_is_pure_confirmation`.
- Tests unitarios de `_audit_proposal` (verificación escritura JSONL).
- Tests unitarios de `_extract_preferences`, `_extract_approved_facts`,
  `_extract_explicit_remember_request`.

Para tests de nodos completos con MemoryService mockeado, ver
`tests/pipelines/test_agent_graph_routing.py` como patrón de mocking.
