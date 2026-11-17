"""Phase 2: the HSK Reader MCP server.

An MCP server is a small program that offers TOOLS to any AI app that speaks MCP
(Claude Code, Claude Desktop, or the harness you'll write in Phase 5).

What happens when an AI app connects:
  1. The app starts this file as a subprocess and talks to it over stdin/stdout
     ("stdio transport"). Messages are JSON lines.
  2. The app asks "what tools do you have?" (tools/list). We answer with each
     tool's name, description and input schema, all generated from the Python
     functions below: the docstring becomes the description, and the type hints
     become the JSON schema.
  3. When the model decides to use a tool, the app sends tools/call with the
     arguments; we run the function and send back the result.

The model never runs code itself. It only reads the descriptions and asks.
So the docstrings below are effectively instructions for the model:
write them for the model as the reader.

IMPORTANT for stdio servers: never print() to stdout. stdout IS the message
channel, so a stray print would corrupt the conversation. Logs go to stderr.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field

from mcp.server.mcpserver import MCPServer

# Let us import tools/hsk_checker.py from the sibling folder.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import article  # noqa: E402
import hsk_checker  # noqa: E402
import search as library  # noqa: E402
import semantic  # noqa: E402

Scheme = Literal["hsk30", "hsk2026", "hsk2"]
Level = Annotated[int, Field(ge=1, le=7, description="HSK level 1-7, where 7 means the combined 7-9 band")]

server = MCPServer(
    "hsk-reader",
    instructions=(
        "Tools for writing and checking Chinese texts at a target HSK level. "
        "Typical flow: draft a text, call check_hsk_level, then replace the words in "
        "'above_target' with simpler ones and check again until meets_target is true. "
        "Default scheme is hsk30 (HSK 3.0, 2021 standard). "
        "For stories about myths, history, festivals or news, FIRST call search_library "
        "and base the story on what it returns, so facts are not invented. "
        "For a news article, read it with read_news_article before rewriting it."
    ),
)


@server.tool()
def check_hsk_level(
    text: Annotated[str, Field(description="Chinese text to analyse (simplified characters)")],
    target_level: Level = 4,
    scheme: Scheme = "hsk30",
) -> dict:
    """Check how hard a Chinese text is for a reader at target_level.

    Returns the share of words at or below each HSK level, whether the text meets
    the target (95% of words at or below it), an estimated level, the list of words
    above the target (with pinyin, meaning, level and how often each appears), and
    names that were ignored. Use this after writing or rewriting any graded text.
    """
    return hsk_checker.check_text(text, target_level, scheme)


@server.tool()
def lookup_word(
    word: Annotated[str, Field(description="One Chinese word, e.g. 保管")],
    scheme: Scheme = "hsk30",
) -> dict:
    """Look up one Chinese word: pinyin, English meanings and HSK level.

    Words not in the HSK list are looked up in CC-CEDICT; compounds of HSK words
    (e.g. 射下 = 射 + 下) get the level of their hardest part.
    """
    return hsk_checker.lookup_word(word, scheme)


@server.tool()
def get_hsk_vocab(
    level: Level,
    scheme: Scheme = "hsk30",
    exclusive: Annotated[bool, Field(
        description="true = only words introduced AT this level; false = this level and all below")] = True,
    limit: Annotated[int, Field(ge=1, le=500, description="max words to return")] = 100,
    offset: Annotated[int, Field(ge=0, description="skip this many words (for paging)")] = 0,
) -> dict:
    """List HSK vocabulary for a level, to help write texts that practise those words.

    Lists can be long (level 7-9 has ~5,600 words), so results are paged with
    limit/offset. Prefer small pages: every word returned takes up context.
    """
    words = hsk_checker.vocab_for_level(level, scheme, exclusive)
    return {
        "level": hsk_checker.level_name(level),
        "scheme": scheme,
        "total": len(words),
        "offset": offset,
        "words": words[offset:offset + limit],
    }


@server.tool()
def search_library(
    query: Annotated[str, Field(description="Chinese keywords, e.g. '嫦娥 不死药' or '端午节 屈原'")],
    collection: Annotated[Literal["news", "wikipedia", "wikisource"] | None, Field(
        description="news = recent headlines; wikipedia = modern articles (facts); "
                    "wikisource = original classical texts. Omit to search all.")] = None,
    category: Annotated[Literal["folklore", "history", "east_asia", "culture", "classics"] | None,
                        Field(description="optional story category filter")] = None,
    k: Annotated[int, Field(ge=1, le=10, description="number of passages to return")] = 4,
    mode: Annotated[Literal["hybrid", "keyword", "semantic"], Field(
        description="hybrid (default) = best overall; keyword = exact words, best for rare "
                    "names; semantic = meaning, best for questions and paraphrases")] = "hybrid",
) -> dict:
    """Search the reading library and return the most relevant passages (RAG retrieval).

    Use this BEFORE writing a story about a myth, historical event, festival,
    East Asian tale or news topic, and base the story on the passages returned.
    Query in Chinese: keywords (嫦娥 不死药) or a question (为什么端午节要吃粽子)
    both work in hybrid mode. Each passage includes its title, section and
    source URL for attribution.
    """
    hits = semantic.MODES[mode](query, k, collection, category)
    return {
        "query": query,
        "results": [
            {k_: h.get(k_) for k_ in ("title", "section", "collection", "category",
                                      "source", "date", "url", "score", "text")}
            for h in hits
        ],
        "note": None if hits else "No matches. Try other or fewer Chinese keywords.",
    }


@server.tool()
def latest_news(
    limit: Annotated[int, Field(ge=1, le=30, description="how many headlines")] = 10,
    source: Annotated[Literal["BBC 中文", "DW 中文", "RFI 中文", "中新网"] | None,
                      Field(description="only this news source")] = None,
) -> dict:
    """List the newest Chinese news headlines with summaries, newest first.

    Headlines come from RSS feeds collected by tools/fetch_news.py. Use
    read_news_article with an item's url to get the full text.
    """
    items = library._read_jsonl(library.NEWS)
    if source:
        items = [i for i in items if i["source"] == source]
    return {"count": len(items), "items": items[:limit]}


@server.tool()
def read_news_article(
    url: Annotated[str, Field(description="article url, as returned by latest_news or search_library")],
) -> dict:
    """Fetch the full text of one news article (simplified Chinese), cached after first use.

    For personal study: use it to rewrite or summarise at an HSK level, and
    always credit the source and link to the original.
    """
    try:
        text = article.fetch_article(url)
    except Exception as e:
        return {"url": url, "error": f"could not fetch article: {e}"}
    return {"url": url, "characters": len(text), "text": text or None,
            "note": None if text else "No article text found on this page."}


if __name__ == "__main__":
    hsk_checker._load()  # load the data once at startup, not on the first call
    library._index()     # build the keyword index once at startup too
    semantic._vectors()  # load (or build) chunk embeddings
    semantic._model()    # load the embedding model for queries
    server.run()         # default transport: stdio
