"""Phase 3.2: build the background library for stories (the knowledge RAG retrieves).

Two free, openly licensed sources:
  * Chinese Wikipedia (CC BY-SA 4.0): modern Chinese articles on myths, history,
    festivals and East Asian tales. Good for facts.
  * Chinese Wikisource 维基文库 (public domain): the ORIGINAL classical texts,
    e.g. 山海经 and 搜神记. Good for authentic old stories to retell.

Both are MediaWiki sites with the same JSON API:
  https://zh.wikipedia.org/w/api.php?action=query&prop=extracts&explaintext=1&titles=嫦娥

Output: data/library/sources.jsonl (one document per line)
Run:    python tools/fetch_sources.py            (fetches what's missing)
        python tools/fetch_sources.py --refresh  (re-downloads everything)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from common import DATA, USER_AGENT, to_simplified

OUT = DATA / "library" / "sources.jsonl"

# Starter topics: Chinese Wikipedia titles, grouped by the app's story categories.
WIKIPEDIA_TOPICS = {
    "folklore": ["嫦娥", "嫦娥奔月", "羿", "女娲补天", "盘古", "精卫填海", "夸父逐日",
                 "牛郎织女", "白蛇传", "孟姜女", "梁山伯与祝英台", "愚公移山", "大禹治水",
                 "哪吒", "孙悟空", "年兽", "八仙过海", "西王母"],
    "history": ["秦始皇", "万里长城", "丝绸之路", "郑和下西洋", "孔子", "四大发明",
                "赤壁之战", "武则天", "玄奘", "秦始皇陵兵马俑"],
    "east_asia": ["桃太郎", "竹取物语", "浦岛太郎", "兴夫传", "春香传", "檀君",
                  "雪女", "一寸法师"],
    "culture": ["春节", "中秋节", "端午节", "元宵节", "大熊猫", "茶", "饺子", "书法"],
}

# Classical texts on Wikisource: a page-title prefix and how many sub-pages to take.
WIKISOURCE_TEXTS = [
    {"prefix": "山海經/", "category": "classics", "max_pages": 18},
    {"prefix": "搜神記/第", "category": "classics", "max_pages": 20},
]


def api(host: str, retries: int = 5, **params) -> dict:
    """Call a MediaWiki API, waiting politely when the server says "slow down".

    HTTP 429 = Too Many Requests. The server often sends Retry-After (seconds);
    otherwise we back off exponentially: 2s, 4s, 8s, ...
    """
    params |= {"format": "json", "formatversion": "2"}
    url = f"https://{host}/w/api.php?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == retries - 1:
                raise
            wait = int(e.headers.get("Retry-After") or 0) or 2 ** (attempt + 1)
            print(f"    … rate-limited, waiting {wait}s")
            time.sleep(wait)
    raise RuntimeError("unreachable")


def _doc_id(host: str, title: str) -> str:
    return hashlib.sha1(f"{host}/{title}".encode()).hexdigest()[:12]


def _clean(text: str) -> str:
    text = to_simplified(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.replace("　", "").strip()


def fetch_page(host: str, title: str, category: str, licence: str) -> dict | None:
    # converttitles: find 浦岛太郎 even if the page is stored as 浦島太郎.
    d = api(host, action="query", prop="extracts|pageprops", explaintext=1,
            redirects=1, converttitles=1, variant="zh-cn", titles=title)
    page = d["query"]["pages"][0]
    if page.get("missing"):
        print(f"  ✗ not found: {title}")
        return None
    if "disambiguation" in page.get("pageprops", {}):
        # A disambiguation page only lists other pages ("后羿 may mean ...").
        print(f"  ~ disambiguation page, skipped: {title}")
        return None
    text = _clean(page.get("extract", ""))
    if len(text) < 100:
        print(f"  ✗ too short: {title}")
        return None
    real_title = to_simplified(page["title"])
    return {
        "id": _doc_id(host, page["title"]),
        "collection": "wikisource" if "wikisource" in host else "wikipedia",
        "category": category,
        "title": real_title,
        "text": text,
        "url": f"https://{host}/wiki/" + urllib.parse.quote(page["title"]),
        "licence": licence,
    }


def wikisource_titles(prefix: str, limit: int) -> list[str]:
    d = api("zh.wikisource.org", action="query", list="allpages", apprefix=prefix, aplimit=limit)
    return [p["title"] for p in d["query"]["allpages"]]


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true", help="re-download existing documents")
    args = ap.parse_args()

    OUT.parent.mkdir(parents=True, exist_ok=True)
    docs: dict[str, dict] = {}
    if OUT.exists() and not args.refresh:
        for line in OUT.read_text(encoding="utf-8").splitlines():
            d = json.loads(line)
            docs[d["id"]] = d
    have = {(d["collection"], d["title"]) for d in docs.values()}

    jobs = []  # (host, title, category, licence)
    for category, titles in WIKIPEDIA_TOPICS.items():
        jobs += [("zh.wikipedia.org", t, category, "CC BY-SA 4.0") for t in titles]
    for src in WIKISOURCE_TEXTS:
        for t in wikisource_titles(src["prefix"], src["max_pages"]):
            jobs.append(("zh.wikisource.org", t, src["category"], "public domain"))

    for host, title, category, licence in jobs:
        coll = "wikisource" if "wikisource" in host else "wikipedia"
        if (coll, to_simplified(title)) in have:
            continue
        try:
            doc = fetch_page(host, title, category, licence)
        except Exception as e:
            print(f"  ✗ {title}: {e}")
            continue
        if doc:
            docs[doc["id"]] = doc
            print(f"  ✓ [{category}] {doc['title']} ({len(doc['text'])} chars)")
        time.sleep(1.0)  # be gentle with a free service: about one page per second

    ordered = sorted(docs.values(), key=lambda d: (d["collection"], d["category"], d["title"]))
    OUT.write_text("".join(json.dumps(d, ensure_ascii=False) + "\n" for d in ordered), encoding="utf-8")
    total = sum(len(d["text"]) for d in ordered)
    print(f"\n{len(ordered)} documents, {total:,} characters in {OUT.relative_to(DATA.parent)}")


if __name__ == "__main__":
    main()
