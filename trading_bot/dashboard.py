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


def _page(cfg: Config, storage: Storage) -> str:
    backtests = storage.latest_backtests()
    accounts = storage.paper_accounts()
    trades = storage.recent_paper_trades()
    snapshots = storage.market_snapshots()
    snapshots_by_symbol = {snapshot["symbol"]: snapshot for snapshot in snapshots}

    active_positions: list[dict] = []
    marked_equity = 0.0
    for account in accounts:
        snapshot = snapshots_by_symbol.get(account["symbol"])
        close = snapshot["close"] if snapshot else None
        if account["quantity"] > 0 and close is not None:
            position_value = account["quantity"] * close * (1 - cfg.fee_rate)
            account_equity = account["cash"] + position_value
            pnl = account_equity - cfg.starting_balance
            active_positions.append({**account, "close": close, "equity": account_equity, "pnl": pnl})
            marked_equity += account_equity
        else:
            marked_equity += account["cash"]

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
        f"<td>{_money(position['entry_price'])}</td><td>{_money(position['close'])}</td>"
        f"<td>{_money(position['stop_price'])}</td>"
        f"<td class='{_pnl_class(position['pnl'])}'>{_signed_money(position['pnl'])} ({position['pnl'] / cfg.starting_balance * 100:+.2f}%)</td>"
        f"<td>{_money(position['equity'])}</td></tr>"
        for position in active_positions
    ) or "<tr><td colspan='7'>No active paper positions.</td></tr>"

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
main{{max-width:1380px;margin:auto;padding:34px 24px 48px}}.top{{display:flex;justify-content:space-between;align-items:flex-start;gap:18px;margin-bottom:25px}}h1{{font-size:30px;letter-spacing:-.7px;margin:8px 0}}h2{{font-size:17px;margin:0 0 6px}}p{{color:var(--muted);line-height:1.55;margin:0}}.badge,.tag{{display:inline-block;border-radius:999px;font-size:11px;font-weight:750;letter-spacing:.06em;padding:5px 9px}}.badge{{background:#123c2a;color:#75e6ae}}.tag.buy{{background:#123c2a;color:#75e6ae}}.tag.sell{{background:#4a1f2b;color:#ff9aa8}}.updated{{font-size:12px;color:var(--muted);text-align:right}}.updated strong{{color:var(--text);display:block;font-size:13px;margin-top:4px}}
.metrics{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;margin:18px 0}}.metric,.section{{background:linear-gradient(145deg,var(--panel2),var(--panel));border:1px solid var(--line);border-radius:16px}}.metric{{padding:18px}}.metric-label{{font-size:12px;color:var(--muted);font-weight:650;text-transform:uppercase;letter-spacing:.06em}}.metric-value{{font-size:25px;font-weight:750;letter-spacing:-.5px;margin:8px 0 4px}}.metric-note{{font-size:12px;color:var(--muted)}}.positive{{color:var(--green)}}.negative{{color:var(--red)}}.neutral{{color:var(--muted)}}
.section{{padding:18px;margin-top:16px;overflow:hidden}}.section-head{{display:flex;justify-content:space-between;gap:18px;align-items:baseline;margin-bottom:14px}}.section-head p{{font-size:12px;max-width:730px}}.table-wrap{{overflow-x:auto}}table{{width:100%;border-collapse:collapse;white-space:nowrap}}th,td{{padding:11px 10px;text-align:left;border-bottom:1px solid var(--line)}}th{{font-size:11px;color:var(--blue);text-transform:uppercase;letter-spacing:.06em}}td{{font-variant-numeric:tabular-nums}}tr:last-child td{{border-bottom:0}}.footnote{{margin-top:17px;border-left:3px solid var(--yellow);padding:10px 12px;background:#292313;color:#e9d6a3;font-size:12px;line-height:1.5}}
@media(max-width:900px){{.metrics{{grid-template-columns:repeat(2,minmax(0,1fr))}}}}@media(max-width:620px){{main{{padding:22px 14px}}.top{{display:block}}.updated{{text-align:left;margin-top:16px}}.metrics{{grid-template-columns:1fr 1fr;gap:9px}}.metric{{padding:14px}}.metric-value{{font-size:20px}}.section{{padding:14px}}h1{{font-size:25px}}}}
</style></head><body><main>
<header class='top'><div><span class='badge'>PAPER MODE · LIVE ORDERS LOCKED</span><h1>Binance Strategy Lab</h1><p>{', '.join(cfg.symbols)} · completed {cfg.interval} candles · isolated $10 strategy accounts</p></div><div class='updated'>Last processed candle<strong>{html.escape(str(snapshot_updated))}</strong></div></header>
<div class='metrics'><article class='metric'><div class='metric-label'>Paper equity</div><div class='metric-value'>{_money(marked_equity)}</div><div class='metric-note'>Across {len(accounts)} isolated accounts</div></article><article class='metric'><div class='metric-label'>Forward P&amp;L</div><div class='metric-value {_pnl_class(total_pnl)}'>{_signed_money(total_pnl)}</div><div class='metric-note'>Marked using latest completed candle</div></article><article class='metric'><div class='metric-label'>Active positions</div><div class='metric-value'>{len(active_positions)}</div><div class='metric-note'>of {len(accounts)} paper accounts</div></article><article class='metric'><div class='metric-label'>Realized P&amp;L</div><div class='metric-value {_pnl_class(realized_pnl)}'>{_signed_money(realized_pnl)}</div><div class='metric-note'>{sum(1 for trade in trades if trade['side'] == 'SELL')} closed trades</div></article></div>
<section class='section'><div class='section-head'><div><h2>Open paper positions</h2><p>Forward-paper positions only. Mark P&amp;L includes an estimated fee for closing at the latest completed 4h close.</p></div></div><div class='table-wrap'><table><thead><tr><th>Market</th><th>Strategy</th><th>Entry</th><th>Last close</th><th>Stop</th><th>Unrealized P&amp;L</th><th>Marked equity</th></tr></thead><tbody>{position_rows}</tbody></table></div></section>
<section class='section'><div class='section-head'><div><h2>Latest candle delta</h2><p>Binance Spot taker-volume proxy. Positive means relatively more taker-buy volume; 24h CVD sums six 4h candle deltas.</p></div></div><div class='table-wrap'><table><thead><tr><th>Market</th><th>Close</th><th>Delta ratio</th><th>Candle delta (USDT)</th><th>24h CVD (USDT)</th><th>Candle open</th></tr></thead><tbody>{snapshot_rows}</tbody></table></div></section>
<section class='section'><div class='section-head'><div><h2>Forward trade ledger</h2><p>Actual simulated entries and exits—not historical backtest trades.</p></div></div><div class='table-wrap'><table><thead><tr><th>Recorded</th><th>Market</th><th>Strategy</th><th>Side</th><th>Price</th><th>Quantity</th><th>Realized P&amp;L</th><th>Reason</th></tr></thead><tbody>{trade_rows}</tbody></table></div></section>
<section class='section'><div class='section-head'><div><h2>Strategy laboratory</h2><p>Historical two-year backtests. Research-only strategies are displayed here but cannot open paper positions.</p></div></div><div class='table-wrap'><table><thead><tr><th>Market</th><th>Strategy</th><th>Return</th><th>Trades</th><th>W/L</th><th>Win rate</th><th>Profit factor</th><th>Max drawdown</th></tr></thead><tbody>{backtest_rows}</tbody></table></div><div class='footnote'>Historical results are not a forecast. Do not use the dashboard as a live-trading instruction; it is a paper-trading research record.</div></section>
</main></body></html>"""


def serve(cfg: Config) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            storage = Storage(cfg.database)
            try:
                if self.path == "/api/status":
                    body = json.dumps({"backtests": storage.latest_backtests(), "accounts": storage.paper_accounts(),
                                       "trades": storage.recent_paper_trades(), "snapshots": storage.market_snapshots()}).encode()
                    self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(body)
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
    finally:
        storage.close()
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Static hosting cannot expose the local JSON endpoint; the HTML contains the current snapshot.
    target.write_text(page.replace("<meta http-equiv='refresh' content='60'>", ""), encoding="utf-8")
