
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

# ---------------------------------------------------------------------------
# Config & logging
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Shared requests session (connection pooling)
# ---------------------------------------------------------------------------
_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": "SpotPilotAI/2.0 (+public-market-data)",
    "Accept": "application/json",
})


def _http_get(url, params=None, timeout=20, retries=2):
    """GET with bounded retries; retries only transient conditions."""
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


# ---------------------------------------------------------------------------
# Number / format helpers
# ---------------------------------------------------------------------------
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
    """Accept both LBank v2 styles: {'result': True} or {'code': 0}."""
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


# ---------------------------------------------------------------------------
# LiveCoinWatch
# ---------------------------------------------------------------------------
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
    """Fetch LCW list with early break when consecutive pages fall below threshold."""
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

        # Early exit: if min_btc_volume > 0 and the whole page volume is well below threshold,
        # we don't need to keep paginating. LCW sorts by volume descending, so once we drop
        # below threshold for a full page, later pages will only be smaller.
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


# ---------------------------------------------------------------------------
# LBank
# ---------------------------------------------------------------------------
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


def _klines_ttl(timeframe):
    return int(TIMEFRAMES.get(timeframe, {}).get("cache_ttl", 60))


# NOTE: Streamlit cannot take a dynamic TTL per call; so we wrap with a fixed
# conservative TTL and let the freshness check inside the caller drive behaviour.
@st.cache_data(ttl=60, show_spinner=False)
def fetch_lbank_klines(pair, timeframe, size=300):
    """
    Fetch closed LBank candles with robust pagination.

    Improvements over v1:
      - Cursor advances by the newest timestamp + interval.
      - Explicit 'we didn't move forward' break to avoid infinite loops.
      - Deduplicates by timestamp.
      - Filters out the currently-forming candle.
      - Returns exactly the requested number of *newest* closed candles.
    """
    if timeframe not in TIMEFRAMES:
        raise ValueError("تایم‌فریم پشتیبانی نمی‌شود.")

    tf = TIMEFRAMES[timeframe]
    interval_seconds = int(tf["seconds"])
    now_seconds = int(time.time())
    requested_size = min(max(int(size), 1), 2000)
    target_rows = requested_size + 10  # headroom for the currently-forming candle
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
        # Guard against non-advancing cursor.
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


# ---------------------------------------------------------------------------
# Indicators
# ---------------------------------------------------------------------------
def calculate_atr(df, period=14):
    """
    True Wilder ATR (RMA smoothing = EMA with alpha=1/period, adjust=False).
    Enforces a minimum of 2 to avoid degenerate period=1 noise.
    """
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
    """UT Bot ATR trailing stop on closed candles."""
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


# ---------------------------------------------------------------------------
# Elliott (now correctly-detected zigzag)
# ---------------------------------------------------------------------------
def detect_elliott_candidates(frame, left=3, right=3, min_swing_pct=0.35):
    """
    Clean zigzag: for each index, we look at a symmetric window of size
    (left + right + 1) and mark a pivot high if highs[i] is strictly greater
    than every value on the left side AND greater-or-equal to values on the
    right side (leftmost of a plateau wins). Lows are mirrored.
    """
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

    # Direction: last confirmed swing + medium-term SMA slope
    slope_up = prices[-1] > prices[-2]
    direction = 1 if slope_up else -1
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


# ---------------------------------------------------------------------------
# BTC-relative and BTC/XAUT
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# CryptoMeter
# ---------------------------------------------------------------------------
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
    response = _http_get(f"{CRYPTOMETER_URL}/{endpoint}/",
                         params=params, timeout=15)
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


# ---------------------------------------------------------------------------
# Other exchanges (CCXT) with instance reuse
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Order book / trades analysis
# ---------------------------------------------------------------------------
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
    """
    Uses the *nearest* significant wall on each side of the mid, then applies
    a light sanity filter. This makes the function actually able to produce
    an ENTRY WATCH when conditions are favourable, unlike v1 which almost
    always returned WAIT because it picked the largest (and usually farthest)
    wall.
    """
    mid = book_stats["mid"]

    def nearest_significant(walls, side):
        candidates = [w for w in walls
                      if (w["price"] < mid if side == "bid" else w["price"] > mid)]
        if not candidates:
            return None
        # Prefer significant walls if any exist; otherwise fall back to closest.
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


# ---------------------------------------------------------------------------
# Liquidation heatmap
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# PDF export (unchanged behaviour, small robustness tweaks)
# ---------------------------------------------------------------------------
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
    add_paragraph(
        "منابع زمانی یکسانی ندارند: قیمت خلاصه از LiveCoinWatch، مقایسه از تیکرهای صرافی‌ها، "
        "و تحلیل تکنیکال از کندل‌های بسته‌شده LBank است."
    )

    add_heading("۱. فهرست ارزهای LiveCoinWatch")
    add_table(report_data.get("coins_df"), col_widths=[1.0, 1.1, 1.25, 1.25, 1.15, 1.1, 0.9, 0.9])

    ratio = report_data.get("btc_xau_metrics") or {}
    if ratio.get("current") is not None:
        add_heading("نسبت BTC/XAUT و تخصیص مدل")
        add_paragraph(
            f"نرخ متقاطع فعلی: {number(ratio.get('current')):.4f} XAUT برای هر BTC | "
            f"تغییر ۱روزه: {number(ratio.get('change_1d'), float('nan')):+.2f}% | "
            f"تغییر ۷روزه: {number(ratio.get('change_7d'), float('nan')):+.2f}%"
        )
        if report_data.get("allocation"):
            add_paragraph(
                f"خروجی مدل تخصیص: {report_data.get('allocation')} | "
                f"سهم XAUT: {report_data.get('gold_pct', '—')}% | "
                f"سهم رمزارز: {report_data.get('crypto_pct', '—')}%"
            )
            add_paragraph(str(report_data.get("allocation_reason") or ""))

    ranking = report_data.get("crypto_ranking") or []
    if ranking:
        add_heading("رتبه‌بندی رمزارزها برای بررسی بیشتر")
        ranking_df = pd.DataFrame(ranking)
        wanted = [c for c in ["Code", "Priority", "Score", "24h Change %", "BTC Relative %", "Reason"]
                  if c in ranking_df.columns]
        ranking_df = ranking_df[wanted].head(7).copy()
        add_table(ranking_df)

    liq_summary = report_data.get("liquidity_summary") or {}
    if liq_summary:
        add_heading("جمع‌بندی ساده نقدینگی LBank")
        add_paragraph(
            f"قیمت میانی: {liq_summary.get('mid', '—')} | "
            f"دیوار خرید زیر قیمت: {liq_summary.get('bid_price', 'پیدا نشد')} "
            f"({liq_summary.get('bid_distance', '—')}) | "
            f"دیوار فروش بالای قیمت: {liq_summary.get('ask_price', 'پیدا نشد')} "
            f"({liq_summary.get('ask_distance', '—')})"
        )
        add_paragraph(str(liq_summary.get("interpretation") or ""))

    add_heading("۲. مقایسه قیمت صرافی‌ها")
    add_table(report_data.get("comparison_df"), col_widths=[1.2, 1.3, 1.1, 1.3])
    errors = report_data.get("compare_errors") or []
    if errors:
        add_paragraph("خطاهای دریافت مقایسه: " + " | ".join(str(e) for e in errors))

    add_heading("۳. تحلیل تکنیکال چندتایم‌فریمی")
    tf_results = report_data.get("timeframe_results") or {}
    if not tf_results:
        add_paragraph("نتیجه‌ای از تحلیل تایم‌فریم‌ها در دسترس نیست.")
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
                f"هدف ۱: {plan.get('target1', '—')} | هدف ۲: {plan.get('target2', '—')} | "
                f"توضیح: {plan.get('reason', '—')}"
            )
        else:
            add_paragraph(f"توضیح سناریو: {plan.get('reason', '—')}")

    add_heading("۴. دفتر سفارش و جریان معاملات LBank")
    add_paragraph(
        f"بهترین خرید: {report_data.get('best_bid', '—')} | بهترین فروش: {report_data.get('best_ask', '—')} | "
        f"فاصله خرید و فروش: {report_data.get('spread_pct', '—')} | عدم‌تعادل دفتر سفارش: {report_data.get('imbalance', '—')}"
    )
    add_paragraph(
        f"حجم خرید اخیر: {report_data.get('buy_usd', '—')} | حجم فروش اخیر: {report_data.get('sell_usd', '—')} | "
        f"جریان خالص: {report_data.get('net_flow', '—')} | تعداد معاملات: {report_data.get('trade_count', '—')}"
    )
    add_heading("سطوح خرید و حمایت احتمالی")
    add_table(report_data.get("bid_df"), col_widths=[1.0, 1.0, 1.5, 1.2])
    add_heading("سطوح فروش و مقاومت احتمالی")
    add_table(report_data.get("ask_df"), col_widths=[1.0, 1.0, 1.5, 1.2])

    add_heading("۵. سناریوی احتمالی ورود و خروج")
    order_plan = report_data.get("order_plan") or {}
    add_paragraph(f"وضعیت: {order_plan.get('status', '—')} | توضیح: {order_plan.get('reason', '—')}")
    if "entry" in order_plan:
        add_paragraph(
            f"ورود احتمالی: {order_plan.get('entry', '—')} | هدف خروج: {order_plan.get('target', '—')} | "
            f"حد ابطال سناریو: {order_plan.get('stop', '—')} | نسبت پاداش به ریسک: {order_plan.get('rr', '—')}"
        )
    add_heading("محدودیت‌ها و هشدار")
    add_paragraph(
        "این گزارش از داده‌های عمومی بازار استفاده می‌کند و هیچ معامله‌ای ثبت نمی‌کند. "
        "سیگنال‌ها و سطوح تحلیلی تخمینی هستند و سود را تضمین نمی‌کنند."
    )

    doc.build(story, onFirstPage=draw_footer, onLaterPages=draw_footer)
    buffer.seek(0)
    return buffer.getvalue()
