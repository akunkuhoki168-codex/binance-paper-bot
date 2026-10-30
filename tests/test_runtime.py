"""Offline tests for the public-data paper runtime orchestration."""
import os
import tempfile
import unittest
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

    def test_invalid_symbol_list_is_rejected(self):
        with self.assertRaises(ValueError):
            FuturesPaperRuntime(self.engine, [], interval="15m")


if __name__ == "__main__":
    unittest.main()
