from __future__ import annotations

import pandas as pd


def add_indicators(frame: pd.DataFrame) -> pd.DataFrame:
    df = frame.copy()
    close = df["close"]
    df["ema20"] = close.ewm(span=20, adjust=False).mean()
    df["ema50"] = close.ewm(span=50, adjust=False).mean()
    df["ema200"] = close.ewm(span=200, adjust=False).mean()

    previous_close = close.shift(1)
    true_range = pd.concat([
        df["high"] - df["low"],
        (df["high"] - previous_close).abs(),
        (df["low"] - previous_close).abs(),
    ], axis=1).max(axis=1)
    df["atr"] = true_range.ewm(alpha=1 / 14, adjust=False).mean()
    df["atr_ratio"] = df["atr"] / close

    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    rs = gain / loss.replace(0, float("nan"))
    df["rsi"] = (100 - (100 / (1 + rs))).fillna(50)

    middle = close.rolling(20).mean()
    std = close.rolling(20).std(ddof=0)
    df["bb_mid"] = middle
    df["bb_upper"] = middle + 2 * std
    df["bb_lower"] = middle - 2 * std
    df["donchian_high_20"] = df["high"].shift(1).rolling(20).max()
    df["donchian_low_10"] = df["low"].shift(1).rolling(10).min()
    df["volume_median_20"] = df["volume"].rolling(20).median()
    df["range"] = df["high"] - df["low"]
    df["range_high_20"] = df["high"].shift(1).rolling(20).max()
    # These reference only candles that completed before the current one.  That
    # prevents the liquidity-sweep rule from comparing a candle to its own low.
    df["prior_low_20"] = df["low"].shift(1).rolling(20).min()
    # Mirror the low-side reference so a Futures short can use the same
    # mechanical liquidity-sweep definition at a prior swing high.
    df["prior_high_20"] = df["high"].shift(1).rolling(20).max()
    # Binance klines include taker-buy volume.  The remainder of total volume
    # is taker-sell volume, letting us calculate an exchange-specific, candle
    # level delta proxy without inventing a trade side from OHLC prices.
    df["taker_sell_base"] = df["volume"] - df["taker_base"]
    df["delta_base"] = df["taker_base"] - df["taker_sell_base"]
    df["delta_quote"] = 2 * df["taker_quote"] - df["quote_volume"]
    df["delta_ratio"] = df["delta_base"] / df["volume"].replace(0, float("nan"))
    df["delta_quote_abs_median_20"] = df["delta_quote"].abs().shift(1).rolling(20).median()
    # Six four-hour candles make a rolling 24-hour CVD, shown for context on
    # the dashboard.  It is not a full tick-by-tick or multi-exchange CVD.
    df["cvd_quote_24h"] = df["delta_quote"].rolling(6).sum()
    df["prior_cvd_24h_low_20"] = df["cvd_quote_24h"].shift(1).rolling(20).min()
    df["atr_ratio_q30"] = df["atr_ratio"].shift(1).rolling(100).quantile(0.30)
    return df
