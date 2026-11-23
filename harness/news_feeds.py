"""Collect today's Chinese tech headlines from RSS feeds and rank them by topic.

This part is plain code, no AI: download each feed, pull out title / link / date /
summary, drop duplicates, and score each headline by the companies and topics we
care about. The harness (news.py) then hands the best ones to Claude.

Try it on its own:  .venv/Scripts/python.exe harness/news_feeds.py
"""
import re, sys, urllib.request, xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

# Feeds tested on 2026-11-23. (36氪, 虎嗅, 机器之心 and 品玩 had no usable feed.)
FEEDS = {
    "IT之家": "https://www.ithome.com/rss/",                       # fast company & product news
    "界面新闻": "https://a.jiemian.com/index.php?m=article&a=rss&cid=65",  # business & tech companies
    "钛媒体": "https://www.tmtpost.com/rss.xml",                   # tech industry
    "量子位": "https://www.qbitai.com/feed",                        # AI models & AI companies
    "InfoQ": "https://www.infoq.cn/feed",                          # enterprise AI, AI agents
    "人人都是产品经理": "https://www.woshipm.com/feed",               # product management
    "极客公园": "https://www.geekpark.net/rss",                     # innovation, founders
    "雷峰网": "https://www.leiphone.com/feed",                      # AI, robots, cars
}

# What we care about. Each word found in a title scores 2, in the summary 1.
# Edit freely: this list decides which news Claude gets to choose from.
COMPANIES = ["腾讯", "阿里", "蚂蚁", "小米", "华为", "字节", "抖音", "百度", "京东", "美团", "拼多多",
             "比亚迪", "宁德", "大疆", "理想", "蔚来", "小鹏", "DeepSeek", "月之暗面", "Kimi", "智谱",
             "MiniMax", "阶跃", "英伟达", "OpenAI", "Anthropic", "苹果", "谷歌", "微软", "Meta", "特斯拉"]
TOPICS = ["大模型", "AI", "人工智能", "智能体", "Agent", "机器人", "人形机器人", "具身智能", "芯片",
          "发布", "新品", "手机", "融资", "出海", "产品经理", "产品", "增长", "创新", "自动驾驶", "开源"]
KEYWORDS = COMPANIES + TOPICS

CHINA = timezone(timedelta(hours=8))   # China time (Asia/Shanghai, no daylight saving)


def today_in_china() -> str:
    return datetime.now(CHINA).strftime("%Y-%m-%d")


UA = {"User-Agent": "Mozilla/5.0 (hsk-reader news harness)"}


def _tag(el) -> str:
    return el.tag.rsplit("}", 1)[-1]   # '{namespace}item' -> 'item'


def _text(el, *names) -> str:
    for child in el:
        if _tag(child) in names:
            return (child.text or child.get("href") or "").strip()
    return ""


def _plain(html: str, limit: int) -> str:
    text = re.sub(r"<[^>]+>", "", html)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


# Feeds that write Beijing time but label it "GMT" (checked 2026-11-24: InfoQ's newest item was
# "01:02 GMT" at 01:02 Beijing time). For these, keep the clock time and ignore the label.
MISLABELLED_GMT = {"InfoQ"}


def _china_time(raw: str, ignore_zone: bool = False) -> str:
    """Feed date (RSS or Atom style, any time zone) -> 'YYYY-MM-DD HH:MM' in China time."""
    for parse in (parsedate_to_datetime, lambda d: datetime.fromisoformat(d.replace("Z", "+00:00"))):
        try:
            dt = parse(raw)
            if dt.tzinfo and not ignore_zone:
                dt = dt.astimezone(CHINA)
            return dt.strftime("%Y-%m-%d %H:%M")
        except Exception:
            pass
    return raw[:16].replace("T", " ")


def fetch_feed(source: str, url: str) -> list[dict]:
    """Download one feed and return its items. A broken feed returns [] instead of crashing."""
    try:
        raw = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=25).read()
        root = ET.fromstring(raw)
    except Exception as e:
        print(f"  ! {source}: {type(e).__name__}", file=sys.stderr)
        return []
    items = []
    for el in root.iter():
        if _tag(el) not in ("item", "entry"):
            continue
        date = _text(el, "pubDate", "published", "updated", "date")
        date = _china_time(date, ignore_zone=source in MISLABELLED_GMT)
        items.append({"source": source, "title": _text(el, "title"), "url": _text(el, "link"), "date": date,
                      "summary": _plain(_text(el, "description", "summary") or _text(el, "encoded"), 300)})
    return items


def score(item: dict) -> int:
    return sum(2 * (k in item["title"]) + (k in item["summary"]) for k in KEYWORDS)


def top_headlines(limit: int = 40, days: int = 0) -> list[dict]:
    """All feeds -> keep only news published today in China time (days=1 also allows yesterday, ...),
    drop duplicates, videos and off-topic items -> best `limit`, highest score first."""
    with ThreadPoolExecutor(len(FEEDS)) as pool:
        batches = pool.map(lambda kv: fetch_feed(*kv), FEEDS.items())
    since = (datetime.now(CHINA) - timedelta(days=days)).strftime("%Y-%m-%d")   # midnight that day
    seen, items = set(), []
    for item in (i for batch in batches for i in batch):
        key = re.sub(r"\W", "", item["title"])[:20]   # same story from two feeds = same key
        if item["date"] < since or "/video/" in item["url"]:   # too old, or a video page we can't read
            continue
        if item["title"] and item["url"] and key not in seen and score(item) > 0:
            seen.add(key)
            items.append({**item, "score": score(item)})
    items.sort(key=lambda i: (i["score"], i["date"]), reverse=True)
    return items[:limit]


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    for i, item in enumerate(top_headlines(), 1):
        print(f"{i:>2}. [{item['score']}] {item['source']} {item['date']}  {item['title']}")
