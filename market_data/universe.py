"""Binance-only Futures universe selection by 24-hour quote volume."""
from __future__ import annotations

from typing import Any, Mapping

from .klines import ranked_usdt_perpetuals_by_quote_volume


def select_volume_ranked_symbols(
    exchange_info: Mapping[str, Any],
    ticker_rows: list[Mapping[str, Any]],
    exclude_top: int = 50,
    limit: int = 200,
) -> list[str]:
    """Select active USDT perpetuals after skipping the highest-volume N."""
    return ranked_usdt_perpetuals_by_quote_volume(
        exchange_info, ticker_rows, exclude_top=exclude_top, limit=limit
    )
