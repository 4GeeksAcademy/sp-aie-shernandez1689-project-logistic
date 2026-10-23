"""Métricas de evaluación para el modelo de pronóstico de ventas de TrackFlow.

Implementa las cuatro métricas exigidas por el briefing de negocio
(``trackflow-BRIEFING.es.md`` §3):

- ``mean_squared_error``        → MSE en EUR² y como % del ingreso mensual medio.
- ``population_stability_index`` → PSI: detecta cambios de distribución entre
  entrenamiento y prueba (p. ej. la mezcla LA/Zaragoza que preocupa a Thomas).
- ``gini_coefficient``          → Gini: capacidad del modelo para ordenar meses
  (separar temporada baja normal de caídas atípicas).
- ``k2_score``                 → K2: proporción de varianza de la serie explicada
  por el modelo (análogo a R² pero formulado en el espacio de la varianza).

Contexto de negocio (TrackFlow):
- El MSE se reporta en EUR² **y** como % del ingreso mensual promedio para que
  Thomas y Ana puedan interpretarlo sin conocer el cuadrado del euro.
- Un MSE bajo no basta: un modelo puede promediar bien (bajo sesgo) pero ser
  inútil para ordenar meses (Gini bajo) o anticipar cambios de volumen (PSI alto).
"""

from __future__ import annotations

from typing import Sequence

import numpy as np


# ── Utilidades internas ─────────────────────────────────────────────────────
def _as_float_array(values: Sequence[float]) -> np.ndarray:
    return np.asarray(values, dtype=float).reshape(-1)


def _buckets_from_observations(
    reference: Sequence[float],
    target: Sequence[float],
    n_buckets: int = 10,
    eps: float = 1e-6,
) -> tuple[np.ndarray, np.ndarray]:
    """Distribuye ``reference`` y ``target`` en buckets con bordes de ``reference``.

    Parameters
    ----------
    reference:
        Distribución de referencia (habitualmente los valores reales de test).
    target:
        Distribución objetivo (habitualmente los valores predichos).
    n_buckets:
        Número de buckets (default 10, estándar de PSI).
    eps:
        Suavizado de probabilidades para evitar p_log/p_base = 0.

    Returns
    -------
    (probs_ref, probs_target)
        Vectores de probabilidad por bucket para cada distribución.
    """
    ref = _as_float_array(reference)
    tgt = _as_float_array(target)

    # Bordes basados en percentiles de la distribución de referencia para que
    # cada bucket tenga masa similar en entrenamiento.
    edges = np.percentile(ref, np.linspace(0, 100, n_buckets + 1))
    edges[0] = -np.inf
    edges[-1] = np.inf

    hist_ref, _ = np.histogram(ref, bins=edges)
    hist_tgt, _ = np.histogram(tgt, bins=edges)

    p_ref = hist_ref / max(len(ref), 1)
    p_tgt = hist_tgt / max(len(tgt), 1)

    # Suavizado Laplace para evitar divisiones por cero en buckets vacíos.
    p_ref = (p_ref + eps) / (1.0 + n_buckets * eps)
    p_tgt = (p_tgt + eps) / (1.0 + n_buckets * eps)

    return p_ref, p_tgt


# ── Métricas públicas ───────────────────────────────────────────────────────
def mean_squared_error(
    y_true: Sequence[float], y_pred: Sequence[float]
) -> float:
    """Error cuadrático medio (MSE) en las unidades de la variable al cuadrado.

    Para ``revenue_eur`` la unidad es EUR². Véase
    ``mse_as_percent_of_mean`` para la interpretación porcentual.

    Parameters
    ----------
    y_true:
        Valores reales (EUR).
    y_pred:
        Valores predichos (EUR).
    """
    y_true = _as_float_array(y_true)
    y_pred = _as_float_array(y_pred)
    if len(y_true) != len(y_pred):
        raise ValueError("y_true e y_pred deben tener el mismo tamaño")
    return float(np.mean((y_true - y_pred) ** 2))


def mse_as_percent_of_mean(
    y_true: Sequence[float], y_pred: Sequence[float]
) -> float:
    """MSE expresado como % del cuadrado del ingreso mensual medio real.

    El briefing pide reportar el MSE también en porcentaje del ingreso medio
    para que el negocio (Thomas/Ana) pueda interpretarlo directamente:

        mse_pct = MSE / (mean(y_true) ** 2) * 100
    """
    y_true = _as_float_array(y_true)
    mean = float(np.mean(y_true))
    if mean == 0:
        raise ValueError("El ingreso medio no puede ser cero")
    return float(mean_squared_error(y_true, y_pred) / (mean ** 2) * 100.0)


def population_stability_index(
    expected: Sequence[float],
    observed: Sequence[float],
    n_buckets: int = 10,
    eps: float = 1e-3,
) -> float:
    """Population Stability Index (PSI).

    Mide cuánto cambió la distribución de una variable entre dos periodos.
    La convención habitual (credit-risk) es:

        PSI = Σ (p_obs - p_exp) · ln(p_obs / p_exp)

    donde ``expected`` es la distribución de referencia (p. ej. las predicciones
    o los reales del **entrenamiento**) y ``observed`` la distribución a
    comparar (p. ej. las predicciones o los reales del **test**).

    Regla de interpretación habitual:
      - PSI < 0.10   → distribución estable (el modelo ve escenarios parecidos).
      - 0.10 ≤ PSI < 0.25 → cambio moderado (revisar).
      - PSI ≥ 0.25   → cambio significativo (posible expansión/contracción de
        la operación de un país, mezcla LA vs Zaragoza, o un cambio de volumen).

    En este modelo se usa para comparar la distribución de las **predicciones
    del test** contra la distribución de los **reales del test**: un PSI alto
    indica que el modelo está desplazado en nivel (sesgo) o demasiado
    comprimido/expandido en varianza respecto a la realidad. También se reporta
    el PSI de los reales train vs reales test como referencia de cuánto cambió
    el propio fenómeno entre periodos.

    Buckets: se construyen los bordes con percentiles de ``expected`` (así cada
    bucket tiene masa similar en la referencia) y se aplican a ``observed``.
    El suavizado ``eps=1e-3`` evita que buckets casi vacíos disparen el log.
    """
    exp = _as_float_array(expected)
    obs = _as_float_array(observed)

    # Bordes basados en percentiles de la referencia.
    edges = np.percentile(exp, np.linspace(0, 100, n_buckets + 1))
    edges[0] = -np.inf
    edges[-1] = np.inf

    hist_exp, _ = np.histogram(exp, bins=edges)
    hist_obs, _ = np.histogram(obs, bins=edges)

    p_exp = (hist_exp + eps) / (len(exp) + n_buckets * eps)
    p_obs = (hist_obs + eps) / (len(obs) + n_buckets * eps)

    with np.errstate(divide="ignore", invalid="ignore"):
        psi = np.sum((p_obs - p_exp) * np.log(p_obs / p_exp))
    return float(max(psi, 0.0))


def gini_coefficient(
    y_true: Sequence[float], y_pred: Sequence[float]
) -> float:
    """Coeficiente de Gini sobre el ranking que impone el modelo.

    Contexto de negocio: Thomas quiere distinguir con confianza entre un mes de
    temporada baja normal (p. ej. febrero) y una caída atípica que amerite
    investigación. Para eso el modelo debe **ordenar** los meses por nivel de
    ingresos igual que el orden real.

    Definición usada (estándar en modelado de riesgo/credit-scoring):

        Gini = 2 · AUC - 1

    donde AUC es el área bajo la curva ROC del ranking de predicciones
    (tratando cada mes como un "caso" y comparando pares ordenados por valor
    real con pares ordenados por predicción). Interpretación:

      - Gini ≈ 1   → el modelo ordena los meses perfectamente igual que el real.
      - Gini ≈ 0   → el modelo no ordena mejor que el azar.
      - Gini < 0   → el modelo ordena peor que el azar (señal de alerta).

    Ventaja frente a la curva de Lorenz: captura directamente la "concordancia
    de ranking" (Kendall/Spearman) que es la capacidad relevante para separar
    temporada baja de caída atípica. La implementación usa la probabilidad de
    concordancia entre pares:

        AUC = P( ŷᵢ < ŷⱼ | yᵢ < yⱼ )                     (empates resueltos por 0.5)

    Fórmula equivalente computacionalmente eficiente (sin O(n²)):
        AUC = (Σᵢ rango(ŷᵢ) - n(n+1)/2) / (n_pos · n_neg)
    con los positivos definidos por umbral del valor real. En práctica, para
    una variable continua se calcula sobre todos los pares de mois.
    """
    y_true = _as_float_array(y_true)
    y_pred = _as_float_array(y_pred)
    if len(y_true) != len(y_pred):
        raise ValueError("y_true e y_pred deben tener el mismo tamaño")

    n = len(y_true)
    if n < 2:
        return 0.0

    # AUC por pares: para cada par (i,j) con y_i < y_j, verificamos si
    # ŷ_i < ŷ_j (concordancia -> +1), ŷ_i == ŷ_j (empate -> +0.5) o
    # ŷ_i > ŷ_j (discordancia -> +0).
    #   Gini = 2·AUC - 1
    concord = 0.0
    n_pairs = 0
    for i in range(n):
        for j in range(i + 1, n):
            if y_true[i] < y_true[j]:
                n_pairs += 1
                if y_pred[i] < y_pred[j]:
                    concord += 1.0
                elif y_pred[i] == y_pred[j]:
                    concord += 0.5
            elif y_true[i] > y_true[j]:
                n_pairs += 1
                if y_pred[i] > y_pred[j]:
                    concord += 1.0
                elif y_pred[i] == y_pred[j]:
                    concord += 0.5
            # y_i == y_j: no es un par informativo para el ranking.
    if n_pairs == 0:
        return 0.0
    auc = concord / n_pairs
    return float(2.0 * auc - 1.0)


def k2_score(
    y_true: Sequence[float], y_pred: Sequence[float]
) -> float:
    """K2 Score: coeficiente de determinación en el espacio de la varianza.

    Es el análogo al R² pero construido sobre la varianza de los residuales
    en lugar del error cuadrático medio:

        K2 = 1 - Var(y - ŷ) / Var(y)

    Interpretación:
      - K2 = 1.0 → el modelo explica toda la varianza de la serie.
      - K2 = 0.0 → explica tanto como predecir siempre la media.
      - K2 < 0.0 → el modelo es peor que la media (señal de fuga o sobreajuste).

    Ventaja frente a R²: es menos sensible a outliers cuadráticos y más directo
    de comunicar como "fracción de la variabilidad capturada".
    """
    y_true = _as_float_array(y_true)
    y_pred = _as_float_array(y_pred)
    if len(y_true) != len(y_pred):
        raise ValueError("y_true e y_pred deben tener el mismo tamaño")

    var_y = float(np.var(y_true))
    if var_y == 0:
        return 1.0 if np.allclose(y_true, y_pred) else 0.0
    var_resid = float(np.var(y_true - y_pred))
    return float(1.0 - var_resid / var_y)