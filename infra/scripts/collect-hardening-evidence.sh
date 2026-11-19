#!/usr/bin/env bash
# Recoge evidencia REAL de endurecimiento (requisitos 1-3 de infra/hardening.md).
# Ejecutar EN el servidor objetivo (Los Ángeles / Zaragoza) con sudo:
#   sudo ./collect-hardening-evidence.sh > "$(hostname)-$(date -u +%Y%m%dT%H%M%SZ).txt"
# Commitear la salida sin editar en docs/security/evidence/.
# Salida != 0 si algún check falla.
set -u
OPS_USER="${OPS_USER:-trackflow-ops}"
ALLOWED_PUBLIC_PORTS="${ALLOWED_PUBLIC_PORTS:-22 443}"
fail=0
pass() { echo "PASS: $*"; }
bad()  { echo "FAIL: $*"; fail=1; }
section() { printf '\n===== %s =====\n' "$*"; }

[ "$(id -u)" -eq 0 ] || { echo "Ejecutar con sudo (sshd -T y ufw lo requieren)"; exit 2; }

section "Contexto"
echo "host: $(hostname -f 2>/dev/null || hostname)"
echo "fecha_utc: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "kernel: $(uname -srm)"
grep -E '^(PRETTY_NAME)=' /etc/os-release 2>/dev/null
echo "ejecutado_por: ${SUDO_USER:-root}"

section "1. SSH: config efectiva (sshd -T)"
sshd_out="$(sshd -T 2>&1)" || bad "sshd -T falló: $sshd_out"
echo "$sshd_out" | grep -Ei '^(permitrootlogin|passwordauthentication|kbdinteractiveauthentication|pubkeyauthentication|allowusers|x11forwarding|allowagentforwarding|clientaliveinterval|clientalivecountmax) '
echo "$sshd_out" | grep -qx 'permitrootlogin no' && pass "PermitRootLogin no" || bad "PermitRootLogin != no"
echo "$sshd_out" | grep -qx 'passwordauthentication no' && pass "PasswordAuthentication no" || bad "PasswordAuthentication != no"

section "2. Usuario operativo no-root"
if id "$OPS_USER"; then
  [ "$(id -u "$OPS_USER")" -ne 0 ] && pass "$OPS_USER uid != 0" || bad "$OPS_USER tiene uid 0"
  id -nG "$OPS_USER" | grep -Eqw 'sudo|wheel|root|docker' \
    && bad "$OPS_USER en grupo privilegiado (sudo/wheel/root/docker): $(id -nG "$OPS_USER")" \
    || pass "$OPS_USER sin grupos privilegiados"
else
  bad "usuario $OPS_USER no existe"
fi
echo "--- cuentas con uid 0:"; awk -F: '$3==0{print $1}' /etc/passwd
echo "--- sudo -l -U $OPS_USER:"; sudo -l -U "$OPS_USER" 2>&1
echo "--- permisos /opt/trackflow:"; stat -c '%A %U:%G %n' /opt/trackflow /opt/trackflow/* 2>&1
echo "--- ~/.ssh:"; stat -c '%A %U:%G %n' "/home/$OPS_USER/.ssh" "/home/$OPS_USER/.ssh/authorized_keys" 2>&1

section "3a. Firewall (ufw status verbose)"
ufw_out="$(ufw status verbose 2>&1)"; echo "$ufw_out"
echo "$ufw_out" | grep -q '^Status: active' && pass "ufw activo" || bad "ufw inactivo"
echo "$ufw_out" | grep -q 'deny (incoming)' && pass "deny-by-default entrante" || bad "política entrante no es deny"

section "3b. Sockets en escucha (ss -ltnH)"
ss -ltnp
# Puertos escuchando fuera de loopback
exposed="$(ss -ltnH | awk '{print $4}' | grep -Ev '^(127\.|\[::1\]|::1)' | sed -E 's/.*:([0-9]+)$/\1/' | sort -un)"
for p in $exposed; do
  case " $ALLOWED_PUBLIC_PORTS " in *" $p "*) pass "puerto $p no-loopback (permitido)";;
    *) bad "puerto $p escucha fuera de loopback y no está en la lista permitida";; esac
done

section "3c. Docker (sus puertos publicados se saltan ufw)"
if command -v docker >/dev/null; then
  docker ps --format '{{.Names}}\t{{.Ports}}'
  docker ps --format '{{.Ports}}' | tr ',' '\n' | grep -E '0\.0\.0\.0:|\[::\]:|:::' \
    && bad "contenedores publicados en todas las interfaces (bypass de ufw)" \
    || pass "ningún contenedor publicado en 0.0.0.0/::"
else
  echo "docker no instalado"
fi

section "Resultado"
[ "$fail" -eq 0 ] && echo "RESULTADO: PASS" || echo "RESULTADO: FAIL"
exit "$fail"
