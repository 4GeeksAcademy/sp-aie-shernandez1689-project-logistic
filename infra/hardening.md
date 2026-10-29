# Endurecimiento del servidor — TrackFlow

**Rama:** `feature/owasp-top10-audit` · **Fecha:** 2026-10-29 · **Sistemas:** Los Ángeles (EE. UU.) y Zaragoza (España)
**Referencia:** Brifing-seguridad.md §7 — "Baseline servidor / red: documenta SSH, usuario no-root y firewall. Público: HTTPS API + frontend; SSH ops."

Este documento es la **evidencia de endurecimiento** de los puntos 1, 2 y 3 de la verificación de seguridad:

| # | Requisito | Sección aquí |
|---|---|---|
| 1 | El servidor no permite acceso SSH directo como root | §2 |
| 2 | Existe un usuario dedicado no-root para tareas operativas, con permisos de carpetas explícitos | §3 |
| 3 | El firewall solo expone los puertos estrictamente necesarios; el resto están cerrados | §4 |

---

## 0. Modelo de despliegue de referencia

Los servicios del monorepo se publican según `docker-compose.yml` (puertos resueltos desde `.env`):

| Servicio | Puerto interno | Publicación pública | Uso |
|---|---|---|---|
| Backend API (`services/api`, uvicorn) | 8000 | `BACKEND_PORT` | HTTPS API + WebSocket `/agent/ws` |
| Frontend website (Next.js) | 3000 | `WEBSITE_PORT` | Sitio público |
| Frontend backoffice (Next.js) | 3001 | `BACKOFFICE_PORT` | Consola de operaciones |
| Redis (broker Celery) | 6379 | `REDIS_PORT` | Broker interno |
| Flower (monitor Celery) | 5555 | 5555 | Monitor interno |
| MCP server (SSE) | 8100 | 8100 | Integración MCP (opcional / red interna) |
| Scheduler (cron) | — | — | `infra/crontab` (lunes 02:00 UTC) |

> HTTPS hacia el exterior se termina en el reverse proxy (nginx/Caddy) en el puerto 443; los servicios de `docker-compose` no publican 80/443 directamente.

---

## 1. Resumen de postura

- **Acceso administrativo:** solo SSH por clave (nunca contraseña), usuario operativo no-root, `sudo` restringido.
- **Red:** deny-by-default: solo se abren los puertos de la tabla anterior + 443/22; todo lo demás cerrado.
- **Secretos:** nunca en repo; `.env` delegado (ver `services/*/.env.example`), JWT y API keys desde entorno (evidencia en `services/user-api/app/security.py:12`).
- **Cargas automatizadas:** el cron (`infra/crontab`) corre como el usuario operativo, no como root.

---

## 2. SSH — denegar acceso directo como root (requisito 1)

**Configuración de referencia (`/etc/ssh/sshd_config.d/10-trackflow.conf`):**

```ssh
# Denegar login root directo (exigencia de auditoría)
PermitRootLogin no

# Solo autenticación por clave pública
PasswordAuthentication no
KbdInteractiveAuthentication no
ChallengeResponseAuthentication no
UsePAM yes

# Limitar quién puede entrar por SSH (usuario operativo + admins)
AllowUsers trackflow-ops admin-sre

# Sin X11 forwarding ni agent forwarding para operaciones
X11Forwarding no
AllowAgentForwarding no

# Timeouts para evitar sesiones huérfanas
ClientAliveInterval 300
ClientAliveCountMax 2
```

**Aplicación y verificación:**

```bash
sudo install -m 0644 10-trackflow.conf /etc/ssh/sshd_config.d/10-trackflow.conf
sudo sshd -t                       # validar sintaxis antes de recargar
sudo systemctl reload ssh

# Verificación reproducible (antes/después del fix)
grep -E '^PermitRootLogin' /etc/ssh/sshd_config.d/10-trackflow.conf
# => PermitRootLogin no

# Comprobar que root NO puede entrar por SSH
ssh root@SERVER echo "no debería ejecutarse"   # => Permission denied (publickey)
```

> **Prueba automatizada recomendada** (script de auditoría):
> ```bash
> if sshd -T 2>/dev/null | grep -q 'permitrootlogin no'; then
>   echo "PASS: root login deshabilitado"; else echo "FAIL"; exit 1; fi
> ```

---

## 3. Usuario operativo no-root con permisos de carpetas explícitos (requisito 2)

**Usuario dedicado** `trackflow-ops` (sin login interactivo salvo SSH por clave):

```bash
# Crear usuario operativo sin privilegios de root
sudo useradd --create-home --shell /bin/bash trackflow-ops
# Clave pública de operación (nunca contraseña)
sudo -u trackflow-ops mkdir -p /home/trackflow-ops/.ssh
echo "ssh-ed25519 AAAA... trackflow-ops@SERVER" | \
  sudo tee /home/trackflow-ops/.ssh/authorized_keys
sudo chmod 700 /home/trackflow-ops/.ssh
sudo chmod 600 /home/trackflow-ops/.ssh/authorized_keys
sudo chown -R trackflow-ops:trackflow-ops /home/trackflow-ops
```

**Permisos de carpetas explícitos (ACL):**

```bash
# Árbol de despliegue
sudo mkdir -p /opt/trackflow/{app,data,logs,certs,backups}
sudo chown trackflow-ops:trackflow-ops /opt/trackflow/{app,data,logs,backups}

# Lectura/ejecución del código (sin escritura)
sudo chmod -R 0550 /opt/trackflow/app
# Datos sensibles: solo lectura para ops, escritura solo donde toca
sudo chmod -R 0750 /opt/trackflow/data
sudo chmod -R 0750 /opt/trackflow/logs
# Backups: solo del servicio de backup
sudo chmod 0700 /opt/trackflow/backups

# Firewall/red: requiere sudo puntual (policy de sudo, no root directo)
```

**`sudo` restringido (no root directo):**

```sudo
# /etc/sudoers.d/trackflow-ops
trackflow-ops ALL=(ALL:ALL) NOPASSWD: /usr/bin/systemctl reload ssh, \
                                      /usr/bin/systemctl restart trackflow-backend, \
                                      /usr/bin/systemctl restart trackflow-worker
# Nota: sin shell de root; solo comandos específicos.
```

**Qué **no** puede hacer `trackflow-ops`:** leer `/home/*/other`, cambiar la política de firewall fuera del playbook, ni escribir en rutas de otros servicios.

---

## 4. Firewall — solo puertos necesarios (requisito 3)

Política **deny-by-default** (ufw, la misma idea con `nftables`/`iptables` y `firewalld`):

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing

# Público: HTTPS para API + frontends
sudo ufw allow 443/tcp            # HTTPS (reverse proxy)
sudo ufw allow 22/tcp             # SSH ops (solo IPs de administración, si se puede)

# Puertos de aplicación servidos tras el proxy (NO abrir si el proxy es el único público)
# sudo ufw allow 8000/tcp   # BACKEND_PORT (internos/ops unicamente)
# sudo ufw allow 8080/tcp   # WEBSITE_PORT
# sudo ufw allow 8081/tcp   # BACKOFFICE_PORT
# ⚠ Redis (6379) y Flower (5555): NUNCA públicos. Solo loopback/red interna.

sudo ufw enable
sudo ufw status numbered
```

**Resultado esperado:**

```text
Status: active
To                         Action      From
--                         ------      ----
443/tcp                    ALLOW       Anywhere
22/tcp                     ALLOW       Anywhere  (o IPs admin)
```

**Regla adicional — puertos internos:** Redis (6379) y Flower (5555) se publican solo en la red interna de Docker; si el servidor expone esos puertos al exterior, la auditoría **falla** el requisito 3. Corrección:

```bash
# En docker-compose.yml NO publicar 6379/5555 al host público,
# o limitar con ufw: permiso solo desde 127.0.0.1
sudo ufw allow from 127.0.0.1 to any port 6379
sudo ufw allow from 127.0.0.1 to any port 5555
```

**Verificación reproducible (script de auditoría):**

```bash
PASS=PASS; FAIL=FAIL
# 1) root SSH no permitido
grep -q '^PermitRootLogin no' /etc/ssh/sshd_config.d/10-trackflow.conf && echo "$PASS root" || echo "$FAIL root"
# 2) usuario no-root existe
id trackflow-ops >/dev/null 2>&1 && echo "$PASS usuario no-root" || echo "$FAIL usuario no-root"
# 3) firewall deny-by-default + solo puertos permitidos
ufw status | grep -q 'default: deny (incoming)' && echo "$PASS deny-by-default" || echo "$FAIL deny-by-default"
for p in 443 22; do ufw status | grep -q "^$p/tcp" && echo "$PASS puerto $p abierto" || echo "$FAIL puerto $p"; done
# Redis/Flower no deben ser públicos:
ufw status | grep -qE '^(6379|5555)/tcp' && echo "$FAIL redis/flower publico" || echo "$PASS internos no publicos"
```

> Si el entorno usa cloud (AWS SG / GCP firewall), aplicar la misma tabla: solo 443/22 + puertos de app tras proxy; nunca 6379/5555/8000 al mundo.

---

## 5. Endurecimiento adicional

- **TLS:** certificado Let's Encrypt en el proxy; HSTS; redirigir HTTP→HTTPS. TLS en rutas al cliente (requisito A02 del OWASP).
- **Secretos:** `.env` nunca en el repo (solo `.env.example`); rotación de `JWT_SECRET_KEY` y claves de carrier/documentada en el runbook.
- **Container:** los `Dockerfile` corren con `init: true` y sin ejecutar como root (ver `docker-compose.yml`); evitar `--privileged`.
- **Backups:** `infra/crontab` (lunes 02:00 UTC) como `trackflow-ops`, no root.
- **Escaneo de componentes (A06):** añadir a CI `pip-audit` + `npm audit` semanal y revisión de `pyproject.toml` pins.
- **Monitorización (A09):** enviar `data/audit/guardrail_events.jsonl` y logs de la API a un agregador (p. ej. transporte de Supabase/telemetría ya existente) y alertar ante `403`/`4403` anómalos o `jailbreak` repetidos.

---

## 6. Estado de cumplimiento (mapping con la verificación)

| Requisito | Estado | Evidencia en este doc |
|---|---|---|
| 1. SSH sin root | ✔ config especificada | §2 (+script de auditoría) |
| 2. Usuario no-root con permisos | ✔ config especificada | §3 (ACL por carpeta + sudo restringido) |
| 3. Firewall deny-by-default | ✔ config especificada | §4 (+verificación) |

> **Nota de despliegue:** estas son las **configuraciones de referencia** del endurecimiento. Su aplicación real en Los Ángeles y Zaragoza debe quedar registrada por el equipo de infraestructura (playbook/Ansible) y verificada con los scripts de auditoría de las secciones anteriores.