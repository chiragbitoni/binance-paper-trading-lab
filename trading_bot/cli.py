from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone

from .backtest import run_backtest
from .config import load_config
from .dashboard import render_static, serve
from .dhan_mcx import DhanMCXClient, MCXExecutionEngine, MCXSignalAnalyzer, MCXTradePlan
from .futures import futures_paper_cycle, render_futures_static
from .market import BinanceFuturesMarketData, BinanceLiveClient, BinanceMarketData
from .paper import paper_cycle, paper_loop
from .realtime import RealtimePaperService
from .storage import Storage
from .strategies import STRATEGIES


def main() -> None:
    parser = argparse.ArgumentParser(description="Paper-first Binance strategy research bot")
    parser.add_argument("--config", default="config.json")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("backtest")
    paper = sub.add_parser("paper")
    paper.add_argument("--loop", action="store_true")
    sub.add_parser("futures-paper")
    sub.add_parser("realtime", help="Run public WebSocket price monitoring and the 4h paper scheduler")
    sub.add_parser("dashboard")
    static = sub.add_parser("render-static")
    static.add_argument("--output", default="docs/index.html")
    futures_static = sub.add_parser("render-futures-static")
    futures_static.add_argument("--output", default="docs/futures.html")
    sub.add_parser("check-live-connection")
    dhan = sub.add_parser("dhan-mcx", help="Dhan MCX analysis and guarded live-order commands")
    dhan_sub = dhan.add_subparsers(dest="dhan_command", required=True)
    dhan_sub.add_parser("status", help="Read Dhan profile, funds and MCX positions; never places an order")
    analyze = dhan_sub.add_parser("analyze", help="Analyze completed Dhan MCX candles; never places an order")
    analyze.add_argument("--security-id", required=True)
    analyze.add_argument("--instrument", required=True, help="Dhan instrument code, for example FUTCOM")
    analyze.add_argument("--interval", type=int, default=5, choices=(1, 5, 15, 25, 60))
    analyze.add_argument("--from-date", required=True, help="IST timestamp: YYYY-MM-DD HH:MM:SS")
    analyze.add_argument("--to-date", required=True, help="IST timestamp: YYYY-MM-DD HH:MM:SS")
    preview = dhan_sub.add_parser("preview", help="Validate a protected MCX Super Order without sending it")
    execute = dhan_sub.add_parser("execute", help="Submit a protected MCX Super Order only after all live gates pass")
    for action in (preview, execute):
        action.add_argument("--security-id", required=True)
        action.add_argument("--side", required=True, choices=("BUY", "SELL"))
        action.add_argument("--quantity", required=True, type=int)
        action.add_argument("--entry", required=True, type=float)
        action.add_argument("--stop", required=True, type=float)
        action.add_argument("--target", required=True, type=float)
        action.add_argument("--trailing-jump", type=float, default=0.0)
        action.add_argument("--product", choices=("INTRADAY", "MARGIN"))
        action.add_argument("--order-type", default="LIMIT", choices=("LIMIT", "MARKET"))
    execute.add_argument("--confirm-live", action="store_true",
                         help="Required in addition to config and environment safety gates")
    args = parser.parse_args()
    cfg = load_config(args.config)

    if args.command == "dashboard":
        serve(cfg)
        return
    if args.command == "render-static":
        render_static(cfg, args.output)
        print(f"Static dashboard written to {args.output}")
        return
    if args.command == "realtime":
        RealtimePaperService(cfg).run()
        return
    if args.command == "render-futures-static":
        render_futures_static(cfg, args.output)
        print(f"Static futures paper dashboard written to {args.output}")
        return
    if args.command == "check-live-connection":
        client = BinanceLiveClient()
        account = client.account()
        print(f"Connected to Binance account. Trading enabled: {account.get('canTrade')}")
        return
    if args.command == "dhan-mcx":
        client = DhanMCXClient.from_environment(cfg.dhan_mcx)
        engine = MCXExecutionEngine(cfg.dhan_mcx, client)
        if args.dhan_command == "status":
            print(json.dumps(engine.status(), indent=2, sort_keys=True))
            return
        if args.dhan_command == "analyze":
            candles = client.intraday_candles(args.security_id, args.instrument, args.interval,
                                               args.from_date, args.to_date)
            print(json.dumps(MCXSignalAnalyzer().analyze(candles).as_dict(), indent=2, sort_keys=True))
            return
        plan = MCXTradePlan(
            security_id=args.security_id, side=args.side, quantity=args.quantity, entry_price=args.entry,
            stop_loss_price=args.stop, target_price=args.target, trailing_jump=args.trailing_jump,
            product_type=args.product or cfg.dhan_mcx["default_product_type"], entry_order_type=args.order_type,
        )
        if args.dhan_command == "preview":
            print(json.dumps(engine.preview(plan), indent=2, sort_keys=True))
            return
        result = engine.execute(plan, confirmed=args.confirm_live)
        print(json.dumps(result, indent=2, sort_keys=True))
        return

    storage = Storage(cfg.database)
    market = BinanceMarketData()
    try:
        if args.command == "backtest":
            end = datetime.now(timezone.utc)
            start = end - timedelta(days=cfg.history_days)
            for symbol in cfg.symbols:
                data = market.candles(symbol, cfg.interval, int(start.timestamp() * 1000), int(end.timestamp() * 1000))
                strategy_keys = cfg.enabled_strategies + cfg.research_only_strategies
                for key in strategy_keys:
                    strategy = STRATEGIES[key]
                    result, trades = run_backtest(data, strategy, cfg, symbol)
                    run_id = storage.save_backtest(result, trades)
                    print(f"#{run_id} {symbol} {strategy.name}: return={result['total_return_pct']:.2f}% "
                          f"trades={result['trades']} win_rate={result['win_rate']:.1f}% "
                          f"PF={result['profit_factor']} DD={result['max_drawdown_pct']:.2f}%")
        elif args.command == "paper":
            paper_strategies = [key for key in cfg.enabled_strategies if key != "buy_hold_benchmark"]
            storage.initialize_paper_accounts(cfg.symbols, paper_strategies, cfg.starting_balance)
            if args.loop:
                paper_loop(cfg, storage, market)
            else:
                messages = paper_cycle(cfg, storage, market)
                print("\n".join(messages) if messages else "Paper accounts updated; no new signals.")
        elif args.command == "futures-paper":
            messages = futures_paper_cycle(cfg, storage, BinanceFuturesMarketData())
            print("\n".join(messages) if messages else "Futures paper wallet updated; no new signals.")
    finally:
        storage.close()


if __name__ == "__main__":
    main()
