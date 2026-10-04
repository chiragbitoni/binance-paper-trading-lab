from __future__ import annotations

import math

import pandas as pd

from .config import Config
from .indicators import add_indicators
from .risk import advance_trailing_stop, stop_fill_price
from .strategies import Strategy


def run_backtest(raw: pd.DataFrame, strategy: Strategy, cfg: Config, symbol: str) -> tuple[dict, list[dict]]:
    df = add_indicators(raw).reset_index(drop=True)
    cash = cfg.starting_balance
    quantity = 0.0
    entry_price = entry_cost = stop_price = high_water = 0.0
    entry_time = None
    pending_entry = pending_exit = False
    pending_entry_index: int | None = None
    exit_reason = "signal"
    trades: list[dict] = []
    equity_curve: list[float] = []

    for i in range(200, len(df)):
        row = df.iloc[i]

        if pending_entry and quantity == 0:
            fill = row.open * (1 + cfg.slippage_rate)
            notional = min(cfg.max_position_value, cash / (1 + cfg.fee_rate))
            if notional >= cfg.minimum_notional:
                quantity = notional / fill
                fee = notional * cfg.fee_rate
                cash -= notional + fee
                entry_price, entry_cost, entry_time = fill, notional + fee, str(row.open_time)
                stop_price = (
                    strategy.initial_stop(df, pending_entry_index, fill)
                    if strategy.initial_stop is not None
                    else fill - strategy.stop_atr * row.atr
                )
                high_water = fill
            pending_entry = False
            pending_entry_index = None

        if quantity > 0:
            stop_fill = stop_fill_price(row.open, row.low, stop_price, cfg.slippage_rate)
            if stop_fill is not None:
                pending_exit = True
                exit_reason = "stop"
                exit_fill_override = stop_fill
            elif strategy.exit(df, i):
                pending_exit = True
                exit_reason = "signal"
                exit_fill_override = None
            else:
                stop_price, high_water = advance_trailing_stop(
                    stop_price, high_water, row.high, row.atr, strategy.trailing_atr
                )

        closed_this_bar = False
        if pending_exit and quantity > 0:
            if exit_reason == "stop":
                fill = exit_fill_override
                exit_time = str(row.open_time)
            elif i + 1 < len(df):
                fill = df.iloc[i + 1].open * (1 - cfg.slippage_rate)
                exit_time = str(df.iloc[i + 1].open_time)
            else:
                fill = row.close * (1 - cfg.slippage_rate)
                exit_time = str(row.close_time)
            proceeds = quantity * fill
            fee = proceeds * cfg.fee_rate
            net = proceeds - fee
            pnl = net - entry_cost
            cash += net
            trades.append({
                "entry_time": entry_time, "exit_time": exit_time, "entry_price": entry_price,
                "exit_price": fill, "quantity": quantity, "pnl": pnl,
                "pnl_pct": pnl / entry_cost * 100, "exit_reason": exit_reason,
            })
            quantity = 0.0
            entry_price = entry_cost = stop_price = high_water = 0.0
            entry_time = None
            pending_exit = False
            closed_this_bar = True

        if quantity == 0 and not pending_entry and not closed_this_bar and strategy.entry(df, i):
            pending_entry = True
            pending_entry_index = i

        equity_curve.append(cash + quantity * row.close)

    if quantity > 0:
        row = df.iloc[-1]
        fill = row.close * (1 - cfg.slippage_rate)
        proceeds = quantity * fill
        fee = proceeds * cfg.fee_rate
        net = proceeds - fee
        pnl = net - entry_cost
        cash += net
        trades.append({"entry_time": entry_time, "exit_time": str(row.open_time), "entry_price": entry_price,
                       "exit_price": fill, "quantity": quantity, "pnl": pnl,
                       "pnl_pct": pnl / entry_cost * 100, "exit_reason": "end_of_test"})

    wins = int(sum(bool(t["pnl"] > 0) for t in trades))
    losses = int(sum(bool(t["pnl"] <= 0) for t in trades))
    gross_profit = sum(max(0.0, t["pnl"]) for t in trades)
    gross_loss = abs(sum(min(0.0, t["pnl"]) for t in trades))
    profit_factor = gross_profit / gross_loss if gross_loss else None
    peak = -math.inf
    max_drawdown = 0.0
    for equity in equity_curve:
        peak = max(peak, equity)
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - equity) / peak * 100)

    result = {
        "strategy_key": strategy.key, "symbol": symbol, "interval": cfg.interval,
        "start_time": str(df.iloc[0].open_time), "end_time": str(df.iloc[-1].close_time),
        "starting_balance": cfg.starting_balance, "ending_balance": round(cash, 8),
        "total_return_pct": round((cash / cfg.starting_balance - 1) * 100, 4),
        "trades": len(trades), "wins": wins, "losses": losses,
        "win_rate": round(wins / len(trades) * 100, 2) if trades else 0.0,
        "profit_factor": round(profit_factor, 3) if profit_factor is not None else None,
        "max_drawdown_pct": round(max_drawdown, 3),
        "parameters": {"fee_rate": cfg.fee_rate, "slippage_rate": cfg.slippage_rate,
                       "max_position_value": cfg.max_position_value, "minimum_notional": cfg.minimum_notional},
    }
    return result, trades
