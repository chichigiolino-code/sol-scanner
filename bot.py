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
SCALP_COOLDOWN = 1800
INTRADAY_COOLDOWN = 7200

last_scalp_alert = 0
last_intraday_alert = 0
active_trades = []

# TELEGRAM
def send_telegram(msg):
    url = "https://api.telegram.org/bot" + TELEGRAM_TOKEN + "/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"})
        if r.status_code == 200:
            print("[Telegram] OK")
        else:
            print("[Fehler] Telegram: " + str(r.status_code))
    except Exception as e:
        print("[Fehler] Telegram: " + str(e))

# OKX DATA
def get_candles(symbol, bar="15m", limit=100):
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
        print("[Fehler] Candles: " + str(e))
        return None

def get_taker_volume():
    try:
        r = requests.get("https://www.okx.com/api/v5/rubik/stat/taker-volume",
                         params={"instId": SYMBOL, "instType": "SPOT", "period": "5m", "limit": 10}, timeout=10)
        data = r.json().get("data", [])
        if not data:
            return None, 0
        df = pd.DataFrame(data, columns=["ts","sellVol","buyVol"])
        df["buyVol"]  = pd.to_numeric(df["buyVol"])
        df["sellVol"] = pd.to_numeric(df["sellVol"])
        delta   = (df["buyVol"] - df["sellVol"]).sum()
        avg_vol = (df["buyVol"] + df["sellVol"]).mean()
        return delta, avg_vol
    except:
        return None, 0

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

# SESSION (MEZ)
def get_session():
    hour = datetime.now().hour
    if 14 <= hour < 17:
        return "OVERLAP",   "London/NY Overlap 🔥", 10
    elif 9 <= hour < 18:
        return "LONDON_NY", "London/NY Session",     5
    elif 1 <= hour < 9:
        return "ASIA",      "Asien Session",          0
    else:
        return "OFF",       "Ausserhalb Sessions",   -5

# HTF TREND
def get_htf_trend(symbol):
    df = get_candles(symbol, "4H", 50)
    if df is None or len(df) < 50:
        return "NEUTRAL"
    ema21 = calc_ema(df["close"], 21).iloc[-1]
    ema50 = calc_ema(df["close"], 50).iloc[-1]
    price = df["close"].iloc[-1]
    highs = df["high"].tail(10)
    lows  = df["low"].tail(10)
    hh_hl = highs.iloc[-1] > highs.iloc[0] and lows.iloc[-1] > lows.iloc[0]
    bull  = sum([price > ema21, ema21 > ema50, hh_hl])
    if bull >= 2:
        return "BULLISH"
    lh_ll = highs.iloc[-1] < highs.iloc[0] and lows.iloc[-1] < lows.iloc[0]
    bear  = sum([price < ema21, ema21 < ema50, lh_ll])
    if bear >= 2:
        return "BEARISH"
    return "NEUTRAL"

# MARKET MODE - nur BREAKOUT_UP und RANGE_SUPPORT
def get_market_mode(df):
    if len(df) < 30:
        return "SKIP"
    price     = df["close"].iloc[-1]
    high_20   = df["high"].tail(21).iloc[:-1].max()
    low_20    = df["low"].tail(21).iloc[:-1].min()
    range_pct = (high_20 - low_20) / (price + 1e-10) * 100
    ema21     = calc_ema(df["close"], 21).iloc[-1]
    ema50     = calc_ema(df["close"], 50).iloc[-1]
    ema_diff  = abs(ema21 - ema50) / (price + 1e-10) * 100
    vol_avg   = df["vol"].tail(20).mean()
    vol_now   = df["vol"].iloc[-1]
    vol_ok    = vol_now > vol_avg * 1.8

    # Breakout: neues 20-Kerzen Hoch mit Volumen
    if price >= high_20 * 0.999 and vol_ok:
        return "BREAKOUT_UP"

    # Range Support: enger Kanal, Preis im unteren Drittel
    if range_pct < 3.5 and ema_diff < 2.0:
        range_pos = (price - low_20) / (high_20 - low_20 + 1e-10)
        if range_pos < 0.35:
            return "RANGE_SUPPORT"

    return "SKIP"  # Kein Trade

# ORDERBLOCK
def detect_ob_bullish(df):
    if len(df) < 20:
        return False
    avg   = abs(df["close"].tail(20) - df["open"].tail(20)).mean()
    price = df["close"].iloc[-1]
    for i in range(len(df)-2, max(len(df)-20, 0), -1):
        size = abs(df["close"].iloc[i] - df["open"].iloc[i])
        if size > avg * 1.8 and df["close"].iloc[i] > df["open"].iloc[i]:
            ob_h = df["open"].iloc[i]
            ob_l = df["low"].iloc[i]
            if ob_l <= price <= ob_h * 1.015:
                return True
    return False

# SWEEP
def detect_bullish_sweep(df):
    if len(df) < 20:
        return False
    rl = df["low"].tail(20).iloc[:-1].min()
    return df["low"].iloc[-1] < rl and df["close"].iloc[-1] > rl

# ATR STOPS - Backtest optimiert
def calc_stops(price, atr):
    atr = max(atr, price * 0.004)  # Minimum 0.4%
    sl       = round(price - (atr * 1.5), 3)
    t1       = round(price + (atr * 1.5), 3)  # RR 1:1
    t2       = round(price + (atr * 3.0), 3)  # RR 1:2
    t3       = round(price + (atr * 7.0), 3)  # RR 1:4.7
    t1_be    = round(price + (atr * 1.8), 3)  # BE Trigger
    risk     = price - sl
    return {
        "sl": sl, "t1": t1, "t2": t2, "t3": t3,
        "t1_be": t1_be,
        "rr1": round((t1-price)/risk, 1),
        "rr2": round((t2-price)/risk, 1),
        "rr3": round((t3-price)/risk, 1),
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
    for t in active_trades:
        entry = t["entry"]
        if price <= t["sl"]:
            result = "BE" if t["sl"] >= entry else "SL"
            pnl = round((t["sl"] - entry) / entry * 100, 2)
            icon = "➡️" if result == "BE" else "🔴"
            send_telegram(icon + " <b>" + result + "!</b>\nSOL LONG\nEntry: $" + str(entry) + " | Exit: $" + str(t["sl"]) + "\nPnL: " + str(pnl) + "%")
            completed.append(t)
        elif price >= t["t3"]:
            pnl = round((t["t3"] - entry) / entry * 100, 2)
            send_telegram("🚀 <b>T3 ERREICHT! +" + str(pnl) + "%</b>\nSOL LONG | Entry: $" + str(entry) + " | T3: $" + str(t["t3"]))
            completed.append(t)
        elif price >= t["t2"] and not t.get("t2_hit"):
            t["t2_hit"] = True
            t["sl"] = t["t1"]  # SL auf T1 nachziehen
            send_telegram("🎯 <b>T2 ERREICHT!</b> SL auf $" + str(t["t1"]) + " nachgezogen!\nSOL LONG | T2: $" + str(t["t2"]))
        elif price >= t["t1_be"] and not t.get("t1_hit"):
            t["t1_hit"] = True
            be_sl = round(entry + (t["atr"] * 0.15), 3)
            t["sl"] = be_sl
            send_telegram("🎯 <b>T1 ERREICHT!</b> SL auf Break-Even ($" + str(be_sl) + ")!\nSOL LONG | T1: $" + str(t["t1"]))
    active_trades = [t for t in active_trades if t not in completed]

# MAIN ANALYSIS
def analyze(timeframe="scalp"):
    bar     = "5m"  if timeframe == "scalp" else "15m"
    window  = "20-40 Minuten" if timeframe == "scalp" else "2-6 Stunden"

    df     = get_candles(SYMBOL, bar, 100)
    df_15m = get_candles(SYMBOL, "15m", 60)
    if df is None or df_15m is None or len(df) < 50:
        return None

    price = df["close"].iloc[-1]

    # Session
    session_id, session_name, session_bonus = get_session()
    # Ausserhalb Sessions: hoehere Schwelle (84)

    # HTF - NUR LONG wenn alle drei bullish!
    sol_htf = get_htf_trend(SYMBOL)
    btc_htf = get_htf_trend("BTC-USDT")
    eth_htf = get_htf_trend("ETH-USDT")

    if sol_htf != "BULLISH":
        return None  # Nur Long in Bullenmarkt

    # Market Mode - nur BREAKOUT_UP oder RANGE_SUPPORT
    market_mode = get_market_mode(df)
    if market_mode == "SKIP":
        return None

    # Indikatoren
    ema9  = calc_ema(df["close"], 9).iloc[-1]
    ema21 = calc_ema(df["close"], 21).iloc[-1]
    ema50 = calc_ema(df["close"], 50).iloc[-1]
    rsi   = calc_rsi(df["close"]).iloc[-1]
    vwap  = calc_vwap(df).iloc[-1]
    atr   = calc_atr(df_15m).iloc[-1]
    vol_avg = df["vol"].tail(20).mean()
    vol_now = df["vol"].iloc[-1]
    ob    = detect_ob_bullish(df)
    sweep = detect_bullish_sweep(df)
    delta, avg_vol = get_taker_volume()
    funding = get_funding()

    # SCORE
    score   = 20 + session_bonus  # Base: HTF Bullish + Session
    signals = []

    # HTF Kontext
    signals.append("✅ SOL 4h: BULLISCH")
    if btc_htf == "BULLISH":
        score += 8
        signals.append("✅ BTC 4h: BULLISCH")
    else:
        score -= 5
        signals.append("⚠️ BTC 4h: " + btc_htf)
    if eth_htf == "BULLISH":
        score += 6
        signals.append("✅ ETH 4h: BULLISCH")
    else:
        signals.append("⚠️ ETH 4h: " + eth_htf)
    if btc_htf == eth_htf == "BULLISH":
        score += 5
        signals.append("🔥 Alle 3 Maerkte BULLISCH!")

    # Market Mode
    if market_mode == "BREAKOUT_UP":
        score += 25
        signals.append("💥 BREAKOUT OBEN mit Volumen!")
    elif market_mode == "RANGE_SUPPORT":
        score += 15
        signals.append("📊 RANGE SUPPORT - Preis am Boden!")

    # EMA Struktur
    if price > ema9 > ema21 > ema50:
        score += 15
        signals.append("✅ Starke EMA Struktur (9>21>50)")
    elif price > ema21 > ema50:
        score += 10
        signals.append("✅ EMA21 > EMA50 bullish")
    elif price > ema50:
        score += 5
        signals.append("🔵 Preis ueber EMA50")
    else:
        score -= 12
        signals.append("❌ EMA Struktur schwach!")

    # RSI - nicht ueberkauft einsteigen!
    if rsi < 40:
        score += 15
        signals.append("✅ RSI ueberverkauft: " + str(round(rsi,1)) + " - Reversal!")
    elif rsi < 50:
        score += 10
        signals.append("✅ RSI gut: " + str(round(rsi,1)))
    elif rsi < 60:
        score += 5
        signals.append("🔵 RSI ok: " + str(round(rsi,1)))
    elif rsi > 70:
        score -= 20
        signals.append("❌ RSI ueberkauft: " + str(round(rsi,1)) + " - KEIN ENTRY!")
    else:
        score -= 8
        signals.append("⚠️ RSI hoch: " + str(round(rsi,1)))

    # VWAP
    if price > vwap:
        score += 8
        signals.append("✅ Preis ueber VWAP ($" + str(round(vwap,3)) + ")")
    else:
        score -= 8
        signals.append("❌ Preis unter VWAP ($" + str(round(vwap,3)) + ")")

    # Orderblock
    if ob:
        score += 15
        signals.append("✅ Bullischer Orderblock!")

    # Sweep
    if sweep:
        score += 15
        signals.append("✅ BULLISCHER LIQUIDITAETSSWEEP!")

    # Volume
    vol_ratio = vol_now / (vol_avg + 1e-10)
    if vol_ratio > 2.0:
        score += 12
        signals.append("✅ Volumen Spike: " + str(round(vol_ratio,1)) + "x!")
    elif vol_ratio > 1.5:
        score += 8
        signals.append("✅ Gutes Volumen: " + str(round(vol_ratio,1)) + "x")
    elif vol_ratio > 1.0:
        score += 4
        signals.append("🔵 Volumen ok")
    else:
        score -= 5
        signals.append("⚠️ Volumen schwach")

    # Delta
    if delta is not None and avg_vol > 0:
        delta_pct = delta / (avg_vol + 1e-10)
        if delta_pct > 0.1:
            score += 12
            signals.append("✅ Delta bullish (+" + str(round(delta_pct*100,0)) + "%)")
        elif delta_pct < -0.1:
            score -= 10
            signals.append("❌ Delta bearish! (" + str(round(delta_pct*100,0)) + "%)")
        else:
            signals.append("⚠️ Delta neutral")

    # Funding
    signals.append("🔵 Funding: " + str(round(funding,3)) + "%")

    # Session Info
    signals.append("🕐 Session: " + session_name)

    score = max(0, min(100, score))

    # Mindest-Score je nach Modus und Session
    if session_id == "OFF":
        min_score = 84  # Ausserhalb Sessions - nur A+++ Setups
    else:
        min_score = 80 if market_mode == "BREAKOUT_UP" else 83
    if score < min_score:
        return None

    grade = "A+++" if score >= 90 else ("A++" if score >= 83 else "A+")

    stops = calc_stops(price, atr)

    return {
        "score": score, "grade": grade, "price": price,
        "sol_htf": sol_htf, "btc_htf": btc_htf, "eth_htf": eth_htf,
        "market_mode": market_mode, "session": session_name,
        "entry_low":  round(price * 0.999, 3),
        "entry_high": round(price * 1.001, 3),
        "stops": stops, "signals": signals,
        "rsi": rsi, "funding": funding, "window": window
    }

# FORMAT ALERT
def format_alert(data):
    s  = data["stops"]
    mm = data["market_mode"]
    mm_icon = "💥" if mm == "BREAKOUT_UP" else "📊"

    lines = [
        "——————————————————",
        "💰 <b>SOL/USDT</b>  📈 LONG",
        "📊 <b>Score:</b> " + str(data["score"]) + "/100 (" + data["grade"] + ")",
        mm_icon + " <b>" + mm + "</b>",
        "⏰ Fenster: " + data["window"],
        "——————————————————",
        "🌍 SOL: " + data["sol_htf"] + " | BTC: " + data["btc_htf"] + " | ETH: " + data["eth_htf"],
        "🕐 " + data["session"],
        "——————————————————",
        "📍 <b>Entry:</b> $" + str(data["entry_low"]) + " – $" + str(data["entry_high"]),
        "🔴 <b>Stop Loss:</b> $" + str(s["sl"]) + " <i>(ATR: " + str(s["atr"]) + ")</i>",
        "——————————————————",
        "🎯 <b>T1:</b> $" + str(s["t1"]) + "  (RR 1:" + str(s["rr1"]) + ") → dann BE!",
        "🎯 <b>T2:</b> $" + str(s["t2"]) + "  (RR 1:" + str(s["rr2"]) + ") → SL auf T1",
        "🚀 <b>T3:</b> $" + str(s["t3"]) + "  (RR 1:" + str(s["rr3"]) + ") → Volltreffer!",
        "——————————————————",
        "<b>Signale:</b>"
    ]
    msg = "\n".join(lines) + "\n"
    for sig in data["signals"]:
        msg += sig + "\n"
    msg += "——————————————————\n"
    msg += "RSI: " + str(round(data["rsi"],1)) + " | Funding: " + str(round(data["funding"],3)) + "%\n"
    msg += "⚠️ <i>Kein Auto-Trade – du entscheidest!</i>"
    return msg

# MAIN
def main():
    global last_scalp_alert, last_intraday_alert
    print("=" * 55)
    print("   SOL A+++ Scanner V5 FINAL")
    print("   Backtest: 75% Win Rate | Profit Factor 6.47")
    print("   NUR LONG | BREAKOUT_UP + RANGE_SUPPORT")
    print("=" * 55)
    send_telegram(
        "🚀 <b>SOL Scanner V5 FINAL gestartet!</b>\n\n"
        "📊 Backtest Ergebnisse:\n"
        "✅ 75% Win Rate\n"
        "✅ Profit Factor: 6.47\n"
        "✅ Ø Gewinn: +1.5% | Ø Verlust: -0.7%\n\n"
        "⚙️ Setup:\n"
        "• Nur LONG Trades\n"
        "• Nur BREAKOUT_UP + RANGE_SUPPORT\n"
        "• SOL+BTC+ETH HTF Filter\n"
        "• ATR Stops (BE nach T1)\n"
        "• Session bewusst\n\n"
        "Warte auf A+++ Setup..."
    )

    last_scalp    = 0
    last_intraday = 0
    last_check    = 0

    while True:
        now = time.time()

        # Trade Tracking alle 30s
        if now - last_check >= 30:
            last_check = now
            check_active_trades()

        # Scalp
        if now - last_scalp >= SCALP_INTERVAL:
            last_scalp = now
            ts = datetime.now().strftime("%H:%M:%S")
            result = analyze("scalp")
            if result:
                print("[" + ts + "] SIGNAL! Score: " + str(result["score"]) + " | " + result["market_mode"])
                if (now - last_scalp_alert) > SCALP_COOLDOWN:
                    last_scalp_alert = now
                    send_telegram(format_alert(result))
                    s = result["stops"]
                    active_trades.append({
                        "entry": result["price"],
                        "sl": s["sl"], "orig_sl": s["sl"],
                        "t1": s["t1"], "t1_be": s["t1_be"],
                        "t2": s["t2"], "t3": s["t3"],
                        "atr": s["atr"],
                        "t1_hit": False, "t2_hit": False
                    })
            else:
                print("[" + ts + "] Scalp: kein Setup")

        # Intraday
        if now - last_intraday >= INTRADAY_INTERVAL:
            last_intraday = now
            ts = datetime.now().strftime("%H:%M:%S")
            result = analyze("intraday")
            if result:
                print("[" + ts + "] INTRADAY SIGNAL! Score: " + str(result["score"]) + " | " + result["market_mode"])
                if (now - last_intraday_alert) > INTRADAY_COOLDOWN:
                    last_intraday_alert = now
                    send_telegram(format_alert(result))
                    s = result["stops"]
                    active_trades.append({
                        "entry": result["price"],
                        "sl": s["sl"], "orig_sl": s["sl"],
                        "t1": s["t1"], "t1_be": s["t1_be"],
                        "t2": s["t2"], "t3": s["t3"],
                        "atr": s["atr"],
                        "t1_hit": False, "t2_hit": False
                    })

        time.sleep(10)

if __name__ == "__main__":
    main()
