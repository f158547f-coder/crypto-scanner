"""Order book management + deep REST fetcher with proxy support."""
from __future__ import annotations
import httpx
from proxy_pool import proxy_pool
from config import PRINT_DEBUG

DEPTH_URL = "https://api.binance.com/api/v3/depth"


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


async def fetch_deep_orderbook(symbol: str, limit: int = 5000) -> tuple[dict, dict]:
    """Fetch deep order book via Binance spot REST API through proxy.

    Returns (bids_dict, asks_dict) where key=price, value=qty.
    Uses proxy pool to bypass geo-restrictions.
    """
    params = {"symbol": symbol.upper(), "limit": limit}

    # Try with proxy first
    for attempt in range(3):
        proxy_url = await proxy_pool.get()
        if proxy_url:
            try:
                async with httpx.AsyncClient(proxy=proxy_url, timeout=15) as client:
                    resp = await client.get(DEPTH_URL, params=params)
                    data = resp.json()
                    if "bids" in data and "asks" in data:
                        bids = {float(p): float(q) for p, q in data["bids"] if float(q) > 0}
                        asks = {float(p): float(q) for p, q in data["asks"] if float(q) > 0}
                        if PRINT_DEBUG:
                            print(f"[DEPTH] {symbol}: {len(bids)} bids, {len(asks)} asks (proxy)")
                        return bids, asks
                    else:
                        if PRINT_DEBUG:
                            print(f"[DEPTH] {symbol}: bad response via proxy: {str(data)[:80]}")
                        proxy_pool.remove(proxy_url)
            except Exception as e:
                if PRINT_DEBUG:
                    print(f"[DEPTH] {symbol} proxy error: {e}")
                if proxy_url:
                    proxy_pool.remove(proxy_url)

    # Fallback: try direct (works if not geo-blocked)
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(DEPTH_URL, params=params)
            data = resp.json()
            if "bids" in data and "asks" in data:
                bids = {float(p): float(q) for p, q in data["bids"] if float(q) > 0}
                asks = {float(p): float(q) for p, q in data["asks"] if float(q) > 0}
                if PRINT_DEBUG:
                    print(f"[DEPTH] {symbol}: {len(bids)} bids, {len(asks)} asks (direct)")
                return bids, asks
    except Exception as e:
        if PRINT_DEBUG:
            print(f"[DEPTH] {symbol} direct error: {e}")

    return {}, {}
