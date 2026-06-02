import requests
import json
import os
from datetime import datetime, timedelta

# ========== ΣΤΡΑΤΗΓΙΚΗ ==========
REBOUND_PCT = 7.0                     # % ανάκαμψης για αγορά
MIN_SWING_DISTANCE_PCT = 20           # ελάχιστη διαφορά μεταξύ swing points

# ========== SECRETS ==========
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")
ENTER_LONG_MESSAGE = os.environ.get("ENTER_LONG_MESSAGE")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# ========== ΡΥΘΜΙΣΕΙΣ ==========
KUCOIN_API_PRICE = "https://api.kucoin.com/api/v1/market/orderbook/level1?symbol=ONDO-USDT"
KUCOIN_API_KLINES = "https://api.kucoin.com/api/v1/market/candles"
STATE_FILE = "state.json"

def load_state():
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except:
        return {
            "activated": False,
            "lowest_since_activation": None,
            "supports": [],
            "resistances": [],
            "last_update": None,
            "asset_age_days": None
        }

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)

def get_price():
    try:
        resp = requests.get(KUCOIN_API_PRICE, timeout=10)
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

def send_buy_signal():
    if not WEBHOOK_URL or not ENTER_LONG_MESSAGE:
        print("Webhook/Message missing")
        return
    try:
        resp = requests.post(WEBHOOK_URL, data=ENTER_LONG_MESSAGE.encode("utf-8"), timeout=10)
        print(f"Webhook response: {resp.status_code}")
        send_telegram(
            f"✅ *Σήμα ΑΓΟΡΑΣ ONDO (Dynamic Hybrid)*\n"
            f"Τιμή τώρα: {get_price()}\n"
            f"Rebound: {REBOUND_PCT}%\n"
            f"Ώρα: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        )
    except Exception as e:
        print(f"Webhook error: {e}")

def get_available_history():
    """Ελέγχει πόσες ημέρες ιστορικών δεδομένων υπάρχουν."""
    test_start = int(datetime(2024, 1, 1).timestamp())
    now = int(datetime.now().timestamp())
    params = {"type": "1day", "symbol": "ONDO-USDT", "startAt": test_start, "endAt": now}
    try:
        resp = requests.get(KUCOIN_API_KLINES, params=params, timeout=10)
        data = resp.json().get('data', [])
        if not data:
            return None
        data.sort(key=lambda x: int(x[0]))
        return (now - int(data[0][0])) // 86400
    except Exception as e:
        print(f"Error getting history length: {e}")
        return None

def determine_lookbacks(state):
    """Καθορίζει lookback για supports & resistances με βάση την ηλικία."""
    days = state.get("asset_age_days")
    if days is None:
        days = get_available_history()
        if days is None:
            days = 365
        state["asset_age_days"] = days

    if days < 365:
        return 180, 365      # 6M, 1Y
    elif days < 1095:
        return 365, 730      # 1Y, 2Y (ONDO)
    else:
        return 730, 1460     # 2Y, 4Y

def get_swings(symbol, lookback_days):
    """Παίρνει εβδομαδιαία κεριά και βρίσκει 3 swing lows & 3 swing highs."""
    end = int(datetime.now().timestamp())
    start = int((datetime.now() - timedelta(days=lookback_days)).timestamp())
    params = {"type": "1week", "symbol": symbol, "startAt": start, "endAt": end}
    try:
        resp = requests.get(KUCOIN_API_KLINES, params=params, timeout=15)
        data = resp.json()['data']
        if not data:
            return [], []
        data.sort(key=lambda x: int(x[0]))
        highs = [float(c[3]) for c in data]
        lows = [float(c[4]) for c in data]

        swing_lows, swing_highs = [], []
        for i in range(1, len(highs)-1):
            if highs[i] > highs[i-1] and highs[i] > highs[i+1]:
                swing_highs.append(highs[i])
            if lows[i] < lows[i-1] and lows[i] < lows[i+1]:
                swing_lows.append(lows[i])

        def filter_swings(points, keep_high=True):
            if not points:
                return []
            points.sort(reverse=keep_high)
            filtered = [points[0]]
            for p in points[1:]:
                if abs(p - filtered[-1]) / filtered[-1] >= MIN_SWING_DISTANCE_PCT / 100:
                    filtered.append(p)
            return filtered

        return filter_swings(swing_lows, keep_high=False)[:3], filter_swings(swing_highs, keep_high=True)[:3]
    except Exception as e:
        print(f"Error fetching klines: {e}")
        return [], []

def update_levels(state):
    """Ανανεώνει supports & resistances μία φορά την εβδομάδα."""
    today = datetime.now().strftime("%Y-%U")
    if state.get("last_update") == today:
        return state

    supp_lb, res_lb = determine_lookbacks(state)
    print(f"Asset age: {state.get('asset_age_days', '?')}d → Supports: {supp_lb}d, Resistances: {res_lb}d")

    supports, _ = get_swings("ONDO-USDT", supp_lb)
    _, resistances = get_swings("ONDO-USDT", res_lb)

    if supports and resistances:
        state["supports"] = supports
        state["resistances"] = resistances
        state["last_update"] = today
        print(f"Supports: {supports}, Resistances: {resistances}")
    return state

# ========== ΚΥΡΙΩΣ ΡΟΗ ==========
state = load_state()
state = update_levels(state)

price = get_price()
if price is None:
    exit()

supports = state.get("supports", [])
resistances = state.get("resistances", [])
if not supports or not resistances:
    print("Αναμονή για swing points.")
    exit()

activation_support = max(supports)        # υψηλότερο support
exit_resistance = min(resistances)        # χαμηλότερο resistance

print(f"Τιμή: {price}, Activation: {activation_support}, Exit zone: {exit_resistance}")

# --- ΕΙΔΟΠΟΙΗΣΗ ΖΩΝΗΣ ΠΩΛΗΣΗΣ ---
if price >= exit_resistance:
    send_telegram(
        f"🔔 *Ζώνη πώλησης ONDO*\n"
        f"Τιμή: {price}\n"
        f"Αντίσταση ενεργοποίησης: {exit_resistance}\n"
        f"Το Trailing Stop του WunderTrading αναλαμβάνει."
    )

# --- ΕΙΣΟΔΟΣ (TRAILING BUY) ---
activated = state.get("activated", False)
lowest = state.get("lowest_since_activation")

if not activated and price < activation_support:
    activated = True
    lowest = price
    print(f"Ενεργοποίηση! Τιμή < {activation_support}, αρχικό χαμηλό: {lowest}")
    send_telegram(f"📉 *Ενεργοποίηση Trailing Buy ONDO*\nΤιμή: {price}")

if activated:
    if lowest is None or price < lowest:
        lowest = price
        print(f"Νέο χαμηλό: {lowest}")
    rebound_level = lowest * (1 + REBOUND_PCT / 100)
    print(f"Rebound level: {rebound_level:.4f}")
    if price > rebound_level:
        print("✅ Rebound! Αποστολή σήματος αγοράς.")
        send_buy_signal()
        activated = False
        lowest = None

state["activated"] = activated
state["lowest_since_activation"] = lowest
save_state(state)
