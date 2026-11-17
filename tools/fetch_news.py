"""Phase 3.1: collect Chinese news headlines + summaries from RSS feeds.

RSS is a simple XML format that news sites publish so readers can follow them.
Each item has a title, a link, a short description and a date. That's all we
store here (the "R" in RAG needs something to search). Full articles are only
fetched later, one at a time, when someone actually wants to read or rewrite
one (see tools/article.py).

Output: data/library/news.jsonl  (one JSON object per line, newest kept, no duplicates)
        Git-ignored: it's other people's journalism, for personal study only.

Run:  python tools/fetch_news.py
"""
from __future__ import annotations

import hashlib
import html
import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from common import DATA, USER_AGENT, to_simplified

FEEDS = [
    {"source": "BBC 中文", "url": "https://feeds.bbci.co.uk/zhongwen/simp/rss.xml"},
    {"source": "DW 中文", "url": "https://rss.dw.com/rdf/rss-chi-all"},
    {"source": "RFI 中文", "url": "https://www.rfi.fr/cn/rss"},
    {"source": "中新网", "url": "https://www.chinanews.com.cn/rss/scroll-news.xml"},
]
OUT = DATA / "library" / "news.jsonl"
KEEP_DAYS = 30
TAG = re.compile(r"<[^>]+>")


def _local(tag: str) -> str:
    """'{http://purl.org/rss/1.0/}item' -> 'item' (RSS 1.0 / RDF uses namespaces)."""
    return tag.rsplit("}", 1)[-1]


def _clean(text: str | None) -> str:
    text = html.unescape(TAG.sub("", text or ""))
    return to_simplified(re.sub(r"\s+", " ", text).strip())


def _iso_date(raw: str) -> str:
    try:  # RSS 2.0: "Tue, 17 Nov 2026 11:37:12 GMT"
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        try:  # RSS 1.0 / Dublin Core: "2026-11-17T11:38:00Z"
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            dt = datetime.now(timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="minutes")


def fetch_feed(feed: dict) -> list[dict]:
    req = urllib.request.Request(feed["url"], headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=20) as resp:
        root = ET.fromstring(resp.read())

    items = []
    for item in (e for e in root.iter() if _local(e.tag) == "item"):
        f = {_local(c.tag): (c.text or "") for c in item}
        link = f.get("link", "").strip()
        title = _clean(f.get("title"))
        if not link or not title:
            continue
        link = link.split("?at_medium=")[0].replace("/trad", "/simp")  # drop BBC tracking
        items.append({
            "id": hashlib.sha1(link.encode()).hexdigest()[:12],
            "collection": "news",
            "source": feed["source"],
            "title": title,
            "summary": _clean(f.get("description")),
            "url": link,
            "date": _iso_date(f.get("pubDate") or f.get("date") or ""),
        })
    return items


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    OUT.parent.mkdir(parents=True, exist_ok=True)

    existing: dict[str, dict] = {}
    if OUT.exists():
        for line in OUT.read_text(encoding="utf-8").splitlines():
            doc = json.loads(line)
            existing[doc["id"]] = doc

    new = 0
    for feed in FEEDS:
        try:
            items = fetch_feed(feed)
        except Exception as e:  # one broken feed shouldn't stop the others
            print(f"  ✗ {feed['source']}: {e}")
            continue
        added = [i for i in items if i["id"] not in existing]
        for i in added:
            existing[i["id"]] = i
        new += len(added)
        print(f"  ✓ {feed['source']}: {len(items)} items, {len(added)} new")

    cutoff = datetime.now(timezone.utc).timestamp() - KEEP_DAYS * 86400
    docs = [d for d in existing.values() if datetime.fromisoformat(d["date"]).timestamp() >= cutoff]
    docs.sort(key=lambda d: d["date"], reverse=True)
    OUT.write_text("".join(json.dumps(d, ensure_ascii=False) + "\n" for d in docs), encoding="utf-8")
    print(f"\n{new} new; {len(docs)} articles from the last {KEEP_DAYS} days in {OUT.relative_to(DATA.parent)}")


if __name__ == "__main__":
    main()
