# Binance Futures Paper Trading Bot

This development build consumes live public Binance USDⓈ-M Futures market data and simulates trades locally. It has no API keys, signed requests, or authenticated trading endpoints, so it cannot place live orders.

## Architecture
- REST snapshots: order book, mark/index price, funding rate, open interest and 24-hour ticker.
- WebSocket: best bid/ask, aggregate trades, mark-price/funding updates and partial depth; reconnect loop included.
- Python strategy: transparent EMA crossover baseline and spread guard.
- Paper engine: simulated 10x leverage (configurable), assumed taker fees, funding cash flows and SQLite trade journal.
- Streamlit dashboard and Docker Compose deployment foundation.

## Development-stage limitations
Not production-ready for unattended trading. EMA is an illustrative baseline, not a recommendation. Fills use best bid/ask and a configured fee assumption; they do not reproduce queue priority, exact partial fills, latency, exchange liquidation mechanics or all margin rules. Funding is applied when the running process observes that the next funding timestamp has passed. Open paper positions are held in memory and are not restored after restart. Daily loss circuit breaker is not implemented. Dashboard updates on browser refresh. Treat simulated performance as approximate.

## Quick start
Python 3.10+:
    python -m venv .venv
    # Windows: .venv\Scripts\activate
    # Linux/macOS: source .venv/bin/activate
    pip install -r requirements.txt
    copy .env.example .env
    python main.py

In a second terminal, activate the environment and run:
    streamlit run dashboard.py
Open http://localhost:8501.

## Docker
    cp .env.example .env
    docker compose up --build -d
Open http://localhost:8501. Logs: docker compose logs -f. Stop: docker compose down.

Configure symbol, leverage, paper margin, starting wallet, fee assumption and EMA periods in .env. Do not add exchange API keys.
