"""Subflow de extracción (E) — envuelve ``extract_telemetry_events`` como ``@flow``.

Este subflow recibe la ventana temporal como ``ExtractInput`` y devuelve
los eventos extraídos como ``ExtractOutput``. Reutiliza la función
``extract_telemetry_events`` (ya marcada como ``@task`` de Prefect),
de modo que el subflow orquesta la tarea con inputs/outputs explícitos.
"""

from __future__ import annotations

import logging
from typing import Any

from prefect import flow

from data.pipelines.weekly_warehouse_client_performance.extract import (
    extract_telemetry_events,
)
from data.pipelines.weekly_warehouse_client_performance.subflows.models import (
    ExtractInput,
    ExtractOutput,
)

logger = logging.getLogger(__name__)


@flow(
    name="extract-subflow",
    description=(
        "Subflow de extracción: lee telemetry_events (solo lectura) "
        "en la ventana [start, end) y devuelve eventos listos para transformar."
    ),
    log_prints=True,
)
def extract_subflow(inputs: ExtractInput) -> ExtractOutput:
    """Ejecuta la extracción de eventos de telemetría.

    Parameters
    ----------
    inputs : ExtractInput
        Ventana temporal con ``start`` (inclusivo) y ``end`` (exclusivo).

    Returns
    -------
    ExtractOutput
        Eventos extraídos con conteo total.
    """
    logger.info(
        "Subflow extract: window [%s, %s).",
        inputs.start.isoformat(),
        inputs.end.isoformat(),
    )

    raw: list[dict[str, Any]] = extract_telemetry_events(inputs.start, inputs.end)

    result = ExtractOutput(raw_events=raw, event_count=len(raw))
    logger.info("Subflow extract: %d event(s) extracted.", result.event_count)
    return result