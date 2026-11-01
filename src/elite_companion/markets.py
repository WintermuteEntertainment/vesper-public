"""Every market screen you open, saved locally and (optionally) shared with EDDN.

The game rewrites Market.json each time you open a station's commodity market.
That's the freshest price data there is, so it's kept in
~/.vesper/markets/<MarketID>.json and preferred over Spansh's copy
whenever it's newer. With [eddn] enabled, each new market is also uploaded to
EDDN (the community feed Spansh, Inara and EDSM read), the same way EDMC does.
"""

from __future__ import annotations

import json
import logging
import threading
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from .config import HOME

log = logging.getLogger(__name__)
CACHE_DIR = HOME / "markets"
EDDN_URL = "https://eddn.edcd.io:4430/upload/"
COMMODITY_SCHEMA = "https://eddn.edcd.io/schemas/commodity/3"
SOFTWARE = ("Vesper", "0.1.0")


def save(market: dict, cache_dir: Path = CACHE_DIR) -> Path | None:
    """Store a Market.json snapshot (needs MarketID and Items)."""
    if not market.get("MarketID") or not market.get("Items"):
        return None
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{market['MarketID']}.json"
    path.write_text(json.dumps(market), encoding="utf-8")
    return path


def load(station: str, system: str | None = None, cache_dir: Path = CACHE_DIR) -> dict | None:
    """The newest cached market for a station (by name, optionally system)."""
    best = None
    for path in cache_dir.glob("*.json") if cache_dir.is_dir() else []:
        try:
            m = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if m.get("StationName", "").lower() != station.lower():
            continue
        if system and m.get("StarSystem", "").lower() != system.lower():
            continue
        if best is None or m.get("timestamp", "") > best.get("timestamp", ""):
            best = m
    return best


# ---- EDDN ----

def _symbol(name: str) -> str:
    """"$platinum_name;" -> "platinum" (EDDN wants the game's symbol, lower case)."""
    return name.strip().removeprefix("$").removesuffix(";").removesuffix("_name").lower()


def eddn_message(market: dict, commander: str, gameversion: str, gamebuild: str, odyssey: bool,
                 test: bool = False) -> dict:
    """A Market journal snapshot as an EDDN commodity/3 message."""
    commodities = []
    for i in market.get("Items", []):
        if "nonmarketable" in (i.get("Category") or "").lower():
            continue  # not tradeable on the market (e.g. mission-only goods)
        commodities.append({
            "name": _symbol(i.get("Name", "")),
            "meanPrice": int(i.get("MeanPrice", 0)),
            "buyPrice": int(i.get("BuyPrice", 0)),
            "stock": int(i.get("Stock", 0)),
            "stockBracket": i.get("StockBracket", ""),
            "sellPrice": int(i.get("SellPrice", 0)),
            "demand": int(i.get("Demand", 0)),
            "demandBracket": i.get("DemandBracket", ""),
        })
    return {
        "$schemaRef": COMMODITY_SCHEMA + ("/test" if test else ""),
        "header": {"uploaderID": commander or "anonymous", "softwareName": SOFTWARE[0], "softwareVersion": SOFTWARE[1],
                   "gameversion": gameversion or "", "gamebuild": gamebuild or ""},
        "message": {"systemName": market["StarSystem"], "stationName": market["StationName"],
                    "marketId": market["MarketID"], "timestamp": market["timestamp"],
                    "horizons": True, "odyssey": bool(odyssey), "commodities": commodities},
    }


def upload(message: dict, timeout: float = 20) -> tuple[bool, str]:
    """Send one message to EDDN; returns (ok, the server's reply or the error)."""
    req = urllib.request.Request(EDDN_URL, data=json.dumps(message).encode("utf-8"),
                                 headers={"Content-Type": "application/json", "User-Agent": "/".join(SOFTWARE)})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200, resp.read().decode("utf-8", "replace")[:200]
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:300]}"
    except (urllib.error.URLError, TimeoutError) as e:
        return False, str(e)


class MarketRecorder:
    """Watcher subscriber: caches each new market screen and, if enabled, uploads it to EDDN."""

    def __init__(self, watcher, eddn: bool = False, cache_dir: Path = CACHE_DIR, log_path: Path = HOME / "eddn.log"):
        self.watcher, self.eddn, self.cache_dir, self.log_path = watcher, eddn, cache_dir, log_path
        self._sent: set[tuple] = set()
        watcher.on("Market", self.on_market)

    def on_market(self, ev) -> None:
        """Cache each market snapshot, and upload new ones to EDDN when [eddn] upload is on.
        Replays, repeats and beta-game data are never sent.
        """
        market = ev.data.get("_file") or {}
        if not market.get("Items"):
            return
        save(market, self.cache_dir)
        if ev.replay or not self.eddn:
            return
        key = (market.get("MarketID"), market.get("timestamp"))
        if key in self._sent:
            return
        self._sent.add(key)
        st = self.watcher.state
        if "beta" in (st.game_version or "").lower():
            return  # EDDN asks clients not to send beta-game data
        msg = eddn_message(market, st.commander, st.game_version, st.records.get("Fileheader", {}).get("build", ""),
                           bool(st.odyssey))
        threading.Thread(target=self._send, args=(msg,), daemon=True, name="eddn").start()

    def _send(self, msg: dict) -> None:
        """Upload in the background and log the result to eddn.log."""
        ok, reply = upload(msg)
        line = (f"{datetime.now(timezone.utc):%Y-%m-%dT%H:%M:%SZ} {'OK ' if ok else 'ERR'} "
                f"{msg['message']['stationName']} ({msg['message']['systemName']}), "
                f"{len(msg['message']['commodities'])} commodities: {reply}\n")
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(line)
        except OSError:
            log.warning("couldn't write eddn.log")
