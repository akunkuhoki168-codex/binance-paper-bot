"""Streamlit control and monitoring UI for the Futures paper runtime.

Run main.py separately to keep the runtime active. Dashboard controls communicate
through SQLite; the dashboard does not pretend to spawn or restart processes.
"""
import os
import sqlite3
from pathlib import Path

import pandas as pd
import requests
import streamlit as st
from dotenv import load_dotenv

from paper_engine.engine import PaperEngine, PaperEngineSettings

load_dotenv()
DB = os.getenv("DB_PATH", "data/paper_engine.sqlite3")

st.set_page_config(page_title="Binance Futures Paper Bot", page_icon="📈", layout="wide")
st.title("Binance Futures Paper Trading")
st.caption("Public Binance USDⓈ-M market data · Simulated paper execution only · No live orders")

if not Path(DB).exists():
    st.info("Start the runtime with `python main.py`, then refresh this dashboard.")
    st.stop()

settings = PaperEngineSettings(
    initial_capital=float(os.getenv("STARTING_BALANCE_USDT", "1000")),
    max_positions=int(os.getenv("MAX_POSITIONS", "5")),
    leverage=int(os.getenv("LEVERAGE", "10")),
    taker_fee_rate=float(os.getenv("TAKER_FEE_RATE", "0.0005")),
    take_profit_usdt=float(os.getenv("TAKE_PROFIT_USDT", "10")),
    stop_loss_usdt=float(os.getenv("STOP_LOSS_USDT", "5")),
)
engine = PaperEngine(DB, settings)

col1, col2, col3, col4, col5 = st.columns(5)

if col1.button("START / RESUME", use_container_width=True):
    engine.set_bot_state("RUNNING")
    st.success("RUNNING: new entries enabled; existing positions are managed.")

if col2.button("PAUSE", use_container_width=True):
    engine.set_bot_state("PAUSED")
    st.warning("PAUSED: new entries disabled; existing positions continue to be managed.")

if col3.button("RESTART SIGNALS", use_container_width=True):
    engine.set_bot_state("RUNNING")
    st.info("Runtime resumed from SQLite state. This does not restart the operating-system process.")

if col4.button("FORCE CLOSE ALL", use_container_width=True):
    if st.session_state.get("confirm_force_close"):
        positions = engine.snapshot()["positions"]
        try:
            response = requests.get(
                "https://fapi.binance.com/fapi/v1/premiumIndex", timeout=10
            )
            response.raise_for_status()
            payload = response.json()
            rows = payload if isinstance(payload, list) else [payload]
            wanted = set(positions)
            marks = {
                str(row.get("symbol", "")).upper(): float(row["markPrice"])
                for row in rows
                if str(row.get("symbol", "")).upper() in wanted
                and float(row.get("markPrice", 0)) > 0
            }
            results = engine.force_close_all(marks)
            st.write(results)
            remaining = engine.snapshot()["positions"]
            if remaining:
                st.error(f"Some positions remain open: {', '.join(remaining)}")
            else:
                st.success("All paper positions are closed.")
        except (requests.RequestException, ValueError, KeyError) as exc:
            st.error(f"Could not obtain valid public mark prices: {exc}")
        st.session_state["confirm_force_close"] = False
    else:
        st.session_state["confirm_force_close"] = True
        st.warning("Click FORCE CLOSE ALL again to confirm. Positions will be simulated closed at current public mark prices.")

if col5.button("STOP BOT", use_container_width=True):
    engine.set_bot_state("STOPPING")
    st.error("STOPPING requested. The running main.py process will close positions at public marks, verify none remain, then exit. Keep this dashboard open.")

snap = engine.snapshot()
status_col1, status_col2, status_col3, status_col4 = st.columns(4)
status_col1.metric("Bot state", snap.get("bot_state", "IDLE"))
status_col2.metric("Paper wallet", f"{snap['wallet']:.2f} USDT")
status_col3.metric("Equity (entry marks)", f"{snap['equity']:.2f} USDT")
status_col4.metric("Active positions", snap["active_positions"])

if snap["positions"]:
    st.subheader("Open positions")
    st.dataframe(pd.DataFrame(snap["positions"]).T, use_container_width=True)

st.subheader("Recent trades")
try:
    with sqlite3.connect(DB) as db:
        trades = pd.read_sql_query(
            "SELECT * FROM paper_trades ORDER BY id DESC LIMIT 50", db
        )
    if not trades.empty:
        st.dataframe(trades, use_container_width=True, hide_index=True)
    else:
        st.info("No trades yet.")
except (sqlite3.Error, pd.errors.DatabaseError) as exc:
    st.warning(f"Could not read trades: {exc}")

st.caption("The runtime must be started separately with python main.py. Dashboard controls update SQLite state. Paper trading only.")
engine.close()
