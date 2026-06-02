import requests
import json
import os
from datetime import datetime, timedelta

# ========== ΡΥΘΜΙΣΕΙΣ ΣΤΡΑΤΗΓΙΚΗΣ ==========
REBOUND_PCT = 7.0                     # % ανάκαμψης από το χαμηλό για αγορά
TRAILING_SELL_DISTANCE = 5.0          # % πτώσης από κορυφή για έξοδο (στο WunderTrading)
MIN_DROP_FROM_ACTIVATION = 20.0       # ελάχιστη πτώση από το activation για να ανοίξει θέση (%)

# Secrets (GitHub Actions)
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")
ENTER_LONG_MESSAGE = os.environ.get("ENTER_LONG_MESSAGE")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# ========== ΠΑΡΑΜΕΤΡΟΙ ΑΝΑΛΥΣΗΣ ==========
LOOKBACK_1Y = 365                     # 1 έτος για supports
LOOKBACK_2Y = 730                     # 2 έτη για resistances
LOOKBACK_4Y = 1460                    # 4 έτη για πολύ παλιά crypto
MIN_SWING_DISTANCE_PCT = 20           # ελάχιστη διαφορά μεταξύ swing points (20%)

KUCOIN_API_PRICE = "https://api.kucoin.com/api/v1/market/orderbook/level1?symbol=ONDO-USDT"
KUCOIN_API_KLINES = "https://api.kucoin.com/api/v1/market/candles"
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
            f"✅ *Σήμα ΑΓΟΡΑΣ ONDO (Hybrid Macro)*\n"
            f"Τιμή τώρα: {get_price()}\n"
            f"Rebound: {REBOUND_PCT}%\n"
            f"Ώρα: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        )
    except Exception as e:
        print(f"Webhook error: {e}")

def get_available_history():
    """Ελέγχει πόσες ημέρες ιστορικών δεδομένων υπάρχουν για το ONDO."""
    # Δοκιμάζουμε να πάρουμε δεδομένα από την αρχή του 2024
    test_start = int(datetime(2024, 1, 1).timestamp())
    now = int(datetime.now().timestamp())
    params = {
        "type": "1day",
        "symbol": "ONDO-USDT",
        "startAt": test_start,
        "endAt": now
    }
    try:
        resp = requests.get(KUCOIN_API_KLINES, params=params, timeout=10)
        data = resp.json().get('data', [])
        if not data:
            return None
        data.sort(key=lambda x: int(x[0]))
        first_timestamp = int(data[0][0])
        days_available = (now - first_timestamp) // 86400
        return days_available
    except Exception as e:
        print(f"Error getting history length: {e}")
        return None

def determine_lookback(state):
    """Καθορίζει το lookback για supports και resistances με βάση την ηλικία του asset."""
    days = state.get("asset_age_days")
    if days is None:
        days = get_available_history()
        if days is None:
            days = 365  # default 1 έτος
        state["asset_age_days"] = days

    if days < 365:
        # Νεοσύστατα (<1 έτους)
        return 180, 365  # 6 μήνες για supports, 1 έτος για resistances
    elif days < 1095:
        # Μεσαία (1-3 έτη, π.χ. ONDO)
        return LOOKBACK_1Y, LOOKBACK_2Y  # 1 έτος supports, 2 έτη resistances
    else:
        # Ώριμα (>3 έτη)
        return LOOKBACK_2Y, LOOKBACK_4Y  # 2 έτη supports, 4 έτη resistances

def get_swings(symbol, lookback_days):
    """Κατεβάζει εβδομαδιαία κεριά και βρίσκει 3 swing lows & 3 swing highs."""
    end_time = int(datetime.now().timestamp())
    start_time = int((datetime.now() - timedelta(days=lookback_days)).timestamp())
    params = {
        "type": "1week",
        "symbol": symbol,
        "startAt": start_time,
        "endAt": end_time
    }
    try:
        resp = requests.get(KUCOIN_API_KLINES, params=params, timeout=15)
        data = resp.json()['data']
        if not data:
            return [], []
        data.sort(key=lambda x: int(x[0]))
        highs = [float(c[3]) for c in data]
        lows = [float(c[4]) for c in data]

        swing_lows = []
        swing_highs = []
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

        swing_highs = filter_swings(swing_highs, keep_high=True)[:3]
        swing_lows = filter_swings(swing_lows, keep_high=False)[:3]
        return swing_lows, swing_highs
    except Exception as e:
        print(f"Error fetching klines for {symbol}: {e}")
        return [], []

def update_levels(state):
    """Ανανεώνει τα support/resistance μία φορά την εβδομάδα."""
    today = datetime.now().strftime("%Y-%U")
    if state.get("last_update") == today:
        return state

    # Καθορισμός lookback με βάση την ηλικία
    supp_lookback, res_lookback = determine_lookback(state)
    print(f"Asset age: {state.get('asset_age_days', '?')} days → Supports: {supp_lookback}d, Resistances: {res_lookback}d")

    # Παίρνουμε ξεχωριστά supports (1 έτος) και resistances (2 έτη)
    supports, _ = get_swings("ONDO-USDT", supp_lookback)
    _, resistances = get_swings("ONDO-USDT", res_lookback)

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
    print("Δεν υπάρχουν ακόμα δεδομένα για swing points.")
    exit()

# --- ΕΠΙΠΕΔΑ ---
activation_support = max(supports)       # υψηλότερο support
exit_resistance = min(resistances)       # χαμηλότερο resistance

print(f"Τιμή: {price}, Activation Support: {activation_support}, Exit Resistance: {exit_resistance}")

# --- ΕΙΔΟΠΟΙΗΣΗ ΓΙΑ ΖΩΝΗ ΠΩΛΗΣΗΣ ---
if price >= exit_resistance:
    msg = (
        f"🔔 *ONDO μπήκε στη ζώνη πώλησης!*\n"
        f"Τιμή: {price}\n"
        f"Όριο αντίστασης: {exit_resistance}\n"
        f"Το Trailing Stop θα ενεργοποιηθεί σύντομα."
    )
    send_telegram(msg)
    print(msg)

# --- ΛΟΓΙΚΗ ΕΙΣΟΔΟΥ (με φίλτρο βάθους 20%) ---
activated = state.get("activated", False)
lowest = state.get("lowest_since_activation")

if not activated and price < activation_support:
    # Έλεγχος ελάχιστης πτώσης 20% από το activation
    min_price_for_entry = activation_support * (1 - MIN_DROP_FROM_ACTIVATION / 100)
    if price < min_price_for_entry:
        activated = True
        lowest = price
        print(f"Ενεργοποίηση με βάθος >20%! Αρχικό χαμηλό: {lowest}")
        send_telegram(
            f"📉 *Ενεργοποίηση Macro Buy (Hybrid)*\n"
            f"Τιμή έσπασε το support {activation_support} και έπεσε πάνω από 20%\n"
            f"Τρέχουσα: {price}"
        )
    else:
        print(f"Η τιμή έσπασε το {activation_support} αλλά δεν έπεσε αρκετά (-20%). Αναμονή.")

if activated:
    if lowest is None or price < lowest:
        lowest = price
        print(f"Νέο χαμηλό: {lowest}")

    rebound_level = lowest * (1 + REBOUND_PCT / 100)
    print(f"Rebound level: {rebound_level:.4f}")

    if price > rebound_level:
        print("✅ Macro rebound! Αποστολή σήματος αγοράς.")
        send_buy_signal()
        activated = False
        lowest = None

state["activated"] = activated
state["lowest_since_activation"] = lowest
save_state(state)
