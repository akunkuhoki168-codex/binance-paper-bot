"""Regression tests for WebSocket callback isolation."""
import unittest
from unittest.mock import Mock, patch

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


    def test_health_reports_connected_then_stale_stream(self):
        self.feed._on_open(None)
        self.assertEqual(self.feed.health(15)["status"], "HEALTHY")
        connected_at = self.feed._connected_at
        with patch("market_data.websocket.time.monotonic", return_value=connected_at + 16):
            health = self.feed.health(15)
        self.assertEqual(health["status"], "STALE")
        self.assertEqual(health["streams"]["BTCUSDT:15m"]["status"], "STALE")

    def test_valid_message_refreshes_only_its_stream_timestamp(self):
        from types import SimpleNamespace

        self.feed._on_open(None)
        candle = SimpleNamespace(symbol="BTCUSDT", interval="15m")
        with patch.object(self.feed, "manager_packet", return_value=(candle, False)):
            with patch("market_data.websocket.time.monotonic", return_value=100.0):
                self.feed._on_message(None, "{}")
        with patch("market_data.websocket.time.monotonic", return_value=104.0):
            health = self.feed.health(10)
        stream = health["streams"]["BTCUSDT:15m"]
        self.assertEqual(stream["status"], "HEALTHY")
        self.assertEqual(stream["age_seconds"], 4.0)

    def test_stale_connection_can_be_forced_into_reconnect_loop_once(self):
        self.feed._on_open(None)
        app = Mock()
        self.feed._app = app
        self.assertTrue(self.feed.request_reconnect("stale test stream"))
        app.close.assert_called_once()
        self.assertFalse(self.feed.request_reconnect("duplicate request"))
        health = self.feed.health(15)
        self.assertEqual(health["status"], "RECONNECTING")
        self.assertEqual(health["stale_reconnect_count"], 1)

    def test_close_and_error_callbacks_are_exposed_in_health(self):
        self.feed._on_error(None, RuntimeError("network failure"))
        self.feed._on_close(None, 1006, "connection lost")
        health = self.feed.health()
        self.assertEqual(health["status"], "DISCONNECTED")
        self.assertIn("network failure", health["last_error"])
        self.assertEqual(health["last_close_code"], 1006)
        self.assertEqual(health["last_close_reason"], "connection lost")


if __name__ == "__main__":
    unittest.main()
