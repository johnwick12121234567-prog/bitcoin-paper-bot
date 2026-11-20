# BTC-CAD Paper Trading Bot

A small mobile-friendly Flask dashboard for **paper trading only**. It reads public BTC-CAD prices and simulates trades. It does not connect to a Coinbase account, create API keys, or place real orders.

## Risk settings
- Starting simulated cash: CAD $20
- Maximum account drawdown: CAD $2 (bot stops)
- Maximum simulated position: CAD $10
- No leverage, no deposits, no withdrawals
- Estimated fee assumption: 0.6% per side (illustrative only; not a statement of Coinbase fees)

## Strategy
A simple 5-hour / 20-hour moving-average crossover, evaluated only when a new hourly candle has closed. This is a demonstration strategy, not a proven profitable strategy. With only $20, fees and spreads can overwhelm small price moves.

## Deploy to Railway
1. Create a private GitHub repository and upload these project files.
2. Create a Railway project and deploy from that repository.
3. Set these environment variables in Railway:
   - `DASHBOARD_PASSWORD`: a long, unique password (required; use at least 20 characters)
   - `ESTIMATED_FEE_RATE`: optional, defaults to `0.006`
   - `POLL_SECONDS`: optional, defaults to `60`
   - `DATA_FILE`: optional, defaults to `bot_state.json`
4. Add a persistent volume and mount it at `/app/data`; set `DATA_FILE=/app/data/bot_state.json`.
5. Use one running replica only. The worker is in-process; multiple replicas would run duplicate paper bots.
6. Deploy, open the generated HTTPS URL on your Android phone, and sign in:
   - Username: `owner`
   - Password: the value you set for `DASHBOARD_PASSWORD`
7. Add the page to your phone's home screen using Chrome's menu → Add to Home screen.

Railway pricing and availability can change. A continuously running cloud service may require a paid plan. Do not assume a free tier will run 24/7.

## Run locally
Python 3.11+:
```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
export DASHBOARD_PASSWORD='choose-a-long-password'
python app.py
```
Then visit `http://localhost:8080` on the same device/network.

## Important limitations
- Coinbase Exchange public market data is used for the BTC-CAD ticker and hourly candles. If the public endpoint is unavailable or the BTC-CAD market is unavailable, the dashboard shows an error.
- The process must stay running for continuous monitoring. Use one cloud instance and persistent storage.
- Dashboard uses HTTP Basic Authentication; deploy only behind the hosting provider's HTTPS endpoint.
- This is not financial advice. Paper trading results are not evidence of future returns.
- There is intentionally no live trading implementation. Do not add Coinbase API credentials to this project.
