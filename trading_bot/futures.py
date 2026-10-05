from __future__ import annotations

import html
import json
from datetime import datetime, timezone

import pandas as pd

from .config import Config
from .dashboard import _interactive_chart_script
from .indicators import add_indicators
from .market import BinanceFuturesMarketData
from .storage import Storage


def _liquidity_delta_signal(df: pd.DataFrame, i: int) -> tuple[str, str] | None:
    """Confirm a 20-candle liquidity sweep with directional taker-volume delta."""
    if i < 2:
        return None
    sweep, confirm = df.iloc[i - 1], df.iloc[i]
    required = (sweep.prior_low_20, sweep.prior_high_20, sweep.atr, confirm.ema20,
                confirm.ema50, confirm.ema200, confirm.rsi, confirm.volume_median_20,
                confirm.delta_ratio, confirm.delta_quote, confirm.delta_quote_abs_median_20)
    if any(pd.isna(value) for value in required):
        return None
    volume_ok = confirm.volume > confirm.volume_median_20
    lower_wick = min(sweep.open, sweep.close) - sweep.low
    upper_wick = sweep.high - max(sweep.open, sweep.close)
    long_sweep = (confirm.ema50 > confirm.ema200 and sweep.low < sweep.prior_low_20
                  and sweep.close > sweep.prior_low_20 and lower_wick >= 0.5 * sweep.atr
                  and confirm.close > sweep.high and confirm.close > confirm.ema20
                  and 50 <= confirm.rsi <= 75 and volume_ok and confirm.delta_ratio >= 0.10
                  and confirm.delta_quote >= confirm.delta_quote_abs_median_20)
    if long_sweep:
        return "LONG", "liquidity sweep + positive delta"
    short_sweep = (confirm.ema50 < confirm.ema200 and sweep.high > sweep.prior_high_20
                   and sweep.close < sweep.prior_high_20 and upper_wick >= 0.5 * sweep.atr
                   and confirm.close < sweep.low and confirm.close < confirm.ema20
                   and 25 <= confirm.rsi <= 50 and volume_ok and confirm.delta_ratio <= -0.10
                   and -confirm.delta_quote >= confirm.delta_quote_abs_median_20)
    if short_sweep:
        return "SHORT", "liquidity sweep + negative delta"
    return None


def _signal(df: pd.DataFrame, i: int) -> tuple[str, str] | None:
    """Return a direction/setup only after trend, liquidity or delta confirmation."""
    if i < 200:
        return None
    row, previous = df.iloc[i], df.iloc[i - 1]
    values = (row.ema20, row.ema50, row.ema200, row.atr, row.rsi, row.volume_median_20,
              previous.ema20)
    if any(pd.isna(value) for value in values) or row.atr <= 0:
        return None
    volume_ok = row.volume > row.volume_median_20
    # Existing trend-pullback setup now requires candle delta to agree with the
    # direction. The independent sweep setup below adds a stricter liquidity
    # reclaim/rejection path for markets that do not make a clean EMA pullback.
    if (row.ema50 > row.ema200 and row.close > row.ema20 and previous.close <= previous.ema20
            and 50 <= row.rsi <= 70 and volume_ok and row.delta_ratio >= 0.05):
        return "LONG", "EMA pullback + positive delta"
    if (row.ema50 < row.ema200 and row.close < row.ema20 and previous.close >= previous.ema20
            and 30 <= row.rsi <= 50 and volume_ok and row.delta_ratio <= -0.05):
        return "SHORT", "EMA pullback + negative delta"
    return _liquidity_delta_signal(df, i)


def _entry_price(open_price: float, side: str, slip: float) -> float:
    return open_price * (1 + slip if side == "LONG" else 1 - slip)


def _exit_price(open_price: float, trigger: float, side: str, reason: str, slip: float) -> float:
    """Use a conservative side-aware protective/target fill including gaps."""
    if side == "LONG":
        base = min(open_price, trigger) if reason in {"stop", "liquidation"} else max(open_price, trigger)
        return base * (1 - slip)
    base = max(open_price, trigger) if reason in {"stop", "liquidation"} else min(open_price, trigger)
    return base * (1 + slip)


def _close(storage: Storage, position: dict, price: float, reason: str, candle_time: str, fee_rate: float) -> float:
    direction = 1 if position["side"] == "LONG" else -1
    notional = position["quantity"] * price
    fee = notional * fee_rate
    pnl = direction * position["quantity"] * (price - position["entry_price"])
    wallet = storage.futures_wallet()
    cash = wallet["cash"] + position["margin"] + pnl - fee
    storage.set_futures_wallet(cash)
    storage.save_futures_trade({
        "symbol": position["symbol"], "side": position["side"], "action": "EXIT", "price": price,
        "quantity": position["quantity"], "margin": position["margin"], "fee": fee, "realized_pnl": pnl - fee,
        "funding_paid": position["funding_paid"], "reason": reason, "candle_time": candle_time,
    })
    storage.clear_futures_position()
    return pnl - fee - position["funding_paid"]


def futures_paper_cycle(cfg: Config, storage: Storage, market: BinanceFuturesMarketData) -> list[str]:
    """Advance one shared-wallet, public-data-only Futures paper simulation."""
    options = cfg.futures_paper
    if not options["enabled"]:
        return ["Futures paper lab is disabled in config.json."]
    storage.initialize_futures_wallet(float(options["starting_balance"]))
    messages: list[str] = []
    completed: dict[str, pd.DataFrame] = {}
    now = datetime.now(timezone.utc)
    for symbol in options["symbols"]:
        raw = market.candles(symbol, options["interval"], limit=300)
        raw = raw[raw["close_time"] <= now].reset_index(drop=True)
        if len(raw) < 201:
            raise RuntimeError(f"Not enough completed futures candles for {symbol}")
        storage.save_futures_candles(symbol, raw)
        df = add_indicators(raw)
        completed[symbol] = df
        row = df.iloc[-1]
        try:
            premium = market.premium_index(symbol)
        except Exception:
            premium = {"mark_price": None, "funding_rate": None, "next_funding_time": None}
        storage.save_futures_snapshot({"symbol": symbol, "candle_time": str(row.open_time), "close": row.close, **premium})

    position = storage.futures_position()
    pending = storage.futures_pending_order()
    if pending and position is None:
        df = completed[pending["symbol"]]
        row = df.iloc[-1]
        candle_time = str(row.open_time)
        if candle_time > pending["signal_time"]:
            wallet = storage.futures_wallet()
            margin = min(float(options["margin_per_trade"]), wallet["cash"])
            price = _entry_price(row.open, pending["side"], float(options["slippage_rate"]))
            notional = margin * float(options["leverage"])
            fee = notional * float(options["fee_rate"])
            if margin > 0 and notional >= float(options["minimum_notional"]) and wallet["cash"] >= margin + fee:
                atr = float(df.iloc[-2].atr)
                stop = price - 2 * atr if pending["side"] == "LONG" else price + 2 * atr
                target = price + 3.5 * atr if pending["side"] == "LONG" else price - 3.5 * atr
                position = {"symbol": pending["symbol"], "side": pending["side"], "quantity": notional / price,
                            "entry_price": price, "stop_price": stop, "take_profit": target, "margin": margin,
                            "high_water": price, "low_water": price, "entry_time": candle_time,
                            "last_candle_time": None, "last_funding_time": None, "funding_paid": 0.0,
                            "pending_exit": 0}
                storage.set_futures_wallet(wallet["cash"] - margin - fee)
                storage.save_futures_position(position)
                storage.save_futures_trade({"symbol": position["symbol"], "side": position["side"], "action": "ENTRY",
                                            "price": price, "quantity": position["quantity"], "margin": margin,
                                            "fee": fee, "realized_pnl": None, "funding_paid": 0.0,
                                            "reason": "confirmed_signal", "candle_time": candle_time})
                messages.append(f"{position['symbol']} {position['side']}: paper entry at {price:.4f}; stop {stop:.4f}; target {target:.4f}")
            else:
                messages.append(f"{pending['symbol']} {pending['side']}: entry skipped (paper wallet/minimum notional)")
            storage.clear_futures_pending_order()

    position = storage.futures_position()
    if position:
        df = completed[position["symbol"]]
        row = df.iloc[-1]
        candle_time = str(row.open_time)
        # A trend exit was discovered from the prior completed candle. It fills
        # at this candle's open before its intrabar high/low are evaluated.
        if position["pending_exit"] and position["last_candle_time"] < candle_time:
            price = (_entry_price(row.open, "SHORT" if position["side"] == "LONG" else "LONG",
                                  float(options["slippage_rate"])))
            outcome = _close(storage, position, price, "trend_exit", candle_time, float(options["fee_rate"]))
            messages.append(f"{position['symbol']} {position['side']}: trend exit closed, P&L {outcome:.4f}")
            position = None
        elif position["last_candle_time"] != candle_time:
            position["last_candle_time"] = candle_time
            side = position["side"]
            # Stops are checked before targets when both are crossed in an OHLC bar.
            if (side == "LONG" and row.low <= position["stop_price"]) or (side == "SHORT" and row.high >= position["stop_price"]):
                price = _exit_price(row.open, position["stop_price"], side, "stop", float(options["slippage_rate"]))
                outcome = _close(storage, position, price, "stop", candle_time, float(options["fee_rate"]))
                messages.append(f"{position['symbol']} {side}: stop closed, P&L {outcome:.4f}")
                position = None
            elif (side == "LONG" and row.high >= position["take_profit"]) or (side == "SHORT" and row.low <= position["take_profit"]):
                price = _exit_price(row.open, position["take_profit"], side, "target", float(options["slippage_rate"]))
                outcome = _close(storage, position, price, "target", candle_time, float(options["fee_rate"]))
                messages.append(f"{position['symbol']} {side}: target closed, P&L {outcome:.4f}")
                position = None
            else:
                # A trailing stop becomes active only on the next completed candle.
                if side == "LONG":
                    position["high_water"] = max(position["high_water"], float(row.high))
                    position["stop_price"] = max(position["stop_price"], position["high_water"] - float(options["trailing_stop_atr"]) * float(row.atr))
                    trend_failed = row.close < row.ema20
                else:
                    position["low_water"] = min(position["low_water"], float(row.low))
                    position["stop_price"] = min(position["stop_price"], position["low_water"] + float(options["trailing_stop_atr"]) * float(row.atr))
                    trend_failed = row.close > row.ema20
                if trend_failed:
                    position["pending_exit"] = 1
                storage.save_futures_position(position)

    if storage.futures_position() is None and storage.futures_pending_order() is None:
        for symbol in options["symbols"]:
            df = completed[symbol]
            signal = _signal(df, len(df) - 1)
            snapshot = next(item for item in storage.futures_snapshots() if item["symbol"] == symbol)
            funding = snapshot.get("funding_rate")
            if signal and (funding is None or abs(funding) <= float(options["max_entry_funding_rate"])):
                side, setup = signal
                storage.set_futures_pending_order(symbol, side, str(df.iloc[-1].open_time))
                messages.append(f"{symbol} {side}: {setup} signal queued for next 4h open")
                break
    return messages


def _money(value: float | None) -> str:
    return "—" if value is None else f"${value:,.4f}" if abs(value) < 100 else f"${value:,.2f}"


def _chart(symbol: str, candles: list[dict], position: dict | None) -> str:
    if not candles:
        return "<p>No stored futures candles yet.</p>"
    chart_frame = pd.DataFrame(candles)
    chart_frame["taker_base"] = 0.0
    chart_frame["taker_quote"] = 0.0
    chart_frame["quote_volume"] = chart_frame["close"] * chart_frame["volume"]
    df = add_indicators(chart_frame)
    payload_candles = [{"time": str(candle["candle_time"]), "open": candle["open"], "high": candle["high"],
                        "low": candle["low"], "close": candle["close"], "volume": candle["volume"]}
                       for candle in candles]
    payload = {"symbol": symbol, "candles": payload_candles, "levels": [], "zones": [], "indicators": {
        "EMA 20": {"colour": "#78a9ff", "values": [None if pd.isna(v) else float(v) for v in df.ema20]},
        "EMA 50": {"colour": "#a78bfa", "values": [None if pd.isna(v) else float(v) for v in df.ema50]},
        "EMA 200": {"colour": "#fbc96a", "values": [None if pd.isna(v) else float(v) for v in df.ema200]},
    }}
    if position:
        payload["levels"] = [
            {"price": position["entry_price"], "colour": "#fbc96a", "kind": "ENTRY", "name": position["side"]},
            {"price": position["stop_price"], "colour": "#ff7b8b", "kind": "STOP", "name": "protective stop"},
            {"price": position["take_profit"], "colour": "#55d998", "kind": "TARGET", "name": "take profit"},
            {"price": float(df.high.tail(20).max()), "colour": "#fb7185", "kind": "R20", "name": "resistance"},
            {"price": float(df.low.tail(20).min()), "colour": "#55d998", "kind": "S20", "name": "support"},
        ]
        payload["zones"] = [{"entry": position["entry_price"], "stop": position["stop_price"],
                             "target": position["take_profit"], "label": "take profit"}]
    data = json.dumps(payload, separators=(",", ":")).replace("<", "\\u003c")
    return f"""<div class='interactive-chart'><div class='chart-controls'><div><strong>{html.escape(symbol)} futures paper chart</strong><span>Actual simulated entry, stop, target, EMA 20/50/200, S20/R20 · scroll to zoom · drag to pan</span></div><div class='chart-buttons'><button data-window='30'>30</button><button data-window='90'>90</button><button data-window='full'>Full</button></div></div><div class='chart-canvas-wrap'><canvas></canvas><div class='chart-tooltip' hidden></div></div><script type='application/json' class='chart-data'>{data}</script></div>"""


def render_futures_static(cfg: Config, output: str = "docs/futures.html") -> None:
    storage = Storage(cfg.database)
    try:
        storage.initialize_futures_wallet(float(cfg.futures_paper["starting_balance"]))
        wallet, position = storage.futures_wallet(), storage.futures_position()
        snapshots, trades, pending = storage.futures_snapshots(), storage.recent_futures_trades(), storage.futures_pending_order()
        mark = next((row.get("mark_price") or row["close"] for row in snapshots if position and row["symbol"] == position["symbol"]), None)
        unrealized = ((1 if position and position["side"] == "LONG" else -1) * position["quantity"] * (mark - position["entry_price"])
                      if position and mark is not None else 0.0)
        chart = _chart(position["symbol"], storage.futures_candles(position["symbol"]), position) if position else "<p class='empty'>No active simulated futures position. The chart appears when a confirmed signal fills.</p>"
    finally:
        storage.close()
    position_html = (f"<tr><td>{html.escape(position['symbol'])}</td><td class='{position['side'].lower()}'>{position['side']}</td><td>{_money(position['entry_price'])}</td><td>{_money(mark)}</td><td class='red'>{_money(position['stop_price'])}</td><td class='green'>{_money(position['take_profit'])}</td><td>{_money(position['margin'])}</td><td>{_money(unrealized)}</td><td>{_money(position['funding_paid'])}</td></tr>" if position else "<tr><td colspan='9'>No open position</td></tr>")
    pending_html = (f"{html.escape(pending['symbol'])} {html.escape(pending['side'])} signal from {html.escape(pending['signal_time'])}" if pending else "No queued entry")
    trade_html = "".join(f"<tr><td>{html.escape(str(t['created_at']))}</td><td>{html.escape(t['symbol'])}</td><td>{html.escape(t['side'])}</td><td>{html.escape(t['action'])}</td><td>{_money(t['price'])}</td><td>{_money(t['realized_pnl'])}</td><td>{html.escape(t['reason'])}</td></tr>" for t in trades) or "<tr><td colspan='7'>No completed simulated trades</td></tr>"
    funding_html = "".join(f"<tr><td>{html.escape(row['symbol'])}</td><td>{_money(row['close'])}</td><td>{_money(row.get('mark_price'))}</td><td>{'—' if row.get('funding_rate') is None else f'{row["funding_rate"] * 100:.4f}%'}</td></tr>" for row in snapshots) or "<tr><td colspan='4'>Awaiting public market data</td></tr>"
    leverage = cfg.futures_paper["leverage"]
    trailing_atr = cfg.futures_paper["trailing_stop_atr"]
    page = f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Futures Paper Lab</title><style>
:root{{--bg:#090d19;--panel:#111827;--line:#29364f;--text:#edf3ff;--muted:#94a3b8;--blue:#78a9ff;--green:#55d998;--red:#ff7b8b}}*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 20% -10%,#1b3059 0,transparent 36%),var(--bg);color:var(--text);font:14px system-ui,sans-serif}}main{{max-width:1240px;margin:auto;padding:34px 24px 48px}}a{{color:var(--blue);font-weight:700;text-decoration:none}}h1{{font-size:30px;margin:10px 0 6px}}h2{{font-size:19px;margin:0}}p{{color:var(--muted);line-height:1.55}}.badge{{display:inline-block;background:#3c2616;color:#ffd28a;border-radius:999px;padding:5px 9px;font-size:11px;font-weight:800;letter-spacing:.06em}}.top{{display:flex;justify-content:space-between;gap:16px;align-items:start}}.metrics{{display:grid;grid-template-columns:repeat(4,1fr);gap:13px;margin:20px 0}}.metric,.section{{background:linear-gradient(145deg,#151f33,#111827);border:1px solid var(--line);border-radius:16px}}.metric{{padding:17px}}.metric span{{display:block;color:var(--muted);font-size:11px;text-transform:uppercase;font-weight:700}}.metric strong{{font-size:23px;display:block;margin:8px 0 2px}}.section{{padding:18px;margin-top:16px;overflow:hidden}}.table-wrap{{overflow:auto}}table{{border-collapse:collapse;width:100%;white-space:nowrap}}th,td{{padding:11px 9px;text-align:left;border-bottom:1px solid var(--line)}}th{{font-size:11px;color:var(--blue);letter-spacing:.05em}}.long,.green{{color:var(--green);font-weight:800}}.short,.red{{color:var(--red);font-weight:800}}.note{{border-left:3px solid #fbc96a;background:#292313;color:#e9d6a3;padding:10px 12px;font-size:12px}}.chart-controls{{display:flex;justify-content:space-between;gap:12px;margin-bottom:9px;color:var(--muted);font-size:12px}}.chart-controls strong{{display:block;color:var(--text);margin-bottom:3px}}.chart-buttons{{display:flex;gap:6px}}button{{border:1px solid #334463;background:#121d30;color:#b9ceef;border-radius:7px;padding:5px 9px;font-weight:700;cursor:pointer}}.chart-canvas-wrap{{position:relative;background:#0a101c;border:1px solid #202c42;border-radius:10px;overflow:hidden}}canvas{{display:block;width:100%;touch-action:none;cursor:crosshair}}.chart-tooltip{{position:absolute;z-index:2;min-width:170px;padding:8px 9px;background:#111c30ef;border:1px solid #425575;border-radius:8px;color:#cbd8ee;font-size:11px;line-height:1.55;pointer-events:none}}.empty{{padding:24px 0}}@media(max-width:760px){{main{{padding:22px 14px}}.top{{display:block}}.metrics{{grid-template-columns:repeat(2,1fr)}}.chart-controls{{flex-direction:column}}}}
</style></head><body><main><header class='top'><div><span class='badge'>PAPER FUTURES · NO ACCOUNT CONNECTION · NO ORDERS</span><h1>Futures Paper Lab</h1><p>One shared simulated wallet, long/short signals, isolated {leverage}× notional cap, and public market data only.</p></div><a href='index.html'>← Spot dashboard</a></header><div class='metrics'><div class='metric'><span>Paper wallet cash</span><strong>{_money(wallet['cash'])}</strong><small>Starting balance {_money(cfg.futures_paper['starting_balance'])}</small></div><div class='metric'><span>Leverage cap</span><strong>{leverage}×</strong><small>{_money(cfg.futures_paper['margin_per_trade'])} margin per attempt</small></div><div class='metric'><span>Open P&amp;L</span><strong>{_money(unrealized)}</strong><small>Marked from public mark price when available</small></div><div class='metric'><span>Queued setup</span><strong>{'Yes' if pending else 'No'}</strong><small>{pending_html}</small></div></div><section class='section'><h2>Open simulated position</h2><div class='table-wrap'><table><thead><tr><th>Market</th><th>Side</th><th>Entry</th><th>Mark</th><th>Stop</th><th>Take profit</th><th>Margin</th><th>Open P&amp;L</th><th>Funding paid</th></tr></thead><tbody>{position_html}</tbody></table></div></section><section class='section'><h2>Position risk / reward chart</h2><p>Green is the simulated take-profit area. Red is the simulated stop-loss area. Stop is checked first if a single OHLC candle crosses both levels.</p>{chart}</section><section class='section'><h2>Market and funding context</h2><div class='table-wrap'><table><thead><tr><th>Market</th><th>Last completed close</th><th>Public mark</th><th>Last funding rate</th></tr></thead><tbody>{funding_html}</tbody></table></div></section><section class='section'><h2>Simulated trade ledger</h2><div class='table-wrap'><table><thead><tr><th>Recorded UTC</th><th>Market</th><th>Side</th><th>Action</th><th>Price</th><th>Realized P&amp;L</th><th>Reason</th></tr></thead><tbody>{trade_html}</tbody></table></div></section><p class='note'>Entries require either an EMA 50/200 trend pullback with directional candle delta, or a 20-candle liquidity sweep confirmed by strong directional delta. Both require RSI and above-median volume. Initial stop: 2 ATR. Target: 3.5 ATR (1.75R). Trailing stop: {trailing_atr} ATR. This is a paper experiment, not a profitable-system claim or a live-trading recommendation.</p></main>{_interactive_chart_script()}</body></html>"""
    from pathlib import Path
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(page, encoding="utf-8")
