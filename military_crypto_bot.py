import os
import time
import threading
import requests
import pandas as pd
from ta.momentum import RSIIndicator
from ta.volume import MFIIndicator
from flask import Flask, request, jsonify
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID         = os.getenv("TELEGRAM_CHAT_ID", "")
WEBHOOK_SECRET  = os.getenv("WEBHOOK_SECRET", "LION_STRIKE_100X")
MODE            = os.getenv("MODE", "POLLING")

BINANCE_KLINES_URL  = "https://api.binance.com/api/v3/klines"
BINANCE_TICKER_URL  = "https://api.binance.com/api/v3/ticker/24hr"
BINANCE_FUTURES_URL = "https://fapi.binance.com/fapi/v1/fundingRate"
BINANCE_OI_URL      = "https://fapi.binance.com/fapi/v1/openInterest"
COINGECKO_URL       = "https://api.coingecko.com/api/v3"

app = Flask(__name__)

COINGECKO_ID_MAP = {
    "BTC": "bitcoin", "ETH": "ethereum", "BNB": "binancecoin",
    "SOL": "solana", "PEPE": "pepe", "DOGE": "dogecoin",
    "SHIB": "shiba-inu", "WIF": "dogwifcoin", "BONK": "bonk",
    "ARB": "arbitrum", "OP": "optimism", "AVAX": "avalanche-2",
    "LINK": "chainlink", "UNI": "uniswap", "MATIC": "matic-network",
    "FET": "fetch-ai", "NEAR": "near", "APT": "aptos",
    "SUI": "sui", "INJ": "injective-protocol", "TIA": "celestia",
    "XRP": "ripple", "ADA": "cardano", "DOT": "polkadot",
    "LTC": "litecoin", "ATOM": "cosmos", "ALGO": "algorand",
}


def clean_symbol(symbol):
    return symbol.upper().replace("/USDT", "").replace("USDT", "").replace("BINANCE:", "").replace(".P", "")


def get_technical_analysis(symbol):
    try:
        sym = clean_symbol(symbol) + "USDT"
        resp = requests.get(BINANCE_KLINES_URL, params={"symbol": sym, "interval": "5m", "limit": 100}, timeout=10)
        resp.raise_for_status()
        raw = resp.json()

        df = pd.DataFrame(raw, columns=[
            'ts', 'open', 'high', 'low', 'close', 'volume',
            'close_ts', 'quote_vol', 'trades', 'taker_buy_base', 'taker_buy_quote', 'ignore'
        ])
        df[['open', 'high', 'low', 'close', 'volume']] = df[['open', 'high', 'low', 'close', 'volume']].astype(float)

        df['rsi'] = RSIIndicator(close=df['close'], window=14).rsi()
        df['mfi'] = MFIIndicator(high=df['high'], low=df['low'], close=df['close'], volume=df['volume'], window=14).money_flow_index()
        df['typical_price'] = (df['high'] + df['low'] + df['close']) / 3
        df['vwap'] = (df['typical_price'] * df['volume']).cumsum() / df['volume'].cumsum()
        vol_avg = df['volume'].rolling(20).mean()
        df['rvol'] = df['volume'] / vol_avg

        latest = df.iloc[-1]

        return {
            "rsi":        round(float(latest['rsi']), 2),
            "mfi":        round(float(latest['mfi']), 2),
            "vwap":       round(float(latest['vwap']), 6),
            "close":      round(float(latest['close']), 6),
            "volume":     round(float(latest['volume']), 2),
            "rvol":       round(float(latest['rvol']), 2),
            "above_vwap": float(latest['close']) > float(latest['vwap']),
            "vol_spike":  float(latest['rvol']) > 2.5,
        }
    except Exception as e:
        print("[TA ERROR] " + str(e))
        return {}


def get_futures_data(symbol):
    try:
        sym = clean_symbol(symbol) + "USDT"
        fr_resp = requests.get(BINANCE_FUTURES_URL, params={"symbol": sym, "limit": 1}, timeout=10)
        funding_rate = 0.0
        if fr_resp.status_code == 200:
            fr_data = fr_resp.json()
            if fr_data:
                funding_rate = float(fr_data[-1].get("fundingRate", 0)) * 100

        oi_resp = requests.get(BINANCE_OI_URL, params={"symbol": sym}, timeout=10)
        open_interest = 0.0
        if oi_resp.status_code == 200:
            oi_data = oi_resp.json()
            open_interest = float(oi_data.get("openInterest", 0))

        return {
            "funding_rate":  round(funding_rate, 4),
            "open_interest": round(open_interest, 2),
            "funding_neg":   funding_rate < -0.01,
            "funding_pos":   funding_rate > 0.01,
        }
    except Exception as e:
        print("[FUTURES ERROR] " + str(e))
        return {"funding_rate": 0.0, "open_interest": 0.0, "funding_neg": False, "funding_pos": False}


def get_smart_money_signals(symbol):
    try:
        ticker = clean_symbol(symbol)
        gecko_id = COINGECKO_ID_MAP.get(ticker)

        if not gecko_id:
            return {"whale_status": "Coin not in map", "vol_mcap_ratio": 0, "signal": "NEUTRAL", "change_24h": 0, "change_1h": 0}

        url = COINGECKO_URL + "/coins/" + gecko_id
        params = {"localization": "false", "tickers": "false", "market_data": "true", "community_data": "false"}
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()
        market = data.get("market_data", {})

        volume_24h = market.get("total_volume", {}).get("usd", 0)
        mcap       = market.get("market_cap", {}).get("usd", 1)
        change_24h = market.get("price_change_percentage_24h", 0) or 0
        change_1h  = (market.get("price_change_percentage_1h_in_currency") or {}).get("usd", 0) or 0
        vol_mcap_ratio = round((volume_24h / mcap) * 100, 2) if mcap > 0 else 0

        if vol_mcap_ratio > 50 and change_24h < -5:
            whale_status = "HIGH VOL + PRICE DROP - Whale Dump Likely"
            signal = "DUMP"
        elif vol_mcap_ratio > 30 and change_24h > 5:
            whale_status = "HIGH VOL + PRICE RISING - Smart Money Accumulating"
            signal = "ACCUMULATE"
        elif vol_mcap_ratio > 20:
            whale_status = "Elevated Activity - Monitor Closely"
            signal = "WATCH"
        else:
            whale_status = "Low Activity - Retail Only"
            signal = "NEUTRAL"

        return {
            "whale_status":   whale_status,
            "signal":         signal,
            "vol_mcap_ratio": vol_mcap_ratio,
            "change_24h":     round(change_24h, 2),
            "change_1h":      round(change_1h, 2),
        }
    except Exception as e:
        print("[WHALE ERROR] " + str(e))
        return {"whale_status": "N/A", "signal": "NEUTRAL", "vol_mcap_ratio": 0, "change_24h": 0, "change_1h": 0}


def get_top_gainers(top_n=3):
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
            if vol < 500000:
                continue
            gainers.append({"symbol": coin, "change_pct": change, "price": float(t.get('lastPrice', 0))})
        gainers.sort(key=lambda x: x['change_pct'], reverse=True)
        return gainers[:top_n]
    except Exception as e:
        print("[GAINER ERROR] " + str(e))
        return []


def send_telegram(message):
    if not TELEGRAM_TOKEN:
        print("[TELEGRAM] Token not set.\n" + message)
        return
    url = "https://api.telegram.org/bot" + TELEGRAM_TOKEN + "/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code == 200:
            print("[TELEGRAM] Alert sent!")
        else:
            print("[TELEGRAM] Error: " + resp.text)
    except Exception as e:
        print("[TELEGRAM] Exception: " + str(e))


def build_alert_message(symbol, ta_data, futures, whale, alert_type):
    if futures.get("funding_neg") and ta_data.get("rvol", 0) > 2:
        squeeze = "SHORT SQUEEZE SETUP - Negative FR (" + str(futures['funding_rate']) + "%) + RVOL " + str(ta_data.get('rvol', 0)) + "x"
    elif futures.get("funding_pos") and ta_data.get("rsi", 50) > 70:
        squeeze = "LONG LIQ RISK - Positive FR (" + str(futures['funding_rate']) + "%) + RSI " + str(ta_data.get('rsi', 50))
    else:
        squeeze = "Neutral - FR: " + str(futures.get('funding_rate', 0)) + "%"

    is_trap = (
        ta_data.get("rsi", 0) > 75 and
        futures.get("funding_pos", False) and
        whale.get("signal") == "DUMP"
    )

    verdict   = "FOMO TRAP - AVOID LONG" if is_trap else "Setup Valid - Entry on Pullback"
    vwap_line = "Above VWAP" if ta_data.get("above_vwap") else "Below VWAP (Weak)"
    vol_line  = "VOLUME SPIKE (" + str(ta_data.get('rvol', 0)) + "x)" if ta_data.get("vol_spike") else "Normal Volume (" + str(ta_data.get('rvol', 0)) + "x)"
    header    = "FOMO TRAP ALERT" if alert_type == "TRAP" else "LONG SETUP / PUMP ALERT"

    msg = (
        "🚨 *" + header + "* 🚨\n\n"
        "🪙 *Coin:* $" + symbol + "/USDT\n"
        "💰 *Price:* $" + str(ta_data.get('close', 0)) + "\n"
        "📈 *Volume:* " + vol_line + "\n\n"
        "📊 *Technical:*\n"
        "   RSI: `" + str(ta_data.get('rsi', 'N/A')) + "`\n"
        "   MFI: `" + str(ta_data.get('mfi', 'N/A')) + "`\n"
        "   VWAP: " + vwap_line + "\n\n"
        "🔥 *Futures:*\n"
        "   " + squeeze + "\n\n"
        "🐋 *Smart Money:*\n"
        "   " + str(whale.get('whale_status', 'N/A')) + "\n"
        "   24h: `" + str(whale.get('change_24h', 0)) + "%` | 1h: `" + str(whale.get('change_1h', 0)) + "%`\n\n"
        "🎯 *Verdict:* " + verdict
    )
    return msg


@app.route('/tv_webhook', methods=['POST'])
def tradingview_webhook():
    try:
        data = request.json
        if not data:
            return jsonify({"error": "No JSON body"}), 400
        if data.get("passphrase") != WEBHOOK_SECRET:
            return jsonify({"error": "Unauthorized"}), 403

        coin = clean_symbol(data.get("ticker", "UNKNOWN"))
        print("[WEBHOOK] Signal: " + coin)

        def process():
            ta_data      = get_technical_analysis(coin)
            futures_data = get_futures_data(coin)
            whale_data   = get_smart_money_signals(coin)
            alert_type   = "TRAP" if (ta_data.get("rsi", 0) > 75 and futures_data.get("funding_pos")) else "LONG"
            msg = build_alert_message(coin, ta_data, futures_data, whale_data, alert_type)
            send_telegram(msg)

        threading.Thread(target=process, daemon=True).start()
        return jsonify({"status": "Signal received"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "Bot Online", "mode": MODE}), 200


def run_polling_loop(coins=None, interval_seconds=300):
    print("Military Crypto Bot — POLLING MODE")
    print("Scanning every " + str(interval_seconds // 60) + " minutes...")

    while True:
        print("\n" + "="*50)
        print("Scan: " + time.strftime('%Y-%m-%d %H:%M:%S'))

        scan_list = coins
        if not scan_list:
            print("Fetching top gainers...")
            gainers   = get_top_gainers(top_n=3)
            scan_list = [g['symbol'] for g in gainers]
            for g in gainers:
                print("  " + g['symbol'] + " (" + "{:+.1f}".format(g['change_pct']) + "%)")

        def scan_coin(symbol):
            try:
                print("Analyzing " + symbol + "...")
                ta_data      = get_technical_analysis(symbol)
                futures_data = get_futures_data(symbol)
                whale_data   = get_smart_money_signals(symbol)

                if not ta_data:
                    print("  No data for " + symbol)
                    return

                print("  RSI:" + str(ta_data.get('rsi')) + " MFI:" + str(ta_data.get('mfi')) + " RVOL:" + str(ta_data.get('rvol')) + "x")

                should_alert = (
                    ta_data.get("vol_spike", False) or
                    ta_data.get("rsi", 50) > 70 or
                    ta_data.get("rsi", 50) < 30 or
                    futures_data.get("funding_neg", False)
                )

                if should_alert:
                    alert_type = "TRAP" if (
                        ta_data.get("rsi", 0) > 75 and
                        futures_data.get("funding_pos") and
                        whale_data.get("signal") == "DUMP"
                    ) else "LONG"
                    msg = build_alert_message(symbol, ta_data, futures_data, whale_data, alert_type)
                    send_telegram(msg)
                    print("  Alert sent! " + alert_type)
                else:
                    print("  No alert conditions met.")
            except Exception as e:
                print("  Error: " + str(e))

        threads = [threading.Thread(target=scan_coin, args=(sym,)) for sym in scan_list]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        print("Next scan in " + str(interval_seconds // 60) + " min...")
        time.sleep(interval_seconds)


if __name__ == '__main__':
    if MODE == "WEBHOOK":
        print("Webhook Server Starting...")
        app.run(host='0.0.0.0', port=int(os.getenv("PORT", 5000)))
    else:
        WATCH_LIST = None  # None = auto top gainers | Ya likho: ["PEPE", "SOL", "WIF"]
        run_polling_loop(coins=WATCH_LIST, interval_seconds=300)
