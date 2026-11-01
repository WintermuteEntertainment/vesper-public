"""Second-screen dashboard: a live page for a tablet, phone or a window pinned in VR.

Runs inside the co-pilot (see [dashboard] in config.toml), or on its own:

    vesper dashboard [--port 8770]

Then open http://<this PC's address>:8770 on the tablet. Read-only: it only shows game state.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import history
from .lookout import SCOOPABLE
from .state import _seconds_between
from .tools import GameDataError, GameTools

log = logging.getLogger(__name__)
PAGE = Path(__file__).parent / "web" / "dashboard.html"
PIPS = ("sys", "eng", "wep")


def dashboard_state(tools: GameTools) -> dict:
    """Everything the page shows, in one small JSON document."""
    try:
        s = tools.status()
    except GameDataError as e:
        return {"error": str(e)}
    st = tools.watcher.state
    ship, loc = s["ship"], s["location"]
    raw = st.status if isinstance(st.status, dict) else {}
    pips = dict(zip(PIPS, raw.get("Pips") or [0, 0, 0]))
    route = [{"system": r.get("StarSystem"), "star": r.get("StarClass"),
              "scoop": (r.get("StarClass") or " ")[0] in SCOOPABLE} for r in st.route_remaining]
    missions = []
    for m in tools.missions()["missions"]:
        left = None
        try:
            left = (datetime.fromisoformat(m["expiry"].replace("Z", "+00:00")) - datetime.now(timezone.utc)).total_seconds()
        except (KeyError, AttributeError, ValueError):
            pass
        missions.append({"name": m.get("name"), "where": " / ".join(x for x in (m.get("go_to_station"),
                         m.get("go_to_system")) if x), "status": m["status"], "seconds_left": left})
    cargo_used = sum(c for c in s["cargo"].values() if isinstance(c, (int, float)))
    return {
        "commander": s["commander"], "credits": s["credits"], "legal": s["legal_state"],
        "game_running": s["game_running"], "flags": s["flags"],
        "location": {"system": loc.get("system"), "where": loc.get("station") if loc.get("docked") else loc.get("body"),
                     "mode": "Docked" if loc.get("docked") else "Supercruise" if loc.get("supercruise") else "Normal space"},
        "ship": {"name": ship.get("name"), "type": ship.get("type"), "hull": ship.get("hull_health"),
                 "fuel": s["fuel"].get("main"), "fuel_capacity": ship.get("fuel_capacity"),
                 "jump_range": ship.get("max_jump_range"), "cargo_used": cargo_used,
                 "cargo_capacity": ship.get("cargo_capacity"), "rebuy": ship.get("rebuy")},
        "cargo": s["owned_cargo"], "mission_cargo": s["mission_cargo"], "pips": pips,
        "route": route, "missions": missions, "threat": tools.threat_summary(), "target": st.target,
        "refits": tools.recent_refits(minutes=60),
        "conversation": history.recent(45, 12),
        "mining": _mining(tools),
        "updated": s.get("last_event_time"),
    }


def _mining(tools: GameTools) -> dict | None:
    """The current mining session, shown only while it's recent (an hour since the last mining event)."""
    m = tools.watcher.state.mining
    if not m or _seconds_between(m.get("last"), datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")) > 3600:
        return None
    try:
        return tools.mining_session()
    except GameDataError:
        return None


def serve(tools: GameTools, host: str = "0.0.0.0", port: int = 8770) -> ThreadingHTTPServer:
    """Start the dashboard in a background thread and return the server."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.split("?")[0] == "/api/state":
                body = json.dumps(dashboard_state(tools), default=str).encode()
                kind = "application/json"
            elif self.path in ("/", "/index.html"):
                body, kind = PAGE.read_bytes(), "text/html; charset=utf-8"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # keep the console quiet
            pass

    server = ThreadingHTTPServer((host, port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True, name="dashboard").start()
    return server


def main(argv: list[str]) -> int:
    """`vesper dashboard`: serve the dashboard on its own, without the copilot."""
    import argparse
    import time

    from .watcher import Watcher
    p = argparse.ArgumentParser(prog="vesper dashboard", description="Live second-screen dashboard")
    p.add_argument("--host", default="127.0.0.1", help="0.0.0.0 to reach it from other devices")
    p.add_argument("--port", type=int, default=8770)
    a = p.parse_args(argv)
    watcher = Watcher()
    watcher.start()
    serve(GameTools(watcher), a.host, a.port)
    print(f"Dashboard on http://localhost:{a.port} (Ctrl+C to stop)", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        return 0
