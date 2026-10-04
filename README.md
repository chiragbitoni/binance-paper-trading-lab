# Binance Paper Trading Strategy Lab

[![Paper Trading](https://img.shields.io/badge/mode-paper%20trading-2ea44f)](https://chiragbitoni.github.io/binance-paper-trading-lab/)
[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Automated](https://img.shields.io/badge/GitHub%20Actions-every%204h-2088FF?logo=githubactions&logoColor=white)](https://github.com/chiragbitoni/binance-paper-trading-lab/actions)

A transparent, paper-first Binance Spot strategy research bot for five liquid USDT markets. It backtests multiple rule-based strategies, tracks isolated forward-paper accounts for every market/strategy pair, stores win/loss statistics in SQLite, and publishes a static dashboard after every completed four-hour candle.

**[Open the live paper-trading dashboard](https://chiragbitoni.github.io/binance-paper-trading-lab/)**

> This project is educational software, not financial advice. Historical results do not predict future performance. Real orders are disabled by default.

## Highlights

- Five parallel markets: BTC, ETH, BNB, SOL, and XRP against USDT
- One forward-paper candidate (50/200 EMA regime) plus research-only variants that are backtested but cannot create paper positions
- Two-year historical backtests and forward-paper signals with fees, slippage, and next-candle-open execution
- Return, win rate, profit factor, trade count, and maximum drawdown tracking
- Forward-paper positions and trades stored independently from backtests
- Decision dashboard with portfolio equity, mark P&L, stops, candle delta, trade ledger, and research results
- Dedicated candlestick chart page that groups all open strategy positions for each ticker
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
| Confirmed liquidity-sweep reclaim | Research-only: reversal after reclaiming a swept 20-candle low |
| Sweep + delta combo | Research-only: liquidity-sweep reclaim with strong positive taker-volume delta |
| CVD divergence | Research-only: a new price low without a matching rolling-24h CVD low, then bullish confirmation |
| Buy-and-hold benchmark | Baseline using the same position cap |

The dashboard contains the latest saved results. Compare net return, drawdown, profit factor, and sample size together—win rate alone is not enough. Research-only strategies appear in historical results but cannot open a forward-paper account until explicitly promoted after review. The current forward-paper candidate is the 50/200 EMA regime; existing positions from retired candidates remain managed until their normal exit rule closes them, but cannot open again.

### Liquidity-sweep rule

The long-only liquidity-sweep strategy uses a fully mechanical four-hour rule: the prior candle must wick below the previous 20-candle low, close back above that level, and have a lower wick of at least `0.5 ATR`. A following candle must close above that sweep candle's high while price is above the 200 EMA. The initial stop is below the sweep wick with a `0.25 ATR` buffer; a close below the 50 EMA exits the position. This describes price behaviour only—it does not prove why orders were triggered or promise a reversal.

### Candle delta and delta strategies

Binance Spot klines expose total volume and taker-buy volume. The dashboard calculates candle delta as `taker-buy − (total − taker-buy)` and displays its percentage of total volume plus a rolling 24-hour CVD. These are Binance-only, candle-level measures—not tick-level or multi-exchange order flow. The sweep + delta combination adds positive delta conditions to the liquidity-sweep reclaim. The separate CVD-divergence strategy looks for a fresh 20-candle price low where rolling 24-hour CVD does not make a new low, then waits for a bullish candle to close above the divergence candle high. Both remain research-only until their own results justify forward-paper testing.

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

- Markets: `BTCUSDT`, `ETHUSDT`, `BNBUSDT`, `SOLUSDT`, and `XRPUSDT`
- Candle interval: `4h`
- Paper balance: `$10` per market/strategy pair
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
python -m unittest discover -s tests -v  Run safety and configuration tests
```

## Automation

`.github/workflows/paper-trading.yml` runs 12 minutes after every four-hour Binance candle closes. Each run:

1. Downloads current public Binance candle data.
2. Runs the paper-engine safety tests.
3. Updates eligible forward-paper accounts and manages any retiring open position until it exits.
4. Rebuilds the static dashboard and Positions page.
5. Commits the updated SQLite state and pages.

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

The included signed client can check account connectivity only; it contains no order-creation method. The automated engine supports paper mode only. A production deployment would require a separate, independently reviewed execution service with quantity rounding, exchange-filter validation, exchange-side stops, reconciliation, alerts, loss limits, and a fixed-IP server.

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
