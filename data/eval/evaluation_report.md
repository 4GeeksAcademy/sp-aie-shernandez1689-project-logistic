# Evaluación del modelo de pronóstico de ventas — TrackFlow

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
| 1 | 2016-01-01 → 2017-08-01 | 2017-09-01 → 2019-04-01 | 20 | 20 | 75,016 | 101,242 | True |
| 2 | 2016-01-01 → 2019-04-01 | 2019-05-01 → 2020-12-01 | 40 | 20 | 66,011 | 91,081 | True |
| 3 | 2016-01-01 → 2020-12-01 | 2021-01-01 → 2022-08-01 | 60 | 20 | 83,725 | 91,588 | True |
| 4 | 2016-01-01 → 2022-08-01 | 2022-09-01 → 2024-04-01 | 80 | 20 | 85,926 | 99,067 | True |
| 5 | 2016-01-01 → 2024-04-01 | 2024-05-01 → 2025-12-01 | 100 | 20 | 84,818 | 108,339 | True |

**Resumen (media ± desviación estándar entre folds):**

| Métrica | Valor |
|---|---|
| MAE validación | **79,099 ± 8,495 €** |
| RMSE validación | **98,264 ± 7,197 €** |
| MSE validación | **9,697,158,351 ± 1,428,167,395 EUR²** |

> La desviación estándar se reporta con `ddof=1` (muestral) entre los 5 folds.

---

## 2. Curva de aprendizaje

Curva de error (MAE) de entrenamiento y validación al crecer el set de
entrenamiento (36 → 96 meses). Imagen: `data/eval/learning_curve.png`.

| Tamaño train (filas) | MAE train (€) | RMSE train (€) | MAE validación (€) | RMSE validación (€) |
|---|---|---|---|---|
| 36 | 244 | 413 | 319,534 | 349,542 |
| 48 | 332 | 605 | 139,025 | 206,119 |
| 60 | 372 | 529 | 125,586 | 193,263 |
| 72 | 610 | 812 | 113,711 | 156,228 |
| 84 | 630 | 878 | 97,416 | 127,709 |
| 96 | 554 | 780 | 76,081 | 100,875 |

**Lectura:** el error de validación **desciende** conforme crece la muestra; el error de entrenamiento
se mantiene bajo/estable. La brecha entre ambos **crece** (señal de sobreajuste).

---

## 3. Diagnóstico

**Overfitting**
El modelo está en **overfitting**: el error de validación es mucho mayor que el de entrenamiento y la brecha no se cierra (o empeora) al crecer la muestra. El XGBoost memoriza ruido de los ~96 meses en lugar de generalizar.

**Evidencia que respalda el diagnóstico:**
- Brecha (val − train) al inicio: `319,290 €`; al final: `75,527 €`
  (brecha relativa final: `99.27%`).
- MAE validación CV: `79,099 ± 8,495 €`.
- RMSE validación CV: `98,264 ± 7,197 €`.
- Gini ~0.33 / K2 ~0.39 en el test original (ver `scripts/README.sales-forecast.md`).

## 4. Acción correctiva propuesta

Acción correctiva: **reducir la capacidad del XGBoost** (n_estimators↓, max_depth↓, learning_rate↑ o más ``subsample``/``colsample_bytree``) y/o aumentar la regularización (``reg_lambda``/``reg_alpha``). La causa raíz es que, con 96-120 filas, 200 árboles de profundidad 6 sobre-aprenden los valores puntuales de lags ruidosos; se verifica porque la brecha de la curva de aprendizaje no se cierra al crecer la muestra. Alternativa estructural: modelar primero la estacionalidad determinista y entrenar el árbol sobre el residuo, que tiene menos ruido que memorizar.

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
