"""Live game state built up from journal events and Status.json.

GameState.apply() takes one journal entry at a time. Unknown events are
ignored, so new game updates won't break anything; they just won't be
tracked until a handler is added here.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone

from .status import GUI_FOCUS, active_flags


@dataclass
class Ship:
    """The ship being flown, from Loadout and the ship-change events."""
    type: str | None = None
    internal: str | None = None  # the game's own name, e.g. "type7"
    name: str | None = None
    ident: str | None = None
    ship_id: int | None = None
    fuel_capacity: float | None = None
    fuel_reserve_capacity: float | None = None
    cargo_capacity: int | None = None
    max_jump_range: float | None = None
    hull_health: float | None = None  # 0.0 to 1.0
    rebuy: int | None = None


@dataclass
class Location:
    """Where the commander is: system, body, station, docked or not."""
    system: str | None = None
    system_address: int | None = None
    star_pos: list[float] | None = None
    body: str | None = None
    body_type: str | None = None
    station: str | None = None
    station_type: str | None = None
    market_id: int | None = None
    docked: bool = False
    landed: bool = False
    supercruise: bool = False
    on_foot: bool = False


@dataclass
class Session:
    """Stats since the last game load (LoadGame)."""

    started: str | None = None
    jumps: int = 0
    distance_ly: float = 0.0
    bodies_scanned: int = 0
    first_discoveries: int = 0
    bodies_mapped: int = 0
    missions_completed: int = 0
    deaths: int = 0
    earnings: dict[str, int] = field(default_factory=dict)

    def earn(self, kind: str, amount: int | float | None) -> None:
        """Add credits earned this session under one kind (bounties, trade, exploration...)."""
        if amount:
            self.earnings[kind] = self.earnings.get(kind, 0) + int(amount)


@dataclass
class GameState:
    """Everything known about the game, built up from journal events and Status.json.

    apply() routes each journal event to its `_on_<Event>` handler; apply_status() takes
    Status.json. The watcher calls both under its lock. snapshot() is the JSON view the tools use.
    """
    commander: str | None = None
    fid: str | None = None
    game_version: str | None = None
    odyssey: bool | None = None
    game_running: bool = False
    game_mode: str | None = None
    credits: int | None = None
    fuel_main: float | None = None
    fuel_reservoir: float | None = None
    legal_state: str | None = None
    ship: Ship = field(default_factory=Ship)
    location: Location = field(default_factory=Location)
    cargo: dict[str, int] = field(default_factory=dict)
    route: list[dict] = field(default_factory=list)
    missions: dict[int, dict] = field(default_factory=dict)
    session: Session = field(default_factory=Session)
    status: dict = field(default_factory=dict)
    last_event_time: str | None = None
    # Bulkier data kept out of snapshot(); the tools read these directly.
    records: dict[str, dict] = field(default_factory=dict)              # latest raw event of each RECORDED kind
    materials: dict[str, dict[str, list]] = field(default_factory=dict)  # category -> name -> [display, count]
    scans: dict[int, dict[str, dict]] = field(default_factory=dict)      # system address -> body name -> details
    mission_cargo: dict[int, dict] = field(default_factory=dict)         # mission id -> {commodity, count} in the hold
    contacts: dict[str, dict] = field(default_factory=dict)              # hostile pilots seen (hails, interdictions)
    target: dict | None = None                                           # the currently locked target
    deaths: list[dict] = field(default_factory=list)                     # recent deaths and who did it
    fits: dict[int, dict] = field(default_factory=dict)                  # ship id -> {slot: (item, engineering)}
    refits: list[dict] = field(default_factory=list)                     # recent loadout changes
    interdictions: list[dict] = field(default_factory=list)              # recent interdictions: time, by, faction
    ship_profiles: dict[int, dict] = field(default_factory=dict)        # ship id -> last loadout summary
    mining_target: str | None = None                                     # what they're hunting, from the last plan/search
    mining: dict = field(default_factory=dict)                           # current mining session (see _mining)
    shipyards: dict[str, dict] = field(default_factory=dict)
    outfitting: dict = field(default_factory=dict)                       # the last outfitting screen opened             # "station|system" -> ships for credits, as seen

    # ---- derived ----

    @property
    def flags(self) -> set[str]:
        """The Status.json flags that are on right now ("Docked", "Supercruise"...)."""
        return active_flags(self.status) if self.status else set()

    @property
    def route_remaining(self) -> list[dict]:
        """Route stops still ahead, not counting the system we're in."""
        here = self.location.system_address
        for i, stop in enumerate(self.route):
            if stop.get("SystemAddress") == here:
                return self.route[i + 1:]
        return list(self.route)

    def snapshot(self) -> dict:
        """The state as plain JSON-ready data, with each mission's status and its cargo in the hold."""
        data = asdict(self)
        data["missions"] = []
        for mid, m in self.missions.items():
            view = dict(m, status=mission_status(m))
            if mid in self.mission_cargo:
                view["mission_cargo_in_hold"] = self.mission_cargo[mid]
            if m.get("cargo_lost") and mid not in self.mission_cargo:
                view["status"] = ("MISSION CARGO LOST when you died. Market-bought goods don't count toward it, and the "
                                  "depot won't reissue it (tested 2026-10-25: 0 collectable), so it can't be completed. "
                                  + view["status"])
            data["missions"].append(view)
        data["owned_cargo"] = {k: v - sum(c["count"] for c in self.mission_cargo.values() if c["commodity"] == k)
                               for k, v in self.cargo.items()}
        data["mission_cargo"] = list(self.mission_cargo.values())
        data["flags"] = sorted(self.flags)
        data["route_remaining"] = [s.get("StarSystem") for s in self.route_remaining]
        data["route"] = [s.get("StarSystem") for s in self.route]
        data["gui_focus"] = GUI_FOCUS.get(self.status.get("GuiFocus", 0))
        data["pips"] = [p / 2 for p in self.status["Pips"]] if "Pips" in self.status else None
        data["destination"] = self.status.get("Destination")
        for bulky in ("status", "records", "materials", "scans", "contacts", "target", "deaths", "fits", "refits", "interdictions", "shipyards", "mining", "ship_profiles", "outfitting"):
            del data[bulky]
        return data

    # ---- updates ----

    def apply(self, entry: dict) -> None:
        """Apply one journal event: keep it if it's a RECORDED snapshot, then run its handler."""
        self.last_event_time = entry.get("timestamp", self.last_event_time)
        if entry["event"] in RECORDED:
            self.records[entry["event"]] = entry
        handler = getattr(self, "_on_" + entry["event"], None)
        if handler:
            handler(entry)

    def apply_status(self, status: dict) -> None:
        """Apply a Status.json read: flags, fuel, credits, position and so on."""
        self.status = status
        fuel = status.get("Fuel")
        if fuel:
            self.fuel_main = fuel.get("FuelMain", self.fuel_main)
            self.fuel_reservoir = fuel.get("FuelReservoir", self.fuel_reservoir)
        if "Balance" in status:
            self.credits = status["Balance"]
        if "LegalState" in status:
            self.legal_state = status["LegalState"]
        flags = self.flags
        if status.get("Flags") or status.get("Flags2"):
            self.location.docked = "Docked" in flags
            self.location.landed = "Landed" in flags
            self.location.supercruise = "Supercruise" in flags
            self.location.on_foot = "OnFoot" in flags

    def _set_system(self, e: dict) -> None:
        """Move to the system named in an event (forgetting the body when the system changes)."""
        loc = self.location
        if e.get("SystemAddress") != loc.system_address:
            loc.body = loc.body_type = None
        loc.system = e.get("StarSystem", loc.system)
        loc.system_address = e.get("SystemAddress", loc.system_address)
        loc.star_pos = e.get("StarPos", loc.star_pos)
        if "Body" in e:
            loc.body, loc.body_type = e.get("Body"), e.get("BodyType")

    def _set_station(self, e: dict) -> None:
        """Record the station named in an event."""
        loc = self.location
        loc.station = e.get("StationName")
        loc.station_type = e.get("StationType")
        loc.market_id = e.get("MarketID")

    def _clear_station(self) -> None:
        """No longer at a station."""
        loc = self.location
        loc.station = loc.station_type = loc.market_id = None
        loc.docked = False

    def _on_Fileheader(self, e: dict) -> None:
        self.game_version = e.get("gameversion")
        self.odyssey = e.get("Odyssey")
        self.game_running = True

    def _on_Commander(self, e: dict) -> None:
        self.commander = e.get("Name")
        self.fid = e.get("FID")

    def _on_LoadGame(self, e: dict) -> None:
        self.commander = e.get("Commander", self.commander)
        self.fid = e.get("FID", self.fid)
        self.game_mode = e.get("GameMode")
        self.credits = e.get("Credits")
        self.fuel_main = e.get("FuelLevel", self.fuel_main)
        ship = self.ship
        if e.get("ShipID") != ship.ship_id:
            self.ship = ship = Ship()
        ship.type = e.get("Ship_Localised") or e.get("Ship")
        ship.internal = (e.get("Ship") or "").lower() or ship.internal
        ship.ship_id = e.get("ShipID")
        ship.name = e.get("ShipName")
        ship.ident = e.get("ShipIdent")
        ship.fuel_capacity = e.get("FuelCapacity", ship.fuel_capacity)
        self.session = Session(started=e.get("timestamp"))

    def _on_Location(self, e: dict) -> None:
        self._set_system(e)
        if e.get("Docked"):
            self._set_station(e)
            self.location.docked = True
        else:
            self._clear_station()

    def _on_FSDJump(self, e: dict) -> None:
        self._set_system(e)
        self._clear_station()
        self.location.supercruise = True
        if "FuelLevel" in e:
            self.fuel_main = e["FuelLevel"]
        self.session.jumps += 1
        self.session.distance_ly += e.get("JumpDist", 0.0)

    def _on_CarrierJump(self, e: dict) -> None:
        self._on_Location(e)

    def _on_Docked(self, e: dict) -> None:
        self._set_station(e)
        self.location.docked = True

    def _on_Undocked(self, e: dict) -> None:
        self._clear_station()

    def _on_SupercruiseEntry(self, e: dict) -> None:
        self.location.supercruise = True
        self.location.body = self.location.body_type = None

    def _on_SupercruiseExit(self, e: dict) -> None:
        self.location.supercruise = False
        self._set_system(e)

    def _on_ApproachBody(self, e: dict) -> None:
        self.location.body, self.location.body_type = e.get("Body"), "Planet"

    def _on_LeaveBody(self, e: dict) -> None:
        self.location.body = self.location.body_type = None

    def _on_Touchdown(self, e: dict) -> None:
        self.location.landed = True

    def _on_Liftoff(self, e: dict) -> None:
        self.location.landed = False

    def _on_Disembark(self, e: dict) -> None:
        self.location.on_foot = True

    def _on_Embark(self, e: dict) -> None:
        self.location.on_foot = False

    def _on_Loadout(self, e: dict) -> None:
        self._note_refit(e)
        self._ship_profile(e)
        if e.get("ShipID") != self.ship.ship_id:
            self.ship = Ship()
        ship = self.ship
        # Loadout only has the internal name ("anaconda"); keep LoadGame's friendlier one.
        ship.type = e.get("Ship_Localised") or ship.type or e.get("Ship")
        ship.internal = (e.get("Ship") or "").lower() or ship.internal
        ship.ship_id = e.get("ShipID")
        ship.name = e.get("ShipName")
        ship.ident = e.get("ShipIdent")
        ship.hull_health = e.get("HullHealth")
        ship.cargo_capacity = e.get("CargoCapacity")
        ship.max_jump_range = e.get("MaxJumpRange")
        ship.rebuy = e.get("Rebuy")
        fuel = e.get("FuelCapacity") or {}
        ship.fuel_capacity = fuel.get("Main", ship.fuel_capacity)
        ship.fuel_reserve_capacity = fuel.get("Reserve", ship.fuel_reserve_capacity)

    def _ship_profile(self, e: dict) -> None:
        """Remember each ship's last loadout, so ships they aren't flying can be described too."""
        if e.get("ShipID") is None:
            return
        fuel = e.get("FuelCapacity") or {}
        self.ship_profiles[e["ShipID"]] = {
            "ship": e.get("Ship"), "name": e.get("ShipName"), "ident": e.get("ShipIdent"),
            "jump_range_ly": round(e.get("MaxJumpRange") or 0, 1) or None, "cargo_capacity": e.get("CargoCapacity"),
            "fuel_capacity": fuel.get("Main") if isinstance(fuel, dict) else None, "rebuy": e.get("Rebuy"),
            "hull_value": e.get("HullValue"), "modules_value": e.get("ModulesValue"),
            "modules": [{"slot": m.get("Slot"), "item": m.get("Item"),
                         "engineering": (f"{(m.get('Engineering') or {}).get('BlueprintName')} "
                                         f"G{(m.get('Engineering') or {}).get('Level')}") if m.get("Engineering") else None}
                        for m in e.get("Modules", [])],
            "as_of": e.get("timestamp"),
        }

    def _note_refit(self, e: dict) -> None:
        """Compare this ship's new fit with the last one seen and record what changed."""
        fit = {}
        for m in e.get("Modules", []):
            eng = m.get("Engineering") or {}
            label = (f"{eng.get('BlueprintName')} G{eng.get('Level')}"
                     + (f" + {eng.get('ExperimentalEffect_Localised')}" if eng.get("ExperimentalEffect_Localised") else "")) if eng else None
            fit[m.get("Slot")] = (m.get("Item", "").lower(), label)
        ship_id = e.get("ShipID")
        old = self.fits.get(ship_id)
        self.fits[ship_id] = fit
        if old is None or old == fit:
            return
        changes = []
        for slot in sorted(set(old) | set(fit)):
            before, after = old.get(slot), fit.get(slot)
            if before == after:
                continue
            if before is None:
                changes.append({"slot": slot, "added": after[0], "engineering": after[1]})
            elif after is None:
                changes.append({"slot": slot, "removed": before[0]})
            elif before[0] != after[0]:
                changes.append({"slot": slot, "from": before[0], "to": after[0], "engineering": after[1]})
            else:
                changes.append({"slot": slot, "module": after[0], "engineering_from": before[1], "engineering_to": after[1]})
        self.refits.append({"time": e.get("timestamp"), "ship_id": ship_id,
                            "ship": e.get("ShipName") or e.get("Ship"), "changes": changes})
        del self.refits[:-10]

    # ---- mining: one session per stretch of mining, reset after an hour without any ----

    def _mining(self, e: dict) -> dict:
        """The current mining session, starting a new one after an hour without mining."""
        ts = e.get("timestamp") or ""
        m = self.mining
        if not m or _seconds_between(m.get("last"), ts) > 3600:
            m = self.mining = {"start": ts, "last": ts, "refined": {}, "prospected": 0, "cores_found": 0,
                               "cracked": 0, "limpets": {}, "ring": self.location.body, "system": self.location.system}
        m["last"] = ts
        return m

    def _on_ProspectedAsteroid(self, e: dict) -> None:
        m = self._mining(e)
        m["prospected"] += 1
        if e.get("MotherlodeMaterial"):
            m["cores_found"] += 1
        m["last_prospect"] = {
            "materials": {_mineral(x.get("Name_Localised") or x.get("Name", "")): round(x.get("Proportion", 0), 1)
                          for x in e.get("Materials", [])},
            "content": (e.get("Content_Localised") or "").replace("Material Content: ", ""),
            "core": e.get("MotherlodeMaterial_Localised") or e.get("MotherlodeMaterial"),
            "remaining": e.get("Remaining"),
        }

    def _on_MiningRefined(self, e: dict) -> None:
        m = self._mining(e)
        name = _mineral(e.get("Type_Localised") or e.get("Type", "").replace("$", "").replace("_name;", ""))
        m["refined"][name] = m["refined"].get(name, 0) + 1

    def _on_AsteroidCracked(self, e: dict) -> None:
        self._mining(e)["cracked"] += 1

    def _on_LaunchDrone(self, e: dict) -> None:
        if not self.mining or _seconds_between(self.mining.get("last"), e.get("timestamp")) > 3600:
            if e.get("Type") != "Prospector":
                return  # collectors alone (salvage) don't start a mining session
        m = self._mining(e)
        kind = e.get("Type", "?")
        m["limpets"][kind] = m["limpets"].get(kind, 0) + 1

    def _on_Outfitting(self, e: dict) -> None:
        data = e.get("_file") or {}
        if data.get("Items") is None or data.get("timestamp") != e.get("timestamp"):
            return
        self.outfitting = {"station": data.get("StationName"), "system": data.get("StarSystem"),
                           "time": data.get("timestamp"),
                           "items": [(i.get("Name", ""), i.get("BuyPrice")) for i in data["Items"]]}

    def _on_Shipyard(self, e: dict) -> None:
        """The ships this shipyard really sells for credits, from the game's own Shipyard.json. Only trusted
        when the file belongs to this event (on catch-up, older events would see today's file)."""
        data = e.get("_file") or {}
        if not data.get("PriceList") and "PriceList" not in data:
            return
        if data.get("timestamp") != e.get("timestamp") or data.get("MarketID") != e.get("MarketID"):
            return
        self.shipyards[f"{data.get('StationName')}|{data.get('StarSystem')}"] = {
            "time": data.get("timestamp"),
            "ships": {(s.get("ShipType_Localised") or s.get("ShipType")): s.get("ShipPrice") for s in data["PriceList"]},
        }

    def _on_ShipyardSwap(self, e: dict) -> None:
        self.ship = Ship(type=e.get("ShipType_Localised") or e.get("ShipType"),
                         internal=(e.get("ShipType") or "").lower() or None, ship_id=e.get("ShipID"))
        self.cargo = {}

    def _on_HullDamage(self, e: dict) -> None:
        if e.get("PlayerPilot", True) and not e.get("Fighter"):
            self.ship.hull_health = e.get("Health", self.ship.hull_health)

    def _on_Repair(self, e: dict) -> None:
        items = e.get("Items") or [e.get("Item")]
        if any(i and i.lower() in ("all", "hull") for i in items):
            self.ship.hull_health = 1.0

    def _on_RepairAll(self, e: dict) -> None:
        self.ship.hull_health = 1.0

    def _on_FuelScoop(self, e: dict) -> None:
        self.fuel_main = e.get("Total", self.fuel_main)

    def _on_RefuelAll(self, e: dict) -> None:
        if self.ship.fuel_capacity:
            self.fuel_main = self.ship.fuel_capacity

    def _on_RefuelPartial(self, e: dict) -> None:
        if self.fuel_main is not None:
            self.fuel_main += e.get("Amount", 0)

    def _on_Cargo(self, e: dict) -> None:
        source = e if "Inventory" in e else e.get("_file") or {}
        if source.get("Vessel", e.get("Vessel")) != "Ship" or "Inventory" not in source:
            return
        self.cargo, self.mission_cargo = {}, {}
        for i in source["Inventory"]:
            name, count = i.get("Name_Localised") or i.get("Name"), i.get("Count", 0)
            self.cargo[name] = self.cargo.get(name, 0) + count
            if i.get("MissionID"):  # issued by a mission; only this exact cargo counts for it
                self.mission_cargo[i["MissionID"]] = {"commodity": name, "count": count}

    def _on_CargoDepot(self, e: dict) -> None:
        m = self.missions.get(e.get("MissionID"))
        if m is not None:
            m["depot"] = {"collected": e.get("ItemsCollected"), "delivered": e.get("ItemsDelivered"),
                          "total": e.get("TotalItemsToDeliver")}
            m.pop("cargo_lost", None) if e.get("UpdateType") == "Collect" else None

    def _on_Died(self, e: dict) -> None:
        self.session.deaths += 1
        self.deaths.append({"time": e.get("timestamp"), "killer": e.get("KillerName_Localised") or e.get("KillerName"),
                            "killer_ship": e.get("KillerShip"), "killer_rank": e.get("KillerRank")})
        for mid in self.mission_cargo:  # mission cargo goes down with the ship
            if mid in self.missions:
                self.missions[mid]["cargo_lost"] = e.get("timestamp")
        self.mission_cargo = {}
        self.target = None

    # ---- threats: hostile pilots, interdictions, the current target ----

    def _contact(self, name: str | None, ts: str | None, **info) -> None:
        """Remember a pilot seen or heard from, merging in whatever this event tells us."""
        if not name:
            return
        c = self.contacts.setdefault(name, {"name": name})
        c.update({k: v for k, v in info.items() if v is not None})
        c["last_seen"] = ts

    def _on_ReceiveText(self, e: dict) -> None:
        if e.get("Channel") == "npc" and str(e.get("Message", "")).startswith("$Pirate"):
            self._contact(e.get("From_Localised"), e.get("timestamp"), hostile=True,
                          last_message=e.get("Message_Localised"))

    def _on_Interdicted(self, e: dict) -> None:
        self.interdictions.append({"time": e.get("timestamp"), "by": e.get("Interdictor_Localised") or e.get("Interdictor"),
                                   "faction": e.get("Faction"), "player": e.get("IsPlayer")})
        del self.interdictions[:-50]
        self._contact(e.get("Interdictor_Localised") or e.get("Interdictor"), e.get("timestamp"), hostile=True,
                      interdicted_you=e.get("timestamp"), player=e.get("IsPlayer"), faction=e.get("Faction"))

    def _on_ShipTargeted(self, e: dict) -> None:
        if not e.get("TargetLocked"):
            self.target = None
            return
        t = {
            "ship": e.get("Ship_Localised") or e.get("Ship"), "pilot": e.get("PilotName_Localised"),
            "rank": e.get("PilotRank"), "shields": e.get("ShieldHealth"), "hull": e.get("HullHealth"),
            "legal": e.get("LegalStatus"), "bounty": e.get("Bounty"), "faction": e.get("Faction"),
            "power": e.get("Power"), "scan_stage": e.get("ScanStage"), "subsystem": e.get("Subsystem_Localised"),
            "time": e.get("timestamp"),
        }
        self.target = {k: (round(v) if isinstance(v, float) else v) for k, v in t.items() if v is not None}
        if t["pilot"] and t["pilot"] in self.contacts:  # a known hostile: remember what they fly
            self._contact(t["pilot"], e.get("timestamp"), ship=t["ship"], rank=t["rank"], bounty=t["bounty"])

    def _on_NavRoute(self, e: dict) -> None:
        route = e.get("Route") or (e.get("_file") or {}).get("Route")
        if route is not None:
            self.route = route

    def _on_NavRouteClear(self, e: dict) -> None:
        self.route = []

    def _on_Missions(self, e: dict) -> None:
        # Written at login with only IDs and seconds left; details come from
        # MissionAccepted, possibly in an older journal (see Watcher.catch_up).
        known = self.missions
        self.missions = {}
        for m in e.get("Active", []):
            mid = m.get("MissionID")
            self.missions[mid] = known.get(mid) or {
                "id": mid,
                "name": _mission_name(m.get("Name")),
                "expiry": _add_seconds(e.get("timestamp"), m.get("Expires")),
                "details_missing": True,
            }

    def missions_missing_details(self) -> set[int]:
        """Missions known only from the Missions list (accepted before the journals
        we read), whose details are still missing.
        """
        return {mid for mid, m in self.missions.items() if m.get("details_missing")}

    def _on_MissionAccepted(self, e: dict) -> None:
        m = mission_from_accepted(e)
        if self.location.docked and self.location.station:  # where it was accepted, i.e. the source station
            m["source_station"], m["source_system"] = self.location.station, self.location.system
        self.missions[e.get("MissionID")] = m

    def _on_MissionRedirected(self, e: dict) -> None:
        # Objective done; now it only needs turning in. These missions don't
        # fail when their expiry passes.
        m = self.missions.get(e.get("MissionID"))
        if m is not None:
            m["objective_complete"] = True
            m["turn_in_system"] = e.get("NewDestinationSystem")
            m["turn_in_station"] = e.get("NewDestinationStation")

    def _on_MissionCompleted(self, e: dict) -> None:
        self.missions.pop(e.get("MissionID"), None)
        self.session.missions_completed += 1
        self.session.earn("missions", e.get("Reward"))

    def _on_MissionFailed(self, e: dict) -> None:
        self.missions.pop(e.get("MissionID"), None)

    def _on_MissionAbandoned(self, e: dict) -> None:
        self.missions.pop(e.get("MissionID"), None)

    def _count_scan(self, e: dict) -> None:
        """Count a body scan (and a first discovery) for the session summary."""
        if e.get("ScanType") == "NavBeaconDetail" or not ("PlanetClass" in e or "StarType" in e):
            return
        self.session.bodies_scanned += 1
        if e.get("WasDiscovered") is False:
            self.session.first_discoveries += 1

    def _on_MarketSell(self, e: dict) -> None:
        self.session.earn("trade", e.get("TotalSale"))

    def _on_RedeemVoucher(self, e: dict) -> None:
        self.session.earn(e.get("Type", "vouchers"), e.get("Amount"))

    def _on_SellExplorationData(self, e: dict) -> None:
        self.session.earn("exploration", e.get("TotalEarnings") or (e.get("BaseValue", 0) + e.get("Bonus", 0)))

    def _on_MultiSellExplorationData(self, e: dict) -> None:
        self.session.earn("exploration", e.get("TotalEarnings"))

    def _on_SellOrganicData(self, e: dict) -> None:
        total = sum(b.get("Value", 0) + b.get("Bonus", 0) for b in e.get("BioData", []))
        self.session.earn("exobiology", total)

    def _on_Shutdown(self, e: dict) -> None:
        self.game_running = False
        self.location.supercruise = self.location.landed = False

    # ---- engineering materials (counts from the login snapshot, kept current) ----

    def _material(self, category: str | None, name: str, delta: int, display: str | None = None) -> None:
        """Change a material count by `delta`, finding its category if the event doesn't say."""
        name = (name or "").lower()
        cat = _material_category(category) or next((c for c, items in self.materials.items() if name in items), None)
        if not cat or not name:
            return
        item = self.materials.setdefault(cat, {}).setdefault(name, [display or name.title(), 0])
        if display:
            item[0] = display
        item[1] = max(0, item[1] + delta)

    def _on_Materials(self, e: dict) -> None:
        self.materials = {}
        for cat in ("Raw", "Manufactured", "Encoded"):
            for m in e.get(cat, []):
                self._material(cat, m.get("Name"), m.get("Count", 0), m.get("Name_Localised"))

    def _on_MaterialCollected(self, e: dict) -> None:
        self._material(e.get("Category"), e.get("Name"), e.get("Count", 1), e.get("Name_Localised"))

    def _on_MaterialDiscarded(self, e: dict) -> None:
        self._material(e.get("Category"), e.get("Name"), -e.get("Count", 1))

    def _on_MaterialTrade(self, e: dict) -> None:
        for side, sign in (("Paid", -1), ("Received", 1)):
            m = e.get(side) or {}
            self._material(m.get("Category"), m.get("Material"), sign * m.get("Quantity", 0), m.get("Material_Localised"))

    def _consume(self, items: list | None) -> None:
        """Take spent materials off the counts (engineering, synthesis, trading)."""
        for m in items or []:
            self._material(m.get("Category"), m.get("Name"), -m.get("Count", 0))

    def _on_EngineerCraft(self, e: dict) -> None:
        self._consume(e.get("Ingredients"))

    def _on_Synthesis(self, e: dict) -> None:
        self._consume(e.get("Materials"))

    def _on_TechnologyBroker(self, e: dict) -> None:
        self._consume(e.get("Materials"))

    # ---- exploration scans ----

    def _body(self, e: dict) -> dict | None:
        """The scan record for the body an event names, created if new; None without a body."""
        system = e.get("SystemAddress") or self.location.system_address
        name = e.get("BodyName")
        if not system or not name:
            return None
        return self.scans.setdefault(system, {}).setdefault(name, {"name": name})

    def _on_Scan(self, e: dict) -> None:
        self._count_scan(e)
        body = self._body(e)
        if body is None:
            return
        body.update({k: v for k, v in {
            "type": e.get("PlanetClass") or (f"{e.get('StarType')} star" if e.get("StarType") else None),
            "distance_ls": round(e["DistanceFromArrivalLS"]) if "DistanceFromArrivalLS" in e else None,
            "terraformable": e.get("TerraformState") == "Terraformable" or None,
            "landable": e.get("Landable") or None,
            "atmosphere": e.get("Atmosphere") or None,
            "volcanism": e.get("Volcanism") or None,
            "rings": [r.get("RingClass", "").replace("eRingClass_", "") for r in e.get("Rings", [])] or None,
            "was_discovered": e.get("WasDiscovered"),
            "was_mapped": e.get("WasMapped"),
        }.items() if v is not None})

    def _on_SAAScanComplete(self, e: dict) -> None:
        self.session.bodies_mapped += 1
        if (body := self._body(e)) is not None:
            body["mapped_by_you"] = True

    def _signals(self, e: dict) -> None:
        """Store a body's signals (and biology genuses) from an FSS or SAA signals event."""
        if (body := self._body(e)) is not None:
            body["signals"] = {s.get("Type_Localised") or s.get("Type"): s.get("Count") for s in e.get("Signals", [])}
            if e.get("Genuses"):
                body["genuses"] = [g.get("Genus_Localised") or g.get("Genus") for g in e["Genuses"]]

    def _on_FSSBodySignals(self, e: dict) -> None:
        self._signals(e)

    def _on_SAASignalsFound(self, e: dict) -> None:
        self._signals(e)


RECORDED = {"Fileheader", "ShipLocker", "Backpack", "StoredShips", "StoredModules", "Loadout", "Rank", "Progress", "Reputation", "EngineerProgress", "Statistics", "Powerplay",
            "SquadronStartup", "Market", "FSSDiscoveryScan", "FSSAllBodiesFound", "NavRoute"}


def _material_category(raw: str | None) -> str | None:
    """"Raw", "Manufactured" or "Encoded" from a journal category string, or None."""
    raw = (raw or "").lower()
    for cat in ("Raw", "Manufactured", "Encoded"):
        if cat.lower() in raw:
            return cat
    return None


def mission_from_accepted(e: dict) -> dict:
    """A mission record from a MissionAccepted event."""
    return {
        "id": e.get("MissionID"),
        "name": e.get("LocalisedName") or _mission_name(e.get("Name")),
        "faction": e.get("Faction"),
        "destination_system": e.get("DestinationSystem"),
        "destination_station": e.get("DestinationStation"),
        "target": e.get("Target_Localised") or e.get("Target"),
        "kill_count": e.get("KillCount"),
        "commodity": e.get("Commodity_Localised"),
        "count": e.get("Count"),
        "expiry": e.get("Expiry"),
        "reward": e.get("Reward"),
        "wing": e.get("Wing"),
    }


def mission_status(m: dict, now: datetime | None = None) -> str:
    """"ready to turn in", "expired" or "active, 3h 20m left"."""
    if m.get("objective_complete"):
        return "ready to turn in"
    try:
        expiry = datetime.fromisoformat(m["expiry"].replace("Z", "+00:00"))
    except (KeyError, AttributeError, ValueError):
        return "active"
    left = (expiry - (now or datetime.now(timezone.utc))).total_seconds()
    if left <= 0:
        return "expired"
    hours, minutes = divmod(int(left // 60), 60)
    return f"active, {hours}h {minutes}m left" if hours else f"active, {minutes}m left"


def _mission_name(raw: str | None) -> str | None:
    """"Mission_Delivery_Agriculture_name" -> "Delivery Agriculture"."""
    if not raw:
        return raw
    name = raw.removeprefix("Mission_").removesuffix("_name")
    return name.replace("_", " ")


def _add_seconds(ts: str | None, seconds: int | None) -> str | None:
    """An ISO timestamp plus some seconds, or None if either is missing."""
    if not ts or seconds is None:
        return None
    try:
        start = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (start + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _seconds_between(a: str | None, b: str | None) -> float:
    """Seconds from journal timestamp a to b; huge if either is missing or unreadable."""
    try:
        ta = datetime.fromisoformat(a.replace("Z", "+00:00"))
        tb = datetime.fromisoformat(b.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return 1e12
    return (tb - ta).total_seconds()


MINERAL_NAMES = {"lowtempdiamonds": "Low Temperature Diamonds", "lowtemperaturediamond": "Low Temperature Diamonds",
                 "lowtemperaturediamonds": "Low Temperature Diamonds", "opal": "Void Opals", "voidopal": "Void Opals"}


def _mineral(name: str) -> str:
    """The game shortens some mined goods ("Low Temp. Diamonds", "opal"); use the full market name."""
    key = "".join(ch for ch in name.lower() if ch.isalnum())
    return MINERAL_NAMES.get(key) or name.title()
