# Plan de Telemetría — TrackFlow

> **Empresa:** TrackFlow — Logística de almacén y última milla (Los Ángeles + Zaragoza)
> **Unidad:** TrackFlow Tech
> **Última actualización:** 2026-10-16
> **Documento consolidado de:** FASE1-CATALOGO-DATOS.md, FASE2-DISENO-EVENT-ENVELOPE.md, FASE3-ESTRATEGIA-ENTREGA.md, AUDIT-TELEMETRIA.md

---

## Índice

1. [Resumen ejecutivo](#1-resumen-ejecutivo)
2. [Contexto del negocio](#2-contexto-del-negocio)
3. [Catálogo de oportunidades (Fase 1)](#3-catálogo-de-oportunidades-fase-1)
4. [Event Envelope estándar (Fase 2)](#4-event-envelope-estándar-fase-2)
5. [Eventos seleccionados — Schemas y allowlists (Fase 2)](#5-eventos-seleccionados--schemas-y-allowlists-fase-2)
6. [Estrategia de entrega (Fase 3)](#6-estrategia-de-entrega-fase-3)
7. [Guía de sanitización de datos sensibles](#7-guía-de-sanitización-de-datos-sensibles)
8. [Riesgos y exclusiones](#8-riesgos-y-exclusiones)
9. [Auditoría y veredicto](#9-auditoría-y-veredicto)
10. [Schemas de validación](#10-schemas-de-validación)

---

## 1. Resumen ejecutivo

TrackFlow necesita un **plan de telemetría** para sus dos almacenes (Los Ángeles y Zaragoza) que cubra las operaciones de inventario, la cadena de suministro y la interacción con clientes B2B. El plan se ha desarrollado en tres fases:

| Fase | Objetivo | Resultado |
|------|----------|-----------|
| **Fase 1** | Catálogo de datos | 45 oportunidades identificadas en 9 dominios (5 obligatorias + 40 oportunidades) |
| **Fase 2** | Diseño de schemas | 18 eventos con Event Envelope estándar, allowlists y JSON Schema |
| **Fase 3** | Estrategia de entrega | Clasificación stream/batch, throttle/debounce, riesgos y exclusiones |

**Auditoría final:** 9.25/10 — Plan listo para instrumentación.

---

## 2. Contexto del negocio

### 2.1 La empresa

TrackFlow es una empresa de logística con dos almacenes y operaciones de última milla.

### 2.2 Almacenes

| Almacén | Ubicación | Zona horaria | Código |
|---------|-----------|-------------|--------|
| **los_angeles** | Los Ángeles, California, EE. UU. | PT (UTC-7/8) | `los_angeles` |
| **zaragoza** | Zaragoza, Aragón, España | CET/CEST (UTC+1/2) | `zaragoza` |

### 2.3 Categorías de producto

- `fashion` — Moda y complementos
- `electronics` — Electrónica de consumo
- `cosmetics` — Cosmética y cuidado personal

### 2.4 Stakeholders

| Persona | Rol | Domina |
|---------|-----|--------|
| **Ana** | Operations / Warehouse Manager | Inventario, operaciones de almacén |
| **Miguel** | Commercial / Sales | Leads, clientes B2B |
| **Carlos** | Suppliers / Operations | Proveedores |
| **Valentina** | Incidents / Support | Incidencias |
| **Andrés** | CTO / Security | Autenticación, rendimiento, seguridad |
| **Thomas** | Executive | Visión global, reporting ejecutivo |

### 2.5 Convenciones generales

- **Nombres de eventos:** Patrón `domain_entity_action`, verbos en pasado (p.ej. `inbound_order_created`, no `inbound_order_create`)
- **Campos comunes de inventario:** `warehouse`, `client_id`, `product_id` (SKU), `product_category`
- **IDs:** UUID v4 para `eventId`, ISO 8601 UTC para `timestamp`, semver `x.y.z` para `schemaVersion`

---

## 3. Catálogo de oportunidades (Fase 1)

### 3.1 Mapa de 45 eventos por dominio

| # | Dominio | Oportunidades | Seleccionados en Fase 2 |
|---|---------|--------------|------------------------|
| 📦 | Inventario | 8 | 6 |
| 🔐 | Autenticación | 9 | 3 |
| 🚚 | Proveedores | 6 | 3 |
| ⚠️ | Incidencias | 6 | 2 |
| 📝 | Leads (web) | 5 | 1 |
| 📊 | Rendimiento | 4 | 2 |
| 🖥️ | Frontend | 1 | 0 |
| 🧭 | Navegación | 4 | 1 |
| 👥 | Usuarios | 3 | 0 |
| **Total** | | **45** | **18** |

### 3.2 Catálogo completo

| # | event_type | Prioridad | Dominio |
|---|-----------|-----------|---------|
| 1 | `inbound_order_created` | ✅ Obligatorio | Inventario |
| 2 | `outbound_order_created` | ✅ Obligatorio | Inventario |
| 3 | `stock_threshold_triggered` | ✅ Obligatorio | Inventario |
| 4 | `direct_stock_edit_rejected` | ✅ Obligatorio | Inventario |
| 5 | `inventory_discrepancy_detected` | ✅ Obligatorio | Inventario |
| 6 | `outbound_order_dispatched` | 💡 Oportunidad | Inventario |
| 7 | `inventory_order_list_viewed` | 💡 Oportunidad | Inventario |
| 8 | `outbound_order_insufficient_stock` | 💡 Oportunidad | Inventario |
| 9 | `login_attempt` | 💡 Oportunidad | Autenticación |
| 10 | `login_succeeded` | 💡 Oportunidad | Autenticación |
| 11 | `login_failed` | 💡 Oportunidad | Autenticación |
| 12 | `session_token_expired` | 💡 Oportunidad | Autenticación |
| 13 | `session_invalid_token` | 💡 Oportunidad | Autenticación |
| 14 | `password_reset_requested` | 💡 Oportunidad | Autenticación |
| 15 | `password_reset_completed` | 💡 Oportunidad | Autenticación |
| 16 | `password_changed` | 💡 Oportunidad | Autenticación |
| 17 | `registration_completed` | 💡 Oportunidad | Usuarios |
| 18 | `supplier_list_viewed` | 💡 Oportunidad | Proveedores |
| 19 | `supplier_detail_viewed` | 💡 Oportunidad | Proveedores |
| 20 | `supplier_created` | 💡 Oportunidad | Proveedores |
| 21 | `supplier_rate_updated` | 💡 Oportunidad | Proveedores |
| 22 | `supplier_status_toggled` | 💡 Oportunidad | Proveedores |
| 23 | `supplier_deleted` | 💡 Oportunidad | Proveedores |
| 24 | `incident_list_viewed` | 💡 Oportunidad | Incidencias |
| 25 | `incident_summary_viewed` | 💡 Oportunidad | Incidencias |
| 26 | `incident_created` | 💡 Oportunidad | Incidencias |
| 27 | `incident_status_changed` | 💡 Oportunidad | Incidencias |
| 28 | `incident_detail_viewed` | 💡 Oportunidad | Incidencias |
| 29 | `lead_form_started` | 💡 Oportunidad | Leads (web) |
| 30 | `lead_form_field_error` | 💡 Oportunidad | Leads (web) |
| 31 | `lead_form_submitted` | 💡 Oportunidad | Leads (web) |
| 32 | `lead_form_abandoned` | 💡 Oportunidad | Leads (web) |
| 33 | `lead_volume_warning_shown` | 💡 Oportunidad | Leads (web) |
| 34 | `api_response_time_high` | 💡 Oportunidad | Rendimiento |
| 35 | `api_response_error_recorded` | 💡 Oportunidad | Rendimiento |
| 36 | `frontend_uncaught_error` | 💡 Oportunidad | Frontend |
| 37 | `page_load_time` | 💡 Oportunidad | Rendimiento |
| 38 | `cache_effectiveness_recorded` | 💡 Oportunidad | Rendimiento |
| 39 | `page_navigated` | 💡 Oportunidad | Navegación |
| 40 | `flow_abandoned_supplier_creation` | 💡 Oportunidad | Navegación |
| 41 | `flow_abandoned_incident_creation` | 💡 Oportunidad | Navegación |
| 42 | `backoffice_session_active` | 💡 Oportunidad | Navegación |
| 43 | `user_profile_updated` | 💡 Oportunidad | Usuarios |
| 44 | `user_account_deactivated` | 💡 Oportunidad | Usuarios |
| 45 | `user_role_changed` | 💡 Oportunidad | Usuarios |

---

## 4. Event Envelope estándar (Fase 2)

### 4.1 Campos comunes (obligatorios para todo evento)

| Campo | Tipo | Formato / Reglas |
|-------|------|-----------------|
| `eventId` | `string` | UUID v4. Generado en el emisor. |
| `timestamp` | `string` | ISO 8601 en UTC (ej. `2026-10-16T14:30:00.000Z`). Reloj del emisor. |
| `sessionId` | `string` | ID de sesión del frontend. Persiste en Session Storage. |
| `userId` | `string` | ID del usuario autenticado. `"anonymous"` si no hay sesión. |
| `event_type` | `string` | `dominio_entidad_accion` en pasado. |
| `schemaVersion` | `string` | Semver `x.y.z` del schema del `properties`. |
| `requestId` | `string` | Correlation ID de la request HTTP. |
| `properties` | `object` | Payload específico del evento (definido por `event_type`). |

### 4.2 Ejemplo completo

```json
{
  "eventId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "timestamp": "2026-10-16T14:30:00.000Z",
  "sessionId": "sess_abc123",
  "userId": "usr_456",
  "event_type": "inbound_order_created",
  "schemaVersion": "1.0.0",
  "requestId": "req_789",
  "properties": {
    "warehouse": "los_angeles",
    "client_id": "client_001",
    "product_id": "SKU-001",
    "product_category": "electronics",
    "quantity": 500,
    "reference": "PO-2026-001"
  }
}
```

### 4.3 Taxonomía de nombres

```
<domain>_<entity>_<action>
```

| Componente | Reglas | Ejemplos |
|-----------|--------|---------|
| `domain` | Preferiblemente una palabra | `inbound`, `outbound`, `stock`, `supplier`, `incident` |
| `entity` | Singular | `order`, `stock`, `token`, `lead` |
| `action` | **Verbo en pasado** | `created`, `triggered`, `failed`, `succeeded`, `submitted` |

**Valores permitidos para `action` (verbos en pasado):**

| Verbo | Uso |
|-------|-----|
| `created` | Alta de un recurso |
| `rejected` | Operación denegada por reglas de negocio |
| `triggered` | Alarma / umbral superado |
| `detected` | Detección automática (discrepancia, anomalía) |
| `succeeded` | Operación completada con éxito |
| `failed` | Operación que falló |
| `expired` | Recurso que caducó |
| `viewed` | Visualización de pantalla/detalle |
| `updated` | Actualización de un recurso |
| `recorded` | Medición técnica registrada |
| `navigated` | Cambio de ruta en SPA |
| `submitted` | Formulario enviado |
| `abandoned` | Flujo abandonado por el usuario |
| `toggled` | Cambio de estado binario |
| `changed` | Cambio de estado (incidencias) |

---

## 5. Eventos seleccionados — Schemas y allowlists (Fase 2)

### 5.1 Inventario (obligatorios + complementarios)

#### 5.1.1 `inbound_order_created`

| Elemento | Contenido |
|----------|-----------|
| **Disparo** | Se crea una orden de entrada. Se recibe mercancía de un cliente B2B. |
| **Hipótesis** | Necesitamos saber qué SKUs están entrando, en qué volumen y en qué almacén. |
| **Decisión** | Dimensionar capacidad de recepción por turno y almacén (Ana). |
| **Consumidor** | Ana (Operations) — operaciones diarias. |
| **Trigger** | `POST /api/inbound-orders` → `201 Created` → emitir evento. |

**Allowlist:**

| Propiedad | Tipo | Obligatorio | Descripción |
|-----------|------|-------------|-------------|
| `warehouse` | `string` | ✅ | `los_angeles` / `zaragoza` |
| `client_id` | `string` | ✅ | ID del cliente B2B |
| `product_id` | `string` | ✅ | SKU del producto |
| `product_category` | `string` | ✅ | `fashion` / `electronics` / `cosmetics` |
| `quantity` | `integer` | ✅ | Cantidad recibida (≥ 1) |
| `reference` | `string` | ✅ | Número de orden de compra del cliente |

#### 5.1.2 `outbound_order_created`

| Elemento | Contenido |
|----------|-----------|
| **Disparo** | Se crea una orden de salida (preparación de pedido para envío). |
| **Hipótesis** | Necesitamos saber qué SKUs están saliendo, en qué volumen y hacia dónde. |
| **Decisión** | Planificar picking y capacidad de packing por turno (Ana). |
| **Consumidor** | Ana (Operations) — operaciones diarias. |

**Allowlist:**

| Propiedad | Tipo | Obligatorio | Descripción |
|-----------|------|-------------|-------------|
| `warehouse` | `string` | ✅ | `los_angeles` / `zaragoza` |
| `client_id` | `string` | ✅ | ID del cliente B2B |
| `product_id` | `string` | ✅ | SKU del producto |
| `product_category` | `string` | ✅ | `fashion` / `electronics` / `cosmetics` |
| `quantity` | `integer` | ✅ | Cantidad solicitada (≥ 1) |
| `exit_type` | `string` | ✅ | `delivery` / `transfer` / `return` |

#### 5.1.3 `stock_threshold_triggered`

| Elemento | Contenido |
|----------|-----------|
| **Disparo** | El stock de un SKU cae por debajo del umbral configurado. |
| **Hipótesis** | Necesitamos saber qué SKUs están cerca de agotarse para reordenar a tiempo. |
| **Decisión** | Activar reposición automática o alertar al comprador (Ana). |
| **Consumidor** | Ana (Operations) — alertas de reposición. |

**Allowlist:**

| Propiedad | Tipo | Obligatorio | Descripción |
|-----------|------|-------------|-------------|
| `warehouse` | `string` | ✅ | `los_angeles` / `zaragoza` |
| `client_id` | `string` | ✅ | ID del cliente B2B |
| `product_id` | `string` | ✅ | SKU del producto |
| `product_category` | `string` | ✅ | `fashion` / `electronics` / `cosmetics` |
| `quantity` | `integer` | ✅ | Stock actual tras la operación (≥ 0) |
| `threshold_value` | `integer` | ✅ | Umbral configurado para ese SKU (≥ 1) |
| `current_stock` | `integer` | ✅ | Stock disponible en el momento (≥ 0) |

#### 5.1.4 `direct_stock_edit_rejected`

| Elemento | Contenido |
|----------|-----------|
| **Disparo** | Un usuario intenta modificar el stock directamente y el sistema lo rechaza. |
| **Hipótesis** | Necesitamos saber si el personal de almacén intenta saltarse el control de trazabilidad. |
| **Decisión** | Reforzar capacitación o permisos en el almacén donde ocurra con más frecuencia. |
| **Consumidor** | Ana (Operations), Andrés (CTO) — supervisión de integridad. |

> ℹ️ **Nota:** Este evento **no incluye `quantity`** porque describe un intento rechazado, no una transacción de cantidad real.

**Allowlist:**

| Propiedad | Tipo | Obligatorio | Descripción |
|-----------|------|-------------|-------------|
| `warehouse` | `string` | ✅ | `los_angeles` / `zaragoza` |
| `client_id` | `string` | ✅ | ID del cliente B2B |
| `product_id` | `string` | ✅ | SKU del producto |
| `product_category` | `string` | ✅ | `fashion` / `electronics` / `cosmetics` |
| `user_role` | `string` | ✅ | Rol del usuario que intentó la edición: `operator` / `supervisor` / `admin` |
| `attempt_type` | `string` | ✅ | Tipo de intento: `quantity_edit` / `stock_adjustment` |

#### 5.1.5 `inventory_discrepancy_detected`

| Elemento | Contenido |
|----------|-----------|
| **Disparo** | Un conteo físico o auditoría detecta diferencia entre stock del sistema y stock real. |
| **Hipótesis** | Necesitamos saber en qué SKUs y almacenes ocurren más discrepancias. |
| **Decisión** | Priorizar auditorías de inventario en los SKUs con mayor tasa de discrepancia (Ana). |
| **Consumidor** | Ana (Operations) — control de calidad. |

**Allowlist:**

| Propiedad | Tipo | Obligatorio | Descripción |
|-----------|------|-------------|-------------|
| `warehouse` | `string` | ✅ | `los_angeles` / `zaragoza` |
| `client_id` | `string` | ✅ | ID del cliente B2B |
| `product_id` | `string` | ✅ | SKU del producto |
| `product_category` | `string` | ✅ | `fashion` / `electronics` / `cosmetics` |
| `expected_quantity` | `integer` | ✅ | Stock esperado según sistema (≥ 0) |
| `actual_quantity` | `integer` | ✅ | Stock real tras conteo (≥ 0) |
| `variance` | `integer` | ✅ | Diferencia (puede ser negativa) |

#### 5.1.6 `outbound_order_insufficient_stock`

| Elemento | Contenido |
|----------|-----------|
| **Disparo** | Se intenta crear una orden de salida y no hay suficiente stock para uno o más SKUs. |
| **Hipótesis** | Necesitamos saber qué SKUs están causando roturas de stock en el proceso de picking. |
| **Decisión** | Ajustar umbrales de reorden o alertar a compras para acelerar reposición (Ana). |
| **Consumidor** | Ana (Operations) — excepciones de inventario. |

**Allowlist:**

| Propiedad | Tipo | Obligatorio | Descripción |
|-----------|------|-------------|-------------|
| `warehouse` | `string` | ✅ | `los_angeles` / `zaragoza` |
| `client_id` | `string` | ✅ | ID del cliente B2B |
| `product_id` | `string` | ✅ | SKU del producto |
| `product_category` | `string` | ✅ | `fashion` / `electronics` / `cosmetics` |
| `requested_quantity` | `integer` | ✅ | Cantidad solicitada (≥ 1) |
| `available_quantity` | `integer` | ✅ | Stock disponible (≥ 0) |
| `shortage` | `integer` | ✅ | Diferencia: `requested_quantity - available_quantity` (> 0) |

---

### 5.2 Autenticación

#### 5.2.1 `login_succeeded`

| Elemento | Contenido |
|----------|-----------|
| **Disparo** | Un usuario completa login con credenciales correctas. |
| **Hipótesis** | Necesitamos saber qué usuarios acceden al sistema y desde qué zona horaria. |
| **Decisión** | Identificar patrones de acceso anómalos (Andrés, CTO). |
| **Consumidor** | Andrés (CTO) — seguridad. |

**Allowlist:**

| Propiedad | Tipo | Obligatorio | Descripción |
|-----------|------|-------------|-------------|
| `user_id` | `string` | ✅ | ID del usuario |
| `user_role` | `string` | ✅ | `operator` / `supervisor` / `admin` / `commercial` / `support` / `viewer` |
| `ip_country` | `string` | ✅ | Código país ISO 3166-1 alpha-2 de la IP (solo país, no IP completa) |
| `auth_method` | `string` | ✅ | `password` / `sso` / `mfa` |

#### 5.2.2 `login_failed`

| Elemento | Contenido |
|----------|-----------|
| **Disparo** | Un intento de login falla por credenciales inválidas. |
| **Hipótesis** | Necesitamos saber con qué frecuencia fallan las credenciales y si hay patrones de fuerza bruta. |
| **Decisión** | Implementar rate-limiting por IP y notificar a Andrés ante picos anómalos. |
| **Consumidor** | Andrés (CTO) — seguridad. |

**Allowlist:**

| Propiedad | Tipo | Obligatorio | Descripción |
|-----------|------|-------------|-------------|
| `user_id` | `string` | ✅ | ID del usuario (si se proporcionó) |
| `email_hash` | `string` | ✅ | SHA-256 del email ingresado (nunca el email en claro) |
| `ip_country` | `string` | ✅ | Código país ISO 3166-1 alpha-2 de la IP (solo país) |
| `failure_reason` | `string` | ✅ | `invalid_credentials` / `account_locked` / `mfa_failed` / `expired_password` |

**🔐 Datos sensibles:** El email se hashea con SHA-256 antes de emitir el evento. La IP completa se descarta; solo se registra el país. Retención máxima: 90 días.

#### 5.2.3 `session_token_expired`

| Elemento | Contenido |
|----------|-----------|
| **Disparo** | El token de sesión de un usuario caduca (timeout de inactividad o expiración). |
| **Hipótesis** | Necesitamos saber con qué frecuencia caducan las sesiones. |
| **Decisión** | Ajustar política de expiración de sesión si hay muchas expiraciones prematuras. |
| **Consumidor** | Andrés (CTO) — seguridad/UX. |

**Allowlist:**

| Propiedad | Tipo | Obligatorio | Descripción |
|-----------|------|-------------|-------------|
| `user_id` | `string` | ✅ | ID del usuario |
| `user_role` | `string` | ✅ | `operator` / `supervisor` / `admin` / `commercial` / `support` / `viewer` |
| `session_duration_minutes` | `integer` | ✅ | Minutos desde inicio de sesión hasta expiración (≥ 0) |
| `expiry_reason` | `string` | ✅ | `inactivity_timeout` / `absolute_expiry` / `manual_logout` |

---

### 5.3 Proveedores (Suppliers)

#### 5.3.1 `supplier_created`

| Elemento | Contenido |
|----------|-----------|
| **Disparo** | Se da de alta un nuevo proveedor en el sistema. |
| **Hipótesis** | Necesitamos saber cuántos proveedores nuevos se incorporan y de qué tipo. |
| **Decisión** | Medir crecimiento del ecosistema de proveedores (Carlos). |
| **Consumidor** | Carlos (Suppliers). |

**Allowlist:**

| Propiedad | Tipo | Obligatorio | Descripción |
|-----------|------|-------------|-------------|
| `supplier_id` | `string` | ✅ | ID del nuevo proveedor |
| `supplier_type` | `string` | ✅ | `carrier` (transportista) / `warehouse` (almacén externo) / `consolidator` (consolidador) |
| `operating_warehouses` | `array` | ✅ | Almacenes donde operará: `["los_angeles"]` / `["zaragoza"]` / `["los_angeles","zaragoza"]` |

#### 5.3.2 `supplier_rate_updated`

| Elemento | Contenido |
|----------|-----------|
| **Disparo** | Se actualiza la tarifa de un proveedor (subida o bajada). |
| **Hipótesis** | Necesitamos saber con qué frecuencia cambian las tarifas y en qué dirección. |
| **Decisión** | Evaluar si un proveedor está volviéndose menos competitivo (Carlos). |
| **Consumidor** | Carlos (Suppliers) — reporting semanal. |

**Allowlist:**

| Propiedad | Tipo | Obligatorio | Descripción |
|-----------|------|-------------|-------------|
| `supplier_id` | `string` | ✅ | ID del proveedor |
| `rate_type` | `string` | ✅ | `pallet_storage` / `pick_pack` / `transport_km` / `monthly_fixed` |
| `old_rate` | `number` | ✅ | Tarifa anterior (≥ 0) |
| `new_rate` | `number` | ✅ | Tarifa nueva (≥ 0) |
| `currency` | `string` | ✅ | `USD` / `EUR` |

#### 5.3.3 `supplier_status_toggled`

| Elemento | Contenido |
|----------|-----------|
| **Disparo** | Un proveedor es activado o desactivado. |
| **Hipótesis** | Necesitamos saber si hay proveedores que se desactivan con frecuencia. |
| **Decisión** | Investigar inestabilidad operativa o problemas de pago (Carlos). |
| **Consumidor** | Carlos (Suppliers) — alertas. |

**Allowlist:**

| Propiedad | Tipo | Obligatorio | Descripción |
|-----------|------|-------------|-------------|
| `supplier_id` | `string` | ✅ | ID del proveedor |
| `new_status` | `string` | ✅ | `active` / `inactive` |
| `reason` | `string` | ✅ | `operational_issues` / `contract_expired` / `voluntary_pause` / `other` |

---

### 5.4 Incidencias

#### 5.4.1 `incident_created`

| Elemento | Contenido |
|----------|-----------|
| **Disparo** | Se registra una nueva incidencia (queja de cliente, daño, retraso, etc.). |
| **Hipótesis** | Necesitamos saber qué tipos de incidencias ocurren y en qué almacén/tipo de envío. |
| **Decisión** | Identificar causas raíz y asignar recursos a las áreas con más incidencias (Valentina). |
| **Consumidor** | Valentina (Incidents) — operaciones diarias. |

**Allowlist:**

| Propiedad | Tipo | Obligatorio | Descripción |
|-----------|------|-------------|-------------|
| `incident_id` | `string` | ✅ | ID de la incidencia |
| `incident_type` | `string` | ✅ | `damage` / `delay` / `lost` / `wrong_item` / `customer_complaint` / `system_issue` |
| `severity` | `string` | ✅ | `low` / `medium` / `high` / `critical` |
| `warehouse` | `string` | ✅ | `los_angeles` / `zaragoza` |
| `order_type` | `string` | ✅ | `inbound` / `outbound` |
| `client_id` | `string` | ✅ | ID del cliente B2B afectado (si aplica) |

#### 5.4.2 `incident_status_changed`

| Elemento | Contenido |
|----------|-----------|
| **Disparo** | Una incidencia cambia de estado (en revisión, resuelta, cerrada, etc.). |
| **Hipótesis** | Necesitamos saber cuánto tiempo pasa en cada estado. |
| **Decisión** | Detectar cuellos de botella en la resolución (Valentina). |
| **Consumidor** | Valentina (Incidents) — seguimiento de resolución. |

**Allowlist:**

| Propiedad | Tipo | Obligatorio | Descripción |
|-----------|------|-------------|-------------|
| `incident_id` | `string` | ✅ | ID de la incidencia |
| `previous_status` | `string` | ✅ | `open` / `investigating` / `resolved` / `closed` |
| `new_status` | `string` | ✅ | `open` / `investigating` / `resolved` / `closed` |
| `assigned_to` | `string` | ✅ | ID del usuario asignado (o `"unassigned"`) |

---

### 5.5 Rendimiento y navegación

#### 5.5.1 `api_response_error_recorded`

| Elemento | Contenido |
|----------|-----------|
| **Disparo** | La API devuelve un error 4xx o 5xx. |
| **Hipótesis** | Necesitamos saber con qué frecuencia la API devuelve errores por endpoint. |
| **Decisión** | Priorizar corrección de los endpoints con mayor tasa de error (Andrés). |
| **Consumidor** | Andrés (CTO) — alertas (5xx) y reporting semanal (4xx). |
| **Canal bifurcado:** | 5xx → **stream** (alerta inmediata). 4xx → **batch diario** (reporting analítico). |

**Allowlist:**

| Propiedad | Tipo | Obligatorio | Descripción |
|-----------|------|-------------|-------------|
| `user_id` | `string` | ✅ | ID del usuario (o `"anonymous"`) |
| `http_method` | `string` | ✅ | `GET` / `POST` / `PUT` / `PATCH` / `DELETE` |
| `endpoint` | `string` | ✅ | Ruta del endpoint (ej. `/api/incidents`, `/api/suppliers`) |
| `http_status_code` | `integer` | ✅ | Código de error HTTP (400–599) |
| `error_message` | `string` | ✅ | Mensaje de error corto. Sin stack traces. |

#### 5.5.2 `cache_effectiveness_recorded`

| Elemento | Contenido |
|----------|-----------|
| **Disparo** | Consulta a un endpoint cacheado de incidencias o proveedores. Se registra hit/miss. |
| **Hipótesis** | Necesitamos saber la tasa de acierto de la caché. |
| **Decisión** | Ajustar TTL o estrategias de invalidación (Andrés). |
| **Consumidor** | Andrés (CTO) — reporting semanal. |
| **Muestreo:** | 1:100 (1 de cada 100 consultas se registra). |

**Allowlist:**

| Propiedad | Tipo | Obligatorio | Descripción |
|-----------|------|-------------|-------------|
| `user_id` | `string` | ✅ | ID del usuario (o `"anonymous"`) |
| `endpoint` | `string` | ✅ | Ruta del endpoint cacheado |
| `cache_hit` | `boolean` | ✅ | `true` si fue hit, `false` si fue miss |
| `response_time_ms` | `integer` | ✅ | Tiempo de respuesta en milisegundos (≥ 0) |

#### 5.5.3 `page_navigated`

| Elemento | Contenido |
|----------|-----------|
| **Disparo** | El usuario navega a una página del backoffice (SPA). |
| **Hipótesis** | Necesitamos saber qué pantallas son más visitadas y en qué orden. |
| **Decisión** | Optimizar las pantallas más usadas y detectar abandono temprano (UX). |
| **Consumidor** | Thomas (Executive) — reporting semanal. |
| **Debounce:** | 5 segundos por usuario y página. |

**Allowlist:**

| Propiedad | Tipo | Obligatorio | Descripción |
|-----------|------|-------------|-------------|
| `user_id` | `string` | ✅ | ID del usuario |
| `user_role` | `string` | ✅ | `operator` / `supervisor` / `admin` / `commercial` / `support` / `viewer` |
| `page_name` | `string` | ✅ | Solo nombre lógico: `inventory` / `suppliers` / `incidents` / `dashboard` / `reports` |
| `referrer_page` | `string` | ✅ | Página de origen (o `"direct"` si es entrada directa) |

---

### 5.6 Leads (web)

#### 5.6.1 `lead_form_submitted`

| Elemento | Contenido |
|----------|-----------|
| **Disparo** | Un visitante web envía el formulario de solicitud de información. |
| **Hipótesis** | Necesitamos saber cuántos leads cualificados genera la web. |
| **Decisión** | Evaluar ROI de campañas de marketing y ajustar capacidad comercial (Miguel). |
| **Consumidor** | Miguel (Commercial) — leads comerciales. |

**Allowlist:**

| Propiedad | Tipo | Obligatorio | Descripción |
|-----------|------|-------------|-------------|
| `product_type` | `string` | ✅ | `fashion` / `electronics` / `cosmetics` / `food` / `other` |
| `monthly_volume` | `string` | ✅ | `0-100` / `101-500` / `501-2000` / `2000+` / `not-sure` |
| `operating_country` | `string` | ✅ | `us` / `es` / `both` / `other` |
| `has_3pl` | `string` | ✅ | `yes` / `no` / `evaluating` |
| `services_interest` | `array` | ✅ | `["warehousing"]` / `["last-mile"]` / `["reverse-logistics"]` / combinaciones |

> **🔐 Privacidad:** No se capturan datos personales del lead. Solo perfil de negocio agregable.

---

## 6. Estrategia de entrega (Fase 3)

### 6.1 Criterio de clasificación

Cada evento se evalúa con tres preguntas:

1. **Urgencia de la decisión:** ¿La decisión que habilita este evento es inmediata o diferible?
2. **Daño si retraso 1 hora:** ¿Qué consecuencias tiene que el evento llegue 1 hora tarde?
3. **Daño si retraso 1 turno:** ¿Qué consecuencias tiene que el evento llegue 1 día tarde?

**Veredicto:** Si el daño con 1 hora de retraso es crítico → **stream**. Si se puede esperar un turno → **batch**.

### 6.2 Clasificación completa

#### Stream (8 eventos puros + 2 alertas + 2 debounced = 12)

| Evento | Justificación | Canal |
|--------|---------------|-------|
| `inbound_order_created` | Ana necesita saber en tiempo real para dimensionar recepción | Stream |
| `outbound_order_created` | Planificar picking inmediato | Stream |
| `stock_threshold_triggered` | Alertar reposición antes de rotura de stock | **Alerta** (stream) |
| `outbound_order_insufficient_stock` | Rechazar la orden y notificar al operador inmediatamente | **Alerta** (stream) |
| `login_failed` | Rate-limiting en segundos. Ventana de ataque muy corta | Stream |
| `supplier_status_toggled` | Desactivación puede bloquear envíos en curso | Stream |
| `incident_created` | Valentina necesita visibilidad inmediata de nuevas incidencias | Stream |
| `incident_status_changed` | Seguimiento de resolución en tiempo real | Stream |
| `api_response_error_recorded` (5xx) | Error de servidor requiere atención inmediata | Stream |
| `lead_form_submitted` | Miguel necesita contactar al lead mientras está caliente | Stream |
| `page_navigated` | Muchos eventos por usuario. Se debounce (5s) | **Debounced** → stream |
| `api_response_error_recorded` (4xx) | Alto volumen. Se throttle (10/min por endpoint+code) | Stream con throttle |

#### Batch (8 diarios + 1 semanal = 9)

| Evento | Frecuencia | Justificación |
|--------|-----------|---------------|
| `direct_stock_edit_rejected` | Diario | La decisión es formativa/analítica. No urgente. |
| `inventory_discrepancy_detected` | Diario | Se audita por lotes al cierre del turno. |
| `login_succeeded` | Diario | El análisis de accesos es retrospectivo. |
| `session_token_expired` | Diario | Ajuste de políticas es decisión semanal. |
| `supplier_created` | Diario | Reporting de crecimiento. Sin urgencia operativa. |
| `supplier_rate_updated` | **Semanal** | La evaluación de competitividad es estratégica. |
| `cache_effectiveness_recorded` | Diario | Muestreado 1:100. Análisis semanal de rendimiento. |
| `page_navigated` | Diario | Datos agregados de navegación. |
| `api_response_error_recorded` (4xx) | Diario | Análisis de tendencias, no alertas. |

### 6.3 Resumen stream vs batch

| Canal | Cantidad | Eventos |
|-------|----------|---------|
| 📡 **Stream** | 12 | `inbound_order_created`, `outbound_order_created`, `stock_threshold_triggered`, `outbound_order_insufficient_stock`, `login_failed`, `supplier_status_toggled`, `incident_created`, `incident_status_changed`, `lead_form_submitted`, `page_navigated` (debounced), `api_response_error_recorded` (5xx → stream, 4xx → batch) |
| 📦 **Batch** | 9 | `direct_stock_edit_rejected`, `inventory_discrepancy_detected`, `login_succeeded`, `session_token_expired`, `supplier_created`, `supplier_rate_updated` (semanal), `cache_effectiveness_recorded`, `page_navigated` (agregado diario), `api_response_error_recorded` (4xx) |

### 6.4 Reglas de throttle y debounce

| Evento | Estrategia | Configuración |
|--------|-----------|---------------|
| `page_navigated` | Debounce | 5 segundos por usuario y `page_name`. Si el usuario navega a la misma página repetidamente, solo se emite el último evento tras 5s sin navegación a esa página. |
| `api_response_error_recorded` | Throttle | Máximo 10 eventos por minuto por combinación `(endpoint, http_status_code)`. El exceso se descarta silenciosamente. |
| `login_failed` | Throttle | Máximo 1 evento cada 5 segundos por `ip_country`. El exceso se descarta para evitar sobrecarga por fuerza bruta. |
| `cache_effectiveness_recorded` | Sampling | 1 de cada 100 consultas se registra (1:100). Aleatorio basado en requestId hash modulo 100. |

### 6.5 Back-pressure

El evento `outbound_order_created` se regula naturalmente porque cada evento corresponde a una orden real. Si el sistema no da abasto, las órdenes simplemente esperan en la cola del sincronizador. No necesita throttle artificial.

---

## 7. Guía de sanitización de datos sensibles

### 7.1 Tabla de datos sensibles

| Dato | Eventos afectados | Tratamiento |
|------|------------------|-------------|
| Email del consumidor final | Todos los de inventario | **Excluido por diseño.** No pertenece al dominio de inventario. |
| Email contacto B2B | `supplier_*` | **Excluido.** No necesario para telemetría agregada. |
| Email del usuario | `login_failed` | **SHA-256 hashing.** Nunca en claro. Retención 90 días máx. |
| IP completa | `login_succeeded`, `login_failed` | **Truncada a país.** Solo código ISO 3166-1 alpha-2. |
| Nombre del consumidor final | Todos los de inventario | **Excluido por diseño.** |
| Dirección del consumidor final | `outbound_order_created` | **Excluida.** No necesaria para telemetría. |
| Teléfono del consumidor | `outbound_order_*` | **Excluido.** |
| Descripción/título de la incidencia | `incident_created` | **Excluido.** Puede contener PII del cliente. |
| Comentarios del lead | `lead_form_submitted` | **Excluidos.** Solo se capturan campos estructurados del perfil de negocio. |
| Contact email del proveedor | `supplier_*` | **Excluido.** No necesario para telemetría. |
| Stack traces | `api_response_error_recorded` | **Excluidos.** `error_message` solo contiene mensaje corto. |

### 7.2 Reglas de oro

1. **Todo evento en properties lleva `additionalProperties: false`** — no se pueden añadir campos no autorizados.
2. **Los datos personales del consumidor final nunca salen del almacén** — ni siquiera hasheados.
3. **Si no sabes si un campo contiene PII, no lo incluyas.**
4. **La retención máxima de eventos con PII es 90 días** — para `login_failed`.

---

## 8. Riesgos y exclusiones

### 8.1 Eventos descartados (con razón)

| Evento descartado | Motivo |
|-------------------|--------|
| `outbound_order_dispatched` | Redundante con `outbound_order_created`. Mismo momento de disparo. |
| `inventory_sku_list_viewed` | No genera una decisión de negocio medible. Ruido. |
| `outbound_order_cancelled` | Pertenece al dominio de última milla, fuera del alcance. |
| `login_attempt` | Reemplazado por `login_succeeded` + `login_failed`. |
| `password_changed` | Fuera del alcance. Plan de seguridad separado. |
| `password_reset_requested` | Incluye PII (email). Fuera de alcance de telemetría. |
| `user_registered` | No existe registro de usuarios en TrackFlow. |
| `user_profile_updated` | PII + bajo valor analítico. |
| `supplier_contact_updated` | PII del contacto comercial. |
| `incident_resolution_time_recorded` | Es dato derivado (calculado), no evento primario. |
| `frontend_error_occurred` | Sin RUM implementado. Pospuesto. |
| `api_latency_recorded` | Requiere acuerdo sobre percentiles y muestreo. Pospuesto. |
| `inventory_stock_snapshot` | Masivo (miles de SKUs). Mejor event sourcing. |
| `email_opened` / `email_clicked` | Sistema externo de marketing. |

### 8.2 Exclusiones por privacidad (9 tipos de datos)

1. Email del consumidor final
2. Email de contacto B2B en proveedores
3. IP completa (solo país)
4. Nombre del consumidor final
5. Dirección del consumidor final
6. Teléfono del consumidor final
7. Descripción/título de incidencias
8. Comentarios de leads
9. Stack traces de errores

### 8.3 Exclusiones por costo / esfuerzo (5 dominios)

1. **Última milla** — Es otro proyecto independiente con su propio plan de telemetría.
2. **RUM (Real User Monitoring)** — No implementado. Coste alto de instrumentación en frontend.
3. **Infraestructura gestionada** — (CloudFront, RDS, etc.) Sin acceso a métricas internas.
4. **Logs completos** — Volumen 100–1000x mayor que eventos de telemetría.
5. **APIs externas** — Clima, geocodificación. Variabilidad no controlada por TrackFlow.

### 8.4 Riesgos activos con mitigación

| Riesgo | Probabilidad | Impacto | Mitigación |
|--------|-------------|---------|------------|
| Fuga de PII en evento no revisado | Baja | Crítico | Code review + validación contra schema + `additionalProperties: false`. |
| Saturación de eventos (Black Friday) | Media | Alto | Throttle en `api_response_error_recorded`, sampling en `cache_effectiveness_recorded`. |
| Evento sin schema por error de código | Media | Medio | Validación contra event-schemas.json en CI/CD. |
| Deriva semántica de propiedades | Alta | Medio | `schemaVersion` en cada evento. Versionado de schemas. |
| Dependencia síncrona del sistema de telemetría | Baja | Alto | Producción no depende de disponibilidad de telemetría (emisión asíncrona). |
| Duplicados de eventos | Media | Bajo | `eventId` UUID + desduplicación en consumidor por `eventId` dentro de ventana de 5 min. |
| Olvido de instrumentación en nuevo desarrollo | Alta | Medio | Template de onboarding que incluye añadir schema y evento en este plan. |

---

## 9. Auditoría y veredicto

### 9.1 Resumen de auditoría

| Dimensión | Puntuación |
|-----------|-----------|
| Completitud (vs. CONTEXT-TELE.md) | ✅ 10/10 |
| Amplitud (técnica + negocio) | ✅ 9/10 |
| Justificación (hipótesis + decisión) | ✅ 10/10 |
| Consistencia entre documentos | ⚠️ 7/10 (corregido) |
| Precisión técnica (schemas, tipos) | ✅ 9/10 |
| Privacidad y PII | ✅ 9/10 |
| Entrega (stream/batch) | ✅ 10/10 |
| Riesgos y exclusiones | ✅ 10/10 |
| **Global** | **✅ 9.25/10** |

### 9.2 Discrepancias encontradas y corregidas

| # | Incidencia | Estado |
|---|-----------|--------|
| 1 | 4 nombres en FASE1 no coincidían con FASE2 | ✅ Corregido |
| 2 | Falta de documentación sobre `quantity` en `direct_stock_edit_rejected` | ✅ Corregido |
| 3 | `contains_pii: true` semánticamente incorrecto en `lead_form_submitted` | ✅ Corregido |
| 4 | Falta nota de normalización de nombres en FASE2 | ✅ Corregido |

---

## 10. Schemas de validación

El archivo **`event-schemas.json`** (en este mismo directorio) contiene las 19 definiciones JSON Schema (draft-07):

- 1 definición del **Event Envelope** estándar
- 18 definiciones de eventos individuales (uno por `event_type` seleccionado)

Cada definición incluye:
- `type: "object"` + `required` + `properties`
- `additionalProperties: false`
- Tipos concretos, enumeraciones, rangos
- `description` con hipótesis y decisión
- Bloque `sensitive` con metadatos de privacidad

**Uso recomendado:** Validar cada evento contra su schema antes de emitirlo, ya sea en tests unitarios o como middleware en el pipeline de emisión.

```python
# Ejemplo de validación (Python)
import json
from jsonschema import validate

with open("docs/telemetry/event-schemas.json") as f:
    schemas = json.load(f)

event = { "eventId": "...", "event_type": "inbound_order_created", ... }
envelope_schema = schemas["$defs"]["EventEnvelope"]
event_schema = schemas["$defs"][event["event_type"]]

validate(event, envelope_schema)  # valida envelope
validate(event["properties"], event_schema)  # valida payload
```

---

*Documento consolidado del Plan de Telemetría de TrackFlow. Para la versión detallada de cada fase, consultar los archivos FASE1-CATALOGO-DATOS.md, FASE2-DISENO-EVENT-ENVELOPE.md, y FASE3-ESTRATEGIA-ENTREGA.md en la raíz del proyecto.*