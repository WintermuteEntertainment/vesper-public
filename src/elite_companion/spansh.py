"""Small client for Spansh's public station search (https://spansh.co.uk).

Spansh's own stock filter doesn't reliably apply, so commodity searches fetch
the nearest stations that list the commodity and filter by stock here.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
import urllib.error
import urllib.parse
import urllib.request

API = "https://spansh.co.uk/api/stations/search"
USER_AGENT = "vesper/0.1"
PAGE_SIZE = 100
CARRIER_TYPES = {"Drake-Class Carrier", "Fleet Carrier"}

# Friendly names -> Spansh service names.
SERVICE_ALIASES = {
    "interstellar factors": "Interstellar Factors Contact",
    "factors": "Interstellar Factors Contact",
    "material trader": "Material Trader",
    "material broker": "Material Trader",
    "raw material trader": "Material Trader",
    "manufactured material trader": "Material Trader",
    "encoded material trader": "Material Trader",
    "tech broker": "Technology Broker",
    "technology broker": "Technology Broker",
    "cartographics": "Universal Cartographics",
    "universal cartographics": "Universal Cartographics",
    "black market": "Black Market",
    "vista genomics": "Vista Genomics",
    "pioneer supplies": "Pioneer Supplies",
    "search and rescue": "Search and Rescue",
}

# Ships that need a large landing pad (the game's internal names).
LARGE_SHIPS = {
    "anaconda", "federation_corvette", "cutter", "type7", "type9", "type9_military",
    "belugaliner", "orca", "panthermkii",
}


class SpanshError(RuntimeError):
    """Spansh didn't answer or returned an error; the message is safe to speak."""
    pass


def service_name(name: str) -> str:
    """Spansh's name for a station service ("interstellar factors" -> its full name)."""
    return SERVICE_ALIASES.get(name.strip().lower(), name.strip())


def needs_large_pad(ship_internal: str | None) -> bool:
    """Whether this ship (journal internal name) only fits a large landing pad."""
    return (ship_internal or "").lower() in LARGE_SHIPS


def _post(body: dict | None, timeout: float = 20, attempts: int = 4, url: str = API) -> dict:
    """POST (or GET when body is None) to Spansh. It throws occasional 500s
    on requests that work a moment later, so server errors and timeouts are
    retried."""
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
    )
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            if e.code < 500 or attempt == attempts:
                raise SpanshError(
                    f"Spansh returned HTTP {e.code}. Check the system name is spelled exactly as in game, "
                    f"or try again shortly."
                ) from e
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt == attempts:
                raise SpanshError(f"Couldn't reach Spansh ({e}). It may be down; try again shortly.") from e
        time.sleep(2 ** (attempt - 1))  # 1, 2, 4 s


def _station_row(r: dict, commodity: str | None = None) -> dict:
    """One Spansh station result as a short row; with `commodity`, its price and stock there."""
    row = {
        "station": r.get("name"),
        "system": r.get("system_name"),
        "distance_ly": round(r.get("distance", 0), 1),
        "distance_to_arrival_ls": round(r.get("distance_to_arrival") or 0),
        "type": r.get("type"),
        "economy": r.get("primary_economy"),
        "large_pad": r.get("has_large_pad"),
        "controlling_faction": r.get("controlling_minor_faction"),
        "market_updated": str(r.get("market_updated_at") or "")[:10] or None,
    }
    if r.get("is_planetary") or r.get("type") in ("Settlement", "Planetary Outpost", "Planetary Port"):
        row["on_body"] = r.get("body_name")  # surface stations only show up near their planet or moon
    updated = str(r.get("updated_at") or r.get("market_updated_at") or "")[:10]
    if updated and updated < (datetime.now(timezone.utc) - timedelta(days=365)).strftime("%Y-%m-%d"):
        row["stale_data"] = f"last updated {updated}"
    if commodity:
        c = next((c for c in r.get("market", []) if (c.get("commodity") or "").lower() == commodity.lower()), {})
        row["stock"] = c.get("supply")
        row["buy_price"] = c.get("buy_price")
    return row


def _search(filters: dict, reference_system: str, keep, max_results: int, max_pages: int, commodity=None) -> list[dict]:
    """Page through a Spansh station search, nearest first, keeping rows `keep` accepts, until
    max_results rows or max_pages pages.
    """
    rows: list[dict] = []
    for page in range(max_pages):
        data = _post({
            "filters": filters,
            "sort": [{"distance": {"direction": "asc"}}, {"distance_to_arrival": {"direction": "asc"}}],
            "size": PAGE_SIZE,
            "page": page,
            "reference_system": reference_system,
        })
        if "error" in data:
            raise SpanshError(f"Spansh error: {data['error']}")
        results = data.get("results", [])
        for r in results:
            if keep(r):
                rows.append(_station_row(r, commodity))
                if len(rows) >= max_results:
                    return rows
        if len(results) < PAGE_SIZE:
            break
    return rows


def _base_filters(large_pad: bool) -> dict:
    """The filter every search starts from: large pad only if the ship needs one."""
    return {"has_large_pad": {"value": True}} if large_pad else {}


def find_commodity(commodity: str, reference_system: str, min_stock: int = 1, large_pad: bool = False,
                   include_carriers: bool = False, max_results: int = 5, max_pages: int = 3) -> list[dict]:
    """Nearest stations selling `commodity` with at least `min_stock` units."""
    filters = _base_filters(large_pad) | {"market": {"value": [{"name": commodity}]}}

    def keep(r: dict) -> bool:
        if not include_carriers and r.get("type") in CARRIER_TYPES:
            return False
        c = next((c for c in r.get("market", []) if (c.get("commodity") or "").lower() == commodity.lower()), {})
        return (c.get("supply") or 0) >= min_stock

    return _search(filters, reference_system, keep, max_results, max_pages, commodity)


# A material trader deals in the kind of material its station's economy suggests.
TRADER_TYPE = {"Extraction": "raw", "Refinery": "raw", "Industrial": "manufactured",
               "High Tech": "encoded", "Military": "encoded"}


def trader_type(economy: str | None) -> str | None:
    """The material trader kind (raw, manufactured, encoded) for a station economy, or None."""
    return TRADER_TYPE.get((economy or "").replace("$economy_", "").rstrip(";"))


def find_service(service: str, reference_system: str, large_pad: bool = False, include_carriers: bool = False,
                 max_results: int = 5, kind: str | None = None) -> list[dict]:
    """Nearest stations offering a service such as Interstellar Factors Contact or Material Trader.

    Spansh silently ignores a service filter it doesn't recognise and returns the nearest stations
    anyway, so every result is checked against its own service list here, scanning up to 500
    stations (material traders are rare: one in the 500 nearest Pegasi Sector CL-O b6-5)."""
    name = service_name(service)
    if kind:  # "raw material trader" etc.
        name = "Material Trader"
    filters = _base_filters(large_pad) | {"services": {"value": [name]}}
    max_pages = 5
    if name == "Material Trader":
        # Rare, and only at real stations. Spansh does honour type and economy filters, which skip the
        # thousands of settlements in between: raw traders near Pegasi Sector CL-O b6-5 are 58+ ly out.
        filters["type"] = {"value": PORT_TYPES}
        if kind:
            filters["primary_economy"] = {"value": [e for e, k in TRADER_TYPE.items() if k == kind]}
        max_pages = 20

    def keep(r: dict) -> bool:
        if not include_carriers and r.get("type") in CARRIER_TYPES:
            return False
        if not any((s.get("name") or "").lower() == name.lower() for s in r.get("services") or []):
            return False
        return not kind or trader_type(r.get("primary_economy")) == kind

    rows = _search_parallel(filters, reference_system, keep, max_results, max_pages)
    if name == "Material Trader":
        for row in rows:
            row["trader_type"] = trader_type(row.get("economy"))
    return rows


LARGE_PORT_TYPES = ["Coriolis Starport", "Orbis Starport", "Ocellus Starport", "Dodec Starport", "Asteroid base",
                    "Planetary Port"]


def _has(r: dict, service: str) -> bool:
    """Whether a Spansh station row lists this service."""
    return any((s.get("name") or "").lower() == service.lower() for s in r.get("services") or [])


def _xyz(r: dict) -> tuple[float, float, float]:
    """A station row's system coordinates."""
    return (r.get("system_x") or 0.0, r.get("system_y") or 0.0, r.get("system_z") or 0.0)


def _ly(a: tuple, b: tuple) -> float:
    """Distance in light years between two coordinates, to one decimal."""
    return round(sum((p - q) ** 2 for p, q in zip(a, b)) ** 0.5, 1)


def material_traders(center: str = "Sol", pages: int = 60, cache_days: float = 7) -> list[dict]:
    """Every material trader Spansh knows among the ~6,000 starports and outposts nearest `center`
    (about 64 ly around Sol), cached on disk for a week: the scan takes ~90 s."""
    import json as _json
    from concurrent.futures import ThreadPoolExecutor
    from .config import HOME
    cache = HOME / "cache" / f"material_traders_{_squash(center)}.json"
    try:
        data = _json.loads(cache.read_text(encoding="utf-8"))
        if time.time() - data["saved"] < cache_days * 86400:
            return data["traders"]
    except (OSError, ValueError, KeyError):
        pass
    filters = {"type": {"value": PORT_TYPES}, "primary_economy": {"value": list(TRADER_TYPE)}}

    def page(n: int) -> list[dict]:
        return _post({"filters": filters, "size": PAGE_SIZE, "page": n, "reference_system": center,
                      "sort": [{"distance": {"direction": "asc"}}]}).get("results", [])

    traders = []
    with ThreadPoolExecutor(6) as pool:
        for start in range(0, pages, 6):
            batch = list(pool.map(page, range(start, min(start + 6, pages))))
            for results in batch:
                traders += [{"system": r.get("system_name"), "station": r.get("name"),
                             "kind": trader_type(r.get("primary_economy")), "xyz": _xyz(r),
                             "large_pad": r.get("has_large_pad"), "distance_to_arrival_ls": round(r.get("distance_to_arrival") or 0)}
                            for r in results if _has(r, "Material Trader")]
            if any(len(b) < PAGE_SIZE for b in batch):
                break
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(_json.dumps({"saved": time.time(), "center": center, "traders": traders}), encoding="utf-8")
    return traders


def nearest_traders(xyz: tuple, traders: list[dict]) -> dict:
    """The closest material trader of each kind to `xyz`, from a list of traders."""
    out = {}
    for kind in ("raw", "manufactured", "encoded"):
        best = min((t for t in traders if t["kind"] == kind), key=lambda t: _ly(xyz, t["xyz"]), default=None)
        out[kind] = {"ly": _ly(xyz, best["xyz"]), "at": f"{best['station']}, {best['system']}",
                     "large_pad": best["large_pad"]} if best else None
    return out


def find_home_bases(center: str = "Sol", max_ly: float = 60, min_population: int = 1_000_000,
                    want_factors: bool = True, candidates: int = 5, from_xyz: tuple | None = None) -> list[dict]:
    """Home-base candidates near `center`: big populated starports with a large pad, shipyard,
    outfitting and (optionally) Interstellar Factors, ranked by how close the nearest raw,
    manufactured and encoded material traders are. Spansh honours the type/shipyard/pad/population/
    distance filters but not service filters, so services are checked on each result."""
    from concurrent.futures import ThreadPoolExecutor
    traders = material_traders(center)
    filters = {"type": {"value": LARGE_PORT_TYPES}, "has_shipyard": {"value": True}, "has_large_pad": {"value": True},
               "system_population": {"value": [str(min_population), "999999999999"], "comparison": "<=>"},
               "distance": {"min": "0", "max": str(max_ly)}}

    def page(n: int) -> list[dict]:
        return _post({"filters": filters, "size": PAGE_SIZE, "page": n, "reference_system": center,
                      "sort": [{"distance": {"direction": "asc"}}]}).get("results", [])

    pool_rows, seen = [], set()
    with ThreadPoolExecutor(4) as pool:
        for batch in pool.map(page, range(8)):
            for r in batch:
                if r.get("system_name") in seen or not _has(r, "Outfitting"):
                    continue
                if want_factors and not _has(r, "Interstellar Factors Contact"):
                    continue
                seen.add(r.get("system_name"))
                xyz = _xyz(r)
                near = nearest_traders(xyz, traders)
                dists = [n["ly"] for n in near.values() if n]
                row = _station_row(r) | {
                    "from_" + _squash(center) + "_ly": round(r.get("distance", 0), 1),
                    "population": r.get("system_population"), "government": r.get("government"),
                    "power": r.get("system_power"), "nearest_material_traders": near,
                    "farthest_trader_ly": max(dists) if len(dists) == 3 else None,
                }
                row.pop("distance_ly", None)
                if from_xyz:
                    row["from_you_ly"] = _ly(from_xyz, xyz)
                pool_rows.append(row)
    pool_rows.sort(key=lambda r: (r["farthest_trader_ly"] is None, r["farthest_trader_ly"] or 0,
                                  r["distance_to_arrival_ls"]))
    return pool_rows[:candidates]


PORT_TYPES = ["Coriolis Starport", "Orbis Starport", "Ocellus Starport", "Dodec Starport", "Asteroid base",
              "Outpost", "Planetary Outpost", "Planetary Port"]


def _search_parallel(filters: dict, reference_system: str, keep, max_results: int, max_pages: int,
                     batch: int = 4) -> list[dict]:
    """Like _search, but fetches pages a few at a time (a 20-page scan otherwise takes ~40 s)."""
    from concurrent.futures import ThreadPoolExecutor

    def page(n: int) -> list[dict]:
        data = _post({"filters": filters, "size": PAGE_SIZE, "page": n, "reference_system": reference_system,
                      "sort": [{"distance": {"direction": "asc"}}, {"distance_to_arrival": {"direction": "asc"}}]})
        if "error" in data:
            raise SpanshError(f"Spansh error: {data['error']}")
        return data.get("results", [])

    rows: list[dict] = []
    with ThreadPoolExecutor(batch) as pool:
        for start in range(0, max_pages, batch):
            pages = list(pool.map(page, range(start, min(start + batch, max_pages))))
            for results in pages:  # in distance order
                rows += [_station_row(r) for r in results if keep(r)]
            if len(rows) >= max_results or any(len(p) < PAGE_SIZE for p in pages):
                break
    return rows[:max_results]


ROUTE_API = "https://spansh.co.uk/api/route"
RESULTS_API = "https://spansh.co.uk/api/results/"


def plot_neutron_route(source: str, destination: str, range_ly: float, efficiency: int = 60,
                       max_wait_s: float = 45, max_waypoints: int = 40) -> dict:
    """Spansh's neutron plotter. Returns a summary plus waypoints (neutron
    stars and other stops to put in the galaxy map); `jumps` on each is the
    number of jumps to reach it from the previous waypoint."""
    query = urllib.parse.urlencode({"efficiency": efficiency, "range": round(range_ly, 2),
                                    "from": source, "to": destination})
    job = _post(None, url=f"{ROUTE_API}?{query}")
    if "error" in job:
        raise SpanshError(f"Spansh couldn't plot that: {job['error']}")
    deadline = time.monotonic() + max_wait_s
    while True:
        res = _post(None, url=RESULTS_API + job["job"])
        if res.get("status") == "ok" and res.get("result"):
            break
        if "error" in res:
            raise SpanshError(f"Spansh couldn't plot that: {res['error']}")
        if time.monotonic() > deadline:
            raise SpanshError("Spansh is still working on that route; ask again in a minute.")
        time.sleep(1.5)
    r = res["result"]
    stops = r.get("system_jumps") or []
    return {
        "from": r.get("source_system"),
        "to": r.get("destination_system"),
        "distance_ly": round(r.get("distance") or 0, 1),
        # Spansh's total_jumps counts waypoints; each waypoint's "jumps" is the real count to reach it.
        "total_jumps": sum(st.get("jumps") or 0 for st in stops),
        "range_ly": r.get("range"),
        "waypoint_count": max(len(stops) - 1, 0),
        "waypoints": [
            {"system": s.get("system"), "jumps": s.get("jumps"), "neutron": s.get("neutron_star"),
             "distance_left_ly": round(s.get("distance_left") or 0)}
            for s in stops[1:max_waypoints + 1]
        ],
        "truncated": len(stops) - 1 > max_waypoints,
        "spansh_url": f"https://spansh.co.uk/plotter/results/{job['job']}",
    }


def _search_raw(filters: dict, reference_system: str | None, sort: list, size: int = 20) -> list[dict]:
    """One page of a Spansh station search with the caller's own filters and sort."""
    body = {"filters": filters, "sort": sort, "size": size, "page": 0}
    if reference_system:
        body["reference_system"] = reference_system
    data = _post(body)
    if "error" in data:
        raise SpanshError(f"Spansh error: {data['error']}")
    return data.get("results", [])


def get_station(name: str, system: str | None = None) -> dict | None:
    """Everything Spansh knows about one station: market, services, ships, modules."""
    filters = {"name": {"value": name}}
    if system:
        filters["system_name"] = {"value": system}
    results = _search_raw(filters, None, [], size=5)
    exact = [r for r in results if (r.get("name") or "").lower() == name.lower()]
    return (exact or results or [None])[0]


def find_best_price(commodity: str, reference_system: str, selling: bool = True, max_distance_ly: float = 50,
                    min_quantity: int = 1, large_pad: bool = False, include_carriers: bool = False,
                    max_results: int = 5, max_age_days: int | None = 30) -> list[dict]:
    """Best place within range to sell (highest price, enough demand) or buy (lowest price, enough stock).
    Markets not updated within max_age_days are skipped: year-old prices aren't worth flying to."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat() if max_age_days else ""
    qty_field = "demand" if selling else "supply"
    filters = _base_filters(large_pad) | {
        "market": {"value": [{"name": commodity, qty_field: {"value": [str(min_quantity), "999999999"], "comparison": "<=>"}}]},
        "distance": {"min": "0", "max": str(max_distance_ly)},
    }
    # Selling: Spansh can sort by sell price. Buying: price 0 means "not sold here" and sorts
    # first, so take the nearest stations within range and rank them by price here.
    sort = ([{"market_sell_price": [{"name": commodity, "direction": "desc"}]}] if selling
            else [{"distance": {"direction": "asc"}}])
    # Spansh doesn't enforce the quantity filter, so check it here too.
    candidates = []
    for r in _search_raw(filters, reference_system, sort, size=100):
        if not include_carriers and r.get("type") in CARRIER_TYPES:
            continue
        c = next((c for c in r.get("market", []) if (c.get("commodity") or "").lower() == commodity.lower()), {})
        price, qty = (c.get("sell_price"), c.get("demand")) if selling else (c.get("buy_price"), c.get("supply"))
        if not price or (qty or 0) < min_quantity:
            continue
        if cutoff and str(r.get("market_updated_at") or "").replace(" ", "T") < cutoff[:19]:
            continue
        row = _station_row(r)
        row.update({"sell_price": price, "demand": qty} if selling else {"buy_price": price, "stock": qty})
        row["_xyz"] = _xyz(r)
        candidates.append((price, row))
    candidates.sort(key=lambda pr: -pr[0] if selling else pr[0])
    return [row for _, row in candidates[:max_results]]


SHIP_NAMES = [  # as Spansh spells them (inconsistently: "Krait MkII" but "Kestrel Mk II")
    "Adder", "Alliance Challenger", "Alliance Chieftain", "Alliance Crusader", "Anaconda", "Asp Explorer", "Asp Scout",
    "Beluga Liner", "Caspian Explorer", "Cobra MkIII", "Cobra MkIV", "Cobra MkV", "Corsair", "Diamondback Explorer",
    "Diamondback Scout", "Dolphin", "Eagle", "Federal Assault Ship", "Federal Corvette", "Federal Dropship",
    "Federal Gunship", "Fer-de-Lance", "Hauler", "Imperial Clipper", "Imperial Courier", "Imperial Cutter",
    "Imperial Eagle", "Keelback", "Kestrel Mk II", "Krait MkII", "Krait Phantom", "Lynx Highliner", "Mamba", "Mandalay",
    "Orca", "Panther Clipper MkII", "Python", "Python MkII", "Sidewinder", "Type-10 Defender", "Type-11 Prospector",
    "Type-6 Transporter", "Type-7 Transporter", "Type-8 Transporter", "Type-9 Heavy", "Viper MkIII", "Viper MkIV", "Vulture",
]


def _squash(s: str) -> str:
    """Lower-case letters and digits only, with Mk 2-5 spelled the way ship names spell them."""
    s = "".join(ch for ch in s.lower() if ch.isalnum())
    for digit, roman in (("mk5", "mkv"), ("mk4", "mkiv"), ("mk3", "mkiii"), ("mk2", "mkii")):
        s = s.replace(digit, roman)
    return s


def ship_name(query: str) -> str:
    """Spansh's name for a ship the commander said: exact, then the shortest partial match."""
    q = _squash(query)
    exact = [n for n in SHIP_NAMES if _squash(n) == q]
    partial = [n for n in SHIP_NAMES if q in _squash(n)]
    return (exact or sorted(partial, key=len) or [query])[0]


def find_ship(ship: str, reference_system: str, max_results: int = 5) -> list[dict]:
    """Nearest shipyards selling a ship (e.g. "Krait Mk 2", "python mk ii", "Type 8")."""
    ship = ship_name(ship)
    rows = []
    for r in _search_raw({"ships": {"value": [ship]}}, reference_system, [{"distance": {"direction": "asc"}}], size=20):
        hit = next((s for s in r.get("ships", []) if _squash(s.get("name") or "") == _squash(ship)), None)
        if hit and r.get("type") not in CARRIER_TYPES:
            rows.append(_station_row(r) | {"ship": hit.get("name"), "price": hit.get("price"),
                                            "shipyard_updated": str(r.get("shipyard_updated_at") or "")[:10] or None})
        if len(rows) >= max_results:
            break
    return rows


def find_module(module: str, reference_system: str, size_class: int | None = None, rating: str | None = None,
                max_results: int = 8, large_pad: bool = False) -> list[dict]:
    """Nearest outfitting selling a module (e.g. "Fuel Scoop", class 5, rating "A"), only stations their ship can
    dock at when it needs a large pad."""
    rows = []
    for r in _search_raw({**_base_filters(large_pad), "modules": {"value": [{"name": module}]}}, reference_system,
                         [{"distance": {"direction": "asc"}}, {"distance_to_arrival": {"direction": "asc"}}], size=60):
        if large_pad and not r.get("has_large_pad"):
            continue
        hits = [m for m in r.get("modules", []) if module.lower() in (m.get("name") or "").lower()
                and (size_class is None or m.get("class") == size_class)
                and (rating is None or (m.get("rating") or "").upper() == rating.upper())]
        if hits and r.get("type") not in CARRIER_TYPES:
            best = max(hits, key=lambda m: (m.get("class") or 0, "EDCBA".find((m.get("rating") or "E").upper())))
            rows.append(_station_row(r) | {"module": f"{best.get('class')}{best.get('rating')} {best.get('name')}",
                                            "price": best.get("price"),
                                            "outfitting_updated": str(r.get("outfitting_updated_at") or "")[:10] or None})
        if len(rows) >= max_results:
            break
    return rows


BODIES_API = "https://spansh.co.uk/api/bodies/search"


RING_SIGNALS = [  # Spansh's spelling of ring hotspot names ("Void Opal" is singular, the diamonds plural)
    "Alexandrite", "Bauxite", "Benitoite", "Bertrandite", "Bromellite", "Coltan", "Gallite", "Gold",
    "Grandidierite", "Hydrogen Peroxide", "Indite", "Lepidolite", "Liquid oxygen", "Lithium Hydroxide",
    "Low Temperature Diamonds", "Methane Clathrate", "Methanol Monohydrate Crystals", "Monazite", "Musgravite",
    "Osmium", "Painite", "Palladium", "Platinum", "Praseodymium", "Rhodplumsite", "Rutile", "Samarium",
    "Serendibite", "Silver", "Tritium", "Uraninite", "Void Opal", "Water",
]
RING_ALIASES = {"ltd": "Low Temperature Diamonds", "ltds": "Low Temperature Diamonds", "opal": "Void Opal",
                "opals": "Void Opal", "trit": "Tritium", "plat": "Platinum"}


def ring_signal_name(commodity: str) -> str:
    """The ring hotspot name for a commodity ("LTDs" -> "Low Temperature Diamonds")."""
    q = _squash(commodity)
    if q in RING_ALIASES:
        return RING_ALIASES[q]
    for n in RING_SIGNALS:
        if _squash(n) in (q, q.rstrip("s")) or _squash(n).rstrip("s") == q.rstrip("s"):
            return n
    return commodity.strip().title()


def find_ring_hotspots(commodity: str, reference_system: str, max_results: int = 5, pristine_only: bool = False,
                       pages: int = 2, max_distance_ly: float | None = None, rank: str = "nearest") -> list[dict]:
    """Planetary rings with hotspots (ring signals) for a mineable commodity, nearest first. Spansh honours
    the ring_signals filter (checked 2026-10-26), but each result is still checked."""
    commodity = ring_signal_name(commodity)
    want = commodity.lower()
    rows: list[dict] = []
    for page in range(pages):
        data = _post({"filters": {"ring_signals": [{"name": commodity, "comparison": "<=>",
                                                   "value": [1, 99]}]},
                      "sort": [{"distance": {"direction": "asc"}}], "size": 25, "page": page,
                      "reference_system": reference_system}, url=BODIES_API)
        for b in data.get("results", []):
            if pristine_only and b.get("reserve_level") != "Pristine":
                continue
            for ring in b.get("rings", []):
                signals = {s.get("name"): s.get("count") for s in ring.get("signals", [])}
                hits = next((c for n, c in signals.items() if (n or "").lower() == want), 0)
                if not hits:
                    continue
                rows.append({
                    "system": b.get("system_name"), "body": b.get("name"), "ring": ring.get("name"),
                    "ring_type": ring.get("type"), "hotspots": hits,
                    "overlap": hits >= 2,
                    "other_hotspots": {n: c for n, c in signals.items() if (n or "").lower() != want},
                    "reserve": b.get("reserve_level"),
                    "distance_ly": round(b.get("distance", 0), 1),
                    "distance_to_arrival_ls": round(b.get("distance_to_arrival") or 0),
                })
        results = data.get("results", [])
        if max_distance_ly is not None:
            if results and results[-1].get("distance", 0) > max_distance_ly or len(results) < 25:
                break
        elif len(rows) >= max_results or len(results) < 25:
            break
    if max_distance_ly is not None:
        rows = [r for r in rows if r["distance_ly"] <= max_distance_ly]
    if rank == "most_hotspots":
        rows.sort(key=lambda r: (-r["hotspots"], r["reserve"] != "Pristine", r["distance_ly"]))
    return rows[:max_results]
