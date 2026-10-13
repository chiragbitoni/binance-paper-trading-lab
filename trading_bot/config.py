from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    mode: str
    symbols: list[str]
    interval: str
    starting_balance: float
    max_position_value: float
    fee_rate: float
    slippage_rate: float
    minimum_notional: float
    trailing_stop_atr: float
    history_days: int
    enabled_strategies: list[str]
    research_only_strategies: list[str]
    database: str
    dashboard_host: str
    dashboard_port: int
    dhan_mcx: dict
    futures_paper: dict


def load_config(path: str | Path = "config.json") -> Config:
    source = Path(path)
    raw = json.loads(source.read_text(encoding="utf-8"))
    cfg = Config(**raw)
    if cfg.mode != "paper":
        raise ValueError("only paper mode is supported; live execution is intentionally not implemented")
    if cfg.starting_balance <= 0 or cfg.max_position_value <= 0:
        raise ValueError("balances must be positive")
    if cfg.minimum_notional <= 0:
        raise ValueError("minimum_notional must be positive")
    if cfg.trailing_stop_atr <= 0:
        raise ValueError("trailing_stop_atr must be positive")
    if not 0 <= cfg.fee_rate < 1 or not 0 <= cfg.slippage_rate < 1:
        raise ValueError("fee_rate and slippage_rate must be between 0 (inclusive) and 1 (exclusive)")
    if cfg.history_days < 35:
        raise ValueError("history_days must allow indicator warm-up")
    if not cfg.symbols or len(cfg.symbols) != len(set(cfg.symbols)):
        raise ValueError("symbols must contain at least one unique market")
    dhan = cfg.dhan_mcx
    required_dhan = {"enabled", "live_order_submission", "allocated_capital_inr", "max_daily_loss_inr",
                     "max_loss_per_trade_inr", "max_open_positions", "default_product_type",
                     "auto_square_off_minutes_before_close", "allowed_security_ids"}
    missing_dhan = required_dhan - set(dhan)
    if missing_dhan:
        raise ValueError(f"dhan_mcx is missing: {', '.join(sorted(missing_dhan))}")
    if dhan["default_product_type"] not in {"INTRADAY", "MARGIN"}:
        raise ValueError("dhan_mcx default_product_type must be INTRADAY or MARGIN")
    if dhan["max_open_positions"] < 1:
        raise ValueError("dhan_mcx max_open_positions must be at least one")
    if dhan["auto_square_off_minutes_before_close"] < 0:
        raise ValueError("dhan_mcx auto_square_off_minutes_before_close cannot be negative")
    risk_values = ("allocated_capital_inr", "max_daily_loss_inr", "max_loss_per_trade_inr")
    if any(float(dhan[field]) < 0 for field in risk_values):
        raise ValueError("dhan_mcx risk amounts cannot be negative")
    if dhan["live_order_submission"] and not dhan["enabled"]:
        raise ValueError("dhan_mcx must be enabled before live_order_submission can be enabled")
    if set(cfg.enabled_strategies) & set(cfg.research_only_strategies):
        raise ValueError("a strategy cannot be both enabled and research-only")
    from .strategies import STRATEGIES
    unknown = set(cfg.enabled_strategies + cfg.research_only_strategies) - set(STRATEGIES)
    if unknown:
        raise ValueError(f"unknown strategy keys: {', '.join(sorted(unknown))}")
    futures = cfg.futures_paper
    required_futures = {"enabled", "symbols", "interval", "starting_balance", "margin_per_trade", "leverage",
                        "fee_rate", "slippage_rate", "minimum_notional", "max_entry_funding_rate", "trailing_stop_atr"}
    missing_futures = required_futures - set(futures)
    if missing_futures:
        raise ValueError(f"futures_paper is missing: {', '.join(sorted(missing_futures))}")
    if futures["leverage"] <= 0 or futures["leverage"] > 3:
        raise ValueError("futures_paper leverage must be between 0 and 3 for this paper lab")
    if futures["starting_balance"] <= 0 or futures["margin_per_trade"] <= 0 or futures["minimum_notional"] <= 0:
        raise ValueError("futures_paper balances must be positive")
    if futures["trailing_stop_atr"] <= 0:
        raise ValueError("futures_paper trailing_stop_atr must be positive")
    if not 0 <= futures["fee_rate"] < 1 or not 0 <= futures["slippage_rate"] < 1:
        raise ValueError("futures_paper fee_rate and slippage_rate must be between 0 and 1")
    if not futures["symbols"] or len(futures["symbols"]) != len(set(futures["symbols"])):
        raise ValueError("futures_paper symbols must contain at least one unique market")
    return cfg
