#!/usr/bin/env python3
"""
ONDO Rebound Bot - Macro Adaptive Crypto Trading Bot

Αυτό το bot υλοποιεί μια στρατηγική Macro Swing Trading για το ONDO/USD.
Εκτελείται κάθε 10 λεπτά σε GitHub Actions και στέλνει σήματα στο WunderTrading
για την εκτέλεση εντολών στο Kraken.
"""

import json
import os
import requests
from datetime import datetime, timedelta
import yfinance as yf
import feedparser
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

# Σταθερές
ATR_MULTIPLIER = 2.0
BUY_BUFFER = 1.01    # +1% πάνω από το support
SELL_BUFFER = 0.99   # -1% κάτω από το resistance
LOOKBACK_DAYS = 1460 # 4 έτη για τον υπολογισμό επιπέδων
SWING_WINDOW = 20    # ημέρες για τον εντοπισμό swing points

# Environment variables (secrets)
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")
ENTER_LONG_MESSAGE = os.environ.get("ENTER_LONG_MESSAGE")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

def load_state():
    """Φορτώνει την κατάσταση από το state.json ή επιστρέφει default τιμές."""
    try:
        with open("state.json", "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        # Default state
        return {
            "activated": False,
            "lowest_since_activation": None,
            "supports": [],
            "resistances": [],
            "last_swing_update": None,
            "asset_age_days": 0,
            "atr_value": 0.0,
            "last_atr_update": None
        }

def save_state(state):
    """Αποθηκεύει την κατάσταση στο state.json."""
    try:
        with open("state.json", "w") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        print(f"Error saving state: {e}")

def get_price():
    """Παίρνει την τρέχουσα τιμή του ONDO από το Kraken API."""
    try:
        response = requests.get("https://api.kraken.com/0/public/Ticker?pair=ONDOUSD")
        data = response.json()
        if data.get("error"):
            print(f"Kraken API error: {data['error']}")
            return None
        return float(data["result"]["ONDOUSD"]["c"][0])
    except Exception as e:
        print(f"Error getting price: {e}")
        return None

def get_daily_klines(days=LOOKBACK_DAYS):
    """Παίρνει ημερήσια κεριά από το Kraken API."""
    try:
        since = int((datetime.now() - timedelta(days=days)).timestamp())
        response = requests.get(f"https://api.kraken.com/0/public/OHLC?pair=ONDOUSD&interval=1440&since={since}")
        data = response.json()
        if data.get("error"):
            print(f"Kraken OHLC API error: {data['error']}")
            return []
        
        # Μετατροπή σε λίστα από dictionaries για ευκολότερη διαχείριση
        klines = []
        for candle in data["result"]["ONDOUSD"]:
            klines.append({
                "time": int(candle[0]),
                "open": float(candle[1]),
                "high": float(candle[2]),
                "low": float(candle[3]),
                "close": float(candle[4]),
                "vwap": float(candle[5]),
                "volume": float(candle[6]),
                "count": int(candle[7])
            })
        return klines
    except Exception as e:
        print(f"Error getting daily klines: {e}")
        return []

def find_swings(highs, lows):
    """Εντοπίζει swing highs και swing lows και επιστρέφει supports/resistances."""
    try:
        swing_highs = []
        swing_lows = []
        
        # Εντοπισμός swing highs
        for i in range(SWING_WINDOW, len(highs) - SWING_WINDOW):
            current_high = highs[i]
            is_swing_high = True
            
            # Έλεγχος αν είναι το υψηλότερο στο παράθυρο
            for j in range(i - SWING_WINDOW, i + SWING_WINDOW + 1):
                if highs[j] > current_high:
                    is_swing_high = False
                    break
            
            if is_swing_high:
                swing_highs.append((i, current_high))
        
        # Εντοπισμός swing lows
        for i in range(SWING_WINDOW, len(lows) - SWING_WINDOW):
            current_low = lows[i]
            is_swing_low = True
            
            # Έλεγχος αν είναι το χαμηλότερο στο παράθυρο
            for j in range(i - SWING_WINDOW, i + SWING_WINDOW + 1):
                if lows[j] < current_low:
                    is_swing_low = False
                    break
            
            if is_swing_low:
                swing_lows.append((i, current_low))
        
        # Ταξινόμηση και επιλογή των 3 σημαντικότερων
        swing_highs.sort(key=lambda x: x[1], reverse=True)
        swing_lows.sort(key=lambda x: x[1])
        
        # Προσθήκη των ακραίων τιμών
        all_highs = [h[1] for h in swing_highs]
        all_lows = [l[1] for l in swing_lows]
        
        resistances = []
        supports = []
        
        # 3 υψηλότερα swing highs + το απόλυτο υψηλό
        if len(swing_highs
