"""Reconnectable public Binance Futures kline WebSocket client.

No credentials or order endpoints are used. Callbacks receive only completed
candles accepted by the supplied KlineDataManager.
"""
from __future__ import annotations

import json
import threading
import time
from typing import Callable, Iterable

from .klines import KlineDataManager, WEBSOCKET_BASE, websocket_stream_names


class KlineWebSocket:
    def __init__(
        self,
        manager: KlineDataManager,
        symbols: Iterable[str],
        intervals: Iterable[str] = ("15m", "1h", "4h"),
        on_candle: Callable | None = None,
        reconnect_seconds: float = 3.0,
        ping_interval: int = 20,
        ping_timeout: int = 10,
    ):
        self.manager = manager
        self.streams = websocket_stream_names(symbols, intervals)
        if not self.streams:
            raise ValueError("at least one symbol and interval are required")
        if len(self.streams) > 1024:
            raise ValueError("combined stream count exceeds Binance limit of 1024")
        if reconnect_seconds <= 0 or ping_interval <= 0 or ping_timeout <= 0:
            raise ValueError("reconnect and ping settings must be positive")
        self.on_candle = on_candle
        self.reconnect_seconds = reconnect_seconds
        self.ping_interval = ping_interval
        self.ping_timeout = ping_timeout
        self._stop = threading.Event()
        self._app = None

    @property
    def url(self) -> str:
        return WEBSOCKET_BASE + "/".join(self.streams)

    def stop(self) -> None:
        self._stop.set()
        app = self._app
        if app is not None:
            app.close()

    def _on_message(self, _ws, message: str) -> None:
        try:
            packet = json.loads(message)
            candle, closed = self.manager_packet(packet)
            if closed and self.on_candle is not None:
                try:
                    self.on_candle(candle)
                except Exception as exc:
                    # A strategy/engine callback failure must not kill the
                    # market-data receive loop. Keep the feed alive and report it.
                    print(f"Closed-candle callback error: {exc}")
        except (ValueError, TypeError, KeyError):
            # Ignore malformed market-data messages; the connection remains alive.
            return

    def manager_packet(self, packet):
        from .klines import parse_websocket_kline
        candle, is_closed = parse_websocket_kline(packet)
        if not is_closed:
            return candle, False
        return candle, self.manager.add_closed(candle)

    def run_forever(self) -> None:
        """Run in the calling thread; reconnect until stop() is called."""
        import websocket

        while not self._stop.is_set():
            app = websocket.WebSocketApp(
                self.url,
                on_message=self._on_message,
            )
            self._app = app
            try:
                app.run_forever(
                    ping_interval=self.ping_interval,
                    ping_timeout=self.ping_timeout,
                )
            except Exception:
                # A transient network failure should not terminate the feed loop.
                pass
            finally:
                self._app = None
            if not self._stop.is_set():
                self._stop.wait(self.reconnect_seconds)


class MarkPriceWebSocket:
    """Reconnectable public all-symbol mark-price WebSocket feed."""

    def __init__(self, symbols: Iterable[str], on_prices: Callable[[dict[str, float]], None]):
        self.symbols = {str(symbol).upper() for symbol in symbols}
        if not self.symbols:
            raise ValueError("at least one symbol is required")
        self.on_prices = on_prices
        self._stop = threading.Event()
        self._app = None

    @property
    def url(self) -> str:
        return WEBSOCKET_BASE + "!markPrice@arr@1s"

    def stop(self) -> None:
        self._stop.set()
        if self._app is not None:
            self._app.close()

    def _on_message(self, _ws, message: str) -> None:
        try:
            packet = json.loads(message)
            rows = packet.get("data", packet)
            if not isinstance(rows, list):
                return
            prices = {}
            for row in rows:
                symbol = str(row.get("s", "")).upper()
                if symbol in self.symbols:
                    price = float(row.get("p", 0))
                    if price > 0:
                        prices[symbol] = price
            if prices:
                self.on_prices(prices)
        except (ValueError, TypeError, KeyError, AttributeError):
            return

    def run_forever(self) -> None:
        import websocket
        while not self._stop.is_set():
            app = websocket.WebSocketApp(self.url, on_message=self._on_message)
            self._app = app
            try:
                app.run_forever(ping_interval=20, ping_timeout=10)
            except Exception:
                pass
            finally:
                self._app = None
            if not self._stop.is_set():
                self._stop.wait(3.0)
