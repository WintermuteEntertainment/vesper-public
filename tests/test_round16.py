"""Free web search through a SearXNG on another machine (no network: ssh and urlopen are mocked)."""

import io
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from elite_companion import websearch  # noqa: E402
from elite_companion.brain import Brain  # noqa: E402

CFG = {"searxng_url": "http://localhost:8888", "ssh_host": "me@searchbox"}


class SearchTests(unittest.TestCase):
    def test_search_over_ssh(self):
        body = {"results": [{"title": "The Sarge", "url": "https://a", "content": "Discovery &amp; unlock"},
                            {"title": "dup", "url": "https://a", "content": ""},
                            {"title": "Guide", "url": "https://b", "content": "x", "publishedDate": "2021-03-28T00:00"}]}
        done = SimpleNamespace(returncode=0, stdout=json.dumps(body), stderr="")
        with mock.patch("subprocess.run", return_value=done) as run:
            rows = websearch.search("The Sarge unlock?", CFG)
        cmd = run.call_args[0][0]
        self.assertEqual(cmd[:1] + cmd[-2:-1], ["ssh", "me@searchbox"])
        self.assertIn("q=The+Sarge+unlock%3F", cmd[-1])  # quoted for the remote shell
        self.assertEqual([r["url"] for r in rows], ["https://a", "https://b"])
        self.assertEqual(rows[0]["snippet"], "Discovery & unlock")
        self.assertEqual(rows[1]["date"], "2021-03-28")

    def test_search_failure_is_an_error_not_a_crash(self):
        with mock.patch("subprocess.run", return_value=SimpleNamespace(returncode=255, stdout="", stderr="timed out")):
            with self.assertRaises(websearch.SearchError):
                websearch.search("x", CFG)

    def test_fetch_page_text(self):
        page = b"<html><head><script>bad()</script></head><body><nav>menu</nav><h1>Sarge</h1><p>Donate 50 items.</p></body></html>"
        resp = mock.MagicMock()
        resp.__enter__.return_value = resp
        resp.read.return_value = page
        resp.headers.get_content_charset.return_value = "utf-8"
        with mock.patch("urllib.request.urlopen", return_value=resp):
            out = websearch.fetch_page("https://example.org/sarge")
        self.assertEqual(out["text"], "Sarge\nDonate 50 items.")
        with self.assertRaises(websearch.SearchError):
            websearch.fetch_page("file:///c:/secret")

    def test_brain_uses_free_tools_not_the_paid_one(self):
        b = Brain(SimpleNamespace(), "claude-haiku-5-5", client=object(), web_search="searxng", search=CFG)
        specs = b._tool_specs("claude-haiku-5-5")
        self.assertIn("web_search", [s.get("name") for s in specs])
        self.assertFalse(any(s.get("type", "").startswith("web_search_") for s in specs))
        paid = Brain(SimpleNamespace(), "claude-haiku-5-5", client=object(), web_search=True)
        self.assertTrue(any(s.get("type", "").startswith("web_search_") for s in paid._tool_specs("claude-haiku-5-5")))


if __name__ == "__main__":
    unittest.main()
