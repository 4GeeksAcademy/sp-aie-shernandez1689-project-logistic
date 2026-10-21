# Reporte de Validación — Pipeline de Desempeño Semanal

Generado: 2026-10-21T15:19:58.571939+00:00

## 0. Ejecución del pipeline

- Estado: **success**
- Eventos procesados: 277
- Filas upserted: 10
- ISO Week: 2026-W42

## 1. Archivos de salida

- `data/eval/weekly_warehouse_client_performance/weekly_kpis.jsonl`: ✅ 2413 bytes
- `data/eval/weekly_warehouse_client_performance/pipeline_runs.jsonl`: ✅ 4094 bytes

## 2. KPIs generados

- Total de filas KPI: **10**


### Filas por almacén

- `los_angeles`: 5 filas
- `zaragoza`: 5 filas

- Semanas cubiertas: 2026-10-12


### Muestra (primeras 3 filas)

```json
[
  {
    "warehouse": "los_angeles",
    "client_id": "fashion-co",
    "week_start": "2026-10-12",
    "inbound_units_count": 2093,
    "outbound_orders_count": 14,
    "stockout_events_count": 1,
    "discrepancy_events_count": 3,
    "discrepancy_rate": 0.21428571428571427
  },
  {
    "warehouse": "los_angeles",
    "client_id": "glowlab-cosmetics",
    "week_start": "2026-10-12",
    "inbound_units_count": 3326,
    "outbound_orders_count": 14,
    "stockout_events_count": 0,
    "discrepancy_events_count": 3,
    "discrepancy_rate": 0.21428571428571427
  },
  {
    "warehouse": "los_angeles",
    "client_id": "purestep-footwear",
    "week_start": "2026-10-12",
    "inbound_units_count": 2705,
    "outbound_orders_count": 17,
    "stockout_events_count": 0,
    "discrepancy_events_count": 5,
    "discrepancy_rate": 0.29411764705882354
  }
]
```


### Resumen de KPIs

| Almacén | Cliente | Inbound | Outbound | Stockout | Disc. | Disc. Rate |
|---------|---------|---------|----------|----------|-------|-----------|
| los_angeles | fashion-co | 2093 | 14 | 1 | 3 | 0.2143 |
| los_angeles | glowlab-cosmetics | 3326 | 14 | 0 | 3 | 0.2143 |
| los_angeles | purestep-footwear | 2705 | 17 | 0 | 5 | 0.2941 |
| los_angeles | soundwave-electronics | 1682 | 5 | 3 | 4 | 0.8000 |
| los_angeles | urban-thread | 1416 | 16 | 1 | 2 | 0.1250 |
| zaragoza | fashion-co | 1489 | 16 | 2 | 5 | 0.3125 |
| zaragoza | glowlab-cosmetics | 3510 | 11 | 3 | 8 | 0.7273 |
| zaragoza | purestep-footwear | 1012 | 13 | 1 | 8 | 0.6154 |
| zaragoza | soundwave-electronics | 3223 | 11 | 2 | 5 | 0.4545 |
| zaragoza | urban-thread | 1974 | 13 | 4 | 5 | 0.3846 |

## 3. Log de corridas

- Total de registros de corrida: **18**

- Corrida: `37fe38af...` → **running**
- Corrida: `37fe38af...` → **success** (277 eventos → 10 filas)
- Corrida: `bfb133e1...` → **running**
- Corrida: `bfb133e1...` → **success** (277 eventos → 10 filas)
- Corrida: `07649fd6...` → **running**
- Corrida: `07649fd6...` → **failed**
- Corrida: `22848fb9...` → **running**
- Corrida: `22848fb9...` → **success** (277 eventos → 10 filas)
- Corrida: `a103c342...` → **running**
- Corrida: `a103c342...` → **success** (277 eventos → 10 filas)
- Corrida: `01de7996...` → **running**
- Corrida: `01de7996...` → **success** (277 eventos → 10 filas)
- Corrida: `1b64ea8a...` → **running**
- Corrida: `1b64ea8a...` → **success** (277 eventos → 10 filas)
- Corrida: `c0c91b25...` → **running**
- Corrida: `c0c91b25...` → **success** (277 eventos → 10 filas)
- Corrida: `2229bf44...` → **running**
- Corrida: `2229bf44...` → **success** (277 eventos → 10 filas)

## 4. Idempotencia

- Re-ejecución: 277 eventos → 10 filas (status: success)
- ✅ **Idempotente**: mismo número de filas en ambas ejecuciones

---

## ✅ Validación exitosa

Todas las verificaciones pasaron correctamente.

*Fin del reporte — ✅*