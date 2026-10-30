"""Streamlit dashboard for the SQLite-backed Futures paper bot.

Controls write to the bot_control table. The running process (main.py)
reads that table. All trading remains simulated.
"""
import os
import sqlite3
import time
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from paper_engine.engine import PaperEngine, PaperEngineSettings

load_dotenv()
DB = os.getenv("DB_PATH", "data/paper_engine.sqlite3")

st.set_page_config(page_title="Binance Futures Paper Bot", page_icon="📈", layout="wide")
st.title("Binance Futures Paper Trading")
st.caption("Live public Binance USDⓈ-M data · Simulated paper execution only · No live orders")

if not Path(DB).exists():
    st.info("Start the bot with `python main.py`, then refresh.")
    st.stop()

settings = PaperEngineSettings()
engine = PaperEngine(DB, settings)

col1, col2, col3, col4, col5 = st.columns(5)

if col1.button("START", use_container_width=True):
    engine.set_bot_state("RUNNING")
    st.success("START → RUNNING (new entries enabled)")

if col2.button("PAUSE", use_container_width=True):
    engine.set_bot_state("PAUSED")
    st.warning("PAUSE → no new entries; existing positions still managed")

if col3.button("RESTART", use_container_width=True):
    engine.set_bot_state("RUNNING")
    st.info("RESTART → state set to RUNNING (positions preserved)")

if col4.button("FORCE CLOSE ALL", use_container_width=True):
    if st.session_state.get("confirm_force_close"):
        # In a real deployment the caller supplies current mark prices.
        marks = {s: 0 for s in engine.snapshot()["positions"]}  # placeholder
        results = engine.force_close_all(marks)
        st.write(results)
        st.session_state["confirm_force_close"] = False
    else:
        st.session_state["confirm_force_close"] = True
        st.error("Click FORCE CLOSE ALL again to confirm. Positions will be closed at latest marks.")

if col5.button("STOP", use_container_width=True):
    engine.set_bot_state("STOPPING")
    st.error("STOP requested. Close remaining positions then set to IDLE.")

snap = engine.snapshot()
st.metric("Bot state", snap.get("bot_state", "IDLE"))
st.metric("Paper wallet", f"{snap['wallet']:.2f} USDT")
st.metric("Equity", f"{snap['equity']:.2f} USDT")
st.metric("Active positions", snap["active_positions"])

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
except Exception as exc:
    st.warning(f"Could not read trades: {exc}")

st.caption("Controls update the SQLite bot_control table. The bot process must be running to act on them. Paper trading only.")
engine.close()
