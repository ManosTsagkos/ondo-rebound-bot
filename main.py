import requests
import json
import os

ACTIVATION_PRICE = 0.24
REBOUND_PCT = 7.0

# ΒΑΛΕ ΤΟ WEBHOOK URL ΑΠΟ ΤΟ WUNDERTRADING
WEBHOOK_URL = "https://wtalerts.com/bot/trading_view"

# ΒΑΛΕ ΤΟ ENTER-LONG ΣΧΟΛΙΟ ΑΠΟ ΤΟ BOT ΣΟΥ
ENTER_LONG_MESSAGE = "ENTER-LONG_KuCoin_ONDO-USDT_BDSATH ONDO_5M_9f474ad57d91e7d0db7b836d"

KUCOIN_API = "https://api.kucoin.com/api/v1/market/orderbook/level1?symbol=ONDO-USDT"
STATE_FILE = "state.json"

def load_state():
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except:
        return {"activated": False, "lowest_since_activation": None}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)

def get_price():
    try:
        resp = requests.get(KUCOIN_API, timeout=10)
        data = resp.json()
        return float(data['data']['price'])
    except Exception as e:
        print(f"Error getting price: {e}")
        return None

def send_webhook():
    print("Sending buy signal...")
    try:
        resp = requests.post(WEBHOOK_URL, data=ENTER_LONG_MESSAGE.encode("utf-8"), timeout=10)
        print(f"Webhook response: {resp.status_code}")
    except Exception as e:
        print(f"Error sending webhook: {e}")

state = load_state()
activated = state["activated"]
lowest_since_activation = state["lowest_since_activation"]

price = get_price()
if price is None:
    exit()

print(f"ONDO price: {price}")

if not activated and price < ACTIVATION_PRICE:
    activated = True
    lowest_since_activation = price
    print(f"Activated. Initial low: {lowest_since_activation}")

if activated:
    if lowest_since_activation is None or price < lowest_since_activation:
        lowest_since_activation = price
        print(f"New low: {lowest_since_activation}")

    rebound_level = lowest_since_activation * (1 + REBOUND_PCT / 100)
    print(f"Rebound level: {rebound_level:.4f}")

    if price > rebound_level:
        print("REBOUND DETECTED! Sending signal.")
        send_webhook()
        activated = False
        lowest_since_activation = None

state["activated"] = activated
state["lowest_since_activation"] = lowest_since_activation
save_state(state)
print("State saved.")
