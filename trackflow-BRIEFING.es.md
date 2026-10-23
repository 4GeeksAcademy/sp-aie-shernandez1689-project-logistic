# BRIEFING — TrackFlow

## Modelo de pronóstico de series de tiempo para predicción de ventas

---

### 1. Por qué esto le importa a TrackFlow

Thomas (CEO) necesita saber si es viable predecir el volumen de facturación de los próximos meses antes de invertir en un dashboard ejecutivo global. TrackFlow factura principalmente por volumen de envíos gestionados para marcas de e-commerce, y ese volumen tiene picos y valles muy marcados por el calendario del comercio electrónico — algo que Ana (Almacén) y Carlos (Carriers) ya anticipan de forma manual cada año.

---

### 2. Estructura de datos

El dataset mensual de ingresos consolidados de TrackFlow ya está incluido en tu monorepo, en `data/raw/trackflow_sales.csv`, con estas columnas exactas:

| Columna | Tipo | Descripción |
|---|---|---|
| `month` | fecha (`YYYY-MM-01`) | Primer día del mes reportado |
| `revenue_eur` | float | Ingresos totales del mes, consolidados en EUR |
| `shipments_processed` | int | Número total de envíos procesados en el mes (ambos países) |
| `avg_revenue_per_shipment_eur` | float | Ingreso promedio por envío del mes |
| `market` | string | `"us"`, `"spain"`, o `"consolidated"` — usa `"consolidated"` como fila principal para el modelo |

La variable objetivo (target) del modelo es `revenue_eur` de la fila `consolidated`.

---

### 3. KPIs y qué significa un buen modelo aquí

- Un **Gini** alto le importa a Thomas para distinguir con confianza entre un mes de temporada baja normal (por ejemplo, febrero) y una caída atípica que amerite investigación.
- El **PSI** ayuda a detectar si la mezcla de volumen entre Los Ángeles y Zaragoza cambió de forma significativa entre entrenamiento y prueba — repórtalo si ocurre, ya que podría indicar una expansión o contracción de operaciones en un país.
- El **MSE** repórtalo en EUR² y también como porcentaje del ingreso mensual promedio, para que Thomas y Ana puedan interpretarlo directamente.

---

### 4. Sobre el dataset provisto

El archivo `data/raw/trackflow_sales.csv` contiene **10 años** de datos mensuales (120 filas de `consolidated`), desde `2016-01` hasta `2025-12`. Ya refleja los siguientes patrones — no necesitas generarlos, pero sí entenderlos para interpretar los resultados de tu modelo:

**Patrón de crecimiento:** crecimiento anual base `X = 6%`, con variación `Y = 3%`. Cada año, el crecimiento real `d` alterna entre `X+Y` y `X-Y` (entre 3% y 9%), siempre positivo.

**Patrón de estacionalidad (presente cada año del dataset):**
- **Noviembre–Diciembre:** alza de ingresos del 25–35% respecto al promedio, por el pico de envíos de Black Friday y la temporada navideña de e-commerce.
- **Febrero:** caída de ingresos del 10–15% respecto al promedio, por la desaceleración típica del e-commerce después de la temporada alta.
- El resto de los meses fluctúa de forma moderada (±5%) alrededor de la tendencia de crecimiento anual.

El dataset se generó con una semilla aleatoria fija (`random_state=42`), por lo que es determinista.

---

### 5. Restricciones de negocio

- Todos los valores de `revenue_eur` deben ser positivos.
- No debe haber meses faltantes en el rango 2016-01 a 2025-12.
- El dataset provisto solo incluye la fila `consolidated`; si separas por mercado como feature adicional, recuerda que Los Ángeles (EE. UU.) mueve un volumen mayor que Zaragoza (España) — usa aproximadamente 60/40 como proporción de referencia.

---

### 6. Entregables esperados

- Script de entrenamiento en `scripts/` que cargue `data/raw/trackflow_sales.csv`, separe los primeros 8 años como entrenamiento y los últimos 2 como prueba.
- Features causales de lag, estadísticas rodantes y calendario/estacionalidad construidos a partir de la serie (validados contra el patrón de crecimiento y estacionalidad descrito arriba), sin fuga de información desde filas futuras.
- Modelo entrenado (XGBoost o Random Forest) con las 4 métricas (MSE, PSI, Gini, K2 Score) calculadas sobre el conjunto de prueba.
- Visualización con la predicción y su rango de variabilidad frente a los datos reales de los 2 años de prueba.
- Prueba unitaria en `tests/pipelines/` que valide el split 8/2 años.

# BRIEFING — TrackFlow

## Hito 7 — RAG y Base de Conocimiento

---

## 1. Introducción

Miguel Torres (Director Comercial) tiene 8 personas entre account managers y business development que responden constantemente las mismas preguntas de marcas prospecto y clientes actuales: qué SLA de entrega manejan, cómo funciona la política de devoluciones, qué transportistas cubren cada zona, cómo se calculan las tarifas de almacenamiento.

El **ticket** que el equipo comercial trasladó a tecnología pide un asistente que cualquier account manager pueda usar **como lo haría un vendedor de TrackFlow** en una llamada con un cliente: con datos exactos y sin prometer condiciones que no existen en los acuerdos estándar.

Tu base de conocimiento debe construirse a partir de los documentos fuente de la sección 2.

---

## 2. Documentos Fuente para la Base de Conocimiento

Usa los siguientes documentos fuente como base de tu base de conocimiento. Cópialos desde [`00-general-contexts/trackflow/`](../00-general-contexts/trackflow/) a `docs/company-knowledge-base/` en tu monorepo. Cada uno se entrega como un archivo independiente para que lo cargues directamente en tu pipeline de chunking.

| Archivo                                                                                                 | Contenido                   |
| ------------------------------------------------------------------------------------------------------- | --------------------------- |
| [`trackflow-sla-delivery.es.md`](../00-general-contexts/trackflow/trackflow-sla-delivery.es.md)         | SLA de Entrega              |
| [`trackflow-returns-policy.es.md`](../00-general-contexts/trackflow/trackflow-returns-policy.es.md)     | Política de Devoluciones    |
| [`trackflow-carrier-coverage.es.md`](../00-general-contexts/trackflow/trackflow-carrier-coverage.es.md) | Cobertura de Transportistas |
| [`trackflow-storage-pricing.es.md`](../00-general-contexts/trackflow/trackflow-storage-pricing.es.md)   | Tarifas de Almacenamiento   |

---

**Nombre de la colección Qdrant:** `trackflow_knowledge` — usa este nombre exacto en `setup()`.

## 3. Estructura de Datos de Dominio

```json
{
  "id": "uuid-del-chunk",
  "vector": [
    /* embedding */
  ],
  "payload": {
    "company": "trackflow",
    "source_document": "sla-delivery | returns-policy | carrier-coverage | storage-pricing",
    "section": "título o subtítulo de la sección de origen",
    "language": "es",
    "chunk_index": 0,
    "text": "cuerpo del chunk usado para armar el prompt"
  }
}
```

---

## 4. KPIs y Umbrales de la Respuesta

- **Recall@3**: al menos el 80% de las preguntas de prueba deben tener el
  chunk correcto entre los 3 primeros resultados recuperados.
- **Faithfulness**: ningún porcentaje, tarifa o plazo en la respuesta puede
  diferir de los chunks recuperados.
- Preguntas sobre descuentos de almacenamiento o excepciones de
  transportista fuera de lo documentado deben responder que requieren
  aprobación, nunca inventar una condición.

---

- **Umbral mínimo de similitud**: define un umbral y documenta por qué lo
  elegiste; si ningún chunk lo supera, la respuesta debe decir explícitamente
  que no hay información suficiente — nunca inventes datos.

## 5. Instrucciones de Datos Semilla

- Indexa los cuatro documentos fuente completos de la sección 2.
- Cada documento debe producir al menos 3 chunks.
- Crea `data/eval/test-queries.json` con al menos 8 preguntas cubriendo los
  cuatro documentos, incluyendo al menos una sobre picos de demanda
  (Black Friday / Rebajas).

---

## 6. Restricciones de Negocio

- Nunca se debe prometer un SLA de entrega durante fechas de alta demanda
  declaradas — debe seguirse la advertencia de `trackflow-sla-delivery.es.md`.
- Las devoluciones internacionales nunca deben describirse como
  "automáticas" — deben remitirse siempre a gestión manual.
- Ningún descuento de almacenamiento puede ofrecerse sin mencionar que
  requiere aprobación de Miguel Torres.

---

## 7. Entregables Esperados

- Base de conocimiento indexada en Qdrant con los cuatro documentos fuente.
- Endpoint FastAPI que responde preguntas como "¿cuál es la ventana de
  devolución estándar?" o "¿qué transportista cubre mejor Aragón rural?"
  con una respuesta generada y trazable a la fuente.
- Interfaz mínima de consulta funcionando contra ese endpoint.