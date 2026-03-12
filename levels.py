from __future__ import annotations
import time
import statistics
from dataclasses import dataclass
from typing import List
from config import LEVEL_MERGE_PCT, PRINT_DEBUG


# --- Anomaly detection settings ---
# A price zone is a "level" if its volume is >= ANOMALY_MULT * median volume
ANOMALY_MULT = 3.0
# Minimum number of zones needed to calculate meaningful statistics
MIN_ZONES_FOR_STATS = 5
# How many price buckets to divide each side into (bids / asks)
NUM_BUCKETS = 50
# Minimum distance between support and resistance as % of price
MIN_SR_DISTANCE_PCT = 0.003  # 0.3%


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


def build_levels(symbol: str, bids: dict, asks: dict, mid_price: float = 0.0) -> List[Level]:
    """Build levels from full orderbook using anomaly detection.

    Instead of a fixed USDT threshold, we:
    1. Bucket nearby prices into zones
    2. Calculate volume per zone
    3. Find zones with anomalously high volume (> ANOMALY_MULT * median)
    4. These become support/resistance levels
    """
    bid_levels = _find_anomaly_levels(bids, "bid", symbol, mid_price)
    ask_levels = _find_anomaly_levels(asks, "ask", symbol, mid_price)

    # Filter: ensure min distance between closest support and resistance
    if mid_price > 0 and bid_levels and ask_levels:
        min_dist = mid_price * MIN_SR_DISTANCE_PCT
        # Remove bid levels too close to mid price
        bid_levels = [l for l in bid_levels if mid_price - l.price >= min_dist]
        # Remove ask levels too close to mid price
        ask_levels = [l for l in ask_levels if l.price - mid_price >= min_dist]

    all_levels = bid_levels + ask_levels
    return _merge_levels(all_levels)


def _find_anomaly_levels(
    book_side: dict, side: str, symbol: str, mid_price: float
) -> List[Level]:
    """Find anomalous volume clusters in one side of the book."""
    if not book_side:
        return []

    prices = sorted(book_side.keys())
    if len(prices) < MIN_ZONES_FOR_STATS:
        return []

    # Bucket prices into zones
    price_min, price_max = prices[0], prices[-1]
    price_range = price_max - price_min
    if price_range <= 0:
        return []

    n_buckets = min(NUM_BUCKETS, len(prices))
    bucket_size = price_range / n_buckets

    # Aggregate volume into buckets
    buckets: list[dict] = []
    for i in range(n_buckets):
        lo = price_min + i * bucket_size
        hi = lo + bucket_size
        vol = 0.0
        weighted_price = 0.0
        count = 0
        for p in prices:
            if lo <= p < hi or (i == n_buckets - 1 and p == hi):
                qty = book_side[p]
                v = p * qty
                vol += v
                weighted_price += p * v
                count += 1
        if vol > 0:
            buckets.append({
                "price": weighted_price / vol,
                "volume": vol,
                "count": count,
            })

    if len(buckets) < MIN_ZONES_FOR_STATS:
        return []

    # Calculate statistics
    volumes = [b["volume"] for b in buckets]
    med = statistics.median(volumes)
    if med <= 0:
        med = statistics.mean(volumes)
    if med <= 0:
        return []

    threshold = med * ANOMALY_MULT

    # Find anomalous buckets
    levels: List[Level] = []
    for b in buckets:
        if b["volume"] >= threshold:
            levels.append(Level(
                side=side,
                price=b["price"],
                volume_usdt=b["volume"],
                symbol=symbol,
            ))

    if PRINT_DEBUG and levels:
        print(f"[LEVELS] {symbol} {side}: {len(levels)} anomalies "
              f"(median={med:.0f}, threshold={threshold:.0f})")

    return levels


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


def calc_strength(level: Level, med_volume: float = 0.0) -> float:
    """Calculate level strength from volume + liquidations."""
    if med_volume > 0:
        vol_score = level.volume_usdt / med_volume
    else:
        vol_score = 1.0
    if level.side == "bid":
        liq_score = level.liq_short_usdt / max(level.volume_usdt, 1)
    else:
        liq_score = level.liq_long_usdt / max(level.volume_usdt, 1)
    level.strength = vol_score + liq_score
    return level.strength
