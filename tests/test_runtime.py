"""Offline tests for the public-data paper runtime orchestration."""
import os
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from paper_engine.engine import PaperEngine, PaperEngineSettings
from runtime import FuturesPaperRuntime


class RuntimeControlTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "runtime.sqlite3")
        self.engine = PaperEngine(
            self.db,
            PaperEngineSettings(
                initial_capital=1000,
                max_positions=5,
                leverage=2,
                take_profit_usdt=10,
                stop_loss_usdt=10,
            ),
        )
        self.runtime = FuturesPaperRuntime(
            self.engine, ["BTCUSDT"], interval="15m", seed_limit=10
        )

    def tearDown(self):
        self.engine.close()
        self.tmp.cleanup()

    def test_stop_closes_positions_using_current_marks(self):
        self.engine.open_position("BTCUSDT", "LONG", 100)
        with patch.object(self.runtime, "refresh_marks", return_value={"BTCUSDT": 101}):
            self.assertTrue(self.runtime._close_all_and_verify())
        self.assertEqual(self.engine.snapshot()["active_positions"], 0)

    def test_stop_does_not_claim_success_without_a_valid_mark(self):
        self.engine.open_position("BTCUSDT", "LONG", 100)
        with patch.object(self.runtime, "refresh_marks", return_value={}):
            self.assertFalse(self.runtime._close_all_and_verify())
        self.assertEqual(self.engine.snapshot()["active_positions"], 1)

    def test_stop_with_no_positions_is_already_complete(self):
        self.assertTrue(self.runtime._close_all_and_verify())

    def test_entry_is_skipped_when_mark_price_is_stale_or_missing(self):
        self.engine.set_bot_state("RUNNING")
        decision = SimpleNamespace(action="OPEN_LONG", side="LONG")
        candle = SimpleNamespace(symbol="BTCUSDT", interval="15m", close=100)
        with patch.object(self.runtime.strategy, "on_closed_candles", return_value=decision):
            self.runtime._on_closed_candle(candle)
        self.assertEqual(self.engine.snapshot()["active_positions"], 0)
        self.assertIn("no fresh public mark price", self.runtime.last_error)

    def test_entry_uses_fresh_mark_not_candle_close(self):
        self.engine.set_bot_state("RUNNING")
        self.runtime._marks = {"BTCUSDT": 105.0}
        self.runtime._mark_updated_at = {"BTCUSDT": time.monotonic()}
        decision = SimpleNamespace(action="OPEN_LONG", side="LONG")
        candle = SimpleNamespace(symbol="BTCUSDT", interval="15m", close=100)
        with patch.object(self.runtime.strategy, "on_closed_candles", return_value=decision):
            self.runtime._on_closed_candle(candle)
        position = self.engine.snapshot()["positions"]["BTCUSDT"]
        self.assertEqual(position["entry_price"], 105.0)

    def test_funding_events_are_applied_and_duplicate_poll_is_avoided(self):
        from market_data.funding import FundingEvent
        self.engine.open_position("BTCUSDT", "LONG", 100, now_ms=1_000)
        event = FundingEvent("BTCUSDT", 2_000, 0.001, 100.0)
        with patch.object(self.runtime.funding_client, "funding_events", return_value=[event]) as fetch:
            self.runtime._sync_funding()
            self.runtime._sync_funding()
        fetch.assert_called_once_with("BTCUSDT", 1_000)
        self.assertAlmostEqual(self.engine.snapshot()["funding_net"], -0.39960039960039967)

    def test_funding_api_outage_does_not_block_engine_tp_management(self):
        import requests
        self.engine.open_position("BTCUSDT", "LONG", 100, now_ms=1_000)
        with patch.object(
            self.runtime.funding_client, "funding_events",
            side_effect=requests.ConnectionError("temporary outage"),
        ), patch.object(
            self.runtime, "refresh_marks", return_value={"BTCUSDT": 103}
        ), patch.object(
            self.runtime.strategy, "evaluate_position",
            side_effect=AssertionError("HA strategy must not manage engine TP/SL"),
        ):
            self.runtime._manage_positions()
        self.assertEqual(self.engine.snapshot()["active_positions"], 0)
        self.assertTrue(self.engine.is_in_cooldown("BTCUSDT"))
        self.assertIn("funding update unavailable", self.runtime.last_error)

    def test_runtime_stop_loss_uses_atomic_engine_reversal(self):
        self.engine.open_position("BTCUSDT", "LONG", 100, now_ms=1_000)
        with patch.object(
            self.runtime, "refresh_marks", return_value={"BTCUSDT": 97}
        ), patch.object(
            self.engine, "open_position", wraps=self.engine.open_position
        ) as separate_open:
            self.runtime._manage_positions()
            separate_open.assert_not_called()

        snapshot = self.engine.snapshot()
        self.assertEqual(snapshot["active_positions"], 1)
        self.assertEqual(snapshot["positions"]["BTCUSDT"]["side"], "SHORT")
        trades = self.engine._db.execute(
            "SELECT action, reason FROM paper_trades ORDER BY id"
        ).fetchall()
        self.assertEqual(
            [(row["action"], row["reason"]) for row in trades],
            [
                ("OPEN", "SIGNAL"),
                ("CLOSE", "STOP_LOSS"),
                ("OPEN", "STOP_LOSS_REVERSAL"),
            ],
        )

    def test_runtime_stop_loss_leaves_position_flat_when_reversal_is_unaffordable(self):
        # Use the already-tested fee rate that permits five exact fixed slots
        # without a floating-point wallet remainder blocking the fifth entry.
        self.engine.settings = PaperEngineSettings(
            initial_capital=1000,
            max_positions=5,
            leverage=2,
            taker_fee_rate=0.001,
            take_profit_usdt=10,
            stop_loss_usdt=10,
        )
        for index in range(5):
            self.engine.open_position(f"COIN{index}USDT", "LONG", 100, now_ms=1_000)
        with patch.object(
            self.runtime, "refresh_marks", return_value={"COIN0USDT": 97}
        ):
            self.runtime._manage_positions()

        snapshot = self.engine.snapshot()
        self.assertEqual(snapshot["active_positions"], 4)
        self.assertNotIn("COIN0USDT", snapshot["positions"])
        self.assertIn("reversal skipped", self.runtime.last_error)

    def test_daily_realized_loss_does_not_auto_pause_runtime(self):
        self.engine.open_position("BTCUSDT", "LONG", 100, now_ms=1_000)
        self.engine.close_position("BTCUSDT", 90, now_ms=2_000)
        self.engine.settings = PaperEngineSettings(
            initial_capital=1000, max_positions=5, leverage=2,
            take_profit_usdt=10, stop_loss_usdt=10, daily_loss_limit_usdt=1,
        )
        self.engine.set_bot_state("RUNNING")

        class StopAfterOneIteration:
            def __init__(self):
                self.stopped = False

            def is_set(self):
                return self.stopped

            def wait(self, timeout):
                self.stopped = True

            def set(self):
                self.stopped = True

        self.runtime._stop = StopAfterOneIteration()
        with patch.object(self.runtime, "bootstrap"), \\
             patch.object(self.runtime, "start_feed"), \\
             patch.object(self.runtime, "stop_feed"), \\
             patch.object(self.runtime, "_manage_positions") as manage_positions:
            self.runtime.run()

        manage_positions.assert_called_once()
        self.assertEqual(self.engine.get_bot_state(), "RUNNING")

    def test_invalid_symbol_list_is_rejected(self):
        with self.assertRaises(ValueError):
            FuturesPaperRuntime(self.engine, [], interval="15m")


if __name__ == "__main__":
    unittest.main()
