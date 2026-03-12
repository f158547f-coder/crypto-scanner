from __future__ import annotations
from context_candles import CandleContext
from config import (PIN_BAR_WICK_RATIO, MIN_CANDLE_BODY_PCT,
                    MAX_CANDLE_RANGE_PCT, USE_CANDLE_PATTERNS,
                    USE_TREND_FILTER, USE_FALSE_BREAK, USE_VOLATILITY_FILTER)


def is_bullish_pin_bar(candle: dict, level_price: float) -> bool:
    """Long lower wick near support, body in upper half."""
    if not candle:
        return False
    o, h, l, c = candle["o"], candle["h"], candle["l"], candle["c"]
    body = abs(c - o)
    full_range = h - l
    if full_range == 0:
        return False
    lower_wick = min(o, c) - l
    upper_wick = h - max(o, c)
    if body == 0:
        body = full_range * 0.001
    return (lower_wick / body >= PIN_BAR_WICK_RATIO
            and c >= o  # bullish close
            and lower_wick > upper_wick)


def is_bearish_pin_bar(candle: dict, level_price: float) -> bool:
    """Long upper wick near resistance, body in lower half."""
    if not candle:
        return False
    o, h, l, c = candle["o"], candle["h"], candle["l"], candle["c"]
    body = abs(c - o)
    full_range = h - l
    if full_range == 0:
        return False
    lower_wick = min(o, c) - l
    upper_wick = h - max(o, c)
    if body == 0:
        body = full_range * 0.001
    return (upper_wick / body >= PIN_BAR_WICK_RATIO
            and c <= o  # bearish close
            and upper_wick > lower_wick)


def is_false_break(candles: list[dict], level_price: float, side: str) -> bool:
    """Check if price pierced level and returned (false breakout)."""
    if len(candles) < 3:
        return False
    if side == "bid":  # support: price went below then came back above
        pierced = any(c["l"] < level_price for c in candles[-3:])
        returned = candles[-1]["c"] > level_price
        return pierced and returned
    else:  # resistance: price went above then came back below
        pierced = any(c["h"] > level_price for c in candles[-3:])
        returned = candles[-1]["c"] < level_price
        return pierced and returned


def is_trend_ok(symbol: str, direction: str, ctx: CandleContext) -> bool:
    """Check if trade direction aligns with EMA trend."""
    ema_f = ctx.ema_fast.get(symbol, 0)
    ema_s = ctx.ema_slow.get(symbol, 0)
    if not ema_f or not ema_s:
        return True  # no data, allow
    if direction == "LONG":
        return ema_f >= ema_s  # uptrend
    else:
        return ema_f <= ema_s  # downtrend


def is_volatility_ok(candle: dict) -> bool:
    """Check candle range is not too small or too large."""
    if not candle:
        return False
    o, h, l = candle["o"], candle["h"], candle["l"]
    if o == 0:
        return False
    range_pct = (h - l) / o
    body_pct = abs(candle["c"] - o) / o
    return body_pct >= MIN_CANDLE_BODY_PCT and range_pct <= MAX_CANDLE_RANGE_PCT


def check_entry_filters(symbol: str, direction: str, level_price: float,
                        ctx: CandleContext) -> tuple[bool, str]:
    """Run all enabled filters. Returns (passed, reason_string)."""
    reasons = []
    candle = ctx.last_entry_candle(symbol)
    prev = ctx.prev_entry_candles(symbol, 5)

    if USE_CANDLE_PATTERNS:
        if direction == "LONG":
            if is_bullish_pin_bar(candle, level_price):
                reasons.append("bullish pin bar")
            else:
                return False, "no bullish candle pattern"
        else:
            if is_bearish_pin_bar(candle, level_price):
                reasons.append("bearish pin bar")
            else:
                return False, "no bearish candle pattern"

    if USE_FALSE_BREAK:
        if is_false_break(prev + ([candle] if candle else []), level_price,
                          "bid" if direction == "LONG" else "ask"):
            reasons.append("false break")

    if USE_TREND_FILTER:
        if not is_trend_ok(symbol, direction, ctx):
            return False, "against trend"
        reasons.append("trend OK")

    if USE_VOLATILITY_FILTER:
        if not is_volatility_ok(candle):
            return False, "volatility filter"
        reasons.append("vol OK")

    return True, ", ".join(reasons) if reasons else "base signal"
