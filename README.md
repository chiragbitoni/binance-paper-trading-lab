# Binance Paper Trading Strategy Lab

This project backtests several transparent strategies and runs each one in an independent paper account. The default configuration uses BTC/USDT, four-hour candles, and $10 per strategy. Real orders are locked.

## Quick start

```powershell
python -m pip install -r requirements.txt
python run.py backtest
python run.py paper
python run.py dashboard
```

Open <http://127.0.0.1:8765> after starting the dashboard. Use `python run.py paper --loop` in another terminal to check each newly completed candle automatically.

Historical results and paper trades are stored in `data/trading_bot.db`. Backtest statistics and forward-paper statistics are intentionally separate.

## Strategies

- Trend + Donchian breakout
- Slow 200-EMA regime
- 50/200 EMA regime
- EMA trend pullback
- Quiet-to-active volatility breakout
- Trend-filtered Bollinger/RSI recovery
- Buy-and-hold historical benchmark using the same $8 position cap

Every backtest applies configured fees, slippage, an $8 maximum position, a $5 minimum notional, next-candle execution for close-based signals, and volatility stops.

## Connecting Binance later

Leave `mode` set to `paper` during research. API credentials are read only from environment variables and are never written to the database:

```powershell
$env:BINANCE_API_KEY="your-read-or-trade-key"
$env:BINANCE_API_SECRET="your-secret"
python run.py check-live-connection
```

Use an API key restricted to a trusted IP and keep withdrawals disabled. The included live client can check account connectivity. Its order method is additionally locked behind `LIVE_TRADING_ACK=I_UNDERSTAND_REAL_ORDERS`; the paper engine never calls that method. Review exchange filters, quantity rounding, live stop handling, and the selected strategy before wiring live execution.

## Free scheduled paper trading on GitHub

The included `.github/workflows/paper-trading.yml` workflow runs 12 minutes after each four-hour Binance candle closes. It updates the SQLite paper state, renders `docs/index.html`, and commits both files back to the repository. It uses no Binance credentials.

For a public repository, enable GitHub Pages under **Settings → Pages**, select **Deploy from a branch**, choose the default branch and `/docs`, then save. Do not add Binance API credentials to this paper workflow or commit a `.env` file.

## Important interpretation

Win rate alone does not measure strategy quality. Compare net return, profit factor, drawdown, trade count, and forward-paper behavior. Historical performance is descriptive and does not establish future profitability.
