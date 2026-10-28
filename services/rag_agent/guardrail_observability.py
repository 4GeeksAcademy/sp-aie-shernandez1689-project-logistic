"""Observabilidad mínima de los guardarraíles del agente (block/redirect).

Cada vez que un guardarraíl **bloquea** o **redirige** una solicitud, este
módulo:

1. **Registra un evento** con el tipo de fallo detectado:
   - ``structural`` : entrada malformada / pregunta vacía (nodo ``validate``).
   - ``content``    : fuera de dominio, uso personal, respuesta de salida no
                      válida, pregunta casual (contenido/alcance).
   - ``security``   : jailbreak, anti-inyección (instrucción incrustada en
                      contenido externo de RAG/tool neutralizada u orden
                      detectada en el input).
2. **Acumula contadores en memoria** (por estado + por tipo de fallo) y
   **persiste cada evento en un log JSONL** (``data/audit/guardrail_events.jsonl``
   por defecto, sobreescribible con la env ``AGENT_GUARDRAIL_EVENTS_FILE``)
   para que el resumen sobreviva entre procesos.

El resumen se expone de dos formas:

- **Línea de comandos**: ``python -m services.rag_agent.guardrail_observability``
  (imprime el resumen acumulado de la sesión/archivo).
- **Endpoint HTTP**: ``GET /agent/guardrails/summary`` en ``services/api``
  (devuelve el mismo resumen como JSON).

Diseño deliberadamente simple y determinista: sin LLM, sin base de datos,
sin dependencias extra (solo stdlib). El registro es *best-effort*: si el
log no se puede escribir, el guardarraíl sigue funcionando (el bloqueo/la
redirección ya ocurrió); la observabilidad nunca altera la decisión.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from collections import Counter
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

# raíz del repo: services/rag_agent/guardrail_observability.py -> ../../
REPO_ROOT = Path(__file__).resolve().parents[2]
EVENTS_FILE = Path(
    os.getenv("AGENT_GUARDRAIL_EVENTS_FILE", str(REPO_ROOT / "data" / "audit" / "guardrail_events.jsonl"))
)

# Tipos de fallo detectados por la capa de observabilidad.
FailureKind = Literal["structural", "content", "security"]

# Estados de guardarraíl que ya produce guardrails.GuardrailState — mapeados
# aquí a su tipo de fallo para el resumen y para el log.
STATE_TO_KIND: dict[str, FailureKind] = {
    "jailbreak": "security",
    "personal_task": "content",
    "off_domain": "content",
    "small_talk": "content",      # redirige suavemente (no bloquea, pero se observa)
    "casual": "content",          # permitido + cierre con redirección
}

_logger = logging.getLogger(__name__)


class GuardrailCounter:
    """Contador acumulado de activaciones de guardarraíl (por estado y tipo).

    Es un *singleton* en memoria por proceso; cada evento se escribe también
    al archivo JSONL para poder reconstruir el resumen en un proceso nuevo.
    """

    def __init__(self) -> None:
        self._by_state: Counter[str] = Counter()
        self._by_kind: Counter[FailureKind] = Counter()

    def record(
        self,
        *,
        state: str,
        kind: FailureKind,
        node: str,
        thread_id: str = "default",
        detail: str | None = None,
    ) -> None:
        """Registra un bloqueo/redirección de guardarraíl.

        Incrementa los contadores en memoria y persiste el evento en el log
        JSONL (best-effort; nunca lanza).
        """
        self._by_state[state] += 1
        self._by_kind[kind] += 1
        event: dict[str, Any] = {
            "timestamp": _now(),
            "thread_id": thread_id,
            "node": node,
            "state": state,
            "kind": kind,
            "detail": detail,
        }
        try:
            EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
            with EVENTS_FILE.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(event, ensure_ascii=False) + "\n")
        except OSError as exc:  # observabilidad best-effort
            _logger.warning("No se pudo registrar evento de guardarraíl: %s", exc)

    def summary(self) -> dict[str, Any]:
        """Resumen acumulado de esta sesión (contadores en memoria)."""
        return build_summary(self._by_state, self._by_kind)

    def reset(self) -> None:
        """Limpia los contadores en memoria (no borra el archivo)."""
        self._by_state = Counter()
        self._by_kind = Counter()


# Singleton por proceso: todos los nodos del grafo llaman a esta misma
# instancia, de modo que el resumen de una sesión de pruebas es acumulativo.
_active = GuardrailCounter()


def get_counter() -> GuardrailCounter:
    """Devuelve el contador singleton del proceso."""
    return _active


def reset_counter() -> None:
    """Resetea el contador singleton (útil en tests)."""
    _active.reset()


def record(
    *,
    state: str,
    kind: FailureKind,
    node: str,
    thread_id: str = "default",
    detail: str | None = None,
) -> None:
    """Atajo: registra un evento en el contador singleton y en el log."""
    _active.record(state=state, kind=kind, node=node, thread_id=thread_id, detail=detail)


# =====================================================================
# Resumen
# =====================================================================


def build_summary(
    by_state: Counter[str] | dict[str, int],
    by_kind: Counter[FailureKind] | dict[str, int],
) -> dict[str, Any]:
    """Construye el resumen a partir de dos contadores.

    Parameters
    ----------
    by_state : Counter[str] | dict[str, int]
        Conteo de activaciones por estado de guardarraíl.
    by_kind : Counter[FailureKind] | dict[str, int]
        Conteo de activaciones por tipo de fallo.

    Returns
    -------
    dict[str, Any]
        ``{"total": int, "by_state": {...}, "by_kind": {...}}``.
    """
    state_counts = {
        state: int(count)
        for state, count in sorted(by_state.items(), key=lambda item: str(item[0]))
    }
    kind_counts = {
        kind: int(count)
        for kind, count in sorted(by_kind.items(), key=lambda item: str(item[0]))
    }
    return {
        "total": sum(state_counts.values()),
        "by_state": state_counts,
        "by_kind": kind_counts,
    }


def summary_from_file(path: Path | None = None) -> dict[str, Any]:
    """Reconstruye el resumen a partir del log JSONL persistido.

    Permite exponer el resumen acumulado de una sesión aunque el contador
    en memoria se haya reiniciado (p. ej. un proceso nuevo que sirve el
    endpoint HTTP).

    Parameters
    ----------
    path : Path | None
        Ruta del log JSONL. Por defecto ``EVENTS_FILE``.

    Returns
    -------
    dict[str, Any]
        Resumen idéntico en forma a ``build_summary``.
    """
    log_path = path or EVENTS_FILE
    by_state: Counter[str] = Counter()
    by_kind: Counter[FailureKind] = Counter()
    if not log_path.exists():
        return build_summary(by_state, by_kind)
    for line in log_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        state = event.get("state")
        kind = event.get("kind")
        if isinstance(state, str):
            by_state[state] += 1
        if isinstance(kind, str):
            by_kind[kind] += 1  # type: ignore[arg-type]
    return build_summary(by_state, by_kind)


def format_summary(summary: dict[str, Any]) -> str:
    """Formatea el resumen como texto legible (para CLI)."""
    lines = [
        "=== Guardarraíles del agente — resumen ===",
        f"Total activaciones (bloqueos + redirecciones): {summary['total']}",
        "",
        "Por estado:",
    ]
    by_state = summary["by_state"]
    if not by_state:
        lines.append("  (sin activaciones registradas)")
    for state, count in by_state.items():
        lines.append(f"  - {state:<15} {count}")
    lines.append("")
    lines.append("Por tipo de fallo:")
    by_kind = summary["by_kind"]
    if not by_kind:
        lines.append("  (sin activaciones registradas)")
    for kind, count in by_kind.items():
        lines.append(f"  - {kind:<12} {count}")
    return "\n".join(lines)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


if __name__ == "__main__":
    # CLI: python -m services.rag_agent.guardrail_observability [--file PATH]
    p = sys.argv[1:] if len(sys.argv) > 1 else []
    if p and p[0] == "--file" and len(p) > 1:
        log_path = Path(p[1])
    else:
        log_path = EVENTS_FILE
    print(format_summary(summary_from_file(log_path)))
    print(
        f"\n(log: {log_path})\n"
        "Sugerencia: reinicia el contador de sesión con el flag si quieres\n"
        "acumular desde cero, o usa el endpoint GET /agent/guardrails/summary."
    )