"""Grafo orchestrator → workers → synthesizer del intake de RFPs (Parte 1).

Grafo LangGraph **dedicado** al intake (no se mezcla con el grafo CX de
``services/rag_agent``). Corre tras la clasificación, solo para RFPs
aceptadas::

    START ─► orchestrator ─┬─ Send ─► worker(warehouse) ─┐
                           ├─ Send ─► worker(lastmile)  ─┼─► synthesizer ─► END
                           └─ Send ─► worker(reverse)   ─┘

- **orchestrator**: descompone la RFP en una subtarea por departamento activo
  (``departments_needed``). Cada subtarea lleva los **metadatos compartidos**
  y solo los **extractos del Markdown relevantes** a ese departamento
  (BRIEFING §2.3).
- **worker** (uno por departamento, en paralelo): construye ``key_aspects``
  a partir de su subtarea. **Nunca inventa cifras**: todo número de un
  ``key_aspect`` debe aparecer en sus extractos o metadatos; si no, se
  descarta. Lo que el departamento necesita y la RFP no dice se registra en
  ``open_questions`` (p. ej. falta el volumen mensual).
- **synthesizer**: consolida el resumen para Ventas (qué pedir a quién y qué
  preguntar al cliente) y el ``handoff`` hacia la Parte 2.

Contrato del handoff (``rfp_metadata.handoff``, JSONB): ``ticket_id``,
``rfp_id``, ``next_status`` (``generando_borrador``), ``departments``,
``section_ids``, ``sections`` (``department_id``, ``owner``, ``key_aspects``,
``open_questions``), ``currency``, ``markdown_path``, ``guidelines`` (§5) y
``pending_client_questions``. La Parte 2 lo lee de los tickets en
``analisis_completo``.

Los workers son deterministas (sin LLM), como el clasificador: auditables y
garantizan que no hay cifras inventadas. La redacción con LLM es Parte 2.
"""

from __future__ import annotations

import operator
import re
from datetime import datetime, timezone
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from .config import (
    DEPARTMENT_ASKS,
    DEPARTMENT_LABELS,
    DEPARTMENT_OWNERS,
    PROPOSAL_GUIDELINES,
    STATUS_GENERANDO_BORRADOR,
)

# Metadatos que se comparten con todos los workers.
SHARED_FIELDS = (
    "doc_type",
    "client_name",
    "client_country",
    "currency",
    "services_requested",
    "monthly_volume",
    "deadline",
    "budget_range",
)

MAX_EXCERPTS = 6
MAX_EXCERPT_CHARS = 400
# Celdas sueltas de tabla ("Almacenamiento") no aportan contexto.
MIN_EXCERPT_WORDS = 3

# Señales léxicas (es/en) para seleccionar extractos por departamento.
_DEPARTMENT_SIGNALS: dict[str, re.Pattern] = {
    "warehouse": re.compile(
        r"almac[eé]n|almacenamiento|almacenaje|warehous|storage|centro\s+log[ií]stico|"
        r"fulfil+ment|pallet|palet|\bSKU|inventario|inventory|stock",
        re.IGNORECASE,
    ),
    "lastmile": re.compile(
        r"[uú]ltima\s+milla|last[- ]?mile|entrega|deliver|env[ií]o|shipping|"
        r"shipment|transportista|carrier|courier|paqueter[ií]a",
        re.IGNORECASE,
    ),
    "reverse": re.compile(
        r"devoluci|returns?\b|reverse|log[ií]stica\s+inversa|reacondicion|"
        r"refurbish|recondition",
        re.IGNORECASE,
    ),
}

# Información que cada departamento necesita y que, si la RFP no la menciona,
# se convierte en pregunta abierta (nunca en un supuesto).
_REQUIRED_INFO: dict[str, tuple[tuple[re.Pattern, str], ...]] = {
    "warehouse": (
        (
            re.compile(r"\bSKU|referencias|pallet|palet|posiciones", re.IGNORECASE),
            "Número de SKUs y posiciones de pallet a almacenar.",
        ),
    ),
    "lastmile": (
        (
            re.compile(
                r"destino|destination|zona|cobertura|coverage|nacional|national|mercado|market|"
                r"internacional|international|ciudad|cities|estados|states",
                re.IGNORECASE,
            ),
            "Destinos/zonas de entrega y mix nacional/internacional.",
        ),
        (
            re.compile(
                r"\bSLA|plazo\s+de\s+entrega|delivery\s+time|same[- ]day|next[- ]day|"
                r"\d+\s*(h|horas|hours)\b",
                re.IGNORECASE,
            ),
            "Plazo/SLA de entrega que espera el cliente.",
        ),
    ),
    "reverse": (
        (
            re.compile(
                r"tasa\s+de\s+devoluci|return\s+rate|%\s*de\s+devoluci|devoluciones\s+al\s+mes|"
                r"returns\s+per\s+month",
                re.IGNORECASE,
            ),
            "Tasa o volumen mensual de devoluciones esperado.",
        ),
        (
            re.compile(
                r"tiempo\s+de\s+procesamiento|turnaround|plazo\s+de\s+devoluci|processing\s+time",
                re.IGNORECASE,
            ),
            "Plazo esperado de procesamiento de devoluciones (TrackFlow no "
            "promete menos de 48 horas).",
        ),
    ),
}

_NUMBER_RE = re.compile(r"\d[\d.,]*\d|\d")


# ── Estado del grafo ────────────────────────────────────────────────────────


class DepartmentTask(TypedDict):
    """Subtarea que el orquestador entrega a un worker."""

    ticket_id: str
    rfp_id: str
    department_id: str
    shared: dict[str, Any]
    excerpts: list[str]


class IntakeGraphState(TypedDict, total=False):
    ticket_id: str
    rfp_id: str
    markdown: str
    markdown_path: str | None
    metadata: dict[str, Any]
    tasks: list[DepartmentTask]
    sections: Annotated[list[dict[str, Any]], operator.add]
    sales_summary: dict[str, Any]
    handoff: dict[str, Any]


# ── Helpers ─────────────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _paragraphs(markdown: str) -> list[str]:
    """Bloques del Markdown separados por línea en blanco, en una sola línea."""
    blocks = re.split(r"\n\s*\n", markdown or "")
    return [" ".join(b.split()) for b in blocks if b.strip()]


def select_excerpts(markdown: str, department_id: str) -> list[str]:
    """Extractos del Markdown relevantes a un departamento (orden original)."""
    signal = _DEPARTMENT_SIGNALS[department_id]
    excerpts: list[str] = []
    for paragraph in _paragraphs(markdown):
        if len(paragraph.split()) >= MIN_EXCERPT_WORDS and signal.search(paragraph):
            excerpts.append(paragraph[:MAX_EXCERPT_CHARS])
        if len(excerpts) == MAX_EXCERPTS:
            break
    return excerpts


def _digits(token: str) -> str:
    return re.sub(r"[.,]", "", token)


def is_grounded(aspect: str, sources: list[str]) -> bool:
    """True si cada número del aspecto aparece en las fuentes del worker.

    Compara dígitos sin separadores, de modo que ``3000`` casa con
    ``3.000`` y ``50.000-80.000`` con ``50000``/``80000``.
    """
    available = {_digits(n) for source in sources for n in _NUMBER_RE.findall(source)}
    return all(_digits(n) in available for n in _NUMBER_RE.findall(aspect))


def _in_routing_order(sections: list[dict[str, Any]], metadata: dict[str, Any]) -> list[dict[str, Any]]:
    """Ordena las secciones como ``departments_needed`` (los workers acaban en cualquier orden)."""
    order = {dep: i for i, dep in enumerate(metadata.get("departments_needed", []))}
    return sorted(sections, key=lambda s: order.get(s["department_id"], len(order)))


# ── Nodos ───────────────────────────────────────────────────────────────────


def orchestrator(state: IntakeGraphState) -> dict[str, Any]:
    """Descompone la RFP en una subtarea por departamento activo."""
    metadata = state["metadata"]
    shared = {key: metadata.get(key) for key in SHARED_FIELDS}
    tasks: list[DepartmentTask] = [
        {
            "ticket_id": state["ticket_id"],
            "rfp_id": state["rfp_id"],
            "department_id": department_id,
            "shared": shared,
            "excerpts": select_excerpts(state["markdown"], department_id),
        }
        for department_id in metadata.get("departments_needed", [])
    ]
    return {"tasks": tasks}


def fan_out(state: IntakeGraphState) -> list[Send] | str:
    """Un worker por subtarea, en paralelo (sin subtareas → synthesizer)."""
    return [Send("worker", task) for task in state["tasks"]] or "synthesizer"


def worker(task: DepartmentTask) -> dict[str, Any]:
    """Construye la DepartmentSection (``key_aspects``) de un departamento."""
    department_id = task["department_id"]
    shared = task["shared"]
    excerpts = task["excerpts"]

    candidates = [f"Servicio solicitado: {DEPARTMENT_LABELS[department_id]}"]
    if shared.get("client_country"):
        candidates.append(
            f"País del cliente: {shared['client_country']} "
            f"(moneda {shared.get('currency') or 'por confirmar'})"
        )
    if shared.get("monthly_volume") is not None:
        candidates.append(
            f"Volumen mensual indicado en la RFP: {shared['monthly_volume']} pedidos/mes"
        )
    if shared.get("deadline"):
        candidates.append(f"Fecha límite de la propuesta: {shared['deadline']}")
    if shared.get("budget_range"):
        candidates.append(f"Presupuesto de referencia: {shared['budget_range']}")
    candidates.extend(f"Extracto de la RFP: «{excerpt}»" for excerpt in excerpts)

    sources = excerpts + [str(v) for v in shared.values() if v is not None]
    key_aspects = [aspect for aspect in candidates if is_grounded(aspect, sources)]

    open_questions: list[str] = []
    if shared.get("monthly_volume") is None:
        open_questions.append("Volumen mensual de pedidos (la RFP no lo indica).")
    if not shared.get("client_country"):
        open_questions.append("País de operación (define la moneda de la propuesta).")
    corpus = " ".join(excerpts)
    for pattern, question in _REQUIRED_INFO[department_id]:
        if not pattern.search(corpus):
            open_questions.append(question)

    now = _now_iso()
    section = {
        "id": f"{task['ticket_id']}_{department_id}",
        "ticket_id": task["ticket_id"],
        "rfp_id": task["rfp_id"],
        "department_id": department_id,
        "department_label": DEPARTMENT_LABELS[department_id],
        "department_owner": DEPARTMENT_OWNERS[department_id],
        "key_aspects": key_aspects,
        "source_excerpts": excerpts,
        "open_questions": open_questions,
        "draft_content": None,  # Parte 2
        "evaluation_results": None,  # Parte 2
        "approval_status": "pendiente",  # Parte 3
        "approver": None,
        "approved_at": None,
        "created_at": now,
        "updated_at": now,
    }
    return {"sections": [section]}


def synthesizer(state: IntakeGraphState) -> dict[str, Any]:
    """Resumen para Ventas (qué pedir a quién) + handoff hacia la Parte 2."""
    metadata = state["metadata"]
    sections = _in_routing_order(state.get("sections", []), metadata)

    requests = [
        {
            "department_id": s["department_id"],
            "department_label": s["department_label"],
            "owner": s["department_owner"],
            "ask": DEPARTMENT_ASKS[s["department_id"]],
            "open_questions": s["open_questions"],
        }
        for s in sections
    ]
    client_questions = list(dict.fromkeys(q for s in sections for q in s["open_questions"]))

    client = metadata.get("client_name") or "Cliente sin nombre"
    who = "; ".join(f"{r['owner']} ({r['department_label']}): {r['ask']}" for r in requests)
    deadline = metadata.get("deadline")
    headline = f"{client} — pedir a {who}" + (f". Fecha límite: {deadline}." if deadline else ".")

    sales_summary = {
        "headline": headline,
        "client_name": metadata.get("client_name"),
        "client_country": metadata.get("client_country"),
        "currency": metadata.get("currency"),
        "monthly_volume": metadata.get("monthly_volume"),
        "deadline": deadline,
        "budget_range": metadata.get("budget_range"),
        "requests": requests,
        "client_questions": client_questions,
        "generated_at": _now_iso(),
    }
    handoff = {
        "part": 2,
        "ticket_id": state["ticket_id"],
        "rfp_id": state["rfp_id"],
        "next_status": STATUS_GENERANDO_BORRADOR,
        "departments": [s["department_id"] for s in sections],
        "section_ids": [s["id"] for s in sections],
        # Payload por departamento para los generadores de la Parte 2 (copia de
        # DepartmentSection; la fila en department_sections es la fuente).
        "sections": [
            {
                "department_id": s["department_id"],
                "owner": s["department_owner"],
                "key_aspects": s["key_aspects"],
                "open_questions": s["open_questions"],
            }
            for s in sections
        ],
        "currency": metadata.get("currency"),
        "markdown_path": state.get("markdown_path"),
        "inputs": (
            "Por departamento: DepartmentSection.key_aspects + source_excerpts + "
            "open_questions; metadatos compartidos en rfp_metadata."
        ),
        "guidelines": list(PROPOSAL_GUIDELINES),
        "pending_client_questions": len(client_questions),
    }
    return {"sales_summary": sales_summary, "handoff": handoff}


# ── Grafo ───────────────────────────────────────────────────────────────────

_builder = StateGraph(IntakeGraphState)
_builder.add_node("orchestrator", orchestrator)
_builder.add_node("worker", worker)
_builder.add_node("synthesizer", synthesizer)
_builder.add_edge(START, "orchestrator")
_builder.add_conditional_edges("orchestrator", fan_out, ["worker", "synthesizer"])
_builder.add_edge("worker", "synthesizer")
_builder.add_edge("synthesizer", END)

department_graph = _builder.compile()


def run_department_graph(
    *,
    ticket_id: str,
    rfp_id: str,
    markdown: str,
    metadata: dict[str, Any],
    markdown_path: str | None = None,
) -> dict[str, Any]:
    """Ejecuta el grafo y devuelve ``sections``, ``sales_summary`` y ``handoff``."""
    final = department_graph.invoke(
        {
            "ticket_id": ticket_id,
            "rfp_id": rfp_id,
            "markdown": markdown,
            "markdown_path": markdown_path,
            "metadata": metadata,
            "sections": [],
        }
    )
    return {
        "sections": _in_routing_order(final["sections"], metadata),
        "sales_summary": final["sales_summary"],
        "handoff": final["handoff"],
    }


__all__ = [
    "department_graph",
    "run_department_graph",
    "orchestrator",
    "worker",
    "synthesizer",
    "select_excerpts",
    "is_grounded",
]
