"""Binance USDⓈ-M Futures paper-trading bot entry point."""
from __future__ import annotations

import os
import signal

from dotenv import load_dotenv

from market_data.klines import BinanceFuturesKlineClient
from market_data.universe import select_volume_ranked_symbols
from paper_engine.engine import PaperEngine, PaperEngineSettings
from runtime import FuturesPaperRuntime

load_dotenv()

DB_PATH = os.getenv("DB_PATH", "data/paper_engine.sqlite3")
STARTING = float(os.getenv("STARTING_BALANCE_USDT", "1000"))
MAX_POS = int(os.getenv("MAX_POSITIONS", "5"))
LEVERAGE = int(os.getenv("LEVERAGE", "10"))
FEE = float(os.getenv("TAKER_FEE_RATE", "0.0005"))
TP = float(os.getenv("TAKE_PROFIT_USDT", "10"))
SL = float(os.getenv("STOP_LOSS_USDT", "5"))
EXCLUDE_TOP_VOLUME = int(os.getenv("EXCLUDE_TOP_VOLUME", "50"))
MAX_UNIVERSE_SYMBOLS = int(os.getenv("MAX_UNIVERSE_SYMBOLS", "200"))
INTERVAL = os.getenv("STRATEGY_INTERVAL", "15m")


def select_futures_universe() -> tuple[str, ...]:
    """Select active USDT perpetuals after excluding the highest-volume N."""
    client = BinanceFuturesKlineClient()
    symbols = select_volume_ranked_symbols(
        client.exchange_info(),
        client.ticker_24hr(),
        exclude_top=EXCLUDE_TOP_VOLUME,
        limit=MAX_UNIVERSE_SYMBOLS,
    )
    if not symbols:
        raise RuntimeError(
            f"No USDT perpetuals remain after excluding the top {EXCLUDE_TOP_VOLUME} by volume."
        )
    return tuple(symbols)


def main() -> None:
    print("BINANCE USDⓈ-M FUTURES PAPER BOT")
    print("PAPER TRADING ONLY — public Binance data; simulated orders only")
    print(f"Database: {DB_PATH}")
    symbols = select_futures_universe()
    print(
        f"Excluded top {EXCLUDE_TOP_VOLUME} by 24h quote volume; "
        f"selected {len(symbols)} USDT perpetuals."
    )
    print(f"Symbols: {', '.join(symbols)} | HA timeframe: {INTERVAL}")
    settings = PaperEngineSettings(
        initial_capital=STARTING,
        max_positions=MAX_POS,
        leverage=LEVERAGE,
        taker_fee_rate=FEE,
        take_profit_usdt=TP,
        stop_loss_usdt=SL,
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
            symbols=symbols,
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
