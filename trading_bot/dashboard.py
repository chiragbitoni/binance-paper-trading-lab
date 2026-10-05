from __future__ import annotations

import html
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pandas as pd

from .config import Config
from .indicators import add_indicators
from .storage import Storage
from .strategies import STRATEGIES


def _money(value: float) -> str:
    return f"${value:,.2f}"


def _signed_money(value: float) -> str:
    return f"{value:+,.2f}"


def _pnl_class(value: float) -> str:
    return "positive" if value > 0 else "negative" if value < 0 else "neutral"


def _active_positions(cfg: Config, accounts: list[dict], snapshots: list[dict]) -> tuple[list[dict], float]:
    snapshots_by_symbol = {snapshot["symbol"]: snapshot for snapshot in snapshots}
    positions: list[dict] = []
    marked_equity = 0.0
    for account in accounts:
        snapshot = snapshots_by_symbol.get(account["symbol"])
        close = snapshot["close"] if snapshot else None
        if account["quantity"] > 0 and close is not None:
            position_value = account["quantity"] * close * (1 - cfg.fee_rate)
            account_equity = account["cash"] + position_value
            pnl = account_equity - cfg.starting_balance
            positions.append({
                **account, "close": close, "equity": account_equity, "pnl": pnl,
                "forward_status": "candidate" if account["strategy_key"] in cfg.enabled_strategies else "retiring",
            })
            marked_equity += account_equity
        else:
            marked_equity += account["cash"]
    return positions, marked_equity


def _page(cfg: Config, storage: Storage) -> str:
    backtests = storage.latest_backtests()
    accounts = storage.paper_accounts()
    trades = storage.recent_paper_trades()
    snapshots = storage.market_snapshots()
    active_positions, marked_equity = _active_positions(cfg, accounts, snapshots)
    pending_orders = [account for account in accounts if account.get("pending_order")]
    starting_equity = len(accounts) * cfg.starting_balance
    total_pnl = marked_equity - starting_equity
    realized_pnl = sum(trade["realized_pnl"] or 0.0 for trade in trades if trade["side"] == "SELL")
    snapshot_updated = max((snapshot["updated_at"] for snapshot in snapshots), default="Waiting for first completed candle")

    snapshot_rows = "".join(
        f"<tr><td><strong>{html.escape(snapshot['symbol'])}</strong></td><td>{_money(snapshot['close'])}</td>"
        f"<td class='{_pnl_class(snapshot['delta_ratio'] or 0)}'>{(snapshot['delta_ratio'] or 0) * 100:+.1f}%</td>"
        f"<td class='{_pnl_class(snapshot['delta_quote'])}'>{_signed_money(snapshot['delta_quote'])}</td>"
        f"<td class='{_pnl_class(snapshot['cvd_quote_24h'] or 0)}'>{_signed_money(snapshot['cvd_quote_24h'] or 0)}</td>"
        f"<td>{html.escape(snapshot['candle_time'])}</td></tr>"
        for snapshot in snapshots
    ) or "<tr><td colspan='6'>Waiting for a completed candle.</td></tr>"

    position_rows = "".join(
        f"<tr><td><strong>{html.escape(position['symbol'])}</strong></td>"
        f"<td>{html.escape(STRATEGIES[position['strategy_key']].name)}</td>"
        f"<td><span class='tag {position['forward_status']}'>{position['forward_status']}</span></td>"
        f"<td>{_money(position['entry_price'])}</td><td>{_money(position['close'])}</td>"
        f"<td>{_money(position['stop_price'])}</td>"
        f"<td class='{_pnl_class(position['pnl'])}'>{_signed_money(position['pnl'])} ({position['pnl'] / cfg.starting_balance * 100:+.2f}%)</td>"
        f"<td>{_money(position['equity'])}</td></tr>"
        for position in active_positions
    ) or "<tr><td colspan='7'>No active paper positions.</td></tr>"

    pending_rows = "".join(
        f"<tr><td><strong>{html.escape(account['symbol'])}</strong></td>"
        f"<td>{html.escape(STRATEGIES[account['strategy_key']].name)}</td>"
        f"<td><span class='tag {'buy' if account['pending_order'] == 'entry' else 'sell'}>"
        f"{'BUY' if account['pending_order'] == 'entry' else 'SELL'}</span></td>"
        f"<td>Next completed candle open</td></tr>"
        for account in pending_orders
    ) or "<tr><td colspan='4'>No queued paper orders.</td></tr>"

    backtest_rows = "".join(
        f"<tr><td>{html.escape(result['symbol'])}</td>"
        f"<td>{html.escape(STRATEGIES.get(result['strategy_key'], STRATEGIES['trend_breakout']).name)}</td>"
        f"<td class='{_pnl_class(result['total_return_pct'])}'>{result['total_return_pct']:+.2f}%</td>"
        f"<td>{result['trades']}</td><td>{result['wins']}/{result['losses']}</td>"
        f"<td>{result['win_rate']:.1f}%</td><td>{result['profit_factor'] if result['profit_factor'] is not None else '—'}</td>"
        f"<td>{result['max_drawdown_pct']:.2f}%</td></tr>"
        for result in backtests
    ) or "<tr><td colspan='8'>Run a backtest to populate results.</td></tr>"

    trade_rows = "".join(
        f"<tr><td>{trade['created_at']}</td><td><strong>{html.escape(trade['symbol'])}</strong></td>"
        f"<td>{html.escape(STRATEGIES.get(trade['strategy_key'], STRATEGIES['trend_breakout']).name)}</td>"
        f"<td><span class='tag {trade['side'].lower()}'>{trade['side']}</span></td>"
        f"<td>{_money(trade['price'])}</td><td>{trade['quantity']:.8f}</td>"
        f"<td class='{_pnl_class(trade['realized_pnl'] or 0)}'>{_signed_money(trade['realized_pnl']) if trade['realized_pnl'] is not None else '—'}</td>"
        f"<td>{html.escape(trade['reason'])}</td></tr>"
        for trade in trades
    ) or "<tr><td colspan='8'>No paper trades yet.</td></tr>"

    return f"""<!doctype html><html lang='en'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width, initial-scale=1'><meta http-equiv='refresh' content='60'>
<title>Binance Paper Trading Lab</title><style>
:root{{color-scheme:dark;--bg:#090d19;--panel:#111827;--panel2:#151f33;--line:#27344e;--text:#edf3ff;--muted:#94a3b8;--blue:#78a9ff;--green:#55d998;--red:#ff7b8b;--yellow:#fbc96a}}
*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 20% -10%,#1b3059 0,transparent 36%),var(--bg);color:var(--text);font:14px Inter,ui-sans-serif,system-ui,-apple-system,Segoe UI,sans-serif}}
main{{max-width:1380px;margin:auto;padding:34px 24px 48px}}.top{{display:flex;justify-content:space-between;align-items:flex-start;gap:18px;margin-bottom:25px}}h1{{font-size:30px;letter-spacing:-.7px;margin:8px 0}}h2{{font-size:17px;margin:0 0 6px}}p{{color:var(--muted);line-height:1.55;margin:0}}.position-link{{display:inline-block;margin-top:12px;color:var(--blue);font-weight:700;text-decoration:none}}.position-link:hover{{text-decoration:underline}}.badge,.tag{{display:inline-block;border-radius:999px;font-size:11px;font-weight:750;letter-spacing:.06em;padding:5px 9px}}.badge{{background:#123c2a;color:#75e6ae}}.tag.buy,.tag.candidate{{background:#123c2a;color:#75e6ae}}.tag.sell,.tag.retiring{{background:#4a1f2b;color:#ff9aa8}}.updated{{font-size:12px;color:var(--muted);text-align:right}}.updated strong{{color:var(--text);display:block;font-size:13px;margin-top:4px}}
.metrics{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;margin:18px 0}}.metric,.section{{background:linear-gradient(145deg,var(--panel2),var(--panel));border:1px solid var(--line);border-radius:16px}}.metric{{padding:18px}}.metric-label{{font-size:12px;color:var(--muted);font-weight:650;text-transform:uppercase;letter-spacing:.06em}}.metric-value{{font-size:25px;font-weight:750;letter-spacing:-.5px;margin:8px 0 4px}}.metric-note{{font-size:12px;color:var(--muted)}}.positive{{color:var(--green)}}.negative{{color:var(--red)}}.neutral{{color:var(--muted)}}
.section{{padding:18px;margin-top:16px;overflow:hidden}}.section-head{{display:flex;justify-content:space-between;gap:18px;align-items:baseline;margin-bottom:14px}}.section-head p{{font-size:12px;max-width:730px}}.table-wrap{{overflow-x:auto}}table{{width:100%;border-collapse:collapse;white-space:nowrap}}th,td{{padding:11px 10px;text-align:left;border-bottom:1px solid var(--line)}}th{{font-size:11px;color:var(--blue);text-transform:uppercase;letter-spacing:.06em}}td{{font-variant-numeric:tabular-nums}}tr:last-child td{{border-bottom:0}}.footnote{{margin-top:17px;border-left:3px solid var(--yellow);padding:10px 12px;background:#292313;color:#e9d6a3;font-size:12px;line-height:1.5}}
@media(max-width:900px){{.metrics{{grid-template-columns:repeat(2,minmax(0,1fr))}}}}@media(max-width:620px){{main{{padding:22px 14px}}.top{{display:block}}.updated{{text-align:left;margin-top:16px}}.metrics{{grid-template-columns:1fr 1fr;gap:9px}}.metric{{padding:14px}}.metric-value{{font-size:20px}}.section{{padding:14px}}h1{{font-size:25px}}}}
</style></head><body><main>
<header class='top'><div><span class='badge'>PAPER MODE · LIVE ORDERS LOCKED</span><h1>Binance Strategy Lab</h1><p>{', '.join(cfg.symbols)} · completed {cfg.interval} candles · isolated $10 strategy accounts</p><p><a class='position-link' href='positions.html'>View spot position charts →</a> &nbsp; <a class='position-link' href='futures.html'>Open Futures Paper Lab →</a></p></div><div class='updated'>Last database update (UTC)<strong>{html.escape(str(snapshot_updated))}</strong></div></header>
<div class='metrics'><article class='metric'><div class='metric-label'>Paper equity</div><div class='metric-value'>{_money(marked_equity)}</div><div class='metric-note'>Across {len(accounts)} isolated accounts</div></article><article class='metric'><div class='metric-label'>Forward P&amp;L</div><div class='metric-value {_pnl_class(total_pnl)}'>{_signed_money(total_pnl)}</div><div class='metric-note'>Marked using latest completed candle</div></article><article class='metric'><div class='metric-label'>Active positions</div><div class='metric-value'>{len(active_positions)}</div><div class='metric-note'>{len(pending_orders)} queued order{'s' if len(pending_orders) != 1 else ''}</div></article><article class='metric'><div class='metric-label'>Realized P&amp;L</div><div class='metric-value {_pnl_class(realized_pnl)}'>{_signed_money(realized_pnl)}</div><div class='metric-note'>{sum(1 for trade in trades if trade['side'] == 'SELL')} closed trades</div></article></div>
<section class='section'><div class='section-head'><div><h2>Open paper positions</h2><p>Forward-paper positions only. Mark P&amp;L includes an estimated fee for closing at the latest completed 4h close. Retiring strategies cannot open again but keep their exit protection until closed.</p></div></div><div class='table-wrap'><table><thead><tr><th>Market</th><th>Strategy</th><th>Status</th><th>Entry</th><th>Last close</th><th>Stop</th><th>Unrealized P&amp;L</th><th>Marked equity</th></tr></thead><tbody>{position_rows}</tbody></table></div></section>
<section class='section'><div class='section-head'><div><h2>Queued paper orders</h2><p>A completed-candle signal does not fill immediately. It is recorded here and filled at the next completed candle's open, unless it is cancelled by an earlier protective exit.</p></div></div><div class='table-wrap'><table><thead><tr><th>Market</th><th>Strategy</th><th>Order</th><th>Planned fill</th></tr></thead><tbody>{pending_rows}</tbody></table></div></section>
<section class='section'><div class='section-head'><div><h2>Latest candle delta</h2><p>Binance Spot taker-volume proxy. Positive means relatively more taker-buy volume; 24h CVD sums six 4h candle deltas.</p></div></div><div class='table-wrap'><table><thead><tr><th>Market</th><th>Close</th><th>Delta ratio</th><th>Candle delta (USDT)</th><th>24h CVD (USDT)</th><th>Candle open</th></tr></thead><tbody>{snapshot_rows}</tbody></table></div></section>
<section class='section'><div class='section-head'><div><h2>Forward trade ledger</h2><p>Actual simulated entries and exits—not historical backtest trades.</p></div></div><div class='table-wrap'><table><thead><tr><th>Recorded</th><th>Market</th><th>Strategy</th><th>Side</th><th>Price</th><th>Quantity</th><th>Realized P&amp;L</th><th>Reason</th></tr></thead><tbody>{trade_rows}</tbody></table></div></section>
<section class='section'><div class='section-head'><div><h2>Strategy laboratory</h2><p>Historical two-year backtests. Research-only strategies are displayed here but cannot open paper positions.</p></div></div><div class='table-wrap'><table><thead><tr><th>Market</th><th>Strategy</th><th>Return</th><th>Trades</th><th>W/L</th><th>Win rate</th><th>Profit factor</th><th>Max drawdown</th></tr></thead><tbody>{backtest_rows}</tbody></table></div><div class='footnote'>Historical results are not a forecast. Do not use the dashboard as a live-trading instruction; it is a paper-trading research record.</div></section>
</main></body></html>"""


def _candlestick_chart(symbol: str, candles: list[dict], positions: list[dict]) -> str:
    """Render a dependency-free canvas chart with zoom, pan, and OHLC inspection."""
    if len(candles) < 2:
        return "<p class='chart-empty'>Chart data will appear after the next completed paper cycle.</p>"

    chart_frame = pd.DataFrame(candles)
    # Stored chart candles keep OHLCV only; delta fields are not needed by the
    # visual indicators, but add_indicators also calculates the dashboard proxy.
    chart_frame["taker_base"] = 0.0
    chart_frame["taker_quote"] = 0.0
    chart_frame["quote_volume"] = chart_frame["close"] * chart_frame["volume"]
    indicator_frame = add_indicators(chart_frame)
    palette = ["#fbc96a", "#a78bfa", "#22d3ee", "#fb7185"]
    levels: list[dict] = []
    zones: list[dict] = []
    legend: list[str] = []
    for index, position in enumerate(positions):
        colour = palette[index % len(palette)]
        name = STRATEGIES[position["strategy_key"]].name
        levels.extend([
            {"price": position["entry_price"], "colour": colour, "kind": "ENTRY", "name": name},
            {"price": position["stop_price"], "colour": colour, "kind": "STOP", "name": name},
        ])
        risk = position["entry_price"] - position["stop_price"]
        # The spot engine has a trailing protective stop but no fixed take-profit
        # order.  The target is deliberately labelled as a visual 1.75R reference,
        # rather than suggesting that it is an executable order.
        zones.append({"entry": position["entry_price"], "stop": position["stop_price"],
                      "target": position["entry_price"] + 1.75 * risk,
                      "label": "1.75R reference"})
        legend.append(
            f"<li><i style='background:{colour}'></i><strong>{html.escape(name)}</strong> · entry {_money(position['entry_price'])} · stop {_money(position['stop_price'])} · <span class='{_pnl_class(position['pnl'])}'>{_signed_money(position['pnl'])}</span></li>"
        )
    levels.extend([
        {"price": float(indicator_frame["high"].tail(20).max()), "colour": "#fb7185", "kind": "R20", "name": "20-candle resistance"},
        {"price": float(indicator_frame["low"].tail(20).min()), "colour": "#55d998", "kind": "S20", "name": "20-candle support"},
    ])
    indicators = {
        "EMA 20": {"colour": "#78a9ff", "values": [None if pd.isna(value) else float(value) for value in indicator_frame["ema20"]]},
        "EMA 50": {"colour": "#a78bfa", "values": [None if pd.isna(value) else float(value) for value in indicator_frame["ema50"]]},
        "EMA 200": {"colour": "#fbc96a", "values": [None if pd.isna(value) else float(value) for value in indicator_frame["ema200"]]},
    }
    payload = {
        "symbol": symbol,
        "candles": [{"time": str(candle["candle_time"]), "open": candle["open"], "high": candle["high"],
                     "low": candle["low"], "close": candle["close"], "volume": candle["volume"]}
                    for candle in candles],
        "levels": levels,
        "zones": zones,
        "indicators": indicators,
    }
    data = json.dumps(payload, separators=(",", ":")).replace("<", "\\u003c")
    return f"""<div class='interactive-chart' data-symbol='{html.escape(symbol)}'>
    <div class='chart-controls'><div><strong>{len(candles)} stored {html.escape(symbol)} candles</strong><span>EMA 20 / 50 / 200 · 20-candle support/resistance · green/red 1.75R reference zone · scroll to zoom · drag to pan · hover for OHLC</span></div><div class='chart-buttons'><button type='button' data-window='30'>30</button><button type='button' data-window='90'>90</button><button type='button' data-window='full'>Full</button></div></div>
    <div class='chart-canvas-wrap'><canvas aria-label='{html.escape(symbol)} interactive OHLC candlestick chart'></canvas><div class='chart-tooltip' hidden></div></div>
    <script type='application/json' class='chart-data'>{data}</script></div><ul class='position-legend'>{''.join(legend)}</ul>"""


def _interactive_chart_script() -> str:
    """Canvas controls shared by every static position chart."""
    return """<script>
(() => {
  const clamp = (value, minimum, maximum) => Math.max(minimum, Math.min(maximum, value));
  document.querySelectorAll('.interactive-chart').forEach((root) => {
    const payload = JSON.parse(root.querySelector('.chart-data').textContent);
    const candles = payload.candles;
    const canvas = root.querySelector('canvas');
    const tooltip = root.querySelector('.chart-tooltip');
    const ctx = canvas.getContext('2d');
    let visible = candles.length;
    let end = candles.length;
    let drag = null;

    const render = () => {
      const bounds = canvas.getBoundingClientRect();
      const width = Math.max(320, Math.floor(bounds.width));
      const height = 440;
      const ratio = window.devicePixelRatio || 1;
      canvas.width = width * ratio;
      canvas.height = height * ratio;
      canvas.style.height = `${height}px`;
      ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
      ctx.clearRect(0, 0, width, height);

      const left = 12, right = 68, top = 18, bottom = 30;
      const plotWidth = width - left - right, plotHeight = height - top - bottom;
      const start = clamp(end - visible, 0, Math.max(0, candles.length - visible));
      const view = candles.slice(start, end);
      const indicatorPrices = Object.values(payload.indicators || {}).flatMap((indicator) =>
        indicator.values.slice(start, end).filter((value) => value !== null));
      const zonePrices = (payload.zones || []).flatMap((zone) => [zone.entry, zone.stop, zone.target]);
      const prices = view.flatMap((candle) => [candle.low, candle.high])
        .concat(payload.levels.map((line) => line.price), indicatorPrices, zonePrices);
      const minimum = Math.min(...prices), maximum = Math.max(...prices);
      const padding = Math.max((maximum - minimum) * 0.08, maximum * 0.001);
      const low = minimum - padding, high = maximum + padding, range = Math.max(high - low, 0.000001);
      const y = (price) => top + (high - price) * plotHeight / range;

      ctx.fillStyle = '#0a101c';
      ctx.fillRect(0, 0, width, height);
      ctx.strokeStyle = '#243149'; ctx.lineWidth = 1; ctx.setLineDash([3, 5]);
      ctx.font = '11px system-ui'; ctx.fillStyle = '#8fa0bd'; ctx.textAlign = 'left';
      for (let grid = 0; grid <= 4; grid += 1) {
        const gridY = top + plotHeight * grid / 4;
        const price = high - range * grid / 4;
        ctx.beginPath(); ctx.moveTo(left, gridY); ctx.lineTo(width - right, gridY); ctx.stroke();
        ctx.fillText(price.toLocaleString(undefined, {maximumFractionDigits: 2}), width - right + 6, gridY + 4);
      }
      ctx.setLineDash([]);
      // Draw the risk/reward blocks before the candles so price action and levels
      // remain readable. These are references for existing Spot positions, not
      // take-profit orders.
      (payload.zones || []).forEach((zone) => {
        const entryY = y(zone.entry), stopY = y(zone.stop), targetY = y(zone.target);
        ctx.globalAlpha = 0.18;
        ctx.fillStyle = '#55d998';
        ctx.fillRect(left, Math.min(targetY, entryY), plotWidth, Math.abs(entryY - targetY));
        ctx.fillStyle = '#ff4f67';
        ctx.fillRect(left, Math.min(entryY, stopY), plotWidth, Math.abs(stopY - entryY));
        ctx.globalAlpha = 1;
        ctx.font = '10px system-ui'; ctx.textAlign = 'left';
        ctx.fillStyle = '#83eabb'; ctx.fillText('POTENTIAL REWARD · ' + zone.label, left + 7, Math.min(targetY, entryY) + 13);
        ctx.fillStyle = '#ff9dab'; ctx.fillText('RISK · STOP', left + 7, Math.min(entryY, stopY) + 13);
      });
      Object.entries(payload.indicators || {}).forEach(([name, indicator]) => {
        const values = indicator.values.slice(start, end);
        ctx.strokeStyle = indicator.colour; ctx.lineWidth = 1.4; ctx.globalAlpha = 0.92;
        ctx.beginPath();
        let drawing = false;
        values.forEach((value, index) => {
          if (value === null) { drawing = false; return; }
          const x = left + (index + 0.5) * (plotWidth / view.length);
          if (!drawing) { ctx.moveTo(x, y(value)); drawing = true; }
          else { ctx.lineTo(x, y(value)); }
        });
        ctx.stroke(); ctx.globalAlpha = 1;
      });
      const step = plotWidth / view.length, body = Math.max(1, step * 0.65);
      view.forEach((candle, index) => {
        const x = left + (index + 0.5) * step;
        const colour = candle.close >= candle.open ? '#55d998' : '#ff7b8b';
        const openY = y(candle.open), closeY = y(candle.close);
        ctx.strokeStyle = colour; ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(x, y(candle.high)); ctx.lineTo(x, y(candle.low)); ctx.stroke();
        ctx.fillStyle = colour;
        ctx.fillRect(x - body / 2, Math.min(openY, closeY), body, Math.max(1, Math.abs(closeY - openY)));
      });
      payload.levels.forEach((line) => {
        const lineY = y(line.price);
        ctx.strokeStyle = line.colour; ctx.globalAlpha = line.kind === 'ENTRY' ? 1 : 0.75;
        ctx.setLineDash(line.kind === 'ENTRY' ? [7, 5] : [2, 5]);
        ctx.beginPath(); ctx.moveTo(left, lineY); ctx.lineTo(width - right, lineY); ctx.stroke();
        ctx.setLineDash([]); ctx.globalAlpha = 1; ctx.fillStyle = line.colour; ctx.textAlign = 'right';
        ctx.fillText(`${line.kind} $${line.price.toLocaleString(undefined, {maximumFractionDigits: 2})}`, width - right - 4, lineY - 5);
      });
      ctx.font = '10px system-ui'; ctx.textAlign = 'left';
      let legendX = left + 6;
      Object.entries(payload.indicators || {}).forEach(([name, indicator]) => {
        ctx.fillStyle = indicator.colour; ctx.fillText(name, legendX, top + 13); legendX += ctx.measureText(name).width + 13;
      });
      ctx.fillStyle = '#8fa0bd'; ctx.textAlign = 'left';
      ctx.fillText(view[0].time.slice(0, 16).replace('T', ' '), left, height - 9);
      ctx.textAlign = 'right';
      ctx.fillText(view[view.length - 1].time.slice(0, 16).replace('T', ' '), width - right, height - 9);
      root.querySelector('.chart-controls strong').textContent = `Showing ${view.length} of ${candles.length} stored ${payload.symbol} candles`;
    };

    const point = (event) => {
      const rect = canvas.getBoundingClientRect();
      return {x: event.clientX - rect.left, y: event.clientY - rect.top, rect};
    };
    canvas.addEventListener('wheel', (event) => {
      event.preventDefault();
      const old = visible;
      const next = clamp(Math.round(old * (event.deltaY < 0 ? 0.78 : 1.28)), Math.min(20, candles.length), candles.length);
      const {x, rect} = point(event);
      const focus = clamp((x - 12) / Math.max(1, rect.width - 80), 0, 1);
      const start = end - old;
      visible = next;
      end = clamp(Math.round(start + focus * old + (1 - focus) * next), next, candles.length);
      render();
    }, {passive: false});
    canvas.addEventListener('pointerdown', (event) => {
      canvas.setPointerCapture(event.pointerId);
      drag = {x: event.clientX, end};
      tooltip.hidden = true;
    });
    canvas.addEventListener('pointermove', (event) => {
      const {x, y: mouseY, rect} = point(event);
      if (drag) {
        const step = Math.max(1, (rect.width - 80) / visible);
        end = clamp(Math.round(drag.end - (event.clientX - drag.x) / step), visible, candles.length);
        render();
        return;
      }
      const start = end - visible;
      const index = clamp(start + Math.floor((x - 12) / Math.max(1, (rect.width - 80) / visible)), start, end - 1);
      const candle = candles[index];
      if (!candle || mouseY < 0 || mouseY > 410) { tooltip.hidden = true; return; }
      tooltip.innerHTML = `<strong>${candle.time.slice(0, 16).replace('T', ' ')} UTC</strong><br>O $${candle.open.toLocaleString()} · H $${candle.high.toLocaleString()}<br>L $${candle.low.toLocaleString()} · C $${candle.close.toLocaleString()}`;
      tooltip.style.left = `${clamp(x + 14, 8, rect.width - 190)}px`;
      tooltip.style.top = `${clamp(mouseY + 14, 8, 340)}px`;
      tooltip.hidden = false;
    });
    canvas.addEventListener('pointerup', () => { drag = null; });
    canvas.addEventListener('pointerleave', () => { if (!drag) tooltip.hidden = true; });
    root.querySelectorAll('[data-window]').forEach((button) => button.addEventListener('click', () => {
      visible = button.dataset.window === 'full' ? candles.length : Math.min(candles.length, Number(button.dataset.window));
      end = candles.length;
      render();
    }));
    new ResizeObserver(render).observe(root);
    render();
  });
})();
</script>"""


def _positions_page(cfg: Config, storage: Storage) -> str:
    accounts = storage.paper_accounts()
    snapshots = storage.market_snapshots()
    positions, _ = _active_positions(cfg, accounts, snapshots)
    by_symbol: dict[str, list[dict]] = {}
    for position in positions:
        by_symbol.setdefault(position["symbol"], []).append(position)
    chart_cards = "".join(
        f"<section class='ticker-card'><div class='ticker-head'><div><span class='eyebrow'>OPEN PAPER POSITION</span><h2>{html.escape(symbol)}</h2><p>{len(symbol_positions)} active strategy position{'s' if len(symbol_positions) != 1 else ''} · up to 180 completed {cfg.interval} candles</p></div><div class='last-price'>{_money(symbol_positions[0]['close'])}<span>last close</span></div></div>{_candlestick_chart(symbol, storage.market_candles(symbol), symbol_positions)}</section>"
        for symbol, symbol_positions in sorted(by_symbol.items())
    ) or "<section class='ticker-card'><p class='chart-empty'>No active paper positions to chart.</p></section>"
    return f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>
<title>Open Positions · Binance Paper Trading Lab</title><style>
:root{{color-scheme:dark;--bg:#090d19;--panel:#111827;--line:#29364f;--text:#edf3ff;--muted:#94a3b8;--blue:#78a9ff;--green:#55d998;--red:#ff7b8b}}*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 20% -10%,#1b3059 0,transparent 36%),var(--bg);color:var(--text);font:14px Inter,ui-sans-serif,system-ui,-apple-system,Segoe UI,sans-serif}}main{{max-width:1240px;margin:auto;padding:34px 24px 48px}}a{{color:var(--blue);font-weight:700;text-decoration:none}}a:hover{{text-decoration:underline}}h1{{font-size:30px;letter-spacing:-.7px;margin:10px 0 6px}}h2{{font-size:22px;margin:5px 0}}p{{color:var(--muted);margin:0;line-height:1.55}}.badge,.eyebrow{{font-size:11px;font-weight:750;letter-spacing:.07em}}.badge{{display:inline-block;background:#123c2a;color:#75e6ae;border-radius:999px;padding:5px 9px}}.eyebrow{{color:#aebedc}}.top{{display:flex;justify-content:space-between;gap:20px;align-items:flex-start;margin-bottom:24px}}.ticker-card{{background:linear-gradient(145deg,#151f33,#111827);border:1px solid var(--line);border-radius:16px;padding:18px;margin-top:16px}}.ticker-head{{display:flex;justify-content:space-between;gap:20px;align-items:flex-start;margin-bottom:13px}}.last-price{{font-size:22px;font-weight:800;text-align:right}}.last-price span{{display:block;color:var(--muted);font-size:11px;font-weight:500;margin-top:3px}}.chart-controls{{display:flex;justify-content:space-between;align-items:center;gap:12px;margin:0 0 9px;color:var(--muted);font-size:12px}}.chart-controls strong{{display:block;color:var(--text);font-size:13px;margin-bottom:3px}}.chart-buttons{{display:flex;gap:6px}}.chart-buttons button{{border:1px solid #334463;background:#121d30;color:#b9ceef;border-radius:7px;padding:5px 9px;font:700 11px system-ui;cursor:pointer}}.chart-buttons button:hover{{background:#203355;color:#fff}}.chart-canvas-wrap{{position:relative;background:#0a101c;border:1px solid #202c42;border-radius:10px;overflow:hidden}}.chart-canvas-wrap canvas{{display:block;width:100%;touch-action:none;cursor:crosshair}}.chart-tooltip{{position:absolute;z-index:2;min-width:170px;padding:8px 9px;background:#111c30eF;border:1px solid #425575;border-radius:8px;color:#cbd8ee;font-size:11px;line-height:1.55;pointer-events:none;box-shadow:0 5px 18px #0008}}.chart-tooltip strong{{color:#fff}}.position-legend{{list-style:none;margin:14px 0 0;padding:0;display:grid;gap:7px;color:var(--muted);font-size:12px}}.position-legend li{{display:flex;gap:7px;align-items:baseline;flex-wrap:wrap}}.position-legend i{{width:10px;height:10px;border-radius:50%;display:inline-block;flex:0 0 auto}}.position-legend strong{{color:var(--text)}}.positive{{color:var(--green)}}.negative{{color:var(--red)}}.neutral{{color:var(--muted)}}.chart-empty{{padding:28px 0}}.note{{margin-top:18px;border-left:3px solid #fbc96a;padding:10px 12px;background:#292313;color:#e9d6a3;font-size:12px;line-height:1.5}}@media(max-width:600px){{main{{padding:22px 14px}}.top,.ticker-head{{display:block}}.last-price{{text-align:left;margin-top:13px}}.chart-controls{{align-items:flex-start;flex-direction:column}}h1{{font-size:25px}}.ticker-card{{padding:14px}}}}
</style></head><body><main><header class='top'><div><span class='badge'>PAPER MODE · LIVE ORDERS LOCKED</span><h1>Open position charts</h1><p>Real Binance Spot OHLC candles stored by the paper engine. All open strategy positions for the same ticker appear in one chart.</p></div><a href='index.html'>← Dashboard overview</a></header>{chart_cards}<p class='note'>Entry and stop lines are drawn from stored paper positions. After each completed candle, every Spot position tightens its ATR trailing stop when price moves favourably; it never widens. Charts are not real-time execution charts or trading instructions.</p></main>{_interactive_chart_script()}</body></html>"""


def serve(cfg: Config) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            storage = Storage(cfg.database)
            try:
                if self.path == "/api/status":
                    body = json.dumps({"backtests": storage.latest_backtests(), "accounts": storage.paper_accounts(),
                                       "trades": storage.recent_paper_trades(), "snapshots": storage.market_snapshots()}).encode()
                    self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(body)
                elif self.path in {"/positions", "/positions.html"}:
                    body = _positions_page(cfg, storage).encode()
                    self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.end_headers(); self.wfile.write(body)
                else:
                    body = _page(cfg, storage).encode()
                    self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.end_headers(); self.wfile.write(body)
            finally:
                storage.close()
        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer((cfg.dashboard_host, cfg.dashboard_port), Handler)
    print(f"Dashboard: http://{cfg.dashboard_host}:{cfg.dashboard_port}")
    server.serve_forever()


def render_static(cfg: Config, output: str = "docs/index.html") -> None:
    storage = Storage(cfg.database)
    try:
        page = _page(cfg, storage)
        positions_page = _positions_page(cfg, storage)
    finally:
        storage.close()
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Static hosting cannot expose the local JSON endpoint; the HTML contains the current snapshot.
    target.write_text(page.replace("<meta http-equiv='refresh' content='60'>", ""), encoding="utf-8")
    target.with_name("positions.html").write_text(positions_page, encoding="utf-8")
