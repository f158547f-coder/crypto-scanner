import httpx
from config import TG_BOT_TOKEN, TG_CHAT_ID, PRINT_DEBUG

TG_API_URL = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"


async def send_telegram(text: str) -> None:
    """Send a message to Telegram chat/channel."""
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        if PRINT_DEBUG:
            print("[TG] token/chat not set:", text[:120])
        return
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.post(
                TG_API_URL,
                json={"chat_id": TG_CHAT_ID, "text": text, "parse_mode": "Markdown"},
            )
            if PRINT_DEBUG:
                print(f"[TG] {resp.status_code}")
        except Exception as e:
            if PRINT_DEBUG:
                print(f"[TG] error: {e}")


async def send_approaching(symbol: str, side: str, level_price: float,
                           volume_usdt: float, dist_pct: float, strength: float) -> None:
    direction = "SUPPORT" if side == "bid" else "RESISTANCE"
    text = (
        f"\u26a0\ufe0f *{symbol.upper()}* approaching {direction}\n"
        f"Level: `{level_price:.4f}`\n"
        f"Volume: `{volume_usdt:,.0f}` USDT\n"
        f"Distance: `{dist_pct*100:.2f}%`\n"
        f"Strength: `{strength:.2f}`"
    )
    await send_telegram(text)


async def send_signal(symbol: str, direction: str, entry: float,
                      level_price: float, stop_loss: float,
                      tp1: float, tp2: float, reason: str,
                      risk_pct: float, leverage: int,
                      whale_summary: str = "") -> None:
    """Send a LONG/SHORT signal with SL/TP."""
    risk_on_balance = risk_pct * leverage * 100
    whale_line = f"\nWhalePortal: {whale_summary}" if whale_summary else ""
    text = (
        f"\U0001f7e2 *{symbol.upper()}* -- *{direction}*\n"
        f"Entry: `{entry:.4f}`\n"
        f"Level: `{level_price:.4f}`\n"
        f"Stop: `{stop_loss:.4f}` (~{risk_pct*100:.2f}%)\n"
        f"TP1: `{tp1:.4f}`\n"
        f"TP2: `{tp2:.4f}`\n"
        f"Leverage: `{leverage}x` (risk ~{risk_on_balance:.1f}% balance)\n"
        f"Reason: {reason}{whale_line}"
    )
    await send_telegram(text)


async def send_tc_signal(symbol: str, direction: str, entry: float,
                         stop_loss: float, tp1: float, tp2: float,
                         wall_price: float, wall_type: str, wall_usdt: float,
                         pattern: str, timeframe: str, vol_ratio: float,
                         risk_pct: float, leverage: int,
                         whale_summary: str = "") -> None:
    """Send TensorCharts-style signal."""
    whale_line = f"\nWhalePortal: {whale_summary}" if whale_summary else ""
    text = (
        f"\U0001f4ca *[TensorCharts]* *{symbol.upper()}* -- *{direction}*\n"
        f"Entry: `{entry:.4f}`\n"
        f"Wall: `{wall_price:.4f}` ({wall_type}, ${wall_usdt/1000:.0f}k)\n"
        f"Pattern: `{pattern}` on `{timeframe}` (vol {vol_ratio:.1f}x)\n"
        f"Stop: `{stop_loss:.4f}` (~{risk_pct*100:.2f}%)\n"
        f"TP1: `{tp1:.4f}`\n"
        f"TP2: `{tp2:.4f}`\n"
        f"Leverage: `{leverage}x`{whale_line}"
    )
    await send_telegram(text)
