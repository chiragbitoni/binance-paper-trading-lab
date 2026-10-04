from __future__ import annotations

import html
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .config import Config
from .storage import Storage
from .strategies import STRATEGIES


def _page(cfg: Config, storage: Storage) -> str:
    backtests = storage.latest_backtests()
    accounts = storage.paper_accounts()
    trades = storage.recent_paper_trades()
    snapshots = storage.market_snapshots()
    snapshot_rows = "".join(
        f"<tr><td>{html.escape(s['symbol'])}</td><td>${s['close']:.4f}</td>"
        f"<td>{s['delta_ratio'] * 100:+.1f}%</td><td>${s['delta_quote']:,.0f}</td>"
        f"<td>${s['cvd_quote_24h']:,.0f}</td><td>{s['candle_time']}</td></tr>"
        for s in snapshots
    ) or "<tr><td colspan='6'>Process a completed candle to populate delta data.</td></tr>"
    backtest_rows = "".join(
        f"<tr><td>{html.escape(r['symbol'])}</td>"
        f"<td>{html.escape(STRATEGIES.get(r['strategy_key'], STRATEGIES['trend_breakout']).name)}</td>"
        f"<td>{r['total_return_pct']:.2f}%</td><td>{r['trades']}</td><td>{r['wins']}/{r['losses']}</td>"
        f"<td>{r['win_rate']:.1f}%</td><td>{r['profit_factor'] if r['profit_factor'] is not None else '—'}</td>"
        f"<td>{r['max_drawdown_pct']:.2f}%</td></tr>" for r in backtests
    ) or "<tr><td colspan='8'>Run a backtest to populate results.</td></tr>"
    account_rows = "".join(
        f"<tr><td>{html.escape(a['symbol'])}</td><td>{html.escape(a['strategy_key'])}</td>"
        f"<td>${a['cash']:.4f}</td><td>{a['quantity']:.8f}</td>"
        f"<td>{('$' + format(a['entry_price'], '.2f')) if a['entry_price'] else '—'}</td><td>{a['updated_at']}</td></tr>"
        for a in accounts
    ) or "<tr><td colspan='6'>Run paper-once to initialize accounts.</td></tr>"
    trade_rows = "".join(
        f"<tr><td>{t['created_at']}</td><td>{html.escape(t['symbol'])}</td>"
        f"<td>{html.escape(t['strategy_key'])}</td><td>{t['side']}</td>"
        f"<td>${t['price']:.2f}</td><td>{t['quantity']:.8f}</td>"
        f"<td>{format(t['realized_pnl'], '.4f') if t['realized_pnl'] is not None else '—'}</td>"
        f"<td>{html.escape(t['reason'])}</td></tr>" for t in trades
    ) or "<tr><td colspan='8'>No paper trades yet.</td></tr>"
    return f"""<!doctype html><html><head><meta charset='utf-8'><meta http-equiv='refresh' content='60'>
<title>Paper Trading Lab</title><style>
body{{font:15px system-ui;margin:0;background:#0b1020;color:#e8edf7}}main{{max-width:1100px;margin:auto;padding:28px}}
h1{{margin:0 0 8px}}p{{color:#aeb9cf}}section{{background:#141b2d;border:1px solid #26304a;border-radius:14px;padding:18px;margin:18px 0}}
table{{width:100%;border-collapse:collapse}}th,td{{text-align:left;padding:10px;border-bottom:1px solid #26304a}}th{{color:#8fb6ff}}
.badge{{display:inline-block;background:#173d2d;color:#7aefad;border-radius:999px;padding:5px 10px}}code{{color:#ffd479}}</style></head>
<body><main><span class='badge'>PAPER MODE</span><h1>Binance Strategy Lab</h1>
<p>{', '.join(cfg.symbols)} · {cfg.interval} candles · ${cfg.starting_balance:.2f} independent balance per market/strategy · live orders locked</p>
<section><h2>Latest candle delta</h2><p>Candle-level Binance Spot taker-volume proxy; positive means more taker-buy volume. 24h CVD is the rolling sum of six 4h candle deltas, not a multi-exchange or tick-level metric.</p><table><thead><tr><th>Market</th><th>Close</th><th>Delta ratio</th><th>Candle delta (USDT)</th><th>24h CVD (USDT)</th><th>Candle open</th></tr></thead><tbody>{snapshot_rows}</tbody></table></section>
<section><h2>Latest historical backtests</h2><table><thead><tr><th>Market</th><th>Strategy</th><th>Return</th><th>Trades</th><th>W/L</th><th>Win rate</th><th>Profit factor</th><th>Max drawdown</th></tr></thead><tbody>{backtest_rows}</tbody></table></section>
<section><h2>Forward paper accounts</h2><table><thead><tr><th>Market</th><th>Strategy</th><th>Cash</th><th>Quantity</th><th>Entry</th><th>Updated</th></tr></thead><tbody>{account_rows}</tbody></table></section>
<section><h2>Recent paper trades</h2><table><thead><tr><th>Time</th><th>Market</th><th>Strategy</th><th>Side</th><th>Price</th><th>Quantity</th><th>PnL</th><th>Reason</th></tr></thead><tbody>{trade_rows}</tbody></table></section>
</main></body></html>"""


def serve(cfg: Config) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            storage = Storage(cfg.database)
            try:
                if self.path == "/api/status":
                    body = json.dumps({"backtests": storage.latest_backtests(), "accounts": storage.paper_accounts(),
                                       "trades": storage.recent_paper_trades()}).encode()
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
