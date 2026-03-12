"""Order book management + deep REST fetcher."""
from __future__ import annotations
import httpx
from config import PRINT_DEBUG

# Spot API endpoints (work globally, no geo-block)
DEPTH_URLS = [
    "https://api.binance.com/api/v3/depth",
    "https://api1.binance.com/api/v3/depth",
    "https://api2.binance.com/api/v3/depth",
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


async def fetch_deep_orderbook(symbol: str, limit: int = 5000) -> tuple[dict, dict]:
    """Fetch deep order book via Binance spot REST API.

    Returns (bids_dict, asks_dict) where key=price, value=qty.
    Spot API limit max is 5000. This gives us full depth.
    """
    params = {"symbol": symbol.upper(), "limit": limit}
    async with httpx.AsyncClient(timeout=15) as client:
        for url in DEPTH_URLS:
            try:
                resp = await client.get(url, params=params)
                data = resp.json()
                if "bids" not in data or "asks" not in data:
                    if PRINT_DEBUG:
                        print(f"[DEPTH] {symbol}: unexpected response: {str(data)[:100]}")
                    continue
                bids = {}
                for p, q in data["bids"]:
                    price, qty = float(p), float(q)
                    if qty > 0:
                        bids[price] = qty
                asks = {}
                for p, q in data["asks"]:
                    price, qty = float(p), float(q)
                    if qty > 0:
                        asks[price] = qty
                if PRINT_DEBUG:
                    print(f"[DEPTH] {symbol}: {len(bids)} bids, {len(asks)} asks")
                return bids, asks
            except Exception as e:
                if PRINT_DEBUG:
                    print(f"[DEPTH] error {symbol} {url}: {e}")
                continue
    return {}, {}
