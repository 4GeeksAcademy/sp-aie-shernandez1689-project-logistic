# Diseno del Sistema RAG — TrackFlow

## 1. Propósito

Sistema de Retrieval-Augmented Generation (RAG) para que los account
managers de TrackFlow consulten informacion comercial (SLAs, devoluciones,
transportistas, tarifas de almacenamiento) mediante lenguaje natural,
obteniendo respuestas precisas trazables a los documentos fuente.

## 2. Arquitectura

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   UI (HTML)   │────>│  FastAPI     │────>│  Qdrant      │
│  /uis/rag.html│     │  /api/rag/*  │     │  Vector Store│
└──────────────┘     └──────┬───────┘     └──────────────┘
                            │
                            ▼
                     ┌──────────────┐
                     │  4Geeks LLM  │
                     │  (generacion)│
                     └──────────────┘
```

### Componentes

| Componente | Tecnologia | Responsabilidad |
|---|---|---|
| Corpus | Markdown en `docs/company-knowledge-base/` | Documentos fuente de la empresa |
| Chunking | `data/process/rag.py` (`_chunk_text`, `_split_markdown_sections`) | Divide documentos en fragmentos con solapamiento |
| Embeddings | API 4Geeks (`EMBEDDINGS_MODEL_ID`) | Convierte texto a vectores (NUNCA reusa el modelo de generacion) |
| Vector store | Qdrant (`trackflow_knowledge`) | Almacena y recupera vectores por similitud coseno |
| Retrieve | `data/pipelines/rag.py` (`retrieve()`) | Busqueda de chunks relevantes |
| Generacion | API 4Geeks (`LLM_MODEL_ID`) | Genera respuesta natural a partir del contexto recuperado |
| API | FastAPI (`services/api/routes/rag.py`) | Endpoint REST `POST /api/rag/query` |
| UI | HTML+Tailwind (`uis/rag.html`) | Interfaz de consulta para account managers |

## 3. Pipeline de Indexacion (setup)

```
Documentos MD → Carga → Chunking por secciones
    → Embedding (4Geeks) → Insercion en Qdrant
```

### Parametros de chunking

| Parametro | Valor | Justificacion |
|---|---|---|
| CHUNK_SIZE | 600 caracteres | Suficiente para capturar contexto sin exceder ventana del LLM |
| CHUNK_OVERLAP | 100 caracteres | Evita perder informacion en cortes entre chunks |
| Estrategia | Por seccion markdown (##, ###) | Respeta la estructura natural de los documentos |

## 4. Pipeline de Consulta (query)

```
Pregunta → Embedding (4Geeks) → Busqueda Qdrant (top_k=3)
    → Contexto recuperado → LLM (4Geeks) → Respuesta + Fuentes
```

### Umbral de similitud

- **Valor:** 0.45 (distancia coseno)
- **Justificacion:** Tras pruebas iniciales, este umbral filtra ruido
  semantico sin descartar chunks relevantes. Si ningun chunk supera
 0.45, la respuesta indica que no hay informacion suficiente.

### Prompt del sistema

El system prompt incluye 8 reglas estrictas:
1. Responder SOLO con informacion de los fragmentos proporcionados.
2. NUNCA inventar porcentajes, tarifas, plazos o condiciones.
3. Descuentos no documentados requieren aprobacion de Miguel Torres.
4. SLAs se extenden durante picos de demanda declarados.
5. Devoluciones internacionales NUNCA son automaticas.
6. Decir explicitamente si no hay informacion suficiente.
7. Citar siempre la fuente (documento y seccion).
8. Responder en el mismo idioma de la pregunta.

## 5. Estructura de Datos en Qdrant

### Coleccion: `trackflow_knowledge`

**Configuracion vectorial:**
- Dimension: 1536 (text-embedding-3-small)
- Distancia: COSINE
- Nombre exacto requerido por el briefing: `trackflow_knowledge`

**Payload de cada punto:**
```json
{
  "company": "trackflow",
  "source_document": "trackflow-sla-delivery",
  "section": "Estandares de entrega",
  "language": "es",
  "chunk_index": 0,
  "text": "cuerpo del chunk usado para armar el prompt"
}
```

## 6. Endpoints API

### POST /api/rag/query

**Request:**
```json
{
  "question": "Cual es el SLA de entrega en Espana?",
  "top_k": 3
}
```

**Response (200):**
```json
{
  "question": "Cual es el SLA de entrega en Espana?",
  "answer": "El SLA estandar para Espana peninsula es de 2-3 dias laborables...",
  "sources": [
    {
      "text": "...",
      "source_document": "trackflow-sla-delivery",
      "section": "Espana peninsular",
      "score": 0.89,
      "language": "es"
    }
  ],
  "has_sufficient_info": true
}
```

### GET /api/rag/health
```json
{"status": "ok", "service": "rag"}
```

## 7. KPIs y Umbrales

| Metrica | Objetivo | Medicion |
|---|---|---|
| Recall@3 | >= 80% | Preguntas de prueba en data/eval/test-queries.json |
| Faithfulness | 100% | Ninguna respuesta debe diferir de los chunks recuperados |
| Umbral minimo | 0.45 | Documentado; si no se supera, responder "sin informacion" |

## 8. Seguridad y Restricciones de Negocio

- No prometer SLA durante altas demandas sin la advertencia del documento.
- Devoluciones internacionales: siempre gestion manual, nunca automaticas.
- Descuentos no documentados requieren aprobacion de Miguel Torres.
- API Key de ambos modelos (embedding y generacion) configuradas en .env.
- Embedding y LLM usan modelos SEPARADOS e IDs diferentes.

## 9. Archivos del Sistema

```
docs/company-knowledge-base/
  trackflow-sla-delivery.es.md
  trackflow-returns-policy.es.md
  trackflow-carrier-coverage.es.md
  trackflow-storage-pricing.es.md

data/
  process/rag.py          # Chunking + embedding + setup
  pipelines/rag.py        # Retrieve + query
  eval/test-queries.json  # 10 preguntas de prueba

services/api/
  .env                    # Config: URLs, API keys, modelo IDs
  routes/rag.py           # FastAPI router
  main.py                 # Registro del router (ya modificado)

uis/
  rag.html               # Interfaz de consulta

tests/
  pipelines/test_rag.py   # Tests unitarios

docs/rag/
  rag-design.md            # Este documento

docker-compose.yml         # Servicio Qdrant agregado
```