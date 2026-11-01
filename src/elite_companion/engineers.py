"""Engineer lookup: who engineers what (blueprint and highest grade), where they are, how far, and their own
progress with each.

Data: data/engineers.json, built by scripts/build_engineers.py from EDEngineer's blueprint list
(github.com/msarilar/EDEngineer, last updated 2024-03) plus a hand table of engineer bases with EDSM
coordinates. Modules with no blueprints there (mining lasers, for one) can't be engineered.
"""

from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).parent / "data" / "engineers.json"

MODULE_ALIASES = {  # how they might say it -> the data's module type
    "fsd": "Frame Shift Drive", "frame shift": "Frame Shift Drive", "jump drive": "Frame Shift Drive",
    "interdictor": "Frame Shift Drive Interdictor", "engines": "Thrusters", "thruster": "Thrusters",
    "shields": "Shield Generator", "shield": "Shield Generator", "booster": "Shield Booster",
    "boosters": "Shield Booster", "hull reinforcement": "Hull Reinforcement Package", "hrp": "Hull Reinforcement Package",
    "distributor": "Power Distributor", "pd": "Power Distributor", "power plant": "Power Plant",
    "multicannon": "Multi-cannon", "multi cannon": "Multi-cannon", "railgun": "Rail Gun", "frag": "Fragment Cannon",
    "fragment": "Fragment Cannon", "slugshot": "Fragment Cannon", "missiles": "Missile Rack", "dss": "Surface Scanner",
    "detailed surface scanner": "Surface Scanner", "prospector": "Prospector Limpet Controller",
    "collector": "Collector Limpet Controller", "afmu": "Auto Field-Maintenance Unit", "scb": "Shield Cell Bank",
    "ecm": "Electronic Countermeasure", "heatsink": "Heat Sink Launcher", "heat sink": "Heat Sink Launcher",
    "kws": "Kill Warrant Scanner", "fuel scoop": "Fuel Scoop", "sensor": "Sensors",
}
NOT_ENGINEERABLE = {"mining laser", "mining lasers", "abrasion blaster", "seismic charge", "sub-surface missile",
                    "subsurface missile", "pulse wave", "pulse wave analyser", "cargo rack", "docking computer",
                    "fighter hangar", "fighter bay", "planetary vehicle hangar"}


@lru_cache(maxsize=1)
def data() -> dict:
    """The bundled engineer data (loaded once): engineers, modules -> blueprints -> grades -> who, and
    ingredients per roll.
    """
    return json.loads(DATA.read_text(encoding="utf-8"))


def _squash(s: str) -> str:
    """Lower-case letters and digits only, for forgiving name matches."""
    return "".join(ch for ch in (s or "").lower() if ch.isalnum())


def module_type(query: str) -> str | None:
    """The data's module type for what the commander said: an alias ("FSD"), then an exact match,
    then the shortest type containing the words. None if nothing fits.
    """
    q = query.lower().strip()
    if q in MODULE_ALIASES:
        return MODULE_ALIASES[q]
    types = list(data()["modules"])
    sq = _squash(q).rstrip("s")
    exact = [t for t in types if _squash(t).rstrip("s") == sq]
    if exact:
        return exact[0]
    partial = [t for t in types if sq and (sq in _squash(t) or _squash(t) in sq)]
    return min(partial, key=len) if partial else None


def engineer_name(query: str) -> str | None:
    """The commander's words or the journal's spelling ("Tod 'The Blaster' McQuinn") -> the data's name."""
    sq = _squash(query.replace("'The Blaster'", ""))
    for name in data()["engineers"]:
        if sq and (sq == _squash(name) or sq in _squash(name)):
            return name
    return None


def _distance(a: list | None, b: list | None) -> float | None:
    """Straight-line distance in light years between two [x, y, z] coordinates, or None."""
    if not a or not b:
        return None
    return round(math.dist(a, b), 1)


def _who(name: str, here: list | None, progress: dict) -> dict:
    """One engineer as a row: where they are, how far from `here`, the commander's status and rank with
    them, and how to unlock them if they aren't unlocked yet.
    """
    e = data()["engineers"].get(name, {})
    p = progress.get(name, {})
    return {k: v for k, v in {
        "engineer": name, "system": e.get("system"), "base": e.get("base"),
        "distance_ly": _distance(here, e.get("coords")), "in_colonia": e.get("colonia") or None,
        "your_status": p.get("status") or "not yet known to you", "your_rank": p.get("rank"),
        "how_to_unlock": e.get("unlock") if p.get("status") != "Unlocked" else None,
    }.items() if v is not None}


def lookup(module: str | None = None, engineer: str | None = None, here: list | None = None,
           progress: dict | None = None) -> dict:
    """module: what can be done to it and by whom. engineer: where they are and what they offer."""
    progress = progress or {}
    mods = data()["modules"]
    if engineer:
        name = engineer_name(engineer)
        if not name:
            return {"error": f"I don't know an engineer called {engineer}.", "known": sorted(data()["engineers"])}
        offers = {}
        for t, bps in mods.items():
            for bp, grades in bps.items():
                top = max((int(g) for g, who in grades.items() if g != "experimental" and name in who), default=0)
                if top:
                    offers.setdefault(t, {})[bp] = f"up to grade {top}"
        out = {**_who(name, here, progress), "offers": offers}
        out.setdefault("how_to_unlock", data()["engineers"][name].get("unlock") or "not in my data (Colonia engineer)")
        out["unlock_source"] = "Alioth Elite wiki, community data that may be dated: check the Engineers panel."
        return out
    if not module:
        return {"error": "Ask about a module (e.g. FSD, shield booster) or an engineer by name."}
    if module.lower().strip() in NOT_ENGINEERABLE:
        return {"module": module, "engineerable": False,
                "note": f"There are no engineering blueprints for {module}. It can't be engineered."}
    t = module_type(module)
    if not t:
        return {"module": module, "engineerable": False,
                "note": f"No engineering blueprints found for {module}. It may not be engineerable, or try "
                        f"another name.", "module_types": sorted(mods)}
    blueprints, everyone = [], set()
    for bp, grades in mods[t].items():
        if bp == "experimental":
            continue
        by = {}
        for g, who in grades.items():
            if g == "experimental":
                continue
            for name in who:
                by[name] = max(by.get(name, 0), int(g))
                everyone.add(name)
        if by:
            blueprints.append({"blueprint": bp, "max_grade": max(by.values()),
                               "engineers": [f"{n} (G{g})" for n, g in sorted(by.items(), key=lambda kv: -kv[1])]})
    experimentals = sorted({bp for bp, grades in mods[t].items() if "experimental" in grades})
    people = sorted((_who(n, here, progress) for n in everyone),
                    key=lambda w: (w.get("your_status") != "Unlocked",
                                   1e9 if w.get("distance_ly") is None else w["distance_ly"]))
    return {"module": t, "engineerable": True, "blueprints": blueprints,
            "experimental_effects": experimentals or None, "engineers": people,
            "note": "Engineers they have unlocked come first, then nearest. Data from EDEngineer (2024); newer "
                    "changes may be missing."}
