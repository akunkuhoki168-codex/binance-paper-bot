import unittest

from market_data.klines import (
    FuturesCandle,
    KlineDataManager,
    eligible_usdt_perpetuals,
    parse_rest_kline,
    parse_websocket_kline,
    websocket_stream_names,
)


def candle(open_time: int, symbol: str = "BTCUSDT", interval: str = "15m") -> FuturesCandle:
    return FuturesCandle(
        symbol=symbol,
        interval=interval,
        open_time=open_time,
        close_time=open_time + 899_999,
        open=100.0,
        high=105.0,
        low=95.0,
        close=102.0,
        volume=10.0,
        quote_volume=1_020.0,
        trades=12,
    )


class KlineParsingTests(unittest.TestCase):
    def test_parse_rest_kline(self):
        row = [0, "100", "105", "95", "102", "10", 899999, "1020", 12, "4", "500", "0"]
        parsed = parse_rest_kline("btcusdt", "15m", row)
        self.assertEqual(parsed.symbol, "BTCUSDT")
        self.assertEqual(parsed.close, 102.0)
        self.assertEqual(parsed.trades, 12)

    def test_websocket_parser_reports_closed_flag(self):
        packet = {
            "stream": "btcusdt@kline_15m",
            "data": {"k": {
                "s": "BTCUSDT", "i": "15m", "t": 0, "T": 899999,
                "o": "100", "h": "105", "l": "95", "c": "102",
                "v": "10", "q": "1020", "n": 12, "x": True,
            }},
        }
        parsed, closed = parse_websocket_kline(packet)
        self.assertTrue(closed)
        self.assertEqual(parsed.symbol, "BTCUSDT")


class KlineManagerTests(unittest.TestCase):
    def test_ignores_open_duplicate_and_out_of_order_candles(self):
        manager = KlineDataManager()
        packet = {"data": {"k": {
            "s": "BTCUSDT", "i": "15m", "t": 0, "T": 899999,
            "o": "100", "h": "105", "l": "95", "c": "102",
            "v": "10", "q": "1020", "n": 12, "x": False,
        }}}
        self.assertFalse(manager.add_websocket_packet(packet))
        self.assertTrue(manager.add_closed(candle(0)))
        self.assertFalse(manager.add_closed(candle(0)))
        self.assertFalse(manager.add_closed(candle(0 - 900_000)))
        self.assertEqual(len(manager.get("BTCUSDT", "15m")), 1)

    def test_cache_is_bounded(self):
        manager = KlineDataManager(max_candles=2)
        manager.seed([candle(0), candle(900_000), candle(1_800_000)])
        self.assertEqual([c.open_time for c in manager.get("BTCUSDT", "15m")],
                         [900_000, 1_800_000])

    def test_histories_are_separate_by_symbol_and_interval(self):
        manager = KlineDataManager()
        manager.add_closed(candle(0, "BTCUSDT", "15m"))
        manager.add_closed(candle(0, "ETHUSDT", "15m"))
        manager.add_closed(candle(0, "BTCUSDT", "1h"))
        self.assertEqual(len(manager.get("BTCUSDT", "15m")), 1)
        self.assertEqual(len(manager.get("ETHUSDT", "15m")), 1)
        self.assertEqual(len(manager.get("BTCUSDT", "1h")), 1)


class UniverseAndStreamsTests(unittest.TestCase):
    def test_universe_filters_to_trading_usdt_perpetuals_in_rank_order(self):
        info = {"symbols": [
            {"symbol": "BTCUSDT", "baseAsset": "BTC", "quoteAsset": "USDT",
             "contractType": "PERPETUAL", "status": "TRADING"},
            {"symbol": "ETHUSDT", "baseAsset": "ETH", "quoteAsset": "USDT",
             "contractType": "PERPETUAL", "status": "TRADING"},
            {"symbol": "XRPUSDT", "baseAsset": "XRP", "quoteAsset": "USDT",
             "contractType": "PERPETUAL", "status": "BREAK"},
            {"symbol": "SOLUSDC", "baseAsset": "SOL", "quoteAsset": "USDC",
             "contractType": "PERPETUAL", "status": "TRADING"},
            {"symbol": "ADAUSDT", "baseAsset": "ADA", "quoteAsset": "USDT",
             "contractType": "CURRENT_QUARTER", "status": "TRADING"},
        ]}
        self.assertEqual(
            eligible_usdt_perpetuals(["ETH", "SOL", "BTC", "XRP"], info, limit=2),
            ["ETHUSDT", "BTCUSDT"],
        )

    def test_stream_names_cover_each_symbol_interval_pair(self):
        self.assertEqual(
            websocket_stream_names(["ETHUSDT", "BTCUSDT"], ["15m", "1h"]),
            [
                "btcusdt@kline_15m", "btcusdt@kline_1h",
                "ethusdt@kline_15m", "ethusdt@kline_1h",
            ],
        )

    def test_invalid_interval_is_rejected(self):
        with self.assertRaises(ValueError):
            websocket_stream_names(["BTCUSDT"], ["5m"])


if __name__ == "__main__":
    unittest.main()
