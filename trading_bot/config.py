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
    history_days: int
    enabled_strategies: list[str]
    research_only_strategies: list[str]
    database: str
    dashboard_host: str
    dashboard_port: int


def load_config(path: str | Path = "config.json") -> Config:
    source = Path(path)
    raw = json.loads(source.read_text(encoding="utf-8"))
    cfg = Config(**raw)
    if cfg.mode not in {"paper", "live"}:
        raise ValueError("mode must be 'paper' or 'live'")
    if cfg.starting_balance <= 0 or cfg.max_position_value <= 0:
        raise ValueError("balances must be positive")
    if not cfg.symbols or len(cfg.symbols) != len(set(cfg.symbols)):
        raise ValueError("symbols must contain at least one unique market")
    if set(cfg.enabled_strategies) & set(cfg.research_only_strategies):
        raise ValueError("a strategy cannot be both enabled and research-only")
    return cfg
