"""CoinMarketCap market-cap ranking provider for the Futures universe.

The API key is sent only to CoinMarketCap. Binance remains public-data-only.
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
            raise ValueError(
                "CMC_API_KEY is required. Add your CoinMarketCap API key to the Codespace secret or .env file."
            )
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self.session = session or requests.Session()
        self.timeout = timeout

    def ranked_base_symbols(self, start_rank: int = 51, limit: int = 2000) -> list[str]:
        """Return unique base-asset symbols from the requested market-cap ranks."""
        if not isinstance(start_rank, int) or not 1 <= start_rank <= 5000:
            raise ValueError("start_rank must be between 1 and 5000")
        if not isinstance(limit, int) or not 1 <= limit <= 5000:
            raise ValueError("limit must be between 1 and 5000")
        if start_rank + limit - 1 > 5000:
            raise ValueError("start_rank + limit cannot exceed CoinMarketCap rank 5000")

        response = self.session.get(
            CMC_LISTINGS_URL,
            headers={"X-CMC_PRO_API_KEY": self.api_key},
            params={"start": start_rank, "limit": limit, "convert": "USD"},
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload: Mapping[str, Any] = response.json()
        rows = payload.get("data")
        if not isinstance(rows, list):
            raise ValueError("CoinMarketCap response has no listings data")

        symbols: list[str] = []
        seen: set[str] = set()
        for index, row in enumerate(rows):
            if not isinstance(row, Mapping):
                continue
            rank = row.get("cmc_rank")
            rank = int(rank) if rank is not None else start_rank + index
            if rank < start_rank:
                continue
            symbol = str(row.get("symbol", "")).strip().upper()
            if symbol and symbol not in seen:
                symbols.append(symbol)
                seen.add(symbol)
        return symbols

    def top_base_symbols(self, limit: int = 50) -> list[str]:
        """Backward-compatible helper returning the highest-ranked symbols."""
        return self.ranked_base_symbols(start_rank=1, limit=limit)
