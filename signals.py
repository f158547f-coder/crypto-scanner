from __future__ import annotations
import time
from typing import List
from levels import Level, calc_strength
from patterns import check_entry_filters
from ai_filter import filter_signal
from whale_filter import check_whale_filter
from context_candles import CandleContext
from telegram_bot import send_approaching, send_signal
from config import (
    LEVEL_NEAR_PCT, LEVEL_TOUCH_PCT, LEVEL_FAR_PCT,
    LEVEL_MIN_STRENGTH, LEVEL_COOLDOWN_SEC,
    LIQ_MIN_USDT, USE_LIQUIDATION_FILTER,
    STOP_LOSS_PCT, TP1_RR, TP2_RR, LEVERAGE, PRINT_DEBUG,
    USE_AI_FILTER,
)

# Global cooldown tracker: {(symbol, side, rounded_price): last_alert_ts}
_alert_cooldowns: dict[tuple, float] = {}
_signal_cooldowns: dict[tuple, float] = {}

ALERT_COOLDOWN = 600   # 10 min between approaching alerts for same level
SIGNAL_COOLDOWN = 1800  # 30 min between signals for same level


def _level_key(symbol: str, lvl: Level) -> tuple:
    """Create a unique key for a level based on symbol, side, and rounded price."""
    # Round price to reduce noise - group nearby prices
    precision = max(lvl.price * 0.002, 1e-8)  # 0.2% bucket
    rounded = round(lvl.price / precision) * precision
    return (symbol, lvl.side, rounded)


async def process_levels(symbol: str, price: float, levels: List[Level],
                         ctx: CandleContext) -> None:
    """Check each level state and send alerts/signals."""
    now = time.time()

    for lvl in levels:
        dist = abs(price - lvl.price) / max(price, 1e-9)
        calc_strength(lvl)
        key = _level_key(symbol, lvl)

        # Reset far away levels
        if dist > LEVEL_FAR_PCT:
            if lvl.state != "FAR":
                lvl.state = "FAR"
            continue

        # Skip already signalled levels (until they go FAR and come back)
        if lvl.state == "SIGNALLED":
            continue

        # FAR -> NEAR (approaching alert)
        if lvl.state == "FAR" and dist <= LEVEL_NEAR_PCT:
            if lvl.strength >= LEVEL_MIN_STRENGTH:
                # Check alert cooldown
                last_alert = _alert_cooldowns.get(key, 0)
                if now - last_alert >= ALERT_COOLDOWN:
                    lvl.state = "NEAR"
                    _alert_cooldowns[key] = now
                    await send_approaching(
                        symbol, lvl.side, lvl.price, lvl.volume_usdt,
                        dist, lvl.strength,
                    )
                    if PRINT_DEBUG:
                        print(f"[NEAR] {symbol} {lvl.side} {lvl.price:.4f}")
                else:
                    lvl.state = "NEAR"  # Update state but don't alert

        # NEAR -> TOUCHED
        if lvl.state == "NEAR" and dist <= LEVEL_TOUCH_PCT:
            lvl.state = "TOUCHED"
            if PRINT_DEBUG:
                print(f"[TOUCH] {symbol} {lvl.side} {lvl.price:.4f}")

        # TOUCHED -> try signal
        if lvl.state == "TOUCHED":
            # Check signal cooldown
            last_sig = _signal_cooldowns.get(key, 0)
            if now - last_sig < SIGNAL_COOLDOWN:
                lvl.state = "SIGNALLED"  # Block further attempts
                continue

            direction = "LONG" if lvl.side == "bid" else "SHORT"

            # Liquidation filter
            if USE_LIQUIDATION_FILTER:
                if direction == "LONG" and lvl.liq_short_usdt < LIQ_MIN_USDT:
                    if PRINT_DEBUG:
                        print(f"[FILTER] {symbol} {direction} no liq support")
                    lvl.state = "SIGNALLED"  # Don't retry
                    continue
                if direction == "SHORT" and lvl.liq_long_usdt < LIQ_MIN_USDT:
                    if PRINT_DEBUG:
                        print(f"[FILTER] {symbol} {direction} no liq support")
                    lvl.state = "SIGNALLED"
                    continue

            # Candle / trend / volatility filters
            passed, reason = check_entry_filters(symbol, direction, lvl.price, ctx)
            if not passed:
                if PRINT_DEBUG:
                    print(f"[FILTER] {symbol} {direction} rejected: {reason}")
                lvl.state = "SIGNALLED"  # Don't retry same level
                continue

            # Calculate SL / TP
            entry = price
            if direction == "LONG":
                stop_loss = lvl.price * (1 - STOP_LOSS_PCT)
                risk = entry - stop_loss
                tp1 = entry + risk * TP1_RR
                tp2 = entry + risk * TP2_RR
            else:
                stop_loss = lvl.price * (1 + STOP_LOSS_PCT)
                risk = stop_loss - entry
                tp1 = entry - risk * TP1_RR
                tp2 = entry - risk * TP2_RR

            risk_pct = abs(entry - stop_loss) / max(entry, 1e-9)

                        # AI filter (optional)
            if USE_AI_FILTER:
                ai_result = await filter_signal(
                    symbol=symbol, direction=direction,
                    entry=entry, stop_loss=stop_loss,
                    tp1=tp1, tp2=tp2,
                    level_price=lvl.price,
                    level_strength=lvl.strength,
                    volume_anomaly=ctx.volume_ratio if ctx else 1.0,
                    liquidation_cluster=lvl.liq_short_usdt + lvl.liq_long_usdt,
                    ema_fast=ctx.ema_fast if ctx else 0,
                    ema_slow=ctx.ema_slow if ctx else 0,
                    atr=ctx.atr if ctx else 0,
                    current_price=price,
                )
                if ai_result and not ai_result.get("approved", False):
                    if PRINT_DEBUG:
                        print(f"[AI REJECT] {symbol} {direction}: {ai_result.get('reason', '')}")
                    lvl.state = "SIGNALLED"
                    continue
                # Apply AI corrections if any
                if ai_result and ai_result.get("corrected", False):
                    direction = ai_result.get("direction", direction)
                    entry = ai_result.get("entry", entry)
                    stop_loss = ai_result.get("stop_loss", stop_loss)
                    tp1 = ai_result.get("tp1", tp1)
                    tp2 = ai_result.get("tp2", tp2)
                    risk_pct = abs(entry - stop_loss) / max(entry, 1e-9)
                    reason = ai_result.get("reason", reason)

                    # WhalePortal filter
        whale_passed, whale_reason, whale_summary = await check_whale_filter(symbol, direction)
        if not whale_passed:
            if PRINT_DEBUG:
                print(f"[WHALE REJECT] {symbol} {direction}: {whale_reason}")
            lvl.state = "SIGNALLED"
            continue

            await send_signal(
                symbol=symbol, direction=direction, entry=entry,
                level_price=lvl.price, stop_loss=stop_loss,
                tp1=tp1, tp2=tp2, reason=reason,
                risk_pct=risk_pct, leverage=LEVERAGE,
                            whale_summary=whale_summary,
            )

            lvl.state = "SIGNALLED"
            lvl.last_signal_ts = now
            _signal_cooldowns[key] = now

            if PRINT_DEBUG:
                print(f"[SIGNAL] {symbol} {direction} @ {entry:.4f}")
