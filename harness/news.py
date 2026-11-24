"""The news harness: turn today's Chinese tech news into short learner digests.

    .venv/Scripts/python.exe harness/news.py               # 5 digests from today's news (China time)
    .venv/Scripts/python.exe harness/news.py --count 3
    .venv/Scripts/python.exe harness/news.py --dry-run     # free: headlines + tools, no API call
    .venv/Scripts/python.exe harness/news.py --publish     # save to the website (what the daily GitHub run does)

How one run works:
  1. Plain code (news_feeds.py) downloads 8 tech-news RSS feeds and ranks the
     headlines by the companies and topics we care about. No AI yet.
  2. Start our MCP server and get its tools (read_news_article, check_hsk_level, ...).
     Add one tool of our own: save_digest, which the harness runs itself.
  3. THE LOOP: Claude picks stories, reads each article through MCP, writes a
     300-600 character digest in its own words, checks it at HSK 6, and saves it.
  4. save_digest is the quality gate: the harness checks length and HSK 6
     coverage, and sends the digest back if it doesn't pass.
"""
import argparse, asyncio, json, re, sys, time
from pathlib import Path

import anthropic
from mcp import Client, StdioServerParameters

import news_feeds
from style_rules import NEWS_STYLE, ai_phrases
from run import ROOT, HERE, load_api_key, short, cost_of   # shared helpers from the story harness

SYSTEM = """You are the editor of a daily Chinese tech-news digest for adult learners who have
finished HSK 5, are preparing for HSK 6, and work in China's tech industry (product, marketing, AI).

From the headlines in the user message, choose {count} stories. All of them were published today
({today}, China time); use only these, never older news. If fewer than {count} are worth a digest,
save fewer:
- concrete news: a company releases a model or product, raises money, makes a deal, enters a market;
- different companies and topics (AI, robots, phones, cars, product management, going overseas);
- prefer one-story articles over daily round-ups (早报, 早知道), and skip ads and event PR.

For each story:
1. Read it with read_news_article. If that fails or the text is empty, pick another headline.
2. Write a digest IN YOUR OWN WORDS: {min_chars}-{max_chars} Chinese characters, natural modern
   Chinese, short-to-medium sentences. Say who did what, the key numbers, and, if the article
   says so, why it matters. Never copy sentences from the article. Use only facts from the article.
3. Check it with check_hsk_level (target_level=6). Goal: {goal:.0%}-{goal_max:.0%} of words within HSK 6,
   so readers meet some real news vocabulary. Company and product names do not count.
   Below {goal:.0%}: simplify words in above_target. Above {goal_max:.0%}: too easy, keep the real news words.
4. Call save_digest. The harness checks it again and may send it back to fix.
When all {count} digests are saved, reply with one short line."""

SAVE_DIGEST = {
    "name": "save_digest",
    "description": "Save one finished news digest. The harness checks its length and HSK 6 coverage "
                   "first and returns an error saying what to fix if it does not pass.",
    "input_schema": {"type": "object", "required": ["title", "title_en", "source", "source_url", "published", "companies",
                                                     "topics", "summary_en", "summary_zh", "keywords", "digest"],
        "properties": {
            "title": {"type": "string", "description": "short Chinese headline, in your own words"},
            "title_en": {"type": "string", "description": "short English headline"},
            "source": {"type": "string", "description": "outlet name, e.g. IT之家"},
            "source_url": {"type": "string", "description": "the original article URL"},
            "published": {"type": "string", "description": "date from the headline list"},
            "companies": {"type": "array", "description": "every company, brand and product name in the digest, "
                          "each with pinyin and a short English gloss so learners see the name, not its characters",
                          "items": {"type": "object", "required": ["name", "pinyin", "meaning"], "properties": {
                              "name": {"type": "string", "description": "exactly as written in the digest, e.g. 来酷"},
                              "pinyin": {"type": "string", "description": "with tone marks, e.g. lái kù (empty for Latin names)"},
                              "meaning": {"type": "string", "description": "short English gloss, e.g. Lecoo (a Lenovo brand)"}}}},
            "topics": {"type": "array", "items": {"type": "string"}, "description": "1-3 of: AI, robots, phones, "
                       "cars, chips, funding, product, overseas, other"},
            "summary_en": {"type": "string", "description": "one-sentence English summary"},
            "summary_zh": {"type": "string", "description": "one short, easy Chinese sentence (under 40 characters)"},
            "keywords": {"type": "array", "items": {"type": "string"}, "description": "3-8 words worth learning"},
            "digest": {"type": "string", "description": "the digest text, plain paragraphs"}}},
}


def headline_list(items: list[dict]) -> str:
    """The first user message: today's ranked headlines, one numbered line each."""
    lines = [f"{i}. [{it['source']} {it['date']}] {it['title']}\n   {it['url']}\n   {it['summary'][:120]}"
             for i, it in enumerate(items, 1)]
    return f"Today is {news_feeds.today_in_china()} (China time). Today's headlines:\n\n" + "\n".join(lines)


STORIES = ROOT / "content" / "stories"   # where the website's build script looks for stories


def published_urls() -> set[str]:
    """Article URLs already on the website, so tomorrow's run doesn't pick the same story."""
    urls = set()
    for path in STORIES.glob("news-*.md"):
        m = re.search(r"^sources:\s*(\S+)", path.read_text(encoding="utf-8"), re.M)
        if m:
            urls.add(m.group(1))
    return urls


def prune_old_news(keep_days: int) -> None:
    """Delete published digests older than keep_days, so the News shelf stays fresh and small."""
    cutoff = time.strftime("%Y-%m-%d", time.localtime(time.time() - keep_days * 86400))
    for path in STORIES.glob("news-*.md"):
        if path.stem[5:15] < cutoff:   # file name: news-YYYY-MM-DD-NN.md
            path.unlink()
            print(f"Removed old digest {path.name}")


def company_names(d: dict) -> list[str]:
    """Company/brand names from save_digest (objects with name/pinyin/meaning; plain strings also accepted)."""
    return [c["name"] if isinstance(c, dict) else str(c) for c in d.get("companies", [])]


def glossed_names(d: dict) -> list[str]:
    """Names for the story header as 'name|pinyin|meaning', so the site shows 'Lecoo (a Lenovo brand)'
    instead of splitting 来酷 into 来 + 酷. The header list is split on commas, so commas and bars
    inside a gloss are replaced."""
    clean = lambda v: re.sub(r"\s*[,，、|]+\s*", "; ", str(v or "")).strip(" ;")
    entries = {}
    for c in d.get("companies", []) + [news_feeds.SOURCE_GLOSSES.get(d["source"], d["source"])]:
        if isinstance(c, dict):
            c = {"name": c.get("name", ""), "pinyin": c.get("pinyin", ""), "meaning": c.get("meaning", "")}
        else:
            c = {"name": str(c), "pinyin": "", "meaning": ""}
        name = clean(c["name"])
        if name and name not in entries:
            entries[name] = f"{name}|{clean(c['pinyin'])}|{clean(c['meaning'])}" if c["meaning"] else name
    return list(entries.values())


def publish_story(d: dict) -> Path:
    """Write one digest as a website story (genre: news) in content/stories/. Then
    tools/build_site.py adds pinyin, meanings and HSK levels like for any other story."""
    one_line = lambda v: re.sub(r"\s+", " ", str(v)).strip()   # header values must stay on one line
    today = time.strftime("%Y-%m-%d")
    number = len(list(STORIES.glob(f"news-{today}-*.md"))) + 1
    path = STORIES / f"news-{today}-{number:02d}.md"
    header = {"title": d["title"], "title_en": d.get("title_en") or d["summary_en"], "genre": "news",
              "blurb_en": d["summary_en"], "blurb_zh": d.get("summary_zh") or d["title"], "source": d["source"],
              "date": str(d["published"])[:10], "sources": d["source_url"],
              "names": ", ".join(glossed_names(d)),   # names are never "hard"; each carries its own gloss
              "keywords": ", ".join(d.get("keywords", []))}
    front = "\n".join(f"{k}: {one_line(v)}" for k, v in header.items())
    credit = f"来源：{d['source']}（本文为学习用摘要，原文链接见下方）"
    path.write_text(f"---\n{front}\n---\n{d['digest'].strip()}\n\n{credit}\n", encoding="utf-8")
    return path


def save_markdown(d: dict, coverage: float) -> Path:
    """Write one digest to harness/output/news/<date>/ with front matter (JSON values are valid YAML)."""
    meta = {"type": "news", **{k: d.get(k) for k in ("title", "source", "source_url", "published", "companies",
                                                 "topics", "summary_en", "keywords")}, "hsk6_coverage": coverage}
    front = "\n".join(f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in meta.items())
    folder = HERE / "output" / "news" / time.strftime("%Y-%m-%d")
    folder.mkdir(parents=True, exist_ok=True)
    safe_title = re.sub(r'[\\/:*?"<>|\s]+', "_", d["title"])[:40]
    path = folder / f"{len(list(folder.glob('*.md'))) + 1:02d}-{safe_title}.md"
    credit = f"来源：[{d['source']}]({d['source_url']})（本文为学习用摘要，请阅读原文）"
    path.write_text(f"---\n{front}\n---\n\n# {d['title']}\n\n{d['digest']}\n\n{credit}\n", encoding="utf-8")
    return path


async def handle_save(mcp, d: dict, args, run: dict) -> tuple[str, bool]:
    """The quality gate. Returns (message for Claude, is_error)."""
    if d["source_url"] in run["saved_urls"]:
        return "Already saved this article. Pick a different story.", True
    tries = run["retries"].setdefault(d["source_url"], 0)
    chars = len(re.findall(r"[一-鿿]", d["digest"]))   # count Chinese characters only
    text = d["digest"]
    for word in company_names(d) + d.get("keywords", []):   # names and taught words don't count
        text = text.replace(word, "，")
    check = json.loads((await mcp.call_tool("check_hsk_level", {"text": text, "target_level": 6})).content[0].text)
    within = check["within_target"]
    problems = []
    if not args.min_chars <= chars <= args.max_chars:
        problems.append(f"length is {chars} characters (need {args.min_chars}-{args.max_chars})")
    if within < args.coverage:
        hard = "、".join(h["word"] for h in check["above_target"][:20])
        problems.append(f"only {within:.0%} of words are within HSK 6 (goal {args.coverage:.0%}); simplify: {hard}")
    if within > args.max_coverage:
        problems.append(f"{within:.0%} of words are within HSK 6, too easy (aim for {args.coverage:.0%}-"
                        f"{args.max_coverage:.0%}); use the real news vocabulary instead of simpler words")
    stiff = ai_phrases("\n".join([d["title"], d.get("summary_zh", ""), d["digest"]]))
    if stiff:
        problems.append(f"sounds machine-written, say it plainly: {'、'.join(stiff)}")
    if problems and tries < args.max_retries:
        run["retries"][d["source_url"]] += 1
        return f"Not saved: {'; '.join(problems)}. Fix this and call save_digest again.", True
    path = publish_story(d) if args.publish else save_markdown(d, within)
    run["saved_urls"].append(d["source_url"]); run["saved"].append(str(path))
    note = f" (saved anyway, out of retries: {'; '.join(problems)})" if problems else ""
    return f"saved to {path.name}: {chars} characters, HSK 6 coverage {within:.0%}{note}", False


async def main(args, claude=None) -> dict:
    sys.stdout.reconfigure(encoding="utf-8")
    if not (args.dry_run or claude):   # check the key first, before doing any work
        claude = anthropic.AsyncAnthropic(api_key=load_api_key())

    # Step 1: plain code collects and ranks the headlines.
    if args.publish:   # website mode: tidy up old digests
        prune_old_news(args.keep_days)
    done = published_urls()   # never pick a story that is already on the website (trial runs too)
    headlines = [h for h in news_feeds.top_headlines(limit=args.headlines + len(done), days=args.days)
                 if h["url"] not in done][:args.headlines]
    when = "today" if args.days == 0 else f"today and the {args.days} day(s) before"
    print(f"Collected {len(headlines)} ranked headlines published {when} (China time) from "
          f"{len(news_feeds.FEEDS)} feeds, skipping {len(done)} already on the website.")
    if not headlines:   # no fallback to older news: save nothing, and don't fail the daily run
        print("No new headlines from today yet, so nothing to write. Try again later in the day.")
        return {}

    server = StdioServerParameters(command=sys.executable, args=[str(ROOT / "mcp_server" / "server.py")])
    async with Client(server) as mcp:
        # Step 2: MCP tools -> Claude API tool definitions, plus our own save_digest.
        wanted = {"read_news_article", "check_hsk_level", "lookup_word"}   # only what this job needs
        tools = [{"name": t.name, "description": t.description, "input_schema": t.input_schema}
                 for t in (await mcp.list_tools()).tools if t.name in wanted] + [SAVE_DIGEST]
        print(f"MCP server connected. Tools: {', '.join(t['name'] for t in tools)}")
        system = SYSTEM.format(today=news_feeds.today_in_china(), count=args.count, min_chars=args.min_chars, max_chars=args.max_chars, goal=args.coverage, goal_max=args.max_coverage) + "\n" + NEWS_STYLE
        messages = [{"role": "user", "content": headline_list(headlines)}]
        if args.dry_run:
            print("\n" + messages[0]["content"][:4000] + "\n...\n\nSYSTEM PROMPT:\n" + system)
            return {}

        run = {"calls": 0, "input": 0, "output": 0, "cache_read": 0, "cost": 0.0,
               "saved": [], "saved_urls": [], "retries": {}, "stop": "max_turns"}

        # Step 3: THE LOOP. Each turn = one API call + running whatever tools Claude asked for.
        for turn in range(1, args.max_turns + 1):
            async with claude.beta.messages.stream(
                model=args.model, max_tokens=64000, system=system, tools=tools, messages=messages,
                thinking={"type": "adaptive", "display": "summarized"},   # show a summary of its reasoning
                output_config={"effort": args.effort},
                cache_control={"type": "ephemeral"},   # re-sent history is billed at the cheap cache rate
                betas=["server-side-fallback-2026-07-01"], fallbacks="default",  # if Opus declines, a fallback model retries
            ) as stream:
                response = await stream.get_final_message()

            u, dollars = response.usage, cost_of(response.usage, response.model)
            run["calls"] += 1; run["cost"] += dollars
            run["input"] += u.input_tokens; run["output"] += u.output_tokens
            run["cache_read"] += u.cache_read_input_tokens or 0
            print(f"\n[turn {turn}] {response.model}  in={u.input_tokens} cached={u.cache_read_input_tokens or 0} "
                  f"out={u.output_tokens}  ${dollars:.4f}  (total ${run['cost']:.4f})  stop={response.stop_reason}")
            for block in response.content:
                if block.type == "thinking" and block.thinking:
                    print(f"  thinks: {short(block.thinking)}")
                elif block.type == "text":
                    print(f"  says:   {short(block.text, 200)}")

            messages.append({"role": "assistant", "content": response.content})   # keep it all, unchanged
            if response.stop_reason != "tool_use":   # end_turn, refusal, max_tokens...: stop the loop
                run["stop"] = response.stop_reason
                break

            results = []   # run every tool Claude asked for; send ALL results back in one message
            for call in (b for b in response.content if b.type == "tool_use"):
                print(f"  -> {call.name}({short(call.input)})")
                if call.name == "save_digest":
                    content, is_error = await handle_save(mcp, call.input, args, run)
                else:   # every other tool lives on the MCP server
                    result = await mcp.call_tool(call.name, call.input)
                    content, is_error = result.content[0].text, bool(result.is_error)
                print(f"  <- {short(content)}")
                results.append({"type": "tool_result", "tool_use_id": call.id, "content": content, "is_error": is_error})
            messages.append({"role": "user", "content": results})

    # Step 4: totals, plus one line per run in harness/runs.jsonl.
    print(f"\nDone: {len(run['saved'])} digests, {run['calls']} API calls, {run['input']} in / {run['output']} out "
          f"tokens (+{run['cache_read']} cache reads), total ${run['cost']:.4f}")
    for path in run["saved"]:
        print(f"  {path}")
    log = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "kind": "news", "model": args.model, "count": args.count,
           **{k: run[k] for k in ("calls", "input", "output", "cache_read", "saved", "stop")}, "cost": round(run["cost"], 4)}
    with open(HERE / "runs.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(log, ensure_ascii=False) + "\n")
    return run


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Turn today's Chinese tech news into HSK 6 learner digests.")
    p.add_argument("--count", type=int, default=5, help="how many digests to write")
    p.add_argument("--days", type=int, default=0,
                   help="0 = only news published today, China time (default); 1 = also yesterday, ...")
    p.add_argument("--headlines", type=int, default=40, help="how many ranked headlines Claude chooses from")
    p.add_argument("--min-chars", type=int, default=300, help="shortest digest, in Chinese characters")
    p.add_argument("--max-chars", type=int, default=600, help="longest digest, in Chinese characters")
    p.add_argument("--coverage", type=float, default=0.90, help="share of words that must be within HSK 6")
    p.add_argument("--max-coverage", type=float, default=0.95, help="above this the digest is too easy and is sent back")
    p.add_argument("--model", default="claude-opus-5-5", help="Claude model ID")
    p.add_argument("--effort", default="medium", choices=["low", "medium", "high", "xhigh", "max"])
    p.add_argument("--max-turns", type=int, default=25, help="safety limit on loop turns (API calls)")
    p.add_argument("--max-retries", type=int, default=2, help="how often save_digest may send one digest back")
    p.add_argument("--publish", action="store_true",
                   help="save digests as website stories in content/stories/ (genre: news) instead of harness/output/")
    p.add_argument("--keep-days", type=int, default=14, help="with --publish: delete digests older than N days")
    p.add_argument("--dry-run", action="store_true", help="show headlines, tools and prompt; no API call")
    return p.parse_args(argv)


if __name__ == "__main__":
    try:
        asyncio.run(main(parse_args()))
    except* anthropic.APIError as group:   # e.g. wrong key, no credit left, rate limit
        error = group
        while isinstance(error, BaseExceptionGroup):   # async code wraps errors in groups; unwrap
            error = error.exceptions[0]
        sys.exit(f"Claude API error: {error}")
