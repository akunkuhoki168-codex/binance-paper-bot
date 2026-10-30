"""Binance USDⓈ-M Futures paper-trading bot entry point.

Uses the SQLite-backed PaperEngine. All execution is simulated.
Public market data only — no API keys or live orders.
"""
from __future__ import annotations

import os
import signal
import time
from pathlib import Path

from dotenv import load_dotenv

from paper_engine.engine import PaperEngine, PaperEngineSettings

load_dotenv()

DB_PATH = os.getenv("DB_PATH", "data/paper_engine.sqlite3")
STARTING = float(os.getenv("STARTING_BALANCE_USDT", "1000"))
MAX_POS = int(os.getenv("MAX_POSITIONS", "5"))
LEVERAGE = int(os.getenv("LEVERAGE", "10"))
FEE = float(os.getenv("TAKER_FEE_RATE", "0.0005"))
TP = float(os.getenv("TAKE_PROFIT_USDT", "10"))
SL = float(os.getenv("STOP_LOSS_USDT", "5"))


def main() -> None:
    print("BINANCE USDⓈ-M FUTURES PAPER BOT")
    print("PAPER TRADING ONLY — no live orders, no API keys")
    print(f"Database: {DB_PATH}")

    settings = PaperEngineSettings(
        initial_capital=STARTING,
        max_positions=MAX_POS,
        leverage=LEVERAGE,
        taker_fee_rate=FEE,
        take_profit_usdt=TP,
        stop_loss_usdt=SL,
    )
    engine = PaperEngine(DB_PATH, settings)

    stop = False

    def _handle_signal(*_):
        nonlocal stop
        stop = True
        print("\nShutdown signal received.")

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    # Simple heartbeat loop. In a full deployment the market-data feed
    # (market_data.websocket or REST mark prices) calls engine.evaluate_exit
    # and strategy decisions call engine.open_position.
    print("Engine ready. Positions restored from SQLite if present.")
    print("Bot state is controlled via the dashboard (or bot_control table).")
    print("Press Ctrl+C to stop this process.")

    try:
        while not stop:
            state = engine.get_bot_state()
            if state == "STOPPING":
                print("STOP requested — engine will idle after positions are closed by dashboard.")
                engine.set_bot_state("IDLE")
            # Real market-data loop would live here.
            time.sleep(2)
            snap = engine.snapshot()
            print(f"[{state}] equity={snap['equity']:.2f} positions={snap['active_positions']}")
    finally:
        engine.close()
        print("Paper bot process exited. Database preserved.")


if __name__ == "__main__":
    main()
