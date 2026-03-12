"""AI signal filter using Google Gemini.

Sends signal context to Gemini for validation.
Gemini can approve, reject, or correct the signal (adjust SL/TP/direction).
"""
from __future__ import annotations
import json
import os
import httpx
from config import PRINT_DEBUG

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"

SYSTEM_PROMPT = """You are a professional crypto futures trading analyst AI.
You receive a trading signal with market context and must validate it.

Your job:
1. Analyze the signal quality based on provided data
2. Return a JSON decision

Rules:
- If the signal looks valid, approve it (possibly with corrections)
- If the signal is clearly wrong (bad direction, terrible R:R, against major trend), reject it
- You can correct: entry, stop_loss, tp1, tp2, direction
- Be conservative - only approve signals with good risk/reward
- Consider the trend (EMA fast vs slow), volatility (ATR), level strength, volume anomaly
- For 20x leverage, stop loss must be tight but not too tight (avoid liquidation from noise)

Always respond with ONLY valid JSON (no markdown, no explanation):
{
  "approved": true/false,
  "reason": "brief explanation",
  "corrected": false,
  "direction": "LONG" or "SHORT",
  "entry": float,
  "stop_loss": float,
  "tp1": float,
  "tp2": float
}

If corrected=true, use your corrected values for entry/sl/tp.
If corrected=false, the original values will be used.
"""


async def ai_validate_signal(
    symbol: str,
    direction: str,
    entry: float,
    level_price: float,
    stop_loss: float,
    tp1: float,
    tp2: float,
    level_volume: float,
    level_strength: float,
    ema_fast: float,
    ema_slow: float,
    atr: float,
    liq_long: float,
    liq_short: float,
    candle_pattern: str,
) -> dict | None:
    """Send signal to Gemini for AI validation.

    Returns dict with keys: approved, reason, corrected, direction, entry, stop_loss, tp1, tp2
    Returns None on API error (signal proceeds without AI filter).
    """
    if not GEMINI_API_KEY:
        if PRINT_DEBUG:
            print("[AI] no GEMINI_API_KEY, skipping filter")
        return None

    prompt = f"""Validate this crypto futures signal:

Symbol: {symbol.upper()}
Direction: {direction}
Entry: {entry:.6f}
Level price: {level_price:.6f}
Stop loss: {stop_loss:.6f}
TP1: {tp1:.6f}
TP2: {tp2:.6f}
Leverage: 20x

Market context:
- Level volume (USDT): {level_volume:,.0f}
- Level strength score: {level_strength:.2f}
- EMA fast: {ema_fast:.6f}
- EMA slow: {ema_slow:.6f}
- ATR: {atr:.6f}
- Liquidation long nearby: {liq_long:,.0f} USDT
- Liquidation short nearby: {liq_short:,.0f} USDT
- Candle pattern: {candle_pattern}
- Trend: {'BULLISH' if ema_fast > ema_slow else 'BEARISH' if ema_slow > ema_fast else 'NEUTRAL'}

Risk analysis:
- Risk per trade at 20x: {abs(entry - stop_loss) / entry * 20 * 100:.1f}% of margin
- R:R to TP1: {abs(tp1 - entry) / max(abs(entry - stop_loss), 1e-9):.2f}
- R:R to TP2: {abs(tp2 - entry) / max(abs(entry - stop_loss), 1e-9):.2f}

Respond with JSON only."""

    payload = {
        "contents": [{
            "parts": [{"text": SYSTEM_PROMPT + "\n\n" + prompt}]
        }],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 500,
        }
    }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{GEMINI_URL}?key={GEMINI_API_KEY}",
                json=payload,
            )
            data = resp.json()

            # Extract text from Gemini response
            text = ""
            candidates = data.get("candidates", [])
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                if parts:
                    text = parts[0].get("text", "")

            if not text:
                if PRINT_DEBUG:
                    print(f"[AI] empty response: {str(data)[:200]}")
                return None

            # Parse JSON from response (strip markdown if present)
            text = text.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()

            result = json.loads(text)

            if PRINT_DEBUG:
                approved = result.get("approved", False)
                corrected = result.get("corrected", False)
                reason = result.get("reason", "")
                print(f"[AI] {symbol} {direction}: "
                      f"{'APPROVED' if approved else 'REJECTED'}"
                      f"{' (corrected)' if corrected else ''} - {reason}")

            return result

    except json.JSONDecodeError as e:
        if PRINT_DEBUG:
            print(f"[AI] JSON parse error: {e}, text: {text[:200]}")
        return None
    except Exception as e:
        if PRINT_DEBUG:
            print(f"[AI] error: {e}")
        return None

  
async def filter_signal(
    symbol: str,
    direction: str,
    entry: float,
    stop_loss: float,
    tp1: float,
    tp2: float,
    level_price: float,
    level_strength: float,
    volume_anomaly: float,
    liquidation_cluster: float,
    ema_fast: float,
    ema_slow: float,
    atr: float,
    current_price: float,
) -> dict | None:
    """Filter a trading signal through AI validation.
    
    Returns dict with approved/corrected fields, or None on error.
    """
    if not GEMINI_API_KEY:
        if PRINT_DEBUG:
            print("[AI] No GEMINI_API_KEY, skipping filter")
        return {"approved": True, "reason": "no AI key", "corrected": False}

    trend = "BULLISH" if ema_fast > ema_slow else "BEARISH"
    risk_reward = abs(tp1 - entry) / abs(entry - stop_loss) if abs(entry - stop_loss) > 0 else 0

    prompt = f"""Signal to validate:
Symbol: {symbol}
Direction: {direction}
Entry: {entry}
Stop Loss: {stop_loss}
TP1: {tp1}
TP2: {tp2}
Risk/Reward: {risk_reward:.2f}

Market context:
Current Price: {current_price}
Level Price: {level_price} (strength: {level_strength:.1f})
Volume Anomaly: {volume_anomaly:.2f}x
Liquidation Cluster: {liquidation_cluster:.1f}
EMA trend: {trend} (fast={ema_fast:.2f}, slow={ema_slow:.2f})
ATR: {atr:.4f}
Leverage: 20x

Respond with JSON only."""

    return await _call_gemini(prompt, symbol, direction)
