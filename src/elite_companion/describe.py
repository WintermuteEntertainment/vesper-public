"""One-line, human-readable descriptions of watcher events (CLI and MCP)."""

from __future__ import annotations

from .watcher import Event

# Very frequent events that drown out everything else when printed.
NOISY = {
    "Music", "ReceiveText", "FSSSignalDiscovered", "Status", "NpcCrewPaidWage", "ReservoirReplenished",
    "StoredModules", "ModuleRetrieve", "ModuleStore", "Outfitting", "ShipLocker", "ModuleInfo", "StoredShips",
    "Shipyard", "Market", "FetchRemoteModule", "Backpack", "SuitLoadout", "Cargo", "ShipTargeted",
}


def is_noise(ev: Event) -> bool:
    """Chatter to leave out of event lists. Hostile hails and scanned targets matter, though
    their event types are mostly noise."""
    if ev.name == "ReceiveText":
        return not str(ev.data.get("Message", "")).startswith("$Pirate")
    if ev.name == "ShipTargeted":
        return not (ev.data.get("TargetLocked") and ev.data.get("ScanStage", 0) >= 3)
    return ev.name in NOISY


def describe(ev: Event) -> str:
    """A one-line English description of an event (e.g. "Jumped to Sol (8.6 ly), fuel 30.2 t").
    Events without a special case come back as their bare event name.
    """
    d = ev.data
    n = ev.name
    if n == "StatusFlag":
        return f"{d['flag']} {'ON' if d['on'] else 'off'}"
    if n == "FSDJump":
        return f"Jumped to {d.get('StarSystem')} ({d.get('JumpDist', 0):.1f} ly), fuel {d.get('FuelLevel', 0):.1f} t"
    if n == "StartJump":
        return f"Jump to {d.get('StarSystem')} ({d.get('StarClass')})" if d.get("JumpType") == "Hyperspace" else "Entering supercruise"
    if n == "Docked":
        return f"Docked at {d.get('StationName')} ({d.get('StarSystem')})"
    if n == "Undocked":
        return f"Undocked from {d.get('StationName')}"
    if n == "Location":
        where = d.get("StationName") if d.get("Docked") else d.get("Body")
        return f"At {d.get('StarSystem')}" + (f" / {where}" if where else "")
    if n == "SupercruiseExit":
        return f"Dropped out near {d.get('Body')}"
    if n == "FSDTarget":
        return f"Next: {d.get('Name')} ({d.get('RemainingJumpsInRoute', '?')} jumps left)"
    if n == "Scan":
        what = d.get("PlanetClass") or d.get("StarType")
        first = " FIRST DISCOVERY" if d.get("WasDiscovered") is False else ""
        return f"Scanned {d.get('BodyName')} ({what}){first}"
    if n == "MissionAccepted":
        return f"Mission: {d.get('LocalisedName') or d.get('Name')}"
    if n == "MissionCompleted":
        return f"Mission complete: {d.get('LocalisedName') or d.get('Name')} +{d.get('Reward', 0):,} cr"
    if n == "MarketSell":
        return f"Sold {d.get('Count')} {d.get('Type_Localised') or d.get('Type')} for {d.get('TotalSale', 0):,} cr"
    if n == "UnderAttack":
        return f"Under attack ({d.get('Target', '?')})"
    if n == "HullDamage":
        return f"Hull at {d.get('Health', 0) * 100:.0f}%"
    if n == "FuelScoop":
        return f"Scooped {d.get('Scooped', 0):.1f} t, tank {d.get('Total', 0):.1f} t"
    if n == "ReceiveText":
        return f"Hostile hail from {d.get('From_Localised')}: \"{d.get('Message_Localised')}\""
    if n == "ShipTargeted":
        bits = [d.get("Ship_Localised") or d.get("Ship"), d.get("PilotName_Localised"), d.get("PilotRank"),
                f"shields {d.get('ShieldHealth', 0):.0f}%", f"hull {d.get('HullHealth', 0):.0f}%", d.get("LegalStatus"),
                f"bounty {d['Bounty']:,} cr" if d.get("Bounty") else None]
        return "Target: " + ", ".join(b for b in bits if b)
    if n == "Interdicted":
        return (f"Interdicted by {d.get('Interdictor_Localised') or d.get('Interdictor', 'someone')}"
                f" ({'submitted' if d.get('Submitted') else 'fought it'})")
    if n == "MissionRedirected":
        return f"Mission objective done, turn in at {d.get('NewDestinationStation')} ({d.get('NewDestinationSystem')})"
    if n in ("MissionFailed", "MissionAbandoned"):
        return f"Mission {'failed' if n == 'MissionFailed' else 'abandoned'}: {d.get('LocalisedName') or d.get('Name')}"
    if n == "MarketBuy":
        return f"Bought {d.get('Count')} {d.get('Type_Localised') or d.get('Type')} for {d.get('TotalCost', 0):,} cr"
    if n == "CommitCrime":
        return f"Crime: {d.get('CrimeType')} against {d.get('Victim_Localised') or d.get('Victim')} ({d.get('Bounty') or d.get('Fine') or 0:,} cr, {d.get('Faction')})"
    if n == "Died":
        return f"Died (killed by {d.get('KillerName_Localised') or d.get('KillerName') or 'unknown'} in a {d.get('KillerShip', '?')})"
    if n == "Resurrect":
        return f"Respawned ({d.get('Option')}, cost {d.get('Cost', 0):,} cr)"
    if n == "ModuleBuy":
        return f"Bought module {d.get('BuyItem_Localised') or d.get('BuyItem')} for {d.get('BuyPrice', 0):,} cr"
    if n == "LoadGame":
        return f"CMDR {d.get('Commander')} in {d.get('Ship_Localised') or d.get('Ship')} ({d.get('GameMode')})"
    return n
