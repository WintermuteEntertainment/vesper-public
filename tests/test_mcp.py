"""The MCP server is built from the same tool registry as the voice copilot."""

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from elite_companion.tools import TOOL_SPECS, TOOLS, GameDataError, GameTools, call_tool  # noqa: E402
from elite_companion.watcher import Watcher  # noqa: E402

try:
    from elite_companion import mcp_server
except ImportError:  # the mcp extra isn't installed
    mcp_server = None


class RegistryTests(unittest.TestCase):
    def test_every_spec_is_registered_once(self):
        names = [s["name"] for s in TOOL_SPECS]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(set(names), set(TOOLS))

    def test_every_registered_method_exists(self):
        for name, (method, kind) in TOOLS.items():
            self.assertTrue(callable(getattr(GameTools, method, None)), name)
            self.assertIn(kind, (None, "local", "web", "write", "delete"), name)

    def test_bad_arguments_are_a_game_data_error(self):
        with tempfile.TemporaryDirectory() as d:
            tools = GameTools(Watcher(Path(d)))
            with self.assertRaisesRegex(GameDataError, "Bad arguments for get_status"):
                call_tool(tools, "get_status", {"nonsense": 1})


@unittest.skipIf(mcp_server is None, "mcp not installed")
class McpTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tools = GameTools(Watcher(Path(self._tmp.name)))  # an empty journal folder
        self.listed = mcp_server.mcp_tools()
        self.offered = {t.name for t in self.listed}

    def tearDown(self):
        self._tmp.cleanup()

    def call(self, name, args=None):
        return asyncio.run(mcp_server.call(self.tools, self.offered, name, args))

    def test_tools_match_the_registry(self):
        want = {"elite_" + n for n, (_, kind) in TOOLS.items() if kind}
        self.assertEqual(self.offered, want)
        self.assertNotIn("elite_copy_to_clipboard", self.offered)
        spec = next(s for s in TOOL_SPECS if s["name"] == "get_status")
        tool = next(t for t in self.listed if t.name == "elite_get_status")
        self.assertEqual(tool.description, spec["description"])
        self.assertTrue(tool.annotations.read_only_hint)

    def test_write_tools_are_not_read_only(self):
        hints = {t.name: t.annotations for t in self.listed}
        self.assertFalse(hints["elite_remember"].read_only_hint)
        self.assertTrue(hints["elite_forget_note"].destructive_hint)
        self.assertTrue(hints["elite_plot_route"].open_world_hint)

    def test_errors_come_back_as_tool_errors(self):
        r = self.call("elite_launch_missiles")
        self.assertTrue(r.is_error)
        self.assertIn("Unknown tool", r.content[0].text)
        r = self.call("elite_get_status")  # no journals yet
        self.assertTrue(r.is_error)
        self.assertIn("No game data yet", r.content[0].text)
        r = self.call("elite_get_status", {"nonsense": 1})
        self.assertTrue(r.is_error)
        self.assertIn("Bad arguments", r.content[0].text)

    def test_server_builds(self):
        server = mcp_server.create_server(Watcher(Path(self._tmp.name)))
        self.assertEqual(server.name, "vesper")


if __name__ == "__main__":
    unittest.main()
