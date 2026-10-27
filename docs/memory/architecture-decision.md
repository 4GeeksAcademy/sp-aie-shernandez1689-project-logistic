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

---

# Consolidación y Política de Expiración

## Contexto

La memoria del agente tiene dos stores (KV + vectorial) con ciclos de vida
distintos. Sin un mecanismo de consolidación coordinado:

- **Qdrant acumula huérfanos**: puntos vectoriales cuya clave KV asociada
  ya expiró (TTL de Redis) pero que siguen vivos en la colección vectorial,
  contaminando búsquedas semánticas.
- **Duplicados se multiplican**: un mismo hecho confirmado en dos sesiones
  diferentes puede persistir como dos puntos separados con alta similitud,
  sesgando la relevancia en `search_similar`.
- **Episodios diarios se acumulan**: la memoria episódica (conversaciones)
  crece indefinidamente si no se condensa en resúmenes de alto nivel.
- **Información obsoleta nunca se limpia**: hechos que el agente ya no
  recupera semánticamente siguen ocupando espacio y añadiendo ruido.

## Política de Expiración por Store

### KV Store (Redis)

El `MemoryKVStore` asigna TTL automático según el tipo de clave:

| Tipo de clave          | TTL       | Fundamento                                                                 |
|------------------------|-----------|---------------------------------------------------------------------------|
| `fact:*`               | **∞** (sin expiración) | Hechos aprobados explícitamente por el usuario — se consideran verdad semántica duradera. |
| `person:role:*`        | **∞** (sin expiración) | Mapeos persona→cargo que el usuario confirmó — no se espera que cambien sin intervención. |
| `decision:*`           | **∞** (sin expiración) | Decisiones operativas aprobadas (umbrales de stock, criterios de alerta, etc.). |
| `user:*:pref:*`        | **90 días**           | Preferencias del usuario — pueden quedar obsoletas si el usuario cambia de opinión o si la cuenta se inactiva. |
| `session:*:summary`    | **30 días**           | Resúmenes de sesiones — útiles para contexto inmediato, pero irrelevantes tras varias semanas. |
| `episode:*`            | **30 días**           | Episodios de conversación — memoria transaccional de corto plazo.        |

Los TTL se aplican automáticamente al escribir (ver `kv.write()`) y eliminan
la clave del store una vez vencidos.

### Vector Store (Qdrant)

`MemoryVectorStore` asigna `expires_at` en el payload si se proporciona
`ttl_days`. Por defecto, los puntos vectoriales **no tienen expiración**
(se gobiernan por consolidación, no por TTL). El campo `expires_at` existe
como mecanismo de seguridad pero actualmente no es evaluado automáticamente
— la limpieza se delega a la consolidación.

## Estrategias de Consolidación

`MemoryConsolidationService` implementa cuatro estrategias complementarias
que se ejecutan en orden:

### 1. Sync Cleanup (Qdrant → Redis)

**Propósito**: eliminar de Qdrant los puntos "huérfanos" cuya clave KV
ya no existe en Redis (por TTL expirado o eliminación explícita).

**Funcionamiento**:
1. Recorre todos los puntos en Qdrant que tengan `metadata.key`.
2. Consulta las claves activas en Redis bajo `agent_memory:fact:*`.
3. Marca como huérfano todo punto cuyo `metadata.key` no esté en el
   conjunto de claves activas.
4. Elimina los huérfanos de Qdrant en un solo batch.

**Cobertura**: solo aplica a tipos `fact`, `person_role` y `user_preference`
(episodios no tienen referencias KV).

**Racional**: Sin este paso, cuando expira un TTL de Redis, el dato
sigue vivo en búsquedas semánticas — el agente puede recuperar un hecho
que técnicamente ya no existe. La sincronización es de sentido único
(Qdrant se limpia contra Redis); nunca se restaura KV desde Qdrant.

### 2. Deduplicación Vectorial

**Propósito**: fusionar puntos en Qdrant con similitud coseno > 0.95
y texto casi idéntico.

**Funcionamiento**:
1. Agrupa puntos por `memory_type`.
2. Dentro de cada grupo, compara pares usando `_is_near_duplicate()`:
   - Similitud coseno de vectores > 0.95 **y**
   - Coincidencia de texto ≥ 85% (Dice coefficient sobre bigramas,
     truncado a 500 caracteres).
3. Conserva el punto más reciente (`created_at` más alto) y elimina
   el más antiguo.

**Racional**: Un mismo hecho confirmado en dos sesiones separadas
(ej. "Ana es Gerente" confirmado en enero y en marzo) crea dos puntos
con vectores casi idénticos. Al hacer `search_similar`, ambos aparecen
en los resultados, duplicando la señal de relevancia y desplazando
otros hechos. La deduplicación mantiene solo una copia, la más reciente.

### 3. Compresión de Episodios

**Propósito**: condensar episodios antiguos (≥ 15 días) en resúmenes
mensuales.

**Funcionamiento**:
1. Identifica puntos de tipo `episode` con `created_at` anterior al
   umbral (15 días por defecto).
2. Agrupa por mes calendario.
3. Para cada grupo con ≥ 2 episodios, construye un punto resumen:
   - Título: `[Resumen mensual YYYY-MM]`
   - Cuerpo: una línea por episodio con session_id y texto truncado.
4. Almacena el nuevo punto resumen sin TTL (permanente).
5. Elimina los episodios originales.

**Racional**: Las sesiones diarias son útiles para contexto inmediato,
pero tras 15 días la mayoría de la información transaccional pierde
relevancia. El resumen mensual ocupa ~1/3 del espacio de N episodios
diarios y sigue siendo útil para búsqueda semántica ("¿qué pasó en
marzo?"). No se pierde información: cada línea contiene el session_id
para recuperar el detalle desde los logs si fuera necesario.

### 4. Poda de Baja Relevancia

**Propósito**: eliminar puntos vectoriales que el agente no ha recuperado
semánticamente en los últimos 60 días.

**Funcionamiento**:
1. Para cada tipo (`fact`, `person_role`, `user_preference`), agrupa
   puntos por clave semántica (`metadata.key`).
2. Para grupos con ≥ 2 puntos con la misma clave, conserva el más
   reciente y elimina los demás si:
   - Su `last_recalled` es anterior al umbral (60 días), **o**
   - Carecen de `last_recalled` (nunca se han recuperado desde que
     se almacenó la entrada más reciente).

**Racional**: Si el agente no ha necesitado un hecho específico en 2
meses de uso continuo, es probable que ya no sea relevante para la
conversación. Se conserva siempre la versión más reciente de cada
clave semántica para no perder la última actualización. Los puntos
con `last_recalled` reciente o con clave única nunca se eliminan.

## Ejecución

### Rate Limiting

La consolidación **no se ejecuta en cada turno** del grafo (sería
muy costosa — requiere escanear toda la colección vectorial y hacer
múltiples consultas a Qdrant). Se controla mediante dos mecanismos:

1. **Rate limit interno**: `MemoryConsolidationService.consolidate()`
   respeta un mínimo de **6 horas** entre ejecuciones. Si se llama
   antes, devuelve un reporte vacío sin ejecutar estrategias.
2. **Señal en el grafo**: `memorize_node` activa `consolidation_pending`
   solo cuando hay una propuesta de memoria (`type != "none"`). Esto
   evita invocar `consolidate_node` en consultas operacionales que
   no generan memoria.

El rate limit existe porque la consolidación es una operación O(n*m)
sobre la colección vectorial — ejecutarla en cada turno consumiría
recursos de Qdrant sin beneficio proporcional.

### Integración con el Grafo

El grafo LangGraph incluye un nodo `consolidate` en el flujo:

```text
memorize_node
  ├── consolidation_pending = True  → consolidate_node → finalize
  └── consolidation_pending = False → finalize
```

`consolidate_node` llama a `_get_consolidation().consolidate()` a
través de `_run_async()` (el nodo es síncrono pero la consolidación
usa `async` internamente). El reporte se almacena en el estado como
`_consolidation_report` y `consolidation_pending` se resetea a `False`.

### Ejecución Externa

Además del trigger desde el grafo, la consolidación puede ejecutarse
por:

- **Cron / scheduler**: para limpieza nocturna programada.
- **Endpoint HTTP**: para trigger manual desde la UI de administración.
- **Tool del agente**: para invocación explícita bajo demanda.

## Auditoría

Cada ejecución de consolidación se registra en el mismo audit log que
las propuestas de memoria: `data/audit/memory_proposals.jsonl` (JSONL).

Formato de entrada:

```json
{
  "event": "consolidation",
  "timestamp": "2026-03-21T02:00:00Z",
  "details": {
    "synced_orphans": 12,
    "dedup_removed": 3,
    "dedup_kept": 15,
    "episodes_compressed": 24,
    "low_relevance_pruned": 7,
    "duration_ms": 3450.2,
    "errors": []
  }
}
```

Los errores parciales (ej. timeout en Qdrant durante dedup) no
interrumpen la consolidación completa — las estrategias son
independientes y el error se captura por estrategia.

## Archivos

- `services/memory/consolidation.py` — `MemoryConsolidationService`
  con las 4 estrategias, rate limiting, y auditoría.
- `services/rag_agent/memory_nodes.py` — `consolidate_node` (nodo del
  grafo) y helper `_get_consolidation()` (singleton lazy).
- `services/rag_agent/graph.py` — arista condicional `route_after_memorize`
  y `route_after_consolidate`, estado `AgentState` con campos
  `consolidation_pending` y `_consolidation_report`.
- `services/memory/kv.py` — TTL por tipo de clave (`DEFAULT_EPISODE_TTL`,
  `DEFAULT_FACT_TTL`, `DEFAULT_PREFS_TTL`).
- `services/memory/vector.py` — `store()` con soporte para `ttl_days`
  y payload `expires_at`.

## Testing

Ver `tests/pipelines/test_consolidation.py` para 37 tests unitarios:

| Clase de test | Estrategia cubierta |
|---|---|
| `TestConsolidationReport` | Dataclass y serialización |
| `TestNormalizeText` | Helper de normalización para dedup |
| `TestConsolidationRateLimit` | Rate limiting (6h) |
| `TestConsolidationSyncCleanup` | Sync cleanup: huérfanos KV→Qdrant |
| `TestConsolidationDeduplicate` | Dedup: similitud coseno + texto |
| `TestIsNearDuplicate` | Umbral de duplicación (≥85% bigramas) |
| `TestConsolidationCompressEpisodes` | Compresión mensual de episodios |
| `TestConsolidationPruneLowRelevance` | Poda por `last_recalled` |
| `TestConsolidationIntegration` | Flujo completo `consolidate()` |
| `TestAuditConsolidation` | Escritura de audit log JSONL |

Los tests usan `_await()` helper sobre `asyncio.run()` en lugar de
`pytest-asyncio` para compatibilidad con pytest 9.x.
