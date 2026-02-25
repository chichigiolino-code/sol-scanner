"""
╔══════════════════════════════════════════════════════════════╗
║           SOL A+++ SETUP SCANNER by Claude                  ║
║     Kein Auto-Trading – nur Analyse & Alert!                ║
║     Scalping (5/15min) + Intraday (1H/4H)                  ║
╚══════════════════════════════════════════════════════════════╝
"""

import time
import os
import requests
import numpy as np
from datetime import datetime

# ─────────────────────────────────────────────────────────────
# KONFIGURATION – hier deine Daten eintragen!
# ─────────────────────────────────────────────────────────────
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "8678164580:AAEmznr79S6qO-NDqHkx8gOM-IqpyA884MQ"

TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "2050191721"

SYMBOL                 = "SOLUSDT"
SCALP_CHECK_SECONDS    = 60    # Scalp: jede Minute prüfen
INTRADAY_CHECK_SECONDS = 300   # Intraday: alle 5 Minuten prüfen
ALERT_THRESHOLD        = 78    # Score ab dem ein Alert gesendet wird

# Aktive Trades überwachen (Entry, SL, T1, T2, T3)
active_trades = {}

# ─────────────────────────────────────────────────────────────
# OKX API – kostenlos, kein Key nötig für Marktdaten
# ─────────────────────────────────────────────────────────────
OKX_BASE = "https://www.okx.com/api/v5"

# OKX Intervall-Mapping
INTERVAL_MAP = {
    "5m":  "5m",
    "15m": "15m",
    "1h":  "1H",
    "4h":  "4H",
}

def get_candles(symbol: str, interval: str, limit: int = 150) -> list:
    """Holt Kerzendaten von OKX (kostenlos, kein API Key)."""
    okx_interval = INTERVAL_MAP.get(interval, interval)
    inst_id = symbol.replace("USDT", "-USDT")  # SOLUSDT → SOL-USDT
    try:
        r = requests.get(f"{OKX_BASE}/market/candles",
                         params={"instId": inst_id, "bar": okx_interval, "limit": limit},
                         timeout=10)
        r.raise_for_status()
        data = r.json().get("data", [])
        # OKX gibt neueste Kerze zuerst → umkehren
        candles = []
        for c in reversed(data):
            candles.append({
                "open":   float(c[1]),
                "high":   float(c[2]),
                "low":    float(c[3]),
                "close":  float(c[4]),
                "volume": float(c[5]),
            })
        return candles
    except Exception as e:
        print(f"[Fehler] Candles {interval}: {e}")
        return []

def get_funding_rate(symbol: str) -> float:
    """Funding Rate von OKX Futures."""
    inst_id = symbol.replace("USDT", "-USDT-SWAP")  # SOL-USDT-SWAP
    try:
        r = requests.get(f"{OKX_BASE}/public/funding-rate",
                         params={"instId": inst_id}, timeout=10)
        r.raise_for_status()
        data = r.json().get("data", [])
        return float(data[0]["fundingRate"]) if data else 0.0
    except:
        return 0.0

def get_open_interest(symbol: str) -> float:
    """Open Interest von OKX."""
    inst_id = symbol.replace("USDT", "-USDT-SWAP")
    try:
        r = requests.get(f"{OKX_BASE}/public/open-interest",
                         params={"instType": "SWAP", "instId": inst_id}, timeout=10)
        r.raise_for_status()
        data = r.json().get("data", [])
        return float(data[0]["oi"]) if data else 0.0
    except:
        return 0.0

def get_long_short_ratio(symbol: str) -> float:
    """Long/Short Ratio von OKX."""
    ccy = symbol.replace("USDT", "")  # SOL
    try:
        r = requests.get(f"{OKX_BASE}/rubik/stat/contracts/long-short-account-ratio",
                         params={"ccy": ccy, "period": "5m"}, timeout=10)
        r.raise_for_status()
        data = r.json().get("data", [])
        # Ratio: >1 = mehr Longs, <1 = mehr Shorts → normalisieren auf 0-1
        if data:
            ratio = float(data[0][1])
            return ratio / (ratio + 1)
        return 0.5
    except:
        return 0.5

def get_current_price(symbol: str) -> float:
    """Aktuellen Preis von OKX holen."""
    inst_id = symbol.replace("USDT", "-USDT")
    try:
        r = requests.get(f"{OKX_BASE}/market/ticker",
                         params={"instId": inst_id}, timeout=10)
        r.raise_for_status()
        data = r.json().get("data", [])
        return float(data[0]["last"]) if data else 0.0
    except:
        return 0.0

# ─────────────────────────────────────────────────────────────
# TECHNISCHE INDIKATOREN
# ─────────────────────────────────────────────────────────────
def ema(values: list, period: int) -> float:
    if len(values) < period:
        return values[-1] if values else 0
    k = 2 / (period + 1)
    val = np.mean(values[:period])
    for v in values[period:]:
        val = v * k + val * (1 - k)
    return val

def rsi(closes: list, period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    deltas = np.diff(closes[-(period+1):])
    gains  = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)
    avg_gain = np.mean(gains)
    avg_loss = np.mean(losses)
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def atr(candles: list, period: int = 14) -> float:
    trs = []
    for i in range(1, len(candles)):
        h, l, pc = candles[i]["high"], candles[i]["low"], candles[i-1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return float(np.mean(trs[-period:])) if trs else 0.0

def bollinger_squeeze(closes: list, period: int = 20) -> bool:
    """True wenn BB Squeeze aktiv (Explosion kommt)."""
    if len(closes) < period:
        return False
    recent = closes[-period:]
    mean   = np.mean(recent)
    std    = np.std(recent)
    upper  = mean + 2 * std
    lower  = mean - 2 * std
    band_width = (upper - lower) / mean
    # Squeeze wenn Bandbreite unter 3%
    return band_width < 0.03

def cvd(candles: list, lookback: int = 20) -> float:
    """Cumulative Volume Delta – positiv = echte Käufer."""
    total = 0.0
    for c in candles[-lookback:]:
        body = c["close"] - c["open"]
        total += c["volume"] * (1 if body > 0 else -1)
    return total

def rsi_divergence(candles: list, period: int = 14) -> str:
    """Erkennt bullische oder bärische RSI-Divergenz."""
    closes = [c["close"] for c in candles]
    lows   = [c["low"]   for c in candles]
    
    if len(closes) < 30:
        return "none"
    
    rsi_now  = rsi(closes[-15:], period)
    rsi_prev = rsi(closes[-30:-15], period)
    
    price_low_now  = min(lows[-15:])
    price_low_prev = min(lows[-30:-15])
    
    # Bullisch: Preis macht neues Tief, RSI nicht
    if price_low_now < price_low_prev and rsi_now > rsi_prev:
        return "bullish"
    
    price_high_now  = max([c["high"] for c in candles[-15:]])
    price_high_prev = max([c["high"] for c in candles[-30:-15]])
    
    # Bärisch: Preis macht neues Hoch, RSI nicht
    if price_high_now > price_high_prev and rsi_now < rsi_prev:
        return "bearish"
    
    return "none"

def find_orderblocks(candles: list, lookback: int = 30) -> dict:
    """Findet bullische und bärische Orderblocks."""
    ob_bulls = []
    ob_bears = []
    recent = candles[-lookback:]
    
    for i in range(2, len(recent) - 1):
        c     = recent[i]
        c_next = recent[i + 1]
        body   = abs(c["close"] - c["open"])
        avg_body = np.mean([abs(x["close"] - x["open"]) for x in recent])
        
        # Bullischer OB: starke rote Kerze gefolgt von starker grüner Kerze
        if (c["close"] < c["open"] and
            c_next["close"] > c_next["open"] and
            body > avg_body * 1.5):
            ob_bulls.append({"high": c["high"], "low": c["low"]})
        
        # Bärischer OB: starke grüne Kerze gefolgt von starker roter Kerze
        if (c["close"] > c["open"] and
            c_next["close"] < c_next["open"] and
            body > avg_body * 1.5):
            ob_bears.append({"high": c["high"], "low": c["low"]})
    
    return {"bulls": ob_bulls[-3:], "bears": ob_bears[-3:]}

def find_fvg(candles: list) -> dict:
    """Fair Value Gaps erkennen."""
    fvg_bulls = []
    fvg_bears = []
    
    for i in range(2, len(candles)):
        prev2 = candles[i - 2]
        curr  = candles[i]
        
        # Bullisches FVG: Lücke zwischen High[i-2] und Low[i]
        if curr["low"] > prev2["high"]:
            fvg_bulls.append({"top": curr["low"], "bottom": prev2["high"]})
        
        # Bärisches FVG: Lücke zwischen Low[i-2] und High[i]
        if curr["high"] < prev2["low"]:
            fvg_bears.append({"top": prev2["low"], "bottom": curr["high"]})
    
    return {"bulls": fvg_bulls[-3:], "bears": fvg_bears[-3:]}

def liquidity_sweep(candles: list, lookback: int = 20) -> dict:
    """Erkennt Stop Hunts / Liquiditätssweeps."""
    result = {"bullish_sweep": False, "bearish_sweep": False}
    if len(candles) < lookback + 2:
        return result
    
    prev_lows  = [c["low"]  for c in candles[-(lookback+2):-2]]
    prev_highs = [c["high"] for c in candles[-(lookback+2):-2]]
    last = candles[-1]
    prev = candles[-2]
    
    key_low  = min(prev_lows)
    key_high = max(prev_highs)
    
    # Bullischer Sweep: Wick unter Key-Low aber Schlusskurs darüber
    if (last["low"] < key_low and last["close"] > key_low and
            last["close"] > last["open"]):
        result["bullish_sweep"] = True
    
    # Bärischer Sweep: Wick über Key-High aber Schlusskurs darunter
    if (last["high"] > key_high and last["close"] < key_high and
            last["close"] < last["open"]):
        result["bearish_sweep"] = True
    
    return result

def find_support_resistance(candles: list, lookback: int = 50) -> dict:
    """Pivot-basierte Support und Resistance Zonen."""
    recent = candles[-lookback:]
    supports    = []
    resistances = []
    
    for i in range(2, len(recent) - 2):
        lo = recent[i]["low"]
        hi = recent[i]["high"]
        
        if (lo < recent[i-1]["low"] and lo < recent[i-2]["low"] and
                lo < recent[i+1]["low"] and lo < recent[i+2]["low"]):
            supports.append(lo)
        
        if (hi > recent[i-1]["high"] and hi > recent[i-2]["high"] and
                hi > recent[i+1]["high"] and hi > recent[i+2]["high"]):
            resistances.append(hi)
    
    return {"supports": sorted(supports), "resistances": sorted(resistances)}

def vwap(candles: list) -> float:
    """Volume Weighted Average Price."""
    total_vol   = sum(c["volume"] for c in candles)
    if total_vol == 0:
        return candles[-1]["close"]
    total_price = sum(((c["high"] + c["low"] + c["close"]) / 3) * c["volume"]
                      for c in candles)
    return total_price / total_vol

# ─────────────────────────────────────────────────────────────
# CORE ANALYSE – Score berechnen
# ─────────────────────────────────────────────────────────────
def analyze(mode: str) -> dict | None:
    """
    mode = "scalp"    → 5min Entry, 15min Kontext
    mode = "intraday" → 1H Entry,  4H Kontext
    """
    if mode == "scalp":
        tf_high  = "15m"
        tf_entry = "5m"
        label    = "SCALP"
        emoji    = "⚡"
        window   = "15-30 Minuten"
    else:
        tf_high  = "4h"
        tf_entry = "1h"
        label    = "INTRADAY"
        emoji    = "📊"
        window   = "2-8 Stunden"

    c_high  = get_candles(SYMBOL, tf_high,  150)
    c_entry = get_candles(SYMBOL, tf_entry, 150)

    if len(c_high) < 50 or len(c_entry) < 50:
        return None

    price   = c_entry[-1]["close"]
    closes_h = [c["close"] for c in c_high]
    closes_e = [c["close"] for c in c_entry]

    score   = 0
    signals = []
    direction = None  # "LONG" oder "SHORT"

    # ── Layer 1: Market Context ──────────────────────────────
    ema21  = ema(closes_h, 21)
    ema50  = ema(closes_h, 50)
    ema200 = ema(closes_h, 200) if len(closes_h) >= 200 else ema(closes_h, len(closes_h))

    bullish_ctx = price > ema50
    bearish_ctx = price < ema50

    if bullish_ctx:
        score += 12
        signals.append("✅ Preis über EMA50 (bullish Context)")
    else:
        score += 12
        signals.append("✅ Preis unter EMA50 (bearish Context)")

    if ema21 > ema50:
        score += 8
        signals.append("✅ EMA21 > EMA50 (bullish Trend)")
    else:
        score += 8
        signals.append("✅ EMA21 < EMA50 (bearish Trend)")

    # ── Layer 2: Struktur & Orderblocks ─────────────────────
    sr      = find_support_resistance(c_high)
    ob      = find_orderblocks(c_entry)
    current_atr = atr(c_entry)

    # Preis nahe an einem Orderblock?
    near_bull_ob = any(abs(price - o["low"]) < current_atr for o in ob["bulls"])
    near_bear_ob = any(abs(price - o["high"]) < current_atr for o in ob["bears"])

    if near_bull_ob:
        score += 15
        signals.append("✅ Bullischer Orderblock in Reichweite")
    if near_bear_ob:
        score += 15
        signals.append("✅ Bärischer Orderblock in Reichweite")

    # VWAP
    vwap_val = vwap(c_entry[-50:])
    if price > vwap_val:
        signals.append(f"📊 Preis über VWAP (${vwap_val:.2f})")
    else:
        signals.append(f"📊 Preis unter VWAP (${vwap_val:.2f})")

    # Bollinger Squeeze
    if bollinger_squeeze(closes_e):
        score += 10
        signals.append("💥 Bollinger Squeeze – Explosion kommt!")

    # ── Layer 3: Smart Money Entry ───────────────────────────
    sweep    = liquidity_sweep(c_entry)
    fvg      = find_fvg(c_entry)
    rsi_val  = rsi(closes_e)
    div      = rsi_divergence(c_entry)
    cvd_val  = cvd(c_entry)

    # Liquiditätssweep
    if sweep["bullish_sweep"]:
        score += 20
        signals.append("🎯 BULLISHER LIQUIDITÄTSSWEEP erkannt!")
        direction = "LONG"
    if sweep["bearish_sweep"]:
        score += 20
        signals.append("🎯 BÄRISCHER LIQUIDITÄTSSWEEP erkannt!")
        direction = "SHORT"

    # FVG
    near_bull_fvg = any(f["bottom"] <= price <= f["top"] for f in fvg["bulls"])
    near_bear_fvg = any(f["bottom"] <= price <= f["top"] for f in fvg["bears"])

    if near_bull_fvg:
        score += 10
        signals.append("✅ Preis in bullischem FVG (Fill erwartet)")
        if not direction:
            direction = "LONG"
    if near_bear_fvg:
        score += 10
        signals.append("✅ Preis in bärischem FVG (Fill erwartet)")
        if not direction:
            direction = "SHORT"

    # RSI
    if rsi_val < 30:
        score += 8
        signals.append(f"✅ RSI überverkauft ({rsi_val:.1f}) – Reversal Signal")
    elif rsi_val > 70:
        score += 8
        signals.append(f"✅ RSI überkauft ({rsi_val:.1f}) – Reversal Signal")

    # RSI Divergenz
    if div == "bullish":
        score += 12
        signals.append("🔥 Bullische RSI Divergenz!")
        direction = "LONG"
    elif div == "bearish":
        score += 12
        signals.append("🔥 Bärische RSI Divergenz!")
        direction = "SHORT"

    # CVD
    if cvd_val > 0:
        score += 5
        signals.append(f"✅ CVD positiv (+{cvd_val:.0f}) – echte Käufer da")
    else:
        score += 5
        signals.append(f"✅ CVD negativ ({cvd_val:.0f}) – echte Verkäufer da")

    # ── Sentiment: Funding, OI, L/S Ratio ───────────────────
    funding  = get_funding_rate(SYMBOL)
    ls_ratio = get_long_short_ratio(SYMBOL)

    # Negative Funding = Short-Squeeze möglich
    if funding < -0.01:
        score += 8
        signals.append(f"🔥 Funding negativ ({funding*100:.3f}%) – Short Squeeze!")
        direction = "LONG"
    elif funding > 0.03:
        score += 8
        signals.append(f"🔥 Funding sehr positiv ({funding*100:.3f}%) – Long Squeeze!")
        direction = "SHORT"

    # Long/Short Ratio
    if ls_ratio > 0.70:
        score += 5
        signals.append(f"⚠️ {ls_ratio*100:.0f}% sind Long – Smart Money holt sie ab (SHORT Signal)")
        if not direction:
            direction = "SHORT"
    elif ls_ratio < 0.35:
        score += 5
        signals.append(f"⚠️ Nur {ls_ratio*100:.0f}% Long – Squeeze nach oben möglich (LONG Signal)")
        if not direction:
            direction = "LONG"

    # ── Fallback Direction ───────────────────────────────────
    if not direction:
        direction = "LONG" if bullish_ctx else "SHORT"

    # ── Targets berechnen ────────────────────────────────────
    targets = calculate_targets(price, direction, sr, current_atr, c_entry)

    return {
        "score":     min(score, 100),
        "direction": direction,
        "price":     price,
        "atr":       current_atr,
        "signals":   signals,
        "targets":   targets,
        "label":     label,
        "emoji":     emoji,
        "window":    window,
        "funding":   funding,
        "ls_ratio":  ls_ratio,
        "rsi":       rsi_val,
    }

# ─────────────────────────────────────────────────────────────
# TARGETS BERECHNEN
# ─────────────────────────────────────────────────────────────
def calculate_targets(price: float, direction: str, sr: dict,
                      current_atr: float, candles: list) -> dict:
    if direction == "LONG":
        entry_low  = round(price - current_atr * 0.3, 2)
        entry_high = round(price + current_atr * 0.1, 2)
        stop       = round(price - current_atr * 1.5, 2)

        resistances = [r for r in sr["resistances"] if r > price]
        t1 = round(resistances[0], 2)  if resistances        else round(price + current_atr * 2, 2)
        t2 = round(resistances[1], 2)  if len(resistances)>1 else round(price + current_atr * 3.5, 2)
        t3 = round(resistances[2], 2)  if len(resistances)>2 else round(price + current_atr * 6,   2)

    else:  # SHORT
        entry_low  = round(price - current_atr * 0.1, 2)
        entry_high = round(price + current_atr * 0.3, 2)
        stop       = round(price + current_atr * 1.5, 2)

        supports = [s for s in sr["supports"] if s < price]
        supports = sorted(supports, reverse=True)
        t1 = round(supports[0], 2)  if supports        else round(price - current_atr * 2, 2)
        t2 = round(supports[1], 2)  if len(supports)>1 else round(price - current_atr * 3.5, 2)
        t3 = round(supports[2], 2)  if len(supports)>2 else round(price - current_atr * 6,   2)

    rr1 = round(abs(t1 - price) / abs(price - stop), 1) if abs(price - stop) > 0 else 0
    rr2 = round(abs(t2 - price) / abs(price - stop), 1) if abs(price - stop) > 0 else 0
    rr3 = round(abs(t3 - price) / abs(price - stop), 1) if abs(price - stop) > 0 else 0

    return {
        "entry_low":  entry_low,
        "entry_high": entry_high,
        "stop":       stop,
        "t1":         t1,
        "t2":         t2,
        "t3":         t3,
        "rr1":        rr1,
        "rr2":        rr2,
        "rr3":        rr3,
        "breakeven":  round(price, 2),
    }

# ─────────────────────────────────────────────────────────────
# SCORE → GRADE
# ─────────────────────────────────────────────────────────────
def grade(score: int) -> str:
    if score >= 88: return "A+++"
    if score >= 80: return "A++"
    if score >= 72: return "A+"
    if score >= 64: return "A"
    return "B"

# ─────────────────────────────────────────────────────────────
# TELEGRAM
# ─────────────────────────────────────────────────────────────
def send_telegram(message: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={
            "chat_id":    TELEGRAM_CHAT_ID,
            "text":       message,
            "parse_mode": "HTML"
        }, timeout=10)
        r.raise_for_status()
        print(f"[Telegram] Alert gesendet ✅")
    except Exception as e:
        print(f"[Fehler] Telegram: {e}")

def build_alert(result: dict) -> str:
    t  = result["targets"]
    g  = grade(result["score"])
    di = "🟢 LONG" if result["direction"] == "LONG" else "🔴 SHORT"

    top_signals = "\n".join(f"  {s}" for s in result["signals"][:6])

    msg = (
        f"{result['emoji']} <b>{result['label']} {di} – {g}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 <b>Paar:</b> SOL/USDT\n"
        f"📈 <b>Score:</b> {result['score']}/100 ({g})\n"
        f"⏰ <b>Fenster:</b> {result['window']}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📍 <b>Limit Order:</b> ${t['entry_low']} – ${t['entry_high']}\n"
        f"🛑 <b>Stop Loss:</b> ${t['stop']}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 <b>T1:</b> ${t['t1']}  (RR 1:{t['rr1']}) ← Min. Move\n"
        f"🎯 <b>T2:</b> ${t['t2']}  (RR 1:{t['rr2']}) ← Wahrscheinlich\n"
        f"🚀 <b>T3:</b> ${t['t3']}  (RR 1:{t['rr3']}) ← Wenn es zündet\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 <b>Signale:</b>\n{top_signals}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📉 RSI: {result['rsi']:.1f} | "
        f"Funding: {result['funding']*100:.3f}% | "
        f"L/S: {result['ls_ratio']*100:.0f}%\n"
        f"⚠️ <i>Kein Auto-Trade – du entscheidest!</i>"
    )
    return msg

def build_breakeven_alert(trade_id: str, trade: dict) -> str:
    return (
        f"🔔 <b>BREAK EVEN ZEIT!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ <b>T1 erreicht:</b> ${trade['t1']}\n"
        f"🛡️ <b>Stop jetzt verschieben auf:</b> ${trade['entry']}\n"
        f"💰 <b>Ab jetzt risikofreier Trade!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 T2 läuft noch: ${trade['t2']}\n"
        f"🚀 T3 läuft noch: ${trade['t3']}\n"
        f"⚠️ <i>Denk daran deinen Stop zu verschieben!</i>"
    )

def build_target_alert(level: str, price: float, trade: dict) -> str:
    emojis = {"T2": "🎯🎯", "T3": "🚀🚀🚀"}
    msgs   = {
        "T2": "Überleg ob du Teilgewinne mitnimmst!",
        "T3": "Voller Move gelaufen – Gewinne sichern!"
    }
    return (
        f"{emojis[level]} <b>{level} ERREICHT!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 <b>Preis:</b> ${price:.2f}\n"
        f"💡 {msgs[level]}\n"
        f"🚀 T3: ${trade['t3']}"
        if level == "T2" else
        f"{emojis[level]} <b>T3 ERREICHT!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🏆 <b>Voller Move gelaufen!</b>\n"
        f"💰 <b>Preis:</b> ${price:.2f}\n"
        f"💡 Gewinne sichern – top getradet!"
    )

def build_stop_alert(price: float, stop: float) -> str:
    return (
        f"❌ <b>STOP LOSS GETRIGGERT</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📉 <b>Preis:</b> ${price:.2f}\n"
        f"🛑 <b>Stop war bei:</b> ${stop:.2f}\n"
        f"💡 Setup invalidiert – warte auf nächsten A+++ Entry.\n"
        f"😤 Kein Revenge Trading!"
    )

# ─────────────────────────────────────────────────────────────
# TRADE ÜBERWACHUNG (Break Even / Targets / Stop)
# ─────────────────────────────────────────────────────────────
def monitor_trades():
    if not active_trades:
        return

    price = get_current_price(SYMBOL)
    if price == 0:
        return

    to_remove = []

    for trade_id, trade in active_trades.items():
        direction = trade["direction"]
        alerted   = trade.get("alerted_levels", set())

        if direction == "LONG":
            # Break Even (T1)
            if price >= trade["t1"] and "BE" not in alerted:
                send_telegram(build_breakeven_alert(trade_id, trade))
                alerted.add("BE")

            # T2
            if price >= trade["t2"] and "T2" not in alerted:
                send_telegram(build_target_alert("T2", price, trade))
                alerted.add("T2")

            # T3
            if price >= trade["t3"] and "T3" not in alerted:
                send_telegram(build_target_alert("T3", price, trade))
                alerted.add("T3")
                to_remove.append(trade_id)

            # Stop
            if price <= trade["stop"] and "SL" not in alerted:
                send_telegram(build_stop_alert(price, trade["stop"]))
                alerted.add("SL")
                to_remove.append(trade_id)

        else:  # SHORT
            if price <= trade["t1"] and "BE" not in alerted:
                send_telegram(build_breakeven_alert(trade_id, trade))
                alerted.add("BE")

            if price <= trade["t2"] and "T2" not in alerted:
                send_telegram(build_target_alert("T2", price, trade))
                alerted.add("T2")

            if price <= trade["t3"] and "T3" not in alerted:
                send_telegram(build_target_alert("T3", price, trade))
                alerted.add("T3")
                to_remove.append(trade_id)

            if price >= trade["stop"] and "SL" not in alerted:
                send_telegram(build_stop_alert(price, trade["stop"]))
                alerted.add("SL")
                to_remove.append(trade_id)

        trade["alerted_levels"] = alerted

    for tid in to_remove:
        del active_trades[tid]
        print(f"[Trade] {tid} abgeschlossen / entfernt.")

# ─────────────────────────────────────────────────────────────
# COOLDOWN – nicht 10x dasselbe Setup senden
# ─────────────────────────────────────────────────────────────
last_alert = {"scalp": 0, "intraday": 0}
COOLDOWN   = {"scalp": 1800, "intraday": 7200}  # 30min / 2h

# ─────────────────────────────────────────────────────────────
# HAUPT-LOOP
# ─────────────────────────────────────────────────────────────
def run():
    print("╔══════════════════════════════════════════════╗")
    print("║     SOL A+++ Setup Scanner – läuft! 🚀      ║")
    print("╚══════════════════════════════════════════════╝")
    send_telegram(
        "🤖 <b>SOL Scanner gestartet!</b>\n"
        "Ich beobachte SOL/USDT auf A+++ Setups.\n"
        "Scalping (5/15min) + Intraday (1H/4H)\n"
        "Du bekommst einen Alert wenn es soweit ist! 🎯"
    )

    scalp_timer    = 0
    intraday_timer = 0

    while True:
        now = time.time()

        # ── Scalp Check ──────────────────────────────────────
        if now - scalp_timer >= SCALP_CHECK_SECONDS:
            scalp_timer = now
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Scalp Check...")
            result = analyze("scalp")
            if result:
                print(f"  Scalp Score: {result['score']}/100 ({grade(result['score'])})")
                if (result["score"] >= ALERT_THRESHOLD and
                        now - last_alert["scalp"] > COOLDOWN["scalp"]):
                    send_telegram(build_alert(result))
                    last_alert["scalp"] = now
                    # Trade zur Überwachung hinzufügen
                    tid = f"scalp_{int(now)}"
                    t   = result["targets"]
                    active_trades[tid] = {
                        "direction": result["direction"],
                        "entry":     result["price"],
                        "stop":      t["stop"],
                        "t1":        t["t1"],
                        "t2":        t["t2"],
                        "t3":        t["t3"],
                        "alerted_levels": set(),
                    }

        # ── Intraday Check ───────────────────────────────────
        if now - intraday_timer >= INTRADAY_CHECK_SECONDS:
            intraday_timer = now
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Intraday Check...")
            result = analyze("intraday")
            if result:
                print(f"  Intraday Score: {result['score']}/100 ({grade(result['score'])})")
                if (result["score"] >= ALERT_THRESHOLD and
                        now - last_alert["intraday"] > COOLDOWN["intraday"]):
                    send_telegram(build_alert(result))
                    last_alert["intraday"] = now
                    tid = f"intraday_{int(now)}"
                    t   = result["targets"]
                    active_trades[tid] = {
                        "direction": result["direction"],
                        "entry":     result["price"],
                        "stop":      t["stop"],
                        "t1":        t["t1"],
                        "t2":        t["t2"],
                        "t3":        t["t3"],
                        "alerted_levels": set(),
                    }

        # ── Trade Monitoring ─────────────────────────────────
        monitor_trades()

        time.sleep(30)

if __name__ == "__main__":
    run()
