"""Mining help: spoken prospector readouts, limpet and hold warnings, and a fit check.

The journal records every prospected asteroid (materials, content, remaining, and a core if
there is one), every refined ton, cracked cores and launched limpets. Subsurface deposits are
only visible in the cockpit (the Pulse Wave Analyser), not in the journal.

Settings in config.toml under [mining]: prospector = "full" | "verdict" | "good" | "off".
"""

from __future__ import annotations

from typing import Callable

from .phrases import t
from .speech import NORMAL
from .state import _seconds_between

# Worth mining by laser when they make up a good share of the rock (prices change; this is a rough cut).
LASER_TARGETS = {
    "Platinum", "Painite", "Osmium", "Palladium", "Gold", "Rhodplumsite", "Serendibite", "Monazite",
    "Musgravite", "Grandidierite", "Alexandrite", "Benitoite", "Bromellite", "Low Temperature Diamonds",
    "Tritium",
}
MINE_IT_PERCENT = 15     # a valuable mineral at or above this: "mine it" (25 proved too picky in play)
FEW_CHUNKS_PERCENT = 8   # at or above this: "worth a few chunks"
TARGET_MINE_IT = 8       # the mineral they're hunting: "mine it" from this much
LIMPET_WARNINGS = (5, 2, 0)
ACTIVE_MINUTES = 15      # a mining session counts as active this long after its last event


def _same(a: str, b: str | None) -> bool:
    """Same mineral name, ignoring case, spaces, punctuation and a plural s ("Void Opal" = "void opals")."""
    sq = lambda s: "".join(ch for ch in (s or "").lower() if ch.isalnum()).rstrip("s")
    return bool(b) and sq(a) == sq(b)


def verdict(prospect: dict, target: str | None = None) -> str:
    """The call for one prospected rock: "Crack it" (has a core), "Mine it", "Worth a few chunks" or
    "Skip". `target` is the mineral being hunted (from the last hotspot search or mining plan);
    it counts from a lower share than other valuable minerals. See the thresholds above.
    """
    mats = prospect.get("materials", {})
    hunted = next((p for n, p in mats.items() if _same(n, target)), 0)
    best = max(((n, p) for n, p in mats.items() if n in LASER_TARGETS), key=lambda np: np[1], default=None)
    if prospect.get("core"):
        return "Crack it" if not best or best[1] < MINE_IT_PERCENT else "Crack it, and laser it too"
    if hunted >= TARGET_MINE_IT or (best and best[1] >= MINE_IT_PERCENT):
        return "Mine it"
    if hunted or (best and best[1] >= FEW_CHUNKS_PERCENT):
        return "Worth a few chunks"
    return "Skip"


# verdict() answers in English for the tools; what's spoken goes through the phrase table.
VERDICT_KEYS = {"Crack it": "verdict_crack", "Crack it, and laser it too": "verdict_crack_laser",
                "Mine it": "verdict_mine", "Worth a few chunks": "verdict_few_chunks", "Skip": "verdict_skip"}


def readout(prospect: dict, style: str = "full", target: str | None = None) -> str | None:
    """What to say about a prospected rock, in the [mining] prospector style:
    full = content, every material, core, what's left, then the verdict;
    verdict / good = the best material (or the core) and the verdict, good skipping "Skip" rocks;
    off = nothing (None).
    """
    v = verdict(prospect, target)
    if style == "off" or (style == "good" and v == "Skip"):
        return None
    said = t(VERDICT_KEYS[v])
    if style in ("verdict", "good"):
        best = max(prospect.get("materials", {}).items(), key=lambda kv: kv[1], default=None)
        lead = t("core", core=prospect["core"]) + ". " if prospect.get("core") else (
            t("material_percent", material=best[0], pct=round(best[1])) + ". " if best else "")
        return f"{lead}{said}."
    mats = sorted(prospect.get("materials", {}).items(), key=lambda kv: -kv[1])
    parts = [t("content", content=prospect["content"]) if prospect.get("content") else ""]
    parts.append(", ".join(t("material_percent", material=n, pct=round(p)) for n, p in mats) or t("no_materials"))
    if prospect.get("core"):
        parts.append(t("core", core=prospect["core"]))
    if prospect.get("remaining") is not None and prospect["remaining"] < 100:
        parts.append(t("percent_left", pct=round(prospect["remaining"])))
    return ". ".join(p for p in parts if p) + f". {said}."


def fit_check(modules: list[dict], cargo: dict, cargo_capacity: int | None) -> dict:
    """What the current ship has for each kind of mining, and what's missing."""
    names = " | ".join((m.get("Item") or "").lower() for m in modules)  # journal Loadout module entries
    has = {
        "refinery": "refinery" in names,
        "prospector controller": "dronecontrol_prospector" in names,
        "collector controller": "dronecontrol_collection" in names,
        "mining laser": "mininglaser" in names,
        "abrasion blaster": "mining_abrblstr" in names,
        "seismic charge launcher": "mining_seismchrgwarhd" in names,
        "sub-surface missile launcher": "mining_subsurfdispmisle" in names,
        "pulse wave analyser": "mrascanner" in names,
        "detailed surface scanner": "detailedsurfacescanner" in names,
    }
    need = {
        "laser": ["refinery", "prospector controller", "collector controller", "mining laser"],
        "core": ["refinery", "prospector controller", "collector controller", "seismic charge launcher",
                 "pulse wave analyser"],
        "sub-surface": ["refinery", "prospector controller", "collector controller",
                        "sub-surface missile launcher", "pulse wave analyser"],
        "seeing hotspots": ["detailed surface scanner"],
    }
    limpets = cargo.get("Limpet", 0) or cargo.get("limpet", 0)
    return {
        "fitted": [k for k, v in has.items() if v],
        "ready_for": {kind: (not [n for n in req if not has[n]]) for kind, req in need.items()},
        "missing": {kind: [n for n in req if not has[n]] for kind, req in need.items() if any(not has[n] for n in req)},
        "limpets_in_hold": limpets,
        "cargo_capacity": cargo_capacity,
        "tip": "Bring about one limpet per 4 to 5 tons of cargo space you expect to fill, plus spares."
               if limpets < 20 else None,
    }


class MiningCallouts:
    """Speaks prospector readouts, limpet warnings and a full-hold warning."""

    def __init__(self, watcher, say: Callable[[str, int], None], callouts=None, config: dict | None = None):
        self.watcher, self.say, self.callouts = watcher, say, callouts
        self.style = (config or {}).get("prospector", "full")
        self.warned: set = set()
        watcher.on("ProspectedAsteroid", self._prospected)
        watcher.on("Cargo", self._cargo)

    def _speak(self, text: str) -> None:
        """Speak unless callouts are off. Mining readouts count as wanted even in critical-only mode."""
        if not self.callouts or self.callouts.mode != "off":
            self.say(text, NORMAL)

    def _prospected(self, ev) -> None:
        """Read out the rock the prospector limpet just hit."""
        if ev.replay:
            return
        p = self.watcher.state.mining.get("last_prospect")
        text = readout(p, self.style, self.watcher.state.mining_target) if p else None
        if text:
            self._speak(text)

    def _active(self, ts: str | None) -> bool:
        """Whether a mining session is under way: some mining event in the last ACTIVE_MINUTES."""
        m = self.watcher.state.mining
        return bool(m) and _seconds_between(m.get("last"), ts) < ACTIVE_MINUTES * 60

    def _cargo(self, ev) -> None:
        """On each cargo change while mining: limpets at 5, 2 and 0 (once each per session), and
        the hold nearly full (5% or 4 tons left) and full.
        """
        if ev.replay or not self._active(ev.data.get("timestamp")):
            return
        s = self.watcher.state
        limpets = s.cargo.get("Limpet", 0)
        session = s.mining.get("start")
        for n in LIMPET_WARNINGS:
            if limpets <= n and (session, "limpets", n) not in self.warned:
                self.warned.update((session, "limpets", k) for k in LIMPET_WARNINGS if k >= limpets)
                self._speak(t("out_of_limpets") if limpets == 0 else t("limpets_left", n=limpets))
                break
        cap = s.ship.cargo_capacity
        used = sum(c for c in s.cargo.values() if isinstance(c, (int, float)))
        if cap and used >= cap and (session, "full") not in self.warned:
            self.warned.add((session, "full"))
            self._speak(t("hold_full"))
        elif cap and cap - used <= max(4, cap * 0.05) and (session, "nearly") not in self.warned:
            self.warned.add((session, "nearly"))
            self._speak(t("hold_nearly_full", n=cap - used))
