"""Phase 3.1b: fetch ONE news article's full text, only when it's needed.

News pages are full of menus, ads and links. The article itself is almost
always in <p> paragraphs, so we keep paragraphs with real Chinese sentences
and drop the rest. Simple, and good enough for the four sites we use.

Results are cached in data/library/articles/<id>.txt (git-ignored) so each
article is downloaded at most once.

Run:  python tools/article.py <url>
"""
from __future__ import annotations

import hashlib
import re
import sys
import urllib.parse
import urllib.request
from html.parser import HTMLParser

from common import DATA, USER_AGENT, to_simplified

CACHE = DATA / "library" / "articles"
CHINESE = re.compile(r"[一-鿿]")
MIN_CHINESE_CHARS = 15  # shorter <p> are usually captions, bylines or buttons
SKIP_INSIDE = {"script", "style", "nav", "footer", "aside", "figcaption", "header"}
SENTENCE_END = ("。", "！", "？", "”", "）", "…", ".", "」", "：")
JUNK = ("不存在或不再可用", "版权所有", "版权声明", "未经授权", "订阅",
        "Instagram", "使用人工智能协助翻译")


class _Paragraphs(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.paragraphs: list[str] = []
        self._buf: list[str] | None = None
        self._link_chars = 0   # how much of the current <p> is inside <a> links
        self._in_link = 0
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in SKIP_INSIDE:
            self._skip += 1
        elif tag == "p" and not self._skip:
            self._buf, self._link_chars = [], 0
        elif tag == "a":
            self._in_link += 1

    def handle_endtag(self, tag):
        if tag in SKIP_INSIDE and self._skip:
            self._skip -= 1
        elif tag == "a" and self._in_link:
            self._in_link -= 1
        elif tag == "p" and self._buf is not None:
            text = re.sub(r"\s+", " ", "".join(self._buf)).strip()
            # Keep real paragraphs only:
            #  - enough Chinese, and ending like a sentence
            #  - mostly NOT link text ("link density"): headline teasers such as
            #    <p><a>大学生阴差阳错踢出神奇一幕，全场沸腾！</a></p> are all link
            #  - not a copyright footer
            if (len(CHINESE.findall(text)) >= MIN_CHINESE_CHARS
                    and text.endswith(SENTENCE_END)
                    and self._link_chars < 0.5 * len(text)
                    and not any(j in text for j in JUNK)):
                self.paragraphs.append(text.lstrip("　 "))
            self._buf = None

    def handle_data(self, data):
        if self._buf is not None and not self._skip:
            self._buf.append(data)
            if self._in_link:
                self._link_chars += len(data.strip())


def fetch_article(url: str) -> str:
    """Return the article's paragraphs (simplified Chinese), from cache if possible."""
    url = url.strip()  # stray \r or spaces would become %0D / %20 and break the link
    CACHE.mkdir(parents=True, exist_ok=True)
    cached = CACHE / f"{hashlib.sha1(url.encode()).hexdigest()[:12]}.txt"
    if cached.exists():
        return cached.read_text(encoding="utf-8")

    # URLs with Chinese characters (DW does this) must be percent-encoded.
    safe_url = urllib.parse.quote(url, safe=":/?&=%#~+,;@!$'()*[]")
    req = urllib.request.Request(safe_url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=20) as resp:
        raw = resp.read()
        charset = resp.headers.get_content_charset() or "utf-8"
    page = raw.decode(charset, errors="replace")

    parser = _Paragraphs()
    parser.feed(page)
    seen, paragraphs = set(), []
    for p in parser.paragraphs:  # some sites repeat the lead paragraph
        if p not in seen:
            seen.add(p)
            paragraphs.append(to_simplified(p))
    text = "\n\n".join(paragraphs)
    if text:
        cached.write_text(text, encoding="utf-8")
    return text


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    body = fetch_article(sys.argv[1])
    print(body[:800] + ("…" if len(body) > 800 else ""))
    print(f"\n[{len(body)} characters, {body.count(chr(10) * 2) + 1 if body else 0} paragraphs]")
