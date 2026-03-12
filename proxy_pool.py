"""Free proxy pool with auto-refresh and validation.

Fetches HTTPS proxies from multiple free sources,
validates them against Binance fapi, and rotates automatically.
"""
from __future__ import annotations
import asyncio
import time
import random
import httpx
from config import PRINT_DEBUG

# Free proxy list sources (return plain text ip:port)
PROXY_SOURCES = [
    "https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/http.txt",
    "https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/https.txt",
    "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/https.txt",
    "https://raw.githubusercontent.com/hookzof/socks5_list/master/proxy.txt",
    "https://raw.githubusercontent.com/clarketm/proxy-list/master/proxy-list-raw.txt",
]

# How often to refresh the proxy list (seconds)
REFRESH_INTERVAL = 600  # 10 min
VALIDATE_TIMEOUT = 8
MAX_WORKING = 10  # keep top N working proxies


class ProxyPool:
    def __init__(self):
        self._working: list[str] = []  # validated proxy strings "http://ip:port"
        self._last_refresh: float = 0
        self._lock = asyncio.Lock()

    async def _fetch_raw_proxies(self) -> list[str]:
        """Download proxy lists from free sources."""
        raw = set()
        async with httpx.AsyncClient(timeout=10) as client:
            for src in PROXY_SOURCES:
                try:
                    resp = await client.get(src)
                    if resp.status_code == 200:
                        for line in resp.text.strip().splitlines():
                            line = line.strip()
                            if line and ":" in line:
                                # keep only ip:port
                                parts = line.split()
                                raw.add(parts[0])
                except Exception:
                    pass
        if PRINT_DEBUG:
            print(f"[PROXY] fetched {len(raw)} raw proxies from {len(PROXY_SOURCES)} sources")
        return list(raw)

    async def _validate_proxy(self, proxy_str: str) -> bool:
        """Check if proxy can reach Binance fapi."""
        proxy_url = f"http://{proxy_str}"
        try:
            async with httpx.AsyncClient(
                proxy=proxy_url,
                timeout=VALIDATE_TIMEOUT,
            ) as client:
                resp = await client.get(
                    "https://fapi.binance.com/fapi/v1/time"
                )
                data = resp.json()
                return "serverTime" in data
        except Exception:
            return False

    async def refresh(self) -> None:
        """Refresh and validate proxy list."""
        async with self._lock:
            now = time.time()
            if now - self._last_refresh < REFRESH_INTERVAL and self._working:
                return
            if PRINT_DEBUG:
                print("[PROXY] refreshing proxy pool...")
            raw = await self._fetch_raw_proxies()
            random.shuffle(raw)
            # Test proxies in batches
            validated = []
            batch_size = 30
            for i in range(0, min(len(raw), 150), batch_size):
                batch = raw[i:i + batch_size]
                tasks = [self._validate_proxy(p) for p in batch]
                results = await asyncio.gather(*tasks)
                for p, ok in zip(batch, results):
                    if ok:
                        validated.append(f"http://{p}")
                        if PRINT_DEBUG:
                            print(f"[PROXY] valid: {p}")
                if len(validated) >= MAX_WORKING:
                    break
            self._working = validated[:MAX_WORKING]
            self._last_refresh = now
            if PRINT_DEBUG:
                print(f"[PROXY] pool ready: {len(self._working)} working proxies")

    async def get(self) -> str | None:
        """Get a random working proxy URL, or None."""
        if not self._working:
            await self.refresh()
        if self._working:
            return random.choice(self._working)
        return None

    def remove(self, proxy_url: str) -> None:
        """Remove a dead proxy from the pool."""
        if proxy_url in self._working:
            self._working.remove(proxy_url)
            if PRINT_DEBUG:
                print(f"[PROXY] removed dead proxy: {proxy_url}")

    @property
    def size(self) -> int:
        return len(self._working)


# Singleton
proxy_pool = ProxyPool()
