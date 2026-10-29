"""Triggers de conflicto entre departamentos (Hito 9, Parte 3 — BRIEFING §7).

Los conflictos se detectan en **estado estructurado**: de cada sección se
extraen hechos deterministas (``section_facts``: monedas cotizadas, promesas
de devolución < 48 h, capacidad comprometida por warehouse, volumen asumido
por lastmile) y ``detect_conflicts`` los compara. Ningún agente negocia: los
generadores solo escriben su sección y el nodo de arbitraje aplica la regla
fija de cada trigger con su árbitro nombrado.

Cada conflicto lleva ``offenders`` (secciones que deben cambiar), el árbitro
de cada una y las ``constraints`` estructuradas que la reescritura debe
respetar (``currency``, ``volume_cap``).
"""

from __future__ import annotations

import re
from typing import Any

from .config import (
    COMMERCIAL_DIRECTOR,
    CONFLICT_TRIGGERS,
    COUNTRY_CURRENCY,
    DEPARTMENT_OWNERS,
    TRIGGER_CURRENCY_MISMATCH,
    TRIGGER_RETURNS_SLA_BREACH,
    TRIGGER_VOLUME_VS_CAPACITY,
)
from .evaluators import _CURRENCY_TOKENS, _check_returns_48h

_NUMBER = r"(\d{1,3}(?:[.,]\d{3})+|\d+)"
_PER_MONTH = r"\s*(?:pedidos|env[ií]os|[oó]rdenes)\s*(?:/\s*mes|al\s+mes|por\s+mes|mensuales)"
_CAPACITY = re.compile(r"capacidad[^.\n]*?" + _NUMBER + _PER_MONTH, re.I)
_VOLUME_CAP = re.compile(r"(?:se\s+)?limita(?:do|da)?\s+a\s+" + _NUMBER + _PER_MONTH, re.I)
_VOLUME = re.compile(_NUMBER + _PER_MONTH, re.I)


def _to_int(raw: str) -> int:
    return int(re.sub(r"[.,]", "", raw))


def section_currencies(draft: str) -> list[str]:
    """Monedas que aparecen en la sección (encabezado de precios incluido)."""
    return sorted(code for code, pattern in _CURRENCY_TOKENS.items() if pattern.search(draft or ""))


def committed_capacity(draft: str) -> int | None:
    """Capacidad mensual que compromete warehouse (pedidos/mes), si la declara."""
    match = _CAPACITY.search(draft or "")
    return _to_int(match.group(1)) if match else None


def assumed_volume(draft: str) -> int | None:
    """Volumen mensual que asume lastmile: el tope ofertado o, si no, el de la RFP."""
    match = _VOLUME_CAP.search(draft or "") or _VOLUME.search(draft or "")
    return _to_int(match.group(1)) if match else None


def section_facts(department_id: str, draft: str) -> dict[str, Any]:
    """Hechos estructurados de una sección sobre los que disparan los triggers."""
    facts: dict[str, Any] = {
        "currencies": section_currencies(draft),
        "returns_breaches": _check_returns_48h(draft or ""),
    }
    if department_id == "warehouse":
        facts["committed_capacity"] = committed_capacity(draft)
    if department_id == "lastmile":
        facts["assumed_volume"] = assumed_volume(draft)
    return facts


def expected_currency(client_country: str | None, fallback: str | None = None) -> str | None:
    """Moneda que manda el país del cliente (US→USD, Spain→EUR)."""
    return COUNTRY_CURRENCY.get(client_country or "") or fallback


def _conflict(trigger: str, offenders: dict[str, str], detail: str,
              constraints: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "trigger": trigger,
        "rule": CONFLICT_TRIGGERS[trigger]["rule"],
        # Árbitro fijo por sección ofensora.
        "arbiters": offenders,
        "offenders": list(offenders),
        "detail": detail,
        "constraints": constraints or {},
    }


def detect_conflicts(sections: dict[str, dict[str, Any]], currency: str | None) -> list[dict[str, Any]]:
    """Conflictos §7 entre las secciones activas no rechazadas.

    ``sections``: ``department_id → {"draft", "approval_status"}``.
    ``currency``: moneda del país del cliente.
    """
    active = {
        dep: section_facts(dep, s.get("draft") or "")
        for dep, s in sections.items()
        if s.get("approval_status") != "rechazada"
    }
    conflicts: list[dict[str, Any]] = []

    # volume-vs-capacity: warehouse no soporta el volumen que asume lastmile.
    capacity = active.get("warehouse", {}).get("committed_capacity")
    volume = active.get("lastmile", {}).get("assumed_volume")
    if capacity is not None and volume is not None and volume > capacity:
        conflicts.append(_conflict(
            TRIGGER_VOLUME_VS_CAPACITY,
            {"lastmile": COMMERCIAL_DIRECTOR},
            f"lastmile asume {volume} pedidos/mes y warehouse compromete {capacity}.",
            {"lastmile": {"volume_cap": capacity}},
        ))

    # returns-sla-breach: Sofía Ramos para su sección, Miguel Torres para el resto.
    for dep, facts in active.items():
        if facts["returns_breaches"]:
            arbiter = DEPARTMENT_OWNERS["reverse"] if dep == "reverse" else COMMERCIAL_DIRECTOR
            conflicts.append(_conflict(
                TRIGGER_RETURNS_SLA_BREACH,
                {dep: arbiter},
                f"{dep}: " + "; ".join(facts["returns_breaches"]),
            ))

    # currency-mismatch: moneda ≠ país del cliente, o dos secciones con monedas distintas.
    quoted = {dep: facts["currencies"] for dep, facts in active.items()}
    if currency:
        offenders = [dep for dep, codes in quoted.items() if any(c != currency for c in codes)]
    else:
        distinct = {tuple(codes) for codes in quoted.values() if codes}
        offenders = list(quoted) if len(distinct) > 1 else []
    if offenders:
        conflicts.append(_conflict(
            TRIGGER_CURRENCY_MISMATCH,
            {dep: COMMERCIAL_DIRECTOR for dep in offenders},
            "monedas cotizadas: " + ", ".join(
                f"{dep}={'/'.join(quoted[dep]) or '—'}" for dep in offenders
            ) + f"; moneda del cliente: {currency or 'sin determinar'}.",
            {dep: {"currency": currency} for dep in offenders} if currency else {},
        ))
    return conflicts


__all__ = [
    "section_currencies",
    "committed_capacity",
    "assumed_volume",
    "section_facts",
    "expected_currency",
    "detect_conflicts",
]
