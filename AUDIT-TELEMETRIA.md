# Auditoría del Plan de Telemetría — TrackFlow

> **Auditado:** 2026-10-16
> **Alcance:** FASE1-CATALOGO-DATOS.md, FASE2-DISENO-EVENT-ENVELOPE.md, FASE3-ESTRATEGIA-ENTREGA.md, event-schemas.json
> **Fuente de requisitos:** CONTEXT-TELE.md

---

## Resumen ejecutivo

| Requisito | Estado |
|-----------|--------|
| ✅ 5 métricas obligatorias presentes | ✅ **Correcto** |
| ✅ Cobertura amplia técnica + negocio | ✅ **Correcto** (45 eventos en F1, 18 seleccionados en F2) |
| ✅ Hipótesis y decisión por evento | ✅ **Correcto** |
| ✅ Event Envelope completo y consistente | ✅ **Correcto** (8 campos, formato `entidad_acción`) |
| ✅ Allowlist de propiedades documentado | ✅ **Correcto** (`additionalProperties: false` en cada esquema) |
| ✅ event-schemas.json válido y consistente | ⚠️ **Válido, con 2 discrepancias menores** |
| ✅ Stream/batch justificado por urgencia de negocio | ✅ **Correcto** |
| ✅ PII identificado y documentado | ✅ **Correcto** |
| ✅ Riesgos y exclusiones con pensamiento crítico | ✅ **Correcto** (14 descartados, 9 datos PII excluidos) |
| ✅ Precisión para instrumentación por otro dev | ✅ **Correcto** (con observaciones) |

---

## 1. Métricas obligatorias presentes

| # | `event_type` (CONTEXT-TELE.md) | FASE1 | FASE2 | JSON Schema |
|---|-------------------------------|-------|-------|-------------|
| 1 | `inbound_order_created` | ✅ 1.1 | ✅ 3.1 | ✅ Presente |
| 2 | `outbound_order_created` | ✅ 1.2 | ✅ 3.2 | ✅ Presente |
| 3 | `stock_threshold_triggered` | ✅ 1.3 | ✅ 3.3 | ✅ Presente |
| 4 | `direct_stock_edit_rejected` | ✅ 1.4 | ✅ 3.4 | ✅ Presente |
| 5 | `inventory_discrepancy_detected` | ✅ 1.5 | ✅ 3.5 | ✅ Presente |

**✅ Las 5 métricas obligatorias están presentes y correctamente identificadas en los 3 documentos.**

### Campos mínimos de inventario

CONTEXT-TELE.md exige: `warehouse`, `client_id`, `product_id`, `product_category`, `quantity`

| Evento | warehouse | client_id | product_id | product_category | quantity |
|--------|-----------|-----------|------------|-----------------|----------|
| `inbound_order_created` | ✅ | ✅ | ✅ | ✅ | ✅ + `reference` |
| `outbound_order_created` | ✅ | ✅ | ✅ | ✅ | ✅ + `exit_type` |
| `stock_threshold_triggered` | ✅ | ✅ | ✅ | ✅ | ✅ + `threshold_value`, `current_stock` |
| `direct_stock_edit_rejected` | ✅ | ✅ | ✅ | ✅ | ❌ (no aplica — no es transacción de cantidad) |
| `inventory_discrepancy_detected` | ✅ | ✅ | ✅ | ✅ | ✅ (como `expected_quantity`, `actual_quantity` + `variance`) |

> ℹ️ `direct_stock_edit_rejected` no tiene `quantity` porque describe un intento rechazado, no una operación de cantidad. Es coherente con la naturaleza del evento.

---

## 2. Cobertura técnica + negocio

### Dominios cubiertos

| Dominio | Naturaleza | Eventos en FASE1 | Eventos en FASE2 |
|---------|-----------|-----------------|-----------------|
| 📦 Inventario | **Negocio** | 8 | 6 |
| 🔐 Autenticación | **Técnica** (seguridad) | 9 | 3 |
| 🚚 Proveedores | **Negocio** | 6 | 3 |
| ⚠️ Incidencias | **Operaciones** | 6 | 2 |
| 📝 Leads web | **Negocio** (comercial) | 5 | 1 |
| 📊 Rendimiento API | **Técnica** | 4 | 2 |
| 🖥️ Frontend | **Técnica** | 1 | 0 |
| 🧭 Navegación | **Técnica** (UX) | 4 | 1 |
| 👥 Usuarios | **Mixto** | 3 | 0 |
| **Total** | | **45** | **18** |

**✅ Cobertura amplia.** La Fase 1 identifica 45 oportunidades que cubren 9 dominios, incluyendo tanto aspectos técnicos (autenticación, rendimiento, errores, navegación, frontend) como de negocio (inventario, proveedores, incidencias, leads, usuarios). La Fase 2 selecciona 18 para el plan definitivo, manteniendo representación de todos los bloques funcionales.

---

## 3. Hipótesis y decisión por evento

**✅ Cada uno de los 18 eventos en FASE2 tiene:**

| Elemento | Presente en todos |
|----------|-------------------|
| Descripción (cuándo se dispara) | ✅ |
| Hipótesis de negocio / operativa (`Necesitamos saber...`) | ✅ |
| Decisión que habilita (`lo que nos permite...`) | ✅ |
| Consumidor / responsable (Ana, Miguel, Carlos, etc.) | ✅ |
| Enlace al trigger en código | ✅ (FASE2 incluye ruta del endpoint) |
| Datos sensibles | ✅ (sección en cada evento) |

**✅ No hay eventos "por si acaso"** — cada uno responde a una hipótesis comprobable que habilita una decisión concreta. Los eventos sin decisión clara fueron descartados en FASE3 (ej. `inventory_sku_list_viewed` → "no genera decisión medible").

---

## 4. Event Envelope

### Campos presentes en FASE2

| Campo | Presente | Formato |
|-------|----------|---------|
| `eventId` | ✅ | UUID v4 (string) |
| `timestamp` | ✅ | ISO 8601 UTC (string) |
| `sessionId` | ✅ | string |
| `userId` | ✅ | string (`"anonymous"` para no autenticados) |
| `event_type` | ✅ | string, formato `entidad_acción` en pasado |
| `schemaVersion` | ✅ | semver `x.y.z` (string) |
| `requestId` | ✅ | string (trace / correlation ID) |
| `properties` | ✅ | object (payload específico del evento) |

### Consistencia

**✅ El mismo envelope se aplica a todos los eventos en los 3 documentos.**
**✅ No hay variación** entre la definición en FASE2 (sección 1), los ejemplos, y la validación en event-schemas.json.

---

## 5. Allowlist de propiedades

### En FASE2-DISENO-EVENT-ENVELOPE.md

**✅ Cada evento tiene una tabla "Allowlist" que lista explícitamente:**
- Nombre de propiedad
- Tipo de dato
- Obligatoriedad
- Descripción / valores permitidos

**✅ No hay propiedades ambiguas** — todas están documentadas con su propósito y restricciones (enums, rangos, formatos).

### En event-schemas.json

**✅ `additionalProperties: false`** está presente en las 19 definiciones (1 EventEnvelope + 18 eventos), impidiendo la inclusión de campos no autorizados. Esto implementa el allowlist a nivel de schema.

---

## 6. event-schemas.json

**✅ El archivo es JSON válido** (validado con `json.load()`).
**✅ Sigue el draft-07** (`"$schema": "http://json-schema.org/draft-07/schema#"`).
**✅ 19 definiciones** (1 Envelope + 18 eventos).
**✅ Todas las definiciones tienen:**
- `description` con hipótesis y decisión
- `type: "object"` + `required` + `properties`
- `additionalProperties: false`
- Tipos concretos (`integer`, `string`, `boolean`, `array`)
- Enumeraciones con valores exactos
- Rangos (`minimum`, `exclusiveMinimum`)
- Descripciones detalladas con ejemplos
- **17/19** tienen bloque `sensitive`
- **2/19** marcan `contains_pii: true` (`login_failed`, `lead_form_submitted`)

---

## 7. Stream / Batch justificado

**✅ Los 18 eventos están clasificados con justificación basada en urgencia de negocio:**

| Canal | Eventos | Justificación tipo |
|-------|---------|-------------------|
| Stream (12) | `inbound_order_created`, `outbound_order_created`, `stock_threshold_triggered`, `outbound_order_insufficient_stock`, `login_failed`, `supplier_status_toggled`, `incident_created`, `incident_status_changed`, `api_response_error_recorded` (5xx), `lead_form_submitted` | "Si se retrasa X horas, el daño es significativo/crítico" |
| Batch (9) | `direct_stock_edit_rejected`, `inventory_discrepancy_detected`, `login_succeeded`, `session_token_expired`, `supplier_created`, `supplier_rate_updated` (semanal), `page_navigated`, `cache_effectiveness_recorded`, `api_response_error_recorded` (4xx) | "La decisión es analítica/estratégica. No hay daño inmediato." |

**✅ Cada evento incluye tabla de evaluación** con 3 criterios (urgencia, daño si retraso 1h, daño si retraso 1 turno) y veredicto explícito.
**✅ El criterio de clasificación está claramente definido al inicio del documento** y se aplica consistentemente en todos los eventos.

---

## 8. PII y sanitización

### Documentación en FASE2 (sección 8)

**✅ Tabla de sanitización** que documenta 7+ campos sensibles con su tratamiento.

### Eventos con PII identificados

| Evento | Dato sensible | Tratamiento |
|--------|---------------|-------------|
| `login_failed` | Email del intento de login | SHA-256 hashing antes de emisión. Retención máxima 90 días. |
| `lead_form_submitted` | Datos personales del lead (nombre, email, teléfono) | **Exclusión total.** Solo perfil de negocio agregable. |
| ~~Todos los de inventario~~ | Datos del consumidor final | **Excluidos por diseño.** No pertenecen al dominio de inventario. |
| `incident_created` | Descripción, título de la incidencia | **Excluidos** — pueden contener PII. |
| `supplier_*` | Contact email del proveedor | **Excluido** — no necesario para telemetría agregada. |

### En event-schemas.json

**✅ Cada evento tiene bloque `sensitive`** con `contains_pii`, `notes`. Los 2 eventos con PII añaden `pii_fields`, `sanitization` y `retention_policy`.

---

## 9. Riesgos y exclusiones (FASE3 sección 4)

### Eventos descartados con razón

**✅ 14 eventos descartados** con justificación explícita:

| Evento descartado | Por qué se descartó |
|-------------------|---------------------|
| `outbound_order_dispatched` | Redundante con `created`. Mismo momento. |
| `inventory_sku_list_viewed` | Sin decisión de negocio medible. Ruido. |
| `outbound_order_cancelled` | Pertenece al dominio de última milla. |
| `login_attempt` | Reemplazado por `succeeded` + `failed`. |
| `password_changed` | Fuera del alcance. Plan de seguridad aparte. |
| `password_reset_requested` | Incluye PII (email). Fuera de alcance. |
| `user_registered` | No existe registro de usuarios en TrackFlow. |
| `user_profile_updated` | PII + bajo valor analítico. |
| `supplier_contact_updated` | PII del contacto comercial. |
| `incident_resolution_time_recorded` | Es dato derivado, no evento primario. |
| `frontend_error_occurred` | Sin RUM implementado. Pospuesto. |
| `api_latency_recorded` | Requiere acuerdo sobre percentiles y muestreo. |
| `inventory_stock_snapshot` | Masivo (miles de SKUs). Mejor event sourcing. |
| `email_opened` / `email_clicked` | Sistema externo de marketing. |

### Datos excluidos por privacidad

**✅ 9 datos excluidos** con justificación: email consumidor final, email contacto B2B, IP completa, nombre consumidor, dirección, teléfono, descripción de incidencias, comentarios de leads, stack traces.

### Exclusiones por costo / esfuerzo

**✅ 5 exclusiones** con motivos concretos: última milla (otro proyecto), RUM (no implementado), infraestructura gestionada (sin acceso), logs completos (volumen 100-1000x), APIs externas (variabilidad).

### Riesgos activos con mitigaciones

**✅ 7 riesgos** evaluados con probabilidad, impacto y mitigación: fuga PII, saturación Black Friday, eventos sin schema, deriva semántica, dependencia síncrona, duplicados, olvido en nuevos desarrollos.

---

## 10. Discrepancias encontradas

### 10.1 Nombres de eventos: FASE1 vs FASE2

| FASE1 | FASE2 / FASE3 / JSON | ¿Afecta instrumentación? |
|-------|---------------------|--------------------------|
| `login_success` | `login_succeeded` | Sí — un desarrollador que siga FASE1 usaría el nombre incorrecto |
| `login_failure` | `login_failed` | Sí — mismo problema |
| `api_response_error` | `api_response_error_recorded` | Sí — mismo problema |
| `cache_effectiveness` | `cache_effectiveness_recorded` | Sí — mismo problema |

**Recomendación:** Actualizar FASE1 para reflejar los nombres definitivos de FASE2, o añadir nota al inicio de FASE2 indicando que los nombres se refinaron para cumplir la taxonomía de verbos en pasado.

### 10.2 Propiedades: FASE1 vs FASE2

| Evento | FASE1 | FASE2 |
|--------|-------|-------|
| `inbound_order_created` | 5 campos (solo mínimos) | 6 campos (+ `reference`) |
| `login_success/succeeded` | `user_id`, `user_role`, `warehouse_country`, `ip_country` | `user_id`, `user_role`, `ip_country`, `auth_method` |
| `login_failure/failed` | `email` hash, `ip_address` hash | `email_hash`, `ip_country` (país, no IP) |

**Recomendación:** FASE2 es la versión autoritativa. FASE1 debe anotarse indicando que los schemas finales están en FASE2.

### 10.3 `direct_stock_edit_rejected` — ausencia de `quantity`

CONTEXT-TELE.md lista `quantity` como campo mínimo común de inventario, pero este evento describe un intento rechazado (sin transacción de cantidad). La ausencia es **justificada conceptualmente**, pero debe documentarse explícitamente en FASE1.

**Recomendación:** Añadir nota en FASE1 sección 1.4: "Este evento no incluye `quantity` porque no representa una transacción de stock, sino un intento rechazado."

### 10.4 `lead_form_submitted` — flag `contains_pii` semánticamente inconsistente

En `event-schemas.json`, `lead_form_submitted` tiene `contains_pii: true` pero `pii_fields: ["none_included"]`. Es una contradicción.

**Recomendación:** Cambiar `contains_pii` a `false` ya que ningún campo PII real se captura.

---

## 11. Precisiones para instrumentación

### Suficiencia

**Un desarrollador podría instrumentar los 18 eventos con:**

| Recurso disponible | ¿Qué le aporta? |
|-------------------|-----------------|
| FASE2 sección 1 | Envelope estándar con 8 campos + ejemplo JSON completo |
| FASE2 secciones 3-7 | Cuándo emitir, qué endpoint dispara, qué properties incluir, valores enum, tipos, rangos |
| FASE2 sección 8 | Cómo sanitizar datos sensibles antes de emitir |
| `event-schemas.json` | Validación programática de cada evento contra schema |
| FASE3 sección 1 | ¿Enviar a stream o batch? ¿Con qué latencia? |
| FASE3 sección 3 | Reglas de throttle/debounce por evento |
| FASE3 sección 4 | Qué NO instrumentar, por qué, y riesgos a mitigar |

### Complementos deseables (no críticos)

1. **Ejemplo JSON por evento** (actualmente hay 1 ejemplo genérico en FASE2 sección 1.2)
2. **Helper de emisión** en Python/TypeScript que construya el envelope y valide contra schema
3. **Convención de prefijos** para IDs de ejemplo (`sess_`, `req_`)

---

## 12. Veredicto final

| Dimensión | Puntuación | Notas |
|-----------|-----------|-------|
| Completitud (vs. CONTEXT-TELE.md) | ✅ 10/10 | 5 obligatorias presentes y correctas |
| Amplitud (técnica + negocio) | ✅ 9/10 | 9 dominios. Sin frontend/RUM por decisión consciente |
| Justificación (hipótesis + decisión) | ✅ 10/10 | Cada evento con hipótesis y decisión medible |
| Consistencia entre documentos | ⚠️ 7/10 | 4 nombres inconsistentes FASE1 vs FASE2 |
| Precisión técnica (schemas, tipos) | ✅ 9/10 | Schemas completos y válidos. Sin discriminador automático |
| Privacidad y PII | ✅ 9/10 | Bien documentado. 1 flag semántico inconsistente |
| Entrega (stream/batch) | ✅ 10/10 | Justificado por urgencia de negocio, no preferencia técnica |
| Riesgos y exclusiones | ✅ 10/10 | 14 descartados, 9 PI excluidos, 7 riesgos con mitigación |
| **Global** | **✅ 9.25/10** | Sólido y listo para instrumentación. Discrepancias menores corregibles. |

---

## 13. Acciones recomendadas

| # | Acción | Archivo | Prioridad |
|---|--------|---------|-----------|
| 1 | Corregir nombres de eventos en FASE1 para coincidir con FASE2 | `FASE1-CATALOGO-DATOS.md` | 🔴 Alta |
| 2 | Documentar en FASE1 que `direct_stock_edit_rejected` no lleva `quantity` | `FASE1-CATALOGO-DATOS.md` | 🟡 Media |
| 3 | Cambiar `contains_pii: true` → `false` en `lead_form_submitted` | `event-schemas.json` | 🟢 Baja |
| 4 | Añadir nota de normalización de nombres al inicio de FASE2 | `FASE2-DISENO-EVENT-ENVELOPE.md` | 🟢 Baja |

---

*Auditoría generada para validar la consistencia y completitud del Plan de Telemetría de TrackFlow (Fases 1–3).*