"""Phase 1.3: the HSK level checker. Plain code, no AI.

Given Chinese text, it answers:
  * What share of the words are at or below each HSK level?
  * Which words are above my target level?
  * Roughly what HSK level is this text?

How it works:
  1. jieba splits the text into words (helped by our 110k-word list).
  2. Each word is looked up in the HSK list.
  3. Words not in HSK are handled in order:
       - names of people/places (capitalised pinyin in CC-CEDICT) -> "name",
         not counted as difficult (you can't "learn" 嫦娥 from a word list)
       - compounds made of HSK words (飞到 = 飞 + 到) -> level of the hardest part
       - anything else -> "beyond HSK"
  4. Counts are per word occurrence, so a word used 5 times counts 5 times.

Use from the command line:
    python tools/hsk_checker.py --target 4 "嫦娥吃了不死药以后，飞到了月亮上。"
    python tools/hsk_checker.py --target 5 --file some_article.txt
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from functools import lru_cache
from pathlib import Path

import jieba

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

BEYOND = 10                 # level number used for words outside HSK
LEVEL_NAMES = {7: "7-9", BEYOND: "beyond HSK"}
HAS_CHINESE = re.compile(r"[一-鿿]")
COMFORTABLE = 0.95          # a text "is" level L when 95% of words are <= L


def level_name(level: int) -> str:
    return LEVEL_NAMES.get(level, str(level))


@lru_cache(maxsize=1)
def _load() -> tuple[dict, dict]:
    """Load the data files once and teach jieba our word list."""
    jieba.setLogLevel("WARNING")
    jieba.load_userdict(str(DATA / "jieba_words.txt"))
    hsk = json.loads((DATA / "hsk_words.json").read_text(encoding="utf-8"))
    dictionary = json.loads((DATA / "dictionary.json").read_text(encoding="utf-8"))
    return hsk, dictionary


def lookup_word(word: str, scheme: str = "hsk30") -> dict:
    """Pinyin, meanings and HSK level of one word (HSK data preferred)."""
    hsk, dictionary = _load()
    if word in hsk:
        e = hsk[word]
        level = e["levels"].get(scheme)
        return {"word": word, "pinyin": e["pinyin"], "meanings": e["meanings"],
                "level": level_name(level) if level else "beyond HSK", "source": "hsk"}
    parts = _split_into_hsk(word, hsk, scheme)
    if word in dictionary:
        e = dictionary[word]
        # Same rule as the checker: a word built from HSK words gets the
        # level of its hardest part (不死药 = 不 + 死 + 药).
        level = level_name(max(hsk[p]["levels"][scheme] for p in parts)) if parts else "beyond HSK"
        return {"word": word, "pinyin": e["pinyin"], "meanings": e["meanings"],
                "level": level, "source": "cc-cedict"}
    if parts:  # e.g. 射下 -> 射 + 下: build pinyin and meaning from the parts
        return {"word": word,
                "pinyin": " ".join(hsk[p]["pinyin"] for p in parts),
                "meanings": [" + ".join(f"{p} ({hsk[p]['meanings'][0]})" for p in parts)],
                "level": level_name(max(hsk[p]["levels"][scheme] for p in parts)),
                "source": "compound"}
    return {"word": word, "pinyin": None, "meanings": [], "level": "unknown", "source": None}


def vocab_for_level(level: int, scheme: str = "hsk30", exclusive: bool = True) -> list[dict]:
    """Words at a level (exclusive) or at and below it (inclusive), most frequent first."""
    hsk, _ = _load()
    out = []
    for word, e in hsk.items():
        lvl = e["levels"].get(scheme)
        if lvl is None or (lvl != level if exclusive else lvl > level):
            continue
        out.append({"word": word, "pinyin": e["pinyin"], "meaning": e["meanings"][0],
                    "level": level_name(lvl)})
    return out


def _split_into_hsk(word: str, hsk: dict, scheme: str) -> list[str] | None:
    """Greedy longest-match split into HSK words, e.g. 飞到 -> [飞, 到]."""
    parts, i = [], 0
    while i < len(word):
        for j in range(len(word), i, -1):
            piece = word[i:j]
            if piece in hsk and scheme in hsk[piece]["levels"]:
                parts.append(piece)
                i = j
                break
        else:
            return None
    return parts if len(parts) > 1 else None


def _is_name(word: str) -> bool:
    """CC-CEDICT writes pinyin of names with a capital letter: 嫦娥 = Cháng é."""
    _, dictionary = _load()
    info = dictionary.get(word)
    return bool(info and info["pinyin"][:1].isupper() and not word.startswith("星期"))  # 星期一 is no name


def classify(word: str, scheme: str = "hsk30") -> tuple[int | None, str]:
    """Return (level, kind). level None means 'don't count this word'."""
    hsk, dictionary = _load()
    entry = hsk.get(word)
    if entry and scheme in entry["levels"]:
        return entry["levels"][scheme], "hsk"
    # The official list writes some words with 儿 (玩儿, 一点儿): 玩 = 玩儿.
    erhua = hsk.get(word + "儿")
    if erhua and scheme in erhua["levels"]:
        return erhua["levels"][scheme], "hsk"
    if _is_name(word):
        return None, "name"
    parts = _split_into_hsk(word, hsk, scheme)
    if parts:
        return max(hsk[p]["levels"][scheme] for p in parts), "compound"
    return BEYOND, "beyond"


def check_text(text: str, target_level: int = 4, scheme: str = "hsk30",
               names: list[str] | tuple = (), keywords: list[str] | tuple = ()) -> dict:
    """Analyse a Chinese text against a target HSK level. Returns plain data.

    names:    proper nouns to ignore (逄蒙, 邓林), like a graded reader would.
    keywords: a few essential story words taught up front (粽子 in a 端午 story);
              not counted as difficult, but listed in the result.
    """
    _load()
    extra = set(names) | set(keywords)
    for w in extra:  # make sure jieba keeps these words whole
        # Only multi-character words: boosting a single character like 射
        # would make jieba split longer words that contain it (射箭 -> 射|箭).
        if len(w) > 1:
            jieba.add_word(w, freq=50000)
    words = [w for w in jieba.lcut(text) if HAS_CHINESE.search(w)]

    counted: list[int] = []
    names_found: set[str] = set()
    hard: dict[str, dict] = {}
    for w in words:
        if w in extra:
            names_found.add(w)
            continue
        level, kind = classify(w, scheme)
        # A name above the target (嫦娥 is officially HSK 7-9) is explained,
        # not learned, so don't let it count against the text.
        if level is not None and level > target_level and _is_name(w):
            level = None
        if level is None:
            names_found.add(w)
            continue
        counted.append(level)
        if level > target_level:
            item = hard.setdefault(w, {**lookup_word(w, scheme), "kind": kind, "count": 0})
            item["level"] = level_name(level)
            item["count"] += 1

    total = len(counted)
    levels = [1, 2, 3, 4, 5, 6, 7, BEYOND]
    coverage = {
        level_name(L): round(sum(1 for x in counted if x <= L) / total, 3) if total else 1.0
        for L in levels
    }
    estimated = next(
        (level_name(L) for L in levels if total and sum(1 for x in counted if x <= L) / total >= COMFORTABLE),
        "beyond HSK",
    )
    within = coverage[level_name(target_level)]

    return {
        "scheme": scheme,
        "target_level": level_name(target_level),
        "words_counted": total,
        "within_target": within,
        "meets_target": within >= COMFORTABLE,
        "estimated_level": estimated,
        "coverage_by_level": coverage,
        "above_target": sorted(hard.values(), key=lambda h: (-h["count"], h["word"])),
        "names_ignored": sorted(names_found - set(keywords)),
        "keywords": [k for k in keywords if k in names_found],
    }


def _print_report(r: dict) -> None:
    status = "OK" if r["meets_target"] else "TOO HARD"
    print(f"Target HSK {r['target_level']} ({r['scheme']})  ->  {status}")
    print(f"  {r['within_target']:.0%} of {r['words_counted']} words are at or below the target "
          f"(goal: {COMFORTABLE:.0%})")
    print(f"  Estimated level of this text: HSK {r['estimated_level']}")
    print("  Coverage: " + "  ".join(f"≤{k}: {v:.0%}" for k, v in r["coverage_by_level"].items()))
    if r["above_target"]:
        print("  Words above target:")
        for h in r["above_target"]:
            meaning = h["meanings"][0] if h["meanings"] else "?"
            extra = " (compound)" if h["kind"] == "compound" else ""
            print(f"    {h['word']:<6} {h['pinyin'] or '':<14} HSK {h['level']:<10} "
                  f"x{h['count']}  {meaning}{extra}")
    if r["names_ignored"]:
        print("  Names (not counted): " + "、".join(r["names_ignored"]))


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description="Check a Chinese text's HSK level.")
    p.add_argument("text", nargs="?", help="Chinese text (or use --file)")
    p.add_argument("--file", help="read text from a UTF-8 file")
    p.add_argument("--target", type=int, default=4, help="target HSK level, 1-7 (7 = 7-9)")
    p.add_argument("--scheme", default="hsk30", choices=["hsk30", "hsk2026", "hsk2"])
    p.add_argument("--json", action="store_true", help="print raw JSON instead of a report")
    a = p.parse_args()

    text = Path(a.file).read_text(encoding="utf-8") if a.file else a.text
    if not text:
        p.error("give some text or --file")
    result = check_text(text, a.target, a.scheme)
    if a.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        _print_report(result)


if __name__ == "__main__":
    main()
