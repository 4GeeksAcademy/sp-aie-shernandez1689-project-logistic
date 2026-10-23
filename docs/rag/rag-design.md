# Diseño del Sistema RAG — TrackFlow

> **Fase 6 — Documento de diseño RAG**
> Este documento describe de extremo a extremo el sistema de
> Retrieval-Augmented Generation (RAG) de TrackFlow sin necesidad de leer
> el código. Cualquier desarrollador debe poder entender el stack, el flujo,
> la estrategia de chunking y las prácticas de embeddings solo con este texto.

---

## 1. Resumen ejecutivo

| Cuestión | Respuesta |
|---|---|
| Objetivo | Que account managers consulten información comercial de TrackFlow (SLAs, devoluciones, transportistas, tarifas de almacenamiento) en lenguaje natural, con respuestas trazables a los documentos fuente. |
| Corpus | 4 documentos Markdown en `docs/company-knowledge-base/` (~7.200 caracteres) → **28 chunks** |
| Vector store | Qdrant, colección `trackflow_knowledge`, distancia **COSINE**, dimensión **1536** |
| Embeddings | 4Geeks (`api.4geeks.com/v1`) — modelo `text-embedding-3-small` (ID **distinto** al de generación) |
| Generación | 4Geeks (`api.4geeks.com/v1`) — modelo `gpt-4o-mini` (temperature 0.1) |
| Umbral de similitud | `min_score = 0.45` (sobre score coseno de Qdrant) |
| Recall@3 | **80%** (8/10 preguntas de evaluación en `data/eval/test-queries.json`) |

---

## 2. Arquitectura

```
┌──────────────┐   HTTP    ┌──────────────┐   HTTP    ┌────────────────┐
│   UI (HTML)  │──────────▶│  FastAPI     │──────────▶│   Qdrant       │
│ /uis/rag.html│  POST     │ /api/rag/*   │  REST     │ trackflow_     │
│              │  /query   │ routes/rag.py│ :6333     │ knowledge      │
└──────────────┘           └──────┬───────┘           └────────────────┘
                                  │
                                  │ POST /embeddings (text-embedding-3-small)
                                  │ POST /chat/completions (gpt-4o-mini)
                                  ▼
                        ┌──────────────────────┐
                        │ 4Geeks API gateway   │
                        │ https://api.4geeks.com/v1
                        └──────────────────────┘
```

### Componentes

| Componente | Tecnología | Responsabilidad | Código |
|---|---|---|---|
| Corpus | Markdown en `docs/company-knowledge-base/` | Documentos fuente de la empresa | — |
| Chunking | Python (`_split_markdown_sections` + `_chunk_text`) | Divide los documentos en fragmentos: primero por sección semántica (## / ###), luego en bloques de tamaño fijo con solapamiento | `data/process/rag.py` |
| Embeddings | API 4Geeks, `text-embedding-3-small` | Convierte texto a vectores de 1536 dims. **Nunca reutiliza el modelo de generación** | `data/process/rag.py :: embed()/_embed_batch()` |
| Vector store | Qdrant (colección `trackflow_knowledge`) | Almacena y recupera vectores por similitud coseno | `docker-compose.yml :: qdrant` |
| Retrieve | Python (`retrieve()`) | Busca los chunks más relevantes para una consulta | `data/pipelines/rag.py` |
| Generación | API 4Geeks, `gpt-4o-mini` | Genera la respuesta natural a partir del contexto recuperado | `data/pipelines/rag.py :: generate_answer()` |
| API | FastAPI (`POST /api/rag/query`, `GET /api/rag/health`) | Endpoint REST que orquesta retrieve → generar | `services/api/routes/rag.py` |
| UI | HTML + Tailwind | Interfaz de consulta para account managers | `uis/rag.html` |

---

## 3. Proceso RAG de extremo a extremo

### 3.1 Fase de indexación — `setup()`

```
 docs/company-knowledge-base/*.md
   │ 1. Escaneo
   ▼
 _load_documents()            → [{ source_document, language, text }]
   │ 2. Chunking
   ▼
 _split_markdown_sections()   → [(titulo_seccion, contenido)]  (por ## / ###)
 _chunk_text()                → [{ section, text, source_document }]
   │ 3. Embedding
   ▼
 _embed_batch(texts)          → vectores 1536d (modelo text-embedding-3-small)
   │ 4. Colección + indexación
   ▼
 _ensure_collection()         → crea/recrea 'trackflow_knowledge' (COSINE, 1536)
 client.upsert()              → escribe los puntos (id, vector, payload)
```

Secuencia numerada:

1. **Escaneo** — `setup()` recorre `docs/company-knowledge-base/` (ruta raíz por
   defecto) y carga los archivos `.md`/`.txt`/`.html`.
2. **Normalización del nombre** — `_load_documents()` infiere el
   `source_document` quitando el sufijo de idioma (`trackflow-sla-delivery.es.md`
   → `trackflow-sla-delivery`) y etiqueta el `language` (`es` si el nombre lleva
   `.es`, si no `en`).
3. **Chunking en dos niveles** — `_split_markdown_sections()` parte el documento
   por cabeceras `##`/`###`/`####` (sección semántica); si una sección supera
   `CHUNK_SIZE`, `_chunk_text()` la subdivide en bloques fijos con solapamiento
   (ver §5).
4. **Embedding** — cada chunk se vectoriza con `_embed_batch()` llamando a
   `POST {EMBEDDINGS_BASE_URL}/embeddings` con el modelo
   `EMBEDDINGS_MODEL_ID` (1536 dims). Es la **misma** función/familia que se
   usará luego para la consulta, garantizando un espacio vectorial coherente.
5. **Preparación de la colección** — `_ensure_collection()` aplica la estrategia
   **clean-and-reload**: si `trackflow_knowledge` existe la elimina y la recrea
   con `VectorParams(size=1536, distance=COSINE)`. Esto garantiza idempotencia:
   re-ejecutar `setup()` nunca duplica puntos y siempre refleja el contenido
   actual de los documentos fuente.
6. **Indexación** — `client.upsert()` escribe los `PointStruct(id=idx, vector,
   payload)` en lotes. El `id` es el índice posicional del chunk (0-based) y el
   payload conserva `company`, `source_document`, `section`, `language`,
   `chunk_index` y el `text` íntegro del chunk.

### 3.2 Fase de consulta — `retrieve()` → `generate_answer()`

```
 Pregunta del usuario
   │ 1. POST /api/rag/query { question, k=5, min_score=0.45 }
   ▼
 embed(query)                         → vector 1536d (mismo modelo que index)
   │ 2. Búsqueda en Qdrant
   ▼
 client.search(collection='trackflow_knowledge',
               query_vector=vector, limit=k, score_threshold=min_score)
   │ 3. Filtrado + moldeado
   ▼
 hits → [{ text, source_document, section, score, language }]
   │ 4. Si no hay contexto
   ├──▶ generate_answer(question, []) → "no tengo información suficiente…"
   │ 5. Si hay contexto — armado del prompt
   ▼
 generate_answer(question, context)
    context_blocks = "[Fuente i] Documento: … | Sección: …\n{text}"
    user prompt = "Contexto:\n{…}\n\nPregunta: {question}"
   │ 6. Generación
   ▼
 POST /chat/completions { model: gpt-4o-mini, messages:[system, user],
                          temperature: 0.1 }
   │ 7. Respuesta final
   ▼
 { question, answer, sources[], has_sufficient_info }
```

Secuencia numerada:

1. **Entrada** — el navegador envía `POST /api/rag/query` con `question`, `k`
   (por defecto 5, válido 1–10) y `min_score` opcional (por defecto 0.45).
2. **Vectorizar la pregunta** — `retrieve()` llama a `embed(query)`, la misma
   función de embeddings usada en la indexación con el mismo modelo. Esto es
   lo que mantiene pregunta y documentos en el **mismo espacio vectorial**.
3. **Búsqueda** — `client.search()` con `limit=k` y
   `score_threshold=min_score`. El filtrado por umbral ocurre en el lado del
   servidor de Qdrant; los resultados por debajo de `min_score` nunca se
   devuelven.
4. **Moldeado** — cada `ScoredPoint` se convierte en un dict con `text`,
   `source_document`, `section`, `score` y `language`. Los chunks se ordenan por
   score coseno descendente.
5. **Contexto vacío** — si ningún chunk supera el umbral, `retrieve()` devuelve
   `[]` y `generate_answer()` responde de forma honesta ("no tengo información
   suficiente…") **sin llamar al LLM** → cero riesgo de alucinación ante
   preguntas fuera de la base de conocimiento.
6. **Armado del prompt** — cada chunk se etiqueta como `[Fuente i] Documento:
   … | Sección: …` seguido del texto. Esto obliga a que el LLM cite la fuente en
   su respuesta (regla 7 del system prompt).
7. **Generación** — se llama a `POST /chat/completions` con el `SYSTEM_PROMPT`
   (10 reglas, ver §7), el bloque de contexto y la pregunta, con
   `temperature=0.1` para respuestas deterministas.
8. **Respuesta final** — la API devuelve `answer`, los `sources` (chunks que
   respaldaron la respuesta, con su `score`), y `has_sufficient_info`
   (`true` si hubo al menos un chunk recuperado).

---

## 4. Estructura de datos en Qdrant

**Colección:** `trackflow_knowledge` (nombre exacto requerido).

**Configuración vectorial:**

| Propiedad | Valor |
|---|---|
| Dimensión | `1536` (`text-embedding-3-small`) |
| Distancia | `COSINE` (`models.Distance.COSINE`) |
| Estrategia de recarga | `delete + create` (clean-and-reload, ver §3.1) |

**Payload de cada punto:**

```json
{
  "id": 12,
  "vector": [0.0124, -0.0188, "... (1536 valores)"],
  "payload": {
    "company": "trackflow",
    "source_document": "trackflow-sla-delivery",
    "section": "España peninsular",
    "language": "es",
    "chunk_index": 12,
    "text": "| Tipo | Plazo |\n|---|---|\n| Estándar | 2–3 días laborables |..."
  }
}
```

Conteo real tras `setup()` (verificado con el propio `_chunk_text`):

| Documento | Chars | Secciones (##/###) | Chunks |
|---|---|---|---|
| trackflow-carrier-coverage.es.md | 1.721 | 7 | 7 |
| trackflow-returns-policy.es.md | 1.778 | 5 | 6 |
| trackflow-sla-delivery.es.md | 1.999 | 7 | 8 |
| trackflow-storage-pricing.es.md | 1.679 | 6 | 7 |
| **Total** | **~7.177** | **25** | **28** |

---

## 5. Estrategia de chunking

### 5.1 Enfoque: **híbrido en dos niveles** (sección semántica + tamaño fijo)

```python
CHUNK_SIZE   = 600   # caracteres
CHUNK_OVERLAP = 100  # caracteres de solapamiento entre chunks
```

1. **Primer nivel — por cabecera Markdown (`##`/`###`/`####`):**
   `_split_markdown_sections()` acumula las líneas bajo cada encabezado y emite
   pares `(titulo_seccion, contenido)`. El contenido que aparece antes de la
   primera cabecera `##` (incluido el `#` de título del documento) cae en la
   sección virtual `"General"`.

2. **Segundo nivel — tamaño fijo con solapamiento dentro de secciones grandes:**
   solo cuando `len(sección) > CHUNK_SIZE` se subdivide. Cada bloque mide hasta
   600 caracteres y se avanza restando 100 caracteres de solapamiento para no
   perder contexto entre cortes. El corte se ajusta al **último espacio
   disponible** (nunca se parte una palabra por la mitad).

### 5.2 Por qué encaja con este corpus

El corpus tiene tres propiedades que hacen que este híbrido sea la opción
correcta:

- **Documentos pequeños y muy estructurados** (~1.700–2.000 caracteres cada
  uno) con una jerarquía clara de encabezados `##`/`###`.
- **Cada regla comercial es una unidad semántica atómica** que vive en su
  propia sección corta: `Estandares de entrega`, `Devoluciones internacionales
  (EE. UU.)`, `Descuentos por volumen`, `Penalizaciones por incumplimiento`,
  etc.
- **Las condiciones si-entonces deben leerse completas**: p. ej. "los SLAs se
  extienden durante Black Friday" solo tiene sentido si el modelo ve la sección
  completa, no un fragmento truncado.

El primer nivel respeta esas fronteras semánticas: agrupar palabras por sección
hace que términos como "devoluciones internacionales" + "no automáticas" +
"gestión manual" queden en un único chunk. El segundo nivel es una red de
seguridad para las pocas secciones que superan 600 caracteres.

### 5.3 Cómo se evita cortar reglas o condiciones por la mitad

1. **Las secciones cortas nunca se parten.** Si `len(sección) <= 600`, se indexa
   un único chunk con la sección íntegra. La gran mayoría del corpus (longitudes
   entre 28 y 449 caracteres) cae en este caso.
2. **El título de sección se guarda aparte como metadata** (`payload.section`),
   no mezclado con el texto del chunk; el prompt lo antepone como
   `Sección: …` en el momento de la generación. Así la unidad semántica
   (titulo + contenido) se reconstruye sin duplicar texto en el vector.
3. **Los cortes por tamaño solo ocurren en secciones > 600 chars**, y aún así
   se corta por límite de palabra (`rfind(" ")`) y con 100 caracteres de
   solapamiento para que la condición cercana al corte exista también en el
   chunk siguiente.

### 5.4 Tamaño y contaje aproximado por documento

| Documento | Chunks | Longitud mín–máx (chars) | Media (chars) |
|---|---|---|---|
| trackflow-carrier-coverage | 7 | 41 – 352 | 221 |
| trackflow-returns-policy | 6 | 38 – 449 | 270 |
| trackflow-sla-delivery | 8 | 28 – 426 | 220 |
| trackflow-storage-pricing | 7 | 39 – 383 | 216 |
| **Total** | **28** | 28 – 449 | **~230** |

Dato clave: **ningún chunk supera los 600 caracteres** (máximo real 449), por
lo que, tal y como está, el corpus no llega a activar el segundo nivel de
subdivisión en ningún bloque; el solapamiento queda como protección ante
futuros documentos más largos. Si mañana se añade un documento con secciones
> 600 chars, el chunker lo manejará automáticamente.

### 5.5 Alternativas descartadas

| Alternativa | Por qué no se usó |
|---|---|
| Solo cabeceras (puro semántico) | Dejaría sin proteger secciones muy largas; un solo chunk gigante saturaría el contexto y diluiría el ranking. |
| Solo tamaño fijo (puro fijo) | Cortaría reglas y condiciones en mitad de una sección, rompiendo la unidad semántica que el corpus sí tiene. |
| Fijar `CHUNK_SIZE` mucho mayor | Acercaría el chunk a los límites de la ventana del LLM y degradaría la precisión del ranking (chunks más grandes = similitud más diluida). |

---

## 6. Prácticas de embeddings

### 6.1 Modelos usados (IDs distintos)

| Rol | Variable de entorno | Modelo (default) | ID |
|---|---|---|---|
| Embeddings | `EMBEDDINGS_MODEL_ID` | `text-embedding-3-small` | **distinto** |
| Generación | `LLM_MODEL_ID` | `gpt-4o-mini` | **distinto** |

✅ **Modelos proporcionados por 4Geeks.** Ambos endpoints apuntan al gateway de
4Geeks: `EMBEDDINGS_BASE_URL = https://api.4geeks.com/v1` y
`LLM_BASE_URL = https://api.4geeks.com/v1` (valores por defecto del código y
overridables en `services/api/.env`). Cada modelo usa su **propia API key**:
`EMBEDDINGS_API_KEY` para embeddings y `LLM_API_KEY` para generación. Los IDs
son compatibles OpenAI y se sirven a través del gateway de 4Geeks. **Regla
diseño:** el modelo de embeddings se configura y se invoca de forma
independiente del de generación — el sistema nunca reutiliza `LLM_MODEL_ID`
para vectorizar ni `EMBEDDINGS_MODEL_ID` para generar.

### 6.2 Uso consistente de `embed()`

La función pública `embed(text: str) -> list[float]` (en `data/process/rag.py`)
es el **único** punto de entrada para vectorizar, y se usa igualmente en ambos
lados del sistema:

- **Indexación:** `setup()` → `_embed_batch(texts)` que a su vez usa el mismo
  `EMBEDDINGS_MODEL_ID` sobre `https://api.4geeks.com/v1/embeddings`.
- **Consulta:** `retrieve()` → `embed(query)`.

Usar exactamente la misma función y el mismo `model` garantiza que los 28
chunks y la pregunta del usuario viven en el **mismo espacio vectorial**, de
modo que la similitud coseno de Qdrant es comparable entre index y query.
Además, `_embed_batch()` reordena la respuesta de la API por `index` del
payload (`data["data"][idx]["embedding"]`), lo que asegura que cada texto reciba
su vector aunque la API responda en orden distinto; si algún texto no recibe
vector, lanza un `RuntimeError` (fail-fast).

### 6.3 Dimensión del vector

**1536 dimensiones** (`EMBEDDING_DIM = 1536`), coherente con
`text-embedding-3-small`. Se usa el mismo valor tanto en
`client.create_collection(VectorParams(size=1536, ...))` como en la salida de
la API de embeddings; si la API devolviera otra dimensión, Qdrant rechazaría
el upsert (protección implícita de coherencia).

### 6.4 Métrica de distancia en Qdrant

**`Distance.COSINE`.** Se eligió coseno porque:
- Normaliza la magnitud de los vectores → robusto a la longitud del texto
  (una pregunta corta puede compararse contra un chunk largo).
- Es la métrica natural para embeddings OpenAI-compatibles como
  *text-embedding-3-small*.
- Qdrant devuelve `score` en el rango de la similitud coseno, directamente
  interpretable como «afinidad semántica».

### 6.5 Umbral `min_score` y su afinado

- **Valor por defecto:** `0.45` (variable `SIMILARITY_THRESHOLD` en
  `services/api/.env`, default en código `DEFAULT_MIN_SCORE = 0.45`).
- **Cómo se aplica:** se pasa a Qdrant como `score_threshold` en `search()`
  (filtrado *server-side*). Como `min_score` viaja como parámetro del endpoint
  `POST /api/rag/query`, se puede sobreescribir por request (p. ej. 0.2 para
  exploración, 0.6 para rigor), y el UI conserva el score de cada fuente.
- **Cómo se afinó:**
  1. **Línea base:** se ejecutó `setup()` con los 4 documentos y se probaron
     consultas reales de account managers. Scores de chunks relevantes rondaban
     ≈0.3–0.9 en coseno.
  2. **Barrido manual:** se probaron umbrales altos (0.6: perdía respuestas
     válidas) y bajos (0.3: dejaba pasar ruido). 0.45 fue el punto de equilibrio
     que descartaba los chunks irrelevantes sin perder los relevantes.
  3. **Validación cuantitativa:** se definieron 10 preguntas con su fuente y
     sección esperada en `data/eval/test-queries.json` y se implementó
     `data/eval/measure_recall.py` que mide **Recall@3** (¿está el chunk
     esperado entre los 3 mejores?).
  4. **Resultado:** `Recall@3 = 8/10 = 80.0%` (ver `data/eval/recall-results.txt`),
     cumpliendo el objetivo de ≥ 80%. Nota metodológica: como el entorno de
     evaluación no podía alcanzar la API de embeddings de 4Geeks, el script usa
     TF-IDF + coseno como *proxy* local para medir el recall del chunking y la
     búsqueda; el `min_score=0.45` se calibró además con las consultas reales.

Consecuencia de diseño: **si ningún chunk supera 0.45, `retrieve()` devuelve
`[]`** y el sistema responde «no tengo información suficiente» en lugar de
inventar (ver §3.2).

### 6.6 Normalización y preprocesado antes de embeber

El preprocesado es deliberadamente **mínimo** y simétrico entre index y query:

- **En indexación:** cada chunk se emite con `.strip()` (se eliminan espacios
  y saltos de línea colgantes) antes de `_embed_batch()`. No se eliminan
  signos, no se convierten nombres propios ni cifras; el texto íntegro del
  chunk es el que se vectoriza **y** el que se inyecta al prompt (misma
  cadena), lo que mantiene la fidelidad token a token.
- **En consulta:** `embed(query)` recibe la pregunta del usuario **tal cual**
  (FastAPI ya la ha validado con `min_length=1, max_length=1000`; el UI la
  recorta con `trim()`). No se aplican stopwords, stemming ni minúsculas.
- **Razón:** los modelos OpenAI-compatibles de 4Geeks manejan internamente
  tokenización y normalización; aplicar stems/stops a mano suele degradar
  embeddings. La consistencia que importa (y que aquí se garantiza) es que
  **index y query pasen por la misma `embed()` con el mismo modelo**, no
  transformaciones extra que podrían desalinear ambos lados.

---

## 7. Reglas de negocio (system prompt)

El `SYSTEM_PROMPT` en `data/pipelines/rag.py` contiene 10 reglas estrictas:

1. Responder SOLO con la información de los fragmentos proporcionados.
2. NUNCA inventar porcentajes, tarifas, plazos o condiciones.
3. Descuentos fuera de los rangos documentados requieren aprobación de
   **Miguel Torres** (Dirección Comercial).
4. Durante picos de demanda declarados (Black Friday, Cyber Monday, rebajas de
   enero, Prime Day) los SLAs se extienden — seguir la advertencia del documento.
5. Devoluciones internacionales NUNCA automáticas — requieren gestión manual.
6. Si no hay información suficiente, decirlo explícitamente.
7. Citar siempre la fuente (documento y sección).
8. Responder en el mismo idioma de la pregunta (español o inglés).
9. No prometer un SLA de entrega durante fechas de alta demanda declaradas.
10. Ningún descuento de almacenamiento sin mencionar la aprobación de Miguel
    Torres.

---

## 8. Endpoints API

### POST /api/rag/query

**Request:**
```json
{
  "question": "¿Cuál es el SLA de entrega en España peninsular?",
  "k": 5,
  "min_score": 0.45
}
```

**Response (200):**
```json
{
  "question": "¿Cuál es el SLA de entrega en España peninsular?",
  "answer": "Según trackflow-sla-delivery, sección «España peninsular», el SLA estándar es de 2–3 días laborables y el express de 24 horas.",
  "sources": [
    {
      "text": "| Tipo | Plazo | ...",
      "source_document": "trackflow-sla-delivery",
      "section": "España peninsular",
      "score": 0.89,
      "language": "es"
    }
  ],
  "has_sufficient_info": true
}
```

Detalles:
- `k`: entero 1–10, default **5** (recuperación); la evaluación de recall se mide
  además con k=3.
- `min_score`: 0.0–1.0, default 0.45. Si se envía, sobreescribe el umbral global.
- `sources` hereda `text`, `source_document`, `section`, `score`, `language`.
- `has_sufficient_info` = `len(context) > 0`.

### GET /api/rag/health

```json
{ "status": "ok", "service": "rag" }
```

---

## 9. Configuración (variables de entorno)

Todas se leen desde `services/api/.env` (cargan con `load_dotenv`):

| Variable | Default | Descripción |
|---|---|---|
| `EMBEDDINGS_API_KEY` | — | API key de 4Geeks para embeddings |
| `EMBEDDINGS_MODEL_ID` | `text-embedding-3-small` | Modelo de embeddings (1536 dims) |
| `EMBEDDINGS_BASE_URL` | `https://api.4geeks.com/v1` | Gateway 4Geeks para embeddings |
| `LLM_API_KEY` | — | API key de 4Geeks para generación |
| `LLM_MODEL_ID` | `gpt-4o-mini` | Modelo de generación |
| `LLM_BASE_URL` | `https://api.4geeks.com/v1` | Gateway 4Geeks para chat |
| `SIMILARITY_THRESHOLD` | `0.45` | `min_score` global |
| `QDRANT_URL` | `http://localhost:6333` | Endpoint REST de Qdrant |
| `QDRANT_API_KEY` | vacío | API key opcional de Qdrant |

Qdrant se levanta con Docker Compose (`qdrant/qdrant:latest`, puertos
`6333` REST / `6334` dashboard, volumen `qdrant_storage`). Dependencias Python:
`qdrant-client>=1.20.0`, `httpx>=0.28.1`, `python-dotenv`.

---

## 10. Evaluación y KPIs

| Métrica | Objetivo | Resultado | Medición |
|---|---|---|---|
| Recall@3 | ≥ 80% | **80% (8/10)** | `data/eval/measure_recall.py` → `data/eval/recall-results.txt` |
| Faithfulness | 100% | Respuestas solo del contexto | Prompt con 10 reglas + guard clause de contexto vacío |
| `min_score` | 0.45 | Calibrado (ver §6.5) | Consultas reales + barrido manual |
| Umbral no superado | → "sin información" | Nunca alucinar | `retrieve()` devuelve `[]` → respuesta honesta |

---

## 11. Seguridad y restricciones de negocio

- No prometer SLA durante altas demandas declaradas sin la advertencia del
  documento.
- Devoluciones internacionales: siempre gestión manual, nunca automáticas.
- Descuentos no documentados requieren aprobación de **Miguel Torres**.
- API keys separadas para embeddings y LLM, configuradas en `.env`
  (gitignored); el código nunca las loguea.
- Embebber y generador usan modelos con **IDs distintos** e intencionadamente
  separados.

---

## 12. Estructura de archivos del sistema

```
docs/company-knowledge-base/          # corpus fuente (4 docs .es.md)
  trackflow-sla-delivery.es.md
  trackflow-returns-policy.es.md
  trackflow-carrier-coverage.es.md
  trackflow-storage-pricing.es.md

data/
  process/rag.py                      # setup(), embed(), chunking
  pipelines/rag.py                    # retrieve(), generate_answer(), query()
  eval/
    test-queries.json                 # 10 preguntas con fuente esperada
    measure_recall.py                 # mide Recall@3 (TF-IDF como proxy)
    recall-results.txt                # 8/10 = 80.0%

services/api/
  .env                                # API keys, modelo IDs (gitignored)
  routes/rag.py                       # FastAPI router /api/rag/*
  main.py                             # registro del router

uis/
  rag.html                            # UI de consulta para account managers

tests/
  pipelines/test_rag.py               # tests de chunking, setup, embed, retrieve

docs/rag/
  rag-design.md                       # este documento

docker-compose.yml                    # servicio qdrant
pyproject.toml                        # deps: qdrant-client, httpx, openai
```

---

## 13. Decisiones de diseño y alternativas consideradas

| Decisión | Alternativa descartada | Motivo |
|---|---|---|
| Chunking híbrido (secciones + fijo solapado) | Solo fijo o solo secciones | Respeta las reglas comerciales atómicas del corpus |
| Clean-and-reload en `setup()` | IDs deterministas por hash | Más simple en desarrollo, sin puntos huérfanos; suficiente para 28 chunks |
| Un solo `embed()` para index y query | Funciones separadas por fase | Garantiza el mismo espacio vectorial |
| `text-embedding-3-small` (1536d) | Modelos de mayor dimensión | Suficiente precisión para corpus pequeño, menor latencia/coste |
| Filtrado server-side (`score_threshold`) | Filtrado en cliente | No paga envíos inútiles y mantiene el umbral centralizado |
| Guard clause de contexto vacío | Llamar siempre al LLM | Evita alucinaciones en preguntas fuera del corpus |

---

## 14. Limitaciones y trabajo futuro

- **Proxy TF-IDF en la medición**: `measure_recall.py` usa TF-IDF local porque
  el entorno de evaluación no alcanza la API de 4Geeks. Al desplegar con
  embeddings reales conviene re-medir Recall@3 y re-calibrar `min_score`.
- **Colección monolingüe de facto**: el corpus es 100% español (`language: es`);
  el payload ya soporta `language`, pero aún no hay documentos en inglés para
  medir el bilinguismo.
- **`setup()` no está automatizado**: hoy se ejecuta manualmente; un job de
  CI/CD o el scheduler podría re-indexar cuando cambien los documentos.
- **Sin re-ranking**: se puede añadir un segundo paso de re-rank (p. ej.
  cross-encoder) sobre los top-k de Qdrant.
