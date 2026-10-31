"""Auto-refreshing Streamlit monitoring for the paper-only Futures bot."""
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
st.set_page_config(page_title="Futures Paper Bot", page_icon="📈", layout="wide")
st.title("Binance USDⓈ-M Futures · Paper Monitor")
st.caption("PUBLIC MARKET DATA · SIMULATED EXECUTION ONLY · NO LIVE ORDERS OR API KEYS")

if not Path(DB).exists():
    st.info("Start the runtime with python main.py, then refresh this dashboard.")
    st.stop()

settings = PaperEngineSettings(
    initial_capital=float(os.getenv("STARTING_BALANCE_USDT", "1000")),
    max_positions=int(os.getenv("MAX_POSITIONS", "5")),
    leverage=int(os.getenv("LEVERAGE", "10")),
    taker_fee_rate=float(os.getenv("TAKER_FEE_RATE", "0.0005")),
    take_profit_usdt=float(os.getenv("TAKE_PROFIT_USDT", "10")),
    stop_loss_usdt=float(os.getenv("STOP_LOSS_USDT", "5")),
    daily_loss_limit_usdt=float(os.getenv("DAILY_LOSS_LIMIT_USDT", "50")),
)
@st.cache_resource
def get_engine(db_path: str, engine_settings: PaperEngineSettings) -> PaperEngine:
    """Keep one open SQLite-backed engine across Streamlit reruns/fragments."""
    return PaperEngine(db_path, engine_settings)


engine = get_engine(DB, settings)

# Risk controls are stored in the same SQLite database used by the bot process.
with sqlite3.connect(DB) as risk_db:
    risk_db.execute(
        "CREATE TABLE IF NOT EXISTS risk_settings ("
        "id INTEGER PRIMARY KEY CHECK(id=1), "
        "take_profit_usdt REAL NOT NULL, stop_loss_usdt REAL NOT NULL, "
        "updated_at INTEGER NOT NULL)"
    )
    risk_db.execute(
        "INSERT OR IGNORE INTO risk_settings(id,take_profit_usdt,stop_loss_usdt,updated_at) "
        "VALUES(1,?,?,?)",
        (settings.take_profit_usdt, settings.stop_loss_usdt, int(pd.Timestamp.utcnow().timestamp() * 1000)),
    )
    saved_risk = risk_db.execute(
        "SELECT take_profit_usdt, stop_loss_usdt FROM risk_settings WHERE id=1"
    ).fetchone()

st.subheader("Risk Management · Paper Trading")
current_tp = float(saved_risk[0])
current_sl = float(saved_risk[1])
risk_cols = st.columns(2)
with risk_cols[0]:
    tp_value = st.number_input(
        "Take Profit (USDT per position)",
        min_value=0.00000001,
        value=current_tp,
        step=0.1,
        format="%.8f",
        key="risk_take_profit",
        help="Enter any positive amount, including 0.10 USDT.",
    )
with risk_cols[1]:
    sl_value = st.number_input(
        "Stop Loss (USDT per position)",
        min_value=0.00000001,
        value=current_sl,
        step=0.1,
        format="%.8f",
        key="risk_stop_loss",
        help="Enter any positive amount, including 0.10 USDT.",
    )

rr = tp_value / sl_value
st.metric("Risk / Reward", f"1 : {rr:.2f}")
st.caption(
    f"Potential target: +{tp_value:.8f} USDT · Risk threshold: -{sl_value:.8f} USDT per position. "
    "Values are net PnL thresholds after estimated fees and funding."
)
if st.button("SAVE TP / SL SETTINGS", use_container_width=True):
    with sqlite3.connect(DB) as risk_db:
        risk_db.execute(
            "UPDATE risk_settings SET take_profit_usdt=?, stop_loss_usdt=?, updated_at=? WHERE id=1",
            (float(tp_value), float(sl_value), int(pd.Timestamp.utcnow().timestamp() * 1000)),
        )
    st.success("TP / SL saved. The running paper bot will apply the new values on its next position-management cycle.")
    st.rerun()

controls = st.columns(4)
if controls[0].button("START / RESUME", use_container_width=True):
    engine.set_bot_state("RUNNING")
    engine.record_event("Dashboard requested START / RESUME")
    st.rerun()
if controls[1].button("PAUSE", use_container_width=True):
    engine.set_bot_state("PAUSED")
    engine.record_event("Dashboard requested PAUSE", "WARNING")
    st.rerun()
if controls[2].button("STOP BOT", use_container_width=True):
    engine.set_bot_state("STOPPING")
    engine.record_event("Dashboard requested graceful STOP", "WARNING")
    st.rerun()
if controls[3].button("FORCE CLOSE ALL", use_container_width=True):
    if st.session_state.get("confirm_force_close"):
        try:
            positions = engine.snapshot()["positions"]
            response = requests.get("https://fapi.binance.com/fapi/v1/premiumIndex", timeout=10)
            response.raise_for_status()
            payload = response.json()
            rows = payload if isinstance(payload, list) else [payload]
            wanted = set(positions)
            marks = {str(row.get("symbol", "")).upper(): float(row["markPrice"])
                     for row in rows if str(row.get("symbol", "")).upper() in wanted
                     and float(row.get("markPrice", 0)) > 0}
            results = engine.force_close_all(marks)
            for symbol, result in results.items():
                engine.record_event(f"Force close {symbol}: {result}",
                                    "TRADE" if result == "closed" else "ERROR")
            st.write(results)
            st.session_state["confirm_force_close"] = False
        except (requests.RequestException, ValueError, KeyError) as exc:
            st.error(f"Could not obtain public mark prices: {exc}")
    else:
        st.session_state["confirm_force_close"] = True
        st.warning("Click FORCE CLOSE ALL again to confirm simulated closes.")

state = engine.get_bot_state()
st.subheader(("🟢 " if state == "RUNNING" else "🟠 " if state == "PAUSED" else "🔴 ")
             + f"BOT {state} · PAPER MODE")

@st.fragment(run_every="5s")
def live_monitor():
    snap = engine.snapshot()
    a, b, c, d, e, f = st.columns(6)
    a.metric("Equity", f"{snap['equity']:,.2f} USDT")
    b.metric("Wallet", f"{snap['wallet']:,.2f} USDT")
    c.metric("Realized P&L", f"{snap['realized_pnl']:+,.2f} USDT")
    d.metric("Fees paid", f"{snap['fees_paid']:,.2f} USDT")
    e.metric("Funding net", f"{snap['funding_net']:+,.4f} USDT")
    f.metric("Open positions", snap["active_positions"])

    with sqlite3.connect(DB) as db:
        prices = pd.read_sql_query("SELECT symbol,mark_price,updated_at FROM market_prices ORDER BY symbol", db)
        signals = pd.read_sql_query("SELECT symbol,action,side,reason,created_at FROM strategy_signals ORDER BY id DESC LIMIT 30", db)
        events = pd.read_sql_query("SELECT level,message,created_at FROM runtime_events ORDER BY id DESC LIMIT 30", db)
        trades = pd.read_sql_query("SELECT symbol,side,action,quantity,price,fee,net_pnl,reason,created_at FROM paper_trades ORDER BY id DESC LIMIT 50", db)

    st.divider()
    st.subheader("Live Futures mark prices")
    if prices.empty:
        st.info("Waiting for public mark-price updates.")
    else:
        prices["Updated (UTC)"] = pd.to_datetime(prices["updated_at"], unit="ms", utc=True).dt.strftime("%H:%M:%S")
        st.dataframe(prices[["symbol","mark_price","Updated (UTC)"]], use_container_width=True, hide_index=True)

    left, right = st.columns(2)
    with left:
        st.subheader("Open paper positions")
        if snap["positions"]:
            rows = [{"Symbol":s,"Side":p["side"],"Quantity":p["quantity"],
                     "Entry price":p["entry_price"],"Margin USDT":p["margin"],
                     "Entry fee":p["entry_fee"],"Funding net":p["funding_net"]}
                    for s,p in snap["positions"].items()]
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        else:
            st.info("No open positions.")
    with right:
        st.subheader("Recent strategy signals")
        if signals.empty:
            st.info("No closed-candle decisions recorded yet. With a 15m strategy, decisions are evaluated when each 15-minute candle closes.")
        else:
            signals["Time (UTC)"] = pd.to_datetime(signals["created_at"], unit="ms", utc=True).dt.strftime("%Y-%m-%d %H:%M:%S")
            st.dataframe(signals[["Time (UTC)","symbol","action","side","reason"]], use_container_width=True, hide_index=True)

    st.divider()
    left, right = st.columns(2)
    with left:
        st.subheader("Trade history")
        if trades.empty:
            st.info("No paper trades yet. The strategy waits for a confirmed Heikin-Ashi colour flip on a completed candle.")
        else:
            trades["Time (UTC)"] = pd.to_datetime(trades["created_at"], unit="ms", utc=True).dt.strftime("%Y-%m-%d %H:%M:%S")
            st.dataframe(trades[["Time (UTC)","symbol","side","action","quantity","price","fee","net_pnl","reason"]], use_container_width=True, hide_index=True)
    with right:
        st.subheader("Bot activity")
        if events.empty:
            st.info("No runtime events recorded yet.")
        else:
            events["Time (UTC)"] = pd.to_datetime(events["created_at"], unit="ms", utc=True).dt.strftime("%Y-%m-%d %H:%M:%S")
            st.dataframe(events[["Time (UTC)","level","message"]], use_container_width=True, hide_index=True)

    st.caption(f"Risk settings: {settings.leverage}× leverage · TP {tp_value:.8f} USDT · "
               f"SL {sl_value:.8f} USDT · max {settings.max_positions} positions · "
               f"daily realized-loss guard {settings.daily_loss_limit_usdt:.2f} USDT (UTC).")

live_monitor()
