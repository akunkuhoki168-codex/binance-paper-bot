"""Regression tests for WebSocket callback isolation."""
import unittest
from unittest.mock import patch

from market_data.websocket import KlineWebSocket


class WebSocketCallbackTests(unittest.TestCase):
    def setUp(self):
        self.feed = KlineWebSocket(
            manager=object(),
            symbols=["BTCUSDT"],
            intervals=["15m"],
            on_candle=lambda candle: None,
        )

    def test_callback_exception_does_not_escape_message_handler(self):
        candle = object()
        with patch.object(self.feed, "manager_packet", return_value=(candle, True)):
            with patch.object(self.feed, "on_candle", side_effect=RuntimeError("test failure")):
                with patch("builtins.print") as report:
                    self.feed._on_message(None, "{}")
        report.assert_called_once()
        self.assertIn("test failure", report.call_args.args[0])

    def test_open_candle_does_not_call_strategy_callback(self):
        candle = object()
        callback = unittest.mock.Mock()
        self.feed.on_candle = callback
        with patch.object(self.feed, "manager_packet", return_value=(candle, False)):
            self.feed._on_message(None, "{}")
        callback.assert_not_called()


if __name__ == "__main__":
    unittest.main()
