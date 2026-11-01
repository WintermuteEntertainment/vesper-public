"""Spansh client tests with canned responses (no network)."""

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from elite_companion import spansh  # noqa: E402


def station(name, dist, stock=None, kind="Coriolis Starport"):
    r = {"name": name, "system_name": "Sys", "distance": dist, "distance_to_arrival": 100, "type": kind,
         "has_large_pad": True, "market_updated_at": "2026-10-24 12:00:00+00"}
    if stock is not None:
        r["market"] = [{"commodity": "Evacuation Shelter", "supply": stock, "buy_price": 200}]
    return r


class SpanshTests(unittest.TestCase):
    def test_commodity_filters_by_stock_and_skips_carriers(self):
        page = {"results": [station("Empty", 1, 0), station("Carrier", 2, 999, "Drake-Class Carrier"),
                            station("Good", 3, 500), station("Small", 4, 50)]}
        with mock.patch.object(spansh, "_post", return_value=page) as post:
            rows = spansh.find_commodity("Evacuation Shelter", "Sol", min_stock=196, large_pad=True)
        self.assertEqual([r["station"] for r in rows], ["Good"])
        self.assertEqual(rows[0]["stock"], 500)
        body = post.call_args[0][0]
        self.assertEqual(body["filters"]["has_large_pad"], {"value": True})
        self.assertEqual(body["reference_system"], "Sol")

    def test_service_alias(self):
        self.assertEqual(spansh.service_name("Interstellar factors"), "Interstellar Factors Contact")
        self.assertEqual(spansh.service_name("Shipyard"), "Shipyard")

    def test_large_pad_ships(self):
        self.assertTrue(spansh.needs_large_pad("type7"))
        self.assertTrue(spansh.needs_large_pad("Anaconda"))
        self.assertFalse(spansh.needs_large_pad("python"))
        self.assertFalse(spansh.needs_large_pad(None))

    def test_retries_server_errors(self):
        import io
        import urllib.error
        err = urllib.error.HTTPError(spansh.API, 500, "boom", {}, io.BytesIO())
        ok = mock.MagicMock()
        ok.__enter__.return_value = io.BytesIO(b'{"results": []}')
        with mock.patch("urllib.request.urlopen", side_effect=[err, ok]), mock.patch("time.sleep"):
            self.assertEqual(spansh._post({}), {"results": []})


if __name__ == "__main__":
    unittest.main()
