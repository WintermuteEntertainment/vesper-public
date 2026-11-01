"""EDSM (https://www.edsm.net) system lookups: who controls a system, its
security, and how many commanders died there recently."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://www.edsm.net"
USER_AGENT = "vesper/0.1"


class EdsmError(RuntimeError):
    """EDSM couldn't be reached, or doesn't know the system asked about."""
    pass


def _get(path: str, **params) -> dict | list:
    """GET an EDSM API path with query parameters and return the decoded JSON."""
    url = f"{BASE}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.load(resp)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        raise EdsmError(f"Couldn't reach EDSM ({e}). Try again shortly.") from e


def system_info(name: str) -> dict:
    """Allegiance, government, controlling faction, security, population, primary star, factions by
    influence (top 8), and recent commander deaths and traffic, for one system.
    """
    info = _get("/api-v1/system", systemName=name, showInformation=1, showPrimaryStar=1)
    if not info:  # EDSM answers [] for unknown systems
        raise EdsmError(f"EDSM doesn't know a system called {name!r}. Check the spelling.")
    deaths = _get("/api-system-v1/deaths", systemName=name) or {}
    traffic = _get("/api-system-v1/traffic", systemName=name) or {}
    factions = _get("/api-system-v1/factions", systemName=name) or {}
    return {
        "system": info.get("name"),
        "information": info.get("information") or "unpopulated or no data",
        "primary_star": info.get("primaryStar"),
        "commander_deaths": deaths.get("deaths"),
        "traffic": traffic.get("traffic"),
        "factions": [
            {"name": f.get("name"), "influence": round((f.get("influence") or 0) * 100, 1), "state": f.get("state"),
             "allegiance": f.get("allegiance"), "government": f.get("government")}
            for f in sorted(factions.get("factions") or [], key=lambda f: -(f.get("influence") or 0))
        ][:8],
        "edsm_url": info.get("url") or deaths.get("url"),
    }


def system_bodies(name: str) -> dict:
    """Stars and planets EDSM knows in a system (up to 40), with landable, terraformable, rings and
    first discoverer, plus EDSM's estimate of the system's exploration value.
    """
    data = _get("/api-system-v1/bodies", systemName=name) or {}
    if not data.get("bodies"):
        raise EdsmError(f"EDSM has no body data for {name!r}.")
    value = _get("/api-system-v1/estimated-value", systemName=name) or {}
    bodies = []
    for b in data["bodies"]:
        bodies.append({k: v for k, v in {
            "name": b.get("name"),
            "type": b.get("subType") or b.get("type"),
            "distance_ls": round(b.get("distanceToArrival") or 0),
            "scoopable": b.get("isScoopable"),
            "landable": b.get("isLandable"),
            "terraforming": b.get("terraformingState") if b.get("terraformingState") not in (None, "Not terraformable") else None,
            "atmosphere": b.get("atmosphereType") if b.get("atmosphereType") not in (None, "No atmosphere") else None,
            "gravity_g": round(b["gravity"], 2) if b.get("gravity") else None,
            "rings": [r.get("type") for r in b.get("rings", [])] or None,
            "first_discovered_by": (b.get("discovery") or {}).get("commander"),
        }.items() if v is not None})
    return {
        "system": data.get("name"),
        "body_count": data.get("bodyCount"),
        "bodies_known_to_edsm": len(bodies),
        "estimated_value_cr": value.get("estimatedValue"),
        "estimated_value_mapped_cr": value.get("estimatedValueMapped"),
        "valuable_bodies": [{"name": v.get("bodyName"), "value_mapped": v.get("valueMax")}
                            for v in value.get("valuableBodies", [])][:8],
        "bodies": bodies[:40],
    }


def system_stations(name: str, include_carriers: bool = False) -> dict:
    """Stations in a system (fleet carriers left out unless asked), nearest the star first, with their
    services. Up to 30.
    """
    data = _get("/api-system-v1/stations", systemName=name) or {}
    stations = []
    for s in data.get("stations", []):
        if not include_carriers and s.get("type") == "Fleet Carrier":
            continue
        services = [n for n, on in (("market", s.get("haveMarket")), ("outfitting", s.get("haveOutfitting")),
                                    ("shipyard", s.get("haveShipyard"))) if on] + (s.get("otherServices") or [])
        stations.append({"name": s.get("name"), "type": s.get("type"), "distance_ls": round(s.get("distanceToArrival") or 0),
                         "economy": s.get("economy"), "government": s.get("government"), "services": services})
    stations.sort(key=lambda s: s["distance_ls"])
    return {"system": data.get("name") or name, "count": len(stations), "stations": stations[:30]}
