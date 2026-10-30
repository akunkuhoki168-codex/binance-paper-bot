# Binance Futures Paper Trading Bot

SQLite-backed multi-position paper trader for Binance USDⓈ-M Futures.

**PAPER TRADING ONLY.** No API keys, no signed requests, no live orders. Uses public market-data endpoints and WebSocket streams only.

## Architecture

- `paper_engine/engine.py` — persistent multi-position (LONG/SHORT) isolated-margin engine with TP/SL, SL-reversal, cooldowns, and bot lifecycle state.
- `market_data/` — public kline REST + reconnecting WebSocket clients.
- `strategies/HA_flips.py` — independent Heikin-Ashi colour-flip decision module (not yet wired into the live loop).
- `main.py` — entry point that loads the PaperEngine and polls bot state.
- `dashboard.py` — Streamlit UI with START / PAUSE / RESTART / FORCE CLOSE ALL / STOP controls that write to the SQLite control table.

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate   # or .venv\Scripts\activate on Windows
pip install -r requirements.txt
cp .env.example .env
python main.py
```

In another terminal:

```bash
streamlit run dashboard.py
```

Open http://localhost:8501.

## Controls

See `RUNNING.md` for full details. Summary:

- **START** — enable new entries
- **PAUSE** — block new entries, continue managing open positions
- **RESTART** — return to RUNNING (state preserved)
- **FORCE CLOSE ALL** — close every paper position at latest marks (confirmation required)
- **STOP** — request shutdown; returns to IDLE after positions are closed

## Recovery

Positions, wallet, trade history, and bot state are stored in `data/paper_engine.sqlite3` and restored on restart.

## Safety

- Never submits real orders.
- No authenticated trading endpoints.
- No secrets or tokens required.
