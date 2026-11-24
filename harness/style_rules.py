"""Writing rules against "AI味" (Chinese that sounds machine-written).

Used twice, by run.py (stories) and news.py (digests):
  1. the rules go into the system prompt, so Claude writes well the first time;
  2. ai_phrases() lists leftovers, so the save gate can send a draft back.
"""
import re

# Never in the narrator's voice. Inside “quotes” they are fine: an office character may say 赋能.
BANNED = ("赋能 破局 底层逻辑 跃迁 升华 蜕变 降维打击 弯道超车 认知升级 高维视角 闭环 抓手 链路 生态位 "
          "价值感 仪式感 松弛感 钝感力 "
          "在当今社会 随着科技的发展 快节奏的时代 众所周知 值得注意的是 值得一提的是 综上所述 总而言之 "
          "由此可见 毋庸置疑 归根结底 真正重要的是 至关重要 深入探讨 充满活力 不断演变 深刻的内涵 "
          "拭目以待 遇见更好的自己 让我们").split()
# Sentences where the narrator announces that a character now sees things differently.
TURNS = (r"(?:才|终于|忽然|突然|这才)(?:知道|明白|懂得|意识到|发现|觉得)，?原来", r"原来[^。！？\n]{1,15}(?:这么|那么|如此)",
         r"(?:忽然|突然|第一次)(?:觉得|明白|意识到|懂得)", r"终于(?:明白|意识到|懂得)", r"笑着笑着[^。\n]{0,8}(?:哭|泪)")
SENTENCE_STARTS = ("总之",)   # fine as a word, not as a closing formula

COMMON = f"""
Write like a person talking, not like an AI:
- Plain spoken words and concrete details. Mix short and longer sentences; don't make them all match.
  Test: if you would never say a phrase out loud, change it.
- Never use these outside a character's quoted speech: {'、'.join(BANNED)}.
- Avoid unless really needed: 深度、内卷、共鸣、颠覆、赛道、护城河、复盘、沉淀、刻意练习、成长型思维.
- 不是…而是…, 以前…现在…, 首先/其次/最后 and lists of three: at most one of them, once, in the whole text.
- Never announce a character's change of view or feeling: no 才知道，原来八点钟的房间这么安静; no 忽然觉得…,
  终于明白…, 第一次意识到…, 笑着笑着眼泪掉了下来. Write what the person does, says or sees, and stop there.
  (原来 or 才知道 for a plain plot fact is fine: 才知道这里是律师事务所.)
- No slogans, no lecturing, no summary or 总之 at the end, no conclusion that says nothing.
"""

STORY_STYLE = COMMON + """- End on an action, a line of speech or an image. Never end with a moral, 鸡汤 or a call to the reader.
- The narrator may have a little attitude and humour. Office jargon is fine in a 职场 character's mouth.
"""

NEWS_STYLE = COMMON + """- Stick to the article: no opinion, no prediction and no praise that the article does not contain.
- Say why it matters only with a fact from the article. If there is none, just stop: the last sentence
  should be a piece of news, not a comment such as 说明…, 标志着…, 可能会成为….
"""


def ai_phrases(text: str) -> list[str]:
    """Banned phrases left in the narrator's own words (quoted speech is ignored)."""
    own = re.sub(r"“[^”]*”|「[^」]*」|‘[^’]*’", "", text)
    found = [w for w in BANNED if w in own]
    found += [m.group() for p in TURNS for m in re.finditer(p, own)]
    found += [w for w in SENTENCE_STARTS if re.search(rf"(?:^|[。！？!?\n])\s*{w}", own)]
    if len(re.findall(r"不是[^。！？\n]{1,30}而是", own)) > 1:
        found.append("不是…而是… (more than once)")
    return found
