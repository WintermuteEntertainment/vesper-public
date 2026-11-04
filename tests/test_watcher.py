"""Tests use made-up journal lines in a temp folder, never real game data.

Run: python -m unittest discover -s tests
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from elite_companion.journal import JournalTail, journal_files  # noqa: E402
from elite_companion.status import active_flags, flag_changes  # noqa: E402
from elite_companion.watcher import Watcher  # noqa: E402

DOCKED, SHIELDS, LOW_FUEL, IN_SHIP = 1 << 0, 1 << 3, 1 << 19, 1 << 24

SESSION = [
    {"event": "Fileheader", "gameversion": "4.4.2.1", "Odyssey": True},
    {"event": "Commander", "FID": "F123", "Name": "Testbot"},
    {"event": "LoadGame", "Commander": "Testbot", "FID": "F123", "Ship": "Asp", "Ship_Localised": "Asp Explorer",
     "ShipID": 7, "ShipName": "Wanderer", "ShipIdent": "TB-07", "FuelLevel": 32.0, "FuelCapacity": 32.0,
     "GameMode": "Solo", "Credits": 1000000},
    {"event": "Loadout", "Ship": "asp", "ShipID": 7, "ShipName": "Wanderer", "ShipIdent": "TB-07", "HullHealth": 1.0,
     "FuelCapacity": {"Main": 32.0, "Reserve": 0.63}, "CargoCapacity": 16, "MaxJumpRange": 45.2, "Rebuy": 500000},
    {"event": "Location", "StarSystem": "Sol", "SystemAddress": 1, "StarPos": [0, 0, 0], "Docked": True,
     "StationName": "Abraham Lincoln", "StationType": "Orbis", "MarketID": 100},
    {"event": "Cargo", "Vessel": "Ship", "Count": 3},
    {"event": "MissionAccepted", "MissionID": 55, "Name": "Mission_Courier", "LocalisedName": "Deliver data",
     "DestinationSystem": "Alpha Centauri", "Reward": 25000},
    {"event": "Undocked", "StationName": "Abraham Lincoln"},
    {"event": "NavRoute"},
    {"event": "FSDJump", "StarSystem": "Barnard's Star", "SystemAddress": 2, "StarPos": [1, 1, 1],
     "JumpDist": 5.9, "FuelLevel": 30.5},
    {"event": "Scan", "ScanType": "Detailed", "BodyName": "Barnard's Star 1", "PlanetClass": "Icy body",
     "WasDiscovered": False},
    {"event": "FSDJump", "StarSystem": "Alpha Centauri", "SystemAddress": 3, "StarPos": [2, 2, 2],
     "JumpDist": 4.4, "FuelLevel": 29.0},
    {"event": "MissionCompleted", "MissionID": 55, "Name": "Mission_Courier", "Reward": 25000},
]

SIDE = {
    "Cargo.json": {"event": "Cargo", "Vessel": "Ship", "Count": 3,
                   "Inventory": [{"Name": "gold", "Name_Localised": "Gold", "Count": 3}]},
    "NavRoute.json": {"event": "NavRoute", "Route": [
        {"StarSystem": "Sol", "SystemAddress": 1},
        {"StarSystem": "Barnard's Star", "SystemAddress": 2},
        {"StarSystem": "Alpha Centauri", "SystemAddress": 3},
        {"StarSystem": "Luhman 16", "SystemAddress": 4},
    ]},
}


def line(entry: dict, ts: str = "2026-10-24T20:00:00Z") -> str:
    return json.dumps({"timestamp": ts, **entry}) + "\n"


class TempJournalDir(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def write_status(self, flags: int, ts: str = "2026-10-24T20:00:00Z", **extra) -> None:
        (self.dir / "Status.json").write_text(json.dumps({"timestamp": ts, "event": "Status", "Flags": flags, **extra}))


class StatusTests(unittest.TestCase):
    def test_decodes_flags(self):
        self.assertEqual(active_flags({"Flags": DOCKED | SHIELDS}), {"Docked", "ShieldsUp"})
        self.assertEqual(active_flags({"Flags": 0, "Flags2": 1}), {"OnFoot"})

    def test_flag_changes(self):
        on, off = flag_changes({"Flags": DOCKED | SHIELDS}, {"Flags": SHIELDS | LOW_FUEL})
        self.assertEqual(on, {"LowFuel"})
        self.assertEqual(off, {"Docked"})


class TailTests(TempJournalDir):
    def test_waits_for_complete_lines(self):
        path = self.dir / "Journal.2026-10-24T200000.01.log"
        full = line({"event": "Music", "MusicTrack": "Exploration"})
        path.write_text(full[:20])
        tail = JournalTail(self.dir)
        self.assertEqual(tail.poll(), [])
        with open(path, "a") as f:
            f.write(full[20:])
        self.assertEqual([e["event"] for e in tail.poll()], ["Music"])
        self.assertEqual(tail.poll(), [])

    def test_follows_new_journal_file(self):
        first = self.dir / "Journal.2026-10-24T200000.01.log"
        first.write_text(line({"event": "Fileheader"}))
        tail = JournalTail(self.dir)
        self.assertEqual(len(tail.poll()), 1)
        with open(first, "a") as f:
            f.write(line({"event": "Shutdown"}))
        (self.dir / "Journal.2026-10-24T210000.01.log").write_text(line({"event": "Fileheader"}))
        self.assertEqual([e["event"] for e in tail.poll()], ["Shutdown", "Fileheader"])
        self.assertEqual(tail.path.name, "Journal.2026-10-24T210000.01.log")

    def test_finding_journals_does_not_stat_each_file(self):
        """Runs four times a second, so its cost must not grow with every journal the game has written."""
        names = ["Journal.2026-10-23T200000.01.log", "Journal.2026-10-24T200000.01.log",
                 "Journal.2026-10-24T210000.01.log"]
        for name in reversed(names):
            (self.dir / name).write_text(line({"event": "Fileheader"}))
        real_stat, touched = Path.stat, []

        def spy(path, **kwargs):
            touched.append(path.name)
            return real_stat(path, **kwargs)

        with patch.object(Path, "stat", spy):
            found = journal_files(self.dir)
        self.assertEqual([p.name for p in found], names)
        self.assertEqual([n for n in touched if n.startswith("Journal.")], [])

    def test_skips_garbage_lines(self):
        (self.dir / "Journal.2026-10-24T200000.01.log").write_text("not json\n" + line({"event": "Music"}))
        self.assertEqual(len(JournalTail(self.dir).poll()), 1)


class StateTests(TempJournalDir):
    def setUp(self):
        super().setUp()
        for name, data in SIDE.items():
            (self.dir / name).write_text(json.dumps(data))
        (self.dir / "Journal.2026-10-24T200000.01.log").write_text("".join(map(line, SESSION)))
        self.watcher = Watcher(self.dir)
        self.watcher.catch_up()
        self.state = self.watcher.state

    def test_commander_and_ship(self):
        s = self.state
        self.assertEqual((s.commander, s.fid, s.game_mode), ("Testbot", "F123", "Solo"))
        self.assertEqual((s.ship.type, s.ship.name, s.ship.cargo_capacity), ("Asp Explorer", "Wanderer", 16))
        self.assertEqual(s.ship.fuel_reserve_capacity, 0.63)

    def test_location_and_fuel(self):
        loc = self.state.location
        self.assertEqual(loc.system, "Alpha Centauri")
        self.assertFalse(loc.docked)
        self.assertIsNone(loc.station)
        self.assertEqual(self.state.fuel_main, 29.0)

    def test_cargo_from_side_file(self):
        self.assertEqual(self.state.cargo, {"Gold": 3})

    def test_route_remaining(self):
        self.assertEqual([s["StarSystem"] for s in self.state.route_remaining], ["Luhman 16"])

    def test_missions_and_session(self):
        s = self.state
        self.assertEqual(s.missions, {})
        self.assertEqual(s.session.jumps, 2)
        self.assertAlmostEqual(s.session.distance_ly, 10.3)
        self.assertEqual(s.session.first_discoveries, 1)
        self.assertEqual(s.session.earnings, {"missions": 25000})

    def test_ship_keeps_friendly_name(self):
        loadgame = dict(SESSION[2], Ship_Localised="Asp Explorer")
        w = Watcher(self.dir)
        w.state.apply(loadgame)
        w.state.apply(SESSION[3])
        self.assertEqual(w.state.ship.type, "Asp Explorer")

    def test_shutdown(self):
        self.assertTrue(self.state.game_running)
        self.state.apply({"event": "Shutdown"})
        self.assertFalse(self.state.game_running)

    def test_snapshot_is_json(self):
        snap = self.watcher.snapshot()
        json.dumps(snap)
        self.assertEqual(snap["route_remaining"], ["Luhman 16"])


class MissionBackfillTests(TempJournalDir):
    def test_details_come_from_older_journal(self):
        accepted = {"event": "MissionAccepted", "MissionID": 9, "Name": "Mission_Assassinate_name",
                    "LocalisedName": "Kill the pirate", "Target": "Bad Guy", "Reward": 1000000,
                    "Expiry": "2026-10-25T20:00:00Z"}
        (self.dir / "Journal.2026-10-23T200000.01.log").write_text(line(accepted, "2026-10-23T20:00:00Z"))
        login = {"event": "Missions", "Active": [{"MissionID": 9, "Name": "Mission_Assassinate_name", "Expires": 3600},
                                                 {"MissionID": 10, "Name": "Mission_Delivery_Agriculture_name", "Expires": 60}]}
        (self.dir / "Journal.2026-10-24T200000.01.log").write_text(line(login))
        w = Watcher(self.dir)
        w.catch_up()
        self.assertEqual(w.state.missions[9]["name"], "Kill the pirate")
        self.assertEqual(w.state.missions[9]["target"], "Bad Guy")
        self.assertEqual(w.state.missions[10]["name"], "Delivery Agriculture")
        self.assertEqual(w.state.missions[10]["expiry"], "2026-10-24T20:01:00Z")

    def test_finished_objective_found_in_older_journal(self):
        accepted = {"event": "MissionAccepted", "MissionID": 9, "LocalisedName": "Kill the pirate",
                    "Expiry": "2020-01-01T00:00:00Z"}
        redirected = {"event": "MissionRedirected", "MissionID": 9, "NewDestinationSystem": "Shinrarta Dezhra",
                      "NewDestinationStation": "Jameson Memorial"}
        (self.dir / "Journal.2026-10-22T200000.01.log").write_text(line(accepted, "2026-10-22T20:00:00Z"))
        (self.dir / "Journal.2026-10-23T200000.01.log").write_text(line(redirected, "2026-10-23T20:00:00Z"))
        login = {"event": "Missions", "Active": [{"MissionID": 9, "Name": "Mission_Assassinate_name", "Expires": 0}]}
        (self.dir / "Journal.2026-10-24T200000.01.log").write_text(line(login))
        w = Watcher(self.dir)
        w.catch_up()
        m = w.snapshot()["missions"][0]
        self.assertEqual(m["name"], "Kill the pirate")
        self.assertTrue(m["objective_complete"])
        self.assertEqual(m["turn_in_station"], "Jameson Memorial")
        self.assertEqual(m["status"], "ready to turn in")


class MissionStatusTests(unittest.TestCase):
    def test_status(self):
        from datetime import datetime, timezone
        from elite_companion.state import mission_status
        now = datetime(2026, 10, 24, 20, 0, tzinfo=timezone.utc)
        self.assertEqual(mission_status({"expiry": "2026-10-24T19:00:00Z"}, now), "expired")
        self.assertEqual(mission_status({"expiry": "2026-10-24T23:30:00Z"}, now), "active, 3h 30m left")
        self.assertEqual(mission_status({"expiry": "2026-10-24T20:05:00Z"}, now), "active, 5m left")
        self.assertEqual(mission_status({"expiry": "2020-01-01T00:00:00Z", "objective_complete": True}, now),
                         "ready to turn in")


class WatcherTests(TempJournalDir):
    def setUp(self):
        super().setUp()
        self.journal = self.dir / "Journal.2026-10-24T200000.01.log"
        self.journal.write_text(line(SESSION[2]))
        self.write_status(DOCKED | IN_SHIP)
        self.watcher = Watcher(self.dir)
        self.seen = []
        self.watcher.on("*", self.seen.append)

    def test_catch_up_is_replay_and_not_dispatched(self):
        events = self.watcher.catch_up()
        self.assertTrue(events and all(e.replay for e in events))
        self.assertEqual(self.seen, [])
        self.assertTrue(self.watcher.state.location.docked)

    def test_new_lines_and_flag_changes(self):
        self.watcher.catch_up()
        with open(self.journal, "a") as f:
            f.write(line({"event": "Undocked", "StationName": "X"}))
        self.write_status(IN_SHIP | LOW_FUEL, ts="2026-10-24T20:00:05Z")
        events = self.watcher.poll_once()
        names = [(e.name, e.data.get("flag"), e.data.get("on")) for e in events]
        self.assertIn(("Undocked", None, None), names)
        self.assertIn(("StatusFlag", "LowFuel", True), names)
        self.assertIn(("StatusFlag", "Docked", False), names)
        self.assertFalse(any(e.replay for e in events))

    def test_status_timestamp_only_change_is_ignored(self):
        self.watcher.catch_up()
        self.write_status(DOCKED | IN_SHIP, ts="2026-10-24T20:00:09Z", )
        self.assertEqual(self.watcher.poll_once(), [])

    def test_bad_handler_does_not_stop_others(self):
        self.watcher.on("Undocked", lambda e: 1 / 0)
        self.watcher.catch_up()
        with open(self.journal, "a") as f:
            f.write(line({"event": "Undocked"}))
        for ev in self.watcher.poll_once():
            self.watcher.dispatch(ev)
        self.assertEqual([e.name for e in self.seen], ["Undocked"])


if __name__ == "__main__":
    unittest.main()
