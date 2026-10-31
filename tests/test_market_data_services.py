import json
import unittest

from market_data.klines import KlineDataManager, ranked_usdt_perpetuals_by_quote_volume
from market_data.websocket import KlineWebSocket, MarkPriceWebSocket


class UniverseProviderTests(unittest.TestCase):
    def test_excludes_top_50_by_volume_and_selects_next_symbols(self):
        exchange = {"symbols": [
            {"symbol": f"COIN{i}USDT", "quoteAsset": "USDT",
             "contractType": "PERPETUAL", "status": "TRADING"}
            for i in range(1, 56)
        ] + [
            {"symbol": "BTCUSDT", "quoteAsset": "USDT",
             "contractType": "PERPETUAL", "status": "TRADING"}
        ]}
        tickers = [
            {"symbol": f"COIN{i}USDT", "quoteVolume": str(1000 - i)}
            for i in range(1, 56)
        ] + [{"symbol": "BTCUSDT", "quoteVolume": "2000"}]
        self.assertEqual(
            ranked_usdt_perpetuals_by_quote_volume(exchange, tickers, 50, 10),
            ["COIN51USDT", "COIN52USDT", "COIN53USDT", "COIN54USDT", "COIN55USDT"],
        )

    def test_only_active_usdt_perpetuals_are_ranked(self):
        exchange = {"symbols": [
            {"symbol": "AUSDT", "quoteAsset": "USDT", "contractType": "PERPETUAL", "status": "TRADING"},
            {"symbol": "BBUSD", "quoteAsset": "BUSD", "contractType": "PERPETUAL", "status": "TRADING"},
            {"symbol": "CUSDT", "quoteAsset": "USDT", "contractType": "CURRENT_QUARTER", "status": "TRADING"},
            {"symbol": "DUSDT", "quoteAsset": "USDT", "contractType": "PERPETUAL", "status": "BREAK"},
        ]}
        tickers = [
            {"symbol": "AUSDT", "quoteVolume": "100"},
            {"symbol": "BBUSD", "quoteVolume": "1000"},
            {"symbol": "CUSDT", "quoteVolume": "900"},
            {"symbol": "DUSDT", "quoteVolume": "800"},
        ]
        self.assertEqual(
            ranked_usdt_perpetuals_by_quote_volume(exchange, tickers, exclude_top=0),
            ["AUSDT"],
        )

    def test_ignores_non_finite_quote_volume(self):
        exchange = {"symbols": [
            {"symbol": "AUSDT", "quoteAsset": "USDT", "contractType": "PERPETUAL", "status": "TRADING"}
        ]}
        self.assertEqual(
            ranked_usdt_perpetuals_by_quote_volume(
                exchange, [{"symbol": "AUSDT", "quoteVolume": "NaN"}], exclude_top=0
            ),
            [],
        )


class WebSocketClientTests(unittest.TestCase):
    def packet(self, closed=True):
        return {"stream": "btcusdt@kline_15m", "data": {"k": {
            "s": "BTCUSDT", "i": "15m", "t": 0, "T": 899999,
            "o": "100", "h": "105", "l": "95", "c": "102",
            "v": "10", "q": "1020", "n": 12, "x": closed,
        }}}

    def test_stream_url_contains_selected_streams(self):
        client = KlineWebSocket(KlineDataManager(), ["BTCUSDT", "ETHUSDT"], ["15m", "1h"])
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

    def test_mark_price_stream_filters_requested_symbols(self):
        received = []
        client = MarkPriceWebSocket(["ALTUSDT"], received.append)
        client._on_message(None, json.dumps({"stream": "!markPrice@arr@1s", "data": [
            {"s": "ALTUSDT", "p": "1.25"},
            {"s": "BTCUSDT", "p": "50000"},
        ]}))
        self.assertEqual(received, [{"ALTUSDT": 1.25}])
        self.assertIn("!markPrice@arr@1s", client.url)

    def test_rejects_more_than_binance_combined_stream_limit(self):
        symbols = [f"COIN{i}USDT" for i in range(342)]
        with self.assertRaises(ValueError):
            KlineWebSocket(KlineDataManager(), symbols)


if __name__ == "__main__":
    unittest.main()
