"""Phase 4: turn story files into data the website can show. Pre-processing.

WHY PRE-PROCESS?
  The browser could split Chinese into words itself, but it would need jieba
  and a 13 MB dictionary on every visitor's phone. Instead we do the heavy
  work ONCE here and ship small, ready-to-display JSON:
    - text already split into words
    - each word's pinyin, short meaning and HSK level attached
  So the site stays tiny and fast, and needs no server.

Input : content/stories/*.md   (a small header + the story paragraphs)
Output: site/data/library.json        list of all stories (for the library page)
        site/data/stories/<id>.json    one file per story (for the reader page)

Story file format (one file per story; chapters start with "## "):
    ---
    title: 玄奘西行
    title_en: The Monk Who Walked West
    genre: history           # see GENRES below
    blurb_en: One short, simple English sentence.
    blurb_zh: 一句简单的中文简介。
    sources: https://zh.wikipedia.org/wiki/玄奘
    names: 玄奘, 长安          # proper nouns: shown in blue, never counted as hard
    keywords: 取经, 佛经        # up to ~6 essential words, taught at the top
    ---
    ## 第一章 偷偷出关
    贞观元年……

    ## 第二章 ……
    A story with no "## " headings is a one-shot.

Run:  python tools/build_site.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import jieba

import hsk_checker as hsk
from common import ROOT

STORIES = ROOT / "content" / "stories"
OUT = ROOT / "site" / "data"
HAS_CHINESE = re.compile(r"[一-鿿]")
READER_LEVEL = 5   # readers have finished HSK 5; anything above it is a "new" word
CHECK_LEVEL = 6    # stories are written for HSK 6 learners (words beyond are allowed)

# Discover tiles, in display order. "soon": shown as "coming soon", no stories yet.
GENRES = [
    {"id": "myths",   "zh": "神话传说", "en": "Myths & Legends", "mark": "神",
     "tagline": "Gods, monsters and heroes from ancient China and East Asia"},
    {"id": "history", "zh": "历史风云", "en": "History", "mark": "史",
     "tagline": "Real people who changed China, told as stories"},
    {"id": "mystery", "zh": "悬疑", "en": "Mystery", "mark": "谜",
     "tagline": "Crimes, clues and a detective to follow"},
    {"id": "work",    "zh": "职场人生", "en": "Life & Work in China", "mark": "职",
     "tagline": "Offices, colleagues and everyday city life"},
    {"id": "romance", "zh": "言情", "en": "Romance", "mark": "情",
     "tagline": "Love stories, light and serialised", "soon": True},
    {"id": "news",    "zh": "时事财经", "en": "News & Markets", "mark": "财",
     "tagline": "This week's news and market moves, made readable", "soon": True},
]
GENRE_IDS = {g["id"] for g in GENRES}

# Hand-written meanings where the dictionary's split-up guess is misleading.
MEANING_FIXES = {
    "白白胖胖": "fair and chubby (of a baby)",
}

NAME = 0      # level code for names (shown, never counted as hard)
KEYWORD = -1  # level code for key words taught at the top of the story


def parse_story(path: Path) -> dict:
    raw = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", raw, re.S)
    if not m:
        raise ValueError(f"{path.name}: missing --- header ---")
    meta = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    if meta.get("genre") not in GENRE_IDS:
        raise ValueError(f"{path.name}: unknown genre {meta.get('genre')!r}")
    for field in ("sources", "names", "keywords"):  # comma-separated lists
        meta[field] = [x.strip() for x in re.split(r"[,，、]", meta.get(field, "")) if x.strip()]
    # A key word may carry its own gloss when the dictionary has none or a poor one:
    #   烽火台|fēng huǒ tái|beacon tower
    meta["glosses"] = {}
    for i, kw in enumerate(meta["keywords"]):
        if "|" in kw:
            word, pinyin, meaning = (kw.split("|") + ["", ""])[:3]
            meta["keywords"][i] = word.strip()
            meta["glosses"][word.strip()] = (pinyin.strip(), meaning.strip())

    # Split the body into chapters at "## " headings (none = a one-shot).
    chapters, current = [], {"title": "", "paragraphs": []}
    for block in re.split(r"\n\s*\n", m.group(2)):
        block = block.strip()
        if not block:
            continue
        if block.startswith("## "):
            if current["paragraphs"]:
                chapters.append(current)
            current = {"title": block[3:].strip(), "paragraphs": []}
        else:
            current["paragraphs"].append(block)
    chapters.append(current)
    meta["chapters"] = chapters
    meta["id"] = path.stem
    return meta


def clean_meanings(meanings: list[str], n: int = 2) -> list[str]:
    """Make CC-CEDICT meanings learner-friendly.

    'cane; CL:把[ba3]'                -> 'cane'          (measure-word codes)
    '(derived from 綠帽子|绿帽子[lu:4 mao4 zi5])' -> '(derived from 绿帽子)'
    """
    out = []
    for m in meanings:
        m = re.sub(r"CL:\S*", "", m)                 # classifier notes
        m = re.sub(r"\S+\|(\S+?)(?=\[|\s|\)|$)", r"\1", m)  # trad|simp -> simp
        m = re.sub(r"\[[^\]]*\]", "", m)              # numbered pinyin refs
        m = re.sub(r"\s{2,}", " ", m).strip(" ;,")
        if m and m not in out:
            out.append(m)
    return out[:n]


def keyword_gloss(word: str, keywords: dict) -> tuple[str, list[str]]:
    """(pinyin, meanings) for a key word: the story's own gloss if given, else the dictionary."""
    if keywords.get(word):
        pinyin, meaning = keywords[word]
        return pinyin, [meaning]
    info = hsk.lookup_word(word)
    return info["pinyin"] or "", clean_meanings(info["meanings"])


def token(word: str, target: int, names: set, keywords: dict) -> list | str:
    """['嫦娥', 'Cháng é', 0, "Chang'e …"]  or plain '，' for punctuation.

    Level codes: 1-7 HSK level (7 = 7-9), 10 beyond HSK, 0 name, -1 key word.
    """
    if not HAS_CHINESE.search(word):
        return word
    if word in keywords:
        pinyin, meanings = keyword_gloss(word, keywords)
        return [word, pinyin, KEYWORD, "; ".join(meanings)]
    level, kind = hsk.classify(word)
    if word in names or kind == "name" or (level and level > target and hsk._is_name(word)):
        level = NAME
    info = hsk.lookup_word(word)
    meanings = info["meanings"]
    # 平平淡淡 (AABB) means the same as 平淡 (AB), only stronger
    if len(word) == 4 and word[0] == word[1] and word[2] == word[3]:
        base = hsk.lookup_word(word[0] + word[2])
        if base["meanings"] and not any(" + " in m for m in base["meanings"]):
            meanings = base["meanings"]
    meaning = MEANING_FIXES.get(word) or "; ".join(resolve_see(clean_meanings(meanings)))
    return [word, info["pinyin"] or "", level if level is not None else NAME, meaning]


def resolve_see(meanings: list[str]) -> list[str]:
    """CC-CEDICT sometimes says 'see 白茅'. Drop those, or follow them if nothing else is left."""
    real = [m for m in meanings if not m.startswith("see ")]
    if real:
        return real
    if meanings:
        target = meanings[0][4:].strip()
        info = hsk.lookup_word(target)
        better = [m for m in clean_meanings(info["meanings"]) if not m.startswith("see ")]
        if better:
            return better
    return meanings


def build() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    hsk._load()
    (OUT / "stories").mkdir(parents=True, exist_ok=True)
    for old in (OUT / "stories").glob("*.json"):
        old.unlink()

    stories = []
    for path in sorted(STORIES.glob("*.md")):
        s = parse_story(path)
        names = set(s["names"])
        keywords = {k: s["glosses"].get(k) for k in s["keywords"]}  # word -> own gloss or None
        full_text = "\n".join(p for ch in s["chapters"] for p in ch["paragraphs"])
        report = hsk.check_text(full_text, CHECK_LEVEL, names=s["names"], keywords=s["keywords"])

        chapters = []
        for ch in s["chapters"]:
            chapters.append({
                "title": ch["title"],
                "title_tokens": [token(w, CHECK_LEVEL, names, keywords) for w in jieba.lcut(ch["title"])],
                "characters": len(HAS_CHINESE.findall("".join(ch["paragraphs"]))),
                "paragraphs": [[token(w, CHECK_LEVEL, names, keywords) for w in jieba.lcut(p)]
                               for p in ch["paragraphs"]],
            })

        story = {k: s.get(k, "") for k in ("id", "title", "title_en", "genre", "blurb_en", "blurb_zh")}
        story["sources"] = s["sources"]
        story["title_tokens"] = [token(w, CHECK_LEVEL, names, keywords) for w in jieba.lcut(s["title"])]
        story["keywords"] = [
            dict(zip(("word", "pinyin", "meanings"), (k, *keyword_gloss(k, keywords))))
            for k in s["keywords"]
        ]
        story |= {"characters": len(HAS_CHINESE.findall(full_text)),
                  "chapter_count": len(chapters),
                  "within_hsk6": report["within_target"]}
        (OUT / "stories" / f"{s['id']}.json").write_text(
            json.dumps({**story, "chapters": chapters}, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8")
        stories.append({**{k: v for k, v in story.items() if k not in ("keywords", "sources", "title_tokens")},
                        "chapter_titles": [c["title"] for c in chapters]})

        short = [c["characters"] for c in chapters if c["characters"] < 700]
        flag = "✓" if not short else f"✗ SHORT CHAPTER {short}"
        print(f"  {flag} [{s['genre']}] {s['title']}  {len(chapters)} ch, {story['characters']} chars, "
              f"{report['within_target']:.0%} within HSK 6, est. {report['estimated_level']}")
        hard = [h["word"] for h in report["above_target"]]
        if hard:
            print("      above HSK 6:", "、".join(hard[:40]))

    library = {"genres": GENRES, "stories": stories}
    (OUT / "library.json").write_text(json.dumps(library, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n{len(stories)} stories -> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    build()
