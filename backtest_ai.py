"""
SOL Scanner V14 – AI Backtest
Läuft V14-Logik über historische OKX-Daten und testet Claude AI-Analyse
"""

import requests
import time
import pandas as pd
import os
import json
from datetime import datetime, timezone

SYMBOL = "SOL-USDT"
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# ─── DATEN ──────────────────────────────────────────────────

def get_candles_hist(symbol, bar, after_ts=None, limit=100):
    """Historische Kerzen von OKX (paginiert via after = älter als ts)"""
    params = {"instId": symbol, "bar": bar, "limit": limit}
    if after_ts: params["after"] = str(after_ts)
    try:
        r = requests.get("https://www.okx.com/api/v5/market/history-candles",
            params=params, timeout=15)
        data = r.json().get("data", [])
        if not data: return None
        df = pd.DataFrame(data, columns=["ts","open","high","low","close","vol","v1","v2","c"])
        for col in ["open","high","low","close","vol"]: df[col] = pd.to_numeric(df[col])
        df["ts"] = pd.to_numeric(df["ts"])
        return df.iloc[::-1].reset_index(drop=True)  # älteste zuerst
    except Exception as e:
        print(f"  [Fehler] Candles {bar}: {e}")
        return None

def fetch_full_history(symbol, bar, days=14):
    """Lädt mehrere Seiten OKX-Daten"""
    print(f"  Lade {bar} Daten ({days} Tage)...")
    all_frames = []
    target_ms = int(time.time() * 1000) - days * 24 * 3600 * 1000
    after_ts = None  # None = neueste Seite zuerst

    for page in range(25):
        df = get_candles_hist(symbol, bar, after_ts=after_ts, limit=100)
        if df is None or len(df) == 0: break
        all_frames.append(df)
        oldest_ts = df["ts"].iloc[0]
        if oldest_ts <= target_ms: break
        after_ts = oldest_ts  # nächste Seite: älter als diese
        time.sleep(0.25)

    if not all_frames: return None
    result = pd.concat(all_frames).drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
    # Nur die gewünschte Zeitspanne
    result = result[result["ts"] >= target_ms].reset_index(drop=True)
    print(f"  → {len(result)} Kerzen geladen ({bar})")
    return result

# ─── V14 LOGIK (aus bot.py kopiert) ─────────────────────────

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

def get_trend_at(df, idx):
    """Trend zu einem bestimmten Index"""
    window = df.iloc[max(0, idx-50):idx+1]
    if len(window) < 30: return "NEUTRAL"
    ema21 = calc_ema(window["close"], 21).iloc[-1]
    ema50 = calc_ema(window["close"], 50).iloc[-1]
    price = window["close"].iloc[-1]
    highs = window["high"].tail(10); lows = window["low"].tail(10)
    hh_hl = highs.iloc[-1] > highs.iloc[0] and lows.iloc[-1] > lows.iloc[0]
    lh_ll = highs.iloc[-1] < highs.iloc[0] and lows.iloc[-1] < lows.iloc[0]
    bull  = sum([price > ema21, ema21 > ema50, hh_hl])
    bear  = sum([price < ema21, ema21 < ema50, lh_ll])
    if bull >= 2: return "BULLISH"
    if bear >= 2: return "BEARISH"
    return "NEUTRAL"

def detect_smart_money_at(df15, idx):
    """V14 Smart Money Detection an Position idx"""
    if idx < 20: return None, []
    window = df15.iloc[max(0, idx-60):idx+1].reset_index(drop=True)
    if len(window) < 20: return None, []

    last = window.iloc[-1]
    price = last["close"]

    # ATR Expansion
    ranges = (window["high"] - window["low"]).tail(9)
    avg_range = ranges.iloc[:-1].mean()
    cur_range = ranges.iloc[-1]
    atr_expand = cur_range / (avg_range + 1e-10)
    if atr_expand < 1.4:
        return None, []

    # Stille Akkumulation
    pre_window = window.tail(9).iloc[:-1]
    w_range_pct = (pre_window["high"].max() - pre_window["low"].min()) / price * 100
    if w_range_pct > 2.5:
        return None, []

    # Volumen Explosion
    vol_avg = window["vol"].tail(20).mean()
    vol_now = window["vol"].iloc[-1]
    vol_ratio = vol_now / (vol_avg + 1e-10)
    if vol_ratio < 1.8:
        return None, []

    # Kerzenmuster
    candle_body = abs(last["close"] - last["open"])
    body_pct = candle_body / (cur_range + 1e-10)
    if body_pct < 0.3:
        return None, []

    direction = "LONG" if last["close"] > last["open"] else "SHORT"
    atr_val = calc_atr(window).iloc[-1]

    return {
        "direction": direction,
        "price": round(price, 3),
        "atr_expand": round(atr_expand, 2),
        "vol_ratio": round(vol_ratio, 2),
        "w_range_pct": round(w_range_pct, 2),
        "body_pct": round(body_pct * 100),
        "atr_val": atr_val
    }, [f"ATR:{atr_expand:.1f}x Vol:{vol_ratio:.1f}x Akku:{w_range_pct:.1f}%"]

def calc_quality_score_simple(sm_data):
    """Vereinfachter Quality Score für Backtest"""
    score = 50
    vr = sm_data["vol_ratio"]
    if vr > 5.0:   score += 15
    elif vr > 3.5: score += 10
    elif vr > 2.5: score += 7
    else:          score += 3
    ae = sm_data["atr_expand"]
    if ae > 3.0:   score += 10
    elif ae > 2.0: score += 7
    else:          score += 3
    wr = sm_data["w_range_pct"]
    if wr < 0.8:   score += 10
    elif wr < 1.5: score += 5
    else:          score += 2
    return min(100, score)

def simulate_outcome(df15, signal_idx, direction, atr):
    """Simuliert Trade-Outcome nach Signal"""
    entry = df15.iloc[signal_idx]["close"]
    sl   = entry - atr * 1.5 if direction == "LONG" else entry + atr * 1.5
    t1   = entry + atr * 1.5 if direction == "LONG" else entry - atr * 1.5
    t2   = entry + atr * 3.0 if direction == "LONG" else entry - atr * 3.0
    t3   = entry + atr * 7.0 if direction == "LONG" else entry - atr * 7.0

    future = df15.iloc[signal_idx+1:signal_idx+50]
    for _, candle in future.iterrows():
        if direction == "LONG":
            if candle["low"] <= sl:  return "SL",  round((sl-entry)/entry*100, 2)
            if candle["high"] >= t3: return "T3",  round((t3-entry)/entry*100, 2)
            if candle["high"] >= t2: return "T2",  round((t2-entry)/entry*100, 2)
            if candle["high"] >= t1: return "T1",  round((t1-entry)/entry*100, 2)
        else:
            if candle["high"] >= sl: return "SL",  round((entry-sl)/entry*100, 2)
            if candle["low"] <= t3:  return "T3",  round((entry-t3)/entry*100, 2)
            if candle["low"] <= t2:  return "T2",  round((entry-t2)/entry*100, 2)
            if candle["low"] <= t1:  return "T1",  round((entry-t1)/entry*100, 2)
    return "OPEN", 0.0

# ─── AI ANALYSE ─────────────────────────────────────────────

def get_ai_analysis(signal):
    """Claude AI Bewertung für ein Signal"""
    if not ANTHROPIC_API_KEY:
        return "(Kein ANTHROPIC_API_KEY gesetzt)"

    prompt = (
        f"Du bist ein erfahrener Crypto-Trader. Analysiere dieses SOL/USDT Signal in max 3 Zeilen auf Deutsch.\n\n"
        f"Signal: {signal['direction']}\n"
        f"Score: {signal['score']}%\n"
        f"ATR Expansion: {signal['atr_expand']}x\n"
        f"Volumen Ratio: {signal['vol_ratio']}x\n"
        f"Akkumulation Range: {signal['w_range_pct']}%\n"
        f"4h + 1h Trend: {signal['trend']}\n\n"
        f"Bewerte: Trending oder choppy? Vertrauenswürdig? Max 3 Zeilen, direkt."
    )
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 120,
                "messages": [{"role": "user", "content": prompt}]
            },
            timeout=15
        )
        if r.status_code == 200:
            return r.json()["content"][0]["text"].strip()
        return f"(API Error {r.status_code})"
    except Exception as e:
        return f"(Fehler: {e})"

# ─── MAIN BACKTEST ───────────────────────────────────────────

def run_backtest(days=14):
    print("=" * 60)
    print(f"  SOL Scanner V14 – AI Backtest ({days} Tage)")
    print("=" * 60)

    df15 = fetch_full_history(SYMBOL, "15m", days=days)
    df1h = fetch_full_history(SYMBOL, "1H",  days=days)
    df4h = fetch_full_history(SYMBOL, "4H",  days=days)

    if df15 is None or df1h is None or df4h is None:
        print("Fehler beim Laden der Daten!"); return

    # Hilfsfunktion: Trend bei bestimmtem Timestamp
    def get_trend_ts(df, ts):
        idx = (df["ts"] <= ts).sum() - 1
        if idx < 50: return "NEUTRAL"
        return get_trend_at(df, idx)

    signals = []
    last_signal_ts = 0
    COOLDOWN_MS = 3600 * 1000  # 1h

    print(f"\nScanne {len(df15)} x 15m Kerzen...\n")

    for i in range(50, len(df15)):
        ts = df15.iloc[i]["ts"]

        # Cooldown
        if ts - last_signal_ts < COOLDOWN_MS:
            continue

        # Trends
        trend_4h = get_trend_ts(df4h, ts)
        trend_1h = get_trend_ts(df1h, ts)

        if trend_4h == "NEUTRAL" or trend_1h == "NEUTRAL": continue
        if trend_4h != trend_1h: continue

        direction = "LONG" if trend_4h == "BULLISH" else "SHORT"

        # Smart Money
        sm_data, sm_logs = detect_smart_money_at(df15, i)
        if sm_data is None: continue
        if sm_data["direction"] != direction: continue

        # Score
        score = calc_quality_score_simple(sm_data)
        if score < 70: continue

        grade = "A+++" if score >= 85 else ("A++" if score >= 75 else "A+")
        dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
        atr = max(sm_data["atr_val"], sm_data["price"] * 0.004)
        outcome, pnl = simulate_outcome(df15, i, direction, atr)

        sig = {
            "dt": dt, "ts": ts,
            "direction": direction,
            "price": sm_data["price"],
            "score": score, "grade": grade,
            "atr_expand": sm_data["atr_expand"],
            "vol_ratio": sm_data["vol_ratio"],
            "w_range_pct": sm_data["w_range_pct"],
            "trend": trend_4h,
            "outcome": outcome, "pnl": pnl
        }
        signals.append(sig)
        last_signal_ts = ts

        icon = "🟢" if outcome in ["T1","T2","T3"] else "🔴"
        print(f"{icon} [{dt}] {direction:5s} | Score:{score}% {grade} | ATR:{sm_data['atr_expand']}x Vol:{sm_data['vol_ratio']}x | → {outcome} ({pnl:+.1f}%)")

    # Statistik
    print(f"\n{'='*60}")
    print(f"  ERGEBNIS: {len(signals)} Signale in {days} Tagen")
    if signals:
        wins   = [s for s in signals if s["outcome"] in ["T1","T2","T3"]]
        losses = [s for s in signals if s["outcome"] == "SL"]
        print(f"  Wins:   {len(wins)} ({round(len(wins)/len(signals)*100)}%)")
        print(f"  Losses: {len(losses)} ({round(len(losses)/len(signals)*100)}%)")
        if losses:
            avg_loss = sum(s["pnl"] for s in losses) / len(losses)
            avg_win  = sum(s["pnl"] for s in wins)  / len(wins) if wins else 0
            print(f"  Avg Win: {avg_win:+.2f}% | Avg Loss: {avg_loss:+.2f}%")
        print(f"{'='*60}")

        # AI Analyse für die letzten 3 Signale
        if ANTHROPIC_API_KEY:
            print("\n🤖 AI ANALYSE (letzte 3 Signale):\n")
            for sig in signals[-3:]:
                print(f"  [{sig['dt']}] {sig['direction']} | {sig['outcome']}")
                ai = get_ai_analysis(sig)
                for line in ai.split("\n"):
                    if line.strip(): print(f"    → {line.strip()}")
                print()
        else:
            print("\n⚠️  ANTHROPIC_API_KEY nicht gesetzt – AI Analyse übersprungen")
            print("    Setze: export ANTHROPIC_API_KEY=sk-ant-...")

    # Ergebnisse als JSON speichern (numpy → native Python)
    def convert(o):
        if hasattr(o, "item"): return o.item()
        return o
    with open("/home/claude/backtest_results.json", "w") as f:
        json.dump(signals, f, indent=2, default=convert)
    print(f"\n📁 Ergebnisse gespeichert: backtest_results.json")

if __name__ == "__main__":
    run_backtest(days=14)
