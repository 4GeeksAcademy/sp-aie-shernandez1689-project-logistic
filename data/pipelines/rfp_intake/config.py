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

# Mapeo servicio solicitado → department_id (BRIEFING §2.1).
SERVICE_TO_DEPARTMENT: dict[str, str] = {
    "warehouse": "warehouse",
    "lastmile": "lastmile",
    "reverse": "reverse",
}

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

# Estados definidos para Partes 2 y 3 (reservados; no se usan todavía).
STATUS_GENERANDO_BORRADOR = "generando_borrador"
STATUS_EN_EVALUACION = "en_evaluación"
STATUS_NEEDS_HUMAN_REVIEW = "needs_human_review"
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

# ── Rutas de datos locales (modo sin-DB para desarrollo/validación) ─────────
# Mismo patrón que data/pipelines/weekly_warehouse_client_performance/db.py:
# con DATABASE_URL se persiste en PostgreSQL; sin ella se escribe en data/eval.

REPO_ROOT = Path(__file__).resolve().parents[3]

# PDFs subidos (BRIEFING §2.4 — artefacto runtime de recepción, data/raw/).
DEFAULT_RAW_RFP_DIR = REPO_ROOT / "data" / "raw" / "rfp"

# Estado persistido en modo local (sin DATABASE_URL) — data/eval/rfp_intake/.
DEFAULT_EVAL_RFP_DIR = REPO_ROOT / "data" / "eval" / "rfp_intake"

# Carpeta con los PDF de prueba del BRIEFING (§4).
ENV_RFP_SAMPLES_DIR = os.getenv("RFP_SAMPLES_DIR", "")
DEFAULT_SAMPLES_DIR = REPO_ROOT / "rfp-requests" / "trackflow"


def raw_rfp_dir() -> Path:
    """Directorio runtime donde se guardan los PDFs recibidos (BRIEFING §2.4)."""
    return DEFAULT_RAW_RFP_DIR


def eval_rfp_dir() -> Path:
    """Directorio de estado persistido en modo local (sin DATABASE_URL)."""
    return DEFAULT_EVAL_RFP_DIR


def samples_dir() -> Path:
    """Directorio con los PDF de prueba (BRIEFING §4)."""
    if ENV_RFP_SAMPLES_DIR:
        return Path(ENV_RFP_SAMPLES_DIR)
    return DEFAULT_SAMPLES_DIR