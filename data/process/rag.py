"""
RAG — Chunking, embedding e indexación en Qdrant.

Funciones públicas
------------------
setup(root: str | Path = None) -> None
    Escanea docs/company-knowledge-base/, divide en chunks, genera
    embeddings con el modelo de 4Geeks e indexa en Qdrant.

embed(text: str) -> list[float]
    Genera un vector de embedding para un texto usando
    el modelo de embeddings configurado (NUNCA el de generación).

_embed_batch(texts: list[str]) -> list[list[float]]
    Idem para múltiples textos (uso interno).
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

load_dotenv(
    os.path.join(os.path.dirname(__file__), "..", "..", "services", "api", ".env")
)

logger = logging.getLogger(__name__)

# ── Constantes ────────────────────────────────────────────────────
COLLECTION_NAME = "trackflow_knowledge"
CHUNK_SIZE = 600  # caracteres
CHUNK_OVERLAP = 100  # caracteres de solapamiento entre chunks
EMBEDDING_DIM = 1536  # text-embedding-3-small

# ── Configuración desde .env ──────────────────────────────────────
EMBEDDINGS_API_KEY: str | None = os.getenv("EMBEDDINGS_API_KEY")
EMBEDDINGS_MODEL_ID: str = os.getenv(
    "EMBEDDINGS_MODEL_ID", "text-embedding-3-small"
)
EMBEDDINGS_BASE_URL: str = os.getenv(
    "EMBEDDINGS_BASE_URL", "https://api.4geeks.com/v1"
)
QDRANT_URL: str = os.getenv("QDRANT_URL", "http://localhost:6333")
QDRANT_API_KEY: str | None = os.getenv("QDRANT_API_KEY", None)

# ── Cliente compartido (lazy) ─────────────────────────────────────
_qdrant: QdrantClient | None = None


def _get_qdrant() -> QdrantClient:
    global _qdrant
    if _qdrant is None:
        _qdrant = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)
    return _qdrant


# ── Chunking ───────────────────────────────────────────────────────


def _split_markdown_sections(text: str) -> list[tuple[str, str]]:
    """Divide un markdown por secciones (## o ###) y devuelve
    lista de (titulo_seccion, contenido_parcial)."""
    sections: list[tuple[str, str]] = []
    lines = text.splitlines()
    current_title = "General"
    current_lines: list[str] = []

    for line in lines:
        if re.match(r"^#{2,4}\s+", line):
            if current_lines:
                sections.append((current_title, "\n".join(current_lines).strip()))
            current_title = re.sub(r"^#+\s*", "", line).strip()
            current_lines = []
        else:
            current_lines.append(line)

    if current_lines:
        sections.append((current_title, "\n".join(current_lines).strip()))

    return sections


def _chunk_text(
    text: str, source_doc: str, language: str
) -> list[dict[str, Any]]:
    """Divide texto en chunks con solapamiento, etiquetando cada uno."""
    sections = _split_markdown_sections(text)
    chunks: list[dict[str, Any]] = []

    for section_title, section_body in sections:
        if not section_body.strip():
            continue
        # Si la seccion es pequena, un solo chunk
        if len(section_body) <= CHUNK_SIZE:
            chunks.append(
                {
                    "section": section_title,
                    "text": section_body,
                    "source_document": source_doc,
                }
            )
            continue

        # Dividir la seccion en fragmentos con solapamiento
        start = 0
        while start < len(section_body):
            end = min(start + CHUNK_SIZE, len(section_body))
            # Tratar de no cortar una palabra
            if end < len(section_body):
                next_space = section_body.rfind(" ", start + CHUNK_SIZE - 30, end)
                if next_space > start:
                    end = next_space
            chunk_text = section_body[start:end].strip()
            if chunk_text:
                chunks.append(
                    {
                        "section": section_title,
                        "text": chunk_text,
                        "source_document": source_doc,
                    }
                )
            start = (
                end - CHUNK_OVERLAP if end < len(section_body) else len(section_body)
            )

    return chunks


# ── Embedding (usa modelo SEPARADO del de generacion) ──────────────


def embed(text: str) -> list[float]:
    """Genera un vector de embedding para un texto usando
    el modelo de embeddings de 4Geeks.

    Esta funcion usa EXCLUSIVAMENTE el modelo configurado en
    EMBEDDINGS_MODEL_ID -- NUNCA el modelo de generacion (LLM_MODEL_ID).

    Parameters
    ----------
    text : str
        Texto a vectorizar.

    Returns
    -------
    list[float]
        Vector de embedding de dimension EMBEDDING_DIM.
    """
    vectors = _embed_batch([text])
    return vectors[0]


def _embed_batch(texts: list[str]) -> list[list[float]]:
    """Genera vectores de embedding para una lista de textos.
    Uso interno — para uso publico prefiere embed(text: str).
    """
    if not texts:
        return []

    headers = {
        "Authorization": f"Bearer {EMBEDDINGS_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "input": texts,
        "model": EMBEDDINGS_MODEL_ID,
    }

    with httpx.Client(timeout=30.0) as client:
        resp = client.post(
            f"{EMBEDDINGS_BASE_URL}/embeddings",
            headers=headers,
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()

    # OpenAI-compatible response: data["data"] es lista de {"object","index","embedding","model"}
    vectors: list[list[float]] = [None] * len(texts)  # type: ignore[list-item]
    for item in data["data"]:
        vectors[item["index"]] = item["embedding"]

    if any(v is None for v in vectors):
        raise RuntimeError(
            "Algun texto no recibio embedding -- revisa la respuesta de la API"
        )

    return vectors  # type: ignore[return-value]


# ── Setup (escaneo -> chunking -> embedding -> indexacion) ────────────


def _load_documents(knowledge_dir: Path) -> list[dict[str, Any]]:
    """Carga todos los documentos .md de la carpeta."""
    docs: list[dict[str, Any]] = []
    if not knowledge_dir.exists():
        logger.warning("No existe %s -- se omite carga", knowledge_dir)
        return docs

    for fpath in sorted(knowledge_dir.iterdir()):
        if fpath.suffix not in (".md", ".txt", ".html"):
            continue
        text = fpath.read_text(encoding="utf-8")
        # Inferir source_document del nombre del archivo (sin extension)
        stem = fpath.stem
        source_doc = re.sub(r"\.(es|en|fr|de|pt|it)$", "", stem)
        lang = "es" if ".es" in stem else "en"
        docs.append(
            {
                "source_document": source_doc,
                "language": lang,
                "text": text,
            }
        )
        logger.info("Cargado: %s (%d chars)", fpath.name, len(text))

    return docs


def setup(root: str | Path | None = None) -> None:
    """Escanea la carpeta de base de conocimiento, divide en chunks,
    genera embeddings e indexa en Qdrant.

    Parameters
    ----------
    root: ruta a la carpeta docs/company-knowledge-base/. Si es None,
         se usa <directorio del script>/../../docs/company-knowledge-base/.
    """
    if root is None:
        root = (
            Path(__file__).resolve().parent.parent.parent
            / "docs"
            / "company-knowledge-base"
        )
    root = Path(root)

    if not root.exists():
        logger.error("La carpeta %s no existe -- abortando setup", root)
        raise FileNotFoundError(
            f"No se encuentra la base de conocimiento: {root}"
        )

    # 1. Cargar documentos
    documents = _load_documents(root)
    if not documents:
        logger.warning("No se encontraron documentos en %s", root)
        return

    # 2. Chunking
    all_chunks: list[dict[str, Any]] = []
    for doc in documents:
        chunks = _chunk_text(
            text=doc["text"],
            source_doc=doc["source_document"],
            language=doc["language"],
        )
        for c in chunks:
            c["company"] = "trackflow"
            c["language"] = doc["language"]
        all_chunks.extend(chunks)
        logger.info(
            "Documento '%s' -> %d chunks", doc["source_document"], len(chunks)
        )

    if not all_chunks:
        logger.warning("No se generaron chunks -- revisa los documentos")
        return

    logger.info("Total chunks generados: %d", len(all_chunks))

    # 3. Generar embeddings
    texts_to_embed = [c["text"] for c in all_chunks]
    vectors = _embed_batch(texts_to_embed)
    logger.info("Embeddings generados: %d vectores", len(vectors))

    # 4. Crear coleccion (si no existe) e indexar
    client = _get_qdrant()
    _ensure_collection(client)

    points = [
        qmodels.PointStruct(
            id=idx,
            vector=vector,
            payload={
                "company": chunk["company"],
                "source_document": chunk["source_document"],
                "section": chunk["section"],
                "language": chunk["language"],
                "chunk_index": idx,
                "text": chunk["text"],
            },
        )
        for idx, (chunk, vector) in enumerate(zip(all_chunks, vectors))
    ]

    # Upsert en lote
    client.upsert(collection_name=COLLECTION_NAME, points=points)
    logger.info(
        "Indexacion completada: %d puntos en coleccion '%s'",
        len(points),
        COLLECTION_NAME,
    )


def _ensure_collection(client: QdrantClient) -> None:
    """Recrea la coleccion desde cero (clean-and-reload).

    Estrategia de idempotencia
    --------------------------
    Se usa **clean-and-reload**: en cada ejecucion se elimina la coleccion
    si existe y se crea de nuevo. Esto garantiza que:

    - No se duplican puntos al re-ejecutar setup().
    - Los puntos siempre reflejan el contenido actual de los documentos fuente.
    - Es seguro en desarrollo porque la recoleccion tarda ~1 segundo.

    Alternativa considerada: IDs deterministas (hash del contenido). Se
    descarto para desarrollo porque el clean-and-reload es mas simple y
    evita gestionar puntos huerfanos cuando cambian los documentos fuente.
    """
    collections = [c.name for c in client.get_collections().collections]
    if COLLECTION_NAME in collections:
        logger.info("Eliminando coleccion '%s' (clean-and-reload)", COLLECTION_NAME)
        client.delete_collection(collection_name=COLLECTION_NAME)

    client.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config=qmodels.VectorParams(
            size=EMBEDDING_DIM,
            distance=qmodels.Distance.COSINE,
        ),
    )
    logger.info(
        "Coleccion '%s' creada (dimension=%d)", COLLECTION_NAME, EMBEDDING_DIM
    )