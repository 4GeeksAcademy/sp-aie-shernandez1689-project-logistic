---
name: run-web
description: Levanta y prueba la web de TrackFlow (estática en uis/, puerto 8080) junto con su backend user-api (FastAPI, puerto 8000). Úsalo cuando pidan "levantar el proyecto", "ver la web", arrancar la API o comprobar un cambio en la UI.
---

# Levantar la web + API

La web es HTML estático en `uis/` y llama a `http://127.0.0.1:8000` (ver `uis/auth.js`, `uis/validation.js`).
El backend es `services/user-api` (FastAPI + TinyDB).

**No uses `docker-compose.yml`**: está desactualizado (referencia apps Next.js `uis/website` y `uis/backoffice` que no existen).

## Arrancar

```bash
bash .claude/skills/run-web/start.sh
```

- Usa el `.venv` de la raíz del repo (ya tiene fastapi, uvicorn, tinydb, pyjwt).
- No hay `.env`: el script genera un `JWT_SECRET_KEY` aleatorio y define `CORS_ALLOW_ORIGINS`, `ACCESS_TOKEN_EXPIRE_MINUTES` y `RESET_PASSWORD_URL`. Si existe `services/user-api/.env`, sus valores tienen prioridad.
- Logs en `${TMPDIR:-/tmp}/trackflow-run/` (o `LOG_DIR=...`).

## Verificar (conducirla, no solo arrancarla)

```bash
curl -s http://127.0.0.1:8080/ | grep -o "<title>.*</title>"   # Proyecto Logística - Inicio
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/docs   # 200
curl -s -i -X OPTIONS http://127.0.0.1:8000/applications \
  -H "Origin: http://localhost:8080" -H "Access-Control-Request-Method: POST" \
  | grep -i access-control-allow-origin                          # http://localhost:8080
```

## En GitHub Codespaces

- Abrir la web en `https://$CODESPACE_NAME-8080.app.github.dev` (el script la imprime); `localhost` no sirve desde el navegador.
- `uis/auth.js` y `uis/validation.js` detectan `*.app.github.dev` y llaman a la API en el puerto `-8000` del mismo codespace.
- El puerto **8000 debe ser público** (Ports → clic derecho en 8000 → Port Visibility → Public), si no el navegador bloquea las llamadas cross-origin. Esto lo decide el usuario: deja la API accesible para cualquiera con la URL.
- Las páginas de `uis/backoffice/` y `uis/application/app/` siguen usando `127.0.0.1:8000` por defecto (varias tienen un campo `apiBase` para cambiarlo).

## Limitaciones conocidas

- Sin `DATABASE_URL`: el esquema de inventario (PostgreSQL) se omite; usuarios/login funcionan con TinyDB.
- Sin `RESEND_API_KEY`: no se envían emails de recuperación de contraseña.
- El JWT secret cambia en cada arranque → las sesiones se invalidan al reiniciar.

## Parar

```bash
bash .claude/skills/run-web/start.sh stop
```
