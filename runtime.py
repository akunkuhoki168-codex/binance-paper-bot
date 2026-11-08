"""Live public-data runtime for the SQLite Futures paper engine.

Only public Binance USDⓈ-M endpoints are used. Every trade is simulated locally.
"""
from __future__ import annotations

import os
import threading
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
        self._market_data_errors: dict[str, str] = {}
        self._last_mark_poll_error: str | None = None
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
        try:
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
        except (
            requests.RequestException,
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
        ) as exc:
            with self._lock:
                self._last_mark_poll_error = f"{type(exc).__name__}: {exc}"
            raise

        # Return only marks from this response. Cached marks are retained for
        # display/diagnostics but are never substituted for a missing response
        # row when managing an open position.
        with self._lock:
            self._marks.update(updated)
            received_at = time.monotonic()
            self._last_mark_poll_error = None
            for symbol in self.symbols:
                if symbol in updated:
                    self._mark_updated_at[symbol] = received_at
                    self._market_data_errors.pop(symbol, None)
                else:
                    self._market_data_errors[symbol] = (
                        "missing from latest public mark-price response"
                    )
            return dict(updated)

    def _max_mark_age_seconds(self) -> float:
        return max(10.0, self.mark_poll_seconds * 3)

    def market_data_health(self) -> dict[str, dict]:
        """Return per-symbol mark-price freshness without using cached prices."""
        now = time.monotonic()
        max_age = self._max_mark_age_seconds()
        position_symbols = set(self.engine.snapshot()["positions"])
        with self._lock:
            symbols = sorted(
                set(self.symbols)
                | set(self._mark_updated_at)
                | set(self._market_data_errors)
                | position_symbols
            )
            health = {}
            for symbol in symbols:
                updated_at = self._mark_updated_at.get(symbol)
                age = None if updated_at is None else max(0.0, now - updated_at)
                reason = self._market_data_errors.get(symbol)
                stale = (
                    updated_at is None
                    or age is None
                    or age > max_age
                    or reason is not None
                )
                if stale and reason is None:
                    if self._last_mark_poll_error:
                        reason = f"last mark-price poll failed: {self._last_mark_poll_error}"
                    else:
                        reason = "no successful mark-price update within freshness window"
                health[symbol] = {
                    "status": "STALE" if stale else "HEALTHY",
                    "age_seconds": age,
                    "max_age_seconds": max_age,
                    "last_error": reason,
                }
            return health

    def _max_websocket_age_seconds(self) -> float:
        return max(15.0, self.mark_poll_seconds * 5)

    def websocket_health(self) -> dict:
        """Expose WebSocket connection and per-symbol/timeframe freshness."""
        if self._feed is None:
            return {
                "status": "NOT_STARTED",
                "connection_state": "NOT_STARTED",
                "last_error": None,
                "streams": {},
                "max_age_seconds": self._max_websocket_age_seconds(),
            }
        return self._feed.health(self._max_websocket_age_seconds())

    def _monitor_websocket_feed(self) -> None:
        """Force a reconnect when an open WebSocket stops delivering stream data."""
        if self._feed is None:
            return
        health = self.websocket_health()
        if health["status"] == "STALE":
            stale_streams = sorted(
                name for name, item in health["streams"].items()
                if item["status"] == "STALE"
            )
            reason = "stale WebSocket streams: " + ", ".join(stale_streams)
            if self._feed.request_reconnect(reason):
                self.last_error = (
                    "WebSocket feed stale; automatic reconnect requested for "
                    + ", ".join(stale_streams)
                )
        elif (
            health["status"] == "HEALTHY"
            and self.last_error
            and self.last_error.startswith("WebSocket feed stale;")
        ):
            self.last_error = None

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
        if decision.action in ("OPEN_LONG", "OPEN_SHORT") and decision.side:
            symbol_health = self.market_data_health().get(candle.symbol)
            marks_are_fresh = (
                symbol_health is not None
                and symbol_health["status"] == "HEALTHY"
            )
            with self._lock:
                mark = self._marks.get(candle.symbol) if marks_are_fresh else None
            if mark is None:
                self.last_error = (
                    f"{candle.symbol}: entry skipped because no fresh public mark price is available"
                )
                return
            try:
                self.engine.open_position(candle.symbol, decision.side, mark)
                print(f" PAPER OPEN {candle.symbol} {decision.side} @ {mark:.8g}")
            except ValueError as exc:
                self.last_error = f"{candle.symbol}: {exc}"

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
                        print(
                            f" PAPER FUNDING {symbol}: {payment:+.6f} USDT "
                            f"at rate {event.funding_rate:+.6%}"
                        )
            except (requests.RequestException, ValueError, KeyError) as exc:
                # A funding-history outage must never interrupt TP/SL monitoring.
                self.last_error = f"{symbol}: funding update unavailable: {exc}"
        self._last_funding_poll = now

    def _manage_positions(self) -> None:
        self._sync_funding()
        marks = self.refresh_marks()
        positions = self.engine.snapshot()["positions"]
        health = self.market_data_health()
        stale_symbols = []
        for symbol, position in positions.items():
            mark = marks.get(symbol)
            symbol_health = health.get(symbol)
            if (
                mark is None
                or symbol_health is None
                or symbol_health["status"] != "HEALTHY"
            ):
                stale_symbols.append(symbol)
                continue
            result = self.engine.evaluate_exit(symbol, mark, marks=marks)
            if result == "TAKE_PROFIT":
                print(f" PAPER TP {symbol} @ {mark:.8g}")
            elif result == "STOP_LOSS_REVERSED":
                opposite_side = "SHORT" if position["side"] == "LONG" else "LONG"
                print(f" PAPER SL-REVERSE {symbol} {opposite_side} @ {mark:.8g}")
            elif result == "STOP_LOSS":
                self.last_error = (
                    f"{symbol}: stop-loss closed the position; reversal skipped "
                    "because the paper wallet cannot safely fund the replacement"
                )
                print(f" PAPER SL {symbol} @ {mark:.8g}; reversal skipped")

        if stale_symbols:
            stale_message = (
                f"Market data stale/missing for {', '.join(sorted(stale_symbols))}; "
                "TP/SL monitoring skipped for those positions."
            )
            # Preserve a more actionable execution/funding error while the
            # per-symbol health report independently exposes stale market data.
            if not self.last_error or self.last_error.startswith("Market data stale/missing for "):
                self.last_error = stale_message
        elif self.last_error and self.last_error.startswith("Market data stale/missing for "):
            self.last_error = None

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
                        # Daily realized-loss auto-pause was removed by user requirement.
                        # Per-position net-PnL TP/SL remains active in _manage_positions().
                        self._manage_positions()
                    except (requests.RequestException, ValueError, KeyError) as exc:
                        self.last_error = str(exc)
                        print(f"Market-data/position update error: {exc}")
                try:
                    self._monitor_websocket_feed()
                except Exception as exc:
                    self.last_error = f"WebSocket health monitoring error: {exc}"
                self._stop.wait(self.mark_poll_seconds)
        finally:
            self.stop_feed()

    def stop_feed(self) -> None:
        self._stop.set()
        if self._feed:
            self._feed.stop()
        if self._feed_thread and self._feed_thread.is_alive():
            self._feed_thread.join(timeout=5)
