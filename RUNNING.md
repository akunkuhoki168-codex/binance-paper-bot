# Run the paper-trading bot on Windows

This program uses Python's standard library, so you do not need to install extra Python packages.

## 1. Install Python

Install Python 3.10 or newer from https://www.python.org/downloads/windows/.

During installation, tick **Add Python to PATH** if the installer offers that option.

## 2. Download the repository

On the GitHub repository page, click the green **Code** button, then **Download ZIP**. Extract the ZIP file to a folder you can find easily, such as your Desktop.

Repository: https://github.com/dedkovic441-hub/binance-paper-bot

## 3. Open a terminal in the project folder

Open the extracted `binance-paper-bot` folder in File Explorer. Click the address bar, type `powershell`, and press Enter.

## 4. Check Python

Run:

```powershell
python --version
```

It should show Python 3.10 or newer. If Windows says Python was not found, install Python and reopen PowerShell.

## 5. Start the bot

Run:

```powershell
python main.py
```

You should see a message saying **PAPER MODE ONLY** and a `paper>` prompt.

## 6. Try safe paper commands

Type each command and press Enter:

```text
price
status
buy 100
status
symbol ETH
price
backtest BTCUSDT 1h 200
status
quit
```

- `price` retrieves the latest public price for the current symbol.
- `buy 100` simulates spending 100 USDT from your virtual balance.
- `sell 0.001` simulates selling 0.001 of the current base asset (if you hold enough).
- `symbol ETH` (or `symbol ETHUSDT`) switches the active market.
- `backtest [symbol] [interval] [candles]` runs a simple SMA crossover backtest.
- `status` displays the simulated portfolio.
- `quit` exits the program.

## Important notes

- This is a manual paper-trading simulator with an optional historical backtest mode.
- It never sends real buy or sell orders and does not need Binance API keys.
- Your simulated balance and trade history are saved in `paper_state.json` in the folder where you run the program. Keep that file if you want to preserve your paper portfolio.
- The bot needs an internet connection to retrieve public market prices and historical data.
- Do not upload passwords, API keys, or secret credentials to GitHub.
