"""Small helpers shared by the build scripts, fetchers and the checker."""
import json
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

# Identify ourselves politely to websites and APIs (no personal details).
USER_AGENT = "hsk-reader-learning-project/0.1 (personal Chinese-reading study tool)"


@lru_cache(maxsize=1)
def _t2s() -> dict[str, str]:
    return json.loads((DATA / "trad_to_simp.json").read_text(encoding="utf-8"))


def to_simplified(text: str) -> str:
    """Traditional -> simplified, character by character (訪華 -> 访华)."""
    table = _t2s()
    text = text.replace("「", "“").replace("」", "”").replace("『", "‘").replace("』", "’")
    text = "".join(table.get(ch, ch) for ch in text)
    # Character-by-character conversion can't know that 著 stays 著 in some
    # words (著作 "writings") but becomes 着 in others (看著 -> 看着). Fix the
    # common words afterwards.
    for wrong, right in WORD_FIXES.items():
        text = text.replace(wrong, right)
    return text


WORD_FIXES = {
    "着作": "著作", "着名": "著名", "显着": "显著", "着称": "著称",
    "原着": "原著", "名着": "名著", "专着": "专著", "论着": "论著", "所着": "所著",
    "编着": "编著", "合着": "合著",
}

# Meanings that signal a rare or cross-reference reading of a character,
# e.g. 抢 qiāng "(literary) to knock against" vs the normal qiǎng "to grab".
RARE_MARKERS = ("variant of", "used in", "(literary)", "(archaic)", "(old)",
                "surname ", "(classical)", "(dialect)")


def rarity(meanings: list[str]) -> float:
    """0 = an everyday reading, 1 = every meaning is rare or a cross-reference."""
    if not meanings:
        return 1.0
    rare = sum(1 for m in meanings if any(k in m for k in RARE_MARKERS))
    return rare / len(meanings)
