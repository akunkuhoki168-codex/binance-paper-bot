# Run the Binance Futures Paper Bot

Live public Futures market data; simulated trades only. This project has no live order capability and does not need Binance API keys.

## Windows
    python -m venv .venv
    .venv\Scripts\activate
    pip install -r requirements.txt
    copy .env.example .env
    python main.py

Open a second PowerShell window in the project folder:
    .venv\Scripts\activate
    streamlit run dashboard.py

Open http://localhost:8501.

## Linux/macOS
    python3 -m venv .venv
    source .venv/bin/activate
    pip install -r requirements.txt
    cp .env.example .env
    python main.py

In another terminal run streamlit run dashboard.py.

## Docker / VPS
    cp .env.example .env
    docker compose up --build -d

Dashboard: http://localhost:8501
Logs: docker compose logs -f
Stop: docker compose down

Use persistent storage for the data directory. Do not expose the dashboard publicly without firewall or reverse-proxy authentication.

## Limitations
This is a development build, not production-ready unattended software. Open paper positions are not restored after restart, the daily loss circuit breaker is not implemented, simulated fills are simplified, and dashboard refresh is manual.
