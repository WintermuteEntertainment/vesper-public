"""Push-to-talk keys: friendly names, Elite's .binds spelling, and the binding conflict check (no devices)."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from elite_companion import bindings, ptt  # noqa: E402

BINDS = """<Root PresetName="Custom" MajorVersion="4" MinorVersion="2">
<UseBoostJuice><Primary Device="Keyboard" Key="Key_Tab" /><Secondary Device="{NoDevice}" Key="" /></UseBoostJuice>
<DeployHeatSink><Primary Device="Mouse" Key="Mouse_4" /><Secondary Device="231D0200" Key="Joy_3" /></DeployHeatSink>
<CamZoomIn><Primary Device="Keyboard" Key="Key_RightControl" /></CamZoomIn>
</Root>"""


class KeyNameTests(unittest.TestCase):
    def test_names_are_forgiving(self):
        self.assertEqual(ptt.key_code("F9"), 0x78)
        self.assertEqual(ptt.key_code("right control"), ptt.key_code("Right Ctrl"))
        self.assertEqual(ptt.key_code("mouse 4"), 0x05)
        self.assertIsNone(ptt.key_code("Hyper"))

    def test_elite_spelling(self):
        self.assertEqual(ptt.elite_key("F9"), ("Keyboard", "Key_F9"))
        self.assertEqual(ptt.elite_key("Right Ctrl"), ("Keyboard", "Key_RightControl"))
        self.assertEqual(ptt.elite_key("Numpad 5"), ("Keyboard", "Key_Numpad_5"))
        self.assertEqual(ptt.elite_key("Mouse 4"), ("Mouse", "Mouse_4"))

    def test_unknown_key_is_refused(self):
        with self.assertRaises(ValueError):
            ptt.KeyPushToTalk("Hyper", lambda: None, lambda: None)


class ConflictTests(unittest.TestCase):
    def test_bound_keys_and_sticks(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "Custom.4.2.binds").write_text(BINDS)
            self.assertEqual(bindings.key_used("Tab", d), ["UseBoostJuice"])
            self.assertEqual(bindings.key_used("Mouse 4", d), ["DeployHeatSink"])
            self.assertEqual(bindings.key_used("Right Ctrl", d), ["CamZoomIn"])
            self.assertEqual(bindings.key_used("F9", d), [])
            # A stick Elite doesn't know by name is written as its USB id; a known one by its Elite name.
            with mock.patch.object(bindings, "device_mappings", return_value={"044FB10A": "T16000M"}):
                self.assertEqual(bindings.joystick_button_used("231D0200", 3, d), ["DeployHeatSink"])
                self.assertEqual(bindings.joystick_button_used("044FB10A", 3, d), [])


if __name__ == "__main__":
    unittest.main()
