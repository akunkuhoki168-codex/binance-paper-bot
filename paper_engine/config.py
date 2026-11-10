"""Environment-to-settings configuration for the paper-only execution engine."""
from __future__ import annotations

import os
from collections.abc import Mapping

from .engine import PaperEngineSettings


def paper_engine_settings_from_env(
    environ: Mapping[str, str] | None = None,
) -> PaperEngineSettings:
    """Build validated engine settings from environment variables.

    ALLOCATION_MODE is intentionally limited to the engine's supported
    values: "fixed" and "compounding". The engine validates all numeric
    limits, including the five-position and 1x-10x leverage caps.
    """
    values = os.environ if environ is None else environ
    return PaperEngineSettings(
        initial_capital=float(values.get("STARTING_BALANCE_USDT", "1000")),
        max_positions=int(values.get("MAX_POSITIONS", "5")),
        leverage=int(values.get("LEVERAGE", "10")),
        allocation_mode=values.get("ALLOCATION_MODE", "fixed").strip().lower(),
        taker_fee_rate=float(values.get("TAKER_FEE_RATE", "0.0005")),
        take_profit_usdt=float(values.get("TAKE_PROFIT_USDT", "10")),
        stop_loss_usdt=float(values.get("STOP_LOSS_USDT", "5")),
        daily_loss_limit_usdt=float(values.get("DAILY_LOSS_LIMIT_USDT", "50")),
    )
