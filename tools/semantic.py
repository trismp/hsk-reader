"""Phase 3.4b: semantic search (embeddings) and hybrid search.

EMBEDDINGS IN ONE PARAGRAPH
  An embedding model reads a text and outputs a list of numbers (here 512):
  a "meaning fingerprint". Texts that mean similar things get similar lists,
  even with no words in common: 飞到月亮上的女人 lands near 嫦娥奔月.
  To search, we fingerprint every chunk ONCE (slow, cached to disk), then for
  each query we fingerprint just the query and find the closest chunks
  (cosine similarity = how closely two fingerprints point the same way).

  Model: BAAI/bge-small-zh-v1.5 (Chinese, 90 MB, runs on your CPU, free).

HYBRID SEARCH
  Keyword search is precise on names and rare words (逄蒙, 汨罗江).
  Semantic search understands paraphrases and questions.
  Hybrid runs both and merges the two rankings with Reciprocal Rank Fusion:
  each chunk gets 1/(60 + rank) from each list, and the sums are compared.
  Simple, and robust because it ignores the two methods' incompatible scores.

Run:  python tools/semantic.py 飞到月亮上的女人             (compare all three)
      python tools/semantic.py --mode semantic 为什么吃粽子
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from functools import lru_cache

import numpy as np

import search as keyword
from common import DATA

MODEL = "BAAI/bge-small-zh-v1.5"
MODEL_DIR = DATA / "models"
INDEX_DIR = DATA / "library" / "index"
# bge models retrieve better when short queries carry this instruction prefix.
QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章："
RRF_K = 60


@lru_cache(maxsize=1)
def _model():
    import warnings
    warnings.filterwarnings("ignore")
    from fastembed import TextEmbedding
    return TextEmbedding(MODEL, cache_dir=str(MODEL_DIR))


def _chunk_text(c: dict) -> str:
    return f"{c['title']} {c['section'] or ''}\n{c['text']}"


def _normalise(m: np.ndarray) -> np.ndarray:
    return m / np.linalg.norm(m, axis=-1, keepdims=True)


@lru_cache(maxsize=1)
def _vectors() -> np.ndarray:
    """One vector per chunk, cached on disk and rebuilt only when the library changes."""
    chunks = keyword._index()[0]
    texts = [_chunk_text(c) for c in chunks]
    fingerprint = hashlib.sha1("\n\x00".join(texts).encode()).hexdigest()[:16]
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    cache = INDEX_DIR / f"vectors-{fingerprint}.npy"
    if cache.exists():
        return np.load(cache)

    for old in INDEX_DIR.glob("vectors-*.npy"):
        old.unlink()  # library changed: old vectors no longer line up with chunks
    print(f"Embedding {len(texts)} chunks (one-time, may take a few minutes)…", file=sys.stderr)
    t = time.time()
    vecs = _normalise(np.array(list(_model().embed(texts, batch_size=32)), dtype=np.float32))
    np.save(cache, vecs)
    print(f"  done in {time.time() - t:.0f}s -> {cache.name}", file=sys.stderr)
    return vecs


def _filtered(collection, category):
    chunks = keyword._index()[0]
    return [i for i, c in enumerate(chunks)
            if (not collection or c["collection"] == collection)
            and (not category or c.get("category") == category)]


def semantic_search(query: str, k: int = 5, collection: str | None = None,
                    category: str | None = None) -> list[dict]:
    chunks = keyword._index()[0]
    vecs = _vectors()
    q = _normalise(np.array(list(_model().query_embed([QUERY_PREFIX + query]))[0], dtype=np.float32))
    allowed = _filtered(collection, category)
    sims = vecs[allowed] @ q  # cosine similarity, since all vectors are normalised
    top = np.argsort(-sims)[:k]
    return [{**chunks[allowed[i]], "score": round(float(sims[i]), 3)} for i in top]


def hybrid_search(query: str, k: int = 5, collection: str | None = None,
                  category: str | None = None, pool: int = 30) -> list[dict]:
    """Reciprocal Rank Fusion of keyword and semantic rankings."""
    def key(c):
        return (c["id"], c["chunk"])

    fused: dict[tuple, dict] = {}
    for results in (keyword.search(query, pool, collection, category),
                    semantic_search(query, pool, collection, category)):
        for rank, c in enumerate(results):
            entry = fused.setdefault(key(c), {**c, "score": 0.0})
            entry["score"] += 1 / (RRF_K + rank + 1)
    ranked = sorted(fused.values(), key=lambda c: c["score"], reverse=True)[:k]
    for c in ranked:
        c["score"] = round(c["score"] * 1000, 1)  # friendlier numbers
    return ranked


MODES = {"keyword": keyword.search, "semantic": semantic_search, "hybrid": hybrid_search}


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("query", nargs="+")
    ap.add_argument("--mode", choices=[*MODES, "compare"], default="compare")
    ap.add_argument("-k", type=int, default=3)
    a = ap.parse_args()
    query = " ".join(a.query)
    modes = list(MODES) if a.mode == "compare" else [a.mode]
    for mode in modes:
        print(f"\n── {mode} ── {query}")
        hits = MODES[mode](query, a.k)
        if not hits:
            print("   (no results)")
        for h in hits:
            where = h["title"] + (f" › {h['section']}" if h["section"] else "")
            print(f"   [{h['score']}] {where}: {h['text'][:60].replace(chr(10), ' ')}…")


if __name__ == "__main__":
    main()
