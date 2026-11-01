"""Notes, reminders, transcript log and the local-command word limit (no audio, no network)."""

import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from elite_companion.memory import MemoryStore, NoteError, Reminders  # noqa: E402
from elite_companion.tools import GameDataError, GameTools  # noqa: E402
from elite_companion.watcher import Event, Watcher  # noqa: E402


def ev(name, data, replay=False):
    return Event(name, {"timestamp": "2026-10-25T05:00:00Z", **data}, "journal", replay)


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.store = MemoryStore(self.dir / "memory.json")
        self.watcher = Watcher(self.dir)
        self.spoken = []
        self.rem = Reminders(self.watcher, self.store, self.spoken.append)

    def tearDown(self):
        self._tmp.cleanup()

    def test_dock_reminder_fires_once_at_the_right_place(self):
        self.store.add("Tritium to Bondar Gateway in Kareldt, about 4.1 million a hold.", "dock", "Jameson Memorial")
        self.rem._on_docked(ev("Docked", {"StationName": "Chandler Landing", "StarSystem": "Col 285"}))
        self.assertEqual(self.spoken, [])
        self.rem._on_docked(ev("Docked", {"StationName": "Jameson Memorial", "StarSystem": "Shinrarta Dezhra"}))
        self.rem._on_docked(ev("Docked", {"StationName": "Jameson Memorial", "StarSystem": "Shinrarta Dezhra"}))
        self.assertEqual(self.spoken, ["Reminder. Tritium to Bondar Gateway in Kareldt, about 4.1 million a hold."])
        self.assertEqual(self.store.list(), [])
        self.assertTrue(self.store.list(include_done=True)[0]["done"])

    def test_dock_by_system_and_arrival(self):
        self.store.add("Sell the gold.", "dock", "Shinrarta Dezhra")
        self.store.add("Scoop here, next star is a brown dwarf.", "arrive", "Col 285 Sector FY-O b20-2")
        self.rem._on_arrived(ev("FSDJump", {"StarSystem": "Col 285 Sector FY-O b20-2"}))
        self.rem._on_docked(ev("Docked", {"StationName": "Skullforge", "StarSystem": "Shinrarta Dezhra"}))
        self.assertEqual(self.spoken, ["Reminder. Scoop here, next star is a brown dwarf.", "Reminder. Sell the gold."])

    def test_replayed_events_never_fire(self):
        self.store.add("x", "dock", "Jameson Memorial")
        self.rem._on_docked(ev("Docked", {"StationName": "Jameson Memorial"}, replay=True))
        self.assertEqual(self.spoken, [])

    def test_startup_and_overdue_time_reminders(self):
        self.store.add("Check the mission timer.", "startup")
        note = self.store.add("Overdue one.", "time", minutes_from_now=5)
        notes = self.store._load()
        for n in notes:
            if n["id"] == note["id"]:
                n["due"] = (datetime.now() - timedelta(minutes=1)).isoformat(timespec="seconds")
        self.store._save(notes)
        self.store.add("Not yet.", "time", minutes_from_now=60)
        self.rem.startup()
        self.assertEqual(self.spoken, ["Reminder. Check the mission timer.", "Reminder. Overdue one."])
        self.assertEqual([n["text"] for n in self.store.list()], ["Not yet."])

    def test_clock_time_rolls_to_tomorrow(self):
        past = (datetime.now() - timedelta(hours=1)).strftime("%H:%M")
        due = datetime.fromisoformat(self.store.add("x", "time", at_local_time=past)["due"])
        self.assertGreater(due, datetime.now())

    def test_validation_and_forget(self):
        self.assertEqual(self.store.add("x", "dock")["place"], "anywhere")  # no place: the next dock anywhere
        with self.assertRaises(NoteError):
            self.store.add("x", "arrive")  # no system
        with self.assertRaises(NoteError):
            self.store.add("x", "time")  # no time
        self.store.add("Mike Dailly hunts me near Col 285.")
        self.store.add("Buy limpets.")
        self.assertEqual(len(self.store.forget(search="dailly")), 1)
        self.assertEqual([n["text"] for n in self.store.list()], ["x", "Buy limpets."])

    def test_tools_wrap_errors(self):
        tools = GameTools(self.watcher, self.store)
        self.assertIn("#1", tools.remember("Plain note")["saved"])
        self.assertEqual(tools.recall_notes()["count"], 1)
        with self.assertRaises(GameDataError):
            tools.forget_note(note_id=99)


class CopilotTextTests(unittest.TestCase):
    def test_commands_only_for_short_utterances_and_transcript(self):
        from elite_companion import config
        from elite_companion.copilot import Copilot
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            pilot = Copilot(config.load(d / "none.toml"), watcher=Watcher(d), speak=False,
                            notes=MemoryStore(d / "memory.json"), log_dir=d / "logs")
            pilot.brain = None
            self.assertEqual(pilot.handle_text("start over"), "Starting fresh.")
            long_q = "Let's start over, but give me the highest profit market I can buy from here"
            self.assertNotEqual(pilot.handle_text(long_q), "Starting fresh.")
            log = next((d / "logs").glob("copilot-*.log")).read_text(encoding="utf-8")
            self.assertIn("? start over", log)
            self.assertIn("> Starting fresh.", log)


if __name__ == "__main__":
    unittest.main()
