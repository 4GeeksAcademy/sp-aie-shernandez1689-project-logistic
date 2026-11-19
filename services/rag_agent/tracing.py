"""Tracing estructurado de corridas del agente RAG.

Cada corrida escribe un archivo JSON consultable después de la corrida
(no solo impresión en consola) en ``data/eval/agent_traces/<thread_id>.json``.

Formato del trace (schema v2)
-----------------------------
{
    "schema_version": 2,
  "thread_id": str,
  "started_at": str (ISO-8601),
  "completed_at": str,
  "question": str,
    "routing": {                  # resumen derivado de nodos ejecutados
        "decision": "rag" | "tool" | "both" | "none",
        "used_rag": bool,
        "tools": [str],
        "order": [str]              # orden efectivo de uso de fuentes
    },
  "steps": [                     # en orden de ejecución
    {"seq": int, "node": str, "started_at": str,
     "output": dict}             # estado que el nodo produjo
  ],
  "final_state": dict             # estado al terminar la corrida
}

``steps`` registra la salida de cada nodo: qué produjo (answer, context,
error...) y al ser append incremental se conserva el orden incluso si la
corrida falla a mitad. El estado completo de cada snapshot lo conserva
además el checkpointer de LangGraph (``graph.get_state``).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# raíz del repo: services/rag_agent/tracing.py -> ../../
REPO_ROOT = Path(__file__).resolve().parents[2]
TRACES_DIR = Path(
    os.getenv("AGENT_TRACES_DIR", str(REPO_ROOT / "data" / "eval" / "agent_traces"))
)

SCHEMA_VERSION = 2

# Datos del trace activo (thread-local por corrida; langgraph ejecuta
# los nodos de una corrida de forma secuencial, sin paralelismo por defecto).
_active: dict[str, Any] | None = None


def start_run(thread_id: str, question: str) -> dict[str, Any]:
    """Abre un trace nuevo para una corrida."""
    global _active
    TRACES_DIR.mkdir(parents=True, exist_ok=True)
    _active = {
        "schema_version": SCHEMA_VERSION,
        "thread_id": thread_id,
        "started_at": _now(),
        "completed_at": None,
        "question": question,
        "steps": [],
        "final_state": {},
    }
    return _active


def ensure_started(thread_id: str, question: str) -> None:
    """Abre el trace si no existe todavía para esta corrida (idempotente)."""
    if _active is None:
        start_run(thread_id, question)


def add_step(node: str, output: dict[str, Any]) -> None:
    """Registra la salida de un nodo (append incremental, en orden).

    Persiste el archivo en cada paso para que el trace sea consultable
    aunque la corrida falle a mitad.
    """
    if _active is None:
        return
    _active["steps"].append(
        {
            "seq": len(_active["steps"]) + 1,
            "node": node,
            "started_at": _now(),
            "output": output,
        }
    )
    _save()


def end_run(final_state: dict[str, Any]) -> Path:
    """Cierra el trace y lo persiste a disco. Devuelve la ruta del archivo."""
    global _active
    if _active is None:
        raise RuntimeError("end_run() sin start_run()")
    _active["routing"] = _routing_summary(_active["steps"])
    _active["completed_at"] = _now()
    _active["final_state"] = final_state
    path = _save()
    _active = None
    return path


def abort_run(thread_id: str, reason: str) -> None:
    """Cierra el trace de una corrida cortada antes de ``finalize``.

    Sin esto ``_active`` seguiría abierto y la siguiente corrida añadiría
    sus pasos al trace viejo. Solo cierra si el trace activo es de
    ``thread_id`` (no toca el de otra corrida).
    """
    if _active is None or _active["thread_id"] != thread_id:
        return
    end_run({"aborted": reason})


def _routing_summary(steps: list[dict[str, Any]]) -> dict[str, Any]:
    """Resume las fuentes realmente ejecutadas, no solo las intenciones.

    ``retrieve`` indica uso efectivo del RAG; una intención de tool que no
    llegó a ejecutarse no se presenta como fuente consultada.
    """
    nodes = [step["node"] for step in steps]
    tools = [
        node for node in nodes
        if node in {"incident_lookup", "inventory_lookup"}
    ]
    used_rag = "retrieve" in nodes
    if tools and used_rag:
        decision = "both"
    elif tools:
        decision = "tool"
    elif used_rag:
        decision = "rag"
    else:
        decision = "none"

    order = [
        f"tool:{node}" if node in {"incident_lookup", "inventory_lookup"} else "rag:retrieve"
        for node in nodes
        if node in {"incident_lookup", "inventory_lookup", "retrieve"}
    ]
    return {
        "decision": decision,
        "used_rag": used_rag,
        "tools": tools,
        "order": order,
    }


def _save() -> Path:
    path = TRACES_DIR / f"{_active['thread_id']}.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(_active, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return path


def load_trace(thread_id: str) -> dict[str, Any]:
    """Carga un trace persistido por su thread_id."""
    path = TRACES_DIR / f"{thread_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"No hay trace para thread_id={thread_id!r}")
    return json.loads(path.read_text(encoding="utf-8"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")