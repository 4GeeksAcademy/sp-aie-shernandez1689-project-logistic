"""Agente generador por departamento (Hito 9, Parte 2).

Entrada: un ``SectionBrief`` construido **solo** con el handoff de la Parte 1
(``rfp_metadata.handoff``: ticket_id, key_aspects, open_questions, moneda,
lineamientos) + los ``source_excerpts`` de su ``DepartmentSection`` y los
metadatos compartidos. El generador no abre el PDF ni el Markdown de la RFP.

Salida: el borrador en Markdown de la sección de propuesta económica del
departamento, con el formato de ``config.SECTION_HEADINGS``.

Dos redactores con la misma interfaz ``draft(brief, previous_draft, evaluation)``:

- ``TemplateDrafter`` (por defecto, determinista): compone la sección con las
  cifras del brief (cliente) y de ``catalog`` (TrackFlow). Con feedback añade
  como pendiente lo que el evaluador de pertinencia echó en falta.
- ``LLMDrafter``: mismo contrato vía API compatible con OpenAI (mismas
  variables ``LLM_API_KEY``/``LLM_MODEL_ID``/``LLM_BASE_URL`` que el RAG). En
  reintentos recibe el borrador anterior y el ``feedback_for_generator``. Si
  la llamada falla, ``generate_section`` cae al redactor de plantilla.

``RFP_GENERATOR=template|llm`` fuerza uno; sin la variable se usa el LLM solo
si hay ``LLM_API_KEY``.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

import httpx

from . import catalog
from .config import DEPARTMENT_LABELS, DEPARTMENT_OWNERS, PROPOSAL_GUIDELINES, SECTION_HEADINGS

logger = logging.getLogger(__name__)


@dataclass
class SectionBrief:
    """Resumen relevante de la Parte 1 para un departamento (entrada del generador)."""

    ticket_id: str
    rfp_id: str
    section_id: str
    department_id: str
    department_label: str
    owner: str
    currency: str | None
    client_name: str | None = None
    client_country: str | None = None
    monthly_volume: int | None = None
    deadline: str | None = None
    budget_range: str | None = None
    services_requested: list[str] = field(default_factory=list)
    key_aspects: list[str] = field(default_factory=list)
    source_excerpts: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    guidelines: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_section_briefs(
    handoff: dict[str, Any],
    metadata: dict[str, Any],
    sections: list[dict[str, Any]],
) -> list[SectionBrief]:
    """Un ``SectionBrief`` por departamento del handoff, en su orden.

    ``handoff`` manda (departamentos, key_aspects, open_questions, moneda);
    de la fila ``DepartmentSection`` solo se toman los ``source_excerpts``.
    """
    rows = {s["department_id"]: s for s in sections}
    section_ids = dict(zip(handoff.get("departments", []), handoff.get("section_ids", [])))
    briefs: list[SectionBrief] = []
    for entry in handoff.get("sections", []):
        department_id = entry["department_id"]
        row = rows.get(department_id, {})
        briefs.append(
            SectionBrief(
                ticket_id=handoff["ticket_id"],
                rfp_id=handoff["rfp_id"],
                section_id=section_ids.get(department_id) or row.get("id")
                or f"{handoff['ticket_id']}_{department_id}",
                department_id=department_id,
                department_label=DEPARTMENT_LABELS[department_id],
                owner=entry.get("owner") or DEPARTMENT_OWNERS[department_id],
                currency=handoff.get("currency") or metadata.get("currency"),
                client_name=metadata.get("client_name"),
                client_country=metadata.get("client_country"),
                monthly_volume=metadata.get("monthly_volume"),
                deadline=metadata.get("deadline"),
                budget_range=metadata.get("budget_range"),
                services_requested=list(metadata.get("services_requested") or []),
                key_aspects=list(entry.get("key_aspects") or []),
                source_excerpts=list(row.get("source_excerpts") or []),
                open_questions=list(entry.get("open_questions") or []),
                guidelines=list(handoff.get("guidelines") or PROPOSAL_GUIDELINES),
            )
        )
    return briefs


def section_headings(brief: SectionBrief) -> list[str]:
    """Encabezados obligatorios de la sección, ya rellenados."""
    currency = brief.currency or "moneda por confirmar"
    return [h.format(label=brief.department_label, currency=currency) for h in SECTION_HEADINGS]


def format_volume(value: int) -> str:
    """``3000`` → ``3.000`` (separador de miles en español)."""
    return f"{value:,}".replace(",", ".")


# ── Redactores ──────────────────────────────────────────────────────────────


class Drafter(Protocol):
    name: str

    def draft(
        self,
        brief: SectionBrief,
        previous_draft: str | None = None,
        evaluation: dict[str, Any] | None = None,
    ) -> str: ...


class DrafterError(RuntimeError):
    """El redactor no pudo producir un borrador (p. ej. fallo del LLM)."""


def _table(header: tuple[str, ...], rows: list[tuple[str, ...]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return lines


def _mentions(excerpts: list[str], *terms: str) -> bool:
    corpus = " ".join(excerpts).lower()
    return any(term in corpus for term in terms)


class TemplateDrafter:
    """Redactor determinista: plantilla + cifras del brief y del catálogo."""

    name = "template"

    def draft(
        self,
        brief: SectionBrief,
        previous_draft: str | None = None,
        evaluation: dict[str, Any] | None = None,
    ) -> str:
        h = section_headings(brief)
        currency = brief.currency
        lines: list[str] = [h[0], "", h[1], *self._scope(brief), "", h[2]]
        lines += self._prices(brief)
        lines += ["", h[3], *self._sla(brief), "", h[4], *self._discounts(brief), "", h[5]]
        pending = list(brief.open_questions)
        if evaluation:
            # Feedback del evaluador de pertinencia: lo que falta se declara
            # como pendiente en vez de inventarlo.
            for aspect in evaluation.get("relevance", {}).get("missing_aspects", []):
                pending.append(f"Por confirmar con el cliente: {aspect}.")
        if currency is None:
            pending.append("País de operación del cliente, que define la moneda de la propuesta.")
        lines += [f"- {q}" for q in dict.fromkeys(pending)] or [
            "- Ninguna: la RFP aporta los datos necesarios para esta sección."
        ]
        lines += ["", h[6], f"{brief.owner} ({brief.department_label}) valida esta sección."]
        return "\n".join(lines).strip() + "\n"

    # Secciones ---------------------------------------------------------

    def _scope(self, brief: SectionBrief) -> list[str]:
        client = brief.client_name or "el cliente"
        site = catalog.SITES.get(brief.currency or "", "nuestro centro más cercano")
        volume = (
            f" La RFP indica un volumen de {format_volume(brief.monthly_volume)} pedidos/mes."
            if brief.monthly_volume is not None
            else ""
        )
        deadline = f" Fecha límite de la propuesta: {brief.deadline}." if brief.deadline else ""
        dep = brief.department_id
        if dep == "warehouse":
            pending = catalog.PENDING_QUOTES["warehouse"]
            return [
                f"Propuesta de almacenamiento para {client} en nuestro centro de {site}.{volume}{deadline}",
                "Capacidad: reservamos las posiciones de palet que necesite su catálogo. "
                "La cifra exacta se fija al confirmar el número de SKUs.",
                pending["onboarding"],
            ]
        if dep == "lastmile":
            rows = [tuple(r) for r in catalog.CARRIERS.get(brief.currency or "", ())]
            return [
                f"Propuesta de entrega de última milla para {client} desde {site}.{volume}{deadline}",
                "Transportistas disponibles según destino:",
                "",
                *_table(("Transportista", "Zona de destino", "Servicio"), rows),
            ]
        refurbish = (
            " Incluye el reacondicionamiento de producto que pide la RFP."
            if _mentions(brief.source_excerpts, "reacondicion", "refurbish", "recondition")
            else ""
        )
        return [
            f"Propuesta de gestión de devoluciones para {client} en nuestro centro de {site}.{volume}{deadline}{refurbish}",
            *[f"- {term}" for term in catalog.RETURNS_TERMS],
            f"- {catalog.INTERNATIONAL_RETURNS_NOTE}",
        ]

    def _prices(self, brief: SectionBrief) -> list[str]:
        dep, currency = brief.department_id, brief.currency or ""
        budget = (
            [f"Presupuesto de referencia indicado en la RFP: {brief.budget_range}."]
            if brief.budget_range
            else []
        )
        if dep == "warehouse":
            prices = catalog.WAREHOUSE_PRICES.get(currency)
            if not prices:
                return [catalog.PENDING_QUOTES["warehouse"]["USD"], *budget]
            return [
                *_table(("Concepto", f"Precio ({currency})"), list(prices)),
                "",
                f"Los primeros {catalog.STORAGE_GRACE_DAYS} días de almacenaje de cada unidad no tienen cargo.",
                *budget,
            ]
        if dep == "lastmile":
            rows = list(catalog.DELIVERY_TIMES.get(currency, ()))
            return [
                catalog.PENDING_QUOTES["lastmile"]["base"],
                "",
                *_table(("Servicio", "Plazo de entrega", f"Costo ({currency})"), rows),
                *([""] + budget if budget else []),
            ]
        return [catalog.PENDING_QUOTES["reverse"]["base"], *budget]

    def _sla(self, brief: SectionBrief) -> list[str]:
        sla = catalog.ON_TIME_SLA[brief.department_id]
        return [
            f"TrackFlow se compromete a un SLA de cumplimiento a tiempo del {sla['pct']}% "
            f"en {sla['metric']}.",
            catalog.PEAK_DEMAND_NOTE,
        ]

    def _discounts(self, brief: SectionBrief) -> list[str]:
        table = catalog.VOLUME_DISCOUNTS[brief.department_id]
        note = (
            "Cualquier descuento en tarifas de devolución requiere aprobación de "
            "Miguel Torres (Dirección Comercial)."
            if brief.department_id == "reverse"
            else catalog.DISCOUNT_APPROVAL_NOTE
        )
        return [*_table((table["unit"], "Descuento"), list(table["tiers"])), "", note]


_LLM_SYSTEM_PROMPT = """Eres el redactor de propuestas económicas de TrackFlow (operador logístico binacional: Los Angeles, EE. UU. y Zaragoza, España).
Escribes UNA sección de la propuesta: la del departamento indicado. En español, frases cortas (máx. 20 palabras), tono comercial sobrio.

Formato obligatorio (Markdown, estos encabezados exactos y en este orden):
{headings}

Lineamientos obligatorios:
{guidelines}
- Nunca afirmes que las devoluciones internacionales son automáticas: se gestionan de forma manual.
- Todo descuento menciona que lo que salga de la tabla requiere aprobación de Miguel Torres.
- Menciona la excepción del SLA en picos de demanda declarados.

Datos permitidos: SOLO los del brief (cliente) y los del catálogo (TrackFlow). No inventes cifras.
Si falta un precio, escribe "a cotizar por <responsable>". Lista lo que falta en "Información pendiente del cliente".

Catálogo de TrackFlow:
{catalog}"""


def _catalog_for(brief: SectionBrief) -> dict[str, Any]:
    currency = brief.currency or ""
    dep = brief.department_id
    data: dict[str, Any] = {
        "sitio": catalog.SITES.get(currency),
        "sla_a_tiempo": catalog.ON_TIME_SLA[dep],
        "picos_de_demanda": catalog.PEAK_DEMAND_NOTE,
        "descuentos_por_volumen": catalog.VOLUME_DISCOUNTS[dep],
        "aprobacion_descuentos": catalog.DISCOUNT_APPROVAL_NOTE,
        "pendiente_de_cotizar": catalog.PENDING_QUOTES[dep],
    }
    if dep == "warehouse":
        data["precios"] = catalog.WAREHOUSE_PRICES.get(currency)
        data["dias_de_carencia"] = catalog.STORAGE_GRACE_DAYS
    elif dep == "lastmile":
        data["transportistas_por_destino"] = catalog.CARRIERS.get(currency)
        data["plazos_y_suplementos"] = catalog.DELIVERY_TIMES.get(currency)
    else:
        data["devoluciones"] = catalog.RETURNS_TERMS
        data["devoluciones_internacionales"] = catalog.INTERNATIONAL_RETURNS_NOTE
    return data


class LLMDrafter:
    """Redactor con LLM (API compatible con OpenAI, mismo proveedor que el RAG)."""

    name = "llm"

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.api_key = api_key or os.getenv("LLM_API_KEY")
        self.model = model or os.getenv("LLM_MODEL_ID", "gpt-4o-mini")
        self.base_url = base_url or os.getenv("LLM_BASE_URL", "https://api.4geeks.com/v1")
        self.timeout = timeout

    def messages(
        self,
        brief: SectionBrief,
        previous_draft: str | None = None,
        evaluation: dict[str, Any] | None = None,
    ) -> list[dict[str, str]]:
        system = _LLM_SYSTEM_PROMPT.format(
            headings="\n".join(section_headings(brief)),
            guidelines="\n".join(f"- {g}" for g in brief.guidelines),
            catalog=json.dumps(_catalog_for(brief), ensure_ascii=False, indent=1),
        )
        user = "Brief del departamento (Parte 1):\n" + json.dumps(
            brief.to_dict(), ensure_ascii=False, indent=1
        )
        if previous_draft and evaluation:
            user += (
                "\n\nTu borrador anterior NO pasó la evaluación. Reescríbelo completo "
                "corrigiendo esto:\n" + evaluation.get("feedback_for_generator", "")
                + "\n\nBorrador anterior:\n" + previous_draft
            )
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    def draft(
        self,
        brief: SectionBrief,
        previous_draft: str | None = None,
        evaluation: dict[str, Any] | None = None,
    ) -> str:
        if not self.api_key:
            raise DrafterError("LLM_API_KEY no configurada")
        payload = {
            "model": self.model,
            "messages": self.messages(brief, previous_draft, evaluation),
            "temperature": 0.2,
        }
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=payload,
                )
                resp.raise_for_status()
                content = resp.json()["choices"][0]["message"]["content"]
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            raise DrafterError(f"LLM no disponible: {type(exc).__name__}") from exc
        if not content or not content.strip():
            raise DrafterError("LLM devolvió un borrador vacío")
        return content.strip() + "\n"


def default_drafter() -> Drafter:
    """Redactor según ``RFP_GENERATOR`` (o LLM si hay ``LLM_API_KEY``)."""
    mode = os.getenv("RFP_GENERATOR", "").strip().lower()
    if mode == "llm" or (not mode and os.getenv("LLM_API_KEY")):
        return LLMDrafter()
    return TemplateDrafter()


def generate_section(
    brief: SectionBrief,
    *,
    drafter: Drafter | None = None,
    previous_draft: str | None = None,
    evaluation: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Agente generador: devuelve ``{"content", "generator"}`` para la sección.

    ``evaluation`` es el EvaluationResult del intento anterior (su
    ``feedback_for_generator`` guía la reescritura).
    """
    drafter = drafter or default_drafter()
    try:
        return {"content": drafter.draft(brief, previous_draft, evaluation), "generator": drafter.name}
    except DrafterError as exc:
        logger.warning("RFP generador %s falló para %s: %s", drafter.name, brief.section_id, exc)
        fallback = TemplateDrafter()
        return {
            "content": fallback.draft(brief, previous_draft, evaluation),
            "generator": f"{fallback.name} (fallback: {exc})",
        }


__all__ = [
    "SectionBrief",
    "build_section_briefs",
    "section_headings",
    "Drafter",
    "DrafterError",
    "TemplateDrafter",
    "LLMDrafter",
    "default_drafter",
    "generate_section",
]
