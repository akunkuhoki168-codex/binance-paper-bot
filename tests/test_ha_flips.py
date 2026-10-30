"""Unit tests for the standalone HA Flips strategy module."""
import unittest
from datetime import datetime, timedelta, timezone

from strategies.HA_flips import (
    Candle,
    HAFlipsSettings,
    HAFlipsStrategy,
    heikin_ashi,
)


def red_then_green():
    return [
        Candle(1, 10, 11, 7, 10),   # HA RED
        Candle(2, 10, 13, 10, 12),  # HA GREEN
    ]


def green_then_red():
    return [
        Candle(1, 10, 12, 9, 10),   # HA GREEN
        Candle(2, 9, 10, 8, 8),     # HA RED
    ]


class TestHAFlips(unittest.TestCase):
    def setUp(self):
        self.settings = HAFlipsSettings(
            take_profit_usdt=10.0,
            stop_loss_usdt=5.0,
            timeframe="15m",
            cooldown_minutes=15,
        )
        self.strategy = HAFlipsStrategy(self.settings)
        self.now = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def test_heikin_ashi_colours(self):
        self.assertEqual(
            [c["colour"] for c in heikin_ashi(red_then_green())],
            ["RED", "GREEN"],
        )
        self.assertEqual(
            [c["colour"] for c in heikin_ashi(green_then_red())],
            ["GREEN", "RED"],
        )

    def test_red_to_green_opens_long(self):
        decision = self.strategy.on_closed_candles(
            "BTCUSDT", red_then_green(), now=self.now
        )
        self.assertEqual(decision.action, "OPEN_LONG")
        self.assertEqual(decision.side, "LONG")

    def test_green_to_red_opens_short(self):
        decision = self.strategy.on_closed_candles(
            "ETHUSDT", green_then_red(), now=self.now
        )
        self.assertEqual(decision.action, "OPEN_SHORT")
        self.assertEqual(decision.side, "SHORT")

    def test_opposite_flip_is_ignored_while_position_open(self):
        decision = self.strategy.on_closed_candles(
            "BTCUSDT", green_then_red(), position_side="LONG", now=self.now
        )
        self.assertEqual(decision.action, "HOLD")

    def test_same_closed_candle_is_not_signalled_twice(self):
        first = self.strategy.on_closed_candles(
            "BTCUSDT", red_then_green(), now=self.now
        )
        second = self.strategy.on_closed_candles(
            "BTCUSDT", red_then_green(), now=self.now
        )
        self.assertEqual(first.action, "OPEN_LONG")
        self.assertEqual(second.action, "HOLD")

    def test_stop_loss_requests_immediate_opposite_side(self):
        long_sl = self.strategy.evaluate_position(
            "BTCUSDT", "LONG", -5.0, now=self.now
        )
        short_sl = self.strategy.evaluate_position(
            "ETHUSDT", "SHORT", -5.0, now=self.now
        )
        self.assertEqual(long_sl.action, "CLOSE_AND_REVERSE")
        self.assertEqual(long_sl.side, "SHORT")
        self.assertEqual(short_sl.side, "LONG")

    def test_take_profit_starts_fixed_15_minute_cooldown(self):
        decision = self.strategy.evaluate_position(
            "BTCUSDT", "LONG", 10.0, now=self.now
        )
        self.assertEqual(decision.action, "CLOSE_TAKE_PROFIT")
        self.assertEqual(decision.cooldown_until, self.now + timedelta(minutes=15))

    def test_entry_is_blocked_during_cooldown_then_allowed(self):
        self.strategy.evaluate_position("BTCUSDT", "LONG", 10.0, now=self.now)
        blocked = self.strategy.on_closed_candles(
            "BTCUSDT", red_then_green(), now=self.now + timedelta(minutes=14)
        )
        allowed = self.strategy.on_closed_candles(
            "BTCUSDT", red_then_green(), now=self.now + timedelta(minutes=15)
        )
        self.assertEqual(blocked.action, "HOLD")
        self.assertEqual(allowed.action, "OPEN_LONG")

    def test_invalid_timeframe_is_rejected(self):
        with self.assertRaises(ValueError):
            HAFlipsSettings(
                take_profit_usdt=10,
                stop_loss_usdt=5,
                timeframe="5m",
            )


if __name__ == "__main__":
    unittest.main()
