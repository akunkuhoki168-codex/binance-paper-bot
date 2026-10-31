import os,json,time,signal,sqlite3,threading
from pathlib import Path
from collections import deque
import requests,websocket
from dotenv import load_dotenv
load_dotenv()
SYMBOL=os.getenv("SYMBOL","BTCUSDT").upper(); LEVERAGE=int(os.getenv("LEVERAGE","10"))
MARGIN=float(os.getenv("ORDER_MARGIN_USDT","25")); START=float(os.getenv("STARTING_BALANCE_USDT","1000"))
FEE=float(os.getenv("TAKER_FEE_RATE","0.0005")); SPREAD_LIMIT=float(os.getenv("MAX_SPREAD_BPS","8"))
FAST=int(os.getenv("FAST_EMA","9")); SLOW=int(os.getenv("SLOW_EMA","21"))
DB=os.getenv("DB_PATH","data/trading.db"); REST="https://fapi.binance.com"
if not 1<=LEVERAGE<=125 or MARGIN<=0 or START<=0 or FAST>=SLOW: raise ValueError("Invalid paper-bot configuration")
Path(DB).parent.mkdir(parents=True,exist_ok=True)
conn=sqlite3.connect(DB,check_same_thread=False); conn.row_factory=sqlite3.Row
conn.executescript("""
CREATE TABLE IF NOT EXISTS account(id INTEGER PRIMARY KEY CHECK(id=1),wallet REAL,realized REAL,fees REAL,funding REAL,updated INTEGER);
CREATE TABLE IF NOT EXISTS trades(id INTEGER PRIMARY KEY,time INTEGER,side TEXT,qty REAL,price REAL,notional REAL,fee REAL,pnl REAL,reason TEXT);
CREATE TABLE IF NOT EXISTS market(id INTEGER PRIMARY KEY,payload TEXT,updated INTEGER);
""")
if not conn.execute("SELECT 1 FROM account WHERE id=1").fetchone():
 conn.execute("INSERT INTO account VALUES(1,?,0,0,0,?)",(START,int(time.time()*1000))); conn.commit()
lock=threading.RLock(); stop=threading.Event(); position=None; last_funding=0; prices=deque(maxlen=SLOW*5); previous=None; latest={}
def account(): return dict(conn.execute("SELECT * FROM account WHERE id=1").fetchone())
def delta(wallet=0,realized=0,fees=0,funding=0):
 conn.execute("UPDATE account SET wallet=wallet+?,realized=realized+?,fees=fees+?,funding=funding+?,updated=? WHERE id=1",(wallet,realized,fees,funding,int(time.time()*1000))); conn.commit()
def record(side,qty,price,fee,pnl,reason):
 conn.execute("INSERT INTO trades(time,side,qty,price,notional,fee,pnl,reason) VALUES(?,?,?,?,?,?,?,?)",(int(time.time()*1000),side,qty,price,qty*price,fee,pnl,reason)); conn.commit()
def ema(vals,n):
 a=2/(n+1); x=vals[0]
 for v in vals[1:]: x=a*v+(1-a)*x
 return x
def execute(action,price,reason):
 global position
 with lock:
  if action=="BUY":
   if position: return
   notional=MARGIN*LEVERAGE; fee=notional*FEE; wallet=account()["wallet"]
   if wallet<MARGIN+fee: print("Insufficient simulated wallet"); return
   qty=notional/price; position={"qty":qty,"entry":price,"margin":MARGIN}
   delta(wallet=-(MARGIN+fee),fees=fee); record("BUY",qty,price,fee,0,reason)
   print("PAPER LONG OPEN",qty,"BTC @",price,"notional",notional,"fee",fee)
  elif action=="SELL" and position:
   p=position; gross=(price-p["entry"])*p["qty"]; fee=price*p["qty"]*FEE; net=gross-fee
   delta(wallet=p["margin"]+gross-fee,realized=net,fees=fee); record("SELL",p["qty"],price,fee,net,reason); position=None
   print("PAPER LONG CLOSED net PnL",net,"fee",fee)
def on_market(d):
 global latest,previous
 with lock:
  latest.update(d); conn.execute("INSERT OR REPLACE INTO market VALUES(1,?,?)",(json.dumps(latest),int(time.time()*1000))); conn.commit()
  price=latest.get("last_price") or latest.get("mark_price")
  if not price: return
  prices.append(price)
  if len(prices)<SLOW+2: return
  v=list(prices); f=ema(v[-FAST*3:],FAST); s=ema(v[-SLOW*3:],SLOW)
  signal_now="BUY" if f>s else "SELL" if f<s else "HOLD"
  action=None
  if signal_now=="BUY" and previous!="BUY": action="BUY"
  elif signal_now=="SELL" and previous=="BUY": action="SELL"
  previous=signal_now
  if not action: return
  bid=latest.get("bid",price); ask=latest.get("ask",price); mid=(bid+ask)/2
  spread=(ask-bid)/mid*10000 if mid else 0
  if spread>SPREAD_LIMIT: print("Signal skipped: spread guard",spread); return
  execute(action,ask if action=="BUY" else bid,"EMA "+("bullish" if action=="BUY" else "bearish")+" crossover")
def rest_snapshot():
 def get(path):
  r=requests.get(REST+path,params={"symbol":SYMBOL},timeout=10); r.raise_for_status(); return r.json()
 b=get("/fapi/v1/depth"); p=get("/fapi/v1/premiumIndex"); oi=get("/fapi/v1/openInterest"); t=get("/fapi/v1/ticker/24hr")
 on_market({"bid":float(b["bids"][0][0]),"bid_qty":float(b["bids"][0][1]),"ask":float(b["asks"][0][0]),"ask_qty":float(b["asks"][0][1]),"mark_price":float(p["markPrice"]),"index_price":float(p["indexPrice"]),"funding_rate":float(p["lastFundingRate"]),"next_funding_time":int(p["nextFundingTime"]),"open_interest":float(oi["openInterest"]),"volume_24h":float(t["volume"]),"quote_volume_24h":float(t["quoteVolume"]),"last_price":float(t["lastPrice"]),"event_time":int(time.time()*1000)})
def ws_loop():
 streams="/".join([SYMBOL.lower()+"@bookTicker",SYMBOL.lower()+"@aggTrade",SYMBOL.lower()+"@markPrice@1s",SYMBOL.lower()+"@depth5@100ms"])
 url="wss://fstream.binance.com/stream?streams="+streams
 while not stop.is_set():
  try:
   app=websocket.WebSocketApp(url,on_message=lambda ws,msg: ws_message(msg),on_open=lambda ws:print("Live Futures WebSocket connected"),on_error=lambda ws,e:print("WebSocket error",e))
   app.run_forever(ping_interval=20,ping_timeout=10)
  except Exception as e: print("WebSocket reconnect",e)
  if not stop.is_set(): time.sleep(3)
def ws_message(msg):
 try:
  packet=json.loads(msg); d=packet.get("data",packet); stream=packet.get("stream",""); u={"event_time":int(d.get("E",time.time()*1000))}
  if "bookTicker" in stream: u.update(bid=float(d["b"]),bid_qty=float(d["B"]),ask=float(d["a"]),ask_qty=float(d["A"]))
  elif "aggTrade" in stream: u.update(last_price=float(d["p"]),last_qty=float(d["q"]))
  elif "markPrice" in stream: u.update(mark_price=float(d["p"]),index_price=float(d["i"]),funding_rate=float(d["r"]),next_funding_time=int(d["T"]))
  elif "depth5" in stream: u.update(bids=d.get("b",[]),asks=d.get("a",[]))
  if len(u)>1: on_market(u)
 except (ValueError,KeyError,TypeError): pass
def main():
 global last_funding
 print("BINANCE USDⓈ-M FUTURES PAPER BOT | LIVE PUBLIC DATA | NO LIVE ORDERS")
 print(SYMBOL,"simulated leverage",LEVERAGE,"x | margin per entry",MARGIN,"USDT")
 signal.signal(signal.SIGINT,lambda *_:stop.set()); signal.signal(signal.SIGTERM,lambda *_:stop.set())
 threading.Thread(target=ws_loop,daemon=True).start(); last_rest=last_log=0
 while not stop.is_set():
  now=time.time()
  if now-last_rest>30:
   try: rest_snapshot()
   except Exception as e: print("REST snapshot error",e)
   last_rest=now
  with lock:
   d=dict(latest)
   if position and d.get("mark_price") and d.get("funding_rate") is not None:
    ft=d.get("next_funding_time",0); current=int(time.time()*1000)
    if ft and current>=ft and last_funding!=ft:
     payment=-position["qty"]*d["mark_price"]*d["funding_rate"]; delta(wallet=payment,funding=payment); last_funding=ft
     print("Simulated funding cash flow",payment,"USDT")
   if now-last_log>15 and d:
    mark=d.get("mark_price",d.get("last_price",0)); upnl=(mark-position["entry"])*position["qty"] if position and mark else 0
    equity=account()["wallet"]+(position["margin"] if position else 0)+upnl
    print("Heartbeat | mark",mark,"| equity",round(equity,2),"USDT |", "LONG" if position else "FLAT")
    last_log=now
  time.sleep(1)
 print("Paper bot stopped.")
if __name__=="__main__": main()
