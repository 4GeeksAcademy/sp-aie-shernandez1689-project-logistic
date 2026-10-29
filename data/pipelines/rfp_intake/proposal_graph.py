"""Grafo de generación + evaluación de la propuesta económica (Hito 9, Parte 2).

Grafo LangGraph dedicado, como el de la Parte 1. Entrada: los
``SectionBrief`` construidos desde el handoff de la Parte 1 (nunca el PDF ni
el Markdown). Un ciclo generador-evaluador por departamento, todos en
paralelo::

    START ─► dispatch ─┬─ Send ─► section_warehouse ─┐
                       ├─ Send ─► section_lastmile  ─┼─► collect ─► END
                       └─ Send ─► section_reverse   ─┘

Cada ``section_<dep>`` usa el agente generador de su departamento
(``generator.generator_for``) y corre en su propio hilo: un departamento
lento o que falla no frena ni tumba a los demás (un error queda en su
sección como ``needs_human_review`` con ``error``).

Cada ``section`` es un subgrafo con los tres evaluadores en paralelo::

    START ─► generate ─┬─► readability ─┐
                ▲      ├─► relevance   ─┼─► aggregate ─┬─► END (pasa)
                │      └─► compliance  ─┘              ├─► END (límite → needs_human_review)
                └──────── feedback_for_generator ◄─────┘ (falla, quedan iteraciones)

Al agotar ``max_iterations`` sin pasar, la sección conserva su último
borrador + EvaluationResult y sale como ``needs_human_review``: no se
descarta y viaja igual a la Parte 3.

``config["configurable"]`` admite ``drafter`` (redactor del generador) y
``progress`` (callback ``(event, section_id, payload)`` con el que el
pipeline persiste el avance: ``drafting``, ``evaluating``, ``evaluated``).
"""

from __future__ import annotations

import logging
import operator
from datetime import datetime, timezone
from typing import Annotated, Any, Callable, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from .config import (
    DEPARTMENT_IDS,
    MAX_GENERATION_ITERATIONS,
    SECTION_APROBADA_EVALUACION,
    SECTION_NEEDS_HUMAN_REVIEW,
)
from .evaluators import (
    combine,
    evaluate_compliance,
    evaluate_readability,
    evaluate_relevance,
)
from .generator import SectionBrief, generate_section

logger = logging.getLogger(__name__)

EVALUATOR_NODES = ("readability", "relevance", "compliance")


class SectionState(TypedDict, total=False):
    brief: SectionBrief
    max_iterations: int
    iteration: int
    draft: str
    generator: str
    readability: Any
    relevance: Any
    compliance: Any
    evaluation: dict[str, Any]
    drafts: list[dict[str, Any]]
    evaluations: list[dict[str, Any]]


class ProposalState(TypedDict, total=False):
    briefs: list[SectionBrief]
    max_iterations: int
    results: Annotated[list[dict[str, Any]], operator.add]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _configurable(config: RunnableConfig | None) -> dict[str, Any]:
    return (config or {}).get("configurable", {}) or {}


def _progress(config: RunnableConfig | None, event: str, section_id: str, payload: dict[str, Any]) -> None:
    callback: Callable[..., None] | None = _configurable(config).get("progress")
    if callback is not None:
        callback(event, section_id, payload)


# ── Subgrafo de sección ─────────────────────────────────────────────────────


def generate(state: SectionState, config: RunnableConfig) -> dict[str, Any]:
    """Agente generador: primer borrador o reescritura con el feedback anterior."""
    brief = state["brief"]
    iteration = state.get("iteration", 0) + 1
    _progress(config, "drafting", brief.section_id, {"iteration": iteration})
    result = generate_section(
        brief,
        drafter=_configurable(config).get("drafter"),
        previous_draft=state.get("draft"),
        evaluation=state.get("evaluation"),
    )
    entry = {
        "iteration": iteration,
        "content": result["content"],
        "generator": result["generator"],
        "generated_at": _now_iso(),
    }
    _progress(config, "evaluating", brief.section_id, entry)
    return {
        "iteration": iteration,
        "draft": result["content"],
        "generator": result["generator"],
        "drafts": [*state.get("drafts", []), entry],
    }


def readability(state: SectionState) -> dict[str, Any]:
    return {"readability": evaluate_readability(state["draft"])}


def relevance(state: SectionState) -> dict[str, Any]:
    return {"relevance": evaluate_relevance(state["draft"], state["brief"])}


def compliance(state: SectionState) -> dict[str, Any]:
    return {"compliance": evaluate_compliance(state["draft"], state["brief"])}


def aggregate(state: SectionState, config: RunnableConfig) -> dict[str, Any]:
    """Funde los tres evaluadores en un EvaluationResult."""
    result = combine(
        brief=state["brief"],
        iteration=state["iteration"],
        readability=state["readability"],
        relevance=state["relevance"],
        compliance=state["compliance"],
    ).to_dict()
    evaluations = [*state.get("evaluations", []), result]
    _progress(config, "evaluated", state["brief"].section_id, {
        "iteration": state["iteration"],
        "draft": state["draft"],
        "drafts": state.get("drafts", []),
        "evaluation": result,
        "evaluations": evaluations,
    })
    return {"evaluation": result, "evaluations": evaluations}


def route_after_evaluation(state: SectionState) -> str:
    """Pasa → fin; falla con iteraciones disponibles → generador; límite → fin."""
    if state["evaluation"]["overall_pass"]:
        return END
    limit = state.get("max_iterations") or MAX_GENERATION_ITERATIONS
    return END if state["iteration"] >= limit else "generate"


_section = StateGraph(SectionState)
_section.add_node("generate", generate)
_section.add_node("readability", readability)
_section.add_node("relevance", relevance)
_section.add_node("compliance", compliance)
_section.add_node("aggregate", aggregate)
_section.add_edge(START, "generate")
for _node in EVALUATOR_NODES:
    _section.add_edge("generate", _node)
_section.add_edge(list(EVALUATOR_NODES), "aggregate")
_section.add_conditional_edges("aggregate", route_after_evaluation, ["generate", END])

section_graph = _section.compile()


# ── Grafo de propuesta ──────────────────────────────────────────────────────


def section_node(department_id: str) -> str:
    return f"section_{department_id}"


def dispatch(state: ProposalState) -> list[Send] | str:
    """Un ciclo generador-evaluador por departamento del handoff, en paralelo."""
    sends = [
        Send(section_node(brief.department_id),
             {"brief": brief, "max_iterations": state.get("max_iterations")})
        for brief in state["briefs"]
    ]
    return sends or "collect"


def _failed_section(brief: SectionBrief, limit: int, exc: Exception) -> dict[str, Any]:
    """Resultado de una sección cuyo ciclo falló: va a revisión humana, no tumba al resto."""
    return {
        "section_id": brief.section_id,
        "department_id": brief.department_id,
        "owner": brief.owner,
        "iterations": 0,
        "max_iterations": limit,
        "generation_status": SECTION_NEEDS_HUMAN_REVIEW,
        "draft_content": None,
        "generator": None,
        "drafts": [],
        "evaluation": None,
        "evaluation_results": [],
        "error": f"error interno en el ciclo: {type(exc).__name__}: {exc}",
    }


def section(state: SectionState, config: RunnableConfig) -> dict[str, Any]:
    """Ejecuta el subgrafo de una sección y resume su resultado."""
    brief = state["brief"]
    limit = state.get("max_iterations") or MAX_GENERATION_ITERATIONS
    try:
        # Cada iteración son 3 supersteps (generate, evaluadores, aggregate).
        final = section_graph.invoke(
            {"brief": brief, "max_iterations": limit, "iteration": 0, "drafts": [], "evaluations": []},
            config={**config, "recursion_limit": 4 * limit + 5},
        )
    except Exception as exc:
        logger.exception("RFP propuesta: falló la sección %s", brief.section_id)
        return {"results": [_failed_section(brief, limit, exc)]}
    evaluation = final["evaluation"]
    return {"results": [{
        "section_id": brief.section_id,
        "department_id": brief.department_id,
        "owner": brief.owner,
        "iterations": final["iteration"],
        "max_iterations": limit,
        "generation_status": (
            SECTION_APROBADA_EVALUACION if evaluation["overall_pass"] else SECTION_NEEDS_HUMAN_REVIEW
        ),
        "draft_content": final["draft"],
        "generator": final["generator"],
        "drafts": final["drafts"],
        "evaluation": evaluation,
        "evaluation_results": final["evaluations"],
    }]}


def collect(state: ProposalState) -> dict[str, Any]:
    return {}


_proposal = StateGraph(ProposalState)
_SECTION_NODES = [section_node(dep) for dep in DEPARTMENT_IDS]
for _node in _SECTION_NODES:
    _proposal.add_node(_node, section)
    _proposal.add_edge(_node, "collect")
_proposal.add_node("collect", collect)
_proposal.add_conditional_edges(START, dispatch, [*_SECTION_NODES, "collect"])
_proposal.add_edge("collect", END)

proposal_graph = _proposal.compile()


def run_proposal_graph(
    briefs: list[SectionBrief],
    *,
    drafter: Any = None,
    progress: Callable[[str, str, dict[str, Any]], None] | None = None,
    max_iterations: int | None = None,
) -> list[dict[str, Any]]:
    """Genera y evalúa todas las secciones; devuelve un resultado por sección (orden del handoff)."""
    final = proposal_graph.invoke(
        {"briefs": briefs, "max_iterations": max_iterations or MAX_GENERATION_ITERATIONS, "results": []},
        config={"configurable": {"drafter": drafter, "progress": progress}},
    )
    order = {b.section_id: i for i, b in enumerate(briefs)}
    return sorted(final["results"], key=lambda r: order.get(r["section_id"], len(order)))


__all__ = [
    "EVALUATOR_NODES",
    "section_node",
    "section_graph",
    "proposal_graph",
    "run_proposal_graph",
    "route_after_evaluation",
]
