"""Binance Spot paper-trading simulator with multi-symbol support and backtest.

Uses Binance's public endpoints only. It never submits real orders
and does not need API keys.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from decimal import Decimal, InvalidOperation
from pathlib import Path
from datetime import datetime, timedelta

DEFAULT_SYMBOL = "BTCUSDT"
QUOTE_ASSET = "USDT"
STARTING_USDT = Decimal("1000.00")
STATE_FILE = Path("paper_state.json")
USER_AGENT = "binance-paper-bot/1.1"


def get_market_price(symbol: str) -> Decimal:
    """Get the latest public price from Binance for the given symbol."""
    url = f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
        return Decimal(data["price"])
    except (urllib.error.URLError, TimeoutError, KeyError, ValueError) as exc:
        raise RuntimeError(f"Could not retrieve price for {symbol}: {exc}") from exc


def fetch_klines(symbol: str, interval: str, limit: int = 500) -> list:
    """Fetch historical klines from Binance public API."""
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise RuntimeError(f"Could not fetch klines for {symbol}: {exc}") from exc


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {
            "quote_balance": str(STARTING_USDT),
            "holdings": {},
            "trades": [],
            "current_symbol": DEFAULT_SYMBOL,
        }
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        # Migrate old single-base format
        if "base_balance" in state and "holdings" not in state:
            old_base = Decimal(state.get("base_balance", "0"))
            symbol = state.get("symbol", DEFAULT_SYMBOL)
            state["holdings"] = {symbol: str(old_base)} if old_base > 0 else {}
            del state["base_balance"]
        state["quote_balance"] = str(Decimal(state.get("quote_balance", STARTING_USDT)))
        state.setdefault("holdings", {})
        state.setdefault("trades", [])
        state.setdefault("current_symbol", DEFAULT_SYMBOL)
        # Normalize holdings to Decimal strings
        for sym, qty in list(state["holdings"].items()):
            state["holdings"][sym] = str(Decimal(qty))
        return state
    except (OSError, json.JSONDecodeError, KeyError, InvalidOperation) as exc:
        raise RuntimeError(
            f"Cannot read {STATE_FILE}. Back it up or remove it to reset paper funds."
        ) from exc


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def money(value: Decimal) -> str:
    return f"{value:,.2f}"


def get_base_asset(symbol: str) -> str:
    if symbol.endswith(QUOTE_ASSET):
        return symbol[:-len(QUOTE_ASSET)]
    return symbol


def show_status(state: dict, price: Decimal) -> None:
    symbol = state["current_symbol"]
    base = get_base_asset(symbol)
    quote = Decimal(state["quote_balance"])
    holdings = Decimal(state["holdings"].get(symbol, "0"))
    total = quote + holdings * price
    print("\n--- PAPER PORTFOLIO ---")
    print(f"Current market:  {symbol}")
    print(f"Latest price:    {QUOTE_ASSET} {money(price)}")
    print(f"Cash:            {QUOTE_ASSET} {money(quote)}")
    print(f"{base} holdings:     {holdings:.8f}")
    print(f"Portfolio value: {QUOTE_ASSET} {money(total)}")
    print(f"Simulated trades:{len(state['trades'])}")
    other = {k: v for k, v in state["holdings"].items() if k != symbol and Decimal(v) > 0}
    if other:
        print("Other holdings:  " + ", ".join(f"{k}: {Decimal(v):.6f}" for k, v in other.items()))
    print("No real orders have been placed.\n")


def buy(state: dict, price: Decimal, amount_text: str) -> None:
    symbol = state["current_symbol"]
    base = get_base_asset(symbol)
    try:
        amount = Decimal(amount_text)
    except InvalidOperation:
        print("Enter a valid USDT amount, for example: buy 100")
        return
    if not amount.is_finite() or amount <= 0:
        print("Buy amount must be greater than zero.")
        return
    cash = Decimal(state["quote_balance"])
    if amount > cash:
        print(f"Not enough paper cash. Available: {QUOTE_ASSET} {money(cash)}")
        return

    quantity = amount / price
    state["quote_balance"] = str(cash - amount)
    current = Decimal(state["holdings"].get(symbol, "0"))
    state["holdings"][symbol] = str(current + quantity)
    state["trades"].append({
        "side": "BUY", "symbol": symbol, "price": str(price),
        "quantity": str(quantity), "quote_amount": str(amount),
        "timestamp": datetime.utcnow().isoformat() + "Z",
    })
    save_state(state)
    print(f"SIMULATED BUY: {quantity:.8f} {base} at {QUOTE_ASSET} {money(price)}")
    print(f"Paper cash remaining: {QUOTE_ASSET} {money(cash - amount)}")


def sell(state: dict, price: Decimal, quantity_text: str) -> None:
    symbol = state["current_symbol"]
    base = get_base_asset(symbol)
    try:
        quantity = Decimal(quantity_text)
    except InvalidOperation:
        print(f"Enter a valid {base} quantity, for example: sell 0.001")
        return
    holdings = Decimal(state["holdings"].get(symbol, "0"))
    if not quantity.is_finite() or quantity <= 0:
        print("Sell quantity must be greater than zero.")
        return
    if quantity > holdings:
        print(f"Not enough paper {base}. Available: {holdings:.8f}")
        return

    proceeds = quantity * price
    state["holdings"][symbol] = str(holdings - quantity)
    if Decimal(state["holdings"][symbol]) == 0:
        del state["holdings"][symbol]
    state["quote_balance"] = str(Decimal(state["quote_balance"]) + proceeds)
    state["trades"].append({
        "side": "SELL", "symbol": symbol, "price": str(price),
        "quantity": str(quantity), "quote_amount": str(proceeds),
        "timestamp": datetime.utcnow().isoformat() + "Z",
    })
    save_state(state)
    print(f"SIMULATED SELL: {quantity:.8f} {base} at {QUOTE_ASSET} {money(price)}")
    print(f"Paper cash now: {QUOTE_ASSET} {money(Decimal(state['quote_balance']))}")


def set_symbol(state: dict, new_symbol: str) -> None:
    new_symbol = new_symbol.upper()
    if not new_symbol.endswith(QUOTE_ASSET):
        new_symbol += QUOTE_ASSET
    try:
        get_market_price(new_symbol)  # validate
    except RuntimeError as exc:
        print(exc)
        return
    state["current_symbol"] = new_symbol
    save_state(state)
    print(f"Switched to {new_symbol}")


def sma(values: list[float], period: int) -> list[float]:
    result = []
    for i in range(len(values)):
        if i < period - 1:
            result.append(None)
        else:
            window = values[i - period + 1 : i + 1]
            result.append(sum(window) / period)
    return result


def run_backtest(symbol: str, interval: str = "1h", limit: int = 200, fast: int = 10, slow: int = 30) -> None:
    """Simple SMA crossover backtest on historical closes."""
    print(f"\nRunning SMA({fast}/{slow}) crossover backtest on {symbol} {interval} (last {limit} candles)...")
    try:
        klines = fetch_klines(symbol, interval, limit)
    except RuntimeError as exc:
        print(exc)
        return
    if len(klines) < slow + 2:
        print("Not enough historical data.")
        return

    closes = [float(k[4]) for k in klines]
    times = [int(k[0]) for k in klines]
    fast_sma = sma(closes, fast)
    slow_sma = sma(closes, slow)

    cash = float(STARTING_USDT)
    position = 0.0
    trades = 0
    entry_price = 0.0

    for i in range(slow, len(closes)):
        price = closes[i]
        if fast_sma[i] is None or slow_sma[i] is None or fast_sma[i-1] is None or slow_sma[i-1] is None:
            continue
        # Golden cross: buy
        if fast_sma[i-1] <= slow_sma[i-1] and fast_sma[i] > slow_sma[i] and cash > 0:
            position = cash / price
            entry_price = price
            cash = 0.0
            trades += 1
        # Death cross: sell
        elif fast_sma[i-1] >= slow_sma[i-1] and fast_sma[i] < slow_sma[i] and position > 0:
            cash = position * price
            position = 0.0
            trades += 1

    final_price = closes[-1]
    final_value = cash + position * final_price
    start_value = float(STARTING_USDT)
    pnl = final_value - start_value
    pnl_pct = (pnl / start_value) * 100

    print(f"Start value:     {QUOTE_ASSET} {start_value:,.2f}")
    print(f"Final value:     {QUOTE_ASSET} {final_value:,.2f}")
    print(f"P&L:             {QUOTE_ASSET} {pnl:,.2f} ({pnl_pct:+.2f}%)")
    print(f"Trades:          {trades}")
    print(f"Final position:  {position:.8f} {get_base_asset(symbol)} @ {final_price:.2f}" if position else "Final position:  flat")
    print("Backtest uses close prices only; no fees or slippage simulated.\n")


def main() -> None:
    print("Binance Spot Paper Trading Bot")
    print("PAPER MODE ONLY — no real orders and no API keys.")
    print(f"Starting paper cash: {QUOTE_ASSET} {money(STARTING_USDT)}")
    print("Commands: price | status | buy <USDT> | sell <qty> | symbol <PAIR> | backtest [symbol] [interval] [candles] | help | quit")

    try:
        state = load_state()
    except RuntimeError as exc:
        print(exc)
        sys.exit(1)

    print(f"Current pair: {state['current_symbol']}")

    while True:
        try:
            raw = input("\npaper> ").strip()
            command = raw.lower().split()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting paper trader.")
            break
        if not command:
            continue
        action = command[0]

        if action in {"quit", "exit", "q"}:
            print("Exiting paper trader. Your simulated portfolio is saved locally.")
            break
        if action == "help":
            print("price                    Show latest public price for current symbol")
            print("status                   Show paper cash, holdings, and portfolio value")
            print("buy <USDT amount>        Simulate a buy using paper cash")
            print("sell <quantity>          Simulate a sell from current symbol holdings")
            print("symbol <PAIR>            Switch market (e.g. ETHUSDT or ETH)")
            print("backtest [sym] [iv] [n]  SMA 10/30 crossover backtest (default BTCUSDT 1h 200)")
            print("quit                     Exit the program")
            continue
        if action == "symbol" and len(command) == 2:
            set_symbol(state, command[1])
            continue
        if action == "backtest":
            sym = command[1].upper() if len(command) > 1 else state["current_symbol"]
            if not sym.endswith(QUOTE_ASSET):
                sym += QUOTE_ASSET
            iv = command[2] if len(command) > 2 else "1h"
            try:
                n = int(command[3]) if len(command) > 3 else 200
            except ValueError:
                n = 200
            run_backtest(sym, iv, n)
            continue
        if action not in {"price", "status", "buy", "sell"}:
            print("Unknown command. Type 'help' for commands.")
            continue
        if action in {"buy", "sell"} and len(command) != 2:
            print("Usage: buy <USDT amount>" if action == "buy" else "Usage: sell <quantity>")
            continue

        try:
            price = get_market_price(state["current_symbol"])
            if action == "price":
                print(f"{state['current_symbol']}: {QUOTE_ASSET} {money(price)}")
            elif action == "status":
                show_status(state, price)
            elif action == "buy":
                buy(state, price, command[1])
            elif action == "sell":
                sell(state, price, command[1])
        except RuntimeError as exc:
            print(exc)


if __name__ == "__main__":
    main()
