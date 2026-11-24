"""Phase 5: my own harness (agent loop) on the Claude API.

A "harness" is the code wrapped around a model: it gives the model tools,
runs the tools the model asks for, feeds the results back, and decides when to stop.

    python harness/run.py "写一个关于后羿射日的故事" --genre myths --chapters 1
    python harness/run.py "..." --dry-run      # no API call: shows tools + prompt only

How one run works:
  1. Start our MCP server (mcp_server/server.py) and ask it for its tools.
  2. Convert those tools into the format the Claude API expects, and add one
     tool of our own, save_story, which the harness runs itself.
  3. THE LOOP: send the conversation to Claude -> if Claude asks for tools, run
     them and send back the results -> repeat until Claude is done.
  4. save_story is a quality gate: the harness checks the story's HSK 6
     coverage and sends it back for simplifying if it is too hard.
"""
import argparse, asyncio, json, os, re, sys, time
from pathlib import Path

import anthropic
from mcp import Client, StdioServerParameters

from style_rules import STORY_STYLE, ai_phrases

ROOT = Path(__file__).resolve().parent.parent
HERE = ROOT / "harness"

# Price per 1 million tokens in US dollars (from the claude-api skill, 2026-11).
# cache_write = 1.25 x input (5-minute cache); cache_read is the published rate.
PRICES = {
    "claude-opus-5-5":   {"input": 4.00, "output": 20.00, "cache_write": 5.00, "cache_read": 0.20},
    "claude-sonnet-5-5": {"input": 2.00, "output": 10.00, "cache_write": 2.50, "cache_read": 0.10},
    "claude-fable-5-2":  {"input": 8.00, "output": 40.00, "cache_write": 10.00, "cache_read": 0.20},
}

SYSTEM = """You write Chinese stories for adult learners who have finished HSK 5, are preparing
for HSK 6, and live and work in China. Genre: {genre}. Length: {chapters} chapter(s), each
700-1,000 Chinese characters (if 1 chapter, one story of that length, no chapter heading).

How to work:
1. Ground the facts first: call search_library (Chinese keywords) and base names, places
   and events on the passages it returns. Do not invent facts the sources contradict.
2. Write a fun story with a clear, coherent plot and no logic holes. Words beyond the HSK
   list are allowed, but keep it readable: natural modern Chinese, short-to-medium sentences.
3. Check your draft with check_hsk_level (target_level=6). Goal: at least {goal:.0%} of words
   within HSK 6. Names of people and places do not count against you. If the coverage is
   too low, replace words in above_target with simpler ones and check again.
4. When done, call save_story once with the full story and its details. The harness checks
   it again and may ask you to simplify more. After it says "saved", reply with one short line."""

# Our own tool: the harness runs it, not the MCP server.
SAVE_STORY = {
    "name": "save_story",
    "description": "Save the finished story. The harness checks HSK 6 coverage first and "
                   "returns an error with the hard words if the story needs simplifying.",
    "input_schema": {"type": "object", "required": ["title", "summary_en", "summary_zh", "names",
                                                     "keywords", "sources", "story_markdown"],
        "properties": {
            "title": {"type": "string", "description": "Chinese title"},
            "summary_en": {"type": "string", "description": "one-sentence English summary"},
            "summary_zh": {"type": "string", "description": "one-sentence Chinese summary"},
            "names": {"type": "array", "items": {"type": "string"}, "description": "people/place names"},
            "keywords": {"type": "array", "items": {"type": "string"}, "description": "3-8 key words worth learning"},
            "sources": {"type": "array", "items": {"type": "string"}, "description": "URLs from search_library"},
            "story_markdown": {"type": "string", "description": "the story; chapters as '## 第一章 ...'"}}},
}


def load_api_key() -> str:
    """Read ANTHROPIC_API_KEY from the environment or the git-ignored .env file. Never print it."""
    env_file = ROOT / ".env"
    if not os.environ.get("ANTHROPIC_API_KEY") and env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            name, _, value = line.partition("=")
            if name.strip() == "ANTHROPIC_API_KEY":
                os.environ["ANTHROPIC_API_KEY"] = value.strip().strip('"').strip("'")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("No API key found. To create one:\n"
                 "  1. Go to https://console.anthropic.com, sign in, add $5 of credit.\n"
                 "  2. Under Limits, set a monthly spending limit (e.g. $10).\n"
                 "  3. Create an API key and put this line in a file named .env in the project folder:\n"
                 "       ANTHROPIC_API_KEY=sk-ant-...\n"
                 "  (.env is git-ignored, so the key never gets committed.)")
    return os.environ["ANTHROPIC_API_KEY"]


def short(value, n=90) -> str:
    """One-line preview of any value, for the step-by-step printout."""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    text = text.replace("\n", " ")
    return text if len(text) <= n else text[:n] + "..."


def cost_of(usage, model: str) -> float:
    """Dollars for one API call, from the token counts the API reports in response.usage."""
    p = PRICES.get(model, PRICES["claude-opus-5-5"])
    return (usage.input_tokens * p["input"] + usage.output_tokens * p["output"]
            + (usage.cache_creation_input_tokens or 0) * p["cache_write"]
            + (usage.cache_read_input_tokens or 0) * p["cache_read"]) / 1_000_000


async def coverage_check(mcp, story: dict) -> dict:
    """Ask the MCP server how much of the story is within HSK 6. Names and keywords are
    blanked out first: a reader is told them, not expected to know them."""
    text = story["story_markdown"]
    for word in story.get("names", []) + story.get("keywords", []):
        text = text.replace(word, "，")
    result = await mcp.call_tool("check_hsk_level", {"text": text, "target_level": 6})
    return json.loads(result.content[0].text)


def save_markdown(story: dict, genre: str, coverage: float) -> Path:
    """Write the story with front matter (JSON values are valid YAML) to harness/output/."""
    meta = {k: story.get(k) for k in ("title", "summary_en", "summary_zh", "names", "keywords", "sources")}
    meta = {"title": meta.pop("title"), "genre": genre, **meta, "hsk6_coverage": coverage}
    front = "\n".join(f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in meta.items())
    safe_title = re.sub(r'[\\/:*?"<>|\s]+', "_", story["title"])[:40]
    path = HERE / "output" / f"{time.strftime('%Y%m%d-%H%M%S')}-{safe_title}.md"
    path.parent.mkdir(exist_ok=True)
    path.write_text(f"---\n{front}\n---\n\n# {story['title']}\n\n{story['story_markdown']}\n", encoding="utf-8")
    return path


async def main(args, claude=None) -> dict:
    sys.stdout.reconfigure(encoding="utf-8")
    if not (args.dry_run or claude):   # check the key first, before starting anything
        claude = anthropic.AsyncAnthropic(api_key=load_api_key())
    server =StdioServerParameters(command=sys.executable, args=[str(ROOT / "mcp_server" / "server.py")])
    async with Client(server) as mcp:
        # Steps 1-2: discover the MCP tools and convert them to Claude API tool definitions.
        mcp_tools = (await mcp.list_tools()).tools
        tools = [{"name": t.name, "description": t.description, "input_schema": t.input_schema}
                 for t in mcp_tools] + [SAVE_STORY]
        print(f"MCP server connected. Tools: {', '.join(t['name'] for t in tools)}")
        system = SYSTEM.format(genre=args.genre, chapters=args.chapters, goal=args.coverage) + "\n" + STORY_STYLE
        if args.dry_run:
            print(json.dumps(tools, ensure_ascii=False, indent=2)[:3000], "\n...\n\nSYSTEM PROMPT:\n" + system)
            return {}

        messages = [{"role": "user", "content": args.request}]
        run = {"calls": 0, "input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "cost": 0.0,
               "saved": None, "coverage": None, "retries": 0, "stop": "max_turns"}

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

            # Cost logging: the API tells us exactly how many tokens each call used.
            u, dollars = response.usage, cost_of(response.usage, response.model)
            run["calls"] += 1; run["cost"] += dollars
            run["input"] += u.input_tokens; run["output"] += u.output_tokens
            run["cache_read"] += u.cache_read_input_tokens or 0; run["cache_write"] += u.cache_creation_input_tokens or 0
            print(f"\n[turn {turn}] {response.model}  in={u.input_tokens} cached={u.cache_read_input_tokens or 0} "
                  f"out={u.output_tokens}  ${dollars:.4f}  (total ${run['cost']:.4f})  stop={response.stop_reason}")
            for block in response.content:
                if block.type == "thinking" and block.thinking:
                    print(f"  thinks: {short(block.thinking)}")
                elif block.type == "text":
                    print(f"  says:   {short(block.text, 200)}")

            # Keep Claude's whole reply (thinking + tool calls) in the history, unchanged.
            messages.append({"role": "assistant", "content": response.content})
            if response.stop_reason != "tool_use":   # end_turn, refusal, max_tokens...: stop the loop
                run["stop"] = response.stop_reason
                break

            # Run every tool Claude asked for, then send ALL results back in one user message.
            results = []
            for call in (b for b in response.content if b.type == "tool_use"):
                print(f"  -> {call.name}({short(call.input)})")
                if call.name == "save_story":
                    content, is_error = await handle_save(mcp, call.input, args, run)
                else:   # every other tool lives on the MCP server
                    result = await mcp.call_tool(call.name, call.input)
                    content, is_error = result.content[0].text, bool(result.is_error)
                print(f"  <- {short(content)}")
                results.append({"type": "tool_result", "tool_use_id": call.id, "content": content, "is_error": is_error})
            messages.append({"role": "user", "content": results})

        # Step 5: one line per run in harness/runs.jsonl, so costs can be compared over time.
        print(f"\nDone: {run['calls']} API calls, {run['input']} in / {run['output']} out tokens "
              f"(+{run['cache_read']} cache reads), total ${run['cost']:.4f}. Saved: {run['saved']}")
        log = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "model": args.model, "request": args.request,
               "genre": args.genre, "chapters": args.chapters, **run, "cost": round(run["cost"], 4)}
        with open(HERE / "runs.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(log, ensure_ascii=False) + "\n")
        return run


async def handle_save(mcp, story: dict, args, run: dict) -> tuple[str, bool]:
    """Step 4, the quality gate: save only if HSK 6 coverage meets the goal, or retries ran out."""
    check = await coverage_check(mcp, story)
    run["coverage"] = within = check["within_target"]
    problems = []
    if within < args.coverage:
        hard = "、".join(h["word"] for h in check["above_target"][:25])
        problems.append(f"only {within:.0%} of words are within HSK 6 (goal {args.coverage:.0%}); simplify: {hard}")
    stiff = ai_phrases(story["story_markdown"])
    if stiff:
        problems.append(f"sounds machine-written, say it plainly: {'、'.join(stiff)}")
    if problems and run["retries"] < args.max_retries:
        run["retries"] += 1
        return (f"Not saved: {'; '.join(problems)}. Keep the plot, then call save_story again "
                f"(retry {run['retries']} of {args.max_retries})."), True
    run["saved"] = str(save_markdown(story, args.genre, within))
    note = f" (saved anyway, out of retries: {'; '.join(problems)})" if problems else ""
    return f"saved to {run['saved']}, HSK 6 coverage {within:.0%}{note}", False


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Write a grounded Chinese story with Claude + the HSK Reader MCP server.")
    p.add_argument("request", help='what to write, e.g. "写一个关于后羿射日的故事"')
    p.add_argument("--genre", default="myths", help="genre label for the site, e.g. myths, history, workplace")
    p.add_argument("--chapters", type=int, default=1, help="number of chapters, 700-1,000 characters each")
    p.add_argument("--coverage", type=float, default=0.90, help="share of words that must be within HSK 6")
    p.add_argument("--model", default="claude-opus-5-5", help="Claude model ID")
    p.add_argument("--effort", default="medium", choices=["low", "medium", "high", "xhigh", "max"])
    p.add_argument("--max-turns", type=int, default=12, help="safety limit on loop turns (API calls)")
    p.add_argument("--max-retries", type=int, default=2, help="how often save_story may ask to simplify")
    p.add_argument("--dry-run", action="store_true", help="connect to MCP and show tools; no API call")
    return p.parse_args(argv)


if __name__ == "__main__":
    try:
        asyncio.run(main(parse_args()))
    except* anthropic.APIError as group:   # e.g. wrong key, no credit left, rate limit
        error = group
        while isinstance(error, BaseExceptionGroup):   # async code wraps errors in groups; unwrap
            error = error.exceptions[0]
        sys.exit(f"Claude API error: {error}")
