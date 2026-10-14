# Auditoria de serializacion de FastAPI

Fecha: 2026-10-14. Fases 1 y 2: auditoria e implementacion.

## Fase 2: estado implementado

Los 37 endpoints declarados tienen ahora `response_model` explicito. Los 34 que devuelven JSON utilizan esquemas Pydantic; los tres DELETE declaran `response_model=None` y mantienen 204 sin cuerpo. Estado actual: **37 ✅, 0 ⚠️, 0 ❌** para los endpoints de negocio, raiz y salud. El contrato vacio de DELETE se considera completo, no un defecto pendiente.

Las rutas automaticas de OpenAPI, Swagger y ReDoc son infraestructura del framework, no handlers con decoradores propios. Se conservan sus `JSONResponse`/`HTMLResponse` nativas; no se agregan esquemas de negocio a HTML ni se reemplaza el generador OpenAPI. Su marca parcial en Fase 1 describia una limitacion de la rubrica, no un cambio de seguridad requerido.

### Contratos actuales

Todos los contratos de esta tabla estan ✅. `No aplica` significa que no hay cuerpo de entrada; `None` significa respuesta vacia, no JSON `null`. Las listas usan proyecciones dedicadas cuando el consumidor no necesita el detalle completo.

| Metodo | Ruta | Esquema de entrada | `response_model` explicito |
| --- | --- | --- | --- |
| POST | `/users` | `UserCreateRequest` | `MessageResponse` |
| POST | `/auth/login` | `LoginRequest` | `TokenResponse` |
| POST | `/auth/forgot-password` | `ForgotPasswordRequest` | `MessageResponse` |
| POST | `/auth/reset-password` | `ResetPasswordRequest` | `MessageResponse` |
| POST | `/auth/change-password` | `ChangePasswordRequest` | `MessageResponse` |
| GET | `/auth/me` | No aplica | `AuthMeResponse` |
| GET | `/users` | No aplica | `list[UserListResponse]` |
| GET | `/users/{user_id}` | No aplica | `UserResponse` |
| PUT | `/users/{user_id}` | `UserUpdateRequest` | `UserResponse` |
| DELETE | `/users/{user_id}` | No aplica | `None` |
| GET | `/profiles` | No aplica | `list[ProfileListResponse]` |
| GET | `/profiles/me` | No aplica | `ProfileResponse` |
| PUT | `/profiles/me` | `ProfileUpdateRequest` | `ProfileResponse` |
| GET | `/profiles/user/{user_id}` | No aplica | `ProfileResponse` |
| GET | `/profiles/{profile_id}` | No aplica | `ProfileResponse` |
| PUT | `/profiles/user/{user_id}` | `ProfileUpdateRequest` | `ProfileResponse` |
| DELETE | `/profiles/user/{user_id}` | No aplica | `None` |
| POST | `/applications` | `ApplicationSubmissionRequest` | `ApplicationSubmissionResponse` |
| GET | `/inventory/products` | No aplica | `list[SKUResponse]` |
| POST | `/inventory/products` | `SKUCreateRequest` | `SKUResponse` |
| GET | `/inventory/products/{id}` | No aplica | `SKUResponse` |
| POST | `/inventory/orders/inbound` | `StockEntryCreateRequest` | `StockEntryResponse` |
| POST | `/inventory/orders/outbound` | `StockExitCreateRequest` | `StockExitResponse` |
| GET | `/inventory/orders` | No aplica | `list[StockEntryOrderResponse \| StockExitOrderResponse]` |
| GET | `/` | No aplica | `RootResponse` |
| GET | `/health` | No aplica | `HealthResponse` |
| POST | `/api/incidents` | `IncidentCreate` | `Incident` |
| GET | `/api/incidents` | No aplica | `list[IncidentListResponse]` |
| GET | `/api/incidents/summary` | No aplica | `IncidentSummaryResponse` |
| GET | `/api/incidents/{incident_id}` | No aplica | `Incident` |
| PATCH | `/api/incidents/{incident_id}/status` | `IncidentStatusUpdate` | `Incident` |
| POST | `/suppliers` | `SupplierCreate` | `SupplierCreatedResponse` |
| GET | `/suppliers` | No aplica | `list[SupplierListResponse]` |
| GET | `/suppliers/{supplier_id}` | No aplica | `SupplierCreatedResponse` |
| PATCH | `/suppliers/{supplier_id}/rate` | `SupplierRateUpdate` | `SupplierCreatedResponse` |
| PATCH | `/suppliers/{supplier_id}/status` | `SupplierStatusUpdate` | `SupplierCreatedResponse` |
| DELETE | `/suppliers/{supplier_id}` | No aplica | `None` |

### Autenticacion y campos sensibles

- Registro devuelve 201 con solo `{"message": "Account created successfully."}`. No devuelve email, contrasena, hash, perfil ni metadatos de cuenta. La creacion de usuario/perfil y su rollback no cambian.
- Login devuelve solo `access_token` y `token_type`. Los nuevos JWT contienen `sub`, `role`, `exp`, sin email. Se mantiene la firma de la funcion `create_access_token` para compatibilidad con sus llamantes internos, pero su argumento email ya no se serializa. No se cambia la validacion de tokens existentes ni se revocan JWT anteriores: los ya emitidos pueden seguir conteniendo email hasta expirar.
- Forgot/reset/change password devuelven solo `MessageResponse.message`; no devuelven token de recuperacion ni credenciales. Forgot mantiene la confirmacion generica.
- `/auth/me` mantiene el email del llamante porque [la vista de perfil](../uis/auth.js#L302) lo utiliza. El perfil anidado es ahora `ProfileSummaryResponse`, no un registro persistido completo.
- Las respuestas de usuario excluyen `password`, `hashed_password`, `password_reset_token_hash` y campos internos inesperados, tambien dentro del perfil. El token de acceso de login es un dato de salida intencional, no un token interno de recuperacion.
- Los handlers de errores existentes siguen evitando reflejar valores de entrada o credenciales. Las pruebas incluyen errores de auth con email, contrasena y token extra y verifican que sus valores no aparecen en la respuesta. Tipar/documentar todos los formatos de error sigue siendo un trabajo distinto de los contratos de exito.

### Listados y decisiones sobre relaciones

| Recurso / relacion | Decision explicita | Campos y motivo |
| --- | --- | --- |
| Listado de usuarios | Proyeccion plana, sin perfil anidado | `id`, `email`, `role`, `is_active`: identificacion y gestion de cuentas para admin. No hay consumidor de listado de usuarios en la UI actual; se conserva solo esta proyeccion administrativa, sin fecha ni datos de contacto de perfil. |
| Perfil en detalle de usuario y `/auth/me` | Proyeccion anidada de contacto, no objeto completo | `name`, `phone`, `address`: usados por cuenta/perfil. Se omiten `profile.id` y `profile.user_id` redundantes con el contexto de usuario o sesion. |
| Listado de perfiles | Proyeccion plana y relacion por ID | `id`, `user_id`, `name`: permiten seleccionar perfil y cuenta en gestion administrativa. No hay consumidor de listado en la UI actual; telefono y direccion quedan en detalle. No se anida un usuario completo, por lo que `user_id` es la representacion elegida, no una clave duplicada junto a un objeto. |
| Perfil independiente | Relacion por ID, sin usuario anidado | `id`, `user_id`, `name`, `phone`, `address`: detalle de perfil. `user_id` permite identificar el propietario en las rutas administrativas; no se serializa `UserRecord`. |
| Listado de productos | Proyeccion plana de SKU | `id`, `name`, `sku`, `client_name`, `category`, `warehouse`, `current_stock`: usados por tabla de SKU y selector de movimientos. No se devuelven colecciones de movimientos anidadas. |
| Producto en listado de movimientos | Proyeccion anidada breve | `product: {name, sku}`: la [tabla de movimientos](../uis/talent-pipeline-tracker/app/backoffice/inventory/orders/page.tsx#L60) utiliza esas dos propiedades. Se retira `sku_id` porque ya existe la relacion representada; no se devuelve el SKU completo ni stock calculado. La forma anidada breve preserva el consumidor sin duplicar identificadores. |
| Creacion de entrada/salida | Relacion de producto solo por ID | `sku_id`: confirma el producto de la operacion recien creada; no hay producto anidado en estas respuestas. No se incluyen ambos modos a la vez. |
| Actor de un movimiento | Solo identificador de trazabilidad | `user_uuid`: requerido por la columna de operario/coordinador de la tabla. No se anida un usuario ni se exponen email o credenciales del actor. |
| Listado de movimientos | Proyeccion especifica de tabla | `id`, `quantity`, `warehouse`, `created_at`, `user_uuid`, `order_type`, `product`; las salidas anaden `exit_type`. Referencia y tracking quedan en la respuesta de creacion, no en el listado que no los utiliza. |
| Listado de proveedores | Proyeccion plana sin datos de contacto | `id`, `name`, `country`, `categories`, `rate_per_shipment`, `currency`, `status`: usados por [la tabla](../uis/application/app/suppliers/suppliers.js#L58). Se omiten notas, email de contacto, zona y fecha; siguen disponibles en el detalle de negocio autorizado. |
| Listado de incidencias | Proyeccion plana de tabla | `id`, `title`, `description`, `category`, `status`, `origin`, `branch`, `created_at`: usados por [el panel](../uis/application/app/incidents/panel.js#L304). Se omite `updated_at`, disponible en detalle y escritura. |

Los IDs elegidos como unica representacion de una relacion no se consideran secretos por si mismos; se conservan cuando identifican un recurso o un actor requerido por el consumidor. No se devuelve una clave foranea junto con el objeto relacionado. Las notas y descripciones de negocio son texto libre autorizado, no una garantia contra informacion sensible escrita voluntariamente por un usuario.

### Entradas de escritura y compatibilidad

Todas las escrituras usan clases de entrada distintas de la clase de salida y `extra="forbid"`. Se comparten campos de dominio por herencia donde ya existia ese patron, pero ningun handler acepta su modelo de respuesta como entrada.

- Registro acepta email, password, name, phone y address; role pasa a ser exclusivamente asignado por el servidor. Enviar `role`, incluso `user`, devuelve 422.
- Actualizacion de usuario acepta email/password y, solo con autorizacion admin, role/is_active. Los campos internos, IDs, fecha y perfil no son escribibles por esta ruta.
- Perfiles aceptan name/phone/address; el propietario se resuelve por ruta o sesion. Enviar `user_id` ya no se ignora: devuelve 422.
- Solicitudes comerciales aceptan solo sus campos de formulario; ID y fecha son generados por el servidor.
- SKU y movimientos conservan sus entradas dedicadas y restricciones existentes; IDs de movimiento, stock calculado, fechas y actor no son escribibles.
- Incidencias aceptan title/description/category/status/origin/branch; ID y timestamps se generan en el servidor. PATCH de estado acepta solo status. Los handlers ahora reciben esquemas tipados directamente y OpenAPI documenta la estructura: cuerpos ausentes o no objeto devuelven 422 en lugar del antiguo 400 manual.
- Proveedores aceptan solo campos comerciales; las entradas de tarifa y estado admiten un unico campo. ID y updated_at no son escribibles.

Las reducciones de registro, listados y perfiles anidados cambian contratos para consumidores externos que dependan de los campos retirados. Los consumidores existentes revisados mantienen sus campos necesarios; [los tipos de movimientos](../uis/talent-pipeline-tracker/types/inventory.ts) se alinearon con la nueva salida. No se implementa versionado ni migracion de consumidores externos en esta fase.

### Verificacion de Fase 2

- Python 3.12 en dos entornos temporales separados, respetando las dependencias distintas de cada servicio. No se cambiaron manifests ni locks.
- Suite raiz de User API: **123 pruebas OK**, cobertura de `app.api.auth`: **100 %**, superior al minimo requerido de 70 %.
- Suite adicional de contrasenas/correo de User API: **19 pruebas OK**.
- Suite de Operations API: **14 pruebas OK** con TestClient y TinyDB temporal; creacion, listado, detalle, PATCH, resumen y DELETE, rechazo de campos internos y filtrado de hashes/tokens inyectados.
- Una prueba AST verifica que los **37 decoradores** declaran `response_model`. OpenAPI de ambas APIs se verifica con TestClient; los modelos de request y response son referencias tipadas, no cuerpos `Any` en incidencias.
- Pruebas de auth verifican cuerpos minimos, claims de JWT sin email, filtrado de campos sensibles, `/auth/me` con email propio y 204 vacios. Las pruebas de inventario verifican la proyeccion breve y la ausencia de `sku_id` redundante en movimientos.
- Comprobacion TypeScript focalizada de las declaraciones de inventario: OK con `--strict --skipLibCheck --moduleResolution bundler`. No se ejecuto el typecheck completo de Next porque no estan instaladas sus dependencias locales; la primera comprobacion aislada sin `skipLibCheck` encontro errores en declaraciones externas de Babel/Jest/Node.
- No se uso la base de datos real ni se modificaron datos de produccion. No se hizo una prueba end-to-end en navegador ni se probo un despliegue PostgreSQL real.

## Referencia historica de Fase 1

Las tablas y hallazgos siguientes describen el estado **anterior** a la implementacion, conservado para comparar el antes/despues. No representan contratos actuales ni tareas pendientes ya resueltas.

## Autenticacion: revision prioritaria

| Metodo | Ruta | Proposito | Respuesta actual | Estado |
| --- | --- | --- | --- | --- |
| POST | `/users` | Registrar usuario y perfil | 201; `response_model=UserResponse`; instancia construida con `model_validate(user)`. Incluye `id`, `email`, `is_active`, `role`, `created_at`, `profile`; no incluye contrasenas. | ⚠️ Parcialmente serializado: revisar si el registro necesita devolver email, metadatos y perfil completo. |
| POST | `/auth/login` | Autenticar y emitir JWT | 200; `response_model=TokenResponse`; instancia con `access_token` y `token_type`. El JWT incluye `sub`, `email`, `role`, `exp`. | ⚠️ Parcialmente serializado: contrato externo minimo, pero el token expone email; justificar o reducir ese claim. |
| POST | `/auth/forgot-password` | Solicitar envio de enlace de recuperacion | 200; sin `response_model` explicito; devuelve `dict[str, str]` anotado con `message` generico, sin email ni token de recuperacion. | ⚠️ Parcialmente serializado: tipado inferido, sin esquema que exija `message`. |
| POST | `/auth/reset-password` | Restablecer contrasena mediante token | 200; sin `response_model` explicito; devuelve `dict[str, str]` anotado con `message`. | ⚠️ Parcialmente serializado: tipado inferido, sin esquema que exija `message`. |
| POST | `/auth/change-password` | Cambiar contrasena del usuario autenticado | 200; sin `response_model` explicito; devuelve `dict[str, str]` anotado con `message`. | ⚠️ Parcialmente serializado: tipado inferido, sin esquema que exija `message`. |
| GET | `/auth/me` | Obtener identidad y perfil de la sesion | 200; `response_model=AuthMeResponse`; instancia con `email`, `role`, `profile: ProfileResponse | None`. | ✅ Ya serializado: el cliente usa email para mostrar la cuenta y completar el formulario de perfil. |

No se observa retorno de `UserRecord`, `AuthUser` ni de un objeto de usuario crudo en estos handlers. `UserRecord.hashed_password` pertenece al almacenamiento, no a `UserResponse` ni `AuthMeResponse`. Un JWT firmado no cifra sus claims: no basta con que el cuerpo externo solo contenga un token para afirmar que no devuelve email.

Fuentes: [handlers auth](../services/user-api/app/api/auth.py), [registro y usuarios](../services/user-api/app/api/users.py), [modelos](../services/user-api/app/models.py), [JWT](../services/user-api/app/security.py).

### Hallazgos de auth

- **Registro: datos innecesarios para el consumidor actual.** En [uis/auth.js](../uis/auth.js#L253), el cliente espera que `performRegister` termine pero ignora su resultado; despues hace login con las credenciales que ya tiene. El email, estado, rol, fecha y perfil devueltos por registro no son necesarios para ese flujo. Recomendar una confirmacion minima o un contrato de registro especifico en una fase posterior.
- **Login: email dentro del JWT.** [create_access_token](../services/user-api/app/security.py#L44) incluye email. Tanto [User API](../services/user-api/app/api/deps.py#L20) como [Operations API](../services/api/auth.py#L56) resuelven la identidad mediante `sub` y consultan el usuario almacenado; no necesitan el claim email. El cliente guarda el token y obtiene la identidad mediante `/auth/me`. Recomendar eliminar el claim si no hay consumidores externos que lo requieran. No es una fuga de contrasena, pero si informacion personal legible.
- **Identidad: email justificado.** [La vista de cuenta y perfil](../uis/auth.js#L302) muestra `me.email` y lo usa como valor del campo de cuenta. Mantenerlo en `/auth/me` tiene un consumidor concreto, a diferencia del registro. El perfil devuelve identificadores, nombre, telefono y direccion; son datos personales y deben permanecer sujetos a autorizacion.
- **Contrasenas y tokens de recuperacion.** Ninguno de estos handlers devuelve contrasenas en claro, hashes de contrasena ni el token de recuperacion. [attach_profile](../services/user-api/app/services/users.py#L147) elimina `hashed_password` y `password_reset_token_hash`; los esquemas publicos tambien los excluyen. El token de acceso de login es intencional.
- **Recuperacion y cambio.** Los tres endpoints responden solo con `message`; la recuperacion usa confirmacion generica independientemente de la existencia de la cuenta. La anotacion `dict[str, str]` permite claves arbitrarias: no impone que exista `message` ni prohíbe una futura clave `password` o `email` con valor string. Falta un contrato de mensaje cerrado.
- **Errores de auth revisados.** Los errores explicitos de login, usuario duplicado, token de recuperacion invalido y contrasena actual incorrecta usan mensajes constantes, sin interpolar credenciales ni email. El 409 de registro informa de que una cuenta existe: riesgo de enumeracion distinto de serializacion, no corregido en esta fase.

## Alcance y criterios

Se auditan las dos aplicaciones FastAPI de produccion del repositorio:

- **User API:** [services/user-api/app/main.py](../services/user-api/app/main.py), routers `auth`, `users`, `profiles`, `applications` e `inventory`: 26 endpoints declarados.
- **Operations API:** [services/api/main.py](../services/api/main.py), routers `incidents` y `suppliers`: 11 endpoints declarados.

[services/api/app.py](../services/api/app.py) crea una aplicacion Flask independiente; sus rutas de analisis y archivos no pertenecen a estas aplicaciones FastAPI y quedan fuera del alcance. La instancia FastAPI de [tests/test_database.py](../tests/test_database.py) es un fixture, no una tercera API de produccion. No se encontraron subaplicaciones montadas ni registros dinamicos adicionales en el codigo Python revisado.

Leyenda aplicada a las respuestas de exito:

- ✅ **Ya serializado:** `response_model` explicito, proyeccion tipada adecuada para el recurso y sin campos de credenciales.
- ⚠️ **Parcialmente serializado:** contrato explicito con datos innecesarios, o contrato inferido/generico que no fija los campos de salida. FastAPI normalmente infiere `response_model` de la anotacion de retorno cuando no se declara en el decorador; por ello un `dict[str, str]` anotado no es un diccionario sin tipado. Se incluye en este estado por tener un contrato incompleto, no por ausencia total de serializacion.
- ❌ **Sin serializar:** ORM crudo sin filtro de salida o diccionario sin tipado. No se encontro ningun endpoint declarado en este estado.

**Excepcion de la rubrica:** los tres DELETE con 204 no tienen cuerpo ni necesitan esquema de datos. Se etiquetan ⚠️ por no declarar `response_model`, pero su contrato vacio es correcto; no requieren agregar un modelo JSON. Las rutas tecnicas automaticas se separan por la misma razon: HTML y redirecciones no son DTO de negocio.

Los estados ✅ indican adecuacion segun el proposito y codigo revisados, no una certificacion de necesidad de cada campo para todos los clientes posibles. Los emails de contacto de proveedores y los datos de perfil tienen proposito de negocio; no se equiparan al eco innecesario de email en registro.

## User API: resto del inventario

Auth y registro se enumeran una sola vez en la primera tabla. En esta tabla se completan los otros 20 endpoints. Todos responden JSON salvo los DELETE sin cuerpo.

| Metodo | Ruta | Proposito | `response_model` explicito | Retorno actual / contenido | Estado |
| --- | --- | --- | --- | --- | --- |
| GET | `/users` | Listar cuentas, solo admin | `list[UserResponse]` | 200; lista de instancias mediante `model_validate`; id, email, estado, rol, fecha y perfil; sin hashes. | ✅ Ya serializado |
| GET | `/users/{user_id}` | Consultar cuenta propia o como admin | `UserResponse` | 200; instancia validada desde dict seguro del servicio. | ✅ Ya serializado |
| PUT | `/users/{user_id}` | Actualizar cuenta y credenciales con control de rol | `UserResponse` | 200; instancia del usuario actualizado; no devuelve la contrasena recibida ni su hash. | ✅ Ya serializado |
| DELETE | `/users/{user_id}` | Eliminar cuenta y perfil asociado | No | 204; retorno `None`, sin cuerpo. | ⚠️ Parcialmente serializado: contrato vacio correcto |
| GET | `/profiles` | Listar perfiles, solo admin | `list[ProfileResponse]` | 200; lista de instancias validadas; id, user_id, name, phone, address. | ✅ Ya serializado |
| GET | `/profiles/me` | Consultar perfil propio | `ProfileResponse` | 200; instancia validada desde registro TinyDB. | ✅ Ya serializado |
| PUT | `/profiles/me` | Crear o actualizar perfil propio | `ProfileResponse` | 200; instancia validada del perfil resultante. | ✅ Ya serializado |
| GET | `/profiles/user/{user_id}` | Consultar perfil propio o como admin | `ProfileResponse` | 200; instancia validada. | ✅ Ya serializado |
| GET | `/profiles/{profile_id}` | Consultar perfil por id, solo admin | `ProfileResponse` | 200; instancia validada. | ✅ Ya serializado |
| PUT | `/profiles/user/{user_id}` | Actualizar perfil existente, solo admin | `ProfileResponse` | 200; instancia validada. | ✅ Ya serializado |
| DELETE | `/profiles/user/{user_id}` | Eliminar perfil, solo admin | No | 204; retorno `None`, sin cuerpo. | ⚠️ Parcialmente serializado: contrato vacio correcto |
| POST | `/applications` | Registrar solicitud comercial publica | `ApplicationSubmissionResponse` | 201; instancia con `id`, `status`, `message`; no devuelve los datos de contacto del registro. | ✅ Ya serializado |
| GET | `/inventory/products` | Listar SKU y existencias | `list[SKUResponse]` | 200; lista de DTO construidos con `_product_response`, no ORM crudo. | ✅ Ya serializado |
| POST | `/inventory/products` | Crear SKU | `SKUResponse` | 201; DTO validado con existencias calculadas. | ✅ Ya serializado |
| GET | `/inventory/products/{id}` | Consultar SKU y existencias | `SKUResponse` | 200; DTO validado. | ✅ Ya serializado |
| POST | `/inventory/orders/inbound` | Registrar entrada de stock | `StockEntryResponse` | 201; `model_validate(entry)` convierte ORM SQLModel a DTO. | ✅ Ya serializado |
| POST | `/inventory/orders/outbound` | Registrar salida de stock | `StockExitResponse` | 201; `model_validate(exit_order)` convierte ORM SQLModel a DTO. | ✅ Ya serializado |
| GET | `/inventory/orders` | Listar entradas y salidas con producto | `list[StockEntryOrderResponse \| StockExitOrderResponse]` | 200; lista ordenada de DTO; `order_type` literal distingue entrada/salida y se anida `SKUResponse`. | ✅ Ya serializado |
| GET | `/` | Mostrar estado y enlaces de servicio | No; inferido de `dict[str, str]` | 200; dict con `message`, `docs`, `health`. | ⚠️ Parcialmente serializado: no fija esas claves |
| GET | `/health` | Comprobar disponibilidad | No; inferido de `dict[str, str]` | 200; dict con `status: "ok"`. | ⚠️ Parcialmente serializado: no fija `status` |

### Proyecciones de inventario

[schemas.py](../services/user-api/app/schemas.py) define DTO separados de las tablas ORM de [models.py](../services/user-api/app/models.py). `from_attributes=True` permite validar atributos ORM; no implica devolver la tabla completa.

- `SKUResponse`: `id`, `name`, `sku`, `client_name`, `category`, `warehouse`, `current_stock`.
- `StockEntryResponse`: `id`, `sku_id`, `quantity`, `reference`, `warehouse`, `created_at`, `user_uuid`.
- `StockExitResponse`: `id`, `sku_id`, `quantity`, `exit_type`, `tracking_number`, `warehouse`, `created_at`, `user_uuid`.
- Las respuestas de listado de ordenes anaden `order_type` y `product`. `user_uuid` identifica al actor de la operacion, no contiene credenciales; revisar su necesidad si aparece un consumidor sin funcion de trazabilidad.

Fuentes: [usuarios](../services/user-api/app/api/users.py), [perfiles](../services/user-api/app/api/profiles.py), [solicitudes](../services/user-api/app/api/applications.py), [inventario](../services/user-api/app/routers/inventory.py), [raiz y salud](../services/user-api/app/main.py).

## Operations API

| Metodo | Ruta | Proposito | `response_model` explicito | Retorno actual / contenido | Estado |
| --- | --- | --- | --- | --- | --- |
| POST | `/api/incidents` | Crear incidencia, admin/manager | `Incident` | 201; instancia Pydantic construida tras validar entrada; no ORM crudo. | ✅ Ya serializado |
| GET | `/api/incidents` | Listar incidencias con filtros | `list[Incident]` | 200; lista de instancias mediante `model_validate(dict(document))`. | ✅ Ya serializado |
| GET | `/api/incidents/summary` | Resumir totales por estado, categoria, origen y sede | No; inferido de `dict[str, Any]` | 200; dict con `total` entero y mapas `status`, `category`, `origin`, `branch`; valores `Any` sin contrato estructural. | ⚠️ Parcialmente serializado: estructura y contadores no validados por esquema dedicado |
| GET | `/api/incidents/{incident_id}` | Consultar incidencia | `Incident` | 200; instancia validada desde documento TinyDB. | ✅ Ya serializado |
| PATCH | `/api/incidents/{incident_id}/status` | Cambiar estado segun transiciones, admin/manager | `Incident` | 200; instancia validada del documento actualizado. | ✅ Ya serializado |
| POST | `/suppliers` | Crear proveedor, admin/manager | `SupplierCreatedResponse` | 201; instancia con id TinyDB y campos de proveedor. | ✅ Ya serializado |
| GET | `/suppliers` | Listar proveedores con filtros | `list[SupplierCreatedResponse]` | 200; lista de instancias construidas desde documentos TinyDB. | ✅ Ya serializado |
| GET | `/suppliers/{supplier_id}` | Consultar proveedor | `SupplierCreatedResponse` | 200; instancia tipada; no documento TinyDB crudo. | ✅ Ya serializado |
| PATCH | `/suppliers/{supplier_id}/rate` | Cambiar tarifa, admin/manager | `SupplierCreatedResponse` | 200; instancia del proveedor actualizado. | ✅ Ya serializado |
| PATCH | `/suppliers/{supplier_id}/status` | Cambiar estado, admin/manager | `SupplierCreatedResponse` | 200; instancia del proveedor actualizado. | ✅ Ya serializado |
| DELETE | `/suppliers/{supplier_id}` | Eliminar proveedor, admin/manager | No | 204; retorno `None`, sin cuerpo. | ⚠️ Parcialmente serializado: contrato vacio correcto |

[Modelos de Operations](../services/api/models.py):

- `Incident`: `id`, `title`, `description`, `category`, `status`, `origin`, `branch`, `created_at`, `updated_at`. Es Pydantic, no una clase ORM; reutilizarlo para almacenamiento no elimina el filtro de `response_model`.
- `SupplierCreatedResponse`: `id`, `name`, `country`, `categories`, `rate_per_shipment`, `currency`, `status`, `service_zone`, `contact_email`, `notes`, `updated_at`. `contact_email` tiene proposito de contacto comercial; no es el email de autenticacion. Las notas y descripciones son texto libre: no hay garantia de que un usuario no introduzca informacion sensible en ellas.

Fuentes: [incidencias](../services/api/routes/incidents.py), [proveedores](../services/api/routes/suppliers.py), [montaje](../services/api/main.py).

## Rutas tecnicas automaticas

Ambas instancias usan los valores por defecto de `FastAPI`, sin desactivar OpenAPI ni documentacion. Cada fila aplica por separado a **User API y Operations API**: ocho entradas ruta/servicio, con GET y HEAD (16 combinaciones metodo/ruta/servicio), fuera del total de 37 endpoints declarados.

| Metodos | Ruta | Proposito | `response_model` explicito | Respuesta actual | Estado |
| --- | --- | --- | --- | --- | --- |
| GET, HEAD | `/openapi.json` | Publicar contrato OpenAPI | No | `JSONResponse` del esquema generado; HEAD sin cuerpo. | ⚠️ Parcialmente serializado: respuesta tecnica directa, no DTO de negocio |
| GET, HEAD | `/docs` | Servir Swagger UI | No | `HTMLResponse`; HEAD sin cuerpo. | ⚠️ Parcialmente serializado: HTML, no requiere modelo JSON |
| GET, HEAD | `/docs/oauth2-redirect` | Servir callback auxiliar de Swagger | No | `HTMLResponse`; HEAD sin cuerpo. No es endpoint propio de login. | ⚠️ Parcialmente serializado: HTML, no requiere modelo JSON |
| GET, HEAD | `/redoc` | Servir ReDoc | No | `HTMLResponse`; HEAD sin cuerpo. | ⚠️ Parcialmente serializado: HTML, no requiere modelo JSON |

Las respuestas OPTIONS del middleware CORS de User API no son endpoints declarados independientes. Los 404/405 del router tampoco lo son. La presencia de rutas automaticas y HEAD se deduce del comportamiento por defecto de FastAPI, no de una consulta en vivo en esta auditoria.

## Errores transversales

`response_model` filtra el retorno normal del handler, no el contenido de un `HTTPException` o de una `Response` directa. Los handlers de excepcion son superficies de respuesta, aunque no endpoints adicionales:

| Superficie | Respuesta actual | Observacion |
| --- | --- | --- |
| User API: validacion de peticion | 422; `JSONResponse` con `detail: [{loc, msg}]` | Omite `input`, cuerpo recibido y `ctx`; mensajes normalizados. No refleja contrasenas ni email recibido como valores. |
| Operations API: validacion de peticion | 422; `JSONResponse` con `errors: [{field, message}]` | Omite `input` y valores de peticion; formato distinto de User API. |
| Ambas: excepciones no controladas | 500; `JSONResponse` con `detail` generico | No devuelve traceback al cliente. |
| Ambas: `HTTPException` | Estado correspondiente; JSON `detail` string o dict, segun handler | Sin DTO de error propio; algunos errores de incidencias usan `{field, message}`. Auth usa mensajes constantes. |

No se declaran modelos de error mediante `responses` en los decoradores revisados. La documentacion 422 automatica de FastAPI puede no coincidir con estos handlers personalizados. Los formatos de error requieren contratos propios en una fase posterior; no cambian la clasificacion de exito de cada endpoint. No se garantiza ausencia de informacion sensible arbitraria en rutas, nombres de campos o texto libre.

## Resumen y siguientes pasos

| Servicio | Endpoints declarados | ✅ | ⚠️ | ❌ |
| --- | --- | --- | --- | --- |
| User API | 26 | 17 | 9 | 0 |
| Operations API | 11 | 9 | 2 | 0 |
| Total | 37 | 26 | 11 | 0 |

De los 11 parciales, tres son DELETE con contrato vacio correcto, seis son diccionarios con tipado inferido y dos requieren minimizacion en auth. Las rutas tecnicas se contabilizan por separado, no como defectos de negocio.

Prioridades propuestas, sin implementar cambios en esta fase:

1. Reducir la respuesta de registro y retirar el claim email de login, previa comprobacion de consumidores externos.
2. Declarar esquemas explicitos para mensajes de contrasena, raiz, salud y resumen de incidencias; fijar claves, tipos y campos permitidos.
3. Documentar y tipar errores 422/HTTPException con contratos que coincidan con las respuestas reales, sin reflejar credenciales.
4. Anadir pruebas de respuesta que inyecten campos de credenciales en registros de prueba y comprueben su exclusion, tambien en respuestas de error. Conservar los 204 sin cuerpo.

### Verificacion y limites

Revision estatica de montajes, todos los handlers declarados, esquemas de respuesta, servicios de usuario, creacion/lectura de JWT y consumidor auth. Validacion mediante AST de cobertura de pares metodo/ruta y de `response_model`/anotaciones de retorno, sin ejecutar handlers ni escribir en bases de datos.

El interprete disponible no tiene FastAPI, Pydantic, TinyDB ni PyJWT instalados. No se ejecuto TestClient, no se consulto OpenAPI en vivo y no se verificaron respuestas reales HTTP; los tipos inferidos y rutas automaticas se describen segun las convenciones de FastAPI. Esta auditoria no afirma haber probado todas las ramas de error ni la autorizacion completa.