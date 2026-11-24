"""Choose how a word is read (and so what it means) from the sentence around it.

Many characters have several readings: 看 is kàn "to look" or kān "to look after".
A single stored default is wrong somewhere, so at build time:
  1. jieba splits the sentence; words no dictionary has (划着) are split again
     into words that are in the HSK list (划 + 着).
  2. pypinyin reads the whole sentence, and jieba's part of speech settles the
     little grammar words (着 zhe, 地 de, 得 de).
  3. We keep the dictionary entry whose reading matches. If none does, the
     stored default stays.
"""
from __future__ import annotations

import unicodedata

import jieba
import jieba.posseg
from pypinyin import Style, pinyin

TONES = {"̄", "́", "̌", "̀"}  # macron, acute, caron, grave
PARTICLES = {("着", "uz"): "zhe", ("了", "ul"): "le", ("过", "ug"): "guo",
             ("地", "uv"): "de", ("的", "uj"): "de"}
# Read this way whenever they stand alone as a word
ALONE = {"都": "dōu", "没": "méi", "觉": "jiào", "缝": "féng", "划": "huá", "还": "hái", "数": "shǔ", "场": "chǎng",
         "长": "cháng", "倒": "dǎo", "得": "de", "切": "qiē",
         "背": "bēi", "种": "zhǒng"}
# ...unless this comes right after it: 长出来, 倒茶
BEFORE = {"长": ("出大得成不满高", "zhǎng"), "倒": ("茶水酒", "dào"),
          "背": ("对后上", "bèi"), "种": ("好地了下", "zhòng"), "还": ("伞书钱。，", "huán")}
NOT_VERBS = {"没", "刚", "只", "都", "又", "也", "再", "已"}  # 没过两天: here 过 is the verb, guò


def same(a: str, b: str, loose: bool = False) -> bool:
    """Same reading? loose: a missing (neutral) tone matches any tone, dōng xi = dōng xī."""
    xs, ys = (unicodedata.normalize("NFD", s.lower()).split() for s in (a, b))
    if len(xs) != len(ys):
        return False
    for x, y in zip(xs, ys):
        if x == y:
            continue
        bx, by = ("".join(c for c in s if c not in TONES) for s in (x, y))
        if not (loose and bx == by and (x == bx or y == by)):
            return False
    return True


def read_words(tokens: list[str]) -> list[str]:
    """Like read_chars, but phrases never cross word borders (都|会 is not 都会 "city")."""
    return [r for w in tokens for r in read_chars(w)]


def read_chars(text: str) -> list[str]:
    """One reading per character of text (the character itself if it isn't Chinese)."""
    out = [p[0] for p in pinyin(text, style=Style.TONE, errors=lambda s: [[c] for c in s])]
    return out if len(out) == len(text) else [""] * len(text)


def pick(options: list, default: str, heard: str, is_name: bool = False):
    """The [pinyin, meanings] option that matches what we heard in the sentence, or None."""
    if not heard.strip():
        return None
    if " " in default and same(default, heard, loose=True):
        heard = default  # keep the everyday neutral tone: gù shi, not gù shì
    if is_name:
        return next((o for o in options if o[0][:1].isupper()), None)
    everyday = [o for o in options if not o[0][:1].isupper()]
    for loose in (False, True):
        for o in everyday:
            if same(o[0], heard, loose):
                return o
    return None


def words(text: str, is_known, split) -> list[tuple[str, str]]:
    """[(word, reading heard in this sentence)] for a piece of text."""
    pieces = jieba.lcut(text)
    heard = read_words(pieces)
    flags, pos = {}, 0
    for p in jieba.posseg.lcut(text):
        flags[pos, p.word] = p.flag
        pos += len(p.word)
    out, pos = [], 0
    for word in pieces:
        parts = [word] if is_known(word) else (split(word) or [word])
        for k, part in enumerate(parts):
            said = " ".join(heard[pos:pos + len(part)])
            flag = flags.get((pos, part))
            before = out[-1][0] if out else ""
            after_verb = before not in NOT_VERBS and (
                k or flags.get((pos - len(before), before), "").startswith("v"))
            if part == "过" and not after_verb:
                said = "guò"     # 过了一会儿, 没过两天
            elif part == "地" and len(before) > 1 and before[-1] == before[-2]:
                said = "de"      # 慢慢地, 整整齐齐地
            elif part == "着" and before in ("不", "得"):
                said = "zháo"    # 睡不着, 睡得着
            elif part in ALONE:
                follows, other = BEFORE.get(part, ("", ""))
                nxt = text[pos + 1:pos + 2]
                said = other if nxt and nxt in follows else ALONE[part]
            elif (part, flag) in PARTICLES:
                said = PARTICLES[part, flag]
            elif part == "过" and k == 1 and len(parts) == 2:
                said = "guo"     # 用过, 来过: "have done before"
            out.append((part, said))
            pos += len(part)
    return out
