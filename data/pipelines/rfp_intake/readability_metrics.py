"""Métricas de legibilidad de una RFP (instalado vía ``uv add py-readability-metrics``).

El BRIEFING (§2.3) pide "métricas de legibilidad" en los metadatos de la RFP.
Este módulo envuelve ``readability.Readability`` de forma segura: devuelve los
índices que la librería soporta y degrada con ``None`` cuando el texto es
demasiado corto (Flesch exige ≥ 100 palabras) o cuando la librería lanza una
excepción — nunca deja caer el pipeline de intake por una métrica.

El tokenizador subyacente es NLTK (train set), aplicable a texto español
e inglés; la métrica se interpreta como orientativa, no normativa.
"""

from __future__ import annotations

import logging
from typing import Any

from readability import Readability
from readability.exceptions import ReadabilityException

logger = logging.getLogger(__name__)


def _safe_score(callable_fn, metric_name: str) -> float | None:
    """Delega en un scorer de readability y degrada a ``None`` si falla."""
    try:
        return round(float(callable_fn()), 2)
    except ReadabilityException as exc:
        # Ej.: Flesch lanza '100 words required.' en textos cortos.
        logger.debug("Métrica %s no disponible: %s", metric_name, exc)
        return None
    except Exception as exc:  # pragma: no cover - defensa ante versiones
        logger.debug("Error inesperado en %s: %s", metric_name, exc)
        return None


def compute_readability_metrics(text: str) -> dict[str, Any]:
    """Calcula las métricas de legibilidad de un texto.

    Parameters
    ----------
    text : str
        Texto de la RFP extraído del PDF.

    Returns
    -------
    dict
        Claves: num_words, num_sentences, avg_words_per_sentence, plus
        ari, coleman_liau, dale_chall, flesch, flesch_kincaid, gunning_fog,
        linsear_write, smog. Cada score es ``float | None``.
    """
    stats: dict[str, Any] = {
        "num_words": None,
        "num_sentences": None,
        "avg_words_per_sentence": None,
        "ari": None,
        "coleman_liau": None,
        "dale_chall": None,
        "flesch": None,
        "flesch_kincaid": None,
        "gunning_fog": None,
        "linsear_write": None,
        "smog": None,
    }

    cleaned = (text or "").strip()
    if not cleaned:
        return stats

    try:
        readability = Readability(cleaned)
    except Exception as exc:  # defensa: tokenización falla en textos raros
        logger.warning("No se pudo inicializar Readability: %s", exc)
        return stats

    raw_stats = readability.statistics()
    stats["num_words"] = raw_stats.get("num_words")
    stats["num_sentences"] = raw_stats.get("num_sentences")
    stats["avg_words_per_sentence"] = round(
        raw_stats.get("avg_words_per_sentence"), 2
    ) if raw_stats.get("avg_words_per_sentence") is not None else None

    stats["ari"] = _safe_score(readability.ari, "ari")
    stats["coleman_liau"] = _safe_score(readability.coleman_liau, "coleman_liau")
    stats["dale_chall"] = _safe_score(readability.dale_chall, "dale_chall")
    stats["flesch"] = _safe_score(readability.flesch, "flesch")
    stats["flesch_kincaid"] = _safe_score(readability.flesch_kincaid, "flesch_kincaid")
    stats["gunning_fog"] = _safe_score(readability.gunning_fog, "gunning_fog")
    stats["linsear_write"] = _safe_score(readability.linsear_write, "linsear_write")
    stats["smog"] = _safe_score(readability.smog, "smog")

    return stats