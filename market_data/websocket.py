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
        self._state_lock = threading.RLock()
        self._connection_state = "DISCONNECTED"
        self._connected_at: float | None = None
        self._last_message_at: dict[tuple[str, str], float] = {}
        self._last_error: str | None = None
        self._last_close_code = None
        self._last_close_reason: str | None = None
        self._connection_count = 0
        self._stale_reconnect_count = 0

    @property
    def url(self) -> str:
        return WEBSOCKET_BASE + "/".join(self.streams)

    def stop(self) -> None:
        self._stop.set()
        with self._state_lock:
            self._connection_state = "STOPPED"
            app = self._app
        if app is not None:
            app.close()

    def _on_open(self, _ws) -> None:
        with self._state_lock:
            if self._stop.is_set():
                self._connection_state = "STOPPED"
                return
            self._connection_state = "CONNECTED"
            self._connected_at = time.monotonic()
            self._connection_count += 1
            self._last_error = None

    def _on_error(self, _ws, error) -> None:
        with self._state_lock:
            self._last_error = f"{type(error).__name__}: {error}"
            self._connection_state = (
                "STOPPED" if self._stop.is_set() else "DISCONNECTED"
            )

    def _on_close(self, _ws, close_status_code=None, close_msg=None) -> None:
        with self._state_lock:
            self._last_close_code = close_status_code
            self._last_close_reason = (
                close_msg.decode("utf-8", errors="replace")
                if isinstance(close_msg, bytes)
                else str(close_msg) if close_msg is not None else None
            )
            self._connection_state = (
                "STOPPED" if self._stop.is_set() else "DISCONNECTED"
            )

    def health(self, max_age_seconds: float = 15.0) -> dict:
        """Return connection and per-symbol/timeframe feed freshness."""
        if not isinstance(max_age_seconds, (int, float)) or max_age_seconds <= 0:
            raise ValueError("max_age_seconds must be positive")
        now = time.monotonic()
        with self._state_lock:
            state = self._connection_state
            streams = {}
            for stream in self.streams:
                symbol, interval = stream.split("@kline_", 1)
                last_seen = self._last_message_at.get((symbol.upper(), interval))
                reference = last_seen if last_seen is not None else self._connected_at
                age = None if reference is None else max(0.0, now - reference)
                if self._stop.is_set():
                    stream_status = "STOPPED"
                elif state == "CONNECTED":
                    stream_status = (
                        "STALE" if age is None or age > max_age_seconds else "HEALTHY"
                    )
                else:
                    stream_status = state
                streams[f"{symbol.upper()}:{interval}"] = {
                    "status": stream_status,
                    "age_seconds": age,
                    "max_age_seconds": float(max_age_seconds),
                }

            if self._stop.is_set():
                status = "STOPPED"
            elif state == "CONNECTED":
                status = (
                    "STALE"
                    if any(item["status"] == "STALE" for item in streams.values())
                    else "HEALTHY"
                )
            else:
                status = state

            return {
                "status": status,
                "connection_state": "STOPPED" if self._stop.is_set() else state,
                "last_error": self._last_error,
                "last_close_code": self._last_close_code,
                "last_close_reason": self._last_close_reason,
                "reconnect_count": max(0, self._connection_count - 1),
                "stale_reconnect_count": self._stale_reconnect_count,
                "streams": streams,
            }

    def request_reconnect(self, reason: str = "feed freshness threshold exceeded") -> bool:
        """Close a stale connection so run_forever() enters its reconnect loop."""
        with self._state_lock:
            if (
                self._stop.is_set()
                or self._connection_state != "CONNECTED"
                or self._app is None
            ):
                return False
            app = self._app
            self._connection_state = "RECONNECTING"
            self._stale_reconnect_count += 1
            self._last_error = str(reason)
        app.close()
        return True

    def _on_message(self, _ws, message: str) -> None:
        try:
            packet = json.loads(message)
            candle, closed = self.manager_packet(packet)
            with self._state_lock:
                self._last_message_at[(candle.symbol, candle.interval)] = time.monotonic()
            if closed and self.on_candle is not None:
                try:
                    self.on_candle(candle)
                except Exception as exc:
                    # A strategy/engine callback failure must not kill the
                    # market-data receive loop. Keep the feed alive and report it.
                    print(f"Closed-candle callback error: {exc}")
        except (ValueError, TypeError, KeyError, AttributeError):
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
            with self._state_lock:
                self._connection_state = "CONNECTING"
            app = websocket.WebSocketApp(
                self.url,
                on_open=self._on_open,
                on_message=self._on_message,
                on_error=self._on_error,
                on_close=self._on_close,
            )
            with self._state_lock:
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
                with self._state_lock:
                    self._app = None
                    if self._stop.is_set():
                        self._connection_state = "STOPPED"
                    elif self._connection_state != "DISCONNECTED":
                        self._connection_state = "DISCONNECTED"
            if not self._stop.is_set():
                self._stop.wait(self.reconnect_seconds)
