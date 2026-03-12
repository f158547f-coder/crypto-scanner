from __future__ import annotations
import asyncio
import json
import time
import websockets
from collections import defaultdict

from orderbook import OrderBook
from levels import build_levels, Level, calc_strength
from liquidations import LiquidationTracker
from context_candles import CandleContext
from signals import process_levels
from config import BINANCE_WS_URL, SYMBOLS, CANDLE_FETCH_INTERVAL, PRINT_DEBUG, LEVEL_MERGE_PCT
from telegram_bot import send_telegram


def _merge_state(old_levels: list[Level], new_levels: list[Level]) -> list[Level]:
    """Transfer state/cooldown from old levels to matching new levels."""
    for nl in new_levels:
        for ol in old_levels:
            if ol.side == nl.side and abs(ol.price - nl.price) / max(nl.price, 1e-9) <= LEVEL_MERGE_PCT * 3:
                nl.state = ol.state
                nl.last_signal_ts = ol.last_signal_ts
                nl.liq_long_usdt = max(nl.liq_long_usdt, ol.liq_long_usdt)
                nl.liq_short_usdt = max(nl.liq_short_usdt, ol.liq_short_usdt)
                break
    return new_levels


class MarketScanner:
    def __init__(self):
        self.orderbooks: dict[str, OrderBook] = {s: OrderBook() for s in SYMBOLS}
        self.last_price: dict[str, float] = {}
        self.levels_by_sym: dict[str, list[Level]] = {s: [] for s in SYMBOLS}
        self.liq_tracker = LiquidationTracker()
        self.candle_ctx = CandleContext()
        self._depth_count: dict[str, int] = defaultdict(int)
        self._last_check: dict[str, float] = defaultdict(float)

    async def run(self):
        await send_telegram("Scanner started. Monitoring: " + ", ".join(s.upper() for s in SYMBOLS))
        await asyncio.gather(
            self._ws_loop(),
            self._candle_refresh_loop(),
            self._level_check_loop(),
        )

    async def _ws_loop(self):
        streams = [f"{s}@depth20@100ms" for s in SYMBOLS]
        streams.append("!forceOrder@arr")
        url = f"{BINANCE_WS_URL}?streams={'/'.join(streams)}"
        while True:
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=60) as ws:
                    if PRINT_DEBUG:
                        print(f"[WS] connected, {len(SYMBOLS)} symbols")
                    async for raw in ws:
                        data = json.loads(raw)
                        stream = data.get("stream", "")
                        payload = data.get("data", {})
                        if "@depth" in stream:
                            self._on_depth(stream, payload)
                        elif "forceOrder" in stream:
                            self._on_liquidation(payload)
            except Exception as e:
                if PRINT_DEBUG:
                    print(f"[WS] error: {e}, reconnecting in 5s")
                await asyncio.sleep(5)

    def _on_depth(self, stream: str, data: dict):
        sym = stream.split("@")[0]
        ob = self.orderbooks.get(sym)
        if not ob:
            return
        ob.update_depth(data.get("b", []), data.get("a", []))
        mid = ob.mid_price()
        if mid:
            self.last_price[sym] = mid

    def _on_liquidation(self, data):
        if isinstance(data, list):
            for item in data:
                self.liq_tracker.add_event(item)
        else:
            self.liq_tracker.add_event(data)

    async def _level_check_loop(self):
        """Check levels every 15 seconds per symbol - prevents spam."""
        while True:
            for sym in SYMBOLS:
                price = self.last_price.get(sym)
                if not price:
                    continue
                try:
                    # Rebuild levels from orderbook
                    ob = self.orderbooks[sym]
                    new_levels = build_levels(sym, ob)
                    self.liq_tracker.bind_to_levels(sym, new_levels)
                    # Preserve state from old levels
                    old_levels = self.levels_by_sym[sym]
                    if old_levels:
                        new_levels = _merge_state(old_levels, new_levels)
                    self.levels_by_sym[sym] = new_levels
                    # Process signals
                    await process_levels(sym, price, new_levels, self.candle_ctx)
                except Exception as e:
                    if PRINT_DEBUG:
                        print(f"[CHECK] error {sym}: {e}")
                await asyncio.sleep(0.5)
            # Wait 15 seconds before next full cycle
            await asyncio.sleep(15)

    async def _candle_refresh_loop(self):
        """Periodically refresh candle data for all symbols."""
        while True:
            for sym in SYMBOLS:
                try:
                    await self.candle_ctx.refresh(sym)
                except Exception as e:
                    if PRINT_DEBUG:
                        print(f"[CANDLE] refresh error {sym}: {e}")
                await asyncio.sleep(0.5)
            await asyncio.sleep(CANDLE_FETCH_INTERVAL)
