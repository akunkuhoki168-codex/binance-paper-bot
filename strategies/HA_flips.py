"""Heikin-Ashi colour-flip strategy for USDⓈ-M Futures paper trading.

This module is deliberately independent of the trading engine. It computes signals
and returns decisions; it never submits exchange orders, reads API credentials, or
places trades. The caller must provide CLOSED candles and net PnL after fees and
funding, and must execute/validate returned decisions in its paper engine.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Literal, Optional

Side = Literal["LONG", "SHORT"]
Timeframe = Literal["15m", "1h", "4h"]
Action = Literal["OPEN_LONG", "OPEN_SHORT", "CLOSE_AND_REVERSE", "CLOSE_TAKE_PROFIT", "HOLD"]


@dataclass(frozen=True)
class HAFlipsSettings:
    """Global settings shared by every symbol and every transaction."""

    take_profit_usdt: float
    stop_loss_usdt: float
    timeframe: Timeframe = "15m"
    cooldown_minutes: int = 15

    def __post_init__(self) -> None:
        if self.timeframe not in ("15m", "1h", "4h"):
            raise ValueError("timeframe must be one of: 15m, 1h, 4h")
        if self.take_profit_usdt <= 0 or self.stop_loss_usdt <= 0:
            raise ValueError("take_profit_usdt and stop_loss_usdt must be positive")
        if self.cooldown_minutes < 0:
            raise ValueError("cooldown_minutes cannot be negative")


@dataclass(frozen=True)
class Candle:
    """A completed OHLC candle. Timestamps are Unix milliseconds."""

    open_time: int
    open: float
    high: float
    low: float
    close: float

    def __post_init__(self) -> None:
        if min(self.open, self.high, self.low, self.close) <= 0:
            raise ValueError("OHLC prices must be positive")
        if self.high < max(self.open, self.close, self.low):
            raise ValueError("high is inconsistent with OHLC values")
        if self.low > min(self.open, self.close, self.high):
            raise ValueError("low is inconsistent with OHLC values")


@dataclass(frozen=True)
class Decision:
    action: Action
    symbol: str
    reason: str
    side: Optional[Side] = None
    cooldown_until: Optional[datetime] = None


def heikin_ashi(candles: Iterable[Candle]) -> list[dict[str, float | int | str]]:
    """Convert chronological completed OHLC candles into Heikin-Ashi candles."""
    source = list(candles)
    if not source:
        return []
    result: list[dict[str, float | int | str]] = []
    previous_ha_open: Optional[float] = None
    previous_ha_close: Optional[float] = None

    for candle in source:
        ha_close = (candle.open + candle.high + candle.low + candle.close) / 4.0
        if previous_ha_open is None or previous_ha_close is None:
            ha_open = (candle.open + candle.close) / 2.0
        else:
            ha_open = (previous_ha_open + previous_ha_close) / 2.0
        ha_high = max(candle.high, ha_open, ha_close)
        ha_low = min(candle.low, ha_open, ha_close)
        colour = "GREEN" if ha_close > ha_open else "RED" if ha_close < ha_open else "DOJI"
        result.append({
            "open_time": candle.open_time,
            "open": ha_open,
            "high": ha_high,
            "low": ha_low,
            "close": ha_close,
            "colour": colour,
        })
        previous_ha_open, previous_ha_close = ha_open, ha_close
    return result


class HAFlipsStrategy:
    """Stateful per-process signal helper; persistence belongs to the engine."""

    def __init__(self, settings: HAFlipsSettings):
        self.settings = settings
        self._cooldown_until: dict[str, datetime] = {}
        self._last_candle_time: dict[str, int] = {}

    def cooldown_until(self, symbol: str) -> Optional[datetime]:
        return self._cooldown_until.get(symbol.upper())

    def evaluate_position(
        self,
        symbol: str,
        position_side: Optional[Side],
        net_pnl_usdt: float,
        now: Optional[datetime] = None,
    ) -> Decision:
        """Evaluate TP/SL on every mark-price update, not just candle close.

        net_pnl_usdt must already include entry/exit fees and funding cash flows.
        On SL, the returned opposite-side intent asks the engine to close and then
        attempt a reversal using whatever margin it can safely allocate.
        """
        symbol = symbol.upper()
        if position_side is None:
            return Decision("HOLD", symbol, "No open position")

        if net_pnl_usdt >= self.settings.take_profit_usdt:
            current = now or datetime.now(timezone.utc)
            if current.tzinfo is None:
                current = current.replace(tzinfo=timezone.utc)
            until = current + timedelta(minutes=self.settings.cooldown_minutes)
            self._cooldown_until[symbol] = until
            return Decision(
                "CLOSE_TAKE_PROFIT", symbol,
                f"Net PnL reached TP of {self.settings.take_profit_usdt:.2f} USDT; "
                f"cooldown for {self.settings.cooldown_minutes} minutes",
                cooldown_until=until,
            )

        if net_pnl_usdt <= -self.settings.stop_loss_usdt:
            opposite: Side = "SHORT" if position_side == "LONG" else "LONG"
            return Decision(
                "CLOSE_AND_REVERSE", symbol,
                f"Net PnL reached SL of {self.settings.stop_loss_usdt:.2f} USDT; "
                "close and request immediate opposite-side entry using available margin",
                side=opposite,
            )

        return Decision("HOLD", symbol, "Neither net-PnL TP nor SL has been reached")

    def on_closed_candles(
        self,
        symbol: str,
        candles: Iterable[Candle],
        position_side: Optional[Side] = None,
        now: Optional[datetime] = None,
    ) -> Decision:
        """Evaluate a new completed HA candle; never reacts to an unfinished candle.

        Opposite colour changes are ignored while a position is open. Entry signals
        are accepted only while flat and outside that symbol's TP cooldown.
        """
        symbol = symbol.upper()
        if position_side is not None:
            return Decision("HOLD", symbol, "Position remains open; HA flips do not reverse it")

        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        until = self._cooldown_until.get(symbol)
        if until and current < until:
            return Decision("HOLD", symbol, f"TP cooldown active until {until.isoformat()}")
        if until and current >= until:
            self._cooldown_until.pop(symbol, None)

        ha = heikin_ashi(candles)
        if len(ha) < 2:
            return Decision("HOLD", symbol, "Need at least two completed candles")
        latest = ha[-1]
        previous = ha[-2]
        candle_time = int(latest["open_time"])
        if self._last_candle_time.get(symbol) == candle_time:
            return Decision("HOLD", symbol, "This completed candle was already evaluated")
        self._last_candle_time[symbol] = candle_time

        old_colour, new_colour = previous["colour"], latest["colour"]
        if old_colour == "RED" and new_colour == "GREEN":
            return Decision("OPEN_LONG", symbol, "Confirmed closed Heikin-Ashi flip RED to GREEN", "LONG")
        if old_colour == "GREEN" and new_colour == "RED":
            return Decision("OPEN_SHORT", symbol, "Confirmed closed Heikin-Ashi flip GREEN to RED", "SHORT")
        return Decision("HOLD", symbol, f"No actionable HA colour flip ({old_colour} to {new_colour})")
