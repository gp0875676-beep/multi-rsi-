"""
╔══════════════════════════════════════════════════════════════════╗
║         🛡️ MILITARY-GRADE CRYPTO BOT — FREE APIs ONLY 🛡️         ║
║   Sources: Binance Public + CoinGecko + Telegram + Flask         ║
║   NO CARD REQUIRED | NO PAID API | 100% FREE TO RUN             ║
╚══════════════════════════════════════════════════════════════════╝

SETUP GUIDE (5 minutes):
─────────────────────────
1. pip install flask requests pandas pandas-ta telebot ccxt python-dotenv

2. Create .env file with:
   TELEGRAM_BOT_TOKEN=your_token   ← Get from @BotFather on Telegram
   TELEGRAM_CHAT_ID=your_chat_id   ← Get from @userinfobot on Telegram
   WEBHOOK_SECRET=LION_STRIKE_100X ← Any secret you want

3. For Webhook Mode: Deploy on Render.com (FREE tier)
   For Polling Mode: Run locally — python military_crypto_bot.py

TradingView Alert JSON (set this in TradingView alert):
{
  "passphrase": "LION_STRIKE_100X",
  "ticker": "{{ticker}}",
  "price": {{close}},
  "rvol": {{volume}} / {{sma(volume,20)}}
}
"""

import os
import time
import threading
import requests
import pandas as pd
import pandas_ta as ta
from flask import Flask, request, jsonify
from dotenv import load_dotenv

# ─── LOAD CONFIG ───────────────────────────────────────────────────────────────
load_dotenv()

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
CHAT_ID          = os.getenv("TELEGRAM_CHAT_ID",   "YOUR_CHAT_ID_HERE")
WEBHOOK_SECRET   = os.getenv("WEBHOOK_SECRET",     "LION_STRIKE_100X")
MODE             = os.getenv("MODE", "POLLING")  # "POLLING" or "WEBHOOK"

# ─── FREE API ENDPOINTS (No Key Required) ──────────────────────────────────────
BINANCE_KLINES_URL   = "https://api.binance.com/api/v3/klines"
BINANCE_TICKER_URL   = "https://api.binance.com/api/v3/ticker/24hr"
BINANCE_FUTURES_URL  = "https://fapi.binance.com/fapi/v1/fundingRate"  # Free futures data
BINANCE_OI_URL       = "https://fapi.binance.com/fapi/v1/openInterest"
COINGECKO_URL        = "https://api.coingecko.com/api/v3"

app = Flask(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# 📊 MODULE 1: TECHNICAL ANALYSIS (Binance Public API — 100% Free)
# ══════════════════════════════════════════════════════════════════════════════

def get_technical_analysis(symbol: str) -> dict:
    """
    Fetch live 5m OHLCV from Binance public API.
    Calculates: RSI, MFI, VWAP, Relative Volume (RVOL), Volume Spike detection.
    NO API KEY NEEDED.
    """
    try:
        params = {
            "symbol": symbol.upper().replace("/", "") + "USDT" if "USDT" not in symbol else symbol,
            "interval": "5m",
            "limit": 100
        }
        # Clean symbol (e.g. "PEPE" → "PEPEUSDT")
        clean_sym = symbol.upper().replace("/USDT", "").replace("USDT", "") + "USDT"
        params["symbol"] = clean_sym

        resp = requests.get(BINANCE_KLINES_URL, params=params, timeout=10)
        resp.raise_for_status()
        raw = resp.json()

        df = pd.DataFrame(raw, columns=[
            'ts', 'open', 'high', 'low', 'close', 'volume',
            'close_ts', 'quote_vol', 'trades', 'taker_buy_base',
            'taker_buy_quote', 'ignore'
        ])
        df[['open','high','low','close','volume']] = df[['open','high','low','close','volume']].astype(float)

        # ── Indicators ──
        df['rsi']  = ta.rsi(df['close'], length=14)
        df['mfi']  = ta.mfi(df['high'], df['low'], df['close'], df['volume'], length=14)

        # VWAP (cumulative intraday approximation)
        df['typical_price'] = (df['high'] + df['low'] + df['close']) / 3
        df['vwap'] = (df['typical_price'] * df['volume']).cumsum() / df['volume'].cumsum()

        # Relative Volume (current vol vs 20-bar average)
        vol_avg = df['volume'].rolling(20).mean()
        df['rvol'] = df['volume'] / vol_avg

        latest = df.iloc[-1]

        return {
            "rsi":       round(float(latest['rsi']), 2),
            "mfi":       round(float(latest['mfi']), 2),
            "vwap":      round(float(latest['vwap']), 6),
            "close":     round(float(latest['close']), 6),
            "volume":    round(float(latest['volume']), 2),
            "rvol":      round(float(latest['rvol']), 2),
            "above_vwap": float(latest['close']) > float(latest['vwap']),
            "vol_spike":  float(latest['rvol']) > 2.5,  # RVOL > 2.5x = spike
        }

    except Exception as e:
        print(f"[TA ERROR] {symbol}: {e}")
        return {}


# ══════════════════════════════════════════════════════════════════════════════
# 🔥 MODULE 2: FUTURES DATA (Binance Futures Public API — 100% Free)
# ══════════════════════════════════════════════════════════════════════════════

def get_futures_data(symbol: str) -> dict:
    """
    Fetches Funding Rate and Open Interest from Binance Futures.
    This replaces Coinglass — same data, completely FREE.
    """
    try:
        clean_sym = symbol.upper().replace("/USDT","").replace("USDT","") + "USDT"

        # Funding Rate
        fr_resp = requests.get(BINANCE_FUTURES_URL,
                               params={"symbol": clean_sym, "limit": 1},
                               timeout=10)
        funding_rate = 0.0
        if fr_resp.status_code == 200:
            fr_data = fr_resp.json()
            if fr_data:
                funding_rate = float(fr_data[-1].get("fundingRate", 0)) * 100  # as %

        # Open Interest
        oi_resp = requests.get(BINANCE_OI_URL,
                               params={"symbol": clean_sym},
                               timeout=10)
        open_interest = 0.0
        if oi_resp.status_code == 200:
            oi_data = oi_resp.json()
            open_interest = float(oi_data.get("openInterest", 0))

        return {
            "funding_rate":   round(funding_rate, 4),
            "open_interest":  round(open_interest, 2),
            "funding_neg":    funding_rate < -0.01,   # Negative = shorts paying longs (bullish)
            "funding_pos":    funding_rate > 0.01,    # Positive = longs paying shorts (bearish)
        }

    except Exception as e:
        print(f"[FUTURES ERROR] {symbol}: {e}")
        return {"funding_rate": 0.0, "open_interest": 0.0, "funding_neg": False, "funding_pos": False}


# ══════════════════════════════════════════════════════════════════════════════
# 🐋 MODULE 3: WHALE / SMART MONEY TRACKER (CoinGecko — Free)
# Replaces Arkham (Paid) with on-chain volume analysis via CoinGecko
# ══════════════════════════════════════════════════════════════════════════════

# Map common tickers to CoinGecko IDs (add more as needed)
COINGECKO_ID_MAP = {
    "BTC": "bitcoin", "ETH": "ethereum", "BNB": "binancecoin",
    "SOL": "solana", "PEPE": "pepe", "DOGE": "dogecoin",
    "SHIB": "shiba-inu", "WIF": "dogwifcoin", "BONK": "bonk",
    "ARB": "arbitrum", "OP": "optimism", "AVAX": "avalanche-2",
    "LINK": "chainlink", "UNI": "uniswap", "MATIC": "matic-network",
    "FET": "fetch-ai", "NEAR": "near", "APT": "aptos",
    "SUI": "sui", "INJ": "injective-protocol", "TIA": "celestia",
}

def get_smart_money_signals(symbol: str) -> dict:
    """
    Uses CoinGecko free API to detect:
    - Large volume vs market cap ratio (whale activity proxy)
    - 24h price change direction
    - Exchange inflow/outflow proxy via vol/mcap ratio

    For actual on-chain whale data (free):
    → Check whale-alert.io (free API: 3 req/sec) manually
    → Or use Etherscan free tier for ERC-20 transfers
    """
    try:
        ticker = symbol.upper().replace("/USDT","").replace("USDT","")
        gecko_id = COINGECKO_ID_MAP.get(ticker)

        if not gecko_id:
            return {"whale_status": "🔍 Coin not in map — add to COINGECKO_ID_MAP", "vol_mcap_ratio": 0}

        url = f"{COINGECKO_URL}/coins/{gecko_id}"
        params = {"localization": "false", "tickers": "false",
                  "market_data": "true", "community_data": "false"}

        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()
        market = data.get("market_data", {})

        volume_24h = market.get("total_volume", {}).get("usd", 0)
        mcap       = market.get("market_cap", {}).get("usd", 1)
        change_24h = market.get("price_change_percentage_24h", 0)
        change_1h  = market.get("price_change_percentage_1h_in_currency", {}).get("usd", 0)

        vol_mcap_ratio = round((volume_24h / mcap) * 100, 2) if mcap > 0 else 0

        # Heuristic: High vol/mcap + price drop = whales dumping
        #            High vol/mcap + price rise = accumulation
        if vol_mcap_ratio > 50 and change_24h < -5:
            whale_status = "🚨 HIGH VOL + PRICE DROP — Whale Dump Likely"
            signal = "DUMP"
        elif vol_mcap_ratio > 30 and change_24h > 5:
            whale_status = "🐋 HIGH VOL + PRICE RISING — Smart Money Accumulating"
            signal = "ACCUMULATE"
        elif vol_mcap_ratio > 20:
            whale_status = "⚡ Elevated Activity — Monitor Closely"
            signal = "WATCH"
        else:
            whale_status = "😴 Low Activity — Retail Only"
            signal = "NEUTRAL"

        return {
            "whale_status":    whale_status,
            "signal":          signal,
            "vol_24h_usd":     f"${volume_24h:,.0f}",
            "vol_mcap_ratio":  vol_mcap_ratio,
            "change_24h":      round(change_24h, 2),
            "change_1h":       round(change_1h, 2),
        }

    except Exception as e:
        print(f"[WHALE ERROR] {symbol}: {e}")
        return {"whale_status": "N/A", "signal": "NEUTRAL"}


# ══════════════════════════════════════════════════════════════════════════════
# 🏆 MODULE 4: TOP GAINER FINDER (Binance 24h — Free)
# ══════════════════════════════════════════════════════════════════════════════

def get_top_gainers(top_n: int = 5) -> list:
    """
    Fetch top N gainers from Binance 24h ticker (USDT pairs only).
    Filters out stablecoins and low-volume coins.
    """
    try:
        resp = requests.get(BINANCE_TICKER_URL, timeout=10)
        tickers = resp.json()

        STABLECOINS = {"USDC", "BUSD", "TUSD", "USDP", "DAI", "FDUSD"}
        gainers = []

        for t in tickers:
            sym = t['symbol']
            if not sym.endswith("USDT"):
                continue
            coin = sym.replace("USDT", "")
            if coin in STABLECOINS:
                continue
            vol = float(t.get('quoteVolume', 0))
            change = float(t.get('priceChangePercent', 0))
            if vol < 500_000:  # Skip low volume (< $500k)
                continue
            gainers.append({
                "symbol": coin,
                "change_pct": change,
                "volume_usd": vol,
                "price": float(t.get('lastPrice', 0))
            })

        gainers.sort(key=lambda x: x['change_pct'], reverse=True)
        return gainers[:top_n]

    except Exception as e:
        print(f"[GAINER ERROR] {e}")
        return []


# ══════════════════════════════════════════════════════════════════════════════
# 📱 MODULE 5: TELEGRAM ALERT SYSTEM
# ══════════════════════════════════════════════════════════════════════════════

def send_telegram(message: str):
    """Send formatted message to Telegram."""
    if not TELEGRAM_TOKEN or TELEGRAM_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("[TELEGRAM] Token not set. Printing to console instead:")
        print(message)
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code == 200:
            print("[TELEGRAM] ✅ Alert sent!")
        else:
            print(f"[TELEGRAM] ❌ Error: {resp.text}")
    except Exception as e:
        print(f"[TELEGRAM] Exception: {e}")


def build_alert_message(symbol: str, ta: dict, futures: dict, whale: dict, alert_type: str) -> str:
    """Build the final structured Telegram message."""

    # ── Squeeze Detection ──
    if futures.get("funding_neg") and ta.get("rvol", 0) > 2:
        squeeze = f"✅ SHORT SQUEEZE SETUP — Negative FR ({futures['funding_rate']}%) + RVOL {ta.get('rvol',0)}x"
    elif futures.get("funding_pos") and ta.get("rsi", 50) > 70:
        squeeze = f"⚠️ LONG LIQUIDATION RISK — Positive FR ({futures['funding_rate']}%) + RSI {ta.get('rsi',50)}"
    else:
        squeeze = f"➖ Neutral — FR: {futures.get('funding_rate',0)}%"

    # ── FOMO Trap Detection ──
    is_trap = (
        ta.get("rsi", 0) > 75 and
        futures.get("funding_pos", False) and
        whale.get("signal") == "DUMP"
    )

    trap_line = "🚨 *FOMO TRAP DETECTED — AVOID LONG*" if is_trap else "✅ *Setup Valid — Entry on Pullback*"

    vwap_line = "✅ Above VWAP" if ta.get("above_vwap") else "❌ Below VWAP (Weak)"
    vol_line  = f"🔥 VOLUME SPIKE ({ta.get('rvol',0)}x RVOL)" if ta.get("vol_spike") else f"📊 Normal Volume ({ta.get('rvol',0)}x RVOL)"

    if alert_type == "LONG":
        header = "🚨 *LONG SETUP / PUMP ALERT* 🚨"
    elif alert_type == "TRAP":
        header = "⛔ *FOMO TRAP / RETAIL BAIT ALERT* ⛔"
    else:
        header = "📡 *LIVE MARKET SCAN* 📡"

    msg = (
        f"{header}\n\n"
        f"🪙 *Coin:* ${symbol}/USDT\n"
        f"💰 *Price:* ${ta.get('close', 0)}\n"
        f"📈 *Trigger:* {vol_line}\n\n\n"
        f"📊 *Technical Analysis:*\n"
        f"   RSI (14): `{ta.get('rsi', 'N/A')}`\n"
        f"   MFI (14): `{ta.get('mfi', 'N/A')}`\n"
        f"   VWAP: `{ta.get('vwap', 'N/A')}` — {vwap_line}\n\n"
        f"🔥 *Futures / Squeeze:*\n"
        f"   {squeeze}\n"
        f"   OI: `{futures.get('open_interest', 'N/A')} contracts`\n\n"
        f"🐋 *Smart Money:*\n"
        f"   {whale.get('whale_status', 'N/A')}\n"
        f"   Vol/MCap Ratio: `{whale.get('vol_mcap_ratio', 0)}%`\n"
        f"   24h Change: `{whale.get('change_24h', 0)}%` | 1h: `{whale.get('change_1h', 0)}%`\n\n\n"
        f"🎯 *Verdict:* {trap_line}\n"
        f"⚠️ *Setup:* Look for 5m pullback to VWAP for optimal entry."
    )
    return msg


# ══════════════════════════════════════════════════════════════════════════════
# 🌐 MODULE 6: FLASK WEBHOOK SERVER (For TradingView)
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/tv_webhook', methods=['POST'])
def tradingview_webhook():
    """
    TradingView sends a POST request here when your Pine Script alert fires.
    In TradingView alert message, set JSON:
    {
      "passphrase": "LION_STRIKE_100X",
      "ticker": "{{ticker}}",
      "price": {{close}},
      "rvol": 0
    }
    """
    try:
        data = request.json
        if not data:
            return jsonify({"error": "No JSON body"}), 400

        # Security check
        if data.get("passphrase") != WEBHOOK_SECRET:
            return jsonify({"error": "Unauthorized"}), 403

        coin  = data.get("ticker", "UNKNOWN").replace("BINANCE:", "").replace("USDT.P","").replace("USDT","")
        price = data.get("price", 0)
        rvol  = data.get("rvol", 0)

        print(f"[WEBHOOK] Signal received: {coin} @ {price} | RVOL: {rvol}")

        # Non-blocking: process in background thread
        def process():
            ta_data      = get_technical_analysis(coin)
            futures_data = get_futures_data(coin)
            whale_data   = get_smart_money_signals(coin)

            alert_type = "TRAP" if (ta_data.get("rsi",0) > 75 and futures_data.get("funding_pos")) else "LONG"
            msg = build_alert_message(coin, ta_data, futures_data, whale_data, alert_type)
            send_telegram(msg)

        threading.Thread(target=process, daemon=True).start()
        return jsonify({"status": "✅ Signal received, processing..."}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "🛡️ Military Bot Online", "mode": "webhook"}), 200


# ══════════════════════════════════════════════════════════════════════════════
# ⚙️ MODULE 7: POLLING MODE (Run locally, no server needed)
# ══════════════════════════════════════════════════════════════════════════════

def run_polling_loop(coins: list = None, interval_seconds: int = 300):
    """
    Polling mode: Scans coins every 5 minutes automatically.
    No TradingView needed. Run directly: python military_crypto_bot.py
    """
    print("🛡️ Military Crypto Bot — POLLING MODE")
    print(f"📡 Scanning every {interval_seconds // 60} minutes...")
    print("Press Ctrl+C to stop.\n")

    while True:
        print(f"\n{'='*60}")
        print(f"🕒 Scan: {time.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'='*60}")

        # Auto-detect top gainers if no coins specified
        scan_list = coins
        if not scan_list:
            print("🔍 Fetching top gainers...")
            gainers = get_top_gainers(top_n=3)
            scan_list = [g['symbol'] for g in gainers]
            if gainers:
                gainer_strs = [g['symbol'] + ' (' + f"{g['change_pct']:+.1f}" + '%)' for g in gainers]
                print("🏆 Top Gainers: " + ', '.join(gainer_strs))

        # Scan each coin in parallel
        def scan_coin(symbol):
            try:
                print(f"\n🔎 Analyzing {symbol}...")
                ta_data      = get_technical_analysis(symbol)
                futures_data = get_futures_data(symbol)
                whale_data   = get_smart_money_signals(symbol)

                if not ta_data:
                    print(f"  ⚠️ No data for {symbol}")
                    return

                print(f"  RSI: {ta_data.get('rsi','N/A')} | MFI: {ta_data.get('mfi','N/A')} | RVOL: {ta_data.get('rvol','N/A')}x")
                print(f"  Funding: {futures_data.get('funding_rate','N/A')}% | Whale: {whale_data.get('signal','N/A')}")

                # Alert conditions
                should_alert = (
                    ta_data.get("vol_spike", False) or        # RVOL spike
                    ta_data.get("rsi", 50) > 70 or            # Overbought
                    ta_data.get("rsi", 50) < 30 or            # Oversold
                    futures_data.get("funding_neg", False)     # Short squeeze setup
                )

                if should_alert:
                    alert_type = "TRAP" if (
                        ta_data.get("rsi", 0) > 75 and
                        futures_data.get("funding_pos") and
                        whale_data.get("signal") == "DUMP"
                    ) else "LONG"

                    msg = build_alert_message(symbol, ta_data, futures_data, whale_data, alert_type)
                    send_telegram(msg)
                    print(f"  📱 Alert sent! Type: {alert_type}")
                else:
                    print(f"  ✅ No alert conditions met.")

            except Exception as e:
                print(f"  ❌ Error scanning {symbol}: {e}")

        threads = [threading.Thread(target=scan_coin, args=(sym,)) for sym in scan_list]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        print(f"\n⏳ Next scan in {interval_seconds // 60} minutes...")
        time.sleep(interval_seconds)


# ══════════════════════════════════════════════════════════════════════════════
# 🚀 ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == '__main__':

    # ── CHOOSE YOUR MODE ──────────────────────────────────────────────────────
    #
    # MODE 1 — POLLING (Recommended to start. Run locally, no server needed.)
    #   Set MODE = "POLLING" in .env or below
    #   Scans top gainers every 5 minutes automatically.
    #
    # MODE 2 — WEBHOOK (Advanced. Deploy on Render.com free tier.)
    #   Set MODE = "WEBHOOK" in .env
    #   TradingView pings your server when alert fires.
    #   Server URL: https://your-app.onrender.com/tv_webhook
    # ─────────────────────────────────────────────────────────────────────────

    if MODE == "WEBHOOK":
        print("🛡️ Military-Grade Webhook Server Starting...")
        print(f"📡 Endpoint: /tv_webhook | Secret: {WEBHOOK_SECRET}")
        app.run(host='0.0.0.0', port=int(os.getenv("PORT", 5000)))

    else:  # POLLING mode (default)
        # To track specific coins, replace None with a list:
        # e.g., WATCH_LIST = ["PEPE", "WIF", "BONK", "SOL"]
        WATCH_LIST = None  # None = auto-detect top 3 gainers every cycle

        run_polling_loop(coins=WATCH_LIST, interval_seconds=300)
