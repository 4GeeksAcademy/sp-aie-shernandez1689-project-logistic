# Fase 1 — Catálogo Exhaustivo de Oportunidades de Datos

## TrackFlow · Plan de Telemetría

> **Empresa:** TrackFlow — Logística de almacén y última milla (Los Ángeles + Zaragoza)
> **Unidad:** TrackFlow Tech
> **Fecha:** 2026-10-16

---

## Índice

1. [Métricas obligatorias (desde CONTEXT-TELE.md)](#1-métricas-obligatorias)
2. [Mapeo del flujo de inventario y puntos de instrumentación](#2-mapeo-flujo-de-inventario)
3. [Catálogo ampliado: autenticación](#3-autenticación)
4. [Catálogo ampliado: backoffice de proveedores](#4-backoffice-de-proveedores)
5. [Catálogo ampliado: gestión de incidencias](#5-gestión-de-incidencias)
6. [Catálogo ampliado: formulario de leads (aplicación web)](#6-formulario-de-leads)
7. [Catálogo ampliado: rendimiento y errores de frontend](#7-rendimiento-y-errores)
8. [Catálogo ampliado: navegación y abandono de flujos](#8-navegación-y-abandono)
9. [Catálogo ampliado: gestión de usuarios y perfiles](#9-gestión-de-usuarios)
10. [Resumen clasificado](#10-resumen-clasificado)

---

## 1. Métricas obligatorias

> Estas 5 métricas vienen directamente de **CONTEXT-TELE.md** (sección 3). Son el **piso mínimo** del plan. Todas deben estar instrumentadas sí o sí.

### 1.1 `inbound_order_created`

| Elemento      | Contenido                                                                                                              |
| ------------- | ----------------------------------------------------------------------------------------------------------------------- |
| **Disparo**   | Un almacén registra la recepción de mercancía de un cliente (`POST /inventory/orders/inbound`)                          |
| **Hipótesis** | Necesitamos saber cuánto volumen entra, por cliente y por almacén                                                        |
| **Decisión**  | Planificar capacidad de almacén y personal según el volumen entrante (Ana)                                                |
| **Properties**| `warehouse` (`los_angeles` / `zaragoza`), `client_id`, `product_id` (SKU), `product_category`, `quantity`               |
| **Clasificación** | ✅ **Obligatorio** (CONTEXT-TELE.md)                                                                                 |

### 1.2 `outbound_order_created`

| Elemento      | Contenido                                                                                                              |
| ------------- | ----------------------------------------------------------------------------------------------------------------------- |
| **Disparo**   | Un almacén completa el picking y despacho de un pedido (`POST /inventory/orders/outbound`)                                |
| **Hipótesis** | Necesitamos saber cuántos pedidos se procesan, por cliente y almacén, y a qué ritmo                                        |
| **Decisión**  | Detectar cuellos de botella operativos antes de que afecten el SLA de entrega (Ana)                                       |
| **Properties**| `warehouse` (`los_angeles` / `zaragoza`), `client_id`, `product_id` (SKU), `product_category`, `quantity`, `exit_type` (`dispatch` / `loss`) |
| **Clasificación** | ✅ **Obligatorio** (CONTEXT-TELE.md)                                                                                 |

### 1.3 `stock_threshold_triggered`

| Elemento      | Contenido                                                                                                              |
| ------------- | ----------------------------------------------------------------------------------------------------------------------- |
| **Disparo**   | El stock de un SKU cae por debajo del mínimo configurado para ese cliente                                                |
| **Hipótesis** | Necesitamos saber con qué frecuencia un cliente se queda sin stock disponible de un SKU                                    |
| **Decisión**  | Alertar al cliente y al equipo comercial antes de un quiebre de stock (Miguel)                                            |
| **Properties**| `warehouse` (`los_angeles` / `zaragoza`), `client_id`, `product_id` (SKU), `product_category`, `quantity`, `threshold_value`, `current_stock` |
| **Clasificación** | ✅ **Obligatorio** (CONTEXT-TELE.md)                                                                                 |

### 1.4 `direct_stock_edit_rejected`

| Elemento      | Contenido                                                                                                              |
| ------------- | ----------------------------------------------------------------------------------------------------------------------- |
| **Disparo**   | Un usuario intenta modificar el stock directamente (fuera de una orden) y el sistema lo rechaza                           |
| **Hipótesis** | Necesitamos saber si el personal de almacén intenta saltarse el control de trazabilidad                                    |
| **Decisión**  | Reforzar capacitación o permisos en el almacén donde esto ocurra con más frecuencia                                       |
| **Properties**| `warehouse` (`los_angeles` / `zaragoza`), `client_id`, `product_id` (SKU), `product_category`, `user_role`, `attempt_type` |
| **Clasificación** | ✅ **Obligatorio** (CONTEXT-TELE.md)                                                                                 |

### 1.5 `inventory_discrepancy_detected`

| Elemento      | Contenido                                                                                                              |
| ------------- | ----------------------------------------------------------------------------------------------------------------------- |
| **Disparo**   | Un conteo físico o una auditoría detecta una diferencia entre el stock del sistema y el stock real                         |
| **Hipótesis** | Necesitamos saber en qué SKUs y almacenes ocurren más discrepancias                                                        |
| **Decisión**  | Priorizar auditorías de inventario en los SKUs con mayor tasa de discrepancia (Ana)                                       |
| **Properties**| `warehouse` (`los_angeles` / `zaragoza`), `client_id`, `product_id` (SKU), `product_category`, `expected_quantity`, `actual_quantity`, `variance` |
| **Clasificación** | ✅ **Obligatorio** (CONTEXT-TELE.md)                                                                                 |

> **Campos comunes a todos los eventos de inventario** (además del envelope estándar):
> `warehouse` (`los_angeles` / `zaragoza`), `client_id`, `product_id` (SKU), `product_category`, `quantity`

---

## 2. Mapeo del flujo de inventario y puntos de instrumentación

### Flujo completo: desde autenticación hasta orden completada

```
[Login] → [Panel / Lista SKUs] → [Consulta stock] → [Crear orden] → [Validación] → [Confirmación]
```

### 2.1 Punto: `inventory_sku_list_viewed`

**Capturamos** `inventory_sku_list_viewed` **porque necesitamos saber** qué SKUs consultan los operadores, con qué frecuencia y desde qué almacén, **lo que nos permite tomar la decisión** de optimizar la interfaz de consulta y pre-cargar en caché los SKUs más consultados.

| Elemento | Valor |
|---|---|
| **Disparo** | Usuario carga la lista de productos (`GET /inventory/products`) |
| **Properties** | `warehouse`, `user_role`, `result_count` |
| **Clasificación** | 💡 **Oportunidad identificada** |

### 2.2 Punto: `inbound_order_created`

> ✅ **Obligatorio** — Documentado en 1.1.

**Disparo en el código:** `POST /inventory/orders/inbound` en `services/user-api/app/routers/inventory.py`. La orden se crea con `sku_id`, `quantity`, `reference`, `warehouse`. Se asocia al usuario autenticado (`user_uuid`).

### 2.3 Punto: `outbound_order_created`

> ✅ **Obligatorio** — Documentado en 1.2.

**Disparo en el código:** `POST /inventory/orders/outbound` en `services/user-api/app/routers/inventory.py`. Incluye validación de stock suficiente antes de crear. Si `quantity > available`, la API rechaza la orden con HTTP 400.

### 2.4 Punto: `outbound_order_insufficient_stock`

**Capturamos** `outbound_order_insufficient_stock` **porque necesitamos saber** con qué frecuencia los operadores intentan despachar más stock del disponible, **lo que nos permite tomar la decisión** de ajustar los umbrales mínimos por SKU o revisar la precisión del inventario registrado.

| Elemento | Valor |
|---|---|
| **Disparo** | API rechaza una salida por stock insuficiente (valida `_current_stock` vs `payload.quantity`) |
| **Properties** | `warehouse`, `product_id`, `product_category`, `client_id`, `requested_quantity`, `available_stock` |
| **Clasificación** | 💡 **Oportunidad identificada** |

### 2.5 Punto: `direct_stock_edit_rejected`

> ✅ **Obligatorio** — Documentado en 1.4.

**Disparo en el código:** No existe endpoint que permita modificar stock directamente. Cualquier intento de llamar a un endpoint inexistente o manipular el estado de un SKU fuera de los endpoints `/orders/inbound` y `/orders/outbound` constituye un intento de edición directa.

### 2.6 Punto: `stock_threshold_triggered`

> ✅ **Obligatorio** — Documentado en 1.3.

**Disparo en el código:** Se calcularía post-creación de `outbound_order`. Tras cada salida, se evalúa el nivel de stock resultante contra el umbral mínimo del cliente/SKU.

### 2.7 Punto: `inventory_discrepancy_detected`

> ✅ **Obligatorio** — Documentado en 1.5.

**Disparo en el código:** Debe ser registrado por el sistema de auditoría (conteo físico), no por el flujo transaccional normal. Podría tener un endpoint dedicado o detectarse mediante procesos batch comparando `StockEntry` - `StockExit` vs. conteo real.

### 2.8 Punto extra: `inventory_order_list_viewed`

**Capturamos** `inventory_order_list_viewed` **porque necesitamos saber** si los operadores consultan con frecuencia el histórico de órdenes, **lo que nos permite tomar la decisión** de paginar, cachear o exportar los listados de órdenes para mejorar la experiencia.

| Elemento | Valor |
|---|---|
| **Disparo** | Usuario carga el listado de órdenes (`GET /inventory/orders`) |
| **Properties** | `warehouse`, `user_role`, `result_count`, `filter_date_range` |
| **Clasificación** | 💡 **Oportunidad identificada** |

---

## 3. Autenticación

### 3.1 `login_attempt`

**Capturamos** `login_attempt` **porque necesitamos saber** la tasa de éxito/fallo en el inicio de sesión por hora y por usuario, **lo que nos permite tomar la decisión** de activar bloqueos temporales por IP ante múltiples fallos y detectar intentos de acceso no autorizados.

| Elemento | Valor |
|---|---|
| **Disparo** | Se envía `POST /auth/login` |
| **Properties** | `email` (hash), `success` (bool), `failure_reason`, `ip_country` |
| **Clasificación** | 💡 **Oportunidad identificada** |

### 3.2 `login_success`

**Capturamos** `login_success` **porque necesitamos saber** qué usuarios acceden al sistema y desde qué zona horaria, **lo que nos permite tomar la decisión** de identificar patrones de acceso anómalos (ej. un operario de Zaragoza conectándose desde huso horario de EE. UU.).

| Elemento | Valor |
|---|---|
| **Disparo** | `POST /auth/login` responde 200 con token JWT |
| **Properties** | `user_id`, `user_role`, `warehouse_country` (asociado al perfil), `ip_country` |
| **Clasificación** | 💡 **Oportunidad identificada** |

### 3.3 `login_failure`

**Capturamos** `login_failure` **porque necesitamos saber** con qué frecuencia fallan las credenciales y si hay patrones de fuerza bruta, **lo que nos permite tomar la decisión** de implementar rate-limiting por IP y notificar a Andrés (CTO) ante picos anómalos.

| Elemento | Valor |
|---|---|
| **Disparo** | `POST /auth/login` responde 401 (credenciales inválidas) |
| **Properties** | `email` (hash), `failure_reason` (`invalid_credentials` / `inactive_user`), `ip_address` (hash), `attempt_count_last_5min` |
| **Clasificación** | 💡 **Oportunidad identificada** |

### 3.4 `session_token_expired`

**Capturamos** `session_token_expired` **porque necesitamos saber** con qué frecuencia los usuarios encuentran la sesión expirada al intentar acceder al backoffice, **lo que nos permite tomar la decisión** de ajustar el TTL del token JWT o implementar refresh tokens silenciosos.

| Elemento | Valor |
|---|---|
| **Disparo** | API responde 401 con `"Token expired"` en cualquier endpoint protegido |
| **Properties** | `endpoint`, `user_id` (si se pudo extraer del token expirado), `time_since_login` |
| **Clasificación** | 💡 **Oportunidad identificada** |

### 3.5 `session_invalid_token`

**Capturamos** `session_invalid_token` **porque necesitamos saber** si hay tokens manipulados o robados circulando, **lo que nos permite tomar la decisión** de rotar la clave JWT y notificar al equipo de seguridad.

| Elemento | Valor |
|---|---|
| **Disparo** | API responde 401 con `"Invalid token"` (token malformado, firma inválida) |
| **Properties** | `endpoint`, `token_origin_ip` (hash), `decoded_error_type` |
| **Clasificación** | 💡 **Oportunidad identificada** |

### 3.6 `password_reset_requested`

**Capturamos** `password_reset_requested` **porque necesitamos saber** con qué frecuencia los usuarios solicitan restablecer su contraseña, **lo que nos permite tomar la decisión** de identificar si hay un problema de usabilidad en el login o una campaña de phishing dirigida a empleados.

| Elemento | Valor |
|---|---|
| **Disparo** | `POST /auth/forgot-password` es llamado |
| **Properties** | `email` (hash), `time` |
| **Clasificación** | 💡 **Oportunidad identificada** |

### 3.7 `password_reset_completed`

**Capturamos** `password_reset_completed` **porque necesitamos saber** qué porcentaje de solicitudes de reset se completan exitosamente, **lo que nos permite tomar la decisión** de optimizar el flujo de recuperación si la tasa de finalización es baja.

| Elemento | Valor |
|---|---|
| **Disparo** | `POST /auth/reset-password` exitoso |
| **Properties** | `user_id`, `reset_method` |
| **Clasificación** | 💡 **Oportunidad identificada** |

### 3.8 `password_changed`

**Capturamos** `password_changed` **porque necesitamos saber** si los usuarios cambian su contraseña periódicamente, **lo que nos permite tomar la decisión** de hacer cumplir la política de rotación de contraseñas.

| Elemento | Valor |
|---|---|
| **Disparo** | `POST /auth/change-password` exitoso |
| **Properties** | `user_id`, `role` |
| **Clasificación** | 💡 **Oportunidad identificada** |

### 3.9 `registration_completed`

**Capturamos** `registration_completed` **porque necesitamos saber** cuántas cuentas nuevas se crean y con qué roles, **lo que nos permite tomar la decisión** de validar si la creación de cuentas debe requerir aprobación de un manager.

| Elemento | Valor |
|---|---|
| **Disparo** | `POST /users` exitoso (creación de usuario) |
| **Properties** | `user_id`, `role`, `created_by` (si fue auto-registro o admin) |
| **Clasificación** | 💡 **Oportunidad identificada** |

---

## 4. Backoffice de proveedores (suppliers)

### 4.1 `supplier_list_viewed`

**Capturamos** `supplier_list_viewed` **porque necesitamos saber** qué filtros (país, categoría) usan los operadores con más frecuencia, **lo que nos permite tomar la decisión** de pre-cargar las combinaciones de filtro más comunes y mejorar la caché de listados.

| Elemento | Valor |
|---|---|
| **Disparo** | `GET /suppliers` con/sin filtros |
| **Properties** | `country_filter`, `category_filter`, `result_count`, `response_time_ms` |
| **Clasificación** | 💡 **Oportunidad identificada** |

### 4.2 `supplier_detail_viewed`

**Capturamos** `supplier_detail_viewed` **porque necesitamos saber** qué proveedores consultan más los operadores, **lo que nos permite tomar la decisión** de mantener actualizados los datos de esos proveedores prioritarios.

| Elemento | Valor |
|---|---|
| **Disparo** | `GET /suppliers/{id}` |
| **Properties** | `supplier_id`, `country`, `categories` |
| **Clasificación** | 💡 **Oportunidad identificada** |

### 4.3 `supplier_created`

**Capturamos** `supplier_created` **porque necesitamos saber** con qué frecuencia se incorporan nuevos proveedores y en qué mercado, **lo que nos permite tomar la decisión** de anticipar la carga de trabajo del equipo de operaciones (Carlos).

| Elemento | Valor |
|---|---|
| **Disparo** | `POST /suppliers` exitoso |
| **Properties** | `supplier_id`, `country`, `categories`, `rate_per_shipment`, `currency` |
| **Clasificación** | 💡 **Oportunidad identificada** |

### 4.4 `supplier_rate_updated`

**Capturamos** `supplier_rate_updated` **porque necesitamos saber** la evolución de tarifas por carrier y mercado, **lo que nos permite tomar la decisión** de renegociar contratos cuando un carrier acumula subidas por encima del promedio del mercado.

| Elemento | Valor |
|---|---|
| **Disparo** | `PATCH /suppliers/{id}/rate` exitoso |
| **Properties** | `supplier_id`, `country`, `old_rate`, `new_rate`, `currency`, `updated_by_user_role` |
| **Clasificación** | 💡 **Oportunidad identificada** |

### 4.5 `supplier_status_toggled`

**Capturamos** `supplier_status_toggled` **porque necesitamos saber** con qué frecuencia se suspenden o reactivan proveedores, **lo que nos permite tomar la decisión** de identificar carriers con problemas recurrentes antes de que afecten el SLA.

| Elemento | Valor |
|---|---|
| **Disparo** | `PATCH /suppliers/{id}/status` (suspend/activate) |
| **Properties** | `supplier_id`, `country`, `old_status`, `new_status`, `categories` |
| **Clasificación** | 💡 **Oportunidad identificada** |

### 4.6 `supplier_deleted`

**Capturamos** `supplier_deleted` **porque necesitamos saber** si algún operador elimina proveedores (en lugar de suspenderlos, que es el flujo recomendado), **lo que nos permite tomar la decisión** de reforzar la política de suspensión vs. eliminación.

| Elemento | Valor |
|---|---|
| **Disparo** | `DELETE /suppliers/{id}` |
| **Properties** | `supplier_id`, `country`, `categories` |
| **Clasificación** | 💡 **Oportunidad identificada** |

---

## 5. Gestión de incidencias

### 5.1 `incident_list_viewed`

**Capturamos** `incident_list_viewed` **porque necesitamos saber** qué combinaciones de filtros (estado, origen, sede, categoría) usan con más frecuencia Ana, Carlos y Valentina, **lo que nos permite tomar la decisión** de ofrecer vistas guardadas personalizadas y precargar los filtros más comunes.

| Elemento | Valor |
|---|---|
| **Disparo** | `GET /api/incidents` con/sin filtros |
| **Properties** | `status_filter`, `origin_filter`, `branch_filter`, `category_filter`, `result_count`, `response_time_ms` |
| **Clasificación** | 💡 **Oportunidad identificada** |

### 5.2 `incident_summary_viewed`

**Capturamos** `incident_summary_viewed` **porque necesitamos saber** con qué frecuencia se consulta el panel resumen de incidencias, **lo que nos permite tomar la decisión** de pre-calcular el resumen y servirlo desde una tabla agregada en lugar de calcularlo en cada consulta.

| Elemento | Valor |
|---|---|
| **Disparo** | `GET /api/incidents/summary` |
| **Properties** | `response_time_ms`, `cache_hit` |
| **Clasificación** | 💡 **Oportunidad identificada** |

### 5.3 `incident_created`

**Capturamos** `incident_created` **porque necesitamos saber** la distribución de tipos de incidencia por almacén y por país, **lo que nos permite tomar la decisión** de asignar recursos preventivos (ej. si hay muchas `delivery_failure` en LA, revisar la calidad de direcciones).

| Elemento | Valor |
|---|---|
| **Disparo** | `POST /api/incidents` exitoso |
| **Properties** | `incident_id`, `category`, `origin`, `branch`, `status` |
| **Clasificación** | 💡 **Oportunidad identificada** |

### 5.4 `incident_status_changed`

**Capturamos** `incident_status_changed` **porque necesitamos saber** el tiempo que pasa entre estados (open → in_progress → resolved), **lo que nos permite tomar la decisión** de detectar incidencias estancadas y escalarlas automáticamente si superan las 24h en `in_progress`.

| Elemento | Valor |
|---|---|
| **Disparo** | `PATCH /api/incidents/{id}/status` exitoso |
| **Properties** | `incident_id`, `category`, `from_status`, `to_status`, `time_in_previous_status_ms`, `updated_by_role` |
| **Clasificación** | 💡 **Oportunidad identificada** |

### 5.5 `incident_detail_viewed`

**Capturamos** `incident_detail_viewed` **porque necesitamos saber** qué incidencias reciben más consultas y seguimiento, **lo que nos permite tomar la decisión** de priorizar su resolución o identificar si hay incidencias mal categorizadas que generan confusión.

| Elemento | Valor |
|---|---|
| **Disparo** | `GET /api/incidents/{incident_id}` |
| **Properties** | `incident_id`, `category`, `origin`, `view_count` |
| **Clasificación** | 💡 **Oportunidad identificada** |

---

## 6. Formulario de leads (aplicación web)

### 6.1 `lead_form_started`

**Capturamos** `lead_form_started` **porque necesitamos saber** cuántos visitantes comienzan a llenar el formulario de solicitud de información, **lo que nos permite tomar la decisión** de medir la tasa de conversión del formulario (completado vs. iniciado).

| Elemento | Valor |
|---|---|
| **Disparo** | Usuario carga la página del formulario de solicitud de información |
| **Properties** | `referrer`, `landing_page` |
| **Clasificación** | 💡 **Oportunidad identificada** |

### 6.2 `lead_form_field_error`

**Capturamos** `lead_form_field_error` **porque necesitamos saber** qué campos causan más errores de validación (formato de teléfono, email, nombre del contacto), **lo que nos permite tomar la decisión** de mejorar las instrucciones y placeholders de esos campos.

| Elemento | Valor |
|---|---|
| **Disparo** | Validación de campo falla en el frontend |
| **Properties** | `field_name`, `validation_type`, `error_count_per_field` |
| **Clasificación** | 💡 **Oportunidad identificada** |

### 6.3 `lead_form_submitted`

**Capturamos** `lead_form_submitted` **porque necesitamos saber** cuántos leads calificados genera la web, **lo que nos permite tomar la decisión** de evaluar el ROI de nuestras campañas de marketing y ajustar la capacidad comercial de Miguel.

| Elemento | Valor |
|---|---|
| **Disparo** | Formulario validado y enviado correctamente (POST) |
| **Properties** | `product_type`, `monthly_volume`, `operating_country`, `has_3pl` |
| **Clasificación** | 💡 **Oportunidad identificada** |

### 6.4 `lead_form_abandoned`

**Capturamos** `lead_form_abandoned` **porque necesitamos saber** en qué paso del formulario los usuarios lo abandonan, **lo que nos permite tomar la decisión** de simplificar los campos en esa sección o añadir un indicador de progreso.

| Elemento | Valor |
|---|---|
| **Disparo** | Usuario abandona la página del formulario sin enviarlo tras haber interactuado con al menos un campo |
| **Properties** | `last_field_interacted`, `time_spent_ms`, `fields_completed_count` |
| **Clasificación** | 💡 **Oportunidad identificada** |

### 6.5 `lead_volume_warning_shown`

**Capturamos** `lead_volume_warning_shown` **porque necesitamos saber** cuántos prospectos con volumen bajo (0-100 envíos/mes) continúan a pesar de la advertencia, **lo que nos permite tomar la decisión** de crear un plan de entrada específico para pequeños comercios.

| Elemento | Valor |
|---|---|
| **Disparo** | Se muestra la advertencia "volumen bajo" y el usuario elige continuar o no |
| **Properties** | `product_type`, `volume_range`, `user_continued` (bool) |
| **Clasificación** | 💡 **Oportunidad identificada** |

---

## 7. Rendimiento y errores de frontend

### 7.1 `api_response_time_high`

**Capturamos** `api_response_time_high` **porque necesitamos saber** qué endpoints de la API tienen tiempos de respuesta degradados (>2s), **lo que nos permite tomar la decisión** de optimizar consultas, añadir caché (como ya existe en suppliers e incidents) o escalar recursos.

| Elemento | Valor |
|---|---|
| **Disparo** | Un endpoint tarda más de 2 segundos en responder |
| **Properties** | `endpoint`, `method`, `response_time_ms`, `status_code`, `user_country` |
| **Clasificación** | 💡 **Oportunidad identificada** |

### 7.2 `api_response_error`

**Capturamos** `api_response_error` **porque necesitamos saber** con qué frecuencia la API devuelve errores (4xx, 5xx) por endpoint, **lo que nos permite tomar la decisión** de priorizar la corrección de los endpoints con mayor tasa de error.

| Elemento | Valor |
|---|---|
| **Disparo** | La API devuelve un código de error HTTP (400, 401, 403, 404, 422, 500) |
| **Properties** | `endpoint`, `method`, `status_code`, `error_detail` (sanitizado, sin datos personales) |
| **Clasificación** | 💡 **Oportunidad identificada** |

### 7.3 `frontend_uncaught_error`

**Capturamos** `frontend_uncaught_error` **porque necesitamos saber** qué errores de JavaScript no capturados ocurren en los navegadores de los operadores, **lo que nos permite tomar la decisión** de corregir bugs antes de que afecten la productividad del almacén.

| Elemento | Valor |
|---|---|
| **Disparo** | Un error JS no capturado (window.onerror, unhandledrejection) |
| **Properties** | `error_message` (sanitizado), `page_url`, `browser`, `os`, `line`, `column` |
| **Clasificación** | 💡 **Oportunidad identificada** |

### 7.4 `page_load_time`

**Capturamos** `page_load_time` **porque necesitamos saber** qué páginas del backoffice cargan más lento, **lo que nos permite tomar la decisión** de optimizar recursos (imágenes, JS bundles, lazy loading) en las páginas más visitadas.

| Elemento | Valor |
|---|---|
| **Disparo** | Una página del backoffice completa su carga (window.load o performance observer) |
| **Properties** | `page_url`, `load_time_ms`, `dom_content_loaded_ms`, `js_heap_size`, `device_category` (mobile/desktop) |
| **Clasificación** | 💡 **Oportunidad identificada** |

### 7.5 `cache_effectiveness`

**Capturamos** `cache_effectiveness` **porque necesitamos saber** la tasa de acierto de la caché en los endpoints de incidencias y proveedores, **lo que nos permite tomar la decisión** de ajustar los TTL (hoy: 60s para incidentes, 120s para proveedores) o las estrategias de invalidación.

| Elemento | Valor |
|---|---|
| **Disparo** | Cada consulta a un endpoint con caché (por medición, no por evento en producción) |
| **Properties** | `cache_name` (`incident` / `supplier`), `cache_key_prefix`, `hit` (bool), `ttl_seconds` |
| **Clasificación** | 💡 **Oportunidad identificada** |

---

## 8. Navegación y abandono de flujos

### 8.1 `page_navigated`

**Capturamos** `page_navigated` **porque necesitamos saber** qué secciones del backoffice son más visitadas y en qué orden, **lo que nos permite tomar la decisión** de reorganizar la navegación principal para reflejar los flujos de trabajo reales de Ana, Carlos y Valentina.

| Elemento | Valor |
|---|---|
| **Disparo** | Usuario navega entre secciones del backoffice (proveedores, incidencias, inventario) |
| **Properties** | `from_page`, `to_page`, `user_role`, `timestamp`, `navigation_method` (click nav vs. link directo vs. breadcrumb) |
| **Clasificación** | 💡 **Oportunidad identificada** |

### 8.2 `flow_abandoned_supplier_creation`

**Capturamos** `flow_abandoned_supplier_creation` **porque necesitamos saber** si los operadores comienzan a crear un proveedor y no completan el formulario, **lo que nos permite tomar la decisión** de simplificar el formulario de creación o añadir autocompletado.

| Elemento | Valor |
|---|---|
| **Disparo** | Usuario interactúa con el formulario de creación de proveedor pero abandona la página sin enviar |
| **Properties** | `fields_started`, `last_field_interacted`, `time_spent_ms` |
| **Clasificación** | 💡 **Oportunidad identificada** |

### 8.3 `flow_abandoned_incident_creation`

**Capturamos** `flow_abandoned_incident_creation` **porque necesitamos saber** si los operadores de almacén empiezan a reportar una incidencia y no la terminan, **lo que nos permite tomar la decisión** de identificar si el formulario es demasiado largo o confuso para el personal de almacén.

| Elemento | Valor |
|---|---|
| **Disparo** | Usuario interactúa con el formulario de incidencias pero abandona sin enviar |
| **Properties** | `fields_started`, `last_field_interacted`, `time_spent_ms`, `origin_branch` |
| **Clasificación** | 💡 **Oportunidad identificada** |

### 8.4 `backoffice_session_active`

**Capturamos** `backoffice_session_active` **porque necesitamos saber** cuánto tiempo pasan los operadores en cada sección del backoffice, **lo que nos permite tomar la decisión** de identificar qué tareas toman más tiempo y podrían beneficiarse de automatización.

| Elemento | Valor |
|---|---|
| **Disparo** | Heartbeat periódico (ej. cada 60s) mientras el usuario tiene una página abierta |
| **Properties** | `current_page`, `user_role`, `time_on_page_seconds` |
| **Clasificación** | 💡 **Oportunidad identificada** |

---

## 9. Gestión de usuarios y perfiles

### 9.1 `user_profile_updated`

**Capturamos** `user_profile_updated` **porque necesitamos saber** con qué frecuencia los usuarios actualizan su perfil (nombre, teléfono, dirección), **lo que nos permite tomar la decisión** de mantener los datos de contacto actualizados para notificaciones operativas.

| Elemento | Valor |
|---|---|
| **Disparo** | `PUT /profiles/me` exitoso |
| **Properties** | `user_id`, `fields_updated` (lista de campos modificados) |
| **Clasificación** | 💡 **Oportunidad identificada** |

### 9.2 `user_role_changed`

**Capturamos** `user_role_changed` **porque necesitamos saber** con qué frecuencia se modifican los roles de usuario, **lo que nos permite tomar la decisión de** auditar cambios de permisos y detectar posibles escaladas de privilegios no autorizadas.

| Elemento | Valor |
|---|---|
| **Disparo** | `PATCH /users/{id}` cambia el campo `role` |
| **Properties** | `target_user_id`, `from_role`, `to_role`, `changed_by_user_id` |
| **Clasificación** | 💡 **Oportunidad identificada** |

### 9.3 `user_deactivated`

**Capturamos** `user_deactivated` **porque necesitamos saber** cuándo se desactivan usuarios (empleados que salen de TrackFlow), **lo que nos permite tomar la decisión** de verificar que los accesos se revocan a tiempo y que no hay cuentas huérfanas.

| Elemento | Valor |
|---|---|
| **Disparo** | `PATCH /users/{id}` con `is_active: false` |
| **Properties** | `target_user_id`, `changed_by_user_id`, `last_active_timestamp` |
| **Clasificación** | 💡 **Oportunidad identificada** |

---

## 10. Resumen clasificado

### Leyenda

| Símbolo | Significado                          |
| ------- | ------------------------------------ |
| ✅      | **Obligatorio** — viene de CONTEXT-TELE.md, debe estar sí o sí |
| 💡      | **Oportunidad identificada** — propuesta del equipo de telemetría |

### Totales

| Tipo         | Cantidad |
| ------------ | -------- |
| ✅ Obligatorias | **5**  |
| 💡 Oportunidades | **40** |
| **Total**    | **45**   |

### Listado completo

| # | `event_type` | Clasificación | Dominio |
|---|---|---|---|
| 1 | `inbound_order_created` | ✅ Obligatorio | Inventario |
| 2 | `outbound_order_created` | ✅ Obligatorio | Inventario |
| 3 | `stock_threshold_triggered` | ✅ Obligatorio | Inventario |
| 4 | `direct_stock_edit_rejected` | ✅ Obligatorio | Inventario |
| 5 | `inventory_discrepancy_detected` | ✅ Obligatorio | Inventario |
| 6 | `inventory_sku_list_viewed` | 💡 Oportunidad | Inventario |
| 7 | `inventory_order_list_viewed` | 💡 Oportunidad | Inventario |
| 8 | `outbound_order_insufficient_stock` | 💡 Oportunidad | Inventario |
| 9 | `login_attempt` | 💡 Oportunidad | Autenticación |
| 10 | `login_success` | 💡 Oportunidad | Autenticación |
| 11 | `login_failure` | 💡 Oportunidad | Autenticación |
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
| 35 | `api_response_error` | 💡 Oportunidad | Rendimiento |
| 36 | `frontend_uncaught_error` | 💡 Oportunidad | Frontend |
| 37 | `page_load_time` | 💡 Oportunidad | Rendimiento |
| 38 | `cache_effectiveness` | 💡 Oportunidad | Rendimiento |
| 39 | `page_navigated` | 💡 Oportunidad | Navegación |
| 40 | `flow_abandoned_supplier_creation` | 💡 Oportunidad | Navegación |
| 41 | `flow_abandoned_incident_creation` | 💡 Oportunidad | Navegación |
| 42 | `backoffice_session_active` | 💡 Oportunidad | Navegación |
| 43 | `user_profile_updated` | 💡 Oportunidad | Usuarios |
| 44 | `user_role_changed` | 💡 Oportunidad | Usuarios |
| 45 | `user_deactivated` | 💡 Oportunidad | Usuarios |

---

## Anexo: Correspondencia con entidades de CONTEXT-TELE.md

| Entidad del CONTEXT | Identificador en el catálogo | Notas |
|---|---|---|
| `Product` | `product_id` (SKU), `product_category`, `client_id` | Los SKUs en el código tienen `client_name`, `sku`, `category` |
| `InboundOrder` | `inbound_order_created` | `POST /inventory/orders/inbound` |
| `OutboundOrder` | `outbound_order_created` | `POST /inventory/orders/outbound` |
| `warehouse` | `warehouse` = `los_angeles` / `zaragoza` | En el código: `LA` / `ZGZ` → se mapea a `los_angeles` / `zaragoza` |
| `client` | `client_id` | En el código: `client_name` (PureStep Footwear, SoundWave Electronics, etc.) |

---
*Documento generado para la Fase 1 del Plan de Telemetría de TrackFlow.*