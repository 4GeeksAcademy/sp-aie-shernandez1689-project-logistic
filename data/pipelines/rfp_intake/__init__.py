"""Paquete del pipeline de intake de RFPs (TrackFlow, Hito 9 - Parte 1).

Flujo (Parte 1): upload (``register_upload``: PDF en data/raw/ + ticket
``analizando``) → en segundo plano ``analyze_ticket``: ``extract``
(PDF → Markdown con MarkItDown) (PDF → texto) → ``classify``
(RFP de TrackFlow? metadatos, departamentos) → legibilidad → persistir
ticket en PostgreSQL. Estado final ``analisis_completo`` o ``descartado``.
"""

from __future__ import annotations

from .classifier import ClassificationResult, classify
from .config import (
    COUNTRY_CURRENCY,
    DEPARTMENT_IDS,
    DEPARTMENT_LABELS,
    DEPARTMENT_OWNERS,
    DEFAULT_RAW_RFP_DIR,
    PIPELINE_NAME,
    SERVICE_TO_DEPARTMENT,
    STATUS_ANALISIS_COMPLETO,
    STATUS_ANALIZANDO,
    STATUS_DESCARTADO,
    raw_rfp_dir,
    samples_dir,
)
from .extract import ConversionUnavailableError, convert_pdf_to_markdown
from .pipeline import (
    analyze_ticket,
    process_rfp_pdf,
    register_upload,
    resolve_currency,
    store_pdf,
)

__all__ = [
    # clasificador / extractor
    "ClassificationResult",
    "classify",
    "ConversionUnavailableError",
    "convert_pdf_to_markdown",
    # orquestador
    "register_upload",
    "analyze_ticket",
    "process_rfp_pdf",
    "resolve_currency",
    "store_pdf",
    # config
    "PIPELINE_NAME",
    "DEPARTMENT_IDS",
    "DEPARTMENT_LABELS",
    "DEPARTMENT_OWNERS",
    "SERVICE_TO_DEPARTMENT",
    "COUNTRY_CURRENCY",
    "STATUS_ANALIZANDO",
    "STATUS_DESCARTADO",
    "STATUS_ANALISIS_COMPLETO",
    "DEFAULT_RAW_RFP_DIR",
    "raw_rfp_dir",
    "samples_dir",
]
