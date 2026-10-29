"""Métricas de legibilidad y costo de procesamiento de una RFP.

Se calculan sobre el Markdown convertido (``extract.convert_pdf_to_markdown``)
para **anticipar el costo** de que los agentes de la Parte 2 lo procesen:

- **Volumen** (siempre disponible): ``num_chars``, ``num_words``,
  ``num_sentences``, ``avg_words_per_sentence`` y ``estimated_tokens``
  (≈ caracteres / 4, heurística estándar para modelos tipo GPT/Claude).
- **Índices de legibilidad** (``py-readability-metrics``): ARI, Coleman-Liau,
  Dale-Chall, Flesch, Flesch-Kincaid, Gunning Fog, Linsear Write, SMOG. La
  librería exige ≥ 100 palabras (SMOG ≥ 30 frases); por debajo devuelve
  ``None``. El tokenizador es NLTK (inglés): en español son orientativos.
- **``processing_cost``**: ``bajo`` | ``medio`` | ``alto``. Sale de
  ``estimated_tokens`` (< 1.500 bajo, < 6.000 medio, resto alto); un texto
  ``medio`` sube a ``alto`` si es denso (Flesch-Kincaid ≥ 14 o > 25
  palabras/frase). En textos cortos la densidad no se aplica: el costo es
  despreciable y los índices ingleses sobrestiman el grado del español.

Nunca deja caer el pipeline por una métrica.
"""

from __future__ import annotations

import logging
import math
import re
from typing import Any

from readability import Readability
from readability.exceptions import ReadabilityException

logger = logging.getLogger(__name__)

CHARS_PER_TOKEN = 4
COST_TIERS = ("bajo", "medio", "alto")
TOKENS_MEDIUM = 1500
TOKENS_HIGH = 6000
DENSE_GRADE = 14
DENSE_WORDS_PER_SENTENCE = 25

_SCORERS = (
    "ari",
    "coleman_liau",
    "dale_chall",
    "flesch",
    "flesch_kincaid",
    "gunning_fog",
    "linsear_write",
    "smog",
)

_MD_SYNTAX = re.compile(r"^\s{0,3}(#{1,6}\s+|[-*+]\s+|\d+\.\s+|>\s*)|[|*_`]+", re.MULTILINE)


def markdown_to_plain(markdown: str) -> str:
    """Quita la sintaxis Markdown (encabezados, listas, tablas, énfasis)."""
    return _MD_SYNTAX.sub(" ", markdown or "")


def _score(value: Any) -> float | None:
    """Normaliza el resultado de un scorer (objeto con ``.score`` o número)."""
    raw = getattr(value, "score", value)
    try:
        return round(float(raw), 2)
    except (TypeError, ValueError):
        return None


def _safe_score(readability: Readability, metric: str) -> float | None:
    try:
        return _score(getattr(readability, metric)())
    except ReadabilityException as exc:
        # Ej.: '100 words required.' en textos cortos.
        logger.debug("Métrica %s no disponible: %s", metric, exc)
        return None
    except Exception as exc:  # pragma: no cover - defensa ante versiones
        logger.debug("Error inesperado en %s: %s", metric, exc)
        return None


def processing_cost(
    estimated_tokens: int,
    flesch_kincaid: float | None,
    avg_words_per_sentence: float | None,
) -> str:
    """Nivel de costo de procesamiento a partir de tamaño y densidad."""
    if estimated_tokens < TOKENS_MEDIUM:
        tier = 0
    elif estimated_tokens < TOKENS_HIGH:
        tier = 1
    else:
        tier = 2
    dense = (flesch_kincaid is not None and flesch_kincaid >= DENSE_GRADE) or (
        avg_words_per_sentence is not None
        and avg_words_per_sentence > DENSE_WORDS_PER_SENTENCE
    )
    if tier == 1 and dense:
        tier = 2
    return COST_TIERS[tier]


def compute_readability_metrics(markdown: str) -> dict[str, Any]:
    """Calcula volumen, índices de legibilidad y costo de procesamiento.

    Returns
    -------
    dict
        ``num_chars``, ``num_words``, ``num_sentences``,
        ``avg_words_per_sentence``, ``estimated_tokens``, los ocho índices
        (``float | None``) y ``processing_cost``.
    """
    plain = markdown_to_plain(markdown).strip()
    num_chars = len(plain)
    estimated_tokens = math.ceil(num_chars / CHARS_PER_TOKEN)
    stats: dict[str, Any] = {
        "num_chars": num_chars,
        "num_words": len(plain.split()),
        "num_sentences": None,
        "avg_words_per_sentence": None,
        "estimated_tokens": estimated_tokens,
        **{metric: None for metric in _SCORERS},
    }

    if plain:
        try:
            readability = Readability(plain)
            raw = readability.statistics()
            stats["num_words"] = raw.get("num_words", stats["num_words"])
            stats["num_sentences"] = raw.get("num_sentences")
            avg = raw.get("avg_words_per_sentence")
            stats["avg_words_per_sentence"] = round(avg, 2) if avg is not None else None
            for metric in _SCORERS:
                stats[metric] = _safe_score(readability, metric)
        except Exception as exc:  # tokenización falla en textos raros
            logger.warning("No se pudo calcular la legibilidad: %s", exc)

    stats["processing_cost"] = processing_cost(
        estimated_tokens, stats["flesch_kincaid"], stats["avg_words_per_sentence"]
    )
    return stats


__all__ = ["compute_readability_metrics", "markdown_to_plain", "processing_cost"]
