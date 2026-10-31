"""Heikin-Ashi colour-flip strategy for USDⓈ-M Futures paper trading.

This module is independent of the trading engine. It emits decisions only: it
never reads credentials or submits exchange orders. The caller supplies CLOSED
candles and net PnL after fees and funding, then safely executes decisions.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import isfinite
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
        if not isfinite(self.take_profit_usdt) or self.take_profit_usdt <= 0:
            raise ValueError("take_profit_usdt must be a finite positive number")
        if not isfinite(self.stop_loss_usdt) or self.stop_loss_usdt <= 0:
            raise ValueError("stop_loss_usdt must be a finite positive number")
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
        prices = (self.open, self.high, self.low, self.close)
        if not all(isfinite(price) and price > 0 for price in prices):
            raise ValueError("OHLC prices must be finite and positive")
        if self.high < max(self.open, self.close, self.low):
            raise ValueError("high is inconsistent with OHLC values")
        if self.low > min(self.open, self.close, self.high):
            raise ValueError("low is inconsistent with OHLC values")
        if not isinstance(self.open_time, int) or self.open_time < 0:
            raise ValueError("open_time must be a non-negative Unix-millisecond integer")


@dataclass(frozen=True)
class Decision:
    action: Action
    symbol: str
    reason: str
    side: Optional[Side] = None
    cooldown_until: Optional[datetime] = None


def _validated_candles(candles: Iterable[Candle]) -> list[Candle]:
    source = list(candles)
    if any(not isinstance(candle, Candle) for candle in source):
        raise TypeError("candles must contain only Candle instances")
    if any(a.open_time >= b.open_time for a, b in zip(source, source[1:])):
        raise ValueError("candles must be in strictly increasing chronological order")
    return source


def heikin_ashi(candles: Iterable[Candle]) -> list[dict[str, float | int | str]]:
    """Convert chronological completed OHLC candles into Heikin-Ashi candles."""
    source = _validated_candles(candles)
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
        self._sl_triggered_side: dict[str, Side] = {}

    def cooldown_until(self, symbol: str) -> Optional[datetime]:
        return self._cooldown_until.get(symbol.upper())

    def evaluate_position(
        self,
        symbol: str,
        position_side: Optional[Side],
        net_pnl_usdt: float,
        now: Optional[datetime] = None,
    ) -> Decision:
        """Evaluate net-PnL TP/SL on each mark-price update.

        net_pnl_usdt must include entry/exit fees and funding cash flows. The
        engine must make close/reversal execution atomic and re-check available
        margin. One SL intent is emitted per continuous position side.
        """
        symbol = symbol.upper()
        if position_side is None:
            self._sl_triggered_side.pop(symbol, None)
            return Decision("HOLD", symbol, "No open position")
        if position_side not in ("LONG", "SHORT"):
            raise ValueError("position_side must be LONG, SHORT, or None")
        if not isfinite(net_pnl_usdt):
            raise ValueError("net_pnl_usdt must be finite")

        if net_pnl_usdt >= self.settings.take_profit_usdt:
            self._sl_triggered_side.pop(symbol, None)
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
            if self._sl_triggered_side.get(symbol) == position_side:
                return Decision("HOLD", symbol, "Stop-loss reversal already requested for this position")
            self._sl_triggered_side[symbol] = position_side
            opposite: Side = "SHORT" if position_side == "LONG" else "LONG"
            return Decision(
                "CLOSE_AND_REVERSE", symbol,
                f"Net PnL reached SL of {self.settings.stop_loss_usdt:.2f} USDT; "
                "close and request immediate opposite-side entry using available margin",
                side=opposite,
            )

        self._sl_triggered_side.pop(symbol, None)
        return Decision("HOLD", symbol, "Neither net-PnL TP nor SL has been reached")

    def on_closed_candles(
        self,
        symbol: str,
        candles: Iterable[Candle],
        position_side: Optional[Side] = None,
        now: Optional[datetime] = None,
    ) -> Decision:
        """Evaluate completed candles; DOJI candles do not erase the last colour.

        Every newly observed candle is consumed even while a position or cooldown
        blocks entry, so stale signals cannot open a position later.
        """
        symbol = symbol.upper()
        if position_side not in (None, "LONG", "SHORT"):
            raise ValueError("position_side must be LONG, SHORT, or None")

        ha = heikin_ashi(candles)
        if not ha:
            return Decision("HOLD", symbol, "No completed candles supplied")
        candle_time = int(ha[-1]["open_time"])
        previous_time = self._last_candle_time.get(symbol)
        if previous_time is not None and candle_time <= previous_time:
            return Decision("HOLD", symbol, "This candle is duplicate or older than the last evaluated candle")
        self._last_candle_time[symbol] = candle_time

        if position_side is not None:
            return Decision("HOLD", symbol, "Position remains open; HA flips do not reverse it")
        if len(ha) < 2:
            return Decision("HOLD", symbol, "Need at least two completed candles")

        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        until = self._cooldown_until.get(symbol)
        if until and current < until:
            return Decision("HOLD", symbol, f"TP cooldown active until {until.isoformat()}")
        if until and current >= until:
            self._cooldown_until.pop(symbol, None)

        colours = [candle["colour"] for candle in ha if candle["colour"] != "DOJI"]
        if len(colours) < 2:
            return Decision("HOLD", symbol, "Need two non-DOJI HA candles to identify a flip")
        old_colour, new_colour = colours[-2], colours[-1]
        if old_colour == "RED" and new_colour == "GREEN":
            return Decision("OPEN_LONG", symbol, "Confirmed closed Heikin-Ashi flip RED to GREEN", "LONG")
        if old_colour == "GREEN" and new_colour == "RED":
            return Decision("OPEN_SHORT", symbol, "Confirmed closed Heikin-Ashi flip GREEN to RED", "SHORT")
        return Decision("HOLD", symbol, f"No actionable HA colour flip ({old_colour} to {new_colour})")
