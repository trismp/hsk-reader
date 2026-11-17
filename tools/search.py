"""Phase 3.3 + 3.4: chunk the library and search it. The "R" in RAG.

WHY CHUNK?
  A Wikipedia article can be 20,000 characters. If we handed Claude whole
  articles, a few results would flood its context. So we cut documents into
  ~400-character chunks (whole paragraphs, never mid-sentence), and search
  returns only the few chunks that matter. Each chunk remembers its document
  title and section, so it still makes sense on its own.

HOW SEARCH WORKS (keyword search with BM25):
  1. Split every chunk into words with jieba, dropping filler words (的, 了, 是 …).
  2. For a query, score each chunk by how often it contains the query's words,
     with two classic adjustments (this formula is called BM25, used by many
     search engines):
       - rare words count more  (嫦娥 is a stronger signal than 中国)
       - long chunks don't win just by being long
  3. Return the top k chunks.

  Limitation: only EXACT words match. "moon goddess" or 月宫仙女 won't find
  嫦娥. Semantic search (embeddings) fixes that; see the end of Phase 3.

Run:  python tools/search.py 嫦娥 偷药
      python tools/search.py --collection news 半导体
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter
from functools import lru_cache

import jieba

from common import DATA

LIBRARY = DATA / "library"
NEWS = LIBRARY / "news.jsonl"
SOURCES = LIBRARY / "sources.jsonl"
CHUNK_CHARS = 400
HAS_WORD = re.compile(r"[一-鿿A-Za-z0-9]")
STOPWORDS = set("的 了 是 在 和 与 及 也 有 就 都 而 被 把 为 对 于 从 以 之 其 这 那 一 个 他 她 它 们 "
                "中 上 下 等 或 并 将 已 曰 者 所 但 又 还 与 着 过 吗 呢 吧 啊".split())
K1, B = 1.5, 0.75  # standard BM25 settings


def _tokens(text: str) -> list[str]:
    return [w for w in jieba.lcut_for_search(text.lower())
            if HAS_WORD.search(w) and w not in STOPWORDS]


def _read_jsonl(path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def chunk_document(doc: dict) -> list[dict]:
    """Split one document into chunks of whole paragraphs, tracking the section."""
    base = {k: doc.get(k) for k in ("id", "collection", "category", "title", "url", "date", "source")}

    if doc["collection"] == "news":  # news items are short: one chunk each
        return [{**base, "chunk": 0, "section": None,
                 "text": f"{doc['title']}。{doc.get('summary', '')}"}]

    chunks, buf, section = [], [], None

    def flush():
        if buf:
            chunks.append({**base, "chunk": len(chunks), "section": section, "text": "\n".join(buf)})
            buf.clear()

    for para in (p.strip() for p in doc["text"].split("\n")):
        if not para:
            continue
        heading = re.fullmatch(r"=+\s*(.+?)\s*=+", para)
        if heading:  # "== 起源 ==" starts a new section
            flush()
            section = heading.group(1)
            continue
        if buf and sum(map(len, buf)) + len(para) > CHUNK_CHARS:
            flush()
        buf.append(para)
    flush()
    return chunks


@lru_cache(maxsize=1)
def _index() -> tuple[list[dict], list[Counter], dict[str, float], float]:
    """Chunk everything and pre-compute what BM25 needs. Cached per process."""
    jieba.setLogLevel("WARNING")
    jieba.load_userdict(str(DATA / "jieba_words.txt"))
    chunks = [c for d in _read_jsonl(NEWS) + _read_jsonl(SOURCES) for c in chunk_document(d)]
    # Index the title and section with the text, so "嫦娥" finds chunks of the 嫦娥 page.
    term_counts = [Counter(_tokens(f"{c['title']} {c['section'] or ''} {c['text']}")) for c in chunks]
    n = len(chunks)
    doc_freq = Counter(t for tc in term_counts for t in tc)
    idf = {t: math.log(1 + (n - df + 0.5) / (df + 0.5)) for t, df in doc_freq.items()}
    avg_len = sum(sum(tc.values()) for tc in term_counts) / max(n, 1)
    return chunks, term_counts, idf, avg_len


def search(query: str, k: int = 5, collection: str | None = None,
           category: str | None = None) -> list[dict]:
    """Return the k best-matching chunks (with score), optionally filtered."""
    chunks, term_counts, idf, avg_len = _index()
    q_terms = set(_tokens(query))
    results = []
    for chunk, tc in zip(chunks, term_counts):
        if collection and chunk["collection"] != collection:
            continue
        if category and chunk.get("category") != category:
            continue
        length = sum(tc.values())
        score = 0.0
        for t in q_terms:
            f = tc.get(t, 0)
            if f:
                score += idf[t] * f * (K1 + 1) / (f + K1 * (1 - B + B * length / avg_len))
        if score > 0:
            results.append({**chunk, "score": round(score, 2)})
    results.sort(key=lambda r: r["score"], reverse=True)
    return results[:k]


def stats() -> dict:
    chunks, _, idf, _ = _index()
    return {"chunks": len(chunks), "vocabulary": len(idf),
            "by_collection": dict(Counter(c["collection"] for c in chunks))}


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Keyword search over the library.")
    ap.add_argument("query", nargs="+")
    ap.add_argument("-k", type=int, default=5)
    ap.add_argument("--collection", choices=["news", "wikipedia", "wikisource"])
    ap.add_argument("--category")
    a = ap.parse_args()
    print(stats())
    for r in search(" ".join(a.query), a.k, a.collection, a.category):
        where = r["title"] + (f" › {r['section']}" if r["section"] else "")
        print(f"\n[{r['score']}] {r['collection']} | {where}")
        print("   " + r["text"][:160].replace("\n", " ") + ("…" if len(r["text"]) > 160 else ""))


if __name__ == "__main__":
    main()
