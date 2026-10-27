# 5 Decisiones Arquitectónicas del Sistema de Memoria

**Versión:** 1.0  
**Fecha:** 2026-10-27  
**Fuentes:** `architecture-decision.md`, `memory_nodes.py`, `consolidation.py`, `Brifing-pipeline.md`, `CONTEXT-TELE.md`, `CONTEXT.md`

---

## 1. Tipos de Memoria: ¿Por qué episódica + semántica vectorial y no knowledge graph?

### Stores implementados

| Tipo | Store | Qué almacena | Clave / Colección | Ejemplo |
|------|-------|-------------|-------------------|---------|
| **Episódica** | Redis (KV) | Historial de conversación por `thread_id` | `agent_memory:episode:{thread_id}` → JSON | Contexto de la sesión anterior |
| **Preferencias** | Redis (KV) | Configuración del usuario | `agent_memory:pref:{user_id}:{key}` | `language → "es"`, `detail_level → "summary"` |
| **Semántica vectorial** | Qdrant | Hechos confirmados como vectores 1536d (COSINE) | Colección `trackflow_agent_memory` | `{key: "person:role:juan_perez", value: "Gerente de Logística"}` |

### ¿Por qué NO knowledge graph?

- Los hechos que el agente recuerda son **aserciones factuales simples** (roles, preferencias, decisiones), no una red compleja de entidades con múltiples tipos de relación.
- La base de conocimiento documental ya está indexada en una colección Qdrant independiente (`trackflow_knowledge`), separada del sistema de memoria del agente.
- Añadir un grafo de conocimiento sería sobredimensionar: añadiría latencia, mantenimiento de esquemas y complejidad innecesaria para un volumen de relaciones que rara vez supera la docena por cliente.

---

## 2. Información que Nunca Debe Entrar en Memoria

Extraído de las restricciones no negociables del BRIEFING, CONTEXT y el código fuente.

| Restricción | Origen | Fundamento |
|-------------|--------|------------|
| **Escritura en `telemetry_events`** | Brifing-pipeline §7 | El pipeline y el agente pueden **leer** telemetry_events, pero nunca escribir ahí. |
| **Mezcla de datos entre clientes** | Brifing-pipeline §7 | "Cada fila pertenece a un único cliente — nunca agregues entre clientes en la misma fila". |
| **Datos personales del consumidor final** | CONTEXT-TELE §3 | "No incluyas datos personales del destinatario del paquete en properties". |
| **Modificaciones directas de stock sin trazabilidad** | CONTEXT-TELE §6 | "El stock nunca se modifica directamente: toda modificación pasa por InboundOrder/OutboundOrder". |
| **Consultas operacionales puntuales** | `memorize_node` (código) | Estado de tickets, stock, políticas de devolución, saludos, agradecimientos. |
| **Información no confirmada** | `architecture-decision.md` | Sin confirmación explícita del usuario, nunca se persiste. |

### Perímetro de seguridad del agente

El agente puede **proponer** recordar hechos operacionales legítimos (roles, preferencias, decisiones), pero el diseño establece un perímetro que prohíbe:

1. Datos personales de terceros (consumidores finales).
2. Escritura en sistemas de origen (telemetría, inventario).
3. Hechos no confirmados explícitamente por el usuario.

---

## 3. Política de Olvido y Propuestas Pendientes

### Consolidación (decidir qué olvidar)

El `MemoryConsolidationService` implementa **4 estrategias complementarias**:

| Estrategia | ¿Qué hace? | ¿Cuándo se activa? | Umbral por defecto |
|-----------|-----------|---------------------|--------------------|
| **Sync orphans** (Qdrant → Redis) | Elimina puntos vectoriales cuya clave KV asociada ya no existe (TTL o eliminación manual) | Nocturna (cron) / manual | N/A |
| **Deduplicación vectorial** | Fusiona puntos con similitud coseno > 0.95: conserva el más reciente | Nocturna (cron) / manual | `_DEFAULT_DEDUP_SIMILARITY = 0.95` |
| **Compresión de episodios** | Episodios > 15 días → resúmenes mensuales (1/3 del espacio) | Nocturna (cron) / manual | `_DEFAULT_EPISODE_AGE_DAYS = 15` |
| **Poda de baja relevancia** | Puntos con `last_recalled` > 60 días → eliminados | Nocturna (cron) / manual | `_DEFAULT_LOW_RELEVANCE_DAYS = 60` |

### Modos de activación

1. **Programación externa** (cron / scheduler) — modo principal, ejecución nocturna.
2. **Trigger manual** vía endpoint o tool del agente.
3. **Nodo opcional en el grafo** solo si han pasado > 6h desde la última consolidación (rate-limited).

### Propuesta pendiente si el usuario nunca responde

El flujo está diseñado para manejar este caso sin pérdida ni aceptación por omisión:

1. **La propuesta sobrevive entre invocaciones** gracias al `MemorySaver` checkpointing de LangGraph. El estado (`_memory_proposal`) se persiste tras cada transición de nodo.
2. **El usuario nunca responde directamente a la propuesta**: En la siguiente interacción, `remember_node` ejecuta `_classify_proposal_intent()` sobre el mensaje completo.
3. **Si cambia de tema** (pregunta por stock, tickets, política...) → `topic_change` → **descarte automático**.
4. **Si responde ambiguamente** (< 8 caracteres sin patrón claro) → `ambiguous` → **descarte por seguridad**.

**Regla sagrada:** "Nunca se asume aprobación por silencio o ambigüedad". La propuesta se mantiene indefinidamente hasta que el usuario interactúa de nuevo, y solo en la siguiente interacción se decide su destino.

---

## 4. Protección Contra Envenenamiento de Memoria

El sistema tiene **siete capas de defensa** para prevenir que un usuario malicioso inyecte hechos falsos:

### 🛡 Capa 1 — Propuesta explícita (nunca escritura directa)
El agente **nunca escribe directamente a memoria**. Solo genera una `_memory_proposal` como pregunta al usuario. Sin confirmación explícita, no hay persistencia. No existe ningún camino de código que permita escritura directa sin pasar por la propuesta.

### 🛡 Capa 2 — Clasificador basado en reglas (no LLM)
`_classify_proposal_intent()` usa **regex deterministas**, no un LLM. Patrones ambiguos o redactados sofisticadamente no producen falsos positivos. No hay "prompt injection" posible sobre el clasificador.

### 🛡 Capa 3 — Una propuesta a la vez
Si `memorize_node` encuentra una `_memory_proposal` pendiente (`type != none`), **NO genera una segunda** (audita `event: "skipped"`). Esto evita que un usuario malicioso acumule propuestas falsas.

### 🛡 Capa 4 — Cambio de tema = descarte automático
Si el usuario ignora la propuesta y pregunta otra cosa → `topic_change` → descarte. No hay "aceptación por omisión".

### 🛡 Capa 5 — Edición = descarte + notificación (nunca se asume)
Si el usuario dice "en realidad cambia eso por X" → `edit` → la propuesta se descarta y se notifica al usuario que debe repetirla correctamente. **Nunca se asume la corrección**.

### � Capa 6 — Ambigüedad = descarte por seguridad
Mensajes vacíos, de solo espacios, o < 8 caracteres sin patrón claro → `ambiguous` → descarte.

### 🛡 Capa 7 — Auditoría completa
Cada evento (proposed, accepted, rejected, edited, discarded, skipped, error) se registra en `data/audit/memory_proposals.jsonl` con timestamp, mensaje origen y resultado. Proporciona trazabilidad forense.

**En conjunto:** Para que un hecho malicioso entre en memoria, el usuario debe (a) esperar a que el agente haga una propuesta, (b) responder explícitamente "sí" a esa propuesta exacta, y (c) superar el filtro de patrones de aceptación. No hay atajo, escritura directa, ni aceptación implícita.

---

## 5. Por Qué una Arquitectura Single-Agent es Suficiente

### El grafo mismo es la máquina de estados

LangGraph no es solo un "agente" — es el **orquestador del flujo de decisión**. Las aristas condicionales `route_after_remember` manejan toda la lógica sin necesidad de un segundo agente:

```
START → validate → remember → (accept/reject puro → finalize)
                              → (combinado → intent → tools → generate → memorize → finalize)
                              → (edit/topic_change/ambiguous → intent → ...)
```

### La auto-evaluación es determinista, no necesita LLM

`memorize_node` sigue **4 reglas explícitas** codificadas en Python:

1. ¿Preferencia detectada? → proponer
2. ¿Hecho aprobado en la respuesta? → proponer
3. ¿Solicitud explícita del usuario? → proponer
4. Ninguna de las anteriores → `{"type": "none"}`

No se necesita un "agente evaluador" separado porque la lógica es contable y acotada.

### El clasificador de intención es reglas, no juicio

`_classify_proposal_intent()` es regex puro: 4 patrones independientes (`_ACCEPT_PATTERNS`, `_REJECT_PATTERNS`, `_EDIT_PATTERNS`, `_TOPIC_CHANGE_SIGNALS`) más un fallback `ambiguous`. No hay llamada a un modelo, no hay agente separado.

### Ventajas frente a un hipotético agente evaluador separado

| Aspecto | Single-agent (actual) | Agente evaluador separado |
|---------|----------------------|--------------------------|
| **Latencia** | 0 llamadas extra al LLM | +1 llamada por turno (más lento) |
| **Complejidad** | Nodos en el mismo grafo | Comunicación entre agentes, colas |
| **Seguridad** | Reglas deterministas | LLM puede ser engañado |
| **Mantenimiento** | Un código, un test suite | Tests de integración entre agentes |
| **State management** | Checkpointing natural de LangGraph | Sincronización de estado entre agentes |

**En conclusión:** El dominio de memoria de TrackFlow es lo suficientemente acotado (preferencias, hechos simples, contexto operacional) y las reglas de decisión son explícitas como para que un único grafo con nodos especializados y aristas condicionales sea la solución óptima. Introducir un segundo agente añadiría latencia, superficie de ataque y complejidad de coordinación sin beneficio real.

---

**Documento generado a partir del análisis de:**
- `docs/memory/architecture-decision.md`
- `services/rag_agent/memory_nodes.py`
- `services/memory/consolidation.py`
- `Brifing-pipeline.md`
- `CONTEXT-TELE.md`
- `CONTEXT.md`