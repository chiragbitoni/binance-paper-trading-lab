# Binance Paper Trading Strategy Lab

[![Paper Trading](https://img.shields.io/badge/mode-paper%20trading-2ea44f)](https://chiragbitoni.github.io/binance-paper-trading-lab/)
[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Automated](https://img.shields.io/badge/GitHub%20Actions-every%204h-2088FF?logo=githubactions&logoColor=white)](https://github.com/chiragbitoni/binance-paper-trading-lab/actions)

A transparent, paper-first Binance strategy research bot for five liquid USDT markets. It backtests multiple rule-based Spot strategies, tracks isolated forward-paper accounts, and includes a separate capped-leverage Futures *simulation* with no account connection or order capability.

**[Open the live paper-trading dashboard](https://chiragbitoni.github.io/binance-paper-trading-lab/)**

> This project is educational software, not financial advice. Historical results do not predict future performance. Real orders are disabled by default.

## Dhan MCX live-execution module

The repository now includes a separate DhanHQ v2 MCX integration for commodity derivatives. It does not share Binance credentials or change the Binance paper engine. It uses Dhan's **Super Order** endpoint for a single protected entry, target, stop-loss, and optional trailing-stop request rather than an unprotected entry followed by a later stop order.

Live order submission is locked by default. Copy `config.json` to the ignored `config.local.json`, set positive `dhan_mcx` amounts for allocated capital, maximum daily loss, and maximum loss per trade there, then use `--config config.local.json`. To submit a real order, all four gates must pass:

1. `dhan_mcx.enabled` is true.
2. `dhan_mcx.live_order_submission` is true.
3. Private environment variables `DHAN_CLIENT_ID`, `DHAN_ACCESS_TOKEN`, and `DHAN_LIVE_ORDER_ACK=I_UNDERSTAND_DHAN_LIVE_ORDERS` are present.
4. The command includes `--confirm-live` after its broker-margin preview succeeds.

For automatic execution, a fifth layer is required: `dhan_mcx.auto_trade_enabled` plus `DHAN_AUTOMATION_ACK=I_UNDERSTAND_DHAN_AUTOMATION`. Add only active contracts to `dhan_mcx.watchlist`, each with a verified Dhan security ID, instrument code, valid lot quantity, and the contract's permitted IST session window. With auto trading disabled, `dhan-mcx auto --loop` still analyses the watchlist and emits qualified previews but never submits an order.

Example private watchlist entry (use the current Dhan instrument master; do not invent a security ID or lot size):

```json
{
  "security_id": "CURRENT_DHAN_MCX_ID",
  "instrument": "FUTCOM",
  "quantity": "CURRENT_VALID_LOT_QUANTITY",
  "interval": 5,
  "product_type": "INTRADAY",
  "entry_order_type": "LIMIT",
  "trailing_jump": 0,
  "session_start_ist": "09:00",
  "session_end_ist": "23:30"
}
```

The session window must be verified for each commodity/contract before enabling automation. The existing Super Order carries the target and stop-loss protection; do not rely on a broker's intraday square-off as a substitute for the configured stop.

Never put Dhan values in this public repository. Dhan requires an access token for API requests and requires static-IP whitelisting for order endpoints. The bot checks Dhan's margin calculator, Dhan available balance, the manually configured allocation cap, stop-based per-trade risk, allowed MCX security IDs, and open-position limit before it can submit a protected order. It intentionally refuses to infer contract lot size: choose an active instrument from Dhan's instrument master and enter its quantity in valid exchange units.

```powershell
# Read-only account status: no orders are possible.
$env:DHAN_CLIENT_ID="..."
$env:DHAN_ACCESS_TOKEN="..."
python run.py dhan-mcx status

# Completed-candle MCX analysis only; it cannot send an order.
python run.py dhan-mcx analyze --security-id YOUR_MCX_SECURITY_ID --instrument FUTCOM --interval 5 --from-date "2026-10-13 09:00:00" --to-date "2026-10-13 17:00:00"

# Repeatedly analyse a configured watchlist. With the default configuration,
# it cannot submit a live order.
python run.py --config config.local.json dhan-mcx auto --loop --poll-seconds 60

# Validate margin/risk only; no order is possible.
python run.py dhan-mcx preview --security-id YOUR_MCX_SECURITY_ID --side BUY --quantity YOUR_VALID_LOT_QUANTITY --entry 100 --stop 95 --target 110 --order-type LIMIT

# Only after completing the four live gates above.
python run.py dhan-mcx execute --security-id YOUR_MCX_SECURITY_ID --side BUY --quantity YOUR_VALID_LOT_QUANTITY --entry 100 --stop 95 --target 110 --confirm-live
```

This is a live-capable execution connector, not a promise of profit or a substitute for contract-specific research, exchange rules, or Dhan order-status reconciliation. Configure an external alert/monitor and review Dhan's current API documentation before live activation.

## Highlights

- Five parallel markets: BTC, ETH, BNB, SOL, and XRP against USDT
- One forward-paper candidate (50/200 EMA regime) plus research-only variants that are backtested but cannot create paper positions
- Two-year historical backtests and forward-paper signals with fees, slippage, and next-candle-open execution
- Return, win rate, profit factor, trade count, and maximum drawdown tracking
- Forward-paper positions and trades stored independently from backtests
- Every simulated Spot and Futures position uses a completed-candle ATR trailing stop; strategy-specific trails override the common 2.5 ATR default where configured
- Decision dashboard with portfolio equity, mark P&L, stops, candle delta, trade ledger, and research results
- Dedicated candlestick chart page that groups all open strategy positions for each ticker
- TradingView-style chart overlays: EMA 20/50/200, 20-candle support/resistance, entry, stop, and green/red risk-reward areas
- Separate USD-M Futures paper lab: long/short setup research using EMA pullbacks with candle-delta confirmation and liquidity-sweep/delta setups, a single shared simulated wallet, 3× maximum simulated leverage, ATR stops/targets, and public funding/mark context
- Binance market-data-only API; no credentials required for paper mode
- An optional always-on real-time worker: live public prices monitor paper stops/trailing stops while 4h signals remain candle-close based
- Automated four-hour updates through GitHub Actions (static demonstration only)
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
python run.py futures-paper
python run.py realtime
python run.py dashboard
```

Open `http://127.0.0.1:8765`. To keep checking for newly completed candles locally, run this in another terminal:

```powershell
python run.py realtime
```

## Configuration

Edit `config.json` to change the symbol, candle interval, simulated balance, position cap, fees, slippage, history length, or enabled strategies. The default experiment uses:

- Markets: `BTCUSDT`, `ETHUSDT`, `BNBUSDT`, `SOLUSDT`, and `XRPUSDT`
- Candle interval: `4h`
- Paper balance: `$10` per market/strategy pair
- Maximum position: `$8`
- Minimum notional: `$5`
- Historical window: `730` days
- Spot trailing-stop default: `2.5 ATR` on every strategy; strategies with their own trailing distance keep that distance
- Futures trailing-stop distance: `2.5 ATR`
- Futures simulation: one `$10` shared wallet, `$5` simulated margin per attempt, maximum `3x` notional, and a separate `4h` watchlist

Backtest statistics and forward-paper results are intentionally stored separately in `data/trading_bot.db`.

## Commands

```text
python run.py backtest               Run historical strategy tests
python run.py paper                  Process the newest completed candle once
python run.py paper --loop           Continuously watch for completed candles
python run.py futures-paper          Process the capped Futures paper lab once
python run.py realtime               Run WebSocket protective-exit monitoring plus 4h strategy maintenance
python run.py dashboard              Serve the local dashboard
python run.py render-static          Rebuild docs/index.html for GitHub Pages
python run.py render-futures-static  Rebuild docs/futures.html for GitHub Pages
python run.py check-live-connection  Test a read-only Binance connection
python -m unittest discover -s tests -v  Run safety and configuration tests
```

## Automation

`.github/workflows/paper-trading.yml` runs 12 minutes after every four-hour Binance candle closes. It is useful for the public static demonstration, but it is not an always-on execution service. Each run:

1. Downloads current public Binance candle data.
2. Runs the paper-engine safety tests.
3. Updates eligible Spot paper accounts and the separate capped Futures paper simulation.
4. Rebuilds the static dashboard, Positions page, and Futures Paper Lab.
5. Commits the updated SQLite state and pages.

The workflow uses no Binance credentials and never invokes a live-order client. The Futures component uses public USD-M candles and mark/funding data only; it cannot submit a futures order.

## Real-time paper operation

`python run.py realtime` runs two separate paper-only loops:

1. Public Binance Spot WebSocket prices continuously mark each position, tighten its ATR trailing stop, and immediately record a simulated protective exit if the observed price crosses its stop or Futures target.
2. Completed four-hour candles are refreshed every minute. A strategy can create or close a *signal* only from completed data; a queued entry fills at the following 4h candle open.

Run it on an always-on host to keep the monitor alive. The prepared Oracle Always Free VM service files and secure dashboard tunnel instructions are in [`deploy/oracle/README.md`](deploy/oracle/README.md). The live monitor uses the public Spot quote as a labelled approximation for a Futures-paper exit if USD-M market data is unavailable; it never represents an exchange order or guaranteed fill.

If Binance restricts USD-M market-data access from the GitHub Actions region, the Futures paper cycle falls back to the project's public Spot candle feed so the scheduler can still run. The Futures dashboard labels that state as **Spot proxy (USD-M unavailable)** and leaves mark/funding fields blank; it is intentionally not presented as Futures data.

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

Trading can lose money. Backtests are sensitive to market regime, assumptions, execution quality, fees, funding, and overfitting. The Futures panel is only a simplified paper simulation—not a Binance liquidation or margin model. Validate strategies with a meaningful forward-paper sample before considering real capital; do not transfer its settings to a live leveraged account.
