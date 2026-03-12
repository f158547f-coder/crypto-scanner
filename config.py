import os
from dotenv import load_dotenv
load_dotenv()
# --- Binance ---
BINANCE_WS_URL = "wss://fstream.binance.com/stream"
BINANCE_REST_URL = "https://fapi.binance.com"
# --- Symbols (top-20 USDT-M futures) ---
SYMBOLS = [
    "btcusdt", "ethusdt", "bnbusdt", "solusdt", "xrpusdt",
    "dogeusdt", "adausdt", "avaxusdt", "dotusdt", "linkusdt",
    "maticusdt", "uniusdt", "ltcusdt", "bchusdt", "arbusdt",
    "opusdt", "aptusdt", "filusdt", "nearusdt", "atomusdt",
]
# --- Level detection ---
LARGE_LEVEL_USDT = 500_000      # min cluster volume in USDT (raised to filter noise)
LEVEL_MERGE_PCT = 0.002         # 0.2% - merge nearby prices
LEVEL_NEAR_PCT = 0.005          # 0.5% - approaching alert
LEVEL_TOUCH_PCT = 0.002         # 0.2% - touched
LEVEL_FAR_PCT = 0.01            # 1.0% - reset to FAR
LEVEL_MIN_STRENGTH = 2.0        # min strength score for signal (raised)
LEVEL_COOLDOWN_SEC = 1800       # 30 min cooldown after signal
# --- Liquidations ---
LIQ_WINDOW_SEC = 300             # 5 min rolling window
LIQ_MIN_USDT = 50_000           # min liq volume near level
# --- Candle / trend context ---
CANDLE_TF_ENTRY = "5m"          # 5m timeframe for entry patterns (was 1m - too noisy)
CANDLE_TF_TREND = "15m"         # timeframe for trend filter
EMA_FAST = 21
EMA_SLOW = 50
ATR_PERIOD = 14
MIN_CANDLE_BODY_PCT = 0.001     # 0.1% min body for entry candle
MAX_CANDLE_RANGE_PCT = 0.03     # 3% max range (skip extreme vol)
PIN_BAR_WICK_RATIO = 2.0        # wick must be >= 2x body
# --- Signals ---
LEVERAGE = 20
STOP_LOSS_PCT = 0.005           # 0.5% behind level (was 0.3%)
TP1_RR = 2.0                    # risk-reward for TP1
TP2_RR = 3.0                    # risk-reward for TP2
RISK_PER_TRADE_PCT = 0.01       # 1% of balance per trade
BALANCE_USDT = 1000             # default balance for position calc
# --- Filters (toggles) ---
USE_CANDLE_PATTERNS = True
USE_TREND_FILTER = True
USE_FALSE_BREAK = True
USE_VOLATILITY_FILTER = True
USE_LIQUIDATION_FILTER = False   # disabled - depth20 rarely has liq data nearby
# --- Telegram ---
TG_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
# --- Debug ---
PRINT_DEBUG = os.getenv("PRINT_DEBUG", "1") == "1"
CANDLE_FETCH_INTERVAL = 60      # 60 seconds between candle refreshes
