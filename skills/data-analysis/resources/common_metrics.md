# Common metrics (reference)

Definitions and formulas for KPIs used across transversal projects. Adapt to company context (Brasaland, TrackFlow, Nexova).

---

## Generic

| Metric      | Formula                                                                    | Notes                         |
| ----------- | -------------------------------------------------------------------------- | ----------------------------- |
| **Rate**    | `(count of events / total opportunities) * 100`                            | Often expressed as %          |
| **Average** | `sum(values) / count(values)`                                              | Handle nulls per domain rules |
| **Trend**   | Compare current period vs previous (e.g. week-over-week, month-over-month) | Same period length            |

---

## Operations (Brasaland / TrackFlow style)

- **Waste rate**: `(waste quantity / total quantity used) * 100`
- **On-time delivery %**: `(deliveries on time / total deliveries) * 100`
- **Covers served**: Count of distinct orders or tickets per location/day

---

## HR / People (Nexova style)

- **Turnover**: `(leavers in period / average headcount in period) * 100`
- **Absenteeism rate**: `(days absent / total working days) * 100`
- **Time to fill**: Days from vacancy open to offer accepted

---

## Forecasting / Demand (TrackFlow — sales forecast milestone)

- **MSE (EUR²)**: `Σ (y − ŷ)² / n`. Mean squared error of forecast vs actual.
- **MSE % of mean income**: `100 · MSE / mean(y_test)²`. Relative cost of error,
  understandable by non-technical stakeholders (Thomas, Ana).
- **PSI (Population Stability Index)**: `Σ (p_obs − p_exp) · ln(p_obs / p_exp)`
  over decile buckets. Detects distribution shift between train and test — for
  TrackFlow, the LA/ZGZ volume-mix change signal.
- **Gini (as 2·AUC−1)**: probability that, for two different months, the model
  ranks them correctly (pairwise concordance). High Gini ⇒ Thomas can distinguish
  a normal low season (Feb) from an atypical drop. Low MSE alone does not imply
  high Gini (mean-compressed predictions rank poorly).
- **K2 Score**: `1 − Var(y − ŷ) / Var(y)`. Fraction of target variance explained
  by the model (R²-style, without requiring zero-mean residuals on test).
- **Why MSE is not enough**: MSE is an average of squared errors — it says
  nothing about ordering quality (Gini), distribution shift (PSI) or variance
  explained (K2). Report all four.

---

_Extend this file as you add metrics for your milestone._
