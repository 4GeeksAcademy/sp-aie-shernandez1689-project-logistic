# Evidencia de endurecimiento aplicada

Aquí va la salida **sin editar** de [`infra/scripts/collect-hardening-evidence.sh`](../../../infra/scripts/collect-hardening-evidence.sh), ejecutada en cada servidor real, y el escaneo externo correspondiente.

| Fichero | Contenido |
|---|---|
| `<hostname>-<YYYYMMDDTHHMMSSZ>.txt` | Salida del script (`sshd -T`, `id`, `ufw status verbose`, `ss -ltnp`, `docker ps`) |
| `<hostname>-<YYYYMMDDTHHMMSSZ>-nmap.txt` | `nmap -Pn -p- <IP pública>` lanzado desde fuera de la red del servidor |

Reglas:

- No editar ni recortar las salidas: si algo es sensible (IPs, usuarios), anotarlo aparte, no tocar el fichero.
- Un requisito de `infra/hardening.md` §6 pasa a ✔ solo con un fichero aquí que termine en `RESULTADO: PASS`.
- Re-ejecutar tras cualquier cambio de SSH, usuarios, firewall o `docker-compose.yml`.

**Estado actual (2026-11-19): vacío. No hay evidencia de Los Ángeles ni de Zaragoza.**
