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
        funding = self.engine.apply_funding("BTCUSDT", 0.0001, 100)
        mark = 101
        gross = (mark - position.entry_price) * position.quantity
        exit_fee = position.quantity * mark * self.engine.settings.taker_fee_rate
        expected = gross - position.entry_fee - exit_fee + funding
        self.assertAlmostEqual(self.engine.position_net_pnl("BTCUSDT", mark), expected)

    def test_force_close_reports_missing_mark_and_keeps_position_open(self):
        self.engine.open_position("BTCUSDT", "LONG", 100, now_ms=1)
        results = self.engine.force_close_all({})
        self.assertEqual(results["BTCUSDT"], "missing or invalid mark price")
        self.assertIn("BTCUSDT", self.engine.snapshot()["positions"])

    def test_take_profit_cooldown_is_persisted(self):
        self.engine.open_position("BTCUSDT", "LONG", 100, now_ms=1)
        self.engine.close_position("BTCUSDT", 101, reason="TAKE_PROFIT", now_ms=1000)
        self.assertTrue(self.engine.is_in_cooldown("BTCUSDT", now_ms=1001))
        with self.assertRaises(ValueError):
            self.engine.open_position("BTCUSDT", "LONG", 100, now_ms=1001)


if __name__ == "__main__":
    unittest.main()
