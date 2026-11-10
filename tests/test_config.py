"""Offline tests for paper-engine environment configuration."""
import unittest

from paper_engine.config import paper_engine_settings_from_env


class PaperEngineEnvironmentConfigTests(unittest.TestCase):
    def test_defaults_to_fixed_allocation(self):
        settings = paper_engine_settings_from_env({})
        self.assertEqual(settings.allocation_mode, "fixed")
        self.assertEqual(settings.max_positions, 5)
        self.assertEqual(settings.leverage, 10)

    def test_compounding_allocation_is_selected_from_environment(self):
        settings = paper_engine_settings_from_env({
            "ALLOCATION_MODE": "  COMPOUNDING ",
            "STARTING_BALANCE_USDT": "2500",
            "MAX_POSITIONS": "4",
            "LEVERAGE": "7",
        })
        self.assertEqual(settings.allocation_mode, "compounding")
        self.assertEqual(settings.initial_capital, 2500)
        self.assertEqual(settings.max_positions, 4)
        self.assertEqual(settings.leverage, 7)

    def test_fixed_allocation_can_be_selected_explicitly(self):
        settings = paper_engine_settings_from_env({"ALLOCATION_MODE": "fixed"})
        self.assertEqual(settings.allocation_mode, "fixed")

    def test_unsupported_allocation_mode_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "allocation_mode"):
            paper_engine_settings_from_env({"ALLOCATION_MODE": "martingale"})

    def test_existing_risk_settings_are_loaded_and_validated(self):
        settings = paper_engine_settings_from_env({
            "TAKER_FEE_RATE": "0.001",
            "TAKE_PROFIT_USDT": "12.5",
            "STOP_LOSS_USDT": "6.25",
        })
        self.assertEqual(settings.taker_fee_rate, 0.001)
        self.assertEqual(settings.take_profit_usdt, 12.5)
        self.assertEqual(settings.stop_loss_usdt, 6.25)

    def test_leverage_limit_is_still_enforced(self):
        with self.assertRaisesRegex(ValueError, "leverage"):
            paper_engine_settings_from_env({"LEVERAGE": "11"})


if __name__ == "__main__":
    unittest.main()
