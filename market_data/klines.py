"""Public Binance USDⓈ-M Futures kline data helpers.

This module is deliberately independent of the paper execution engine. It only
fetches public market data, filters completed candles, and maintains a bounded
per-symbol/timeframe candle cache.
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from math import isfinite
from typing import Any, Iterable, Mapping, Sequence

import requests

REST_BASE = "https://fapi.binance.com"
WEBSOCKET_BASE = "wss://fstream.binance.com/stream?streams="
SUPPORTED_INTERVALS = ("15m", "1h", "4h")


@dataclass(frozen=True)
class FuturesCandle:
    symbol: str
    interval: str
    open_time: int
    close_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    quote_volume: float
    trades: int

    def __post_init__(self) -> None:
        if self.interval not in SUPPORTED_INTERVALS:
            raise ValueError(f"unsupported interval: {self.interval}")
        if not self.symbol or self.symbol != self.symbol.upper():
            raise ValueError("symbol must be a non-empty uppercase Binance symbol")
        if self.open_time < 0 or self.close_time < self.open_time:
            raise ValueError("invalid candle timestamps")
        prices = (self.open, self.high, self.low, self.close)
        if not all(isfinite(value) and value > 0 for value in prices):
            raise ValueError("OHLC prices must be finite and positive")
        if self.high < max(self.open, self.close, self.low):
            raise ValueError("high is inconsistent with OHLC values")
        if self.low > min(self.open, self.close, self.high):
            raise ValueError("low is inconsistent with OHLC values")
        if not isfinite(self.volume) or self.volume < 0:
            raise ValueError("volume must be finite and non-negative")
        if not isfinite(self.quote_volume) or self.quote_volume < 0:
            raise ValueError("quote_volume must be finite and non-negative")
        if self.trades < 0:
            raise ValueError("trades must be non-negative")


def parse_rest_kline(symbol: str, interval: str, row: Sequence[Any]) -> FuturesCandle:
    """Parse one Binance REST /fapi/v1/klines row."""
    if len(row) < 9:
        raise ValueError("Binance kline row must contain at least 9 fields")
    return FuturesCandle(
        symbol=symbol.upper(),
        interval=interval,
        open_time=int(row[0]),
        open=float(row[1]),
        high=float(row[2]),
        low=float(row[3]),
        close=float(row[4]),
        volume=float(row[5]),
        close_time=int(row[6]),
        quote_volume=float(row[7]),
        trades=int(row[8]),
    )


def parse_websocket_kline(packet: Mapping[str, Any]) -> tuple[FuturesCandle, bool]:
    """Parse a combined-stream packet and return (candle, is_closed)."""
    data = packet.get("data", packet)
    kline = data.get("k")
    if not isinstance(kline, Mapping):
        raise ValueError("websocket packet does not contain a kline payload")
    symbol = str(kline.get("s", "")).upper()
    interval = str(kline.get("i", ""))
    candle = FuturesCandle(
        symbol=symbol,
        interval=interval,
        open_time=int(kline["t"]),
        close_time=int(kline["T"]),
        open=float(kline["o"]),
        high=float(kline["h"]),
        low=float(kline["l"]),
        close=float(kline["c"]),
        volume=float(kline["v"]),
        quote_volume=float(kline["q"]),
        trades=int(kline["n"]),
    )
    return candle, bool(kline.get("x", False))


def eligible_usdt_perpetuals(
    ranked_base_symbols: Iterable[str],
    exchange_info: Mapping[str, Any],
    limit: int = 30,
) -> list[str]:
    """Intersect ranked base assets with tradable USDT perpetual contracts.

    ranked_base_symbols should be supplied in CoinMarketCap rank order by a
    separate universe provider. This function does not call CoinMarketCap.
    """
    if not isinstance(limit, int) or limit < 1:
        raise ValueError("limit must be a positive integer")
    eligible: dict[str, str] = {}
    for item in exchange_info.get("symbols", []):
        if (
            item.get("status") == "TRADING"
            and item.get("contractType") == "PERPETUAL"
            and item.get("quoteAsset") == "USDT"
        ):
            base = str(item.get("baseAsset", "")).upper()
            symbol = str(item.get("symbol", "")).upper()
            if base and symbol:
                eligible.setdefault(base, symbol)
    selected: list[str] = []
    seen: set[str] = set()
    for base in ranked_base_symbols:
        key = str(base).upper()
        symbol = eligible.get(key)
        if symbol and symbol not in seen:
            selected.append(symbol)
            seen.add(symbol)
        if len(selected) >= limit:
            break
    return selected


def ranked_usdt_perpetuals_by_quote_volume(
    exchange_info: Mapping[str, Any],
    ticker_rows: Iterable[Mapping[str, Any]],
    exclude_top: int = 50,
    limit: int = 200,
) -> list[str]:
    """Rank active USDT perpetuals by 24h quote volume and skip the busiest N."""
    if not isinstance(exclude_top, int) or exclude_top < 0:
        raise ValueError("exclude_top must be a non-negative integer")
    if not isinstance(limit, int) or limit < 1:
        raise ValueError("limit must be a positive integer")
    eligible = {
        str(item.get("symbol", "")).upper()
        for item in exchange_info.get("symbols", [])
        if item.get("status") == "TRADING"
        and item.get("contractType") == "PERPETUAL"
        and item.get("quoteAsset") == "USDT"
        and item.get("symbol")
    }
    volumes: dict[str, float] = {}
    for row in ticker_rows:
        symbol = str(row.get("symbol", "")).upper()
        if symbol not in eligible:
            continue
        try:
            volume = float(row.get("quoteVolume", 0))
        except (TypeError, ValueError):
            continue
        if isfinite(volume) and volume >= 0:
            volumes[symbol] = volume
    ordered = sorted(volumes, key=lambda symbol: (-volumes[symbol], symbol))
    return ordered[exclude_top:exclude_top + limit]


def websocket_stream_names(symbols: Iterable[str], intervals: Iterable[str]) -> list[str]:
    """Build Binance combined-stream names for every symbol/timeframe pair."""
    normalized_symbols = sorted({str(s).lower() for s in symbols if str(s)})
    normalized_intervals = list(dict.fromkeys(intervals))
    if any(interval not in SUPPORTED_INTERVALS for interval in normalized_intervals):
        raise ValueError("intervals must be selected from 15m, 1h, and 4h")
    return [
        f"{symbol}@kline_{interval}"
        for symbol in normalized_symbols
        for interval in normalized_intervals
    ]


class BinanceFuturesKlineClient:
    """Unauthenticated REST client for public USDⓈ-M Futures market data."""

    def __init__(self, session: requests.Session | None = None, timeout: float = 10.0):
        self.session = session or requests.Session()
        self.timeout = timeout

    def exchange_info(self) -> Mapping[str, Any]:
        response = self.session.get(
            f"{REST_BASE}/fapi/v1/exchangeInfo", timeout=self.timeout
        )
        response.raise_for_status()
        return response.json()

    def ticker_24hr(self) -> list[Mapping[str, Any]]:
        """Fetch the public 24-hour ticker snapshot for USDⓈ-M Futures."""
        response = self.session.get(f"{REST_BASE}/fapi/v1/ticker/24hr", timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise ValueError("Binance 24-hour ticker response must be a list")
        return payload

    def closed_klines(
        self,
        symbol: str,
        interval: str,
        limit: int = 500,
        now_ms: int | None = None,
    ) -> list[FuturesCandle]:
        if interval not in SUPPORTED_INTERVALS:
            raise ValueError("interval must be 15m, 1h, or 4h")
        if not 1 <= limit <= 1500:
            raise ValueError("limit must be between 1 and 1500")
        response = self.session.get(
            f"{REST_BASE}/fapi/v1/klines",
            params={"symbol": symbol.upper(), "interval": interval, "limit": limit},
            timeout=self.timeout,
        )
        response.raise_for_status()
        current_ms = now_ms
        if current_ms is None:
            import time
            current_ms = int(time.time() * 1000)
        candles = [parse_rest_kline(symbol, interval, row) for row in response.json()]
        return [candle for candle in candles if candle.close_time < current_ms]


class KlineDataManager:
    """Bounded, deduplicated cache of completed candles by symbol and interval."""

    def __init__(self, max_candles: int = 600):
        if not isinstance(max_candles, int) or max_candles < 2:
            raise ValueError("max_candles must be an integer >= 2")
        self.max_candles = max_candles
        self._candles: dict[tuple[str, str], deque[FuturesCandle]] = defaultdict(
            lambda: deque(maxlen=self.max_candles)
        )

    def seed(self, candles: Iterable[FuturesCandle]) -> int:
        accepted = 0
        for candle in candles:
            accepted += int(self.add_closed(candle))
        return accepted

    def add_websocket_packet(self, packet: Mapping[str, Any]) -> bool:
        candle, is_closed = parse_websocket_kline(packet)
        if not is_closed:
            return False
        return self.add_closed(candle)

    def add_closed(self, candle: FuturesCandle) -> bool:
        if not isinstance(candle, FuturesCandle):
            raise TypeError("candle must be a FuturesCandle")
        key = (candle.symbol, candle.interval)
        history = self._candles[key]
        if history and candle.open_time <= history[-1].open_time:
            return False
        history.append(candle)
        return True

    def get(self, symbol: str, interval: str) -> list[FuturesCandle]:
        if interval not in SUPPORTED_INTERVALS:
            raise ValueError("unsupported interval")
        return list(self._candles.get((symbol.upper(), interval), ()))

    def latest(self, symbol: str, interval: str) -> FuturesCandle | None:
        history = self._candles.get((symbol.upper(), interval))
        return history[-1] if history else None
