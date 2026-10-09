# Progress

## Estado actual del desarrollo
- Se creo y estructuro el banco de memoria del proyecto en memory-bank/.
- Se alineo projectbrief.md con el negocio TrackFlow y el alcance del hito 1 (website corporativo + captacion de leads).
- Se alineo techContext.md con stack y restricciones obligatorias: Tailwind, validaciones completas, accesibilidad, SEO y Schema.org.
- Se definio una skill operativa para sincronizar progreso de trabajo de forma recurrente.
- services/user-api: modelo User (TinyDB) con RoleEnum (admin/manager/user), capa de servicios y CRUD /users.
  - POST /users crea siempre el Profile vinculado (con rollback del User si falla) y fuerza role=user.
  - PUT /users/{id}: solo el propio usuario o admin; role e is_active solo modificables por admin.
  - DELETE /users/{id} elimina tambien el perfil vinculado.
  - Smoke test con TestClient sobre DB temporal: OK (201/409/422/401/403/204/404 segun caso).
- services/user-api: Profile (id, user_id, name, phone, address) 1:1 con User; GET/PUT /profiles/me verificados (401 sin token, el dueño solo edita su perfil, user_id del body ignorado).
- services/user-api: POST /auth/login (JWT HS256) y GET /auth/me (email, role, profile) verificados; login valida password antes de revelar estado inactivo.
- services/user-api: get_current_user devuelve 401 + WWW-Authenticate ante cualquier fallo (cabecera, firma, expiracion, usuario inexistente o inactivo); JWT exige claims exp y sub. JWT_SECRET_KEY y ACCESS_TOKEN_EXPIRE_MINUTES se leen de .env (ignorado por git; plantilla en .env.example).
- Proteccion de rutas: en user-api solo son publicas POST /auth/login, POST /users, POST /applications, / y /health; el resto devuelve 401 sin token y 403 sobre recursos ajenos.
- services/api (/suppliers, 6 rutas): nueva dependencia auth.get_current_user que valida el JWT de user-api (mismo JWT_SECRET_KEY) y busca el usuario en la DB de user-api (USER_DB_PATH). Lectura: cualquier usuario autenticado; escritura (POST/PATCH/DELETE): solo admin o manager (403 para user). suppliers.js envia el token guardado en localStorage.
- get_current_user (ambos servicios) usa HTTPBearer(auto_error=False): Swagger /docs muestra boton Authorize (un parametro Header "Authorization" es ignorado por OpenAPI). Verificado con servidores reales (copia en /tmp): registro 201 -> login -> /auth/me y /suppliers 200; sin token, mal formado, manipulado o expirado -> 401.
- Verificacion final de criterios de aceptacion (13 puntos) contra servidores reales: 44/44 checks OK. Supabase/PostgreSQL no presente en el repo; User/Profile solo en TinyDB.

## Proximos pasos previstos
1. Definir el idioma base del sitio y confirmar si la version bilingue entra en este hito.
2. Implementar la landing en el orden de secciones requerido por CONTEXT.md.
3. Implementar formulario con todos los campos obligatorios y sus validaciones especificas.
4. Integrar mensajes de error exactos, warning de bajo volumen y mensaje de exito de envio simulado.
5. Aplicar criterios de calidad transversal: responsive, accesibilidad y SEO on-page.
6. Agregar y validar markup Schema.org Organization con datos de TrackFlow.
