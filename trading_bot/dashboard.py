from __future__ import annotations

import html
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .config import Config
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
<header class='top'><div><span class='badge'>PAPER MODE · LIVE ORDERS LOCKED</span><h1>Binance Strategy Lab</h1><p>{', '.join(cfg.symbols)} · completed {cfg.interval} candles · isolated $10 strategy accounts</p><p><a class='position-link' href='positions.html'>View candlestick position charts →</a></p></div><div class='updated'>Last database update (UTC)<strong>{html.escape(str(snapshot_updated))}</strong></div></header>
<div class='metrics'><article class='metric'><div class='metric-label'>Paper equity</div><div class='metric-value'>{_money(marked_equity)}</div><div class='metric-note'>Across {len(accounts)} isolated accounts</div></article><article class='metric'><div class='metric-label'>Forward P&amp;L</div><div class='metric-value {_pnl_class(total_pnl)}'>{_signed_money(total_pnl)}</div><div class='metric-note'>Marked using latest completed candle</div></article><article class='metric'><div class='metric-label'>Active positions</div><div class='metric-value'>{len(active_positions)}</div><div class='metric-note'>{len(pending_orders)} queued order{'s' if len(pending_orders) != 1 else ''}</div></article><article class='metric'><div class='metric-label'>Realized P&amp;L</div><div class='metric-value {_pnl_class(realized_pnl)}'>{_signed_money(realized_pnl)}</div><div class='metric-note'>{sum(1 for trade in trades if trade['side'] == 'SELL')} closed trades</div></article></div>
<section class='section'><div class='section-head'><div><h2>Open paper positions</h2><p>Forward-paper positions only. Mark P&amp;L includes an estimated fee for closing at the latest completed 4h close. Retiring strategies cannot open again but keep their exit protection until closed.</p></div></div><div class='table-wrap'><table><thead><tr><th>Market</th><th>Strategy</th><th>Status</th><th>Entry</th><th>Last close</th><th>Stop</th><th>Unrealized P&amp;L</th><th>Marked equity</th></tr></thead><tbody>{position_rows}</tbody></table></div></section>
<section class='section'><div class='section-head'><div><h2>Queued paper orders</h2><p>A completed-candle signal does not fill immediately. It is recorded here and filled at the next completed candle's open, unless it is cancelled by an earlier protective exit.</p></div></div><div class='table-wrap'><table><thead><tr><th>Market</th><th>Strategy</th><th>Order</th><th>Planned fill</th></tr></thead><tbody>{pending_rows}</tbody></table></div></section>
<section class='section'><div class='section-head'><div><h2>Latest candle delta</h2><p>Binance Spot taker-volume proxy. Positive means relatively more taker-buy volume; 24h CVD sums six 4h candle deltas.</p></div></div><div class='table-wrap'><table><thead><tr><th>Market</th><th>Close</th><th>Delta ratio</th><th>Candle delta (USDT)</th><th>24h CVD (USDT)</th><th>Candle open</th></tr></thead><tbody>{snapshot_rows}</tbody></table></div></section>
<section class='section'><div class='section-head'><div><h2>Forward trade ledger</h2><p>Actual simulated entries and exits—not historical backtest trades.</p></div></div><div class='table-wrap'><table><thead><tr><th>Recorded</th><th>Market</th><th>Strategy</th><th>Side</th><th>Price</th><th>Quantity</th><th>Realized P&amp;L</th><th>Reason</th></tr></thead><tbody>{trade_rows}</tbody></table></div></section>
<section class='section'><div class='section-head'><div><h2>Strategy laboratory</h2><p>Historical two-year backtests. Research-only strategies are displayed here but cannot open paper positions.</p></div></div><div class='table-wrap'><table><thead><tr><th>Market</th><th>Strategy</th><th>Return</th><th>Trades</th><th>W/L</th><th>Win rate</th><th>Profit factor</th><th>Max drawdown</th></tr></thead><tbody>{backtest_rows}</tbody></table></div><div class='footnote'>Historical results are not a forecast. Do not use the dashboard as a live-trading instruction; it is a paper-trading research record.</div></section>
</main></body></html>"""


def _candlestick_chart(symbol: str, candles: list[dict], positions: list[dict]) -> str:
    """Render real stored Binance OHLC candles and all active positions for one ticker."""
    candles = candles[-60:]
    if len(candles) < 2:
        return "<p class='chart-empty'>Chart data will appear after the next completed paper cycle.</p>"

    prices = [price for candle in candles for price in (candle["low"], candle["high"])]
    prices.extend(position["entry_price"] for position in positions)
    prices.extend(position["stop_price"] for position in positions)
    low, high = min(prices), max(prices)
    padding = max((high - low) * 0.10, high * 0.002)
    low, high = low - padding, high + padding
    width, height, left, right, top, bottom = 980, 340, 14, 14, 14, 26
    plot_width, plot_height = width - left - right, height - top - bottom
    price_range = max(high - low, 0.000001)

    def y(price: float) -> float:
        return top + (high - price) * plot_height / price_range

    step = plot_width / len(candles)
    body_width = max(2.0, step * 0.62)
    candle_shapes: list[str] = []
    for index, candle in enumerate(candles):
        x = left + (index + 0.5) * step
        is_up = candle["close"] >= candle["open"]
        colour = "#55d998" if is_up else "#ff7b8b"
        open_y, close_y = y(candle["open"]), y(candle["close"])
        body_y = min(open_y, close_y)
        body_height = max(1.5, abs(close_y - open_y))
        candle_shapes.append(
            f"<line x1='{x:.1f}' x2='{x:.1f}' y1='{y(candle['high']):.1f}' y2='{y(candle['low']):.1f}' stroke='{colour}' stroke-width='1.2'/>"
            f"<rect x='{x - body_width / 2:.1f}' y='{body_y:.1f}' width='{body_width:.1f}' height='{body_height:.1f}' fill='{colour}'/>"
        )

    palette = ["#fbc96a", "#a78bfa", "#22d3ee", "#fb7185"]
    markers: list[str] = []
    legend: list[str] = []
    for index, position in enumerate(positions):
        colour = palette[index % len(palette)]
        entry_y, stop_y = y(position["entry_price"]), y(position["stop_price"])
        markers.append(
            f"<line x1='{left}' x2='{width - right}' y1='{entry_y:.1f}' y2='{entry_y:.1f}' stroke='{colour}' stroke-width='1.8' stroke-dasharray='7 5'/>"
            f"<line x1='{left}' x2='{width - right}' y1='{stop_y:.1f}' y2='{stop_y:.1f}' stroke='{colour}' stroke-width='1.2' stroke-dasharray='2 5' opacity='.85'/>"
        )
        name = html.escape(STRATEGIES[position["strategy_key"]].name)
        legend.append(
            f"<li><i style='background:{colour}'></i><strong>{name}</strong> · entry {_money(position['entry_price'])} · stop {_money(position['stop_price'])} · <span class='{_pnl_class(position['pnl'])}'>{_signed_money(position['pnl'])}</span></li>"
        )

    first_time = html.escape(str(candles[0]["candle_time"])[:16])
    last_time = html.escape(str(candles[-1]["candle_time"])[:16])
    return f"""<svg class='candle-chart' viewBox='0 0 {width} {height}' role='img' aria-label='{html.escape(symbol)} OHLC candlestick chart'>
    <rect x='{left}' y='{top}' width='{plot_width}' height='{plot_height}' class='chart-bg'/>
    <line x1='{left}' x2='{width - right}' y1='{top + plot_height * .25:.1f}' y2='{top + plot_height * .25:.1f}' class='grid-line'/>
    <line x1='{left}' x2='{width - right}' y1='{top + plot_height * .50:.1f}' y2='{top + plot_height * .50:.1f}' class='grid-line'/>
    <line x1='{left}' x2='{width - right}' y1='{top + plot_height * .75:.1f}' y2='{top + plot_height * .75:.1f}' class='grid-line'/>
    {''.join(candle_shapes)}{''.join(markers)}
    <text x='{left}' y='{height - 7}' class='axis-text'>{first_time}</text><text x='{width - right}' y='{height - 7}' text-anchor='end' class='axis-text'>{last_time}</text>
    </svg><ul class='position-legend'>{''.join(legend)}</ul>"""


def _positions_page(cfg: Config, storage: Storage) -> str:
    accounts = storage.paper_accounts()
    snapshots = storage.market_snapshots()
    positions, _ = _active_positions(cfg, accounts, snapshots)
    by_symbol: dict[str, list[dict]] = {}
    for position in positions:
        by_symbol.setdefault(position["symbol"], []).append(position)
    chart_cards = "".join(
        f"<section class='ticker-card'><div class='ticker-head'><div><span class='eyebrow'>OPEN PAPER POSITION</span><h2>{html.escape(symbol)}</h2><p>{len(symbol_positions)} active strategy position{'s' if len(symbol_positions) != 1 else ''} · last 60 completed {cfg.interval} candles</p></div><div class='last-price'>{_money(symbol_positions[0]['close'])}<span>last close</span></div></div>{_candlestick_chart(symbol, storage.market_candles(symbol), symbol_positions)}</section>"
        for symbol, symbol_positions in sorted(by_symbol.items())
    ) or "<section class='ticker-card'><p class='chart-empty'>No active paper positions to chart.</p></section>"
    return f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>
<title>Open Positions · Binance Paper Trading Lab</title><style>
:root{{color-scheme:dark;--bg:#090d19;--panel:#111827;--line:#29364f;--text:#edf3ff;--muted:#94a3b8;--blue:#78a9ff;--green:#55d998;--red:#ff7b8b}}*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 20% -10%,#1b3059 0,transparent 36%),var(--bg);color:var(--text);font:14px Inter,ui-sans-serif,system-ui,-apple-system,Segoe UI,sans-serif}}main{{max-width:1240px;margin:auto;padding:34px 24px 48px}}a{{color:var(--blue);font-weight:700;text-decoration:none}}a:hover{{text-decoration:underline}}h1{{font-size:30px;letter-spacing:-.7px;margin:10px 0 6px}}h2{{font-size:22px;margin:5px 0}}p{{color:var(--muted);margin:0;line-height:1.55}}.badge,.eyebrow{{font-size:11px;font-weight:750;letter-spacing:.07em}}.badge{{display:inline-block;background:#123c2a;color:#75e6ae;border-radius:999px;padding:5px 9px}}.eyebrow{{color:#aebedc}}.top{{display:flex;justify-content:space-between;gap:20px;align-items:flex-start;margin-bottom:24px}}.ticker-card{{background:linear-gradient(145deg,#151f33,#111827);border:1px solid var(--line);border-radius:16px;padding:18px;margin-top:16px}}.ticker-head{{display:flex;justify-content:space-between;gap:20px;align-items:flex-start;margin-bottom:13px}}.last-price{{font-size:22px;font-weight:800;text-align:right}}.last-price span{{display:block;color:var(--muted);font-size:11px;font-weight:500;margin-top:3px}}.candle-chart{{display:block;width:100%;height:auto;background:#0a101c;border:1px solid #202c42;border-radius:10px}}.chart-bg{{fill:#0a101c}}.grid-line{{stroke:#25324a;stroke-width:1;stroke-dasharray:3 5}}.axis-text{{fill:#8fa0bd;font-size:10px}}.position-legend{{list-style:none;margin:14px 0 0;padding:0;display:grid;gap:7px;color:var(--muted);font-size:12px}}.position-legend li{{display:flex;gap:7px;align-items:baseline;flex-wrap:wrap}}.position-legend i{{width:10px;height:10px;border-radius:50%;display:inline-block;flex:0 0 auto}}.position-legend strong{{color:var(--text)}}.positive{{color:var(--green)}}.negative{{color:var(--red)}}.neutral{{color:var(--muted)}}.chart-empty{{padding:28px 0}}.note{{margin-top:18px;border-left:3px solid #fbc96a;padding:10px 12px;background:#292313;color:#e9d6a3;font-size:12px;line-height:1.5}}@media(max-width:600px){{main{{padding:22px 14px}}.top,.ticker-head{{display:block}}.last-price{{text-align:left;margin-top:13px}}h1{{font-size:25px}}.ticker-card{{padding:14px}}}}
</style></head><body><main><header class='top'><div><span class='badge'>PAPER MODE · LIVE ORDERS LOCKED</span><h1>Open position charts</h1><p>Real Binance Spot OHLC candles stored by the paper engine. All open strategy positions for the same ticker appear in one chart.</p></div><a href='index.html'>← Dashboard overview</a></header>{chart_cards}<p class='note'>Dashed line = entry; dotted line = stored paper stop. Charts use completed 4-hour candles, so they are not real-time execution charts or trading instructions.</p></main></body></html>"""


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
