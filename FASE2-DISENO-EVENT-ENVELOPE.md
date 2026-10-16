# Fase 2 — Diseño del Event Envelope

## TrackFlow · Plan de Telemetría

> **Empresa:** TrackFlow — Logística de almacén y última milla (Los Ángeles + Zaragoza)
> **Unidad:** TrackFlow Tech
> **Fecha:** 2026-10-16

> ℹ️ **Nota sobre normalización de nombres:** Los eventos listados en este documento son la versión autoritativa. Algunos nombres fueron refinados respecto al catálogo de oportunidad (FASE1) para cumplir la taxonomía de verbos en pasado (p.ej., `login_success` → `login_succeeded`, `api_response_error` → `api_response_error_recorded`). Consultar FASE1 para el contexto de descubrimiento original; usar FASE2 y event-schemas.json para instrumentación.

---

## Índice

1. [Event Envelope estándar](#1-event-envelope-estándar)
2. [Taxonomía de nombres](#2-taxonomía-de-nombres)
3. [Catálogo de eventos: inventario (obligatorios)](#3-inventario-obligatorios)
4. [Catálogo de eventos: autenticación](#4-autenticación)
5. [Catálogo de eventos: proveedores (suppliers)](#5-proveedores)
6. [Catálogo de eventos: incidencias](#6-incidencias)
7. [Catálogo de eventos: rendimiento y navegación](#7-rendimiento-y-navegación)
8. [Guía de sanitización de datos sensibles](#8-guía-de-sanitización)
9. [Exportación JSON Schema](#9-exportación-json-schema)

---

## 1. Event Envelope estándar

Todo evento emitido por TrackFlow debe cumplir con el siguiente envelope mínimo. Estos campos son **obligatorios** y comunes a todos los `event_type`.

### 1.1 Envelope (campos comunes)

| Campo | Tipo | Obligatorio | Descripción |
|-------|------|-------------|-------------|
| `eventId` | `string (UUID v4)` | ✅ | Identificador único del evento. Generado en el punto de emisión. |
| `timestamp` | `string (ISO 8601 UTC)` | ✅ | Momento exacto en que ocurrió el evento. Formato: `2026-10-16T14:30:00.000Z` |
| `sessionId` | `string` | ✅ | Identificador de la sesión del usuario (JWT session o session token). Permite correlacionar eventos dentro de un mismo inicio de sesión. |
| `userId` | `string` | ✅ | Identificador del usuario autenticado que generó el evento. Para eventos no autenticados (ej. login_attempt), usar `"anonymous"`. |
| `event_type` | `string` | ✅ | Nombre del evento según la taxonomía `entidad_acción`. |
| `schemaVersion` | `string` | ✅ | Versión del esquema de `properties`. Formato semver: `"1.0.0"`. Permite evolucionar esquemas sin romper consumidores. |
| `requestId` | `string` | ✅ | ID de correlación de la petición HTTP (trace ID). Permite seguir un request a través de API gateway, servicio y base de datos. |
| `properties` | `object` | ✅ | Payload específico del evento. Cada event_type define su **allowlist** exacta de claves permitidas. |

### 1.2 Ejemplo de envelope completo

```json
{
  "eventId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "timestamp": "2026-10-16T14:30:00.000Z",
  "sessionId": "sess_8f7a3b2c1d0e",
  "userId": "usr_00a1b2c3-d4e5-6789-abcd-ef0123456789",
  "event_type": "inbound_order_created",
  "schemaVersion": "1.0.0",
  "requestId": "req_abc123def456",
  "properties": {
    "warehouse": "los_angeles",
    "client_id": "clt_purestep",
    "product_id": "CLT-SNK-W-42",
    "product_category": "fashion",
    "quantity": 40
  }
}
```

---

## 2. Taxonomía de nombres

Todos los `event_type` siguen la convención `{entidad}_{acción}` en **inglés** con verbos en **pasado** (eventos ya ocurridos).

### Verbos aceptados

| Verbo | Uso |
|-------|-----|
| `created` | Recurso creado (orden, proveedor, incidencia, usuario, lead) |
| `rejected` | Operación rechazada por validación de negocio |
| `triggered` | Alarma o umbral que se activa automáticamente |
| `detected` | Condición detectada por un proceso batch o auditoría |
| `attempted` | Intento de acción (login) |
| `succeeded` | Intento exitoso (login) |
| `failed` | Intento fallido (login) |
| `expired` | Recurso cuya validez terminó (sesión, token) |
| `viewed` | Lectura de listado o detalle |
| `updated` | Modificación de un recurso existente (tarifa, estado, perfil) |
| `recorded` | Medición técnica (latencia, error) |
| `navigated` | Cambio de página en el frontend |
| `abandoned` | Formulario iniciado pero no completado |
| `submitted` | Formulario completado y enviado |

### Dominios y prefijos

| Dominio | Prefijo `event_type` |
|---------|----------------------|
| Inventario | `inbound_order_*`, `outbound_order_*`, `stock_*`, `direct_stock_*`, `inventory_*` |
| Autenticación | `login_*`, `session_*`, `password_*`, `registration_*` |
| Proveedores | `supplier_*` |
| Incidencias | `incident_*` |
| Leads / Web | `lead_form_*` |
| Rendimiento | `api_*`, `frontend_*`, `page_*`, `cache_*` |
| Navegación | `page_*`, `flow_*`, `backoffice_*` |
| Usuarios | `user_profile_*`, `user_role_*`, `user_*` |

---

## 3. Inventario (obligatorios)

### 3.1 `inbound_order_created`

| Aspecto | Detalle |
|---------|---------|
| **Descripción** | Un almacén registra la recepción de mercancía de un cliente. Se emite tras crear un `StockEntry` vía `POST /inventory/orders/inbound`. |
| **Dominio** | negocio/inventario |
| **Clasificación** | ✅ Obligatorio (CONTEXT-TELE.md) |
| **Disparo en código** | `services/user-api/app/routers/inventory.py` — `create_inbound_order()` |

**Allowlist de `properties`:**

| Propiedad | Tipo | Obligatorio | Descripción |
|-----------|------|-------------|-------------|
| `warehouse` | `string` (enum) | ✅ | Almacén donde se recibe: `"los_angeles"` \| `"zaragoza"` |
| `client_id` | `string` | ✅ | Identificador del cliente B2B dueño del SKU |
| `product_id` | `string` | ✅ | Código SKU del producto (ej. `"CLT-SNK-W-42"`) |
| `product_category` | `string` (enum) | ✅ | Categoría del producto: `"fashion"` \| `"electronics"` \| `"cosmetics"` |
| `quantity` | `integer` (>0) | ✅ | Cantidad de unidades recibidas |
| `reference` | `string` | ✅ | Referencia de la orden de entrada (PO number o GR number) |

**Datos sensibles:** Ninguno. No incluye datos del transportista ni del destinatario final.

---

### 3.2 `outbound_order_created`

| Aspecto | Detalle |
|---------|---------|
| **Descripción** | Un almacén completa el picking y despacho de un pedido. Se emite tras crear un `StockExit` vía `POST /inventory/orders/outbound`. |
| **Dominio** | negocio/inventario |
| **Clasificación** | ✅ Obligatorio (CONTEXT-TELE.md) |
| **Disparo en código** | `services/user-api/app/routers/inventory.py` — `create_outbound_order()` |

**Allowlist de `properties`:**

| Propiedad | Tipo | Obligatorio | Descripción |
|-----------|------|-------------|-------------|
| `warehouse` | `string` (enum) | ✅ | Almacén de salida: `"los_angeles"` \| `"zaragoza"` |
| `client_id` | `string` | ✅ | Identificador del cliente B2B dueño del SKU |
| `product_id` | `string` | ✅ | Código SKU del producto |
| `product_category` | `string` (enum) | ✅ | Categoría del producto |
| `quantity` | `integer` (>0) | ✅ | Cantidad de unidades despachadas |
| `exit_type` | `string` (enum) | ✅ | Tipo de salida: `"dispatch"` (despacho normal) \| `"loss"` (merma) |

**Datos sensibles:** Ninguno. No incluye datos del transportista ni del destinatario final.

---

### 3.3 `stock_threshold_triggered`

| Aspecto | Detalle |
|---------|---------|
| **Descripción** | El stock de un SKU cae por debajo del mínimo configurado para ese cliente. Se evalúa tras cada `outbound_order_created`. |
| **Dominio** | negocio/inventario |
| **Clasificación** | ✅ Obligatorio (CONTEXT-TELE.md) |

**Allowlist de `properties`:**

| Propiedad | Tipo | Obligatorio | Descripción |
|-----------|------|-------------|-------------|
| `warehouse` | `string` (enum) | ✅ | Almacén donde se detectó el umbral |
| `client_id` | `string` | ✅ | Identificador del cliente |
| `product_id` | `string` | ✅ | SKU que cayó por debajo del umbral |
| `product_category` | `string` (enum) | ✅ | Categoría del producto |
| `quantity` | `integer` (≥0) | ✅ | Cantidad de unidades en el momento de la alerta (stock restante) |
| `threshold_value` | `integer` (≥0) | ✅ | Valor del umbral mínimo configurado para ese cliente/SKU |
| `current_stock` | `integer` (≥0) | ✅ | Stock disponible tras la operación que disparó el umbral |

**Datos sensibles:** Ninguno.

---

### 3.4 `direct_stock_edit_rejected`

| Aspecto | Detalle |
|---------|---------|
| **Descripción** | Un usuario intenta modificar el stock directamente (fuera de una orden de entrada o salida) y el sistema lo rechaza. |
| **Dominio** | negocio/inventario |
| **Clasificación** | ✅ Obligatorio (CONTEXT-TELE.md) |

**Allowlist de `properties`:**

| Propiedad | Tipo | Obligatorio | Descripción |
|-----------|------|-------------|-------------|
| `warehouse` | `string` (enum) | ✅ | Almacén sobre el que se intentó la operación |
| `client_id` | `string` | ✅ | Cliente asociado al SKU objetivo |
| `product_id` | `string` | ✅ | SKU que se intentó modificar |
| `product_category` | `string` (enum) | ✅ | Categoría del producto |
| `user_role` | `string` | ✅ | Rol del usuario que hizo el intento (`"admin"`, `"manager"`, `"user"`) |
| `attempt_type` | `string` | ✅ | Tipo de intento: `"direct_api_call"` \| `"invalid_endpoint"` \| `"unauthorized_method"` |

**Datos sensibles:** Ninguno. No se registra el email del usuario, solo su rol.

---

### 3.5 `inventory_discrepancy_detected`

| Aspecto | Detalle |
|---------|---------|
| **Descripción** | Un conteo físico o una auditoría detecta una diferencia entre el stock registrado en el sistema y el stock real en el almacén. |
| **Dominio** | negocio/inventario |
| **Clasificación** | ✅ Obligatorio (CONTEXT-TELE.md) |

**Allowlist de `properties`:**

| Propiedad | Tipo | Obligatorio | Descripción |
|-----------|------|-------------|-------------|
| `warehouse` | `string` (enum) | ✅ | Almacén donde se detectó la discrepancia |
| `client_id` | `string` | ✅ | Cliente asociado al SKU discrepante |
| `product_id` | `string` | ✅ | SKU con discrepancia |
| `product_category` | `string` (enum) | ✅ | Categoría del producto |
| `expected_quantity` | `integer` (≥0) | ✅ | Stock que el sistema tenía registrado |
| `actual_quantity` | `integer` (≥0) | ✅ | Stock real según conteo físico |
| `variance` | `integer` | ✅ | Diferencia: `actual_quantity - expected_quantity`. Negativo si falta stock, positivo si sobra. |

**Datos sensibles:** Ninguno.

---

### 3.6 `outbound_order_insufficient_stock`

| Aspecto | Detalle |
|---------|---------|
| **Descripción** | Una orden de salida es rechazada porque la cantidad solicitada supera el stock disponible. |
| **Dominio** | negocio/inventario |
| **Clasificación** | ✅ Complementario (derivado de `outbound_order_created`) |

**Allowlist de `properties`:**

| Propiedad | Tipo | Obligatorio | Descripción |
|-----------|------|-------------|-------------|
| `warehouse` | `string` (enum) | ✅ | Almacén donde se detectó la falta de stock |
| `product_id` | `string` | ✅ | SKU que no pudo despacharse |
| `product_category` | `string` (enum) | ✅ | Categoría del producto |
| `client_id` | `string` | ✅ | Identificador del cliente |
| `requested_quantity` | `integer` (≥1) | ✅ | Cantidad que se intentó despachar |
| `available_stock` | `integer` (≥0) | ✅ | Stock disponible en el momento del intento |

**Datos sensibles:** Ninguno.

---

## 4. Autenticación

### 4.1 `login_succeeded`

| Aspecto | Detalle |
|---------|---------|
| **Descripción** | Un usuario inicia sesión correctamente. Se emite tras `POST /auth/login` con respuesta 200. |
| **Dominio** | autenticación |
| **Clasificación** | 💡 Oportunidad identificada |

**Allowlist de `properties`:**

| Propiedad | Tipo | Obligatorio | Descripción |
|-----------|------|-------------|-------------|
| `user_id` | `string` | ✅ | UUID del usuario autenticado. No PII por sí mismo. |
| `user_role` | `string` (enum) | ✅ | Rol del usuario: `"admin"` \| `"manager"` \| `"user"` |
| `ip_country` | `string` | ✅ | País de origen de la IP (código ISO 3166-1 alpha-2, ej. `"US"`, `"ES"`). No es IP exacta. |
| `auth_method` | `string` | ✅ | Método de autenticación: `"password"` \| `"token"` |

**Datos sensibles:** No contiene PII. La IP se reduce solo a país (`ip_country`). No se almacena la IP completa ni el email.

---

### 4.2 `login_failed`

| Aspecto | Detalle |
|---------|---------|
| **Descripción** | Un intento de inicio de sesión falla por credenciales inválidas o usuario inactivo. |
| **Dominio** | autenticación |
| **Clasificación** | 💡 Oportunidad identificada |

**Allowlist de `properties`:**

| Propiedad | Tipo | Obligatorio | Descripción |
|-----------|------|-------------|-------------|
| `email_hash` | `string` | ✅ | SHA-256 del email intentado. Nunca el email en texto plano. |
| `failure_reason` | `string` (enum) | ✅ | Motivo del fallo: `"invalid_credentials"` \| `"inactive_user"` |
| `ip_country` | `string` | ✅ | País de origen (código ISO 3166-1 alpha-2) |
| `attempt_count_last_5min` | `integer` (≥0) | ✅ | Número de intentos fallidos desde esa IP en los últimos 5 minutos |

> ⚠️ **Sanitización:** El email se hashea con SHA-256 antes de emitirse. La IP se reduce a país. Nunca se incluye la IP completa ni el email en texto plano.

---

### 4.3 `session_token_expired`

| Aspecto | Detalle |
|---------|---------|
| **Descripción** | Un intento de acceso a un endpoint protegido falla porque el token JWT ha expirado. |
| **Dominio** | autenticación |
| **Clasificación** | 💡 Oportunidad identificada |

**Allowlist de `properties`:**

| Propiedad | Tipo | Obligatorio | Descripción |
|-----------|------|-------------|-------------|
| `user_id` | `string` | ✅ | UUID del usuario, extraído del token expirado si fue posible decodificarlo |
| `endpoint` | `string` | ✅ | Ruta del endpoint al que se intentaba acceder (ej. `"/suppliers"`) |
| `time_since_login_minutes` | `integer` (≥0) | ✅ | Minutos transcurridos desde que se emitió el token hasta el intento |
| `ip_country` | `string` | ✅ | País de origen de la solicitud |

**Datos sensibles:** No contiene PII. `user_id` es un UUID no reversible.

---

## 5. Proveedores (suppliers)

### 5.1 `supplier_created`

| Aspecto | Detalle |
|---------|---------|
| **Descripción** | Se registra un nuevo proveedor en el directorio. Se emite tras `POST /suppliers` exitoso. |
| **Dominio** | negocio/proveedores |
| **Clasificación** | 💡 Oportunidad identificada |

**Allowlist de `properties`:**

| Propiedad | Tipo | Obligatorio | Descripción |
|-----------|------|-------------|-------------|
| `supplier_id` | `integer` | ✅ | ID interno del proveedor en TinyDB |
| `country` | `string` (enum) | ✅ | País del contrato: `"USA"` \| `"Spain"` |
| `categories` | `array[string]` | ✅ | Categorías del proveedor (mínimo 1) |
| `rate_per_shipment` | `float` (>0) | ✅ | Tarifa por envío en la moneda del contrato |
| `currency` | `string` (enum) | ✅ | `"USD"` \| `"EUR"` |

**Datos sensibles:** Ninguno. `contact_email` no se incluye — es PII y no necesario para telemetría agregada.

---

### 5.2 `supplier_rate_updated`

| Aspecto | Detalle |
|---------|---------|
| **Descripción** | Se actualiza la tarifa por envío de un proveedor. Se emite tras `PATCH /suppliers/{id}/rate` exitoso. |
| **Dominio** | negocio/proveedores |
| **Clasificación** | 💡 Oportunidad identificada |

**Allowlist de `properties`:**

| Propiedad | Tipo | Obligatorio | Descripción |
|-----------|------|-------------|-------------|
| `supplier_id` | `integer` | ✅ | ID interno del proveedor |
| `country` | `string` (enum) | ✅ | País del contrato |
| `old_rate` | `float` (>0) | ✅ | Tarifa anterior |
| `new_rate` | `float` (>0) | ✅ | Tarifa nueva |
| `currency` | `string` (enum) | ✅ | `"USD"` \| `"EUR"` |
| `updated_by_role` | `string` | ✅ | Rol del usuario que realizó el cambio |

**Datos sensibles:** Ninguno.

---

### 5.3 `supplier_status_toggled`

| Aspecto | Detalle |
|---------|---------|
| **Descripción** | Se suspende o reactiva un proveedor. Se emite tras `PATCH /suppliers/{id}/status` exitoso. |
| **Dominio** | negocio/proveedores |
| **Clasificación** | 💡 Oportunidad identificada |

**Allowlist de `properties`:**

| Propiedad | Tipo | Obligatorio | Descripción |
|-----------|------|-------------|-------------|
| `supplier_id` | `integer` | ✅ | ID interno del proveedor |
| `country` | `string` (enum) | ✅ | País del contrato |
| `old_status` | `string` (enum) | ✅ | Estado anterior: `"active"` \| `"suspended"` |
| `new_status` | `string` (enum) | ✅ | Estado nuevo |
| `categories` | `array[string]` | ✅ | Categorías del proveedor |

**Datos sensibles:** Ninguno.

---

## 6. Incidencias

### 6.1 `incident_created`

| Aspecto | Detalle |
|---------|---------|
| **Descripción** | Se registra una nueva incidencia operativa en el sistema. Se emite tras `POST /api/incidents` exitoso. |
| **Dominio** | operaciones/incidencias |
| **Clasificación** | 💡 Oportunidad identificada |

**Allowlist de `properties`:**

| Propiedad | Tipo | Obligatorio | Descripción |
|-----------|------|-------------|-------------|
| `incident_id` | `string` (UUID) | ✅ | Identificador único de la incidencia |
| `category` | `string` (enum) | ✅ | Categoría: `"lost_parcel"` \| `"delivery_failure"` \| `"inventory_discrepancy"` \| `"carrier_issue"` \| `"returns_issue"` \| `"warehouse_incident"` \| `"system_failure"` \| `"client_complaint"` \| `"other"` |
| `origin` | `string` (enum) | ✅ | Origen: `"customer"` \| `"branch"` \| `"internal"` |
| `branch` | `string` (enum) | ✅ | Sede donde ocurrió: `"central"` \| `"la_warehouse"` \| `"la_office"` \| `"zaragoza_warehouse"` \| `"zaragoza_office"` |
| `status` | `string` (enum) | ✅ | Estado inicial: `"open"` |

**Datos sensibles:** No se incluye la `description` (podría contener PII del cliente o del consumidor final). Tampoco se incluye el `title`.

---

### 6.2 `incident_status_changed`

| Aspecto | Detalle |
|---------|---------|
| **Descripción** | Una incidencia cambia de estado (open → in_progress, in_progress → resolved, etc.). Se emite tras `PATCH /api/incidents/{id}/status` exitoso. |
| **Dominio** | operaciones/incidencias |
| **Clasificación** | 💡 Oportunidad identificada |

**Allowlist de `properties`:**

| Propiedad | Tipo | Obligatorio | Descripción |
|-----------|------|-------------|-------------|
| `incident_id` | `string` (UUID) | ✅ | Identificador de la incidencia |
| `category` | `string` (enum) | ✅ | Categoría de la incidencia |
| `from_status` | `string` (enum) | ✅ | Estado anterior |
| `to_status` | `string` (enum) | ✅ | Estado nuevo |
| `time_in_previous_status_ms` | `integer` (≥0) | ✅ | Tiempo en milisegundos que estuvo en el estado anterior |
| `updated_by_role` | `string` | ✅ | Rol del usuario que realizó el cambio |

**Datos sensibles:** Ninguno.

---

## 7. Rendimiento y navegación

### 7.1 `api_response_error_recorded`

| Aspecto | Detalle |
|---------|---------|
| **Descripción** | La API devuelve un código de error HTTP (4xx o 5xx). Se emite desde un middleware de respuesta. |
| **Dominio** | rendimiento/api |
| **Clasificación** | 💡 Oportunidad identificada |

**Allowlist de `properties`:**

| Propiedad | Tipo | Obligatorio | Descripción |
|-----------|------|-------------|-------------|
| `endpoint` | `string` | ✅ | Ruta del endpoint (ej. `"/api/incidents"`). Sin parámetros de path. |
| `method` | `string` (enum) | ✅ | Método HTTP: `"GET"` \| `"POST"` \| `"PATCH"` \| `"DELETE"` |
| `status_code` | `integer` | ✅ | Código HTTP de respuesta (400, 401, 403, 404, 422, 500, etc.) |
| `error_category` | `string` (enum) | ✅ | Categoría del error: `"client_error"` (4xx) \| `"server_error"` (5xx) |
| `error_detail` | `string` | ✅ | Descripción genérica del error (ej. `"Validation error"`, `"Insufficient permissions"`). Sin datos de entrada del usuario ni PII. |

> ⚠️ **Sanitización:** `error_detail` solo contiene mensajes genéricos predefinidos. Nunca incluye datos de entrada del usuario, valores de campos, ni trazas de stack completas.

---

### 7.2 `page_navigated`

| Aspecto | Detalle |
|---------|---------|
| **Descripción** | Un operador de backoffice navega entre secciones de la aplicación. Se emite desde el frontend al cambiar de ruta. |
| **Dominio** | navegación/ux |
| **Clasificación** | 💡 Oportunidad identificada |

**Allowlist de `properties`:**

| Propiedad | Tipo | Obligatorio | Descripción |
|-----------|------|-------------|-------------|
| `from_page` | `string` | ✅ | Sección de origen (ej. `"suppliers"`, `"incidents_panel"`, `"incidents_form"`) |
| `to_page` | `string` | ✅ | Sección de destino |
| `user_role` | `string` (enum) | ✅ | Rol del usuario |
| `navigation_method` | `string` (enum) | ✅ | Cómo navegó: `"nav_link"` \| `"breadcrumb"` \| `"direct_link"` \| `"browser_back"` |

**Datos sensibles:** Ninguno. No se registran URLs completas con parámetros.

---

### 7.3 `lead_form_submitted`

| Aspecto | Detalle |
|---------|---------|
| **Descripción** | Un visitante de la web pública completa y envía el formulario de solicitud de información. |
| **Dominio** | negocio/leads |
| **Clasificación** | 💡 Oportunidad identificada |

**Allowlist de `properties`:**

| Propiedad | Tipo | Obligatorio | Descripción |
|-----------|------|-------------|-------------|
| `product_type` | `string` (enum) | ✅ | Tipo de producto: `"fashion"` \| `"electronics"` \| `"cosmetics"` \| `"food"` \| `"other"` |
| `monthly_volume` | `string` (enum) | ✅ | Volumen estimado: `"0-100"` \| `"101-500"` \| `"501-2000"` \| `"2000+"` \| `"not-sure"` |
| `operating_country` | `string` (enum) | ✅ | `"us"` \| `"es"` \| `"both"` \| `"other"` |
| `has_3pl` | `string` (enum) | ✅ | `"yes"` \| `"no"` \| `"evaluating"` |
| `services_interest` | `array[string]` | ✅ | Servicios seleccionados: `["warehousing"]`, `["last-mile"]`, `["reverse-logistics"]` |

> ⚠️ **Sanitización:** No se incluye ningún PII del lead (nombre, email, teléfono, ni comentarios). Solo perfil de negocio agregable.

---

### 7.4 `cache_effectiveness_recorded`

| Aspecto | Detalle |
|---------|---------|
| **Descripción** | Medición periódica de la tasa de acierto de caché en los endpoints de incidencias y proveedores. |
| **Dominio** | rendimiento/caché |
| **Clasificación** | 💡 Oportunidad identificada |

**Allowlist de `properties`:**

| Propiedad | Tipo | Obligatorio | Descripción |
|-----------|------|-------------|-------------|
| `cache_name` | `string` (enum) | ✅ | Identificador de la caché: `"incident"` \| `"supplier"` |
| `cache_key_prefix` | `string` | ✅ | Prefijo de la clave (ej. `"list:"`, `"detail:"`, `"summary"`) |
| `hit` | `boolean` | ✅ | `true` si el valor se sirvió desde caché, `false` si se calculó |
| `ttl_seconds` | `integer` (>0) | ✅ | TTL configurado para esa entrada (60 para incidentes, 120 para proveedores) |

**Datos sensibles:** Ninguno.

---

## 8. Guía de sanitización de datos sensibles

### 8.1 Principios generales

1. **No incluir datos del consumidor final** en eventos de inventario — eso pertenece al dominio de última milla (CONTEXT-TELE.md sección 3).
2. **Cualquier campo que permita identificar a una persona física** debe ser omitido o anonimizado antes de emitir el evento.
3. **Email es siempre PII.** Nunca se emite en texto plano. Si es necesario para correlación, se hashea con SHA-256.
4. **IP completa es PII.** Solo se emite el país (`ip_country`) derivado de la IP.
5. **El campo `description` de incidencias** puede contener PII del cliente o consumidor. No se incluye en eventos de telemetría.
6. **El campo `contact_email` de proveedores** es PII del contacto comercial. No se incluye en eventos de telemetría.
7. **Los comentarios del formulario de leads** pueden contener PII. No se incluyen en eventos.

### 8.2 Tabla de campos sensibles y su tratamiento

| Campo | ¿PII? | Tratamiento en eventos |
|-------|-------|------------------------|
| `email` (usuario) | ✅ Sí | No se incluye. Usar `user_id` (UUID) en su lugar. |
| `email` (lead del formulario) | ✅ Sí | No se incluye en eventos de telemetría. |
| `email` (intento de login fallido) | ✅ Sí | SHA-256 → `email_hash`. Nunca el email original. |
| `password` | ✅ Sí | Nunca se captura ni se incluye en ningún evento. |
| `phone` | ✅ Sí | No se incluye en ningún evento de telemetría. |
| `ip_address` | ✅ Sí | Solo se emite el país derivado (`ip_country`). |
| `contact_email` (proveedor) | ✅ Sí | No se incluye en eventos de telemetría. |
| `description` (incidencia) | ⚠️ Potencial | No se incluye en eventos. |
| `title` (incidencia) | ⚠️ Potencial | No se incluye en eventos. |
| `company_name` / `contact_person` | ✅ Sí | No se incluyen en eventos de telemetría. |
| `comments` (lead) | ✅ Sí | No se incluye en eventos. |
| `tracking_number` (última milla) | ❌ No | No aplica — fuera del alcance del sistema de inventario. |

### 8.3 Regla de oro

> Si no puedes demostrar que un campo es necesario para una decisión de negocio agregada, no lo incluyas.

---

## 9. Exportación JSON Schema

Los esquemas completos en formato JSON Schema draft-07 se encuentran en el archivo:

📄 **[`event-schemas.json`](event-schemas.json)**

Estructura del archivo:

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "TrackFlow Telemetry Events",
  "description": "JSON Schema definitions for all TrackFlow telemetry events",
  "definitions": {
    "EventEnvelope": { "...": "..." },
    "inbound_order_created": { "...": "..." },
    "outbound_order_created": { "...": "..." },
    "outbound_order_insufficient_stock": { "...": "..." },
    "...": "..."
  }
}
```

Cada definición incluye:
- `type: "object"` con `required` fields
- `properties` con tipos, enumeraciones, descripciones
- `additionalProperties: false` para enforce del allowlist
- `description` con la hipótesis y decisión de negocio
- `sensitive` metadata opcional para documentar PII

---

*Documento generado para la Fase 2 del Plan de Telemetría de TrackFlow.*