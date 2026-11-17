"""Small helpers shared by the build scripts and the checker."""

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
