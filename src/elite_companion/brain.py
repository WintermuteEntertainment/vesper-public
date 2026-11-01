"""The copilot's brain: answers spoken questions with Claude, using the game tools.

Cost guardrails: every request's token usage is priced and logged to
~/.vesper/usage.jsonl, and questions stop for the day once the
estimated spend reaches daily_budget_usd.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from datetime import date, datetime
from pathlib import Path

import anthropic

from .language import answer_rule
from .config import HOME
from .memory import describe_note
from .tools import TOOL_SPECS, GameDataError, GameTools, call_tool

log = logging.getLogger(__name__)
USAGE_LOG = HOME / "usage.jsonl"
MAX_TOOL_ROUNDS = 6

# USD per million tokens: (input, output). Cache writes cost 1.25x input, reads 0.1x.
PRICES = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
}
# Galaxy searches that can take a while, and what to say while they run.
SLOW_TOOLS = {
    "find_service": "Searching, this can take half a minute.",
    "plot_route": "Plotting, one moment.",
    "trade_between": "Comparing markets, one moment.",
    "find_best_price": "Checking prices, one moment.",
    "best_trades": "Checking the best trades, give me half a minute.",
    "trades_along_missions": "Checking trades along your missions, this can take a minute.",
    "write_captains_log": "Writing up your log, give me half a minute.",
    "find_mining_hotspots": "Looking for hotspots, one moment.",
    "web_search": "Searching the web, one moment.",
    "plan_mining_run": "Planning that run, give me half a minute.",
    "get_hold_value": "Pricing your hold, one moment.",
    "find_home_base": "That's a big search. Give me up to a couple of minutes.",
}
# Questions that ask for the stronger model outright.
HARD_PREFIX = re.compile(r"^\W*(think (hard|harder|carefully|it through)|ask (sonnet|the big (model|brain))|"
                         r"take your time|big brain)\b[\s,:.-]*", re.I)
THINK_HARDER_SPEC = {
    "name": "think_harder",
    "description": "Hand this question to a stronger model. Use it when the question needs real planning or "
                   "judgement: comparing several options, a multi-stop plan, engineering or build advice, "
                   "working out why something happened, or when you're unsure and a wrong answer would cost the commander. "
                   "Never for simple lookups, status, prices or notes. Its answer is returned to you: say it as is.",
    "input_schema": {"type": "object", "properties": {"reason": {"type": "string"}}},
}
LOCAL_RULE = ("\n\nYOU ARE THE LOCAL MODEL ({model}, running on the commander's own hardware), not Claude and not "
              "the cloud. If asked, say so plainly; saying \"go cloud\" switches back. You can't switch modes yourself."
              "\n\nTOOL RULE: the live line below only covers location, ship, fuel, hold, threats and notes. "
              "For anything else (missions, prices, stations, loadout, routes, materials, locker) call a tool "
              "first. Never state a number that didn't come from a tool result or the live line.\n\n")
# Tools a local model doesn't get: they send screenshots, and most local models can't read images.
LOCAL_BLOCKED = {"look_at_screen", "read_contacts"}
WEB_SEARCH_NEW = {"claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5", "claude-sonnet-5", "claude-fable-5-1",
                  "claude-fable-5"}
WEB_SEARCH_USD = 0.01  # per search ($10 per 1,000)
FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5"}  # models that take server-side refusal fallbacks

SYSTEM = """\
You are the ship's co-pilot for a commander in Elite Dangerous. The commander is busy flying (often with a \
HOTAS or in VR), so everything you write is read aloud by a text-to-speech voice.

What you are, when asked: Vesper, an open-source voice co-pilot running on the commander's PC. Your answers \
come from Anthropic's Claude API: a fast model for everyday questions, and a stronger one when a question \
starts with "think hard" or you hand it over with think_harder. In local mode ("go local") a model on the \
commander's own hardware answers instead. Speech is turned into text on the PC with Whisper, and Windows reads \
your answers aloud. Say this plainly; never claim not to know.

How to answer:
- Speak like a calm wingman on comms. One to three short sentences unless asked for more.
- Plain spoken English only: no markdown, lists, symbols or emoji. Round numbers the way a person \
says them ("about 4.3 million credits", "17 light years").
- Write "co-pilot" with a hyphen, never "copilot".
- Lead with the answer. Don't recap the question or describe which tools you used.
- Answer every part of the question; call several tools at once when it has several parts.
- Each question starts with a "(Live: ...)" line from the game: it is the commander's real current location \
and ship, so rely on it and never hedge about where they are.
- Use the tools for anything about the game or the galaxy; never guess stations, prices, stock, routes or \
mission details. Game data comes from the live journal. Galaxy data (markets, stations, ships, modules, \
bodies) comes from player uploads, so mention it if the updated date is more than a few days old. "Where I'm \
going" means the targeted station or route end (get_market where=destination).
- Speech recognition mishears names. "Seoul" means Sol. If a name doesn't exist, try the closest-sounding \
name from the live line, the missions or what you just said before saying you can't find it.
- System and station names are often long codes (Col 285 Sector FY-O b20-2). Say them exactly.
- You can only read game data. You can't fly the ship or change anything in game. The one key you can \
press is Ctrl+V (paste_clipboard).
- When the commander is telling you something ("I'm gaining on them"), acknowledge it briefly. Don't coach \
or suggest tactics unless asked.

Memory and conversation:
- Replies are spoken, printed in a terminal window, and saved to a daily transcript (~/.vesper/logs).
- remember, recall_notes and forget_note keep notes between sessions. Reminders are spoken automatically \
on docking at or arriving in the place, at the next start, or at a set time. On "remember" or "remind me", \
save it with remember and confirm in a few words. Saved notes appear in the live line.
- "Remind me when I land/dock/get there" at a station means when='dock' with that station's name.
- The conversation lasts until about 15 minutes of silence or a restart, so anything worth keeping belongs \
in a note. For something said earlier that you don't have, use recent_conversation (or \
search_conversations for older days) instead of saying you don't remember.
- Talk about notes by what they're about ("your black market reminder"), never by number.
- Your name and the commander's standing preferences are in the live line: use the name and follow every \
preference. When named or asked to pick a name, save it with set_my_name. When told to stop or always do \
something, save it with add_preference.

Navigation and names:
- Whenever you name a system the commander might fly to, copy it exactly with copy_to_clipboard and say \
"copied to your clipboard". When asked for "the system" while heading somewhere, copy the destination's \
system (a mission's go_to_system, or the station named), never the current one. Say the name you copied so \
a mistake can be caught. To repeat or spell a system code, use the NATO alphabet and say digits one by one \
("RZ-F" becomes "Romeo Zulu dash Foxtrot").
- After you copy a system, the commander can say "paste" and Vesper presses Ctrl+V (for the galaxy map \
search). If they say it didn't work, believe them: suggest clicking into the search box first, or pressing \
Ctrl+V themselves. Never claim it worked.
- Station distances from the star are approximate (stations orbit planets): say "about". Surface stations \
(on_body) only show near their planet or moon; say which body. Flag stale_data.

Threats:
- Never volunteer old threats. Mention a hostile pilot only when the live line says ACTIVE THREAT, or when \
asked.
- You can't see other ships' positions, ranges or speeds: the journal doesn't record them. Say so in one \
sentence, then give what you do know: hostile hails, interdictions, the locked target's ship, rank, shields, \
hull and bounty (get_threats), and whether that pilot has killed the commander before.
- Hull figures come from the last time the game logged damage, which it does only in big steps. If the \
commander reports a different number, their HUD is right.

Ships, engineering and trade:
- Ships: get_ship_details for any of their ships, including ones not being flown. Stored ships and parts: \
get_stored_ships and get_stored_modules (as of the last shipyard/outfitting visit). get_outfitting_here for \
what this station sells (outfitting must have been opened).
- Engineering: find_engineer for who engineers what, where they are, the commander's status with them and \
how to unlock them. Never name an engineer from memory. "Sarge" means The Sarge, not Colonel Bris Dekker. \
check_engineering_materials for "can I do grade 4 ..." or "what do I need for ...". Keep the material \
shopping list with shopping_list.
- Engineering materials (Selenium, Arsenic, Polymer Capacitors and so on) are not market commodities: they \
come from material traders, mining, salvage and scanning. The market tools say when a name is a material.
- Selling: plan_sale for where to sell the hold (one or two stops). If one stop loses more than about 10%, \
recommend splitting. Before saying a station doesn't buy something, check get_recent_events: it may have \
been sold there already.
- Bindings: find_binding for "what's bound to ..." or "what does this button do".

Mining:
- plan_mining_run for what and where to mine (hotspot, market and profit per hour), get_hold_value for what \
the hold is worth, find_mining_hotspots for where to mine, get_mining_session for how it's going, \
check_mining_fit for the ship. Laser mining needs a refinery, prospector and collector limpets and mining \
lasers; core mining also needs seismic charges and a Pulse Wave Analyser to spot cores; sub-surface needs \
displacement missiles. You can't see subsurface deposits or fissures: they only show in the cockpit. Ring \
hotspots only appear after the ring (or its planet) is mapped with a Detailed Surface Scanner. Resource \
Extraction Sites are combat sites for bounty hunting, not hotspots. To sell mined goods, use find_best_price \
with selling, and mind demand: a full hold can crash a small market.

Screen:
- look_at_screen when asked to look at something or read the screen.
- read_contacts reads the Contacts panel into a list (who's near, wanted, ranks). If it says the list isn't \
visible, ask the commander to open the Contacts tab and ask again.

Web:
- web_search (and fetch_page to read a result), when available: only for game facts your tools don't cover \
(unlock requirements, game rules, patch changes, events). One or two searches, answer in a sentence or two, \
and say it came from the web.

Game rules you must get right:
- Cargo moves with the commander when swapping ships at a station; if the new ship can't hold it, the game \
won't allow the swap.
- Cargo a mission gives is tagged to that mission. If it's lost (death, jettison), cargo bought on the market \
does NOT count toward the mission, and the mission depot does not reissue it.
- Submitting to an interdiction costs no cargo by itself.
- Missions are handed in from the station's mission board (cargo deliveries through its mission depot), not \
through Contacts.
- "Sells to you" is the buy side of the market screen; "buys from you" is the sell side.

What you can do, if asked: everything in your tools list. If the question isn't about Elite Dangerous, \
answer briefly anyway."""


def _clip(text: str, n: int) -> str:
    """Shorten a note for the context line, pointing at recall_notes for the rest."""
    return text if len(text) <= n else text[: n - 1] + "… (truncated; use recall_notes for the full note)"


class BudgetExceeded(RuntimeError):
    """Today's spending limit ([claude] daily_budget_usd) is used up."""
    pass


def has_credentials() -> bool:
    """True if an API key is available. Also checks the Windows user variables
    directly: apps started before the key was set (like a terminal inside an
    already-running app) don't inherit it."""
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return True
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, _ = winreg.QueryValueEx(key, "ANTHROPIC_API_KEY")
    except OSError:
        return False
    if value:
        os.environ["ANTHROPIC_API_KEY"] = value
    return bool(value)


def request_cost(model: str, usage) -> float:
    """What one API response cost in US dollars: input, cache writes (1.25x) and reads (0.1x),
    output, and server-side web searches.
    """
    inp, out = PRICES.get(model, PRICES["claude-opus-5-5"])
    cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    server = getattr(usage, "server_tool_use", None)
    searches = (getattr(server, "web_search_requests", 0) or 0) if server else 0
    return (usage.input_tokens * inp + cache_write * inp * 1.25 + cache_read * inp * 0.1
            + usage.output_tokens * out) / 1_000_000 + searches * WEB_SEARCH_USD


def spent_today(log_path: Path = USAGE_LOG) -> float:
    """Dollars spent today, summed from the usage log."""
    today = date.today().isoformat()
    total = 0.0
    if log_path.exists():
        for line in log_path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("date") == today:
                total += row.get("cost_usd", 0)
    return total


class Brain:
    """Answers spoken questions with Claude (or a local model) and the game tools.

    Each question gets a live context line (where, ship, cargo, notes) and runs a tool loop until
    Claude answers in words. The conversation resets after max_history_turns or idle_reset_minutes
    of silence. A "think harder" prefix (or the think_harder tool) hands the question to hard_model.
    extra_tools adds copilot-only tools: {name: (spec, function)}.
    """
    def __init__(self, tools: GameTools, model: str = "claude-opus-5-5", effort: str = "low",
                 daily_budget_usd: float = 1.0, max_history_turns: int = 40, client=None,
                 usage_log: Path = USAGE_LOG, idle_reset_minutes: float = 15, on_slow_tool=None,
                 extra_tools: dict | None = None, hard_model: str = "", hard_effort: str = "medium",
                 local: dict | None = None, web_search: str | bool = "off", search: dict | None = None,
                 language: str = "en"):
        self.tools, self.model, self.effort = tools, model, effort
        self.language = language
        self.hard_model, self.hard_effort = hard_model, hard_effort
        # "anthropic": the API's paid server-side search. "searxng": free search on your own SearXNG, as
        # ordinary tools (so local mode can search too). Old configs said True/False.
        self.web_search = {True: "anthropic", False: "off"}.get(web_search, web_search) or "off"
        self.search_cfg = search or {}
        if self.web_search == "searxng" and not self.search_cfg.get("searxng_url"):
            log.warning("web_search is searxng but [search] searxng_url is empty: web search is off")
            self.web_search = "off"
        self.local_cfg = local or {}
        self.local_mode = bool(self.local_cfg.get("start_local"))
        self.local_history: list[dict] = []
        self.budget, self.max_turns = daily_budget_usd, max_history_turns
        self.client = client or anthropic.Anthropic(max_retries=2, timeout=60)
        self.usage_log = usage_log
        self.history: list[dict] = []
        self.turns = 0
        self.idle_reset_s = idle_reset_minutes * 60
        self.on_slow_tool = on_slow_tool  # e.g. speak "Searching" so a long galaxy search isn't dead air
        # Co-pilot-only actions (name -> (spec, function)), e.g. paste_clipboard; not in the shared game tools.
        self.extra_tools = dict(extra_tools or {})
        if self.web_search == "searxng":
            from . import websearch
            self.extra_tools["web_search"] = (websearch.WEB_SEARCH_SPEC, self._free_search)
            self.extra_tools["fetch_page"] = (websearch.FETCH_PAGE_SPEC, self._fetch_page)
        self.last_asked = time.monotonic()

    def reset(self) -> None:
        """Forget the conversation (Claude and local)."""
        self.history, self.local_history, self.turns = [], [], 0

    def _log_usage(self, usage, question: str, model: str | None = None) -> float:
        """Append one response's tokens and cost to the usage log; returns the cost."""
        model = model or self.model
        cost = request_cost(model, usage)
        self.usage_log.parent.mkdir(parents=True, exist_ok=True)
        with open(self.usage_log, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "date": date.today().isoformat(), "time": datetime.now().isoformat(timespec="seconds"),
                "model": model, "input": usage.input_tokens, "output": usage.output_tokens,
                "cache_read": getattr(usage, "cache_read_input_tokens", 0) or 0,
                "cache_write": getattr(usage, "cache_creation_input_tokens", 0) or 0,
                "cost_usd": round(cost, 6), "question": question[:200],
            }) + "\n")
        return cost

    def _context_line(self) -> str:
        """The live state line put in front of each question: where, ship, fuel, cargo, saved
        notes and so on, so simple questions need no tool call.
        """
        try:
            s = self.tools.status()
        except GameDataError:
            return "(No live game data yet.)"
        loc, ship = s["location"], s["ship"]
        where = loc.get("station") if loc.get("docked") else (loc.get("body") or "in space")
        mode = "docked" if loc.get("docked") else "supercruise" if loc.get("supercruise") else "normal space"
        notes = self.tools.notes.list()
        if not notes:
            memo = "Saved notes: none."
        elif len(notes) <= 6:
            memo = "Saved notes: " + " | ".join(_clip(describe_note(n), 400) for n in notes) + "."
        else:
            memo = f"{len(notes)} saved notes (use recall_notes)."
        cargo = ", ".join(f"{n} {c}" for n, c in s["owned_cargo"].items() if c) or "empty"
        mission_cargo = ", ".join(f"{c['commodity']} {c['count']}" for c in s["mission_cargo"])
        lost = [m["name"] for m in self.tools.missions()["missions"] if m["status"].startswith("MISSION CARGO LOST")]
        cargo_line = (f"Hold: {cargo}" + (f"; mission cargo: {mission_cargo}" if mission_cargo else "")
                      + (f"; mission cargo LOST for: {'; '.join(lost)}" if lost else "") + ".")
        turn_ins = self.tools.turn_ins()
        if turn_ins:
            cargo_line += f" Missions ready to turn in at: {'; '.join(turn_ins)}."
        prof = self.tools.notes.profile()
        me = f"Your name: {prof['name']}. " if prof.get("name") else ""
        prefs = ("Standing preferences: " + " | ".join(p.rstrip(".") for p in prof["preferences"]) + ". ") if prof.get("preferences") else ""
        threat = self.tools.threat_summary()
        refits = self.tools.recent_refits(minutes=15)
        if refits:
            threat += f" Just refitted {refits[-1]['ship']}: " + "; ".join(refits[-1]["changes"][:6]) + "."
        fuel = s["fuel"].get("main")
        fuel_txt = (f"Fuel: {fuel:.1f} of {ship['fuel_capacity']:.0f} tons. "
                    if fuel is not None and ship.get("fuel_capacity") else "")
        return (f"(Live: {loc.get('system')}, {where}, {mode}. Ship: {ship.get('type')} '{ship.get('name')}'. {fuel_txt}"
                f"Legal: {s.get('legal_state')}. Game running: {s.get('game_running')}. {cargo_line} "
                f"{threat + ' ' if threat else ''}Local time {datetime.now():%H:%M}. {me}{prefs}{memo})")

    def _system(self) -> str:
        """The name goes first in the instructions: the model skipped it in the live line. It only
        changes on a rename, so the prompt cache still holds."""
        name = self.tools.notes.profile().get("name")
        try:
            cmdr = self.tools.status().get("commander")
        except Exception:  # no journal yet: the prompt works without the name
            cmdr = None
        return ((f"Your name is {name}; answer to it and use it when asked who you are.\n" if name else "")
                + (f"The commander is CMDR {cmdr}.\n" if cmdr else "") + answer_rule(self.language)
                + ("\n" if name or cmdr or self.language != "en" else "") + SYSTEM)

    def _tool_specs(self, model: str) -> list[dict]:
        """Every tool this model may call: game tools, copilot extras, the server web search when
        [claude] web_search is "anthropic", and think_harder unless this already is the hard model.
        """
        specs = TOOL_SPECS + [spec for spec, _ in self.extra_tools.values()]
        if self.web_search == "anthropic":
            # Server-side search run by the API. Opus/Sonnet 5.x take the newer version with result filtering.
            kind = "web_search_20260209" if model in WEB_SEARCH_NEW else "web_search_20250305"
            specs = specs + [{"type": kind, "name": "web_search", "max_uses": 3}]
        return specs + [THINK_HARDER_SPEC] if self.hard_model and model != self.hard_model else specs

    def _create(self, messages: list, model: str | None = None, effort: str | None = None):
        """One Messages API call with the system prompt, tools and prompt caching. Models that support
        it get server-side fallback, so a declined request is retried on another model.
        """
        model = model or self.model
        kwargs = dict(
            model=model, max_tokens=4000, system=self._system(),
            tools=self._tool_specs(model), messages=messages,
            output_config={"effort": effort or self.effort}, cache_control={"type": "ephemeral"},
        )
        if model in FALLBACK_MODELS:
            # Server-side fallback: if a safety classifier declines, the API retries on a suitable model.
            return self.client.beta.messages.create(betas=["server-side-fallback-2026-07-01"],
                                                    fallbacks="default", **kwargs)
        return self.client.messages.create(**kwargs)

    def _free_search(self, query: str) -> dict:
        """The web_search tool when [claude] web_search is "searxng": the commander's own SearXNG."""
        from . import websearch
        try:
            return {"query": query, "results": websearch.search(query, self.search_cfg)}
        except websearch.SearchError as e:
            return {"error": str(e)}

    def _fetch_page(self, url: str) -> dict:
        """The read_web_page tool: a web page as plain text."""
        from . import websearch
        try:
            return websearch.fetch_page(url)
        except websearch.SearchError as e:
            return {"error": str(e)}

    def _ask_hard(self, prompt: str, question: str, history: list[dict] | None = None) -> str:
        """One question to the stronger model, with the conversation so far as plain text. Its own tool
        calls stay inside this call; only the answer comes back."""
        messages = _plain(history if history is not None else self.history) + [{"role": "user", "content": prompt}]
        if messages[0]["role"] != "user" or len(messages) > 1 and messages[-2]["role"] == "user":
            messages = [{"role": "user", "content": prompt}]
        said = ""
        for _ in range(MAX_TOOL_ROUNDS):
            response = self._create(messages, self.hard_model, self.hard_effort)
            self._log_usage(response.usage, "[hard] " + question, self.hard_model)
            messages.append({"role": "assistant", "content": response.content})
            if response.stop_reason == "refusal":
                return "I can't help with that one."
            if response.stop_reason == "pause_turn":
                continue
            tool_uses = [b for b in response.content if b.type == "tool_use"]
            said = " ".join(b.text for b in response.content if b.type == "text").strip() or said
            if response.stop_reason != "tool_use" or not tool_uses:
                return said or "I don't have an answer for that."
            slow = [b.name for b in tool_uses if b.name in SLOW_TOOLS]
            if slow and self.on_slow_tool:
                self.on_slow_tool(SLOW_TOOLS[slow[0]])
            messages.append({"role": "user", "content": [self._run_tool(b) for b in tool_uses]})
        return said or "That took too many steps; try asking a simpler way."

    def _run_tool(self, block) -> dict:
        """Run one tool call and wrap the result for the API. A screenshot result (_image) goes back as
        an image; game-data errors go back as is_error results Claude can explain.
        """
        try:
            if block.name in self.extra_tools:
                out = self.extra_tools[block.name][1](**dict(block.input or {}))
            else:
                out = call_tool(self.tools, block.name, dict(block.input or {}))
            if isinstance(out, dict) and out.get("_image"):  # a screenshot: hand Claude the picture itself
                rest = {k: v for k, v in out.items() if k != "_image"}
                return {"type": "tool_result", "tool_use_id": block.id, "content": [
                    {"type": "text", "text": json.dumps(rest, default=str)},
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": out["_image"]}}]}
            return {"type": "tool_result", "tool_use_id": block.id, "content": json.dumps(out, default=str)}
        except (GameDataError, TypeError) as e:
            return {"type": "tool_result", "tool_use_id": block.id, "content": str(e), "is_error": True}

    # ---- local mode: an OpenAI-compatible server instead of Claude ----

    def set_local(self, on: bool) -> str:
        """Switch between Claude and the local model ("go local" / "back to Claude")."""
        if on and not self.local_cfg.get("base_url"):
            return "No local model is set up in config."
        self.local_mode = on
        self.reset()
        return "Local mode." if on else "Back on Claude."

    def warm_local(self) -> bool:
        """Load the local model (llama-swap loads on first request; can take half a minute)."""
        try:
            self._local_post([{"role": "user", "content": "Say OK."}], tools=False, max_tokens=5)
            return True
        except (OSError, KeyError, ValueError):
            log.exception("local model warm-up failed")
            return False

    def _local_post(self, messages: list[dict], tools: bool = True, max_tokens: int = 1000) -> dict:
        """One chat-completions call to the local OpenAI-compatible server, with the tools as
        functions (minus LOCAL_BLOCKED). Logs tokens at no cost.
        """
        cfg = self.local_cfg
        body = {"model": cfg["model"], "messages": messages, "max_tokens": max_tokens, "temperature": 0.3}
        if tools:
            body["tools"] = [{"type": "function", "function": {"name": s["name"], "description": s["description"],
                                                               "parameters": s["input_schema"]}}
                             for s in TOOL_SPECS + [spec for name, (spec, _) in self.extra_tools.items()
                                                    if name not in LOCAL_BLOCKED]]
        req = urllib.request.Request(cfg["base_url"].rstrip("/") + "/chat/completions",
                                     data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=cfg.get("timeout_seconds", 120)) as r:
            data = json.loads(r.read())
        u = data.get("usage") or {}
        self.usage_log.parent.mkdir(parents=True, exist_ok=True)
        with open(self.usage_log, "a", encoding="utf-8") as f:
            f.write(json.dumps({"date": date.today().isoformat(), "time": datetime.now().isoformat(timespec="seconds"),
                                "model": "local:" + cfg["model"], "input": u.get("prompt_tokens", 0),
                                "output": u.get("completion_tokens", 0), "cost_usd": 0}) + "\n")
        return data["choices"][0]["message"]

    def _ask_local(self, question: str) -> str:
        """Answer with the local model: the same tool loop as Claude, over the OpenAI chat format."""
        idle = time.monotonic() - self.last_asked > self.idle_reset_s
        if self.turns >= self.max_turns or idle:
            self.reset()
        self.turns += 1
        self.last_asked = time.monotonic()
        # The live line goes in the system message: inside the question, local models answer from it
        # (and invent the rest) instead of calling tools.
        system = {"role": "system", "content": self._system() + LOCAL_RULE.format(model=self.local_cfg.get("model") or "a local model")
                  + self._context_line()}
        messages = self.local_history + [{"role": "user", "content": question}]
        said = ""
        try:
            for _ in range(MAX_TOOL_ROUNDS):
                msg = self._local_post([system] + messages)
                calls = msg.get("tool_calls") or []
                text = re.sub(r"<think>.*?</think>", "", msg.get("content") or "", flags=re.S).strip()
                said = text or said
                messages.append({"role": "assistant", "content": msg.get("content") or "",
                                 **({"tool_calls": calls} if calls else {})})
                if not calls:
                    self.local_history = messages
                    return said or "I don't have an answer for that."
                slow = [c["function"]["name"] for c in calls if c["function"]["name"] in SLOW_TOOLS]
                if slow and self.on_slow_tool:
                    self.on_slow_tool(SLOW_TOOLS[slow[0]])
                for c in calls:
                    name = c["function"]["name"]
                    try:
                        args = json.loads(c["function"].get("arguments") or "{}")
                        if name in self.extra_tools:
                            out = json.dumps(self.extra_tools[name][1](**args), default=str)
                        else:
                            out = json.dumps(call_tool(self.tools, name, args), default=str)
                    except (GameDataError, TypeError, ValueError) as e:
                        out = f"Error: {e}"
                    messages.append({"role": "tool", "tool_call_id": c.get("id", ""), "content": out[:12000]})
        except OSError as e:  # URLError, HTTPError and timeouts
            log.warning("local model failed: %s", e)
            self.local_history = []
            return "I can't reach the local model. Say go cloud to switch back to Claude."
        self.local_history = []
        return "That took too many steps; try asking a simpler way."

    def ask(self, question: str) -> str:
        """Answer one question in words to speak. Goes local in local mode, or to hard_model for
        "think harder" questions. Raises BudgetExceeded when today's budget is spent.
        """
        if self.local_mode:
            return self._ask_local(question)
        hard = HARD_PREFIX.match(question)
        if hard and self.hard_model:
            question = question[hard.end():] or question
        if spent_today(self.usage_log) >= self.budget:
            raise BudgetExceeded(f"Today's Claude budget of {self.budget:.2f} dollars is used up.")
        idle = time.monotonic() - self.last_asked > self.idle_reset_s
        if self.turns >= self.max_turns or idle:
            self.reset()  # start fresh rather than trimming (editing history breaks thinking blocks)
        self.turns += 1
        self.last_asked = time.monotonic()
        prompt = f"{self._context_line()}\n{question}"
        if hard and self.hard_model:
            answer = self._ask_hard(prompt, question)
            # Keep the exchange in the everyday conversation as plain text.
            self.history = self.history + [{"role": "user", "content": prompt},
                                           {"role": "assistant", "content": answer}]
            return answer
        messages = self.history + [{"role": "user", "content": prompt}]
        said = ""  # the answer may come alongside a tool call (e.g. copy_to_clipboard), not after it
        for _ in range(MAX_TOOL_ROUNDS):
            response = self._create(messages)
            self._log_usage(response.usage, question)
            messages.append({"role": "assistant", "content": response.content})
            if response.stop_reason == "refusal":
                self.reset()
                return "I can't help with that one."
            if response.stop_reason == "pause_turn":
                continue  # web search still running server-side: re-send as is and it resumes
            tool_uses = [b for b in response.content if b.type == "tool_use"]
            text = " ".join(b.text for b in response.content if b.type == "text").strip()
            said = text or said
            if response.stop_reason != "tool_use" or not tool_uses:
                self.history = messages
                return said or "I don't have an answer for that."
            results = []
            slow = [b.name for b in tool_uses if b.name in SLOW_TOOLS]
            if slow and self.on_slow_tool:
                self.on_slow_tool(SLOW_TOOLS[slow[0]])
            for block in tool_uses:
                if block.name == "think_harder":
                    if self.on_slow_tool:
                        self.on_slow_tool("Let me think about that one properly.")
                    try:
                        answer = self._ask_hard(prompt, question, self.history)
                        content = f"Answer from the stronger model. Say it as is, nothing added:\n{answer}"
                    except anthropic.APIError as e:
                        content = f"The stronger model failed ({e.__class__.__name__}); answer as best you can."
                    results.append({"type": "tool_result", "tool_use_id": block.id, "content": content})
                    continue
                results.append(self._run_tool(block))
            messages.append({"role": "user", "content": results})
            if spent_today(self.usage_log) >= self.budget:
                self.reset()
                raise BudgetExceeded("I hit today's budget partway through that one.")
        self.reset()
        return "That took too many steps; try asking a simpler way."


def _plain(history: list[dict]) -> list[dict]:
    """The conversation as plain alternating text turns: no tool calls or thinking, so another model
    (or another provider) can read it."""
    out: list[dict] = []
    for m in history:
        c = m["content"]
        if isinstance(c, str):
            text = c
        else:
            text = " ".join(getattr(b, "text", None) or (b.get("text", "") if isinstance(b, dict) and b.get("type") == "text" else "")
                            for b in c).strip()
        if not text:
            continue
        if out and out[-1]["role"] == m["role"]:
            out[-1]["content"] += "\n" + text
        else:
            out.append({"role": m["role"], "content": text})
    while out and out[0]["role"] != "user":
        out.pop(0)
    return out


def spoken_error(e: Exception) -> str:
    """Short spoken explanations for API failures."""
    if isinstance(e, BudgetExceeded):
        return str(e)
    if isinstance(e, anthropic.AuthenticationError):
        return "My Claude API key isn't working. Check ANTHROPIC_API_KEY."
    if isinstance(e, anthropic.RateLimitError):
        return "Claude is rate limiting me. Try again in a minute."
    if isinstance(e, anthropic.APIConnectionError):
        return "I can't reach Claude right now."
    if isinstance(e, anthropic.APIStatusError):
        return f"Claude returned an error, status {e.status_code}."
    log.exception("copilot error")
    return "Something went wrong with that question."
