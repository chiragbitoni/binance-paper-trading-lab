from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import pandas as pd


Signal = Callable[[pd.DataFrame, int], bool]
InitialStop = Callable[[pd.DataFrame, int, float], float]


@dataclass(frozen=True)
class Strategy:
    key: str
    name: str
    description: str
    entry: Signal
    exit: Signal
    stop_atr: float
    trailing_atr: float | None
    initial_stop: InitialStop | None = None


def _valid(df: pd.DataFrame, i: int, columns: list[str]) -> bool:
    return i > 0 and all(pd.notna(df.iloc[i][column]) for column in columns)


def trend_breakout_entry(df: pd.DataFrame, i: int) -> bool:
    if not _valid(df, i, ["ema200", "donchian_high_20", "volume_median_20"]):
        return False
    row = df.iloc[i]
    return row.close > row.ema200 and row.close > row.donchian_high_20 and row.volume > row.volume_median_20


def trend_breakout_exit(df: pd.DataFrame, i: int) -> bool:
    return _valid(df, i, ["donchian_low_10"]) and df.iloc[i].close < df.iloc[i].donchian_low_10


def ema_trend_entry(df: pd.DataFrame, i: int) -> bool:
    if not _valid(df, i, ["ema50", "ema200"]):
        return False
    row, prev = df.iloc[i], df.iloc[i - 1]
    return row.ema50 > row.ema200 and row.close > row.ema50 and prev.close <= prev.ema50


def ema_trend_exit(df: pd.DataFrame, i: int) -> bool:
    return _valid(df, i, ["ema50"]) and df.iloc[i].close < df.iloc[i].ema50


def slow_trend_entry(df: pd.DataFrame, i: int) -> bool:
    if not _valid(df, i, ["ema200"]):
        return False
    row, prev = df.iloc[i], df.iloc[i - 1]
    return row.close > row.ema200 and prev.close <= prev.ema200


def slow_trend_exit(df: pd.DataFrame, i: int) -> bool:
    return _valid(df, i, ["ema200"]) and df.iloc[i].close < df.iloc[i].ema200


def golden_cross_entry(df: pd.DataFrame, i: int) -> bool:
    if not _valid(df, i, ["ema50", "ema200"]):
        return False
    row, prev = df.iloc[i], df.iloc[i - 1]
    return row.ema50 > row.ema200 and prev.ema50 <= prev.ema200


def golden_cross_exit(df: pd.DataFrame, i: int) -> bool:
    if not _valid(df, i, ["ema50", "ema200"]):
        return False
    row, prev = df.iloc[i], df.iloc[i - 1]
    return row.ema50 < row.ema200 and prev.ema50 >= prev.ema200


def volatility_breakout_entry(df: pd.DataFrame, i: int) -> bool:
    if not _valid(df, i, ["ema200", "range_high_20", "atr_ratio_q30", "atr_ratio"]):
        return False
    row = df.iloc[i]
    was_quiet = df.iloc[i - 1].atr_ratio < df.iloc[i - 1].atr_ratio_q30
    return was_quiet and row.close > row.ema200 and row.close > row.range_high_20 and row.volume > row.volume_median_20


def volatility_breakout_exit(df: pd.DataFrame, i: int) -> bool:
    return _valid(df, i, ["ema20"]) and df.iloc[i].close < df.iloc[i].ema20


def bollinger_rsi_entry(df: pd.DataFrame, i: int) -> bool:
    if not _valid(df, i, ["bb_lower", "rsi", "ema200"]):
        return False
    row = df.iloc[i]
    return row.close > row.ema200 and row.close < row.bb_lower and row.rsi < 30


def bollinger_rsi_exit(df: pd.DataFrame, i: int) -> bool:
    if not _valid(df, i, ["bb_mid", "rsi"]):
        return False
    row = df.iloc[i]
    return row.close >= row.bb_mid or row.rsi >= 60


def _liquidity_sweep_reclaim(df: pd.DataFrame, i: int) -> bool:
    """Buy a confirmed reclaim after a 20-candle sell-side liquidity sweep.

    The sweep is the previous completed candle: it must pierce the prior
    20-candle low, reclaim that level by its close, and leave a meaningful lower
    wick.  The current candle confirms the reclaim by closing above the sweep
    candle high.  The 200 EMA filter keeps this Spot-only strategy long-only in
    a broad uptrend.
    """
    if i < 2 or not _valid(df, i, ["ema200", "atr"]):
        return False
    sweep = df.iloc[i - 1]
    confirm = df.iloc[i]
    level = sweep.prior_low_20
    if pd.isna(level) or pd.isna(sweep.atr):
        return False
    lower_wick = min(sweep.open, sweep.close) - sweep.low
    return (
        confirm.close > confirm.ema200
        and sweep.low < level
        and sweep.close > level
        and lower_wick >= 0.5 * sweep.atr
        and confirm.close > sweep.high
    )


def liquidity_sweep_entry(df: pd.DataFrame, i: int) -> bool:
    return _liquidity_sweep_reclaim(df, i)


def liquidity_sweep_exit(df: pd.DataFrame, i: int) -> bool:
    """Exit when the post-sweep recovery fails its medium-term trend filter."""
    return _valid(df, i, ["ema50"]) and df.iloc[i].close < df.iloc[i].ema50


def liquidity_sweep_initial_stop(df: pd.DataFrame, i: int, fill: float) -> float:
    """Place the invalidation beyond the sweep wick with a small ATR buffer."""
    sweep = df.iloc[i - 1]
    wick_stop = sweep.low - 0.25 * sweep.atr
    # A gap at execution must never create a stop above the actual fill.
    return min(wick_stop, fill - 0.5 * sweep.atr)


def delta_liquidity_sweep_entry(df: pd.DataFrame, i: int) -> bool:
    """Require strong positive taker-volume delta on the reclaim confirmation.

    This uses Binance's candle-level taker-buy fields, so it is a single-venue
    proxy—not a tick-level, multi-exchange CVD signal.
    """
    if not _liquidity_sweep_reclaim(df, i):
        return False
    row = df.iloc[i]
    if pd.isna(row.delta_ratio) or pd.isna(row.delta_quote_abs_median_20):
        return False
    return row.delta_ratio >= 0.10 and row.delta_quote >= row.delta_quote_abs_median_20


def delta_divergence_entry(df: pd.DataFrame, i: int) -> bool:
    """Independent long-only CVD-divergence setup, without a sweep requirement.

    The previous candle must make a fresh 20-candle price low while the rolling
    24-hour CVD does not make a matching low.  A subsequent bullish candle that
    closes above the divergence candle high confirms the entry.  The EMA filter
    keeps the Spot-only implementation aligned with the wider trend.
    """
    if i < 2 or not _valid(df, i, ["ema200", "atr"]):
        return False
    divergence = df.iloc[i - 1]
    confirm = df.iloc[i]
    if pd.isna(divergence.prior_low_20) or pd.isna(divergence.prior_cvd_24h_low_20):
        return False
    price_makes_new_low = divergence.low < divergence.prior_low_20
    cvd_holds_higher = divergence.cvd_quote_24h > divergence.prior_cvd_24h_low_20
    return (
        price_makes_new_low
        and cvd_holds_higher
        and confirm.close > confirm.ema200
        and confirm.close > confirm.open
        and confirm.close > divergence.high
    )


def delta_divergence_exit(df: pd.DataFrame, i: int) -> bool:
    return _valid(df, i, ["ema20"]) and df.iloc[i].close < df.iloc[i].ema20


def delta_divergence_initial_stop(df: pd.DataFrame, i: int, fill: float) -> float:
    divergence = df.iloc[i - 1]
    return min(divergence.low - 0.25 * divergence.atr, fill - 0.5 * divergence.atr)


def buy_hold_entry(df: pd.DataFrame, i: int) -> bool:
    return i == 200


def never_exit(df: pd.DataFrame, i: int) -> bool:
    return False


STRATEGIES: dict[str, Strategy] = {
    "trend_breakout": Strategy("trend_breakout", "Trend + Donchian breakout",
                               "20-bar breakout above the 200 EMA with volume confirmation.",
                               trend_breakout_entry, trend_breakout_exit, 2.0, 3.0),
    "slow_trend": Strategy("slow_trend", "Slow EMA regime",
                           "Holds BTC while price remains above the 200 EMA.",
                           slow_trend_entry, slow_trend_exit, 3.0, None),
    "golden_cross": Strategy("golden_cross", "50/200 EMA regime",
                             "Enters on a 50/200 EMA golden cross and exits on the reverse cross.",
                             golden_cross_entry, golden_cross_exit, 4.0, None),
    "ema_trend": Strategy("ema_trend", "EMA trend pullback",
                          "Buys a recovery above the 50 EMA while the 50 EMA is above the 200 EMA.",
                          ema_trend_entry, ema_trend_exit, 2.0, 3.0),
    "volatility_breakout": Strategy("volatility_breakout", "Quiet-to-active breakout",
                                    "Follows a range breakout after compressed ATR conditions.",
                                    volatility_breakout_entry, volatility_breakout_exit, 2.0, 2.5),
    "bollinger_rsi": Strategy("bollinger_rsi", "Trend-filtered Bollinger recovery",
                              "Buys an oversold Bollinger move only above the 200 EMA.",
                              bollinger_rsi_entry, bollinger_rsi_exit, 2.0, None),
    "liquidity_sweep": Strategy("liquidity_sweep", "Liquidity-sweep reclaim (research-only)",
                                 "Buys a 20-candle low sweep only after a 4h reclaim and confirmation above the sweep high.",
                                 liquidity_sweep_entry, liquidity_sweep_exit, 2.0, None,
                                 liquidity_sweep_initial_stop),
    "delta_liquidity_sweep": Strategy("delta_liquidity_sweep", "Sweep + delta combo (research-only)",
                                       "Adds positive taker-volume delta and above-median flow to the liquidity-sweep reclaim.",
                                       delta_liquidity_sweep_entry, liquidity_sweep_exit, 2.0, None,
                                       liquidity_sweep_initial_stop),
    "delta_divergence": Strategy("delta_divergence", "CVD divergence (research-only)",
                                  "Independent price/CVD divergence: a new price low without a matching 24h CVD low, then bullish confirmation.",
                                  delta_divergence_entry, delta_divergence_exit, 2.0, None,
                                  delta_divergence_initial_stop),
    "buy_hold_benchmark": Strategy("buy_hold_benchmark", "Buy-and-hold benchmark",
                                   "Buys once at the common warm-up point and holds to the test end.",
                                   buy_hold_entry, never_exit, 1000.0, None),
}
