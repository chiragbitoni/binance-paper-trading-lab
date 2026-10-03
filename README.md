# Binance Paper Trading Strategy Lab

[![Paper Trading](https://img.shields.io/badge/mode-paper%20trading-2ea44f)](https://chiragbitoni.github.io/binance-paper-trading-lab/)
[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Automated](https://img.shields.io/badge/GitHub%20Actions-every%204h-2088FF?logo=githubactions&logoColor=white)](https://github.com/chiragbitoni/binance-paper-trading-lab/actions)

A transparent, paper-first Binance Spot strategy research bot for BTC/USDT. It backtests multiple rule-based strategies, tracks independent forward-paper accounts, stores win/loss statistics in SQLite, and publishes a static dashboard after every completed four-hour candle.

**[Open the live paper-trading dashboard](https://chiragbitoni.github.io/binance-paper-trading-lab/)**

> This project is educational software, not financial advice. Historical results do not predict future performance. Real orders are disabled by default.

## Highlights

- Multiple explainable trading strategies with separate $10 paper balances
- Two-year historical backtests with fees, slippage, and next-candle execution
- Return, win rate, profit factor, trade count, and maximum drawdown tracking
- Forward-paper positions and trades stored independently from backtests
- Binance market-data-only API; no credentials required for paper mode
- Automated four-hour updates through GitHub Actions
- Lightweight dashboard published through GitHub Pages
- Live-order method protected by an explicit safety lock

## Included strategies

| Strategy | Research purpose |
| --- | --- |
| 50/200 EMA regime | Long-term trend participation |
| Trend + Donchian breakout | Breakout confirmation inside a trend |
| Slow EMA regime | Broad directional trend filter |
| EMA trend pullback | Pullback entries during an established trend |
| Quiet-to-active breakout | Volatility expansion after compression |
| Bollinger/RSI recovery | Mean recovery with a trend filter |
| Buy-and-hold benchmark | Baseline using the same position cap |

The dashboard contains the latest saved results. Compare net return, drawdown, profit factor, and sample size together—win rate alone is not enough.

## Quick start

Requirements: Python 3.11 or newer.

```powershell
git clone https://github.com/chiragbitoni/binance-paper-trading-lab.git
cd binance-paper-trading-lab
python -m pip install -r requirements.txt
python run.py backtest
python run.py paper
python run.py dashboard
```

Open `http://127.0.0.1:8765`. To keep checking for newly completed candles locally, run this in another terminal:

```powershell
python run.py paper --loop
```

## Configuration

Edit `config.json` to change the symbol, candle interval, simulated balance, position cap, fees, slippage, history length, or enabled strategies. The default experiment uses:

- Symbol: `BTCUSDT`
- Candle interval: `4h`
- Paper balance: `$10` per strategy
- Maximum position: `$8`
- Minimum notional: `$5`
- Historical window: `730` days

Backtest statistics and forward-paper results are intentionally stored separately in `data/trading_bot.db`.

## Commands

```text
python run.py backtest               Run historical strategy tests
python run.py paper                  Process the newest completed candle once
python run.py paper --loop           Continuously watch for completed candles
python run.py dashboard              Serve the local dashboard
python run.py render-static          Rebuild docs/index.html for GitHub Pages
python run.py check-live-connection  Test a read-only Binance connection
```

## Automation

`.github/workflows/paper-trading.yml` runs 12 minutes after every four-hour Binance candle closes. Each run:

1. Downloads current public Binance candle data.
2. Updates every forward-paper account.
3. Rebuilds the static dashboard.
4. Commits the updated SQLite state and dashboard.

The workflow uses no Binance credentials and never invokes the live-order client.

## Security and optional Binance connectivity

Paper mode needs no API key. For a read-only connectivity test, provide credentials only as local environment variables:

```powershell
$env:BINANCE_API_KEY="your-api-key"
$env:BINANCE_API_SECRET="your-api-secret"
python run.py check-live-connection
```

- Never commit credentials or place them in `config.json`.
- `.env` files are ignored by Git.
- Use a dedicated Binance key with withdrawals disabled.
- Restrict any account key to a trusted static IP.
- Do not add real-account credentials to the public GitHub Actions workflow.

The included signed client can check account connectivity. Its market-order method is locked behind `LIVE_TRADING_ACK=I_UNDERSTAND_REAL_ORDERS`, and the automated paper engine never calls it. A production deployment still requires quantity rounding, exchange-filter validation, persistent stop handling, reconciliation, alerts, loss limits, and a fixed-IP server.

## Project structure

```text
trading_bot/     Strategies, indicators, backtester, storage and Binance clients
data/            SQLite backtest and paper-trading state
docs/            Generated static dashboard for GitHub Pages
.github/         Scheduled paper-trading workflow
config.json      Experiment and risk settings
run.py           Command-line entry point
```

## Risk notice

Trading can lose money. Backtests are sensitive to market regime, assumptions, execution quality, fees, and overfitting. Validate strategies with a meaningful forward-paper sample before considering real capital, and avoid leverage while testing.
