import json
import unittest

from market_data.klines import KlineDataManager, eligible_usdt_perpetuals
from market_data.websocket import KlineWebSocket
from market_data.universe import CoinMarketCapUniverse


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return FakeResponse(self.payload)


class UniverseProviderTests(unittest.TestCase):
    def test_requires_api_key(self):
        with self.assertRaises(ValueError):
            CoinMarketCapUniverse(api_key="")

    def test_returns_unique_ranked_symbols_and_sends_key_header(self):
        session = FakeSession({"data": [
            {"cmc_rank": 1, "symbol": "BTC"},
            {"cmc_rank": 2, "symbol": "ETH"},
            {"cmc_rank": 3, "symbol": "BTC"},
            {"cmc_rank": 4, "symbol": ""},
            {"cmc_rank": 5, "name": "No symbol"},
        ]})
        provider = CoinMarketCapUniverse(api_key="test-key", session=session)
        self.assertEqual(provider.top_base_symbols(5), ["BTC", "ETH"])
        _url, kwargs = session.calls[0]
        self.assertEqual(kwargs["headers"]["X-CMC_PRO_API_KEY"], "test-key")
        self.assertEqual(kwargs["params"]["limit"], 5)
        self.assertEqual(kwargs["params"]["start"], 1)

    def test_ranked_list_starts_at_51_and_excludes_any_top_50_rows(self):
        session = FakeSession({"data": [
            {"cmc_rank": 50, "symbol": "BTC"},
            {"cmc_rank": 51, "symbol": "ALT"},
            {"cmc_rank": 52, "symbol": "ALT2"},
        ]})
        provider = CoinMarketCapUniverse(api_key="test-key", session=session)
        self.assertEqual(provider.ranked_base_symbols(start_rank=51, limit=100), ["ALT", "ALT2"])
        _url, kwargs = session.calls[0]
        self.assertEqual(kwargs["params"]["start"], 51)
        self.assertEqual(kwargs["params"]["limit"], 100)

    def test_rejects_bad_response(self):
        provider = CoinMarketCapUniverse(
            api_key="test-key", session=FakeSession({"status": {}})
        )
        with self.assertRaises(ValueError):
            provider.ranked_base_symbols()

    def test_rejects_rank_range_beyond_supported_listings(self):
        provider = CoinMarketCapUniverse(api_key="test-key", session=FakeSession({"data": []}))
        with self.assertRaises(ValueError):
            provider.ranked_base_symbols(start_rank=5000, limit=2)

    def test_intersects_ranked_assets_with_active_usdt_perpetuals(self):
        ranked = ["ALT", "ALT2", "NOTLISTED"]
        exchange = {"symbols": [
            {"symbol": "BTCUSDT", "baseAsset": "BTC", "quoteAsset": "USDT",
             "contractType": "PERPETUAL", "status": "TRADING"},
            {"symbol": "ALTUSDT", "baseAsset": "ALT", "quoteAsset": "USDT",
             "contractType": "PERPETUAL", "status": "TRADING"},
            {"symbol": "ALT2USDT", "baseAsset": "ALT2", "quoteAsset": "USDT",
             "contractType": "PERPETUAL", "status": "BREAK"},
            {"symbol": "ALT2BUSD", "baseAsset": "ALT2", "quoteAsset": "BUSD",
             "contractType": "PERPETUAL", "status": "TRADING"},
        ]}
        self.assertEqual(eligible_usdt_perpetuals(ranked, exchange, limit=10), ["ALTUSDT"])


class WebSocketClientTests(unittest.TestCase):
    def packet(self, closed=True):
        return {"stream": "btcusdt@kline_15m", "data": {"k": {
            "s": "BTCUSDT", "i": "15m", "t": 0, "T": 899999,
            "o": "100", "h": "105", "l": "95", "c": "102",
            "v": "10", "q": "1020", "n": 12, "x": closed,
        }}}

    def test_stream_url_contains_selected_streams(self):
        client = KlineWebSocket(
            KlineDataManager(), ["BTCUSDT", "ETHUSDT"], ["15m", "1h"]
        )
        self.assertIn("btcusdt@kline_15m", client.url)
        self.assertIn("ethusdt@kline_1h", client.url)

    def test_only_new_closed_candles_reach_callback(self):
        manager = KlineDataManager()
        received = []
        client = KlineWebSocket(manager, ["BTCUSDT"], ["15m"], received.append)
        client._on_message(None, json.dumps(self.packet(closed=False)))
        client._on_message(None, json.dumps(self.packet(closed=True)))
        client._on_message(None, json.dumps(self.packet(closed=True)))
        self.assertEqual(len(received), 1)
        self.assertEqual(len(manager.get("BTCUSDT", "15m")), 1)

    def test_rejects_more_than_binance_combined_stream_limit(self):
        # 342 symbols x 3 intervals would exceed 1024 streams.
        symbols = [f"COIN{i}USDT" for i in range(342)]
        with self.assertRaises(ValueError):
            KlineWebSocket(KlineDataManager(), symbols)


if __name__ == "__main__":
    unittest.main()
