"""Unit tests for the standalone HA Flips strategy module."""
import unittest
from datetime import datetime, timedelta, timezone

from strategies.HA_flips import Candle, HAFlipsSettings, HAFlipsStrategy, heikin_ashi


def red_then_green(offset=0):
    return [Candle(1 + offset, 10, 11, 7, 10), Candle(2 + offset, 10, 13, 10, 12)]


def green_then_red(offset=0):
    return [Candle(1 + offset, 10, 12, 9, 10), Candle(2 + offset, 9, 10, 8, 8)]


class TestHAFlips(unittest.TestCase):
    def setUp(self):
        self.settings = HAFlipsSettings(10.0, 5.0, "15m", 15)
        self.strategy = HAFlipsStrategy(self.settings)
        self.now = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def test_heikin_ashi_colours(self):
        self.assertEqual([c["colour"] for c in heikin_ashi(red_then_green())], ["RED", "GREEN"])
        self.assertEqual([c["colour"] for c in heikin_ashi(green_then_red())], ["GREEN", "RED"])

    def test_red_to_green_opens_long(self):
        decision = self.strategy.on_closed_candles("BTCUSDT", red_then_green(), now=self.now)
        self.assertEqual((decision.action, decision.side), ("OPEN_LONG", "LONG"))

    def test_green_to_red_opens_short(self):
        decision = self.strategy.on_closed_candles("ETHUSDT", green_then_red(), now=self.now)
        self.assertEqual((decision.action, decision.side), ("OPEN_SHORT", "SHORT"))

    def test_opposite_flip_is_ignored_while_position_open_and_candle_consumed(self):
        first = self.strategy.on_closed_candles("BTCUSDT", green_then_red(), position_side="LONG", now=self.now)
        later = self.strategy.on_closed_candles("BTCUSDT", green_then_red(), position_side=None, now=self.now)
        self.assertEqual(first.action, "HOLD")
        self.assertEqual(later.action, "HOLD")

    def test_same_or_older_candle_is_not_signalled_twice(self):
        first = self.strategy.on_closed_candles("BTCUSDT", red_then_green(), now=self.now)
        duplicate = self.strategy.on_closed_candles("BTCUSDT", red_then_green(), now=self.now)
        older = self.strategy.on_closed_candles("BTCUSDT", [Candle(0, 10, 11, 7, 10), Candle(1, 10, 13, 10, 12)], now=self.now)
        self.assertEqual(first.action, "OPEN_LONG")
        self.assertEqual(duplicate.action, "HOLD")
        self.assertEqual(older.action, "HOLD")

    def test_stop_loss_requests_opposite_side_once_per_position(self):
        first = self.strategy.evaluate_position("BTCUSDT", "LONG", -5.0, now=self.now)
        duplicate = self.strategy.evaluate_position("BTCUSDT", "LONG", -6.0, now=self.now)
        self.assertEqual((first.action, first.side), ("CLOSE_AND_REVERSE", "SHORT"))
        self.assertEqual(duplicate.action, "HOLD")
        self.strategy.evaluate_position("BTCUSDT", None, 0.0, now=self.now)
        next_position = self.strategy.evaluate_position("BTCUSDT", "SHORT", -5.0, now=self.now)
        self.assertEqual((next_position.action, next_position.side), ("CLOSE_AND_REVERSE", "LONG"))

    def test_take_profit_starts_15_minute_cooldown(self):
        decision = self.strategy.evaluate_position("BTCUSDT", "LONG", 10.0, now=self.now)
        self.assertEqual(decision.action, "CLOSE_TAKE_PROFIT")
        self.assertEqual(decision.cooldown_until, self.now + timedelta(minutes=15))

    def test_cooldown_consumes_old_signal_but_accepts_new_candle_after_expiry(self):
        self.strategy.evaluate_position("BTCUSDT", "LONG", 10.0, now=self.now)
        blocked = self.strategy.on_closed_candles("BTCUSDT", red_then_green(), now=self.now + timedelta(minutes=14))
        stale = self.strategy.on_closed_candles("BTCUSDT", red_then_green(), now=self.now + timedelta(minutes=15))
        fresh = self.strategy.on_closed_candles("BTCUSDT", red_then_green(offset=1), now=self.now + timedelta(minutes=15))
        self.assertEqual(blocked.action, "HOLD")
        self.assertEqual(stale.action, "HOLD")
        self.assertEqual(fresh.action, "OPEN_LONG")

    def test_doji_does_not_erase_previous_non_doji_colour(self):
        candles = [
            Candle(1, 10, 11, 7, 10),   # RED
            Candle(2, 10, 10, 9, 10),   # HA DOJI
            Candle(3, 10, 13, 10, 12),  # GREEN
        ]
        colours = [c["colour"] for c in heikin_ashi(candles)]
        self.assertEqual(colours, ["RED", "DOJI", "GREEN"])
        decision = self.strategy.on_closed_candles("BTCUSDT", candles, now=self.now)
        self.assertEqual(decision.action, "OPEN_LONG")

    def test_unsorted_candles_are_rejected(self):
        with self.assertRaises(ValueError):
            heikin_ashi(list(reversed(red_then_green())))

    def test_invalid_position_side_is_rejected(self):
        with self.assertRaises(ValueError):
            self.strategy.on_closed_candles("BTCUSDT", red_then_green(), position_side="SIDEWAYS")

    def test_invalid_timeframe_is_rejected(self):
        with self.assertRaises(ValueError):
            HAFlipsSettings(10, 5, "5m")

    def test_non_finite_settings_are_rejected(self):
        with self.assertRaises(ValueError):
            HAFlipsSettings(float("nan"), 5)


if __name__ == "__main__":
    unittest.main()
