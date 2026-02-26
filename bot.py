import requests
import time
import pandas as pd
import numpy as np
from datetime import datetime

# ─── CONFIG ───────────────────────────────────────────────────────────────────

TELEGRAM_TOKEN = “8678164580:AAEmznr79S6qO-NDqHkx8gOM-IqpyA884MQ”
TELEGRAM_CHAT_ID = “2050191721”
SYMBOL = “SOL-USDT”
SCALP_INTERVAL = 60
INTRADAY_INTERVAL = 300
SCALP_MIN_SCORE = 78
INTRADAY_MIN_SCORE = 78
SCALP_COOLDOWN = 1800
INTRADAY_COOLDOWN = 7200

last_scalp_alert = 0
last_intraday_alert = 0

# ─── TELEGRAM ─────────────────────────────────────────────────────────────────

def send_telegram(msg):
url = f”https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage”
try:
r = requests.post(url, json={“chat_id”: TELEGRAM_CHAT_ID, “text”: msg, “parse_mode”: “HTML”})
if r.status_code == 200:
print(”[Telegram] Alert gesendet ✅”)
else:
print(f”[Fehler] Telegram: {r.status_code} - {r.text}”)
except Exception as e:
print(f”[Fehler] Telegram: {e}”)

# ─── OKX DATA ─────────────────────────────────────────────────────────────────

def get_candles(bar=“1m”, limit=100):
url = “https://www.okx.com/api/v5/market/candles”
params = {“instId”: SYMBOL, “bar”: bar, “limit”: limit}
try:
r = requests.get(url, params=params, timeout=10)
data = r.json().get(“data”, [])
if not data:
return None
df = pd.DataFrame(data, columns=[“ts”,“open”,“high”,“low”,“close”,“vol”,“volCcy”,“volCcyQuote”,“confirm”])
for col in [“open”,“high”,“low”,“close”,“vol”,“volCcy”]:
df[col] = pd.to_numeric(df[col])
df = df.iloc[::-1].reset_index(drop=True)
return df
except Exception as e:
print(f”[Fehler] Candles {bar}: {e}”)
return None

def get_taker_volume():
url = “https://www.okx.com/api/v5/rubik/stat/taker-volume”
params = {“instId”: SYMBOL, “instType”: “SPOT”, “period”: “1m”, “limit”: 20}
try:
r = requests.get(url, params=params, timeout=10)
data = r.json().get(“data”, [])
if not data:
return None
df = pd.DataFrame(data, columns=[“ts”, “sellVol”, “buyVol”])
df[“buyVol”] = pd.to_numeric(df[“buyVol”])
df[“sellVol”] = pd.to_numeric(df[“sellVol”])
df[“delta”] = df[“buyVol”] - df[“sellVol”]
return df
except Exception as e:
print(f”[Fehler] Taker Volume: {e}”)
return None

def get_funding_rate():
try:
r = requests.get(“https://www.okx.com/api/v5/public/funding-rate”,
params={“instId”: “SOL-USDT-SWAP”}, timeout=10)
data = r.json().get(“data”, [{}])
return float(data[0].get(“fundingRate”, 0)) * 100
except:
return 0.0

# ─── INDICATORS ───────────────────────────────────────────────────────────────

def calc_ema(series, period):
return series.ewm(span=period, adjust=False).mean()

def calc_rsi(series, period=14):
delta = series.diff()
gain = delta.clip(lower=0).rolling(period).mean()
loss = (-delta.clip(upper=0)).rolling(period).mean()
rs = gain / (loss + 1e-10)
return 100 - (100 / (1 + rs))

def calc_atr(df, period=14):
high = df[“high”]
low = df[“low”]
close = df[“close”]
tr = pd.concat([
high - low,
(high - close.shift()).abs(),
(low - close.shift()).abs()
], axis=1).max(axis=1)
return tr.rolling(period).mean()

def calc_vwap(df):
tp = (df[“high”] + df[“low”] + df[“close”]) / 3
return (tp * df[“vol”]).cumsum() / df[“vol”].cumsum()

def calc_bollinger(df, period=20):
ma = df[“close”].rolling(period).mean()
std = df[“close”].rolling(period).std()
upper = ma + 2 * std
lower = ma - 2 * std
bandwidth = (upper - lower) / (ma + 1e-10)
return upper, lower, bandwidth

# ─── HTF TREND FILTER ─────────────────────────────────────────────────────────

def get_htf_trend():
df = get_candles(“4H”, 50)
if df is None or len(df) < 50:
return “NEUTRAL”
ema21 = calc_ema(df[“close”], 21).iloc[-1]
ema50 = calc_ema(df[“close”], 50).iloc[-1]
price = df[“close”].iloc[-1]
hh = df[“high”].tail(10).iloc[-1] > df[“high”].tail(10).iloc[-5]
hl = df[“low”].tail(10).iloc[-1] > df[“low”].tail(10).iloc[-5]
lh = df[“high”].tail(10).iloc[-1] < df[“high”].tail(10).iloc[-5]
ll = df[“low”].tail(10).iloc[-1] < df[“low”].tail(10).iloc[-5]
bull = sum([price > ema21, ema21 > ema50, hh and hl])
bear = sum([price < ema21, ema21 < ema50, lh and ll])
if bull >= 2:
return “BULLISH”
elif bear >= 2:
return “BEARISH”
return “NEUTRAL”

# ─── DELTA CONFIRMATION ───────────────────────────────────────────────────────

def get_delta_signal(direction=“LONG”):
taker = get_taker_volume()
if taker is None:
return True, 0
recent_delta = taker[“delta”].tail(5).sum()
if direction == “LONG”:
return recent_delta > 0, recent_delta
else:
return recent_delta < 0, recent_delta

# ─── ATR STOP LOSS ────────────────────────────────────────────────────────────

def calc_atr_stops(df, direction=“LONG”, atr_mult=1.5):
atr = calc_atr(df).iloc[-1]
price = df[“close”].iloc[-1]
if direction == “LONG”:
sl = price - (atr * atr_mult)
t1 = price + (atr * 1.0)
t2 = price + (atr * 2.5)
t3 = price + (atr * 4.0)
else:
sl = price + (atr * atr_mult)
t1 = price - (atr * 1.0)
t2 = price - (atr * 2.5)
t3 = price - (atr * 4.0)
risk = abs(price - sl)
return {
“sl”: round(sl, 4),
“t1”: round(t1, 4), “rr1”: round(abs(price-t1)/risk, 1),
“t2”: round(t2, 4), “rr2”: round(abs(price-t2)/risk, 1),
“t3”: round(t3, 4), “rr3”: round(abs(price-t3)/risk, 1),
“atr”: round(atr, 4)
}

# ─── ORDERBLOCK & SWEEP ───────────────────────────────────────────────────────

def detect_orderblock(df):
if len(df) < 10:
return None, None
avg_size = abs(df[“close”] - df[“open”]).mean()
for i in range(len(df)-2, max(len(df)-15, 0), -1):
size = abs(df[“close”].iloc[i] - df[“open”].iloc[i])
if size > avg_size * 1.5:
price = df[“close”].iloc[-1]
if df[“close”].iloc[i] > df[“open”].iloc[i]:
ob_h, ob_l = df[“open”].iloc[i], df[“low”].iloc[i]
if ob_l <= price <= ob_h * 1.01:
return “BULLISH”, (ob_l, ob_h)
else:
ob_h, ob_l = df[“high”].iloc[i], df[“open”].iloc[i]
if ob_l * 0.99 <= price <= ob_h:
return “BEARISH”, (ob_l, ob_h)
return None, None

def detect_sweep(df):
if len(df) < 20:
return None
rh = df[“high”].tail(20).iloc[:-1].max()
rl = df[“low”].tail(20).iloc[:-1].min()
if df[“high”].iloc[-1] > rh and df[“close”].iloc[-1] < rh:
return “BEARISH_SWEEP”
if df[“low”].iloc[-1] < rl and df[“close”].iloc[-1] > rl:
return “BULLISH_SWEEP”
return None

# ─── MAIN ANALYSIS ────────────────────────────────────────────────────────────

def analyze(timeframe=“scalp”):
if timeframe == “scalp”:
bar1, bar2 = “1m”, “5m”
label = “Scalping”
window = “10-20 Minuten”
else:
bar1, bar2 = “15m”, “1H”
label = “Intraday”
window = “1-4 Stunden”

```
df1 = get_candles(bar1, 100)
if df1 is None or len(df1) < 50:
    return None

price = df1["close"].iloc[-1]
ema21 = calc_ema(df1["close"], 21).iloc[-1]
ema50 = calc_ema(df1["close"], 50).iloc[-1]
rsi = calc_rsi(df1["close"]).iloc[-1]
vwap = calc_vwap(df1).iloc[-1]
bb_upper, bb_lower, bb_bw = calc_bollinger(df1)
bb_squeeze = bb_bw.iloc[-1] < bb_bw.tail(20).mean() * 0.7
vol_avg = df1["vol"].tail(20).mean()
vol_spike = df1["vol"].iloc[-1] > vol_avg * 1.5
ob_type, ob_range = detect_orderblock(df1)
sweep = detect_sweep(df1)
htf = get_htf_trend()
funding = get_funding_rate()

score = 0
signals = []
direction = "LONG"

# HTF Filter (20 Punkte)
if htf == "BULLISH":
    score += 20
    direction = "LONG"
    signals.append("✅ HTF 4h Trend: BULLISCH")
elif htf == "BEARISH":
    score += 20
    direction = "SHORT"
    signals.append("✅ HTF 4h Trend: BÄRISCH")
else:
    score += 5
    signals.append("⚠️ HTF 4h Trend: NEUTRAL")

# EMA (10 Punkte)
if price > ema21 > ema50:
    score += 10
    signals.append("✅ EMA21 > EMA50 (bullish Trend)")
elif price < ema21 < ema50:
    score += 10
    signals.append("✅ EMA21 < EMA50 (bearish Trend)")
else:
    score += 5
    signals.append("🔵 EMA gemischt")

# RSI (12 Punkte)
if rsi < 35:
    score += 12
    signals.append(f"✅ RSI überverkauft: {rsi:.1f}")
    direction = "LONG"
elif rsi > 65:
    score += 12
    signals.append(f"✅ RSI überkauft: {rsi:.1f}")
    direction = "SHORT"
else:
    score += 5
    signals.append(f"🔵 RSI neutral: {rsi:.1f}")

# VWAP (8 Punkte)
if price > vwap:
    score += 8
    signals.append(f"🔵 Preis über VWAP (${vwap:.2f})")
else:
    score += 8
    signals.append(f"🔴 Preis unter VWAP (${vwap:.2f})")

# Orderblock (15 Punkte)
if ob_type == "BULLISH":
    score += 15
    direction = "LONG"
    signals.append(f"✅ Bullischer Orderblock: ${ob_range[0]:.2f}-${ob_range[1]:.2f}")
elif ob_type == "BEARISH":
    score += 15
    direction = "SHORT"
    signals.append(f"✅ Bärischer Orderblock: ${ob_range[0]:.2f}-${ob_range[1]:.2f}")

# Liquidity Sweep (12 Punkte)
if sweep == "BULLISH_SWEEP":
    score += 12
    direction = "LONG"
    signals.append("✅ BULLISCHER LIQUIDITÄTSSWEEP!")
elif sweep == "BEARISH_SWEEP":
    score += 12
    direction = "SHORT"
    signals.append("🎯 BÄRISCHER LIQUIDITÄTSSWEEP!")

# Bollinger (8 Punkte)
if bb_squeeze:
    score += 8
    signals.append("💥 Bollinger Squeeze – Explosion kommt!")

# Volume (5 Punkte)
if vol_spike:
    score += 5
    signals.append(f"📊 Volumen Spike!")

# Delta Confirmation (15 Punkte - NEU)
delta_ok, delta_val = get_delta_signal(direction)
if delta_ok:
    score += 15
    signals.append(f"✅ Delta bestätigt {direction}! (Δ{delta_val:+.0f})")
else:
    score -= 10
    signals.append(f"⚠️ Delta widerspricht {direction}! (Δ{delta_val:+.0f})")

# Funding (5 Punkte)
signals.append(f"📉 Funding: {funding:.3f}%")

score = max(0, min(100, score))

# ATR Stops (NEU)
stops = calc_atr_stops(df1, direction)

if score >= 85:
    grade = "A+++"
elif score >= 78:
    grade = "A++"
elif score >= 70:
    grade = "A+"
elif score >= 60:
    grade = "A"
else:
    grade = "B"

return {
    "score": score, "grade": grade, "price": price,
    "direction": direction, "htf": htf,
    "entry_low": round(price * 0.999, 4),
    "entry_high": round(price * 1.001, 4),
    "stops": stops, "signals": signals,
    "rsi": rsi, "funding": funding,
    "label": label, "window": window,
    "delta_ok": delta_ok
}
```

# ─── FORMAT ALERT ─────────────────────────────────────────────────────────────

def format_alert(data):
s = data[“stops”]
arrow = “📈 LONG” if data[“direction”] == “LONG” else “📉 SHORT”
msg = f”””
——————————————————
💰 <b>Paar:</b> SOL/USDT
📊 <b>Score:</b> {data[‘score’]}/100 ({data[‘grade’]})
{arrow}
⏰ <b>Fenster:</b> {data[‘window’]}
🌍 <b>HTF Trend:</b> {data[‘htf’]}
——————————————————
📍 <b>Limit Order:</b> ${data[‘entry_low’]} – ${data[‘entry_high’]}
🔴 <b>Stop Loss:</b> ${s[‘sl’]} <i>(ATR: {s[‘atr’]})</i>
——————————————————
🎯 <b>T1:</b> ${s[‘t1’]}  (RR 1:{s[‘rr1’]}) ← Min. Move
🎯 <b>T2:</b> ${s[‘t2’]}  (RR 1:{s[‘rr2’]}) ← Wahrscheinlich
🚀 <b>T3:</b> ${s[‘t3’]}  (RR 1:{s[‘rr3’]}) ← Wenn es zündet
——————————————————
<b>📊 Signale:</b>
“””
for sig in data[“signals”]:
msg += f”{sig}\n”
msg += f”——————————————————\n📉 RSI: {data[‘rsi’]:.1f} | Funding: {data[‘funding’]:.3f}%\n⚠️ <i>Kein Auto-Trade – du entscheidest!</i>”
return msg

# ─── MAIN LOOP ────────────────────────────────────────────────────────────────

def main():
global last_scalp_alert, last_intraday_alert
print(”=” * 50)
print(”   SOL A+++ Setup Scanner – läuft! 🚀”)
print(”   NEU: HTF Filter + Delta + ATR Stops”)
print(”=” * 50)
send_telegram(“🚀 <b>SOL Scanner V2 gestartet!</b>\n\n✅ HTF 4h Trend Filter\n✅ Delta Confirmation\n✅ ATR Stop Loss\n\nTrefferquote verbessert! Warte auf Setups…”)

```
last_scalp = 0
last_intraday = 0

while True:
    now = time.time()

    if now - last_scalp >= SCALP_INTERVAL:
        last_scalp = now
        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Scalp Check...")
        result = analyze("scalp")
        if result:
            print(f"  Score: {result['score']}/100 ({result['grade']}) | HTF: {result['htf']} | Delta: {'✅' if result['delta_ok'] else '❌'}")
            if result["score"] >= SCALP_MIN_SCORE and (now - last_scalp_alert) > SCALP_COOLDOWN:
                last_scalp_alert = now
                send_telegram(format_alert(result))

    if now - last_intraday >= INTRADAY_INTERVAL:
        last_intraday = now
        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Intraday Check...")
        result = analyze("intraday")
        if result:
            print(f"  Score: {result['score']}/100 ({result['grade']}) | HTF: {result['htf']} | Delta: {'✅' if result['delta_ok'] else '❌'}")
            if result["score"] >= INTRADAY_MIN_SCORE and (now - last_intraday_alert) > INTRADAY_COOLDOWN:
                last_intraday_alert = now
                send_telegram(format_alert(result))

    time.sleep(10)
```

if **name** == “**main**”:
main()
