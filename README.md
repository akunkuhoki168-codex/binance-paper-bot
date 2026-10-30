# Binance Spot Paper Trading Bot

A Python CLI for paper-trading Binance Spot pairs with real market prices and simulated orders. It never places real orders and does not require API keys.

## Features

- Multi-symbol support (any USDT pair, e.g. BTCUSDT, ETHUSDT, SOLUSDT)
- Live public price quotes
- Simulated buy/sell with persistent paper portfolio
- Trade history with timestamps
- Simple SMA crossover backtest mode using historical klines
- Standard library only (no external packages)

## Quick start

See `RUNNING.md` for Windows setup instructions.

```bash
python main.py
```

### Commands

- `price` — current price of the active symbol
- `status` — portfolio (cash + holdings across symbols)
- `buy <USDT amount>` — simulated market buy
- `sell <quantity>` — simulated market sell of the current symbol
- `symbol <PAIR>` — switch market (e.g. `symbol ETH` or `symbol ETHUSDT`)
- `backtest [symbol] [interval] [candles]` — run SMA 10/30 crossover backtest  
  Example: `backtest ETHUSDT 1h 300`
- `help` / `quit`

## Backtest

The backtest fetches public historical klines and simulates a simple fast/slow SMA crossover strategy (default 10/30). It reports starting vs final portfolio value and number of trades. No fees or slippage are applied.

## Safety

This project is paper-trading only. Never commit API keys, secrets, or `.env` files. Live trading is not implemented.

## State

Simulated balances and trades are stored in `paper_state.json` (gitignored). Delete the file to reset.
