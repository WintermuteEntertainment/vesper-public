"""Changing settings from the dashboard: editing config.toml safely, what's allowed, and who may ask.

Everything runs on temp copies and a throwaway local port.
"""

import json
import sys
import tempfile
import tomllib
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from elite_companion import config as config_mod  # noqa: E402
from elite_companion.copilot import Copilot  # noqa: E402
from elite_companion.dashboard import may_edit, serve, trusted_request  # noqa: E402
from elite_companion.memory import MemoryStore  # noqa: E402
from elite_companion.settings import SettingError, Settings, device_choices  # noqa: E402
from elite_companion.watcher import Watcher  # noqa: E402

CONFIG = """# Vesper settings

[ptt]
device = "Throttle"
button = 5            # checked free in Elite's bindings

[eddn]
enabled = true               # share market screens with EDDN

[brain]
model = "claude-haiku-5-5"   # chosen for cost

[listen]
mic = "USB Headset"               # the default was a silent virtual microphone
"""
MICS = ["Headset Microphone (USB Headset)", "Microphone (Webcam)", "Line In (Sound Card)"]


class TempConfig(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "config.toml"
        self.path.write_text(CONFIG, encoding="utf-8", newline="")

    def backups(self):
        folder = self.path.parent / "config-backups"
        return list(folder.iterdir()) if folder.exists() else []


class WriteValuesTests(TempConfig):
    def test_changes_only_that_value_and_keeps_every_comment(self):
        self.assertTrue(config_mod.write_values({"listen.mic": "Microphone (Webcam)"}, self.path))
        self.assertEqual(self.path.read_text(encoding="utf-8"), CONFIG.replace(
            'mic = "USB Headset"               #', 'mic = "Microphone (Webcam)"               #'))
        self.assertEqual(len(self.backups()), 1)
        self.assertEqual(self.backups()[0].read_text(encoding="utf-8"), CONFIG)

    def test_number_keeps_its_comment(self):
        config_mod.write_values({"ptt.button": 7}, self.path)
        self.assertIn("button = 7            # checked free", self.path.read_text(encoding="utf-8"))

    def test_adds_to_a_section_or_a_new_one(self):
        config_mod.write_values({"brain.effort": "medium", "voice.rate": 3,
                                 "callouts.rules.under_attack.enabled": False}, self.path)
        got = tomllib.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(got["brain"], {"model": "claude-haiku-5-5", "effort": "medium"})
        self.assertEqual(got["voice"], {"rate": 3})
        self.assertFalse(got["callouts"]["rules"]["under_attack"]["enabled"])
        self.assertEqual(got["ptt"]["button"], 5)
        self.assertIn("# chosen for cost", self.path.read_text(encoding="utf-8"))

    def test_same_value_or_default_writes_nothing(self):
        self.assertFalse(config_mod.write_values({"listen.mic": "USB Headset", "voice.volume": 100}, self.path))
        self.assertEqual(self.path.read_text(encoding="utf-8"), CONFIG)
        self.assertEqual(self.backups(), [])

    def test_hash_inside_a_string_is_not_a_comment(self):
        self.path.write_text('[listen]\nmic = "Mic #2"  # mine\n', encoding="utf-8")
        config_mod.write_values({"listen.mic": "USB Headset"}, self.path)
        self.assertEqual(self.path.read_text(encoding="utf-8"), '[listen]\nmic = "USB Headset"  # mine\n')

    def test_windows_line_endings_stay(self):
        self.path.write_bytes(CONFIG.replace("\n", "\r\n").encode())
        config_mod.write_values({"ptt.button": 7, "brain.effort": "high"}, self.path)
        data = self.path.read_bytes()
        self.assertEqual(data.count(b"\n"), data.count(b"\r\n"))
        self.assertEqual(tomllib.loads(data.decode())["ptt"]["button"], 7)

    def test_layout_it_cant_edit_is_refused_and_left_alone(self):
        odd = '[other]\nbuttons = [\n  7,\n  9,\n]\n'
        self.path.write_text(odd, encoding="utf-8")
        with self.assertRaises(ValueError):
            config_mod.write_values({"other.buttons": [1, 2]}, self.path)
        self.assertEqual(self.path.read_text(encoding="utf-8"), odd)

    def test_no_file_yet(self):
        self.path.unlink()
        config_mod.write_values({"listen.mic": "USB Headset", "voice.rate": 3}, self.path)
        self.assertEqual(config_mod.load(self.path)["voice"]["rate"], 3)


class SettingsTests(TempConfig):
    def make(self, running=None, **kw):
        return Settings(running, self.path, devices=lambda key, refresh=False: MICS, **kw)

    def field(self, s, key):
        return next(f for g in s.state()["groups"] for f in g["fields"] if f["key"] == key)

    def test_every_setting_in_the_defaults_is_on_the_page(self):
        keys = set(self.make().fields)
        for section, values in config_mod.DEFAULTS.items():
            for name in values:
                if name != "rules" and section not in ("controls", "screen"):  # file-only: nicknames and paths
                    self.assertIn(f"{section}.{name}", keys)

    def test_live_change_reaches_the_running_copilot(self):
        running, applied = config_mod.load(self.path), []
        s = self.make(running, apply=lambda k, v: applied.append((k, v)))
        out = s.change("listen.mic", MICS[1])
        self.assertEqual(applied, [("listen.mic", MICS[1])])
        self.assertEqual(running["listen"]["mic"], MICS[1])
        self.assertEqual(config_mod.load(self.path)["listen"]["mic"], MICS[1])
        self.assertIn("in use now", out["message"])

    def test_restart_setting_saves_and_says_so(self):
        running, applied = config_mod.load(self.path), []
        s = self.make(running, apply=lambda k, v: applied.append(k))
        out = s.change("listen.whisper_model", "small.en")
        self.assertEqual(applied, [])
        self.assertIn("restart", out["message"])
        self.assertEqual(out["field"]["running"], "base.en")
        self.assertEqual(running["listen"]["whisper_model"], "base.en")

    def test_without_vesper_it_only_saves(self):
        out = self.make().change("voice.rate", 3)
        self.assertIn("when Vesper starts", out["message"])

    def test_bad_values_are_refused_and_nothing_is_saved(self):
        s = self.make()
        for key, value in (("voice.rate", 11), ("voice.rate", "fast"), ("voice.rate", True), ("voice.rate", 1.5),
                           ("brain.model", "gpt-4"), ("eddn.enabled", "yes"), ("ptt.key", "NotAKey"),
                           ("listen.mic", 5), ("listen.mic", "a\nb"),
                           ("nothing.here", 1)):
            with self.assertRaises(SettingError, msg=f"{key}={value!r}"):
                s.change(key, value)
        self.assertEqual(self.path.read_text(encoding="utf-8"), CONFIG)

    def test_settings_that_steer_data_or_programs_are_locked(self):
        s = self.make()
        for key, value in (("search.ssh_host", "x@y"), ("local.base_url", "http://192.0.2.1:1/v1"),
                           ("dashboard.host", "0.0.0.0"), ("dashboard.editable", True), ("dashboard.network_edit", True)):
            with self.assertRaises(SettingError, msg=key):
                s.change(key, value)
        self.assertEqual(self.path.read_text(encoding="utf-8"), CONFIG)

    def test_editable_off_makes_it_read_only(self):
        self.path.write_text(CONFIG + "\n[dashboard]\neditable = false\n", encoding="utf-8")
        s = self.make()
        self.assertFalse(s.state()["editable"])
        with self.assertRaises(SettingError):
            s.change("voice.rate", 3)

    def test_callout_rule_shows_its_own_default(self):
        s = self.make()
        self.assertIs(self.field(s, "callouts.rules.under_attack.enabled")["value"], True)
        self.assertIs(self.field(s, "callouts.rules.docking.enabled")["value"], False)
        s.change("callouts.rules.docking.enabled", True)
        self.assertIs(self.field(s, "callouts.rules.docking.enabled")["value"], True)

    def test_voice_command_mode_shows_as_the_current_mode(self):
        s = self.make(config_mod.load(self.path), live_value=lambda k: "off" if k == "callouts.mode" else None)
        f = self.field(s, "callouts.mode")
        self.assertEqual(f["value"], "off")
        self.assertNotIn("running", f)

    def test_device_dropdown_shows_the_full_name_for_a_partial_setting(self):
        got = device_choices("USB Headset", MICS, "Windows default")
        self.assertEqual(got[0], {"value": "", "label": "Windows default"})
        self.assertEqual(got[1], {"value": "USB Headset", "label": "Headset Microphone (USB Headset)"})
        self.assertEqual(got[2]["value"], MICS[1])
        missing = device_choices("C920", MICS, "Windows default")
        self.assertEqual(missing[-1], {"value": "C920", "label": "C920 (not found)"})
        self.assertEqual(len(device_choices("", MICS, "Windows default")), 4)


class ApplyToCopilotTests(TempConfig):
    """Every setting the page calls live has to really reach the part of Vesper that uses it."""

    def setUp(self):
        super().setUp()
        self.pilot = Copilot(config_mod.load(self.path), watcher=Watcher(self.path.parent), speak=False,
                             notes=MemoryStore(self.path.parent / "m.json"), log_dir=None)
        self.pilot.brain = SimpleNamespace(model="", effort="", hard_model="", hard_effort="", budget=0,
                                           max_turns=0, idle_reset_s=0)
        self.s = Settings(self.pilot.cfg, self.path, apply=self.pilot.apply_setting,
                          devices=lambda key, refresh=False: MICS, live_value=self.pilot._live_setting)

    def test_each_live_setting_changes_its_owner(self):
        p = self.pilot
        for key, value, read in (
                ("callouts.mode", "critical", lambda: p.callouts.mode),
                ("callouts.rules.docking.enabled", True,
                 lambda: next(r for r in p.callouts.rules if r.id == "docking").enabled),
                ("lookout.patterns", False, lambda: p.lookout.enabled["patterns"]),
                ("lookout.limpets", False, lambda: p.lookout.checklist["limpets"]),
                ("mining.prospector", "good", lambda: p.mining.style),
                ("eddn.enabled", False, lambda: p.markets.eddn),
                ("brain.model", "claude-sonnet-5-5", lambda: p.brain.model),
                ("brain.hard_effort", "high", lambda: p.brain.hard_effort),
                ("brain.daily_budget_usd", 2.5, lambda: p.brain.budget),
                ("brain.max_history_turns", 10, lambda: p.brain.max_turns),
                ("local.model", "some-local-model", lambda: p.cfg["local"]["model"])):
            self.s.change(key, value)
            self.assertEqual(read(), value, key)
        self.s.change("brain.idle_reset_minutes", 2)
        self.assertEqual(p.brain.idle_reset_s, 120)

    def test_no_live_setting_raises_when_applied(self):
        samples = {"bool": True, "int": 1, "float": 1, "text": "x", "device": ""}
        for f in self.s.fields.values():
            if f.live and not f.locked:
                self.pilot.apply_setting(f.key, f.choices[0] if f.kind == "choice" else samples[f.kind])

    def test_microphone_switches_the_recorder(self):
        calls = []
        self.pilot.recorder = SimpleNamespace(set_mic=lambda m: calls.append(m) or True)
        self.s.change("listen.mic", MICS[1])
        self.assertEqual(calls, [MICS[1]])

    def test_voice_change_reaches_the_speaker(self):
        calls, said = [], []
        self.pilot.speaker = SimpleNamespace(update=lambda *a: calls.append(a), say=lambda *a: said.append(a[0]))
        self.s.change("voice.rate", 3)
        self.assertEqual(calls, [("", 3, 100, "")])
        self.assertEqual(said, ["Voice settings changed."])


class WebTests(TempConfig):
    def setUp(self):
        super().setUp()
        settings = Settings(None, self.path, devices=lambda key, refresh=False: MICS)
        self.server = serve(None, "127.0.0.1", 0, settings)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/api/settings"

    def post(self, body, headers=None):
        req = urllib.request.Request(self.url, json.dumps(body).encode(), method="POST",
                                     headers=headers or {"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            return e.code, json.load(e)

    def test_read_and_change(self):
        with urllib.request.urlopen(self.url + "?refresh=1", timeout=5) as r:
            state = json.load(r)
        self.assertEqual(state["groups"][0]["title"], "Microphone")
        code, out = self.post({"key": "voice.rate", "value": 3})
        self.assertEqual(code, 200)
        self.assertEqual(out["field"]["value"], 3)
        self.assertEqual(config_mod.load(self.path)["voice"]["rate"], 3)

    def test_refused_requests_change_nothing(self):
        cases = [
            ({"key": "voice.rate", "value": 99}, None, 400),
            ({"key": "voice.rate"}, None, 400),
            (["voice.rate"], None, 400),
            ({"key": "voice.rate", "value": 3}, {"Content-Type": "text/plain"}, 400),  # a plain cross-site form post
            ({"key": "voice.rate", "value": 3}, {"Content-Type": "application/json", "Origin": "http://example.com"}, 403),
            ({"key": "voice.rate", "value": 3}, {"Content-Type": "application/json", "Host": "example.com"}, 403),
        ]
        for body, headers, want in cases:
            self.assertEqual(self.post(body, headers)[0], want, f"{body} {headers}")
        self.assertEqual(self.path.read_text(encoding="utf-8"), CONFIG)

    def test_who_may_edit(self):
        self.assertTrue(may_edit("127.0.0.1", False))
        self.assertTrue(may_edit("::1", False))
        self.assertFalse(may_edit("192.0.2.10", False))
        self.assertTrue(may_edit("192.0.2.10", True))

    def test_who_is_trusted(self):
        self.assertTrue(trusted_request("192.0.2.10:8770", None))
        self.assertTrue(trusted_request("192.0.2.10:8770", "http://192.0.2.10:8770"))
        self.assertTrue(trusted_request("localhost:8770", "http://localhost:8770"))
        self.assertFalse(trusted_request("192.0.2.10:8770", "http://example.com"))
        self.assertFalse(trusted_request("example.com:8770", "http://example.com:8770"))
        self.assertFalse(trusted_request(None, None))


if __name__ == "__main__":
    unittest.main()
