"""GameTools tests with made-up journals (no network)."""

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from elite_companion.tools import TOOL_SPECS, GameDataError, GameTools, call_tool  # noqa: E402
from elite_companion.watcher import Watcher  # noqa: E402


def ts(minutes_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


class CrimeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        d = Path(self._tmp.name)
        lines = [
            {"timestamp": ts(60), "event": "LoadGame", "Commander": "Testbot", "Ship": "type7", "ShipID": 1},
            {"timestamp": ts(50), "event": "CommitCrime", "CrimeType": "murder", "Faction": "Union", "Victim": "Bob", "Bounty": 5000},
            {"timestamp": ts(40), "event": "PayBounties", "Faction": "Union", "Amount": 5000},
            {"timestamp": ts(30), "event": "CommitCrime", "CrimeType": "assault", "Faction": "Union", "Victim": "Cop", "Bounty": 100},
            {"timestamp": ts(20), "event": "CommitCrime", "CrimeType": "dockingMinorTresspass", "Faction": "Other", "Fine": 400},
            {"timestamp": ts(10), "event": "Died", "KillerName": "Mike Dailly"},
        ]
        (d / "Journal.2026-10-24T200000.01.log").write_text("".join(json.dumps(l) + "\n" for l in lines))
        w = Watcher(d)
        w.catch_up()
        self.tools = GameTools(w)

    def tearDown(self):
        self._tmp.cleanup()

    def test_unpaid_counts_only_crimes_after_payment(self):
        r = self.tools.crimes(days=1)
        self.assertEqual(r["unpaid_estimate_by_faction"],
                         {"Union": {"bounties": 100, "fines": 0, "crimes": 1}, "Other": {"bounties": 0, "fines": 400, "crimes": 1}})
        self.assertEqual(len(r["crimes"]), 3)
        self.assertEqual(r["deaths"][0]["killer"], "Mike Dailly")

    def test_dispatch_matches_specs(self):
        names = {s["name"] for s in TOOL_SPECS}
        self.assertEqual(call_tool(self.tools, "get_session_summary", {})["commander"], "Testbot")
        with self.assertRaises(GameDataError):
            call_tool(self.tools, "launch_missiles", {})
        self.assertIn("get_crimes", names)

    def test_no_game_data_message(self):
        empty = tempfile.TemporaryDirectory()
        try:
            with self.assertRaises(GameDataError):
                GameTools(Watcher(empty.name)).status()
        finally:
            empty.cleanup()


if __name__ == "__main__":
    unittest.main()
