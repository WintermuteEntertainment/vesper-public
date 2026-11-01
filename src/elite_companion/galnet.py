"""GalNet news, from Frontier's own feed."""

from __future__ import annotations

import html
import json
import re
import urllib.error
import urllib.request

FEED = "https://cms.zaonce.net/en-GB/jsonapi/node/galnet_article?sort=-published_at&page%5Blimit%5D={n}"


class GalnetError(RuntimeError):
    """Frontier's GalNet feed couldn't be reached."""
    pass


def latest(count: int = 3, chars: int = 600) -> list[dict]:
    """The newest GalNet articles: title, date and the text with HTML stripped, cut to `chars`."""
    req = urllib.request.Request(FEED.format(n=count), headers={"User-Agent": "vesper/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.load(resp)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        raise GalnetError(f"Couldn't reach GalNet ({e}).") from e
    articles = []
    for a in data.get("data", []):
        attrs = a.get("attributes", {})
        text = re.sub(r"<[^>]+>", " ", (attrs.get("body") or {}).get("value", ""))
        text = re.sub(r"\s+", " ", html.unescape(text)).strip()
        articles.append({"title": attrs.get("title"), "date": attrs.get("field_galnet_date"),
                         "text": text[:chars] + ("…" if len(text) > chars else "")})
    return articles
