"""Callout rules and the Claude brain, with fakes (no audio, no network)."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from elite_companion.brain import Brain, BudgetExceeded, request_cost  # noqa: E402
from elite_companion.callouts import Callouts  # noqa: E402
from elite_companion.speech import CRITICAL  # noqa: E402
from elite_companion.tools import GameTools  # noqa: E402
from elite_companion.watcher import Event, Watcher  # noqa: E402


def ev(name, data, ts="2026-10-25T01:00:00Z", **kw):
    return Event(name, {"timestamp": ts, **data}, kw.pop("source", "journal"), **kw)


class CalloutTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.watcher = Watcher(self._tmp.name)
        self.spoken = []
        self.callouts = Callouts(self.watcher, lambda text, pri: self.spoken.append((text, pri)))

    def tearDown(self):
        self._tmp.cleanup()

    def test_hostile_hail_is_critical_with_per_sender_cooldown(self):
        hail = {"Channel": "npc", "Message": "$Pirate_HunterHostileSC_Relevant04;", "From": "$npc_name_decorate:#name=Mike Dailly;",
                "From_Localised": "Mike Dailly"}
        self.callouts.handle(ev("ReceiveText", hail, "2026-10-25T01:00:00Z"))
        self.callouts.handle(ev("ReceiveText", hail, "2026-10-25T01:00:30Z"))  # within 90 s
        self.callouts.handle(ev("ReceiveText", hail, "2026-10-25T01:02:00Z"))
        self.assertEqual(self.spoken, [("Warning. Hostile hail from Mike Dailly.", CRITICAL)] * 2)

    def test_harmless_chatter_is_silent(self):
        self.callouts.handle(ev("ReceiveText", {"Channel": "npc", "Message": "$Commuter_AuthorityScan08;", "From_Localised": "Jim"}))
        self.callouts.handle(ev("ReceiveText", {"Channel": "npc", "Message": "$STATION_NoFireZone_exited;"}))
        self.callouts.handle(ev("Music", {"MusicTrack": "Combat_Dogfight"}))
        self.assertEqual(self.spoken, [])

    def test_interdiction_flag_and_hull(self):
        self.callouts.handle(ev("StatusFlag", {"flag": "BeingInterdicted", "on": True}, source="derived"))
        self.callouts.handle(ev("HullDamage", {"Health": 0.39, "PlayerPilot": True, "Fighter": False}))
        self.assertEqual([t for t, _ in self.spoken], ["Interdiction!", "Hull 39 percent."])

    def test_replay_and_modes(self):
        self.callouts.handle(ev("UnderAttack", {"Target": "You"}, replay=True))
        self.assertEqual(self.spoken, [])
        self.callouts.set_mode("critical")
        self.callouts.handle(ev("UnderAttack", {"Target": "You"}))  # normal priority: skipped
        self.callouts.handle(ev("StatusFlag", {"flag": "LowFuel", "on": True}, source="derived"))
        self.assertEqual(self.spoken, [("Fuel low.", CRITICAL)])

    def test_config_overrides(self):
        spoken = []
        c = Callouts(self.watcher, lambda t, p: spoken.append(t),
                     {"rules": {"under_attack": {"enabled": False}, "docking": {"enabled": True}}})
        c.handle(ev("UnderAttack", {"Target": "You"}))
        c.handle(ev("DockingGranted", {"StationName": "Jameson Memorial"}))
        self.assertEqual(spoken, ["Docking granted."])

    def test_shields_down_ignored_while_docked(self):
        self.watcher.state.location.docked = True
        self.callouts.handle(ev("ShieldState", {"ShieldsUp": False}))
        self.assertEqual(self.spoken, [])


def block(**kw):
    return SimpleNamespace(**kw)


class FakeClient:
    """Returns scripted responses; records requests."""

    def __init__(self, responses):
        self.responses, self.requests = list(responses), []
        self.messages = SimpleNamespace(create=self._create)
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})  # snapshot; the list keeps growing
        return self.responses.pop(0)


def usage(i=1000, o=50):
    return SimpleNamespace(input_tokens=i, output_tokens=o, cache_creation_input_tokens=0, cache_read_input_tokens=0)


class BrainTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        d = Path(self._tmp.name)
        (d / "Journal.2026-10-24T200000.01.log").write_text(json.dumps(
            {"timestamp": "2026-10-24T20:00:00Z", "event": "LoadGame", "Commander": "Testbot", "Ship": "type7", "ShipID": 1}) + "\n")
        w = Watcher(d)
        w.catch_up()
        self.tools = GameTools(w)
        self.log = d / "usage.jsonl"

    def tearDown(self):
        self._tmp.cleanup()

    def test_tool_loop_and_usage_log(self):
        client = FakeClient([
            SimpleNamespace(stop_reason="tool_use", usage=usage(),
                            content=[block(type="tool_use", id="t1", name="get_session_summary", input={})]),
            SimpleNamespace(stop_reason="end_turn", usage=usage(), content=[block(type="text", text="You're Testbot.")]),
        ])
        brain = Brain(self.tools, client=client, usage_log=self.log)
        self.assertEqual(brain.ask("who am I?"), "You're Testbot.")
        result = client.requests[1]["messages"][-1]["content"][0]
        self.assertEqual(result["tool_use_id"], "t1")
        self.assertIn("Testbot", result["content"])
        self.assertEqual(client.requests[0]["betas"], ["server-side-fallback-2026-07-01"])
        self.assertEqual(len(self.log.read_text().splitlines()), 2)

    def test_budget_stops_questions(self):
        client = FakeClient([SimpleNamespace(stop_reason="end_turn", usage=usage(200_000, 0),
                                             content=[block(type="text", text="ok")])])
        brain = Brain(self.tools, client=client, usage_log=self.log, daily_budget_usd=0.5)
        brain.ask("one")  # 200k input tokens on Opus 5.5 = $0.80
        with self.assertRaises(BudgetExceeded):
            brain.ask("two")

    def test_cost(self):
        self.assertAlmostEqual(request_cost("claude-opus-5-5", usage(1_000_000, 100_000)), 6.0)
        self.assertAlmostEqual(request_cost("claude-haiku-5-5", usage(1_000_000, 0)), 0.10)


if __name__ == "__main__":
    unittest.main()
