"""Agentes evaluadores de cada sección generada (Hito 9, Parte 2).

Tres evaluadores independientes y deterministas — el grafo de la Parte 2 los
corre en paralelo sobre cada borrador:

- ``evaluate_readability`` — py-readability-metrics sobre la prosa de la
  sección (sin tablas ni encabezados): longitud, palabras por frase y grado
  Flesch-Kincaid.
- ``evaluate_relevance`` — pertinencia: la sección responde lo que su
  departamento debe aportar (BRIEFING §2.1) y lo que pide esta RFP (cliente,
  volumen, sitio, servicios de los extractos, preguntas abiertas); y no
  ofrece servicios que la RFP excluye.
- ``evaluate_compliance`` — lineamientos del BRIEFING §5 (moneda por país,
  SLA a tiempo en %, devoluciones ≥ 48 h, tabla de descuentos, sin tarifas de
  transportistas), restricciones de negocio del BRIEFING del Hito 7 §6 y el
  formato de sección (``config.SECTION_HEADINGS``).

``combine`` los funde en un ``EvaluationResult`` con ``overall_pass`` y un
``feedback_for_generator`` concreto (una acción por fallo).
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from .catalog import CARRIER_NAMES
from .config import COUNTRY_CURRENCY
from .generator import SectionBrief, section_headings
from .readability_metrics import compute_readability_metrics

# ── Resultado ───────────────────────────────────────────────────────────────


@dataclass
class ReadabilityCheck:
    passed: bool
    score: float | None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"pass": self.passed, "score": self.score, "details": self.details}


@dataclass
class RelevanceCheck:
    passed: bool
    missing_aspects: list[str] = field(default_factory=list)
    out_of_scope: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "pass": self.passed,
            "missing_aspects": self.missing_aspects,
            "out_of_scope": self.out_of_scope,
        }


@dataclass
class ComplianceCheck:
    passed: bool
    rule_ids: list[str] = field(default_factory=list)  # reglas incumplidas
    violations: list[dict[str, str]] = field(default_factory=list)
    rules_checked: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "pass": self.passed,
            "rule_ids": self.rule_ids,
            "violations": self.violations,
            "rules_checked": self.rules_checked,
        }


@dataclass
class EvaluationResult:
    """Evaluación persistida de una sección (``department_sections.evaluation_results``)."""

    section_id: str
    department_id: str
    iteration: int
    readability: dict[str, Any]
    relevance: dict[str, Any]
    compliance: dict[str, Any]
    overall_pass: bool
    feedback_for_generator: str
    evaluated_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── Legibilidad ─────────────────────────────────────────────────────────────

MIN_WORDS = 60
MAX_WORDS = 600
MAX_WORDS_PER_SENTENCE = 22
# Grado Flesch-Kincaid máximo. El silabeo de la librería es inglés y
# sobrestima el grado del español; el umbral está calibrado sobre secciones
# en español que un account manager lee sin esfuerzo.
MAX_FK_GRADE = 14.0


def prose_of(draft: str) -> str:
    """Prosa de la sección: sin tablas ni encabezados; cada viñeta es una frase."""
    lines: list[str] = []
    for raw in draft.splitlines():
        line = raw.strip()
        if not line or line.startswith(("|", "#")):
            continue
        line = re.sub(r"^[-*+]\s+", "", line)
        if not line.endswith((".", ":", "!", "?")):
            line += "."
        lines.append(line)
    return "\n".join(lines)


# Frases citadas en el feedback cuando la legibilidad falla.
MAX_QUOTED_SENTENCES = 3


def long_sentences(text: str, limit: int = MAX_WORDS_PER_SENTENCE) -> list[dict[str, Any]]:
    """Frases de la prosa con más de ``limit`` palabras, de la más larga a la más corta."""
    sentences = list(dict.fromkeys(
        s.strip() for s in re.split(r"(?<=[.!?:])\s+|\n+", text) if s.strip()
    ))
    found = [{"text": s, "words": len(s.split())} for s in sentences if len(s.split()) > limit]
    return sorted(found, key=lambda s: -s["words"])


def evaluate_readability(draft: str) -> ReadabilityCheck:
    """Legibilidad con py-readability-metrics (vía ``compute_readability_metrics``)."""
    text = prose_of(draft)
    stats = compute_readability_metrics(text)
    words = stats["num_words"]
    per_sentence = stats["avg_words_per_sentence"]
    grade = stats["flesch_kincaid"]
    problems: list[str] = []
    if words < MIN_WORDS:
        problems.append(f"sección demasiado corta ({words} palabras de prosa; mínimo {MIN_WORDS})")
    if words > MAX_WORDS:
        problems.append(f"sección demasiado larga ({words} palabras de prosa; máximo {MAX_WORDS})")
    if per_sentence is not None and per_sentence > MAX_WORDS_PER_SENTENCE:
        problems.append(
            f"frases largas ({per_sentence} palabras por frase; máximo {MAX_WORDS_PER_SENTENCE})"
        )
    if grade is not None and grade > MAX_FK_GRADE:
        problems.append(f"texto denso (grado Flesch-Kincaid {grade}; máximo {MAX_FK_GRADE})")
    details = {
        "num_words": words,
        "num_sentences": stats["num_sentences"],
        "avg_words_per_sentence": per_sentence,
        "flesch_kincaid": grade,
        "flesch": stats["flesch"],
        "thresholds": {
            "min_words": MIN_WORDS,
            "max_words": MAX_WORDS,
            "max_words_per_sentence": MAX_WORDS_PER_SENTENCE,
            "max_flesch_kincaid": MAX_FK_GRADE,
        },
        "problems": problems,
        # Las frases que hay que partir: el feedback las cita textualmente.
        "long_sentences": long_sentences(text)[:MAX_QUOTED_SENTENCES],
    }
    return ReadabilityCheck(passed=not problems, score=grade, details=details)


# ── Pertinencia ─────────────────────────────────────────────────────────────

# Lo que cada departamento aporta a la propuesta (BRIEFING §2.1).
_DEPARTMENT_ASPECTS: dict[str, tuple[tuple[str, re.Pattern], ...]] = {
    "warehouse": (
        ("capacidad de almacenamiento", re.compile(r"capacidad|posiciones de palet", re.I)),
        (
            "costo por pallet/SKU",
            re.compile(
                r"(precio|cost[eo]|tarifa|cotizar)[^\n]*(palet|pallet|SKU)|"
                r"(palet|pallet|SKU)[^\n]*(€|USD|EUR|cotizar)",
                re.I,
            ),
        ),
        ("tiempo de onboarding", re.compile(r"onboarding|puesta en marcha", re.I)),
    ),
    "lastmile": (
        ("costo por envío", re.compile(r"cost[eo] por env[ií]o|per shipment", re.I)),
        (
            "transportistas disponibles según destino",
            re.compile(r"(transportista|carrier)[^\n]*(destino|zona)", re.I),
        ),
        ("SLA de entrega", re.compile(r"plazo de entrega|SLA", re.I)),
    ),
    "reverse": (
        (
            "costo de procesamiento de devoluciones",
            re.compile(r"(cost[eo]|tarifa|precio)[^\n]*devoluci", re.I),
        ),
        (
            "tiempo de procesamiento de devoluciones",
            re.compile(r"procesamiento[^\n]*\d+\s*(horas|d[ií]as)", re.I),
        ),
    ),
}

# Servicios concretos que, si la RFP los pide en los extractos del
# departamento, la sección debe tratar.
_REQUESTED_TERMS: dict[str, tuple[tuple[re.Pattern, str, re.Pattern], ...]] = {
    "warehouse": (
        (re.compile(r"picking", re.I), "picking", re.compile(r"picking", re.I)),
        (re.compile(r"etiquetad|labell?ing", re.I), "etiquetado", re.compile(r"etiquetad", re.I)),
    ),
    "lastmile": (
        (re.compile(r"same[- ]day|mismo d[ií]a", re.I), "entrega en el mismo día",
         re.compile(r"same[- ]day|mismo d[ií]a", re.I)),
        (re.compile(r"internacional|international", re.I), "envíos internacionales",
         re.compile(r"internacional", re.I)),
    ),
    "reverse": (
        (re.compile(r"reacondicion|refurbish|recondition", re.I), "reacondicionamiento",
         re.compile(r"reacondicion", re.I)),
    ),
}

_SITES = (
    (re.compile(r"zaragoza", re.I), "Zaragoza"),
    (re.compile(r"los [aá]ngeles", re.I), "Los Angeles"),
)

# Servicio → cómo se detecta en un borrador que se ofrece.
_SERVICE_OFFER = {
    "lastmile": re.compile(r"[uú]ltima milla|last[- ]mile", re.I),
    "reverse": re.compile(r"gesti[oó]n de devoluciones|log[ií]stica inversa", re.I),
}

_STOPWORDS = frozenset(
    "de del la las el los y o a en que por para con sin al se su sus un una "
    "esperado esperada indica número".split()
)


def _digits(text: str) -> set[str]:
    return {re.sub(r"[.,]", "", n) for n in re.findall(r"\d[\d.,]*\d|\d", text)}


def _content_words(text: str) -> set[str]:
    words = re.findall(r"[a-záéíóúñü]{4,}", text.lower())
    return {w for w in words if w not in _STOPWORDS}


def _addresses(question: str, draft: str) -> bool:
    """La pregunta abierta aparece (≥ 60 % de sus palabras de contenido)."""
    words = _content_words(question)
    if not words:
        return True
    present = _content_words(draft)
    return len(words & present) / len(words) >= 0.6


def evaluate_relevance(draft: str, brief: SectionBrief) -> RelevanceCheck:
    """¿La sección responde lo que pide la RFP para este departamento?"""
    missing: list[str] = []
    for label, pattern in _DEPARTMENT_ASPECTS[brief.department_id]:
        if not pattern.search(draft):
            missing.append(label)

    if brief.client_name:
        first = brief.client_name.split()[0]
        if first.lower() not in draft.lower():
            missing.append(f"nombre del cliente ({brief.client_name})")
    if brief.monthly_volume is not None and str(brief.monthly_volume) not in _digits(draft):
        missing.append(f"volumen mensual indicado en la RFP ({brief.monthly_volume} pedidos/mes)")

    corpus = " ".join(brief.source_excerpts + brief.key_aspects)
    for found, site in _SITES:
        if found.search(corpus) and not found.search(draft):
            missing.append(f"centro de operación que menciona la RFP ({site})")
    for requested, label, answered in _REQUESTED_TERMS[brief.department_id]:
        if requested.search(" ".join(brief.source_excerpts)) and not answered.search(draft):
            missing.append(f"servicio solicitado en la RFP: {label}")

    for question in brief.open_questions:
        if not _addresses(question, draft):
            missing.append(f"pregunta abierta para el cliente: {question}")

    out_of_scope = [
        f"ofrece {service} y la RFP no lo solicita"
        for service, offered in _SERVICE_OFFER.items()
        if service != brief.department_id
        and service not in brief.services_requested
        and offered.search(re.sub(r"(?i)no est[aá] incluida[^\n]*", "", draft))
    ]
    return RelevanceCheck(
        passed=not missing and not out_of_scope,
        missing_aspects=missing,
        out_of_scope=out_of_scope,
    )


# ── Cumplimiento ────────────────────────────────────────────────────────────

# Reglas de cumplimiento: id → (origen, qué exige, cómo corregirlo).
RULES: dict[str, dict[str, str]] = {
    "currency-by-country": {
        "source": "BRIEFING §5",
        "rule": "El precio se cotiza en USD (EE. UU.) o EUR (España) según client_country.",
        "fix": "Expresa todos los importes en {currency} y elimina los importes en otra moneda.",
    },
    "on-time-sla": {
        "source": "BRIEFING §5",
        "rule": "Toda propuesta indica el SLA de entrega a tiempo (%) comprometido.",
        "fix": "Añade en 'SLA comprometido' el porcentaje de cumplimiento a tiempo del catálogo (p. ej. 'SLA de cumplimiento a tiempo del 95%').",
    },
    "returns-min-48h": {
        "source": "BRIEFING §5",
        "rule": "Ninguna propuesta promete procesamiento de devoluciones en menos de 48 horas.",
        "fix": "Sustituye el plazo de devoluciones por el del catálogo (72 horas laborables); nunca menos de 48 horas.",
    },
    "volume-discount-table": {
        "source": "BRIEFING §5",
        "rule": "Toda propuesta incluye una tabla de descuentos por volumen.",
        "fix": "Incluye en 'Descuentos por volumen' una tabla Markdown (volumen | descuento) con al menos dos tramos en %.",
    },
    "no-carrier-rates": {
        "source": "BRIEFING §5",
        "rule": "Ninguna propuesta revela tarifas negociadas con transportistas.",
        "fix": "Quita cualquier tarifa o importe asociado a un transportista; deja solo el costo final ofrecido al cliente.",
    },
    "peak-demand-sla": {
        "source": "BRIEFING Hito 7 §6",
        "rule": "Nunca prometer un SLA de entrega en fechas de alta demanda declaradas.",
        "fix": "Añade tras el SLA la excepción por picos de demanda declarados (Black Friday, Cyber Monday, rebajas, Prime Day).",
    },
    "discount-approval": {
        "source": "BRIEFING Hito 7 §6",
        "rule": "Ningún descuento se ofrece sin mencionar la aprobación de Miguel Torres.",
        "fix": "Añade bajo la tabla de descuentos que cualquier descuento fuera de ella requiere aprobación de Miguel Torres.",
    },
    "intl-returns-manual": {
        "source": "BRIEFING Hito 7 §6",
        "rule": "Las devoluciones internacionales nunca se describen como automáticas.",
        "fix": "Indica que las devoluciones internacionales se gestionan de forma manual con atención al cliente.",
    },
    "section-format": {
        "source": "Formato de sección (config.SECTION_HEADINGS)",
        "rule": "La sección usa los encabezados obligatorios, en orden.",
        "fix": "Usa exactamente estos encabezados, en este orden: {headings}.",
    },
}

_CURRENCY_TOKENS: dict[str, re.Pattern] = {
    "EUR": re.compile(r"€|\bEUR\b|\beuros?\b", re.I),
    "USD": re.compile(r"\$|\bUSD\b|d[oó]lares|\bdollars?\b", re.I),
}
_AMOUNT = re.compile(r"\d+(?:[.,]\d+)?\s*(?:€|EUR|USD|\$)|(?:€|EUR|USD|\$)\s*\d", re.I)
_NEGATION = re.compile(r"\b(no|nunca|jam[aá]s|never|not)\b", re.I)
_RETURNS_CONTEXT = re.compile(r"devoluci|\breturns?\b|reverse|reacondicion", re.I)
_HOURS = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:h|hrs?|horas?|hours?)\b", re.I)
_DAYS = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:d[ií]as?|days?)\b", re.I)
_SAME_DAY = re.compile(r"mismo d[ií]a|same[- ]day|en el d[ií]a|inmediat|al instante", re.I)
_NEGOTIATED = re.compile(
    r"tarifas?\s+negociadas?|negotiated\s+rates?|nos\s+cobra|nos\s+factura|precio\s+de\s+compra",
    re.I,
)


def _sentences(draft: str) -> list[str]:
    """Frases y filas de tabla (las filas son unidades independientes)."""
    out: list[str] = []
    for line in draft.splitlines():
        out.extend(p for p in re.split(r"(?<=[.!?])\s+", line) if p.strip())
    return out


def _check_currency(draft: str, brief: SectionBrief) -> list[str]:
    expected = brief.currency or COUNTRY_CURRENCY.get(brief.client_country or "")
    if expected not in _CURRENCY_TOKENS:
        return ["no se puede determinar la moneda: falta client_country"]
    problems: list[str] = []
    for other, pattern in _CURRENCY_TOKENS.items():
        if other == expected:
            continue
        hits = [s.strip() for s in _sentences(draft) if pattern.search(s)]
        problems += [f"importe en {other} en una propuesta en {expected}: «{h[:120]}»" for h in hits]
    if not _CURRENCY_TOKENS[expected].search(draft):
        problems.append(f"la sección no expresa ningún precio en {expected}")
    return problems


def _check_on_time_sla(draft: str) -> list[str]:
    for sentence in _sentences(draft):
        if re.search(r"\bSLA\b|a tiempo|on[- ]time|en plazo", sentence, re.I):
            for pct in re.findall(r"(\d{1,3}(?:[.,]\d+)?)\s*%", sentence):
                if 0 < float(pct.replace(",", ".")) <= 100:
                    return []
    return ["no indica el SLA de cumplimiento a tiempo en %"]


def _check_returns_48h(draft: str) -> list[str]:
    problems: list[str] = []
    for sentence in _sentences(draft):
        if not _RETURNS_CONTEXT.search(sentence) or _NEGATION.search(sentence):
            continue
        hours = [float(h.replace(",", ".")) for h in _HOURS.findall(sentence)]
        days = [float(d.replace(",", ".")) for d in _DAYS.findall(sentence)]
        if any(h < 48 for h in hours) or any(d < 2 for d in days) or _SAME_DAY.search(sentence):
            problems.append(f"promete devoluciones en menos de 48 horas: «{sentence.strip()[:120]}»")
        elif re.search(r"(menos de|under|less than|within)\s*48\s*(h|horas|hours)", sentence, re.I):
            problems.append(f"promete devoluciones en menos de 48 horas: «{sentence.strip()[:120]}»")
    return problems


def _tables(draft: str) -> list[list[str]]:
    tables, current = [], []
    for line in draft.splitlines():
        if line.strip().startswith("|"):
            current.append(line.strip())
        elif current:
            tables.append(current)
            current = []
    if current:
        tables.append(current)
    return tables


def _check_discount_table(draft: str) -> list[str]:
    for table in _tables(draft):
        if not re.search(r"descuento|discount", table[0], re.I):
            continue
        rows = [r for r in table[1:] if not re.fullmatch(r"\|[\s:|-]+", r)]
        if sum("%" in r for r in rows) >= 2:
            return []
    return ["no incluye una tabla de descuentos por volumen con al menos dos tramos"]


def _check_carrier_rates(draft: str) -> list[str]:
    carriers = re.compile(r"\b(" + "|".join(map(re.escape, CARRIER_NAMES)) + r")\b", re.I)
    problems: list[str] = []
    for sentence in _sentences(draft):
        if _NEGOTIATED.search(sentence) and not _NEGATION.search(sentence):
            problems.append(f"menciona tarifas negociadas: «{sentence.strip()[:120]}»")
        elif carriers.search(sentence) and _AMOUNT.search(sentence):
            problems.append(f"asocia un importe a un transportista: «{sentence.strip()[:120]}»")
    return problems


def _check_peak_demand(draft: str) -> list[str]:
    if not re.search(r"\bSLA\b", draft):
        return []
    if re.search(r"picos? de demanda|alta demanda|black friday|peak", draft, re.I):
        return []
    return ["promete el SLA sin la excepción por picos de demanda declarados"]


def _check_discount_approval(draft: str) -> list[str]:
    if not re.search(r"descuento|discount", draft, re.I):
        return []
    if re.search(r"aprobaci[oó]n", draft, re.I) and re.search(r"Miguel Torres", draft):
        return []
    return ["ofrece descuentos sin mencionar la aprobación de Miguel Torres"]


def _check_intl_returns(draft: str) -> list[str]:
    return [
        f"describe las devoluciones internacionales como automáticas: «{s.strip()[:120]}»"
        for s in _sentences(draft)
        if re.search(r"internacional|international", s, re.I)
        and re.search(r"autom[aá]tic", s, re.I)
        and not _NEGATION.search(s)
    ]


def _check_format(draft: str, brief: SectionBrief) -> list[str]:
    lines = [line.strip() for line in draft.splitlines()]
    position = -1
    problems: list[str] = []
    for heading in section_headings(brief):
        try:
            found = lines.index(heading)
        except ValueError:
            problems.append(f"falta el encabezado «{heading}»")
            continue
        if found < position:
            problems.append(f"encabezado fuera de orden: «{heading}»")
        position = max(position, found)
    return problems


def evaluate_compliance(draft: str, brief: SectionBrief) -> ComplianceCheck:
    """Lineamientos del BRIEFING §5 (+ Hito 7 §6 y formato de sección)."""
    checks = {
        "currency-by-country": _check_currency(draft, brief),
        "on-time-sla": _check_on_time_sla(draft),
        "returns-min-48h": _check_returns_48h(draft),
        "volume-discount-table": _check_discount_table(draft),
        "no-carrier-rates": _check_carrier_rates(draft),
        "peak-demand-sla": _check_peak_demand(draft),
        "discount-approval": _check_discount_approval(draft),
        "intl-returns-manual": _check_intl_returns(draft),
        "section-format": _check_format(draft, brief),
    }
    violations = [
        {"rule_id": rule_id, "source": RULES[rule_id]["source"], "detail": detail}
        for rule_id, problems in checks.items()
        for detail in problems
    ]
    failed = list(dict.fromkeys(v["rule_id"] for v in violations))
    return ComplianceCheck(
        passed=not failed, rule_ids=failed, violations=violations, rules_checked=list(checks)
    )


# ── Resultado combinado ─────────────────────────────────────────────────────


def _clip(text: str, limit: int = 140) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def build_feedback(
    readability: ReadabilityCheck,
    relevance: RelevanceCheck,
    compliance: ComplianceCheck,
    brief: SectionBrief,
) -> str:
    """Instrucciones concretas para el generador, una por fallo."""
    actions: list[str] = []
    quoted = "; ".join(
        f"«{_clip(s['text'])}» ({s['words']} palabras)"
        for s in readability.details.get("long_sentences", [])
    )
    for problem in readability.details.get("problems", []):
        if problem.startswith("frases largas") or problem.startswith("texto denso"):
            target = f" Empieza por estas: {quoted}." if quoted else ""
            actions.append(
                f"Legibilidad: {problem}. Divide las frases en otras de menos de "
                f"{MAX_WORDS_PER_SENTENCE} palabras.{target}"
            )
        else:
            actions.append(f"Legibilidad: {problem}.")
    for aspect in relevance.missing_aspects:
        actions.append(f"Pertinencia: falta {aspect}. Respóndelo en la sección o declaralo como pendiente.")
    for item in relevance.out_of_scope:
        actions.append(f"Pertinencia: {item}; elimínalo.")
    for rule_id in compliance.rule_ids:
        fix = RULES[rule_id]["fix"].format(
            currency=brief.currency or "la moneda del país del cliente",
            headings=" / ".join(section_headings(brief)),
        )
        details = "; ".join(v["detail"] for v in compliance.violations if v["rule_id"] == rule_id)
        actions.append(f"Cumplimiento [{rule_id}] ({details}): {fix}")
    return "\n".join(f"{i}. {a}" for i, a in enumerate(actions, 1))


def combine(
    *,
    brief: SectionBrief,
    iteration: int,
    readability: ReadabilityCheck,
    relevance: RelevanceCheck,
    compliance: ComplianceCheck,
) -> EvaluationResult:
    overall = readability.passed and relevance.passed and compliance.passed
    return EvaluationResult(
        section_id=brief.section_id,
        department_id=brief.department_id,
        iteration=iteration,
        readability=readability.to_dict(),
        relevance=relevance.to_dict(),
        compliance=compliance.to_dict(),
        overall_pass=overall,
        feedback_for_generator=(
            "" if overall else build_feedback(readability, relevance, compliance, brief)
        ),
        evaluated_at=datetime.now(timezone.utc).isoformat(),
    )


def evaluate_section(draft: str, brief: SectionBrief, iteration: int = 1) -> EvaluationResult:
    """Los tres evaluadores en secuencia (el grafo los corre en paralelo)."""
    return combine(
        brief=brief,
        iteration=iteration,
        readability=evaluate_readability(draft),
        relevance=evaluate_relevance(draft, brief),
        compliance=evaluate_compliance(draft, brief),
    )


__all__ = [
    "EvaluationResult",
    "ReadabilityCheck",
    "RelevanceCheck",
    "ComplianceCheck",
    "RULES",
    "evaluate_readability",
    "evaluate_relevance",
    "evaluate_compliance",
    "combine",
    "evaluate_section",
    "prose_of",
    "long_sentences",
]
