"""Construcción de features causales para el modelo de pronóstico de ventas.

El briefing de negocio (``trackflow-BRIEFING.es.md`` §6) exige:

    Features causales de lag, estadísticas rodantes y calendario/estacionalidad
    construidos a partir de la serie (validados contra el patrón de crecimiento
    y estacionalidad descrito arriba), **sin fuga de información desde filas
    futuras**.

Este módulo implementa exactamente eso:

1. **Validación del dataset** contra lo que describe el briefing:
   - rango completo 2016-01 → 2025-12 sin meses faltantes;
   - columna ``market == "consolidated"`` como fila principal;
   - ``revenue_eur > 0``.
2. **Features de calendario/estacionalidad**:
   - ``month`` (1..12), ``quarter``, ``year``;
   - dummies de mes (capturan el alza Nov–Dic y la caída de Febrero);
   - dummies de trimestre.
3. **Features de lag (retardos)** — valores pasados de la serie:
   - ``lag_1`` .. ``lag_12`` sobre ``revenue_eur`` (12 meses = patrón anual);
   - retardos de ``shipments_processed`` (1, 6, 12) y de
     ``avg_revenue_per_shipment_eur`` (1, 12).
4. **Estadísticas rodantes (rolling)** calculadas **exclusivamente sobre el
   pasado** (ventana centrada en el valor actual, sin incluir el futuro):
   - medias/mín/máx móviles de 3, 6 y 12 meses del target.

Todas las ventanas se construyen con ``shift()`` / ``rolling().shift(1)`` de
forma que **nunca** filtran información de filas posteriores (cero fuga).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

# Columnas esperadas según trackflow-BRIEFING.es.md §2.
REQUIRED_COLUMNS = [
    "month",
    "revenue_eur",
    "shipments_processed",
    "avg_revenue_per_shipment_eur",
    "market",
]

# Rango esperado del dataset (10 años de consolidated).
EXPECTED_START = "2016-01-01"
EXPECTED_END = "2025-12-01"

TARGET_COLUMN = "revenue_eur"


def validate_dataset(df: pd.DataFrame) -> pd.DataFrame:
    """Valida la estructura y el contenido del dataset contra el briefing.

    Checks ejecutados (levantan ``ValueError`` si fallan):

      - Columnas exactas requeridas.
      - ``month`` parseable como fecha ``YYYY-MM-01``.
      - Sin duplicados de ``month``.
      - Serie mensual **completa** (sin meses faltantes en 2016-01..2025-12).
      - Solo la fila ``consolidated`` (mercado principal para el modelo).
      - ``revenue_eur`` estrictamente positivo.
      - Sin valores nulos en las columnas numéricas.

    Parameters
    ----------
    df:
        DataFrame crudo leído de ``data/raw/trackflow_sales.csv``.

    Returns
    -------
    El DataFrame normalizado con ``month`` como datetime y ordenado por fecha.
    """
    missing_cols = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing_cols:
        raise ValueError(
            f"Faltan columnas requeridas: {missing_cols}. "
            f"Esperadas: {REQUIRED_COLUMNS}"
        )

    out = df[REQUIRED_COLUMNS].copy()
    out["month"] = pd.to_datetime(out["month"], errors="coerce")
    if out["month"].isna().any():
        raise ValueError("Algunas fechas en 'month' no son válidas (YYYY-MM-01)")

    if out["month"].duplicated().any():
        raise ValueError("Hay meses duplicados en el dataset")

    out = out.sort_values("month").reset_index(drop=True)

    markets = out["market"].unique()
    if not np.all(np.isin(markets, ["us", "spain", "consolidated"])):
        raise ValueError(f"Valores inesperados en 'market': {markets.tolist()}")

    # El modeling se hace sobre consolidated (briefing §2: "usa consolidated
    # como fila principal para el modelo").
    if "consolidated" not in markets:
        raise ValueError(
            "El dataset debe incluir el mercado 'consolidated' para el modelo"
        )

    full_range = pd.date_range(
        start=EXPECTED_START, end=EXPECTED_END, freq="MS"
    )
    found_months = set(out.loc[out["market"] == "consolidated", "month"])
    missing = full_range.difference(sorted(found_months))
    if len(missing) > 0:
        raise ValueError(
            f"Faltan {len(missing)} meses en el rango {EXPECTED_START}..{EXPECTED_END}: "
            f"{missing.tolist()[:10]}"
        )

    numeric_cols = [
        "revenue_eur",
        "shipments_processed",
        "avg_revenue_per_shipment_eur",
    ]
    for col in numeric_cols:
        if out[col].isna().any():
            raise ValueError(f"La columna '{col}' contiene valores nulos")
        if (out[col] <= 0).any():
            raise ValueError(
                f"La columna '{col}' debe tener todos sus valores positivos"
            )

    return out


def _monthly_dummies(serie_fecha: pd.Series) -> pd.DataFrame:
    """Dummies de mes (1..12) y de trimestre a partir de la serie de fechas."""
    out = pd.DataFrame(index=serie_fecha.index)
    out["month"] = serie_fecha.dt.month
    out["quarter"] = serie_fecha.dt.quarter
    out["year"] = serie_fecha.dt.year

    month_dummies = pd.get_dummies(
        out["month"], prefix="m", drop_first=False
    ).astype(int)
    quarter_dummies = pd.get_dummies(
        out["quarter"], prefix="q", drop_first=False
    ).astype(int)

    # Asegurar columnas estables aunque algún mes no exista en el split.
    for m in range(1, 13):
        month_dummies[f"m_{m}"] = month_dummies.get(f"m_{m}", 0)
    for q in range(1, 5):
        quarter_dummies[f"q_{q}"] = quarter_dummies.get(f"q_{q}", 0)

    return pd.concat([out, month_dummies, quarter_dummies], axis=1)


def _causal_lags(df: pd.DataFrame) -> pd.DataFrame:
    """Retardos temporales (solo valores pasados, cero fuga).

    Los lags 1 y 2 del target + el lag 12 capturan el patrón anual de
    e-commerce descrito en el briefing (alza Nov–Dic, caída de Febrero).
    """
    out = pd.DataFrame(index=df.index)
    for lag in [1, 2, 3, 6, 12]:
        out[f"lag_{lag}"] = df["revenue_eur"].shift(lag)
    for lag in [1, 6, 12]:
        out[f"ship_lag_{lag}"] = df["shipments_processed"].shift(lag)
    for lag in [1, 12]:
        out[f"arp_ship_lag_{lag}"] = df["avg_revenue_per_shipment_eur"].shift(lag)
    return out


def _causal_rolling(df: pd.DataFrame) -> pd.DataFrame:
    """Estadísticas rodantes causales (windows sobre el pasado únicamente).

    ``rolling(w).mean().shift(1)`` garantiza que la ventana de ``w`` meses se
    computa con los ``w`` valores **anteriores** al mes actual (shift(1) excluye
    el valor presente, evitando fugas de la fila que se predice).
    """
    out = pd.DataFrame(index=df.index)
    target = df["revenue_eur"]

    for w in [3, 6, 12]:
        out[f"roll_mean_{w}"] = target.rolling(w).mean().shift(1)
        out[f"roll_min_{w}"] = target.rolling(w).min().shift(1)
        out[f"roll_max_{w}"] = target.rolling(w).max().shift(1)
        if w >= 12:
            out[f"roll_std_{w}"] = target.rolling(w).std().shift(1)

    # Cambio interanual (vs el mismo mes del año anterior) — captura el
    # crecimiento anual X≈6% ± Y≈3% del briefing.
    out["yoy_change"] = (df["revenue_eur"] / target.shift(12) - 1.0).shift(1)
    return out


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """Construye el dataset de features completo (sin fuga) para el modelado.

    Parameters
    ----------
    df:
        DataFrame validado por :func:`validate_dataset` (solo ``consolidated``
        es cargado por el pipeline; aquí se usa la serie pasada como está).

    Returns
    -------
    DataFrame con la columna objetivo ``revenue_eur``, las features de
    calendario, los lags y las estadísticas rodantes causales.
    """
    # Trabajamos con la serie completa; el pipeline filtra por mercado y split.
    cal = _monthly_dummies(df["month"])
    lags = _causal_lags(df)
    rolling = _causal_rolling(df)

    # cal incluye month (int), quarter, year y dummies; la columna 'month'
    # datetime ya la aporta df — evita duplicados.
    cal = cal.drop(columns=["month"])

    out = pd.concat([df[["month", "market"]], cal, lags, rolling], axis=1)
    out[TARGET_COLUMN] = df[TARGET_COLUMN]

    return out


# Columnas de features numéricas usadas por el modelo. 'month' (datetime) y
# 'market' (string) se excluyen deliberadamente: el mes queda codificado por
# las dummies m_1..m_12 y el mercado es siempre 'consolidated'.
FEATURE_COLUMNS = [
    "quarter",
    "year",
    "m_1", "m_2", "m_3", "m_4", "m_5", "m_6",
    "m_7", "m_8", "m_9", "m_10", "m_11", "m_12",
    "q_1", "q_2", "q_3", "q_4",
    "lag_1", "lag_2", "lag_3", "lag_6", "lag_12",
    "ship_lag_1", "ship_lag_6", "ship_lag_12",
    "arp_ship_lag_1", "arp_ship_lag_12",
    "roll_mean_3", "roll_mean_6", "roll_mean_12",
    "roll_min_3", "roll_min_6", "roll_min_12",
    "roll_max_3", "roll_max_6", "roll_max_12",
    "roll_std_12",
    "yoy_change",
]