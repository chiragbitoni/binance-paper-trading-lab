from __future__ import annotations


def stop_fill_price(open_price: float, low_price: float, stop_price: float, slippage_rate: float) -> float | None:
    """Return the simulated stop fill, or None when the stop was not touched."""
    if low_price > stop_price:
        return None
    return min(open_price, stop_price) * (1 - slippage_rate)


def advance_trailing_stop(
    stop_price: float, high_water: float, candle_high: float, atr: float, trailing_atr: float | None
) -> tuple[float, float]:
    """Advance a trailing stop only after the candle has finished.

    A completed candle cannot use its high to tighten a stop and then claim the
    same candle's earlier low triggered that new stop.  Call this only after the
    pre-existing stop has been checked against that candle's low.
    """
    next_high_water = max(high_water, candle_high)
    if trailing_atr is None:
        return stop_price, next_high_water
    return max(stop_price, next_high_water - trailing_atr * atr), next_high_water
