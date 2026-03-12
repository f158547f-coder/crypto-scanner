"""Market scanner: deep orderbook levels + WS price/liquidations.

Architecture:
- REST: fetch_deep_orderbook every 30s per symbol (5000 levels)
- WS: aggTrade for real-time price + forceOrder for liquidations
- Levels built from deep orderbook using anomaly detection
- Signals checked every 15s against levels
"""
from __future__ import annotations
import asyncio
import json
import time
import websockets
from collections import defaultdict

from orderbook import OrderBook, fetch_deep_orderbook
from levels import build_levels, Level, calc_strength
from liquidations import LiquidationTracker
from context_candles import CandleContext
from signals import process_levels
from tc_signals import check_tc_signals
from config import (BINANCE_WS_URL, SYMBOLS, CANDLE_FETCH_INTERVAL,
                    PRINT_DEBUG, LEVEL_MERGE_PCT)
from telegram_bot import send_telegram
from proxy_pool import proxy_pool

DEEP_OB_INTERVAL = 30


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
        self.deep_bids: dict[str, dict] = {s: {} for s in SYMBOLS}
        self.deep_asks: dict[str, dict] = {s: {} for s in SYMBOLS}
        self.liq_tracker = LiquidationTracker()
        self.candle_ctx = CandleContext()

    async def run(self):
        await proxy_pool.refresh()
        await send_telegram("Scanner started. Monitoring: " + ", ".join(s.upper() for s in SYMBOLS))
        await asyncio.gather(
            self._ws_loop(),
            self._deep_orderbook_loop(),
            self._candle_refresh_loop(),
            self._level_check_loop(),
        )

    async def _ws_loop(self):
        """WebSocket for real-time price (aggTrade) and liquidations."""
        streams = [f"{s}@aggTrade" for s in SYMBOLS]
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
                        if "@aggTrade" in stream:
                            sym = stream.split("@")[0]
                            price = float(payload.get("p", 0))
                            if price > 0:
                                self.last_price[sym] = price
                        elif "forceOrder" in stream:
                            self._on_liquidation(payload)
            except Exception as e:
                if PRINT_DEBUG:
                    print(f"[WS] error: {e}, reconnecting in 5s")
                await asyncio.sleep(5)

    def _on_liquidation(self, data):
        if isinstance(data, list):
            for item in data:
                self.liq_tracker.add_event(item)
        else:
            self.liq_tracker.add_event(data)

    async def _deep_orderbook_loop(self):
        """Periodically fetch full depth orderbook via REST for all symbols."""
        while True:
            for sym in SYMBOLS:
                try:
                    bids, asks = await fetch_deep_orderbook(sym)
                    if bids and asks:
                        self.deep_bids[sym] = bids
                        self.deep_asks[sym] = asks
                except Exception as e:
                    if PRINT_DEBUG:
                        print(f"[DEEP_OB] error {sym}: {e}")
                await asyncio.sleep(1)
            await asyncio.sleep(DEEP_OB_INTERVAL)

    async def _level_check_loop(self):
        """Check levels every 15 seconds per symbol."""
        await asyncio.sleep(10)
        while True:
            for sym in SYMBOLS:
                price = self.last_price.get(sym)
                if not price:
                    continue
                bids = self.deep_bids.get(sym, {})
                asks = self.deep_asks.get(sym, {})
                if not bids and not asks:
                    continue
                try:
                    new_levels = build_levels(sym, bids, asks, price)
                    self.liq_tracker.bind_to_levels(sym, new_levels)
                    old_levels = self.levels_by_sym[sym]
                    if old_levels:
                        new_levels = _merge_state(old_levels, new_levels)
                    self.levels_by_sym[sym] = new_levels
                    await process_levels(sym, price, new_levels, self.candle_ctx)
                                      await check_tc_signals(sym, price, bids, asks)
                except Exception as e:
                    if PRINT_DEBUG:
                        print(f"[CHECK] error {sym}: {e}")
                await asyncio.sleep(0.5)
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
