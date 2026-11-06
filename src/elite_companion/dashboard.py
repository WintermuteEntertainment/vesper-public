"""Second-screen dashboard: a live page for a tablet, phone or a window pinned in VR.

Runs inside the co-pilot (see [dashboard] in config.toml), or on its own:

    vesper dashboard [--port 8770]

Then open http://<this PC's address>:8770 on the tablet. It shows game state, and its Settings page can
change Vesper's settings (see settings.py for which, and [dashboard] editable to turn that off).
"""

from __future__ import annotations

import ipaddress
import json
import logging
import socket
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import history
from .lookout import SCOOPABLE
from .state import _seconds_between
from .settings import Settings
from .tools import GameDataError, GameTools

log = logging.getLogger(__name__)
PAGE = Path(__file__).parent / "web" / "dashboard.html"
PIPS = ("sys", "eng", "wep")
MAX_POST = 4096


def trusted_request(host: str | None, origin: str | None) -> bool:
    """Is this request from a page of ours? The settings have no password, so a web page open somewhere on this
    network mustn't be able to reach them: not directly (its Origin would differ), and not by pointing its own
    domain name at this PC (the address would be a name we don't recognise)."""
    name = (urlsplit("//" + (host or "")).hostname or "").lower()
    try:
        ipaddress.ip_address(name)
        known = True
    except ValueError:
        known = name in ("localhost", socket.gethostname().lower()) or name.endswith((".ts.net", ".local"))
    return known and (not origin or urlsplit(origin).netloc.lower() == (host or "").lower())


def may_edit(client_ip: str, network_edit: bool) -> bool:
    """May this device change settings? The PC running Vesper always can; others only with [dashboard] network_edit."""
    return network_edit or ipaddress.ip_address(client_ip).is_loopback


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


def serve(tools: GameTools, host: str = "0.0.0.0", port: int = 8770,
          settings: Settings | None = None) -> ThreadingHTTPServer:
    """Start the dashboard in a background thread and return the server."""
    settings = settings or Settings()

    class Handler(BaseHTTPRequestHandler):
        def _json(self, data: dict, code: int = 200) -> None:
            self._send(json.dumps(data, default=str).encode(), "application/json", code)

        def _trusted(self) -> bool:
            if trusted_request(self.headers.get("Host"), self.headers.get("Origin")):
                return True
            self._json({"error": "Settings can't be reached from there."}, 403)
            return False

        def do_GET(self):
            url = urlsplit(self.path)
            if url.path == "/api/state":
                self._json(dashboard_state(tools))
            elif url.path == "/api/settings":
                if self._trusted():
                    state = settings.state(refresh="refresh" in parse_qs(url.query))
                    if not may_edit(self.client_address[0], settings.network_edit):
                        state["editable"], state["pc_only"] = False, True
                    self._json(state)
            elif self.path in ("/", "/index.html"):
                self._send(PAGE.read_bytes(), "text/html; charset=utf-8")
            else:
                self.send_error(404)

        def do_POST(self):
            if self.path != "/api/settings":
                self.send_error(404)
                return
            if not self._trusted():
                return
            if not may_edit(self.client_address[0], settings.network_edit):
                self._json({"error": "Settings can only be changed on the PC running Vesper."}, 403)
                return
            length = int(self.headers.get("Content-Length") or 0)
            if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json" or not 0 < length <= MAX_POST:
                self._json({"error": "That isn't a settings change."}, 400)
                return
            try:
                data = json.loads(self.rfile.read(length))
                self._json(settings.change(data["key"], data["value"]))
            except (KeyError, TypeError):
                self._json({"error": "That isn't a settings change."}, 400)
            except ValueError as e:  # includes SettingError, and a config.toml it couldn't edit or read
                self._json({"error": str(e)}, 400)
            except OSError as e:
                log.exception("couldn't save a setting")
                self._json({"error": f"Couldn't save config.toml: {e}"}, 500)

        def _send(self, body: bytes, kind: str, code: int = 200) -> None:
            self.send_response(code)
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
