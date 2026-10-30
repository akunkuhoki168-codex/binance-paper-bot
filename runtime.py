"""Live public-data runtime for the SQLite Futures paper engine.

Only public Binance USDⓈ-M endpoints are used. Every trade is simulated locally.
"""
from __future__ import annotations

import os
import threading
import time
from typing import Iterable

import requests

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
                if price > 0:
                    updated[symbol] = price
        with self._lock:
            self._marks.update(updated)
            return dict(self._marks)

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
            with self._lock:
                mark = self._marks.get(candle.symbol, candle.close)
            try:
                self.engine.open_position(candle.symbol, decision.side, mark)
                print(f" PAPER OPEN {candle.symbol} {decision.side} @ {mark:.8g}")
            except ValueError as exc:
                self.last_error = f"{candle.symbol}: {exc}"

    def _manage_positions(self) -> None:
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
                print(f" PAPER TP {symbol} @ {mark:.8g}; net={net:.4f} USDT")
            elif decision.action == "CLOSE_AND_REVERSE":
                self.engine.close_position(symbol, mark, reason="STOP_LOSS")
                try:
                    self.engine.open_position(symbol, decision.side, mark)
                    print(f" PAPER SL-REVERSE {symbol} {decision.side} @ {mark:.8g}")
                except ValueError as exc:
                    self.last_error = f"{symbol}: stopped flat; reversal skipped: {exc}"

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
                    if self._close_all_and_verify():
                        self.engine.set_bot_state("IDLE")
                        self._stop.set()
                        break
                elif state in ("RUNNING", "PAUSED"):
                    try:
                        self._manage_positions()
                    except (requests.RequestException, ValueError, KeyError) as exc:
                        self.last_error = str(exc)
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
