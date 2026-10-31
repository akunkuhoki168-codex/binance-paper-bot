"""Public Binance USDⓈ-M funding-rate history client (no credentials)."""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any

import requests

REST_BASE = "https://fapi.binance.com"


@dataclass(frozen=True)
class FundingEvent:
    symbol: str
    funding_time: int
    funding_rate: float
    mark_price: float


class BinanceFundingClient:
    """Fetch settled funding events from the public funding-rate endpoint."""

    def __init__(self, session: requests.Session | None = None, timeout: float = 10.0):
        self.session = session or requests.Session()
        self.timeout = timeout

    def funding_events(self, symbol: str, start_time: int, end_time: int | None = None, limit: int = 1000) -> list[FundingEvent]:
        if not symbol or symbol != symbol.upper():
            raise ValueError("symbol must be uppercase")
        if not isinstance(start_time, int) or start_time < 0:
            raise ValueError("start_time must be a non-negative integer")
        if end_time is not None and (not isinstance(end_time, int) or end_time < start_time):
            raise ValueError("end_time must be >= start_time")
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        params: dict[str, Any] = {"symbol": symbol, "startTime": start_time, "limit": limit}
        if end_time is not None:
            params["endTime"] = end_time
        response = self.session.get(f"{REST_BASE}/fapi/v1/fundingRate", params=params, timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise ValueError("Binance funding response must be a list")
        events = []
        for row in payload:
            try:
                event = FundingEvent(str(row["symbol"]).upper(), int(row["fundingTime"]), float(row["fundingRate"]), float(row["markPrice"]))
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("Malformed Binance funding event") from exc
            if event.symbol != symbol or event.funding_time < start_time:
                continue
            if not isfinite(event.funding_rate):
                raise ValueError("Funding rate must be finite")
            if not isfinite(event.mark_price) or event.mark_price <= 0:
                raise ValueError("Funding mark price must be finite and positive")
            events.append(event)
        return sorted(events, key=lambda item: item.funding_time)
