"""Phase 0 check: can we split Chinese text into words?

Chinese has no spaces between words, so before we can look up a word's
HSK level we must first find where each word starts and ends.
jieba does that for us. No AI involved: it uses a dictionary + statistics.
"""
import sys

import jieba

sys.stdout.reconfigure(encoding="utf-8")  # so Windows terminals print Chinese correctly
jieba.setLogLevel("WARNING")              # hide jieba's startup messages

text = "嫦娥吃了不死药以后，飞到了月亮上。"
words = jieba.lcut(text)

print("Original :", text)
print("Words    :", " | ".join(words))
print("Count    :", len(words), "words")
