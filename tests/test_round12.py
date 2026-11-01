"""Leaving-port checklist, dock-anywhere reminders, paste and restart commands, DSS in the fit check."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from elite_companion import mining  # noqa: E402
from elite_companion.copilot import COMMANDS  # noqa: E402
from elite_companion.lookout import Lookout  # noqa: E402
from elite_companion.memory import MemoryStore, Reminders  # noqa: E402
from elite_companion.watcher import Event, Watcher  # noqa: E402

MINER = {"event": "Loadout", "Modules": [{"Item": "int_refinery_size4_class5"},
                                         {"Item": "int_dronecontrol_prospector_size1_class5"},
                                         {"Item": "int_fighterbay_size5_class1"}]}


def undock(w, day="2099-01-01"):
    w.dispatch(Event("Undocked", {"timestamp": f"{day}T00:00:00Z", "event": "Undocked"}, "journal"))


class ChecklistTests(unittest.TestCase):
    def test_limpets_and_fighter_pilot(self):
        w = Watcher(Path("."))
        said = []
        Lookout(w, lambda t, p: said.append(t))
        w.state.records["Loadout"] = MINER
        undock(w)
        self.assertEqual(said, ["You have no limpets aboard. Did you mean to buy some? "
                                "Fighter hangar fitted. Is your fighter pilot aboard?"])
        w.state.cargo = {"Limpet": 12}
        undock(w)  # fighter pilot asked once a day
        self.assertEqual(said[-1], "Only 12 limpets aboard. Enough for this run?")
        w.state.cargo = {"Limpet": 60}
        undock(w)
        self.assertEqual(len(said), 2)

    def test_not_a_mining_ship(self):
        w = Watcher(Path("."))
        said = []
        Lookout(w, lambda t, p: said.append(t))
        w.state.records["Loadout"] = {"Modules": [{"Item": "hpt_multicannon_gimbal_medium"}]}
        undock(w)
        self.assertEqual(said, [])


class DockAnywhereTests(unittest.TestCase):
    def test_fires_at_any_station(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = MemoryStore(Path(tmp) / "m.json")
            store.add("Update Vesper's prospector code.", "dock")
            w = Watcher(Path("."))
            said = []
            Reminders(w, store, said.append)
            w.dispatch(Event("Docked", {"event": "Docked", "StationName": "Wales Reach", "StarSystem": "X"}, "journal"))
            self.assertTrue(any("prospector" in s for s in said))


class CommandTests(unittest.TestCase):
    def action(self, text):
        return next((a for p, a in COMMANDS if p.search(text)), None)

    def test_paste(self):
        self.assertEqual(self.action("paste."), "paste")
        self.assertEqual(self.action("paste the system"), "paste")
        self.assertIsNone(self.action("where can i sell toothpaste"))


class FitTests(unittest.TestCase):
    def test_hotspots_need_a_dss(self):
        out = mining.fit_check([{"Item": "int_refinery_size4_class5"}], {}, 256)
        self.assertEqual(out["missing"]["seeing hotspots"], ["detailed surface scanner"])


if __name__ == "__main__":
    unittest.main()
