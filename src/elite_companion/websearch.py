"""Free web search through your own SearXNG instance, plus a simple page reader.

Used when [brain] web_search = "searxng". SearXNG must allow JSON output (search.formats includes json in its
settings.yml). If it only listens on another machine, set [search] ssh_host and the search runs there as
`curl` over SSH (key login). Pages are fetched from this PC. Nothing here is paid; only the tokens Claude
reads cost.
"""

from __future__ import annotations

import html
import json
import re
import subprocess
import urllib.parse
import urllib.request
from html.parser import HTMLParser

USER_AGENT = "Mozilla/5.0 (Vesper co-pilot)"


class SearchError(RuntimeError):
    """The search or page fetch failed; the message is safe to speak."""
    pass


def search(query: str, cfg: dict, max_results: int = 6) -> list[dict]:
    """Search with the commander's SearXNG ([search] searxng_url, JSON format enabled).
    With [search] ssh_host set, the request runs on that host through ssh and curl, for a
    SearXNG that only listens locally there. Returns title, url and snippet per result.
    """
    q = urllib.parse.quote_plus(query.strip())
    url = f"{cfg['searxng_url'].rstrip('/')}/search?q={q}&format=json"
    try:
        if cfg.get("ssh_host"):
            out = subprocess.run(
                ["ssh", "-o", "ConnectTimeout=6", "-o", "BatchMode=yes", cfg["ssh_host"], f'curl -s -m 15 "{url}"'],
                capture_output=True, text=True, timeout=30, encoding="utf-8", errors="replace")
            if out.returncode != 0 or not out.stdout.strip():
                raise SearchError(f"The search over SSH failed ({out.stderr.strip()[:120] or 'no answer'}).")
            data = json.loads(out.stdout)
        else:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": USER_AGENT}), timeout=20) as r:
                data = json.load(r)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as e:
        raise SearchError(f"The web search isn't answering ({e.__class__.__name__}).") from e
    rows, seen = [], set()
    for r in data.get("results", []):
        if r.get("url") in seen:
            continue
        seen.add(r.get("url"))
        rows.append({"title": (r.get("title") or "")[:150], "url": r.get("url"),
                     "snippet": html.unescape(r.get("content") or "")[:400],
                     "date": (r.get("publishedDate") or "")[:10] or None})
        if len(rows) >= max_results:
            break
    return rows


class _Text(HTMLParser):
    """Collects a page's readable text, skipping scripts, styles, navigation and forms."""
    SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "header", "form"}

    def __init__(self):
        super().__init__()
        self.parts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag in ("p", "br", "li", "tr", "h1", "h2", "h3", "h4", "div"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def fetch_page(url: str, max_chars: int = 6000) -> dict:
    """A web page as plain text, cut to max_chars."""
    if not re.match(r"^https?://", url or ""):
        raise SearchError("Only web pages (http or https) can be read.")
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": USER_AGENT}), timeout=20) as r:
            raw = r.read(2_000_000).decode(r.headers.get_content_charset() or "utf-8", errors="replace")
    except OSError as e:
        raise SearchError(f"Couldn't open that page ({e}).") from e
    p = _Text()
    p.feed(raw)
    text = re.sub(r"\n\s*\n+", "\n", re.sub(r"[ \t\r\f\v]+", " ", "".join(p.parts))).strip()
    return {"url": url, "text": text[:max_chars], "truncated": len(text) > max_chars}


WEB_SEARCH_SPEC = {
    "name": "web_search",
    "description": "Search the web (free, through the commander's own server) for game facts your other tools don't cover: "
                   "unlock requirements, game rules, patch notes, events, guides. Returns titles, links and "
                   "snippets; use fetch_page to read one if the snippets aren't enough.",
    "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
}
FETCH_PAGE_SPEC = {
    "name": "fetch_page",
    "description": "Read the text of a web page (from web_search results).",
    "input_schema": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
}
