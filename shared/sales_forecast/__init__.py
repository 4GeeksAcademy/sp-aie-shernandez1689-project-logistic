"""Paquete compartido para el modelo de pronóstico de ventas de TrackFlow.

Contiene las métricas de evaluación (MSE, PSI, Gini, K2) y la construcción de
features causales (lags, ventanas rodantes y calendario/estacionalidad) que se
reutilizan desde ``scripts/sales_forecast_train.py`` y los tests en
``tests/pipelines/``.

Referencia de negocio: ``trackflow-BRIEFING.es.md`` — Modelo de pronóstico de
series de tiempo para predicción de ventas.
"""

from shared.sales_forecast.features import build_features, validate_dataset
from shared.sales_forecast.metrics import (
    gini_coefficient,
    k2_score,
    mean_squared_error,
    population_stability_index,
)

__all__ = [
    "build_features",
    "validate_dataset",
    "gini_coefficient",
    "k2_score",
    "mean_squared_error",
    "population_stability_index",
]