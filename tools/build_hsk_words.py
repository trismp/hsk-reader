"""Phase 1.1: turn the downloaded HSK list into our own small, simple file.

Input : data/raw/hsk_complete.json  (11,470 entries, lots of fields we don't need)
Output: data/hsk_words.json         (one entry per word: pinyin, meaning, levels)

Each word gets levels under three schemes, so we can switch later:
  "hsk30"   - HSK 3.0 standard (2021, GF0025-2021)  -> our default
  "hsk2026" - 2026 revision of HSK 3.0 levels
  "hsk2"    - old HSK 2.0 (levels 1-6)
Level 7 in HSK 3.0 means the combined "7-9" band; we store it as 7.

Source: github.com/drkameleon/complete-hsk-vocabulary (MIT licence).
"""
import json
import sys
from pathlib import Path

from common import rarity
from readings import read_chars, same

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw" / "hsk_complete.json"
OUT = ROOT / "data" / "hsk_words.json"

SCHEMES = {"new": "hsk30", "newest": "hsk2026", "old": "hsk2"}


TONE_MARKED = set("āáǎàēéěèīíǐìōóǒòūúǔùǖǘǚǜĀÁǍÀĒÉĚÈŌÓǑÒ")


def _neutral_ending(pinyin: str) -> bool:
    """True if a multi-syllable word ends in a neutral (unmarked) tone: 'dōng xi'."""
    syllables = pinyin.split()
    return len(syllables) > 1 and not (set(syllables[-1]) & TONE_MARKED)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    entries = json.loads(RAW.read_text(encoding="utf-8"))

    words: dict[str, dict] = {}
    for e in entries:
        word = e["simplified"]
        # Pick the most everyday reading. The source lists readings alphabetically,
        # so its order says nothing (看 kān comes before kàn). Instead:
        #  0. not a surname or place (藏 Zàng), then the reading pypinyin gives the word
        #  1. not a rare/literary one   (抢 qiǎng "grab", not literary qiāng)
        #  2. prefer a neutral-tone ending, which is usually the colloquial word:
        #     东西 dōng xi "thing" (not dōng xī "east and west"),
        #     妻子 qī zi "wife"    (not qī zǐ "wife and children")
        # sorted() is stable, so ties keep the source order.
        usual = " ".join(read_chars(word))
        forms = sorted(e["forms"], key=lambda f: (f["transcriptions"]["pinyin"][:1].isupper(),
                                                  not same(f["transcriptions"]["pinyin"], usual, loose=len(word) > 1),
                                                  rarity(f["meanings"]),
                                                  not _neutral_ending(f["transcriptions"]["pinyin"])))
        form = forms[0]
        other_meanings = [m for f in forms[1:] for m in f["meanings"]
                          if "variant of" not in m][:2]
        levels = {}
        for tag in e["level"]:
            prefix, num = tag.rsplit("-", 1)
            levels[SCHEMES[prefix]] = int(num)

        if word in words:
            # Same characters listed twice (e.g. two readings): keep the
            # easiest level per scheme and add the extra meanings.
            old = words[word]
            for scheme, lvl in levels.items():
                old["levels"][scheme] = min(lvl, old["levels"].get(scheme, 99))
            old["meanings"] += [m for m in form["meanings"] if m not in old["meanings"]]
            continue

        words[word] = {
            "pinyin": form["transcriptions"]["pinyin"],
            # main reading's meanings first, then the other reading's
            "meanings": form["meanings"][:4] + [m for m in other_meanings if m not in form["meanings"]],
            "levels": levels,
        }
        if len({f["transcriptions"]["pinyin"] for f in forms}) > 1:  # for tools/readings.py
            by_reading: dict[str, list[str]] = {}
            for f in forms:
                known = by_reading.setdefault(f["transcriptions"]["pinyin"], [])
                known += [m for m in f["meanings"] if m not in known and "variant of" not in m]
            words[word]["readings"] = [[p, m[:4]] for p, m in by_reading.items() if m]

    OUT.write_text(json.dumps(words, ensure_ascii=False, indent=0), encoding="utf-8")

    # Summary so we can sanity-check against the official counts.
    for scheme in ("hsk30", "hsk2026", "hsk2"):
        counts = {}
        for w in words.values():
            if scheme in w["levels"]:
                lvl = w["levels"][scheme]
                counts[lvl] = counts.get(lvl, 0) + 1
        print(f"{scheme:8}", dict(sorted(counts.items())), "total", sum(counts.values()))
    print(f"\nWrote {len(words)} unique words to {OUT.relative_to(ROOT)}")
    print("Example 嫦娥:", words.get("嫦娥", "not in HSK"))
    print("Example 月亮:", words["月亮"])


if __name__ == "__main__":
    main()
