import requests
import json
import os
from datetime import datetime, timedelta
import yfinance as yf
import pandas as pd

# ========== ΣΤΑΘΕΡΕΣ ΣΤΡΑΤΗΓΙΚΗΣ ==========
REBOUND_PCT = 7.0
ATR_MULTIPLIER = 2.0

SUPPORTS = [0.34, 0.24, 0.20, 0.08]
RESISTANCES = [0.50, 0.87, 1.17, 2.14]

BUY_BUFFER = 1.01
SELL_BUFFER = 0.99

# ========== SECRETS ==========
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")
ENTER_LONG_MESSAGE = os.environ.get("ENTER_LONG_MESSAGE")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# ========== API ENDPOINTS ==========
KUCOIN_PRICE_URL = "https://api.kucoin.com/api/v1/market/orderbook/level1?symbol={}"
KUCOIN_KLINES_URL = "https://api.kucoin.com/api/v1/market/candles"
FG_URL = "https://api.alternative.me/fng/"
STATE_FILE = "state.json"

# ========== ΒΟΗΘΗΤΙΚΕΣ ΣΥΝΑΡΤΗΣΕΙΣ ==========
def load_state():
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except:
        return {
            "activated": False,
            "lowest_since_activation": None,
            "last_atr_update": None,
            "atr_value": None
        }

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)

def get_price(symbol="ONDO-USDT"):
    try:
        resp = requests.get(KUCOIN_PRICE_URL.format(symbol), timeout=10)
        return float(resp.json()['data']['price'])
    except Exception as e:
        print(f"Error getting price: {e}")
        return None

def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}, timeout=10)
    except Exception as e:
        print(f"Telegram error: {e}")

def send_buy_signal(trailing_distance_pct):
    if not WEBHOOK_URL or not ENTER_LONG_MESSAGE:
        print("Webhook/Message missing")
        return
    payload = {
        "code": ENTER_LONG_MESSAGE,
        "orderType": "market",
        "amountPerTradeType": "quote",
        "amountPerTrade": 50,
        "leverage": 1,
        "trailingStop": {
            "activation": 2.0,
            "execute": trailing_distance_pct
        }
    }
    try:
        resp = requests.post(WEBHOOK_URL, json=payload, timeout=10)
        print(f"Webhook response: {resp.status_code}")
        send_telegram(
            f"✅ *Σήμα ΑΓΟΡΑΣ ONDO (Macro-Adaptive)*\n"
            f"Τιμή: {get_price()}\n"
            f"Rebound: {REBOUND_PCT}%\n"
            f"Trailing Distance: {trailing_distance_pct:.1f}%\n"
            f"Ώρα: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        )
    except Exception as e:
        print(f"Webhook error: {e}")

def get_daily_klines(symbol, days=730):
    end = int(datetime.now().timestamp())
    start = int((datetime.now() - timedelta(days=days)).timestamp())
    params = {"type": "1day", "symbol": symbol, "startAt": start, "endAt": end}
    try:
        resp = requests.get(KUCOIN_KLINES_URL, params=params, timeout=15)
        data = resp.json()['data']
        if not data:
            return []
        data.sort(key=lambda x: int(x[0]))
        return [{"high": float(c[3]), "low": float(c[4]), "close": float(c[2])} for c in data]
    except Exception as e:
        print(f"Error fetching klines: {e}")
        return []

def calculate_daily_atr(klines, period=14):
    if len(klines) < period + 1:
        return None
    tr = []
    for i in range(1, len(klines)):
        h_l = klines[i]["high"] - klines[i]["low"]
        h_cp = abs(klines[i]["high"] - klines[i-1]["close"])
        l_cp = abs(klines[i]["low"] - klines[i-1]["close"])
        tr.append(max(h_l, h_cp, l_cp))
    return sum(tr[-period:]) / period

def get_season(month, day=None):
    if month == 11 or (month == 12 and day is not None and day <= 15):
        return "Bull"
    if month in [6, 7, 8]:
        return "Bear"
    return "Neutral"

def select_buy_support(current_month, current_day):
    next_month = 1 if current_month == 12 else current_month + 1
    next_season = get_season(next_month)
    return 0 if next_season == "Bull" else 1

def select_sell_resistance(current_month, current_day):
    next_month = 1 if current_month == 12 else current_month + 1
    current_season = get_season(current_month, current_day)
    next_season = get_season(next_month)
    if current_season == "Bear" or next_season == "Bear":
        return 0
    elif current_season == "Neutral" or next_season == "Neutral":
        return 1
    else:
        return 2

def update_atr(state):
    today = datetime.now().strftime("%Y-%m-%d")
    if state.get("last_atr_update") == today:
        return state
    klines = get_daily_klines("ONDO-USDT", 730)
    if klines:
        atr = calculate_daily_atr(klines, 14)
        if atr:
            state["atr_value"] = atr
            state["last_atr_update"] = today
            print(f"Daily ATR updated: {atr}")
    return state

# ========== ΝΕΕΣ ΣΥΝΑΡΤΗΣΕΙΣ MACRO SCORE ==========

def get_fear_greed_index():
    try:
        resp = requests.get(FG_URL, timeout=10)
        return int(resp.json()['data'][0]['value'])
    except Exception as e:
        print(f"Error getting Fear & Greed: {e}")
        return 50

def get_ticker_sma(ticker, period=50):
    try:
        df = yf.download(ticker, period="60d", progress=False)
        if df.empty:
            return None
        sma = df['Close'].rolling(window=period).mean().iloc[-1]
        return df['Close'].iloc[-1] > sma
    except Exception as e:
        print(f"Error getting {ticker}: {e}")
        return None

def get_ticker_return(ticker, period="50d"):
    try:
        df = yf.download(ticker, period="60d", progress=False)
        if df.empty or len(df) < 50:
            return None
        past_price = df['Close'].iloc[-50]
        return (df['Close'].iloc[-1] - past_price) / past_price
    except Exception as e:
        print(f"Error getting return for {ticker}: {e}")
        return None

def get_macro_sentiment_score():
    score = 0

    # 1. Παραδοσιακές Αγορές
    qqq_signal = get_ticker_sma("QQQ")
    if qqq_signal is not None:
        score += 2 if qqq_signal else -2

    try:
        df_vix = yf.download("^VIX", period="5d", progress=False)
        if not df_vix.empty and df_vix['Close'].iloc[-1] < 20:
            score += 2
        else:
            score -= 2
    except:
        pass

    # 2. Μάκρο
    tnx_return = get_ticker_return("^TNX", "50d")
    if tnx_return is not None:
        if tnx_return < -0.02: score += 1.5
        elif tnx_return > 0.02: score -= 1.5

    dxy_return = get_ticker_return("DX-Y.NYB", "50d")
    if dxy_return is not None:
        if dxy_return < -0.02: score += 1.5
        elif dxy_return > 0.02: score -= 1.5

    # 3. Κλίμα Crypto
    fg_value = get_fear_greed_index()
    if fg_value <= 25: score += 3
    elif fg_value <= 45: score += 1
    elif fg_value >= 80: score -= 3
    elif fg_value >= 65: score -= 1

    return max(-10, min(10, score))

def adjust_level_by_score(base_index, macro_score, is_support=True):
    shift = int(macro_score / 2.5)
    if is_support:
        return max(0, base_index - shift)
    else:
        return min(len(RESISTANCES)-1, base_index + shift)

# ========== ΚΥΡΙΩΣ ΡΟΗ ==========
now = datetime.now()
current_month, current_day = now.month, now.day

state = load_state()
state = update_atr(state)

price = get_price()
if price is None:
    exit()

base_buy_idx = select_buy_support(current_month, current_day)
base_sell_idx = select_sell_resistance(current_month, current_day)

macro_score = get_macro_sentiment_score()

final_buy_idx = adjust_level_by_score(base_buy_idx, macro_score, is_support=True)
final_sell_idx = adjust_level_by_score(base_sell_idx, macro_score, is_support=False)

buy_support = SUPPORTS[final_buy_idx]
sell_resistance = RESISTANCES[final_sell_idx]

activation_buy = buy_support * BUY_BUFFER
activation_sell = sell_resistance * SELL_BUFFER

atr = state.get("atr_value")
if atr and price > 0:
    trailing_distance_pct = (atr * ATR_MULTIPLIER) / price * 100
else:
    trailing_distance_pct = 5.0

print(f"Μήνας: {current_month}/{current_day}, Season buy: {base_buy_idx}, sell: {base_sell_idx}")
print(f"Macro Score: {macro_score} → Final buy: {final_buy_idx} (${buy_support}), sell: {final_sell_idx} (${sell_resistance})")
print(f"Act. Buy: {activation_buy:.4f}, Act. Sell: {activation_sell:.4f}, Trailing: {trailing_distance_pct:.1f}%")

if price >= activation_sell:
    send_telegram(
        f"🔔 *Ζώνη πώλησης ONDO*\n"
        f"Τιμή: {price}\n"
        f"Αντίσταση: {sell_resistance} (act: {activation_sell:.4f})\n"
        f"Macro Score: {macro_score}\n"
        f"Το Trailing Stop αναλαμβάνει."
    )

activated = state.get("activated", False)
lowest = state.get("lowest_since_activation")

if not activated and price < activation_buy:
    activated = True
    lowest = price
    print(f"Ενεργοποίηση! Τιμή < {activation_buy:.4f}, χαμηλό: {lowest}")
    send_telegram(f"📉 *Ενεργοποίηση Trailing Buy ONDO*\nΤιμή: {price}\nMacro Score: {macro_score}")

if activated:
    if lowest is None or price < lowest:
        lowest = price
        print(f"Νέο χαμηλό: {lowest}")
    rebound_level = lowest * (1 + REBOUND_PCT / 100)
    print(f"Rebound level: {rebound_level:.4f}")
    if price > rebound_level:
        print("✅ Rebound! Αποστολή σήματος αγοράς.")
        send_buy_signal(trailing_distance_pct)
        activated = False
        lowest = None

state["activated"] = activated
state["lowest_since_activation"] = lowest
save_state(state)
