from __future__ import annotations

import time
from datetime import datetime, timezone

from .config import Config
from .indicators import add_indicators
from .market import BinanceMarketData
from .storage import Storage
from .strategies import STRATEGIES


def paper_cycle(cfg: Config, storage: Storage, market: BinanceMarketData) -> list[str]:
    raw = market.candles(cfg.symbol, cfg.interval, limit=300)
    now = datetime.now(timezone.utc)
    raw = raw[raw["close_time"] <= now].reset_index(drop=True)
    if len(raw) < 201:
        raise RuntimeError("Not enough completed candles")
    df = add_indicators(raw)
    i = len(df) - 1
    row = df.iloc[i]
    candle_time = str(row.open_time)
    paper_strategies = [key for key in cfg.enabled_strategies if key != "buy_hold_benchmark"]
    storage.initialize_paper_accounts(paper_strategies, cfg.starting_balance)
    messages: list[str] = []

    for account in storage.paper_accounts():
        key = account["strategy_key"]
        if key not in paper_strategies or key not in STRATEGIES:
            continue
        strategy = STRATEGIES[key]
        if account["last_candle_time"] == candle_time:
            continue
        account["last_candle_time"] = candle_time

        if account["quantity"] > 0:
            high_water = max(account["high_water"] or account["entry_price"], row.high)
            stop_price = account["stop_price"]
            if strategy.trailing_atr is not None:
                stop_price = max(stop_price, high_water - strategy.trailing_atr * row.atr)
            reason = None
            if row.low <= stop_price:
                sell_price = min(row.open, stop_price) * (1 - cfg.slippage_rate)
                reason = "stop"
            elif strategy.exit(df, i):
                sell_price = row.close * (1 - cfg.slippage_rate)
                reason = "signal"
            if reason:
                proceeds = account["quantity"] * sell_price
                fee = proceeds * cfg.fee_rate
                cost = account["quantity"] * account["entry_price"] * (1 + cfg.fee_rate)
                pnl = proceeds - fee - cost
                account["cash"] += proceeds - fee
                storage.save_paper_trade({"strategy_key": key, "side": "SELL", "price": sell_price,
                                          "quantity": account["quantity"], "fee": fee, "realized_pnl": pnl,
                                          "reason": reason, "candle_time": candle_time})
                messages.append(f"{key}: SELL {account['quantity']:.8f} at {sell_price:.2f} ({reason}, PnL {pnl:.4f})")
                account.update(quantity=0.0, entry_price=None, stop_price=None, high_water=None)
            else:
                account["stop_price"] = stop_price
                account["high_water"] = high_water
        elif strategy.entry(df, i):
            buy_price = row.close * (1 + cfg.slippage_rate)
            notional = min(cfg.max_position_value, account["cash"] / (1 + cfg.fee_rate))
            if notional >= cfg.minimum_notional:
                quantity = notional / buy_price
                fee = notional * cfg.fee_rate
                account["cash"] -= notional + fee
                account.update(quantity=quantity, entry_price=buy_price,
                               stop_price=buy_price - strategy.stop_atr * row.atr, high_water=buy_price)
                storage.save_paper_trade({"strategy_key": key, "side": "BUY", "price": buy_price,
                                          "quantity": quantity, "fee": fee, "realized_pnl": None,
                                          "reason": "entry_signal", "candle_time": candle_time})
                messages.append(f"{key}: BUY {quantity:.8f} at {buy_price:.2f}")
        storage.update_paper_account(account)
    return messages


def paper_loop(cfg: Config, storage: Storage, market: BinanceMarketData, seconds: int = 60) -> None:
    while True:
        try:
            messages = paper_cycle(cfg, storage, market)
            for message in messages:
                print(message, flush=True)
        except Exception as exc:
            print(f"paper cycle error: {exc}", flush=True)
        time.sleep(seconds)
