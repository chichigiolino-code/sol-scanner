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

# ═══════════════════════════════════════════════════════════
# V15 KERN: WYCKOFF SPRING / UPTHRUST DETECTION
#
# Smart Money sammelt STILL in enger Range (Akkumulation).
# Dann: FAKEOUT unter Support (Spring) oder über Resistance (Upthrust)
#   → Retail Stops werden abgeräumt
#   → Sofortige Umkehr = Smart Money Entry
# Wir steigen EIN wenn Smart Money einsteigt — nicht danach!
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


def detect_wyckoff_spring(df15, df5, direction, lookback=24):
    """
    SPRING (LONG):
      Kerze bricht unter Akkumulations-Support → schliesst DARÜBER zurück
      = Smart Money hat alle Stops abgeholt und dreht jetzt nach oben

    UPTHRUST (SHORT):
      Kerze bricht über Akkumulations-Resistance → schliesst DARUNTER zurück
      = Smart Money hat alle Stops abgeholt und dreht jetzt nach unten
    """
    if df15 is None or len(df15) < lookback + 5:
        return None, []

    logs  = []
    last  = df15.iloc[-1]
    price = last["close"]
    atr_v = calc_atr(df15).iloc[-1]

    # Akkumulationszone (letzte N Kerzen ohne aktuelle)
    window    = df15.tail(lookback + 1).iloc[:-1]
    zone_high = window["high"].max()
    zone_low  = window["low"].min()
    zone_size = (zone_high - zone_low) / price * 100

    if zone_size > 5.0 or zone_size < 0.4:
        return None, [f"❌ Zone {round(zone_size,2)}% – nicht im Bereich (0.4–5%)"]
    logs.append(f"✅ Akkumulation: {round(zone_size,2)}% Zone (${round(zone_low,2)}–${round(zone_high,2)})")

    vol_avg    = window["vol"].mean()
    vol_spring = last["vol"]
    vol_ratio  = vol_spring / (vol_avg + 1e-10)
    vol_early  = window["vol"].iloc[:lookback//2].mean()
    vol_late   = window["vol"].iloc[lookback//2:].mean()
    vol_steigt = vol_late > vol_early * 0.85
    if vol_steigt: logs.append("✅ Volumen steigt in Zone (Smart Money aktiv)")

    if direction == "LONG":
        # Fakeout unter zone_low
        pen = (zone_low - last["low"]) / price * 100
        if pen <= 0.02 or pen > 2.0:
            return None, [f"❌ Spring: Penetration {round(pen,3)}% (brauche 0.02–2%)"]
        if last["close"] < zone_low:
            return None, ["❌ Schliesst unter zone_low – kein Rebound"]
        if last["close"] < last["open"]:
            return None, ["❌ Bearische Kerze – kein bullischer Spring"]

        cr = last["high"] - last["low"]
        close_pct = (last["close"] - last["low"]) / (cr + 1e-10)
        if close_pct < 0.45:
            return None, [f"❌ Kerze schliesst zu tief ({round(close_pct*100)}%)"]

        if vol_ratio < 1.1:
            return None, [f"❌ Spring-Volumen zu schwach ({round(vol_ratio,2)}x)"]

        # Delta aus 5m
        delta = 0.0
        if df5 is not None and len(df5) >= 5:
            for _, row in df5.tail(6).iterrows():
                rng = row["high"] - row["low"]
                if rng == 0: continue
                cp = (row["close"] - row["low"]) / rng
                delta += row["vol"] * (cp - (1 - cp))
        if delta < -vol_avg * 0.5:
            return None, [f"❌ Delta bearisch beim Spring ({round(delta,0)})"]

        rsi_val = calc_rsi(df15["close"]).iloc[-1]
        if rsi_val > 72:
            return None, [f"❌ RSI überkauft: {round(rsi_val,1)}"]

        logs += [
            f"✅ Spring: {round(pen,3)}% unter zone_low → Rebound!",
            f"✅ Kerze: {round(close_pct*100)}% oben (bullisch)",
            f"✅ Volumen: {round(vol_ratio,2)}x",
            f"✅ Delta: {'+' if delta>=0 else ''}{round(delta,0)}",
            f"✅ RSI: {round(rsi_val,1)}"
        ]

        score = 60
        if vol_ratio > 2.0:   score += 15
        elif vol_ratio > 1.5: score += 10
        else:                  score += 5
        if pen < 0.3:          score += 10
        if close_pct > 0.7:    score += 10
        if vol_steigt:         score += 5
        if rsi_val < 45:       score += 5

        return {
            "direction": "LONG", "price": round(price, 3),
            "zone_low": round(zone_low,3), "zone_high": round(zone_high,3),
            "zone_size": round(zone_size,2), "pen": round(pen,3),
            "vol_ratio": round(vol_ratio,2), "close_pct": round(close_pct*100),
            "atr_val": atr_v, "rsi": round(rsi_val,1),
            "delta": round(delta,0), "score": min(100, score)
        }, logs

    else:  # SHORT Upthrust
        pen = (last["high"] - zone_high) / price * 100
        if pen <= 0.02 or pen > 2.0:
            return None, [f"❌ Upthrust: Penetration {round(pen,3)}% (brauche 0.02–2%)"]
        if last["close"] > zone_high:
            return None, ["❌ Schliesst über zone_high – kein Rebound"]
        if last["close"] > last["open"]:
            return None, ["❌ Bullische Kerze – kein bearischer Upthrust"]

        cr = last["high"] - last["low"]
        close_pct = (last["high"] - last["close"]) / (cr + 1e-10)
        if close_pct < 0.45:
            return None, [f"❌ Kerze schliesst zu hoch ({round(close_pct*100)}%)"]

        if vol_ratio < 1.1:
            return None, [f"❌ Upthrust-Volumen zu schwach ({round(vol_ratio,2)}x)"]

        delta = 0.0
        if df5 is not None and len(df5) >= 5:
            for _, row in df5.tail(6).iterrows():
                rng = row["high"] - row["low"]
                if rng == 0: continue
                cp = (row["close"] - row["low"]) / rng
                delta += row["vol"] * (cp - (1 - cp))
        if delta > vol_avg * 0.5:
            return None, [f"❌ Delta bullisch beim Upthrust (+{round(delta,0)})"]

        rsi_val = calc_rsi(df15["close"]).iloc[-1]
        if rsi_val < 28:
            return None, [f"❌ RSI überverkauft: {round(rsi_val,1)}"]

        logs += [
            f"✅ Upthrust: {round(pen,3)}% über zone_high → Rebound!",
            f"✅ Kerze: {round(close_pct*100)}% unten (bearisch)",
            f"✅ Volumen: {round(vol_ratio,2)}x",
            f"✅ Delta: {round(delta,0)}",
            f"✅ RSI: {round(rsi_val,1)}"
        ]

        score = 60
        if vol_ratio > 2.0:   score += 15
        elif vol_ratio > 1.5: score += 10
        else:                  score += 5
        if pen < 0.3:          score += 10
        if close_pct > 0.7:    score += 10
        if vol_steigt:         score += 5
        if rsi_val > 55:       score += 5

        return {
            "direction": "SHORT", "price": round(price, 3),
            "zone_low": round(zone_low,3), "zone_high": round(zone_high,3),
            "zone_size": round(zone_size,2), "pen": round(pen,3),
            "vol_ratio": round(vol_ratio,2), "close_pct": round(close_pct*100),
            "atr_val": atr_v, "rsi": round(rsi_val,1),
            "delta": round(delta,0), "score": min(100, score)
        }, logs


def get_session():
    hour = datetime.now().hour
    if 14 <= hour < 17:   return "OVERLAP",   "London/NY Overlap 🔥"
    elif 8 <= hour < 18:  return "LONDON_NY", "London/NY Session"
    elif 0 <= hour < 8:   return "ASIA",      "Asia Session"
    else:                  return "EVENING",   "Evening Session"


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

# ─── HAUPT-ANALYSE V15 ─────────────────────────────────────

def analyze():
    df15 = get_candles(SYMBOL, "15m", 80)
    df1h = get_candles(SYMBOL, "1H",  60)
    df4h = get_candles(SYMBOL, "4H",  60)
    df5  = get_candles(SYMBOL, "5m",  30)

    if df15 is None or df1h is None: return None

    trend_4h, sig_4h = get_trend(df4h, "4h")
    trend_1h, sig_1h = get_trend(df1h, "1h")

    if trend_4h == "NEUTRAL" or trend_1h == "NEUTRAL": return None
    if trend_4h != trend_1h: return None

    direction = "LONG" if trend_4h == "BULLISH" else "SHORT"

    result, logs = detect_wyckoff_spring(df15, df5, direction, lookback=24)
    if result is None: return None
    if result["score"] < 65: return None

    session_id, session_name = get_session()
    funding = get_funding()
    price   = result["price"]
    atr     = max(result["atr_val"], price * 0.004)
    stops   = calc_stops(direction, price, atr)
    grade   = "A+++" if result["score"] >= 85 else ("A++" if result["score"] >= 75 else "A+")

    return {
        "score": result["score"], "grade": grade,
        "price": price, "direction": direction,
        "session": session_name, "stops": stops, "funding": funding,
        "entry_low":  round(price * 0.999, 3),
        "entry_high": round(price * 1.001, 3),
        "result": result, "logs": logs,
        "sig_4h": sig_4h, "sig_1h": sig_1h,
        "trend_4h": trend_4h
    }

# ─── FORMAT ALERT ──────────────────────────────────────────

def format_alert(data):
    s     = data["stops"]
    d     = data["direction"]
    r     = data["result"]
    arrow = "📈 LONG" if d == "LONG" else "📉 SHORT"
    stype = "🌱 SPRING" if d == "LONG" else "📍 UPTHRUST"

    lines = [
        "——————————————————",
        f"💰 <b>SOL/USDT</b>  {arrow}",
        f"🧠 <b>Wyckoff {stype} V15</b>",
        f"📊 <b>Score: {data['score']}% ({data['grade']})</b>",
        f"🕐 {data['session']}",
        "——————————————————",
        f"📍 <b>Entry:</b> ${data['entry_low']} – ${data['entry_high']}",
        f"🔴 <b>SL:</b> ${s['sl']}  <i>(ATR: {s['atr']})</i>",
        "——————————————————",
        f"🎯 <b>T1:</b> ${s['t1']}  (RR 1:{s['rr1']}) → BE!",
        f"🎯 <b>T2:</b> ${s['t2']}  (RR 1:{s['rr2']}) → SL auf T1",
        f"🚀 <b>T3:</b> ${s['t3']}  (RR 1:{s['rr3']}) → Volltreffer!",
        "——————————————————",
        f"📐 Zone: ${r['zone_low']}–${r['zone_high']} ({r['zone_size']}%)",
        f"💥 Fakeout: {r['pen']}% | Rebound: {r['close_pct']}% Body",
        "——————————————————",
        "<b>🔍 Wyckoff Bestätigung:</b>",
    ]
    for log in data["logs"]:
        lines.append(log)
    lines += [
        "——————————————————",
        data["sig_4h"], data["sig_1h"],
        f"🔵 Funding: {round(data['funding'], 3)}%",
        "——————————————————",
        "⚠️ <i>Kein Auto-Trade – du entscheidest!</i>"
    ]
    return "\n".join(lines)

# ─── MAIN ──────────────────────────────────────────────────

def main():
    global last_alert, active_trades
    print("=" * 60)
    print("   SOL A+++ Scanner V15 – Wyckoff Spring Edition")
    print("   🌱 SPRING:   Fakeout unter Support  → Smart Money kauft")
    print("   📍 UPTHRUST: Fakeout über Resistance → Smart Money shortet")
    print("   🧠 Entry BEVOR der Trend startet – nicht danach!")
    print("=" * 60)

    if active_trades:
        print(f"♻️ {len(active_trades)} Trade(s) wiederhergestellt!")
        send_telegram(f"♻️ <b>V15 Restart – {len(active_trades)} Trade(s) wiederhergestellt!</b>")
    else:
        send_telegram(
            "🚀 <b>SOL Scanner V15 – Wyckoff Spring Edition!</b>\n\n"
            "🧠 <b>Neue Philosophie: Smart Money früher erwischen</b>\n\n"
            "🌱 <b>Spring (LONG):</b>\n"
            "Enge Akkumulationszone → kurzer Fakeout unter Support\n"
            "→ Retail Stops werden abgeräumt\n"
            "→ Sofortiger Rebound = Smart Money kauft!\n\n"
            "📍 <b>Upthrust (SHORT):</b>\n"
            "Enge Zone → Fakeout über Resistance\n"
            "→ Retail Longs werden gestoppt\n"
            "→ Sofortiger Reversal = Smart Money shortet!\n\n"
            "✅ Filter: 4h+1h aligned | Delta | RSI | Volumen\n"
            "📊 Weniger Signale – PRÄZISER Entry\n\n"
            "Warte auf Spring/Upthrust... 👀"
        )

    print("Startup: 3 Minuten warten...")
    time.sleep(180)
    print("Bereit!")

    last_scan = 0; last_check = 0

    while True:
        now = time.time()

        if now - last_check >= 30:
            last_check = now
            check_active_trades()

        if now - last_scan >= SCAN_INTERVAL:
            last_scan = now
            ts = datetime.now().strftime("%H:%M:%S")
            result = analyze()

            if result:
                s = result["stops"]
                r = result["result"]
                stype = "🌱 SPRING" if result["direction"] == "LONG" else "📍 UPTHRUST"
                print(f"[{ts}] {stype} {result['direction']} | Score:{result['score']}% | Pen:{r['pen']}% | VR:{r['vol_ratio']}x")
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
                print(f"[{ts}] Kein Spring/Upthrust")

        time.sleep(10)

if __name__ == "__main__":
    main()
