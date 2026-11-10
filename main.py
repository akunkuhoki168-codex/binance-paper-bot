"""Binance USDⓈ-M Futures paper-trading bot entry point.

Uses public Futures market data and local SQLite paper execution only.
No Binance credentials or live order endpoints are used.
"""
from __future__ import annotations

import os
import signal

from dotenv import load_dotenv

from paper_engine.config import paper_engine_settings_from_env
from paper_engine.engine import PaperEngine
from runtime import FuturesPaperRuntime

load_dotenv()

DB_PATH = os.getenv("DB_PATH", "data/paper_engine.sqlite3")
SYMBOLS = tuple(
    symbol.strip().upper()
    for symbol in os.getenv(
        "FUTURES_SYMBOLS", "BTCUSDT,ETHUSDT,BNBUSDT,SOLUSDT,XRPUSDT"
    ).split(",")
    if symbol.strip()
)
INTERVAL = os.getenv("STRATEGY_INTERVAL", "15m")


def main() -> None:
    """Start the USDⓈ-M Futures paper-trading runtime."""
    print("BINANCE USDⓈ-M FUTURES PAPER BOT")
    print("PAPER TRADING ONLY — public market data; no live orders or API keys")
    print(f"Database: {DB_PATH}")
    print(f"Symbols: {', '.join(SYMBOLS)} | HA timeframe: {INTERVAL}")

    settings = paper_engine_settings_from_env()
    print(
        f"Paper settings: allocation={settings.allocation_mode}, "
        f"positions={settings.max_positions}, leverage={settings.leverage}x"
    )
    engine = PaperEngine(DB_PATH, settings)

    def _handle_signal(*_args: object) -> None:
        print("\nShutdown requested; asking runtime to close positions safely.")
        engine.set_bot_state("STOPPING")

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    runtime = None
    try:
        runtime = FuturesPaperRuntime(
            engine=engine,
            symbols=SYMBOLS,
            interval=INTERVAL,
            seed_limit=int(os.getenv("SEED_CANDLES", "120")),
            mark_poll_seconds=float(os.getenv("MARK_POLL_SECONDS", "3")),
        )
        runtime.run()
    finally:
        if runtime is not None:
            runtime.stop_feed()
        engine.close()
        print("Paper bot process exited. SQLite database preserved.")


if __name__ == "__main__":
    main()
