from __future__ import annotations
import time
from typing import List
from levels import Level, calc_strength
from patterns import check_entry_filters
from context_candles import CandleContext
from telegram_bot import send_approaching, send_signal
from config import (
    LEVEL_NEAR_PCT, LEVEL_TOUCH_PCT, LEVEL_FAR_PCT,
    LEVEL_MIN_STRENGTH, LEVEL_COOLDOWN_SEC,
    LIQ_MIN_USDT, USE_LIQUIDATION_FILTER,
    STOP_LOSS_PCT, TP1_RR, TP2_RR, LEVERAGE, PRINT_DEBUG,
)


async def process_levels(symbol: str, price: float, levels: List[Level],
                         ctx: CandleContext) -> None:
    """Check each level state and send alerts/signals."""
    now = time.time()
    for lvl in levels:
        dist = abs(price - lvl.price) / max(price, 1e-9)
        calc_strength(lvl)

        # Reset far away levels
        if dist > LEVEL_FAR_PCT:
            if lvl.state != "FAR":
                lvl.state = "FAR"
            continue

        # FAR -> NEAR
        if lvl.state == "FAR" and dist <= LEVEL_NEAR_PCT:
            if lvl.strength >= LEVEL_MIN_STRENGTH:
                lvl.state = "NEAR"
                await send_approaching(
                    symbol, lvl.side, lvl.price,
                    lvl.volume_usdt, dist, lvl.strength,
                )
                if PRINT_DEBUG:
                    print(f"[NEAR] {symbol} {lvl.side} {lvl.price:.4f}")

        # NEAR -> TOUCHED -> try signal
        if lvl.state in ("NEAR", "FAR") and dist <= LEVEL_TOUCH_PCT:
            lvl.state = "TOUCHED"
            if PRINT_DEBUG:
                print(f"[TOUCH] {symbol} {lvl.side} {lvl.price:.4f}")

        if lvl.state == "TOUCHED":
            # Cooldown check
            if now - lvl.last_signal_ts < LEVEL_COOLDOWN_SEC:
                continue

            direction = "LONG" if lvl.side == "bid" else "SHORT"

            # Liquidation filter
            if USE_LIQUIDATION_FILTER:
                if direction == "LONG" and lvl.liq_short_usdt < LIQ_MIN_USDT:
                    continue
                if direction == "SHORT" and lvl.liq_long_usdt < LIQ_MIN_USDT:
                    continue

            # Candle / trend / volatility filters
            passed, reason = check_entry_filters(symbol, direction, lvl.price, ctx)
            if not passed:
                if PRINT_DEBUG:
                    print(f"[FILTER] {symbol} {direction} rejected: {reason}")
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

            await send_signal(
                symbol=symbol, direction=direction, entry=entry,
                level_price=lvl.price, stop_loss=stop_loss,
                tp1=tp1, tp2=tp2, reason=reason,
                risk_pct=risk_pct, leverage=LEVERAGE,
            )
            lvl.state = "SIGNALLED"
            lvl.last_signal_ts = now
            if PRINT_DEBUG:
                print(f"[SIGNAL] {symbol} {direction} @ {entry:.4f}")
