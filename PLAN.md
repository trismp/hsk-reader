# HSK Reader: Project Plan

A website (later an Android app) for reading Chinese at your HSK level:
stories from news, history, folklore and general knowledge, plus daily news
rewritten to a chosen HSK 3.0 level.

**Main goal:** learn **MCP**, **RAG** and **harness** by building it, one concept per phase.

| Concept | One-line meaning | Where it lives in this project |
|---|---|---|
| **RAG** | Give Claude the right *information* before it writes | `tools/` fetchers + search over `data/` |
| **MCP** | Give Claude *abilities* (tools) through a standard plug | `mcp_server/` |
| **Harness** | The program that runs the loop: request, tools, check, repeat | Claude Code (Phases 2–4), then your own in `harness/` (Phase 5) |

## Folder layout

```
hsk-reader/
  data/        word lists, dictionary, content library
  tools/       level checker, fetchers (plain Python, no AI)
  mcp_server/  your MCP server (not named "mcp", which would hide the mcp package)
  harness/     your own Claude loop (Phase 5)
  site/        the public website
```

## Decisions
- **Audience (since 2026-11-23):** people who have finished HSK 5, are preparing for HSK 6
  and work in China. Goal: ease them into reading Chinese *often*, with fun stories,
  because real novels are still too hard. No level picker; words beyond the HSK list are fine.
- Stories: every chapter (or one-shot) has **700+ Chinese characters** (~700–1,000 per chapter),
  a coherent plot (no logic holes), an English + Chinese blurb.
- Genres (Discover tiles): 神话传说 Myths & Legends · 历史风云 History · 悬疑 Mystery ·
  职场人生 Life & Work in China · 言情 Romance (soon) · 时事财经 News & Markets (soon).
- Tabs: 发现 Discover → genre → story list · 书架 Library (saved stories + progress) ·
  生词本 My Words (saved words with their sentence → flashcards + quiz).
- Reading aids: tap a word for pinyin, meaning and level; pinyin for new words (above HSK 5); saved words.
- Installable on phones (PWA: manifest + service worker; works offline for stories already opened).
- Website first, shared publicly; Android later (PWA first, then maybe Expo).
- Cost model: a pre-generated library (readers are free) plus an optional
  "create your own" that uses each visitor's own Claude account or API key.

---

## Phase 0: Setup (free)
- [x] 0.1 Project folder + git
- [x] 0.2 Python environment; install `jieba`, `mcp` (v2: `MCPServer`, not `FastMCP`)
- [x] 0.3 Folder layout

## Phase 1: Data foundation (free). *Not everything needs AI*
- [x] 1.1 HSK word list → `data/hsk_words.json` (11,470 words; schemes hsk30 / hsk2026 / hsk2)
- [x] 1.2 CC-CEDICT → `data/dictionary.json` (120k words) + `data/jieba_words.txt`
- [x] 1.3 Level checker `tools/hsk_checker.py`: % per level, words above target, estimated level
- [x] 1.4 Tested on `data/samples/`: easy → HSK 2, 嫦娥 story → HSK 4, news → HSK 7-9

Lessons: re-adding known words to jieba breaks it (以后 → 以|后); rare readings
must be ranked down (抢 qiǎng, not qiāng); names are explained, not counted.

## Phase 2: MCP server (free with Pro). *MCP*
- [x] 2.1 Tools: `check_hsk_level`, `lookup_word`, `get_hsk_vocab` (`mcp_server/server.py`)
- [x] 2.2 See the raw discovery and call messages with our own client (`mcp_server/peek.py`)
- [x] 2.3 Connect to Claude Code (`.mcp.json`; approve the server when a new session starts)
- [ ] 2.4 Ask Claude to write and check a story with your tools

## Phase 3: Content library and search (free). *RAG*
- [x] 3.1 News fetcher `tools/fetch_news.py` (BBC 中文, DW 中文, RFI 中文, 中新网: summaries only)
      + on-demand full article `tools/article.py` (link-density filter, cached, git-ignored)
- [x] 3.2 Sources `tools/fetch_sources.py`: 81 docs from Wikipedia + 维基文库 (山海经, 搜神记), polite backoff on 429
- [x] 3.3 Chunking: whole paragraphs, ~400 chars, keeps title + section (1,785 chunks)
- [x] 3.4a Keyword search (BM25) `tools/search.py`
- [x] 3.4b Semantic search `tools/semantic.py` (bge-small-zh, 512-number vectors, cached) + hybrid (RRF)
      Result: semantic wins on paraphrases (飞到月亮上的女人 → 嫦娥), keyword wins on rare
      names (逄蒙), hybrid is good on all → default in `search_library`
- [x] 3.5 MCP tools: `search_library` (hybrid/keyword/semantic), `latest_news`, `read_news_article`
- [ ] 3.6 Compare stories with and without RAG (grounding)

Lessons: feeds give summaries, not articles; BBC's "simp" feed is traditional;
free APIs rate-limit (429), so honour Retry-After; char-level trad→simp needs word fixes (著/着).

## Phase 4: Website (free). *Static sites, pre-processing*
- [x] 4.1 Library page (filter by level and category): `site/`
- [x] 4.2 Reader page (key words box, names in blue, sources credited)
- [x] 4.3 Tap-a-word popup; text pre-split by `tools/build_site.py`
- [x] 4.4 Pinyin: off / only words above my level / all, plus "underline words above my level"
- [x] 4.5 Saved words (browser storage) + CSV export for Anki
- [x] 4.6 Starter library: 10 stories (HSK 3–6, all categories) in `content/stories/`,
      grounded in search results and checked: every one ≥ 99% within level
- [x] 4.7 Published on GitHub Pages → **https://trismp.github.io/hsk-reader/**
      (repo github.com/trismp/hsk-reader; `.github/workflows/pages.yml` redeploys `site/` on every push)

Lessons: first drafts were ALL too hard (that's the loop the harness will automate);
graded readers exclude names and pre-teach key words; neutral-tone readings are usually
the everyday ones (东西 dōng xi "thing"); boosting a 1-char word in jieba splits longer words.
Preview locally: `python -m http.server 8321 --directory site` → http://127.0.0.1:8321

## Phase 4b: Product redesign (free). *Product thinking*
- [x] Repositioned for HSK 5 → 6 readers working in China; dropped level filters
- [x] Story format with genres, chapters (`## ` headings), EN + ZH blurbs, own key-word glosses
- [x] 12 stories: 10 old ones rewritten (plots fixed, 700+ chars/chapter, facts re-checked; old
      versions in `content/archive/`) + first Mystery (张县令烧猪破案) and Life & Work (安娜的第一周)
- [x] Tabs: Discover (genre tiles → stories) · Library (save stories, progress) · My Words
- [x] Flashcards (tiny spaced repetition) and multiple-choice quiz from saved words
- [x] Installable on phones: `manifest.webmanifest`, `sw.js`, icons from `tools/make_icons.py`
- [ ] Romance + News & Markets genres (News can come from the Phase 6 pipeline)
- [ ] Publish (push to GitHub → Pages redeploys)

## Phase 5: Your own harness (~$5 API credit). *Harness*
- [ ] 5.1 API key in `.env`, spending limit set in Console
- [ ] 5.2 Simplest single call; inspect the response and tokens
- [ ] 5.3 Tool-use loop written by hand
- [ ] 5.4 Harness as an MCP client of your own server
- [ ] 5.5 Generate → check → simplify loop with a retry limit
- [ ] 5.6 Cost logging; compare Haiku / Sonnet / Opus

## Phase 6: Automation (a few $/month). *Pipelines*
- [ ] 6.1 Daily pipeline: fetch → rewrite per level → split into words → save
- [ ] 6.2 GitHub Actions daily schedule, API key stored as a secret
- [ ] 6.3 Safeguards: daily cap, duplicate detection, spending limit

## Phase 7: Extensions (optional)
- [ ] "Create your own story" (visitor's own Claude account or key)
- [ ] PWA (installable on Android) → later Expo app
- [ ] Audio, flashcards, quizzes

## Cost summary
| Phases | Cost |
|---|---|
| 0–4 | $0 (covered by Claude Pro) |
| 5 | ~$5 prepaid API credit |
| 6 | ~$3–20/month, depending on the model and number of levels |

## Safety rules
- The API key lives only in `.env` (ignored by git) or GitHub Secrets, **never in the website**.
- Always keep a spending limit set in the Anthropic Console.
- News: show a retelling or summary plus a link to the original; don't republish full articles.
