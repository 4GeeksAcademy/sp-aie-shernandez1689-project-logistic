"""
Measure Recall@3 for the RAG pipeline eval queries.

Since the 4Geeks embeddings API is currently unreachable (Vercel deployment
removed), this script uses TF-IDF + cosine similarity as a local embedding
substitute to measure retrieval recall.

Usage:
    cd /workspaces/sp-aie-shernandez1689-project-logistic
    python3 data/eval/measure_recall.py
"""

import json
import sys
from pathlib import Path

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

# Ensure we can import from the project
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from data.process.rag import _chunk_text, _load_documents

EVAL_FILE = Path(__file__).resolve().parent / "test-queries.json"
KB_ROOT = Path(__file__).resolve().parent.parent.parent / "docs" / "company-knowledge-base"


def load_queries() -> list[dict]:
    with open(EVAL_FILE, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict) and "queries" in data:
        return data["queries"]
    if isinstance(data, list):
        return data
    raise TypeError(f"Unexpected JSON structure: {type(data)}")


def build_chunks() -> list[dict]:
    """Load documents and chunk them the same way as setup()."""
    docs = _load_documents(KB_ROOT)
    all_chunks: list[dict] = []
    for doc in docs:
        chunks = _chunk_text(
            text=doc["text"],
            source_doc=doc["source_document"],
            language=doc["language"],
        )
        all_chunks.extend(chunks)
    return all_chunks


def measure_recall(chunks: list[dict], queries: list[dict], k: int = 3) -> dict:
    """
    For each query, embed with TF-IDF, find top-k chunks by cosine similarity,
    and check if the expected chunk (by source_document + section) is in top-k.
    """
    chunk_texts = [c["text"] for c in chunks]

    vectorizer = TfidfVectorizer(
        analyzer="word",
        token_pattern=r"(?u)\b\w+\b",
        max_features=5000,
        stop_words=None,
    )
    chunk_vectors = vectorizer.fit_transform(chunk_texts)

    hits = 0
    details = []

    for q in queries:
        qid = q["id"]
        question = q["question"]
        exp_source = q["expected_source"]
        exp_section = q["expected_section"]
        exp_tokens = q.get("expected_text_tokens", [])

        query_vec = vectorizer.transform([question])
        sims = cosine_similarity(query_vec, chunk_vectors).flatten()
        top_k_indices = np.argsort(sims)[::-1][:k]

        top_chunks = []
        expected_found = False

        for idx in top_k_indices:
            c = chunks[idx]
            score = float(sims[idx])
            source_match = c["source_document"] == exp_source
            section_match = c["section"] == exp_section
            token_match = False
            if exp_tokens:
                token_match = all(t.lower() in c["text"].lower() for t in exp_tokens)

            top_chunks.append({
                "chunk_index": idx,
                "source_document": c["source_document"],
                "section": c["section"],
                "score": round(score, 4),
                "matches_source": source_match,
                "matches_section": section_match,
                "matches_tokens": token_match,
            })

            if source_match and (section_match or token_match):
                expected_found = True

        if expected_found:
            hits += 1
            status = "✅"
        else:
            status = "❌"

        details.append({
            "id": qid,
            "question": question[:80],
            "expected_source": exp_source,
            "expected_section": exp_section,
            "status": status,
            "found": expected_found,
            "top_k": top_chunks,
        })

    recall = hits / len(queries)
    return {
        "hits": hits,
        "total": len(queries),
        "recall": recall,
        "recall_pct": round(recall * 100, 1),
        "details": details,
    }


def main():
    print("=" * 70)
    print("\U0001f4ca RAG Recall@3 Measurement (TF-IDF local substitute)")
    print("=" * 70)

    queries = load_queries()
    print(f"\n\U0001f4dd Loaded {len(queries)} eval queries from {EVAL_FILE.name}")

    chunks = build_chunks()
    print(f"\U0001f4e6 Built {len(chunks)} chunks from {KB_ROOT}")

    print(f"\n\U0001f50d Measuring Recall@{3}...\n")

    result = measure_recall(chunks, queries, k=3)

    for d in result["details"]:
        print(f"  {d['status']} Q{d['id']}: {d['question']}")
        print(f"      Expected: {d['expected_source']} / {d['expected_section']}")
        for tc in d["top_k"]:
            src = tc["source_document"]
            sec = tc["section"]
            sc = tc["score"]
            markers = []
            if tc["matches_source"]:
                markers.append("src\u2713")
            if tc["matches_section"]:
                markers.append("sec\u2713")
            if tc["matches_tokens"]:
                markers.append("tok\u2713")
            marker_str = " ".join(markers) if markers else ""
            print(f"      [{sc:.4f}] {src} / {sec}  {marker_str}")
        print()

    print("=" * 70)
    print(f"\U0001f4ca Recall@{3}: {result['hits']}/{result['total']} = {result['recall_pct']}%")
    threshold = 80.0
    if result["recall_pct"] >= threshold:
        print(f"\u2705 \u2705 \u2705 TARGET METIDO: {result['recall_pct']}% \u2265 {threshold}%")
    else:
        print(f"\u274c  BAJO UMBRAL: {result['recall_pct']}% < {threshold}%")
    print("=" * 70)


if __name__ == "__main__":
    main()
