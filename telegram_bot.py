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
    """Send 'approaching level' alert."""
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
                      risk_pct: float, leverage: int) -> None:
    """Send a LONG/SHORT signal with SL/TP."""
    risk_on_balance = risk_pct * leverage * 100
    text = (
        f"\U0001f7e2 *{symbol.upper()}* — *{direction}*\n"
        f"Entry: `{entry:.4f}`\n"
        f"Level: `{level_price:.4f}`\n"
        f"Stop: `{stop_loss:.4f}` (~{risk_pct*100:.2f}%)\n"
        f"TP1: `{tp1:.4f}` (RR 1:{int(risk_pct and (abs(tp1-entry)/abs(entry-stop_loss)) or 0)})\n"
        f"TP2: `{tp2:.4f}` (RR 1:{int(risk_pct and (abs(tp2-entry)/abs(entry-stop_loss)) or 0)})\n"
        f"Leverage: `{leverage}x` (risk ~{risk_on_balance:.1f}% balance)\n"
        f"Reason: {reason}"
    )
    await send_telegram(text)
