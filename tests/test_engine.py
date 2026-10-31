"""Offline regression tests for the SQLite paper execution engine."""
import os
import tempfile
import unittest

from paper_engine.engine import PaperEngine, PaperEngineSettings


class PaperEngineAccountingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "engine.sqlite3")
        self.engine = PaperEngine(
            self.db,
            PaperEngineSettings(
                initial_capital=1000,
                max_positions=5,
                leverage=2,
                taker_fee_rate=0.001,
                take_profit_usdt=100,
                stop_loss_usdt=100,
                cooldown_minutes=1,
            ),
        )

    def tearDown(self):
        self.engine.close()
        self.tmp.cleanup()

    def test_net_pnl_includes_entry_fee_exit_fee_and_funding(self):
        position = self.engine.open_position("BTCUSDT", "LONG", 100, now_ms=1)
        funding = self.engine.apply_funding("BTCUSDT", 0.0001, 100, funding_time_ms=2)
        mark = 101
        gross = (mark - position.entry_price) * position.quantity
        exit_fee = position.quantity * mark * self.engine.settings.taker_fee_rate
        expected = gross - position.entry_fee - exit_fee + funding
        self.assertAlmostEqual(self.engine.position_net_pnl("BTCUSDT", mark), expected)

    def test_duplicate_funding_event_is_applied_only_once(self):
        position = self.engine.open_position("BTCUSDT", "LONG", 100, now_ms=1)
        first = self.engine.apply_funding(
            "BTCUSDT", 0.0001, 100, funding_time_ms=2
        )
        second = self.engine.apply_funding(
            "BTCUSDT", 0.0001, 100, funding_time_ms=2
        )
        self.assertNotEqual(first, 0.0)
        self.assertEqual(second, 0.0)
        self.assertAlmostEqual(
            self.engine.snapshot()["positions"]["BTCUSDT"]["funding_net"], first
        )

    def test_funding_before_position_open_is_ignored(self):
        self.engine.open_position("BTCUSDT", "LONG", 100, now_ms=10)
        payment = self.engine.apply_funding(
            "BTCUSDT", 0.0001, 100, funding_time_ms=9
        )
        self.assertEqual(payment, 0.0)

    def test_force_close_reports_missing_mark_and_keeps_position_open(self):
        self.engine.open_position("BTCUSDT", "LONG", 100, now_ms=1)
        results = self.engine.force_close_all({})
        self.assertEqual(results["BTCUSDT"], "missing or invalid mark price")
        self.assertIn("BTCUSDT", self.engine.snapshot()["positions"])

    def test_dashboard_monitoring_persistence(self):
        self.engine.record_signal("btcusdt", "OPEN_LONG", "LONG", "HA flip", 123)
        self.engine.record_event("feed connected")
        self.engine.update_market_prices({"btcusdt": 101.5})
        import sqlite3
        with sqlite3.connect(self.db) as db:
            signal = db.execute("SELECT symbol,action,side,reason,candle_time FROM strategy_signals").fetchone()
            event = db.execute("SELECT message FROM runtime_events").fetchone()
            price = db.execute("SELECT symbol,mark_price FROM market_prices").fetchone()
        self.assertEqual(signal, ("BTCUSDT", "OPEN_LONG", "LONG", "HA flip", 123))
        self.assertEqual(event[0], "feed connected")
        self.assertEqual(price, ("BTCUSDT", 101.5))

    def test_take_profit_cooldown_is_persisted(self):
        self.engine.open_position("BTCUSDT", "LONG", 100, now_ms=1)
        self.engine.close_position("BTCUSDT", 101, reason="TAKE_PROFIT", now_ms=1000)
        self.assertTrue(self.engine.is_in_cooldown("BTCUSDT", now_ms=1001))
        with self.assertRaises(ValueError):
            self.engine.open_position("BTCUSDT", "LONG", 100, now_ms=1001)


if __name__ == "__main__":
    unittest.main()
