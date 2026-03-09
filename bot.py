import requests
import time
import pandas as pd
import os
import json
from datetime import datetime

COOLDOWN_FILE  = "/tmp/bot_last_alert.json"
TRADES_FILE    = "/tmp/bot_active_trades.json"
WARNING_FILE   = "/tmp/bot_last_warning.json"
BREAKOUT_FILE  = "/tmp/bot_last_breakout.json"

def save_last_alert(ts):
    try:
        with open(COOLDOWN_FILE, "w") as f:
            json.dump({"last_alert": ts}, f)
    except:
        pass

def load_last_alert():
    try:
        if os.path.exists(COOLDOWN_FILE):
            with open(COOLDOWN_FILE) as f:
                data = json.load(f)
            return data.get("last_alert", 0)
    except:
        pass
    return 0

def save_active_trades(trades):
    try:
        with open(TRADES_FILE, "w") as f:
            json.dump(trades, f)
    except:
        pass

def save_last_warning(ts):
    try:
        with open(WARNING_FILE, "w") as f:
            json.dump({"last_warning": ts}, f)
    except:
        pass

def load_last_warning():
    try:
        if os.path.exists(WARNING_FILE):
            with open(WARNING_FILE) as f:
                data = json.load(f)
            return data.get("last_warning", 0)
    except:
        pass
    return 0

def save_last_breakout(level, direction, ts):
    try:
        with open(BREAKOUT_FILE, "w") as f:
            json.dump({"level": level, "direction": direction, "ts": ts}, f)
    except:
        pass

def load_last_breakout():
    try:
        if os.path.exists(BREAKOUT_FILE):
            with open(BREAKOUT_FILE) as f:
                return json.load(f)
    except:
        pass
    return None

def load_active_trades():
    try:
        if os.path.exists(TRADES_FILE):
            with open(TRADES_FILE) as f:
                return json.load(f)
    except:
        pass
    return []

# CONFIG
TELEGRAM_TOKEN   = "8678164580:AAEmznr79S6qO-NDqHkx8gOM-IqpyA884MQ"
TELEGRAM_CHAT_ID = "2050191721"
SYMBOL           = "SOL-USDT"
SCAN_INTERVAL    = 60
COOLDOWN         = 3600
MIN_SCORE        = 75.0

last_alert    = load_last_alert()
last_warning  = load_last_warning()
last_breakout = load_last_breakout()
active_trades = load_active_trades()

# TELEGRAM
def send_telegram(msg):
    url = "https://api.telegram.org/bot" + TELEGRAM_TOKEN + "/sendMessage"
    for attempt in range(3):
        try:
            r = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"}, timeout=10)
            if r.status_code == 200:
                print("[Telegram] OK")
                return True
            else:
                print("[Fehler] Telegram: " + str(r.status_code) + " (Versuch " + str(attempt+1) + ")")
                time.sleep(2)
        except Exception as e:
            print("[Fehler] Telegram: " + str(e) + " (Versuch " + str(attempt+1) + ")")
            time.sleep(2)
    print("[Telegram] KRITISCH: Alert nicht gesendet!")
    return False

# OKX DATA
def get_candles(symbol, bar, limit=100):
    try:
        r = requests.get("https://www.okx.com/api/v5/market/candles",
            params={"instId": symbol, "bar": bar, "limit": limit}, timeout=10)
        data = r.json().get("data", [])
        if not data:
            return None
        df = pd.DataFrame(data, columns=["ts","open","high","low","close","vol","volCcy","volCcyQuote","confirm"])
        for col in ["open","high","low","close","vol"]:
            df[col] = pd.to_numeric(df[col])
        df["ts"] = pd.to_numeric(df["ts"])
        return df.iloc[::-1].reset_index(drop=True)
    except Exception as e:
        print("[Fehler] Candles " + bar + ": " + str(e))
        return None

def get_taker_volume():
    try:
        r = requests.get("https://www.okx.com/api/v5/rubik/stat/taker-volume",
            params={"instId": SYMBOL, "instType": "SPOT", "period": "5m", "limit": 10}, timeout=10)
        data = r.json().get("data", [])
        if not data:
            return 0, 0
        df = pd.DataFrame(data, columns=["ts","sellVol","buyVol"])
        df["buyVol"]  = pd.to_numeric(df["buyVol"])
        df["sellVol"] = pd.to_numeric(df["sellVol"])
        return (df["buyVol"] - df["sellVol"]).sum(), (df["buyVol"] + df["sellVol"]).mean()
    except:
        return 0, 0

def get_funding():
    try:
        r = requests.get("https://www.okx.com/api/v5/public/funding-rate",
            params={"instId": "SOL-USDT-SWAP"}, timeout=10)
        return float(r.json().get("data",[{}])[0].get("fundingRate", 0)) * 100
    except:
        return 0.0

def get_price():
    try:
        r = requests.get("https://www.okx.com/api/v5/market/ticker",
            params={"instId": SYMBOL}, timeout=10)
        return float(r.json().get("data",[{}])[0].get("last", 0))
    except:
        return 0.0

# INDICATORS
def calc_ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def calc_rsi(series, period=14):
    delta = series.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    return 100 - (100 / (1 + gain / (loss + 1e-10)))

def calc_atr(df, period=14):
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift()).abs(),
        (df["low"]  - df["close"].shift()).abs()
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()

def calc_vwap(df):
    tp = (df["high"] + df["low"] + df["close"]) / 3
    return (tp * df["vol"]).cumsum() / df["vol"].cumsum()

# SCORE
def calc_prob_score(factors):
    prob = 0.50
    for f in factors:
        prob *= f
    return round(max(0.05, min(0.95, prob)) * 100, 1)

# SESSION
def get_session():
    hour = datetime.now().hour
    if 14 <= hour < 17:
        return "OVERLAP",   "London/NY Overlap 🔥", 1.30
    elif 8 <= hour < 18:
        return "LONDON_NY", "London/NY Session",    1.20
    elif 0 <= hour < 8:
        return "ASIA",      "Asia Session",          1.00
    else:
        return "EVENING",   "Evening Session",       1.00

# ═══════════════════════════════════════════════════════════
# NEU V13: ORDER FLOW FAKTOREN
# ═══════════════════════════════════════════════════════════

def get_delta_factor(direction):
    """
    V13: Delta als echter Score-Faktor statt nur Info-Text.
    Starkes positives Delta bei LONG = Boost, negatives = Penalty.
    """
    delta, avg_vol = get_taker_volume()
    if avg_vol == 0:
        return 1.0, "⚠️ Delta: keine Daten"
    dp = delta / (avg_vol + 1e-10)

    if direction == "LONG":
        if dp > 0.30:
            return 1.25, "✅ Delta stark bullish (+" + str(round(dp*100,0)) + "%)"
        if dp > 0.10:
            return 1.10, "✅ Delta bullish (+" + str(round(dp*100,0)) + "%)"
        if dp < -0.20:
            return 0.65, "❌ Delta bearish – Gegenwind! (" + str(round(dp*100,0)) + "%)"
        if dp < -0.10:
            return 0.85, "⚠️ Delta leicht bearish (" + str(round(dp*100,0)) + "%)"
        return 1.0, "⚠️ Delta neutral (" + str(round(dp*100,0)) + "%)"
    else:
        if dp < -0.30:
            return 1.25, "✅ Delta stark bearish (" + str(round(dp*100,0)) + "%)"
        if dp < -0.10:
            return 1.10, "✅ Delta bearish (" + str(round(dp*100,0)) + "%)"
        if dp > 0.20:
            return 0.65, "❌ Delta bullish – Gegenwind! (+" + str(round(dp*100,0)) + "%)"
        if dp > 0.10:
            return 0.85, "⚠️ Delta leicht bullish (+" + str(round(dp*100,0)) + "%)"
        return 1.0, "⚠️ Delta neutral (" + str(round(dp*100,0)) + "%)"


def get_absorption_factor(direction):
    """
    V13: Absorption Detection.
    Hohes Volumen + kleine Kerze = Institutionelle absorbieren Orders.
    Bullische Absorption bei LONG = starkes Continuation-Signal.
    """
    df = get_candles(SYMBOL, "5m", 20)
    if df is None or len(df) < 5:
        return 1.0, ""

    last      = df.iloc[-1]
    avg_range = (df["high"] - df["low"]).tail(10).mean()
    avg_vol   = df["vol"].tail(10).mean()
    candle_range = last["high"] - last["low"]
    vol_ratio    = last["vol"] / (avg_vol + 1e-10)
    compression  = candle_range / (avg_range + 1e-10)

    # Absorption: hohes Volumen (>2x) + kleine Kerze (<50% avg range)
    if vol_ratio > 2.0 and compression < 0.5:
        is_bull = last["close"] > last["open"]
        if direction == "LONG" and is_bull:
            return 1.20, "✅ Bullische Absorption! (Vol:" + str(round(vol_ratio,1)) + "x Comp:" + str(round(compression,2)) + ")"
        elif direction == "SHORT" and not is_bull:
            return 1.20, "✅ Bärische Absorption! (Vol:" + str(round(vol_ratio,1)) + "x Comp:" + str(round(compression,2)) + ")"
        else:
            return 0.85, "⚠️ Absorption – Richtung unklar"

    # Schwächere Absorption
    if vol_ratio > 1.5 and compression < 0.6:
        is_bull = last["close"] > last["open"]
        if direction == "LONG" and is_bull:
            return 1.10, "🔵 Schwache bullische Absorption"
        elif direction == "SHORT" and not is_bull:
            return 1.10, "🔵 Schwache bärische Absorption"

    return 1.0, ""


def get_volume_imbalance_factor(direction):
    """
    V13: Volume Imbalance auf 15m.
    3 konsekutive Kerzen mit klarem Volumen-Überhang = Trend-Bestätigung.
    """
    df = get_candles(SYMBOL, "15m", 10)
    if df is None or len(df) < 5:
        return 1.0, ""

    last3      = df.tail(3)
    bull_c     = int((last3["close"] > last3["open"]).sum())
    bear_c     = int((last3["close"] < last3["open"]).sum())
    vol_bull   = last3[last3["close"] > last3["open"]]["vol"].sum()
    vol_bear   = last3[last3["close"] < last3["open"]]["vol"].sum()
    total_vol  = vol_bull + vol_bear + 1e-10

    if direction == "LONG":
        if bull_c == 3 and vol_bull > vol_bear * 2.0:
            return 1.20, "✅ Volume Imbalance stark bullish (3/3 Kerzen, " + str(round(vol_bull/total_vol*100)) + "% Buy-Vol)"
        if bull_c >= 2 and vol_bull > vol_bear * 1.5:
            return 1.10, "✅ Volume Imbalance bullish (" + str(bull_c) + "/3 Kerzen)"
        if bear_c == 3:
            return 0.75, "❌ Alle 3 Kerzen bärisch – Volume gegen LONG!"
        if bear_c >= 2 and vol_bear > vol_bull * 1.5:
            return 0.85, "⚠️ Volume Imbalance gegen LONG"
    else:
        if bear_c == 3 and vol_bear > vol_bull * 2.0:
            return 1.20, "✅ Volume Imbalance stark bearish (3/3 Kerzen, " + str(round(vol_bear/total_vol*100)) + "% Sell-Vol)"
        if bear_c >= 2 and vol_bear > vol_bull * 1.5:
            return 1.10, "✅ Volume Imbalance bearish (" + str(bear_c) + "/3 Kerzen)"
        if bull_c == 3:
            return 0.75, "❌ Alle 3 Kerzen bullisch – Volume gegen SHORT!"
        if bull_c >= 2 and vol_bull > vol_bear * 1.5:
            return 0.85, "⚠️ Volume Imbalance gegen SHORT"

    return 1.0, ""

# ═══════════════════════════════════════════════════════════
# LAYER 0-5 (unverändert von V12)
# ═══════════════════════════════════════════════════════════

def analyze_4h():
    df = get_candles(SYMBOL, "4H", 60)
    if df is None or len(df) < 30:
        return "NEUTRAL", 1.0, "⚠️ 4h: Keine Daten"
    ema21 = calc_ema(df["close"], 21).iloc[-1]
    ema50 = calc_ema(df["close"], 50).iloc[-1]
    price = df["close"].iloc[-1]
    highs = df["high"].tail(10); lows = df["low"].tail(10)
    hh_hl = highs.iloc[-1] > highs.iloc[0] and lows.iloc[-1] > lows.iloc[0]
    lh_ll = highs.iloc[-1] < highs.iloc[0] and lows.iloc[-1] < lows.iloc[0]
    bull  = sum([price > ema21, ema21 > ema50, hh_hl])
    bear  = sum([price < ema21, ema21 < ema50, lh_ll])
    if bull >= 2: return "BULLISH", 1.0, "✅ 4h: BULLISCH"
    if bear >= 2: return "BEARISH", 1.0, "✅ 4h: BAERISCH"
    return "NEUTRAL", 1.0, "⚠️ 4h: NEUTRAL"

def analyze_1h():
    df = get_candles(SYMBOL, "1H", 50)
    if df is None or len(df) < 30:
        return "NEUTRAL", 1.0, "⚠️ 1h: Keine Daten"
    ema21 = calc_ema(df["close"], 21).iloc[-1]
    ema50 = calc_ema(df["close"], 50).iloc[-1]
    price = df["close"].iloc[-1]
    highs = df["high"].tail(10); lows = df["low"].tail(10)
    hh_hl = highs.iloc[-1] > highs.iloc[0] and lows.iloc[-1] > lows.iloc[0]
    lh_ll = highs.iloc[-1] < highs.iloc[0] and lows.iloc[-1] < lows.iloc[0]
    bull  = sum([price > ema21, ema21 > ema50, hh_hl])
    bear  = sum([price < ema21, ema21 < ema50, lh_ll])
    if bull == 3: return "BULLISH", 1.50, "✅ 1h: Stark BULLISCH (3/3)"
    if bull == 2: return "BULLISH", 1.30, "✅ 1h: BULLISCH (2/3)"
    if bear == 3: return "BEARISH", 1.50, "✅ 1h: Stark BAERISCH (3/3)"
    if bear == 2: return "BEARISH", 1.30, "✅ 1h: BAERISCH (2/3)"
    return "NEUTRAL", 0.70, "⚠️ 1h: NEUTRAL"

def analyze_30m(direction):
    df = get_candles(SYMBOL, "30m", 50)
    if df is None or len(df) < 30:
        return 1.0, "⚠️ 30m: Keine Daten"
    ema21 = calc_ema(df["close"], 21).iloc[-1]
    ema50 = calc_ema(df["close"], 50).iloc[-1]
    price = df["close"].iloc[-1]
    highs = df["high"].tail(8); lows = df["low"].tail(8)
    factor = 1.0; signals = []
    if direction == "LONG":
        if price > ema21 > ema50:   factor *= 1.25; signals.append("✅ 30m: EMA Struktur bullish")
        elif price > ema50:          factor *= 1.10; signals.append("🔵 30m: Preis > EMA50")
        else:                        factor *= 0.70; signals.append("❌ 30m: EMA gegen LONG!")
        if highs.iloc[-1] > highs.iloc[0] and lows.iloc[-1] > lows.iloc[0]:
            factor *= 1.15; signals.append("✅ 30m: HH/HL Struktur")
        else:
            factor *= 0.90; signals.append("⚠️ 30m: Struktur neutral")
    else:
        if price < ema21 < ema50:   factor *= 1.25; signals.append("✅ 30m: EMA Struktur bearish")
        elif price < ema50:          factor *= 1.10; signals.append("🔵 30m: Preis < EMA50")
        else:                        factor *= 0.70; signals.append("❌ 30m: EMA gegen SHORT!")
        if highs.iloc[-1] < highs.iloc[0] and lows.iloc[-1] < lows.iloc[0]:
            factor *= 1.15; signals.append("✅ 30m: LH/LL Struktur")
        else:
            factor *= 0.90; signals.append("⚠️ 30m: Struktur neutral")
    return factor, " | ".join(signals)

def check_pullback(direction):
    df5  = get_candles(SYMBOL, "5m", 60)
    df15 = get_candles(SYMBOL, "15m", 60)
    if df5 is None or df15 is None or len(df5) < 20:
        return None
    price   = df5["close"].iloc[-1]
    atr     = calc_atr(df5).iloc[-1]
    rsi     = calc_rsi(df5["close"]).iloc[-1]
    ema9    = calc_ema(df5["close"], 9).iloc[-1]
    ema21   = calc_ema(df5["close"], 21).iloc[-1]
    highs_15m = []; lows_15m = []
    for i in range(2, min(40, len(df15)-2)):
        if df15["high"].iloc[-i] > df15["high"].iloc[-i-1] and df15["high"].iloc[-i] > df15["high"].iloc[-i+1]:
            highs_15m.append(df15["high"].iloc[-i])
        if df15["low"].iloc[-i] < df15["low"].iloc[-i-1] and df15["low"].iloc[-i] < df15["low"].iloc[-i+1]:
            lows_15m.append(df15["low"].iloc[-i])
    tolerance = atr * 0.6
    if direction == "LONG":
        for level in highs_15m[:5]:
            if level < price * 0.999:
                if abs(price - level) <= tolerance and rsi < 65 and price > ema9 and price > level:
                    last3 = df5["close"].tail(3)
                    if last3.iloc[-1] >= last3.min() * 0.998:
                        return {"direction":"LONG","level":round(level,3),"price":round(price,3),"atr":round(atr,3),"rsi":round(rsi,1),"type":"Ehem. Widerstand → Support"}
        ema21_15m = calc_ema(df15["close"], 21).iloc[-1]
        if abs(price - ema21_15m) <= tolerance and price > ema21_15m and rsi < 60:
            return {"direction":"LONG","level":round(ema21_15m,3),"price":round(price,3),"atr":round(atr,3),"rsi":round(rsi,1),"type":"EMA21 (15m) Support"}
    else:
        for level in lows_15m[:5]:
            if level > price * 1.001:
                if abs(price - level) <= tolerance and rsi > 35 and price < ema9 and price < level:
                    last3 = df5["close"].tail(3)
                    if last3.iloc[-1] <= last3.max() * 1.002:
                        return {"direction":"SHORT","level":round(level,3),"price":round(price,3),"atr":round(atr,3),"rsi":round(rsi,1),"type":"Ehem. Support → Widerstand"}
        ema21_15m = calc_ema(df15["close"], 21).iloc[-1]
        if abs(price - ema21_15m) <= tolerance and price < ema21_15m and rsi > 40:
            return {"direction":"SHORT","level":round(ema21_15m,3),"price":round(price,3),"atr":round(atr,3),"rsi":round(rsi,1),"type":"EMA21 (15m) Widerstand"}
    return None

def format_pullback(pb, session_name):
    d = pb["direction"]; arrow = "📈 LONG" if d == "LONG" else "📉 SHORT"
    atr = pb["atr"]; price = pb["price"]
    if d == "LONG":
        sl = round(price - atr*1.5,3); t1 = round(price + atr*1.5,3)
        t2 = round(price + atr*3.0,3); t3 = round(price + atr*7.0,3)
    else:
        sl = round(price + atr*1.5,3); t1 = round(price - atr*1.5,3)
        t2 = round(price - atr*3.0,3); t3 = round(price - atr*7.0,3)
    risk = abs(price - sl)
    lines = [
        "——————————————————",
        "🔄 <b>PULLBACK ENTRY!</b>  " + arrow,
        "🕐 " + session_name,
        "——————————————————",
        "📍 Level: $" + str(pb["level"]) + "  <i>(" + pb.get("type","Retest") + ")</i>",
        "💰 Aktuell: $" + str(price),
        "✅ Level hält – sauberer Entry!",
        "📊 RSI: " + str(pb["rsi"]),
        "——————————————————",
        "🔴 <b>SL:</b> $" + str(sl) + "  <i>(ATR: " + str(atr) + ")</i>",
        "🎯 <b>T1:</b> $" + str(t1) + "  (RR 1:" + str(round(abs(price-t1)/risk,1)) + ")",
        "🎯 <b>T2:</b> $" + str(t2) + "  (RR 1:" + str(round(abs(price-t2)/risk,1)) + ")",
        "🚀 <b>T3:</b> $" + str(t3) + "  (RR 1:" + str(round(abs(price-t3)/risk,1)) + ")",
        "——————————————————",
        "⚠️ <i>Kein Auto-Trade – du entscheidest!</i>"
    ]
    return "\n".join(lines)

def check_prebreakout_warning(direction, session_name):
    df = get_candles(SYMBOL, "15m", 60)
    if df is None or len(df) < 30:
        return None
    price   = df["close"].iloc[-1]
    high_20 = df["high"].tail(21).iloc[:-1].max()
    low_20  = df["low"].tail(21).iloc[:-1].min()
    vol_avg = df["vol"].tail(20).mean()
    vol_now = df["vol"].iloc[-1]
    vol_ok  = vol_now > vol_avg * 1.3
    if direction == "LONG":
        dist = (high_20 - price) / high_20 * 100
        if 0.0 < dist <= 0.8 and vol_ok:
            return {"direction": direction, "level": round(high_20,3), "price": round(price,3), "dist": round(dist,2), "session": session_name}
    else:
        dist = (price - low_20) / low_20 * 100
        if 0.0 < dist <= 0.8 and vol_ok:
            return {"direction": direction, "level": round(low_20,3), "price": round(price,3), "dist": round(dist,2), "session": session_name}
    return None

def format_warning(w):
    arrow = "📈 LONG" if w["direction"] == "LONG" else "📉 SHORT"
    action = "Widerstand" if w["direction"] == "LONG" else "Support"
    lines = [
        "⚠️ <b>BREAKOUT SETUP nähert sich!</b>",
        "💰 SOL/USDT  " + arrow,
        "🕐 " + w["session"],
        "——————————————————",
        "📍 Aktuell: $" + str(w["price"]),
        "🎯 Breakout-Level: $" + str(w["level"]),
        "📏 Abstand: nur " + str(w["dist"]) + "%",
        "——————————————————",
        "👀 <b>Sei bereit – Signal kommt bald!</b>",
        "⏳ Beobachte " + action + " bei $" + str(w["level"])
    ]
    return "\n".join(lines)

def analyze_15m(direction):
    df = get_candles(SYMBOL, "15m", 60)
    if df is None or len(df) < 30:
        return None, 1.0, 0, ""
    price   = df["close"].iloc[-1]
    atr     = calc_atr(df).iloc[-1]
    vwap    = calc_vwap(df).iloc[-1]
    high_20 = df["high"].tail(21).iloc[:-1].max()
    low_20  = df["low"].tail(21).iloc[:-1].min()
    vol_avg = df["vol"].tail(20).mean()
    vol_now = df["vol"].iloc[-1]
    vol_ok  = vol_now > vol_avg * 1.8
    signals = []
    if direction == "LONG":
        if price >= high_20 * 0.999 and vol_ok: market_mode = "BREAKOUT_UP"
        else: return None, 1.0, atr, ""
    else:
        if price <= low_20 * 1.001 and vol_ok: market_mode = "BREAKOUT_DOWN"
        else: return None, 1.0, atr, ""
    vr = vol_now / (vol_avg + 1e-10)
    factor = 1.40
    signals.append("💥 15m: BREAKOUT mit " + str(round(vr,1)) + "x Volumen!")
    if vr > 3.0:   factor *= 1.20; signals.append("🔥 Sehr starkes Volumen!")
    elif vr > 2.0: factor *= 1.10
    elif vr > 1.8: factor *= 1.05
    avg_c = abs(df["close"].tail(20) - df["open"].tail(20)).mean()
    for i in range(len(df)-2, max(len(df)-20,0), -1):
        sz = abs(df["close"].iloc[i] - df["open"].iloc[i])
        if sz > avg_c * 1.8:
            if direction == "LONG" and df["close"].iloc[i] > df["open"].iloc[i]:
                if df["low"].iloc[i] <= price <= df["open"].iloc[i] * 1.015:
                    factor *= 1.20; signals.append("✅ 15m: Bullischer OB!"); break
            elif direction == "SHORT" and df["close"].iloc[i] < df["open"].iloc[i]:
                if df["open"].iloc[i] * 0.985 <= price <= df["high"].iloc[i]:
                    factor *= 1.20; signals.append("✅ 15m: Baerischer OB!"); break
    if direction == "LONG":
        rl = df["low"].tail(20).iloc[:-1].min()
        if df["low"].iloc[-1] < rl and df["close"].iloc[-1] > rl:
            factor *= 1.20; signals.append("✅ 15m: Bullischer Sweep!")
    else:
        rh = df["high"].tail(20).iloc[:-1].max()
        if df["high"].iloc[-1] > rh and df["close"].iloc[-1] < rh:
            factor *= 1.20; signals.append("✅ 15m: Baerischer Sweep!")
    if (direction == "LONG" and price > vwap) or (direction == "SHORT" and price < vwap):
        factor *= 1.10; signals.append("✅ 15m: VWAP bestaetigt ($" + str(round(vwap,3)) + ")")
    else:
        factor *= 0.85; signals.append("⚠️ 15m: Gegen VWAP ($" + str(round(vwap,3)) + ")")
    return market_mode, factor, atr, " | ".join(signals)

def analyze_5m(direction):
    df = get_candles(SYMBOL, "5m", 60)
    if df is None or len(df) < 30:
        return 1.0, ""
    rsi     = calc_rsi(df["close"]).iloc[-1]
    ema9    = calc_ema(df["close"], 9).iloc[-1]
    ema21   = calc_ema(df["close"], 21).iloc[-1]
    price   = df["close"].iloc[-1]
    vol_avg = df["vol"].tail(20).mean()
    vr      = df["vol"].iloc[-1] / (vol_avg + 1e-10)
    factor  = 1.0; signals = []
    if direction == "LONG":
        if rsi < 35:    factor *= 1.30; signals.append("✅ 5m: RSI ueberverkauft " + str(round(rsi,1)))
        elif rsi < 45:  factor *= 1.15; signals.append("✅ 5m: RSI gut " + str(round(rsi,1)))
        elif rsi < 55:  factor *= 1.05; signals.append("🔵 5m: RSI ok " + str(round(rsi,1)))
        elif rsi > 70:  factor *= 0.50; signals.append("❌ 5m: RSI ueberkauft " + str(round(rsi,1)) + "!")
        elif rsi > 60:  factor *= 0.80; signals.append("⚠️ 5m: RSI hoch " + str(round(rsi,1)))
        else:           factor *= 0.95; signals.append("⚠️ 5m: RSI " + str(round(rsi,1)))
    else:
        if rsi > 65:    factor *= 1.30; signals.append("✅ 5m: RSI ueberkauft " + str(round(rsi,1)))
        elif rsi > 55:  factor *= 1.15; signals.append("✅ 5m: RSI gut " + str(round(rsi,1)))
        elif rsi > 45:  factor *= 1.05; signals.append("🔵 5m: RSI ok " + str(round(rsi,1)))
        elif rsi < 30:  factor *= 0.50; signals.append("❌ 5m: RSI ueberverkauft " + str(round(rsi,1)) + "!")
        elif rsi < 40:  factor *= 0.80; signals.append("⚠️ 5m: RSI niedrig " + str(round(rsi,1)))
        else:           factor *= 0.95; signals.append("⚠️ 5m: RSI " + str(round(rsi,1)))
    if direction == "LONG" and price > ema9 > ema21:    factor *= 1.15; signals.append("✅ 5m: EMA9 > EMA21 bullish")
    elif direction == "SHORT" and price < ema9 < ema21: factor *= 1.15; signals.append("✅ 5m: EMA9 < EMA21 bearish")
    else:                                                factor *= 0.95; signals.append("⚠️ 5m: EMA neutral")
    if vr > 2.5:   factor *= 1.20; signals.append("✅ 5m: Starkes Volumen " + str(round(vr,1)) + "x")
    elif vr > 1.5: factor *= 1.10; signals.append("✅ 5m: Gutes Volumen " + str(round(vr,1)) + "x")
    elif vr > 1.0: factor *= 1.05; signals.append("🔵 5m: Volumen ok")
    else:          factor *= 0.85; signals.append("⚠️ 5m: Volumen schwach")
    return factor, " | ".join(signals)

def analyze_1m(direction):
    df = get_candles(SYMBOL, "1m", 30)
    if df is None or len(df) < 10:
        return 1.0, ""
    last3  = df["close"].tail(3)
    rsi    = calc_rsi(df["close"]).iloc[-1]
    factor = 1.0; signals = []
    if direction == "LONG":
        if last3.iloc[-1] > last3.iloc[-2] > last3.iloc[-3]:   factor *= 1.15; signals.append("✅ 1m: Momentum bullish")
        elif last3.iloc[-1] > last3.iloc[-2]:                   factor *= 1.08; signals.append("🔵 1m: Letzte Kerze bullish")
        else:                                                    factor *= 0.90; signals.append("⚠️ 1m: Kein Momentum")
        if rsi < 25: factor *= 1.15; signals.append("✅ 1m: RSI extrem " + str(round(rsi,1)))
    else:
        if last3.iloc[-1] < last3.iloc[-2] < last3.iloc[-3]:   factor *= 1.15; signals.append("✅ 1m: Momentum bearish")
        elif last3.iloc[-1] < last3.iloc[-2]:                   factor *= 1.08; signals.append("🔵 1m: Letzte Kerze bearish")
        else:                                                    factor *= 0.90; signals.append("⚠️ 1m: Kein Momentum")
        if rsi > 75: factor *= 1.15; signals.append("✅ 1m: RSI extrem " + str(round(rsi,1)))
    return factor, " | ".join(signals)

def calc_stops(direction, price, atr):
    atr = max(atr, price * 0.004)
    if direction == "LONG":
        sl = round(price - atr*1.5,3); t1 = round(price + atr*1.5,3)
        t2 = round(price + atr*3.0,3); t3 = round(price + atr*7.0,3)
        t1_be = round(price + atr*1.8,3)
    else:
        sl = round(price + atr*1.5,3); t1 = round(price - atr*1.5,3)
        t2 = round(price - atr*3.0,3); t3 = round(price - atr*7.0,3)
        t1_be = round(price - atr*1.8,3)
    risk = abs(price - sl)
    return {"sl": sl, "t1": t1, "t2": t2, "t3": t3, "t1_be": t1_be,
            "rr1": round(abs(price-t1)/risk,1), "rr2": round(abs(price-t2)/risk,1),
            "rr3": round(abs(price-t3)/risk,1), "atr": round(atr,3)}

def check_active_trades():
    global active_trades
    if not active_trades: return
    price = get_price()
    if price == 0: return
    changed = False; completed = []
    for t in active_trades:
        d = t["direction"]; entry = t["entry"]; atr_e = t["atr"]
        if d == "LONG":
            if price <= t["sl"]:
                result = "BE" if t["sl"] >= entry else "SL"
                pnl = round((t["sl"]-entry)/entry*100,2)
                icon = "➡️" if result == "BE" else "🔴"
                send_telegram(icon + " <b>" + result + "!</b>\nSOL LONG | Entry: $" + str(entry) + " | Exit: $" + str(t["sl"]) + " | PnL: " + str(pnl) + "%")
                completed.append(t); changed = True
            elif price >= t["t3"]:
                pnl = round((t["t3"]-entry)/entry*100,2)
                send_telegram("🚀 <b>T3! +" + str(pnl) + "%</b>\nSOL LONG | T3: $" + str(t["t3"]))
                completed.append(t); changed = True
            elif price >= t["t2"] and not t.get("t2_hit"):
                t["t2_hit"] = True; t["sl"] = t["t1"]
                send_telegram("🎯 <b>T2!</b> SL auf $" + str(t["t1"]) + "\nSOL LONG"); changed = True
            elif price >= t["t1_be"] and not t.get("t1_hit"):
                t["t1_hit"] = True; be = round(entry + atr_e*0.15,3); t["sl"] = be
                send_telegram("🎯 <b>T1!</b> SL auf BE $" + str(be) + "\nSOL LONG"); changed = True
        else:
            if price >= t["sl"]:
                result = "BE" if t["sl"] <= entry else "SL"
                pnl = round((entry-t["sl"])/entry*100,2)
                icon = "➡️" if result == "BE" else "🔴"
                send_telegram(icon + " <b>" + result + "!</b>\nSOL SHORT | Entry: $" + str(entry) + " | Exit: $" + str(t["sl"]) + " | PnL: " + str(pnl) + "%")
                completed.append(t); changed = True
            elif price <= t["t3"]:
                pnl = round((entry-t["t3"])/entry*100,2)
                send_telegram("🚀 <b>T3! +" + str(pnl) + "%</b>\nSOL SHORT | T3: $" + str(t["t3"]))
                completed.append(t); changed = True
            elif price <= t["t2"] and not t.get("t2_hit"):
                t["t2_hit"] = True; t["sl"] = t["t1"]
                send_telegram("🎯 <b>T2!</b> SL auf $" + str(t["t1"]) + "\nSOL SHORT"); changed = True
            elif price <= t["t1_be"] and not t.get("t1_hit"):
                t["t1_hit"] = True; be = round(entry - atr_e*0.15,3); t["sl"] = be
                send_telegram("🎯 <b>T1!</b> SL auf BE $" + str(be) + "\nSOL SHORT"); changed = True
    active_trades = [t for t in active_trades if t not in completed]
    if changed: save_active_trades(active_trades)

# ═══════════════════════════════════════════════════════════
# HAUPT-ANALYSE V13 – MIT ORDER FLOW
# ═══════════════════════════════════════════════════════════

def analyze():
    price = get_price()
    if price == 0: return None

    session_id, session_name, session_factor = get_session()

    trend_1h, factor_1h, sig_1h = analyze_1h()
    if trend_1h == "NEUTRAL": return None

    direction = "LONG" if trend_1h == "BULLISH" else "SHORT"

    market_mode, factor_15m, atr, sig_15m = analyze_15m(direction)
    if market_mode is None: return None

    factor_30m, sig_30m = analyze_30m(direction)

    trend_4h, _, sig_4h = analyze_4h()
    if trend_4h == "BULLISH" and direction == "LONG":   factor_4h = 1.15
    elif trend_4h == "BEARISH" and direction == "SHORT": factor_4h = 1.15
    elif trend_4h == "NEUTRAL":                          factor_4h = 1.00
    else:                                                factor_4h = 0.75

    factor_5m, sig_5m = analyze_5m(direction)
    factor_1m, sig_1m = analyze_1m(direction)

    # ── V13: Drei neue Order-Flow Faktoren ──
    factor_delta,  sig_delta  = get_delta_factor(direction)
    factor_absorb, sig_absorb = get_absorption_factor(direction)
    factor_imbal,  sig_imbal  = get_volume_imbalance_factor(direction)

    # Score mit allen Faktoren inkl. Order Flow
    score = calc_prob_score([
        factor_4h, factor_1h, factor_30m, factor_15m,
        factor_5m, factor_1m,
        factor_delta, factor_absorb, factor_imbal,   # ← NEU V13
        session_factor
    ])

    if score < MIN_SCORE: return None

    funding = get_funding()
    stops   = calc_stops(direction, price, atr)

    return {
        "score": score, "price": price,
        "direction": direction, "trend_1h": trend_1h,
        "market_mode": market_mode, "session": session_name,
        "stops": stops, "funding": funding,
        "entry_low":  round(price * 0.999, 3),
        "entry_high": round(price * 1.001, 3),
        "trend_4h": trend_4h,
        "sig_4h": sig_4h, "sig_1h": sig_1h, "sig_30m": sig_30m,
        "sig_15m": sig_15m, "sig_5m": sig_5m, "sig_1m": sig_1m,
        "sig_delta": sig_delta, "sig_absorb": sig_absorb, "sig_imbal": sig_imbal
    }

def format_alert(data):
    s = data["stops"]; d = data["direction"]
    arrow = "📈 LONG" if d == "LONG" else "📉 SHORT"
    grade = "A+++" if data["score"] >= 85 else "A++"
    trend_4h = data.get("trend_4h", "NEUTRAL")
    if (trend_4h == "BULLISH" and d == "SHORT") or (trend_4h == "BEARISH" and d == "LONG"):
        warn_4h = "⚠️ <b>VORSICHT: 4h Trend ist " + trend_4h + " – Gegentrade!</b>"
    elif trend_4h == "NEUTRAL":
        warn_4h = "⚠️ 4h: Neutral – kein klarer Trend"
    else:
        warn_4h = "✅ 4h Trend aligned – " + trend_4h

    # Order Flow Zeilen nur wenn Inhalt vorhanden
    of_lines = []
    if data.get("sig_delta"):  of_lines.append(data["sig_delta"])
    if data.get("sig_absorb"): of_lines.append(data["sig_absorb"])
    if data.get("sig_imbal"):  of_lines.append(data["sig_imbal"])

    lines = [
        "——————————————————",
        "💰 <b>SOL/USDT</b>  " + arrow,
        "📊 <b>Score: " + str(data["score"]) + "% (" + grade + ")</b>",
        "💥 " + data["market_mode"],
        "🕐 " + data["session"],
        "——————————————————",
        "📍 <b>Entry:</b> $" + str(data["entry_low"]) + " – $" + str(data["entry_high"]),
        "🔴 <b>SL:</b> $" + str(s["sl"]) + "  <i>(ATR: " + str(s["atr"]) + ")</i>",
        "——————————————————",
        "🎯 <b>T1:</b> $" + str(s["t1"]) + "  (RR 1:" + str(s["rr1"]) + ") → BE!",
        "🎯 <b>T2:</b> $" + str(s["t2"]) + "  (RR 1:" + str(s["rr2"]) + ") → SL auf T1",
        "🚀 <b>T3:</b> $" + str(s["t3"]) + "  (RR 1:" + str(s["rr3"]) + ") → Volltreffer!",
        "——————————————————",
        warn_4h,
        "——————————————————",
        "<b>Top-Down Analyse:</b>",
        data["sig_4h"], data["sig_1h"], data["sig_30m"],
        data["sig_15m"], data["sig_5m"], data["sig_1m"],
    ]
    if of_lines:
        lines.append("——————————————————")
        lines.append("📊 <b>Order Flow V13:</b>")
        lines.extend(of_lines)
    lines += [
        "🔵 Funding: " + str(round(data["funding"],3)) + "%",
        "——————————————————",
        "⚠️ <i>Kein Auto-Trade – du entscheidest!</i>"
    ]
    return "\n".join(lines)

def main():
    global last_alert, active_trades
    print("=" * 55)
    print("   SOL A+++ Scanner V13 – Order Flow Edition")
    print("   ⚠️  Stufe 1: Warning (0.8% vor Breakout)")
    print("   💥  Stufe 2: Breakout Signal")
    print("   🔄  Stufe 3: Pullback – unabhängig!")
    print("   📊  NEU: Delta + Absorption + Volume Imbalance")
    print("   Min Score: 75%")
    print("=" * 55)

    if active_trades:
        print("♻️ " + str(len(active_trades)) + " Trade(s) nach Restart wiederhergestellt!")
        send_telegram("♻️ <b>Bot Restart – " + str(len(active_trades)) + " Trade(s) wiederhergestellt!</b>\nSL/T1/T2/T3 Tracking läuft weiter.")
    else:
        send_telegram(
            "🚀 <b>SOL Scanner V13 – Order Flow Edition!</b>\n\n"
            "🆕 <b>Drei neue Order Flow Faktoren:</b>\n"
            "📊 Delta → echter Score-Faktor\n"
            "🧱 Absorption Detection\n"
            "⚖️ Volume Imbalance (15m)\n\n"
            "📊 <b>System:</b>\n"
            "• 4h | 1h | 30m | 15m | 5m | 1m\n"
            "• 24/7 aktiv – kein Session-Filter\n"
            "• LONG + SHORT | Nur BREAKOUT\n"
            "• Min Score: 75%\n\nWarte auf Setup..."
        )

    print("Startup: 3 Minuten warten...")
    time.sleep(180)
    print("Bereit!")

    last_scan = 0; last_check = 0; last_warn_scan = 0

    while True:
        now = time.time()

        if now - last_check >= 30:
            last_check = now
            check_active_trades()

        if now - last_warn_scan >= 30:
            last_warn_scan = now
            session_id, session_name, _ = get_session()
            trend_1h, _, _ = analyze_1h()
            if trend_1h != "NEUTRAL":
                direction = "LONG" if trend_1h == "BULLISH" else "SHORT"
                warning = check_prebreakout_warning(direction, session_name)
                if warning and (now - last_warning) > 1800:
                    sent = send_telegram(format_warning(warning))
                    if sent:
                        last_warning = now; save_last_warning(now)
                        print("[WARNING] Pre-Breakout Alert! " + direction)
                pullback = check_pullback(direction)
                if pullback and (now - last_alert) > COOLDOWN:
                    pb_msg  = format_pullback(pullback, session_name)
                    pb_sent = send_telegram(pb_msg)
                    if pb_sent:
                        last_alert = now; save_last_alert(now)
                        atr = pullback["atr"]; p = pullback["price"]
                        s = {
                            "sl":    round(p - atr*1.5,3) if direction == "LONG" else round(p + atr*1.5,3),
                            "t1":    round(p + atr*1.5,3) if direction == "LONG" else round(p - atr*1.5,3),
                            "t1_be": round(p + atr*1.8,3) if direction == "LONG" else round(p - atr*1.8,3),
                            "t2":    round(p + atr*3.0,3) if direction == "LONG" else round(p - atr*3.0,3),
                            "t3":    round(p + atr*7.0,3) if direction == "LONG" else round(p - atr*7.0,3),
                            "atr":   atr
                        }
                        active_trades.append({"direction": direction, "entry": p,
                            "sl": s["sl"], "orig_sl": s["sl"], "t1": s["t1"], "t1_be": s["t1_be"],
                            "t2": s["t2"], "t3": s["t3"], "atr": s["atr"], "t1_hit": False, "t2_hit": False})
                        save_active_trades(active_trades)
                        print("[PULLBACK] Signal gesendet & Trade getrackt!")

        if now - last_scan >= SCAN_INTERVAL:
            last_scan = now
            ts = datetime.now().strftime("%H:%M:%S")
            result = analyze()
            if result:
                s = result["stops"]
                print("[" + ts + "] SIGNAL! " + result["direction"] + " | Score: " + str(result["score"]) + "% | " + result["market_mode"])
                if (now - last_alert) > COOLDOWN:
                    alert_sent = send_telegram(format_alert(result))
                    if alert_sent:
                        last_alert = now; save_last_alert(now)
                        last_breakout = {"level": result["price"], "direction": result["direction"], "ts": now}
                        save_last_breakout(result["price"], result["direction"], now)
                        active_trades.append({"direction": result["direction"], "entry": result["price"],
                            "sl": s["sl"], "orig_sl": s["sl"], "t1": s["t1"], "t1_be": s["t1_be"],
                            "t2": s["t2"], "t3": s["t3"], "atr": s["atr"], "t1_hit": False, "t2_hit": False})
                        save_active_trades(active_trades)
                        print("  Trade getrackt & gespeichert!")
                    else:
                        print("  Trade NICHT getrackt – Alert fehlgeschlagen!")
                else:
                    remaining = round((COOLDOWN-(now-last_alert))/60)
                    print("  Cooldown: " + str(remaining) + " min")
            else:
                print("[" + ts + "] Kein Setup")

        time.sleep(10)

if __name__ == "__main__":
    main()
