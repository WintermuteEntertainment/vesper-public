"""Mining: session tracking, prospector readouts, limpet/hold callouts, fit check, hotspot names (no network)."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from elite_companion import mining, spansh  # noqa: E402
from elite_companion.memory import MemoryStore  # noqa: E402
from elite_companion.tools import GameTools  # noqa: E402
from elite_companion.watcher import Event, Watcher  # noqa: E402

from test_phase4 import journal  # noqa: E402


def prospect(ts, mats, core=None, content="High", remaining=100.0):
    e = {"_ts": ts, "event": "ProspectedAsteroid", "Content_Localised": f"Material Content: {content}",
         "Remaining": remaining, "Materials": [{"Name": n, "Proportion": p} for n, p in mats.items()]}
    if core:
        e |= {"MotherlodeMaterial": core.lower(), "MotherlodeMaterial_Localised": core}
    return e


class MiningSessionTests(unittest.TestCase):
    def test_session_counts_and_resets_after_an_hour(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            journal(d, [
                {"event": "LoadGame", "Commander": "Jameson", "Ship": "type9_military", "Credits": 1},
                {"_ts": "2099-01-01T00:00:00Z", "event": "LaunchDrone", "Type": "Prospector"},
                prospect("2099-01-01T00:00:05Z", {"Platinum": 31.5, "Bauxite": 12.0}),
                {"_ts": "2099-01-01T00:01:00Z", "event": "LaunchDrone", "Type": "Collector"},
                {"_ts": "2099-01-01T00:02:00Z", "event": "MiningRefined", "Type": "$platinum_name;",
                 "Type_Localised": "Platinum"},
                {"_ts": "2099-01-01T00:03:00Z", "event": "MiningRefined", "Type_Localised": "Platinum"},
                prospect("2099-01-01T00:04:00Z", {"Bauxite": 20.0}, core="Void Opals"),
                {"_ts": "2099-01-01T00:05:00Z", "event": "AsteroidCracked", "Body": "X A Ring"},
            ])
            w = Watcher(d)
            w.catch_up()
            t = GameTools(w, MemoryStore(d / "m.json"))
            s = t.mining_session()
            self.assertEqual(s["refined_tons"], {"Platinum": 2})
            self.assertEqual((s["asteroids_prospected"], s["cores_found"], s["cores_cracked"]), (2, 1, 1))
            self.assertEqual(s["limpets_launched"], {"Prospector": 1, "Collector": 1})
            self.assertEqual(s["last_prospect"]["core"], "Void Opals")
            # an hour later a new session starts
            w.state.apply({"timestamp": "2099-01-01T02:00:00Z", "event": "MiningRefined", "Type_Localised": "Gold"})
            self.assertEqual(t.mining_session()["refined_tons"], {"Gold": 1})

    def test_collectors_alone_dont_start_a_session(self):
        w = Watcher(Path("."))
        w.state.apply({"timestamp": "2099-01-01T00:00:00Z", "event": "LaunchDrone", "Type": "Collector"})
        self.assertEqual(w.state.mining, {})


class ReadoutTests(unittest.TestCase):
    P = {"materials": {"Platinum": 34.2, "Bauxite": 20.0, "Painite": 8.1}, "content": "High", "core": None,
         "remaining": 100}

    def test_full_readout_and_verdicts(self):
        self.assertEqual(mining.readout(self.P),
                         "High content. Platinum 34 percent, Bauxite 20 percent, Painite 8 percent. Mine it.")
        self.assertEqual(mining.verdict({"materials": {"Painite": 17}}), "Mine it")
        self.assertEqual(mining.verdict({"materials": {"Painite": 10}}), "Worth a few chunks")
        self.assertEqual(mining.verdict({"materials": {"Bromellite": 9}}, target="Bromellite"), "Mine it")
        self.assertEqual(mining.verdict({"materials": {"Bauxite": 9}}, target="Low Temperature Diamonds"), "Skip")
        self.assertEqual(mining.verdict({"materials": {"Bauxite": 40}}), "Skip")
        self.assertEqual(mining.verdict({"materials": {"Bauxite": 40}, "core": "Void Opals"}), "Crack it")

    def test_styles(self):
        self.assertEqual(mining.readout({**self.P, "core": "Void Opals"}, "verdict"),
                         "Core: Void Opals. Crack it, and laser it too.")
        self.assertIsNone(mining.readout({"materials": {"Bauxite": 40}}, "good"))
        self.assertIsNone(mining.readout(self.P, "off"))
        self.assertIn("60 percent left", mining.readout({**self.P, "remaining": 60.0}))


class CalloutTests(unittest.TestCase):
    def test_limpet_and_hold_warnings_once_each(self):
        w = Watcher(Path("."))
        said = []
        mining.MiningCallouts(w, lambda t, p: said.append(t))
        w.state.ship.cargo_capacity = 100
        w.state.apply({"timestamp": "2099-01-01T00:00:00Z", "event": "LaunchDrone", "Type": "Prospector"})

        def cargo(limpets, ore):
            e = {"timestamp": "2099-01-01T00:05:00Z", "event": "Cargo", "Vessel": "Ship",
                 "Inventory": [{"Name": "drones", "Name_Localised": "Limpet", "Count": limpets},
                               {"Name": "platinum", "Name_Localised": "Platinum", "Count": ore}]}
            w.state.apply(e)
            w.dispatch(Event("Cargo", e, "journal"))

        cargo(10, 50)
        cargo(5, 60)
        cargo(4, 70)
        cargo(2, 94)
        cargo(0, 100)
        self.assertEqual(said, ["5 limpets left.", "2 limpets left.", "Hold nearly full, 4 tons left.",
                                "Out of limpets.", "Hold full."])


class FitAndNameTests(unittest.TestCase):
    def test_fit_check(self):
        mods = [{"Item": "int_refinery_size4_class5"}, {"Item": "int_dronecontrol_prospector_size1_class5"},
                {"Item": "int_dronecontrol_collection_size7_class5"}, {"Item": "hpt_mininglaser_turret_medium"}]
        out = mining.fit_check(mods, {"Limpet": 30}, 256)
        self.assertTrue(out["ready_for"]["laser"])
        self.assertFalse(out["ready_for"]["core"])
        self.assertIn("seismic charge launcher", out["missing"]["core"])
        self.assertIsNone(out["tip"])

    def test_hotspot_names(self):
        self.assertEqual(spansh.ring_signal_name("Void Opals"), "Void Opal")
        self.assertEqual(spansh.ring_signal_name("LTDs"), "Low Temperature Diamonds")
        self.assertEqual(spansh.ring_signal_name("painite"), "Painite")


if __name__ == "__main__":
    unittest.main()
