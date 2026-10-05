# Propuesta de arquitectura backend para TrackFlow

## 1. Contexto y objetivo

TrackFlow coordina logística de última milla, almacenes y devoluciones en Estados Unidos y España. Tiene unos 130 empleados, dos almacenes con sistemas distintos, un ERP heredado, ocho transportistas y un equipo tecnológico de siete personas. Parte de sus procesos actuales dependen de hojas de cálculo, consultas manuales e integraciones punto a punto sin documentar.

La propuesta establece una base backend que unifique operaciones y datos sin exigir una migración simultánea de todos los sistemas existentes. El primer alcance debería priorizar inventario, pedidos, envíos y devoluciones; los sistemas de almacén, ERP y transportistas se integrarían gradualmente detrás de adaptadores. La disponibilidad, la trazabilidad de cambios y el funcionamiento en dos países son requisitos del diseño, no añadidos posteriores.

## 2. Patrón arquitectónico recomendado

Se recomienda una **arquitectura en capas organizada como monolito modular por dominios** (también llamada monolito modular o arquitectura hexagonal aplicada dentro de un servicio). La API y los módulos se despliegan inicialmente como una aplicación FastAPI, pero cada dominio conserva límites explícitos y dependencias controladas.

Dentro de cada módulo, la responsabilidad se separa en:

- **API/presentación:** routers HTTP, validación de entrada y conversión a respuestas.
- **Aplicación:** casos de uso y coordinación de transacciones o procesos.
- **Dominio:** reglas e invariantes propias, independientes de FastAPI y de proveedores externos.
- **Infraestructura:** persistencia, clientes de ERP/SGA y adaptadores de transportistas.

Las capas no obligan a crear un archivo por cada función: se aplican donde mantengan una frontera útil. La API no debería contener reglas de negocio, y los módulos de dominio no deberían llamar directamente a servicios externos.

### Por qué encaja con TrackFlow

- **Equipo y coste operativo:** siete personas pueden desarrollar y desplegar un servicio coherente sin asumir desde el inicio la carga de observabilidad, redes, contratos y despliegues de muchos microservicios.
- **Integraciones heterogéneas:** los dos SGA, el ERP antiguo y las APIs de transportistas pueden aislarse en adaptadores sustituibles. Un cambio en el proveedor de Zaragoza, por ejemplo, no debería propagarse a las reglas de inventario.
- **Procesos conectados:** un pedido puede reservar inventario, generar un envío y, posteriormente, originar una devolución. Los límites de dominio permiten coordinar ese flujo sin duplicar reglas ni acoplar el código a un único sistema heredado.
- **Evolución progresiva:** la modularidad permite extraer más adelante un dominio con necesidades independientes de escala o disponibilidad —por ejemplo, tracking o procesamiento de eventos— sin comenzar con la complejidad distribuida.
- **Operación binacional:** reglas, configuraciones y datos deben contemplar país, almacén, cliente y transportista. No se deben codificar como supuestos globales valores que cambian entre EE. UU. y España.

No se recomienda empezar con microservicios: los equipos y los sistemas actuales aún necesitan primero contratos estables y datos compartidos fiables. Tampoco bastaría una arquitectura en capas puramente técnica (por ejemplo, una carpeta global para todos los modelos y otra para todos los servicios), porque con el crecimiento sería difícil saber qué módulo es responsable de una regla logística.

## 3. Estructura propuesta

El repositorio transversal ya distingue `uis/`, `services/`, `data/` y `workflows/`. Se propone mantener esa separación y crear el backend bajo `services/`, con despliegue independiente del frontend aunque ambos permanezcan en el monorepo.

```text
services/
└── trackflow-api/
    ├── app/
    │   ├── main.py
    │   ├── api/
    │   │   └── v1/
    │   │       ├── router.py
    │   │       └── deps.py
    │   ├── core/
    │   │   ├── config.py
    │   │   ├── security.py
    │   │   ├── errors.py
    │   │   └── observability.py
    │   ├── db/
    │   │   ├── session.py
    │   │   └── migrations/
    │   ├── modules/
    │   │   ├── clients/
    │   │   ├── warehouse/
    │   │   ├── orders/
    │   │   ├── shipments/
    │   │   ├── returns/
    │   │   └── reporting/
    │   └── integrations/
    │       ├── erp/
    │       ├── wms/
    │       └── carriers/
    ├── tests/
    │   ├── unit/
    │   ├── integration/
    │   └── api/
    └── README.md
```

Cada módulo de `modules/` puede contener, según la complejidad real, `router.py`, `schemas.py`, `models.py`, `service.py` o `use_cases.py`, `repository.py` y pruebas. `schemas.py` define los contratos de entrada y salida de la API; `models.py` representa las entidades persistidas. No deben confundirse: un cambio interno de base de datos no debería modificar inadvertidamente la respuesta HTTP.

### Criterio de separación

La carpeta `modules/` se organiza **por dominio de negocio**, no por tipo de archivo. Así, las reglas de devoluciones y sus casos de uso quedan juntas, en vez de repartirse por carpetas globales de routers, servicios y modelos. `core/` se reserva para capacidades transversales pequeñas (configuración, seguridad y observabilidad), no para lógica logística. `integrations/` contiene adaptadores externos y normaliza formatos de proveedores; los módulos de negocio dependen de interfaces propias, no de SDKs específicos.

Responsabilidades iniciales:

- **clients:** marcas, acuerdos y reglas de servicio asociadas al cliente.
- **warehouse:** almacenes, ubicaciones, existencias y movimientos de inventario.
- **orders:** recepción, validación y estado de pedidos, incluida la procedencia de cada sistema.
- **shipments:** selección de transportista, etiquetas, seguimiento e incidencias.
- **returns:** elegibilidad y reglas por cliente, inspección, decisión y flujo de devolución.
- **reporting:** consultas y agregados para paneles e informes; no debe convertirse en el lugar donde se ejecutan transacciones operativas.
- **integrations:** conectores con ERP, SGA y transportistas, incluidos reintentos, límites de llamadas y traducción de estados externos a estados internos.

Los procesos que tarden o dependan de terceros (ingesta de pedidos, sincronización de tracking, generación de etiquetas y notificaciones) deben ejecutarse mediante tareas en segundo plano, con reintentos idempotentes y registro de estado. La petición HTTP inicia o consulta el proceso; no debe quedar abierta mientras se espera a múltiples proveedores.

## 4. FastAPI: convenciones y aplicación al diseño

FastAPI no impone una estructura única, pero su guía para aplicaciones grandes recomienda dividir la aplicación en módulos y componer routers con `APIRouter` e `include_router`. Es habitual separar routers, modelos de datos, configuración, dependencias y pruebas. Pydantic se usa normalmente para validar y serializar los contratos de API; la configuración se carga desde variables de entorno y los secretos no se guardan en el repositorio.

La propuesta adopta esas convenciones, pero sitúa los routers dentro de su dominio (`modules/returns/router.py`, por ejemplo) para conservar la propiedad funcional. `api/v1/router.py` solo agrega los routers de la versión vigente y `main.py` crea la aplicación, conecta el router raíz y configura el ciclo de vida y middleware. Las dependencias comunes de autenticación o acceso a base de datos se declaran de forma reutilizable, evitando que cada endpoint implemente su propia variante.

Las respuestas y entradas públicas se validan con esquemas explícitos. Las entidades de persistencia, las reglas de negocio y los esquemas HTTP tienen propósitos distintos y no se deben exponer directamente por conveniencia. Las migraciones de base de datos y las pruebas de integración completan la estructura: prueban el contrato con la base de datos y los adaptadores sin depender de llamadas reales a proveedores en las pruebas unitarias.

Se versiona el prefijo de la API (`/api/v1`) para permitir cambios incompatibles gradualmente. La versión inicial no debe duplicar routers sin necesidad: se introduce `v2` cuando haya un cambio de contrato que no pueda mantenerse compatible.

## 5. Routers y endpoints por dominio

Los routers se agrupan por recurso y capacidad de negocio, comparten `/api/v1` y usan nombres consistentes. Las rutas operativas requieren autenticación y autorización por rol, cliente y ubicación; la API pública de tracking debe exponer únicamente la información necesaria mediante identificadores difíciles de adivinar o tokens limitados.

| Router | Rutas representativas | Criterio |
| --- | --- | --- |
| `clients` | `GET/POST /clients`, `GET/PATCH /clients/{client_id}` | Gestión interna de marcas y configuración de servicios; acceso restringido. |
| `warehouse` | `GET /warehouses`, `GET /inventory`, `GET /inventory/{sku}`, `POST /inventory/adjustments` | Consulta de stock por SKU, almacén y país; cambios de inventario explícitos y auditables. |
| `orders` | `POST /orders`, `GET /orders/{order_id}`, `GET /orders` | Ingesta y consulta del ciclo de pedido; operaciones de carga masiva pueden ser asíncronas. |
| `shipments` | `POST /shipments/quote`, `POST /shipments`, `GET /shipments/{shipment_id}`, `GET /shipments/{shipment_id}/events` | Selección de servicio, creación y seguimiento interno del envío. |
| `tracking` | `GET /public/tracking/{tracking_token}` | Consulta pública mínima para destinatarios, sin revelar datos de la marca ni del almacén. |
| `returns` | `POST /returns`, `GET /returns/{return_id}`, `POST /returns/{return_id}/decision`, `POST /returns/{return_id}/inspection` | Solicitud, revisión/decisión e inspección. La decisión automática debe conservar motivo, regla aplicada y posibilidad de revisión humana. |
| `reporting` | `GET /reports/operations`, `GET /reports/carriers`, `GET /reports/returns` | Lecturas agregadas filtrables por país, cliente y periodo; acceso según el ámbito del usuario. |
| `webhooks` | `POST /webhooks/carriers/{provider}` | Recepción de eventos externos con autenticación, validación de firma, deduplicación y registro antes de actualizar el estado interno. |

Los filtros comunes —por ejemplo, país, almacén, cliente y rango temporal— deben tener semántica documentada y autorización en el servidor; no basta con ocultar opciones en la interfaz. Los cambios sensibles, como ajustes de stock y decisiones de devolución, deben dejar actor, fecha, motivo y valores anteriores/nuevos en un registro de auditoría.

## 6. Frontend y backend como sistemas separados

El frontend web y la API son aplicaciones con responsabilidades, dependencias y ciclos de despliegue distintos. La interfaz actual vive bajo `uis/`; el backend propuesto viviría bajo `services/trackflow-api/`. **Se recomienda mantenerlos en el monorepo por ahora**, porque el equipo es pequeño, la plantilla ya organiza así sus componentes y los cambios de contratos pueden revisarse en conjunto. Monorepo no significa aplicación única: cada sistema tiene sus propias dependencias, pruebas, configuración y despliegue. Si los equipos, permisos o ciclos de publicación divergen sustancialmente, podrían separarse en repositorios sin cambiar el contrato HTTP.

La comunicación se realiza mediante una API HTTP JSON documentada con OpenAPI. El frontend consume contratos versionados y no accede directamente a la base de datos ni a las credenciales de transportistas. Las modificaciones compatibles se coordinan con el esquema OpenAPI; los cambios incompatibles requieren una transición de versión y periodo de compatibilidad. Las tareas largas se exponen como operaciones con estado consultable, no como solicitudes que dependan de una conexión abierta.

Configuración y seguridad entre entornos:

- La URL base de la API se configura por entorno en el frontend. Solo los valores explícitamente públicos pueden incluirse en el bundle del navegador; tokens privados, claves de proveedores y credenciales permanecen en el backend o en un gestor de secretos.
- El backend obtiene conexión a base de datos, credenciales y configuración regional desde variables de entorno o el gestor de secretos del entorno. Se mantienen valores distintos para desarrollo, pruebas y producción, y no se versionan secretos.
- CORS se configura en el backend con una lista explícita de orígenes de frontend por entorno, métodos y cabeceras mínimos. No se debe habilitar `*` junto con credenciales. CORS controla qué navegadores pueden leer respuestas; no sustituye autenticación ni autorización.
- La autenticación debe identificar tanto al usuario/servicio como su alcance. Las rutas B2B y las consultas públicas de destinatarios tienen permisos y exposición de datos distintos.
- Para dos países, país y zona horaria se modelan explícitamente; las fechas se almacenan en UTC y se presentan según el contexto local. La retención y el acceso a datos personales deben revisarse para los marcos aplicables en EE. UU. y la UE (incluido GDPR), así como para contratos con clientes.

## 7. Riesgos y puntos de atención

1. **El monolito modular puede degradarse a un monolito acoplado.** Si los endpoints escriben directamente en tablas de otros dominios o se colocan todas las reglas en `core/`, los cambios vuelven a ser difíciles de aislar. Mitigación: definir propietarios de datos y casos de uso, revisar dependencias entre módulos y probar contratos en los límites.
2. **La normalización de integraciones puede ocultar diferencias operativas.** Equiparar estados o unidades de los SGA y transportistas sin conservar su valor de origen puede producir stock o tracking incorrectos. Mitigación: guardar identificador/proveedor y payload de origen cuando corresponda, documentar el mapeo, validar por país y monitorizar discrepancias.
3. **La sincronización asíncrona puede duplicar operaciones o perder eventos.** Reintentos de webhooks y llamadas de red fallidas pueden crear etiquetas, movimientos o decisiones repetidos. Mitigación: claves de idempotencia, deduplicación, colas durables, reintentos acotados y cola de fallos con alertas.
4. **Una API compartida puede exponer datos entre marcas o países.** Un filtro enviado por el frontend no es una barrera de seguridad. Mitigación: autorización en cada consulta, pruebas de aislamiento multi-cliente, mínimo privilegio y auditoría de accesos.
5. **Los contratos pueden divergir entre frontend y backend.** Si cada equipo interpreta los campos de forma distinta, se rompen flujos al desplegar. Mitigación: OpenAPI como contrato, validación automatizada y cambios compatibles antes de retirar campos.
6. **La extracción prematura de microservicios añade coste sin resolver datos inconsistentes.** Mitigación: extraer un módulo solo ante una necesidad medible de escala, disponibilidad, aislamiento de fallos o autonomía de equipo, después de estabilizar sus límites y contratos.

## 8. Referencias técnicas

- FastAPI, aplicaciones más grandes y `APIRouter`: [Bigger Applications - Multiple Files](https://fastapi.tiangolo.com/tutorial/bigger-applications/).
- FastAPI, configuración: [Settings and Environment Variables](https://fastapi.tiangolo.com/advanced/settings/).
- FastAPI, configuración CORS: [CORS (Cross-Origin Resource Sharing)](https://fastapi.tiangolo.com/tutorial/cors/).
- Pydantic, gestión de configuración: [Pydantic Settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/).

Estas referencias describen convenciones y mecanismos del framework; la organización por dominios y la elección de monolito modular son decisiones de diseño para las necesidades concretas de TrackFlow, no requisitos impuestos por FastAPI.