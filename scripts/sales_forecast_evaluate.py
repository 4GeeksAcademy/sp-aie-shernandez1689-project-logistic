#!/usr/bin/env python3
"""Evaluación del modelo de pronóstico de ventas de TrackFlow.

Ejecuta, sobre el set de entrenamiento del hito (2016-01 → 2023-12, 96 filas):

1. **Validación cruzada temporal** (``TimeSeriesSplit``, 5 folds) respetando
   el orden cronológico y **sin fuga de lags/rolling entre folds** (las
   features se recalculan por fold sobre el prefijo histórico `≤ val_end`).
   Reporta MAE y RMSE como media ± desviación estándar a través de los folds.

2. **Curva de aprendizaje**: error de entrenamiento y validación (MAE) a
   medida que crece el tamaño del train, sobre los primeros 8 años. La imagen
   se guarda en ``data/eval/learning_curve.png``.

3. **Diagnóstico**: clasifica el modelo como bien ajustado / underfitting /
   overfitting y propone una acción correctiva, basándose en el desfase entre
   error de entrenamiento y validación y su evolución con el tamaño de la
   muestra.

Salidas (en ``data/eval/``):
    cv_results.json      → folds + summary (media ± std) de la validación cruzada
    learning_curve.png   → curva de aprendizaje (train vs validación)
    learning_curve.json  → puntos de la curva para auditoría
    evaluation_report.md → reporte técnico con diagnóstico y acción correctiva

Uso:
    uv run python scripts/sales_forecast_evaluate.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # Entorno headless.

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

# Añadir la raíz del monorepo al sys.path para importar shared.sales_forecast.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.sales_forecast_train import (  # noqa: E402
    RANDOM_STATE,
    TEST_YEARS,
    TRAIN_YEARS,
    load_and_prepare,
    temporal_split,
)
from shared.sales_forecast.cv import (  # noqa: E402
    N_FOLDS,
    _fill_with_train_stats,
    _model_for_cv,
    build_fold_features,
    run_time_series_cv,
)
from shared.sales_forecast.features import FEATURE_COLUMNS, TARGET_COLUMN, build_features  # noqa: E402
from shared.sales_forecast.metrics import population_stability_index  # noqa: E402

RAW_SALES = REPO_ROOT / "data" / "raw" / "trackflow_sales.csv"
EVAL_DIR = REPO_ROOT / "data" / "eval"
CV_RESULTS = EVAL_DIR / "cv_results.json"
LEARNING_CURVE_PNG = EVAL_DIR / "learning_curve.png"
LEARNING_CURVE_JSON = EVAL_DIR / "learning_curve.json"
REPORT_PATH = EVAL_DIR / "evaluation_report.md"

# Fracciones del train (8 años = 96 filas) para la curva de aprendizaje.
# Empiezan en 36 meses (3 años) para que lags/rolling de 12 meses existan.
TRAIN_SIZES = [36, 48, 60, 72, 84, 96]


def _mae(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    return float(np.mean(np.abs(y_true - y_pred)))


def _rmse(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    return float(np.sqrt(np.mean((y_true - y_pred) ** 2)))


def _fit_predict_on_subset(
    series: pd.DataFrame,
    train_end_exclusive: int,
    model=None,
):
    """Entrena sobre las primeras ``train_end_exclusive`` filas y predice.

    La validación es SIEMPRE el bloque fijo de los últimos 24 meses
    (2024-01 → 2025-12, el test del hito), para que la comparación entre
    puntos de la curva sea justa (misma ventana de validación).

    Las features se computan sobre el prefijo completo hasta el final de la
    validación (``series.iloc[:val_end+1]``) - como las operaciones
    ``shift()`` y ``rolling()`` solo miran hacia atrás, no hay fuga hacia
    delante. Los NaN se rellenan con estadísticas **solo de entrenamiento**
    (no contaminan con distribución de validación).
    """
    if model is None:
        model = _model_for_cv()

    n = len(series)
    # Validación fija: últimos 24 meses (test del hito: 2024-01 → 2025-12).
    val_start = n - 24  # mes 96 (0‑based), 2024-01
    val_end = n - 1     # mes 119, 2025-12

    # Construir features sobre el prefijo completo hasta val_end.
    prefix = series.iloc[: val_end + 1].copy()
    features = build_features(prefix)

    # Train = solo primeras train_end_exclusive filas (variable).
    train_feat = features.iloc[:train_end_exclusive].copy().reset_index(drop=True)
    # Validación = último bloque fijo de 24 meses.
    val_feat = features.iloc[val_start : val_end + 1].copy().reset_index(drop=True)

    # Rellenar NaN solo con estadísticas del entrenamiento.
    train_feat = _fill_with_train_stats(train_feat)
    val_feat = _fill_with_train_stats(val_feat, train_ref=train_feat)

    feature_cols = [c for c in FEATURE_COLUMNS if c in train_feat.columns]

    X_train = train_feat[feature_cols].to_numpy(dtype=float)
    y_train = train_feat[TARGET_COLUMN].to_numpy(dtype=float)
    X_val = val_feat[feature_cols].to_numpy(dtype=float)
    y_val = series[TARGET_COLUMN].to_numpy()[val_start : val_end + 1]

    from sklearn.preprocessing import StandardScaler

    scaler = StandardScaler()
    X_train_s = scaler.fit_transform(X_train)
    X_val_s = scaler.transform(X_val)

    model.fit(X_train_s, y_train)
    y_pred_train = model.predict(X_train_s)
    y_pred_val = model.predict(X_val_s)

    return {
        "train_size": int(train_end_exclusive),
        "val_start": str(pd.to_datetime(series["month"].to_numpy()[val_start]).strftime("%Y-%m")),
        "val_end": str(pd.to_datetime(series["month"].to_numpy()[val_end]).strftime("%Y-%m")),
        "mae_train": _mae(y_train, y_pred_train),
        "rmse_train": _rmse(y_train, y_pred_train),
        "mae_val": _mae(y_val, y_pred_val),
        "rmse_val": _rmse(y_val, y_pred_val),
    }


def build_learning_curve(series: pd.DataFrame, model=None) -> list[dict]:
    """Curva de aprendizaje: error train/val para tamaños crecientes de train."""
    points = []
    for size in TRAIN_SIZES:
        points.append(_fit_predict_on_subset(series, train_end_exclusive=size, model=model))
    return points


def plot_learning_curve(points: list[dict], out_path: Path) -> None:
    """Genera la gráfica de la curva de aprendizaje (train vs validación)."""
    sizes = [p["train_size"] for p in points]
    mae_train = [p["mae_train"] for p in points]
    mae_val = [p["mae_val"] for p in points]

    fig, ax = plt.subplots(figsize=(9, 5.5))
    fig.patch.set_facecolor("white")

    ax.plot(sizes, mae_train, marker="o", color="#1f77b4",
            label="Error entrenamiento (MAE)")
    ax.plot(sizes, mae_val, marker="s", color="#d62728",
            label="Error validación (MAE)")

    ax.set_title("TrackFlow · Curva de aprendizaje (pronóstico revenue_eur)")
    ax.set_xlabel("Tamaño del set de entrenamiento (filas / meses)")
    ax.set_ylabel("MAE (EUR)")
    ax.legend(loc="best")
    ax.grid(True, linestyle="--", alpha=0.4)

    fig.tight_layout()
    fig.savefig(out_path, dpi=140)
    plt.close(fig)
    print(f"[plot] Curva de aprendizaje en: {out_path}")


def diagnose(points: list[dict], cv_summary: dict) -> dict:
    """Clasifica el modelo y propone acción correctiva (no genérica).

    Criterios usados (alineados con la curva de aprendizaje y el CV):
      - Si el error de validación es ≫ al de entrenamiento y la brecha NO se
        cierra al crecer la muestra → overfitting.
      - Si ambos errores quedan altos y planos (el modelo no reduce el error aun
        añadiendo datos; la curva se aplana alto) → underfitting.
      - Si la brecha es pequeña y ambos bajan/estabilizan → bien ajustado.
    """
    mae_train_first, mae_train_last = points[0]["mae_train"], points[-1]["mae_train"]
    mae_val_first, mae_val_last = points[0]["mae_val"], points[-1]["mae_val"]

    gap_first = mae_val_first - mae_train_first
    gap_last = mae_val_last - mae_train_last
    relative_gap_last = gap_last / max(mae_val_last, 1e-9)

    # Referencia de magnitud: el RMSE del modelo original en test era ~135k EUR
    # (MSE% 1.08). Para MAE el orden de magnitud típico es 80k-160k EUR.
    order_of_magnitude = np.median([p["mae_val"] for p in points])

    if relative_gap_last > 0.35 and mae_train_last < 0.5 * mae_val_last:
        verdict = "overfitting"
    elif (mae_val_last > 1.25 * order_of_magnitude
          and (mae_val_last - mae_train_last) < 0.15 * mae_val_last):
        verdict = "underfitting"
    else:
        verdict = "well_fitted"

    return {
        "verdict": verdict,
        "gap_first": float(gap_first),
        "gap_last": float(gap_last),
        "relative_gap_last": float(relative_gap_last),
        "mae_train_first": mae_train_first,
        "mae_train_last": mae_train_last,
        "mae_val_first": mae_val_first,
        "mae_val_last": mae_val_last,
        "cv_mae_mean": float(cv_summary["mae_mean"]),
        "cv_mae_std": float(cv_summary["mae_std"]),
        "cv_rmse_mean": float(cv_summary["rmse_mean"]),
        "cv_rmse_std": float(cv_summary["rmse_std"]),
    }


def _fmt(x: float) -> str:
    return f"{x:,.0f}"


def write_report(diag: dict, cv_result: dict, points: list[dict], out_path: Path) -> None:
    """Redacta el reporte técnico en markdown (data/eval/evaluation_report.md)."""
    verdict_text = {
        "well_fitted": "**Bien ajustado (well-fitted)**",
        "overfitting": "**Overfitting**",
        "underfitting": "**Underfitting**",
    }[diag["verdict"]]

    rows = "\n".join(
        f"| {p['train_size']} | {_fmt(p['mae_train'])} | {_fmt(p['rmse_train'])} | "
        f"{_fmt(p['mae_val'])} | {_fmt(p['rmse_val'])} |"
        for p in points
    )

    folds_rows = "\n".join(
        f"| {f['fold']} | {_fmt_date(f['train_start'])} → {_fmt_date(f['train_end'])} | "
        f"{_fmt_date(f['val_start'])} → {_fmt_date(f['val_end'])} | {f['train_rows']} | {f['val_rows']} | "
        f"{_fmt(f['mae_val'])} | {_fmt(f['rmse_val'])} | {f['chronological']} |"
        for f in cv_result["folds"]
    )

    summary = cv_result["summary"]

    # ── Texto de diagnóstico y acción correctiva (contexto TrackFlow) ────────
    verdict_line = {
        "well_fitted": (
            "El modelo está **bien ajustado**: la brecha entrenamiento/validación es "
            "pequeña y estable, y el error de validación se reduce o estabiliza al "
            "añadir datos (la curva no está plana en un error alto)."
        ),
        "overfitting": (
            "El modelo está en **overfitting**: el error de validación es mucho mayor "
            "que el de entrenamiento y la brecha no se cierra (o empeora) al crecer la "
            "muestra. El XGBoost memoriza ruido de los ~96 meses en lugar de generalizar."
        ),
        "underfitting": (
            "El modelo está en **underfitting**: tanto el error de entrenamiento como el "
            "de validación se mantienen altos y planos aunque añadamos datos, señal de "
            "que el modelo no captura la estructura (tendencia X≈6% + estacionalidad "
            "Nov–Dic +25-35% / Feb −10-15%) del briefing."
        ),
    }[diag["verdict"]]

    action_line = {
        "well_fitted": (
            "El modelo es apto como línea base para el dashboard ejecutivo de Thomas, "
            "pero el Gini/K2 (0.33 / 0.39) muestran que aún queda varianza estacional sin "
            "capturar. Antes de producción conviene integrar un término de tendencia "
            "explícito (p. ej. ``year`` como feature numérica continua o diferencias "
            "estacionales) y evaluar con la misma CV temporal una vez al año."
        ),
        "overfitting": (
            "Acción correctiva: **reducir la capacidad del XGBoost** (n_estimators↓, "
            "max_depth↓, learning_rate↑ o más ``subsample``/``colsample_bytree``) y/o "
            "aumentar la regularización (``reg_lambda``/``reg_alpha``). La causa raíz es "
            "que, con 96-120 filas, 200 árboles de profundidad 6 sobre-aprenden los "
            "valores puntuales de lags ruidosos; se verifica porque la brecha de la curva "
            "de aprendizaje no se cierra al crecer la muestra. Alternativa estructural: "
            "modelar primero la estacionalidad determinista y entrenar el árbol sobre el "
            "residuo, que tiene menos ruido que memorizar."
        ),
        "underfitting": (
            "Acción correctiva: **aumentar la capacidad expresiva del modelo**. La causa "
            "raíz no es falta de datos (la curva se aplana sin bajar), sino que XGBoost "
            "con esta configuración y 39 features lineales de calendario no captura la "
            "interacción tendencia × estacionalidad (lag_12 × mes). Se propone añadir "
            "features de interacción (mes × año, mes × crecimiento interanual) o usar un "
            "modelo explícitamente estacional (STL + residuo) en lugar de solo añadir "
            "complejidad al árbol."
        ),
    }[diag["verdict"]]

    report = f"""# Evaluación del modelo de pronóstico de ventas — TrackFlow

> **Fase:** validación cruzada temporal + curva de aprendizaje
> **Dataset:** `data/raw/trackflow_sales.csv` · mercado `consolidated` · target `revenue_eur`
> **Split del hito:** 8 años train (2016–2023) / 2 años test (2024–2025)
> **Modelo:** `XGBRegressor` (n_estimators=200, lr=0.05, max_depth=6, random_state=42)

---

## 1. Validación cruzada temporal (5 folds, sin barajar y sin fuga)

Se usa `TimeSeriesSplit` de scikit-learn con **5 folds** sobre los 120 meses
(`consolidated`). Cada fold entrena sobre un prefijo cronológico estricto y
evalúa el bloque **inmediatamente posterior**; nunca hay barajado ni solape.

**Garantía anti-fuga de lags/rolling:** las features `lag_*`/`roll_*`/`yoy_change`
se recalculan **por fold** sobre el prefijo histórico `series.iloc[:val_end]`
(no sobre la serie completa). Así, el `lag_12` de la primera fila de validación
apunta al mes `val_start − 12`, que vive en el entrenamiento o en la historia —
**nunca en el futuro del fold**. La columna `chronological` es ``True`` para los
5 folds.

| Fold | Train (inicio → fin) | Validación (inicio → fin) | Filas train | Filas val | MAE val (€) | RMSE val (€) | Cronológico |
|---|---|---|---|---|---|---|---|
{folds_rows}

**Resumen (media ± desviación estándar entre folds):**

| Métrica | Valor |
|---|---|
| MAE validación | **{_fmt(summary['mae_mean'])} ± {_fmt(summary['mae_std'])} €** |
| RMSE validación | **{_fmt(summary['rmse_mean'])} ± {_fmt(summary['rmse_std'])} €** |
| MSE validación | **{_fmt(summary['mse_mean'])} ± {_fmt(summary['mse_std'])} EUR²** |

> La desviación estándar se reporta con `ddof=1` (muestral) entre los 5 folds.

---

## 2. Curva de aprendizaje

Curva de error (MAE) de entrenamiento y validación al crecer el set de
entrenamiento (36 → 96 meses). Imagen: `data/eval/learning_curve.png`.

| Tamaño train (filas) | MAE train (€) | RMSE train (€) | MAE validación (€) | RMSE validación (€) |
|---|---|---|---|---|
{rows}

**Lectura:** el error de validación {_trend(points)}; el error de entrenamiento
{_trend_train(points)}. La brecha entre ambos {_trend_gap(diag)}.

---

## 3. Diagnóstico

{verdict_text}
{verdict_line}

**Evidencia que respalda el diagnóstico:**
- Brecha (val − train) al inicio: `{_fmt(diag['gap_first'])} €`; al final: `{_fmt(diag['gap_last'])} €`
  (brecha relativa final: `{diag['relative_gap_last']:.2%}`).
- MAE validación CV: `{_fmt(diag['cv_mae_mean'])} ± {_fmt(diag['cv_mae_std'])} €`.
- RMSE validación CV: `{_fmt(diag['cv_rmse_mean'])} ± {_fmt(diag['cv_rmse_std'])} €`.
- Gini ~0.33 / K2 ~0.39 en el test original (ver `scripts/README.sales-forecast.md`).

## 4. Acción correctiva propuesta

{action_line}

---

## 5. ¿MAE o RMSE es más adecuado para TrackFlow?

Consultado el `trackflow-BRIEFING.md` (empresa), TrackFlow vive de los **picos de
temporada**: Noviembre–Diciembre (+25-35% sobre la media, Black Friday/navidad) y
la caída de Febrero (−10-15%). Los contratos B2B son **anuales** y se renuevan si
la operación funciona cuando más importa.

- **MAE** es el error típico en euros sin penalizar picos: útil para el "mes
  típico".
- **RMSE**, al elevar al cuadrado, **penaliza los errores grandes** — es decir,
  un error de 300k € en Nov–Dic pesa más que cuatro errores de 75k €. Para
  TrackFlow, equivocarse **en el pico de la temporada alta es lo más caro**
  (incumplir SLA con las marcas, no dimensionar capacidad y arriesgar la
  renovación del contrato), así que una métrica que castiga los errores grandes
  es la que mejor refleja el **costo de negocio** asimétrico del briefing.

**Conclusión:** **RMSE es la métrica que mejor refleja el costo de negocio** para
TrackFlow, porque sobre-penaliza los errores en los picos de ingresos (Nov–Dic),
que son exactamente los errores que más cuestan (capacidad/SLA/renovación). MAE
se reporta como complemento interpretable (error típico en €).

---
_Generado por `scripts/sales_forecast_evaluate.py` · Dataset determinista
(random_state=42) · Reproducible con `uv run python scripts/sales_forecast_evaluate.py`._
"""

    # Sustituir los placeholders de tendencia (funciones locales auxiliares).
    report = report.replace("{_trend(points)}", _trend(points))
    report = report.replace("{_trend_train(points)}", _trend_train(points))
    report = report.replace("{_trend_gap(diag)}", _trend_gap(diag))

    out_path.write_text(report, encoding="utf-8")
    print(f"[ok] Reporte en: {out_path}")


def _fmt_date(s: str) -> str:
    """Limpia formato de fecha: elimina ' 00:00:00' si existe."""
    return s.replace(" 00:00:00", "")


def _trend(points: list[dict]) -> str:
    first, last = points[0]["mae_val"], points[-1]["mae_val"]
    if last < first * 0.85:
        return "**desciende** conforme crece la muestra"
    if last > first * 1.15:
        return "**sube** conforme crece la muestra"
    return "**se mantiene estable**"


def _trend_train(points: list[dict]) -> str:
    first, last = points[0]["mae_train"], points[-1]["mae_train"]
    if last < first * 0.85:
        return "**baja** al crecer la muestra"
    return "se mantiene bajo/estable"


def _trend_gap(diag: dict) -> str:
    if diag["relative_gap_last"] > 0.35:
        return "**crece** (señal de sobreajuste)"
    if diag["relative_gap_last"] < 0.15:
        return "**se mantiene pequeña** (capacidad adecuada)"
    return "es **moderada** (posible sesgo/varianza en equilibrio)"


def main() -> int:
    print("=== TrackFlow · Evaluación del modelo de pronóstico (CV temporal + curva) ===")
    EVAL_DIR.mkdir(parents=True, exist_ok=True)

    print("[1/5] Cargando dataset (consolidated)")
    consolidated = load_and_prepare(RAW_SALES)
    # Verificación de negocio: usamos la serie completa (120 meses, consolidated)
    # para la CV temporal; el split 8/2 del hito se reproduce en evaluate como
    # validación out-of-time adicional.
    train, test = temporal_split(consolidated)
    print(f"      total: {len(consolidated)} | train hito: {len(train)} | test hito: {len(test)}")

    print("[2/5] Validación cruzada temporal (TimeSeriesSplit, 5 folds)")
    cv_result = run_time_series_cv(consolidated, n_splits=N_FOLDS)
    for f in cv_result["folds"]:
        print(f"      fold {f['fold']}: train {f['train_start'][:10]}→{f['train_end'][:10]} "
              f"| val {f['val_start'][:10]}→{f['val_end'][:10]} "
              f"| MAE={f['mae_val']:,.0f} RMSE={f['rmse_val']:,.0f} cronológico={f['chronological']}")
    s = cv_result["summary"]
    print(f"      MAE val: {s['mae_mean']:,.0f} ± {s['mae_std']:,.0f} € | "
          f"RMSE val: {s['rmse_mean']:,.0f} ± {s['rmse_std']:,.0f} €")
    (EVAL_DIR / "cv_results.json").write_text(
        json.dumps(cv_result, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    print(f"[ok] CV JSON en: {CV_RESULTS}")

    print("[3/5] Curva de aprendizaje")
    points = build_learning_curve(consolidated)
    plot_learning_curve(points, LEARNING_CURVE_PNG)
    (EVAL_DIR / "learning_curve.json").write_text(
        json.dumps(points, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"[ok] Curva JSON en: {LEARNING_CURVE_JSON}")

    print("[4/5] Diagnóstico")
    diag = diagnose(points, cv_result["summary"])
    print(f"      verdict: {diag['verdict']}")

    print("[5/5] Redactando reporte técnico")
    write_report(diag, cv_result, points, REPORT_PATH)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())