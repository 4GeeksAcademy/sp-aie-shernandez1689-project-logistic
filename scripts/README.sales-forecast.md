# Sales Forecast — TrackFlow (una última milla en Los Ángeles + Zaragoza)

Documento de interpretación del hito **"Modelo de pronóstico de series de
tiempo para predicción de ventas"** (ver `trackflow-BRIEFING.es.md` §3, §6).
Acompaña a:

- `scripts/sales_forecast_train.py` — pipeline de entrenamiento/validación.
- `scripts/sales_forecast_results.json` — métricas en formato máquina.
- `scripts/sales_forecast_plot.png` — predicción ± banda de variabilidad vs real.
- `tests/pipelines/test_sales_forecast_split.py` — prueba del split 8/2 años.

---

## 1. Resumen del modelo

| | |
|---|---|
| **Target** | `revenue_eur` (fila `consolidated`) |
| **Datos** | `data/raw/trackflow_sales.csv`, 120 meses (2016-01 → 2025-12) |
| **Split** | 8 años train (2016–2023, 96 filas) / 2 años test (2024–2025, 24 filas) |
| **Algoritmo** | `XGBRegressor` (XGBoost) |
| **Hiperparámetros** | `n_estimators=200`, `learning_rate=0.05`, `max_depth=6`, `subsample=0.9`, `colsample_bytree=0.9`, `random_state=42` |
| **Features** | 39 causales: dummies de mes/trimestre/año + lags (`lag_1,2,3,6,12`, envíos y ARPU) + rodantes (`roll_mean/min/max_3,6,12`, `roll_std_12`, `yoy_change`) — todas con `.shift(1)` (sin fuga) |

### Métricas sobre el test (2024–2025)

| Métrica | Valor | Interpretación |
|---|---|---|
| **MSE** | 1 827 247 708 EUR² | Error cuadrático medio absoluto |
| **MSE % ingreso medio** | 1.083 % | Error relativo al mes promedio |
| **RMSE** | 135 176 EUR | Error típico en euros |
| **Gini** | 0.326 | Poder de ranking (ver §3) |
| **K2 Score** | 0.391 | Varianza explicada (ver §6) |
| **PSI (pred vs real test)** | 5.485 | Desvío de forma pred-vs-real en test |
| **PSI (real train vs real test)** | 6.708 | Cambio de distribución real entre periodos |

---

## 2. Por qué elegimos XGBoost (y no Random Forest)

El briefing permite explícitamente XGBoost o Random Forest. Decisión **XGBoost**
por tres razones:

1. **Datos pequeños (96 filas)**: el boosting con regularización (`learning_rate`
   bajo, `subsample`, `colsample_bytree`) evita el sobreajuste mejor que un bosque
   grande en muestras tan cortas.
2. **Estacionalidad**: los picos de Nov–Dic (+25–35%) y la caída de Feb (−10–15%)
   son el núcleo del negocio (Black Friday/navidad). El boosting combina árboles
   poco profundos que capturan interacciones mes×tendencia con baja varianza.
3. **Explicabilidad**: `feature_importances` nos dice que el modelo de verdad usa
   `lag_12` (0.358) + `ship_lag_12` (0.103) + `roll_min_12` (0.099) — el mismo
   mes del año anterior —, coherente con la estacionalidad anual del briefing,
   y `m_12` (0.051) para la campaña navideña.

> Benchmark: al probar `RandomForestRegressor(n_estimators=500)` en el mismo
> test dio **MSE% 1.39 %**, **K2 0.226**, **Gini 0.326**: más error y menos
> varianza explicada que XGBoost (MSE% 1.08 %, K2 0.391, Gini 0.326).

---

## 3. Las 4 métricas, explicadas para TrackFlow

### 3.1 MSE — ¿cuánto me equivoco de media?

- **Qué mide**: promedio del error al cuadrado entre lo predicho y lo real.
- **Cómo se reporta (briefing §3)**: en **EUR²** —que no es intuitivo— y por eso
  también como **% del ingreso mensual promedio**: `100 · MSE / mean(y_test)²`.
- **Resultado TrackFlow**: `MSE = 18.27·10⁹ EUR²` ⇔ `MSE% = 1.08 %`,
  `RMSE = 135 176 EUR`.
- **Pros**: métrica directa que todo el equipo entiende.
- **Limitación clave**: el MSE es una **media de errores al cuadrado**, así que
  no dice *nada* sobre si el modelo ordena bien los meses ni sobre si la
  distribución de operaciones cambió. Un modelo puede tener un MSE% bajo y aun así
  ser inútil para la decisión de Thomas (ver §4).

### 3.2 PSI — ¿la población de clientes/volumen se mantuvo?

- **Qué mide**: cuánto se ha desplazado una distribución respecto a otra.
  `PSI = Σ (p_obs − p_exp) · ln(p_obs / p_exp)` sobre 10 buckets.
- **Para TrackFlow (briefing §3)**: detecta si la **mezcla de volumen
  Los Ángeles ↔ Zaragoza** cambió de forma significativa entre entrenamiento y
  prueba. Un PSI alto en `real_train_vs_real_test` avisaría de una expansión o
  contracción de operaciones en un país.
- **Interpretación estándar**: `< 0.1` sin cambios; `0.1–0.25` cambio moderado;
  `> 0.25` cambio fuerte.
- **Resultado TrackFlow**: reportamos **dos** PSI con semánticas distintas:
  - `psi_real_train_vs_test = 6.708` → la distribución **real** de ingresos
    cambió sustancialmente entre los 8 años de entrenamiento y los 2 de prueba
    (crecimiento anual + estacionalidad más marcada). Es una señal de negocio:
    el mix LA/ZGZ o el nivel de actividad no es estacionario.
  - `psi_pred_vs_real_test = 5.485` → lo que el modelo produce se desvía de lo
    que realmente pasó en test en cuanto a *forma* (compresión hacia la media;
    ver §5). No es una "fuga": es el sesgo esperado de un regresor en datos cortos.
- **Por qué no basta el PSI**: mide distribución de la *variable en bloque*, pero
  no la calidad del ranking temporal ni el error puntual.

### 3.3 Gini — ¿puedo confiar en el *orden* de los meses?

- **Qué mide**: probabilidad de que, dados dos meses distintos, el modelo los
  ordene correctamente. Se calcula por pares (AUC) y se reporta como
  `Gini = 2·AUC − 1`; **1.0** = ranking perfecto, **0** = ranking aleatorio,
  **−1** = ranking invertido.
- **Para TrackFlow (briefing §3)**: es **la métrica que le importa a Thomas**.
  Un Gini alto significa que el modelo distingue con confianza un mes de
  temporada baja **normal** (p. ej. febrero, −10–15%) de una **caída atípica**
  que amerite investigación. Si el modelo solo acierta la *media* pero ordena
  mal los meses, el Gini se hunde aunque el MSE sea bajo.
- **Resultado TrackFlow**: `Gini = 0.326` ⇒ el modelo ordena correctamente ~66 %
  de los pares de meses del test. Coherente con la correlación de Spearman
  (0.45): útil para detectar *tendencia direccional*, pero con margen de mejora
  en los picos.

### 3.4 K2 Score — ¿qué fracción de la variabilidad explico?

- **Qué mide**: `K2 = 1 − Var(y − ŷ) / Var(y)`. Fracción de la varianza total de
  los ingresos que el modelo explica (equivale a R² en regresión, pero sin el
  supuesto de que los residuos tengan media 0 en el test).
- **Para Ana (finanzas)**: si una caída/quiebre de ingresos ocurre, K2 mide
  cuánta de esa variación es *explicable por el modelo* y cuánta es *ruido* que
  hay que investigar.
- **Resultado TrackFlow**: `K2 = 0.391` ⇒ el modelo captura ~39 % de la varianza
  del test. El resto es la estacionalidad no modelada + ruido.
- **Por qué no basta**: K2 dice *cuánta* varianza explico, pero **no si la
  explico en el orden correcto** (eso lo dice el Gini). Un modelo puede tener
  buen K2 y Gini bajo si acierta amplia magnitud pero desordena meses.

---

## 4. ¿Por qué un MSE% bajo NO es suficiente? (lección clave del hito)

Supongamos un modelo "ingenuo" que siempre predice la **media** del test.
- Tendría un MSE% bajo (porque los valores están alrededor de la media)…
- …pero **Gini ≈ 0** (no distingue febrero normal de un desplome atípico) y
- **PSI alto** (las predicciones son casi constantes, no siguen la forma real).

En TrackFlow medimos **las cuatro** y cada una cubre una dimensión distinta:

| Dimensión | Métrica | Pregunta que responde |
|---|---|---|
| Magnitud del error | **MSE / MSE%** | ¿Cuánto me equivoco en €? |
| Cambio de distribución | **PSI** | ¿Cambió el mix LA/ZGZ o la forma de la serie? |
| Orden/capacidad de clasificar | **Gini** | ¿Distingo meses normales de atípicos? |
| Varianza explicada | **K2** | ¿Cuánta variabilidad modelo y cuánto es ruido? |

Nuestro modelo concreto ilustra el punto: **MSE% 1.08 % parece excelente**, pero
Gini 0.326 y K2 0.391 dicen que solo ~66 % de los pares están bien ordenados y
~39 % de la varianza se explica. El MSE% bajo engaña porque las predicciones
están **comprimidas hacia la media** (ver §5).

---

## 5. Diagnóstico honesto de los resultados

### 5.1 Sesgo a la media (compresión)

Las predicciones del test tienen desviación estándar **~59k €** frente a
**~171k €** reales; el rango predicho es más angosto que el real. Es el
comportamiento característico de los ensembles de árboles con pocas filas de
entrenamiento: "encogen" hacia el promedio. Consecuencias:

- El **RMSE** (135k €) subestima el error en los picos de Nov–Dic (el modelo
  acierta mejor la media que la cola).
- Feb-2024 se subestima ~+25 % y Nov/Dic se subestiman ~−13/−16 %: captura la
  **dirección** de la estacionalidad pero no toda su **amplitud**.

### 5.2 Qué significa el PSI alto

El `psi_real_train_vs_test = 6.71` no es culpa del modelo: es una **señal de
negocio**. La serie real de ingresos muestra crecimiento + estacionalidad cada
vez más amplia, así que la distribución de los últimos 2 años no es igual a la
de los 8 anteriores. En producción esto sugiere **reentrenar con ventana
deslizante** o incluir un término de tendencia explícito.

### 5.3 Interpretación para la decisión de Thomas

- **Uso seguro**: el Gini 0.326 > 0 confirma que el modelo **sí discrimina**
  febrero normal de caídas atípicas a nivel direccional — mejor que lanzar una
  moneda.
- **Mejoras recomendadas** (fuera de alcance del hito): normalizar por tendencia
  antes de entrenar, usar `year` como trend feature explícita, o ensamblar con un
  modelo de estacionalidad determinista (p. ej. media móvil estacional + residuo
  XGBoost).

---

## 6. Reproducibilidad

```bash
export PATH="$HOME/.local/bin:$PATH"   # si usas uv
uv run python scripts/sales_forecast_train.py

# Prueba del split 8/2:
uv run pytest tests/pipelines/test_sales_forecast_split.py -v --no-cov
```

Salidas regeneradas: `scripts/sales_forecast_results.json` y
`scripts/sales_forecast_plot.png`. Dataset determinista (`random_state=42`),
semilla fija en el modelo — los resultados son reproducibles.

---

## 7. Referencias

- `trackflow-BRIEFING.es.md` §3 (KPIs y qué significa un buen modelo), §4 (patrón
  de datos), §6 (entregables).
- `shared/sales_forecast/metrics.py` — implementación de MSE, PSI, Gini, K2.
- `shared/sales_forecast/features.py` — features causales sin fuga.
- `scripts/sales_forecast_train.py` — pipeline completo.
- `tests/pipelines/test_sales_forecast_split.py` — validación del split.