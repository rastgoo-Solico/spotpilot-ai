"""
SpotPilot AI — Market analysis dashboard
Full single-file version: improved backend + complete Streamlit UI.
"""
import statistics
import math
import time
import io
import os
import html
import logging
import requests
import pandas as pd
import numpy as np
import streamlit as st
import ccxt
import plotly.graph_objects as go

from datetime import datetime, timezone
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT, TA_LEFT, TA_CENTER
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak,
    Image as ReportLabImage, LongTable, KeepTogether,
)

# ===========================================================================
# CONFIG
# ===========================================================================
LCW_URL = "https://api.livecoinwatch.com"
LBANK_URL = "https://api.lbank.info/v2"
CRYPTOMETER_URL = "https://api.cryptometer.io"
OTHER_EXCHANGES = ["binance", "bybit", "okx", "kucoin", "kraken"]
QUOTE = "USDT"
PINNED_CODES = ("BTC", "XAUT", "SOL", "ETH")
PINNED_NAMES = {"BTC": "Bitcoin", "XAUT": "Tether Gold", "SOL": "Solana", "ETH": "Ethereum"}
COINGLASS_HEATMAP_URL = "https://www.coinglass.com/pro/futures/LiquidationHeatMap"

TIMEFRAMES = {
    "15 دقیقه": {"type": "minute15", "seconds": 15 * 60, "cache_ttl": 45},
    "4 ساعت":   {"type": "hour4",   "seconds": 4 * 60 * 60, "cache_ttl": 90},
    "روزانه":   {"type": "day1",    "seconds": 24 * 60 * 60, "cache_ttl": 300},
}

DEBUG = os.environ.get("SPOTPILOT_DEBUG", "0") == "1"
logger = logging.getLogger("spotpilot")
if DEBUG:
    logging.basicConfig(level=logging.INFO)


# ===========================================================================
# HTTP
# ===========================================================================
_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": "SpotPilotAI/2.0 (+public-market-data)",
    "Accept": "application/json",
})


def _http_get(url, params=None, timeout=20, retries=2):
    last_error = None
    for attempt in range(max(1, retries + 1)):
        try:
            response = _SESSION.get(url, params=params, timeout=timeout)
            if response.status_code in (408, 425, 429, 500, 502, 503, 504):
                last_error = RuntimeError(f"HTTP {response.status_code}")
                if attempt < retries:
                    delay = min(6.0, 1.2 * (2 ** attempt))
                    ra = response.headers.get("Retry-After")
                    if ra:
                        try:
                            delay = min(8.0, max(1.0, float(ra)))
                        except (TypeError, ValueError):
                            pass
                    time.sleep(delay)
                    continue
                response.raise_for_status()
            response.raise_for_status()
            return response
        except (requests.Timeout, requests.ConnectionError) as exc:
            last_error = exc
            if attempt >= retries:
                raise
            time.sleep(1.2 * (2 ** attempt))
    if last_error:
        raise last_error
    raise RuntimeError("HTTP request failed")


def get_json(url, params=None, timeout=20):
    return _http_get(url, params=params, timeout=timeout).json()


# ===========================================================================
# HELPERS
# ===========================================================================
def number(value, default=0.0):
    try:
        result = float(value) if value is not None else default
        return result if pd.notna(result) else default
    except (TypeError, ValueError):
        return default


def fmt_price(value):
    if value is None:
        return "—"
    try:
        value = float(value)
        if not pd.notna(value) or value <= 0:
            return "—"
        if value >= 1000:
            return f"{value:,.2f}"
        if value >= 1:
            return f"{value:,.6f}".rstrip("0").rstrip(".")
        return f"{value:.10f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return "—"


def fmt_usd(value):
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return "—"


def format_percent(value):
    try:
        value = float(value)
        if not pd.notna(value):
            return "—"
        if value != 0 and abs(value) < 0.0001:
            return "<0.0001%" if value > 0 else ">-0.0001%"
        if abs(value) < 0.01:
            return f"{value:+.4f}%"
        if abs(value) < 1:
            return f"{value:+.3f}%"
        return f"{value:+.2f}%"
    except (TypeError, ValueError):
        return "—"


def is_success(payload):
    if not isinstance(payload, dict):
        return False
    result = payload.get("result")
    if result is True or str(result).lower() == "true":
        return True
    code = payload.get("code")
    if code in (0, "0", "success"):
        return True
    return False


def get_lcw_key():
    try:
        return str(st.secrets["LIVECOINWATCH_API_KEY"]).strip()
    except Exception:
        return ""


def get_cryptometer_key():
    try:
        secret = str(st.secrets.get("CRYPTOMETER_API_KEY", "")).strip()
        if secret:
            return secret
    except Exception:
        pass
    return os.environ.get("CRYPTOMETER_API_KEY", "").strip()


# ===========================================================================
# LIVE COIN WATCH
# ===========================================================================
def lcw_post(endpoint, payload, retries=3):
    key = get_lcw_key()
    if not key:
        raise RuntimeError("LIVECOINWATCH_API_KEY در Streamlit Secrets تنظیم نشده است.")
    last_error = None
    for attempt in range(max(1, int(retries))):
        try:
            response = _SESSION.post(
                f"{LCW_URL}/{endpoint}",
                headers={"content-type": "application/json", "x-api-key": key},
                json=payload,
                timeout=25,
            )
            if response.status_code in (408, 425, 429, 500, 502, 503, 504):
                last_error = RuntimeError(f"LiveCoinWatch موقتاً پاسخ مناسب نداد (HTTP {response.status_code})")
                if attempt < max(1, int(retries)) - 1:
                    ra = response.headers.get("Retry-After")
                    try:
                        delay = min(8.0, max(1.0, float(ra))) if ra else 1.5 * (2 ** attempt)
                    except (TypeError, ValueError):
                        delay = 1.5 * (2 ** attempt)
                    time.sleep(delay)
                    continue
                response.raise_for_status()
            response.raise_for_status()
            return response.json()
        except (requests.Timeout, requests.ConnectionError) as exc:
            last_error = exc
            if attempt >= max(1, int(retries)) - 1:
                raise RuntimeError(f"اتصال به LiveCoinWatch پس از {attempt + 1} تلاش ناموفق بود: {type(exc).__name__}") from exc
            time.sleep(1.5 * (2 ** attempt))
    if last_error:
        raise last_error
    raise RuntimeError("دریافت اطلاعات از LiveCoinWatch ناموفق بود.")


@st.cache_data(ttl=60, show_spinner=False)
def fetch_lcw_market(min_btc_volume):
    btc_usd = None
    all_coins = []
    empty_pages = 0

    for offset in range(0, 2000, 100):
        batch = lcw_post("coins/list", {
            "currency": "USD", "sort": "volume", "order": "descending",
            "offset": offset, "limit": 100, "meta": True,
        })
        if not isinstance(batch, list) or not batch:
            break
        all_coins.extend(batch)

        if btc_usd is None:
            for item in batch:
                if str(item.get("code", "")).upper() == "BTC":
                    btc_usd = number(item.get("rate"), None)
                    break

        if min_btc_volume and btc_usd:
            min_usd = min_btc_volume * btc_usd
            below = sum(1 for c in batch if number(c.get("volume")) < min_usd)
            if below == len(batch) and offset >= 200:
                empty_pages += 1
                if empty_pages >= 2:
                    break
            else:
                empty_pages = 0

        if len(batch) < 100:
            break

    if btc_usd is None or btc_usd <= 0:
        btc = lcw_post("coins/single", {"currency": "USD", "code": "BTC", "meta": False})
        btc_usd = number(btc.get("rate"), None)
    if btc_usd is None or btc_usd <= 0:
        raise RuntimeError("قیمت BTC از LiveCoinWatch معتبر نیست؛ بعداً دوباره تلاش کنید.")

    min_usd_volume = min_btc_volume * btc_usd
    rows = []
    for coin in all_coins:
        coin_code = str(coin.get("code", "")).upper()
        volume_usd = number(coin.get("volume"))
        price_usd = number(coin.get("rate"))

        if volume_usd < min_usd_volume and coin_code not in PINNED_CODES:
            continue

        delta = coin.get("delta") or {}
        day_delta = delta.get("day")
        change_pct = (number(day_delta) - 1) * 100 if day_delta is not None else None

        rows.append({
            "Name": coin.get("name", ""),
            "Code": coin_code,
            "Price USD": price_usd,
            "24h Change %": change_pct,
            "Volume USD": volume_usd,
            "Volume BTC equivalent": volume_usd / btc_usd,
            "Exchanges reported": coin.get("exchanges"),
            "Markets reported": coin.get("markets"),
            "Pairs reported": coin.get("pairs"),
            "Price check": (
                "قیمت نامعتبر" if price_usd <= 0
                else "بررسی دستی" if price_usd >= 1_000_000
                else "—"
            ),
        })

    pin_rank = {code: index for index, code in enumerate(PINNED_CODES)}
    rows.sort(key=lambda row: (
        0 if row["Code"] in pin_rank else 1,
        pin_rank.get(row["Code"], 999),
        -(row["24h Change %"] if row["24h Change %"] is not None else float("-inf")),
    ))
    return rows, btc_usd, len(all_coins)


# ===========================================================================
# LBANK
# ===========================================================================
def lbank_symbol_from_code(code):
    return f"{code.lower()}_usdt"


def lbank_btc_symbol_from_code(code):
    return f"{code.lower()}_btc"


@st.cache_data(ttl=300, show_spinner=False)
def fetch_lbank_pairs():
    payload = get_json(f"{LBANK_URL}/currencyPairs.do")
    if not is_success(payload):
        raise RuntimeError(f"LBank currencyPairs API error: {payload.get('msg', payload)}")
    raw = payload.get("data") or []
    pairs = set()
    for item in raw:
        if isinstance(item, str):
            pair = item.lower().replace("-", "_").replace("/", "_")
            if pair.endswith(("_usdt", "_btc")):
                pairs.add(pair)
        elif isinstance(item, dict):
            candidate = item.get("symbol") or item.get("pair") or item.get("name") or ""
            if candidate:
                pair = str(candidate).lower().replace("-", "_").replace("/", "_")
                if pair.endswith(("_usdt", "_btc")):
                    pairs.add(pair)
    if not pairs:
        raise RuntimeError("جفت‌ارزهای USDT قابل شناسایی نبودند.")
    return sorted(pairs)


@st.cache_data(ttl=15, show_spinner=False)
def fetch_lbank_book(pair):
    payload = get_json(f"{LBANK_URL}/depth.do", params={"symbol": pair, "size": 100})
    if not is_success(payload):
        raise RuntimeError(f"LBank depth API error: {payload.get('msg', payload)}")
    data = payload.get("data") or {}
    bids = data.get("bids") or []
    asks = data.get("asks") or []
    if not bids or not asks:
        raise RuntimeError(f"دفتر سفارش {pair} خالی است.")
    return {"bids": bids, "asks": asks, "timestamp": data.get("timestamp") or payload.get("ts")}


@st.cache_data(ttl=15, show_spinner=False)
def fetch_lbank_trades(pair):
    payload = get_json(f"{LBANK_URL}/supplement/trades.do", params={"symbol": pair, "size": 500})
    if not is_success(payload):
        raise RuntimeError(f"LBank trades API error: {payload.get('msg', payload)}")
    data = payload.get("data") or []
    normalized = []
    for trade in data:
        price = number(trade.get("price"))
        amount = number(trade.get("qty"))
        cost = number(trade.get("quoteQty"))
        if cost <= 0:
            cost = price * amount
        maker = trade.get("isBuyerMaker")
        if maker is True or str(maker).lower() == "true":
            side = "sell"
        elif maker is False or str(maker).lower() == "false":
            side = "buy"
        else:
            side = None
        normalized.append({
            "price": price, "amount": amount, "cost": cost,
            "side": side, "time": trade.get("time"),
        })
    return normalized


@st.cache_data(ttl=15, show_spinner=False)
def fetch_lbank_ticker(pair):
    payload = get_json(f"{LBANK_URL}/supplement/ticker/price.do", params={"symbol": pair})
    if not is_success(payload):
        raise RuntimeError(f"LBank ticker API error: {payload.get('msg', payload)}")
    data = payload.get("data") or []
    if isinstance(data, dict):
        data = [data]
    for ticker in data:
        if str(ticker.get("symbol", "")).lower() == pair.lower():
            return number(ticker.get("price"))
    if data:
        return number(data[0].get("price"))
    return 0.0


@st.cache_data(ttl=60, show_spinner=False)
def fetch_lbank_klines(pair, timeframe, size=300):
    if timeframe not in TIMEFRAMES:
        raise ValueError("تایم‌فریم پشتیبانی نمی‌شود.")

    tf = TIMEFRAMES[timeframe]
    interval_seconds = int(tf["seconds"])
    now_seconds = int(time.time())
    requested_size = min(max(int(size), 1), 2000)
    target_rows = requested_size + 10
    cursor = max(0, now_seconds - (target_rows + 4) * interval_seconds)

    raw_candles = []
    seen_timestamps = set()
    max_rounds = int((target_rows + 149) / 150) + 6
    last_seen_max = 0

    for _ in range(max_rounds):
        remaining = target_rows - len(seen_timestamps)
        if remaining <= 0:
            break
        chunk_size = min(150, remaining)
        payload = get_json(
            f"{LBANK_URL}/kline.do",
            params={"symbol": pair, "type": tf["type"], "size": chunk_size, "time": int(cursor)},
            timeout=25,
        )
        if not isinstance(payload, dict) or not is_success(payload):
            message = payload.get("msg", payload) if isinstance(payload, dict) else payload
            raise RuntimeError(f"LBank kline API error: {message}")

        batch = payload.get("data") or []
        if not isinstance(batch, list) or not batch:
            break

        normalized_times = []
        new_count = 0
        for candle in batch:
            if not isinstance(candle, (list, tuple)) or len(candle) < 6:
                continue
            timestamp = number(candle[0])
            if timestamp <= 0:
                continue
            if timestamp > 10_000_000_000:
                timestamp /= 1000.0
            timestamp = int(timestamp)
            normalized_times.append(timestamp)
            if timestamp not in seen_timestamps:
                raw_candles.append((timestamp, candle))
                seen_timestamps.add(timestamp)
                new_count += 1

        if not normalized_times or new_count == 0:
            break
        new_max = max(normalized_times)
        if new_max <= last_seen_max:
            break
        last_seen_max = new_max
        cursor = new_max + interval_seconds

        if len(batch) < chunk_size:
            break

    rows = []
    for timestamp, candle in raw_candles:
        open_price = number(candle[1])
        high_price = number(candle[2])
        low_price = number(candle[3])
        close_price = number(candle[4])
        volume = number(candle[5], float("nan"))
        if min(open_price, high_price, low_price, close_price) <= 0:
            continue
        if pd.isna(volume) or volume < 0:
            continue
        if high_price < max(open_price, close_price, low_price):
            continue
        if low_price > min(open_price, close_price, high_price):
            continue
        rows.append({
            "timestamp": timestamp,
            "Open": open_price, "High": high_price,
            "Low": low_price, "Close": close_price,
            "Volume": volume,
        })

    df = pd.DataFrame(rows)
    if df.empty:
        raise RuntimeError(f"LBank برای {pair} در تایم‌فریم {timeframe} کندل معتبر برنگرداند.")

    df = df.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
    df = df[(df["timestamp"] + interval_seconds) <= now_seconds].copy()
    df = df.tail(requested_size).reset_index(drop=True)

    min_required = min(requested_size, 200)
    if len(df) < min_required:
        raise RuntimeError(
            f"پس از صفحه‌بندی و حذف کندل باز، فقط {len(df)} کندل بسته‌شده برای "
            f"{pair} ({timeframe}) باقی ماند؛ حداقل موردنیاز {min_required} کندل است."
        )

    df["Time"] = pd.to_datetime(df["timestamp"], unit="s", utc=True)
    return df


# ===========================================================================
# INDICATORS
# ===========================================================================
def calculate_atr(df, period=14):
    period = max(int(period), 2)
    high = df["High"]
    low = df["Low"]
    close = df["Close"]
    prev_close = close.shift(1)
    true_range = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr = true_range.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    return atr


def calculate_ut_bot(df, atr_period=14, key_value=2.0):
    out = df.copy()
    close = out["Close"]
    atr = calculate_atr(out, atr_period)
    loss = atr * float(key_value)

    stops = [float("nan")] * len(out)
    buy_signal = [False] * len(out)
    sell_signal = [False] * len(out)

    for i in range(len(out)):
        src = number(close.iloc[i])
        nloss = number(loss.iloc[i], float("nan"))
        if pd.isna(nloss) or nloss <= 0:
            continue
        if i == 0 or pd.isna(stops[i - 1]):
            stops[i] = src - nloss
            continue
        prev_src = number(close.iloc[i - 1])
        prev_stop = stops[i - 1]
        if src > prev_stop and prev_src > prev_stop:
            stops[i] = max(prev_stop, src - nloss)
        elif src < prev_stop and prev_src < prev_stop:
            stops[i] = min(prev_stop, src + nloss)
        elif src > prev_stop:
            stops[i] = src - nloss
        else:
            stops[i] = src + nloss
        buy_signal[i] = src > stops[i] and prev_src <= prev_stop
        sell_signal[i] = src < stops[i] and prev_src >= prev_stop

    out["ATR"] = atr
    out["UT Stop"] = stops
    out["UT Buy"] = buy_signal
    out["UT Sell"] = sell_signal
    return out


def add_market_indicators(df, atr_period=14, key_value=2.0):
    out = calculate_ut_bot(df, atr_period, key_value)
    for period in (14, 50, 200):
        out[f"SMA {period}"] = out["Close"].rolling(window=period, min_periods=period).mean()

    lookback = 20
    previous_high = out["High"].shift(1).rolling(lookback).max()
    previous_low = out["Low"].shift(1).rolling(lookback).min()
    out["Prior Structure High"] = previous_high
    out["Prior Structure Low"] = previous_low
    out["BOS Up"] = out["Close"] > previous_high
    out["BOS Down"] = out["Close"] < previous_low

    trend = []
    state = 0
    for i in range(len(out)):
        up = bool(out["BOS Up"].iloc[i]) if pd.notna(previous_high.iloc[i]) else False
        down = bool(out["BOS Down"].iloc[i]) if pd.notna(previous_low.iloc[i]) else False
        if up and state < 0:
            trend.append("CHoCH صعودی"); state = 1
        elif down and state > 0:
            trend.append("CHoCH نزولی"); state = -1
        elif up:
            trend.append("BOS صعودی"); state = 1
        elif down:
            trend.append("BOS نزولی"); state = -1
        else:
            trend.append("بدون شکست ساختار")
    out["Structure"] = trend
    return out


def latest_signal_summary(df):
    if df.empty:
        raise ValueError("داده‌ای برای تحلیل وجود ندارد.")
    last = df.iloc[-1]
    close = number(last["Close"])
    atr = number(last["ATR"])
    ut_stop = number(last["UT Stop"], float("nan"))

    if close <= 0 or atr <= 0 or pd.isna(ut_stop):
        status = "داده ناکافی"
    else:
        ma50 = number(last.get("SMA 50"), float("nan"))
        ma200 = number(last.get("SMA 200"), float("nan"))
        if bool(last["UT Buy"]):
            status = "سیگنال خرید UT"
        elif bool(last["UT Sell"]):
            status = "سیگنال فروش UT"
        elif close > ut_stop:
            status = "قیمت بالای حد UT"
        else:
            status = "قیمت زیر حد UT"
        if pd.notna(ma50) and pd.notna(ma200):
            if close > ma50 > ma200:
                status += " | روند MA صعودی"
            elif close < ma50 < ma200:
                status += " | روند MA نزولی"

    return {
        "status": status, "close": close, "atr": atr, "ut_stop": ut_stop,
        "buy_signal": bool(last["UT Buy"]), "sell_signal": bool(last["UT Sell"]),
        "structure": str(last["Structure"]), "time": last["Time"],
    }


def make_atr_trade_plan(df, key_value=2.0):
    summary = latest_signal_summary(df)
    close = summary["close"]
    atr = summary["atr"]
    if close <= 0 or atr <= 0:
        return {"status": "WAIT", "reason": "داده کافی برای محاسبه ATR وجود ندارد."}

    last = df.iloc[-1]
    ma50 = number(last.get("SMA 50"), float("nan"))
    ma200 = number(last.get("SMA 200"), float("nan"))
    bullish_ma = pd.notna(ma50) and pd.notna(ma200) and close > ma50 > ma200
    bearish_ma = pd.notna(ma50) and pd.notna(ma200) and close < ma50 < ma200

    if summary["buy_signal"] and not summary["sell_signal"]:
        direction = "LONG"
    elif summary["sell_signal"] and not summary["buy_signal"]:
        direction = "SHORT"
    elif bullish_ma and close > summary["ut_stop"]:
        direction = "LONG WATCH"
    elif bearish_ma and close < summary["ut_stop"]:
        direction = "SHORT WATCH"
    else:
        return {"status": "WAIT", "reason": "سیگنال UT و تأیید روند هم‌جهت نیستند؛ فعلاً ورود تأیید نشده است.",
                "close": close, "atr": atr}

    risk = atr * float(key_value)
    if risk <= 0:
        return {"status": "WAIT", "reason": "ریسک محاسباتی معتبر نیست."}

    if direction.startswith("LONG"):
        stop = close - risk
        target1 = close + 1.5 * risk
        target2 = close + 2.0 * risk
    else:
        stop = close + risk
        target1 = close - 1.5 * risk
        target2 = close - 2.0 * risk

    is_confirmed_signal = direction in ("LONG", "SHORT")
    return {
        "status": direction,
        "reason": ("سیگنال UT روی آخرین کندل بسته‌شده ثبت شده است."
                   if is_confirmed_signal else
                   "این فقط واچ‌لیست روند است؛ سیگنال جدید UT ثبت نشده است."),
        "close": close, "atr": atr, "stop": stop, "target1": target1, "target2": target2,
        "rr1": 1.5, "rr2": 2.0, "direction": direction,
    }


# ===========================================================================
# ELLIOTT
# ===========================================================================
def detect_elliott_candidates(frame, left=3, right=3, min_swing_pct=0.35):
    columns = ["index", "Time", "Price", "Kind", "Label"]
    if frame is None or len(frame) < left + right + 8:
        return pd.DataFrame(columns=columns)

    data = frame.reset_index(drop=True).copy()
    highs = pd.to_numeric(data["High"], errors="coerce").to_numpy()
    lows = pd.to_numeric(data["Low"], errors="coerce").to_numpy()
    n = len(data)
    raw = []

    for i in range(left, n - right):
        h_window = highs[i - left:i + right + 1]
        l_window = lows[i - left:i + right + 1]
        h_val, l_val = highs[i], lows[i]

        if pd.notna(h_val) and h_val == np.nanmax(h_window):
            left_side = highs[i - left:i]
            if len(left_side) == 0 or h_val > np.nanmax(left_side):
                raw.append({"index": i, "Time": data.loc[i, "Time"],
                            "Price": float(h_val), "Kind": "high"})

        if pd.notna(l_val) and l_val == np.nanmin(l_window):
            left_side = lows[i - left:i]
            if len(left_side) == 0 or l_val < np.nanmin(left_side):
                raw.append({"index": i, "Time": data.loc[i, "Time"],
                            "Price": float(l_val), "Kind": "low"})

    raw.sort(key=lambda p: (p["index"], 0 if p["Kind"] == "low" else 1))
    pivots = []
    for point in raw:
        if pivots and point["Kind"] == pivots[-1]["Kind"]:
            more_extreme = (point["Price"] > pivots[-1]["Price"] if point["Kind"] == "high"
                            else point["Price"] < pivots[-1]["Price"])
            if more_extreme:
                pivots[-1] = point
            continue
        if pivots:
            prev = pivots[-1]["Price"]
            if not prev or abs(point["Price"] / prev - 1) * 100 < min_swing_pct:
                continue
        pivots.append(point)

    if len(pivots) < 2:
        return pd.DataFrame(columns=columns)

    out = pd.DataFrame(pivots)
    out["Label"] = ""
    recent_n = min(6, len(out))
    labels = ["0", "1", "2", "3", "4", "5"][-recent_n:]
    out.loc[out.index[-recent_n:], "Label"] = labels
    return out


def analyze_elliott_context(frame, pivots):
    result = {"direction": 0, "score": 0.0, "confidence": "کم",
              "notes": [], "invalid": [], "forecast": []}
    if frame is None or len(frame) < 30 or pivots is None or len(pivots) < 5:
        result["notes"].append("داده یا پیوت کافی برای شمارش معتبر وجود ندارد.")
        return result

    d = frame.reset_index(drop=True).copy()
    p = pivots.tail(6).reset_index(drop=True)
    prices = p["Price"].astype(float).tolist()
    kinds = p["Kind"].tolist()
    direction = 1 if prices[-1] > prices[-2] else -1
    result["direction"] = direction

    if len(p) == 6:
        up = kinds == ["low", "high", "low", "high", "low", "high"]
        down = kinds == ["high", "low", "high", "low", "high", "low"]
        if up:
            w1, w2, w3, w4, w5 = prices[1]-prices[0], prices[1]-prices[2], prices[3]-prices[2], prices[3]-prices[4], prices[5]-prices[4]
            if prices[2] <= prices[0]: result["invalid"].append("موج ۲ از مبدأ موج ۱ عبور کرده")
            if prices[3] <= prices[1]: result["invalid"].append("موج ۳ سقف موج ۱ را نشکسته")
            if prices[4] < prices[1]: result["invalid"].append("هم‌پوشانی موج ۴ و ۱؛ مگر در قطر")
            if w3 <= min(w1, w5): result["invalid"].append("موج ۳ کوتاه‌ترین موج محرک است")
        elif down:
            w1, w2, w3, w4, w5 = prices[0]-prices[1], prices[2]-prices[1], prices[2]-prices[3], prices[4]-prices[3], prices[4]-prices[5]
            if prices[2] >= prices[0]: result["invalid"].append("موج ۲ از مبدأ موج ۱ عبور کرده")
            if prices[3] >= prices[1]: result["invalid"].append("موج ۳ کف موج ۱ را نشکسته")
            if prices[4] > prices[1]: result["invalid"].append("هم‌پوشانی موج ۴ و ۱؛ مگر در قطر")
            if w3 <= min(w1, w5): result["invalid"].append("موج ۳ کوتاه‌ترین موج محرک است")
        else:
            result["invalid"].append("توالی پیوت‌ها با ایمپالس استاندارد هم‌جهت نیست")
        if result["invalid"]:
            result["notes"].append("شمارش ۵ موجی با یک یا چند قانون سخت سازگار نیست؛ برچسب‌ها قطعی نیستند.")
        else:
            result["score"] += 2
            result["notes"].append("توالی اخیر با قواعد اصلی ایمپالس سازگار است؛ همچنان یک شمارش احتمالی است.")

    close = pd.to_numeric(d["Close"], errors="coerce")
    last = float(close.iloc[-1])
    if "SMA 14" in d and "SMA 50" in d:
        fast, slow = d["SMA 14"].iloc[-1], d["SMA 50"].iloc[-1]
        if pd.notna(fast) and pd.notna(slow):
            if last > fast > slow: result["score"] += 1; result["notes"].append("قیمت بالای SMA14 و SMA50 است")
            elif last < fast < slow: result["score"] -= 1; result["notes"].append("قیمت زیر SMA14 و SMA50 است")
    if "UT Buy" in d and bool(d["UT Buy"].iloc[-1]): result["score"] += 1; result["notes"].append("UT خرید اخیر را تأیید می‌کند")
    if "UT Sell" in d and bool(d["UT Sell"].iloc[-1]): result["score"] -= 1; result["notes"].append("UT فروش اخیر را تأیید می‌کند")
    if "Volume" in d:
        vol = pd.to_numeric(d["Volume"], errors="coerce")
        baseline = vol.rolling(20).median().iloc[-1]
        if pd.notna(baseline) and baseline > 0 and vol.iloc[-1] > baseline * 1.5:
            result["notes"].append("حجم آخرین کندل بیش از ۱٫۵ برابر میانه ۲۰ کندل است")
            result["score"] += 0.5 if direction > 0 else -0.5
    recent = d.tail(20)
    support = float(pd.to_numeric(recent["Low"], errors="coerce").iloc[:-1].min())
    resistance = float(pd.to_numeric(recent["High"], errors="coerce").iloc[:-1].max())
    last_low, last_high = float(d["Low"].iloc[-1]), float(d["High"].iloc[-1])
    sweep_down = last_low < support and last > support
    sweep_up = last_high > resistance and last < resistance
    if sweep_down: result["score"] += 1; result["notes"].append("سایه زیر حمایت و بازگشت قیمت: احتمال جمع‌آوری نقدینگی فروش")
    if sweep_up: result["score"] -= 1; result["notes"].append("سایه بالای مقاومت و بازگشت قیمت: احتمال جمع‌آوری نقدینگی خرید")
    result["score"] = max(-5, min(5, result["score"]))
    result["direction"] = 1 if result["score"] > 0 else -1 if result["score"] < 0 else direction
    result["confidence"] = "متوسط" if abs(result["score"]) >= 2 else "کم"
    result["support"], result["resistance"] = support, resistance

    candle_range = (pd.to_numeric(d["High"], errors="coerce") - pd.to_numeric(d["Low"], errors="coerce"))
    atr = float(candle_range.rolling(14).mean().iloc[-1])
    if not math.isfinite(atr) or atr <= 0:
        atr = max(last * 0.005, 1e-8)
    direction = result["direction"]
    recent_swings = []
    if pivots is not None and len(pivots) >= 3:
        recent_swings = pd.to_numeric(pivots.tail(6)["Price"], errors="coerce").dropna().astype(float).tolist()
    impulse_size = max((max(recent_swings) - min(recent_swings)) if len(recent_swings) >= 3 else 0.0, atr * 1.5)
    leg = max(atr * 0.65, min(impulse_size * 0.236, atr * 2.5))
    if direction > 0:
        a = max(support, last - leg)
        b = min(last + leg * 0.62, resistance if resistance > last else last + leg * 0.62)
        c = max(support, a - leg * 0.15)
        target = max(last + impulse_size * 0.382, resistance + atr * 0.15)
    else:
        a = min(resistance, last + leg)
        b = max(last - leg * 0.62, support if support < last else last - leg * 0.62)
        c = min(resistance, a + leg * 0.15)
        target = min(last - impulse_size * 0.382, support - atr * 0.15)
    result["forecast"] = [a, b, c, target]
    result["forecast_labels"] = ["A? اصلاح", "B? بازگشت", "C? اصلاح", "ادامه احتمالی"]
    result["impulse_size"] = impulse_size
    return result


# ===========================================================================
# BTC-relative & trend score
# ===========================================================================
def get_btc_relative_metrics(code, lbank_pairs):
    direct_pair = lbank_btc_symbol_from_code(code)
    btc_usdt_pair = "btc_usdt"
    asset_usdt_pair = lbank_symbol_from_code(code)

    if direct_pair in lbank_pairs:
        candles = fetch_lbank_klines(direct_pair, "روزانه", 4)
        if len(candles) >= 2:
            current = number(candles.iloc[-1]["Close"])
            previous = number(candles.iloc[-2]["Close"])
            change = (current / previous - 1) * 100 if previous > 0 else None
            return {"btc_value": current, "change_pct": change,
                    "source_pair": direct_pair,
                    "method": "جفت‌ارز مستقیم در بازار LBank"}

    if asset_usdt_pair in lbank_pairs and btc_usdt_pair in lbank_pairs:
        asset = fetch_lbank_klines(asset_usdt_pair, "روزانه", 4)
        btc = fetch_lbank_klines(btc_usdt_pair, "روزانه", 4)
        if len(asset) >= 2 and len(btc) >= 2:
            asset_now = number(asset.iloc[-1]["Close"]); asset_prev = number(asset.iloc[-2]["Close"])
            btc_now = number(btc.iloc[-1]["Close"]); btc_prev = number(btc.iloc[-2]["Close"])
            if min(asset_now, asset_prev, btc_now, btc_prev) > 0:
                current_ratio = asset_now / btc_now
                previous_ratio = asset_prev / btc_prev
                return {"btc_value": current_ratio,
                        "change_pct": (current_ratio / previous_ratio - 1) * 100,
                        "source_pair": f"{asset_usdt_pair} + {btc_usdt_pair}",
                        "method": "نرخ متقاطع محاسبه‌شده از کندل‌های واقعی USDT"}
    return {"btc_value": None, "change_pct": None, "source_pair": direct_pair,
            "method": "داده کافی در LBank در دسترس نیست"}


@st.cache_data(ttl=300, show_spinner=False)
def get_btc_xau_metrics(lbank_pairs):
    btc_pair, gold_pair = "btc_usdt", "xaut_usdt"
    if btc_pair not in lbank_pairs or gold_pair not in lbank_pairs:
        return {"current": None, "change_1d": None, "change_7d": None,
                "source": f"{btc_pair} + {gold_pair}",
                "method": "برای ساخت نرخ متقاطع، داده واقعی BTC/USDT و XAUT/USDT در LBank لازم است."}
    btc = fetch_lbank_klines(btc_pair, "روزانه", 40)
    gold = fetch_lbank_klines(gold_pair, "روزانه", 40)
    joined = pd.merge(
        btc[["timestamp", "Close"]].rename(columns={"Close": "btc_usdt"}),
        gold[["timestamp", "Close"]].rename(columns={"Close": "xaut_usdt"}),
        on="timestamp", how="inner",
    )
    joined = joined[(joined["btc_usdt"] > 0) & (joined["xaut_usdt"] > 0)]
    if len(joined) < 8:
        return {"current": None, "change_1d": None, "change_7d": None,
                "source": f"{btc_pair} + {gold_pair}",
                "method": "برای محاسبه تغییر یک‌روزه و هفت‌روزه، کندل بسته‌شده کافی نیست."}
    ratio = joined["btc_usdt"] / joined["xaut_usdt"]
    current = float(ratio.iloc[-1]); prev = float(ratio.iloc[-2]); week_ago = float(ratio.iloc[-8])
    return {"current": current,
            "change_1d": (current / prev - 1) * 100 if prev > 0 else None,
            "change_7d": (current / week_ago - 1) * 100 if week_ago > 0 else None,
            "source": f"{btc_pair} + {gold_pair}",
            "method": "نرخ متقاطع مصنوعی BTC/XAUT = قیمت BTC/USDT ÷ قیمت XAUT/USDT"}


@st.cache_data(ttl=180, show_spinner=False)
def get_asset_trend_score(code, lbank_pairs, atr_period=14, ut_multiplier=2.0):
    pair = lbank_symbol_from_code(code)
    if pair not in lbank_pairs:
        return {"score": None, "notes": [f"{pair} در LBank موجود نیست."]}
    score = 0
    notes = []
    for timeframe in ("4 ساعت", "روزانه"):
        try:
            candles = fetch_lbank_klines(pair, timeframe, 260)
            indicators = add_market_indicators(candles, atr_period, ut_multiplier)
            summary = latest_signal_summary(indicators)
            if summary["buy_signal"]:
                score += 2; notes.append(f"{timeframe}: سیگنال خرید UT")
            elif summary["sell_signal"]:
                score -= 2; notes.append(f"{timeframe}: سیگنال فروش UT")
            last = indicators.iloc[-1]
            ma50 = number(last.get("SMA 50"), float("nan"))
            ma200 = number(last.get("SMA 200"), float("nan"))
            if pd.notna(ma50) and pd.notna(ma200):
                if summary["close"] > ma50 > ma200:
                    score += 1; notes.append(f"{timeframe}: روند میانگین‌ها صعودی")
                elif summary["close"] < ma50 < ma200:
                    score -= 1; notes.append(f"{timeframe}: روند میانگین‌ها نزولی")
        except Exception as exc:
            notes.append(f"{timeframe}: داده کافی نیست ({type(exc).__name__})")
    return {"score": score, "notes": notes}


def rank_crypto_candidates(coins_df, lbank_pairs, atr_period=14, ut_multiplier=2.0):
    excluded = {"XAUT", "USDT", "USDC", "FDUSD", "TUSD", "DAI", "USDE",
                "USDD", "USDP", "PYUSD", "EURC", "USD1", "USTC"}
    eligible = coins_df[
        coins_df["LBank listed"] & ~coins_df["Code"].astype(str).str.upper().isin(excluded)
    ].copy()
    if eligible.empty:
        return []
    eligible["Volume USD"] = pd.to_numeric(eligible["Volume USD"], errors="coerce").fillna(0)
    core = ["BTC", "ETH", "SOL"]
    selected_codes = [c for c in core if c in set(eligible["Code"].astype(str).str.upper())]
    by_volume = eligible.sort_values("Volume USD", ascending=False)["Code"].astype(str).str.upper().tolist()
    for code in by_volume:
        if code not in selected_codes:
            selected_codes.append(code)
        if len(selected_codes) >= 7:
            break

    rows = []
    for code in selected_codes[:7]:
        coin_rows = eligible[eligible["Code"].astype(str).str.upper() == code]
        if coin_rows.empty:
            continue
        coin = coin_rows.iloc[0]
        day_change = number(coin.get("24h Change %"), float("nan"))
        trend = get_asset_trend_score(code, lbank_pairs, int(atr_period), float(ut_multiplier))
        relative = get_btc_relative_metrics(code, lbank_pairs)
        trend_score = trend.get("score")
        relative_change = relative.get("change_pct")
        if trend_score is None and relative_change is None:
            score = None
        else:
            score = int(trend_score or 0)
            if relative_change is not None:
                score += 1 if relative_change > 0 else (-1 if relative_change < 0 else 0)
            if pd.notna(day_change):
                score += 1 if day_change > 0 else (-1 if day_change < 0 else 0)

        if score is None:
            label, reason = "داده ناکافی", "برای روند و قدرت نسبی داده کافی نیست"
        elif score >= 3:
            label, reason = "اولویت ۱: بررسی خرید", "روند تکنیکال و قدرت نسبی بهتر؛ هنوز منتظر تأیید نقطه ورود"
        elif score >= 1:
            label, reason = "اولویت ۲: انتظار برای تأیید", "نشانه‌های مثبت محدود یا ترکیبی؛ خرید فوری تأیید نشده"
        elif score <= -2:
            label, reason = "فعلاً اولویت پایین", "روند یا قدرت نسبی ضعیف است"
        else:
            label, reason = "خنثی: صبر", "مزیت روشن نسبت به گزینه‌های دیگر دیده نمی‌شود"

        rows.append({
            "Code": code, "Score": score, "Priority": label,
            "24h Change %": day_change if pd.notna(day_change) else None,
            "BTC Relative %": relative_change,
            "Volume USD": number(coin.get("Volume USD")),
            "Reason": reason,
            "Trend notes": trend.get("notes") or [],
            "Relative method": relative.get("method", ""),
        })
    rows.sort(key=lambda row: (
        row["Score"] is None,
        -(row["Score"] if row["Score"] is not None else -999),
        -row["Volume USD"],
    ))
    return rows


# ===========================================================================
# CRYPTOMETER
# ===========================================================================
@st.cache_data(ttl=900, show_spinner=False)
def fetch_cryptometer_endpoint(endpoint, api_key, endpoint_params=None):
    allowed_endpoints = {
        "trend-indicator-v3", "merged-orderbook", "rapid-movements", "tickerlist",
        "24h-trade-volume-v2", "open-interest", "long-shorts-data",
    }
    if endpoint not in allowed_endpoints:
        raise ValueError("CryptoMeter endpoint is not enabled.")
    params = dict(endpoint_params or {})
    params["api_key"] = api_key
    response = _http_get(f"{CRYPTOMETER_URL}/{endpoint}/", params=params, timeout=15)
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("CryptoMeter پاسخ نامعتبر برگرداند.")
    if str(payload.get("success", "")).lower() not in {"true", "1"}:
        message = payload.get("message") or payload.get("error_message") or payload.get("error") or "خطای نامشخص API"
        raise RuntimeError(f"CryptoMeter: {str(message)[:180]}")
    data = payload.get("data")
    if data is None:
        raise RuntimeError("CryptoMeter در پاسخ، داده‌ای برنگرداند.")
    return data


# ===========================================================================
# EXCHANGES (CCXT)
# ===========================================================================
_EXCHANGE_CACHE = {}


def make_exchange(exchange_id):
    inst = _EXCHANGE_CACHE.get(exchange_id)
    if inst is None:
        cls = getattr(ccxt, exchange_id)
        inst = cls({
            "enableRateLimit": True,
            "timeout": 15000,
            "options": {"defaultType": "spot"},
        })
        _EXCHANGE_CACHE[exchange_id] = inst
    return inst


@st.cache_data(ttl=300, show_spinner=False)
def get_other_exchange_symbols(exchange_id):
    exchange = make_exchange(exchange_id)
    markets = exchange.load_markets()
    return [s for s, m in markets.items()
            if m.get("spot") and m.get("quote") == QUOTE and m.get("active") is not False]


@st.cache_data(ttl=20, show_spinner=False)
def fetch_other_exchange_ticker(exchange_id, symbol):
    exchange = make_exchange(exchange_id)
    return exchange.fetch_ticker(symbol)


def compare_exchange_prices(code, lbank_pair=None):
    rows, errors = [], []
    if lbank_pair:
        try:
            price = fetch_lbank_ticker(lbank_pair)
            if price > 0:
                rows.append({"Exchange": "LBANK", "Pair": lbank_pair, "Last price": price})
        except Exception as exc:
            errors.append(f"LBANK: {type(exc).__name__}: {str(exc)[:150]}")
    for exchange_id in OTHER_EXCHANGES:
        try:
            symbols = get_other_exchange_symbols(exchange_id)
            target_symbol = f"{code.upper()}/{QUOTE}"
            if target_symbol not in symbols:
                continue
            ticker = fetch_other_exchange_ticker(exchange_id, target_symbol)
            price = number(ticker.get("last"))
            if price > 0:
                rows.append({"Exchange": exchange_id.upper(), "Pair": target_symbol, "Last price": price})
        except Exception as exc:
            errors.append(f"{exchange_id.upper()}: {type(exc).__name__}: {str(exc)[:150]}")
    if rows:
        median_price = statistics.median(r["Last price"] for r in rows)
        for row in rows:
            row["Difference from median %"] = (row["Last price"] / median_price - 1) * 100
    return rows, errors


# ===========================================================================
# ORDER BOOK / TRADES
# ===========================================================================
def analyze_book(book):
    bids = [(number(l[0]), number(l[1])) for l in book.get("bids", [])
            if len(l) >= 2 and number(l[0]) > 0 and number(l[1]) > 0]
    asks = [(number(l[0]), number(l[1])) for l in book.get("asks", [])
            if len(l) >= 2 and number(l[0]) > 0 and number(l[1]) > 0]
    if not bids or not asks:
        raise ValueError("دفتر سفارش خرید یا فروش معتبر نیست.")
    best_bid = max(p for p, q in bids)
    best_ask = min(p for p, q in asks)
    if best_ask < best_bid:
        raise ValueError("قیمت‌های دفتر سفارش نامعتبر یا متقاطع هستند.")
    mid = (best_bid + best_ask) / 2
    spread_pct = (best_ask - best_bid) / mid * 100
    bid_usd = sum(p * q for p, q in bids)
    ask_usd = sum(p * q for p, q in asks)
    total_usd = bid_usd + ask_usd
    imbalance = (bid_usd - ask_usd) / total_usd if total_usd > 0 else 0

    band_width = max(mid * 0.001, 1e-12)

    def aggregate_zones(levels, side):
        buckets = {}
        for price, qty in levels:
            bucket = math.floor(price / band_width)
            zone = buckets.setdefault(bucket, {"low": bucket*band_width, "high": (bucket+1)*band_width,
                                               "quantity": 0.0, "value": 0.0, "weighted_price": 0.0})
            notional = price * qty
            zone["quantity"] += qty
            zone["value"] += notional
            zone["weighted_price"] += price * notional
        zones = []
        for zone in buckets.values():
            if zone["value"] <= 0:
                continue
            zones.append({"price": zone["weighted_price"] / zone["value"],
                          "zone_low": zone["low"], "zone_high": zone["high"],
                          "quantity": zone["quantity"], "value": zone["value"], "side": side})
        side_values = [z["value"] for z in zones]
        if side_values:
            median_v = statistics.median(side_values)
            sorted_v = sorted(side_values)
            p90 = sorted_v[min(len(sorted_v)-1, int(len(sorted_v) * 0.90))]
            threshold = max(median_v * 2.5, p90)
        else:
            threshold = 0.0
        for zone in zones:
            zone["significant"] = zone["value"] >= threshold if threshold else False
            zone["classification"] = ("تراکم بالا در بازه ۰٫۱۰٪" if zone["significant"]
                                      else "ناحیه نسبی دفتر سفارش")
        zones.sort(key=lambda z: z["value"], reverse=True)
        return zones[:8], threshold

    bid_walls, bid_threshold = aggregate_zones(bids, "bid")
    ask_walls, ask_threshold = aggregate_zones(asks, "ask")
    wall_threshold = max(bid_threshold, ask_threshold)
    return {
        "bids": bids, "asks": asks, "best_bid": best_bid, "best_ask": best_ask,
        "mid": mid, "spread_pct": spread_pct, "bid_usd": bid_usd, "ask_usd": ask_usd,
        "imbalance": imbalance, "bid_walls": bid_walls, "ask_walls": ask_walls,
        "wall_threshold": wall_threshold,
    }


def analyze_trades(trades):
    buy_usd = sell_usd = unknown_usd = 0.0
    buy_count = sell_count = 0
    for t in trades:
        price = number(t.get("price")); amount = number(t.get("amount")); cost = number(t.get("cost"))
        if cost <= 0:
            cost = price * amount
        side = t.get("side")
        if side == "buy":
            buy_usd += cost; buy_count += 1
        elif side == "sell":
            sell_usd += cost; sell_count += 1
        else:
            unknown_usd += cost
    known_total = buy_usd + sell_usd
    return {
        "buy_usd": buy_usd, "sell_usd": sell_usd, "unknown_usd": unknown_usd,
        "net_flow": buy_usd - sell_usd,
        "buy_ratio": buy_usd / known_total if known_total else None,
        "buy_count": buy_count, "sell_count": sell_count,
        "total_count": len(trades),
    }


def calculate_trade_plan(book_stats, trade_stats):
    mid = book_stats["mid"]

    def nearest_significant(walls, side):
        candidates = [w for w in walls
                      if (w["price"] < mid if side == "bid" else w["price"] > mid)]
        if not candidates:
            return None
        sig = [w for w in candidates if w.get("significant")]
        pool = sig if sig else candidates
        pool.sort(key=lambda w: abs(w["price"] - mid))
        return pool[0]

    support = nearest_significant(book_stats["bid_walls"], "bid")
    resistance = nearest_significant(book_stats["ask_walls"], "ask")

    if not support or not resistance:
        return {"status": "WAIT", "reason": "برای تعیین سطوح، دفتر سفارش داده کافی ندارد."}

    entry = support["price"] * 1.001
    target = resistance["price"] * 0.999
    stop = support["price"] * 0.995

    if entry >= mid or target <= entry:
        return {"status": "WAIT",
                "reason": "سطوح فعلی هدف بالاتری از ورود ایجاد نمی‌کنند.",
                "support": support, "resistance": resistance}

    risk = entry - stop
    reward = target - entry
    rr = reward / risk if risk > 0 else 0
    support_distance_pct = (mid - support["price"]) / mid * 100

    confirmations, warnings = [], []
    if book_stats["imbalance"] > 0:
        confirmations.append("ارزش دفتر سفارش به نفع خریداران است.")
    else:
        warnings.append("عدم‌تعادل دفتر سفارش به نفع خریداران نیست.")
    if trade_stats["net_flow"] > 0:
        confirmations.append("جریان خالص معاملات طبقه‌بندی‌شده مثبت است.")
    elif trade_stats["net_flow"] < 0:
        warnings.append("جریان خالص معاملات طبقه‌بندی‌شده منفی است.")
    else:
        warnings.append("جریان خالص خرید مثبت تأیید نشده است.")
    if support_distance_pct > 3:
        warnings.append("نزدیک‌ترین ناحیه خرید بیش از ۳٪ پایین‌تر از قیمت فعلی است.")
    if book_stats["spread_pct"] > 0.5:
        warnings.append("فاصله خرید و فروش بالاست.")
    if rr < 1.2:
        warnings.append("نسبت سود احتمالی به ریسک کمتر از ۱٫۲ است.")

    confirmed = (
        support_distance_pct <= 3
        and book_stats["spread_pct"] <= 0.5
        and rr >= 1.2
        and (book_stats["imbalance"] > 0 or trade_stats["net_flow"] > 0)
    )
    return {
        "status": "ENTRY WATCH" if confirmed else "WAIT",
        "reason": " ".join(warnings + confirmations),
        "entry": entry, "target": target, "stop": stop, "rr": rr,
        "support": support, "resistance": resistance,
        "support_distance_pct": support_distance_pct,
    }


# ===========================================================================
# LIQUIDATION HEATMAP
# ===========================================================================
@st.cache_data(ttl=180, show_spinner=False)
def fetch_free_liquidation_heatmap(symbol="BTC", window_minutes=240, price_buckets=50):
    try:
        response = _http_get("https://api.smartmoneyapi.com/v1/liquidations/heatmap",
                             params={"symbol": symbol, "window_minutes": int(window_minutes),
                                     "price_buckets": int(price_buckets)},
                             timeout=25)
        payload = response.json()
    except Exception as exc:
        return None, f"{type(exc).__name__}: {str(exc)[:180]}"
    if not isinstance(payload, dict):
        return None, "پاسخ API در قالب مورد انتظار نیست."
    prices = payload.get("price_levels") or []
    times = payload.get("time_buckets") or []
    matrix = payload.get("matrix") or []
    clusters = payload.get("clusters") or []
    if not prices or not times or not matrix:
        return {"clusters": clusters, "totals": payload.get("totals") or {},
                "by_side": payload.get("by_side") or {},
                "note": payload.get("note") or "در بازه انتخاب‌شده لیکوییدیشن ثبت‌شده کافی موجود نیست.",
                "public": payload.get("public", True)}, None
    if len(matrix) == len(times) and matrix and len(matrix[0]) == len(prices):
        z = [list(row) for row in zip(*matrix)]
    elif len(matrix) == len(prices) and matrix and len(matrix[0]) == len(times):
        z = matrix
    else:
        return None, "ابعاد ماتریس لیکوییدیشن با محورهای قیمت/زمان سازگار نیست."
    x_values = []
    for value in times:
        ts = number(value, None)
        if ts is not None:
            if ts > 10_000_000_000:
                ts /= 1000.0
            x_values.append(datetime.fromtimestamp(ts, tz=timezone.utc))
        else:
            x_values.append(str(value))
    return {"x": x_values, "y": prices, "z": z, "clusters": clusters,
            "totals": payload.get("totals") or {}, "by_side": payload.get("by_side") or {},
            "note": payload.get("note"), "public": payload.get("public", True)}, None


# ===========================================================================
# PDF
# ===========================================================================
def _pdf_rtl(value):
    text_value = "" if value is None else str(value)
    if any("\u0600" <= ch <= "\u06ff" for ch in text_value):
        try:
            import arabic_reshaper
            from bidi.algorithm import get_display
            return get_display(arabic_reshaper.reshape(text_value))
        except Exception:
            return text_value
    return text_value


def _pdf_font_name():
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    ]
    try:
        from matplotlib import font_manager
        candidates.append(font_manager.findfont("DejaVu Sans"))
    except Exception:
        pass
    for path in candidates:
        if os.path.isfile(path):
            try:
                from reportlab.pdfbase import pdfmetrics
                from reportlab.pdfbase.ttfonts import TTFont
                if "SpotPilotUnicode" not in pdfmetrics.getRegisteredFontNames():
                    pdfmetrics.registerFont(TTFont("SpotPilotUnicode", path))
                return "SpotPilotUnicode"
            except Exception:
                continue
    return "Helvetica"


def build_pdf_report(report_data):
    font_name = _pdf_font_name()
    buffer = io.BytesIO()
    page_size = landscape(A4)

    def draw_footer(canvas, doc):
        canvas.saveState()
        canvas.setFont(font_name, 8)
        canvas.setFillColor(colors.grey)
        canvas.drawString(14 * mm, 8 * mm, "SpotPilot AI | Public market data | No auto-trading")
        canvas.drawRightString(page_size[0] - 14 * mm, 8 * mm, f"{doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(
        buffer, pagesize=page_size,
        rightMargin=13 * mm, leftMargin=13 * mm,
        topMargin=13 * mm, bottomMargin=15 * mm,
        title="SpotPilot AI - Market Analysis Report", author="SpotPilot AI",
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("SpotPilotTitle", parent=styles["Title"], fontName=font_name,
                                 fontSize=18, leading=23, alignment=TA_RIGHT, spaceAfter=5 * mm)
    h_style = ParagraphStyle("SpotPilotHeading", parent=styles["Heading2"], fontName=font_name,
                             fontSize=12, leading=16, alignment=TA_RIGHT, spaceBefore=4 * mm, spaceAfter=2 * mm)
    body_style = ParagraphStyle("SpotPilotBody", parent=styles["BodyText"], fontName=font_name,
                                fontSize=8.5, leading=12, alignment=TA_RIGHT, wordWrap="CJK")
    cell_style = ParagraphStyle("SpotPilotCell", parent=body_style, fontSize=7, leading=9,
                                spaceAfter=0, spaceBefore=0)
    header_style = ParagraphStyle("SpotPilotHeader", parent=cell_style, textColor=colors.white,
                                  fontName=font_name, alignment=TA_CENTER)
    story = []
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    story.append(Paragraph(_pdf_rtl("گزارش کامل SpotPilot AI"), title_style))
    story.append(Paragraph(_pdf_rtl(f"زمان تولید گزارش: {generated_at}"), body_style))
    story.append(Paragraph(_pdf_rtl(
        f"ارز انتخاب‌شده: {report_data.get('selected_code', '—')} | "
        f"جفت‌ارز LBank: {report_data.get('selected_pair') or 'در دسترس نیست'}"
    ), body_style))
    story.append(Spacer(1, 3 * mm))

    def add_paragraph(text):
        story.append(Paragraph(_pdf_rtl(text), body_style))
        story.append(Spacer(1, 1.5 * mm))

    def add_heading(text):
        story.append(Paragraph(_pdf_rtl(text), h_style))

    def add_table(df, max_rows=None, col_widths=None):
        if df is None:
            return
        try:
            frame = df.copy()
            if frame.empty:
                add_paragraph("داده‌ای برای نمایش وجود ندارد.")
                return
            if max_rows is not None:
                frame = frame.head(max_rows)
            headers = [Paragraph(_pdf_rtl(html.escape(str(c))), header_style) for c in frame.columns]
            rows = [headers]
            for row in frame.itertuples(index=False, name=None):
                cells = []
                for value in row:
                    if pd.isna(value):
                        value = "—"
                    lines = str(value).splitlines() or [str(value)]
                    safe = "<br/>".join(_pdf_rtl(html.escape(line)) for line in lines)
                    cells.append(Paragraph(safe, cell_style))
                rows.append(cells)
            available_width = page_size[0] - doc.leftMargin - doc.rightMargin
            if col_widths is None:
                ncols = max(1, len(frame.columns))
                col_widths = [available_width / ncols] * ncols
            else:
                total = sum(col_widths)
                col_widths = [available_width * w / total for w in col_widths]
            table_cls = LongTable if len(rows) > 35 else Table
            table = table_cls(rows, colWidths=col_widths, repeatRows=1, hAlign="LEFT")
            table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#263746")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#c7ced4")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f3f6f8")]),
            ]))
            story.append(table)
            story.append(Spacer(1, 3 * mm))
        except Exception as exc:
            add_paragraph(f"ساخت جدول {type(exc).__name__}: {exc}")

    add_heading("خلاصه بازار")
    add_paragraph(
        f"قیمت BTC/USD: {report_data.get('btc_usd', '—')} | "
        f"تعداد ارزهای دریافت‌شده: {report_data.get('fetched_count', '—')} | "
        f"تعداد ارزهای پس از فیلتر: {report_data.get('coin_count', '—')}"
    )
    add_paragraph(f"وضعیت LBank: {report_data.get('lbank_status', '—')}")

    add_heading("۱. فهرست ارزهای LiveCoinWatch")
    add_table(report_data.get("coins_df"), col_widths=[1.0, 1.1, 1.25, 1.25, 1.15, 1.1, 0.9, 0.9])

    ranking = report_data.get("crypto_ranking") or []
    if ranking:
        add_heading("رتبه‌بندی رمزارزها")
        ranking_df = pd.DataFrame(ranking)
        wanted = [c for c in ["Code", "Priority", "Score", "24h Change %", "BTC Relative %", "Reason"]
                  if c in ranking_df.columns]
        ranking_df = ranking_df[wanted].head(7).copy()
        add_table(ranking_df)

    add_heading("۲. مقایسه قیمت صرافی‌ها")
    add_table(report_data.get("comparison_df"), col_widths=[1.2, 1.3, 1.1, 1.3])

    add_heading("۳. تحلیل تکنیکال چندتایم‌فریمی")
    tf_results = report_data.get("timeframe_results") or {}
    for tf_name in ["15 دقیقه", "4 ساعت", "روزانه"]:
        result = tf_results.get(tf_name)
        if not result:
            continue
        summary = result.get("summary", {})
        plan = result.get("plan", {})
        add_heading(f"تایم‌فریم {tf_name}")
        add_paragraph(
            f"زمان آخرین کندل بسته‌شده (UTC): {summary.get('time', '—')} | "
            f"قیمت بسته‌شده: {summary.get('close', '—')} | ATR: {summary.get('atr', '—')} | "
            f"حد متحرک UT: {summary.get('ut_stop', '—')}"
        )
        add_paragraph(
            f"سیگنال: {'خرید' if summary.get('buy_signal') else 'فروش' if summary.get('sell_signal') else 'بدون سیگنال تازه'} | "
            f"ساختار بازار: {summary.get('structure', '—')} | سناریو: {plan.get('status', '—')}"
        )
        if "stop" in plan:
            add_paragraph(
                f"قیمت مبنا: {plan.get('close', '—')} | حد ضرر: {plan.get('stop', '—')} | "
                f"هدف ۱: {plan.get('target1', '—')} | هدف ۲: {plan.get('target2', '—')}"
            )

    add_heading("۴. دفتر سفارش و جریان معاملات LBank")
    add_paragraph(
        f"بهترین خرید: {report_data.get('best_bid', '—')} | بهترین فروش: {report_data.get('best_ask', '—')} | "
        f"فاصله خرید و فروش: {report_data.get('spread_pct', '—')} | عدم‌تعادل: {report_data.get('imbalance', '—')}"
    )
    add_paragraph(
        f"حجم خرید اخیر: {report_data.get('buy_usd', '—')} | حجم فروش اخیر: {report_data.get('sell_usd', '—')} | "
        f"جریان خالص: {report_data.get('net_flow', '—')} | تعداد معاملات: {report_data.get('trade_count', '—')}"
    )

    add_heading("۵. سناریوی احتمالی ورود و خروج")
    order_plan = report_data.get("order_plan") or {}
    add_paragraph(f"وضعیت: {order_plan.get('status', '—')} | توضیح: {order_plan.get('reason', '—')}")
    if "entry" in order_plan:
        add_paragraph(
            f"ورود احتمالی: {order_plan.get('entry', '—')} | هدف: {order_plan.get('target', '—')} | "
            f"حد ابطال: {order_plan.get('stop', '—')} | R/R: {order_plan.get('rr', '—')}"
        )

    add_heading("محدودیت‌ها")
    add_paragraph(
        "این گزارش از داده‌های عمومی بازار استفاده می‌کند و هیچ معامله‌ای ثبت نمی‌کند. "
        "سیگنال‌ها و سطوح تحلیلی تخمینی هستند و سود را تضمین نمی‌کنند."
    )

    doc.build(story, onFirstPage=draw_footer, onLaterPages=draw_footer)
    buffer.seek(0)
    return buffer.getvalue()


# ===========================================================================
# ========== STREAMLIT UI (THIS IS THE PART THAT WAS MISSING) ===============
# ===========================================================================
st.set_page_config(page_title="SpotPilot AI", page_icon="📊", layout="wide")

st.markdown(
    """
    <style>
    :root { --spotpilot-persian-font: Tahoma, Arial, sans-serif; }

    html, body, .stApp,
    [data-testid="stAppViewContainer"],
    [data-testid="stHeader"],
    [data-testid="stMain"],
    [data-testid="stMainBlockContainer"],
    [data-testid="stSidebar"],
    [data-testid="stSidebarContent"],
    [data-testid="stBottom"],
    [data-testid="stPopover"],
    [data-testid="stDialog"] {
        direction: rtl;
        text-align: right;
        font-family: var(--spotpilot-persian-font) !important;
    }

    .stApp p, .stApp label, .stApp button, .stApp input,
    .stApp textarea, .stApp select, .stApp option,
    .stApp h1, .stApp h2, .stApp h3, .stApp h4, .stApp h5, .stApp h6,
    .stApp li, .stApp td, .stApp th,
    .stApp [data-testid="stMarkdownContainer"],
    .stApp [data-testid="stWidgetLabel"],
    .stApp [data-testid="stCaptionContainer"],
    .stApp [data-testid="stAlert"],
    .stApp [data-testid="stMetric"],
    .stApp [data-testid="stExpander"],
    [role="listbox"], [role="option"], [role="menu"], [role="menuitem"] {
        font-family: var(--spotpilot-persian-font) !important;
        direction: rtl;
        text-align: right;
        line-height: 1.65;
    }

    .block-container { max-width: 1500px; padding-top: 1.4rem; padding-bottom: 2.5rem; }

    [data-testid="stSidebar"] *:not(svg):not(path):not(.material-icons):not(.material-symbols-rounded) {
        text-align: right;
    }

    [data-testid="stHorizontalBlock"],
    [data-testid="stVerticalBlock"],
    [data-testid="stExpander"],
    [data-testid="stForm"],
    [data-testid="stAlert"],
    [data-testid="stNotificationContent"] { direction: rtl; }

    .stTabs [data-baseweb="tab-list"] { direction: rtl; }

    .stButton, .stDownloadButton,
    [data-testid="stButton"], [data-testid="stDownloadButton"] {
        direction: rtl; text-align: center;
    }

    [data-baseweb="select"] > div,
    [data-baseweb="input"] > div,
    [data-baseweb="textarea"],
    [role="listbox"], [role="option"] {
        direction: rtl; text-align: right;
        font-family: var(--spotpilot-persian-font) !important;
    }

    .stMarkdown ul, .stMarkdown ol,
    [data-testid="stMarkdownContainer"] ul,
    [data-testid="stMarkdownContainer"] ol {
        padding-right: 1.5rem; padding-left: 0;
    }

    [data-testid="stMetricLabel"] {
        direction: rtl; text-align: right; justify-content: flex-start;
        font-family: var(--spotpilot-persian-font) !important;
    }

    [data-testid="stMetricValue"],
    [data-testid="stMetricDelta"] {
        direction: ltr; text-align: right;
        unicode-bidi: plaintext;
        font-variant-numeric: tabular-nums;
    }

    .stNumberInput input, .stDateInput input, .stTimeInput input {
        direction: ltr; text-align: left;
        font-variant-numeric: tabular-nums;
    }

    [data-testid="stDataFrame"], [data-testid="stTable"], [data-testid="stDataEditor"] {
        direction: rtl; text-align: right;
    }

    code, pre, .stCode, [data-testid="stCode"] {
        direction: ltr; text-align: left;
        font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace !important;
    }

    .material-icons, .material-symbols-rounded, [data-testid="stIconMaterial"] {
        font-family: "Material Icons", "Material Symbols Rounded", sans-serif !important;
        direction: ltr;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
st.title("📊 SpotPilot AI")
st.caption("داشبورد بررسی بازار رمزارزها با داده‌های عمومی؛ بدون ثبت سفارش و بدون معامله خودکار.")

with st.expander("راهنمای سریع: از کجا شروع کنم؟", expanded=False):
    st.markdown(
        "۱. **یک ارز انتخاب کن**؛ فهرست اولیه بر اساس حداقل حجم ۲۴ساعته فیلتر می‌شود.\n"
        "۲. **قیمت صرافی‌ها را مقایسه کن**؛ اختلاف قیمت به‌تنهایی فرصت آربیتراژ نیست.\n"
        "۳. **نمودار را بخوان**؛ میانگین‌ها روند را هموار می‌کنند و UT فقط یک ابزار کمکی است.\n"
        "۴. **دفتر سفارش را بررسی کن**؛ سفارش‌های نمایش‌داده‌شده ممکن است پیش از اجرا لغو شوند.\n\n"
        "**مهم:** خروجی‌ها ابزار بررسی‌اند، نه توصیه قطعی خرید یا فروش."
    )

with st.sidebar:
    pdf_download_placeholder = st.empty()
    st.header("فیلترهای بازار")

    min_btc_volume = st.number_input(
        "حداقل حجم ۲۴ساعته، معادل BTC",
        min_value=0.0, value=1000.0, step=100.0,
    )

    st.divider()
    with st.expander("تنظیمات پیشرفته UT Bot"):
        st.caption("اگر با این پارامترها آشنا نیستی، مقدار پیش‌فرض را تغییر نده.")
        atr_period = st.number_input(
            "دوره ATR", min_value=2, max_value=100, value=14, step=1,
            help="تعداد کندل‌های مورد استفاده برای برآورد نوسان. مقدار پیش‌فرض ۱۴ استاندارد Wilder است.",
        )
        ut_multiplier = st.number_input(
            "ضریب ATR (Key Value)", min_value=0.1, max_value=20.0, value=2.0, step=0.1,
            help="ضریب فاصله حد متحرک UT از قیمت.",
        )

    refresh = st.button("🔄 به‌روزرسانی داده‌ها", use_container_width=True)

if refresh:
    fetch_lcw_market.clear()
    fetch_lbank_pairs.clear()
    fetch_lbank_book.clear()
    fetch_lbank_trades.clear()
    fetch_lbank_ticker.clear()
    fetch_lbank_klines.clear()
    fetch_cryptometer_endpoint.clear()
    get_other_exchange_symbols.clear()
    fetch_other_exchange_ticker.clear()
    get_btc_xau_metrics.clear()
    get_asset_trend_score.clear()
    fetch_free_liquidation_heatmap.clear()
    st.rerun()

if not get_lcw_key():
    st.error("LIVECOINWATCH_API_KEY در Streamlit Secrets تنظیم نشده است.")
    st.stop()

try:
    with st.spinner("دریافت ارزها از LiveCoinWatch..."):
        coins, btc_usd, fetched_count = fetch_lcw_market(min_btc_volume)
except Exception as exc:
    st.error(f"دریافت LiveCoinWatch ناموفق بود: {exc}")
    st.stop()

m1, m2, m3 = st.columns(3)
m1.metric("BTC/USD", fmt_usd(btc_usd))
m2.metric("ارزهای دریافت‌شده پیش از فیلتر", fetched_count)
m3.metric("ارزهای باقی‌مانده پس از فیلتر", len(coins))

if not coins:
    st.warning("با فیلتر فعلی ارزی باقی نمانده است. حداقل حجم را بررسی کن.")
    st.stop()

try:
    lbank_pairs = set(fetch_lbank_pairs())
    lbank_status = f"{sum(1 for p in lbank_pairs if p.endswith('_usdt'))} جفت‌ارز USDT و {sum(1 for p in lbank_pairs if p.endswith('_btc'))} جفت‌ارز BTC شناسایی شد"
except Exception as exc:
    lbank_pairs = set()
    lbank_status = f"دریافت جفت‌ارزهای LBank ناموفق بود: {exc}"

st.caption(f"وضعیت بازار LBank: {lbank_status}")

existing_codes = {str(r.get("Code", "")).upper() for r in coins}
for pinned_code in PINNED_CODES:
    if pinned_code not in existing_codes:
        coins.append({
            "Name": PINNED_NAMES[pinned_code], "Code": pinned_code,
            "Price USD": None, "24h Change %": None,
            "Volume USD": None, "Volume BTC equivalent": None,
            "Exchanges reported": None, "Markets reported": None, "Pairs reported": None,
            "Price check": "داده ناموجود",
        })

coins_df = pd.DataFrame(coins)
pin_rank = {code: index for index, code in enumerate(PINNED_CODES)}
coins_df["_pin_rank"] = coins_df["Code"].map(lambda code: pin_rank.get(code, 999))
coins_df = coins_df.sort_values(
    by=["_pin_rank", "24h Change %"], ascending=[True, False], na_position="last"
).drop(columns=["_pin_rank"]).reset_index(drop=True)
coins_df["LBank listed"] = coins_df["Code"].apply(
    lambda code: lbank_symbol_from_code(code) in lbank_pairs
)

display_df = coins_df[
    ["Name", "Code", "Price USD", "24h Change %",
     "Volume USD", "Volume BTC equivalent", "LBank listed", "Price check"]
].copy()
display_df = display_df.rename(columns={
    "Name": "نام ارز", "Code": "نماد", "Price USD": "قیمت (دلار)",
    "24h Change %": "تغییر ۲۴ساعته", "Volume USD": "حجم ۲۴ساعته (دلار)",
    "Volume BTC equivalent": "حجم معادل BTC", "LBank listed": "در LBank",
    "Price check": "کنترل قیمت",
})
display_df["قیمت (دلار)"] = display_df["قیمت (دلار)"].map(fmt_price)
display_df["حجم ۲۴ساعته (دلار)"] = display_df["حجم ۲۴ساعته (دلار)"].map(fmt_usd)
display_df["حجم معادل BTC"] = display_df["حجم معادل BTC"].map(
    lambda v: f"{v:,.2f}" if pd.notna(v) else "—")
display_df["تغییر ۲۴ساعته"] = display_df["تغییر ۲۴ساعته"].map(
    lambda v: f"{v:+.2f}%" if v is not None and pd.notna(v) else "—")
display_df["در LBank"] = display_df["در LBank"].map(lambda b: "بله" if b else "خیر")

st.subheader("۱. انتخاب ارز")
st.caption(
    "چهار جفت‌ارز ثابت همیشه در ابتدای فهرست هستند: BTC/USDT، XAUT/USDT، "
    "SOL/USDT و ETH/USDT."
)

event = st.dataframe(
    display_df, use_container_width=True, hide_index=True, height=360,
    on_select="rerun", selection_mode="single-row", key="lcw_coin_selector",
)

if event.selection.rows:
    selected_coin = coins_df.iloc[event.selection.rows[0]]
    selected_code = selected_coin["Code"]
else:
    selected_code = st.selectbox("انتخاب ارز برای تحلیل", coins_df["Code"].tolist())
    selected_coin = coins_df[coins_df["Code"] == selected_code].iloc[0]

# ---------------------------------------------------------------------------
st.divider()
st.subheader("۲. اولویت رمزارزها برای بررسی خرید")
st.caption("رتبه‌ها از روند ۴ساعته/روزانه، عملکرد نسبت به BTC، تغییر ۲۴ساعته و حجم واقعی کمک می‌گیرند.")
crypto_ranking = []
try:
    with st.spinner("مقایسه روند و قدرت نسبی رمزارزهای برتر فهرست..."):
        crypto_ranking = rank_crypto_candidates(
            coins_df, lbank_pairs, int(atr_period), float(ut_multiplier))
    if crypto_ranking:
        ranking_df = pd.DataFrame(crypto_ranking)
        best = ranking_df.iloc[0]
        if best["Score"] is not None and best["Score"] >= 3:
            st.success(f"**انتخاب اول فعلی برای بررسی: {best['Code']}** — {best['Reason']}.")
        else:
            st.info("**فعلاً خرید فوری تأیید نشده است.** رتبه اول جدول فقط بهترین گزینه نسبی بین داده‌های موجود است.")
        display_ranking = ranking_df[[
            "Code", "Priority", "Score", "24h Change %", "BTC Relative %", "Reason"
        ]].copy()
        display_ranking = display_ranking.rename(columns={
            "Code": "رمزارز", "Priority": "اولویت", "Score": "امتیاز مدل",
            "24h Change %": "تغییر ۲۴ساعته (%)", "BTC Relative %": "تغییر نسبت به BTC (%)",
            "Reason": "دلیل ساده",
        })
        for col in ("تغییر ۲۴ساعته (%)", "تغییر نسبت به BTC (%)"):
            display_ranking[col] = display_ranking[col].map(
                lambda v: f"{v:+.2f}%" if v is not None and pd.notna(v) else "—")
        display_ranking["امتیاز مدل"] = display_ranking["امتیاز مدل"].map(
            lambda v: str(v) if v is not None and pd.notna(v) else "—")
        st.dataframe(display_ranking, use_container_width=True, hide_index=True)
        with st.expander("چرا این رتبه‌بندی را داده؟"):
            for item in crypto_ranking:
                st.markdown(f"**{item['Code']} — {item['Priority']}**")
                st.caption("؛ ".join(item["Trend notes"][:5]) if item["Trend notes"] else item["Reason"])
                st.caption(item["Relative method"])
    else:
        st.warning("از فهرست فعلی، رمزارز قابل بررسی با جفت USDT در LBank پیدا نشد.")
except Exception as exc:
    st.warning(f"رتبه‌بندی رمزارزها کامل نشد: {type(exc).__name__}: {str(exc)[:180]}")

# ---------------------------------------------------------------------------
st.divider()
st.subheader(f"۳. مقایسه قیمت {selected_code}")
st.caption("اختلاف‌ها ممکن است ناشی از زمان به‌روزرسانی، بازار یا نقدشوندگی باشند.")

selected_lbank_pair = lbank_symbol_from_code(selected_code)
selected_lbank_pair = selected_lbank_pair if selected_lbank_pair in lbank_pairs else None

with st.spinner("مقایسه قیمت بازارهای عمومی..."):
    comparison, compare_errors = compare_exchange_prices(selected_code, selected_lbank_pair)

comp_df = pd.DataFrame()
if comparison:
    comp_df = pd.DataFrame(comparison)
    comp_df_display = comp_df.copy()
    comp_df_display["Last price"] = comp_df_display["Last price"].map(fmt_price)
    comp_df_display["Difference from median %"] = comp_df_display["Difference from median %"].map(
        lambda v: f"{v:+.3f}%")
    st.dataframe(comp_df_display, use_container_width=True, hide_index=True)
else:
    st.warning("قیمت قابل استفاده‌ای از صرافی‌های بررسی‌شده دریافت نشد.")

if compare_errors:
    with st.expander("جزئیات خطاهای مقایسه قیمت"):
        st.code("\n".join(compare_errors))

# ---------------------------------------------------------------------------
st.divider()
st.subheader("۴. ارزش نسبت به بیت‌کوین (BTC Pair)")
st.caption("بررسی می‌کند خودِ ارز در برابر BTC قوی‌تر شده یا ضعیف‌تر.")
try:
    with st.spinner("بررسی نسبت واقعی ارز به BTC در LBank..."):
        btc_relative = get_btc_relative_metrics(selected_code, lbank_pairs)
    rb1, rb2, rb3 = st.columns(3)
    rb1.metric(f"{selected_code} برحسب BTC",
               f"{btc_relative['btc_value']:.8f} BTC" if btc_relative["btc_value"] is not None else "—")
    rb2.metric("تغییر نسبت در آخرین ۲۴ ساعت",
               f"{btc_relative['change_pct']:+.2f}%" if btc_relative["change_pct"] is not None else "—")
    rb3.metric("منبع/بازار", btc_relative["source_pair"])
    st.caption(btc_relative["method"])
    if btc_relative["change_pct"] is not None:
        if btc_relative["change_pct"] > 0:
            st.success(f"در بازه محاسبه‌شده، {selected_code} نسبت به BTC عملکرد بهتری داشته است.")
        elif btc_relative["change_pct"] < 0:
            st.warning(f"در بازه محاسبه‌شده، {selected_code} نسبت به BTC ضعیف‌تر شده است.")
        else:
            st.info("عملکرد نسبت به BTC تقریباً بدون تغییر بوده است.")
except Exception as exc:
    st.warning(f"تحلیل نسبت به BTC انجام نشد: {type(exc).__name__}: {str(exc)[:180]}")

if selected_code in ("BTC", "XAUT"):
    st.markdown("#### نسبت بیت‌کوین به طلا (BTC/XAUT)")
    try:
        btc_xau = get_btc_xau_metrics(lbank_pairs)
        if btc_xau["current"] is None:
            st.info(btc_xau["method"])
        else:
            q1, q2, q3 = st.columns(3)
            q1.metric("BTC/XAUT مصنوعی", f"{btc_xau['current']:.4f} XAUT برای هر BTC")
            q2.metric("تغییر یک‌روزه", f"{btc_xau['change_1d']:+.2f}%")
            q3.metric("تغییر هفت‌روزه", f"{btc_xau['change_7d']:+.2f}%")
            st.caption(btc_xau["method"])
    except Exception as exc:
        st.warning(f"محاسبه BTC/XAUT انجام نشد: {type(exc).__name__}: {str(exc)[:160]}")

# ---------------------------------------------------------------------------
st.divider()
st.subheader("۵. تحلیل تکنیکال")
st.caption("۱۵ دقیقه برای کوتاه‌مدت، ۴ ساعت میان‌مدت، روزانه بلندمدت. سیگنال‌ها از کندل‌های بسته‌شده محاسبه می‌شوند.")

timeframe_results = {}
if not selected_lbank_pair:
    st.warning(f"{selected_code} در فهرست جفت‌ارزهای USDT در LBank پیدا نشد.")
else:
    st.write(f"جفت‌ارز تحلیل: **{selected_lbank_pair}**")
    tf_tabs = st.tabs(["۱۵ دقیقه", "۴ ساعت", "روزانه"])
    tf_names = ["15 دقیقه", "4 ساعت", "روزانه"]

    for tab, tf_name in zip(tf_tabs, tf_names):
        with tab:
            try:
                with st.spinner(f"دریافت کندل‌های {tf_name}..."):
                    candles = fetch_lbank_klines(selected_lbank_pair, tf_name, 300)
                    indicators = add_market_indicators(candles, int(atr_period), float(ut_multiplier))
                    summary = latest_signal_summary(indicators)
                    plan = make_atr_trade_plan(indicators, float(ut_multiplier))

                timeframe_results[tf_name] = {
                    "candles": candles, "indicators": indicators,
                    "summary": summary, "plan": plan,
                }

                if summary["buy_signal"]:
                    st.success("سیگنال UT Bot خرید روی آخرین کندل بسته‌شده")
                elif summary["sell_signal"]:
                    st.error("سیگنال UT Bot فروش روی آخرین کندل بسته‌شده")
                else:
                    st.info("روی آخرین کندل بسته‌شده سیگنال تازه UT ثبت نشده است.")

                a, b, c, d = st.columns(4)
                a.metric("آخرین قیمت بسته‌شده", fmt_price(summary["close"]))
                b.metric("ATR", fmt_price(summary["atr"]))
                c.metric("حد متحرک UT", fmt_price(summary["ut_stop"]))
                d.metric("وضعیت", plan["status"])

                st.caption(f"زمان شروع آخرین کندل بسته‌شده (UTC): {summary['time'].strftime('%Y-%m-%d %H:%M')}")
                candle_close_time = summary["time"] + pd.Timedelta(seconds=int(TIMEFRAMES[tf_name]["seconds"]))
                candle_age_minutes = max(0.0, (pd.Timestamp.now(tz="UTC") - candle_close_time).total_seconds() / 60)
                stale_threshold = int(TIMEFRAMES[tf_name]["seconds"]) / 60 * 2
                if candle_age_minutes > stale_threshold:
                    st.warning(f"داده قدیمی است: آخرین کندل {candle_age_minutes:.0f} دقیقه پیش بسته شد.")
                else:
                    st.caption(f"تازگی داده: مناسب | پایان آخرین کندل: {candle_close_time.strftime('%Y-%m-%d %H:%M UTC')}")

                last = indicators.iloc[-1]
                st.markdown("#### میانگین‌های متحرک")
                ma_cols = st.columns(3)
                ma_values = {}
                ma_explanations = {
                    14: "کوتاه‌مدت؛ سریع‌تر واکنش نشان می‌دهد.",
                    50: "میان‌مدت؛ برای دیدن جهت کلی‌تر.",
                    200: "بلندمدت؛ به تاریخچه کافی نیاز دارد.",
                }
                for col, period in zip(ma_cols, (14, 50, 200)):
                    ma_value = number(last.get(f"SMA {period}"), float("nan"))
                    ma_values[period] = ma_value
                    if pd.isna(ma_value) or ma_value <= 0:
                        col.metric(f"SMA {period}", "داده کافی نیست")
                        col.caption(ma_explanations[period])
                    else:
                        distance_pct = (summary["close"] / ma_value - 1) * 100
                        col.metric(f"SMA {period}", fmt_price(ma_value),
                                   delta=f"{distance_pct:+.2f}%",
                                   help=ma_explanations[period])
                        col.caption(ma_explanations[period])

                valid_ma = {p: v for p, v in ma_values.items() if pd.notna(v) and v > 0}
                if 50 in valid_ma and 200 in valid_ma:
                    if summary["close"] > valid_ma[50] > valid_ma[200]:
                        st.success("قیمت بالاتر از میانگین‌های ۵۰ و ۲۰۰ است؛ روند متمایل به صعودی.")
                    elif summary["close"] < valid_ma[50] < valid_ma[200]:
                        st.warning("قیمت پایین‌تر از میانگین‌های ۵۰ و ۲۰۰ است؛ روند متمایل به نزولی.")
                    else:
                        st.info("میانگین‌ها هم‌جهت نیستند؛ روند یک‌دست نیست.")
                else:
                    st.info("برای برداشت روند بلندمدت، ابتدا باید داده کافی برای SMA 50 و SMA 200 دریافت شود.")

                st.write(f"**ساختار بازار:** {summary['structure']}")

                if "stop" in plan:
                    p1, p2, p3, p4 = st.columns(4)
                    p1.metric("قیمت مبنای سناریو", fmt_price(plan["close"]))
                    p2.metric("حد ضرر تحلیلی", fmt_price(plan["stop"]))
                    p3.metric("هدف ۱ (۱٫۵R)", fmt_price(plan["target1"]))
                    p4.metric("هدف ۲ (۲R)", fmt_price(plan["target2"]))
                    st.write(plan["reason"])
                else:
                    st.warning(plan["reason"])

                # Chart
                st.markdown("#### نمودار قیمت و میانگین‌ها")
                elliott_enabled = st.checkbox("نمایش شمارش احتمالی امواج الیوت",
                                              value=False, key=f"elliott_overlay_{tf_name}")
                st.caption("⚠️ شمارش الیوت ذهنی است. الگوریتم فقط پیوت‌های متناوب را برچسب می‌زند. "
                           "مسیر فرضی است، نه پیش‌بینی.")
                chart_df = indicators.tail(150).copy()
                fig = go.Figure()
                fig.add_trace(go.Candlestick(
                    x=chart_df["Time"], open=chart_df["Open"], high=chart_df["High"],
                    low=chart_df["Low"], close=chart_df["Close"], name="قیمت",
                    increasing_line_color="#16a34a", decreasing_line_color="#dc2626",
                ))
                ma_styles = {
                    14: ("#2563eb", "SMA 14 — کوتاه‌مدت"),
                    50: ("#f59e0b", "SMA 50 — میان‌مدت"),
                    200: ("#9333ea", "SMA 200 — بلندمدت"),
                }
                for period, (line_color, line_name) in ma_styles.items():
                    col_name = f"SMA {period}"
                    if col_name in chart_df.columns and chart_df[col_name].notna().any():
                        fig.add_trace(go.Scatter(
                            x=chart_df["Time"], y=chart_df[col_name], mode="lines",
                            name=line_name, line={"color": line_color, "width": 2},
                            connectgaps=False,
                        ))
                if "UT Stop" in chart_df.columns:
                    fig.add_trace(go.Scatter(
                        x=chart_df["Time"], y=chart_df["UT Stop"], mode="lines",
                        name="حد متحرک UT",
                        line={"color": "#64748b", "width": 1.5, "dash": "dash"},
                    ))
                buy_points = chart_df[chart_df["UT Buy"].astype(bool)]
                sell_points = chart_df[chart_df["UT Sell"].astype(bool)]
                if not buy_points.empty:
                    fig.add_trace(go.Scatter(
                        x=buy_points["Time"], y=buy_points["Low"] * 0.998,
                        mode="markers", name="علامت خرید UT",
                        marker={"symbol": "triangle-up", "size": 10, "color": "#16a34a"},
                    ))
                if not sell_points.empty:
                    fig.add_trace(go.Scatter(
                        x=sell_points["Time"], y=sell_points["High"] * 1.002,
                        mode="markers", name="علامت فروش UT",
                        marker={"symbol": "triangle-down", "size": 10, "color": "#dc2626"},
                    ))
                if elliott_enabled:
                    wave_points = detect_elliott_candidates(chart_df)
                    context = analyze_elliott_context(chart_df, wave_points)
                    if not wave_points.empty:
                        fig.add_trace(go.Scatter(
                            x=wave_points["Time"], y=wave_points["Price"],
                            mode="lines+markers+text", text=wave_points["Label"],
                            textposition="top center", name="پیوت‌ها / شمارش احتمالی",
                            line={"color": "#7c3aed", "width": 2},
                            marker={"size": 7, "color": "#7c3aed", "symbol": "circle"},
                            textfont={"color": "#7c3aed", "size": 11},
                            connectgaps=False,
                        ))
                        if context.get("forecast") and len(chart_df):
                            last_price = float(chart_df["Close"].iloc[-1])
                            t0 = pd.to_datetime(chart_df["Time"].iloc[-1], utc=True, errors="coerce")
                            step_seconds = int(TIMEFRAMES.get(tf_name, {}).get("seconds", 14400))
                            next_time = t0 + pd.Timedelta(seconds=step_seconds)
                            future_times = [t0 + pd.Timedelta(seconds=step_seconds * k) for k in (2, 3, 4, 5)]
                            future_prices = context["forecast"]
                            last_pivot = wave_points.iloc[-1]
                            pivot_time = pd.to_datetime(last_pivot["Time"], utc=True, errors="coerce")
                            if pd.notna(pivot_time) and pivot_time < t0:
                                fig.add_trace(go.Scatter(
                                    x=[pivot_time, t0], y=[float(last_pivot["Price"]), last_price],
                                    mode="lines", name="پیوت تا قیمت فعلی",
                                    line={"color": "#94a3b8", "width": 1.5, "dash": "dot"},
                                ))
                            fig.add_trace(go.Scatter(
                                x=[next_time] + future_times, y=[last_price] + future_prices,
                                mode="lines+markers+text",
                                text=["مبدأ سناریو"] + context["forecast_labels"],
                                textposition="top center", name="سناریوی آینده — فرضیه",
                                line={"color": "#dc2626", "width": 2.5, "dash": "dash"},
                                marker={"size": 7, "color": "#dc2626"},
                                connectgaps=False,
                            ))
                            st.caption(
                                f"جهت سناریو: {'صعودی' if context['direction'] > 0 else 'نزولی' if context['direction'] < 0 else 'خنثی'} | "
                                f"اطمینان: {context['confidence']}"
                            )
                            if context["notes"]:
                                st.write("**شواهد:** " + "؛ ".join(context["notes"]))
                            if context["invalid"]:
                                st.warning("قوانین ایمپالس تأیید نشده‌اند: " + "؛ ".join(context["invalid"]))
                    else:
                        st.info("برای شمارش موج، پیوت کافی وجود ندارد.")
                fig.update_layout(
                    height=520, margin={"l": 12, "r": 12, "t": 28, "b": 12},
                    hovermode="x unified", xaxis_title="زمان (UTC)", yaxis_title="قیمت",
                    legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "xanchor": "left", "x": 0},
                    xaxis={"rangeslider": {"visible": False}, "showgrid": True},
                    yaxis={"side": "right", "showgrid": True},
                    template="plotly_white",
                )
                st.plotly_chart(fig, use_container_width=True,
                                config={"displaylogo": False, "scrollZoom": True})

                signal_table = indicators.tail(12)[
                    ["Time", "Open", "High", "Low", "Close", "Volume",
                     "ATR", "UT Stop", "UT Buy", "UT Sell", "Structure"]
                ].copy()
                signal_table["Time"] = signal_table["Time"].dt.strftime("%Y-%m-%d %H:%M UTC")
                for col in ["Open", "High", "Low", "Close", "ATR", "UT Stop"]:
                    signal_table[col] = signal_table[col].map(fmt_price)
                with st.expander("جزئیات کندل‌های اخیر"):
                    st.dataframe(signal_table, use_container_width=True, hide_index=True)

            except Exception as exc:
                st.error(f"تحلیل تایم‌فریم {tf_name} ناموفق بود: {type(exc).__name__}: {str(exc)[:300]}")

    if timeframe_results:
        st.markdown("#### خلاصه هم‌جهتی تایم‌فریم‌ها")
        overview = []
        for tf_name in tf_names:
            result = timeframe_results.get(tf_name)
            if not result:
                continue
            summary = result["summary"]
            plan = result["plan"]
            overview.append({
                "تایم‌فریم": tf_name,
                "قیمت بسته‌شده": fmt_price(summary["close"]),
                "سیگنال UT": ("خرید" if summary["buy_signal"]
                              else "فروش" if summary["sell_signal"]
                              else "بدون سیگنال تازه"),
                "ساختار": summary["structure"],
                "سناریو": plan["status"],
            })
        st.dataframe(pd.DataFrame(overview), use_container_width=True, hide_index=True)

        buy_count = sum(1 for r in timeframe_results.values() if r["summary"]["buy_signal"])
        sell_count = sum(1 for r in timeframe_results.values() if r["summary"]["sell_signal"])
        if buy_count >= 2:
            st.success(f"هم‌جهتی اولیه: سیگنال خرید UT در {buy_count} تایم‌فریم.")
        elif sell_count >= 2:
            st.warning(f"هم‌جهتی نزولی: سیگنال فروش UT در {sell_count} تایم‌فریم.")
        else:
            st.info("هنوز دو تایم‌فریم سیگنال هم‌جهت تازه ندارند.")

# ---------------------------------------------------------------------------
# CryptoMeter expander
st.divider()
st.subheader("۶. نقدینگی و معاملات اخیر LBank")

with st.expander("جریان نقدینگی و فشار خرید/فروش — CryptoMeter.io", expanded=False):
    cryptometer_key = get_cryptometer_key()
    if not cryptometer_key:
        st.info("برای فعال‌کردن، CRYPTOMETER_API_KEY را در Streamlit Secrets تنظیم کن.")
    else:
        try:
            trend = {}
            book_df = pd.DataFrame()
            movement_df = pd.DataFrame()
            movers_df = pd.DataFrame()
            with st.spinner("دریافت داده‌های رایگان CryptoMeter..."):
                try:
                    trend_data = fetch_cryptometer_endpoint("trend-indicator-v3", cryptometer_key, {})
                    trend_rows = trend_data if isinstance(trend_data, list) else [trend_data]
                    trend_rows = [r for r in trend_rows if isinstance(r, dict)]
                    trend = trend_rows[-1] if trend_rows else {}
                except Exception as e:
                    st.caption(f"trend-indicator-v3 در دسترس نبود: {type(e).__name__}")
                if trend:
                    buy_pressure = number(trend.get("buy_pressure"), None)
                    sell_pressure = number(trend.get("sell_pressure"), None)
                    trend_score = number(trend.get("trend_score"), None)
                    tc1, tc2, tc3 = st.columns(3)
                    tc1.metric("امتیاز روند", f"{trend_score:.2f}" if trend_score is not None else "—")
                    tc2.metric("فشار خرید", f"{buy_pressure:.2f}%" if buy_pressure is not None else "—")
                    tc3.metric("فشار فروش", f"{sell_pressure:.2f}%" if sell_pressure is not None else "—")
        except Exception as exc:
            st.warning(f"دریافت CryptoMeter ناموفق بود: {type(exc).__name__}: {str(exc)[:180]}")

with st.expander("نقشه رایگان لیکوییدیشن BTC (بدون کلید API)", expanded=False):
    st.markdown("این نقشه از API عمومی SmartMoneyAPI استفاده می‌کند.")
    try:
        heatmap, heatmap_error = fetch_free_liquidation_heatmap("BTC", 240, 50)
        if heatmap and heatmap.get("z"):
            totals = heatmap.get("totals") or {}
            sides = heatmap.get("by_side") or {}
            m1, m2, m3 = st.columns(3)
            m1.metric("مجموع لیکوییدیشن", fmt_usd(number(totals.get("total_notional"))))
            m2.metric("لانگ", fmt_usd(number(sides.get("long", totals.get("long_liq_notional")))))
            m3.metric("شورت", fmt_usd(number(sides.get("short", totals.get("short_liq_notional")))))
            fig_liq = go.Figure(data=go.Heatmap(
                x=heatmap["x"], y=heatmap["y"], z=heatmap["z"],
                colorscale="Turbo", colorbar={"title": "لیکوییدیشن"},
            ))
            fig_liq.update_layout(height=500, title="BTC — لیکوییدیشن‌های ۴ ساعت اخیر")
            st.plotly_chart(fig_liq, use_container_width=True)
        else:
            st.info(f"نقشه فعلاً در دسترس نیست: {heatmap_error}")
    except Exception as exc:
        st.warning(f"دریافت نقشه ناموفق بود: {type(exc).__name__}: {str(exc)[:180]}")

# ---------------------------------------------------------------------------
if not selected_lbank_pair:
    st.warning(f"تحلیل دفتر سفارش برای {selected_code} در دسترس نیست.")
    st.stop()

st.write(f"جفت‌ارز LBank: **{selected_lbank_pair}**")

try:
    with st.spinner("دریافت دفتر سفارش عمومی LBank..."):
        book = fetch_lbank_book(selected_lbank_pair)
        book_stats = analyze_book(book)
except Exception as exc:
    st.error(f"دریافت یا تحلیل دفتر سفارش ناموفق بود: {exc}")
    st.stop()

try:
    with st.spinner("دریافت معاملات اخیر LBank..."):
        trades = fetch_lbank_trades(selected_lbank_pair)
        trade_stats = analyze_trades(trades)
except Exception as exc:
    trade_stats = analyze_trades([])
    st.warning(f"معاملات اخیر قابل دریافت نبودند: {type(exc).__name__}: {str(exc)[:200]}")

metrics = st.columns(4)
metrics[0].metric("بهترین خرید", fmt_price(book_stats["best_bid"]))
metrics[1].metric("بهترین فروش", fmt_price(book_stats["best_ask"]))
metrics[2].metric("فاصله خرید و فروش", format_percent(book_stats["spread_pct"]))
metrics[3].metric("عدم‌تعادل دفتر سفارش", f"{book_stats['imbalance'] * 100:+.2f}%")

if book_stats["imbalance"] > 0:
    st.info("ارزش سفارش‌های خریدِ قابل‌مشاهده بیشتر است؛ این به‌تنهایی به معنی رشد نیست.")
elif book_stats["imbalance"] < 0:
    st.info("ارزش سفارش‌های فروشِ قابل‌مشاهده بیشتر است؛ این به‌تنهایی به معنی افت نیست.")
else:
    st.info("عدم‌تعادل نزدیک به صفر است.")

mid_price = number(book_stats.get("mid"))
bid_zones = [z for z in book_stats.get("bid_walls", []) if number(z.get("price")) < mid_price]
ask_zones = [z for z in book_stats.get("ask_walls", []) if number(z.get("price")) > mid_price]
strongest_bid = max(bid_zones, key=lambda z: number(z.get("value")), default=None)
strongest_ask = max(ask_zones, key=lambda z: number(z.get("value")), default=None)

st.markdown("#### جمع‌بندی ساده نقدینگی")

def format_wall_distance(distance):
    if distance < 0.0001: return "<0.0001%"
    if distance < 0.01: return f"{distance:.4f}%"
    if distance < 1: return f"{distance:.3f}%"
    return f"{distance:.2f}%"

if strongest_bid and strongest_ask:
    bid_distance = (mid_price - number(strongest_bid["price"])) / mid_price * 100
    ask_distance = (number(strongest_ask["price"]) - mid_price) / mid_price * 100
    liq_a, liq_b = st.columns(2)
    liq_a.metric("قوی‌ترین ناحیه خرید زیر قیمت", fmt_price(strongest_bid["price"]),
                 delta=f"{format_wall_distance(bid_distance)} پایین‌تر")
    liq_b.metric("قوی‌ترین ناحیه فروش بالای قیمت", fmt_price(strongest_ask["price"]),
                 delta=f"{format_wall_distance(ask_distance)} بالاتر")
    st.write(f"ارزش: **خرید {fmt_usd(number(strongest_bid.get('value')))}** در برابر "
             f"**فروش {fmt_usd(number(strongest_ask.get('value')))}**.")
else:
    st.info("ناحیه تجمیعی معناداری در یکی از دو سمت پیدا نشد.")

flows = st.columns(4)
flows[0].metric("حجم خرید اخیر", fmt_usd(trade_stats["buy_usd"]))
flows[1].metric("حجم فروش اخیر", fmt_usd(trade_stats["sell_usd"]))
flows[2].metric("جریان خالص", fmt_usd(trade_stats["net_flow"]))
flows[3].metric("تعداد معاملات", trade_stats["total_count"])

bid_df = pd.DataFrame()
ask_df = pd.DataFrame()
with st.expander("جزئیات دفتر سفارش: سطوح خرید و فروش"):
    bid_col, ask_col = st.columns(2)
    with bid_col:
        st.markdown("**سطوح خرید**")
        bid_df = pd.DataFrame(book_stats["bid_walls"]).rename(columns={
            "price": "قیمت", "quantity": "مقدار", "value": "ارزش تقریبی USDT",
            "classification": "نوع سطح",
        })
        if not bid_df.empty:
            bid_df["قیمت"] = bid_df["قیمت"].map(fmt_price)
            bid_df["مقدار"] = bid_df["مقدار"].map(lambda v: f"{v:,.6f}")
            bid_df["ارزش تقریبی USDT"] = bid_df["ارزش تقریبی USDT"].map(fmt_usd)
            st.dataframe(bid_df, use_container_width=True, hide_index=True)
    with ask_col:
        st.markdown("**سطوح فروش**")
        ask_df = pd.DataFrame(book_stats["ask_walls"]).rename(columns={
            "price": "قیمت", "quantity": "مقدار", "value": "ارزش تقریبی USDT",
            "classification": "نوع سطح",
        })
        if not ask_df.empty:
            ask_df["قیمت"] = ask_df["قیمت"].map(fmt_price)
            ask_df["مقدار"] = ask_df["مقدار"].map(lambda v: f"{v:,.6f}")
            ask_df["ارزش تقریبی USDT"] = ask_df["ارزش تقریبی USDT"].map(fmt_usd)
            st.dataframe(ask_df, use_container_width=True, hide_index=True)

# ---------------------------------------------------------------------------
st.divider()
st.subheader("۷. سناریوی احتمالی ورود و خروج")
st.caption("محاسبه تقریبی بر پایه دفتر سفارش، نه سفارش واقعی یا توصیه خریدوفروش.")

plan = calculate_trade_plan(book_stats, trade_stats)

if plan["status"] == "ENTRY WATCH":
    st.success("وضعیت: فرصت برای بررسی بیشتر")
else:
    st.warning("وضعیت: انتظار برای تأیید")

st.write(plan["reason"])

if "entry" in plan:
    levels = st.columns(4)
    levels[0].metric("ورود احتمالی", fmt_price(plan["entry"]))
    levels[1].metric("هدف خروج", fmt_price(plan["target"]))
    levels[2].metric("حد ابطال سناریو", fmt_price(plan["stop"]))
    levels[3].metric("نسبت پاداش به ریسک", f"{plan['rr']:.2f}")

# Position size calculator
with st.expander("🧮 ماشین‌حساب حجم پوزیشن بر پایه ATR"):
    col_a, col_b = st.columns(2)
    capital = col_a.number_input("سرمایه کل (USDT)", min_value=10.0, value=1000.0, step=50.0)
    risk_pct = col_b.number_input("درصد ریسک هر معامله", min_value=0.1, max_value=5.0, value=1.0, step=0.1)
    plan_4h = timeframe_results.get("4 ساعت", {}).get("plan", {})
    ref_close = plan_4h.get("close")
    ref_stop = plan_4h.get("stop")
    if ref_close and ref_stop:
        risk_per_unit = abs(ref_close - ref_stop)
        risk_amount = capital * (risk_pct / 100)
        position_size = risk_amount / risk_per_unit if risk_per_unit > 0 else 0
        notional = position_size * ref_close
        st.markdown(
            f"- **ریسک دلاری:** {fmt_usd(risk_amount)}\n"
            f"- **حجم پیشنهادی:** {position_size:,.6f} واحد\n"
            f"- **ارزش پوزیشن:** {fmt_usd(notional)}\n"
            f"- **فاصله stop تا entry:** {fmt_price(risk_per_unit)}"
        )
        st.caption("بر پایه ATR تایم‌فریم ۴ساعته.")
    else:
        st.info("برای محاسبه، ابتدا تحلیل تایم‌فریم ۴ساعته باید کامل شود.")

with st.expander("⚠️ آگاهی از ریسک ضرر زنجیره‌ای"):
    table = pd.DataFrame({
        "ریسک هر معامله": [f"{p}%" for p in [0.5, 1, 2, 3, 5]],
        "۵ ضرر متوالی": [f"-{round((1-(1-p/100)**5)*100,1)}%" for p in [0.5, 1, 2, 3, 5]],
        "۱۰ ضرر متوالی": [f"-{round((1-(1-p/100)**10)*100,1)}%" for p in [0.5, 1, 2, 3, 5]],
    })
    st.dataframe(table, hide_index=True, use_container_width=True)

# ---------------------------------------------------------------------------
st.divider()
st.subheader("۸. جمع‌بندی تصمیم: طلا، رمزارز یا ترکیب؟")
try:
    allocation_ratio = get_btc_xau_metrics(lbank_pairs)
    btc_trend = get_asset_trend_score("BTC", lbank_pairs, int(atr_period), float(ut_multiplier))
    gold_trend = get_asset_trend_score("XAUT", lbank_pairs, int(atr_period), float(ut_multiplier))
    ratio_week = allocation_ratio.get("change_7d")
    ratio_day = allocation_ratio.get("change_1d")
    btc_score = btc_trend.get("score")
    gold_score = gold_trend.get("score")

    if ratio_week is None or ratio_day is None or btc_score is None or gold_score is None:
        raise ValueError("داده کافی برای تخصیص پویا وجود ندارد.")

    ratio_signal = max(-1.0, min(1.0, (0.65 * ratio_week + 0.35 * ratio_day) / 5.0))
    trend_signal = max(-1.0, min(1.0, (float(btc_score) - float(gold_score)) / 4.0))
    crypto_edge = 0.65 * ratio_signal + 0.35 * trend_signal
    crypto_pct = int(round(max(10.0, min(90.0, 50.0 + 40.0 * crypto_edge))))
    gold_pct = 100 - crypto_pct

    if crypto_edge >= 0.25:
        allocation = f"تمایل به رمزارز: {crypto_pct}% رمزارز + {gold_pct}% طلا"
        allocation_reason = "نسبت BTC/XAUT و/یا روند BTC برتری رمزارز را نشان می‌دهد."
    elif crypto_edge <= -0.25:
        allocation = f"تمایل به طلا: {gold_pct}% طلا + {crypto_pct}% رمزارز"
        allocation_reason = "نسبت BTC/XAUT و/یا روند XAUT برتری طلا را نشان می‌دهد."
    else:
        allocation = f"سیگنال ترکیبی: {gold_pct}% طلا + {crypto_pct}% رمزارز"
        allocation_reason = "شواهد متناقض یا نزدیک به هم‌اند."

    st.subheader(f"پیشنهاد فعلی مدل: {allocation}")
    ac1, ac2, ac3 = st.columns(3)
    ac1.metric("سهم طلا (XAUT)", f"{gold_pct}%")
    ac2.metric("سهم رمزارز", f"{crypto_pct}%")
    ac3.metric("تغییر هفت‌روزه BTC/XAUT", f"{ratio_week:+.2f}%")
    st.write(allocation_reason)
    st.caption("کنترل‌های تأیید: " + "؛ ".join(
        (btc_trend.get("notes") or []) + (gold_trend.get("notes") or [])))
except Exception as exc:
    allocation = None
    allocation_reason = None
    gold_pct = None
    crypto_pct = None
    st.warning(f"جمع‌بندی تخصیص کامل نشد: {type(exc).__name__}: {str(exc)[:180]}")

st.warning(
    "این برنامه از داده‌های عمومی بازار استفاده می‌کند و معامله‌ای ثبت نمی‌کند. "
    "سیگنال‌ها، سطوح ATR و سطوح دفتر سفارش تخمینی هستند و سود را تضمین نمی‌کنند."
)

# ---------------------------------------------------------------------------
# PDF download
# ---------------------------------------------------------------------------
try:
    pdf_report_data = {
        "selected_code": selected_code,
        "selected_pair": selected_lbank_pair,
        "btc_usd": fmt_usd(btc_usd),
        "fetched_count": fetched_count,
        "coin_count": len(coins),
        "lbank_status": lbank_status,
        "coins_df": display_df,
        "comparison_df": comp_df,
        "crypto_ranking": crypto_ranking,
        "btc_xau_metrics": locals().get("allocation_ratio") or {},
        "allocation": locals().get("allocation"),
        "allocation_reason": locals().get("allocation_reason"),
        "gold_pct": locals().get("gold_pct"),
        "crypto_pct": locals().get("crypto_pct"),
        "timeframe_results": timeframe_results,
        "best_bid": fmt_price(book_stats.get("best_bid")),
        "best_ask": fmt_price(book_stats.get("best_ask")),
        "spread_pct": format_percent(book_stats.get("spread_pct", 0)),
        "imbalance": f"{book_stats.get('imbalance', 0) * 100:+.2f}%",
        "buy_usd": fmt_usd(trade_stats.get("buy_usd", 0)),
        "sell_usd": fmt_usd(trade_stats.get("sell_usd", 0)),
        "net_flow": fmt_usd(trade_stats.get("net_flow", 0)),
        "trade_count": trade_stats.get("total_count", 0),
        "bid_df": bid_df,
        "ask_df": ask_df,
        "order_plan": plan,
    }
    pdf_bytes = build_pdf_report(pdf_report_data)
    safe_code = "".join(ch for ch in str(selected_code) if ch.isalnum() or ch in "-_") or "market"
    pdf_download_placeholder.download_button(
        label="📄 دانلود گزارش کامل PDF",
        data=pdf_bytes,
        file_name=f"SpotPilot_AI_{safe_code}_report.pdf",
        mime="application/pdf",
        use_container_width=True,
    )
except Exception as pdf_exc:
    pdf_download_placeholder.error(
        f"ساخت PDF ناموفق بود: {type(pdf_exc).__name__}: {str(pdf_exc)[:250]}"
    )
