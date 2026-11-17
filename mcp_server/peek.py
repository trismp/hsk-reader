"""See MCP from the AI app's side: start our server and talk to it.

This is exactly what Claude Code does behind the scenes when it connects:
  1. launch the server as a subprocess
  2. ask for its tool list   (what the model gets to read)
  3. call a tool             (what happens when the model asks to use one)

Run:  .venv/Scripts/python.exe mcp_server/peek.py                 # list tools + demo call
      .venv/Scripts/python.exe mcp_server/peek.py search_library '{"query": "嫦娥 不死药", "k": 2}'
"""
import asyncio
import json
import sys
from pathlib import Path

from mcp import Client, StdioServerParameters

ROOT = Path(__file__).resolve().parent.parent


def show(title: str, data) -> None:
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")
    print(json.dumps(data, ensure_ascii=False, indent=2))


async def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    params = StdioServerParameters(
        command=sys.executable,                      # this venv's python
        args=[str(ROOT / "mcp_server" / "server.py")],
    )
    tool = sys.argv[1] if len(sys.argv) > 1 else "check_hsk_level"
    args = (json.loads(sys.argv[2]) if len(sys.argv) > 2 else
            {"text": "后来，王母娘娘送给后羿一种不死药，他把药交给嫦娥保管。", "target_level": 4})

    async with Client(params) as client:
        # Step 1: discovery. This is ALL the model knows about our tools.
        tools = await client.list_tools()
        if len(sys.argv) == 1:
            show("1) tools/list: what the model sees",
                 [t.model_dump(by_alias=True, exclude_none=True,
                               include={"name", "description", "input_schema"})
                  for t in tools.tools])
        else:
            print("Tools available:", ", ".join(t.name for t in tools.tools))

        # Step 2: the model decides to call a tool with some arguments.
        show(f"2) tools/call {tool}: arguments the model sends", args)
        result = await client.call_tool(tool, args)

        # Step 3: the result that goes back into the model's context.
        # On the wire it's a text block containing JSON; we decode it to read it.
        show("3) result returned to the model (decoded)",
             result.structured_content or json.loads(result.content[0].text))


if __name__ == "__main__":
    asyncio.run(main())
