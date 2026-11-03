"""Everything Vesper says by herself, in English, with translations from locales/<code>.json.

Callouts, lookout and mining readouts, reminders, command replies and spoken errors all come from
here through t("key", ...). Claude's own answers don't: Claude is simply told which language to use
(see language.py).

A locale file has:
    "_meta"     the language's name and who wrote or checked the translation
    "phrases"   key -> text, overriding EN below. Anything missing falls back to English.
    "commands"  the short fixed commands in that language: action -> whole phrases ("silence")
    "padding"   polite words a command may come with ("please", "okay")

Plural phrases are {"one": ..., "other": ...}; Russian and Polish also use "few" and "many".
Placeholders in braces are filled in by the code; a translation may leave some out.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)
LOCALES = Path(__file__).parent / "locales"

EN: dict[str, str | dict[str, str]] = {
    # ---- callouts ----
    "hostile_hail": "Warning. Hostile hail from {pilot}.",
    "unknown_pilot": "unknown pilot",
    "interdiction": "Interdiction!",
    "shields_down": "Shields down.",
    "hull": "Hull {percent} percent.",
    "under_attack": "Under attack.",
    "low_fuel": "Fuel low.",
    "overheating": "Overheating!",
    "objective_done": "Objective complete. Return to {station}.",
    "destination": "Destination reached. {system}.",
    "jumps_left": {"one": "{n} jump remaining.", "other": "{n} jumps remaining."},
    "escaped": "Escaped.",
    "docking_granted": "Docking granted.",
    "docking_denied": "Docking denied.",
    "first_discovery": "First discovery. {body}.",
    "crime": "Crime: {crime}.",
    "fuel_full": "Tank full.",
    # ---- lookout ----
    "mission_expiring": "Heads up: {mission}{where} expires in {time}.",
    "a_mission": "a mission",
    "in_system": " in {system}",
    "a_minute": "a minute",
    "minutes": {"one": "{n} minute", "other": "{n} minutes"},
    "about_an_hour": "about an hour",
    "hours": "{n} hours",
    "missions_expiring": {"one": "{n} mission expires within three hours: {list}.",
                          "other": "{n} missions expire within three hours: {list}."},
    "valuable_scan": "Valuable scan: {body} is {what}{first}. Worth mapping.",
    "earthlike": "an Earth-like world",
    "water_world": "a water world",
    "ammonia_world": "an ammonia world",
    "terraformable_planet": "a terraformable {planet}",
    "and_terraformable": ", terraformable",
    "undiscovered": ", and nobody's discovered it yet",
    "fuel_scoopable_here": "Fuel at {pct} percent. This star is scoopable.",
    "fuel_no_scoopable": "Fuel at {pct} percent, and there's no scoopable star left on this route.",
    "fuel_scoopable_ahead": {"one": "Fuel at {pct} percent. Next scoopable star is {n} jump out.",
                             "other": "Fuel at {pct} percent. Next scoopable star is {n} jumps out."},
    "trader_here": "There's a {trader} here. On your list: {items}.",
    "trader_raw": "raw material trader",
    "trader_manufactured": "manufactured material trader",
    "trader_encoded": "encoded material trader",
    "trader_any": "material trader",
    "no_limpets": "You have no limpets aboard. Did you mean to buy some?",
    "few_limpets": "Only {n} limpets aboard. Enough for this run?",
    "fighter_pilot": "Fighter hangar fitted. Is your fighter pilot aboard?",
    "interdictions_tonight": "That's the {ordinal} interdiction tonight",  # {n} works too: the count as a number
    "ordinal_2": "second", "ordinal_3": "third", "ordinal_4": "fourth", "ordinal_5": "fifth",
    "ordinal_n": "{n}th",
    "same_pilot": "{pilot} again",
    "same_faction": "same faction, {faction}",
    # ---- mining ----
    "verdict_crack": "Crack it",
    "verdict_crack_laser": "Crack it, and laser it too",
    "verdict_mine": "Mine it",
    "verdict_few_chunks": "Worth a few chunks",
    "verdict_skip": "Skip",
    "core": "Core: {core}",
    "material_percent": "{material} {pct} percent",
    "content": "{content} content",
    "no_materials": "no materials",
    "percent_left": "{pct} percent left",
    "out_of_limpets": "Out of limpets.",
    "limpets_left": {"one": "{n} limpet left.", "other": "{n} limpets left."},
    "hold_full": "Hold full.",
    "hold_nearly_full": {"one": "Hold nearly full, {n} ton left.", "other": "Hold nearly full, {n} tons left."},
    # ---- reminders ----
    "reminder": "Reminder. {text}",
    # ---- the copilot ----
    "online": "Co-pilot online.",
    "needs_api_key": "Questions need a Claude API key. Set ANTHROPIC_API_KEY and restart me.",
    "callouts_off": "Callouts off.",
    "callouts_critical": "Critical callouts only.",
    "callouts_on": "Callouts on.",
    "starting_fresh": "Starting fresh.",
    "budget_used": "About {cents} cents of today's {budget} dollar budget used.",
    "no_brain": "I have no brain to switch.",
    "going_local": "Going local. Give me up to half a minute to load the model.",
    "local_ready": "Local model ready.",
    "local_not_answering": "The local model isn't answering. Say go cloud to switch back to Claude.",
    "local_not_set_up": "No local model is set up in config.",
    "local_mode": "Local mode.",
    "back_on_claude": "Back on Claude.",
    "mic_silent": "I can't hear anything. I'm listening to {mic}, and it's silent. Set [listen] mic in your config.toml.",
    "pasted": "Pasted into {window}.",
    "the_active_window": "the active window",
    "paste_blocked": "Windows blocked the paste. Press Control V yourself.",
    # ---- while a slow tool runs ----
    "slow_find_service": "Searching, this can take half a minute.",
    "slow_plot_route": "Plotting, one moment.",
    "slow_trade_between": "Comparing markets, one moment.",
    "slow_find_best_price": "Checking prices, one moment.",
    "slow_best_trades": "Checking the best trades, give me half a minute.",
    "slow_trades_along_missions": "Checking trades along your missions, this can take a minute.",
    "slow_write_captains_log": "Writing up your log, give me half a minute.",
    "slow_find_mining_hotspots": "Looking for hotspots, one moment.",
    "slow_web_search": "Searching the web, one moment.",
    "slow_plan_mining_run": "Planning that run, give me half a minute.",
    "slow_get_hold_value": "Pricing your hold, one moment.",
    "slow_find_home_base": "That's a big search. Give me up to a couple of minutes.",
    "thinking_harder": "Let me think about that one properly.",
    # ---- spoken errors ----
    "cant_help": "I can't help with that one.",
    "too_many_steps": "That took too many steps; try asking a simpler way.",
    "cant_reach_local": "I can't reach the local model. Say go cloud to switch back to Claude.",
    "budget_exceeded": "Today's Claude budget of {budget} dollars is used up.",
    "bad_api_key": "My Claude API key isn't working. Check ANTHROPIC_API_KEY.",
    "rate_limited": "Claude is rate limiting me. Try again in a minute.",
    "cant_reach_claude": "I can't reach Claude right now.",
    "api_error": "Claude returned an error, status {status}.",
    "went_wrong": "Something went wrong with that question.",
}

_lang = "en"
_locale: dict = {}


def load(code: str) -> dict:
    """A locale file as a dict ({} for English, or when there's no file for that language)."""
    if code == "en":
        return {}
    path = LOCALES / f"{code}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        log.warning("no translation for %r yet: callouts stay in English", code)
    except (OSError, json.JSONDecodeError):
        log.exception("couldn't read %s: callouts stay in English", path)
    return {}


def set_language(code: str) -> None:
    """Speak in this language from now on (the copilot calls this once at start-up)."""
    global _lang, _locale
    _lang, _locale = code, load(code)


def language() -> str:
    """The language set with set_language()."""
    return _lang


def plural_category(n: int, lang: str | None = None) -> str:
    """CLDR plural category for a whole number: "one" or "other", and for Russian, Ukrainian and Polish
    also "few" and "many". French and Portuguese count 0 as "one"."""
    lang = lang or _lang
    if lang in ("ru", "uk"):
        if n % 10 == 1 and n % 100 != 11:
            return "one"
        if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
            return "few"
        return "many"
    if lang == "pl":
        if n == 1:
            return "one"
        if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
            return "few"
        return "many"
    if lang in ("fr", "pt"):
        return "one" if n in (0, 1) else "other"
    return "one" if n == 1 else "other"


def t(key: str, **kw) -> str:
    """The phrase for `key` in the current language, filled in. Plural phrases take the count as n.
    A translation that's missing, or that breaks, falls back to English."""
    for table, lang in ((_locale.get("phrases", {}), _lang), (EN, "en")):
        text = table.get(key)
        if isinstance(text, dict):
            category = plural_category(int(kw.get("n", 0)), lang)
            text = text.get(category) or text.get("other") or text.get("many")
        if text is None:
            continue
        try:
            return text.format(**kw)
        except (KeyError, IndexError, ValueError):
            log.warning("bad %s translation for %r", lang, key)
    raise KeyError(key)


def commands() -> dict[str, list[str]]:
    """The fixed commands in the current language: action -> whole phrases. {} for English, whose
    commands are the patterns in copilot.COMMANDS."""
    return _locale.get("commands", {})


def padding() -> list[str]:
    """Polite words a command may come with in the current language."""
    return _locale.get("padding", [])
