# Binance Futures Paper Trading Bot

SQLite-backed multi-position paper trader for Binance USDⓈ-M Futures.

**PAPER TRADING ONLY.** No API keys, signed requests, or live orders. The runtime uses public market-data endpoints and public WebSocket streams only.

## Architecture

- `paper_engine/engine.py` — persistent multi-position LONG/SHORT isolated-margin execution simulator.
- `market_data/klines.py` and `market_data/websocket.py` — public REST candle bootstrap, completed-candle cache, and reconnecting WebSocket feed.
- `strategies/HA_flips.py` — Heikin-Ashi colour-flip entries and net-PnL TP/SL decisions.
- `runtime.py` — coordinates market data, strategy decisions, paper execution, mark-price monitoring, and graceful STOP.
- `main.py` — starts the runtime and restores SQLite state.
- `dashboard.py` — Streamlit monitoring and SQLite controls.

## Quick start

```bash
python -m venv .venv
```

Activate the environment:

```bash
# Windows
.venv\Scripts\activate

# Linux/macOS
source .venv/bin/activate
```

Install dependencies and start the bot:

```bash
pip install -r requirements.txt
python main.py
```

In a second terminal, start the dashboard:

```bash
streamlit run dashboard.py
```

Open http://localhost:8501.

The runtime must remain running in its own terminal. The dashboard does not spawn or restart operating-system processes.

## Configuration

Optional environment variables:

| Variable | Default | Purpose |
|---|---:|---|
| `DB_PATH` | `data/paper_engine.sqlite3` | Shared SQLite database |
| `FUTURES_SYMBOLS` | `BTCUSDT,ETHUSDT,BNBUSDT,SOLUSDT,XRPUSDT` | Symbols to monitor |
| `STRATEGY_INTERVAL` | `15m` | HA interval: 15m, 1h, or 4h |
| `SEED_CANDLES` | `120` | Completed candles fetched per symbol at startup |
| `MARK_POLL_SECONDS` | `3` | Public mark-price refresh interval |
| `STARTING_BALANCE_USDT` | `1000` | Initial paper wallet |
| `MAX_POSITIONS` | `5` | Maximum simultaneous positions (1–5) |
| `LEVERAGE` | `10` | Simulated leverage (1–10) |
| `TAKER_FEE_RATE` | `0.0005` | Simulated taker fee |
| `TAKE_PROFIT_USDT` | `10` | Net PnL take-profit threshold |
| `STOP_LOSS_USDT` | `5` | Net PnL stop-loss threshold |
| `DAILY_LOSS_LIMIT_USDT` | `50` | Pause new entries after this much net realized loss in one UTC day; set to `0` to disable |

## Dashboard controls

- **START / RESUME** — enables new HA entries.
- **PAUSE** — blocks new entries while continuing to manage open positions.
- **RESTART SIGNALS** — resumes the runtime state; it does not restart the OS process.
- **FORCE CLOSE ALL** — requires a second click and uses current public mark prices.
- **STOP BOT** — requests graceful shutdown. The runtime closes positions using public marks, verifies no positions remain, then exits. If marks are unavailable, it keeps retrying rather than claiming success.

## Persistence and safety

Positions, wallet, trade history, fees, funding, cooldowns, and bot state are stored in SQLite. Restarting the process restores persisted positions. Settled funding-rate events are polled from Binance's public funding-history endpoint and applied once per funding timestamp.

The daily loss guard sums net realized PnL from closed trades since 00:00 UTC. When the configured limit is reached, the runtime pauses new entries while continuing to manage open positions. It does **not** include unrealized losses, so it is a limited circuit breaker rather than a complete account-equity drawdown control.

This is a paper simulator: fills, slippage, funding timing, liquidation, and exchange execution are not exact reproductions of Binance.

Never commit API keys, secrets, or `.env` files. Live trading is not implemented.