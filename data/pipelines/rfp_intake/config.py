"""Configuración del pipeline de recepción de RFPs (Hito 9, Parte 1).

Centraliza el vocabulario del tramo de intake — identificadores de
departamento (BRIEFING §2.1), estados de ticket (BRIEFING §2.3), mapeo de
moneda según país del cliente (BRIEFING §5) y rutas de datos locales.

La regla estricta de capas (Brifing-pipeline.md §5, mismo espíritu en
trackflow.BRIEFING.md §2.4) se respeta aquí: ``services/`` importa desde este
paquete, nunca al revés. Este módulo NO importa nada de ``services/``.
"""

from __future__ import annotations

import os
from pathlib import Path

# ── Pipeline ────────────────────────────────────────────────────────────────

PIPELINE_NAME = "rfp_intake"

# ── Departamentos (BRIEFING §2.1 — identificadores exactos) ─────────────────

DEPARTMENT_IDS: tuple[str, ...] = ("warehouse", "lastmile", "reverse")

DEPARTMENT_LABELS: dict[str, str] = {
    "warehouse": "Warehouse Operations",
    "lastmile": "Last Mile and Carrier Management",
    "reverse": "Reverse Logistics",
}

DEPARTMENT_OWNERS: dict[str, str] = {
    "warehouse": "Ana Whitfield",
    "lastmile": "Carlos Vega",
    "reverse": "Sofía Ramos",
}


def approver_emails(department_id: str) -> frozenset[str]:
    """Cuentas (email del JWT) que deciden la sección del departamento (Parte 3).

    ``RFP_APPROVER_EMAILS=warehouse=ana@x;lastmile=carlos@x;reverse=sofia@x,suplente@x``.
    Un departamento sin cuentas configuradas no lo decide nadie por la API
    (fail-closed). Se lee en cada llamada para poder cambiarlo sin reiniciar.
    """
    for entry in os.getenv("RFP_APPROVER_EMAILS", "").split(";"):
        dep, _, emails = entry.partition("=")
        if dep.strip() == department_id:
            return frozenset(e.strip().lower() for e in emails.split(",") if e.strip())
    return frozenset()

# Mapeo servicio solicitado → department_id (BRIEFING §2.1).
SERVICE_TO_DEPARTMENT: dict[str, str] = {
    "warehouse": "warehouse",
    "lastmile": "lastmile",
    "reverse": "reverse",
}

# Qué aporta cada departamento a la propuesta (BRIEFING §2.1): es lo que
# Ventas debe pedir a cada responsable en el resumen del synthesizer.
DEPARTMENT_ASKS: dict[str, str] = {
    "warehouse": "capacidad de almacenamiento, costo por pallet/SKU y tiempo de onboarding",
    "lastmile": "costo por envío, transportistas disponibles según destino y SLA de entrega",
    "reverse": "costo y tiempo de procesamiento de devoluciones",
}

# Lineamientos de propuesta (BRIEFING §5) que la Parte 2 debe cumplir; viajan
# en el handoff para que los generadores/evaluadores los reciban explícitos.
PROPOSAL_GUIDELINES: tuple[str, ...] = (
    "Precio en la moneda del país del cliente (US→USD, España→EUR).",
    "Indicar el SLA de entrega a tiempo (%) comprometido.",
    "No prometer procesamiento de devoluciones en menos de 48 horas.",
    "Incluir una tabla de descuentos por volumen.",
    "No revelar tarifas negociadas con transportistas; solo el costo final.",
)

# ── Países y moneda (BRIEFING §5 — la moneda se determina por client_country) ──

COUNTRY_CURRENCY: dict[str, str] = {
    "US": "USD",
    "USA": "USD",
    "United States": "USD",
    "EE. UU.": "USD",
    "Spain": "EUR",
    "España": "EUR",
}

# ── Estados de ticket (BRIEFING §2.3 — Parte 1) ─────────────────────────────

STATUS_ANALIZANDO = "analizando"
STATUS_DESCARTADO = "descartado"
STATUS_ANALISIS_COMPLETO = "analisis_completo"

# Parte 2 (generación + evaluación).
STATUS_GENERANDO_BORRADOR = "generando_borrador"
STATUS_EN_EVALUACION = "en_evaluación"
STATUS_NEEDS_HUMAN_REVIEW = "needs_human_review"
# Parte 3 (aprobación); la Parte 2 deja aquí los tickets con todas las
# secciones aprobadas por los evaluadores.
STATUS_ESPERANDO_APROBACION = "esperando_aprobación"
STATUS_TERMINADO = "terminado"

VALID_STATUSES: frozenset[str] = frozenset(
    {
        STATUS_ANALIZANDO,
        STATUS_DESCARTADO,
        STATUS_ANALISIS_COMPLETO,
        STATUS_GENERANDO_BORRADOR,
        STATUS_EN_EVALUACION,
        STATUS_NEEDS_HUMAN_REVIEW,
        STATUS_ESPERANDO_APROBACION,
        STATUS_TERMINADO,
    }
)

# ── Parte 2: ciclo generador-evaluador ──────────────────────────────────────

# Límite de iteraciones generador-evaluador por sección (KPI §3: ideal < 2).
# Al agotarlo sin pasar, la sección queda ``needs_human_review`` con su último
# borrador + EvaluationResult y viaja igual a la Parte 3.
MAX_GENERATION_ITERATIONS = int(os.getenv("RFP_MAX_GENERATION_ITERATIONS", "3"))

# Estado de cada DepartmentSection durante la Parte 2
# (``department_sections.generation_status``).
SECTION_GENERANDO = "generando_borrador"
SECTION_EN_EVALUACION = "en_evaluación"
SECTION_APROBADA_EVALUACION = "evaluacion_superada"
SECTION_NEEDS_HUMAN_REVIEW = "needs_human_review"

# Formato esperado de cada sección de propuesta económica: encabezados en este
# orden. ``{label}`` y ``{currency}`` se rellenan por sección; el evaluador de
# cumplimiento verifica que estén todos (regla ``section-format``).
SECTION_HEADINGS: tuple[str, ...] = (
    "## Propuesta económica — {label}",
    "### Alcance",
    "### Precios ({currency})",
    "### SLA comprometido",
    "### Descuentos por volumen",
    "### Información pendiente del cliente",
    "### Responsable",
)

# ── Parte 3: aprobación humana por departamento ─────────────────────────────

# ``department_sections.approval_status``.
APPROVAL_PENDIENTE = "pendiente"
APPROVAL_CAMBIOS_SOLICITADOS = "cambios_solicitados"
APPROVAL_APROBADA = "aprobada"
APPROVAL_RECHAZADA = "rechazada"

# Respuestas humanas admitidas al reanudar una sección interrumpida.
DECISION_APPROVE = "approve"
DECISION_REJECT = "reject"
DECISION_REQUEST_CHANGES = "request_changes"
APPROVAL_DECISIONS: tuple[str, ...] = (DECISION_APPROVE, DECISION_REJECT, DECISION_REQUEST_CHANGES)

# Límite del ciclo request_changes → reescritura → nueva aprobación por
# sección (cuenta también las reescrituras que fuerza el arbitraje). Al
# agotarlo la sección queda rechazada.
MAX_APPROVAL_ROUNDS = int(os.getenv("RFP_MAX_APPROVAL_ROUNDS", "3"))
# Límite de rondas de arbitraje entre departamentos por ticket. Si el
# conflicto sigue tras el límite, el árbitro rechaza las secciones ofensoras.
MAX_ARBITRATION_ROUNDS = int(os.getenv("RFP_MAX_ARBITRATION_ROUNDS", "3"))

# Árbitro fijo (BRIEFING §7). No hay escalera jerárquica: solo responsables
# pares por departamento (§6) y Miguel Torres como árbitro de los triggers.
COMMERCIAL_DIRECTOR = "Miguel Torres"

# Triggers de conflicto (BRIEFING §7): se detectan en estado estructurado y
# los resuelve el nodo de arbitraje con una regla determinista.
TRIGGER_VOLUME_VS_CAPACITY = "volume-vs-capacity"
TRIGGER_RETURNS_SLA_BREACH = "returns-sla-breach"
TRIGGER_CURRENCY_MISMATCH = "currency-mismatch"
CONFLICT_TRIGGERS: dict[str, dict[str, str]] = {
    TRIGGER_VOLUME_VS_CAPACITY: {
        "arbiter": COMMERCIAL_DIRECTOR,
        "rule": "Limitar el volumen de la propuesta a la capacidad de warehouse; "
                "lastmile revisa volumen/costo a la baja.",
    },
    TRIGGER_RETURNS_SLA_BREACH: {
        # Sofía Ramos rechaza su propia sección; en otra sección arbitra Miguel Torres.
        "arbiter": "Sofía Ramos / Miguel Torres",
        "rule": "request_changes forzado en toda sección que prometa devoluciones en "
                "menos de 48 h; sin documento final hasta cumplir.",
    },
    TRIGGER_CURRENCY_MISMATCH: {
        "arbiter": COMMERCIAL_DIRECTOR,
        "rule": "Reescribir las secciones ofensoras a la moneda del país del cliente; "
                "rechazar si no se resuelve tras el límite de iteraciones.",
    },
}


def ticket_thread_id(ticket_id: str) -> str:
    """``thread_id`` del grafo coordinador de un ticket (checkpoint por ticket)."""
    return f"rfp-{ticket_id}"


def department_thread_id(ticket_id: str, department_id: str) -> str:
    """``thread_id`` de la rama de aprobación de un departamento."""
    return f"rfp-{ticket_id}:{department_id}"


# ── Rutas de datos locales ──────────────────────────────────────────────────
# Solo artefactos de archivo (PDFs). Ticket/metadatos/secciones se persisten
# en PostgreSQL (ver db.py); no hay estado local en archivos.

REPO_ROOT = Path(__file__).resolve().parents[3]

# PDFs subidos (BRIEFING §2.4 — artefacto runtime de recepción, data/raw/).
DEFAULT_RAW_RFP_DIR = REPO_ROOT / "data" / "raw" / "rfp"

# Carpeta con los PDF de prueba del BRIEFING (§4).
ENV_RFP_SAMPLES_DIR = os.getenv("RFP_SAMPLES_DIR", "")
DEFAULT_SAMPLES_DIR = REPO_ROOT / "rfp-requests" / "trackflow"


def raw_rfp_dir() -> Path:
    """Directorio runtime donde se guardan los PDFs recibidos (BRIEFING §2.4)."""
    return DEFAULT_RAW_RFP_DIR


def samples_dir() -> Path:
    """Directorio con los PDF de prueba (BRIEFING §4)."""
    if ENV_RFP_SAMPLES_DIR:
        return Path(ENV_RFP_SAMPLES_DIR)
    return DEFAULT_SAMPLES_DIR