"""Order book management + deep REST fetcher with direct + proxy fallback."""
from __future__ import annotations
import httpx
from proxy_pool import proxy_pool
from config import PRINT_DEBUG

# Use futures API (fapi) as primary - works from most locations
DEPTH_URLS = [
    "https://fapi.binance.com/fapi/v1/depth",
    "https://api.binance.com/api/v3/depth",
]


class OrderBook:
    """Simple local order book maintained via depth diff stream."""

    def __init__(self):
        self.bids: dict[float, float] = {}  # price -> qty
        self.asks: dict[float, float] = {}  # price -> qty

    def update_depth(self, bids: list, asks: list) -> None:
        for price_str, qty_str in bids:
            price, qty = float(price_str), float(qty_str)
            if qty == 0:
                self.bids.pop(price, None)
            else:
                self.bids[price] = qty
        for price_str, qty_str in asks:
            price, qty = float(price_str), float(qty_str)
            if qty == 0:
                self.asks.pop(price, None)
            else:
                self.asks[price] = qty

    def best_bid(self) -> float | None:
        return max(self.bids.keys()) if self.bids else None

    def best_ask(self) -> float | None:
        return min(self.asks.keys()) if self.asks else None

    def mid_price(self) -> float | None:
        bb, ba = self.best_bid(), self.best_ask()
        if bb and ba:
            return (bb + ba) / 2
        return bb or ba

    def top_bids(self, n: int = 10) -> list[tuple[float, float]]:
        return sorted(self.bids.items(), reverse=True)[:n]

    def top_asks(self, n: int = 10) -> list[tuple[float, float]]:
        return sorted(self.asks.items())[:n]


def _parse_depth(data: dict) -> tuple[dict, dict] | None:
    """Parse bids/asks from Binance depth response."""
    if "bids" in data and "asks" in data:
        bids = {float(p): float(q) for p, q in data["bids"] if float(q) > 0}
        asks = {float(p): float(q) for p, q in data["asks"] if float(q) > 0}
        return bids, asks
    return None


async def fetch_deep_orderbook(symbol: str, limit: int = 1000) -> tuple[dict, dict]:
    """Fetch deep order book - try direct first, then proxy fallback.
    Returns (bids_dict, asks_dict) where key=price, value=qty.
    """
    params = {"symbol": symbol.upper(), "limit": limit}

    # 1. Try DIRECT first (works from Railway us-west2)
    for url in DEPTH_URLS:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(url, params=params)
                data = resp.json()
                result = _parse_depth(data)
                if result:
                    if PRINT_DEBUG:
                        print(f"[DEPTH] {symbol}: {len(result[0])} bids, {len(result[1])} asks (direct)")
                    return result
        except Exception as e:
            if PRINT_DEBUG:
                print(f"[DEPTH] {symbol} direct error ({url.split('/')[2]}): {e}")

    # 2. Fallback to PROXY if direct fails
    for attempt in range(2):
        proxy_url = await proxy_pool.get()
        if not proxy_url:
            break
        try:
            async with httpx.AsyncClient(proxy=proxy_url, timeout=15) as client:
                resp = await client.get(DEPTH_URLS[0], params=params)
                data = resp.json()
                result = _parse_depth(data)
                if result:
                    if PRINT_DEBUG:
                        print(f"[DEPTH] {symbol}: {len(result[0])} bids, {len(result[1])} asks (proxy)")
                    return result
                else:
                    if PRINT_DEBUG:
                        print(f"[DEPTH] {symbol}: bad proxy response: {str(data)[:80]}")
                    proxy_pool.remove(proxy_url)
        except Exception as e:
            if PRINT_DEBUG:
                print(f"[DEPTH] {symbol} proxy error: {e}")
            if proxy_url:
                proxy_pool.remove(proxy_url)

    return {}, {}
