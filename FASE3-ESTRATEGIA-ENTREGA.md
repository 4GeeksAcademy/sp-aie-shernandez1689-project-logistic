# Fase 3 — Estrategia de Entrega

## TrackFlow · Plan de Telemetría

> **Empresa:** TrackFlow — Logística de almacén y última milla (Los Ángeles + Zaragoza)
> **Unidad:** TrackFlow Tech
> **Fecha:** 2026-10-16
> **Documento:** Estrategia de entrega (stream vs. batch), throttle/debounce, y riesgos/exclusiones

---

## Índice

1. [Decisión stream vs. batch por evento](#1-decisión-stream-vs-batch-por-evento)
2. [Mapa de entrega resumen](#2-mapa-de-entrega-resumen)
3. [Estrategia de throttle / debounce](#3-estrategia-de-throttle--debounce)
4. [Riesgos y exclusiones](#4-riesgos-y-exclusiones)

---

## 1. Decisión stream vs. batch por evento

Cada evento se clasifica como **stream** (procesamiento en tiempo real, latencia de segundos a minutos) o **batch** (procesamiento por lotes periódicos, latencia de horas a días). La clasificación no responde a preferencia técnica sino a la **urgencia de la decisión de negocio** que el evento alimenta.

### Criterio de clasificación

| Canal | Latencia | Cuándo usarlo |
|-------|----------|---------------|
| **Stream** | Segundos a minutos | La decisión que habilita el evento no puede esperar. Un retraso de horas causaría daño operativo (pérdida de stock, violación de SLA, fuga de clientes, riesgo de seguridad). |
| **Batch** | Horas a días (lote diario) | La decisión es analítica o estratégica. El evento se agregará con otros para detectar tendencias, patrones o asignar recursos. Un retraso de horas no causa daño inmediato. |

---

### 1.1 `inbound_order_created` → **Stream** (near-real-time)

**Justificación:**

Ana necesita planificar capacidad de almacén y personal. Aunque las órdenes de entrada suelen planificarse con antelación, cuando llegan múltiples órdenes imprevistas (ej. un cliente acelera su producción navideña), la decisión de reasignar personal o espacio en el almacén se toma en **minutos, no en días**.

Un lote diario implicaría que Ana descubre al día siguiente que ayer entraron 10 pallets de más y no había personal asignado. El daño operativo (gente ociosa en un almacén, saturación en el otro) se produce en el turno, no en el reporte.

| Criterio | Evaluación |
|----------|------------|
| Urgencia de la decisión | Media-alta. Reasignación de personal​ entre almacenes ocurre en el día. |
| ¿Daño si se retrasa 1 hora? | Menor. Los inbound son recepciones planificadas. |
| ¿Daño si se retrasa 1 turno? | Medio. Personal mal asignado = sobrecosto operativo. |
| **Veredicto** | **Stream**. Latencia aceptable: segundos a minutos. |

---

### 1.2 `outbound_order_created` → **Stream** (real-time)

**Justificación:**

Ana necesita **detectar cuellos de botella antes de que afecten el SLA de entrega**. Si a las 10:00 el almacén de Los Ángeles está procesando pedidos al 200% de su capacidad normal, y el equipo no recibe la señal hasta el reporte del día siguiente, los pedidos de ese día ya incumplirán el SLA.

La decisión operativa es inmediata: redistribuir pickers de una zona a otra, autorizar horas extra, o desviar tráfico al otro almacén. Cada hora sin actuar reduce la capacidad de recuperación.

| Criterio | Evaluación |
|----------|------------|
| Urgencia de la decisión | Alta. Cuello de botella → SLA incumplido en horas. |
| ¿Daño si se retrasa 1 hora? | Significativo. Pedidos acumulados → envíos tardíos. |
| ¿Daño si se retrasa 1 turno? | Crítico. El SLA del día ya está comprometido. |
| **Veredicto** | **Stream**. Latencia aceptable: segundos. |

---

### 1.3 `stock_threshold_triggered` → **Stream** (real-time + alerta)

**Justificación:**

Miguel necesita **alertar al cliente antes de un quiebre de stock**. Un quiebre de stock no es un problema si se detecta con tiempo para reabastecer; el problema es detectarlo cuando el stock ya es cero. Cada hora entre el trigger y la alerta reduce la ventana de reabastecimiento del cliente.

Además, el evento puede encadenarse a una automatización: si el stock de un SKU cae por debajo del umbral, el sistema puede generar automáticamente una solicitud de reabastecimiento al cliente. Esa automatización necesita el evento en tiempo real.

| Criterio | Evaluación |
|----------|------------|
| Urgencia de la decisión | Alta. Cliente podría quedarse sin stock. |
| ¿Daño si se retrasa 1 hora? | Significativo. Ventana de reabastecimiento reducida. |
| ¿Daño si se retrasa 1 turno? | Crítico. Posible quiebre de stock y pérdida de ventas. |
| **Veredicto** | **Stream**. Debe activar una alerta inmediata a Miguel y al cliente. |

---

### 1.4 `direct_stock_edit_rejected` → **Batch** (diario)

**Justificación:**

La decisión que habilita es **reforzar capacitación o permisos en el almacén donde esto ocurre con más frecuencia**. Es una decisión de supervisión y entrenamiento, no una decisión de respuesta inmediata. Un usuario que intenta saltarse el control no representa un riesgo inminente (el sistema ya lo rechazó); el riesgo es que se convierta en un patrón.

Un lote diario permite ver qué usuarios y almacenes concentran los intentos, sin necesidad de reaccionar en tiempo real.

| Criterio | Evaluación |
|----------|------------|
| Urgencia de la decisión | Baja. El intento ya fue rechazado. No hay daño inmediato. |
| ¿Daño si se retrasa 1 hora? | Ninguno. El patrón requiere acumulación de datos. |
| ¿Daño si se retrasa 1 turno? | Ninguno. La decisión es de formación, no operativa. |
| **Veredicto** | **Batch**. Lote diario al cierre del turno. |

---

### 1.5 `inventory_discrepancy_detected` → **Batch** (diario)

**Justificación:**

La decisión es **priorizar auditorías**: identificar qué SKUs y almacenes tienen más discrepancias para enfocar los recursos de auditoría. Es una decisión analítica que requiere agregación sobre múltiples eventos. Una discrepancia individual no requiere acción inmediata (el ajuste se hace durante la auditoría programada); el patrón de discrepancias es lo que realmente informa la decisión.

| Criterio | Evaluación |
|----------|------------|
| Urgencia de la decisión | Baja. Las discrepancias se resuelven en auditorías programadas. |
| ¿Daño si se retrasa 1 hora? | Ninguno. Una discrepancia aislada no es emergencia. |
| ¿Daño si se retrasa 1 turno? | Bajo. La agregación diaria es suficiente. |
| **Veredicto** | **Batch**. Lote diario con agregación por SKU/almacén. |

---

### 1.6 `outbound_order_insufficient_stock` → **Stream** (con debounce)

**Justificación:**

Este evento se dispara cuando un operador intenta despachar más unidades de las disponibles. Aunque el intento individual es de baja urgencia (el sistema ya lo rechazó), una **ráfaga de estos eventos en minutos** indica un problema grave: el stock del sistema no refleja el stock real, o hay un error de configuración del SKU.

El stream permite detectar la ráfaga. Pero no tiene sentido procesar evento por evento en tiempo real; lo correcto es **acumular en ventanas de 1 minuto** y emitir una alerta si se supera un umbral (ej. >3 eventos del mismo SKU en 1 minuto).

| Criterio | Evaluación |
|----------|------------|
| Urgencia de la decisión | Media. El evento individual no es urgente, pero la ráfaga sí. |
| ¿Daño si se retrasa 1 hora? | Medio. Si hay una ráfaga, el stock podría ser incorrecto. |
| **Veredicto** | **Stream** con ventana de 1 minuto para detectar ráfagas. Ver debounce en sección 3. |

---

### 1.7 `login_succeeded` → **Batch** (diario)

**Justificación:**

La hipótesis es detectar **patrones de acceso anómalos** (ej. un operador de Zaragoza iniciando sesión desde una IP de EE. UU.). Esto no requiere respuesta en tiempo real — el usuario ya está autenticado. La detección de anomalías funciona mejor con datos agregados de varios días para establecer una línea base de comportamiento.

| Criterio | Evaluación |
|----------|------------|
| Urgencia de la decisión | Baja. El usuario ya ingresó. No hay daño inmediato. |
| ¿Daño si se retrasa 1 hora? | Ninguno. La anomalía requiere línea base histórica. |
| **Veredicto** | **Batch**. Lote diario para análisis de anomalías. |

---

### 1.8 `login_failed` → **Stream** (real-time + alerta)

**Justificación:**

Un ataque de fuerza bruta puede comprometer una cuenta en minutos si no se detecta a tiempo. La decisión de **bloquear una IP o activar rate-limiting** no puede esperar al lote diario. Cada intento fallido adicional acerca el atacante a una contraseña válida.

El evento incluye `attempt_count_last_5min` para que el pipeline de stream pueda decidir si bloquear sin depender de agregación externa.

| Criterio | Evaluación |
|----------|------------|
| Urgencia de la decisión | Alta. Ataque de fuerza bruta en curso. |
| ¿Daño si se retrasa 1 hora? | Crítico. Cuenta comprometida. |
| ¿Daño si se retrasa 1 turno? | Inaceptable. |
| **Veredicto** | **Stream**. Alerta en tiempo real con rate-limiting automático. |

---

### 1.9 `session_token_expired` → **Batch** (diario)

**Justificación:**

La decisión es **ajustar el TTL del JWT o implementar refresh tokens**. Esto se decide analizando la distribución de tiempos de expiración: ¿los usuarios encuentran sesiones expiradas después de 5 minutos? ¿Después de 2 horas? Es una métrica de UX que requiere agregación, no respuesta inmediata.

| Criterio | Evaluación |
|----------|------------|
| Urgencia de la decisión | Baja. Ajuste de configuración, no respuesta operativa. |
| ¿Daño si se retrasa 1 hora? | Ninguno. |
| **Veredicto** | **Batch**. Lote diario con histograma de `time_since_login_minutes`. |

---

### 1.10 `supplier_created` → **Batch** (diario)

**Justificación:**

Carlos necesita anticipar carga de trabajo operativa por la incorporación de nuevos proveedores. Esta es una decisión de planificación: asignar recursos para integrar al nuevo carrier en los sistemas de TrackFlow. No hay urgencia operativa en el momento exacto del alta.

| Criterio | Evaluación |
|----------|------------|
| Urgencia de la decisión | Baja. La integración del proveedor toma días, no minutos. |
| ¿Daño si se retrasa 1 hora? | Ninguno. |
| **Veredicto** | **Batch**. Lote diario. |

---

### 1.11 `supplier_rate_updated` → **Batch** (semanal)

**Justificación:**

La hipótesis es **detectar tendencias de incremento de tarifas por carrier y mercado**. La decisión de renegociar un contrato se toma en semanas, no en horas. Un incremento de tarifa individual rara vez justifica acción inmediata; el patrón de incrementos acumulados es lo que importa.

Además, los rates no cambian con frecuencia — este evento ocurre quizás una vez al mes por proveedor. La agregación semanal es suficiente.

| Criterio | Evaluación |
|----------|------------|
| Urgencia de la decisión | Muy baja. Renegociación de contratos es estratégica. |
| Frecuencia del evento | Muy baja (semanal o mensual por proveedor). |
| **Veredicto** | **Batch**. Lote semanal. |

---

### 1.12 `supplier_status_toggled` → **Stream** (real-time)

**Justificación:**

Si un carrier pasa a `suspended`, las operaciones de despacho necesitan saberlo **inmediatamente** para no asignar envíos a un carrier que no puede recoger. Cada minuto que el sistema sigue asignando envíos a un carrier suspendido genera incidencias de "carrier no se presentó", paquetes retrasados y clientes insatisfechos.

La decisión no es de Carlos revisando reports; es del sistema de asignación de envíos, que debe consumir este evento y dejar de ofrecer ese carrier en la interfaz de selección.

| Criterio | Evaluación |
|----------|------------|
| Urgencia de la decisión | Alta. Envíos mal asignados generan incidencias y retrasos. |
| ¿Daño si se retrasa 1 hora? | Significativo. Varias expediciones mal asignadas. |
| ¿Daño si se retrasa 1 turno? | Crítico. Decenas de envíos fallidos. |
| **Veredicto** | **Stream**. El sistema de asignación debe consumirlo en tiempo real. |

---

### 1.13 `incident_created` → **Stream** (real-time)

**Justificación:**

Una incidencia abierta requiere visibilidad inmediata: el equipo de operaciones necesita saber que hay un paquete perdido, una devolución problemática o una queja de cliente para poder actuar. La incidencia representa un problema activo que empeora con el tiempo.

La decisión de **asignar un responsable** o **iniciar una investigación** debe ocurrir en minutos, no en horas. Si un `lost_parcel` se registra a las 09:00 y el equipo lo descubre en el reporte del día siguiente, el paquete ya no se recuperará.

| Criterio | Evaluación |
|----------|------------|
| Urgencia de la decisión | Alta. El problema está activo y empeora. |
| ¿Daño si se retrasa 1 hora? | Significativo. Ventana de recuperación perdida. |
| **Veredicto** | **Stream**. Debe notificar al equipo de operaciones. |

---

### 1.14 `incident_status_changed` → **Stream** (near-real-time)

**Justificación:**

La hipótesis es **medir el tiempo entre transiciones de estado** para auto-escalar incidencias estancadas. Si una incidencia pasa a `in_progress` y no se mueve en 24 horas, el sistema debe escalarla automáticamente. Ese temporizador necesita el evento de transición en tiempo real para empezar a contar.

| Criterio | Evaluación |
|----------|------------|
| Urgencia de la decisión | Media. El escalado automático depende del tiempo exacto. |
| ¿Daño si se retrasa? | El temporizador de escalado se desvía. |
| **Veredicto** | **Stream**. Necesario para el SLA de resolución. |

---

### 1.15 `api_response_error_recorded` → **Stream** (real-time con debounce)

**Justificación:**

Un pico de errores 500 indica una caída del sistema o un despliegue fallido. Cada segundo que el equipo de ingeniería no sabe que hay un pico de errores, los clientes B2B están recibiendo respuestas de error en sus integraciones.

Sin embargo, no tiene sentido alertar por cada error 404 o 400 individual (errores de cliente que no requieren acción). La estrategia debe diferenciar:
- **5xx** → stream con alerta inmediata (umbral: >1% de errores en 1 minuto).
- **4xx** → batch diario (errores de cliente, no requieren respuesta inmediata).

| Criterio | Evaluación |
|----------|------------|
| Urgencia de la decisión | Alta para 5xx. Baja para 4xx. |
| ¿Daño si se retrasa? | Una caída no detectada afecta a todos los clientes. |
| **Veredicto** | **Stream** para 5xx. **Batch** para 4xx. Ver debounce en sección 3. |

---

### 1.16 `page_navigated` → **Batch** (diario)

**Justificación:**

El objetivo es entender cómo navegan Ana, Carlos y Valentina para **reorganizar la navegación del backoffice**. Esto es análisis de UX puro: requiere agregar cientos de miles de eventos para construir mapas de calor de navegación y embudos de conversión. No hay decisión urgente que tomar ante una navegación individual.

| Criterio | Evaluación |
|----------|------------|
| Urgencia de la decisión | Nula. Rediseño de navegación es trimestral. |
| Frecuencia del evento | Muy alta (docenas por sesión de usuario). |
| **Veredicto** | **Batch**. Lote diario con agregación por par origen-destino. |

---

### 1.17 `lead_form_submitted` → **Stream** (real-time)

**Justificación:**

Un lead que completa el formulario es una **oportunidad de venta activa**. Miguel (comercial) debe contactar al lead mientras el interés está fresco — cuanto más tiempo pasa, menor tasa de conversión. La decisión de asignar un comercial y contactar al lead se toma en minutos, no en días.

El evento no contiene PII del lead por diseño (ver sección 8.3 de Fase 2), pero debe activar una notificación al CRM o al equipo comercial para que accedan a los datos completos del lead desde el formulario (que están almacenados de forma separada y segura).

| Criterio | Evaluación |
|----------|------------|
| Urgencia de la decisión | Alta. Ventana de conversión de lead es corta. |
| ¿Daño si se retrasa 1 hora? | Significativo. El lead se enfría. |
| ¿Daño si se retrasa 1 turno? | Probable pérdida del lead frente a competencia. |
| **Veredicto** | **Stream**. Notificación inmediata al equipo comercial. |

---

### 1.18 `cache_effectiveness_recorded` → **Batch** (según su propia frecuencia)

**Justificación:**

Este evento **ya es periódico por diseño** — se emite cada vez que se consulta la caché (hit o miss). No hay urgencia en procesarlo en tiempo real; el objetivo es calcular la tasa de acierto (`hit / total`) para ajustar TTL. Ese cálculo requiere agregación.

| Criterio | Evaluación |
|----------|------------|
| Urgencia de la decisión | Nula. Ajuste de TTL es decisión de infraestructura. |
| Naturaleza del evento | Ya es periódico. |
| **Veredicto** | **Batch**. Agregación horaria o diaria de la tasa de acierto. |

---

## 2. Mapa de entrega resumen

| # | Evento | Canal | Latencia objetivo | Consumidor principal |
|---|--------|-------|-------------------|---------------------|
| 1 | `inbound_order_created` | Stream | Segundos | Dashboard Ana |
| 2 | `outbound_order_created` | Stream | Segundos | Dashboard Ana + SLA |
| 3 | `stock_threshold_triggered` | **Stream + Alerta** | Segundos | Alerta Miguel + cliente |
| 4 | `direct_stock_edit_rejected` | Batch | Diario | Reporte supervisión |
| 5 | `inventory_discrepancy_detected` | Batch | Diario | Auditoría Ana |
| 6 | `outbound_order_insufficient_stock` | **Stream (debounce)** | Ventana 1 min | Alerta umbral |
| 7 | `login_succeeded` | Batch | Diario | Anomalías Andrés |
| 8 | `login_failed` | **Stream + Alerta** | Segundos | Rate-limiting automático |
| 9 | `session_token_expired` | Batch | Diario | UX / configuración |
| 10 | `supplier_created` | Batch | Diario | Planificación Carlos |
| 11 | `supplier_rate_updated` | Batch | Semanal | Renegociación Carlos |
| 12 | `supplier_status_toggled` | **Stream** | Segundos | Sistema asignación envíos |
| 13 | `incident_created` | **Stream** | Segundos | Equipo operaciones |
| 14 | `incident_status_changed` | Stream | Minutos | SLA / escalado automático |
| 15 | `api_response_error_recorded` | **Stream (5xx)** / Batch (4xx) | 1 min (5xx) / diario (4xx) | Alerta ingeniería |
| 16 | `page_navigated` | Batch | Diario | UX / producto |
| 17 | `lead_form_submitted` | **Stream** | Segundos | CRM / Miguel |
| 18 | `cache_effectiveness_recorded` | Batch | Horaria | Infraestructura |

**Totales:**

| Canal | Cantidad |
|-------|----------|
| **Stream** (tiempo real) | 12 eventos |
| &nbsp;&nbsp;⊢ Stream puro | 8 |
| &nbsp;&nbsp;⊢ Stream + alerta dedicada | 2 (`stock_threshold_triggered`, `login_failed`) |
| &nbsp;&nbsp;⊢ Stream con debounce | 2 (`outbound_order_insufficient_stock`, `api_response_error_recorded`) |
| **Batch** (lotes periódicos) | 9 eventos |
| &nbsp;&nbsp;⊢ Batch diario | 8 |
| &nbsp;&nbsp;⊢ Batch semanal | 1 (`supplier_rate_updated`) |

> Nota: `api_response_error_recorded` se bifurca: 5xx en stream, 4xx en batch. Por eso la suma excede 18.

---

## 3. Estrategia de throttle / debounce

No todos los eventos necesitan limitación de frecuencia. La tabla siguiente solo incluye eventos **que pueden dispararse decenas o cientos de veces en un período corto** y que requieren protección del pipeline de telemetría.

### 3.1 Eventos de alta frecuencia identificados

| Evento | Frecuencia estimada | Riesgo | Estrategia |
|--------|-------------------|--------|------------|
| `page_navigated` | Decenas por sesión de usuario. Con ~50 usuarios simultáneos: hasta 1.000 eventos/min. | Saturación del pipeline. Bajo valor analítico por evento individual. | **Debounce por usuario**: si el mismo usuario navega a otra página en menos de 5 segundos, solo se emite el último evento. Esto reduce el volumen ~40% sin perder el flujo de navegación. |
| `api_response_error_recorded` | Bajo condiciones normales: raro. Durante un incidente (500 masivo): cientos por minuto. | Inundación de alertas. Cada 500 genera un evento, pero 100 errores idénticos no aportan más información que "hay una caída". | **Throttle por endpoint + código**: máximo 10 eventos/minuto por tupla (endpoint, status_code). Los eventos excedentes se agregan en contador y se emiten como un único evento `error_burst_detected` si el contador supera 100. |
| `login_failed` | Normal: ocasional. Durante ataque de fuerza bruta: cientos por minuto. | Inundación + riesgo de seguridad. | **Throttle por IP**: máximo 1 evento/login_failed por IP cada 5 segundos. El campo `attempt_count_last_5min` ya captura la agregación. No se pierde información de seguridad. |
| `cache_effectiveness_recorded` | Por cada consulta a caché: potencialmente miles por minuto. | Saturación innecesaria. No hay valor en registrar cada hit/miss individual. | **Muestreo sistemático**: se emite 1 evento cada 100 consultas (ratio 1:100). Para cálculos de tasa de acierto, el muestreo es estadísticamente suficiente con N grande (>10.000 consultas/día). |
| `outbound_order_created` | Picos horarios: hasta 60 eventos/min en hora punta (Black Friday). | Volumen manejable, pero en picos extremos podría saturar. | **Back-pressure natural**: el evento se emite sincrónicamente tras completar el picking. Si el pipeline está saturado, el picking se ralentiza — lo cual es aceptable porque evita que el backlog de telemetría crezca sin control. No se aplica throttle artificial. |

### 3.2 Resumen de reglas

| Regla | Eventos afectados | Configuración |
|-------|-------------------|---------------|
| **Debounce** (por usuario) | `page_navigated` | Ventana de 5 segundos. Solo se emite el último evento. |
| **Throttle** (por endpoint + código) | `api_response_error_recorded` | 10 eventos/min por (endpoint, status_code). Agregar excedentes. |
| **Throttle** (por IP) | `login_failed` | 1 evento cada 5 segundos por IP de origen. |
| **Muestreo** (1:N) | `cache_effectiveness_recorded` | Ratio 1:100. Emitir 1 de cada 100 ocurrencias. |
| **Back-pressure** | `outbound_order_created` | Sin throttle. El síncrono regula naturalmente el flujo. |

### 3.3 Implementación recomendada

Para eventos con throttle/debounce, la lógica de limitación debe ejecutarse **en el emisor** (antes de emitir el evento), no en el pipeline de ingestión. Esto evita:

1. Ocupar ancho de banda de red con eventos descartados.
2. Acumular colas en el broker de eventos.
3. Confundir métricas de throughput del pipeline con eventos reales.

**Excepción:** Para `login_failed`, el throttle debe estar en el API Gateway (a nivel de infraestructura) para bloquear la fuente antes de que llegue al emisor.

---

## 4. Riesgos y exclusiones

### 4.1 Eventos considerados y descartados

| Evento descartado | Dominio | Motivo de exclusión |
|-------------------|---------|---------------------|
| `outbound_order_dispatched` | Inventario | Redundante con `outbound_order_created`. El "dispatch" es el mismo momento que "created". No aporta información adicional. |
| `inventory_sku_list_viewed` | Inventario | Originalmente identificado en Fase 1, pero descartado: un operador que consulta un SKU no genera decisión de negocio medible. Solo añade ruido. |
| `outbound_order_cancelled` | Inventario | Fuera del alcance de Fase 2. Las cancelaciones son gestionadas por el sistema de última milla (tracking), no por el sistema de inventario. Se considerará en futura fase del proyecto de tracking. |
| `login_attempt` | Autenticación | Reemplazado por `login_succeeded` + `login_failed`. Un intento es, por definición, exitoso o fallido. El evento intermedio no aporta valor y duplica el volumen. |
| `password_changed` | Autenticación | Evento de seguridad relevante pero fuera del alcance del plan de telemetría de inventario/operaciones. Se considera para un plan de seguridad independiente. |
| `password_reset_requested` | Autenticación | Ídem `password_changed`. Incluye además PII (email en texto plano para el envío del reset). Evitamos ese riesgo deliberadamente. |
| `user_registered` | Usuarios | No existe registro de usuarios en TrackFlow (los usuarios son creados por administradores). No hay evento que capturar. |
| `user_profile_updated` | Usuarios | Podría contener PII (nombre, email, teléfono). El valor analítico es bajo frente al riesgo de fuga de datos. Excluido. |
| `supplier_contact_updated` | Proveedores | Contiene PII del contacto comercial (nombre, email, teléfono). El evento de telemetría no aporta valor suficiente para justificar el riesgo. |
| `incident_resolution_time_recorded` | Incidencias | Dato derivado: se calcula como `incident_status_changed` a `resolved` - `incident_created`. No necesita evento propio. |
| `frontend_error_occurred` | Rendimiento | Valor limitado sin contexto de sesión completo. Se decide no instrumentar errores de frontend en esta fase para evitar ruido. Se evaluará con RUM (Real User Monitoring) en el futuro. |
| `api_latency_recorded` | Rendimiento | Requiere agreement sobre percentiles y muestreo. Se pospone a una fase de observabilidad más madura. La latencia puede inferirse indirectamente del gap entre `outbound_order_created` y eventos downstream. |
| `inventory_stock_snapshot` | Inventario | Un snapshot periódico de todo el stock sería un evento masivo (miles de SKUs por almacén). Mejor construir la foto desde los eventos incrementales (event sourcing). |
| `email_opened` / `email_clicked` | Marketing | Fuera del alcance del sistema de TrackFlow. Corresponden a la herramienta de email marketing del equipo comercial. |

### 4.2 Datos excluidos por privacidad

| Dato | ¿Dónde aparece? | Motivo de exclusión |
|------|-----------------|---------------------|
| Email del consumidor final | Envíos, incidencias, tracking | PII del destinatario. Prohibido por CONTEXT-TELE.md sección 3. |
| Email del cliente B2B (contacto) | Proveedores, clientes | PII del contacto comercial. No necesario para telemetría agregada. |
| IP completa (dirección IPv4/IPv6) | Autenticación, API | PII. Solo se emite `ip_country` (código de país). |
| Nombre del consumidor final | Órdenes de salida | PII. No pertenece al dominio de inventario. |
| Dirección del consumidor final | Órdenes de salida | PII. Pertenece al dominio de última milla. |
| Teléfono del consumidor final | Incidencias, envíos | PII. No incluido por diseño. |
| Descripción y título de incidencias | Incidencias | Pueden contener PII del cliente o consumidor, datos de carriers, o información comercial sensible. |
| Comentarios del formulario web | Leads | Pueden contener PII del lead. El lead se gestiona en CRM con acceso controlado, no en telemetría. |
| Stack traces completos | Errores API | Pueden contener rutas internas, nombres de base de datos, IPs internas. Solo se emite `error_detail` con mensajes predefinidos. |

### 4.3 Exclusiones por costo / esfuerzo

| Exclusión | Motivo |
|-----------|--------|
| **Eventos de última milla** (tracking de paquetes, entregas, firmas) | Corresponden a un proyecto de telemetría diferente. El sistema de inventario termina cuando el paquete sale del almacén. El tracking post-despacho es otro dominio. |
| **Métricas de frontend** (Core Web Vitals, errores de JS, tiempos de carga) | Requeriría instrumentar un agente RUM (Real User Monitoring) en el frontend. TrackFlow no tiene RUM implementado. Se pospone. |
| **Métricas de infraestructura** (CPU, memoria, disco) | TrackFlow usa servicios gestionados (TinyDB como servicio, Railway). No tiene acceso a métricas de infraestructura a nivel de sistema operativo. |
| **Logs completos de aplicación** (request/response completos con bodies) | El volumen de log completo es 100-1000x mayor que el de eventos de telemetría. Se prioriza la captura estructurada de eventos sobre el log masivo. |
| **Eventos de integración con APIs externas** (llamadas a transportistas, tracking externo) | El volumen y la variabilidad de schemas externos harían el mantenimiento prohibitivo. Se monitorea solo desde TrackFlow hacia fuera, no la respuesta del externo. |

### 4.4 Riesgos activos y mitigaciones

| Riesgo | Probabilidad | Impacto | Mitigación |
|--------|-------------|---------|------------|
| **PII filtrada por error de instrumentación** | Media | Alto (sanción, pérdida de confianza) | Allowlist estricto con `additionalProperties: false` en cada evento. Code review obligatorio en PRs que toquen schemas. |
| **Saturación del pipeline en Black Friday** | Alta (estacional) | Alto (pérdida de eventos, datos incompletos) | Throttle configurado. Muestreo de caché. Back-pressure natural en outbound. Considerar auto-scaling del broker de eventos. |
| **Evento nuevo sin schema aprobado** | Baja | Medio (datos no válidos, rotura de consumidores) | Validación contra JSON Schema en el punto de ingestión. Rechazar eventos que no cumplan el schema. |
| **Deriva de semántica** (un campo cambia de significado sin cambiar de nombre) | Media | Medio (informes incorrectos, decisiones erróneas) | `schemaVersion` en el envelope. Si cambia la semántica de un campo, se incrementa la versión y se notifica a consumidores. |
| **Dependencia del emisor síncrono** (la aplicación espera a que se emita el evento) | Media | Medio (latencia agregada en la respuesta al usuario) | Emisión asíncrona (fire-and-forget) desde un middleware o worker. El tiempo de emisión del evento no debe sumarse al tiempo de respuesta de la API. |
| **Eventos duplicados** (el mismo evento emitido dos veces por error de red) | Media | Bajo (métricas ligeramente infladas) | `eventId` único (UUID) permite deduplicación en el consumidor (exactly-once processing). |
| **Olvido de eventos en nuevos desarrollos** (una nueva feature no emite eventos) | Alta | Medio (lagunas en el plan de telemetría) | Incluir checklist de telemetría en el template de PR. Todo endpoint nuevo debe incluir al menos un evento. |

---

*Documento generado para la Fase 3 de la serie de telemetría de TrackFlow. Todas las decisiones de canal (stream/batch) están justificadas por la urgencia de la decisión de negocio que el evento alimenta, no por preferencia técnica.*