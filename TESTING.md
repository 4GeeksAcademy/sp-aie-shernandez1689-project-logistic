# Plan de pruebas

Este documento registra los casos previstos antes de añadir nuevas pruebas automatizadas. En este cambio solo se documenta el plan: no se crean ni modifican tests. El alcance incluye los endpoints de `services/user-api`, la API FastAPI de operaciones (`services/api/main.py`) y la API Flask de análisis/UI (`services/api/app.py`).

## Cómo ejecutar las pruebas

Desde la raíz del proyecto, Python 3.12 o posterior y `uv`:

```bash
uv run pytest
uv run pytest --cov
```

`pyproject.toml` administra las dependencias de ejecución y prueba. Pytest descubre los módulos de `tests/` en la raíz y mide `app.api.auth` con un umbral mínimo de 70%. La suite heredada `services/user-api/tests/test_passwords.py` sigue ejecutándose por separado desde ese servicio con `python -B -m unittest discover -s tests -v`; las pruebas nuevas de autenticación usan una base TinyDB en memoria y simulan el correo.

## Suites y criterios

| Suite prevista | Cobertura | Motivo |
| --- | --- | --- |
| User API: autenticación y cuentas | Registro, login, JWT, recuperación/cambio de contraseña | Protege credenciales, evita revelar si una cuenta existe y verifica límites de acceso y consumo de tokens. |
| User API: perfiles y solicitudes | CRUD de perfiles y alta de solicitudes comerciales (plan futuro) | Comprueba la relación usuario-perfil, validación de datos y recepción del lead. |
| Operations API: incidencias | Alta, consulta, filtros, resumen y transiciones de estado | Asegura trazabilidad operativa, agregaciones y reglas del ciclo de vida. |
| Operations API: proveedores | CRUD disponible, filtros, tarifas, estados y roles | Protege los datos de proveedores y las reglas de país, moneda y permisos. |
| Flask: análisis CSV y resultados | Carga CSV, consulta del último análisis y exportación | Cubre errores comunes de carga y consistencia entre análisis, JSON y CSV. |
| Flask: páginas y recursos | Rutas de sitio, dashboard y backoffice heredado | Verifica que los recursos publicados carguen y que archivos inexistentes no se expongan. |

Las pruebas de autenticación ya implementadas usan TinyDB en memoria y datos controlados. Los flujos de correo se simulan, sin enviar mensajes reales. Las pruebas todavía planificadas usarán tokens de roles y CSV fixtures pequeños y deterministas.

## Casos planeados por endpoint

En cada fila se especifican camino feliz, límite y fallo. Para endpoints protegidos, el caso de fallo incluye ausencia o invalidez de credenciales; para endpoints públicos incluye datos mal formados o inválidos. Los códigos mencionados son expectativas para verificar contra la implementación.

### User API: `services/user-api`

| Endpoint | Camino feliz | Caso límite | Modo de fallo |
| --- | --- | --- | --- |
| `GET /` | Devuelve mensaje, docs y enlace de health. | Comprueba que la respuesta no requiere configuración de cuenta. | Error inesperado devuelve 500 sin detalles internos. |
| `GET /health` | Devuelve `status: ok`. | Repite la llamada sin estado previo. | Error inesperado devuelve 500. |
| `POST /auth/login` | Credenciales válidas devuelven Bearer JWT. | Usuario inactivo no puede iniciar sesión. | Email/contraseña incorrectos: 401; cuerpo inválido: 422. |
| `POST /auth/forgot-password` | Cuenta existente programa el envío y responde con mensaje genérico. | Email válido inexistente y fallo del proveedor conservan la misma respuesta pública. | Email inválido o cuerpo incompleto: 422; no debe filtrarse la existencia de la cuenta. |
| `POST /auth/reset-password` | Token vigente cambia la contraseña y queda consumido. | Segundo uso, token anterior tras nueva solicitud, token vencido o contraseña en el límite válido. | Token inválido/de sesión: 400; contraseña fuera de límites o JSON inválido: 422. |
| `POST /auth/change-password` | Token de sesión y contraseña actual correctos actualizan la contraseña. | Verifica la longitud máxima en bytes y que el token de recuperación pendiente quede invalidado. | Sin token/token inválido: 401; contraseña actual errónea: 400; cuerpo o contraseña inválidos: 422. |
| `GET /auth/me` | Devuelve email, rol y perfil del usuario autenticado. | Usuario sin perfil devuelve `profile: null`. | Sin token, token manipulado/vencido o usuario inexistente/inactivo: 401. |
| `POST /users` | Crea usuario con rol `user` y perfil opcional; contraseña almacenada como hash. | Alta sin campos opcionales y contraseña de longitud mínima. | Email duplicado: 409; rol privilegiado, email inválido o contraseña corta: 422. |
| `GET /users` | Admin obtiene la lista sin hashes de contraseña. | Lista vacía devuelve `[]`. | Sin token: 401; usuario/manager no admin: 403. |
| `GET /users/{user_id}` | Propietario o admin obtiene el usuario. | Admin consulta un ID inexistente: 404. | Sin token: 401; otro usuario: 403; ID ausente: 404. |
| `PUT /users/{user_id}` | Propietario cambia email/contraseña; admin actualiza rol o estado. | Payload vacío no altera los campos; respeta `exclude_none`. | Sin token: 401; ajeno o cambio de rol/estado no admin: 403; email duplicado: 409; usuario ausente: 404; datos inválidos: 422. |
| `DELETE /users/{user_id}` | Propietario o admin elimina cuenta y perfil vinculado. | Comprueba eliminación cuando la cuenta no tenía perfil. | Sin token: 401; usuario ajeno: 403; ID inexistente: 404. |
| `GET /profiles` | Admin lista perfiles. | Sin perfiles devuelve `[]`. | Sin token: 401; no admin: 403. |
| `GET /profiles/me` | Usuario obtiene su perfil. | Perfil faltante devuelve 404. | Sin token o token inválido: 401. |
| `PUT /profiles/me` | Usuario crea o actualiza su propio perfil. | Campos opcionales omitidos o enviados como `null` no rompen el perfil. | Sin token: 401; tipos/formato inválidos: 422. |
| `GET /profiles/user/{user_id}` | Propietario o admin obtiene el perfil del usuario. | Perfil inexistente devuelve 404. | Sin token: 401; usuario ajeno que no sea admin: 403. |
| `GET /profiles/{profile_id}` | Admin obtiene un perfil por ID. | ID válido pero inexistente devuelve 404. | Sin token: 401; no admin: 403. |
| `PUT /profiles/user/{user_id}` | Admin actualiza un perfil existente. | Campos opcionales ausentes mantienen sus valores. | Sin token: 401; no admin: 403; perfil inexistente: 404; payload inválido: 422. |
| `DELETE /profiles/user/{user_id}` | Admin elimina el perfil existente. | Verifica el estado posterior con consulta por usuario. | Sin token: 401; no admin: 403; perfil inexistente: 404. |
| `POST /applications` | Solicitud completa y válida se almacena y devuelve `received`. | Website y comentarios opcionales omitidos; listas con espacios se limpian; un servicio seleccionado es suficiente. | Faltan campos, email/URL inválidos, contacto sin nombre y apellido, servicios vacíos o privacidad falsa: 422. |

### Operations API FastAPI: `services/api/main.py`

| Endpoint | Camino feliz | Caso límite | Modo de fallo |
| --- | --- | --- | --- |
| `POST /api/incidents` | Admin/manager crea incidencia válida y recibe el registro con ID y fechas. | Valores opcionales/de estado por defecto; título en longitud mínima/máxima. | Sin token: 401; rol `user`: 403; cuerpo no-objeto: 400; campos/enum inválidos o extras: 422. |
| `GET /api/incidents` | Usuario autenticado lista incidencias. | Sin resultados y filtros combinados que no coinciden devuelven `[]`. | Sin token: 401; filtro enum inválido: 422. |
| `GET /api/incidents/summary` | Devuelve total y conteos por estado, categoría, origen y sede. | Base vacía produce cero en todas las dimensiones; suma de conteos coincide con total. | Sin token: 401. |
| `GET /api/incidents/{incident_id}` | Usuario autenticado obtiene una incidencia existente. | Incidencia creada puede recuperarse por ID. | Sin token: 401; ID inexistente: 404. |
| `PATCH /api/incidents/{incident_id}/status` | Admin/manager transiciona `open → in_progress` y `in_progress → resolved`. | También permite transiciones válidas a `discarded`; `updated_at` avanza. | Sin token: 401; rol `user`: 403; ID ausente: 404; transición desde estado final/no permitida: 400; body o enum inválido: 400/422 según forma del JSON. |
| `POST /suppliers` | Admin/manager crea proveedor válido. | Tarifa positiva mínima y valores de estado aceptados. | Sin token: 401; rol `user`: 403; campos/categorías inválidos, tarifa no positiva o moneda incompatible con país: 422. |
| `GET /suppliers` | Usuario autenticado lista proveedores. | Sin resultados; filtros válidos por país y categoría. | Sin token: 401; país no admitido: 422. |
| `GET /suppliers/{supplier_id}` | Usuario autenticado obtiene un proveedor existente. | ID numérico no existente devuelve 404. | Sin token: 401; ID no numérico: 422; proveedor ausente: 404. |
| `PATCH /suppliers/{supplier_id}/rate` | Admin/manager actualiza tarifa y fecha de modificación. | Tarifa positiva mínima permitida. | Sin token: 401; rol `user`: 403; proveedor ausente: 404; tarifa cero/negativa o payload inválido: 422. |
| `PATCH /suppliers/{supplier_id}/status` | Admin/manager cambia entre estados admitidos. | Actualiza el mismo estado sin alterar otros datos. | Sin token: 401; rol `user`: 403; proveedor ausente: 404; estado/body inválido: 422. |
| `DELETE /suppliers/{supplier_id}` | Admin/manager elimina proveedor y responde 204 sin cuerpo. | Segunda eliminación devuelve 404. | Sin token: 401; rol `user`: 403; ID ausente: 404; ID no numérico: 422. |

### Flask: `services/api/app.py`

| Endpoint | Camino feliz | Caso límite | Modo de fallo |
| --- | --- | --- | --- |
| `GET /` | Sirve la página principal. | Respuesta contiene HTML y tipo de contenido apropiado. | Método no permitido devuelve 405; archivo inexistente devuelve 404. |
| `GET /application.html` | Sirve la página de solicitud. | HTML vacío/no alterado por llamadas previas al analizador. | Método no permitido: 405; archivo inexistente: 404. |
| `GET /styles.css` | Sirve CSS. | Contenido con tipo `text/css`. | Método no permitido: 405; archivo inexistente: 404. |
| `GET /validation.js` | Sirve JavaScript de validación. | Contenido con tipo JavaScript. | Método no permitido: 405; archivo inexistente: 404. |
| `GET /logo-logistica.svg` | Sirve el logo. | Contenido SVG no vacío. | Método no permitido: 405; archivo inexistente: 404. |
| `GET /web/` | Sirve el inicio del dashboard. | Página accesible sin archivo/resultado de análisis previo. | Método no permitido: 405; archivo inexistente: 404. |
| `GET /web/{filename}` | Sirve un recurso existente del dashboard. | Nombre de recurso anidado permitido dentro del directorio web. | Recurso inexistente o intento de salir del directorio: 404. |
| `GET /backoffice/` | La ruta heredada sirve el inicio del dashboard. | Mismo contenido funcional que `/web/`. | Método no permitido: 405; archivo inexistente: 404. |
| `GET /backoffice/{filename}` | La ruta heredada sirve recursos existentes del dashboard. | Recurso anidado permitido dentro del directorio web. | Recurso inexistente o intento de salir del directorio: 404. |
| `POST /api/inbcidents/analyze` | Alias heredado procesa CSV válido y devuelve resumen con verificación de contexto. | CSV solo con cabecera o con filas inválidas produce resumen consistente según el analizador. | Falta `file`, nombre/contenido vacío, encoding no UTF-8 o CSV inválido: 400. |
| `POST /api/incidents/analyze` | Procesa CSV válido y devuelve el mismo contrato que el alias heredado. | CSV con cero registros válidos conserva el esquema de resumen. | Falta `file`, archivo vacío, encoding inválido o columnas requeridas ausentes: 400. |
| `GET /api/incidents/results/latest` | Devuelve JSON del último análisis. | Nuevo análisis reemplaza el anterior. | Antes de analizar: 404; no debe exponer un resultado previo inexistente. |
| `GET /api/incidents/results/export` | Devuelve CSV descargable del mismo análisis y nombre `resultados.csv`. | CSV válido con solo cabecera o métricas vacías conserva cabeceras. | Antes de analizar: 404. |

## Prioridad y razones de inclusión

Se priorizan autorización, validación de entradas, integridad de datos y estados de recursos porque los endpoints gestionan credenciales, perfiles y operaciones internas. Los límites cubren valores mínimos/máximos, datos opcionales, listas vacías, filtros sin coincidencias, estados terminales y ausencia de resultados; son puntos donde suelen aparecer errores que un camino feliz no detecta. Los fallos de autenticación/autorización se repiten por endpoint protegido para comprobar que no exista una ruta accidentalmente pública. En las APIs se validarán también los códigos de error y el formato estable del cuerpo de respuesta.

## TypeScript: Jest

Desde la raíz, ejecutar `npx jest --coverage`. Jest usa `jest.config.cjs` y `ts-jest`; `src/utils/validations.test.ts` cubre éxito y fallo de `validateProduct`, `validateShipment` y `validateCarrier`. No hay utilidades TypeScript de autenticación para generar/validar tokens ni para hashear contraseñas en este repositorio; esas funciones pertenecen al backend Python, así que no se duplican ni se inventan para esta suite.