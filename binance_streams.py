from __future__ import annotations
import asyncio
import json
import time
import websockets
from collections import defaultdict

from orderbook import OrderBook
from levels import build_levels, Level
from liquidations import LiquidationTracker
from context_candles import CandleContext
from signals import process_levels
from config import BINANCE_WS_URL, SYMBOLS, CANDLE_FETCH_INTERVAL, PRINT_DEBUG
from telegram_bot import send_telegram


class MarketScanner:
    def __init__(self):
        self.orderbooks: dict[str, OrderBook] = {s: OrderBook() for s in SYMBOLS}
        self.last_price: dict[str, float] = {}
        self.levels_by_sym: dict[str, list[Level]] = {s: [] for s in SYMBOLS}
        self.liq_tracker = LiquidationTracker()
        self.candle_ctx = CandleContext()
        self._depth_count = 0

    async def run(self):
        await send_telegram("Scanner started. Monitoring: " + ", ".join(s.upper() for s in SYMBOLS))
        await asyncio.gather(
            self._ws_loop(),
            self._candle_refresh_loop(),
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
                            await self._on_depth(stream, payload)
                        elif "forceOrder" in stream:
                            self._on_liquidation(payload)
            except Exception as e:
                if PRINT_DEBUG:
                    print(f"[WS] error: {e}, reconnecting in 5s")
                await asyncio.sleep(5)

    async def _on_depth(self, stream: str, data: dict):
        sym = stream.split("@")[0]
        ob = self.orderbooks.get(sym)
        if not ob:
            return
        ob.update_depth(data.get("b", []), data.get("a", []))
        mid = ob.mid_price()
        if mid:
            self.last_price[sym] = mid

        self._depth_count += 1
        if self._depth_count % 50 == 0:  # throttle level rebuild
            await self._rebuild_and_check(sym)

    def _on_liquidation(self, data):
        if isinstance(data, list):
            for item in data:
                self.liq_tracker.add_event(item)
        else:
            self.liq_tracker.add_event(data)

    async def _rebuild_and_check(self, sym: str):
        price = self.last_price.get(sym)
        if not price:
            return
        ob = self.orderbooks[sym]
        levels = build_levels(sym, ob)
        self.liq_tracker.bind_to_levels(sym, levels)
        self.levels_by_sym[sym] = levels
        await process_levels(sym, price, levels, self.candle_ctx)

    async def _candle_refresh_loop(self):
        """Periodically refresh candle data for all symbols."""
        while True:
            for sym in SYMBOLS:
                try:
                    await self.candle_ctx.refresh(sym)
                except Exception as e:
                    if PRINT_DEBUG:
                        print(f"[CANDLE] refresh error {sym}: {e}")
                await asyncio.sleep(0.5)  # rate limit
            await asyncio.sleep(CANDLE_FETCH_INTERVAL)
