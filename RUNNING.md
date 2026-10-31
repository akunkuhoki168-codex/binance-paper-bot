# Run the Binance Futures Paper Bot

**Paper trading only.** The application uses unauthenticated public Binance USDⓈ-M Futures market-data endpoints. It does not submit exchange orders and does not require API keys.

## Setup

```bash
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
```

## Start the runtime

```bash
python main.py
```

At startup, the runtime fetches completed historical candles for each configured symbol, loads persisted SQLite positions, starts the public kline WebSocket feed, and refreshes public mark prices.

Default symbols: BTCUSDT, ETHUSDT, BNBUSDT, SOLUSDT, XRPUSDT.
Default strategy interval: 15m.

## Start the dashboard

In a second terminal:

```bash
streamlit run dashboard.py
```

Open http://localhost:8501. Keep the `main.py` process running. The dashboard writes control states to the same SQLite database; it does not launch or restart OS processes.

## Controls

| Control | Behaviour |
|---|---|
| **START / RESUME** | Enables new entries from newly completed HA flip candles. |
| **PAUSE** | Blocks new entries; existing positions continue to be monitored for net-PnL TP/SL. |
| **RESTART SIGNALS** | Sets the state to RUNNING and resumes signal processing. It does not restart the process. |
| **FORCE CLOSE ALL** | Requires confirmation; fetches public mark prices and attempts to close all open paper positions. |
| **STOP BOT** | Requests STOPPING. Runtime closes positions at valid public marks, verifies zero positions remain, sets IDLE, and exits. If a mark is unavailable, shutdown remains pending and retries. |

## Environment variables

- `DB_PATH`: shared SQLite database path.
- `FUTURES_SYMBOLS`: comma-separated symbols, e.g. `BTCUSDT,ETHUSDT`.
- `STRATEGY_INTERVAL`: `15m`, `1h`, or `4h`.
- `SEED_CANDLES`: historical completed candles per symbol (2–1500).
- `MARK_POLL_SECONDS`: mark-price polling interval.
- `STARTING_BALANCE_USDT`, `MAX_POSITIONS`, `LEVERAGE`, `TAKER_FEE_RATE`, `TAKE_PROFIT_USDT`, `STOP_LOSS_USDT`: paper-account configuration.

Set the same `DB_PATH` for the runtime and dashboard.
Type each command and press Enter:

```text
price
status
buy 100
status
symbol ETH
price
backtest BTCUSDT 1h 200
status
quit
```

- `price` retrieves the latest public price for the current symbol.
- `buy 100` simulates spending 100 USDT from your virtual balance.
- `sell 0.001` simulates selling 0.001 of the current base asset (if you hold enough).
- `symbol ETH` (or `symbol ETHUSDT`) switches the active market.
- `backtest [symbol] [interval] [candles]` runs a simple SMA crossover backtest.
- `status` displays the simulated portfolio.
- `quit` exits the program.

## Recovery and limitations

- SQLite preserves wallet, open positions, trades, and cooldowns across process restarts.
- If the runtime crashes, restart `python main.py`; open positions are recovered and managed after the runtime reconnects.
- Strategy cooldown/last-candle in-memory state is rebuilt from fresh market data, not separately persisted.
- Simulated fills use current mark prices and do not model order-book depth, partial fills, slippage, liquidation mechanics, or exact funding settlement schedules.
- The dashboard requires Streamlit reruns/refreshes to display updated state.
- This is a manual paper-trading simulator with an optional historical backtest mode.
- It never sends real buy or sell orders and does not need Binance API keys.
- Your simulated balance and trade history are saved in `paper_state.json` in the folder where you run the program. Keep that file if you want to preserve your paper portfolio.
- The bot needs an internet connection to retrieve public market prices and historical data.
- Do not upload passwords, API keys, or secret credentials to GitHub.
