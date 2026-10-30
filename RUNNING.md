# Run the Binance Futures Paper Bot

Live public Futures market data; simulated trades only. This project has no live order capability and does not need Binance API keys.

The runtime is the SQLite-backed `paper_engine.engine.PaperEngine`. Positions, wallet, trades, and bot state survive restarts.

## Setup

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # or copy on Windows
```

## Start the bot

```bash
python main.py
```

The process loads any existing positions from `data/paper_engine.sqlite3` and waits for dashboard controls (or the `bot_control` table).

## Dashboard

In a second terminal:

```bash
streamlit run dashboard.py
```

Open http://localhost:8501.

### Controls

| Button | Effect |
|--------|--------|
| **START** | Sets state to RUNNING. New entries allowed. |
| **PAUSE** | Sets state to PAUSED. No new entries; existing positions continue to be monitored for TP/SL. |
| **RESTART** | Sets state back to RUNNING (positions and history preserved). |
| **FORCE CLOSE ALL** | Closes every open paper position at the latest mark prices. Requires confirmation. |
| **STOP** | Requests STOPPING. After positions are closed the process should return to IDLE. Only START reactivates it. |

Controls write to the SQLite `bot_control` table. The running `main.py` process reads that table.

## Restart & recovery

- Open positions, wallet, realized PnL, fees, and cooldowns are stored in SQLite.
- Restarting the process (or the machine) reloads them automatically.
- The bot state (IDLE/RUNNING/PAUSED) is also persisted.

## Limitations

- Paper trading only.
- Full multi-symbol HA strategy + live WebSocket loop is not yet wired into the heartbeat in `main.py` (the engine and market_data modules are ready to be connected).
- Simulated fills use the supplied mark/price; no queue, partial fills, or exact exchange liquidation mechanics.
- Dashboard requires a browser refresh or Streamlit rerun to reflect the latest DB state.

## Troubleshooting

- No database: run `python main.py` once to create it.
- Dashboard shows no data: confirm `DB_PATH` matches between bot and dashboard.
- Controls do nothing: the bot process must be running and polling `bot_control`.
- Reset everything: stop the process and delete `data/paper_engine.sqlite3`.
