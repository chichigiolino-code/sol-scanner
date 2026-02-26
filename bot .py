import requests
import time
import pandas as pd
from datetime import datetime

# CONFIG
TELEGRAM_TOKEN = "8678164580:AAEmznr79S6qO-NDqHkx8gOM-IqpyA884MQ"
TELEGRAM_CHAT_ID = "2050191721"
SYMBOL = "SOL-USDT"
SCALP_INTERVAL = 60
INTRADAY_INTERVAL = 300
SCALP_MIN_SCORE = 80
INTRADAY_MIN_SCORE = 80
SCALP_COOLDOWN = 1800
INTRADAY_COOLDOWN = 7200

last_scalp_alert = 0
last_intraday_alert = 0

# Aktive Trades tracken
active_trades = []

# TELEGRAM
def send_telegram(msg):
    url = "https://api.telegram.org/bot" + TELEGRAM_TOKEN + "/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"})
        if r.status_code == 200:
            print("[Telegram] Alert gesendet OK")
        else:
            print("[Fehler] Telegram: " + str(r.status_code))
    except Exception as e:
        print("[Fehler] Telegram: " + str(e))

# OKX DATA
def get_candles(bar="1m", limit=100):
    url = "https://www.okx.com/api/v5/market/candles"
    params = {"instId": SYMBOL, "bar": bar, "limit": limit}
    try:
        r = requests.get(url, params=params, timeout=10)
        data = r.json().get("data", [])
        if not data:
            return None
        df = pd.DataFrame(data, columns=["ts","open","high","low","close","vol","volCcy","volCcyQuote","confirm"])
        for col in ["open","high","low","close","vol"]:
            df[col] = pd.to_numeric(df[col])
        df = df.iloc[::-1].reset_index(drop=True)
        return df
    except Exception as e:
        print("[Fehler] Candles " + bar + ": " + str(e))
        return None

def get_taker_volume():
    url = "https://www.okx.com/api/v5/rubik/stat/taker-volume"
    params = {"instId": SYMBOL, "instType": "SPOT", "period": "5m", "limit": 10}
    try:
        r = requests.get(url, params=params, timeout=10)
        data = r.json().get("data", [])
        if not data:
            return None, 0
        df = pd.DataFrame(data, columns=["ts", "sellVol", "buyVol"])
        df["buyVol"] = pd.to_numeric(df["buyVol"])
        df["sellVol"] = pd.to_numeric(df["sellVol"])
        df["delta"] = df["buyVol"] - df["sellVol"]
        total_delta = df["delta"].sum()
        avg_vol = (df["buyVol"] + df["sellVol"]).mean()
        return total_delta, avg_vol
    except Exception as e:
        print("[Fehler] Taker: " + str(e))
        return None, 0

def get_funding():
    try:
        r = requests.get("https://www.okx.com/api/v5/public/funding-rate",
                         params={"instId": "SOL-USDT-SWAP"}, timeout=10)
        data = r.json().get("data", [{}])
        return float(data[0].get("fundingRate", 0)) * 100
    except:
        return 0.0

def get_price():
    try:
        r = requests.get("https://www.okx.com/api/v5/market/ticker",
                         params={"instId": SYMBOL}, timeout=10)
        data = r.json().get("data", [{}])
        return float(data[0].get("last", 0))
    except:
        return 0.0

# INDICATORS
def calc_ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def calc_rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / (loss + 1e-10)
    return 100 - (100 / (1 + rs))

def calc_atr(df, period=14):
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift()).abs(),
        (df["low"] - df["close"].shift()).abs()
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()

def calc_vwap(df):
    tp = (df["high"] + df["low"] + df["close"]) / 3
    return (tp * df["vol"]).cumsum() / df["vol"].cumsum()

def calc_bb(df, period=20):
    ma = df["close"].rolling(period).mean()
    std = df["close"].rolling(period).std()
    bw = (4 * std) / (ma + 1e-10)
    return bw

# HTF TREND - Nur Long wenn bullish, nur Short wenn bearish
def get_htf_trend():
    df = get_candles("4H", 50)
    if df is None or len(df) < 50:
        return "NEUTRAL"
    ema21 = calc_ema(df["close"], 21).iloc[-1]
    ema50 = calc_ema(df["close"], 50).iloc[-1]
    price = df["close"].iloc[-1]
    
    # Higher High / Higher Low
    highs = df["high"].tail(10)
    lows = df["low"].tail(10)
    hh_hl = (highs.iloc[-1] > highs.iloc[0]) and (lows.iloc[-1] > lows.iloc[0])
    lh_ll = (highs.iloc[-1] < highs.iloc[0]) and (lows.iloc[-1] < lows.iloc[0])
    
    bull_score = sum([price > ema21, ema21 > ema50, hh_hl])
    bear_score = sum([price < ema21, ema21 < ema50, lh_ll])
    
    if bull_score >= 2:
        return "BULLISH"
    elif bear_score >= 2:
        return "BEARISH"
    return "NEUTRAL"

# ORDERBLOCK
def detect_ob(df):
    if len(df) < 10:
        return None, None
    avg = abs(df["close"] - df["open"]).mean()
    price = df["close"].iloc[-1]
    for i in range(len(df)-2, max(len(df)-20, 0), -1):
        size = abs(df["close"].iloc[i] - df["open"].iloc[i])
        if size > avg * 1.8:
            if df["close"].iloc[i] > df["open"].iloc[i]:
                ob_h = df["open"].iloc[i]
                ob_l = df["low"].iloc[i]
                if ob_l <= price <= ob_h * 1.015:
                    return "BULLISH", (ob_l, ob_h)
            else:
                ob_h = df["high"].iloc[i]
                ob_l = df["open"].iloc[i]
                if ob_l * 0.985 <= price <= ob_h:
                    return "BEARISH", (ob_l, ob_h)
    return None, None

# SWEEP
def detect_sweep(df):
    if len(df) < 20:
        return None
    rh = df["high"].tail(20).iloc[:-1].max()
    rl = df["low"].tail(20).iloc[:-1].min()
    if df["high"].iloc[-1] > rh and df["close"].iloc[-1] < rh:
        return "BEARISH_SWEEP"
    if df["low"].iloc[-1] < rl and df["close"].iloc[-1] > rl:
        return "BULLISH_SWEEP"
    return None

# ATR STOPS - benutzt 15m Kerzen fuer realistischere Werte
def calc_stops(direction, price, df_15m):
    atr = calc_atr(df_15m, 14).iloc[-1]
    
    # Mindest-ATR damit Stops Sinn machen
    min_atr = price * 0.003  # mindestens 0.3% vom Preis
    atr = max(atr, min_atr)
    
    if direction == "LONG":
        sl = price - (atr * 2.0)
        t1 = price + (atr * 2.0)   # RR 1:1
        t2 = price + (atr * 4.0)   # RR 1:2
        t3 = price + (atr * 7.0)   # RR 1:3.5
    else:
        sl = price + (atr * 2.0)
        t1 = price - (atr * 2.0)
        t2 = price - (atr * 4.0)
        t3 = price - (atr * 7.0)
    
    risk = abs(price - sl)
    return {
        "sl": round(sl, 3),
        "t1": round(t1, 3), "rr1": round(abs(price-t1)/risk, 1),
        "t2": round(t2, 3), "rr2": round(abs(price-t2)/risk, 1),
        "t3": round(t3, 3), "rr3": round(abs(price-t3)/risk, 1),
        "atr": round(atr, 3)
    }

# TRADE TRACKING
def check_active_trades():
    global active_trades
    if not active_trades:
        return
    
    price = get_price()
    if price == 0:
        return
    
    completed = []
    for trade in active_trades:
        direction = trade["direction"]
        sl = trade["sl"]
        t1 = trade["t1"]
        t2 = trade["t2"]
        t3 = trade["t3"]
        entry = trade["entry"]
        
        if direction == "LONG":
            if price <= sl:
                msg = "🔴 <b>STOP LOSS getriggert!</b>\nSOL/USDT LONG\nEntry: $" + str(entry) + "\nSL: $" + str(sl) + "\nPreis: $" + str(round(price, 3))
                send_telegram(msg)
                completed.append(trade)
            elif price >= t3:
                msg = "🚀 <b>T3 ERREICHT!</b>\nSOL/USDT LONG\nEntry: $" + str(entry) + "\nT3: $" + str(t3) + "\nPreis: $" + str(round(price, 3))
                send_telegram(msg)
                completed.append(trade)
            elif price >= t2 and not trade.get("t2_hit"):
                trade["t2_hit"] = True
                msg = "🎯 <b>T2 ERREICHT!</b>\nSOL/USDT LONG\nEntry: $" + str(entry) + "\nT2: $" + str(t2)
                send_telegram(msg)
            elif price >= t1 and not trade.get("t1_hit"):
                trade["t1_hit"] = True
                msg = "🎯 <b>T1 ERREICHT!</b> SL auf Break-Even!\nSOL/USDT LONG\nEntry: $" + str(entry) + "\nT1: $" + str(t1)
                send_telegram(msg)
        else:  # SHORT
            if price >= sl:
                msg = "🔴 <b>STOP LOSS getriggert!</b>\nSOL/USDT SHORT\nEntry: $" + str(entry) + "\nSL: $" + str(sl) + "\nPreis: $" + str(round(price, 3))
                send_telegram(msg)
                completed.append(trade)
            elif price <= t3:
                msg = "🚀 <b>T3 ERREICHT!</b>\nSOL/USDT SHORT\nEntry: $" + str(entry) + "\nT3: $" + str(t3) + "\nPreis: $" + str(round(price, 3))
                send_telegram(msg)
                completed.append(trade)
            elif price <= t2 and not trade.get("t2_hit"):
                trade["t2_hit"] = True
                msg = "🎯 <b>T2 ERREICHT!</b>\nSOL/USDT SHORT\nEntry: $" + str(entry) + "\nT2: $" + str(t2)
                send_telegram(msg)
            elif price <= t1 and not trade.get("t1_hit"):
                trade["t1_hit"] = True
                msg = "🎯 <b>T1 ERREICHT!</b> SL auf Break-Even!\nSOL/USDT SHORT\nEntry: $" + str(entry) + "\nT1: $" + str(t1)
                send_telegram(msg)
    
    active_trades = [t for t in active_trades if t not in completed]

# MAIN ANALYSIS
def analyze(timeframe="scalp"):
    if timeframe == "scalp":
        bar1 = "5m"
        window = "20-40 Minuten"
        label = "Scalping"
    else:
        bar1 = "15m"
        window = "2-6 Stunden"
        label = "Intraday"

    df = get_candles(bar1, 100)
    df_15m = get_candles("15m", 100)  # Immer 15m fuer ATR
    
    if df is None or df_15m is None or len(df) < 50:
        return None

    price = df["close"].iloc[-1]
    ema21 = calc_ema(df["close"], 21).iloc[-1]
    ema50 = calc_ema(df["close"], 50).iloc[-1]
    rsi = calc_rsi(df["close"]).iloc[-1]
    vwap = calc_vwap(df).iloc[-1]
    bb_bw = calc_bb(df)
    bb_squeeze = bb_bw.iloc[-1] < bb_bw.tail(20).mean() * 0.7
    vol_avg = df["vol"].tail(20).mean()
    vol_spike = df["vol"].iloc[-1] > vol_avg * 1.5

    ob_type, ob_range = detect_ob(df)
    sweep = detect_sweep(df)
    htf = get_htf_trend()
    funding = get_funding()
    delta, avg_vol = get_taker_volume()

    score = 0
    signals = []

    # HTF bestimmt Richtung - STRIKT
    if htf == "BULLISH":
        direction = "LONG"
        score += 20
        signals.append("✅ HTF 4h Trend: BULLISCH")
    elif htf == "BEARISH":
        direction = "SHORT"
        score += 20
        signals.append("✅ HTF 4h Trend: BÄRISCH")
    else:
        direction = "LONG"  # Default
        score += 5
        signals.append("⚠️ HTF 4h: NEUTRAL - schwaches Setup")

    # EMA muss HTF bestaetigen
    if direction == "LONG":
        if price > ema21 > ema50:
            score += 15
            signals.append("✅ EMA21 > EMA50 (bullish bestaetigt)")
        elif price > ema50:
            score += 8
            signals.append("🔵 Preis ueber EMA50")
        else:
            score -= 5
            signals.append("❌ EMA gegen LONG Richtung")
    else:
        if price < ema21 < ema50:
            score += 15
            signals.append("✅ EMA21 < EMA50 (bearish bestaetigt)")
        elif price < ema50:
            score += 8
            signals.append("🔴 Preis unter EMA50")
        else:
            score -= 5
            signals.append("❌ EMA gegen SHORT Richtung")

    # RSI
    if direction == "LONG" and rsi < 40:
        score += 15
        signals.append("✅ RSI ueberverkauft: " + str(round(rsi, 1)))
    elif direction == "SHORT" and rsi > 60:
        score += 15
        signals.append("✅ RSI ueberkauft: " + str(round(rsi, 1)))
    elif 45 <= rsi <= 55:
        score += 8
        signals.append("🔵 RSI neutral: " + str(round(rsi, 1)))
    else:
        score += 3
        signals.append("⚠️ RSI: " + str(round(rsi, 1)))

    # VWAP
    if (direction == "LONG" and price > vwap) or (direction == "SHORT" and price < vwap):
        score += 10
        signals.append("✅ Preis auf richtiger VWAP Seite ($" + str(round(vwap, 3)) + ")")
    else:
        score += 3
        signals.append("⚠️ Preis gegen VWAP ($" + str(round(vwap, 3)) + ")")

    # Orderblock - muss Richtung matchen
    if ob_type == "BULLISH" and direction == "LONG":
        score += 15
        signals.append("✅ Bullischer OB: $" + str(round(ob_range[0], 3)) + "-$" + str(round(ob_range[1], 3)))
    elif ob_type == "BEARISH" and direction == "SHORT":
        score += 15
        signals.append("✅ Baerischer OB: $" + str(round(ob_range[0], 3)) + "-$" + str(round(ob_range[1], 3)))
    elif ob_type is not None:
        score -= 5
        signals.append("❌ OB gegen Richtung")

    # Sweep - muss Richtung matchen
    if sweep == "BULLISH_SWEEP" and direction == "LONG":
        score += 15
        signals.append("✅ BULLISCHER LIQUIDITAETSSWEEP!")
    elif sweep == "BEARISH_SWEEP" and direction == "SHORT":
        score += 15
        signals.append("✅ BAERISCHER LIQUIDITAETSSWEEP!")
    elif sweep is not None:
        score -= 5
        signals.append("❌ Sweep gegen Richtung")

    # Bollinger Squeeze
    if bb_squeeze:
        score += 8
        signals.append("💥 Bollinger Squeeze - Explosion kommt!")

    # Volume
    if vol_spike:
        score += 5
        signals.append("📊 Volumen Spike!")

    # Delta - nur zaehlen wenn signifikant
    if delta is not None and avg_vol > 0:
        delta_pct = delta / (avg_vol + 1e-10)
        if direction == "LONG" and delta_pct > 0.1:
            score += 15
            signals.append("✅ Delta bestaetigt LONG (+" + str(round(delta_pct*100, 0)) + "%)")
        elif direction == "SHORT" and delta_pct < -0.1:
            score += 15
            signals.append("✅ Delta bestaetigt SHORT (" + str(round(delta_pct*100, 0)) + "%)")
        elif abs(delta_pct) <= 0.1:
            score += 3
            signals.append("⚠️ Delta neutral - keine Bestaetigung")
        else:
            score -= 8
            signals.append("❌ Delta gegen Richtung!")
    else:
        signals.append("⚪ Delta: keine Daten")

    # Funding
    if abs(funding) > 0.03:
        if (funding > 0.03 and direction == "SHORT") or (funding < -0.03 and direction == "LONG"):
            score += 5
            signals.append("✅ Funding unterstuetzt Richtung: " + str(round(funding, 3)) + "%")
        else:
            signals.append("⚠️ Funding gegen Richtung: " + str(round(funding, 3)) + "%")
    else:
        signals.append("🔵 Funding neutral: " + str(round(funding, 3)) + "%")

    score = max(0, min(100, score))

    if score >= 85:
        grade = "A+++"
    elif score >= 80:
        grade = "A++"
    elif score >= 70:
        grade = "A+"
    elif score >= 60:
        grade = "A"
    else:
        grade = "B"

    # Stops mit 15m ATR fuer realistische Werte
    stops = calc_stops(direction, price, df_15m)

    # Entry Zone
    if direction == "LONG":
        entry_low = round(price * 0.998, 3)
        entry_high = round(price * 1.002, 3)
    else:
        entry_low = round(price * 0.998, 3)
        entry_high = round(price * 1.002, 3)

    return {
        "score": score, "grade": grade, "price": price,
        "direction": direction, "htf": htf,
        "entry_low": entry_low, "entry_high": entry_high,
        "stops": stops, "signals": signals,
        "rsi": rsi, "funding": funding,
        "label": label, "window": window
    }

# FORMAT ALERT
def format_alert(data):
    s = data["stops"]
    arrow = "📈 LONG" if data["direction"] == "LONG" else "📉 SHORT"
    
    lines = [
        "——————————————————",
        "💰 <b>Paar:</b> SOL/USDT",
        "📊 <b>Score:</b> " + str(data["score"]) + "/100 (" + data["grade"] + ")",
        arrow,
        "⏰ <b>Fenster:</b> " + data["window"],
        "🌍 <b>HTF Trend:</b> " + data["htf"],
        "——————————————————",
        "📍 <b>Entry:</b> $" + str(data["entry_low"]) + " – $" + str(data["entry_high"]),
        "🔴 <b>Stop Loss:</b> $" + str(s["sl"]) + " <i>(ATR 15m: " + str(s["atr"]) + ")</i>",
        "——————————————————",
        "🎯 <b>T1:</b> $" + str(s["t1"]) + "  (RR 1:" + str(s["rr1"]) + ")",
        "🎯 <b>T2:</b> $" + str(s["t2"]) + "  (RR 1:" + str(s["rr2"]) + ")",
        "🚀 <b>T3:</b> $" + str(s["t3"]) + "  (RR 1:" + str(s["rr3"]) + ")",
        "——————————————————",
        "<b>📊 Signale:</b>"
    ]
    
    msg = "\n".join(lines) + "\n"
    for sig in data["signals"]:
        msg += sig + "\n"
    msg += "——————————————————\n"
    msg += "📉 RSI: " + str(round(data["rsi"], 1)) + " | Funding: " + str(round(data["funding"], 3)) + "%\n"
    msg += "⚠️ <i>Kein Auto-Trade – du entscheidest!</i>"
    return msg

# MAIN
def main():
    global last_scalp_alert, last_intraday_alert
    print("=" * 50)
    print("   SOL A+++ Scanner V3 - laeuft!")
    print("   HTF Filter + Delta + ATR 15m + Trade Tracking")
    print("=" * 50)
    
    send_telegram("🚀 <b>SOL Scanner V3 gestartet!</b>\n\n✅ HTF erzwingt Richtung\n✅ Delta Confirmation\n✅ ATR Stops (15m)\n✅ Trade Tracking\n\nWarte auf Setups...")

    last_scalp = 0
    last_intraday = 0
    last_trade_check = 0

    while True:
        now = time.time()

        # Trade Tracking alle 30 Sekunden
        if now - last_trade_check >= 30:
            last_trade_check = now
            check_active_trades()

        # Scalp Check
        if now - last_scalp >= SCALP_INTERVAL:
            last_scalp = now
            ts = datetime.now().strftime("%H:%M:%S")
            print("\n[" + ts + "] Scalp Check...")
            result = analyze("scalp")
            if result:
                print("  Score: " + str(result["score"]) + "/100 (" + result["grade"] + ") | HTF: " + result["htf"] + " | Dir: " + result["direction"])
                if result["score"] >= SCALP_MIN_SCORE and (now - last_scalp_alert) > SCALP_COOLDOWN:
                    last_scalp_alert = now
                    send_telegram(format_alert(result))
                    # Trade zu Tracking hinzufuegen
                    s = result["stops"]
                    active_trades.append({
                        "direction": result["direction"],
                        "entry": result["price"],
                        "sl": s["sl"], "t1": s["t1"],
                        "t2": s["t2"], "t3": s["t3"],
                        "t1_hit": False, "t2_hit": False
                    })

        # Intraday Check
        if now - last_intraday >= INTRADAY_INTERVAL:
            last_intraday = now
            ts = datetime.now().strftime("%H:%M:%S")
            print("\n[" + ts + "] Intraday Check...")
            result = analyze("intraday")
            if result:
                print("  Score: " + str(result["score"]) + "/100 (" + result["grade"] + ") | HTF: " + result["htf"] + " | Dir: " + result["direction"])
                if result["score"] >= INTRADAY_MIN_SCORE and (now - last_intraday_alert) > INTRADAY_COOLDOWN:
                    last_intraday_alert = now
                    send_telegram(format_alert(result))
                    s = result["stops"]
                    active_trades.append({
                        "direction": result["direction"],
                        "entry": result["price"],
                        "sl": s["sl"], "t1": s["t1"],
                        "t2": s["t2"], "t3": s["t3"],
                        "t1_hit": False, "t2_hit": False
                    })

        time.sleep(10)

if __name__ == "__main__":
    main()
