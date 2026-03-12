from __future__ import annotations
import time
from dataclasses import dataclass, field
from collections import defaultdict
from typing import List
from config import LIQ_WINDOW_SEC, LEVEL_TOUCH_PCT
from levels import Level


@dataclass
class LiqEvent:
    symbol: str
    side: str       # "BUY" (short liq) or "SELL" (long liq)
    price: float
    qty: float
    usdt: float
    ts: float


class LiquidationTracker:
    """Collect liquidation events and bind them to levels."""

    def __init__(self):
        # symbol -> list of recent LiqEvents
        self.events: dict[str, list[LiqEvent]] = defaultdict(list)

    def add_event(self, data: dict) -> None:
        """Parse Binance forceOrder payload and store."""
        o = data.get("o", {})
        symbol = o.get("s", "").lower()
        side = o.get("S", "")        # BUY = short liquidated, SELL = long liquidated
        price = float(o.get("ap", 0))  # average price
        qty = float(o.get("q", 0))
        usdt = price * qty
        ev = LiqEvent(symbol=symbol, side=side, price=price, qty=qty, usdt=usdt, ts=time.time())
        self.events[symbol].append(ev)

    def cleanup(self, symbol: str) -> None:
        """Remove old events outside rolling window."""
        cutoff = time.time() - LIQ_WINDOW_SEC
        self.events[symbol] = [e for e in self.events[symbol] if e.ts >= cutoff]

    def bind_to_levels(self, symbol: str, levels: List[Level]) -> None:
        """Sum liquidations near each level and update liq fields."""
        self.cleanup(symbol)
        events = self.events.get(symbol, [])
        for lvl in levels:
            lvl.liq_long_usdt = 0.0
            lvl.liq_short_usdt = 0.0
            for ev in events:
                dist = abs(ev.price - lvl.price) / max(lvl.price, 1e-9)
                if dist <= LEVEL_TOUCH_PCT * 3:  # wider zone for liqs
                    if ev.side == "SELL":  # long liquidated
                        lvl.liq_long_usdt += ev.usdt
                    else:  # short liquidated
                        lvl.liq_short_usdt += ev.usdt
