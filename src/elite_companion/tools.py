"""The game tools, shared by the MCP server and the voice copilot.

Each method returns a JSON-friendly dict, or raises GameDataError with a
message meant for the end user (it gets spoken or shown as-is).
"""

from __future__ import annotations

import inspect
import re
import time
from datetime import datetime, timedelta, timezone

from .bindings import BINDINGS_SPEC
from . import ardent, edsm, engineers, galnet, history, markets, mining, spansh
from .describe import describe, is_noise
from .state import _seconds_between
from .journal import journal_files, read_entries
from .memory import MemoryStore, NoteError, describe_note
from .watcher import Watcher


class GameDataError(RuntimeError):
    """The question can't be answered from what's known; the message is safe to speak."""
    pass


class GameTools:
    """Every game tool Vesper and the MCP server offer, as plain methods returning JSON-ready dicts.

    Local tools read the watcher's state; galaxy tools call Spansh, EDSM, Ardent or GalNet and
    default to the current system and pad size. TOOL_SPECS below describes each one to Claude.
    Failures raise GameDataError.
    """
    def __init__(self, watcher: Watcher, notes: MemoryStore | None = None):
        self.watcher = watcher
        self.notes = notes or MemoryStore()

    # ---- memory (notes and reminders, kept between sessions) ----

    def remember(self, text: str, when: str = "none", place: str | None = None,
                 minutes_from_now: int | None = None, at_local_time: str | None = None) -> dict:
        """Save a note or reminder (see MemoryStore.add)."""
        try:
            note = self.notes.add(text, when, place, minutes_from_now, at_local_time)
        except NoteError as e:
            raise GameDataError(str(e)) from e
        return {"saved": describe_note(note)}

    def recall_notes(self, search: str | None = None, include_done: bool = False) -> dict:
        """Saved notes, optionally searched."""
        notes = self.notes.list(search, include_done)
        return {"count": len(notes), "notes": [describe_note(n) + (" (done)" if n.get("done") else "") for n in notes]}

    def set_my_name(self, name: str) -> dict:
        """Rename the copilot."""
        if not name.strip():
            raise GameDataError("Give me a name.")
        self.notes.set_name(name)
        return {"name": name.strip()}

    def add_preference(self, text: str) -> dict:
        """Add a standing preference for how the copilot answers."""
        return {"preferences": self.notes.add_preference(text)}

    def remove_preference(self, search: str) -> dict:
        """Drop preferences matching `search`."""
        return {"preferences": self.notes.remove_preference(search)}

    def copy_to_clipboard(self, text: str) -> dict:
        """Put exact text (usually a system name) on the Windows clipboard for pasting into the galaxy map."""
        try:
            import win32clipboard
            win32clipboard.OpenClipboard()
            try:
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardText(text.strip(), win32clipboard.CF_UNICODETEXT)
            finally:
                win32clipboard.CloseClipboard()
        except Exception as e:  # noqa: BLE001 - clipboard busy, or pywin32 missing
            raise GameDataError(f"Couldn't copy to the clipboard ({e}).") from e
        return {"copied": text.strip()}

    def forget_note(self, note_id: int | None = None, search: str | None = None) -> dict:
        """Delete notes by number or text."""
        try:
            gone = self.notes.forget(note_id, search)
        except NoteError as e:
            raise GameDataError(str(e)) from e
        return {"forgot": [describe_note(n) for n in gone]}

    # ---- helpers ----

    def _snapshot(self) -> dict:
        """The state snapshot, or GameDataError if no journal has been read yet."""
        snap = self.watcher.snapshot()
        if not snap.get("commander"):
            raise GameDataError(
                f"No game data yet. Has Elite Dangerous been played on this PC? Journal folder: {self.watcher.directory}"
            )
        return snap

    def _system(self, near_system: str | None) -> str:
        """The system to search around: the one named, else the current one."""
        system = near_system or self._snapshot()["location"]["system"]
        if not system:
            raise GameDataError("Current system unknown; name a system explicitly.")
        return system

    def _large_pad(self, large_pad: bool | None) -> bool:
        """A ship that needs a large pad always gets large-pad stations: the model once turned the filter off
        and sent the Type-10 to an outpost."""
        needs = spansh.needs_large_pad(self._snapshot()["ship"]["internal"])
        return True if needs else bool(large_pad)

    # ---- local game data ----

    def status(self) -> dict:
        """Where the commander is, the ship, fuel, cargo, credits and other live basics."""
        s = self._snapshot()
        return {
            "commander": s["commander"],
            "game_running": s["game_running"],
            "game_mode": s["game_mode"],
            "credits": s["credits"],
            "legal_state": s["legal_state"],
            "location": s["location"],
            "ship": s["ship"],
            "fuel": {"main": s["fuel_main"], "reservoir": s["fuel_reservoir"]},
            "cargo": s["cargo"],
            "owned_cargo": s["owned_cargo"],
            "mission_cargo": s["mission_cargo"],
            "route_remaining": s["route_remaining"],
            "flags": s["flags"],
            "last_event_time": s["last_event_time"],
        }

    def missions(self) -> dict:
        """Active missions, ready-to-turn-in first, each with one clear "go here next"."""
        missions = self._snapshot()["missions"]
        missions.sort(key=lambda m: (m["status"] != "ready to turn in", m.get("expiry") or ""))
        rows = []
        for m in missions:
            m = dict(m)
            # One unambiguous "go here next": once the objective is done, the old target system is history.
            if m.get("turn_in_system"):
                m["go_to_system"], m["go_to_station"] = m.pop("turn_in_system"), m.pop("turn_in_station", None)
                m["objective_was_in"] = m.pop("destination_system", None)
                m.pop("destination_station", None)
            else:
                m["go_to_system"], m["go_to_station"] = m.get("destination_system"), m.get("destination_station")
            rows.append({k: v for k, v in m.items() if v is not None})
        return {"count": len(rows), "missions": rows}

    def turn_ins(self) -> list[str]:
        """Pending turn-ins as "Station (System) x N", for the live context line."""
        counts: dict[str, int] = {}
        for m in self.missions()["missions"]:
            if m["status"] == "ready to turn in" and m.get("go_to_system"):
                key = f"{m.get('go_to_station') or '?'} ({m['go_to_system']})"
                counts[key] = counts.get(key, 0) + 1
        return [f"{k} x{n}" if n > 1 else k for k, n in counts.items()]

    def recent_events(self, minutes: int = 15, include_noisy: bool = False, limit: int = 100) -> dict:
        """What happened in the last few minutes, described in words."""
        events = [e for e in self.watcher.recent(seconds=minutes * 60) if include_noisy or not is_noise(e)]
        lines = []
        for e in events:
            text = describe(e)
            if lines and lines[-1]["description"] == text:  # a locked target re-logs every second
                continue
            lines.append({"time": e.timestamp, "event": e.name, "description": text})
        lines = lines[-limit:]
        return {"minutes": minutes, "count": len(lines), "events": lines}

    def threats(self, hours: float = 24) -> dict:
        """Hostile pilots seen recently, the current target, and who has killed you lately."""
        state = self.watcher.state
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
        killers = {d["killer"] for d in state.deaths if d.get("killer")}
        hostiles = []
        for c in sorted(state.contacts.values(), key=lambda c: c.get("last_seen", ""), reverse=True):
            if c.get("last_seen", "") < cutoff:
                continue
            row = {k: v for k, v in c.items() if k not in ("last_message",)}
            row["last_seen_ago"] = _ago(c.get("last_seen"))
            if c["name"] in killers:
                row["killed_you"] = [d["time"] for d in state.deaths if d.get("killer") == c["name"]]
            hostiles.append(row)
        return {
            "current_target": state.target,
            "hostile_pilots": hostiles,
            "recent_deaths": [dict(d, ago=_ago(d["time"])) for d in state.deaths[-5:]],
            "note": "Positions, ranges and speeds of other ships aren't in the journal; only the HUD shows them.",
        }

    def threat_summary(self, fresh_minutes: int = 10) -> str:
        """Live-context line: only threats from the last few minutes (they asked not to be reminded of
        old ones), plus the locked target if any. Empty when there's nothing current."""
        state = self.watcher.state
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=fresh_minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")
        killers = {d["killer"] for d in state.deaths if d.get("killer")}
        parts = []
        for c in sorted(state.contacts.values(), key=lambda c: c.get("last_seen", ""), reverse=True)[:3]:
            if c.get("last_seen", "") < cutoff:
                continue
            bits = [c.get("ship"), c.get("rank"), f"hostile contact {_ago(c.get('last_seen'))}",
                    "has killed you before" if c["name"] in killers else None]
            parts.append(f"{c['name']} ({', '.join(b for b in bits if b)})")
        t = state.target
        target = ""
        if t:
            bits = [t.get("pilot") or "unknown pilot", t.get("rank"), f"shields {t['shields']}%" if "shields" in t else None,
                    f"hull {t['hull']}%" if "hull" in t else None, t.get("legal")]
            target = f" Target locked: {t.get('ship')}, " + ", ".join(b for b in bits if b) + "."
        return (("ACTIVE THREAT: " + "; ".join(parts) + ".") if parts else "") + target

    def session_summary(self) -> dict:
        """This session's earnings and activity counts."""
        s = self._snapshot()
        return {"commander": s["commander"], "credits_now": s["credits"], **s["session"]}

    def crimes(self, days: int = 7) -> dict:
        """Crimes, fines, bounties and payments from the last N days of journals."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        cutoff_ts = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")
        files = [p for p in journal_files(self.watcher.directory) if p.stat().st_mtime >= time.time() - days * 86400]
        crimes, payments, deaths = [], [], []
        for path in files:
            for e in read_entries(path):
                if e.get("timestamp", "") < cutoff_ts:
                    continue
                ev = e["event"]
                if ev == "CommitCrime":
                    crimes.append({"time": e["timestamp"], "faction": e.get("Faction"), "crime": e.get("CrimeType"),
                                   "victim": e.get("Victim_Localised") or e.get("Victim"),
                                   "bounty": e.get("Bounty"), "fine": e.get("Fine")})
                elif ev in ("PayBounties", "PayFines", "PayLegacyFines"):
                    payments.append({"time": e["timestamp"], "type": ev, "faction": e.get("Faction"),
                                     "all_fines": e.get("AllFines"), "amount": e.get("Amount"),
                                     "broker_percentage": e.get("BrokerPercentage")})
                elif ev == "Died":
                    deaths.append({"time": e["timestamp"], "killer": e.get("KillerName_Localised") or e.get("KillerName")})

        # Per-faction totals of crimes committed after that faction's last payment.
        by_faction: dict[str, dict] = {}
        for c in crimes:
            paid_after = [p for p in payments if p["time"] > c["time"] and (p["faction"] == c["faction"] or p["all_fines"])]
            if paid_after:
                continue
            f = by_faction.setdefault(c["faction"] or "unknown", {"bounties": 0, "fines": 0, "crimes": 0})
            f["bounties"] += c["bounty"] or 0
            f["fines"] += c["fine"] or 0
            f["crimes"] += 1
        return {
            "days": days,
            "legal_state_here": self._snapshot()["legal_state"],
            "unpaid_estimate_by_faction": by_faction,
            "crimes": crimes[-30:],
            "payments": payments[-10:],
            "deaths": deaths[-10:],
            "notes": ("Estimate from the journal. Bounties only apply in the issuing faction's jurisdiction. "
                      "Interstellar Factors can't clear bounties in the system that issued them, and won't "
                      "clear bounties while you have notoriety, which drops one point per 2 hours played."),
        }

    # ---- galaxy data (Spansh / EDSM) ----

    def find_commodity(self, commodity: str, min_stock: int = 1, near_system: str | None = None,
                       large_pad: bool | None = None, max_results: int = 5) -> dict:
        """Nearest stations selling a commodity (or, for a material, where to get it)."""
        if material := self._material_info(commodity):
            return material
        system, pad = self._system(near_system), self._large_pad(large_pad)
        try:
            rows = spansh.find_commodity(commodity, system, min_stock, pad, False, max_results)
        except spansh.SpanshError:
            rows = self._ardent(commodity, system, False, 100, min_stock, pad, None, max_results)
            rows.sort(key=lambda r: r["distance_ly"])
        result = {"near_system": system, "large_pad_required": pad, "stations": rows}
        if not rows:
            result["hint"] = ("Nothing found. Check the spelling, lower min_stock, or it may be an engineering material "
                              "(those come from material traders, not markets).")
        return result

    def find_service(self, service: str, near_system: str | None = None, large_pad: bool | None = None,
                     max_results: int = 5, trader_kind: str | None = None) -> dict:
        """Nearest stations with a service (material trader of a kind, interstellar factors...)."""
        system, pad = self._system(near_system), self._large_pad(large_pad)
        low = service.lower()
        kind = trader_kind or next((k for k in ("raw", "manufactured", "encoded") if k in low), None)
        try:
            rows = spansh.find_service(service, system, pad, False, max_results, kind=kind)
        except spansh.SpanshError as e:
            raise GameDataError(str(e)) from e
        result = {"near_system": system, "service": spansh.service_name(service), "trader_kind": kind,
                  "large_pad_required": pad, "stations": rows,
                  "note": "Every station listed was checked against its own services list."}
        if not rows:
            result["hint"] = ("None within the nearest 500 stations. Try without large_pad, without trader_kind, "
                              "or from another system with near_system.")
        return result

    def find_home_base(self, max_from_sol_ly: float = 60, min_population: int = 1_000_000,
                       need_interstellar_factors: bool = True, near_system: str = "Sol") -> dict:
        """Candidate home bases ranked by how close all three material trader kinds are."""
        here = self.watcher.state.location.star_pos
        try:
            rows = spansh.find_home_bases(near_system, max_from_sol_ly, min_population, need_interstellar_factors,
                                          from_xyz=tuple(here) if here else None)
        except spansh.SpanshError as e:
            raise GameDataError(str(e)) from e
        return {"criteria": {"within_ly_of": near_system, "max_ly": max_from_sol_ly, "min_population": min_population,
                             "needs": ["large pad", "shipyard", "outfitting"]
                             + (["Interstellar Factors"] if need_interstellar_factors else []),
                             "ranked_by": "distance to the farthest of the nearest raw, manufactured and encoded traders"},
                "candidates": rows,
                "note": "Material traders mapped within about 64 ly of Sol (cached for a week)."}

    def plot_route(self, destination: str, source: str | None = None, jump_range_ly: float | None = None,
                   efficiency: int = 60) -> dict:
        """A neutron-boosted route from Spansh, using the ship's jump range."""
        s = self._snapshot()
        source = source or s["location"]["system"]
        jump_range = jump_range_ly or s["ship"]["max_jump_range"]
        if not source or not jump_range:
            raise GameDataError("Need a starting system and jump range; the journal doesn't have them yet.")
        try:
            route = spansh.plot_neutron_route(source, destination, jump_range, efficiency)
        except spansh.SpanshError as e:
            raise GameDataError(str(e)) from e
        route["note"] = ("Neutron route. jump_range is the ship's current laden range "
                         f"({jump_range:.1f} ly); boosted jumps from neutron stars are already counted.")
        return route

    def system_info(self, system: str | None = None) -> dict:
        """EDSM's facts about a system: allegiance, security, population, factions."""
        try:
            return edsm.system_info(self._system(system))
        except edsm.EdsmError as e:
            raise GameDataError(str(e)) from e

    # ---- markets ----

    def _destination(self) -> tuple[str | None, str | None]:
        """(station or None, system or None) for what's targeted / plotted."""
        s = self._snapshot()
        dest = s.get("destination") or {}
        route_end = self.watcher.state.route[-1] if self.watcher.state.route else None
        system = None
        if dest.get("System"):
            if dest["System"] == s["location"]["system_address"]:
                system = s["location"]["system"]
            else:
                system = next((st.get("StarSystem") for st in self.watcher.state.route
                               if st.get("SystemAddress") == dest["System"]), None)
        system = system or (route_end or {}).get("StarSystem")
        # A targeted station reports its own name; a targeted system reports the system name.
        system_names = {n.lower() for n in [system, s["location"]["system"]] if n}
        system_names |= {(st.get("StarSystem") or "").lower() for st in self.watcher.state.route}
        name = dest.get("Name")
        station = name if name and name.lower() not in system_names else None
        return station, system

    def _market_items(self, station: str, system: str | None) -> tuple:
        """(station, system, items, source, updated): the freshest of your live market screen, your
        own cached visits (~/.vesper/markets) and Spansh."""
        def from_game(m: dict, source: str) -> tuple:
            items = [{"commodity": i.get("Name_Localised") or i.get("Name"), "category": i.get("Category_Localised"),
                      "buy_price": i.get("BuyPrice"), "stock": i.get("Stock"), "sell_price": i.get("SellPrice"),
                      "demand": i.get("Demand"), "galactic_average": i.get("MeanPrice")}
                     for i in m.get("Items") or (m.get("_file") or {}).get("Items", [])]
            return m.get("StationName"), m.get("StarSystem"), items, source, m.get("timestamp")

        live = self.watcher.state.records.get("Market")
        if live and live.get("StationName", "").lower() == station.lower() and \
                (not system or live.get("StarSystem", "").lower() == system.lower()):
            return from_game(live, "your game (live market screen)")
        cached = markets.load(station, system)
        try:
            st = spansh.get_station(station, system)
        except spansh.SpanshError as e:
            if cached:
                return from_game(cached, "your own earlier visit")
            raise GameDataError(str(e)) from e
        spansh_time = str((st or {}).get("market_updated_at") or "").replace(" ", "T")
        if cached and (not st or not st.get("market") or cached.get("timestamp", "") > spansh_time):
            return from_game(cached, "your own earlier visit")
        if not st:
            raise GameDataError(f"Couldn't find a station called {station}" + (f" in {system}." if system else "."))
        if not st.get("market"):
            raise GameDataError(f"{st.get('name')} has no market data.")
        items = [{"commodity": c.get("commodity"), "category": c.get("category"), "buy_price": c.get("buy_price"),
                  "stock": c.get("supply"), "sell_price": c.get("sell_price"), "demand": c.get("demand")}
                 for c in st["market"]]
        return st.get("name"), st.get("system_name"), items, "Spansh (player uploads)", st.get("market_updated_at")

    def trade_between(self, from_station: str | None = None, from_system: str | None = None,
                      to_station: str | None = None, to_system: str | None = None, max_results: int = 8) -> dict:
        """What to buy at one market to sell at another, by profit per ton. Defaults: from the docked
        station, to the destination (targeted station, or the markets in the route's end system)."""
        s = self._snapshot()
        if not from_station:
            if not s["location"].get("docked"):
                raise GameDataError("You're not docked; name the station to buy from.")
            from_station, from_system = s["location"].get("station"), s["location"].get("system")
        a_name, a_sys, a_items, a_src, a_upd = self._market_items(from_station, from_system)
        buyable = {i["commodity"].lower(): i for i in a_items if i["commodity"] and i["buy_price"] and i["stock"]}

        if to_station:
            targets = [(to_station, to_system)]
        else:
            dest_station, dest_system = self._destination()
            to_system = to_system or dest_system
            if dest_station and not to_system or (dest_station and dest_system == to_system):
                targets = [(dest_station, dest_system)]
            elif to_system:
                stations = [x for x in self.system_stations(to_system)["stations"] if "market" in x["services"]]
                targets = [(x["name"], to_system) for x in stations[:4]]
            else:
                raise GameDataError("No destination set; name a station or system to sell at.")
        cargo_space = self.watcher.state.ship.cargo_capacity or 0
        results = []
        for station, system in targets:
            try:
                b_name, b_sys, b_items, b_src, b_upd = self._market_items(station, system)
            except GameDataError as e:
                results.append({"station": station, "error": str(e)})
                continue
            trades = []
            for i in b_items:
                src = buyable.get((i["commodity"] or "").lower())
                if src and i["sell_price"] and i["demand"] and i["sell_price"] > src["buy_price"]:
                    tons = min(src["stock"], i["demand"], cargo_space or src["stock"])
                    profit = i["sell_price"] - src["buy_price"]
                    trades.append({"commodity": i["commodity"], "buy_at": src["buy_price"], "sell_at": i["sell_price"],
                                   "profit_per_ton": profit, "tons_possible": tons, "trip_profit": profit * tons})
            trades.sort(key=lambda t: -t["trip_profit"])
            results.append({"sell_station": b_name, "system": b_sys, "market_updated": b_upd, "source": b_src,
                            "best_trades": trades[:max_results]})
        return {"buy_station": a_name, "buy_system": a_sys, "buy_market_updated": a_upd, "cargo_capacity": cargo_space,
                "destinations": results,
                "note": "trip_profit assumes a full hold limited by stock and demand; prices move, so check dates."}

    # ---- trade rankings ----

    def set_home_base(self, station: str, system: str) -> dict:
        """Set the home base used by home trade rankings."""
        self.notes.set_home(station, system)
        return {"home": {"station": station, "system": system}}

    def best_trades(self, rank: str = "hold", max_distance_ly: float = 40, max_age_days: int = 30,
                    from_station: str | None = None, from_system: str | None = None, candidates: int = 8) -> dict:
        """What to buy here and where to sell it, ranked by: hold (profit per full trip), hour (profit
        per estimated hour, so far-from-star stations rank lower), round_trip (with the best cargo back)
        or home (trades to or from the home base)."""
        s = self._snapshot()
        home = self.notes.home()
        if not from_station:
            if not s["location"].get("docked"):
                raise GameDataError("You're not docked; name the station to buy from.")
            from_station, from_system = s["location"].get("station"), s["location"].get("system")
        if rank == "home" and not home:
            raise GameDataError("No home base set yet. Say \"set my home base\" while docked there.")
        if rank == "home" and (from_station or "").lower() != home["station"].lower():
            trip = self.trade_between(from_station, from_system, home["station"], home["system"])
            return {"rank": "home", "home": home, "trades_to_home": trip}

        name, system, items, source, updated = self._market_items(from_station, from_system)
        ship = self.watcher.state.ship
        free = max((ship.cargo_capacity or 0) - sum(s["owned_cargo"].values()) - sum(c["count"] for c in s["mission_cargo"]), 0)
        hold = free or ship.cargo_capacity or 0  # hold full: plan for after they unload
        credits = s.get("credits") or 0
        pad = spansh.needs_large_pad(ship.internal)
        buyable = [i for i in items if i["buy_price"] and i["stock"]]
        # Most promising first: biggest gap under the galactic average (live data), else highest value.
        buyable.sort(key=lambda i: -((i.get("galactic_average") or i["buy_price"] * 1.2) - i["buy_price"])
                     * min(i["stock"], hold or i["stock"]))
        shortlist = buyable[:candidates]

        def sell_options(item):
            tons = max(1, min(hold or item["stock"], item["stock"], credits // max(item["buy_price"], 1)))
            try:
                return item, tons, spansh.find_best_price(item["commodity"], system, True, max_distance_ly, tons, pad,
                                                          max_results=2, max_age_days=max_age_days)
            except spansh.SpanshError:
                try:
                    return item, tons, ardent.nearby(item["commodity"], system, True, max_distance_ly, tons, pad,
                                                     max_age_days, max_results=2)
                except ardent.ArdentError:
                    return item, tons, []

        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(4) as pool:
            options = list(pool.map(sell_options, shortlist))
        trades = []
        for item, tons, rows in options:
            for r in rows:
                if r["system"] == system and r["station"] == name:
                    continue
                tons_sold = min(tons, r.get("demand") or tons)
                profit = (r["sell_price"] - item["buy_price"]) * tons_sold
                if profit <= 0:
                    continue
                minutes = _trip_minutes(r["distance_ly"], ship.max_jump_range or 20, r["distance_to_arrival_ls"])
                trades.append({"commodity": item["commodity"], "buy_at": item["buy_price"], "sell_at": r["sell_price"],
                               "sell_station": r["station"], "sell_system": r["system"], "distance_ly": r["distance_ly"],
                               "station_from_star_ls": r["distance_to_arrival_ls"], "tons": tons_sold,
                               "trip_profit": profit, "est_minutes": round(minutes), "profit_per_hour": round(profit / minutes * 60),
                               "sell_price_updated": r.get("market_updated")})
                break  # the best buyer for this commodity
        if rank == "round_trip":
            for t in sorted(trades, key=lambda t: -t["trip_profit"])[:3]:
                try:
                    back = self.trade_between(t["sell_station"], t["sell_system"], name, system, max_results=1)
                    best = (back["destinations"][0].get("best_trades") or [None])[0]
                except GameDataError:
                    best = None
                t["return_cargo"] = best
                t["round_trip_profit"] = t["trip_profit"] + (best["trip_profit"] if best else 0)
                t["round_trip_minutes"] = t["est_minutes"] * 2
        key = {"hold": lambda t: -t["trip_profit"], "hour": lambda t: -t["profit_per_hour"],
               "round_trip": lambda t: -t.get("round_trip_profit", -1), "home": lambda t: -t["trip_profit"]}.get(rank)
        if key is None:
            raise GameDataError("rank must be hold, hour, round_trip or home.")
        trades.sort(key=key)
        return {"rank": rank, "buy_at": name, "system": system, "market_source": source, "market_updated": updated,
                "free_cargo_t": free, "planned_for_t": hold,
                "hold_note": None if free else "Hold is full: profits assume you unload first and carry a full hold.",
                "within_ly": max_distance_ly, "trades": trades[:5],
                "note": "Minutes are rough: jumps, supercruise by distance from star, docking. Sell prices come "
                        "from player uploads; check the dates."}

    def market(self, station: str | None = None, system: str | None = None, where: str = "here",
               commodity: str | None = None, category: str | None = None) -> dict:
        """A station's market: here (live), the route destination, or any named station."""
        s = self._snapshot()
        if not station and where == "destination":
            station, system = self._destination()
            if not station:
                if not system:
                    raise GameDataError("Nothing is targeted or plotted. Name a station.")
                stations = self.system_stations(system)
                return {"note": f"Your destination is the system {system}, not a station. Pick one of its markets.",
                        "stations_with_markets": [x for x in stations["stations"] if "market" in x["services"]][:12]}
        loc = s["location"]
        if not station:
            if not loc.get("docked"):
                raise GameDataError("You're not docked. Name a station, or ask about your destination.")
            station, system = loc.get("station"), loc.get("system")

        station, system, items, source, updated = self._market_items(station, system)
        result = {"station": station, "system": system, "source": source, "updated": updated}
        if commodity:
            hits = [i for i in items if commodity.lower() in (i["commodity"] or "").lower()]
            return result | {"commodities": hits or f"{commodity} isn't traded here."}
        by_name = {i["commodity"].lower(): i for i in items if i["commodity"]}
        cargo = {name: count for name, count in s["cargo"].items() if name.lower() != "limpet"}
        result["your_cargo_here"] = [
            {"commodity": n, "tons": c, "sell_price": by_name.get(n.lower(), {}).get("sell_price") or "not bought here",
             "value": (by_name.get(n.lower(), {}).get("sell_price") or 0) * c} for n, c in cargo.items()]
        if category:
            items = [i for i in items if category.lower() in (i.get("category") or "").lower()]
        # Complete lists (compact strings), so answers never rest on a partial view.
        sold = sorted([i for i in items if i["stock"] and i["buy_price"]], key=lambda i: -(i["buy_price"] or 0))
        wanted = sorted([i for i in items if i["demand"] and i["sell_price"]], key=lambda i: -(i["sell_price"] or 0))
        result["sells_to_you"] = [f"{i['commodity']}: {i['buy_price']} cr, stock {i['stock']}" for i in sold]
        result["buys_from_you"] = [f"{i['commodity']}: {i['sell_price']} cr, demand {i['demand']}" for i in wanted]
        result["complete_lists"] = True
        result["totals"] = {"commodities_sold": len(sold), "commodities_wanted": len(wanted)}
        return result

    def best_price(self, commodity: str, selling: bool = True, max_distance_ly: float = 50, quantity: int = 1,
                   near_system: str | None = None, large_pad: bool | None = None, max_age_days: int = 30) -> dict:
        """The best place nearby to sell (or buy) a commodity, with recent prices."""
        if material := self._material_info(commodity):
            return material
        commodity = market_name(commodity)
        system, pad = self._system(near_system), self._large_pad(large_pad)
        try:
            rows = spansh.find_best_price(commodity, system, selling, max_distance_ly, quantity, pad,
                                          max_age_days=max_age_days)
        except spansh.SpanshError:
            rows = self._ardent(commodity, system, selling, max_distance_ly, quantity, pad, max_age_days, 5)
        rows = [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]
        result = {"commodity": commodity, "mode": "sell" if selling else "buy", "near_system": system,
                  "within_ly": max_distance_ly, "large_pad_required": pad, "stations": rows}
        if not rows:
            result["hint"] = ("Nothing found. Try a larger max_distance_ly or max_age_days, a smaller quantity, "
                              "or check the spelling.")
        return result

    def _ardent(self, commodity, system, selling, max_ly, qty, pad, max_age, n) -> list[dict]:
        """Fallback when Spansh's market search is down."""
        try:
            return ardent.nearby(commodity, system, selling, max_ly, qty, pad, max_age, n)
        except ardent.ArdentError as e:
            raise GameDataError(f"Both galaxy data services failed: Spansh's market search is down and {e}") from e

    def _material_info(self, name: str) -> dict | None:
        """Engineering materials (Selenium, Polymer Capacitors) never appear on markets; say where they come from."""
        kind, display, count = material_kind(name, self.watcher.state.materials)
        if not kind:
            return None
        how = {"raw": "a raw material trader, mining (surface prospecting or asteroid cores) or rewards",
               "manufactured": "a manufactured material trader, ship salvage, signal sources or rewards",
               "encoded": "an encoded material trader, scanning ships, wakes and data points, or rewards"}[kind]
        return {"is_material": True, "material": display, "category": kind, "you_have": count,
                "where_to_get": f"It's an engineering material, not a market commodity: get it from {how}.",
                "next": f"Use find_service with service 'Material Trader' and trader_kind '{kind}' for the nearest trader."}

    # ---- refits, ship locker, trades along mission routes ----

    def recent_refits(self, minutes: float | None = None) -> list[dict]:
        """Modules bought, sold or swapped (in the last `minutes`, or all today)."""
        cutoff = ((datetime.now(timezone.utc) - timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")
                  if minutes else "")
        out = []
        for r in self.watcher.state.refits:
            if (r.get("time") or "") < cutoff:
                continue
            changes = []
            for c in r["changes"]:
                if "added" in c:
                    changes.append(f"{c['slot']}: added {_module_name(c['added'])}" + (f" ({_humanize(c['engineering'])})" if c.get("engineering") else ""))
                elif "removed" in c:
                    changes.append(f"{c['slot']}: removed {_module_name(c['removed'])}")
                elif "to" in c:
                    changes.append(f"{c['slot']}: {_module_name(c['from'])} -> {_module_name(c['to'])}"
                                   + (f" ({_humanize(c['engineering'])})" if c.get("engineering") else ""))
                else:
                    changes.append(f"{c['slot']}: {_module_name(c['module'])} engineering "
                                   f"{_humanize(c['engineering_from'] or 'none')} -> {_humanize(c['engineering_to'] or 'none')}")
            out.append({"time": r["time"], "ship": r["ship"], "ago": _ago(r["time"]), "changes": changes})
        return out

    def ship_locker(self, search: str | None = None) -> dict:
        """On-foot items in the ship locker and backpack."""
        rec = self.watcher.state.records.get("ShipLocker") or {}
        data = rec.get("_file") if not rec.get("Items") and rec.get("_file") else rec
        if not data:
            raise GameDataError("No ship locker data yet. It updates when you're on foot or open the locker.")
        out = {}
        for group in ("Items", "Components", "Consumables", "Data"):
            rows = {}
            for i in data.get(group, []):
                name = i.get("Name_Localised") or _humanize(i.get("Name", ""))
                if search and search.lower() not in name.lower():
                    continue
                rows[name] = rows.get(name, 0) + i.get("Count", 0)
            out[group.lower()] = dict(sorted(rows.items(), key=lambda kv: -kv[1]))
        backpack = self.watcher.state.records.get("Backpack") or {}
        bp = backpack.get("_file") or backpack
        carried = {i.get("Name_Localised") or i.get("Name"): i.get("Count", 0)
                   for g in ("Items", "Components", "Consumables", "Data") for i in bp.get(g, [])}
        return {"as_of": data.get("timestamp"), "ship_locker": out, "backpack": carried or None}

    def trades_along_missions(self, include_return: bool = True, max_stops: int = 3) -> dict:
        """For each place their active missions send them: what to buy here to sell there, and what to
        buy there to bring back and sell at home (only when a home base is set)."""
        s = self._snapshot()
        home = self.notes.home()
        stops = []
        for m in s["missions"]:
            if m["status"].startswith(("ready", "expired", "MISSION CARGO LOST")):
                continue
            system = m.get("destination_system")
            if system and system not in [x["system"] for x in stops] and system != s["location"]["system"]:
                stops.append({"system": system, "for": m.get("name")})
        if not stops:
            raise GameDataError("No active missions with somewhere to go.")
        stops = stops[:max_stops]
        here_station = s["location"].get("station") if s["location"].get("docked") else None
        results = []
        for stop in stops:
            entry = dict(stop)
            if here_station:
                try:
                    out = self.trade_between(to_system=stop["system"], max_results=2)
                    best = max((d for d in out["destinations"] if d.get("best_trades")),
                               key=lambda d: d["best_trades"][0]["trip_profit"], default=None)
                    entry["take_there"] = ({**best["best_trades"][0], "sell_station": best["sell_station"]} if best else None)
                except GameDataError as e:
                    entry["take_there"] = str(e)
            if include_return and home:
                try:
                    stations = [x for x in self.system_stations(stop["system"])["stations"] if "market" in x["services"]][:2]
                    backs = []
                    for st in stations:
                        try:
                            back = self.trade_between(st["name"], stop["system"], home["station"], home["system"], max_results=1)
                            t = (back["destinations"][0].get("best_trades") or [None])[0]
                            if t:
                                backs.append({**t, "buy_station": st["name"]})
                        except GameDataError:
                            continue
                    entry["bring_home"] = max(backs, key=lambda t: t["trip_profit"]) if backs else None
                except GameDataError as e:
                    entry["bring_home"] = str(e)
            results.append(entry)
        return {"from": here_station or "(not docked: no outbound trades)", "home": home, "stops": results,
                "note": "Profits assume a full hold; prices come from player uploads, so check the dates."}

    # ---- engineering materials, shopping list, outfitting here ----

    def engineering_materials(self, module: str, blueprint: str | None = None, experimental: str | None = None,
                              up_to_grade: int = 5) -> dict:
        """Materials needed for a blueprint or experimental, against what's held."""
        from . import engineering_materials as em
        held = em.held_counts(self.watcher.state.materials)
        out = em.experimental_cost(module, experimental, held) if experimental else em.check(module, blueprint, held, up_to_grade)
        if "error" in out and not out.get("blueprints") and not out.get("experimentals"):
            raise GameDataError(out["error"])
        return out

    def shopping_list(self, add: str | None = None, count: int | None = None, remove: str | None = None) -> dict:
        """Show, add to or remove from the material shopping list."""
        if add:
            for item in [x.strip() for x in add.split(",") if x.strip()]:
                self.notes.shop(item, count)
        if remove:
            for item in [x.strip() for x in remove.split(",") if x.strip()]:
                self.notes.shop(item, remove=True)
        items = self.notes.shopping()
        held = {}
        for cat in self.watcher.state.materials.values():
            for _, (display, n) in cat.items():
                held[display.lower()] = n
        rows = []
        for name, want in items.items():
            kind = material_kind(name, self.watcher.state.materials)[0]
            rows.append({k: v for k, v in {"item": name, "want": want or None, "have": held.get(name.lower()),
                                           "trader": kind}.items() if v is not None})
        return {"shopping_list": rows, "note": "Read out when they dock at a material trader of the right kind."}

    def outfitting_here(self, size: int | None = None, engineerable_only: bool = False) -> dict:
        """Modules for sale at the station's outfitting (from the last outfitting screen)."""
        from . import engineers as eng
        o = self.watcher.state.outfitting
        loc = self.watcher.state.location
        if not o:
            raise GameDataError("I haven't seen an outfitting list yet. Open outfitting once and ask again.")
        here = o.get("station") == loc.station
        rows = []
        for item, price in o["items"]:
            name = _module_name(item)
            if not name or item.lower().startswith(("paintjob", "decal", "nameplate", "bobble", "voicepack")):
                continue
            m = re.search(r"size(\d+)", item.lower())
            if size and (not m or int(m.group(1)) != size):
                continue
            base = re.sub(r"^\d[A-Z] ", "", name)
            mtype = eng.module_type(base)
            if engineerable_only and not mtype:
                continue
            rows.append({k: v for k, v in {"module": name, "price": price,
                                           "engineerable": bool(mtype) or None}.items() if v is not None})
        return {"station": o.get("station"), "system": o.get("system"), "seen": _ago(o.get("time")),
                "this_is_where_you_are": here, "count": len(rows), "modules": rows[:120],
                "note": None if here else "This list is from the last outfitting screen they opened, not where they are."}

    # ---- engineering ----

    def find_engineer(self, module: str | None = None, engineer: str | None = None) -> dict:
        """Engineers for a module, or one engineer's details, with distance and unlock status."""
        rec = self.watcher.state.records.get("EngineerProgress") or {}
        progress = {(engineers.engineer_name(e.get("Engineer", "")) or e.get("Engineer")):  # "Tod 'The Blaster' McQuinn"
                    {"status": e.get("Progress"), "rank": e.get("Rank")} for e in rec.get("Engineers", [])}
        here = self.watcher.state.location.star_pos
        out = engineers.lookup(module, engineer, list(here) if here else None, progress)
        if "error" in out and not out.get("known"):
            raise GameDataError(out["error"])
        return out

    # ---- mining ----

    def mining_hotspots(self, commodity: str, near_system: str | None = None, max_results: int = 5,
                        pristine_only: bool = False, max_distance_ly: float | None = None,
                        rank: str = "nearest") -> dict:
        """Ring hotspots for a mineral near a system; also sets it as the mining target."""
        system = self._system(near_system)
        self.watcher.state.mining_target = spansh.ring_signal_name(commodity)
        try:
            rows = spansh.find_ring_hotspots(commodity, system, max_results, pristine_only,
                                             pages=8 if max_distance_ly else 2, max_distance_ly=max_distance_ly,
                                             rank=rank)
        except spansh.SpanshError as e:
            raise GameDataError(str(e)) from e
        if not rows:
            raise GameDataError(f"No {commodity} hotspots found near {system}. Check the spelling, or it may "
                                f"not be minable from rings.")
        return {"commodity": spansh.ring_signal_name(commodity), "near_system": system, "rings": rows,
                "notes": "Hotspots show on the ring once you're near it (or after a DSS map). Overlap means two "
                         "or more hotspots of the same mineral in one ring: better yields. Pristine reserves are "
                         "richest."}

    def _sell_best(self, commodity: str, system: str, max_ly: float, tons: int, pad: bool,
                   max_age_days: int = 30, n: int = 1) -> list[dict]:
        """Best places to sell, Spansh first, Ardent if Spansh's market search is down."""
        try:
            return spansh.find_best_price(commodity, system, True, max_ly, tons, pad, max_results=n,
                                          max_age_days=max_age_days)
        except spansh.SpanshError:
            try:
                return ardent.nearby(commodity, system, True, max_ly, tons, pad, max_age_days, n)
            except ardent.ArdentError:
                return []

    def hold_value(self, max_distance_ly: float = 150, max_age_days: int = 30, one_station: bool = False) -> dict:
        """What everything in the hold (not limpets or mission cargo) fetches at the best price nearby.
        one_station: the single station that pays most for the whole lot."""
        from concurrent.futures import ThreadPoolExecutor
        s = self._snapshot()
        owned = {market_name(n): c for n, c in s["owned_cargo"].items() if c and n.lower() not in ("limpet", "drones")}
        if not owned:
            raise GameDataError("Your hold is empty, apart from limpets.")
        system, pad = s["location"]["system"], self._large_pad(None)
        n = 15 if one_station else 1
        with ThreadPoolExecutor(max_workers=4) as pool:
            found = dict(zip(owned, pool.map(lambda kv: self._sell_best(kv[0], system, max_distance_ly, kv[1], pad,
                                                                        max_age_days, n), owned.items())))
        split_total = sum(owned[c] * rows[0]["sell_price"] for c, rows in found.items() if rows)
        if one_station:
            stations: dict = {}
            for c, rows in found.items():
                for r in rows:
                    st = stations.setdefault((r["station"], r["system"]), {"row": r, "prices": {}})
                    st["prices"][c] = r["sell_price"]
            ranked = sorted(stations.values(), key=lambda st: (-len(st["prices"]),
                            -sum(owned[c] * p for c, p in st["prices"].items())))
            if not ranked:
                raise GameDataError(f"No station within {max_distance_ly} ly buys any of your hold.")
            best = ranked[0]
            total = sum(owned[c] * p for c, p in best["prices"].items())
            r = best["row"]
            return {"near_system": system, "sell_all_at": f"{r['station']}, {r['system']}",
                    "distance_ly": r["distance_ly"], "total_value": total,
                    "items": [{"commodity": c, "tons": owned[c], "price": best["prices"].get(c),
                               "value": owned[c] * best["prices"][c] if c in best["prices"] else None}
                              for c in owned],
                    "not_bought_there": [c for c in owned if c not in best["prices"]] or None,
                    "selling_each_at_its_best_station": split_total,
                    "cost_of_one_stop": split_total - total,
                    "recommend": ("one stop" if split_total and (split_total - total) / split_total <= 0.10
                                  else "split the sale: one stop loses more than 10%. Use plan_sale for the route"),
                    "note": "One stop is simpler; selling each item at its own best station pays "
                            "cost_of_one_stop more. Prices come from player uploads."}
        items, total = [], 0
        for name, tons in sorted(owned.items(), key=lambda kv: -kv[1]):
            best = (found.get(name) or [None])[0]
            if best:
                value = tons * best["sell_price"]
                total += value
                items.append({"commodity": name, "tons": tons, "price": best["sell_price"], "value": value,  # noqa
                              "sell_at": f"{best['station']}, {best['system']}", "distance_ly": best["distance_ly"],
                              "demand": best.get("demand"), "market_updated": best.get("market_updated")})
            else:
                items.append({"commodity": name, "tons": tons,
                              "price": None, "note": f"No buyer for {tons} t within {max_distance_ly} ly"})
        return {"near_system": system, "total_value": total, "items": items,
                "note": "Each item at its own best station within range, with enough demand for the whole lot. "
                        "Prices come from player uploads: check the dates."}

    def plan_sale(self, max_distance_ly: float = 150, max_age_days: int = 30) -> dict:
        """Where to sell the hold: the best one-stop and two-stop routes, with travel time and what the extra
        stop earns."""
        import math
        from concurrent.futures import ThreadPoolExecutor
        from itertools import permutations
        s = self._snapshot()
        owned = {market_name(n): c for n, c in s["owned_cargo"].items() if c and n.lower() not in ("limpet", "drones")}
        if not owned:
            raise GameDataError("Your hold is empty, apart from limpets.")
        system, pad = s["location"]["system"], self._large_pad(None)
        here = tuple(s["location"].get("star_pos") or (0, 0, 0))
        jump = self.watcher.state.ship.max_jump_range or 20
        with ThreadPoolExecutor(max_workers=4) as pool:
            found = dict(zip(owned, pool.map(lambda kv: self._sell_best(kv[0], system, max_distance_ly, kv[1], pad,
                                                                        max_age_days, 8), owned.items())))
        stations: dict = {}
        for c, rows in found.items():
            for r in rows:
                st = stations.setdefault((r["station"], r["system"]), {"row": r, "prices": {}})
                st["prices"][c] = r["sell_price"]
        if not stations:
            raise GameDataError(f"No station within {max_distance_ly} ly buys anything in your hold.")

        def leg(a: tuple, b: dict) -> float:
            xyz = b["row"].get("_xyz")
            ly = math.dist(a, xyz) if xyz and any(xyz) else b["row"]["distance_ly"]
            return _trip_minutes(ly, jump, b["row"].get("distance_to_arrival_ls") or 0)

        def describe(stops: list) -> dict:
            value, sold, minutes, pos, out = 0, set(), 0.0, here, []
            for st in stops:
                take = {c: st["prices"][c] for c in st["prices"] if c not in sold and
                        st["prices"][c] >= max(o["prices"].get(c, 0) for o in stops)}
                minutes += leg(pos, st)
                pos = st["row"].get("_xyz") or pos
                v = sum(owned[c] * p for c, p in take.items())
                value += v
                sold |= set(take)
                out.append({"station": f"{st['row']['station']}, {st['row']['system']}", "sell": {
                    c: f"{owned[c]} t at {p:,}" for c, p in take.items()}, "value": v})
            return {"stops": out, "total_value": value, "minutes": round(minutes),
                    "unsold": [c for c in owned if c not in sold] or None}

        cands = list(stations.values())
        one = max((describe([st]) for st in cands), key=lambda p: p["total_value"])
        top = sorted(cands, key=lambda st: -sum(owned[c] * p for c, p in st["prices"].items()))[:8]
        two = max((describe(list(pair)) for pair in permutations(top, 2)),
                  key=lambda p: (p["total_value"], -p["minutes"]), default=None)
        if two and two["total_value"] <= one["total_value"]:
            two = None
        gain = (two["total_value"] - one["total_value"]) if two else 0
        extra_min = (two["minutes"] - one["minutes"]) if two else 0
        worth = bool(two) and gain >= max(500_000, 0.10 * one["total_value"])
        return {"from": system, "one_stop": one, "two_stops": two,
                "second_stop_adds": gain or None, "second_stop_extra_minutes": extra_min if two else None,
                "recommend": "two stops" if worth else "one stop",
                "note": "Times are rough. Prices come from player uploads, so check the dates. Copy the first "
                        "stop's system for them."}

    def plan_mining_run(self, commodity: str | None = None, method: str = "laser", tons: int | None = None,
                        tons_per_hour: float | None = None, near_system: str | None = None,
                        max_sell_ly: float = 150) -> dict:
        """Hotspot + where to sell + profit per hour, for one commodity or a comparison of the usual targets."""
        from concurrent.futures import ThreadPoolExecutor
        s = self._snapshot()
        start, pad = self._system(near_system), self._large_pad(None)
        ship = self.watcher.state.ship
        jump = ship.max_jump_range or 20
        cap = ship.cargo_capacity or 0
        hold = tons or max(cap, 1)  # the limpets aboard get used up while mining, so a run fills the whole hold
        m = self.watcher.state.mining
        rate, rate_from = tons_per_hour, "you gave it"
        if not rate and m and sum(m.get("refined", {}).values()) >= 10:
            mins = max(1.0, _seconds_between(m["start"], m["last"]) / 60)
            rate, rate_from = sum(m["refined"].values()) / mins * 60, "your current session"
        if not rate:
            rate, rate_from = MINING_RATES.get(method, 100), f"a typical {method} mining rate (a guess)"
        targets = [commodity] if commodity else MINING_TARGETS.get(method, MINING_TARGETS["laser"])

        def plan(c: str) -> dict:
            try:
                rings = spansh.find_ring_hotspots(c, start, 3, pages=4, max_distance_ly=50, rank="most_hotspots")
            except spansh.SpanshError as e:
                return {"commodity": c, "error": str(e)}
            if not rings:
                return {"commodity": c, "error": "no hotspot found within 50 ly"}
            options = [plan_ring(c, r) for r in rings]
            good = [o for o in options if "per_hour" in o]
            return max(good, key=lambda o: o["per_hour"]) if good else options[0]

        def plan_ring(c: str, ring: dict) -> dict:
            sells = self._sell_best(market_name(c), ring["system"], max_sell_ly, hold, pad, n=2)
            if not sells:
                return {"commodity": c, "hotspot": ring, "error": f"no station buys {hold} t within {max_sell_ly} ly"}
            sell = sells[0]
            out_min = _trip_minutes(ring["distance_ly"], jump, ring["distance_to_arrival_ls"]) - 1.5  # no docking
            mine_min = hold / rate * 60
            back_min = _trip_minutes(sell["distance_ly"], jump, sell["distance_to_arrival_ls"])
            total_min = out_min + mine_min + back_min
            profit = hold * sell["sell_price"]
            return {
                "commodity": market_name(c),
                "mine_at": f"{ring['ring']} ({ring['ring_type']}, {ring['reserve']}, {ring['hotspots']} hotspot"
                           f"{'s' if ring['hotspots'] > 1 else ''})",
                "mine_system": ring["system"], "ring_distance_ly": ring["distance_ly"],
                "ring_from_star_ls": ring["distance_to_arrival_ls"],
                "sell_at": f"{sell['station']}, {sell['system']}", "sell_distance_from_ring_ly": sell["distance_ly"],
                "price": sell["sell_price"], "demand": sell.get("demand"), "market_updated": sell.get("market_updated"),
                "hold_tons": hold, "run_value": profit,
                "minutes": {"to_ring": round(out_min), "mining": round(mine_min), "to_market": round(back_min),
                            "total": round(total_min)},
                "per_hour": round(profit / total_min * 60) if total_min else None,
            }

        with ThreadPoolExecutor(max_workers=3) as pool:
            plans = list(pool.map(plan, targets))
        good = sorted((p for p in plans if "per_hour" in p), key=lambda p: -p["per_hour"])
        if good:
            self.watcher.state.mining_target = spansh.ring_signal_name(good[0]["commodity"])
        return {"from": start, "method": method, "hold_tons": hold,
                "mining_rate_tph": round(rate), "rate_from": rate_from,
                "plans": good + [p for p in plans if "per_hour" not in p],
                "note": "Times are rough (jump count from your unladen range; a full hold jumps shorter). The mining "
                        "rate dominates: give tons_per_hour once you know yours. Copy the best mine_system for them."}

    def mining_session(self) -> dict:
        """This mining session: tons refined, rate, rocks prospected, limpets used."""
        m = self.watcher.state.mining
        if not m:
            raise GameDataError("No mining yet. A session starts with your first prospector limpet.")
        minutes = max(1.0, _seconds_between(m["start"], m["last"]) / 60)
        tons = sum(m["refined"].values())
        s = self.watcher.state
        cap = s.ship.cargo_capacity
        used = sum(c for c in s.cargo.values() if isinstance(c, (int, float)))
        return {k: v for k, v in {
            "where": m.get("ring") or m.get("system"), "started": _ago(m["start"]), "minutes": round(minutes),
            "refined_tons": m["refined"], "total_tons": tons,
            "tons_per_hour": round(tons / minutes * 60) if tons else 0,
            "asteroids_prospected": m["prospected"], "cores_found": m["cores_found"], "cores_cracked": m["cracked"],
            "limpets_launched": m["limpets"], "limpets_left": s.cargo.get("Limpet", 0),
            "hold": f"{used} of {cap} tons" if cap else None,
            "last_prospect": m.get("last_prospect"),
        }.items() if v is not None}

    def mining_fit(self) -> dict:
        """Whether the ship is fitted for mining: refinery, limpet controllers, limpets, space."""
        lo = self.watcher.state.records.get("Loadout")
        if not lo:
            raise GameDataError("No ship loadout in today's journal yet.")
        s = self.watcher.state
        out = mining.fit_check(lo.get("Modules", []), s.cargo, s.ship.cargo_capacity or lo.get("CargoCapacity"))
        out["ship"] = f"{s.ship.type or SHIP_TYPES.get(lo.get('Ship', ''), _humanize(lo.get('Ship', '')))} '{s.ship.name or lo.get('ShipName')}'"
        return out

    # ---- stored ships and modules ----

    def ship_details(self, ship: str | None = None) -> dict:
        """Jump range, cargo, value and modules of any of their ships, from the last time they flew it."""
        profiles = self.watcher.state.ship_profiles
        if not profiles:
            raise GameDataError("No ship loadouts in the journals yet.")
        def label(p: dict) -> str:
            return f"{SHIP_TYPES.get(p['ship'] or '', _humanize(p['ship'] or ''))} '{p.get('name') or '?'}'"
        if not ship:
            return {"ships": [{"ship": label(p), "jump_range_ly": p["jump_range_ly"], "cargo": p["cargo_capacity"],
                               "last_flown": _ago(p["as_of"])} for p in profiles.values()]}
        q = "".join(ch for ch in ship.lower() if ch.isalnum())
        hits = [p for p in profiles.values()
                if q in "".join(ch for ch in label(p).lower() if ch.isalnum()) or q in (p["ship"] or "")]
        if not hits:
            raise GameDataError(f"No ship of yours matches {ship}. Your ships: "
                                + ", ".join(label(p) for p in profiles.values()))
        p = max(hits, key=lambda p: p["as_of"] or "")
        skip = ("Decal", "ShipName", "ShipID", "PaintJob", "Bobble", "VesselVoice", "ShipCockpit", "CargoHatch",
                "WeaponColour", "EngineColour", "ShipKitSpoiler", "ShipKitWings", "ShipKitTail", "ShipKitBumper")
        mods = [f"{m['slot']}: {_module_name(m['item'] or '')}" + (f" ({_humanize(m['engineering'])})" if m["engineering"] else "")
                for m in p["modules"] if not (m["slot"] or "").startswith(skip)]
        return {"ship": label(p), "ident": p.get("ident"), "jump_range_ly": p["jump_range_ly"],
                "cargo_capacity": p["cargo_capacity"], "fuel_capacity": p["fuel_capacity"], "rebuy": p["rebuy"],
                "value": (p.get("hull_value") or 0) + (p.get("modules_value") or 0) or None,
                "modules": mods, "as_of": _ago(p["as_of"]),
                "note": "From the last time they flew it; jump range is the game's maximum (empty hold)."}

    def stored_ships(self) -> dict:
        """Ships in storage, here and elsewhere (from the last shipyard visit)."""
        rec = self.watcher.state.records.get("StoredShips")
        if not rec:
            raise GameDataError("No stored-ships list yet. It updates whenever you open a shipyard.")
        def row(s, here):
            return {k: v for k, v in {"ship": s.get("ShipType_Localised") or (s.get("ShipType") or "").title(), "name": s.get("Name"),
                    "where": f"{rec.get('StationName')}, {rec.get('StarSystem')}" if here else s.get("StarSystem"),
                    "transfer_cost": s.get("TransferPrice"), "transfer_minutes": round(s["TransferTime"] / 60) if s.get("TransferTime") else None,
                    "in_transit": s.get("InTransit") or None, "hot": s.get("Hot") or None}.items() if v is not None}
        return {"as_of": rec.get("timestamp"), "seen_at": f"{rec.get('StationName')}, {rec.get('StarSystem')}",
                "here": [row(s, True) for s in rec.get("ShipsHere", [])],
                "elsewhere": [row(s, False) for s in rec.get("ShipsRemote", [])],
                "flying_now": self.watcher.state.ship.type}

    def stored_modules(self, search: str | None = None) -> dict:
        """Modules in storage (from the last outfitting visit)."""
        rec = self.watcher.state.records.get("StoredModules")
        if not rec:
            raise GameDataError("No stored-modules list yet. It updates whenever you open outfitting.")
        rows = []
        for m in rec.get("Items", []):
            name = _module_name(m.get("Name", "")) if not m.get("Name_Localised") else m["Name_Localised"]
            grade = _module_name(m.get("Name", "")).split(" ")[0] if m.get("Name", "").lower().startswith(("int_", "hpt_")) else ""
            label = f"{grade} {name}".strip() if grade and not name.startswith(grade) else name
            if search and search.lower() not in label.lower() and search.lower() not in m.get("Name", "").lower():
                continue
            rows.append({k: v for k, v in {
                "module": label, "where": m.get("StarSystem") or "in transit",
                "engineering": f"{_humanize(m['EngineerModifications'])} G{m.get('Level')}" if m.get("EngineerModifications") else None,
                "transfer_cost": m.get("TransferCost"), "hot": m.get("Hot") or None}.items() if v is not None})
        return {"as_of": rec.get("timestamp"), "count": len(rows), "modules": rows[:80],
                "truncated": len(rows) > 80}

    # ---- conversation memory (the daily transcripts) ----

    def recent_conversation(self, minutes: int = 60, max_lines: int = 60) -> dict:
        """What the commander and Vesper said recently."""
        return {"lines": history.recent(minutes, max_lines)}

    def search_conversations(self, query: str, days: int = 30) -> dict:
        """Past conversation lines matching a query."""
        hits = history.search(query, days)
        return {"query": query, "matches": hits, "count": len(hits)}

    # ---- stations, ships, modules ----

    def station_info(self, station: str, system: str | None = None) -> dict:
        """One station's details from Spansh: services, pads, economy, faction."""
        try:
            st = spansh.get_station(station, system)
        except spansh.SpanshError as e:
            raise GameDataError(str(e)) from e
        import difflib
        if st and difflib.SequenceMatcher(None, station.lower(), (st.get("name") or "").lower()).ratio() < 0.75:
            st = None  # Spansh answers a misheard name with a loose guess; check the system's own list instead
        if not st and system:
            try:
                names = [x["name"] for x in self.system_stations(system).get("stations", [])]
            except GameDataError:
                names = []
            guess = difflib.get_close_matches(station, names, n=1, cutoff=0.5)
            if guess:
                st = spansh.get_station(guess[0], system)
        if not st:
            raise GameDataError(f"Couldn't find a station called {station}.")
        return {
            "station": st.get("name"), "system": st.get("system_name"), "type": st.get("type"),
            "distance_to_arrival_ls": round(st.get("distance_to_arrival") or 0),
            "pads": {"large": st.get("large_pads"), "medium": st.get("medium_pads"), "small": st.get("small_pads")},
            "planetary": st.get("is_planetary"), "economy": st.get("primary_economy"),
            "government": st.get("government"), "controlling_faction": st.get("controlling_minor_faction"),
            "faction_state": st.get("controlling_minor_faction_state"), "power": st.get("system_power"),
            "services": [x.get("name") for x in st.get("services") or []],
            "ships_sold": [f"{x.get('name')} ({x.get('price'):,} cr)" for x in st.get("ships") or []],
            "modules_sold_count": len(st.get("modules") or []),
            "updated": {"market": st.get("market_updated_at"), "shipyard": st.get("shipyard_updated_at"),
                        "outfitting": st.get("outfitting_updated_at")},
        }

    def find_ship(self, ship: str, near_system: str | None = None) -> dict:
        """Nearest shipyards selling a ship."""
        system = self._system(near_system)
        try:
            rows = spansh.find_ship(ship, system)
        except spansh.SpanshError as e:
            raise GameDataError(str(e)) from e
        name = spansh.ship_name(ship)
        key = "".join(ch for ch in name.lower() if ch.isalnum())
        for r in rows:  # what they saw themselves beats player-uploaded data
            seen = self.watcher.state.shipyards.get(f"{r.get('station')}|{r.get('system')}")
            if seen:
                listed = any(key in "".join(ch for ch in s.lower() if ch.isalnum()) for s in seen["ships"])
                r["when_you_looked"] = (f"listed for credits ({_ago(seen['time'])})" if listed else
                                        f"NOT in the credit shipyard when you looked ({_ago(seen['time'])})")
        rows.sort(key=lambda r: "NOT in" in r.get("when_you_looked", ""))
        return {"ship": name, "near_system": system, "shipyards": rows,
                "caution": "Galaxy shipyard data comes from other players and can be out of date. Newer ships "
                           "(like the Caspian Explorer) may be sold only for Arx or as pre-built ships at first, "
                           "and then don't appear in the credit shipyard. Trust when_you_looked over the rest."}

    def shipyard_here(self) -> dict:
        """The ships for sale for credits at the station they're docked at, from the game itself."""
        loc = self.watcher.state.location
        seen = self.watcher.state.shipyards.get(f"{loc.station}|{loc.system}")
        if not seen:
            raise GameDataError("I haven't seen this shipyard's list yet. Open the shipyard once and ask again.")
        return {"station": loc.station, "system": loc.system, "as_of": _ago(seen["time"]),
                "ships_for_credits": seen["ships"],
                "note": "Ships sold only for Arx or as pre-built ships aren't in this list."}

    def find_module(self, module: str, size_class: int | None = None, rating: str | None = None,
                    near_system: str | None = None, large_pad: bool | None = None) -> dict:
        """Nearest outfitting selling a module, by size and rating."""
        system, pad = self._system(near_system), self._large_pad(large_pad)
        try:
            rows = spansh.find_module(module, system, size_class, rating, large_pad=pad)
        except spansh.SpanshError as e:
            raise GameDataError(str(e)) from e
        return {"module": module, "class": size_class, "rating": rating, "near_system": system,
                "large_pad_only": pad, "stations": rows}

    # ---- systems ----

    def system_bodies(self, system: str | None = None) -> dict:
        """A system's bodies from EDSM, merged with the commander's own scans."""
        system = self._system(system)
        try:
            result = edsm.system_bodies(system)
        except edsm.EdsmError as e:
            result = {"system": system, "edsm": str(e)}
        local = self.exploration(system)
        if local.get("bodies"):
            result["your_scans"] = local
        return result

    def system_stations(self, system: str | None = None) -> dict:
        """A system's stations from EDSM."""
        try:
            return edsm.system_stations(self._system(system))
        except edsm.EdsmError as e:
            raise GameDataError(str(e)) from e

    def galnet(self, count: int = 3) -> dict:
        """The latest GalNet articles."""
        try:
            return {"articles": galnet.latest(count)}
        except galnet.GalnetError as e:
            raise GameDataError(str(e)) from e

    # ---- ship and commander (local) ----

    def find_binding(self, action: str | None = None, device: str | None = None,
                     button: int | None = None) -> dict:
        """The commander's Elite control bindings: what an action is bound to, or what a button does."""
        from .bindings import lookup
        return lookup(action, device, button)

    def loadout(self) -> dict:
        """The ship's modules and engineering, plus refits in the last three hours."""
        result = self._loadout()
        result["recent_refits"] = self.recent_refits(minutes=180) or None
        return result

    def _loadout(self) -> dict:
        """The ship's modules and engineering from the last Loadout event."""
        lo = self.watcher.state.records.get("Loadout")
        if not lo:
            raise GameDataError("No ship loadout in today's journal yet.")
        modules = []
        for m in lo.get("Modules", []):
            name = _module_name(m.get("Item", ""))
            if not name:
                continue
            row = {"slot": m.get("Slot"), "module": name}
            if m.get("Health", 1) < 0.999:
                row["health"] = f"{m['Health'] * 100:.0f}%"
            if m.get("On") is False:
                row["off"] = True
            if eng := m.get("Engineering"):
                row["engineering"] = (f"{_humanize(eng.get('BlueprintName', ''))} G{eng.get('Level')}"
                                      + (f" + {eng['ExperimentalEffect_Localised']}" if eng.get("ExperimentalEffect_Localised") else ""))
            modules.append(row)
        return {
            "ship": self.watcher.state.ship.type, "name": lo.get("ShipName"), "ident": lo.get("ShipIdent"),
            "max_jump_range_ly": round(lo.get("MaxJumpRange") or 0, 2), "cargo_capacity": lo.get("CargoCapacity"),
            "fuel_capacity": lo.get("FuelCapacity"), "unladen_mass": lo.get("UnladenMass"),
            "hull_health": f"{(lo.get('HullHealth') or 0) * 100:.0f}%", "rebuy_cr": lo.get("Rebuy"),
            "value_cr": (lo.get("HullValue") or 0) + (lo.get("ModulesValue") or 0), "modules": modules,
        }

    def materials(self, search: str | None = None, category: str | None = None) -> dict:
        """Engineering materials held, optionally filtered by name or category."""
        mats = self.watcher.state.materials
        if not mats:
            raise GameDataError("No materials snapshot in today's journal yet.")
        out = {}
        for cat, items in mats.items():
            if category and category.lower() not in cat.lower():
                continue
            rows = sorted(((d, c) for d, c in items.values() if c and (not search or search.lower() in d.lower())),
                          key=lambda dc: -dc[1])
            if rows:
                out[cat] = {d: c for d, c in rows}
        return {"materials": out, "note": "Counts from the login snapshot, updated as you collect, trade and craft."}

    def commander_profile(self) -> dict:
        """Ranks with progress, superpower reputation, engineer progress and career stats."""
        rec = self.watcher.state.records
        rank, progress = rec.get("Rank", {}), rec.get("Progress", {})
        ranks = {}
        for key, names in RANK_NAMES.items():
            if key in rank:
                level = rank[key]
                ranks[key] = {"rank": _rank_name(names, level), "progress_to_next": f"{progress.get(key, 0)}%"}
        rep = {k: round(v) for k, v in rec.get("Reputation", {}).items() if isinstance(v, (int, float))}
        engineers = [{"name": e.get("Engineer"), "status": e.get("Progress"), "rank": e.get("Rank")}
                     for e in rec.get("EngineerProgress", {}).get("Engineers", [])]
        stats = rec.get("Statistics", {})
        pick = {"Bank_Account": ["Current_Wealth", "Spent_On_Ships", "Insurance_Claims"],
                "Combat": ["Bounties_Claimed", "Bounty_Hunting_Profit", "Assassinations"],
                "Exploration": ["Systems_Visited", "Total_Hyperspace_Distance", "Greatest_Distance_From_Start", "Time_Played"],
                "Trading": ["Market_Profits", "Resources_Traded"], "Mining": ["Mining_Profits"],
                "Exobiology": ["Organic_Data_Profits"]}
        highlights = {sec: {k: stats.get(sec, {}).get(k) for k in keys if k in stats.get(sec, {})}
                      for sec, keys in pick.items() if sec in stats}
        if "Exploration" in highlights and "Time_Played" in highlights["Exploration"]:
            highlights["Exploration"]["Time_Played_hours"] = round(highlights["Exploration"].pop("Time_Played") / 3600)
        return {"commander": self._snapshot()["commander"], "ranks": ranks, "superpower_reputation": rep,
                "engineers": engineers, "stats": highlights}

    def route_details(self) -> dict:
        """The rest of the plotted route, with each star's class and scoopability."""
        state = self.watcher.state
        if not state.route:
            raise GameDataError("No route plotted.")
        remaining = state.route_remaining
        stops = [{"system": st.get("StarSystem"), "star": st.get("StarClass"),
                  "scoopable": (st.get("StarClass") or "")[:1] in "KGBFOAM"} for st in remaining]
        return {"destination": state.route[-1].get("StarSystem"), "jumps_left": len(remaining),
                "unscoopable_stops": [x["system"] for x in stops if not x["scoopable"]], "stops": stops[:40],
                "fuel_now_t": state.fuel_main}

    def exploration(self, system: str | None = None) -> dict:
        """Bodies scanned in a system (default here): values, signals, first discoveries."""
        state = self.watcher.state
        here = state.location.system
        name = system or here
        address = state.location.system_address if name == here else next(
            (a for a, bodies in state.scans.items() if any(b.startswith(name) for b in bodies)), None)
        bodies = list(state.scans.get(address, {}).values()) if address else []
        scan = state.records.get("FSSDiscoveryScan", {})
        return {"system": name, "bodies_scanned": len(bodies),
                "bodies_in_system": scan.get("BodyCount") if scan.get("SystemName") == name else None,
                "all_found": state.records.get("FSSAllBodiesFound", {}).get("SystemName") == name,
                "bodies": sorted(bodies, key=lambda b: b.get("distance_ls", 0))[:40]}


MINING_RATES = {"laser": 120, "core": 60, "sub-surface": 80}  # tons/hour guesses until their own rate is known
MINING_TARGETS = {"laser": ["Platinum", "Painite", "Low Temperature Diamonds"],
                  "core": ["Void Opals", "Low Temperature Diamonds", "Alexandrite"],
                  "sub-surface": ["Painite", "Low Temperature Diamonds", "Void Opals"]}
MARKET_NAMES = {  # journal cargo ids and hotspot names -> market commodity names
    "lowtemperaturediamond": "Low Temperature Diamonds", "lowtemperaturediamonds": "Low Temperature Diamonds",
    "lowtempdiamond": "Low Temperature Diamonds", "lowtempdiamonds": "Low Temperature Diamonds",
    "ltd": "Low Temperature Diamonds", "ltds": "Low Temperature Diamonds", "opal": "Void Opals",
    "voidopal": "Void Opals", "voidopals": "Void Opals", "hydrogenperoxide": "Hydrogen Peroxide",
    "liquidoxygen": "Liquid oxygen", "methanolmonohydratecrystals": "Methanol Monohydrate Crystals",
    "methaneclathrate": "Methane Clathrate", "lithiumhydroxide": "Lithium Hydroxide",
}


def market_name(name: str) -> str:
    """The market's spelling of a commodity the commander said."""
    key = "".join(ch for ch in name.lower() if ch.isalnum())
    return MARKET_NAMES.get(key) or (name if any(ch.isupper() for ch in name) else name.title())


SHIP_TYPES = {"type9_military": "Type-10 Defender", "type9": "Type-9 Heavy", "type7": "Type-7 Transporter",
              "cutter": "Imperial Cutter", "anaconda": "Anaconda", "python": "Python", "federation_corvette":
              "Federal Corvette", "krait_mkii": "Krait Mk II", "type8": "Type-8 Transporter",
              "diamondbackxl": "Diamondback Explorer", "asp": "Asp Explorer", "federation_dropship_mkii": "Federal Gunship",
              "empire_trader": "Imperial Clipper", "cobramkiii": "Cobra Mk III", "viper": "Viper Mk III", "dolphin": "Dolphin",
              "keelback": "Keelback", "explorer_nx": "Caspian Explorer"}  # journal ids

RAW_MATERIALS = {
    "antimony", "arsenic", "boron", "cadmium", "carbon", "chromium", "germanium", "iron", "lead", "manganese",
    "mercury", "molybdenum", "nickel", "niobium", "phosphorus", "polonium", "rhenium", "ruthenium", "selenium",
    "sulphur", "technetium", "tellurium", "tin", "tungsten", "vanadium", "yttrium", "zinc", "zirconium",
}


# Common manufactured and encoded materials (they may hold none, so they're not in their own list).
MANUFACTURED_MATERIALS = {
    "polymercapacitors", "militarygradealloys", "protolightalloys", "protoradiolicalloys", "protoheatradiators",
    "imperialshielding", "pharmaceuticalisolators", "improvisedcomponents", "coredynamicscomposites",
    "fedcorecomposites", "fedproprietarycomposites", "biotechconductors", "exquisitefocuscrystals",
    "refinedfocuscrystals", "focuscrystals", "flawedfocuscrystals", "conductivepolymers", "conductiveceramics",
    "conductivecomponents", "basicconductors", "configurablecomponents", "electrochemicalarrays", "mechanicalcomponents",
    "mechanicalequipment", "mechanicalscrap", "heatexchangers", "heatvanes", "heatdispersionplate", "heatconductionwiring",
    "heatresistantceramics", "thermicalloys", "precipitatedalloys", "phasealloys", "galvanisingalloys", "salvagedalloys",
    "compoundshielding", "shieldingsensors", "shieldemitters", "wornshieldemitters", "highdensitycomposites",
    "filamentcomposites", "chemicalprocessors", "chemicaldistillery", "chemicalmanipulators", "chemicalstorageunits",
    "compactcomposites", "hybridcapacitors", "gridresistors", "crystalshards", "uncutfocuscrystals", "tgtc", "tgrc",
}
ENCODED_MATERIALS = {
    "classifiedscandata", "modifiedembeddedfirmware", "adaptiveencryptorscapture", "datamineddrive",
    "securityfirmwarepatch", "decodedemissiondata", "unusualencryptedfiles", "abnormalcompactemissionsdata",
    "anomalousbulkscandata", "unexpectedemissiondata", "exceptionalscrambledemissiondata", "irregularemissiondata",
    "shieldpatternanalysis", "shieldfrequencydata", "shielddensityreports", "shieldcyclerecordings",
    "shieldsoakanalysis", "fsdtelemetry", "dataminedwake", "strangewakesolutions", "eccentrichyperspace",
    "hyperspacetrajectories", "wakesolutions", "embeddedfirmware", "industrialfirmware", "consumerfirmware",
    "crackedindustrialfirmware", "taggedencryptioncodes", "openlysymmetrickeys", "atypicalencryptionarchives",
    "specialisedlegacyfirmware", "emissiondata", "scrambledemissiondata", "archivedemissiondata", "symmetrickeys",
    "securityfirmware", "compactemissionsdata", "bulkscandata", "encryptionarchives", "encryptioncodes",
}


def material_kind(name: str, held: dict) -> tuple[str | None, str, int]:
    """(raw|manufactured|encoded or None, display name, count held). Uses their own materials list (every
    material they've ever had a count of) plus the full raw-material list."""
    key = "".join(ch for ch in name.lower() if ch.isalnum())
    for cat, items in held.items():
        for internal, (display, count) in items.items():
            if key in ("".join(ch for ch in display.lower() if ch.isalnum()), internal.lower()):
                return cat.lower(), display, count
    for kind, names in (("raw", RAW_MATERIALS), ("manufactured", MANUFACTURED_MATERIALS), ("encoded", ENCODED_MATERIALS)):
        if key in names:
            return kind, name.strip().title(), 0
    return None, name, 0


def _trip_minutes(distance_ly: float, jump_range_ly: float, from_star_ls: float) -> float:
    """Rough door-to-door time: undock and leave, jumps, supercruise out to the station, dock."""
    import math
    jumps = math.ceil(distance_ly / max(jump_range_ly * 0.9, 1)) if distance_ly > 0.5 else 0
    supercruise = 45 + 1.5 * math.sqrt(max(from_star_ls, 0))  # ~1 min at 100 ls, ~15 min at 343,000 ls
    return (120 + jumps * 55 + supercruise + 90) / 60


def _ago(ts: str | None) -> str | None:
    """How long ago a timestamp was, in words, or None."""
    if not ts:
        return None
    try:
        secs = (datetime.now(timezone.utc) - datetime.fromisoformat(ts.replace("Z", "+00:00"))).total_seconds()
    except ValueError:
        return None
    if secs < 90:
        return "just now"
    if secs < 3600:
        return f"{secs // 60:.0f} minutes ago"
    if secs < 86400:
        return f"{secs / 3600:.1f} hours ago"
    return f"{secs / 86400:.1f} days ago"


RANK_NAMES = {
    "Combat": ["Harmless", "Mostly Harmless", "Novice", "Competent", "Expert", "Master", "Dangerous", "Deadly", "Elite"],
    "Trade": ["Penniless", "Mostly Penniless", "Peddler", "Dealer", "Merchant", "Broker", "Entrepreneur", "Tycoon", "Elite"],
    "Explore": ["Aimless", "Mostly Aimless", "Scout", "Surveyor", "Trailblazer", "Pathfinder", "Ranger", "Pioneer", "Elite"],
    "Soldier": ["Defenceless", "Mostly Defenceless", "Rookie", "Soldier", "Gunslinger", "Warrior", "Gladiator", "Deadeye", "Elite"],
    "Exobiologist": ["Directionless", "Mostly Directionless", "Compiler", "Collector", "Cataloguer", "Taxonomist",
                     "Ecologist", "Geneticist", "Elite"],
    "Empire": ["None", "Outsider", "Serf", "Master", "Squire", "Knight", "Lord", "Baron", "Viscount", "Count", "Earl",
               "Marquis", "Duke", "Prince", "King"],
    "Federation": ["None", "Recruit", "Cadet", "Midshipman", "Petty Officer", "Chief Petty Officer", "Warrant Officer",
                   "Ensign", "Lieutenant", "Lieutenant Commander", "Post Commander", "Post Captain", "Rear Admiral",
                   "Vice Admiral", "Admiral"],
}


def _rank_name(names: list[str], level: int) -> str:
    """The rank title for a level, including Elite I-V."""
    if level < len(names):
        return names[level]
    return f"Elite {['I', 'II', 'III', 'IV', 'V'][min(level - len(names), 4)]}" if names[-1] == "Elite" else str(level)


def _humanize(name: str) -> str:
    """An internal name in plain words: "python_nx" -> "Python Nx"."""
    return " ".join(w.capitalize() for w in name.replace("_", " ").split())


MODULE_WORDS = {  # journal shorthand -> the in-game name
    "mrascanner": "Pulse Wave Analyser", "mining abrblstr": "Abrasion Blaster",
    "mining seismchrgwarhd": "Seismic Charge Launcher", "mining subsurfdispmisle": "Sub-surface Displacement Missile",
    "mininglaser": "Mining Laser", "dronecontrol prospector": "Prospector Limpet Controller",
    "dronecontrol collection": "Collector Limpet Controller", "dronecontrol fueltransfer": "Fuel Transfer Limpet Controller",
    "dronecontrol repair": "Repair Limpet Controller", "dronecontrol resourcesiphon": "Hatch Breaker Limpet Controller",
    "dronecontrol recon": "Recon Limpet Controller", "detailedsurfacescanner": "Detailed Surface Scanner",
    "fighterbay": "Fighter Hangar", "cargorack": "Cargo Rack", "shieldbooster": "Shield Booster",
    "shieldgenerator": "Shield Generator", "shieldcellbank": "Shield Cell Bank", "hullreinforcement":
    "Hull Reinforcement", "modulereinforcement": "Module Reinforcement", "fuelscoop": "Fuel Scoop",
    "dockingcomputer advanced": "Advanced Docking Computer", "dockingcomputer standard": "Standard Docking Computer",
    "hyperdrive": "Frame Shift Drive", "powerdistributor": "Power Distributor", "powerplant": "Power Plant",
    "lifesupport": "Life Support", "fueltank": "Fuel Tank", "heatsinklauncher": "Heat Sink Launcher",
    "chafflauncher": "Chaff Launcher", "plasmapointdefence": "Point Defence", "electroniccountermeasure": "ECM",
    "cloudscanner": "Wake Scanner", "crimescanner": "Kill Warrant Scanner", "cargoscanner": "Manifest Scanner",
    "slugshot": "Fragment Cannon", "dumbfiremissilerack": "Missile Rack", "basicmissilerack": "Seeker Missile Rack",
    "planetapproachsuite advanced": "Planetary Approach Suite", "refinery": "Refinery",
}


def _module_name(item: str) -> str:
    """"int_fuelscoop_size6_class5" -> "6A Fuel Scoop"; "hpt_mrascanner_size0_class5" -> "0A Pulse Wave Analyser"."""
    parts = item.lower().split("_")
    if not parts or parts[0] not in ("int", "hpt"):
        return _humanize(item)
    size = next((p[4:] for p in parts if p.startswith("size")), None)
    cls = next((p[5:] for p in parts if p.startswith("class")), None)
    words = " ".join(p for p in parts[1:] if not p.startswith(("size", "class")))
    grade = f"{size}{'EDCBA'[int(cls) - 1]} " if size and cls and cls.isdigit() and 1 <= int(cls) <= 5 else ""
    for short, full in sorted(MODULE_WORDS.items(), key=lambda kv: -len(kv[0])):
        if words == short or words.startswith(short + " "):
            rest = words[len(short):].strip()
            return grade + full + (f" {_humanize(rest)}" if rest else "")
    return grade + _humanize(words)


# Tool descriptions shared by both front ends (MCP uses the docstrings below via
# its own decorators; the copilot sends these to Claude as tool definitions).
TOOL_SPECS = [
    BINDINGS_SPEC,
    {
        "name": "remember",
        "description": "Save a note that lasts between sessions, optionally as a reminder. when: 'none' (just remember "
                       "it), 'dock' (when they dock at place), 'arrive' (when they jump into system place), 'startup' "
                       "(next time the co-pilot starts), 'time' (give minutes_from_now or at_local_time like '21:30'). 'dock' "
                       "with no place means the next time they dock or land anywhere. "
                       "Write the note so it makes sense read aloud later, with names and numbers.",
        "input_schema": {"type": "object", "properties": {
            "text": {"type": "string"},
            "when": {"type": "string", "enum": ["none", "dock", "arrive", "startup", "time"]},
            "place": {"type": "string", "description": "Station or system name for dock/arrive."},
            "minutes_from_now": {"type": "integer", "minimum": 1},
            "at_local_time": {"type": "string", "description": "24h local time, e.g. '21:30'."},
        }, "required": ["text"]},
    },
    {
        "name": "recall_notes",
        "description": "The commander's saved notes and pending reminders, optionally filtered by a word. Use when they ask what they "
                       "wanted to remember, or when an old plan might matter.",
        "input_schema": {"type": "object", "properties": {
            "search": {"type": "string"}, "include_done": {"type": "boolean"},
        }},
    },
    {
        "name": "get_ship_locker",
        "description": "The commander's on-foot inventory: ship locker items, components, consumables and data (Odyssey), plus "
                       "what they're carrying in their backpack. Optional search.",
        "input_schema": {"type": "object", "properties": {"search": {"type": "string"}}},
    },
    {
        "name": "trades_along_missions",
        "description": "For each system their active missions send them to: the best cargo to buy where they're docked and "
                       "sell there, and the best cargo to buy there and bring back to sell at home. Use for 'what "
                       "can I trade on the way' around mission runs.",
        "input_schema": {"type": "object", "properties": {"include_return": {"type": "boolean"}}},
    },
    {
        "name": "check_engineering_materials",
        "description": "Materials for an engineering blueprint, grade by grade, against what they hold: cost per roll, "
                       "how many rolls they can afford at each grade, and what's missing. module (e.g. 'shield "
                       "generator') + blueprint (e.g. 'reinforced'), or experimental (e.g. 'hi-cap') for that "
                       "effect's cost. Use for 'can I do grade 4 reinforced shields?', 'what do I need for boosters?'.",
        "input_schema": {"type": "object", "properties": {
            "module": {"type": "string"}, "blueprint": {"type": "string"}, "experimental": {"type": "string"},
            "up_to_grade": {"type": "integer"}}, "required": ["module"]},
    },
    {
        "name": "shopping_list",
        "description": "The commander's material shopping list (kept between sessions). add: comma-separated materials to add "
                       "(with count if they say one); remove: ones to drop. With neither, read the list with how "
                       "many they have and which trader sells each. Use for 'I need Germanium and Mercury', 'what's on "
                       "my list'.",
        "input_schema": {"type": "object", "properties": {
            "add": {"type": "string"}, "count": {"type": "integer"}, "remove": {"type": "string"}}},
    },
    {
        "name": "get_outfitting_here",
        "description": "Modules for sale at the station they're docked at (from the game's own outfitting list, after "
                       "they open outfitting), with prices and whether each can be engineered. Filter by size "
                       "(class number) or engineerable_only. Use for 'what class 5 can I buy here to engineer?'.",
        "input_schema": {"type": "object", "properties": {
            "size": {"type": "integer"}, "engineerable_only": {"type": "boolean"}}},
    },
    {
        "name": "find_engineer",
        "description": "Engineering lookup, including how to find, get invited by and unlock each engineer. "
                       "With module (FSD, shield booster, refinery, collector limpets, multi-"
                       "cannon...): the blueprints, each engineer's highest grade, experimental effects, and each "
                       "engineer's system, distance from them and their own status with them (unlocked engineers "
                       "first). With engineer: where they are and everything they offer. Says so when a module "
                       "can't be engineered (mining lasers can't). Never name an engineer without this tool.",
        "input_schema": {"type": "object", "properties": {
            "module": {"type": "string"}, "engineer": {"type": "string"}}},
    },
    {
        "name": "plan_mining_run",
        "description": "Plan a mining run: nearest hotspot ring, best station to sell a full hold (with enough "
                       "demand), travel and mining time, and profit per hour. With no commodity it compares the "
                       "usual targets for the method (laser: Platinum, Painite, LTDs; core: Void Opals, LTDs, "
                       "Alexandrite). Use for 'what should I mine', 'plan a mining run'. Copy the mine system.",
        "input_schema": {"type": "object", "properties": {
            "commodity": {"type": "string"},
            "method": {"type": "string", "enum": ["laser", "core", "sub-surface"]},
            "tons_per_hour": {"type": "number", "description": "The commander's mining rate if they say it."},
            "tons": {"type": "integer", "description": "Tons they plan to mine. Default: the whole hold (limpets "
                                                         "get used up while mining)."}}},
    },
    {
        "name": "plan_sale",
        "description": "Where to sell everything in their hold: the best single stop and the best two-stop route "
                       "(which items at which station, in order), travel time, and what the second stop adds. Use for "
                       "'where should I sell this', 'is a second stop worth it'.",
        "input_schema": {"type": "object", "properties": {
            "max_distance_ly": {"type": "number", "description": "Default 150."}}},
    },
    {
        "name": "get_hold_value",
        "description": "What their hold is worth: each commodity at the best price nearby (with enough demand), and "
                       "the total. one_station=true finds the single station that pays most for everything, and "
                       "what that costs compared with selling each item at its own best station. Use for 'what's "
                       "my hold worth', 'where do I sell all this', 'sell everything in one place'.",
        "input_schema": {"type": "object", "properties": {
            "max_distance_ly": {"type": "number", "description": "Default 150."},
            "one_station": {"type": "boolean"}}},
    },
    {
        "name": "find_mining_hotspots",
        "description": "Nearest planetary rings with hotspots for a mineable commodity (Platinum, Painite, Low "
                       "Temperature Diamonds, Void Opals, Tritium...), with ring type, reserve level, overlaps "
                       "and other hotspots. Copy the system for them.",
        "input_schema": {"type": "object", "properties": {
            "commodity": {"type": "string"}, "near_system": {"type": "string"},
            "pristine_only": {"type": "boolean"}, "max_results": {"type": "integer"},
            "max_distance_ly": {"type": "number", "description": "Search radius; set it for 'within X ly'."},
            "rank": {"type": "string", "enum": ["nearest", "most_hotspots"],
                     "description": "most_hotspots for 'best' or 'richest' rings (overlaps first)."}},
            "required": ["commodity"]},
    },
    {
        "name": "get_mining_session",
        "description": "This mining session so far: tons refined by type, tons per hour, asteroids prospected, "
                       "cores found and cracked, limpets used and left, hold space, and the last prospector result.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "check_mining_fit",
        "description": "Whether their current ship is fitted for laser, core and sub-surface mining, what's missing, "
                       "and how many limpets they carry.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_ship_details",
        "description": "Jump range, cargo space, fuel, value and every module (with engineering) of any of their ships, "
                       "including ones they aren't flying, from the last time they flew each. With no ship: a list of all "
                       "their ships with range and cargo. Use for 'what's the Cutter's range', 'compare my ships'.",
        "input_schema": {"type": "object", "properties": {
            "ship": {"type": "string", "description": "Name or type, e.g. 'Cutter', 'Her Majesty's Wrath'."}}},
    },
    {
        "name": "get_stored_ships",
        "description": "The commander's stored ships (at this station and elsewhere) with location and transfer cost/time, from "
                       "the last time they opened a shipyard.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_stored_modules",
        "description": "The commander's stored modules (parts in storage) with class/rating, engineering, location and transfer "
                       "cost, from the last time they opened outfitting. Filter with search, e.g. 'shield', 'multicannon'.",
        "input_schema": {"type": "object", "properties": {"search": {"type": "string"}}},
    },
    {
        "name": "recent_conversation",
        "description": "Your recent conversation with them from the saved transcript, including before a restart. Use "
                       "when they refer to something said earlier that you don't have ('what did you say?', 'where "
                       "were we?').",
        "input_schema": {"type": "object", "properties": {
            "minutes": {"type": "integer", "description": "How far back. Default 60."}}},
    },
    {
        "name": "search_conversations",
        "description": "Search all saved transcripts (past days too) for a topic, e.g. 'Selenium', 'Type-9', "
                       "'black market'. Returns matching lines with dates.",
        "input_schema": {"type": "object", "properties": {
            "query": {"type": "string"}, "days": {"type": "integer", "description": "Default 30."}},
            "required": ["query"]},
    },
    {
        "name": "copy_to_clipboard",
        "description": "Copy exact text to their Windows clipboard so they can paste it into the galaxy map search. "
                       "Whenever you name a system they might fly to, copy that exact system name (not the station) "
                       "and say \"copied\". If you name several, copy the one you recommend.",
        "input_schema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
    },
    {
        "name": "set_my_name",
        "description": "Save the name they call you (or that you chose when they asked you to pick one). Lasts forever.",
        "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
    },
    {
        "name": "add_preference",
        "description": "Save a standing instruction about how they want you to behave (\"stop reminding me about X\", "
                       "\"keep answers shorter\"). Lasts across sessions; follow it from then on.",
        "input_schema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
    },
    {
        "name": "remove_preference",
        "description": "Drop a standing instruction containing this word.",
        "input_schema": {"type": "object", "properties": {"search": {"type": "string"}}, "required": ["search"]},
    },
    {
        "name": "forget_note",
        "description": "Delete a saved note or reminder by its number, or every note containing a word.",
        "input_schema": {"type": "object", "properties": {
            "note_id": {"type": "integer"}, "search": {"type": "string"},
        }},
    },
    {
        "name": "get_status",
        "description": "Where the commander is and what they're flying: system, station/body, docked or in supercruise, "
                       "ship with fuel/cargo/jump range, credits, legal state, status flags, cargo and remaining route.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_missions",
        "description": 'Active missions with target, destination, reward and a status of "ready to turn in" '
                       '(objective done; these never fail at expiry), "expired" or "active, Xh Ym left".',
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_recent_events",
        "description": "Short descriptions of what happened in the last N minutes of game time: jumps, docking, "
                       "interdictions, attacks, hull damage, deaths, scans, missions, purchases.",
        "input_schema": {"type": "object", "properties": {
            "minutes": {"type": "integer", "minimum": 1, "maximum": 720, "description": "How far back. Default 15."},
            "include_noisy": {"type": "boolean", "description": "Include music, chatter and module storage. Default false."},
        }},
    },
    {
        "name": "get_threats",
        "description": "Hostile pilots seen recently (pirate and hunter hails, interdictions) with ship, rank and bounty "
                       "when scanned, the currently locked target's details, and who has killed them lately.",
        "input_schema": {"type": "object", "properties": {
            "hours": {"type": "number", "description": "How far back to look. Default 24."}}},
    },
    {
        "name": "get_session_summary",
        "description": "Stats since the game loaded: jumps, distance, scans, discoveries, missions, deaths, earnings.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_crimes",
        "description": "Crimes, bounties and fines from the last N days, an unpaid estimate per faction, payments and deaths.",
        "input_schema": {"type": "object", "properties": {
            "days": {"type": "integer", "minimum": 1, "maximum": 60, "description": "Default 7."},
        }},
    },
    {
        "name": "find_commodity",
        "description": "Nearest stations selling a commodity with enough stock (Spansh). Defaults to the current system "
                       "and to a large pad if the ship needs one. Check market_updated for staleness.",
        "input_schema": {"type": "object", "properties": {
            "commodity": {"type": "string", "description": 'As named in game, e.g. "Evacuation Shelter".'},
            "min_stock": {"type": "integer", "minimum": 1},
            "near_system": {"type": "string"},
            "large_pad": {"type": "boolean"},
        }, "required": ["commodity"]},
    },
    {
        "name": "find_service",
        "description": "Nearest stations offering a service such as Interstellar Factors, Material Trader, Technology "
                       "Broker, Shipyard or Universal Cartographics (Spansh). Results are verified against each "
                       "station's own service list, so trust them. Material traders come with trader_type (raw, "
                       "manufactured or encoded, from the station economy); pass trader_kind to want one kind. "
                       "Material traders are rare: if none have a large pad, try again with large_pad false.",
        "input_schema": {"type": "object", "properties": {
            "service": {"type": "string"},
            "near_system": {"type": "string"},
            "large_pad": {"type": "boolean"},
            "trader_kind": {"type": "string", "enum": ["raw", "manufactured", "encoded"]},
        }, "required": ["service"]},
    },
    {
        "name": "find_home_base",
        "description": "Suggest home-base stations: big populated starports with a large pad, shipyard, outfitting and "
                       "Interstellar Factors, ranked by how close the nearest raw, manufactured and encoded material "
                       "traders are. Defaults to within 60 ly of Sol. Includes distance from their current system.",
        "input_schema": {"type": "object", "properties": {
            "max_from_sol_ly": {"type": "number", "description": "Default 60."},
            "min_population": {"type": "integer", "description": "Default 1,000,000."},
            "need_interstellar_factors": {"type": "boolean"},
            "near_system": {"type": "string", "description": "Centre of the search. Default Sol."},
        }},
    },
    {
        "name": "plot_route",
        "description": "Neutron-highway route to a destination system (Spansh), from the current system with the "
                       "ship's jump range unless given. Returns total jumps and the waypoints to enter in the galaxy map.",
        "input_schema": {"type": "object", "properties": {
            "destination": {"type": "string"},
            "source": {"type": "string"},
            "jump_range_ly": {"type": "number"},
        }, "required": ["destination"]},
    },
    {
        "name": "get_system_info",
        "description": "A system's allegiance, government, controlling faction and state, security, population, "
                       "primary star, factions by influence, and commander deaths/traffic recently (EDSM). "
                       "Defaults to the current system.",
        "input_schema": {"type": "object", "properties": {"system": {"type": "string"}}},
    },
    {
        "name": "get_market",
        "description": "A station's complete market: everything it sells (price, stock) and buys (price, demand), and "
                       "what your cargo is worth there. Default: the station you're docked at (live from your game). where='destination' "
                       "uses the targeted station or route end. Or name any station. commodity narrows to one item.",
        "input_schema": {"type": "object", "properties": {
            "station": {"type": "string"}, "system": {"type": "string"},
            "where": {"type": "string", "enum": ["here", "destination"]},
            "commodity": {"type": "string"},
            "category": {"type": "string", "description": "e.g. Metals, Minerals, Legal Drugs, Technology, Slavery."},
        }},
    },
    {
        "name": "trade_between",
        "description": "What to buy at one market and sell at another, ranked by trip profit (profit per ton times tons "
                       "the hold, stock and demand allow). Defaults: buy where you're docked, sell at your destination "
                       "(targeted station, or the markets in your route's end system). Use for 'what should I haul'.",
        "input_schema": {"type": "object", "properties": {
            "from_station": {"type": "string"}, "from_system": {"type": "string"},
            "to_station": {"type": "string"}, "to_system": {"type": "string"},
        }},
    },
    {
        "name": "best_trades",
        "description": "What to buy where they're docked and where to sell it, checking the best buyers for the most "
                       "promising goods. rank: 'hold' (profit per full trip, default), 'hour' (profit per estimated "
                       "hour; far-from-star stations rank lower), 'round_trip' (adds the best cargo for the way back), "
                       "'home' (trades to or from their home base). Accounts for free cargo space and credits.",
        "input_schema": {"type": "object", "properties": {
            "rank": {"type": "string", "enum": ["hold", "hour", "round_trip", "home"]},
            "max_distance_ly": {"type": "number", "description": "Default 40."},
            "max_age_days": {"type": "integer", "description": "Skip sell markets older than this. Default 30."},
            "from_station": {"type": "string"}, "from_system": {"type": "string"},
        }},
    },
    {
        "name": "set_home_base",
        "description": "Save their home base station (used by best_trades rank='home').",
        "input_schema": {"type": "object", "properties": {"station": {"type": "string"}, "system": {"type": "string"}},
                         "required": ["station", "system"]},
    },
    {
        "name": "find_best_price",
        "description": "Engineering materials (e.g. Selenium) aren't commodities: this tool says so. Best place within a radius to SELL a commodity (highest price with enough demand) or BUY it "
                       "(lowest price with enough stock), from Spansh. Use for trade decisions.",
        "input_schema": {"type": "object", "properties": {
            "commodity": {"type": "string"},
            "selling": {"type": "boolean", "description": "true = where to sell (default), false = where to buy."},
            "max_distance_ly": {"type": "number", "description": "Default 50."},
            "quantity": {"type": "integer", "description": "Tons to sell or buy; stations need at least this demand/stock."},
            "near_system": {"type": "string"}, "large_pad": {"type": "boolean"},
            "max_age_days": {"type": "integer", "description": "Skip markets not updated in this many days. Default 30."},
        }, "required": ["commodity"]},
    },
    {
        "name": "get_station_info",
        "description": "A station's type, pads, distance from star, faction, economy, services, and ships sold (Spansh).",
        "input_schema": {"type": "object", "properties": {"station": {"type": "string"}, "system": {"type": "string"}},
                         "required": ["station"]},
    },
    {
        "name": "get_shipyard_here",
        "description": "The ships really for sale for credits at the station they're docked at, from the game's own "
                       "shipyard list (after they open the shipyard). Use before telling them they can buy a ship here.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "find_ship",
        "description": "Nearest shipyards selling a ship, with price (Spansh, player-uploaded). Each result "
                       "says when_you_looked if they have opened that shipyard themselves: trust that over Spansh.",
        "input_schema": {"type": "object", "properties": {"ship": {"type": "string"}, "near_system": {"type": "string"}},
                         "required": ["ship"]},
    },
    {
        "name": "find_module",
        "description": 'Nearest outfitting selling a module, e.g. "Fuel Scoop" class 6 rating "A" (Spansh).',
        "input_schema": {"type": "object", "properties": {
            "module": {"type": "string"}, "size_class": {"type": "integer"},
            "rating": {"type": "string", "enum": ["A", "B", "C", "D", "E"]}, "near_system": {"type": "string"},
        }, "required": ["module"]},
    },
    {
        "name": "get_system_bodies",
        "description": "Stars and planets in a system: types, distances, landable, terraformable, rings, who discovered "
                       "them, and estimated exploration value (EDSM), plus your own scans. Default: current system.",
        "input_schema": {"type": "object", "properties": {"system": {"type": "string"}}},
    },
    {
        "name": "get_system_stations",
        "description": "All stations and settlements in a system with distance from the star and services (EDSM).",
        "input_schema": {"type": "object", "properties": {"system": {"type": "string"}}},
    },
    {
        "name": "get_galnet",
        "description": "Latest GalNet news headlines and summaries from Frontier.",
        "input_schema": {"type": "object", "properties": {"count": {"type": "integer", "minimum": 1, "maximum": 10}}},
    },
    {
        "name": "get_loadout",
        "description": "Current ship's modules with class/rating, engineering, damaged modules, jump range, rebuy and value, plus recent_refits: exactly what changed in recent outfitting.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_materials",
        "description": "Engineering materials on hand (raw, manufactured, encoded), optionally filtered by name or category.",
        "input_schema": {"type": "object", "properties": {"search": {"type": "string"}, "category": {"type": "string"}}},
    },
    {
        "name": "get_commander_profile",
        "description": "Pilot ranks and progress, superpower reputation, engineer unlock status, and career stats.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_route_details",
        "description": "The plotted route's remaining jumps with each star's class and whether it's fuel-scoopable.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_exploration",
        "description": "Bodies you've scanned in a system this session: type, terraformable, landable, rings, bio/geo "
                       "signals, first-discovery status. Default: current system.",
        "input_schema": {"type": "object", "properties": {"system": {"type": "string"}}},
    },
]


# ---- the registry: every tool, for both the voice copilot and the MCP server ----

# MCP kinds: "local" reads this PC's game files only; "web" also asks Spansh, EDSM, Ardent or GalNet;
# "write" and "delete" change Vesper's notes file; None keeps a tool voice-only (it's about the copilot
# itself, or acts on this PC's clipboard).
ANNOTATIONS = ("local", "web", "write", "delete")

TOOLS: dict[str, tuple[str, str | None]] = {
    # TOOL_SPECS name: (GameTools method, MCP kind)
    "remember": ("remember", "write"),
    "recall_notes": ("recall_notes", "local"),
    "forget_note": ("forget_note", "delete"),
    "copy_to_clipboard": ("copy_to_clipboard", None),
    "set_my_name": ("set_my_name", None),
    "add_preference": ("add_preference", None),
    "remove_preference": ("remove_preference", None),
    "recent_conversation": ("recent_conversation", "local"),
    "search_conversations": ("search_conversations", "local"),
    # where you are and what's going on
    "get_status": ("status", "local"),
    "get_missions": ("missions", "local"),
    "get_recent_events": ("recent_events", "local"),
    "get_session_summary": ("session_summary", "local"),
    "get_threats": ("threats", "local"),
    "get_crimes": ("crimes", "local"),
    "get_commander_profile": ("commander_profile", "local"),
    "get_route_details": ("route_details", "local"),
    "get_exploration": ("exploration", "local"),
    "find_binding": ("find_binding", "local"),
    # your ship and stuff
    "get_loadout": ("loadout", "local"),
    "get_ship_details": ("ship_details", "local"),
    "get_stored_ships": ("stored_ships", "local"),
    "get_stored_modules": ("stored_modules", "local"),
    "get_ship_locker": ("ship_locker", "local"),
    "get_materials": ("materials", "local"),
    "get_outfitting_here": ("outfitting_here", "local"),
    "get_shipyard_here": ("shipyard_here", "local"),
    # engineering
    "find_engineer": ("find_engineer", "local"),
    "check_engineering_materials": ("engineering_materials", "local"),
    "shopping_list": ("shopping_list", "write"),
    # mining
    "find_mining_hotspots": ("mining_hotspots", "web"),
    "plan_mining_run": ("plan_mining_run", "web"),
    "get_mining_session": ("mining_session", "local"),
    "check_mining_fit": ("mining_fit", "local"),
    # trade
    "get_market": ("market", "web"),
    "find_best_price": ("best_price", "web"),
    "best_trades": ("best_trades", "web"),
    "trade_between": ("trade_between", "web"),
    "trades_along_missions": ("trades_along_missions", "web"),
    "get_hold_value": ("hold_value", "web"),
    "plan_sale": ("plan_sale", "web"),
    "set_home_base": ("set_home_base", "write"),
    # the galaxy
    "find_commodity": ("find_commodity", "web"),
    "find_service": ("find_service", "web"),
    "find_home_base": ("find_home_base", "web"),
    "plot_route": ("plot_route", "web"),
    "get_system_info": ("system_info", "web"),
    "get_system_bodies": ("system_bodies", "web"),
    "get_system_stations": ("system_stations", "web"),
    "get_station_info": ("station_info", "web"),
    "find_ship": ("find_ship", "web"),
    "find_module": ("find_module", "web"),
    "get_galnet": ("galnet", "web"),
}


def call_tool(tools: GameTools, name: str, args: dict) -> dict:
    """Run a tool by its TOOL_SPECS name. Unknown names and bad arguments raise GameDataError."""
    if name not in TOOLS:
        raise GameDataError(f"Unknown tool {name!r}.")
    fn = getattr(tools, TOOLS[name][0])
    try:
        inspect.signature(fn).bind(**args)  # check the arguments only, so a TypeError inside the tool isn't hidden
    except TypeError as e:
        raise GameDataError(f"Bad arguments for {name}: {e}") from e
    return fn(**args)
