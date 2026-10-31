# Binance Futures Paper Trading Bot

SQLite-backed multi-position paper trader for Binance USDⓈ-M Futures.

**PAPER TRADING ONLY.** No Binance API keys, signed requests, or live orders. Binance public REST is used for startup discovery and candle history. Live mark prices and closed candles arrive through public WebSocket streams. Every order is simulated locally.

## Market universe

At startup, the bot gets Binance USDⓈ-M exchange metadata and the public 24-hour ticker snapshot. It considers only active **USDT-quoted perpetual contracts**, sorts them by 24-hour quote volume (USDT), excludes the **50 highest-volume** contracts, then selects up to **200** contracts after those.

This is a volume ranking, **not market-cap ranking**. The selection is recalculated each time the bot starts. Selected symbols receive live candle and mark-price updates through Binance public WebSocket streams. If discovery fails or returns no eligible symbols, the bot stops instead of using a fixed fallback list.

No CoinMarketCap account or API key is required. No Binance API credentials are required.

## Architecture

- `paper_engine/engine.py` — persistent multi-position LONG/SHORT isolated-margin simulator.
- `market_data/universe.py` — Binance 24-hour volume-based universe selection.
- `market_data/klines.py` and `market_data/websocket.py` — public Binance candle history, ticker snapshot, and reconnecting kline/mark-price WebSocket feeds.
- `strategies/HA_flips.py` — Heikin-Ashi colour-flip entries and net-PnL TP/SL decisions.
- `runtime.py` — market data, strategy, paper execution, WebSocket mark-price monitoring, and graceful STOP.
- `main.py` — selects the volume-ranked universe and starts the runtime.
- `dashboard.py` — Streamlit monitoring and SQLite controls.

## Quick start

```bash
python -m venv .venv
```

Activate the environment:

```bash
# Windows
.venv\\Scripts\\activate

# Linux/macOS
source .venv/bin/activate
```

Install dependencies and start the bot:

```bash
pip install -r requirements.txt
python main.py
```

In a second terminal:

```bash
streamlit run dashboard.py
```

Open http://localhost:8501. The runtime must remain running in its own terminal.

## Configuration

| Variable | Default | Purpose |
|---|---:|---|
| `EXCLUDE_TOP_VOLUME` | `50` | Highest-volume USDT perpetual contracts to skip |
| `MAX_UNIVERSE_SYMBOLS` | `200` | Number of next contracts to monitor |
| `DB_PATH` | `data/paper_engine.sqlite3` | Shared SQLite database |
| `STRATEGY_INTERVAL` | `15m` | HA interval: 15m, 1h, or 4h |
| `SEED_CANDLES` | `120` | Completed candles fetched per symbol at startup |
| `MARK_POLL_SECONDS` | `3` | Runtime management loop interval; live marks arrive by WebSocket |
| `STARTING_BALANCE_USDT` | `1000` | Initial paper wallet |
| `MAX_POSITIONS` | `5` | Maximum simultaneous positions |
| `LEVERAGE` | `10` | Simulated leverage |
| `TAKER_FEE_RATE` | `0.0005` | Simulated taker fee |
| `TAKE_PROFIT_USDT` | `10` | Net PnL take-profit threshold |
| `STOP_LOSS_USDT` | `5` | Net PnL stop-loss threshold |
| `DAILY_LOSS_LIMIT_USDT` | `50` | Pause new entries after this much net realized daily loss; 0 disables |

## Dashboard controls

- **START / RESUME** — enables new HA entries.
- **PAUSE** — blocks new entries while continuing to manage open positions.
- **RESTART SIGNALS** — resumes runtime state; it does not restart the OS process.
- **FORCE CLOSE ALL** — requires a second click and uses current public mark prices.
- **STOP BOT** — requests graceful shutdown. The runtime closes positions using fresh WebSocket marks and verifies no positions remain.

## Persistence and safety

Positions, wallet, trade history, fees, funding, cooldowns, and bot state are stored in SQLite. Settled funding-rate events are polled from Binance's public funding-history endpoint and applied once per funding timestamp.

The daily loss guard sums net realized PnL from closed trades since 00:00 UTC. It does not include unrealized losses, so it is a limited circuit breaker rather than a complete account-equity drawdown control.

This is a paper simulator: fills, slippage, funding timing, liquidation, and exchange execution are not exact reproductions of Binance. Never commit API keys, secrets, or `.env` files. Live trading is not implemented.
