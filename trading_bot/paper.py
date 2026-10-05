from __future__ import annotations

import time
from datetime import datetime, timezone

from .config import Config
from .indicators import add_indicators
from .market import BinanceMarketData
from .risk import advance_trailing_stop, stop_fill_price
from .storage import Storage
from .strategies import STRATEGIES


def paper_cycle(cfg: Config, storage: Storage, market: BinanceMarketData) -> list[str]:
    paper_strategies = [key for key in cfg.enabled_strategies if key != "buy_hold_benchmark"]
    storage.initialize_paper_accounts(cfg.symbols, paper_strategies, cfg.starting_balance)
    messages: list[str] = []

    for symbol in cfg.symbols:
        raw = market.candles(symbol, cfg.interval, limit=300)
        now = datetime.now(timezone.utc)
        raw = raw[raw["close_time"] <= now].reset_index(drop=True)
        if len(raw) < 201:
            raise RuntimeError(f"Not enough completed candles for {symbol}")
        storage.save_market_candles(symbol, raw)
        df = add_indicators(raw)
        i = len(df) - 1
        row = df.iloc[i]
        candle_time = str(row.open_time)
        storage.save_market_snapshot({
            "symbol": symbol, "candle_time": candle_time, "close": row.close,
            "delta_base": row.delta_base, "delta_quote": row.delta_quote,
            "delta_ratio": row.delta_ratio, "cvd_quote_24h": row.cvd_quote_24h,
        })

        for account in (a for a in storage.paper_accounts() if a["symbol"] == symbol):
            key = account["strategy_key"]
            can_open_new = key in paper_strategies
            should_manage_open_position = account["quantity"] > 0
            if key not in STRATEGIES or not (can_open_new or should_manage_open_position):
                continue
            strategy = STRATEGIES[key]
            if account["last_candle_time"] == candle_time:
                continue
            account["last_candle_time"] = candle_time

            # A close-based signal is intentionally filled at the next bar's
            # open.  This keeps forward paper execution aligned with the
            # backtest and avoids pretending we could fill before a completed
            # candle revealed its signal.
            if account.get("pending_order") == "entry" and account["quantity"] == 0:
                buy_price = row.open * (1 + cfg.slippage_rate)
                notional = min(cfg.max_position_value, account["cash"] / (1 + cfg.fee_rate))
                account["pending_order"] = None
                if notional >= cfg.minimum_notional:
                    quantity = notional / buy_price
                    fee = notional * cfg.fee_rate
                    account["cash"] -= notional + fee
                    stop_price = (
                        strategy.initial_stop(df, i - 1, buy_price)
                        if strategy.initial_stop is not None
                        else buy_price - strategy.stop_atr * row.atr
                    )
                    account.update(quantity=quantity, entry_price=buy_price,
                                   stop_price=stop_price, high_water=buy_price)
                    storage.save_paper_trade({"symbol": symbol, "strategy_key": key, "side": "BUY",
                                              "price": buy_price, "quantity": quantity, "fee": fee,
                                              "realized_pnl": None, "reason": "entry_signal",
                                              "candle_time": candle_time})
                    messages.append(f"{symbol} {key}: BUY {quantity:.8f} at {buy_price:.2f}")
            elif account.get("pending_order") == "signal_exit" and account["quantity"] > 0:
                sell_price = row.open * (1 - cfg.slippage_rate)
                proceeds = account["quantity"] * sell_price
                fee = proceeds * cfg.fee_rate
                cost = account["quantity"] * account["entry_price"] * (1 + cfg.fee_rate)
                pnl = proceeds - fee - cost
                account["cash"] += proceeds - fee
                storage.save_paper_trade({"symbol": symbol, "strategy_key": key, "side": "SELL",
                                          "price": sell_price, "quantity": account["quantity"], "fee": fee,
                                          "realized_pnl": pnl, "reason": "signal", "candle_time": candle_time})
                messages.append(f"{symbol} {key}: SELL {account['quantity']:.8f} at {sell_price:.2f} "
                                f"(signal, PnL {pnl:.4f})")
                account.update(quantity=0.0, entry_price=None, stop_price=None, high_water=None,
                               pending_order=None)
                storage.update_paper_account(account)
                continue

            if account["quantity"] > 0:
                stop_price = account["stop_price"]
                reason = None
                stop_fill = stop_fill_price(row.open, row.low, stop_price, cfg.slippage_rate)
                if stop_fill is not None:
                    sell_price = stop_fill
                    reason = "stop"
                if reason:
                    proceeds = account["quantity"] * sell_price
                    fee = proceeds * cfg.fee_rate
                    cost = account["quantity"] * account["entry_price"] * (1 + cfg.fee_rate)
                    pnl = proceeds - fee - cost
                    account["cash"] += proceeds - fee
                    storage.save_paper_trade({"symbol": symbol, "strategy_key": key, "side": "SELL",
                                              "price": sell_price, "quantity": account["quantity"], "fee": fee,
                                              "realized_pnl": pnl, "reason": reason, "candle_time": candle_time})
                    messages.append(f"{symbol} {key}: SELL {account['quantity']:.8f} at {sell_price:.2f} "
                                    f"({reason}, PnL {pnl:.4f})")
                    account.update(quantity=0.0, entry_price=None, stop_price=None, high_water=None,
                                   pending_order=None)
                elif strategy.exit(df, i):
                    account["pending_order"] = "signal_exit"
                    messages.append(f"{symbol} {key}: exit signal queued for next candle open")
                else:
                    stop_price, high_water = advance_trailing_stop(
                        stop_price, account["high_water"] or account["entry_price"], row.high, row.atr,
                        strategy.trailing_atr if strategy.trailing_atr is not None else cfg.trailing_stop_atr,
                    )
                    account["stop_price"] = stop_price
                    account["high_water"] = high_water
            elif can_open_new and strategy.entry(df, i):
                account["pending_order"] = "entry"
                messages.append(f"{symbol} {key}: entry signal queued for next candle open")
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
