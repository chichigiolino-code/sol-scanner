# 🚀 SOL A+++ Scanner – Setup Anleitung

## Was du brauchst
- Ein Smartphone mit Telegram
- Einen kostenlosen GitHub Account
- Einen kostenlosen Railway Account
- Ca. 15 Minuten Zeit

---

## SCHRITT 1: Telegram Bot erstellen (5 Min)

### 1a. Bot Token holen
1. Öffne Telegram auf deinem Handy
2. Suche nach **@BotFather**
3. Schreibe: `/newbot`
4. Er fragt nach einem Namen → z.B. `SOL Scanner`
5. Er fragt nach einem Username → z.B. `sol_scanner_patrick_bot`
6. Er gibt dir einen **Token** zurück → sieht so aus:
   ```
   1234567890:AAHdqTcvCH1vGWJxfSeofSs0K95VBo_D80
   ```
7. **Diesen Token kopieren und irgendwo speichern!**

### 1b. Deine Chat ID holen
1. Suche in Telegram nach **@userinfobot**
2. Schreibe `/start`
3. Er antwortet mit deiner **Chat ID** → z.B. `123456789`
4. **Diese Zahl kopieren und speichern!**

### 1c. Bot aktivieren
1. Suche in Telegram nach deinem neuen Bot (dem Namen den du vergeben hast)
2. Drücke **START** – sonst kann er dir keine Nachrichten schicken!

---

## SCHRITT 2: Code auf GitHub hochladen (5 Min)

1. Gehe zu **github.com** und erstelle einen kostenlosen Account
2. Klicke auf **"New Repository"**
3. Name: `sol-scanner`
4. Stelle auf **Private** (wichtig – Token nicht öffentlich!)
5. Klicke **"Create Repository"**
6. Lade die 3 Dateien hoch:
   - `bot.py`
   - `requirements.txt`
   - `Procfile`

---

## SCHRITT 3: Railway einrichten (5 Min)

1. Gehe zu **railway.app**
2. Klicke **"Login with GitHub"**
3. Klicke **"New Project"** → **"Deploy from GitHub repo"**
4. Wähle dein `sol-scanner` Repository
5. Railway erkennt den Code automatisch

### Wichtig – Umgebungsvariablen setzen:
1. Klicke auf deinen Service in Railway
2. Gehe zu **"Variables"**
3. Füge hinzu:
   ```
   TELEGRAM_TOKEN = 1234567890:AAHdqTcvCH1vGWJxfSeofSs0K95VBo_D80
   TELEGRAM_CHAT_ID = 123456789
   ```
   (mit deinen echten Werten natürlich!)
4. Klicke **"Deploy"**

---

## SCHRITT 4: Testen

Nach dem Deploy (ca. 2 Min) sollte dein Bot dir eine Telegram-Nachricht schicken:

```
🤖 SOL Scanner gestartet!
Ich beobachte SOL/USDT auf A+++ Setups.
Scalping (5/15min) + Intraday (1H/4H)
Du bekommst einen Alert wenn es soweit ist! 🎯
```

Wenn diese Nachricht ankommt → alles funktioniert! ✅

---

## Was der Bot überwacht

### Scalping (jede Minute)
- 5min Entry Chart
- 15min Kontext Chart
- Alert wenn Score ≥ 78/100

### Intraday (alle 5 Minuten)
- 1H Entry Chart
- 4H Kontext Chart
- Alert wenn Score ≥ 78/100

### Nach einem Alert überwacht er automatisch:
- 🔔 T1 erreicht → Break Even Alert (Stop verschieben!)
- 🎯 T2 erreicht → Teilgewinne Alert
- 🚀 T3 erreicht → Voller Move Alert
- ❌ Stop getriggert → Setup invalidiert Alert

---

## Score System

| Score | Grade | Bedeutung |
|-------|-------|-----------|
| 88-100 | A+++ | Perfektes Setup – sofort handeln |
| 80-87  | A++  | Sehr starkes Setup |
| 72-79  | A+   | Gutes Setup |
| 64-71  | A    | Solides Setup |
| < 64   | B    | Kein Alert |

---

## Indikatoren die der Bot nutzt

**Layer 1 – Market Context (4H/15min)**
- EMA 21, 50, 200

**Layer 2 – Struktur**
- Pivot Support & Resistance
- Orderblock Erkennung
- VWAP
- Bollinger Squeeze

**Layer 3 – Smart Money Entry**
- Liquiditätssweep (Stop Hunt Erkennung)
- Fair Value Gap (FVG)
- RSI + RSI Divergenz
- CVD (Cumulative Volume Delta)

**Sentiment**
- Funding Rate
- Long/Short Ratio

---

## Kosten

- GitHub: **kostenlos**
- Railway: **kostenlos** (500h/Monat gratis, reicht für 24/7)
- Binance API: **kostenlos** (nur Marktdaten lesen)
- Telegram Bot: **kostenlos**

**Gesamt: 0€** 🎉

---

## Probleme?

**Bot sendet keine Nachricht:**
→ Hast du dem Bot in Telegram /start geschickt?
→ Sind Token und Chat ID korrekt in Railway eingetragen?

**Railway Deploy schlägt fehl:**
→ Sind alle 3 Dateien (bot.py, requirements.txt, Procfile) hochgeladen?

**Bot läuft aber kein Alert:**
→ Normal! Der Bot wartet auf ein echtes A+++ Setup.
→ Schau in Railway unter "Logs" ob Scores angezeigt werden.
