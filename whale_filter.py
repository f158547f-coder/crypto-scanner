"""WhalePortal-style filter: Funding Rate, OI change, Taker ratio, Premium Index.

Fetches derivative metrics from Binance Futures API and checks
whether they align with the proposed trade direction.
If metrics contradict the signal -> reject.
"""
from __future__ import annotations

import time
import httpx
from dataclasses import dataclass
from config import PRINT_DEBUG

BASE = "https://fapi.binance.com"
DATA_BASE = "https://fapi.binance.com/futures/data"

FUNDING_EXTREME = 0.0005
OI_CHANGE_PCT_THRESHOLD = 3.0
TAKER_BULL_THRESHOLD = 1.05
TAKER_BEAR_THRESHOLD = 0.95
PREMIUM_EXTREME = 0.0003

_cache: dict[str, tuple[float, dict]] = {}
CACHE_TTL = 120


@dataclass
class WhaleData:
    funding_rate: float
    premium_index: float
    taker_ratio: float
    oi_change_pct: float
    long_short_ratio: float


async def _get_json(url: str, params: dict | None = None) -> dict | list | None:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, params=params)
            return resp.json()
    except Exception as e:
        if PRINT_DEBUG:
            print(f"[WHALE] fetch error {url}: {e}")
        return None


async def fetch_whale_data(symbol: str) -> WhaleData | None:
    sym = symbol.upper()
    now = time.time()
    if sym in _cache:
        ts, cached = _cache[sym]
        if now - ts < CACHE_TTL:
            return cached

    premium_data = await _get_json(f"{BASE}/fapi/v1/premiumIndex", {"symbol": sym})
    if not premium_data or isinstance(premium_data, list):
        return None

    funding_rate = float(premium_data.get("lastFundingRate", 0))
    mark_p = float(premium_data.get("markPrice", 0))
    index_p = float(premium_data.get("indexPrice", 1))
    premium_index = (mark_p - index_p) / index_p if index_p > 0 else 0.0

    taker_data = await _get_json(
        f"{DATA_BASE}/takerlongshortRatio",
        {"symbol": sym, "period": "5m", "limit": 4}
    )
    taker_ratio = 1.0
    if taker_data and isinstance(taker_data, list) and len(taker_data) > 0:
        ratios = [float(d.get("buySellRatio", 1.0)) for d in taker_data]
        taker_ratio = sum(ratios) / len(ratios)

      oi_data = await _get_json(
        f"{DATA_BASE}/openInterestHist",
        {"symbol": sym, "period": "5m", "limit": 13}
    )
    oi_change_pct = 0.0
    if oi_data and isinstance(oi_data, list) and len(oi_data) >= 2:
        oi_old = float(oi_data[0].get("sumOpenInterestValue", 0))
        oi_new = float(oi_data[-1].get("sumOpenInterestValue", 0))
        if oi_old > 0:
            oi_change_pct = ((oi_new - oi_old) / oi_old) * 100

    ls_data = await _get_json(
        f"{DATA_BASE}/globalLongShortAccountRatio",
        {"symbol": sym, "period": "5m", "limit": 4}
    )
    ls_ratio = 1.0
    if ls_data and isinstance(ls_data, list) and len(ls_data) > 0:
        ls_ratios = [float(d.get("longShortRatio", 1.0)) for d in ls_data]
        ls_ratio = sum(ls_ratios) / len(ls_ratios)

    result = WhaleData(
        funding_rate=funding_rate,
        premium_index=premium_index,
        taker_ratio=taker_ratio,
        oi_change_pct=oi_change_pct,
        long_short_ratio=ls_ratio,
    )
    _cache[sym] = (now, result)
    return result


def whale_verdict(data: WhaleData, direction: str) -> tuple[bool, str, str]:
    issues = []
    is_long = direction.upper() == "LONG"

    if is_long and data.funding_rate > FUNDING_EXTREME:
        issues.append(f"FundRate {data.funding_rate:.5f} high for LONG")
    elif not is_long and data.funding_rate < -FUNDING_EXTREME:
        issues.append(f"FundRate {data.funding_rate:.5f} neg for SHORT")

    if is_long and data.premium_index < -PREMIUM_EXTREME:
        issues.append(f"Premium {data.premium_index:.5f} neg")
    elif not is_long and data.premium_index > PREMIUM_EXTREME:
        issues.append(f"Premium {data.premium_index:.5f} pos")

    if is_long and data.taker_ratio < TAKER_BEAR_THRESHOLD:
        issues.append(f"Taker {data.taker_ratio:.3f} sellers")
    elif not is_long and data.taker_ratio > TAKER_BULL_THRESHOLD:
        issues.append(f"Taker {data.taker_ratio:.3f} buyers")

      if abs(data.oi_change_pct) > OI_CHANGE_PCT_THRESHOLD:
        if is_long and data.oi_change_pct < -OI_CHANGE_PCT_THRESHOLD:
            issues.append(f"OI drop {data.oi_change_pct:.1f}%")
        elif not is_long and data.oi_change_pct > OI_CHANGE_PCT_THRESHOLD:
            issues.append(f"OI rise {data.oi_change_pct:.1f}%")

    if is_long and data.long_short_ratio > 2.0:
        issues.append(f"L/S {data.long_short_ratio:.2f} crowded longs")
    elif not is_long and data.long_short_ratio < 0.5:
        issues.append(f"L/S {data.long_short_ratio:.2f} crowded shorts")

    summary = (
        f"FR:{data.funding_rate:+.5f} | "
        f"Prem:{data.premium_index:+.5f} | "
        f"Taker:{data.taker_ratio:.3f} | "
        f"OI:{data.oi_change_pct:+.1f}% | "
        f"L/S:{data.long_short_ratio:.2f}"
    )

    passed = len(issues) <= 1
    reason = "; ".join(issues) if issues else "All metrics aligned"

    if PRINT_DEBUG:
        status = "PASS" if passed else "REJECT"
        print(f"[WHALE] {direction} {status}: {reason}")

    return passed, reason, summary


async def check_whale_filter(symbol: str, direction: str) -> tuple[bool, str, str]:
    """Main entry: fetch data + run verdict.
    Returns: (passed, reason, summary)
    """
    data = await fetch_whale_data(symbol)
    if data is None:
        return True, "WhaleData unavailable", "N/A"
    return whale_verdict(data, direction)
