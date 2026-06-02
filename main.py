import requests
import json
import os
from datetime import datetime, timedelta

# ========== ΣΤΡΑΤΗΓΙΚΗ ==========
REBOUND_PCT = 7.0                     # % ανάκαμψης για αγορά
ATR_MULTIPLIER = 2.0                  # Πόσες φορές το ATR θα είναι το trailing distance
MIN_SWING_DISTANCE_PCT = 20           # ελάχιστη διαφορά μεταξύ swing points

# ========== SECRETS ==========
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")
# Δεν χρειαζόμαστε πλέον το ENTER_LONG_MESSAGE, γιατί στέλνουμε JSON
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
            "asset_age_days": None,
            "atr_value": None
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

def send_buy_signal(trailing_distance_pct):
    """Στέλνει σήμα αγοράς με δυναμικό trailing distance."""
    if not WEBHOOK_URL:
        print("Webhook missing")
        return
    # Δημιουργούμε JSON payload που θα διαβάσει το WunderTrading
    payload = {
        "code": "ENTER-LONG_KuCoin_ONDO-USDT_BDSATH ONDO_5M_9f474ad57d91e7d0db7b836d",  # Βάλε το δικό σου Enter‑Long σχόλιο
        "orderType": "market",
        "amountPerTradeType": "quote",
        "amountPerTrade": 50,          # ή όσο έχεις ορίσει
        "leverage": 1,
        "takeProfits": [
            {
                "price": 0,            # αφήνουμε το Trailing Stop να το καθορίσει
                "portfolio": 100       # 100% της θέσης
            }
        ],
        "trailingStop": {
            "activation": 2.0,         # ενεργοποίηση στο +2% κέρδος
            "execute": trailing_distance_pct   # δυναμική απόσταση από το ATR
        }
    }
    try:
        resp = requests.post(WEBHOOK_URL, json=payload, timeout=10)
        print(f"Webhook response: {resp.status_code}")
        send_telegram(
            f"✅ *Σήμα ΑΓΟΡΑΣ ONDO (Dynamic ATR)*\n"
            f"Τιμή τώρα: {get_price()}\n"
            f"Rebound: {REBOUND_PCT}%\n"
            f"Trailing Distance (ATR×{ATR_MULTIPLIER}): {trailing_distance_pct:.1f}%\n"
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

def get_weekly_data(symbol, lookback_days):
    """Παίρνει εβδομαδιαία κεριά και επιστρέφει highs, lows, closes."""
    end = int(datetime.now().timestamp())
    start = int((datetime.now() - timedelta(days=lookback_days)).timestamp())
    params = {"type": "1week", "symbol": symbol, "startAt": start, "endAt": end}
    try:
        resp = requests.get(KUCOIN_API_KLINES, params=params, timeout=15)
        data = resp.json()['data']
        if not data:
            return [], [], []
        data.sort(key=lambda x: int(x[0]))
        highs = [float(c[3]) for c in data]
        lows = [float(c[4]) for c in data]
        closes = [float(c[2]) for c in data]
        return highs, lows, closes
    except Exception as e:
        print(f"Error fetching klines: {e}")
        return [], [], []

def find_swings(highs, lows):
    """Βρίσκει 3 swing lows & 3 swing highs."""
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

def calculate_atr(highs, lows, closes, period=14):
    """Υπολογίζει το Average True Range (ATR)."""
    if len(highs) < period:
        return None
    tr = []
    for i in range(1, len(highs)):
        h_l = highs[i] - lows[i]
        h_cp = abs(highs[i] - closes[i-1])
        l_cp = abs(lows[i] - closes[i-1])
        tr.append(max(h_l, h_cp, l_cp))
    if len(tr) < period:
        return None
    return sum(tr[-period:]) / period

def update_levels(state):
    """Ανανεώνει supports, resistances και ATR μία φορά την εβδομάδα."""
    today = datetime.now().strftime("%Y-%U")
    if state.get("last_update") == today:
        return state

    supp_lb, res_lb = determine_lookbacks(state)
    print(f"Asset age: {state.get('asset_age_days', '?')}d → Supports: {supp_lb}d, Resistances: {res_lb}d")

    # Για supports: 1 έτος
    s_highs, s_lows, s_closes = get_weekly_data("ONDO-USDT", supp_lb)
    if s_highs:
        supports, _ = find_swings(s_highs, s_lows)
        state["supports"] = supports
        # Υπολογισμός ATR
        atr = calculate_atr(s_highs, s_lows, s_closes, 14)
        if atr:
            state["atr_value"] = atr
            print(f"ATR: {atr}")

    # Για resistances: 2 έτη
    r_highs, r_lows, _ = get_weekly_data("ONDO-USDT", res_lb)
    if r_highs:
        _, resistances = find_swings(r_highs, r_lows)
        state["resistances"] = resistances

    if state.get("supports") and state.get("resistances"):
        state["last_update"] = today
        print(f"Supports: {state['supports']}, Resistances: {state['resistances']}")
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

activation_support = max(supports)
exit_resistance = min(resistances)

# Υπολογισμός trailing distance (%)
atr = state.get("atr_value")
if atr and price > 0:
    trailing_distance_pct = (atr * ATR_MULTIPLIER) / price * 100
else:
    trailing_distance_pct = 5.0  # fallback

print(f"Τιμή: {price}, Activation: {activation_support}, Exit zone: {exit_resistance}, Trailing distance: {trailing_distance_pct:.1f}%")

# --- ΕΙΔΟΠΟΙΗΣΗ ΖΩΝΗΣ ΠΩΛΗΣΗΣ ---
if price >= exit_resistance:
    send_telegram(
        f"🔔 *Ζώνη πώλησης ONDO*\n"
        f"Τιμή: {price}\n"
        f"Αντίσταση ενεργοποίησης: {exit_resistance}\n"
        f"Το Trailing Stop (ATR×{ATR_MULTIPLIER}) αναλαμβάνει."
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
        send_buy_signal(trailing_distance_pct)
        activated = False
        lowest = None

state["activated"] = activated
state["lowest_since_activation"] = lowest
save_state(state)
