"""Ardent Insight (https://ardent-insight.com): commodity prices from the EDDN feed.

Used when Spansh's market search fails (on 2026-10-25 every Spansh commodity query returned
"Could not perform search" while its station search still worked). Ardent answers "who sells /
buys X near system Y" directly. It knows commodities by their game symbol ("silver",
"evacuationshelter"); engineering materials (Selenium) simply return nothing.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

BASE = "https://api.ardent-insight.com/v2"
PAD = {1: "small", 2: "medium", 3: "large"}


class ArdentError(RuntimeError):
    """Ardent couldn't be reached or answered with something unreadable."""
    pass


def symbol(commodity: str) -> str:
    """"Evacuation Shelter" -> "evacuationshelter"."""
    return "".join(ch for ch in commodity.lower() if ch.isalnum())


def _get(path: str) -> list:
    """GET an Ardent API path and return its JSON list ([] if it answered with anything else)."""
    req = urllib.request.Request(f"{BASE}/{path}", headers={"User-Agent": "vesper/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            data = json.load(resp)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        raise ArdentError(f"Couldn't reach Ardent Insight ({e}).") from e
    return data if isinstance(data, list) else []


def nearby(commodity: str, system: str, selling: bool, max_distance_ly: float = 50, min_quantity: int = 1,
           large_pad: bool = False, max_age_days: int | None = 30, max_results: int = 5) -> list[dict]:
    """Stations near `system` that buy from you (selling=True: imports) or sell to you (exports),
    best price first, in the same row shape as the Spansh client."""
    kind = "imports" if selling else "exports"
    path = (f"system/name/{urllib.parse.quote(system)}/commodity/name/{symbol(commodity)}/nearby/{kind}"
            f"?maxDistance={int(max_distance_ly)}")
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()[:19] if max_age_days else ""
    rows = []
    for r in _get(path):
        price, qty = (r.get("sellPrice"), r.get("demand")) if selling else (r.get("buyPrice"), r.get("stock"))
        if not price or (qty or 0) < min_quantity:
            continue
        if large_pad and r.get("maxLandingPadSize") != 3:
            continue
        if cutoff and (r.get("updatedAt") or "")[:19] < cutoff:
            continue
        row = {"station": r.get("stationName"), "system": r.get("systemName"), "distance_ly": r.get("distance"),
               "distance_to_arrival_ls": round(r.get("distanceToArrival") or 0), "type": r.get("stationType"),
               "large_pad": r.get("maxLandingPadSize") == 3, "pad": PAD.get(r.get("maxLandingPadSize")),
               "market_updated": (r.get("updatedAt") or "")[:10] or None, "source": "Ardent Insight"}
        row.update({"sell_price": price, "demand": qty} if selling else {"buy_price": price, "stock": qty})
        rows.append(row)
    rows.sort(key=lambda r: -r["sell_price"] if selling else r["buy_price"])
    return rows[:max_results]
