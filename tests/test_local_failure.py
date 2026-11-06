"""What Vesper says when the local model fails, against real sockets on this PC (no model involved)."""

import json
import socket
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from elite_companion.brain import Brain  # noqa: E402
from elite_companion.memory import MemoryStore  # noqa: E402
from elite_companion.tools import GameTools  # noqa: E402
from elite_companion.watcher import Watcher  # noqa: E402


class LocalFailureTests(unittest.TestCase):
    def ask(self, port, timeout=1):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            brain = Brain(GameTools(Watcher(d), MemoryStore(d / "m.json")), client=object(), usage_log=d / "u.jsonl",
                          local={"base_url": f"http://127.0.0.1:{port}/v1", "model": "scout",
                                 "timeout_seconds": timeout, "start_local": True})
            return brain._ask_local("Can we test the web search?")

    def test_too_slow_says_so(self):
        with socket.socket() as s:  # accepts the connection and never answers, like a model still working
            s.bind(("127.0.0.1", 0))
            s.listen()
            said = self.ask(s.getsockname()[1])
        self.assertIn("didn't answer within 1 seconds", said)
        self.assertIn("Settings page", said)
        self.assertNotIn("can't reach", said)

    def test_nothing_listening_is_cant_reach(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        self.assertIn("I can't reach the local model", self.ask(port))

    def test_server_error_gives_the_status(self):
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.send_error(503)

            def log_message(self, *args):
                pass
        server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.assertIn("status 503", self.ask(server.server_address[1], timeout=5))

    def test_system_message_names_the_configured_model(self):
        sent = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                sent.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                self.send_error(503)

            def log_message(self, *args):
                pass
        server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.ask(server.server_address[1], timeout=5)
        system = sent[0]["messages"][0]["content"]
        self.assertIn("YOU ARE THE LOCAL MODEL (scout, running on the commander's own hardware)", system)
        self.assertNotIn("qwen3-coder-30b", system)


class LocalConversationTests(unittest.TestCase):
    """Against a real local socket that plays back model replies, one per request."""

    def start(self, replies):
        sent = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                sent.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                body = json.dumps({"choices": [{"message": replies.pop(0)}]}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass
        server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = Path(tmp.name)
        brain = Brain(GameTools(Watcher(d), MemoryStore(d / "m.json")), client=object(), usage_log=d / "u.jsonl",
                      local={"base_url": f"http://127.0.0.1:{server.server_address[1]}/v1", "model": "scout",
                             "timeout_seconds": 5, "start_local": True})
        return brain, sent

    def test_live_line_goes_just_before_the_question(self):
        brain, sent = self.start([{"content": "One."}, {"content": "Two."}])
        brain._ask_local("First?")
        brain._ask_local("Second?")
        roles = [m["role"] for m in sent[1]["messages"]]
        self.assertEqual(roles, ["system", "user", "assistant", "system", "user"])
        self.assertNotIn("No live game data", sent[1]["messages"][0]["content"])
        self.assertIn("No live game data", sent[1]["messages"][3]["content"])
        self.assertEqual(sent[1]["messages"][4]["content"], "Second?")

    def test_claim_without_a_tool_is_checked_once(self):
        brain, sent = self.start([{"content": "Got it. I'll remember that you prefer large landing pads."},
                                  {"content": "I didn't save that."}])
        self.assertEqual(brain._ask_local("Remember that I prefer large landing pads."), "I didn't save that.")
        self.assertEqual(len(sent), 2)
        self.assertIn("you called no tool that does it", sent[1]["messages"][-1]["content"])

    def test_repeated_claim_is_not_passed_on(self):
        claim = "Got it. I'll remember that you prefer large landing pads."
        brain, sent = self.start([{"content": claim}, {"content": claim}])
        self.assertIn("didn't actually do that", brain._ask_local("Remember that I prefer large landing pads."))
        self.assertEqual(len(sent), 2)

    def test_claim_after_a_real_tool_call_is_left_alone(self):
        call = {"id": "1", "type": "function", "function": {"name": "remember", "arguments": '{"text": "large pads"}'}}
        brain, sent = self.start([{"content": "", "tool_calls": [call]}, {"content": "I saved that."}])
        self.assertEqual(brain._ask_local("Remember that I prefer large landing pads."), "I saved that.")
        self.assertEqual(len(sent), 2)

    def test_claim_after_only_a_read_tool_is_still_checked(self):
        call = {"id": "1", "type": "function", "function": {"name": "recall_notes", "arguments": "{}"}}
        brain, sent = self.start([{"content": "", "tool_calls": [call]}, {"content": "I've copied the system name."},
                                  {"content": "I didn't copy it."}])
        self.assertEqual(brain._ask_local("Plot me a route to Sol."), "I didn't copy it.")
        self.assertEqual(len(sent), 3)

    def test_claim_without_saying_i_is_still_checked(self):
        call = {"id": "1", "type": "function", "function": {"name": "recall_notes", "arguments": "{}"}}
        brain, sent = self.start([{"content": "", "tool_calls": [call]},
                                  {"content": "The route is plotted and copied to your clipboard."},
                                  {"content": "The route is plotted."}])
        self.assertEqual(brain._ask_local("Plot me a route to Sol."), "The route is plotted.")
        self.assertEqual(len(sent), 3)

    def test_ordinary_answer_is_not_mistaken_for_a_claim(self):
        brain, sent = self.start([{"content": "You have 16.2 tons of fuel."}])
        self.assertEqual(brain._ask_local("How much fuel do I have?"), "You have 16.2 tons of fuel.")
        self.assertEqual(len(sent), 1)

    def test_warm_up_reads_the_instructions_and_tools(self):
        brain, sent = self.start([{"content": "OK"}])
        self.assertTrue(brain.warm_local())
        self.assertIn("tools", sent[0])
        self.assertIn("YOU ARE THE LOCAL MODEL", sent[0]["messages"][0]["content"])


if __name__ == "__main__":
    unittest.main()
