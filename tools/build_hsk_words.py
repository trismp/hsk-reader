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

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw" / "hsk_complete.json"
OUT = ROOT / "data" / "hsk_words.json"

SCHEMES = {"new": "hsk30", "newest": "hsk2026", "old": "hsk2"}


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    entries = json.loads(RAW.read_text(encoding="utf-8"))

    words: dict[str, dict] = {}
    for e in entries:
        word = e["simplified"]
        # Pick the most everyday reading (抢 qiǎng "grab", not literary qiāng).
        # min() keeps the first form when several are equally common.
        form = min(e["forms"], key=lambda f: rarity(f["meanings"]))
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
            "meanings": form["meanings"][:4],
            "levels": levels,
        }

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
