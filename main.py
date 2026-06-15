#!/usr/bin/env python3
"""
ONDO Rebound Bot - Macro Adaptive Crypto Trading Bot

This bot implements a Macro Swing Trading strategy for ONDO/USD.
It runs every 10 minutes on GitHub Actions and sends signals to WunderTrading
for order execution on Kraken.
"""

import json
import os
import requests
from datetime import datetime, timedelta
import yfinance as yf
import feedparser
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

# Constants
ATR_MULTIPLIER = 2.0
BUY_BUFFER = 1.01    # +1% above support
SELL_BUFFER = 0.99   # -1% below resistance
LOOKBACK_DAYS = 1460 # 4 years for level calculation
SWING_WINDOW = 20    # days for swing point detection

# Environment variables (secrets)
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")
ENTER_LONG_MESSAGE = os.environ.get("ENTER_LONG_MESSAGE")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
TRADE_AMOUNT = float(os.environ.get("TRADE_AMOUNT", "1"))  # Default \$1 for testing

def load_state():
    """Load state from state.json or return default values."""
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
    """Save state to state.json."""
    try:
        with open("state.json", "w") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        print(f"Error saving state: {e}")

def get_price():
    """Get current ONDO price from Kraken API."""
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
    """Get daily candles from Kraken API."""
    try:
        since = int((datetime.now() - timedelta(days=days)).timestamp())
        response = requests.get(f"https://api.kraken.com/0/public/OHLC?pair=ONDOUSD&interval=1440&since={since}")
        data = response.json()
        if data.get("error"):
            print(f"Kraken OHLC API error: {data['error']}")
            return []
        
        # Convert to list of dictionaries for easier handling
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
    """Find swing highs and swing lows and return supports/resistances."""
    try:
        swing_highs = []
        swing_lows = []
        
        # Find swing highs
        for i in range(SWING_WINDOW, len(highs) - SWING_WINDOW):
            current_high = highs[i]
            is_swing_high = True
            
            # Check if it's the highest in the window
            for j in range(i - SWING_WINDOW, i + SWING_WINDOW + 1):
                if highs[j] > current_high:
                    is_swing_high = False
                    break
            
            if is_swing_high:
                swing_highs.append((i, current_high))
        
        # Find swing lows
        for i in range(SWING_WINDOW, len(lows) - SWING_WINDOW):
            current_low = lows[i]
            is_swing_low = True
            
            # Check if it's the lowest in the window
            for j in range(i - SWING_WINDOW, i + SWING_WINDOW + 1):
                if lows[j] < current_low:
                    is_swing_low = False
                    break
            
            if is_swing_low:
                swing_lows.append((i, current_low))
        
        # Sort and select the 3 most significant
        swing_highs.sort(key=lambda x: x[1], reverse=True)
        swing_lows.sort(key=lambda x: x[1])
        
        # Add extreme values
        all_highs = [h[1] for h in swing_highs]
        all_lows = [l[1] for l in swing_lows]
        
        resistances = []
        supports = []
        
        # 3 highest swing highs + absolute high
        if len(swing_highs) >= 3:
            resistances = [h[1] for h in swing_highs[:3]]
        else:
            resistances = [h[1] for h in swing_highs]
        
        if all_highs:
            resistances.append(max(all_highs))
        
        # 3 lowest swing lows + absolute low
        if len(swing_lows) >= 3:
            supports = [l[1] for l in swing_lows[:3]]
        else:
            supports = [l[1] for l in swing_lows]
        
        if all_lows:
            supports.append(min(all_lows))
        
        return supports, resistances
    except Exception as e:
        print(f"Error finding swings: {e}")
        return [], []

def calculate_daily_atr(klines):
    """Calculate ATR (Average True Range) for 14 days."""
    try:
        if len(klines) < 15:
            return None
        
        tr_values = []
        
        for i in range(1, len(klines)):
            high = klines[i]["high"]
            low = klines[i]["low"]
            prev_close = klines[i-1]["close"]
            
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            tr_values.append(tr)
        
        # Average of the last 14 TR values
        atr = sum(tr_values[-14:]) / 14
        return atr
    except Exception as e:
        print(f"Error calculating ATR: {e}")
        return None

def update_swing_levels(state):
    """Update support/resistance levels once a week."""
    try:
        current_week = datetime.now().strftime("%Y-%U")
        last_update = state.get("last_swing_update")
        
        if last_update == current_week:
            return state
        
        print("Updating swing levels...")
        
        # Get data
        klines = get_daily_klines()
        if not klines:
            return state
        
        # Extract highs and lows
        highs = [k["high"] for k in klines]
        lows = [k["low"] for k in klines]
        
        # Find swing points
        supports, resistances = find_swings(highs, lows)
        
        # Update state
        state["supports"] = supports
        state["resistances"] = resistances
        state["last_swing_update"] = current_week
        
        # Calculate asset age (days from first candle)
        if klines:
            first_candle_time = datetime.fromtimestamp(klines[0]["time"])
            asset_age = (datetime.now() - first_candle_time).days
            state["asset_age_days"] = asset_age
        
        print(f"Updated supports: {supports}")
        print(f"Updated resistances: {resistances}")
        
        return state
    except Exception as e:
        print(f"Error updating swing levels: {e}")
        return state

def update_atr(state):
    """Update ATR value once a day."""
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        last_update = state.get("last_atr_update")
        
        if last_update == today:
            return state
        
        print("Updating ATR...")
        
        # Get data
        klines = get_daily_klines()
        if not klines:
            return state
        
        # Calculate ATR
        atr = calculate_daily_atr(klines)
        if atr:
            state["atr_value"] = atr
            state["last_atr_update"] = today
            print(f"Updated ATR: {atr}")
        
        return state
    except Exception as e:
        print(f"Error updating ATR: {e}")
        return state

def send_buy_signal(trailing_pct):
    """Send buy signal to WunderTrading."""
    try:
        if not WEBHOOK_URL or not ENTER_LONG_MESSAGE:
            print("Missing webhook URL or enter long message")
            return
