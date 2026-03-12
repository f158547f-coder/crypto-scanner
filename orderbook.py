from collections import defaultdict


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
        return None

    def top_bids(self, n: int = 20) -> list[tuple[float, float]]:
        """Return top-N bid levels sorted desc by price."""
        return sorted(self.bids.items(), key=lambda x: -x[0])[:n]

    def top_asks(self, n: int = 20) -> list[tuple[float, float]]:
        """Return top-N ask levels sorted asc by price."""
        return sorted(self.asks.items(), key=lambda x: x[0])[:n]
