import requests
import time
import pandas as pd
import os
import json
from datetime import datetime

COOLDOWN_FILE = "/tmp/bot_last_alert.json"
TRADES_FILE   = "/tmp/bot_active_trades.json"
WARNING_FILE  = "/tmp/bot_last_warning.json"

def save_last_alert(ts):
    try:
        with open(COOLDOWN_FILE, "w") as f: json.dump({"last_alert": ts}, f)
    except: pass

def load_last_alert():
    try:
        if os.path.exists(COOLDOWN_FILE):
            with open(COOLDOWN_FILE) as f: return json.load(f).get("last_alert", 0)
    except: pass
    return 0

def save_active_trades(trades):
    try:
        with open(TRADES_FILE, "w") as f: json.dump(trades, f)
    except: pass

def load_active_trades():
    try:
        if os.path.exists(TRADES_FILE):
            with open(TRADES_FILE) as f: return json.load(f)
    except: pass
    return []

def save_last_warning(ts):
    try:
        with open(WARNING_FILE, "w") as f: json.dump({"last_warning": ts}, f)
    except: pass

def load_last_warning():
    try:
        if os.path.exists(WARNING_FILE):
            with open(WARNING_FILE) as f: return json.load(f).get("last_warning", 0)
    except: pass
    return 0

# CONFIG
TELEGRAM_TOKEN   = "8678164580:AAEmznr79S6qO-NDqHkx8gOM-IqpyA884MQ"
TELEGRAM_CHAT_ID = "2050191721"
SYMBOL           = "SOL-USDT"
SCAN_INTERVAL    = 30   # Alle 30 Sek scannen (war 60)
COOLDOWN         = 3600
MIN_SCORE        = 70   # Hard Filter muss passen, Score für Qualität

last_alert    = load_last_alert()
last_warning  = load_last_warning()
active_trades = load_active_trades()

# ─── TELEGRAM ──────────────────────────────────────────────

def send_telegram(msg):
    url = "https://api.telegram.org/bot" + TELEGRAM_TOKEN + "/sendMessage"
    for attempt in range(3):
        try:
            r = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"}, timeout=10)
            if r.status_code == 200:
                print("[Telegram] OK"); return True
            time.sleep(2)
        except Exception as e:
            print(f"[Telegram Fehler] {e}"); time.sleep(2)
    return False

# ─── OKX DATA ──────────────────────────────────────────────

def get_candles(symbol, bar, limit=100):
    try:
        r = requests.get("https://www.okx.com/api/v5/market/candles",
            params={"instId": symbol, "bar": bar, "limit": limit}, timeout=10)
        data = r.json().get("data", [])
        if not data: return None
        df = pd.DataFrame(data, columns=["ts","open","high","low","close","vol","v1","v2","c"])
        for col in ["open","high","low","close","vol"]: df[col] = pd.to_numeric(df[col])
        df["ts"] = pd.to_numeric(df["ts"])
        return df.iloc[::-1].reset_index(drop=True)
    except Exception as e:
        print(f"[Fehler] Candles {bar}: {e}"); return None

def get_taker_volume_raw():
    """Gibt rohe Buy/Sell Volumen zurück für Delta-Berechnung"""
    try:
        r = requests.get("https://www.okx.com/api/v5/rubik/stat/taker-volume",
            params={"instId": SYMBOL, "instType": "SPOT", "period": "5m", "limit": 10}, timeout=10)
        data = r.json().get("data", [])
        if not data: return 0, 0, 0
        df = pd.DataFrame(data, columns=["ts","sellVol","buyVol"])
        df["buyVol"]  = pd.to_numeric(df["buyVol"])
        df["sellVol"] = pd.to_numeric(df["sellVol"])
        total_buy  = df["buyVol"].sum()
        total_sell = df["sellVol"].sum()
        avg_vol    = (df["buyVol"] + df["sellVol"]).mean()
        return total_buy - total_sell, total_buy, total_sell
    except:
        return 0, 0, 0

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

# ─── INDIKATOREN ───────────────────────────────────────────

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

# ═══════════════════════════════════════════════════════════
# V14 KERN: SMART MONEY ARCHITEKTUR
#
# STUFE 1 – HARTE PFLICHTFILTER (alle müssen passen)
# STUFE 2 – SOFT SCORE (Qualitäts-Boost)
# Philosophie: Weniger Signale, jedes mit Smart Money dahinter
# ═══════════════════════════════════════════════════════════

def get_trend(df, label=""):
    """Trend-Analyse: Bullish / Bearish / Neutral"""
    if df is None or len(df) < 30:
        return "NEUTRAL", f"⚠️ {label}: Keine Daten"
    ema21 = calc_ema(df["close"], 21).iloc[-1]
    ema50 = calc_ema(df["close"], 50).iloc[-1]
    price = df["close"].iloc[-1]
    highs = df["high"].tail(10); lows = df["low"].tail(10)
    hh_hl = highs.iloc[-1] > highs.iloc[0] and lows.iloc[-1] > lows.iloc[0]
    lh_ll = highs.iloc[-1] < highs.iloc[0] and lows.iloc[-1] < lows.iloc[0]
    bull  = sum([price > ema21, ema21 > ema50, hh_hl])
    bear  = sum([price < ema21, ema21 < ema50, lh_ll])
    if bull >= 2: return "BULLISH", f"✅ {label}: BULLISCH ({bull}/3)"
    if bear >= 2: return "BEARISH", f"✅ {label}: BAERISCH ({bear}/3)"
    return "NEUTRAL", f"⚠️ {label}: NEUTRAL"


def detect_smart_money(df15, df5):
    """
    ═══════════════════════════════════════
    SMART MONEY DETECTION (Wyckoff-Logik)
    ═══════════════════════════════════════
    
    Signatur: Stille Akkumulation → Volumen-Explosion → Ausbruch
    
    Erkennt:
    1. Enge Konsolidierung (Smart Money sammelt heimlich)
    2. ATR-Expansion (Markt erwacht)
    3. Volumen-Explosion (Institutionelle treten aufs Gas)
    4. Kerzenmuster bestätigt Richtung
    """
    if df15 is None or len(df15) < 20:
        return None, []
    
    logs = []
    last = df15.iloc[-1]
    price = last["close"]
    
    # ── FILTER 1: ATR-Expansion ───────────────────────────
    ranges = (df15["high"] - df15["low"]).tail(9)
    avg_range = ranges.iloc[:-1].mean()
    cur_range = ranges.iloc[-1]
    atr_expand = cur_range / (avg_range + 1e-10)
    
    if atr_expand < 1.4:
        return None, ["❌ ATR: Kein Ausbruch (" + str(round(atr_expand, 2)) + "x)"]
    logs.append("✅ ATR-Explosion: " + str(round(atr_expand, 2)) + "x")
    
    # ── FILTER 2: Stille Akkumulation davor ──────────────
    # Letzte 8 Kerzen VOR der aktuellen: enger Bereich!
    window = df15.tail(9).iloc[:-1]
    w_high = window["high"].max()
    w_low  = window["low"].min()
    w_range_pct = (w_high - w_low) / price * 100
    
    if w_range_pct > 2.5:
        return None, ["❌ Akkumulation: Zu viel Bewegung davor (" + str(round(w_range_pct, 2)) + "%)"]
    logs.append("✅ Akkumulation: " + str(round(w_range_pct, 2)) + "% Range (Still)")
    
    # ── FILTER 3: Volumen-Explosion ───────────────────────
    vol_avg = df15["vol"].tail(20).mean()
    vol_now = df15["vol"].iloc[-1]
    vol_ratio = vol_now / (vol_avg + 1e-10)
    
    if vol_ratio < 1.8:
        return None, ["❌ Volumen: Zu schwach (" + str(round(vol_ratio, 2)) + "x)"]
    logs.append("✅ Volumen-Explosion: " + str(round(vol_ratio, 2)) + "x")
    
    # ── FILTER 4: Kerzenmuster = Richtung ─────────────────
    is_bull_candle = last["close"] > last["open"]
    candle_body = abs(last["close"] - last["open"])
    candle_total = cur_range
    body_pct = candle_body / (candle_total + 1e-10)
    
    if body_pct < 0.3:
        return None, ["❌ Kerze: Zu viel Docht, kein klares Signal (" + str(round(body_pct*100)) + "% Body)"]
    
    direction = "LONG" if is_bull_candle else "SHORT"
    arrow = "📈 LONG" if direction == "LONG" else "📉 SHORT"
    logs.append("✅ Kerze " + arrow + " (" + str(round(body_pct*100)) + "% Body)")
    
    # ── FILTER 5: Delta nicht gegen uns ───────────────────
    if df5 is not None and len(df5) >= 8:
        s5 = df5.tail(8)
        delta = 0.0
        for _, row in s5.iterrows():
            rng = row["high"] - row["low"]
            if rng == 0: continue
            cp = (row["close"] - row["low"]) / rng
            delta += row["vol"] * (cp - (1 - cp))
        
        delta_threshold = vol_avg * 0.3
        if direction == "LONG" and delta < -delta_threshold:
            return None, ["❌ Delta: Gegen LONG (" + str(round(delta, 0)) + ")"]
        if direction == "SHORT" and delta > delta_threshold:
            return None, ["❌ Delta: Gegen SHORT (+" + str(round(delta, 0)) + ")"]
        
        delta_str = ("+" if delta >= 0 else "") + str(round(delta, 0))
        logs.append("✅ Delta: " + delta_str + " (aligned)")
    
    return {
        "direction":   direction,
        "price":       round(price, 3),
        "atr_expand":  round(atr_expand, 2),
        "vol_ratio":   round(vol_ratio, 2),
        "w_range_pct": round(w_range_pct, 2),
        "body_pct":    round(body_pct * 100),
        "atr_val":     calc_atr(df15).iloc[-1]
    }, logs


def calc_quality_score(sm_data, direction, df15, df1h, df30, df5, session_factor):
    """
    SOFT SCORE: Nur für Qualitäts-Boost NACH bestandenen Hard Filters.
    Basis: 50 Punkte (Hard Filter bestanden)
    Max: 100 Punkte
    """
    score = 50
    sig_lines = []

    # Volumen-Stärke
    vr = sm_data["vol_ratio"]
    if vr > 5.0:   score += 15; sig_lines.append("🔥 Volumen extrem stark (" + str(round(vr,1)) + "x)")
    elif vr > 3.5: score += 10; sig_lines.append("✅ Volumen sehr stark (" + str(round(vr,1)) + "x)")
    elif vr > 2.5: score += 7;  sig_lines.append("✅ Volumen stark (" + str(round(vr,1)) + "x)")
    else:          score += 3;  sig_lines.append("🔵 Volumen ok (" + str(round(vr,1)) + "x)")

    # ATR-Expansion
    ae = sm_data["atr_expand"]
    if ae > 3.0:   score += 10; sig_lines.append("🔥 ATR extrem (" + str(round(ae,1)) + "x)")
    elif ae > 2.0: score += 7;  sig_lines.append("✅ ATR stark (" + str(round(ae,1)) + "x)")
    else:          score += 3;  sig_lines.append("🔵 ATR ok (" + str(round(ae,1)) + "x)")

    # Enge Akkumulation (je enger, desto besser)
    wr = sm_data["w_range_pct"]
    if wr < 0.8:   score += 10; sig_lines.append("✅ Sehr enge Akkumulation (" + str(wr) + "%)")
    elif wr < 1.5: score += 5;  sig_lines.append("✅ Enge Akkumulation (" + str(wr) + "%)")
    else:          score += 2;  sig_lines.append("🔵 Akkumulation " + str(wr) + "%")

    # 30m Struktur
    if df30 is not None and len(df30) >= 20:
        e21 = calc_ema(df30["close"], 21).iloc[-1]
        e50 = calc_ema(df30["close"], 50).iloc[-1]
        p   = df30["close"].iloc[-1]
        if direction == "LONG" and p > e21 > e50:
            score += 5; sig_lines.append("✅ 30m: EMA bullish")
        elif direction == "SHORT" and p < e21 < e50:
            score += 5; sig_lines.append("✅ 30m: EMA bearish")
        else:
            sig_lines.append("⚠️ 30m: EMA neutral")

    # 5m RSI Timing
    if df5 is not None and len(df5) >= 15:
        rsi = calc_rsi(df5["close"]).iloc[-1]
        if direction == "LONG":
            if rsi < 45:   score += 5; sig_lines.append("✅ 5m RSI: " + str(round(rsi,1)) + " (gut)")
            elif rsi > 65: score -= 5; sig_lines.append("⚠️ 5m RSI: " + str(round(rsi,1)) + " (hoch)")
            else:          sig_lines.append("🔵 5m RSI: " + str(round(rsi,1)))
        else:
            if rsi > 55:   score += 5; sig_lines.append("✅ 5m RSI: " + str(round(rsi,1)) + " (gut)")
            elif rsi < 35: score -= 5; sig_lines.append("⚠️ 5m RSI: " + str(round(rsi,1)) + " (niedrig)")
            else:          sig_lines.append("🔵 5m RSI: " + str(round(rsi,1)))

    # Session-Bonus
    if session_factor >= 1.30:
        score += 5; sig_lines.append("🔥 London/NY Overlap!")
    elif session_factor >= 1.20:
        score += 3; sig_lines.append("✅ London/NY Session")

    return min(100, score), sig_lines


def get_session():
    hour = datetime.now().hour
    if 14 <= hour < 17:   return "OVERLAP",   "London/NY Overlap 🔥", 1.30
    elif 8 <= hour < 18:  return "LONDON_NY", "London/NY Session",    1.20
    elif 0 <= hour < 8:   return "ASIA",      "Asia Session",          1.00
    else:                  return "EVENING",   "Evening Session",       1.00


def calc_stops(direction, price, atr):
    atr = max(atr, price * 0.004)
    if direction == "LONG":
        sl = round(price - atr*1.5, 3); t1 = round(price + atr*1.5, 3)
        t2 = round(price + atr*3.0, 3); t3 = round(price + atr*7.0, 3)
        t1_be = round(price + atr*1.8, 3)
    else:
        sl = round(price + atr*1.5, 3); t1 = round(price - atr*1.5, 3)
        t2 = round(price - atr*3.0, 3); t3 = round(price - atr*7.0, 3)
        t1_be = round(price - atr*1.8, 3)
    risk = abs(price - sl)
    return {
        "sl": sl, "t1": t1, "t2": t2, "t3": t3, "t1_be": t1_be,
        "rr1": round(abs(price-t1)/risk, 1),
        "rr2": round(abs(price-t2)/risk, 1),
        "rr3": round(abs(price-t3)/risk, 1),
        "atr": round(atr, 3)
    }

# ─── TRADE TRACKING ────────────────────────────────────────

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
                pnl = round((t["sl"]-entry)/entry*100, 2)
                icon = "➡️" if result == "BE" else "🔴"
                send_telegram(icon + " <b>" + result + "!</b>\nSOL LONG | Entry: $" + str(entry) + " | Exit: $" + str(t["sl"]) + " | PnL: " + str(pnl) + "%")
                completed.append(t); changed = True
            elif price >= t["t3"]:
                pnl = round((t["t3"]-entry)/entry*100, 2)
                send_telegram("🚀 <b>T3! +" + str(pnl) + "%</b>\nSOL LONG | T3: $" + str(t["t3"]))
                completed.append(t); changed = True
            elif price >= t["t2"] and not t.get("t2_hit"):
                t["t2_hit"] = True; t["sl"] = t["t1"]
                send_telegram("🎯 <b>T2!</b> SL auf $" + str(t["t1"]) + "\nSOL LONG"); changed = True
            elif price >= t["t1_be"] and not t.get("t1_hit"):
                t["t1_hit"] = True; be = round(entry + atr_e*0.15, 3); t["sl"] = be
                send_telegram("🎯 <b>T1!</b> SL auf BE $" + str(be) + "\nSOL LONG"); changed = True
        else:
            if price >= t["sl"]:
                result = "BE" if t["sl"] <= entry else "SL"
                pnl = round((entry-t["sl"])/entry*100, 2)
                icon = "➡️" if result == "BE" else "🔴"
                send_telegram(icon + " <b>" + result + "!</b>\nSOL SHORT | Entry: $" + str(entry) + " | Exit: $" + str(t["sl"]) + " | PnL: " + str(pnl) + "%")
                completed.append(t); changed = True
            elif price <= t["t3"]:
                pnl = round((entry-t["t3"])/entry*100, 2)
                send_telegram("🚀 <b>T3! +" + str(pnl) + "%</b>\nSOL SHORT | T3: $" + str(t["t3"]))
                completed.append(t); changed = True
            elif price <= t["t2"] and not t.get("t2_hit"):
                t["t2_hit"] = True; t["sl"] = t["t1"]
                send_telegram("🎯 <b>T2!</b> SL auf $" + str(t["t1"]) + "\nSOL SHORT"); changed = True
            elif price <= t["t1_be"] and not t.get("t1_hit"):
                t["t1_hit"] = True; be = round(entry - atr_e*0.15, 3); t["sl"] = be
                send_telegram("🎯 <b>T1!</b> SL auf BE $" + str(be) + "\nSOL SHORT"); changed = True
    active_trades = [t for t in active_trades if t not in completed]
    if changed: save_active_trades(active_trades)

# ─── HAUPT-ANALYSE V14 ─────────────────────────────────────

def analyze():
    # Daten laden
    df15 = get_candles(SYMBOL, "15m", 60)
    df1h = get_candles(SYMBOL, "1H",  60)
    df4h = get_candles(SYMBOL, "4H",  60)
    df30 = get_candles(SYMBOL, "30m", 60)
    df5  = get_candles(SYMBOL, "5m",  60)

    if df15 is None or df1h is None:
        return None

    # ══ HARD FILTER 1: 4h UND 1h müssen aligned sein ══
    trend_4h, sig_4h = get_trend(df4h, "4h")
    trend_1h, sig_1h = get_trend(df1h, "1h")

    if trend_4h == "NEUTRAL" or trend_1h == "NEUTRAL":
        return None
    if trend_4h != trend_1h:
        # Contra-Trade: warnen aber KEIN Signal!
        return None

    direction = "LONG" if trend_4h == "BULLISH" else "SHORT"

    # ══ HARD FILTERS 2-6: Smart Money Detection ══
    sm_data, sm_logs = detect_smart_money(df15, df5)
    if sm_data is None:
        return None  # Hard Filter nicht bestanden

    # Richtung aus Smart Money muss mit Trend übereinstimmen
    if sm_data["direction"] != direction:
        return None  # Kerze zeigt gegen Trend

    # ══ SOFT SCORE: Qualitäts-Bewertung ══
    session_id, session_name, session_factor = get_session()
    score, score_logs = calc_quality_score(
        sm_data, direction, df15, df1h, df30, df5, session_factor
    )

    if score < MIN_SCORE:
        return None

    funding = get_funding()
    price   = sm_data["price"]
    atr     = max(sm_data["atr_val"], price * 0.004)
    stops   = calc_stops(direction, price, atr)

    grade = "A+++" if score >= 85 else ("A++" if score >= 75 else "A+")

    return {
        "score": score, "grade": grade,
        "price": price, "direction": direction,
        "session": session_name,
        "stops": stops, "funding": funding,
        "entry_low":  round(price * 0.999, 3),
        "entry_high": round(price * 1.001, 3),
        "sm_data": sm_data,
        "sm_logs": sm_logs,
        "score_logs": score_logs,
        "sig_4h": sig_4h, "sig_1h": sig_1h,
        "trend_4h": trend_4h
    }

# ─── FORMAT ALERT ──────────────────────────────────────────

def format_alert(data):
    s     = data["stops"]
    d     = data["direction"]
    arrow = "📈 LONG" if d == "LONG" else "📉 SHORT"
    sm    = data["sm_data"]

    lines = [
        "——————————————————",
        "💰 <b>SOL/USDT</b>  " + arrow,
        "🧠 <b>Smart Money Signal V14</b>",
        "📊 <b>Score: " + str(data["score"]) + "% (" + data["grade"] + ")</b>",
        "🕐 " + data["session"],
        "——————————————————",
        "📍 <b>Entry:</b> $" + str(data["entry_low"]) + " – $" + str(data["entry_high"]),
        "🔴 <b>SL:</b> $" + str(s["sl"]) + "  <i>(ATR: " + str(s["atr"]) + ")</i>",
        "——————————————————",
        "🎯 <b>T1:</b> $" + str(s["t1"]) + "  (RR 1:" + str(s["rr1"]) + ") → BE!",
        "🎯 <b>T2:</b> $" + str(s["t2"]) + "  (RR 1:" + str(s["rr2"]) + ") → SL auf T1",
        "🚀 <b>T3:</b> $" + str(s["t3"]) + "  (RR 1:" + str(s["rr3"]) + ") → Volltreffer!",
        "——————————————————",
        "✅ 4h + 1h aligned: " + data["trend_4h"],
        "——————————————————",
        "<b>🧠 Smart Money Bestätigung:</b>",
    ]

    # Smart Money Logs
    for log in data["sm_logs"]:
        lines.append(log)

    lines.append("——————————————————")
    lines.append("<b>📊 Qualitäts-Score Faktoren:</b>")

    for log in data["score_logs"]:
        lines.append(log)

    lines += [
        "——————————————————",
        data["sig_4h"],
        data["sig_1h"],
        "🔵 Funding: " + str(round(data["funding"], 3)) + "%",
        "——————————————————",
        "⚠️ <i>Kein Auto-Trade – du entscheidest!</i>"
    ]
    return "\n".join(lines)

# ─── PULLBACK DETECTION (vereinfacht, V13-bewährt) ─────────

def check_pullback(direction):
    df5  = get_candles(SYMBOL, "5m",  60)
    df15 = get_candles(SYMBOL, "15m", 60)
    if df5 is None or df15 is None or len(df5) < 20:
        return None
    price     = df5["close"].iloc[-1]
    atr       = calc_atr(df5).iloc[-1]
    rsi       = calc_rsi(df5["close"]).iloc[-1]
    ema9      = calc_ema(df5["close"], 9).iloc[-1]
    ema21_15m = calc_ema(df15["close"], 21).iloc[-1]
    tolerance = atr * 0.6

    # Strukturelle 15m Levels
    highs_15m = []; lows_15m = []
    for i in range(2, min(40, len(df15)-2)):
        if df15["high"].iloc[-i] > df15["high"].iloc[-i-1] and df15["high"].iloc[-i] > df15["high"].iloc[-i+1]:
            highs_15m.append(df15["high"].iloc[-i])
        if df15["low"].iloc[-i] < df15["low"].iloc[-i-1] and df15["low"].iloc[-i] < df15["low"].iloc[-i+1]:
            lows_15m.append(df15["low"].iloc[-i])

    if direction == "LONG":
        for level in highs_15m[:5]:
            if level < price * 0.999 and abs(price - level) <= tolerance:
                if rsi < 65 and price > ema9 and price > level:
                    last3 = df5["close"].tail(3)
                    if last3.iloc[-1] >= last3.min() * 0.998:
                        return {"direction":"LONG","level":round(level,3),"price":round(price,3),"atr":round(atr,3),"rsi":round(rsi,1),"type":"Ehem. Widerstand → Support"}
        if abs(price - ema21_15m) <= tolerance and price > ema21_15m and rsi < 60:
            return {"direction":"LONG","level":round(ema21_15m,3),"price":round(price,3),"atr":round(atr,3),"rsi":round(rsi,1),"type":"EMA21 (15m) Support"}
    else:
        for level in lows_15m[:5]:
            if level > price * 1.001 and abs(price - level) <= tolerance:
                if rsi > 35 and price < ema9 and price < level:
                    last3 = df5["close"].tail(3)
                    if last3.iloc[-1] <= last3.max() * 1.002:
                        return {"direction":"SHORT","level":round(level,3),"price":round(price,3),"atr":round(atr,3),"rsi":round(rsi,1),"type":"Ehem. Support → Widerstand"}
        if abs(price - ema21_15m) <= tolerance and price < ema21_15m and rsi > 40:
            return {"direction":"SHORT","level":round(ema21_15m,3),"price":round(price,3),"atr":round(atr,3),"rsi":round(rsi,1),"type":"EMA21 (15m) Widerstand"}
    return None

def format_pullback(pb, session_name):
    d = pb["direction"]; arrow = "📈 LONG" if d == "LONG" else "📉 SHORT"
    atr = pb["atr"]; price = pb["price"]
    if d == "LONG":
        sl = round(price-atr*1.5,3); t1 = round(price+atr*1.5,3)
        t2 = round(price+atr*3.0,3); t3 = round(price+atr*7.0,3)
    else:
        sl = round(price+atr*1.5,3); t1 = round(price-atr*1.5,3)
        t2 = round(price-atr*3.0,3); t3 = round(price-atr*7.0,3)
    risk = abs(price - sl)
    lines = [
        "——————————————————",
        "🔄 <b>PULLBACK ENTRY!</b>  " + arrow,
        "🕐 " + session_name,
        "——————————————————",
        "📍 Level: $" + str(pb["level"]) + "  <i>(" + pb.get("type","Retest") + ")</i>",
        "💰 Aktuell: $" + str(price),
        "📊 RSI: " + str(pb["rsi"]),
        "——————————————————",
        "🔴 <b>SL:</b> $" + str(sl),
        "🎯 <b>T1:</b> $" + str(t1) + "  (RR 1:" + str(round(abs(price-t1)/risk,1)) + ")",
        "🎯 <b>T2:</b> $" + str(t2) + "  (RR 1:" + str(round(abs(price-t2)/risk,1)) + ")",
        "🚀 <b>T3:</b> $" + str(t3) + "  (RR 1:" + str(round(abs(price-t3)/risk,1)) + ")",
        "——————————————————",
        "⚠️ <i>Kein Auto-Trade – du entscheidest!</i>"
    ]
    return "\n".join(lines)

# ─── MAIN ──────────────────────────────────────────────────

def main():
    global last_alert, last_warning, active_trades
    print("=" * 60)
    print("   SOL A+++ Scanner V14 – Smart Money Edition")
    print("   🧠 Wyckoff: Akkumulation → Explosion")
    print("   ✅ HARD FILTER: 4h + 1h aligned (KEIN Gegentrade!)")
    print("   💥 ATR-Explosion + Akkumulation + Volumen-Explosion")
    print("   📊 Qualitäts-Score ab 70%")
    print("=" * 60)

    if active_trades:
        print("♻️ " + str(len(active_trades)) + " Trade(s) wiederhergestellt!")
        send_telegram("♻️ <b>V14 Restart – " + str(len(active_trades)) + " Trade(s) wiederhergestellt!</b>")
    else:
        send_telegram(
            "🚀 <b>SOL Scanner V14 – Smart Money Edition!</b>\n\n"
            "🧠 <b>Neue Architektur:</b>\n"
            "✅ Hard Filter 1: 4h + 1h müssen aligned sein\n"
            "✅ Hard Filter 2: ATR-Explosion (Markt erwacht)\n"
            "✅ Hard Filter 3: Stille Akkumulation davor\n"
            "✅ Hard Filter 4: Volumen-Explosion ≥1.8x\n"
            "✅ Hard Filter 5: Kerzenrichtung bestätigt\n"
            "✅ Hard Filter 6: Delta nicht gegen uns\n\n"
            "📊 Qualitäts-Score: 70%+ für Signal\n"
            "🎯 Weniger Signale – jedes mit Smart Money\n\n"
            "Warte auf Setup... 👀"
        )

    print("Startup: 3 Minuten warten...")
    time.sleep(180)
    print("Bereit!")

    last_scan = 0; last_check = 0; last_pb_scan = 0

    while True:
        now = time.time()

        # Trade Tracking alle 30 Sek
        if now - last_check >= 30:
            last_check = now
            check_active_trades()

        # Pullback Check alle 30 Sek
        if now - last_pb_scan >= 30:
            last_pb_scan = now
            session_id, session_name, _ = get_session()
            df1h_pb = get_candles(SYMBOL, "1H", 50)
            if df1h_pb is not None:
                trend_pb, _ = get_trend(df1h_pb, "1h")
                if trend_pb != "NEUTRAL":
                    d_pb = "LONG" if trend_pb == "BULLISH" else "SHORT"
                    pullback = check_pullback(d_pb)
                    if pullback and (now - last_alert) > COOLDOWN:
                        pb_msg = format_pullback(pullback, session_name)
                        if send_telegram(pb_msg):
                            last_alert = now; save_last_alert(now)
                            atr = pullback["atr"]; p = pullback["price"]
                            s = {
                                "sl":    round(p-atr*1.5,3) if d_pb=="LONG" else round(p+atr*1.5,3),
                                "t1":    round(p+atr*1.5,3) if d_pb=="LONG" else round(p-atr*1.5,3),
                                "t1_be": round(p+atr*1.8,3) if d_pb=="LONG" else round(p-atr*1.8,3),
                                "t2":    round(p+atr*3.0,3) if d_pb=="LONG" else round(p-atr*3.0,3),
                                "t3":    round(p+atr*7.0,3) if d_pb=="LONG" else round(p-atr*7.0,3),
                                "atr":   atr
                            }
                            active_trades.append({"direction": d_pb, "entry": p,
                                "sl": s["sl"], "orig_sl": s["sl"], "t1": s["t1"], "t1_be": s["t1_be"],
                                "t2": s["t2"], "t3": s["t3"], "atr": s["atr"], "t1_hit": False, "t2_hit": False})
                            save_active_trades(active_trades)
                            print("[PULLBACK] Signal gesendet!")

        # Haupt-Scan
        if now - last_scan >= SCAN_INTERVAL:
            last_scan = now
            ts = datetime.now().strftime("%H:%M:%S")
            result = analyze()

            if result:
                s = result["stops"]
                print(f"[{ts}] SIGNAL! {result['direction']} | Score: {result['score']}% | ATR:{result['sm_data']['atr_expand']}x Vol:{result['sm_data']['vol_ratio']}x")
                if (now - last_alert) > COOLDOWN:
                    if send_telegram(format_alert(result)):
                        last_alert = now; save_last_alert(now)
                        active_trades.append({
                            "direction": result["direction"], "entry": result["price"],
                            "sl": s["sl"], "orig_sl": s["sl"], "t1": s["t1"], "t1_be": s["t1_be"],
                            "t2": s["t2"], "t3": s["t3"], "atr": s["atr"], "t1_hit": False, "t2_hit": False
                        })
                        save_active_trades(active_trades)
                        print("  Trade getrackt!")
                else:
                    remaining = round((COOLDOWN - (now - last_alert)) / 60)
                    print(f"  Cooldown: {remaining} min")
            else:
                print(f"[{ts}] Kein Setup")

        time.sleep(10)

if __name__ == "__main__":
    main()
