"""AI signal filter with multi-provider fallback.

Provider priority: Gemini -> Groq -> OpenRouter.
If primary fails, automatically tries next provider.
"""
from __future__ import annotations
import json
import os
import httpx
from config import PRINT_DEBUG

# --- API Keys ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")

# --- Endpoints ---
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

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
  "direction": "LONG/SHORT",
  "entry": 0.0,
  "stop_loss": 0.0,
  "tp1": 0.0,
  "tp2": 0.0
}

If corrected=true, fill in corrected values. If corrected=false, values are ignored.
Respond with JSON only."""


def _parse_ai_response(text: str, symbol: str, direction: str) -> dict | None:
    """Parse JSON from AI response text, stripping markdown if present."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()
    try:
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


async def _call_gemini(prompt: str, symbol: str, direction: str) -> dict | None:
    """Call Google Gemini API."""
    if not GEMINI_API_KEY:
        return None
    payload = {
        "contents": [{"parts": [{"text": SYSTEM_PROMPT + "\n\n" + prompt}]}],
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": 500},
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(f"{GEMINI_URL}?key={GEMINI_API_KEY}", json=payload)
            data = resp.json()
            candidates = data.get("candidates", [])
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                if parts:
                    text = parts[0].get("text", "")
                    if text:
                        return _parse_ai_response(text, symbol, direction)
            if PRINT_DEBUG:
                print(f"[AI:Gemini] empty response: {str(data)[:200]}")
    except Exception as e:
        if PRINT_DEBUG:
            print(f"[AI:Gemini] error: {e}")
    return None


async def _call_groq(prompt: str, symbol: str, direction: str) -> dict | None:
    """Call Groq API (OpenAI-compatible)."""
    if not GROQ_API_KEY:
        return None
    payload = {
        "model": "llama-3.1-70b-versatile",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "max_tokens": 500,
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                GROQ_URL,
                json=payload,
                headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
            )
            data = resp.json()
            choices = data.get("choices", [])
            if choices:
                text = choices[0].get("message", {}).get("content", "")
                if text:
                    return _parse_ai_response(text, symbol, direction)
            if PRINT_DEBUG:
                print(f"[AI:Groq] empty response: {str(data)[:200]}")
    except Exception as e:
        if PRINT_DEBUG:
            print(f"[AI:Groq] error: {e}")
    return None


async def _call_openrouter(prompt: str, symbol: str, direction: str) -> dict | None:
    """Call OpenRouter API (OpenAI-compatible)."""
    if not OPENROUTER_API_KEY:
        return None
    payload = {
        "model": "meta-llama/llama-3.1-70b-instruct",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "max_tokens": 500,
    }
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                OPENROUTER_URL,
                json=payload,
                headers={
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                    "HTTP-Referer": "https://github.com/f158547f-coder/crypto-scanner",
                    "X-Title": "CryptoScanner",
                },
            )
            data = resp.json()
            choices = data.get("choices", [])
            if choices:
                text = choices[0].get("message", {}).get("content", "")
                if text:
                    return _parse_ai_response(text, symbol, direction)
            if PRINT_DEBUG:
                print(f"[AI:OpenRouter] empty response: {str(data)[:200]}")
    except Exception as e:
        if PRINT_DEBUG:
            print(f"[AI:OpenRouter] error: {e}")
    return None


# Provider chain: try in order, stop on first success
_PROVIDERS = [
    ("Gemini", _call_gemini),
    ("Groq", _call_groq),
    ("OpenRouter", _call_openrouter),
]


async def _call_ai(prompt: str, symbol: str, direction: str) -> dict | None:
    """Try all AI providers in order until one succeeds."""
    for name, fn in _PROVIDERS:
        result = await fn(prompt, symbol, direction)
        if result is not None:
            if PRINT_DEBUG:
                print(f"[AI] used provider: {name}")
            return result
    if PRINT_DEBUG:
        print(f"[AI] all providers failed for {symbol} {direction}")
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
    if not (GEMINI_API_KEY or GROQ_API_KEY or OPENROUTER_API_KEY):
        if PRINT_DEBUG:
            print("[AI] No API keys set, skipping filter")
        return {"approved": True, "reason": "no AI keys", "corrected": False}

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

    return await _call_ai(prompt, symbol, direction)
