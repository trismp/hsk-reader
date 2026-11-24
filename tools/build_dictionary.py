"""Phase 1.2: turn CC-CEDICT into data/dictionary.json, plus a jieba word list.

CC-CEDICT line format:
    傳統 传统 [chuan2 tong3] /tradition/traditional/
    traditional simplified [numbered pinyin] /meaning/meaning/

Outputs:
  data/dictionary.json   simplified word -> {"pinyin": "chuán tǒng", "meanings": [...]}
  data/jieba_words.txt   every dictionary + HSK word, so jieba keeps words
                         like 不死药 together instead of splitting them.

Licence: CC-CEDICT is CC BY-SA 4.0 (credit MDBG / CC-CEDICT on the website).
"""
import json
import re
import sys
from pathlib import Path

import jieba

from common import rarity

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw" / "cedict_ts.u8"
HSK = ROOT / "data" / "hsk_words.json"
OUT_DICT = ROOT / "data" / "dictionary.json"
OUT_JIEBA = ROOT / "data" / "jieba_words.txt"
OUT_T2S = ROOT / "data" / "trad_to_simp.json"

LINE = re.compile(r"^(\S+) (\S+) \[([^\]]*)\] /(.*)/$")
TONE_MARKS = {
    "a": "āáǎà", "e": "ēéěè", "i": "īíǐì",
    "o": "ōóǒò", "u": "ūúǔù", "ü": "ǖǘǚǜ",
}


def syllable_to_marks(syl: str) -> str:
    """'zhong1' -> 'zhōng', 'lu:4' -> 'lǜ', 'ma5' -> 'ma'."""
    syl = syl.replace("u:", "ü").replace("v", "ü")
    if not syl or not syl[-1].isdigit():
        return syl
    tone, body = int(syl[-1]), syl[:-1]
    if tone in (0, 5):
        return body
    lower = body.lower()
    # Standard rule: a or e takes the mark; in "ou" the o does; else the last vowel.
    if "a" in lower:
        pos = lower.index("a")
    elif "e" in lower:
        pos = lower.index("e")
    elif "ou" in lower:
        pos = lower.index("o")
    else:
        pos = max(i for i, ch in enumerate(lower) if ch in "aeiouü") if any(
            ch in "aeiouü" for ch in lower) else -1
    if pos < 0:
        return body  # e.g. "r5" or "m2": no vowel to mark
    marked = TONE_MARKS[lower[pos]][tone - 1]
    if body[pos].isupper():
        marked = marked.upper()
    return body[:pos] + marked + body[pos + 1:]


def to_tone_marks(pinyin: str) -> str:
    return " ".join(syllable_to_marks(s) for s in pinyin.split())


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    # A word can have several lines (several readings). Collect them all,
    # then put the most everyday reading first (绿 lǜ "green", not lù).
    readings: dict[str, list[tuple[str, list[str]]]] = {}
    t2s_candidates: dict[str, list[str]] = {}
    for line in RAW.read_text(encoding="utf-8").splitlines():
        if line.startswith("#"):
            continue
        m = LINE.match(line)
        if not m:
            continue
        trad, simp, pinyin, meanings = m.groups()
        # Single characters give us a traditional -> simplified table (訪 -> 访).
        # One traditional character can have several candidates (麼 -> 么 or 幺),
        # so collect them all and choose below.
        if len(trad) == 1 and trad != simp and "variant of" not in meanings:
            t2s_candidates.setdefault(trad, []).append(simp)
        meanings = [x for x in meanings.split("/") if x]
        readings.setdefault(simp, []).append((to_tone_marks(pinyin), meanings))

    # Pick the everyday simplified form: one that appears in HSK vocabulary
    # (么 is in 什么; 幺 is not), else one in the main CJK block (宁, not 㝉).
    hsk_chars = set("".join(json.loads(HSK.read_text(encoding="utf-8"))))

    def best(cands: list[str]) -> str:
        return min(cands, key=lambda c: (c not in hsk_chars, not "一" <= c <= "鿿"))

    trad_to_simp = {t: best(c) for t, c in t2s_candidates.items()}

    dictionary: dict[str, dict] = {}
    for simp, options in readings.items():
        options.sort(key=lambda o: rarity(o[1]))  # stable: ties keep file order
        pinyin = options[0][0]
        merged: list[str] = []
        for _, meanings in options:
            for x in meanings:
                if x not in merged and not x.startswith("variant of"):
                    merged.append(x)
        if merged:
            dictionary[simp] = {"pinyin": pinyin, "meanings": merged[:5]}
            if len({p for p, _ in options}) > 1:  # for tools/readings.py
                by_reading: dict[str, list[str]] = {}
                for p, ms in options:
                    seen = by_reading.setdefault(p, [])
                    seen += [x for x in ms if x not in seen and not x.startswith("variant of")]
                dictionary[simp]["readings"] = [[p, ms[:4]] for p, ms in by_reading.items() if ms]

    OUT_DICT.write_text(json.dumps(dictionary, ensure_ascii=False, indent=0), encoding="utf-8")
    OUT_T2S.write_text(json.dumps(trad_to_simp, ensure_ascii=False), encoding="utf-8")
    print(f"Trad->simp: {len(trad_to_simp)} characters -> {OUT_T2S.relative_to(ROOT)}")

    # jieba user dictionary: one word per line ("word frequency").
    # Only add words jieba does NOT already know. Re-adding a known word like
    # 以后 with our small frequency would overwrite jieba's real statistics
    # and make it split common words apart (以后 -> 以 | 后).
    jieba.setLogLevel("WARNING")
    jieba.dt.initialize()
    known = jieba.dt.FREQ
    hsk_words = json.loads(HSK.read_text(encoding="utf-8"))
    multi_char = sorted(
        w for w in set(dictionary) | set(hsk_words)
        if len(w) > 1 and not known.get(w)
    )
    OUT_JIEBA.write_text("".join(f"{w} 5\n" for w in multi_char), encoding="utf-8")

    print(f"Dictionary: {len(dictionary)} words -> {OUT_DICT.relative_to(ROOT)}")
    print(f"jieba list: {len(multi_char)} words -> {OUT_JIEBA.relative_to(ROOT)}")
    for w in ("不死药", "嫦娥", "绿", "中国"):
        print(f"  {w}: {dictionary.get(w)}")


if __name__ == "__main__":
    main()
