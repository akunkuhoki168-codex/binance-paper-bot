"""Binance Spot paper-trading simulator.

Uses Binance's public market-price endpoint only. It never submits real orders
and does not need API keys.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from decimal import Decimal, InvalidOperation
from pathlib import Path

SYMBOL = "BTCUSDT"
BASE_ASSET = "BTC"
QUOTE_ASSET = "USDT"
STARTING_USDT = Decimal("1000.00")
STATE_FILE = Path("paper_state.json")
PRICE_URL = f"https://api.binance.com/api/v3/ticker/price?symbol={SYMBOL}"


def get_market_price() -> Decimal:
    """Get the latest public BTC/USDT price from Binance."""
    request = urllib.request.Request(
        PRICE_URL, headers={"User-Agent": "binance-paper-bot/1.0"}
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
        return Decimal(data["price"])
    except (urllib.error.URLError, TimeoutError, KeyError, ValueError) as exc:
        raise RuntimeError(f"Could not retrieve Binance market price: {exc}") from exc


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {
            "quote_balance": str(STARTING_USDT),
            "base_balance": "0",
            "trades": [],
        }
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        state["quote_balance"] = str(Decimal(state["quote_balance"]))
        state["base_balance"] = str(Decimal(state["base_balance"]))
        state.setdefault("trades", [])
        return state
    except (OSError, json.JSONDecodeError, KeyError, InvalidOperation) as exc:
        raise RuntimeError(
            f"Cannot read {STATE_FILE}. Back it up or remove it to reset paper funds."
        ) from exc


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def money(value: Decimal) -> str:
    return f"{value:,.2f}"


def show_status(state: dict, price: Decimal) -> None:
    quote = Decimal(state["quote_balance"])
    base = Decimal(state["base_balance"])
    total = quote + base * price
    print("\n--- PAPER PORTFOLIO ---")
    print(f"Market:          {SYMBOL}")
    print(f"Latest price:    {QUOTE_ASSET} {money(price)}")
    print(f"Cash:            {QUOTE_ASSET} {money(quote)}")
    print(f"{BASE_ASSET} holdings:      {base:.8f}")
    print(f"Portfolio value: {QUOTE_ASSET} {money(total)}")
    print(f"Simulated trades:{len(state['trades'])}")
    print("No real orders have been placed.\n")


def buy(state: dict, price: Decimal, amount_text: str) -> None:
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
    state["base_balance"] = str(Decimal(state["base_balance"]) + quantity)
    state["trades"].append({
        "side": "BUY", "symbol": SYMBOL, "price": str(price),
        "quantity": str(quantity), "quote_amount": str(amount),
    })
    save_state(state)
    print(f"SIMULATED BUY: {quantity:.8f} {BASE_ASSET} at {QUOTE_ASSET} {money(price)}")
    print(f"Paper cash remaining: {QUOTE_ASSET} {money(cash - amount)}")


def sell(state: dict, price: Decimal, quantity_text: str) -> None:
    try:
        quantity = Decimal(quantity_text)
    except InvalidOperation:
        print("Enter a valid BTC quantity, for example: sell 0.001")
        return
    holdings = Decimal(state["base_balance"])
    if not quantity.is_finite() or quantity <= 0:
        print("Sell quantity must be greater than zero.")
        return
    if quantity > holdings:
        print(f"Not enough paper BTC. Available: {holdings:.8f} BTC")
        return

    proceeds = quantity * price
    state["base_balance"] = str(holdings - quantity)
    state["quote_balance"] = str(Decimal(state["quote_balance"]) + proceeds)
    state["trades"].append({
        "side": "SELL", "symbol": SYMBOL, "price": str(price),
        "quantity": str(quantity), "quote_amount": str(proceeds),
    })
    save_state(state)
    print(f"SIMULATED SELL: {quantity:.8f} {BASE_ASSET} at {QUOTE_ASSET} {money(price)}")
    print(f"Paper cash now: {QUOTE_ASSET} {money(Decimal(state['quote_balance']))}")


def main() -> None:
    print("Binance Spot Paper Trading Bot")
    print("PAPER MODE ONLY — no real orders and no API keys.")
    print(f"Pair: {SYMBOL} | Starting paper cash: {QUOTE_ASSET} {money(STARTING_USDT)}")
    print("Commands: price | status | buy <USDT amount> | sell <BTC quantity> | help | quit")

    try:
        state = load_state()
    except RuntimeError as exc:
        print(exc)
        sys.exit(1)

    while True:
        try:
            command = input("\npaper> ").strip().lower().split()
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
            print("price              Show latest public BTC/USDT price")
            print("status             Show paper cash, BTC holdings, and portfolio value")
            print("buy <USDT amount>  Simulate a buy using paper cash")
            print("sell <BTC amount>  Simulate a sell from paper BTC holdings")
            print("quit               Exit the program")
            continue
        if action not in {"price", "status", "buy", "sell"}:
            print("Unknown command. Type 'help' for commands.")
            continue
        if action in {"buy", "sell"} and len(command) != 2:
            print("Usage: buy <USDT amount>" if action == "buy" else "Usage: sell <BTC quantity>")
            continue

        try:
            price = get_market_price()
            if action == "price":
                print(f"{SYMBOL}: {QUOTE_ASSET} {money(price)}")
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
