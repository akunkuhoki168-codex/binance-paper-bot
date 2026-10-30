import os,json,sqlite3
from pathlib import Path
import pandas as pd
import streamlit as st
from dotenv import load_dotenv
load_dotenv()
DB=os.getenv("DB_PATH","data/trading.db")
st.set_page_config(page_title="Binance Futures Paper Bot",page_icon="📈",layout="wide")
st.title("Binance Futures Paper Trading")
st.caption("Live public Binance USDⓈ-M data · Simulated paper execution only · No live orders")
if not Path(DB).exists(): st.info("Start the bot using python main.py, then refresh this dashboard."); st.stop()
db=sqlite3.connect(DB); a=pd.read_sql_query("SELECT * FROM account WHERE id=1",db); m=pd.read_sql_query("SELECT payload FROM market WHERE id=1",db); trades=pd.read_sql_query("SELECT * FROM trades ORDER BY id DESC LIMIT 200",db)
if m.empty: st.info("Waiting for market data..."); st.stop()
d=json.loads(m.iloc[0]["payload"]); mark=float(d.get("mark_price") or d.get("last_price") or 0); wallet=float(a.iloc[0]["wallet"])
c=st.columns(4); c[0].metric("BTC mark price",f"{mark:,.2f} USDT"); c[1].metric("Paper wallet",f"{wallet:,.2f} USDT"); c[2].metric("Funding rate",f"{float(d.get('funding_rate',0))*100:.4f}%"); c[3].metric("24h quote volume",f"{float(d.get('quote_volume_24h',0)):,.0f} USDT")
st.subheader("Live market data"); c=st.columns(4); c[0].metric("Best bid",f"{float(d.get('bid',0)):,.2f}"); c[1].metric("Best ask",f"{float(d.get('ask',0)):,.2f}"); c[2].metric("Index price",f"{float(d.get('index_price',0)):,.2f}"); c[3].metric("Open interest",f"{float(d.get('open_interest',0)):,.3f} BTC")
st.subheader("Recent simulated trades")
if trades.empty: st.info("No trades yet. The EMA strategy waits for a crossover.")
else: st.dataframe(trades,use_container_width=True,hide_index=True)
st.caption("Refresh the browser to update. This dashboard cannot place orders.")
