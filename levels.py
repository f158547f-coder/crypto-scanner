from __future__ import annotations
import time
from dataclasses import dataclass, field
from typing import List
from orderbook import OrderBook
from config import LARGE_LEVEL_USDT, LEVEL_MERGE_PCT


@dataclass
class Level:
    side: str            # "bid" or "ask"
    price: float
    volume_usdt: float
    symbol: str
    strength: float = 0.0
    state: str = "FAR"   # FAR / NEAR / TOUCHED / SIGNALLED
    last_signal_ts: float = 0.0
    liq_long_usdt: float = 0.0
    liq_short_usdt: float = 0.0


def build_levels(symbol: str, ob: OrderBook) -> List[Level]:
    """Scan order book for large limit clusters."""
    raw: List[Level] = []

    for price, qty in ob.bids.items():
        vol = price * qty
        if vol >= LARGE_LEVEL_USDT:
            raw.append(Level(side="bid", price=price, volume_usdt=vol, symbol=symbol))

    for price, qty in ob.asks.items():
        vol = price * qty
        if vol >= LARGE_LEVEL_USDT:
            raw.append(Level(side="ask", price=price, volume_usdt=vol, symbol=symbol))

    return _merge_levels(raw)


def _merge_levels(levels: List[Level]) -> List[Level]:
    """Merge nearby levels of same side into clusters."""
    if not levels:
        return []
    levels = sorted(levels, key=lambda x: x.price)
    merged: List[Level] = [levels[0]]
    for lvl in levels[1:]:
        prev = merged[-1]
        if (lvl.side == prev.side
                and abs(lvl.price - prev.price) / max(prev.price, 1e-9) <= LEVEL_MERGE_PCT):
            total = prev.volume_usdt + lvl.volume_usdt
            avg_price = (prev.price * prev.volume_usdt + lvl.price * lvl.volume_usdt) / total
            prev.price = avg_price
            prev.volume_usdt = total
        else:
            merged.append(lvl)
    return merged


def calc_strength(level: Level) -> float:
    """Calculate level strength from volume + liquidations."""
    vol_score = level.volume_usdt / LARGE_LEVEL_USDT  # 1.0 = minimum
    if level.side == "bid":
        liq_score = level.liq_short_usdt / max(LARGE_LEVEL_USDT, 1)
    else:
        liq_score = level.liq_long_usdt / max(LARGE_LEVEL_USDT, 1)
    level.strength = vol_score + liq_score
    return level.strength
