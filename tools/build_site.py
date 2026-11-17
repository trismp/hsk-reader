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

Story file format:
    ---
    title: 嫦娥奔月
    level: 4                 # target HSK level (1-7, 7 = 7-9)
    category: folklore       # folklore | history | east_asia | culture | classics
    summary: The archer's wife who flew to the moon.
    sources: https://zh.wikipedia.org/wiki/嫦娥奔月
    names: 逄蒙                # proper nouns: shown, not counted as hard
    keywords: 射箭            # up to ~5 essential words, taught at the top
    ---
    很久以前，天上有十个太阳……

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
NAME = 0      # level code for names (shown, never counted as hard)
KEYWORD = -1  # level code for key words taught at the top of the story


def parse_story(path: Path) -> dict:
    raw = path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", raw, re.S)
    if not m:
        raise ValueError(f"{path.name}: missing --- header ---")
    meta = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.split("#")[0].strip() if k.strip() == "level" else v.strip()
    meta["level"] = int(meta["level"])
    for field in ("sources", "names", "keywords"):  # comma-separated lists
        meta[field] = [s.strip() for s in re.split(r"[,，、]", meta.get(field, "")) if s.strip()]
    meta["paragraphs"] = [p.strip() for p in m.group(2).split("\n\n") if p.strip()]
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


def token(word: str, target: int, names: set, keywords: set) -> list | str:
    """['嫦娥', 'Cháng é', 0, "Chang'e …"]  or plain '，' for punctuation.

    Level codes: 1-7 HSK level (7 = 7-9), 10 beyond HSK, 0 name, -1 key word.
    """
    if not HAS_CHINESE.search(word):
        return word
    if word in keywords:
        info = hsk.lookup_word(word)
        return [word, info["pinyin"] or "", KEYWORD, "; ".join(clean_meanings(info["meanings"]))]
    level, kind = hsk.classify(word)
    if word in names or kind == "name" or (level and level > target and hsk._is_name(word)):
        level = NAME
    info = hsk.lookup_word(word)
    meaning = "; ".join(clean_meanings(info["meanings"]))
    return [word, info["pinyin"] or "", level if level is not None else NAME, meaning]


def build() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    hsk._load()
    (OUT / "stories").mkdir(parents=True, exist_ok=True)
    for old in (OUT / "stories").glob("*.json"):
        old.unlink()

    library = []
    for path in sorted(STORIES.glob("*.md")):
        s = parse_story(path)
        full_text = "\n".join(s["paragraphs"])
        report = hsk.check_text(full_text, s["level"], names=s["names"], keywords=s["keywords"])
        names, keywords = set(s["names"]), set(s["keywords"])
        paragraphs = [[token(w, s["level"], names, keywords) for w in jieba.lcut(p)]
                      for p in s["paragraphs"]]

        story = {k: s[k] for k in ("id", "title", "level", "category", "summary", "sources")}
        story["keywords"] = [
            {"word": k, "pinyin": hsk.lookup_word(k)["pinyin"],
             "meanings": clean_meanings(hsk.lookup_word(k)["meanings"])}
            for k in s["keywords"]
        ]
        story |= {"estimated_level": report["estimated_level"],
                  "within_target": report["within_target"],
                  "characters": len(HAS_CHINESE.findall(full_text))}
        (OUT / "stories" / f"{s['id']}.json").write_text(
            json.dumps({**story, "paragraphs": paragraphs}, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8")
        library.append({k: v for k, v in story.items() if k != "keywords"})

        flag = "✓" if report["meets_target"] else "✗ TOO HARD"
        print(f"  {flag} HSK {s['level']}  {s['title']}  ({story['characters']} chars, "
              f"{report['within_target']:.0%} within level, est. {report['estimated_level']})")
        if not report["meets_target"]:
            print("      above target:", "、".join(h["word"] for h in report["above_target"]))

    library.sort(key=lambda s: (s["level"], s["title"]))
    (OUT / "library.json").write_text(json.dumps(library, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n{len(library)} stories -> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    build()
