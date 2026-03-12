"""TensorCharts-style signals: Wall + Volume spike + Reversal pattern.

Logic:
1. Detect persistent walls from orderbook (wall_detector)
2. When price approaches a wall -> fetch 1m/5m/15m klines
3. Look for volume spike + reversal candle pattern
4. Confirm with AI filter
5. Send signal with 'TensorCharts' tag
"""
from __future__ import annotations
import time
import httpx
from dataclasses import dataclass
from wall_detector import Wall, get_tracker
from ai_filter import filter_signal as ai_filter_signal
from telegram_bot import send_tc_signal
from config import PRINT_DEBUG

# --- Config ---
TC_APPROACH_PCT = 0.005   # Price within 0.5% of wall = approaching
TC_TOUCH_PCT = 0.002      # Price within 0.2% of wall = touching
TC_VOL_SPIKE_MULT = 2.0   # Volume must be 2x average
TC_COOLDOWN_SEC = 1800    # 30min cooldown per wall signal
TC_MIN_WALL_USDT = 100_000  # Min wall size for TC signals

KLINE_URL = "https://data-api.binance.vision/api/v3/klines"


@dataclass
class Kline:
    open_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    close_time: int


# --- Kline fetching ---
async def fetch_klines(symbol: str, interval: str, limit: int = 30) -> list[Kline]:
    """Fetch klines from Binance."""
    params = {"symbol": symbol.upper(), "interval": interval, "limit": limit}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(KLINE_URL, params=params)
            data = resp.json()
            klines = []
            for k in data:
                klines.append(Kline(
                    open_time=k[0], open=float(k[1]), high=float(k[2]),
                    low=float(k[3]), close=float(k[4]), volume=float(k[5]),
                    close_time=k[6],
                ))
            return klines
    except Exception as e:
        if PRINT_DEBUG:
            print(f"[TC] kline fetch error {symbol} {interval}: {e}")
        return []


# --- Reversal pattern detection ---
def detect_reversal(klines: list[Kline], side: str) -> tuple[bool, str, float]:
    """Check last few candles for reversal pattern + volume spike.
    
    Args:
        klines: Recent klines (at least 10)
        side: 'bid' (wall below = expect bounce UP) or 'ask' (wall above = expect bounce DOWN)
    
    Returns:
        (has_signal, pattern_name, volume_ratio)
    """
    if len(klines) < 10:
        return False, "", 0.0

    # Calculate average volume (exclude last 2 candles)
    avg_vol = sum(k.volume for k in klines[:-2]) / max(len(klines) - 2, 1)
    if avg_vol <= 0:
        return False, "", 0.0

    # Check last 3 candles for patterns
    last = klines[-1]
    prev = klines[-2]
    prev2 = klines[-3]

    body_last = abs(last.close - last.open)
    range_last = last.high - last.low
    vol_ratio = last.volume / avg_vol

    # Volume spike required
    if vol_ratio < TC_VOL_SPIKE_MULT:
        return False, "", vol_ratio

    if side == 'bid':  # Wall below - looking for bullish reversal
        # Pin bar (hammer): long lower wick, small body at top
        lower_wick = min(last.open, last.close) - last.low
        upper_wick = last.high - max(last.open, last.close)
        if range_last > 0 and lower_wick > range_last * 0.6 and body_last < range_last * 0.3:
            return True, "Hammer", vol_ratio

        # Bullish engulfing
        if (prev.close < prev.open  # prev bearish
                and last.close > last.open  # last bullish
                and last.close > prev.open
                and last.open <= prev.close):
            return True, "BullEngulf", vol_ratio

        # Morning doji star (simplified)
        if (prev2.close < prev2.open  # bearish
                and abs(prev.close - prev.open) < range_last * 0.1  # doji
                and last.close > last.open):  # bullish
            return True, "MorningStar", vol_ratio

        # Bullish rejection: long lower wick with close near high
        if range_last > 0 and lower_wick > range_last * 0.5 and last.close > last.open:
            return True, "BullReject", vol_ratio

    elif side == 'ask':  # Wall above - looking for bearish reversal
        # Shooting star: long upper wick, small body at bottom
        upper_wick = last.high - max(last.open, last.close)
        lower_wick = min(last.open, last.close) - last.low
        if range_last > 0 and upper_wick > range_last * 0.6 and body_last < range_last * 0.3:
            return True, "ShootStar", vol_ratio

        # Bearish engulfing
        if (prev.close > prev.open  # prev bullish
                and last.close < last.open  # last bearish
                and last.close < prev.open
                and last.open >= prev.close):
            return True, "BearEngulf", vol_ratio

        # Evening star (simplified)
        if (prev2.close > prev2.open  # bullish
                and abs(prev.close - prev.open) < range_last * 0.1  # doji
                and last.close < last.open):  # bearish
            return True, "EveningStar", vol_ratio

        # Bearish rejection: long upper wick with close near low
        if range_last > 0 and upper_wick > range_last * 0.5 and last.close < last.open:
            return True, "BearReject", vol_ratio

    return False, "", vol_ratio


# --- Signal cooldowns ---
_tc_cooldowns: dict[tuple, float] = {}


def _cooldown_key(symbol: str, wall: Wall) -> tuple:
    precision = max(wall.price * 0.002, 1e-8)
    rounded = round(wall.price / precision) * precision
    return (symbol, wall.side, rounded)


# --- Main TC signal check ---
async def check_tc_signals(
    symbol: str, current_price: float,
    bids: dict[float, float], asks: dict[float, float],
) -> None:
    """Main function: check for TensorCharts-style signals.
    
    Called every depth update cycle for each symbol.
    1. Update wall tracker
    2. Find walls near price
    3. Fetch multi-TF klines
    4. Check reversal + volume
    5. AI confirm
    6. Send signal
    """
    now = time.time()
    tracker = get_tracker(symbol)

    # Update walls from current orderbook
    persistent_walls = tracker.update(bids, asks, current_price)

    if PRINT_DEBUG and persistent_walls:
        top = persistent_walls[:3]
        walls_str = ", ".join(
            f"{w.wall_type[0].upper()}@{w.price:.2f}(${w.volume_usdt/1000:.0f}k)"
            for w in top
        )
        print(f"[TC] {symbol} walls: {walls_str}")

    # Check each wall near price
    for wall in persistent_walls:
        if wall.signalled or wall.volume_usdt < TC_MIN_WALL_USDT:
            continue

        dist = abs(current_price - wall.price) / max(current_price, 1e-9)
        if dist > TC_APPROACH_PCT:
            continue  # Not close enough

        # Cooldown check
        key = _cooldown_key(symbol, wall)
        last_signal = _tc_cooldowns.get(key, 0)
        if now - last_signal < TC_COOLDOWN_SEC:
            continue

        # Price is near wall - fetch klines on multiple timeframes
        best_tf = None
        best_pattern = ""
        best_vol_ratio = 0.0

        for tf in ["1m", "5m", "15m"]:
            klines = await fetch_klines(symbol, tf, limit=20)
            if not klines:
                continue

            has_reversal, pattern, vol_ratio = detect_reversal(klines, wall.side)
            if has_reversal and vol_ratio > best_vol_ratio:
                best_tf = tf
                best_pattern = pattern
                best_vol_ratio = vol_ratio

        if not best_tf:
            continue  # No reversal pattern found

        # Determine direction
        direction = "LONG" if wall.side == 'bid' else "SHORT"

        if PRINT_DEBUG:
            print(f"[TC] {symbol} {direction} reversal: {best_pattern} "
                  f"on {best_tf} (vol:{best_vol_ratio:.1f}x) "
                  f"at {wall.wall_type} wall ${wall.volume_usdt/1000:.0f}k")

        # Calculate SL/TP
        atr_est = abs(current_price * 0.005)  # ~0.5% estimated ATR
        if direction == "LONG":
            stop_loss = wall.price * 0.997  # Below the wall
            risk = current_price - stop_loss
            tp1 = current_price + risk * 2.0
            tp2 = current_price + risk * 3.0
        else:
            stop_loss = wall.price * 1.003  # Above the wall
            risk = stop_loss - current_price
            tp1 = current_price - risk * 2.0
            tp2 = current_price - risk * 3.0

        # AI confirmation
        ai_result = await ai_filter_signal(
            symbol=symbol, direction=direction,
            entry=current_price, stop_loss=stop_loss,
            tp1=tp1, tp2=tp2,
            level_price=wall.price,
            level_strength=wall.strength,
            volume_anomaly=best_vol_ratio,
            liquidation_cluster=wall.volume_usdt,
            ema_fast=0, ema_slow=0,  # Not used for TC
            atr=atr_est,
            current_price=current_price,
        )

        if ai_result and not ai_result.get("approved", False):
            if PRINT_DEBUG:
                print(f"[TC] {symbol} {direction} AI rejected: {ai_result.get('reason', '')}")
            wall.signalled = True
            _tc_cooldowns[key] = now
            continue

        # Apply AI corrections if any
        if ai_result and ai_result.get("corrected", False):
            direction = ai_result.get("direction", direction)
            stop_loss = ai_result.get("stop_loss", stop_loss)
            tp1 = ai_result.get("tp1", tp1)
            tp2 = ai_result.get("tp2", tp2)

        risk_pct = abs(current_price - stop_loss) / max(current_price, 1e-9)

        # Send TC signal
        await send_tc_signal(
            symbol=symbol,
            direction=direction,
            entry=current_price,
            stop_loss=stop_loss,
            tp1=tp1, tp2=tp2,
            wall_price=wall.price,
            wall_type=wall.wall_type,
            wall_usdt=wall.volume_usdt,
            pattern=best_pattern,
            timeframe=best_tf,
            vol_ratio=best_vol_ratio,
            risk_pct=risk_pct,
            leverage=20,
        )

        wall.signalled = True
        _tc_cooldowns[key] = now

        if PRINT_DEBUG:
            print(f"[TC SIGNAL] {symbol} {direction} @ {current_price:.4f} "
                  f"| {best_pattern} on {best_tf} | vol:{best_vol_ratio:.1f}x "
                  f"| wall:{wall.wall_type} ${wall.volume_usdt/1000:.0f}k")
