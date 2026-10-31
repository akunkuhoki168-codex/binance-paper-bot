"""Live public-data runtime for the SQLite Futures paper engine.

Only public Binance USDⓈ-M endpoints are used. Every trade is simulated locally.
"""
from __future__ import annotations

import os
import threading
from datetime import datetime, timezone
from math import isfinite
import time
from typing import Iterable

import requests

from market_data.funding import BinanceFundingClient
from market_data.klines import (
    BinanceFuturesKlineClient,
    FuturesCandle,
    KlineDataManager,
)
from market_data.websocket import KlineWebSocket
from paper_engine.engine import PaperEngine
from strategies.HA_flips import (
    Candle,
    HAFlipsSettings,
    HAFlipsStrategy,
)


class FuturesPaperRuntime:
    """Coordinates public market data, HA signals, and paper-only execution."""

    def __init__(
        self,
        engine: PaperEngine,
        symbols: Iterable[str],
        interval: str = "15m",
        seed_limit: int = 120,
        mark_poll_seconds: float = 3.0,
    ):
        self.engine = engine
        self.symbols = tuple(dict.fromkeys(s.strip().upper() for s in symbols if s.strip()))
        if not self.symbols:
            raise ValueError("At least one Binance USDT perpetual symbol is required")
        if interval not in ("15m", "1h", "4h"):
            raise ValueError("interval must be 15m, 1h, or 4h")
        if not 2 <= seed_limit <= 1500:
            raise ValueError("seed_limit must be between 2 and 1500")
        if mark_poll_seconds <= 0:
            raise ValueError("mark_poll_seconds must be positive")

        self.interval = interval
        self.seed_limit = seed_limit
        self.mark_poll_seconds = mark_poll_seconds
        self.client = BinanceFuturesKlineClient()
        self.funding_client = BinanceFundingClient()
        self.funding_poll_seconds = 60.0
        self._last_funding_poll = 0.0
        self.data = KlineDataManager(max_candles=max(seed_limit, 600))
        self.strategy = HAFlipsStrategy(
            HAFlipsSettings(
                take_profit_usdt=engine.settings.take_profit_usdt,
                stop_loss_usdt=engine.settings.stop_loss_usdt,
                timeframe=interval,
                cooldown_minutes=engine.settings.cooldown_minutes,
            )
        )
        self._stop = threading.Event()
        self._feed_thread: threading.Thread | None = None
        self._feed: KlineWebSocket | None = None
        self._marks: dict[str, float] = {}
        self._mark_updated_at: dict[str, float] = {}
        self._lock = threading.RLock()
        self.last_error: str | None = None

    def bootstrap(self) -> None:
        """Seed completed candles before accepting new WebSocket signals."""
        for symbol in self.symbols:
            candles = self.client.closed_klines(
                symbol, self.interval, limit=self.seed_limit
            )
            self.data.seed(candles)
            if len(self.data.get(symbol, self.interval)) < 2:
                raise RuntimeError(f"Not enough completed candles for {symbol}")
        self.refresh_marks()

    def start_feed(self) -> None:
        if self._feed_thread and self._feed_thread.is_alive():
            return
        self._feed = KlineWebSocket(
            manager=self.data,
            symbols=self.symbols,
            intervals=(self.interval,),
            on_candle=self._on_closed_candle,
        )
        self._feed_thread = threading.Thread(
            target=self._feed.run_forever,
            name="binance-public-kline-feed",
            daemon=True,
        )
        self._feed_thread.start()

    def refresh_marks(self) -> dict[str, float]:
        """Fetch public mark prices; never uses authenticated endpoints."""
        response = requests.get(
            "https://fapi.binance.com/fapi/v1/premiumIndex",
            timeout=10,
        )
        response.raise_for_status()
        payload = response.json()
        rows = payload if isinstance(payload, list) else [payload]
        wanted = set(self.symbols)
        updated = {}
        for row in rows:
            symbol = str(row.get("symbol", "")).upper()
            if symbol in wanted:
                price = float(row["markPrice"])
                if isfinite(price) and price > 0:
                    updated[symbol] = price
        # Return only marks from this response. Cached marks may be stale and
        # must never be used to close or manage a position after a data outage.
        with self._lock:
            self._marks.update(updated)
            received_at = time.monotonic()
            for symbol in updated:
                self._mark_updated_at[symbol] = received_at
            self.engine.update_market_prices(updated)
            return dict(updated)

    def _on_closed_candle(self, candle: FuturesCandle) -> None:
        if self.engine.get_bot_state() != "RUNNING":
            return
        history = self.data.get(candle.symbol, candle.interval)
        strategy_candles = [
            Candle(
                open_time=item.open_time,
                open=item.open,
                high=item.high,
                low=item.low,
                close=item.close,
            )
            for item in history
        ]
        position = self.engine.snapshot()["positions"].get(candle.symbol)
        decision = self.strategy.on_closed_candles(
            candle.symbol,
            strategy_candles,
            position_side=position["side"] if position else None,
        )
        decision_reason = getattr(decision, "reason", decision.action)
        candle_time = int(getattr(candle, "open_time", time.time() * 1000))
        self.engine.record_signal(candle.symbol, decision.action, decision.side,
                                  decision_reason, candle_time)
        self.engine.record_event(f"{candle.symbol} {decision.action}: {decision_reason}")
        if decision.action in ("OPEN_LONG", "OPEN_SHORT") and decision.side:
            with self._lock:
                mark_time = self._mark_updated_at.get(candle.symbol, 0.0)
                marks_are_fresh = (
                    mark_time > 0
                    and time.monotonic() - mark_time
                    <= max(10.0, self.mark_poll_seconds * 3)
                )
                mark = self._marks.get(candle.symbol) if marks_are_fresh else None
            if mark is None:
                self.last_error = (
                    f"{candle.symbol}: entry skipped because no fresh public mark price is available"
                )
                self.engine.record_event(self.last_error, "WARNING")
                return
            try:
                self.engine.open_position(candle.symbol, decision.side, mark)
                message = f"PAPER OPEN {candle.symbol} {decision.side} @ {mark:.8g}"
                self.engine.record_event(message, "TRADE")
                print(f" {message}")
            except ValueError as exc:
                self.last_error = f"{candle.symbol}: {exc}"
                self.engine.record_event(self.last_error, "WARNING")

    def _sync_funding(self) -> None:
        """Apply newly settled public funding events once per open position."""
        now = time.monotonic()
        if self._last_funding_poll and now - self._last_funding_poll < self.funding_poll_seconds:
            return
        for symbol in list(self.engine.snapshot()["positions"]):
            start_time = self.engine.last_funding_time(symbol)
            if start_time is None:
                continue
            try:
                events = self.funding_client.funding_events(symbol, start_time)
                for event in events:
                    payment = self.engine.apply_funding(
                        symbol,
                        event.funding_rate,
                        event.mark_price,
                        funding_time_ms=event.funding_time,
                    )
                    if payment:
                        message = (f"PAPER FUNDING {symbol}: {payment:+.6f} USDT "
                                   f"at rate {event.funding_rate:+.6%}")
                        self.engine.record_event(message, "FUNDING")
                        print(f" {message}")
            except (requests.RequestException, ValueError, KeyError) as exc:
                # A funding-history outage must never interrupt TP/SL monitoring.
                self.last_error = f"{symbol}: funding update unavailable: {exc}"
                self.engine.record_event(self.last_error, "WARNING")
        self._last_funding_poll = now

    def _daily_realized_loss_reached(self) -> bool:
        limit = self.engine.settings.daily_loss_limit_usdt
        if limit <= 0:
            return False
        now = datetime.now(timezone.utc)
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        start_ms = int(midnight.timestamp() * 1000)
        realized_today = self.engine.realized_pnl_since(start_ms)
        return realized_today <= -limit

    def _manage_positions(self) -> None:
        self._sync_funding()
        marks = self.refresh_marks()
        positions = self.engine.snapshot()["positions"]
        for symbol, position in positions.items():
            mark = marks.get(symbol)
            if mark is None:
                continue
            net = self.engine.position_net_pnl(symbol, mark)
            decision = self.strategy.evaluate_position(
                symbol, position["side"], net
            )
            if decision.action == "CLOSE_TAKE_PROFIT":
                self.engine.close_position(symbol, mark, reason="TAKE_PROFIT")
                message = f"PAPER TP {symbol} @ {mark:.8g}; net={net:.4f} USDT"
                self.engine.record_event(message, "TRADE")
                print(f" {message}")
            elif decision.action == "CLOSE_AND_REVERSE":
                self.engine.close_position(symbol, mark, reason="STOP_LOSS")
                self.engine.record_event(
                    f"PAPER STOP LOSS {symbol} @ {mark:.8g}; net={net:.4f} USDT", "TRADE"
                )
                try:
                    self.engine.open_position(symbol, decision.side, mark)
                    message = f"PAPER SL-REVERSE {symbol} {decision.side} @ {mark:.8g}"
                    self.engine.record_event(message, "TRADE")
                    print(f" {message}")
                except ValueError as exc:
                    self.last_error = f"{symbol}: stopped flat; reversal skipped: {exc}"
                    self.engine.record_event(self.last_error, "WARNING")

    def _close_all_and_verify(self) -> bool:
        positions = self.engine.snapshot()["positions"]
        if not positions:
            return True
        marks = self.refresh_marks()
        results = self.engine.force_close_all(marks)
        failures = {symbol: result for symbol, result in results.items() if result != "closed"}
        if failures:
            self.last_error = f"STOP close failures: {failures}"
            return False
        return self.engine.snapshot()["active_positions"] == 0

    def run(self) -> None:
        self.bootstrap()
        self.start_feed()
        print(
            f"Public Futures feed connected for {', '.join(self.symbols)} "
            f"({self.interval}); paper execution only."
        )
        try:
            while not self._stop.is_set():
                state = self.engine.get_bot_state()
                if state == "STOPPING":
                    try:
                        if self._close_all_and_verify():
                            self.engine.set_bot_state("IDLE")
                            self._stop.set()
                            break
                    except (requests.RequestException, ValueError, KeyError) as exc:
                        # Do not exit while paper positions remain open just
                        # because public mark-price data is temporarily unavailable.
                        self.last_error = f"STOP close attempt failed: {exc}"
                        print(self.last_error)
                elif state in ("RUNNING", "PAUSED"):
                    try:
                        if state == "RUNNING" and self._daily_realized_loss_reached():
                            self.engine.set_bot_state("PAUSED")
                            self.last_error = (
                                "Daily realized-loss limit reached; new entries paused "
                                "until manually resumed."
                            )
                            self.engine.record_event(self.last_error, "WARNING")
                            print(self.last_error)
                        self._manage_positions()
                    except (requests.RequestException, ValueError, KeyError) as exc:
                        self.last_error = str(exc)
                        self.engine.record_event(self.last_error, "ERROR")
                        print(f"Market-data/position update error: {exc}")
                self._stop.wait(self.mark_poll_seconds)
        finally:
            self.stop_feed()

    def stop_feed(self) -> None:
        self._stop.set()
        if self._feed:
            self._feed.stop()
        if self._feed_thread and self._feed_thread.is_alive():
            self._feed_thread.join(timeout=5)
