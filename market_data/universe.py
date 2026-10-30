"""CoinMarketCap Top-N base-asset provider.

Requires a CoinMarketCap API key supplied by the caller or CMC_API_KEY env var.
The key is sent only to CoinMarketCap and is never used for Binance.
"""
from __future__ import annotations

import os
from typing import Any, Mapping

import requests

CMC_LISTINGS_URL = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/listings/latest"


class CoinMarketCapUniverse:
    def __init__(
        self,
        api_key: str | None = None,
        session: requests.Session | None = None,
        timeout: float = 10.0,
    ):
        self.api_key = api_key or os.getenv("CMC_API_KEY")
        if not self.api_key:
            raise ValueError("CMC_API_KEY is required to retrieve CoinMarketCap rankings")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self.session = session or requests.Session()
        self.timeout = timeout

    def top_base_symbols(self, limit: int = 50) -> list[str]:
        if not isinstance(limit, int) or not 1 <= limit <= 5000:
            raise ValueError("limit must be between 1 and 5000")
        response = self.session.get(
            CMC_LISTINGS_URL,
            headers={"X-CMC_PRO_API_KEY": self.api_key},
            params={"start": 1, "limit": limit, "convert": "USD"},
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload: Mapping[str, Any] = response.json()
        rows = payload.get("data")
        if not isinstance(rows, list):
            raise ValueError("CoinMarketCap response has no listings data")
        symbols: list[str] = []
        seen: set[str] = set()
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            symbol = str(row.get("symbol", "")).strip().upper()
            if symbol and symbol not in seen:
                symbols.append(symbol)
                seen.add(symbol)
        return symbols
