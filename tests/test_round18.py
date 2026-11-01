"""Bindings, engineering materials, shopping list, outfitting, station rows, paste mishearings,
mission triage, the material-trader reminder, and images from tools (no network, no screen)."""

import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from elite_companion import bindings, engineering_materials as em, spansh  # noqa: E402
bindings.set_nicknames({"T16000M": "right stick", "LogitechExtreme3DPro": "left stick"})  # as a user's config would
from elite_companion.brain import Brain  # noqa: E402
from elite_companion.copilot import COMMANDS  # noqa: E402
from elite_companion.lookout import Lookout  # noqa: E402
from elite_companion.memory import MemoryStore  # noqa: E402
from elite_companion.tools import GameTools  # noqa: E402
from elite_companion.watcher import Event, Watcher  # noqa: E402

from test_phase4 import journal  # noqa: E402

BINDS = """<Root PresetName="Custom" MajorVersion="4" MinorVersion="2">
<OrbitLinesToggle><Primary Device="T16000M" Key="Joy_13" /><Secondary Device="{NoDevice}" Key="" /></OrbitLinesToggle>
<UseBoostJuice><Primary Device="LogitechExtreme3DPro" Key="Joy_2" /><Secondary Device="Keyboard" Key="Key_Tab" /></UseBoostJuice>
<DeployHeatSink><Primary Device="{NoDevice}" Key="" /><Secondary Device="Keyboard" Key="Key_V">
<Modifier Device="Keyboard" Key="Key_LeftShift" /></Secondary></DeployHeatSink>
</Root>"""


class BindingTests(unittest.TestCase):
    def test_by_action_and_by_button(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "Custom.4.2.binds").write_text(BINDS)
            self.assertEqual(bindings.lookup("orbit lines", directory=d)["bound"],
                             {"OrbitLinesToggle": [{"device": "right stick", "key": "button 13", "modifiers": None}]})
            hs = bindings.lookup("heat sink", directory=d)["bound"]["DeployHeatSink"][0]
            self.assertEqual((hs["key"], hs["modifiers"]), ("V", ["Key_LeftShift"]))
            self.assertEqual(bindings.lookup(device="left stick", button=2, directory=d)["bound_to"], ["UseBoostJuice"])


class MaterialsTests(unittest.TestCase):
    def test_reinforced_shields_rolls_and_shortfall(self):
        held = {"phosphorus": 10, "conductivecomponents": 3, "mechanicalcomponents": 2, "conductiveceramics": 6,
                "configurablecomponents": 6, "manganese": 6, "conductivepolymers": 4, "improvisedcomponents": 2}
        out = em.check("shield generator", "reinforced", held)
        self.assertEqual(out["blueprint"], "Reinforced Shields")
        rolls = {g["grade"]: g["rolls_you_can_afford"] for g in out["grades"]}
        self.assertEqual(rolls, {1: 10, 2: 3, 3: 2, 4: 6, 5: 0})
        self.assertEqual(out["missing_for_one_roll_each"], {"Arsenic": 1})
        self.assertNotIn("Hi-cap", em.blueprint_name("Shield Generator", None))  # experimentals aren't blueprints
        self.assertEqual(em.experimental_cost("shield generator", "hi-cap", held)["experimental"], "Hi-cap")


class ShoppingAndOutfittingTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.d = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def tools(self, events):
        journal(self.d, [{"event": "LoadGame", "Commander": "Jameson", "Ship": "type9_military", "Credits": 1},
                         {"event": "Location", "StarSystem": "Laksak", "StationName": "Trader's Rest", "Docked": True}]
                + events)
        w = Watcher(self.d)
        w.catch_up()
        return GameTools(w, MemoryStore(self.d / "m.json"))

    def test_shopping_list(self):
        t = self.tools([{"event": "Materials", "Raw": [{"Name": "germanium", "Count": 3}], "Manufactured": [], "Encoded": []}])
        t.shopping_list(add="Germanium, Mercury")
        out = t.shopping_list(remove="mercury")["shopping_list"]
        self.assertEqual([r["item"] for r in out], ["Germanium"])
        self.assertEqual((out[0]["have"], out[0]["trader"]), (3, "raw"))

    def test_outfitting_here(self):
        (self.d / "Outfitting.json").write_text(
            '{"timestamp":"2099-01-01T00:00:00Z","event":"Outfitting","StationName":"Trader\'s Rest","StarSystem":"Laksak",'
            '"Items":[{"Name":"int_shieldgenerator_size5_class3","BuyPrice":100},{"Name":"int_cargorack_size5_class1","BuyPrice":5},'
            '{"Name":"int_shieldgenerator_size4_class5","BuyPrice":9}]}')
        t = self.tools([])
        out = t.outfitting_here(size=5, engineerable_only=True)
        self.assertEqual([m["module"] for m in out["modules"]], ["5C Shield Generator"])
        self.assertTrue(out["this_is_where_you_are"])


class StationRowTests(unittest.TestCase):
    def test_body_and_stale_data(self):
        row = spansh._station_row({"name": "Fernandes Tourism Resort", "system_name": "Laksak", "type": "Settlement",
                                   "is_planetary": True, "body_name": "Laksak BC 2 a", "updated_at": "2023-09-12T00:00:00Z"})
        self.assertEqual(row["on_body"], "Laksak BC 2 a")
        self.assertIn("2023-09-12", row["stale_data"])
        fresh = spansh._station_row({"name": "X", "updated_at": datetime.now(timezone.utc).isoformat()})
        self.assertNotIn("stale_data", fresh)
        self.assertNotIn("on_body", fresh)


class VoiceAndLookoutTests(unittest.TestCase):
    def test_paste_mishearings(self):
        act = lambda t: next((a for p, a in COMMANDS if p.search(t)), None)
        for t in ("paced.", "paced it", "base,", "paste from the clipboard"):
            self.assertEqual(act(t), "paste", t)
        self.assertIsNone(act("set my home base"))

    def test_startup_triage_and_trader_reminder(self):
        with tempfile.TemporaryDirectory() as tmp:
            w = Watcher(Path("."))
            said = []
            lk = Lookout(w, lambda s, p: said.append(s))
            now = datetime(2099, 1, 1, tzinfo=timezone.utc)
            w.state.missions = {1: {"name": "Deliver tea", "expiry": (now + timedelta(minutes=50)).strftime("%Y-%m-%dT%H:%M:%SZ")},
                                2: {"name": "Kill a pirate", "expiry": (now + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")},
                                3: {"name": "Far off", "expiry": (now + timedelta(hours=9)).strftime("%Y-%m-%dT%H:%M:%SZ")}}
            text = lk.startup_summary(now)
            self.assertTrue(text.startswith("2 missions expire within three hours: Deliver tea, 50 minutes; Kill a pirate, 2.0 hours"))
            lk.notes = MemoryStore(Path(tmp) / "m.json")
            lk.notes.shop("Germanium")
            lk.notes.shop("Configurable Components")
            w.dispatch(Event("Docked", {"event": "Docked", "StationServices": ["dock", "materialtrader"],
                                        "StationEconomy_Localised": "Extraction"}, "journal"))
            self.assertEqual(said[-1], "There's a raw material trader here. On your list: Germanium.")


class ImageToolTests(unittest.TestCase):
    def test_screenshot_reaches_claude_as_an_image(self):
        b = Brain(SimpleNamespace(), "claude-haiku-5-5", client=object(),
                  extra_tools={"look_at_screen": ({"name": "look_at_screen"}, lambda: {"_image": "QUJD", "source": "x"})})
        out = b._run_tool(SimpleNamespace(name="look_at_screen", input={}, id="t1"))
        self.assertEqual(out["content"][1]["type"], "image")
        self.assertEqual(out["content"][1]["source"]["data"], "QUJD")
        self.assertNotIn("QUJD", out["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
