"""Tests unitarios del split temporal 8/2 del modelo de pronóstico de ventas.

Verifica la pieza más delicada del hito "Modelo de pronóstico de series de
tiempo" (ver ``trackflow-BRIEFING.es.md`` §3): que el split entre
entrenamiento (primeros 8 años: 2016–2023) y prueba (últimos 2 años:
2024–2025) es **estrictamente temporal, sin barajar ni solape**, de modo que
el modelo nunca ve los años de prueba durante el entrenamiento.

Cada test opera de forma aislada y en memoria, sin base de datos ni APIs
externas (misma convención que ``tests/pipelines/test_pipeline.py``).

La función bajo prueba es ``scripts.sales_forecast_train.temporal_split``,
que a su vez es la pieza que alimenta a ``build_features_matrix`` y a todo el
flujo de entrenamiento/validación.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

# ── Añadir la raíz del monorepo al sys.path ────────────────────────────────
# ``scripts/`` vive en <repo>/scripts/. Este archivo está en
# <repo>/tests/pipelines/ → parents[2] = <repo>.
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.sales_forecast_train import (  # noqa: E402
    TRAIN_YEARS,
    TEST_YEARS,
    build_features_matrix,
    load_and_prepare,
    temporal_split,
)
from shared.sales_forecast.features import (  # noqa: E402
    FEATURE_COLUMNS,
    TARGET_COLUMN,
    build_features,
)


@pytest.fixture(scope="module")
def dataset() -> pd.DataFrame:
    """Dataset real de TrackFlow ya validado y filtrado a ``consolidated``.

    Se carga una sola vez por módulo y se reutiliza en todos los tests
    (es la misma fuente que usa el pipeline de entrenamiento).
    """
    df = load_and_prepare(Path(REPO_ROOT) / "data" / "raw" / "trackflow_sales.csv")
    return df


@pytest.fixture(scope="module")
def split(dataset: pd.DataFrame):
    """Tuple (train, test) resultado de aplicar ``temporal_split``."""
    return temporal_split(dataset)


# ===================================================================
# Tests — tamaño y proporción de la partición
# ===================================================================


class TestSplitSize:
    """El split 8/2 produce exactamente 96 filas de train y 24 de test."""

    def test_total_rows_120(self, dataset: pd.DataFrame):
        """El dataset consolidado cubre 120 meses (2016-01 → 2025-12)."""
        assert len(dataset) == 120

    def test_train_has_96_rows(self, split):
        """8 años · 12 meses = 96 filas de entrenamiento."""
        train, _ = split
        assert len(train) == TRAIN_YEARS * 12 == 96

    def test_test_has_24_rows(self, split):
        """2 años · 12 meses = 24 filas de prueba."""
        _, test = split
        assert len(test) == TEST_YEARS * 12 == 24


# ===================================================================
# Tests — estricta temporalidad (sin solape ni fuga)
# ===================================================================


class TestSplitTemporal:
    """La clave del hito: el train nunca contiene meses del test."""

    def test_no_date_overlap(self, split):
        """Ninguna fecha aparece en train y test a la vez."""
        train, test = split
        train_dates = set(train["month"])
        test_dates = set(test["month"])
        overlap = train_dates & test_dates
        assert overlap == set(), f"Solape de fechas entre train y test: {overlap}"

    def test_train_is_entirely_before_test(self, split):
        """La última fecha de train es anterior a la primera del test."""
        train, test = split
        assert train["month"].max() < test["month"].min()

    def test_train_ends_dec_2023(self, split):
        """El train termina en 2023-12-01 (8 años: 2016..2023)."""
        train, _ = split
        assert train["month"].iloc[-1].strftime("%Y-%m-%d") == "2023-12-01"

    def test_test_starts_jan_2024(self, split):
        """El test empieza en 2024-01-01 (2 años: 2024..2025)."""
        _, test = split
        assert test["month"].iloc[0].strftime("%Y-%m-%d") == "2024-01-01"

    def test_no_random_shuffle(self, dataset: pd.DataFrame, split):
        """Sin barajar: el orden del dataset se conserva dentro de cada parte."""
        train, test = split
        # El primer mes del train es el más antiguo disponible (2016-01) y el
        # último del test es el más reciente (2025-12).
        assert train["month"].iloc[0].strftime("%Y-%m-%d") == "2016-01-01"
        assert test["month"].iloc[-1].strftime("%Y-%m-%d") == "2025-12-01"

    def test_strictly_monotonic_dates(self, split):
        """Dentro de cada partición las fechas son estrictamente crecientes."""
        train, test = split
        assert train["month"].is_monotonic_increasing
        assert test["month"].is_monotonic_increasing
        assert train["month"].is_unique
        assert test["month"].is_unique


# ===================================================================
# Tests — contenido de negocio (TrackFlow)
# ===================================================================


class TestSplitBusiness:
    """El split respeta el contexto del briefing de TrackFlow."""

    def test_train_covers_expected_years(self, split):
        """El train va de 2016 a 2023 (inclusive), ambos extremos presentes."""
        train, _ = split
        assert str(train["month"].min())[:4] == "2016"
        assert str(train["month"].max())[:4] == "2023"

    def test_test_covers_expected_years(self, split):
        """El test va de 2024 a 2025 (inclusive)."""
        _, test = split
        assert str(test["month"].min())[:4] == "2024"
        assert str(test["month"].max())[:4] == "2025"

    def test_market_is_consolidated(self, dataset: pd.DataFrame):
        """Todo el dataset es de mercado 'consolidated'."""
        assert set(dataset["market"].unique()) == {"consolidated"}

    def test_revenue_is_positive(self, dataset: pd.DataFrame):
        """El target (revenue_eur) es estrictamente positivo (sin ceros falsos)."""
        assert (dataset["revenue_eur"] > 0).all()


# ===================================================================
# Tests — ausencia de fuga de datos (data leakage) en features
# ===================================================================


class TestNoDataLeakageFeatures:
    """El briefing §6 exige features causales **sin fuga de información desde
    filas futuras**. Verificamos que cada lag/rolling usa solo el pasado, y que
    ninguna feature de una fila test coincide con su propio target futuro (lo
    que revelaría que la feature se construyó con el valor presente/futuro)."""

    def test_lags_use_only_past_values(self, dataset: pd.DataFrame):
        """Cada columna lag_n(t) debe ser igual al target de t-n (no de t ni de
        t+k). Un lag correcto nunca repite el value del propio mes t."""
        features = build_features(dataset)
        for lag in [1, 2, 3, 6, 12]:
            lag_col = f"lag_{lag}"
            for t in range(lag, len(dataset)):
                expected = dataset[TARGET_COLUMN].iloc[t - lag]
                assert features[lag_col].iloc[t] == pytest.approx(expected), (
                    f"{lag_col}[{t}] debe usar el valor del mes {t-lag}, "
                    f"no del mes {t}"
                )

    def test_rolling_uses_only_lagged_past(self, dataset: pd.DataFrame):
        """El rolling.mean_w(t) debe ser la media de los w meses anteriores
        (w.rolling().shift(1)), nunca incluir el mes t ni futuros."""
        features = build_features(dataset)
        for w in [3, 6, 12]:
            col = f"roll_mean_{w}"
            for t in range(w, len(dataset)):
                # Los w valores ANTERIORES a t (índices t-w .. t-1).
                expected = dataset[TARGET_COLUMN].iloc[t - w : t].mean()
                assert features[col].iloc[t] == pytest.approx(expected), (
                    f"{col}[{t}] debe ser media de {dataset[TARGET_COLUMN].iloc[t-w:t].tolist()} "
                    f"(meses {t-w}..{t-1}), sin incluir el mes {t}"
                )

    def test_yoy_change_uses_previous_year_same_month(self, dataset: pd.DataFrame):
        """yoy_change(t) = (revenue[t-1] / revenue[t-13] - 1), es decir el
        crecimiento interanual del último mes OBSERVADO (t-1) vs el mismo mes
        del año anterior (t-13), con un shift(1) final. Nunca compara contra el
        mes actual t (cero fuga)."""
        features = build_features(dataset)
        for t in range(13, len(dataset)):
            # Indexación real: (x / x.shift(12)).shift(1)
            #   (x / x.shift(12))[t-1] = revenue[t-1] / revenue[t-13]
            prev_year = dataset[TARGET_COLUMN].iloc[t - 13]
            last_observed = dataset[TARGET_COLUMN].iloc[t - 1]
            expected = (last_observed / prev_year - 1.0)
            assert features["yoy_change"].iloc[t] == pytest.approx(expected), (
                f"yoy_change[{t}] debe comparar revenue[t-1] ({last_observed}) vs "
                f"revenue[t-13] ({prev_year}), no el mes actual t"
            )

    def test_no_feature_equals_its_own_future_target(self, split, dataset: pd.DataFrame):
        """Fuga dura: para las filas de test, ninguna feature de lag/rolling
        coincide exactamente con el target del propio mes t. Si lo hiciera,
        el modelo habría "visto" el futuro y las métricas de test estarían
        contaminadas."""
        features = build_features(dataset)
        _, test = split
        for t in range(1, len(test) - 13):  # filas con lags definidos
            row_index = test.index[t]
            for col in [c for c in features.columns if c.startswith(("lag_", "roll_", "yoy"))]:
                val = features[col].iloc[row_index]
                if pd.notna(val):
                    assert val != pytest.approx(dataset[TARGET_COLUMN].iloc[row_index]), (
                        f"Feature {col} del mes test {t} coincide con su propio "
                        f"target futuro: fuga de datos"
                    )


# ===================================================================
# Tests — ausencia de fuga en la matriz de entrenamiento (escalado)
# ===================================================================


class TestNoDataLeakageMatrix:
    """El pipeline no debe dejar escapar info de test al entrenamiento:
    el StandardScaler se ajusta SOLO con el train."""

    def test_scaler_is_fit_on_train_only(self, split):
        """El escalado usa la media/desviación del train; reescalar una fila de
        test con el scaler y comparar contra el valor bruto confirma que el
        scaler conoce solo el train (las medias de train/test difieren, así que
        un scaler ajustado con test no sería consistente)."""
        train, test = split
        data = build_features_matrix(train, test)
        scaler = data["scaler"]
        # El centrado del scaler debe usar estadísticas del train: la media de
        # cada feature escalada en test no debe ser 0 (el scaler no se fijó
        # en test). Es una heurística que detecta el patrón más común de fuga
        # (fit() sobre train+test juntos).
        import numpy as np

        means = np.asarray(data["X_test"]).mean(axis=0)
        assert not np.allclose(means, 0.0, atol=1e-6), (
            "El scaler parece ajustado también con test (medias ~0 en test). "
            "Debe ajustarse SOLO con train."
        )

    def test_feature_names_are_stable_and_from_train(self, split):
        """Las features que usa el modelo están en el set de features causales
        y no incluyen la fecha datetime ni market (evitarían NaN y errores de
        dtype en el escalado)."""
        train, test = split
        data = build_features_matrix(train, test)
        for name in data["feature_names"]:
            assert name in FEATURE_COLUMNS
            assert name not in ("month", "market")

    def test_test_target_never_used_in_training_features(self, split):
        """Comprobación de coherencia: ninguna fila de test aparece como lag o
        rolling de una fila de train. Como el split es temporal y los lags
        miran hacia atrás, una fila de train jamaás usa un valor futuro (que
        viviría en test)."""
        train, test = split
        train_dates = set(train["month"])
        test_dates = set(test["month"])
        assert train_dates.isdisjoint(test_dates), "Solape de fechas train/test"
        # Si el train no contiene fechas de test, los lags causales del train
        # (que solo miran atrás) no pueden haber usado filas de test.
        assert train["month"].max() < test["month"].min()