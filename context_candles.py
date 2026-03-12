from __future__ import annotations
import httpx
from collections import defaultdict
from config import (BINANCE_REST_URL, CANDLE_TF_ENTRY, CANDLE_TF_TREND,
                    EMA_FAST, EMA_SLOW, ATR_PERIOD, PRINT_DEBUG)


def _ema(values: list[float], period: int) -> float:
    """Simple EMA over the last `period` values."""
    if not values:
        return 0.0
    k = 2 / (period + 1)
    ema = values[0]
    for v in values[1:]:
        ema = v * k + ema * (1 - k)
    return ema


def _atr(candles: list[dict], period: int) -> float:
    """Average True Range."""
    trs = []
    for i, c in enumerate(candles):
        h, l, pc = c["h"], c["l"], candles[i-1]["c"] if i > 0 else c["o"]
        tr = max(h - l, abs(h - pc), abs(l - pc))
        trs.append(tr)
    if len(trs) < period:
        return sum(trs) / max(len(trs), 1)
    return sum(trs[-period:]) / period


async def fetch_candles(symbol: str, interval: str, limit: int = 100) -> list[dict]:
    """Fetch klines from Binance REST."""
    url = f"{BINANCE_REST_URL}/fapi/v1/klines"
    params = {"symbol": symbol.upper(), "interval": interval, "limit": limit}
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.get(url, params=params)
            data = resp.json()
            if not isinstance(data, list):
                if PRINT_DEBUG:
                    print(f"[CANDLES] {symbol} {interval}: unexpected response: {str(data)[:100]}")
                return []
            candles = []
            for k in data:
                if not isinstance(k, (list, tuple)) or len(k) < 6:
                    continue
                candles.append({
                    "ts": k[0], "o": float(k[1]), "h": float(k[2]),
                    "l": float(k[3]), "c": float(k[4]), "v": float(k[5]),
                })
            return candles
        except Exception as e:
            if PRINT_DEBUG:
                print(f"[CANDLES] error {symbol} {interval}: {e}")
            return []


class CandleContext:
    """Holds candle data + computed indicators per symbol."""

    def __init__(self):
        self.entry_candles: dict[str, list[dict]] = defaultdict(list)
        self.trend_candles: dict[str, list[dict]] = defaultdict(list)
        self.ema_fast: dict[str, float] = {}
        self.ema_slow: dict[str, float] = {}
        self.atr: dict[str, float] = {}

    async def refresh(self, symbol: str) -> None:
        """Fetch fresh candles and recompute indicators."""
        entry = await fetch_candles(symbol, CANDLE_TF_ENTRY, 60)
        trend = await fetch_candles(symbol, CANDLE_TF_TREND, 60)
        if entry:
            self.entry_candles[symbol] = entry
        if trend:
            self.trend_candles[symbol] = trend
            closes = [c["c"] for c in trend]
            self.ema_fast[symbol] = _ema(closes, EMA_FAST)
            self.ema_slow[symbol] = _ema(closes, EMA_SLOW)
            self.atr[symbol] = _atr(trend, ATR_PERIOD)

    def last_entry_candle(self, symbol: str) -> dict | None:
        candles = self.entry_candles.get(symbol, [])
        return candles[-1] if candles else None

    def prev_entry_candles(self, symbol: str, n: int = 5) -> list[dict]:
        candles = self.entry_candles.get(symbol, [])
        return candles[-(n+1):-1] if len(candles) > n else candles[:-1]
