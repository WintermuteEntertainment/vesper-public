"""MCP server: lets Claude Desktop (or any MCP client) use Vesper's game tools.

The tools are the voice copilot's own, from tools.TOOLS and TOOL_SPECS, offered with an "elite_"
prefix (get_status becomes elite_get_status). Tools marked voice-only there aren't offered. Nothing
here sends input to the game; a few tools change Vesper's notes file, and say so in their hints.

Runs its own journal watcher in a background thread, so answers are always current.

Run:  vesper-mcp        (stdio transport)

stdio carries the protocol on stdout, so nothing in this process may print to stdout. Logging goes
to stderr.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys

import anyio
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from . import __version__
from .markets import MarketRecorder
from .tools import TOOL_SPECS, TOOLS, GameDataError, GameTools, call_tool
from .watcher import Watcher

PREFIX = "elite_"

INSTRUCTIONS = """\
Live data from the player's Elite Dangerous game, read from the game's own journal files.
Use elite_get_status first for where the commander is and what they're flying. Local game
data: missions, recent events, crimes, session summary, loadout, materials, commander profile,
route details, exploration scans, bindings, and the docked station's live market. Galaxy data:
markets and best prices, stations, ships and modules for sale, routes (Spansh); system info,
bodies and stations (EDSM); GalNet news (Frontier). Galaxy searches default to the commander's
current system, jump range and pad size. Station data comes from player uploads, so check the
updated dates. Notes, reminders, the shopping list and the home base are shared with the voice
copilot; those are the only tools that change anything."""

# What each MCP kind in tools.TOOLS tells the client about a tool.
ANNOTATIONS = {
    "local": types.ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True,
                                   open_world_hint=False),
    "web": types.ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True,
                                 open_world_hint=True),
    "write": types.ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False,
                                   open_world_hint=False),
    "delete": types.ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=True,
                                    open_world_hint=False),
}


def mcp_tools() -> list[types.Tool]:
    """Every tool offered over MCP, built from TOOL_SPECS: the same descriptions and schemas Claude
    sees in the voice copilot."""
    out = []
    for spec in TOOL_SPECS:
        kind = TOOLS[spec["name"]][1]
        if kind:
            out.append(types.Tool(name=PREFIX + spec["name"], description=spec["description"],
                                  input_schema=spec["input_schema"], annotations=ANNOTATIONS[kind]))
    return out


def _result(out, error: bool = False) -> types.CallToolResult:
    """A tool's answer as JSON text, plus structured content when it is a dict."""
    if error:
        return types.CallToolResult(content=[types.TextContent(type="text", text=str(out))], is_error=True)
    text = json.dumps(out, default=str)
    structured = json.loads(text) if isinstance(out, dict) else None
    return types.CallToolResult(content=[types.TextContent(type="text", text=text)], structured_content=structured)


async def call(tools: GameTools, offered: set[str], name: str, arguments: dict | None) -> types.CallToolResult:
    """Run one MCP tool call in a worker thread. Unknown tools, bad arguments and game-data problems
    come back as tool errors the model can read."""
    if name not in offered:
        return _result(f"Unknown tool {name!r}.", error=True)
    try:
        out = await asyncio.to_thread(call_tool, tools, name.removeprefix(PREFIX), dict(arguments or {}))
    except GameDataError as e:
        return _result(e, error=True)
    return _result(out)


def create_server(watcher: Watcher) -> Server:
    """The MCP server: lists the registry's MCP tools and runs them in a worker thread."""
    tools = GameTools(watcher)
    listed = mcp_tools()
    offered = {t.name for t in listed}

    async def list_tools(ctx, params) -> types.ListToolsResult:
        """tools/list: the same list every time."""
        return types.ListToolsResult(tools=listed)

    async def run_tool(ctx, params: types.CallToolRequestParams) -> types.CallToolResult:
        """tools/call."""
        return await call(tools, offered, params.name, params.arguments)

    return Server("vesper", version=__version__, instructions=INSTRUCTIONS,
                  on_list_tools=list_tools, on_call_tool=run_tool)


def main() -> None:
    """Run the MCP server over stdio, with its own watcher."""
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    watcher = Watcher()
    MarketRecorder(watcher, eddn=False)  # cache markets; only the copilot uploads, so nothing is sent twice
    watcher.catch_up()  # read today's journal first, so the first question already has the game state
    watcher.start(catch_up=False)
    server = create_server(watcher)

    async def serve() -> None:
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    anyio.run(serve)


if __name__ == "__main__":
    main()
