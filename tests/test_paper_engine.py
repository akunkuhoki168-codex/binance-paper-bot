import os
import tempfile
import unittest

from paper_engine.engine import PaperEngine, PaperEngineSettings


class PaperEngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "paper.sqlite3")
        self.settings = PaperEngineSettings(
            initial_capital=1000, max_positions=5, leverage=2,
            allocation_mode="fixed", taker_fee_rate=0.001,
            take_profit_usdt=10, stop_loss_usdt=10, cooldown_minutes=15,
        )
        self.engine = PaperEngine(self.db, self.settings)

    def tearDown(self):
        self.engine.close()
        self.tmp.cleanup()

    def test_opens_long_and_short_up_to_five_positions(self):
        for i in range(5):
            self.engine.open_position(f"COIN{i}USDT", "LONG" if i % 2 == 0 else "SHORT", 100)
        self.assertEqual(self.engine.snapshot()["active_positions"], 5)
        with self.assertRaises(ValueError):
            self.engine.open_position("SIXTHUSDT", "LONG", 100)

    def test_isolated_margin_and_entry_fee_are_reserved(self):
        p = self.engine.open_position("BTCUSDT", "LONG", 100)
        expected_margin = 200 / 1.002
        expected_fee = 0.4 / 1.002
        self.assertAlmostEqual(p.margin, expected_margin)
        self.assertAlmostEqual(p.quantity, expected_margin * 2 / 100)
        self.assertAlmostEqual(p.entry_fee, expected_fee)
        self.assertAlmostEqual(self.engine.snapshot()["wallet"], 800.0)
        self.assertAlmostEqual(self.engine.equity(), 999.6007984031936)

    def test_long_and_short_gross_pnl_signs(self):
        self.engine.open_position("BTCUSDT", "LONG", 100)
        self.engine.open_position("ETHUSDT", "SHORT", 100)
        self.assertAlmostEqual(self.engine._gross_pnl(self.engine._positions["BTCUSDT"], 110), 39.92015968063872)
        self.assertAlmostEqual(self.engine._gross_pnl(self.engine._positions["ETHUSDT"], 90), 39.92015968063872)

    def test_fees_and_funding_are_included_in_net_pnl(self):
        self.engine.open_position("BTCUSDT", "LONG", 100, now_ms=1_000)
        payment = self.engine.apply_funding("BTCUSDT", 0.001, 100, funding_time_ms=2_000)
        self.assertAlmostEqual(payment, -0.3992015968063872)
        net = self.engine.close_position("BTCUSDT", 101)
        # Net includes gross PnL, both fees, and funding paid.
        self.assertAlmostEqual(net, 2.7904191616766467)
        snap = self.engine.snapshot()
        self.assertAlmostEqual(snap["realized_pnl"], net)
        self.assertAlmostEqual(snap["fees_paid"], 0.8023952095808383)
        self.assertAlmostEqual(snap["funding_net"], -0.3992015968063872)

    def test_short_funding_receives_when_rate_is_positive(self):
        self.engine.open_position("ETHUSDT", "SHORT", 100, now_ms=1_000)
        self.assertAlmostEqual(self.engine.apply_funding("ETHUSDT", 0.001, 100, funding_time_ms=2_000), 0.3992015968063872)

    def test_funding_at_or_before_position_entry_is_ignored(self):
        self.engine.open_position("BTCUSDT", "LONG", 100, now_ms=2_000)
        self.assertEqual(
            self.engine.apply_funding("BTCUSDT", 0.001, 100, funding_time_ms=2_000),
            0.0,
        )
        self.assertEqual(
            self.engine.apply_funding("BTCUSDT", 0.001, 100, funding_time_ms=1_999),
            0.0,
        )
        self.assertEqual(self.engine.snapshot()["funding_net"], 0.0)
        event_count = self.engine._db.execute(
            "SELECT COUNT(*) FROM paper_funding_events WHERE symbol='BTCUSDT'"
        ).fetchone()[0]
        self.assertEqual(event_count, 0)

    def test_restart_recovers_open_positions(self):
        self.engine.open_position("BTCUSDT", "LONG", 100)
        self.engine.close()
        self.engine = PaperEngine(self.db, self.settings)
        snap = self.engine.snapshot()
        self.assertEqual(snap["active_positions"], 1)
        self.assertEqual(snap["positions"]["BTCUSDT"]["side"], "LONG")
        self.assertAlmostEqual(snap["positions"]["BTCUSDT"]["margin"], 200 / 1.002)

    def test_take_profit_closes_and_starts_persistent_cooldown(self):
        self.engine.open_position("BTCUSDT", "LONG", 100, now_ms=1_000)
        # $10 net threshold is reached after price appreciation and fees.
        result = self.engine.evaluate_exit("BTCUSDT", 103, now_ms=2_000)
        self.assertEqual(result, "TAKE_PROFIT")
        self.assertTrue(self.engine.is_in_cooldown("BTCUSDT", now_ms=2_001))
        with self.assertRaises(ValueError):
            self.engine.open_position("BTCUSDT", "LONG", 100, now_ms=2_001)

    def test_stop_loss_closes_and_reverses_when_margin_is_available(self):
        self.engine.open_position("BTCUSDT", "LONG", 100)
        result = self.engine.evaluate_exit("BTCUSDT", 97)
        self.assertEqual(result, "STOP_LOSS_REVERSED")
        snap = self.engine.snapshot()
        self.assertEqual(snap["active_positions"], 1)
        self.assertEqual(snap["positions"]["BTCUSDT"]["side"], "SHORT")

    def test_stop_loss_closes_without_reversal_when_margin_is_insufficient(self):
        for i in range(5):
            self.engine.open_position(f"COIN{i}USDT", "LONG", 100)
        result = self.engine.evaluate_exit("COIN0USDT", 97)
        self.assertEqual(result, "STOP_LOSS")
        snap = self.engine.snapshot()
        self.assertEqual(snap["active_positions"], 4)
        self.assertNotIn("COIN0USDT", snap["positions"])

    def test_realized_pnl_since_timestamp_sums_only_closed_trades(self):
        self.engine.open_position("BTCUSDT", "LONG", 100, now_ms=1_000)
        self.engine.close_position("BTCUSDT", 101, now_ms=2_000)
        self.engine.open_position("ETHUSDT", "LONG", 100, now_ms=3_000)
        self.engine.close_position("ETHUSDT", 99, now_ms=4_000)
        self.assertAlmostEqual(
            self.engine.realized_pnl_since(3_000),
            sum(row["net_pnl"] for row in self.engine._db.execute(
                "SELECT net_pnl FROM paper_trades WHERE action='CLOSE' AND created_at >= 3000"
            ).fetchall()),
        )

    def test_daily_loss_limit_setting_validation(self):
        with self.assertRaises(ValueError):
            PaperEngineSettings(daily_loss_limit_usdt=-1)

    def test_invalid_settings_and_prices_are_rejected(self):
        with self.assertRaises(ValueError):
            PaperEngineSettings(leverage=11)
        with self.assertRaises(ValueError):
            self.engine.open_position("BTCUSDT", "LONG", 0)

    def test_fixed_allocation_capacity_drops_after_realized_loss(self):
        for i in range(5):
            self.engine.open_position(f"COIN{i}USDT", "LONG", 100)
        self.engine.close_position("COIN0USDT", 97, reason="STOP_LOSS")

        self.assertEqual(self.engine.snapshot()["active_positions"], 4)
        self.assertEqual(self.engine.effective_position_capacity(), 4)
        with self.assertRaisesRegex(ValueError, "effective position capacity is 4"):
            self.engine.open_position("NEWUSDT", "LONG", 100)

    def test_compounding_allocation_recalculates_after_realized_profit(self):
        settings = PaperEngineSettings(
            initial_capital=1000,
            max_positions=5,
            leverage=2,
            allocation_mode="compounding",
            taker_fee_rate=0.001,
        )
        engine = PaperEngine(os.path.join(self.tmp.name, "compounding.sqlite3"), settings)
        try:
            first = engine.open_position("BTCUSDT", "LONG", 100, now_ms=1_000)
            first_budget = first.margin + first.entry_fee
            engine.close_position("BTCUSDT", 110, now_ms=2_000)

            expected_next_budget = engine.equity() / settings.max_positions
            second = engine.open_position("ETHUSDT", "SHORT", 100, now_ms=3_000)

            self.assertGreater(second.margin + second.entry_fee, first_budget)
            self.assertAlmostEqual(
                second.margin + second.entry_fee,
                expected_next_budget,
            )
        finally:
            engine.close()

    def test_fixed_allocation_does_not_silently_shrink_to_remaining_wallet(self):
        for i in range(4):
            self.engine.open_position(f"COIN{i}USDT", "LONG", 100)
        # Four slots consume four all-in allocations; the fifth still fits.
        self.engine.open_position("FIFTHUSDT", "LONG", 100)
        self.assertEqual(self.engine.snapshot()["active_positions"], 5)
        with self.assertRaises(ValueError):
            self.engine.open_position("SIXTHUSDT", "LONG", 100)


if __name__ == "__main__":
    unittest.main()
