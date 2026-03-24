import requests
import time
import pandas as pd
import os
import json
from datetime import datetime

COOLDOWN_FILE = "/tmp/bot_last_alert.json"
TRADES_FILE   = "/tmp/bot_active_trades.json"

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

# CONFIG
TELEGRAM_TOKEN   = "8678164580:AAEmznr79S6qO-NDqHkx8gOM-IqpyA884MQ"
TELEGRAM_CHAT_ID = "2050191721"
SYMBOL           = "SOL-USDT"
SCAN_INTERVAL    = 30
COOLDOWN         = 3600

last_alert    = load_last_alert()
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

def get_funding():
    try:
        r = requests.get("https://www.okx.com/api/v5/public/funding-rate",
            params={"instId": "SOL-USDT-SWAP"}, timeout=10)
        return float(r.json().get("data",[{}])[0].get("fundingRate", 0)) * 100
    except: return 0.0

def get_price():
    try:
        r = requests.get("https://www.okx.com/api/v5/market/ticker",
            params={"instId": SYMBOL}, timeout=10)
        return float(r.json().get("data",[{}])[0].get("last", 0))
    except: return 0.0

def get_btc_candles(bar, limit=30):
    """BTC Daten für 5m Exit-Warnung und 1m Entry-Timing"""
    try:
        r = requests.get("https://www.okx.com/api/v5/market/candles",
            params={"instId": "BTC-USDT", "bar": bar, "limit": limit}, timeout=10)
        data = r.json().get("data", [])
        if not data: return None
        df = pd.DataFrame(data, columns=["ts","open","high","low","close","vol","v1","v2","c"])
        for col in ["open","high","low","close","vol"]: df[col] = pd.to_numeric(df[col])
        df["ts"] = pd.to_numeric(df["ts"])
        return df.iloc[::-1].reset_index(drop=True)
    except: return None


def get_btc1m_entry_timing(direction):
    """
    ════════════════════════════════════════════
    BTC 1m Entry-Timing
    ════════════════════════════════════════════
    Analysiert BTC 1m Kerzen um den OPTIMALEN
    Entry-Zeitpunkt innerhalb der Signal-Zone
    zu finden.

    LONG:
      BTC 1m grüne Kerze + Vol steigt → JETZT ✅
      BTC 1m rote Kerze               → warten ⏳
      BTC 1m 2x rot                   → vorsicht ⚠️

    SHORT:
      Spiegelverkehrt

    Gibt einen Hinweis-Text für den Alert zurück.
    ════════════════════════════════════════════
    """
    try:
        btc1m = get_btc_candles("1m", 5)
        if btc1m is None or len(btc1m) < 3:
            return "⚡ BTC 1m: keine Daten – Entry nach eigenem Urteil"

        last  = btc1m.iloc[-1]
        prev  = btc1m.iloc[-2]
        prev2 = btc1m.iloc[-3]

        is_bull_last  = last["close"] > last["open"]
        is_bull_prev  = prev["close"] > prev["open"]
        is_bull_prev2 = prev2["close"] > prev2["open"]

        vol_avg  = btc1m["vol"].mean()
        vol_last = last["vol"]
        vol_up   = vol_last > vol_avg * 1.1

        move_last = round((last["close"] - last["open"]) / last["open"] * 100, 2)

        if direction == "LONG":
            if is_bull_last and vol_up:
                return f"⚡ BTC 1m: Grüne Kerze + Vol ↑ → <b>JETZT einsteigen!</b> ✅"
            elif is_bull_last:
                return f"⚡ BTC 1m: Grüne Kerze ({move_last:+.2f}%) → Entry ok ✅"
            elif not is_bull_last and not is_bull_prev:
                return f"⚡ BTC 1m: 2x rote Kerzen ⚠️ → Warte auf grüne Kerze!"
            else:
                return f"⚡ BTC 1m: Rote Kerze ({move_last:+.2f}%) → 1 Min warten ⏳"
        else:  # SHORT
            if not is_bull_last and vol_up:
                return f"⚡ BTC 1m: Rote Kerze + Vol ↑ → <b>JETZT einsteigen!</b> ✅"
            elif not is_bull_last:
                return f"⚡ BTC 1m: Rote Kerze ({move_last:+.2f}%) → Entry ok ✅"
            elif is_bull_last and is_bull_prev:
                return f"⚡ BTC 1m: 2x grüne Kerzen ⚠️ → Warte auf rote Kerze!"
            else:
                return f"⚡ BTC 1m: Grüne Kerze ({move_last:+.2f}%) → 1 Min warten ⏳"
    except:
        return "⚡ BTC 1m: Timing nicht verfügbar"

# NEU V14.2: BTC 5m Exit-Warnung
last_btc_warning = 0   # damit wir nicht jede Sekunde warnen

def check_btc_exit_warning(active_trades):
    """
    V14.2: BTC 5m Exit-Warnung
    ════════════════════════════════════════════
    BTC läuft SOL um ~1-5 Minuten voraus.
    Wenn BTC in einer 5m Kerze stark dreht
    UND wir einen offenen Trade haben:
    → Sofort Telegram-Warnung senden
    → Du kannst früher entscheiden ob du rausgehst

    Beispiel heute Morgen:
    05:05 SOL LONG Signal
    05:05 BTC fällt bereits → Warnung wäre raus
    05:15 SOL SL gerissen
    ════════════════════════════════════════════
    """
    global last_btc_warning
    if not active_trades: return False

    btc5m = get_btc_candles("5m", 5)
    if btc5m is None or len(btc5m) < 3: return False

    last  = btc5m.iloc[-1]
    prev  = btc5m.iloc[-2]
    move  = (last["close"] - prev["close"]) / prev["close"] * 100
    btc_p = round(last["close"], 0)

    now = time.time()
    if now - last_btc_warning < 300:  # max 1 Warnung alle 5 Min
        return False

    for t in active_trades:
        d = t["direction"]
        triggered = (d == "LONG" and move <= -0.5) or (d == "SHORT" and move >= 0.5)
        if triggered:
            arrow   = "📈 LONG" if d == "LONG" else "📉 SHORT"
            btc_dir = "fällt" if move < 0 else "steigt"
            msg = (
                "——————————————————\n"
                f"⚡ <b>BTC EXIT-WARNUNG!</b>\n"
                f"Offener {arrow} auf SOL\n"
                "——————————————————\n"
                f"📉 BTC {btc_dir}: <b>{move:+.2f}%</b> in 5 Min\n"
                f"   BTC Preis: ${btc_p:,.0f}\n"
                "——————————————————\n"
                f"SOL Entry: ${t['entry']} | SL: ${t['sl']}\n"
                "——————————————————\n"
                "⚡ <b>SOL folgt BTC meist in 1-5 Min!</b>\n"
                "🎯 Du entscheidest: Halten oder früh raus?"
            )
            sent = send_telegram(msg)
            if sent:
                last_btc_warning = now
                print(f"[BTC WARNUNG] {d} Trade | BTC {move:+.2f}%")
            return sent
    return False

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

def calc_adx(df, period=14):
    """
    ADX = Average Directional Index
    Misst die STÄRKE eines Trends (nicht die Richtung)
    ADX < 20  = kein Trend / Seitwärts → KEIN SIGNAL
    ADX 20-25 = schwacher Trend
    ADX > 25  = klarer Trend → Signal erlaubt
    ADX > 40  = starker Trend
    ADX > 60  = sehr starker Trend
    """
    h = df["high"]; l = df["low"]; c = df["close"]
    pdm = h.diff().clip(lower=0)
    mdm = (-l.diff()).clip(lower=0)
    pdm[pdm < mdm] = 0
    mdm[mdm < pdm] = 0
    tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    atr_s  = tr.rolling(period).mean()
    pdi    = 100 * (pdm.rolling(period).mean() / (atr_s + 1e-10))
    mdi    = 100 * (mdm.rolling(period).mean() / (atr_s + 1e-10))
    dx     = 100 * (pdi - mdi).abs() / (pdi + mdi + 1e-10)
    adx    = dx.rolling(period).mean()
    return adx, pdi, mdi

def count_consecutive(df, direction):
    """Zählt konsekutive Kerzen in Trendrichtung"""
    closes = df["close"].tail(10)
    count = 0
    for j in range(len(closes)-1, 0, -1):
        if direction == "LONG" and closes.iloc[j] > closes.iloc[j-1]:
            count += 1
        elif direction == "SHORT" and closes.iloc[j] < closes.iloc[j-1]:
            count += 1
        else:
            break
    return count

# ═══════════════════════════════════════════════════════════
# V14.1 ARCHITEKTUR — Smart Money + ADX Filter
#
# Backtest-Ergebnis der Verbesserungen:
#   V14 Original:        PF 2.77  WR 52%  PnL +31%
#   V14.1 (ADX+Konsek):  PF 7.34  WR 73%  PnL +41.5%
#
# NEUE HARD FILTER:
#   1. ADX >= 25 auf 1H  → nur bei echtem Trend
#   2. Mind. 2 konsekutive Kerzen → echtes Momentum
#
# SIGNAL-TYPEN:
#   💥 Breakout  (V14 Logik, bewährt)
#   🌱 Spring    (V15 Wyckoff, neu)
# ═══════════════════════════════════════════════════════════

def get_trend(df, label=""):
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


def check_adx_filter(df1h):
    """
    HARD FILTER: ADX auf 1H — Session-abhängig
    
    London/NY + Overlap:  ADX >= 25  (normaler Filter)
    Asia + Evening:       ADX >= 30  (strenger – wenig Volumen!)
    
    Grund: Asia Session hat dünnes Volumen → Breakouts oft Fakeouts.
    Erster echter Trade (21.03 05:05 Uhr) war genau so ein Asia-Fakeout
    bei ADX 26.5 → wäre mit diesem Filter verhindert worden!
    """
    if df1h is None or len(df1h) < 30:
        return False, 0.0, "⚠️ ADX: Keine Daten"
    adx_vals, pdi, mdi = calc_adx(df1h)
    adx_now = adx_vals.iloc[-1]
    if pd.isna(adx_now):
        return False, 0.0, "⚠️ ADX: Kein Wert"
    adx_now = round(adx_now, 1)

    # Session-abhängige ADX-Schwelle
    hour = datetime.now().hour
    if 14 <= hour < 17:   # London/NY Overlap
        min_adx = 25; session_label = "Overlap"
    elif 8 <= hour < 18:  # London/NY
        min_adx = 25; session_label = "London/NY"
    else:                  # Asia + Evening (00-08 + 18-24)
        min_adx = 30; session_label = "Asia/Evening"

    if adx_now < min_adx:
        return False, adx_now, f"❌ ADX: {adx_now} (min {min_adx} für {session_label} – zu schwach!)"
    strength = "🔥 sehr stark" if adx_now > 50 else ("✅ stark" if adx_now > 35 else "✅ ok")
    return True, adx_now, f"✅ ADX: {adx_now} ({strength}) [{session_label}: min {min_adx}]"


def detect_breakout(df15, direction):
    """
    💥 BREAKOUT SIGNAL (V14 Kern-Logik, bewährt)
    Akkumulation → ATR-Explosion → Volumen-Explosion → Kerzenbestätigung
    """
    if df15 is None or len(df15) < 20: return None, []
    logs = []
    last  = df15.iloc[-1]
    price = last["close"]
    atr_v = calc_atr(df15).iloc[-1]

    # ATR-Explosion
    ranges    = (df15["high"] - df15["low"]).tail(9)
    avg_range = ranges.iloc[:-1].mean()
    cur_range = ranges.iloc[-1]
    ae        = cur_range / (avg_range + 1e-10)
    if ae < 1.4:
        return None, [f"❌ ATR: {round(ae,2)}x (min 1.4x)"]
    logs.append(f"✅ ATR-Explosion: {round(ae,2)}x")

    # Stille Akkumulation davor
    window    = df15.tail(9).iloc[:-1]
    wr        = (window["high"].max() - window["low"].min()) / price * 100
    if wr > 2.5:
        return None, [f"❌ Akkumulation: {round(wr,2)}% (max 2.5%)"]
    logs.append(f"✅ Akkumulation: {round(wr,2)}%")

    # Volumen-Explosion
    vol_avg = df15["vol"].tail(20).mean()
    vol_now = df15["vol"].iloc[-1]
    vr      = vol_now / (vol_avg + 1e-10)
    if vr < 1.8:
        return None, [f"❌ Volumen: {round(vr,2)}x (min 1.8x)"]
    logs.append(f"✅ Volumen: {round(vr,2)}x")

    # Kerze in richtiger Richtung
    is_bull  = last["close"] > last["open"]
    body_pct = abs(last["close"] - last["open"]) / (cur_range + 1e-10)
    if body_pct < 0.3:
        return None, [f"❌ Kerze: {round(body_pct*100)}% Body (min 30%)"]
    if direction == "LONG" and not is_bull:
        return None, ["❌ Bearische Kerze – gegen LONG"]
    if direction == "SHORT" and is_bull:
        return None, ["❌ Bullische Kerze – gegen SHORT"]
    logs.append(f"✅ Kerze: {round(body_pct*100)}% Body")

    # Konsekutive Kerzen (NEUER FILTER V14.1)
    consec = count_consecutive(df15, direction)
    if consec < 2:
        return None, [f"❌ Momentum: nur {consec} konsekutive Kerze(n) (min 2)"]
    logs.append(f"✅ Momentum: {consec} konsekutive Kerzen")

    return {
        "type":    "BREAKOUT",
        "price":   round(price, 3),
        "atr_val": atr_v,
        "vr":      round(vr, 2),
        "ae":      round(ae, 2),
        "consec":  consec
    }, logs


def detect_spring(df15, df5, direction):
    """
    🌱 WYCKOFF SPRING / UPTHRUST (V15 Logik)
    Fakeout unter Support/über Resistance → sofortiger Rebound
    = Smart Money Entry BEVOR der Trend startet
    """
    if df15 is None or len(df15) < 28: return None, []
    logs  = []
    last  = df15.iloc[-1]
    price = last["close"]
    atr_v = calc_atr(df15).iloc[-1]

    window    = df15.tail(25).iloc[:-1]
    zone_high = window["high"].max()
    zone_low  = window["low"].min()
    zone_size = (zone_high - zone_low) / price * 100

    if zone_size > 5.0 or zone_size < 0.4:
        return None, [f"❌ Zone: {round(zone_size,2)}% (brauche 0.4–5%)"]
    logs.append(f"✅ Zone: {round(zone_size,2)}% (${round(zone_low,2)}–${round(zone_high,2)})")

    vol_avg   = window["vol"].mean()
    vol_ratio = last["vol"] / (vol_avg + 1e-10)

    if direction == "LONG":
        pen = (zone_low - last["low"]) / price * 100
        if pen <= 0.02 or pen > 2.0:
            return None, [f"❌ Spring: {round(pen,3)}% Fakeout (brauche 0.02–2%)"]
        if last["close"] < zone_low:
            return None, ["❌ Schliesst unter zone_low – kein Rebound"]
        if last["close"] < last["open"]:
            return None, ["❌ Bearische Kerze"]
        cr = last["high"] - last["low"]
        cp = (last["close"] - last["low"]) / (cr + 1e-10)
        if cp < 0.45:
            return None, [f"❌ Rebound schwach: {round(cp*100)}%"]
        if vol_ratio < 1.1:
            return None, [f"❌ Volumen: {round(vol_ratio,2)}x (min 1.1x)"]
        rsi_v = calc_rsi(df15["close"]).iloc[-1]
        if rsi_v > 72:
            return None, [f"❌ RSI überkauft: {round(rsi_v,1)}"]

        # Delta Check
        delta = 0.0
        if df5 is not None and len(df5) >= 5:
            for _, row in df5.tail(6).iterrows():
                rng = row["high"] - row["low"]
                if rng == 0: continue
                cp_d = (row["close"] - row["low"]) / rng
                delta += row["vol"] * (cp_d - (1 - cp_d))
        if delta < -vol_avg * 0.5:
            return None, [f"❌ Delta bearisch: {round(delta,0)}"]

        logs += [f"✅ Spring: {round(pen,3)}% Fakeout → Rebound {round(cp*100)}%",
                 f"✅ Volumen: {round(vol_ratio,2)}x | RSI: {round(rsi_v,1)}"]

        score = 65
        if vol_ratio > 2.0: score += 15
        elif vol_ratio > 1.5: score += 8
        if pen < 0.3: score += 10
        if cp > 0.7: score += 8
        if rsi_v < 45: score += 5

        return {
            "type":      "SPRING",
            "price":     round(price, 3),
            "atr_val":   atr_v,
            "vr":        round(vol_ratio, 2),
            "pen":       round(pen, 3),
            "zone_low":  round(zone_low, 3),
            "zone_high": round(zone_high, 3),
            "zone_size": round(zone_size, 2),
            "score":     min(100, score)
        }, logs

    else:  # SHORT Upthrust
        pen = (last["high"] - zone_high) / price * 100
        if pen <= 0.02 or pen > 2.0:
            return None, [f"❌ Upthrust: {round(pen,3)}% (brauche 0.02–2%)"]
        if last["close"] > zone_high:
            return None, ["❌ Schliesst über zone_high – kein Rebound"]
        if last["close"] > last["open"]:
            return None, ["❌ Bullische Kerze"]
        cr = last["high"] - last["low"]
        cp = (last["high"] - last["close"]) / (cr + 1e-10)
        if cp < 0.45:
            return None, [f"❌ Rebound schwach: {round(cp*100)}%"]
        if vol_ratio < 1.1:
            return None, [f"❌ Volumen: {round(vol_ratio,2)}x"]
        rsi_v = calc_rsi(df15["close"]).iloc[-1]
        if rsi_v < 28:
            return None, [f"❌ RSI überverkauft: {round(rsi_v,1)}"]

        delta = 0.0
        if df5 is not None and len(df5) >= 5:
            for _, row in df5.tail(6).iterrows():
                rng = row["high"] - row["low"]
                if rng == 0: continue
                cp_d = (row["close"] - row["low"]) / rng
                delta += row["vol"] * (cp_d - (1 - cp_d))
        if delta > vol_avg * 0.5:
            return None, [f"❌ Delta bullisch: +{round(delta,0)}"]

        logs += [f"✅ Upthrust: {round(pen,3)}% Fakeout → Rebound {round(cp*100)}%",
                 f"✅ Volumen: {round(vol_ratio,2)}x | RSI: {round(rsi_v,1)}"]

        score = 65
        if vol_ratio > 2.0: score += 15
        elif vol_ratio > 1.5: score += 8
        if pen < 0.3: score += 10
        if cp > 0.7: score += 8
        if rsi_v > 55: score += 5

        return {
            "type":      "UPTHRUST",
            "price":     round(price, 3),
            "atr_val":   atr_v,
            "vr":        round(vol_ratio, 2),
            "pen":       round(pen, 3),
            "zone_low":  round(zone_low, 3),
            "zone_high": round(zone_high, 3),
            "zone_size": round(zone_size, 2),
            "score":     min(100, score)
        }, logs


def get_session():
    hour = datetime.now().hour
    if 14 <= hour < 17:   return "London/NY Overlap 🔥"
    elif 8 <= hour < 18:  return "London/NY Session"
    elif 0 <= hour < 8:   return "Asia Session"
    else:                  return "Evening Session"


def calc_stops(direction, price, atr):
    atr = max(atr, price * 0.004)
    if direction == "LONG":
        sl    = round(price - atr * 1.5, 3)
        t1    = round(price + atr * 1.5, 3)
        t2    = round(price + atr * 3.0, 3)
        t3    = round(price + atr * 7.0, 3)
        t1_be = round(price + atr * 1.8, 3)
    else:
        sl    = round(price + atr * 1.5, 3)
        t1    = round(price - atr * 1.5, 3)
        t2    = round(price - atr * 3.0, 3)
        t3    = round(price - atr * 7.0, 3)
        t1_be = round(price - atr * 1.8, 3)
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
                send_telegram(f'{icon} <b>{result}!</b>\nSOL LONG | Entry: ${entry} | Exit: ${t["sl"]} | PnL: {pnl}%')
                completed.append(t); changed = True
            elif price >= t["t3"]:
                pnl = round((t["t3"]-entry)/entry*100, 2)
                send_telegram(f'🚀 <b>T3! +{pnl}%</b>\nSOL LONG | T3: ${t["t3"]}')
                completed.append(t); changed = True
            elif price >= t["t2"] and not t.get("t2_hit"):
                t["t2_hit"] = True; t["sl"] = t["t1"]
                send_telegram(f'🎯 <b>T2!</b> SL auf ${t["t1"]}\nSOL LONG'); changed = True
            elif price >= t["t1_be"] and not t.get("t1_hit"):
                t["t1_hit"] = True; be = round(entry + atr_e*0.15, 3); t["sl"] = be
                send_telegram(f'🎯 <b>T1!</b> SL auf BE ${be}\nSOL LONG'); changed = True
        else:
            if price >= t["sl"]:
                result = "BE" if t["sl"] <= entry else "SL"
                pnl = round((entry-t["sl"])/entry*100, 2)
                icon = "➡️" if result == "BE" else "🔴"
                send_telegram(f'{icon} <b>{result}!</b>\nSOL SHORT | Entry: ${entry} | Exit: ${t["sl"]} | PnL: {pnl}%')
                completed.append(t); changed = True
            elif price <= t["t3"]:
                pnl = round((entry-t["t3"])/entry*100, 2)
                send_telegram(f'🚀 <b>T3! +{pnl}%</b>\nSOL SHORT | T3: ${t["t3"]}')
                completed.append(t); changed = True
            elif price <= t["t2"] and not t.get("t2_hit"):
                t["t2_hit"] = True; t["sl"] = t["t1"]
                send_telegram(f'🎯 <b>T2!</b> SL auf ${t["t1"]}\nSOL SHORT'); changed = True
            elif price <= t["t1_be"] and not t.get("t1_hit"):
                t["t1_hit"] = True; be = round(entry - atr_e*0.15, 3); t["sl"] = be
                send_telegram(f'🎯 <b>T1!</b> SL auf BE ${be}\nSOL SHORT'); changed = True
    active_trades = [t for t in active_trades if t not in completed]
    if changed: save_active_trades(active_trades)

# ─── HAUPT-ANALYSE V14.1 ───────────────────────────────────

def analyze():
    df15 = get_candles(SYMBOL, "15m", 80)
    df1h = get_candles(SYMBOL, "1H",  60)
    df4h = get_candles(SYMBOL, "4H",  60)
    df5  = get_candles(SYMBOL, "5m",  30)

    if df15 is None or df1h is None: return None

    # HARD FILTER 1: 4h + 1h Trend aligned
    trend_4h, sig_4h = get_trend(df4h, "4h")
    trend_1h, sig_1h = get_trend(df1h, "1h")
    if trend_4h == "NEUTRAL" or trend_1h == "NEUTRAL": return None
    if trend_4h != trend_1h: return None
    direction = "LONG" if trend_4h == "BULLISH" else "SHORT"

    # HARD FILTER 2: ADX >= 25 (NEU V14.1)
    adx_ok, adx_val, adx_sig = check_adx_filter(df1h)
    if not adx_ok:
        print(f"  [ADX] {adx_sig}")
        return None

    # SIGNAL-ERKENNUNG: Breakout ODER Spring
    breakout_result, breakout_logs = detect_breakout(df15, direction)
    spring_result,   spring_logs   = detect_spring(df15, df5, direction)

    # Bevorzuge Spring wenn beide aktiv (präziserer Entry)
    if spring_result and spring_result["score"] >= 70:
        result = spring_result; logs = spring_logs; sig_type = "SPRING"
    elif breakout_result:
        result = breakout_result; logs = breakout_logs; sig_type = "BREAKOUT"
    else:
        return None

    session_name = get_session()
    funding      = get_funding()
    price        = result["price"]
    atr          = max(result["atr_val"], price * 0.004)
    stops        = calc_stops(direction, price, atr)

    score = result.get("score", 75)
    grade = "A+++" if score >= 85 else ("A++" if score >= 75 else "A+")

    return {
        "score": score, "grade": grade,
        "price": price, "direction": direction,
        "sig_type": sig_type,
        "session": session_name,
        "stops": stops, "funding": funding,
        "entry_low":  round(price * 0.999, 3),
        "entry_high": round(price * 1.001, 3),
        "result": result, "logs": logs,
        "adx": adx_val, "adx_sig": adx_sig,
        "sig_4h": sig_4h, "sig_1h": sig_1h,
        "trend_4h": trend_4h,
        "btc1m_timing": get_btc1m_entry_timing(direction)
    }

# ─── FORMAT ALERT ──────────────────────────────────────────

def format_alert(data):
    s     = data["stops"]
    d     = data["direction"]
    r     = data["result"]
    t     = data["sig_type"]
    arrow = "📈 LONG" if d == "LONG" else "📉 SHORT"
    type_icon = {
        "BREAKOUT": "💥 BREAKOUT",
        "SPRING":   "🌱 SPRING",
        "UPTHRUST": "📍 UPTHRUST"
    }.get(t, "💥")

    lines = [
        "——————————————————",
        f"💰 <b>SOL/USDT</b>  {arrow}",
        f"<b>{type_icon} V14.2</b>",
        f"📊 <b>Score: {data['score']}% ({data['grade']})</b>",
        f"🕐 {data['session']}",
        "——————————————————",
        f"📍 <b>Entry:</b> ${data['entry_low']} – ${data['entry_high']}",
        data.get("btc1m_timing", ""),
        f"🔴 <b>SL:</b> ${s['sl']}  <i>(ATR: {s['atr']})</i>",
        "——————————————————",
        f"🎯 <b>T1:</b> ${s['t1']}  (RR 1:{s['rr1']}) → BE!",
        f"🎯 <b>T2:</b> ${s['t2']}  (RR 1:{s['rr2']}) → SL auf T1",
        f"🚀 <b>T3:</b> ${s['t3']}  (RR 1:{s['rr3']}) → Volltreffer!",
        "——————————————————",
        data["adx_sig"],
        data["sig_4h"],
        data["sig_1h"],
        "——————————————————",
        f"<b>🔍 Signal-Bestätigung:</b>",
    ]
    for log in data["logs"]:
        lines.append(log)
    if t in ("SPRING", "UPTHRUST"):
        lines.append(f"📐 Zone: ${r['zone_low']}–${r['zone_high']} ({r['zone_size']}%) | Fakeout: {r['pen']}%")
    lines += [
        "——————————————————",
        f"🔵 Funding: {round(data['funding'], 3)}%",
        "——————————————————",
        "⚠️ <i>Kein Auto-Trade – du entscheidest!</i>"
    ]
    return "\n".join(lines)

# ─── MAIN ──────────────────────────────────────────────────

def main():
    global last_alert, active_trades
    print("=" * 62)
    print("   SOL A+++ Scanner V14.1 – Smart Money Edition")
    print()
    print("   NEUE HARD FILTER (Backtest: PF 2.77 → 7.34):")
    print("   SOL A+++ Scanner V14.2 – Smart Money + BTC Exit")
    print("   📊 ADX Session-Filter (London>=25 / Asia>=30)")
    print("   ⚡ BTC 5m Exit-Warnung bei offenem Trade")
    print("   💥 Breakout + 🌱 Spring Signale")
    print("=" * 62)

    if active_trades:
        print(f"♻️ {len(active_trades)} Trade(s) wiederhergestellt!")
        send_telegram(f"♻️ <b>V14.2 Restart – {len(active_trades)} Trade(s) wiederhergestellt!</b>")
    else:
        send_telegram(
            "🚀 <b>SOL Scanner V14.2 – Smart Money + BTC!</b>\n\n"
            "🆕 <b>NEU in V14.2:</b>\n"
            "⚡ BTC 5m Exit-Warnung!\n"
            "   BTC läuft SOL um 1-5 Min voraus\n"
            "   Wenn BTC dreht → sofort Telegram\n"
            "   Du entscheidest früher ob du rausgehst\n\n"
            "📊 <b>Filter:</b>\n"
            "London/NY:    ADX >= 25\n"
            "Asia/Evening: ADX >= 30\n\n"
            "🔀 <b>Signale:</b>\n"
            "💥 Breakout | 🌱 Spring\n\n"
            "Warte auf Setup... 👀"
        )

    print("Startup: 3 Minuten warten...")
    time.sleep(180)
    print("Bereit!")

    last_scan = 0; last_check = 0; last_btc_check = 0

    while True:
        now = time.time()

        # Trade Tracking alle 30 Sek
        if now - last_check >= 30:
            last_check = now
            check_active_trades()

        # BTC 5m Exit-Warnung alle 60 Sek (nur wenn Trade offen)
        if now - last_btc_check >= 60 and active_trades:
            last_btc_check = now
            check_btc_exit_warning(active_trades)

        if now - last_scan >= SCAN_INTERVAL:
            last_scan = now
            ts = datetime.now().strftime("%H:%M:%S")
            result = analyze()

            if result:
                s  = result["stops"]
                st = result["sig_type"]
                print(f"[{ts}] {st}! {result['direction']} | Score:{result['score']}% | ADX:{result['adx']}")
                if (now - last_alert) > COOLDOWN:
                    if send_telegram(format_alert(result)):
                        last_alert = now; save_last_alert(now)
                        active_trades.append({
                            "direction": result["direction"],
                            "entry":  result["price"],
                            "sl":     s["sl"], "orig_sl": s["sl"],
                            "t1":     s["t1"], "t1_be":   s["t1_be"],
                            "t2":     s["t2"], "t3":      s["t3"],
                            "atr":    s["atr"],
                            "t1_hit": False, "t2_hit": False
                        })
                        save_active_trades(active_trades)
                        print("  Trade getrackt!")
                else:
                    remaining = round((COOLDOWN - (now - last_alert)) / 60)
                    print(f"  Cooldown: {remaining} min")
            else:
                print(f"[{ts}] Kein Setup (ADX/Trend/Signal)")

        time.sleep(10)

if __name__ == "__main__":
    main()
