"""Collect today's Chinese tech headlines from RSS feeds and rank them by topic.

This part is plain code, no AI: download each feed, pull out title / link / date /
summary, drop duplicates, and score each headline by the companies and topics we
care about. The harness (news.py) then hands the best ones to Claude.

Try it on its own:  .venv/Scripts/python.exe harness/news_feeds.py
"""
import html, re, sys, urllib.request, xml.etree.ElementTree as ET
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

# Feeds tested on 2026-11-24. (36氪, 机器之心 and 品玩 had no usable feed.)
FEEDS = {
    "IT之家": "https://www.ithome.com/rss/",                       # fast company & product news
    "界面新闻": "https://a.jiemian.com/index.php?m=article&a=rss&cid=65",  # business & tech companies
    "钛媒体": "https://www.tmtpost.com/rss.xml",                   # tech industry
    "量子位": "https://www.qbitai.com/feed",                        # AI models & AI companies
    "InfoQ": "https://www.infoq.cn/feed",                          # enterprise AI, AI agents
    "人人都是产品经理": "https://www.woshipm.com/feed",               # product management
    "极客公园": "https://www.geekpark.net/rss",                     # innovation, founders
    "雷峰网": "https://www.leiphone.com/feed",                      # AI, robots, cars
    "虎嗅": "https://rss.huxiu.com/",                              # business analysis
    "少数派": "https://sspai.com/feed",                             # apps, software, gadgets
}

# Our article reader finds no text on these sites' pages, but their feed carries the whole article.
# So we keep the feed's text, and news.py gives Claude that instead of opening the page.
FULL_TEXT_FEEDS = {"虎嗅"}
MIN_FULL_TEXT = 500   # shorter than this is a video or a teaser, not an article: skip it

MAX_PER_SOURCE = 6   # IT之家 alone can fill more than half the list otherwise

# How each outlet's name is shown on the website (pinyin + English), used in the digest's credit line.
SOURCE_GLOSSES = {
    "IT之家": {"name": "IT之家", "pinyin": "IT zhī jiā", "meaning": "IT Home (a Chinese tech news site)"},
    "界面新闻": {"name": "界面新闻", "pinyin": "jiè miàn xīn wén", "meaning": "Jiemian News (a Chinese business news site)"},
    "钛媒体": {"name": "钛媒体", "pinyin": "tài méi tǐ", "meaning": "TMTPost (a Chinese tech media site)"},
    "量子位": {"name": "量子位", "pinyin": "liàng zǐ wèi", "meaning": "QbitAI (a Chinese AI news site)"},
    "InfoQ": {"name": "InfoQ", "pinyin": "", "meaning": "InfoQ (a tech news site for developers)"},
    "人人都是产品经理": {"name": "人人都是产品经理", "pinyin": "rén rén dōu shì chǎn pǐn jīng lǐ",
                    "meaning": "Woshipm (a Chinese product-management community)"},
    "极客公园": {"name": "极客公园", "pinyin": "jí kè gōng yuán", "meaning": "GeekPark (a Chinese tech media site)"},
    "雷峰网": {"name": "雷峰网", "pinyin": "léi fēng wǎng", "meaning": "Leiphone (a Chinese AI and tech news site)"},
    "虎嗅": {"name": "虎嗅", "pinyin": "hǔ xiù", "meaning": "Huxiu (a Chinese business news site)"},
    "少数派": {"name": "少数派", "pinyin": "shǎo shù pài", "meaning": "Sspai (a Chinese site about apps and gadgets)"},
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


def _plain(raw_html: str, limit: int | None = None) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", "", raw_html))
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
        body = _text(el, "description", "summary") or _text(el, "encoded")
        item = {"source": source, "title": _text(el, "title"), "url": _text(el, "link"), "date": date,
                "summary": _plain(body, 300)}
        if source in FULL_TEXT_FEEDS:
            item["url"] = item["url"].removesuffix("?f=rss")   # clean link for the credit line
            item["text"] = _plain(_text(el, "description"))    # the whole article, not just the summary
            if len(item["text"]) < MIN_FULL_TEXT:
                continue
        items.append(item)
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
        if item["date"] < since or "限时特惠" in item["title"]:   # too old, or an ad
            continue
        if "/video/" in item["url"] or "/prime/" in item["url"]:   # a video or paid page we can't read
            continue
        if item["title"] and item["url"] and key not in seen and score(item) > 0:
            seen.add(key)
            items.append({**item, "score": score(item)})
    items.sort(key=lambda i: (i["score"], i["date"]), reverse=True)
    # At most MAX_PER_SOURCE each, so one busy outlet can't crowd out the others.
    kept, per_source = [], Counter()
    for item in items:
        if per_source[item["source"]] < MAX_PER_SOURCE:
            per_source[item["source"]] += 1
            kept.append(item)
    return kept[:limit]


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    for i, item in enumerate(top_headlines(), 1):
        print(f"{i:>2}. [{item['score']}] {item['source']} {item['date']}  {item['title']}")
