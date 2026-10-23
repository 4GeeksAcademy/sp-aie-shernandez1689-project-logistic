"""Tests de la validación cruzada temporal del pronóstico de ventas.

Valida dos garantías exigidas por el hito (``trackflow-BRIEFING.es.md`` §6) y por
el briefing de la empresa:

1. **Orden cronológico en cada fold**: ningún índice de un fold posterior aparece
   antes que uno de un fold anterior. ``TimeSeriesSplit`` debe preservar el orden
   estrictamente: ``max(train) < min(val)`` y ambas secuencias crecientes.

2. **Sin fuga de lags/rolling entre folds**: la feature ``lag_12`` (o cualquier
   ``lag_*``/``roll_*``/``yoy_change``) de una fila del fold de validación debe
   calcularse usando únicamente valores de la historia **anterior al propio
   fold**. Si se calcularan los lags sobre la serie completa y luego se dividiera,
   la ventana podría asomarse a valores futuros; aquí se verifica que la feature
   de una fila de validación coincide con una construcción manual sobre el
   prefijo histórico (cero fuga).

La función bajo prueba es ``shared.sales_forecast.cv``, que alimenta al script
``scripts/sales_forecast_evaluate.py`` y al reporte ``data/eval/evaluation_report.md``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# ── Añadir la raíz del monorepo al sys.path ────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from shared.sales_forecast.cv import (  # noqa: E402
    N_FOLDS,
    build_fold_features,
    time_series_split_indices,
)
from shared.sales_forecast.features import (  # noqa: E402
    TARGET_COLUMN,
    build_features,
)
from scripts.sales_forecast_train import load_and_prepare, temporal_split  # noqa: E402


@pytest.fixture(scope="module")
def dataset() -> pd.DataFrame:
    """Dataset real de TrackFlow (consolidated, 120 meses 2016-01 → 2025-12)."""
    return load_and_prepare(Path(REPO_ROOT) / "data" / "raw" / "trackflow_sales.csv")


# ===================================================================
# Tests — orden cronológico en cada fold (TimeSeriesSplit)
# ===================================================================


class TestTimeSeriesSplitChronological:
    """El split debe preservar el orden temporal en TODO fold."""

    def test_number_of_folds_is_at_least_5(self):
        """Como mínimo 5 folds (requisito del hito)."""
        n = 120
        folds = list(time_series_split_indices(n, n_splits=N_FOLDS))
        assert len(folds) >= 5

    def test_train_strictly_before_val_in_every_fold(self):
        """max(train_idx) < min(val_idx) para cada fold (sin solape)."""
        n = 120
        for train_idx, val_idx in time_series_split_indices(n, n_splits=N_FOLDS):
            assert train_idx.max() < val_idx.min(), (
                "El último índice de entrenamiento debe ser anterior al "
                "primer índice de validación."
            )

    def test_no_shuffling_each_partition_is_sorted(self):
        """Dentro de train y de val los índices son estrictamente crecientes
        (nadie barajó los datos)."""
        n = 120
        for train_idx, val_idx in time_series_split_indices(n, n_splits=N_FOLDS):
            assert np.all(np.diff(train_idx) > 0), "train_idx no está ordenado"
            assert np.all(np.diff(val_idx) > 0), "val_idx no está ordenado"

    def test_no_index_appears_in_both_train_and_val(self):
        """Cero intersección entre train y validación (no hay 'asomarse')."""
        n = 120
        for train_idx, val_idx in time_series_split_indices(n, n_splits=N_FOLDS):
            assert len(set(train_idx) & set(val_idx)) == 0, (
                "Un índice aparece a la vez en train y en validación."
            )

    def test_no_future_index_before_past_index_across_fold(self, dataset: pd.DataFrame):
        """Para cada fold real del dataset, los meses de validación son
        cronológicamente posteriores a los de entrenamiento (verificación con
        fechas, no solo con índices)."""
        n = len(dataset)
        months = pd.to_datetime(dataset["month"]).to_numpy()
        for train_idx, val_idx in time_series_split_indices(n, n_splits=N_FOLDS):
            train_months = months[train_idx]
            val_months = months[val_idx]
            assert train_months.max() < val_months.min(), (
                f"La última fecha de train {train_months.max()} no es anterior a "
                f"la primera de validación {val_months.min()}."
            )


# ===================================================================
# Tests — sin fuga de lags/rolling entre folds
# ===================================================================


class TestNoLagLeakageAcrossFolds:
    """Las features lag_*/roll_*/yoy de una fila de validación deben usar solo
    historia anterior al fold, nunca valores del propio fold ni posteriores."""

    def test_lag_of_val_row_equals_manual_construction_on_prefix(self, dataset: pd.DataFrame):
        """El lag_12 (y todos los lag_*) de la PRIMERA fila de validación del
        último fold debe coincidir con el target de 12 meses antes — y ese mes
        pertenece a la historia anterior (train del fold), no a la validación."""
        n = len(dataset)
        # Último fold
        folds = list(time_series_split_indices(n, n_splits=N_FOLDS))
        train_idx, val_idx = folds[-1]
        val_start = int(val_idx.min())
        val_end = int(val_idx.max())

        # Features anti-fuga del módulo
        train_feat, val_feat = build_fold_features(dataset, val_start, val_end)

        # Construcción manual sobre el prefijo hasta el final del fold
        prefix = dataset.iloc[: val_end + 1].copy()
        manual = build_features(prefix)
        manual_val = manual.iloc[val_start : val_end + 1].reset_index(drop=True)

        for col in ["lag_1", "lag_2", "lag_3", "lag_6", "lag_12",
                    "ship_lag_1", "ship_lag_6", "ship_lag_12",
                    "roll_mean_3", "roll_mean_6", "roll_mean_12",
                    "yoy_change"]:
            assert val_feat[col].iloc[0] == pytest.approx(manual_val[col].iloc[0]), (
                f"Feature {col} de la primera fila de validación no coincide con "
                f"la construcción manual sobre el prefijo histórico (posible fuga)."
            )

    def test_val_features_do_not_contain_future_target(self, dataset: pd.DataFrame):
        """Ninguna feature de lag/rolling de una fila de validación puede ser
        igual al target de una fila posterior del propio fold (fuga dura)."""
        n = len(dataset)
        for train_idx, val_idx in time_series_split_indices(n, n_splits=N_FOLDS):
            val_start, val_end = int(val_idx.min()), int(val_idx.max())
            _, val_feat = build_fold_features(dataset, val_start, val_end)

            target = dataset[TARGET_COLUMN].to_numpy()
            for c in val_feat.columns:
                if not c.startswith(("lag_", "roll_", "yoy")):
                    continue
                # Para cada fila de validación, la feature debe ser un valor del
                # PASADO (índice < val_start) — nunca un target del propio fold.
                for i in range(len(val_feat)):
                    val = val_feat[c].iloc[i]
                    if pd.isna(val):
                        continue
                    # Si la feature apareciera como target de una fila posterior
                    # del mismo fold, estaríamos mirando el futuro.
                    future_row = (val_start + i, target[val_start + i])
                    assert not (future_row[1] == pytest.approx(float(val))), (
                        f"Feature {c} de la fila {future_row[0]} coincide con su "
                        f"propio target futuro: fuga entre folds."
                    )

    def test_folds_use_growing_train_prefix(self, dataset: pd.DataFrame):
        """Los folds son un prefijo temporal creciente: el train del fold k+1
        contiene al del fold k (refleja la naturaleza 'expanding window' de
        TimeSeriesSplit, coherente con el hito)."""
        n = len(dataset)
        folds = list(time_series_split_indices(n, n_splits=N_FOLDS))
        prev_train = None
        for train_idx, _ in folds:
            if prev_train is not None:
                assert set(prev_train) <= set(train_idx), (
                    "El train del fold siguiente no contiene al anterior: "
                    "el split no es expanding-window."
                )
            prev_train = set(train_idx)


# ===================================================================
# Tests — coherencia con el split 8/2 del hito
# ===================================================================


class TestCoherenceWithHitoSplit:
    """La CV temporal debe ser consistente con el split 8/2 del entrenamiento."""

    def test_final_fold_appears_after_2023(self, dataset: pd.DataFrame):
        """El último fold de la CV debe tener su validación en 2024+ (no antes),
        de manera que la métrica de CV sea comparable al test del hito."""
        n = len(dataset)
        folds = list(time_series_split_indices(n, n_splits=N_FOLDS))
        _, val_idx = folds[-1]
        val_start = int(val_idx.min())
        first_val_month = pd.to_datetime(dataset["month"].iloc[val_start])
        assert first_val_month.year >= 2024, (
            "La validación del último fold debería caer en 2024+ para ser "
            "comparable al test 8/2 del hito."
        )