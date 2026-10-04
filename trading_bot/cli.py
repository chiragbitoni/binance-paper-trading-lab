from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone

from .backtest import run_backtest
from .config import load_config
from .dashboard import render_static, serve
from .market import BinanceLiveClient, BinanceMarketData
from .paper import paper_cycle, paper_loop
from .storage import Storage
from .strategies import STRATEGIES


def main() -> None:
    parser = argparse.ArgumentParser(description="Paper-first Binance strategy research bot")
    parser.add_argument("--config", default="config.json")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("backtest")
    paper = sub.add_parser("paper")
    paper.add_argument("--loop", action="store_true")
    sub.add_parser("dashboard")
    static = sub.add_parser("render-static")
    static.add_argument("--output", default="docs/index.html")
    sub.add_parser("check-live-connection")
    args = parser.parse_args()
    cfg = load_config(args.config)

    if args.command == "dashboard":
        serve(cfg)
        return
    if args.command == "render-static":
        render_static(cfg, args.output)
        print(f"Static dashboard written to {args.output}")
        return
    if args.command == "check-live-connection":
        client = BinanceLiveClient()
        account = client.account()
        print(f"Connected to Binance account. Trading enabled: {account.get('canTrade')}")
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
    finally:
        storage.close()


if __name__ == "__main__":
    main()
