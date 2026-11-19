# Endurecimiento del servidor — TrackFlow

**Rama:** `feature/owasp-top10-audit` · **Fecha:** 2026-10-29 · **Sistemas:** Los Ángeles (EE. UU.) y Zaragoza (España)
**Referencia:** Brifing-seguridad.md §7 — "Baseline servidor / red: documenta SSH, usuario no-root y firewall. Público: HTTPS API + frontend; SSH ops."

Este documento es la **configuración de referencia** (no la evidencia de aplicación, ver §6) de los puntos 1, 2 y 3 de la verificación de seguridad:

| # | Requisito | Sección aquí |
|---|---|---|
| 1 | El servidor no permite acceso SSH directo como root | §2 |
| 2 | Existe un usuario dedicado no-root para tareas operativas, con permisos de carpetas explícitos | §3 |
| 3 | El firewall solo expone los puertos estrictamente necesarios; el resto están cerrados | §4 |

> ### ⚠ Estado: configuración definida, aplicación NO verificada
>
> | Qué **sí** contiene este documento | Qué **no** contiene |
> |---|---|
> | La configuración objetivo de SSH, usuario operativo y firewall (§2-§4) | Prueba de que esa configuración esté aplicada en Los Ángeles o Zaragoza |
> | Un script que recoge la evidencia real ([`scripts/collect-hardening-evidence.sh`](./scripts/collect-hardening-evidence.sh)) | Ninguna salida real de `sshd -T`, `id`, `ufw status` ni `ss -ltn` |
> | El criterio para dar cada requisito por cumplido (§6) | — |
>
> **Conclusión para la auditoría:** los requisitos 1-3 están **diseñados pero no verificados**. Ver §6 para el motivo, el riesgo residual y cómo cerrarlo.

---

## 0. Modelo de despliegue de referencia

Los servicios del monorepo se publican según `docker-compose.yml` (puertos resueltos desde `.env`):

| Servicio | Puerto interno | Publicación en el host | Uso |
|---|---|---|---|
| Backend API (`services/user-api`, uvicorn) | 8000 | `0.0.0.0:BACKEND_PORT` (detrás del proxy) | HTTPS API + WebSocket `/agent/ws` |
| Frontend website (Next.js) | 3000 | `0.0.0.0:WEBSITE_PORT` (detrás del proxy) | Sitio público |
| Frontend backoffice (Next.js) | 3001 | `0.0.0.0:BACKOFFICE_PORT` (detrás del proxy) | Consola de operaciones |
| Redis (broker Celery) | 6379 | `127.0.0.1:REDIS_PORT` | Broker interno |
| Flower (monitor Celery) | 5555 | `127.0.0.1:FLOWER_PORT` | Monitor interno |
| Qdrant (vector DB) | 6333 / 6334 | `127.0.0.1` | RAG interno |
| Scheduler (cron) | — | — | `infra/crontab` (lunes 02:00 UTC) |

> Hasta el 2026-11-19, Redis, Flower y Qdrant se publicaban en `0.0.0.0`, es decir, en todas las interfaces (ver §4, aviso sobre Docker y ufw). El servidor MCP (8100) que aparecía antes en esta tabla no está en `docker-compose.yml`. Si se despliega, debe publicarse solo en `127.0.0.1`.
>
> ⚠ Los puertos de la API y los frontends siguen publicados en `0.0.0.0`. Como Docker se salta ufw, también son accesibles directamente, sin pasar por el proxy. Para que el proxy sea la única entrada pública hay que publicarlos en `127.0.0.1` en el compose de producción. No se cambia aquí porque afecta al entorno de desarrollo.

> HTTPS hacia el exterior se termina en el reverse proxy (nginx/Caddy) en el puerto 443; los servicios de `docker-compose` no publican 80/443 directamente.

---

## 1. Resumen de postura

- **Acceso administrativo:** solo SSH por clave (nunca contraseña), usuario operativo no-root, `sudo` restringido.
- **Red:** deny-by-default. Solo 443 (proxy) y 22 (SSH de operaciones) son públicos; los puertos de la tabla anterior quedan detrás del proxy o en loopback.
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

> ⚠ **Docker se salta ufw.** Los puertos publicados por `docker-compose` (`"HOST:CONT"`) se insertan en la cadena `DOCKER` de iptables *antes* que las reglas de ufw. Un `ufw status` limpio **no** prueba que Redis/Flower/Qdrant estén cerrados. Por eso `docker-compose.yml` los publica solo en `127.0.0.1` y la verificación usa `ss -ltn` + `docker ps`, no solo ufw.

**Verificación reproducible:** [`infra/scripts/collect-hardening-evidence.sh`](./scripts/collect-hardening-evidence.sh) (sustituye al snippet anterior, que tenía dos fallos: `ufw status` sin `verbose` nunca imprime la política por defecto, y el grep sobre el fichero no refleja la config **efectiva** de sshd). Comprueba:

- `sshd -T` → `permitrootlogin no`, `passwordauthentication no`
- `id trackflow-ops` → uid ≠ 0, sin grupos `sudo`/`wheel`/`docker`; `sudo -l -U`, `stat` de `/opt/trackflow/*`
- `ufw status verbose` → activo + `deny (incoming)`
- `ss -ltnp` → ningún socket fuera de loopback salvo 22/443
- `docker ps` → ningún contenedor publicado en `0.0.0.0`/`::`

```bash
sudo infra/scripts/collect-hardening-evidence.sh \
  > docs/security/evidence/$(hostname)-$(date -u +%Y%m%dT%H%M%SZ).txt
```

Complementar con un escaneo **desde fuera** (`nmap -Pn -p- <IP pública>`), que es la única prueba de lo que realmente ve un atacante.

> Si el entorno usa cloud (AWS SG / GCP firewall), aplicar la misma tabla: solo 443/22 + puertos de app tras proxy; nunca 6379/5555/8000 al mundo.

---

## 5. Endurecimiento adicional

- **TLS:** certificado Let's Encrypt en el proxy; HSTS; redirigir HTTP→HTTPS. TLS en rutas al cliente (requisito A02 del OWASP).
- **Secretos:** `.env` nunca en el repo (solo `.env.example`); rotación de `JWT_SECRET_KEY` y claves de carrier/documentada en el runbook.
- **Container:** ⚠ **pendiente.** Ningún Dockerfile (`services/Dockerfile`, `uis/Dockerfile`, `infra/worker.Dockerfile`, `infra/scheduler.Dockerfile`) declara `USER`, así que todos los contenedores corren como root. `init: true` (en `docker-compose.yml`) solo reenvía señales y recoge procesos zombi; no es una medida de privilegios. Corrección: crear un usuario sin privilegios en cada imagen y añadir `USER`. Además, no usar nunca `--privileged`.
- **Backups:** `infra/crontab` (lunes 02:00 UTC) como `trackflow-ops`, no root.
- **Escaneo de componentes (A06):** añadir a CI `pip-audit` + `npm audit` semanal y revisión de `pyproject.toml` pins.
- **Monitorización (A09):** enviar `data/audit/guardrail_events.jsonl` y logs de la API a un agregador (p. ej. transporte de Supabase/telemetría ya existente) y alertar ante `403`/`4403` anómalos o `jailbreak` repetidos.

---

## 6. Estado de cumplimiento (mapping con la verificación)

| Requisito | Configuración | Aplicado en LA | Aplicado en Zaragoza |
|---|---|---|---|
| 1. SSH sin root | ✔ especificada (§2) | ⏳ **sin evidencia** | ⏳ **sin evidencia** |
| 2. Usuario no-root con permisos | ✔ especificada (§3) | ⏳ **sin evidencia** | ⏳ **sin evidencia** |
| 3. Firewall / puertos mínimos | ✔ especificada (§4) + compose en loopback | ⏳ **sin evidencia** | ⏳ **sin evidencia** |

> **Este documento NO es evidencia de aplicación.** Hasta que exista la evidencia descrita en §6.4, la auditoría debe tratar los requisitos 1-3 como **no verificados**.

### 6.1 Qué está hecho

| Elemento | Dónde | Tipo |
|---|---|---|
| Configuración objetivo de SSH, usuario operativo, sudo y firewall | §2-§4 | Documentación |
| Script de recogida de evidencia, que devuelve un código de salida ≠ 0 si algo falla | [`scripts/collect-hardening-evidence.sh`](./scripts/collect-hardening-evidence.sh) | Herramienta (sintaxis validada, sin ejecutar en un servidor real) |
| Carpeta para la evidencia, con reglas de uso | [`docs/security/evidence/`](../docs/security/evidence/README.md) | Vacía |
| Redis, Flower y Qdrant publicados solo en loopback | `docker-compose.yml` | **Cambio aplicado en el repo** |

### 6.2 Qué no está hecho y por qué

- **No se ha aplicado ni verificado nada en los servidores de Los Ángeles ni de Zaragoza.** Quien redactó esta auditoría no tiene acceso a esos servidores. Ejecutar los comandos en un entorno de desarrollo (codespace, portátil) no prueba nada sobre producción, así que no se ha hecho para no presentarlo como evidencia.
- **No hay registro de aplicación** (playbook de Ansible, log de sesión, ticket de cambio) por parte del equipo de infraestructura.

### 6.3 Riesgo residual mientras siga sin verificar

| Si en realidad no estuviera aplicado… | Consecuencia |
|---|---|
| Req. 1: `PermitRootLogin` ≠ `no` | Fuerza bruta o robo de clave dan acceso root directo, sin rastro de un usuario nominal |
| Req. 2: ops trabaja como root | Un error o un script comprometido tiene el control total del host |
| Req. 3: Redis, Flower o Qdrant accesibles | Redis sin autenticación permite leer y encolar tareas de Celery (posible ejecución remota de código en los workers). Flower expone y controla esas tareas. Qdrant expone los datos del RAG |
| Req. 3: imágenes desplegadas antes del 2026-11-19 | Ese compose publicaba los servicios internos en `0.0.0.0`. **Si no se ha vuelto a desplegar, la exposición es probable aunque ufw esté bien** |

### 6.4 Cómo se cierra (criterio de aceptación)

Un requisito pasa a ✔ en un servidor solo cuando se cumplen las tres condiciones:

1. En `docs/security/evidence/` está la salida **sin editar** de `sudo infra/scripts/collect-hardening-evidence.sh` ejecutado en ese servidor, y termina en `RESULTADO: PASS`.
2. Junto a ella está un `nmap -Pn -p- <IP pública>` lanzado **desde fuera** de la red del servidor, que muestra solo los puertos 22 y 443 abiertos.
3. Ambas salidas son posteriores al último despliegue del `docker-compose.yml` con los puertos en loopback.

Responsable: el equipo de infraestructura u operaciones con acceso a los servidores. Hay que repetirlo tras cualquier cambio de SSH, usuarios, firewall o compose.