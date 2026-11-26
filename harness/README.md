# Phase 5: my own harness

`news.py` is a small agent built by hand on the Claude API. It reads today's
Chinese tech news (AI models, robots, new phones, funding, product management) and
writes short learner digests for someone preparing for HSK 6 who works in China's
tech industry. Each digest is in Claude's own words, 300–600 characters, checked
for HSK 6 difficulty, and credits and links the original article.

```powershell
.venv/Scripts/python.exe harness/news.py
.venv/Scripts/python.exe harness/news.py --count 3
.venv/Scripts/python.exe harness/news.py --dry-run
```

`--dry-run` is free: it shows the ranked headlines, the tools and the prompt, with no
API call. Other options: `--count 5` digests, `--days 0` (only news published today, China time), `--coverage 0.90` and `--max-coverage 0.95`
(share of words that must be within HSK 6), `--min-chars 300`, `--max-chars 600`,
`--model claude-opus-5-5`, `--effort medium`, `--max-turns 25`, `--max-retries 2`.

Digests are saved to `harness/output/news/<date>/` as Markdown with front matter
(title, source, source_url, published, companies, topics, summary_en, keywords,
hsk6_coverage). My untested estimate is $0.20–0.40 per digest on Opus 5.5; each run prints the real cost.

## Daily on GitHub (publishing to the website)

`.github/workflows/news.yml` is scheduled daily for 09:50 Beijing time, with backups at 10:25 and 16:25
(GitHub can start late or skip a run; backups stop for free once today's digests are published), on GitHub's servers:
`news.py --publish --count 1 --effort low` writes the digests straight into `content/stories/` as
`news-<date>-<NN>.md` (genre `news`, with the source link in `sources`), then
`tools/build_site.py` adds pinyin and meanings, the workflow commits, and the Pages
deploy runs. `--publish` also skips articles already on the site and deletes digests
older than `--keep-days 14`.

One-time setup: add the API key as a repository secret named `ANTHROPIC_API_KEY`
(repo → Settings → Secrets and variables → Actions), set a monthly spending limit in the
Anthropic Console, then test with Actions → "Daily news digests" → Run workflow.

## Files

| File | What it does |
|---|---|
| `news_feeds.py` | Plain code, no AI. Downloads 10 tech-news RSS feeds and ranks headlines by the companies and topics in its `KEYWORDS` list (edit that list to change what you get). |
| `news.py` | The news harness: the agent loop, the tools and the quality gate. |
| `run.py` | The first version: writes one grounded story from a request. Stories can also be written in Claude Code, so this one is optional. |

Feeds (tested 2026-11-24): IT之家, 界面新闻, 钛媒体, 量子位, InfoQ, 人人都是产品经理,
极客公园, 雷峰网, 虎嗅, 少数派. 虎嗅 is read from its feed, which carries the full article.
36氪, 机器之心 and 品玩 had no usable RSS feed.

## The loop in one picture

```
 news_feeds.py (plain code): 10 RSS feeds ─► ranked headlines
                                                   │
            ┌──────────────────── news.py (the HARNESS) ─────────┼───────────────────┐
            │ system prompt + tools + messages (headline list) ◄─┘                   │
            │        │                                                               │
            │        ▼                                                               │
            │   ┌──────────┐  stop_reason == "tool_use"  ┌─────────────────────────┐ │
            │   │  Claude  │ ───────────────────────────►│ run each tool call      │ │
            │   │   API    │                             │ • read_news_article ────┼─┼─► MCP server ─► news site
            │   └──────────┘ ◄────────────────────────── │ • check_hsk_level ──────┼─┼─► MCP server ─► HSK word list
            │        │        tool_results go back       │ • save_digest (local):  │ │
            │        │        as the next user message   │   length + HSK 6 gate,  │ │
            │        │                                   │   then writes the .md   │ │
            │        │ stop_reason == "end_turn"         └─────────────────────────┘ │
            │        ▼   (or 25 turns reached)                                       │
            │  print totals, append a line to runs.jsonl                             │
            └────────────────────────────────────────────────────────────────────────┘
```

Each trip around the loop costs one API call. The terminal prints every step: the
call's token counts and dollar cost, a summary of what Claude was thinking, which
tool it called with which arguments, and a short preview of the result.

## Which part is what

| Part | What it is here | Where |
|---|---|---|
| **Harness** | The code around the model. It holds the conversation, offers tools, runs the tools Claude asks for, enforces limits (max turns, max retries), checks quality, logs cost and saves the output. The model only *asks*; the harness *does*. | `main()` loop, `handle_save()` in `news.py` |
| **MCP** | The standard plug between an AI app and tools. The harness is an **MCP client**: it starts `mcp_server/server.py`, asks "what tools do you have?" and converts the answer into Claude API tool definitions. The same server also works in Claude Code and Claude Desktop. | the top of `main()`, `mcp.call_tool(...)` |
| **RAG** | Retrieval-augmented generation: fetch the right text first, then generate from it. Here the feeds and `read_news_article` retrieve today's articles, and Claude writes only from what it read, with the link saved as the source. (`run.py` uses `search_library` over the Wikipedia/Wikisource library the same way.) | `news_feeds.py`, the system prompt |

Design choices worth pointing out:

- **Plain code where AI isn't needed.** Downloading and ranking feeds is cheap and
  predictable, so code does it. Claude is used only for judgement and writing:
  choosing stories, reading them, and explaining them simply.
- **`save_digest` is the harness's own tool, not an MCP tool.** When Claude calls it,
  the harness counts the characters and runs `check_hsk_level` itself. If the digest is
  too short, too long or too hard, the harness refuses to save it and says what to
  fix (up to `--max-retries` times). So the rules are enforced by code, not just
  requested in the prompt.
- **Copyright-safe by design.** Digests are short, in Claude's own words, and always
  credit and link the original. Full articles are only read, never published.
- **Cost is measured, not guessed.** Each API response reports its `usage`. The harness
  prints the cost of every call and the total, and appends one line per run to
  `harness/runs.jsonl`. Prompt caching makes the re-sent history much cheaper.

## Safety and cost settings

- The API key is read from `.env` (git-ignored) and is never printed. If it is missing,
  the script explains how to create one. Set a spending limit in the Anthropic Console.
- `--max-turns` stops a runaway loop. `--dry-run` shows everything except the API call.
- `fallbacks="default"`: if Opus declines a request, the API retries it on a fallback
  model in the same call. The log shows which model actually answered.

## Three interview talking points

1. **"An agent is a loop, and I can show you the loop."** Send messages and tools →
   if the model asks for a tool, run it and send back the result → repeat until it
   says it's done. I wrote it by hand in about 40 lines instead of using a framework,
   so I can explain every step and its cost to a customer.
2. **"Use AI only where it adds judgement."** Plain code collects and ranks 10 news
   feeds for free; the model does the parts code can't: picking the stories that
   matter, reading them and rewriting them for a learner. MCP made my tools reusable
   across Claude Code, Claude Desktop and this harness.
3. **"The harness is where product decisions live."** The model writes; the harness
   decides what counts as done (length and HSK 6 gates with a retry limit), what is
   safe to publish (own-words digests with credit), what it may spend (turn limit,
   per-call cost logging) and how it fails (clear messages). Those are the levers I
   would tune with a customer for their use case.
