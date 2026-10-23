"""Validación cruzada temporal para el pronóstico de ventas de TrackFlow.

La validación cruzada estándar (``KFold``/``ShuffleSplit``) **baraja** los datos
y es inválida para series temporales: un fold de prueba caería cronológicamente
antes (o entre) los folds de entrenamiento, y el modelo vería el futuro.

Este módulo implementa una validación cruzada **temporal y sin fuga**, alineada
con el hito de pronóstico de ``trackflow-BRIEFING.es.md`` §6:

- ``time_series_split_indices``: folds con ``TimeSeriesSplit`` de scikit-learn.
  Garantiza que cada bloque de entrenamiento es un **prefijo cronológico
  estricto** del conjunto y que la validación es **inmediatamente posterior**
  al último mes de entrenamiento. No baraja ni mezcla: se valida por
  construcción y mediante el test
  ``tests/pipelines/test_sales_forecast_cv.py``.

- **Regla anti-fuga de lags/rolling entre folds**: las features causales se
  calculan **por fold** sobre el prefijo histórico `series.iloc[:val_end]`
  (todo lo anterior a la validación, con su ventana de lag incluida). Como
  todos los lags/rolling miran solo hacia atrás (`shift`/`rolling`), esto es
  exactamente la información disponible en producción en el momento de predecir
  la validación. Luego:

    - train del fold = filas anteriores al inicio de la validación
      (índices `< val_start`)  → **nunca** usa filas de validación;
    - validación = filas del fold (`val_start..val_end`) → sus ``lag_12``/``roll_*``
      apuntan a meses dentro del entrenamiento o de la historia, **no al futuro
      del fold**.

  Si en lugar de esto se calcularan los lags sobre la serie completa y después
  se dividiera, una ventana de ``lag_12`` de la **primera** fila de un fold
  futuro podría asomarse a valores futuros al rellenar NaN de forma incorrecta,
  o —al usar el bloque completo— contaminar las features de train con la
  distribución de validación. Aquí se evita por construcción.

Convención de dominio (briefing):
  - dataset: ``data/raw/trackflow_sales.csv``, mercado ``consolidated``,
    target ``revenue_eur``.
  - Rango: 2016-01 → 2025-12 (120 meses), 8 años train / 2 años test.
"""

from __future__ import annotations

from typing import Iterator

import numpy as np
import pandas as pd
from sklearn.model_selection import TimeSeriesSplit
from sklearn.preprocessing import StandardScaler

from shared.sales_forecast.features import FEATURE_COLUMNS, TARGET_COLUMN, build_features
from shared.sales_forecast.metrics import (
    gini_coefficient,
    k2_score,
    mean_squared_error,
    population_stability_index,
)

# Hiperparámetros del modelo (congruentes con scripts/sales_forecast_train.py).
RANDOM_STATE = 42
N_FOLDS = 5
MAX_LAG = 12  # mayor retardo usado por las features (lag_12 / roll_*_12).


def time_series_split_indices(
    n: int,
    n_splits: int = N_FOLDS,
    test_size: int | None = None,
    gap: int = 0,
) -> Iterator[tuple[np.ndarray, np.ndarray]]:
    """Folds temporales (índices) preservando el orden cronológico.

    Parámetros
    ----------
    n:
        Número total de filas (meses) de la serie.
    n_splits:
        Número de folds (por defecto 5, coherente con el hito).
    test_size:
        Tamaño del bloque de validación. Si es ``None`` lo calcula
        ``TimeSeriesSplit`` como ``n // (n_splits + 1)``.
    gap:
        Hueco deseado (filas) entre train y validación; por defecto 0.

    Yields
    ------
    (train_idx, val_idx): arrays de índices **ordenados y copiados** (inmunes a
    mutación externa). Por construcción ``max(train_idx) < min(val_idx)`` y
    ambas secuencias son estrictamente crecientes.
    """
    tss = TimeSeriesSplit(n_splits=n_splits, test_size=test_size, gap=gap)
    for train_idx, val_idx in tss.split(np.zeros((n, 1))):
        yield (
            np.sort(np.asarray(train_idx, dtype=int)),
            np.sort(np.asarray(val_idx, dtype=int)),
        )


def build_fold_features(
    series: pd.DataFrame,
    val_start: int,
    val_end: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Construye las matrices de features de un fold **sin fuga de lags/rolling**.

    Parameters
    ----------
    series:
        Serie consolidada completa (solo ``consolidated``), ordenada por fecha,
        con las columnas del briefing (``month``, ``revenue_eur``, ...).
    val_start:
        Índice (en ``series``) de la **primera** fila del fold de validación.
    val_end:
        Índice (en ``series``) de la **última** fila del fold de validación.

    Returns
    -------
    (train_feat, val_feat): DataFrames alineados con el target incluido.
    """
    # Prefijo histórico hasta el final del fold (incluye la ventana de lag de
    # la validación pero NUNCA filas posteriores al propio fold).
    prefix = series.iloc[: val_end + 1].copy()
    features = build_features(prefix)

    # Train = filas anteriores al inicio del fold de validación.
    train_feat = features.iloc[:val_start].copy().reset_index(drop=True)
    # Validación = filas del propio fold.
    val_feat = features.iloc[val_start : val_end + 1].copy().reset_index(drop=True)

    # Relleno de nulos de lags/rolling SOLO con estadísticas del train del fold
    # (mismo criterio que el pipeline original, nunca con distribución de val).
    train_feat = _fill_with_train_stats(train_feat)
    val_feat = _fill_with_train_stats(val_feat, train_ref=train_feat)

    return train_feat, val_feat


def _fill_with_train_stats(
    feat: pd.DataFrame,
    train_ref: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Rellena nulos de una matriz de features con la mediana de la referencia.

    La referencia (por defecto la propia matriz) debe ser **el bloque de
    entrenamiento** para no filtrar la distribución de validación. Las columnas
    no numéricas (``month``, ``market``) se dejan intactas.
    """
    ref = train_ref if train_ref is not None else feat
    out = feat.copy()
    for col in out.columns:
        if col in ("month", "market") or out[col].dtype == object or out[col].dtype == "datetime64[ns]":
            continue
        if out[col].isna().any():
            median = ref[col].median() if not ref[col].isna().all() else out[col].median()
            out[col] = out[col].fillna(median)
    return out


def _fit_scaler_only_train(
    X_train: np.ndarray,
    X_val: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, StandardScaler]:
    """Estandariza ajustando el scaler SOLO con el train (sin fuga de val)."""
    scaler = StandardScaler()
    X_train_s = scaler.fit_transform(X_train)
    X_val_s = scaler.transform(X_val)
    return X_train_s, X_val_s, scaler


def _model_for_cv():
    """Instancia XGBoost con la configuración ganadora documentada en el hito."""
    from xgboost import XGBRegressor

    return XGBRegressor(
        n_estimators=200,
        learning_rate=0.05,
        max_depth=6,
        subsample=0.9,
        colsample_bytree=0.9,
        random_state=RANDOM_STATE,
        objective="reg:squarederror",
        verbosity=0,
    )


def evaluate_fold(
    series: pd.DataFrame,
    train_idx: np.ndarray,
    val_idx: np.ndarray,
    model=None,
) -> dict:
    """Entrena y evalúa un fold temporal sin fuga de lags.

    Parameters
    ----------
    series:
        Serie consolidada completa (solo ``consolidated``) ordenada por mes.
    train_idx, val_idx:
        Índices cronológicos del fold (ver ``time_series_split_indices``).
    model:
        Regresor a clonar por fold; si es ``None`` usa XGBoost.

    Returns
    -------
    Dict con fechas/índices del fold, métricas de entrenamiento y validación
    (MAE, RMSE), métricas de ranking (Gini, K2, PSI) y la garantía
    ``chronological`` del orden.
    """
    if model is None:
        model = _model_for_cv()

    dates = series["month"].to_numpy()
    target = series[TARGET_COLUMN].to_numpy()

    val_start = int(val_idx.min())
    val_end = int(val_idx.max())

    # ── Anti-fuga de lags/rolling entre folds ────────────────────────────────
    train_feat, val_feat = build_fold_features(series, val_start, val_end)

    feature_cols = [c for c in FEATURE_COLUMNS if c in train_feat.columns]

    X_train = train_feat[feature_cols].to_numpy(dtype=float)
    y_train = train_feat[TARGET_COLUMN].to_numpy(dtype=float)
    X_val = val_feat[feature_cols].to_numpy(dtype=float)
    y_val = target[val_idx]

    X_train_s, X_val_s, _ = _fit_scaler_only_train(X_train, X_val)

    model.fit(X_train_s, y_train)
    y_pred_train = model.predict(X_train_s)
    y_pred_val = model.predict(X_val_s)

    mae_train = float(np.mean(np.abs(y_train - y_pred_train)))
    rmse_train = float(np.sqrt(np.mean((y_train - y_pred_train) ** 2)))
    mae_val = float(np.mean(np.abs(y_val - y_pred_val)))
    rmse_val = float(np.sqrt(mean_squared_error(y_val, y_pred_val)))

    # Garantía estructural del split: el último mes de train es anterior al
    # primer mes de validación (jamás hay solape ni barajado).
    assert int(train_idx.max()) < int(val_idx.min()), (
        "Fold no cronológico: train y validación solapan."
    )

    return {
        "fold": 0,  # se fija en run_time_series_cv
        "train_start": str(pd.to_datetime(dates[int(train_idx.min())])),
        "train_end": str(pd.to_datetime(dates[int(train_idx.max())])),
        "val_start": str(pd.to_datetime(dates[int(val_idx.min())])),
        "val_end": str(pd.to_datetime(dates[int(val_idx.max())])),
        "train_rows": int(len(train_idx)),
        "val_rows": int(len(val_idx)),
        "mse": float(mean_squared_error(y_val, y_pred_val)),
        "mae_train": mae_train,
        "rmse_train": rmse_train,
        "mae_val": mae_val,
        "rmse_val": rmse_val,
        "gini": float(gini_coefficient(y_val, y_pred_val)),
        "k2": float(k2_score(y_val, y_pred_val)),
        "psi_pred_vs_real": float(population_stability_index(y_val, y_pred_val)),
        "chronological": bool(int(train_idx.max()) < int(val_idx.min())),
    }


def run_time_series_cv(
    series: pd.DataFrame,
    n_splits: int = N_FOLDS,
    model=None,
) -> dict:
    """Ejecuta la validación cruzada temporal de ``n_splits`` folds.

    Returns
    -------
    Dict con:
      - ``folds``: lista de resultados por fold;
      - ``summary``: ``mae_mean``/``mae_std``, ``rmse_mean``/``rmse_std``,
        ``mse_mean``/``mse_std`` (media ± desviación estándar entre folds,
        no solo el agregado).
    """
    if len(series) <= (n_splits + 1) * MAX_LAG:
        raise ValueError(
            f"Serie demasiado corta ({len(series)}) para {n_splits} folds con "
            f"lag máximo {MAX_LAG}: se necesitan al menos {(n_splits + 1) * MAX_LAG} filas."
        )

    n = len(series)
    folds_meta = []
    for k, (train_idx, val_idx) in enumerate(
        time_series_split_indices(n, n_splits=n_splits), start=1
    ):
        res = evaluate_fold(series, train_idx, val_idx, model=model)
        res["fold"] = k
        folds_meta.append(res)

    mae_vals = [f["mae_val"] for f in folds_meta]
    rmse_vals = [f["rmse_val"] for f in folds_meta]
    mse_vals = [f["mse"] for f in folds_meta]

    summary = {
        "n_splits": n_splits,
        "mae_mean": float(np.mean(mae_vals)),
        "mae_std": float(np.std(mae_vals, ddof=1)),
        "rmse_mean": float(np.mean(rmse_vals)),
        "rmse_std": float(np.std(rmse_vals, ddof=1)),
        "mse_mean": float(np.mean(mse_vals)),
        "mse_std": float(np.std(mse_vals, ddof=1)),
    }
    return {"folds": folds_meta, "summary": summary}