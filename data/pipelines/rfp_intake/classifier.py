"""Clasificador y extractor de metadatos del tramo de intake de RFPs.

Responsabilidades (BRIEFING §2.1–§2.3, Parte 1):

1. **Clasificación** — decide si el documento es una RFP de TrackFlow
   (aceptar) o no (rechazar). Distingue RFP **formal**, RFP **informal**
   y documento **inválido** (p. ej. pitch entrante de un transportista
   vendiendo tarifas a TrackFlow, no una RFP de cliente).

2. **Extracción de metadatos** — `client_name`, `client_country`,
   `services_requested`, `monthly_volume`, `deadline`, `budget_range`,
   `departments_needed` y métricas de legibilidad (estas últimas las añade
   el pipeline a partir de `readability_metrics.py`).

3. **Enrutamiento por departamento** — decide qué departamentos activa la
   RFP según el alcance solicitado (BRIEFING §2.1): un cliente que usa su
   propio transportista NO activa `lastmile`.

Es un clasificador **determinista basado en reglas** (sin LLM en esta Parte 1):
más fácil de testear, auditable y suficiente para los 3 documentos de prueba
del BRIEFING §4. La extracción de servicios/país/volumen usa señales léxicas
bilingües (es/en) y nunca inventa datos que no aparecen en el texto.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# ── Decisión de clasificación ────────────────────────────────────────────────


@dataclass
class ClassificationResult:
    """Resultado del clasificador sobre el texto de un documento."""

    is_rfp: bool = False
    doc_type: str = "unknown"  # formal | informal | invalid | unknown
    reason: str = ""
    client_name: str | None = None
    client_country: str | None = None
    services_requested: list[str] = field(default_factory=list)
    monthly_volume: int | None = None
    deadline: str | None = None
    budget_range: str | None = None
    departments_needed: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_rfp": self.is_rfp,
            "doc_type": self.doc_type,
            "reason": self.reason,
            "client_name": self.client_name,
            "client_country": self.client_country,
            "services_requested": self.services_requested,
            "monthly_volume": self.monthly_volume,
            "deadline": self.deadline,
            "budget_range": self.budget_range,
            "departments_needed": self.departments_needed,
        }


# ── Señales léxicas bilingües (es/en) ────────────────────────────────────────

_RFP_HEADER_RE = re.compile(
    r"(request\s+for\s+proposal|solicitud\s+de\s+propuesta|"
    r"\bRFP\b|request\s+for\s+quotation|RFQ)",
    re.IGNORECASE,
)

# Título explícito de RFP al inicio del documento ("Request for Proposal — …",
# "Solicitud de propuesta …"). A diferencia de _RFP_HEADER_RE no acepta un
# "RFP" suelto en el cuerpo: solo la frase completa en la cabecera.
_RFP_TITLE_RE = re.compile(
    r"request\s+for\s+(proposal|quotation)|solicitud\s+de\s+(propuesta|cotizaci[oó]n)",
    re.IGNORECASE,
)
_TITLE_WINDOW_CHARS = 300

# Señales de "documento NO es RFP de cliente": un transportista/vendor que
# VENDE tarifas A TrackFlow (inbound pitch), no un prospecto tercerizando.
_VENDOR_PITCH_RE = re.compile(
    r"(we\s+(are|could|would)\s+(like|love|want)\s+to\s+(offer|partner|work)|"
    r"our\s+(price|rate|tarifa|cost)\s*(card|list|sheet)|"
    r"partner\s+carrier\s+(rates?|tariffs)|"
    r"these\s+are\s+our\s+(rates|prices)|"
    r"los\s+siguientes\s+precios|nuestras\s+tarifas|"
    r"as\s+your\s+logistics\s+partner|queremos\s+ofrecerte)",
    re.IGNORECASE,
)

# Señales de cliente tercerizando: pide servicios de logística para SU negocio.
_CLIENT_RFP_RE = re.compile(
    r"(we\s+(need|are\s+looking\s+for|want|require)|"
    r"estamos\s+buscando|necesitamos|"
    r"we\s+are\s+(a|an)\s+(fashion|cosmetics|electronic|e-?commerce)\s+(brand|company)|"
    r"somos\s+una\s+(marca|empresa)|"
    r"we\s+would\s+like\s+to\s+outsource|"
    r"queremos\s+tercerizar|outsource\s+our)",
    re.IGNORECASE,
)

# Servicios solicitados (bilingüe). Orden importa: "last mile" antes de "mile".
_SERVICE_PATTERNS: list[tuple[str, re.Pattern]] = [
    (
        "warehouse",
        re.compile(
            r"warehouse|warehousing|storage|almacen|almacenamiento|"
            r"almacenaje|deposito|fulfillment\s+center",
            re.IGNORECASE,
        ),
    ),
    (
        "lastmile",
        re.compile(
            r"last[- ]?mile|última\s+mill[aá]|ultima\s+milla|entrega\s+final|"
            r"last[- ]?mile\s+delivery|delivery\s+network",
            re.IGNORECASE,
        ),
    ),
    (
        "reverse",
        re.compile(
            r"reverse[- ]?logistics|returns?|devolucion(es)?|log[ií]stica\s+inversa|"
            r"product\s+reconditioning|reacondicionamiento",
            re.IGNORECASE,
        ),
    ),
]

_SERVICE_LABELS: dict[str, str] = {
    "warehouse": "warehousing",
    "lastmile": "last-mile delivery",
    "reverse": "reverse logistics",
}

# País → moneda (BRIEFING §5). Orden: patrones específicos primero.
_COUNTRY_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("US", re.compile(r"\bUS\b|United States|EE\.?\s?UU|EEUU|Los Angeles", re.IGNORECASE)),
    ("Spain", re.compile(r"Spain|España|Espana|Zaragoza|Madrid|Barcelona", re.IGNORECASE)),
]

# Volumen mensual: "X orders per month", "X pedidos al mes", etc.
_VOLUME_PATTERNS = [
    re.compile(r"(\d{1,3}(?:[.,]\d{3})*)\s*(?:orders|order|pedidos?|env[ií]os)\s*(?:per|por|a|al)\s*(?:month|mes)", re.IGNORECASE),
    re.compile(r"(?:monthly|mensual(?:es)?|al\s+mes|per\s+month)[^\d]{0,20}(\d{1,3}(?:[.,]\d{3})*)", re.IGNORECASE),
    re.compile(r"(\d{1,3}(?:[.,]\d{3})*)\s+(?:orders|pedidos?)\s*/\s*(?:month|mes)", re.IGNORECASE),
]

_DEADLINE_PATTERNS = [
    re.compile(r"(?:deadline|fecha\s+limite|fecha\s+l[ií]mite|fecha\s+tope)[^\n]{0,40}(\d{4}-\d{2}-\d{2})", re.IGNORECASE),
    re.compile(r"(\d{4}-\d{2}-\d{2})", re.IGNORECASE),
    re.compile(r"(?:deadline|fecha\s+limite|due\s+date)[^\n]{0,20}((?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|ene|feb|mar|abr|may|jun|jul|ago|sep|oct|nov|dic)[a-z]*\s+\d{1,2},?\s+\d{4})", re.IGNORECASE),
]

_BUDGET_PATTERNS = [
    re.compile(r"(?:budget|presupuesto)[^\n]{0,40}((?:US\$|USD|EUR|€|[$€])\s?\d{1,3}(?:[.,]\d{3})*(?:\s?[-–—to]\s?(?:US\$|USD|EUR|€|[$€])?\s?\d{1,3}(?:[.,]\d{3})*)?)", re.IGNORECASE),
    re.compile(r"((?:US\$|USD|EUR|€|[$€])\s?\d{1,3}(?:[.,]\d{3})*(?:\s?[-–—to]\s?(?:US\$|USD|EUR|€|[$€])?\s?\d{1,3}(?:[.,]\d{3})*)?\s?(?:budget|presupuesto)?)", re.IGNORECASE),
]


def _normalize_volume(raw: str) -> int | None:
    """'5.000' -> 5000 ; '5,000' -> 5000 ; '5000' -> 5000."""
    cleaned = raw.replace(".", "").replace(",", "").strip()
    if not cleaned.isdigit():
        return None
    return int(cleaned)


def _detect_services(text: str) -> list[str]:
    """Detecta los servicios solicitados; respeta el orden canónico del BRIEFING."""
    found: list[str] = []
    for dep_id, pattern in _SERVICE_PATTERNS:
        if pattern.search(text):
            found.append(dep_id)
    return found


def _detect_country(text: str) -> str | None:
    """Detecta el país de origen del cliente (EE. UU. o España)."""
    for country, pattern in _COUNTRY_PATTERNS:
        if pattern.search(text):
            return country
    return None


def _detect_volume(text: str) -> int | None:
    """Detecta el volumen mensual estimado (pedidos/mes)."""
    for pattern in _VOLUME_PATTERNS:
        match = pattern.search(text)
        if match:
            raw = next((g for g in match.groups() if g), None)
            if raw:
                normalized = _normalize_volume(raw)
                if normalized is not None and normalized > 0:
                    return normalized
    return None


def _detect_deadline(text: str) -> str | None:
    """Detecta la fecha límite de la propuesta."""
    for pattern in _DEADLINE_PATTERNS:
        match = pattern.search(text)
        if match:
            return next((g for g in match.groups() if g), match.group(0)).strip()
    return None


def _detect_budget(text: str) -> str | None:
    """Detecta el rango de presupuesto de referencia."""
    for pattern in _BUDGET_PATTERNS:
        match = pattern.search(text)
        if match:
            value = next((g for g in match.groups() if g), None)
            if value:
                return value.strip()
    return None


def classify(text: str) -> ClassificationResult:
    """Clasifica un documento (texto extraído) como RFP de TrackFlow o no.

    Lógica (determinista, Parte 1):
    1. Texto vacío → rechazar (``unknown``).
    2. Señal de **pitch entrante de vendor/transportista** (vende tarifas A
       TrackFlow) → rechazar con ``doc_type='invalid'`` (BRIEFING §4, doc 3).
    3. Señal de **cliente tercerizando** (frase de cliente o título
       explícito de RFP en la cabecera) → aceptar. El tipo se decide por la
       estructura del encabezado: ``formal`` si trae cabecera de RFP explícita
       ("Request for Proposal", "Solicitud de propuesta", "RFP"); si no,
       ``informal`` (p. ej. un correo pidiendo servicios — BRIEFING §4, doc 2).
    4. Sin señales claras → rechazar (``unknown``) para no aceptar ruido.

    Returns
    -------
    ClassificationResult
        Decisión y metadatos extraídos del texto.
    """
    result = ClassificationResult()

    cleaned = (text or "").strip()
    if not cleaned:
        result.reason = "documento vacío o sin texto extraíble"
        return result

    # 1) ¿Es un pitch entrante de un vendor vendiéndonos tarifas?
    if _VENDOR_PITCH_RE.search(cleaned):
        result.is_rfp = False
        result.doc_type = "invalid"
        result.reason = (
            "documento entrante de un transportista/vendor con tarifas "
            "propias — no es una RFP de cliente tercerizando logística"
        )
        return result

    # 2) ¿Es un cliente tercerizando su logística? Vale la frase de cliente
    #    ("necesitamos", "we need"…) o un título explícito de RFP en la
    #    cabecera: una RFP formal puede no usar ninguna frase gatillo.
    has_rfp_title = bool(_RFP_TITLE_RE.search(cleaned[:_TITLE_WINDOW_CHARS]))
    if not (_CLIENT_RFP_RE.search(cleaned) or has_rfp_title):
        result.is_rfp = False
        result.doc_type = "unknown"
        result.reason = "sin señales de RFP de cliente de TrackFlow"
        return result

    # Aceptar: es una RFP de cliente.
    result.is_rfp = True
    result.doc_type = "formal" if _RFP_HEADER_RE.search(cleaned) else "informal"

    # ── Metadatos ─────────────────────────────────────────────────────
    result.services_requested = _detect_services(cleaned)
    # Si el cliente declara usar su propio transportista, la mención a la
    # última milla es una exclusión ("NO está incluida"), no un servicio
    # solicitado: se quita de services_requested y, por tanto, del enrutado.
    if _OWN_CARRIER_RE.search(cleaned):
        result.services_requested = [
            s for s in result.services_requested if s != "lastmile"
        ]
    result.client_country = _detect_country(cleaned)
    result.monthly_volume = _detect_volume(cleaned)
    result.deadline = _detect_deadline(cleaned)
    result.budget_range = _detect_budget(cleaned)

    # Nombre del cliente: primera línea que parezca un nombre de marca.
    result.client_name = _detect_client_name(cleaned)

    # ── Enrutamiento por departamento (BRIEFING §2.1) ─────────────────
    # Solo se activan los departamentos que el alcance solicita realmente.
    result.departments_needed = [
        dep_id for dep_id in result.services_requested if dep_id in _SERVICE_LABELS
    ]

    if not result.departments_needed:
        result.reason = "RFP de cliente sin servicios logísticos detectables"
        result.is_rfp = False
        result.doc_type = "unknown"
        return result

    # Razón legible para el ticket.
    deps = ", ".join(result.departments_needed)
    result.reason = (
        f"RFP {'formal' if result.doc_type == 'formal' else 'informal'} de "
        f"{result.client_name or 'cliente'} — departamentos activos: {deps}"
    )

    return result


# Cliente usa su propio transportista → NO activar lastmile (BRIEFING §2.1).
_OWN_CARRIER_RE = re.compile(
    r"(own\s+(carrier|delivery|transport)|in[- ]?house\s+(carrier|delivery)|"
    r"propio\s+transportista|transportista\s+propio|"
    r"we\s+use\s+our\s+own\s+(carrier|delivery)|usamos\s+nuestro)"
    r"|(delivery|last[- ]?mile)[^.]{0,60}(own\s+carrier|propio\s+transportista)",
    re.IGNORECASE,
)


# Palabras que casi nunca son nombre de marca: no devolverlas como client_name.
_CLIENT_NAME_STOPWORDS = frozenset(
    {
        # en
        "proposal", "quotations", "quotation", "rfp", "rfq", "request",
        "contact", "the", "and", "for", "our", "your", "us", "we", "are",
        "budget", "deadline", "services", "service", "monthly", "orders",
        # es
        "propuesta", "solicitud", "rfp", "contacto", "la", "el", "los", "las",
        "de", "y", "nuestra", "nuestro", "presupuesto", "plazo", "servicios",
        "pedidos", "mensual", "precio", "tarifa",
    }
)


def _is_plausible_name(candidate: str) -> bool:
    """True si el candidato parece un nombre de marca (no stopword ni ruido)."""
    candidate = candidate.strip()
    if not candidate or len(candidate) < 3:
        return False
    if "\n" in candidate or "\t" in candidate:
        return False
    tokens = candidate.split()
    # La primera palabra DEBE empezar en mayúscula (comparación sensible a
    # mayúsculas). Sin esto, con re.IGNORECASE, "for last mile" capturaría
    # "last mile" (lowercase) como nombre de marca.
    if not tokens[0][0].isupper():
        return False
    first_word = tokens[0].lower().strip(":.,;")
    if first_word in _CLIENT_NAME_STOPWORDS:
        return False
    # Al menos una palabra con inicial en mayúscula.
    return any(tok[:1].isupper() for tok in tokens)


def _detect_client_name(text: str) -> str | None:
    """Intenta inferir el nombre de la marca del cliente.

    Estrategia determinista:
    1. Etiquetas explícitas: "Client/Cliente/Company/Empresa/For/De <Name>".
    2. "Somos/We are <Name>".
    3. Nombre propio seguido de verbo de solicitud ("needs", "busca", ...).
    Aplica filtro de stopwords y rechaza candidatos que cruzan saltos de
    línea. Devuelve ``None`` si no hay señal segura (nunca inventa).
    """
    # IMPORTANTE: las palabras clave van en grupos inline sin distinguir
    # mayúsculas, pero el nombre capturado se busca con [A-Z] SENSIBLE a
    # mayúsculas y sin salto de línea en el interior. Con re.IGNORECASE,
    # "[A-Z]..." también casaría "last mile" (lowercase) y cruzaría líneas,
    # capturando ruido como "for PROPOSAL\nClient:" de una sola vez.
    patterns = [
        re.compile(
            r"(?i:client|cliente|company|empresa)\s*[:：]?\s*"
            r"([A-Z][A-Za-z0-9&.']+(?:[ \t]+[A-Z][A-Za-z0-9&.']+){0,2})"
        ),
        re.compile(
            r"(?i:somos|we are)\s+([A-Z][A-Za-z0-9&.']+(?:[ \t]+[A-Z][A-Za-z0-9&.']+){1,2})"
        ),
        re.compile(
            r"([A-Z][A-Za-z0-9&.']+(?:[ \t]+[A-Z][A-Za-z0-9&.']+){0,2})\s+"
            r"(?i:is\s+(?:a|an)\s+(?:fashion|cosmetics|electronic|e-?commerce)|"
            r"solicita|busca|necesita|requests|needs|would\s+like)"
        ),
    ]
    for pattern in patterns:
        for match in pattern.finditer(text):
            candidate = match.group(1).strip()
            if _is_plausible_name(candidate):
                return candidate
    return None


__all__ = ["ClassificationResult", "classify"]