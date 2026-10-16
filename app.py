"""
SpotPilot AI — Market analysis dashboard
Version: v2.6-early-flow-risk
- Multi-source klines with exchange-time candle validation
- UT Bot ATR(1) defaults aligned with the requested TradingView setup
- ATR(14) daily volatility normalization for ranking (separate from UT ATR)
- State-aware scoring without double-counting the same UT trend
- Per-timeframe candle freshness and order-book age visibility
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

# Primary kline sources in preference order. LBank is a direct-API fallback
# at the end of fetch_klines_multi_source.
KLINE_SOURCES = ["binance", "bybit", "okx", "kucoin"]

TIMEFRAMES = {
    "15 دقیقه": {"type": "minute15", "seconds": 15 * 60, "cache_ttl": 45,  "ccxt": "15m"},
    "4 ساعت":   {"type": "hour4",    "seconds": 4 * 60 * 60, "cache_ttl": 90, "ccxt": "4h"},
    "روزانه":   {"type": "day1",     "seconds": 24 * 60 * 60, "cache_ttl": 300, "ccxt": "1d"},
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
    "User-Agent": "SpotPilotAI/2.2 (+public-market-data)",
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


def tiered_score(pct, thresholds=(0.5, 3.0)):
    """Fixed percentage tiering, retained as fallback when ATR is unavailable."""
    try:
        if pct is None or not pd.notna(pct):
            return 0
        pct = float(pct)
    except (TypeError, ValueError):
        return 0
    low, high = thresholds
    if pct >= high: return 2
    if pct >= low:  return 1
    if pct <= -high: return -2
    if pct <= -low:  return -1
    return 0


def tiered_score_atr(pct, atr_pct, z_thresholds=(1.0, 3.0)):
    """Volatility-adjusted tiered score using daily ATR(14) percentage."""
    if atr_pct is None or not pd.notna(atr_pct) or atr_pct <= 0:
        return tiered_score(pct, thresholds=(0.5, 3.0))
    try:
        pct = float(pct)
        atr_pct = float(atr_pct)
        if not pd.notna(pct) or atr_pct <= 0:
            return 0
        z = pct / atr_pct
    except (TypeError, ValueError):
        return 0
    low, high = z_thresholds
    if z >= high: return 2
    if z >= low: return 1
    if z <= -high: return -2
    if z <= -low: return -1
    return 0


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
# CCXT core
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


# ===========================================================================
# LBANK — direct API (used for order book, trades, pair list, and kline fallback)
# ===========================================================================
def lbank_symbol_from_code(code, quote="usdt"):
    return f"{code.lower()}_{quote.lower()}"


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
    """Fetch the LBank order book and record exchange and local receive timestamps."""
    payload = get_json(f"{LBANK_URL}/depth.do", params={"symbol": pair, "size": 100})
    if not is_success(payload):
        raise RuntimeError(f"LBank depth API error: {payload.get('msg', payload)}")
    data = payload.get("data") or {}
    bids = data.get("bids") or []
    asks = data.get("asks") or []
    if not bids or not asks:
        raise RuntimeError(f"دفتر سفارش {pair} خالی است.")
    exchange_ts = data.get("timestamp") or payload.get("ts")
    try:
        exchange_ts = float(exchange_ts)
        if exchange_ts > 1e12:
            exchange_ts /= 1000.0
    except (TypeError, ValueError):
        exchange_ts = None
    return {"bids": bids, "asks": asks, "timestamp": exchange_ts, "fetched_at": time.time()}


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
    """Direct LBank kline API. Used as last-resort fallback."""
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
# MULTI-SOURCE KLINES
# ===========================================================================
def _exchange_now_seconds(exchange):
    """Prefer exchange server time; fall back to local UTC time if unavailable."""
    try:
        if getattr(exchange, "has", {}).get("fetchTime"):
            server_ms = exchange.fetch_time()
            if server_ms:
                return int(server_ms // 1000)
    except Exception:
        pass
    try:
        server_ms = exchange.milliseconds()
        if server_ms:
            return int(server_ms // 1000)
    except Exception:
        pass
    return int(time.time())


def _normalize_ccxt_ohlcv(raw, timeframe, now_seconds=None):
    """Normalize OHLCV, validate candle geometry, and discard unfinished candles."""
    if not raw:
        return None
    df = pd.DataFrame(raw, columns=["timestamp_ms", "Open", "High", "Low", "Close", "Volume"])
    df["timestamp"] = pd.to_numeric(df["timestamp_ms"], errors="coerce") // 1000
    df = df.dropna(subset=["timestamp"])
    df["timestamp"] = df["timestamp"].astype("int64")
    for col in ["Open", "High", "Low", "Close", "Volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["Open", "High", "Low", "Close", "Volume"])
    df = df[(df["Open"] > 0) & (df["High"] > 0) & (df["Low"] > 0)
            & (df["Close"] > 0) & (df["Volume"] >= 0)]
    df = df[(df["High"] >= df[["Open", "Close", "Low"]].max(axis=1))
            & (df["Low"] <= df[["Open", "Close", "High"]].min(axis=1))]
    interval_seconds = int(TIMEFRAMES[timeframe]["seconds"])
    if now_seconds is None:
        now_seconds = int(time.time())
    df = df[(df["timestamp"] + interval_seconds) <= int(now_seconds)]
    df = df.sort_values("timestamp").drop_duplicates(subset=["timestamp"], keep="last").reset_index(drop=True)
    df["Time"] = pd.to_datetime(df["timestamp"], unit="s", utc=True)
    return df


@st.cache_data(ttl=60, show_spinner=False)
def fetch_klines_multi_source(code, timeframe, size=300, quote="USDT"):
    """
    Try major exchanges first (Binance/Bybit/OKX/KuCoin), then fall back to
    LBank direct API. Returns (df, source_name, errors_list).
    """
    code = str(code).upper()
    quote = str(quote).upper()
    symbol = f"{code}/{quote}"
    ccxt_tf = TIMEFRAMES[timeframe]["ccxt"]
    errors = []

    if timeframe not in TIMEFRAMES:
        return None, None, [f"تایم‌فریم پشتیبانی نمی‌شود: {timeframe}"]

    for ex_id in KLINE_SOURCES:
        try:
            symbols = get_other_exchange_symbols(ex_id)
            if symbol not in symbols:
                errors.append(f"{ex_id}: {symbol} موجود نیست")
                continue
            exchange = make_exchange(ex_id)
            raw = exchange.fetch_ohlcv(symbol, timeframe=ccxt_tf, limit=int(size))
            if not raw or len(raw) < 50:
                errors.append(f"{ex_id}: فقط {len(raw) if raw else 0} کندل")
                continue
            df = _normalize_ccxt_ohlcv(raw, timeframe, now_seconds=_exchange_now_seconds(exchange))
            if df is None or df.empty:
                errors.append(f"{ex_id}: داده نامعتبر")
                continue
            min_needed = min(int(size), 200)
            if len(df) < min_needed:
                errors.append(f"{ex_id}: تعداد کافی نیست ({len(df)}<{min_needed})")
                continue
            return df, ex_id.upper(), errors
        except Exception as exc:
            errors.append(f"{ex_id}: {type(exc).__name__}: {str(exc)[:100]}")
            continue

    # Last resort: LBank direct API
    try:
        lbank_pair = lbank_symbol_from_code(code, quote)
        df = fetch_lbank_klines(lbank_pair, timeframe, size)
        return df, "LBANK", errors
    except Exception as exc:
        errors.append(f"LBANK: {type(exc).__name__}: {str(exc)[:120]}")

    return None, None, errors


# ===========================================================================
# INDICATORS
# ===========================================================================
def calculate_atr(df, period=1):
    """True Range for period=1; Wilder ATR with an SMA seed for longer periods."""
    period = max(int(period), 1)
    high = pd.to_numeric(df["High"], errors="coerce")
    low = pd.to_numeric(df["Low"], errors="coerce")
    close = pd.to_numeric(df["Close"], errors="coerce")
    prev_close = close.shift(1)
    true_range = pd.concat([
        high - low, (high - prev_close).abs(), (low - prev_close).abs(),
    ], axis=1).max(axis=1, skipna=True)
    true_range = true_range.where(np.isfinite(true_range) & (true_range >= 0))
    if period == 1:
        return true_range.rename("ATR")
    values = true_range.to_numpy(dtype=float)
    result = np.full(len(values), np.nan, dtype=float)
    for end in range(period - 1, len(values)):
        window = values[end - period + 1:end + 1]
        if not np.isfinite(window).all():
            continue
        if end == period - 1 or not np.isfinite(result[end - 1]):
            result[end] = float(np.mean(window))
        else:
            result[end] = ((result[end - 1] * (period - 1)) + values[end]) / period
    return pd.Series(result, index=df.index, name="ATR")


def calculate_ut_bot(df, atr_period=1, key_value=2.0):
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


def add_market_indicators(df, atr_period=1, key_value=2.0):
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


def ut_status(df):
    """
    UT Bot is a stateful trailing-stop indicator. After a buy fires, the trend
    stays bullish until a sell fires — regardless of how many candles ago that
    was. We walk backwards to find the most recent crossover.
    """
    if df is None or df.empty:
        return {"last_signal": None, "candles_ago": None, "fresh": False, "trend": 0}

    last = df.iloc[-1]
    close = number(last.get("Close"))
    ut_stop = number(last.get("UT Stop"), float("nan"))

    trend = 0
    if close > 0 and pd.notna(ut_stop):
        trend = 1 if close > ut_stop else -1

    n = len(df)
    for i in range(n - 1, -1, -1):
        try:
            if bool(df["UT Buy"].iloc[i]):
                return {"last_signal": "buy", "candles_ago": n - 1 - i,
                        "fresh": i == n - 1, "trend": trend}
            if bool(df["UT Sell"].iloc[i]):
                return {"last_signal": "sell", "candles_ago": n - 1 - i,
                        "fresh": i == n - 1, "trend": trend}
        except (KeyError, IndexError):
            break
    return {"last_signal": None, "candles_ago": None, "fresh": False, "trend": trend}


def latest_signal_summary(df):
    if df.empty:
        raise ValueError("داده‌ای برای تحلیل وجود ندارد.")

    last = df.iloc[-1]
    close = number(last["Close"])
    atr = number(last["ATR"])
    ut_stop = number(last["UT Stop"], float("nan"))
    ut = ut_status(df)

    if close <= 0 or atr <= 0 or pd.isna(ut_stop):
        status = "داده ناکافی"
    else:
        if ut["last_signal"] == "buy":
            status = "سیگنال خرید UT (تازه)" if ut["fresh"] else f"آخرین سیگنال: خرید ({ut['candles_ago']} کندل قبل)"
        elif ut["last_signal"] == "sell":
            status = "سیگنال فروش UT (تازه)" if ut["fresh"] else f"آخرین سیگنال: فروش ({ut['candles_ago']} کندل قبل)"
        else:
            status = "بدون سیگنال UT"

        ma50 = number(last.get("SMA 50"), float("nan"))
        ma200 = number(last.get("SMA 200"), float("nan"))
        if pd.notna(ma50) and pd.notna(ma200):
            if close > ma50 > ma200:
                status += " | روند MA صعودی"
            elif close < ma50 < ma200:
                status += " | روند MA نزولی"

    return {
        "status": status,
        "close": close, "atr": atr, "ut_stop": ut_stop,
        "buy_signal": bool(last["UT Buy"]),
        "sell_signal": bool(last["UT Sell"]),
        "ut_last_signal": ut["last_signal"],
        "ut_candles_ago": ut["candles_ago"],
        "ut_fresh": ut["fresh"],
        "ut_trend": ut["trend"],
        "structure": str(last["Structure"]),
        "time": last["Time"],
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

    last_sig = summary.get("ut_last_signal")
    trend = summary.get("ut_trend", 0)

    if summary["buy_signal"] and not summary["sell_signal"]:
        direction = "LONG"
        reason = "سیگنال خرید تازه UT روی آخرین کندل بسته‌شده."
    elif summary["sell_signal"] and not summary["buy_signal"]:
        direction = "SHORT"
        reason = "سیگنال فروش تازه UT روی آخرین کندل بسته‌شده."
    elif last_sig == "buy" and trend > 0 and bullish_ma:
        direction = "LONG"
        reason = (f"آخرین سیگنال خرید UT {summary['ut_candles_ago']} کندل قبل ثبت شد "
                  "و روند فعلی با آن هم‌جهت است.")
    elif last_sig == "sell" and trend < 0 and bearish_ma:
        direction = "SHORT"
        reason = (f"آخرین سیگنال فروش UT {summary['ut_candles_ago']} کندل قبل ثبت شد "
                  "و روند فعلی با آن هم‌جهت است.")
    elif bullish_ma and close > summary["ut_stop"]:
        direction = "LONG WATCH"
        reason = "روند میانگین‌ها صعودی است و قیمت بالای حد UT قرار دارد؛ سیگنال UT ثبت نشده."
    elif bearish_ma and close < summary["ut_stop"]:
        direction = "SHORT WATCH"
        reason = "روند میانگین‌ها نزولی است و قیمت زیر حد UT قرار دارد؛ سیگنال UT ثبت نشده."
    else:
        return {"status": "WAIT",
                "reason": "سیگنال UT، روند میانگین‌ها و وضعیت قیمت هم‌جهت نیستند؛ ورود تأیید نشده.",
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

    return {
        "status": direction,
        "reason": reason,
        "close": close, "atr": atr, "stop": stop,
        "target1": target1, "target2": target2,
        "rr1": 1.5, "rr2": 2.0, "direction": direction,
    }


def freshness_report(summary, timeframe, source_name=None, ticker_price=None):
    interval_seconds = int(TIMEFRAMES.get(timeframe, {}).get("seconds", 0))
    if not interval_seconds:
        return {"stale": False, "age_minutes": 0.0, "last_candle_close": None,
                "message": "تایم‌فریم نامشخص.", "ticker_note": None, "source": source_name}
    last_open = summary.get("time")
    if last_open is None or pd.isna(last_open):
        return {"stale": True, "age_minutes": float("inf"), "last_candle_close": None,
                "message": "زمان آخرین کندل نامشخص است.", "ticker_note": None, "source": source_name}
    candle_close = pd.to_datetime(last_open, utc=True) + pd.Timedelta(seconds=interval_seconds)
    age_minutes = max(0.0, (pd.Timestamp.now(tz="UTC") - candle_close).total_seconds() / 60.0)
    stale_limits = {"15 دقیقه": 30.0, "4 ساعت": 480.0, "روزانه": 2160.0}
    stale_limit = stale_limits.get(timeframe, (interval_seconds / 60.0) * 2.0)
    stale = age_minutes > stale_limit
    ticker_note = None
    if stale and ticker_price:
        try:
            close = number(summary.get("close"))
            gap_pct = (ticker_price / close - 1) * 100 if close > 0 else float("nan")
            ticker_note = (f"آخرین قیمت زنده LBank: {fmt_price(ticker_price)} "
                           f"({gap_pct:+.3f}% نسبت به آخرین کندل بسته)")
        except Exception:
            ticker_note = f"آخرین قیمت زنده LBank: {fmt_price(ticker_price)}"
    src_part = f" | منبع: {source_name}" if source_name else ""
    message = (f"آخرین کندل بسته‌شده: {candle_close.strftime('%Y-%m-%d %H:%M UTC')} | "
               f"فاصله: {age_minutes:.0f} دقیقه | هشدار stale پس از {stale_limit:.0f} دقیقه{src_part}")
    return {"stale": stale, "age_minutes": age_minutes,
            "last_candle_close": candle_close, "message": message,
            "ticker_note": ticker_note, "source": source_name}


# ===========================================================================
# ELLIOTT
# ===========================================================================
def detect_elliott_candidates(frame, left=3, right=3, min_swing_pct=0.35):
    """Return one recent rule-valid five-wave impulse, never a raw ZigZag."""
    columns = ["index", "Time", "Price", "Kind", "Label"]
    empty = pd.DataFrame(columns=columns)
    if frame is None or len(frame) < max(40, left + right + 12):
        return empty
    data = frame.reset_index(drop=True).copy()
    highs = pd.to_numeric(data["High"], errors="coerce").to_numpy(dtype=float)
    lows = pd.to_numeric(data["Low"], errors="coerce").to_numpy(dtype=float)
    raw = []
    for i in range(left, len(data) - right):
        hw, lw = highs[i-left:i+right+1], lows[i-left:i+right+1]
        if not np.isfinite(highs[i]) or not np.isfinite(lows[i]):
            continue
        if np.isfinite(hw).any() and highs[i] >= np.nanmax(hw) and highs[i] > np.nanmax(highs[i-left:i]):
            raw.append({"index": i, "Time": data.loc[i, "Time"], "Price": float(highs[i]), "Kind": "high"})
        if np.isfinite(lw).any() and lows[i] <= np.nanmin(lw) and lows[i] < np.nanmin(lows[i-left:i]):
            raw.append({"index": i, "Time": data.loc[i, "Time"], "Price": float(lows[i]), "Kind": "low"})
    raw.sort(key=lambda p: (p["index"], 0 if p["Kind"] == "low" else 1))
    pivots = []
    for point in raw:
        if pivots and point["Kind"] == pivots[-1]["Kind"]:
            more_extreme = point["Price"] > pivots[-1]["Price"] if point["Kind"] == "high" else point["Price"] < pivots[-1]["Price"]
            if more_extreme: pivots[-1] = point
            continue
        if pivots:
            prev = pivots[-1]["Price"]
            if prev <= 0 or abs(point["Price"] / prev - 1) * 100 < min_swing_pct: continue
        pivots.append(point)
    if len(pivots) < 6: return empty
    candidates = []
    for start in range(len(pivots) - 5):
        pts = pivots[start:start+6]
        p = [x["Price"] for x in pts]
        kinds = [x["Kind"] for x in pts]
        up = kinds == ["low","high","low","high","low","high"]
        down = kinds == ["high","low","high","low","high","low"]
        if not (up or down): continue
        direction = 1 if up else -1
        w = [direction * (p[i+1]-p[i]) for i in range(5)]
        w1,w2,w3,w4,w5 = w
        if min(w) <= 0 or w2 > w1 or w3 <= min(w1,w5): continue
        if (up and (p[2] <= p[0] or p[3] <= p[1] or p[4] < p[1])) or (down and (p[2] >= p[0] or p[3] >= p[1] or p[4] > p[1])): continue
        fib_penalty = abs(w2/w1 - 0.5) + abs(w4/w3 - 0.382) + abs(w3/w1 - 1.618) * 0.35
        recency_penalty = (len(pivots) - 1 - start - 5) * 0.08
        candidates.append((fib_penalty + recency_penalty, pts, fib_penalty))
    if not candidates: return empty
    candidates.sort(key=lambda item: (item[0], -item[1][-1]["index"]))
    _, chosen, fib_penalty = candidates[0]
    out = pd.DataFrame(chosen)
    out["Label"] = ["0","1","2","3","4","5"]
    out.attrs["fib_penalty"] = float(fib_penalty)
    out.attrs["count_quality"] = "higher" if fib_penalty < 0.75 else "moderate"
    return out

def analyze_elliott_context(frame, pivots):
    """Validate the selected impulse and project a conditional ABC correction."""
    out = {"direction": 0, "score": 0.0, "confidence": "کم", "notes": [], "invalid": [],
           "forecast": [], "forecast_labels": [], "phase": "no_valid_impulse"}
    if frame is None or len(frame) < 30 or pivots is None or len(pivots) != 6:
        out["notes"].append("ایمپالس معتبر پنج‌موجی پیدا نشد؛ شمارش زیگزاگی نمایش داده نمی‌شود.")
        return out
    pivots = pivots.reset_index(drop=True)
    prices = pd.to_numeric(pivots["Price"], errors="coerce").astype(float).tolist()
    kinds = pivots["Kind"].tolist()
    up = kinds == ["low","high","low","high","low","high"]
    down = kinds == ["high","low","high","low","high","low"]
    if not (up or down):
        out["invalid"].append("توالی پیوت‌ها با ایمپالس سازگار نیست."); return out
    direction = 1 if up else -1
    w = [direction * (prices[i+1]-prices[i]) for i in range(5)]
    w1,w2,w3,w4,w5 = w
    if min(w) <= 0 or w2 > w1 or w3 <= min(w1,w5):
        out["invalid"].append("قواعد طول موج‌های ۱ تا ۵ نقض شده است."); return out
    if (up and (prices[2] <= prices[0] or prices[3] <= prices[1] or prices[4] < prices[1])) or (down and (prices[2] >= prices[0] or prices[3] >= prices[1] or prices[4] > prices[1])):
        out["invalid"].append("قواعد مبدأ موج ۲، شکست موج ۳ یا هم‌پوشانی موج ۴ نقض شده است."); return out
    data = frame.reset_index(drop=True)
    last = number(data["Close"].iloc[-1], prices[5])
    end_index = int(pivots["index"].iloc[-1])
    impulse_size = abs(prices[5]-prices[0])
    ranges = pd.to_numeric(data["High"], errors="coerce") - pd.to_numeric(data["Low"], errors="coerce")
    atr = float(ranges.rolling(14).mean().iloc[-1]) if len(ranges) >= 14 else float("nan")
    if not math.isfinite(atr) or atr <= 0: atr = max(abs(last)*0.005, 1e-8)
    out.update({"direction": direction, "score": 2.0, "phase": "abc_correction_scenario",
                "confidence": "متوسط" if pivots.attrs.get("count_quality") == "higher" else "کم",
                "wave5_price": prices[5], "impulse_size": impulse_size})
    out["notes"].append("یک شمارش پنج‌موجی از قواعد اصلی ایمپالس عبور کرده است.")
    out["notes"].append("فیبوناچی فقط برای رتبه‌بندی سناریو استفاده شده و اثبات موج نیست.")
    if end_index < len(data)-1 and abs(last-prices[5]) > max(atr*1.5, impulse_size*0.12):
        out["phase"] = "post_impulse_unconfirmed"
        out["notes"].append("قیمت از انتهای موج ۵ فاصله گرفته؛ پیش‌بینی کهنه صادر نمی‌شود.")
        return out
    sign = -direction
    a = prices[5] + sign*impulse_size*0.382
    b = a - sign*impulse_size*0.191
    cc = b + sign*impulse_size*0.382
    out["forecast"] = [a,b,cc]
    out["forecast_labels"] = ["A احتمالی (0.382)", "B احتمالی (بازگشت 0.5 از A)", "C احتمالی (امتداد)"]
    out["notes"].append("سناریوی اصلاح ABC از انتهای موج ۵ و اندازه ایمپالس محاسبه شده است.")
    return out

def get_asset_trend_score(code, lbank_pairs, atr_period=1, ut_multiplier=2.0):
    """
    UT crossover: +/-2 when fresh and aligned; existing aligned state: +/-1.
    MA alignment: +/-1. ATR% is independent daily ATR(14), not UT ATR(1).
    """
    score = 0
    notes = []
    atr_pct_latest = None
    for timeframe in ("4 ساعت", "روزانه"):
        try:
            candles, src, errs = fetch_klines_multi_source(code, timeframe, 260)
            if candles is None or candles.empty:
                notes.append(f"{timeframe}: داده موجود نیست")
                continue
            indicators = add_market_indicators(candles, atr_period, ut_multiplier)
            ut = ut_status(indicators)
            if ut["fresh"] and ut["last_signal"] == "buy" and ut["trend"] > 0:
                score += 2
                notes.append(f"{timeframe}: سیگنال خرید تازه UT هم‌جهت با وضعیت فعلی ({src})")
            elif ut["fresh"] and ut["last_signal"] == "sell" and ut["trend"] < 0:
                score -= 2
                notes.append(f"{timeframe}: سیگنال فروش تازه UT هم‌جهت با وضعیت فعلی ({src})")
            elif ut["last_signal"] == "buy" and ut["trend"] > 0:
                score += 1
                notes.append(f"{timeframe}: حالت خرید UT فعال ({src})")
            elif ut["last_signal"] == "sell" and ut["trend"] < 0:
                score -= 1
                notes.append(f"{timeframe}: حالت فروش UT فعال ({src})")
            elif ut["last_signal"] is not None:
                notes.append(f"{timeframe}: تعارض سیگنال آخر با وضعیت UT؛ بدون امتیاز UT ({src})")
            elif ut["trend"] != 0:
                notes.append(f"{timeframe}: وضعیت UT بدون سیگنال ثبت‌شده؛ بدون امتیاز کراس ({src})")
            last = indicators.iloc[-1]
            close = number(last.get("Close"))
            ma50 = number(last.get("SMA 50"), float("nan"))
            ma200 = number(last.get("SMA 200"), float("nan"))
            if pd.notna(ma50) and pd.notna(ma200):
                if close > ma50 > ma200:
                    score += 1
                    notes.append(f"{timeframe}: روند MA صعودی")
                elif close < ma50 < ma200:
                    score -= 1
                    notes.append(f"{timeframe}: روند MA نزولی")
            if timeframe == "روزانه" and close > 0:
                atr14 = calculate_atr(candles, 14).iloc[-1]
                if pd.notna(atr14) and float(atr14) > 0:
                    atr_pct_latest = float(atr14) / close * 100.0
        except Exception as exc:
            notes.append(f"{timeframe}: خطا ({type(exc).__name__})")
    return {"score": score, "atr_pct": atr_pct_latest, "notes": notes}


def rank_crypto_candidates(coins_df, lbank_pairs, atr_period=1, ut_multiplier=2.0):
    excluded = {"XAUT", "USDT", "USDC", "FDUSD", "TUSD", "DAI", "USDE",
                "USDD", "USDP", "PYUSD", "EURC", "USD1", "USTC"}
    eligible = coins_df[
        coins_df["LBank listed"] & ~coins_df["Code"].astype(str).str.upper().isin(excluded)
    ].copy()
    if eligible.empty:
        return []
    eligible["Volume USD"] = pd.to_numeric(eligible["Volume USD"], errors="coerce").fillna(0)
    eligible["Code"] = eligible["Code"].astype(str).str.upper()
    core = ["BTC", "ETH", "SOL"]
    selected_codes = [x for x in core if x in set(eligible["Code"])]
    by_volume = eligible.sort_values("Volume USD", ascending=False)["Code"].tolist()
    for code in by_volume:
        if code not in selected_codes:
            selected_codes.append(code)
        if len(selected_codes) >= 50:
            break
    rows = []
    for code in selected_codes[:50]:
        coin_rows = eligible[eligible["Code"] == code]
        if coin_rows.empty:
            continue
        coin = coin_rows.iloc[0]
        day_change = number(coin.get("24h Change %"), float("nan"))
        trend = get_asset_trend_score(code, lbank_pairs, int(atr_period), float(ut_multiplier))
        relative = get_btc_relative_metrics(code, lbank_pairs)
        trend_score = trend.get("score")
        atr_pct = trend.get("atr_pct")
        relative_change = relative.get("change_pct")
        if trend_score is None and relative_change is None and not pd.notna(day_change):
            score = None
        else:
            score = int(trend_score or 0)
            score += tiered_score_atr(relative_change, atr_pct)
            score += tiered_score_atr(day_change if pd.notna(day_change) else None, atr_pct)
        if score is None:
            label, reason = "داده ناکافی", "داده کافی نیست"
        elif score >= 5:
            label, reason = "اولویت ۱: بررسی خرید", "نشانه‌های روند و قدرت نسبی قوی؛ ورود همچنان نیازمند تأیید است"
        elif score >= 2:
            label, reason = "اولویت ۲: انتظار برای تأیید", "نشانه‌های مثبت محدود؛ ورود قطعی نیست"
        elif score <= -3:
            label, reason = "فعلاً اولویت پایین", "روند یا قدرت نسبی ضعیف"
        else:
            label, reason = "خنثی: صبر", "مزیت روشن دیده نمی‌شود"
        rows.append({
            "Code": code, "Score": score, "Priority": label,
            "24h Change %": day_change if pd.notna(day_change) else None,
            "BTC Relative %": relative_change, "Volume USD": number(coin.get("Volume USD")),
            "ATR%": atr_pct, "Reason": reason,
            "Trend notes": trend.get("notes") or [],
            "Relative method": relative.get("method", ""),
        })
    rows.sort(key=lambda row: (
        row["Score"] is None,
        -(row["Score"] if row["Score"] is not None else -999),
        -number(row["24h Change %"], float("-inf")),
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
        message = payload.get("message") or payload.get("error_message") or payload.get("error") or "خطای نامشخص"
        raise RuntimeError(f"CryptoMeter: {str(message)[:180]}")
    data = payload.get("data")
    if data is None:
        raise RuntimeError("CryptoMeter در پاسخ، داده‌ای برنگرداند.")
    return data


# ===========================================================================
# EXCHANGE PRICE COMPARISON
# ===========================================================================
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
    imbalance = (bid_usd - ask_usd) / total_usd if total_usd > 0 else None

    band_width = max(mid * 0.001, 1e-12)

    def aggregate_zones(levels, side):
        buckets = {}
        for price, qty in levels:
            bucket = math.floor(price / band_width)
            zone = buckets.setdefault(bucket, {
                "low": bucket * band_width, "high": (bucket + 1) * band_width,
                "quantity": 0.0, "value": 0.0, "weighted_price": 0.0,
            })
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
            if len(side_values) < 5:
                threshold = median_v * 2.5
            else:
                sorted_v = sorted(side_values)
                p90 = sorted_v[min(len(sorted_v) - 1, int(len(sorted_v) * 0.90))]
                threshold = max(median_v * 2.5, p90)
        else:
            threshold = 0.0
        for zone in zones:
            zone["significant"] = zone["value"] >= threshold if threshold else False
            zone["classification"] = ("تراکم بالا در بازه ۰٫۱۰٪"
                                      if zone["significant"]
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
        price = number(t.get("price")); amount = number(t.get("amount"))
        cost = number(t.get("cost"))
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
    """Long-only setup candidate. A trade is actionable only at >=3R."""
    mid = number(book_stats.get("mid"))
    if mid <= 0:
        return {"status": "WAIT", "reason": "قیمت میانی دفتر سفارش معتبر نیست."}

    def nearest(walls, side):
        candidates = [w for w in walls if
                      (number(w.get("price")) < mid if side == "bid" else number(w.get("price")) > mid)]
        if not candidates:
            return None
        significant = [w for w in candidates if w.get("significant")]
        return min(significant or candidates, key=lambda w: abs(number(w.get("price")) - mid))

    support = nearest(book_stats.get("bid_walls", []), "bid")
    resistance = nearest(book_stats.get("ask_walls", []), "ask")
    if not support or not resistance:
        return {"status": "WAIT", "reason": "سطح حمایت یا مقاومت قابل اتکا در دفتر سفارش موجود نیست."}

    support_price = number(support.get("price"))
    resistance_price = number(resistance.get("price"))
    entry = max(number(book_stats.get("best_ask")), support_price * 1.001)
    stop = support_price * 0.995
    risk = entry - stop
    target = resistance_price * 0.999
    if entry <= 0 or stop <= 0 or risk <= 0 or target <= entry:
        return {"status": "WAIT", "reason": "ساختار فعلی قیمت، ورود لانگ با حدضرر و هدف معتبر نمی‌سازد.",
                "support": support, "resistance": resistance}

    reward = target - entry
    rr = reward / risk
    support_distance_pct = max(0.0, (mid - support_price) / mid * 100)
    imbalance = book_stats.get("imbalance")
    flow = number(trade_stats.get("net_flow"))
    spread = number(book_stats.get("spread_pct"), 999)
    confirmations = []
    warnings = []
    if imbalance is not None and imbalance > 0.05:
        confirmations.append("دفتر سفارش به نفع خریداران است")
    else:
        warnings.append("عدم‌تعادل دفتر سفارش خرید را تأیید نمی‌کند")
    if flow > 0:
        confirmations.append("جریان خالص معاملات اخیر مثبت است")
    else:
        warnings.append("جریان خالص معاملات اخیر مثبت نیست")
    if spread > 0.5:
        warnings.append("اسپرد بالاتر از ۰٫۵٪ است")
    if support_distance_pct > 3:
        warnings.append("حمایت بیش از ۳٪ پایین‌تر از قیمت فعلی است")
    if rr < 3:
        warnings.append(f"مقاومت فعلی فقط نسبت سود به ریسک {rr:.2f}R می‌دهد؛ شرط حداقل 3R تأمین نیست")

    data_ok = spread <= 0.5 and support_distance_pct <= 3 and rr >= 3
    flow_ok = (imbalance is not None and imbalance > 0.05) or flow > 0
    actionable = data_ok and flow_ok
    return {
        "status": "LONG SETUP" if actionable else "WAIT",
        "reason": "؛ ".join(confirmations + warnings) or "شرایط اولیه بررسی لانگ برقرار است.",
        "entry": entry, "target": target, "stop": stop, "rr": rr,
        "support": support, "resistance": resistance,
        "support_distance_pct": support_distance_pct,
        "risk_per_unit": risk,
        "reward_per_unit": reward,
        "actionable": actionable,
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
                "note": payload.get("note") or "لیکوییدیشن کافی موجود نیست.",
                "public": payload.get("public", True)}, None
    if len(matrix) == len(times) and matrix and len(matrix[0]) == len(prices):
        z = [list(row) for row in zip(*matrix)]
    elif len(matrix) == len(prices) and matrix and len(matrix[0]) == len(times):
        z = matrix
    else:
        return None, "ابعاد ماتریس سازگار نیست."
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
                             fontSize=12, leading=16, alignment=TA_RIGHT,
                             spaceBefore=4 * mm, spaceAfter=2 * mm)
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
        f"ارزهای دریافت‌شده: {report_data.get('fetched_count', '—')} | "
        f"پس از فیلتر: {report_data.get('coin_count', '—')}"
    )
    add_paragraph(f"وضعیت LBank: {report_data.get('lbank_status', '—')}")

    add_heading("۱. فهرست ارزهای LiveCoinWatch")
    add_table(report_data.get("coins_df"), col_widths=[1.0, 1.1, 1.25, 1.25, 1.15, 1.1, 0.9, 0.9])

    ratio = report_data.get("btc_xau_metrics") or {}
    if ratio.get("current") is not None:
        add_heading("نسبت BTC/XAUT و تخصیص")
        add_paragraph(
            f"نرخ: {number(ratio.get('current')):.4f} XAUT/BTC | "
            f"۱ روزه: {number(ratio.get('change_1d'), float('nan')):+.2f}% | "
            f"۷ روزه: {number(ratio.get('change_7d'), float('nan')):+.2f}%"
        )
        if report_data.get("allocation"):
            add_paragraph(
                f"تخصیص: {report_data.get('allocation')} | "
                f"XAUT: {report_data.get('gold_pct', '—')}% | رمزارز: {report_data.get('crypto_pct', '—')}%"
            )
            add_paragraph(str(report_data.get("allocation_reason") or ""))

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
        source = result.get("source", "?")
        add_heading(f"تایم‌فریم {tf_name}")
        add_paragraph(
            f"منبع: {source} | آخرین کندل: {summary.get('time', '—')} | "
            f"قیمت: {summary.get('close', '—')} | ATR: {summary.get('atr', '—')} | "
            f"حد UT: {summary.get('ut_stop', '—')}"
        )
        add_paragraph(
            f"وضعیت: {summary.get('status', '—')} | "
            f"ساختار: {summary.get('structure', '—')} | سناریو: {plan.get('status', '—')}"
        )
        if "stop" in plan:
            add_paragraph(
                f"مبنا: {plan.get('close', '—')} | SL: {plan.get('stop', '—')} | "
                f"TP1: {plan.get('target1', '—')} | TP2: {plan.get('target2', '—')}"
            )

    add_heading("۴. دفتر سفارش و جریان معاملات LBank")
    add_paragraph(
        f"بهترین خرید: {report_data.get('best_bid', '—')} | بهترین فروش: {report_data.get('best_ask', '—')} | "
        f"اسپرد: {report_data.get('spread_pct', '—')} | عدم‌تعادل: {report_data.get('imbalance', '—')}"
    )
    add_paragraph(
        f"حجم خرید: {report_data.get('buy_usd', '—')} | حجم فروش: {report_data.get('sell_usd', '—')} | "
        f"جریان خالص: {report_data.get('net_flow', '—')} | تعداد: {report_data.get('trade_count', '—')}"
    )
    add_heading("سطوح خرید")
    add_table(report_data.get("bid_df"), col_widths=[1.0, 1.0, 1.5, 1.2])
    add_heading("سطوح فروش")
    add_table(report_data.get("ask_df"), col_widths=[1.0, 1.0, 1.5, 1.2])

    add_heading("۵. سناریوی ورود و خروج")
    order_plan = report_data.get("order_plan") or {}
    add_paragraph(f"وضعیت: {order_plan.get('status', '—')} | توضیح: {order_plan.get('reason', '—')}")
    if "entry" in order_plan:
        add_paragraph(
            f"ورود: {order_plan.get('entry', '—')} | هدف: {order_plan.get('target', '—')} | "
            f"SL: {order_plan.get('stop', '—')} | R/R: {order_plan.get('rr', '—')}"
        )

    add_heading("محدودیت‌ها")
    add_paragraph(
        "این گزارش از داده‌های عمومی استفاده می‌کند و معامله‌ای ثبت نمی‌کند. "
        "سیگنال‌ها و سطوح تخمینی هستند و سود را تضمین نمی‌کنند."
    )

    doc.build(story, onFirstPage=draw_footer, onLaterPages=draw_footer)
    buffer.seek(0)
    return buffer.getvalue()


# ===========================================================================
# ============================ STREAMLIT UI =================================
# ===========================================================================
st.set_page_config(page_title="SpotPilot AI", page_icon="📊", layout="wide")

st.markdown(
    """
    <style>
    :root { --spotpilot-persian-font: Tahoma, Arial, sans-serif; }
    html, body, .stApp,
    [data-testid="stAppViewContainer"], [data-testid="stHeader"],
    [data-testid="stMain"], [data-testid="stMainBlockContainer"],
    [data-testid="stSidebar"], [data-testid="stSidebarContent"],
    [data-testid="stBottom"], [data-testid="stPopover"], [data-testid="stDialog"] {
        direction: rtl; text-align: right;
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
        direction: rtl; text-align: right; line-height: 1.65;
    }
    .block-container { max-width: 1500px; padding-top: 1.4rem; padding-bottom: 2.5rem; }
    [data-testid="stSidebar"] *:not(svg):not(path):not(.material-icons):not(.material-symbols-rounded) { text-align: right; }
    [data-testid="stHorizontalBlock"], [data-testid="stVerticalBlock"],
    [data-testid="stExpander"], [data-testid="stForm"],
    [data-testid="stAlert"], [data-testid="stNotificationContent"] { direction: rtl; }
    .stTabs [data-baseweb="tab-list"] { direction: rtl; }
    .stButton, .stDownloadButton,
    [data-testid="stButton"], [data-testid="stDownloadButton"] { direction: rtl; text-align: center; }
    [data-baseweb="select"] > div, [data-baseweb="input"] > div,
    [data-baseweb="textarea"], [role="listbox"], [role="option"] {
        direction: rtl; text-align: right;
        font-family: var(--spotpilot-persian-font) !important;
    }
    .stMarkdown ul, .stMarkdown ol,
    [data-testid="stMarkdownContainer"] ul, [data-testid="stMarkdownContainer"] ol {
        padding-right: 1.5rem; padding-left: 0;
    }
    [data-testid="stMetricLabel"] {
        direction: rtl; text-align: right; justify-content: flex-start;
        font-family: var(--spotpilot-persian-font) !important;
    }
    [data-testid="stMetricValue"], [data-testid="stMetricDelta"] {
        direction: ltr; text-align: right;
        unicode-bidi: plaintext; font-variant-numeric: tabular-nums;
    }
    .stNumberInput input, .stDateInput input, .stTimeInput input {
        direction: ltr; text-align: left; font-variant-numeric: tabular-nums;
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
        "۳. **نمودار را بخوان**؛ داده از بهترین صرافی موجود (Binance/Bybit/OKX/KuCoin/LBank) گرفته می‌شود.\n"
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
            "دوره ATR برای UT Bot", min_value=1, max_value=100, value=1, step=1,
            help="پیش‌فرض ۱ برای تطبیق با تنظیم TradingView شماست؛ رتبه‌بندی نوسان از ATR روزانه ۱۴ دوره‌ای مستقل استفاده می‌کند.",
        )
        ut_multiplier = st.number_input(
            "ضریب ATR (Key Value)", min_value=0.1, max_value=20.0, value=2.0, step=0.1,
        )

    refresh = st.button("🔄 به‌روزرسانی داده‌ها", use_container_width=True)

if refresh:
    fetch_lcw_market.clear()
    fetch_lbank_pairs.clear()
    fetch_lbank_book.clear()
    fetch_lbank_trades.clear()
    fetch_lbank_ticker.clear()
    fetch_lbank_klines.clear()
    fetch_klines_multi_source.clear()
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
    lbank_status = (f"{sum(1 for p in lbank_pairs if p.endswith('_usdt'))} USDT "
                    f"و {sum(1 for p in lbank_pairs if p.endswith('_btc'))} BTC")
except Exception as exc:
    lbank_pairs = set()
    lbank_status = f"دریافت جفت‌ارزها ناموفق بود: {exc}"

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
st.caption("چهار جفت‌ارز ثابت همیشه در ابتدای فهرست هستند.")

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
st.caption(
    "امتیاز ترکیبی از روند UT (state-aware) + کراس‌اُور تازه + ساختار MA در دو تایم‌فریم، "
    "به‌همراه تغییر پله‌ای نسبت به BTC و تغییر ۲۴ساعته."
)
crypto_ranking = []
try:
    with st.spinner("مقایسه روند و قدرت نسبی رمزارزهای برتر فهرست..."):
        crypto_ranking = rank_crypto_candidates(
            coins_df, lbank_pairs, int(atr_period), float(ut_multiplier))
    if crypto_ranking:
        ranking_df = pd.DataFrame(crypto_ranking)
        best = ranking_df.iloc[0]
        if best["Score"] is not None and best["Score"] >= 5:
            st.success(f"**انتخاب اول فعلی برای بررسی: {best['Code']}** — {best['Reason']}.")
        else:
            st.info("**فعلاً خرید فوری تأیید نشده است.** رتبه اول فقط بهترین گزینه نسبی است.")
        display_ranking = ranking_df[[
            "Code", "Priority", "Score", "24h Change %", "BTC Relative %", "Reason"
        ]].copy()
        display_ranking = display_ranking.rename(columns={
            "Code": "رمزارز", "Priority": "اولویت", "Score": "امتیاز مدل",
            "24h Change %": "تغییر ۲۴ساعته (%)", "BTC Relative %": "نسبت به BTC (%)",
            "Reason": "دلیل ساده",
        })
        for col in ("تغییر ۲۴ساعته (%)", "نسبت به BTC (%)"):
            display_ranking[col] = display_ranking[col].map(
                lambda v: f"{v:+.2f}%" if v is not None and pd.notna(v) else "—")
        display_ranking["امتیاز مدل"] = display_ranking["امتیاز مدل"].map(
            lambda v: str(v) if v is not None and pd.notna(v) else "—")
        st.dataframe(display_ranking, use_container_width=True, hide_index=True)
        with st.expander("چرا این رتبه‌بندی؟"):
            for item in crypto_ranking:
                st.markdown(f"**{item['Code']} — {item['Priority']} (امتیاز {item['Score']})**")
                st.caption("؛ ".join(item["Trend notes"][:6]) if item["Trend notes"] else item["Reason"])
                st.caption(item["Relative method"])
    else:
        st.warning("رمزارز قابل بررسی با جفت USDT پیدا نشد.")
except Exception as exc:
    st.warning(f"رتبه‌بندی ناموفق: {type(exc).__name__}: {str(exc)[:180]}")

# ---------------------------------------------------------------------------
st.divider()
st.subheader("۲٫۵. هشدار زودهنگام ورود پول و فعالیت غیرعادی")
st.caption("هدف این بخش پیدا کردن حرکت‌های اولیه است، نه معرفی ارزی که قبلاً جهش بزرگی کرده. فیلتر بازار عمومی به‌تنهایی اثبات فعالیت نهنگ نیست؛ دادهٔ مستقیم نهنگ/آن‌چین فقط در صورت ارائه توسط منبع داده قابل تأیید است.")

early_rows = []
for coin in coins:
    code = str(coin.get("Code", "")).upper()
    change = number(coin.get("24h Change %"), None)
    volume_btc = number(coin.get("Volume BTC equivalent"))
    if not code or change is None or volume_btc < max(100.0, float(min_btc_volume)):
        continue
    # Early watch: activity/volume is meaningful, but avoid coins already extended.
    if 0.25 <= change <= 8.0:
        early_rows.append({
            "رمزارز": code,
            "تغییر ۲۴ساعته": change,
            "حجم ۲۴ساعته (BTC)": volume_btc,
            "قیمت (USD)": number(coin.get("Price USD")),
            "منبع": "LiveCoinWatch",
            "وضعیت": "پایش زودهنگام؛ تأیید جریان پول لازم است",
        })
early_rows.sort(key=lambda row: (row["حجم ۲۴ساعته (BTC)"], row["تغییر ۲۴ساعته"]), reverse=True)
if early_rows:
    early_df = pd.DataFrame(early_rows[:30])
    early_df["تغییر ۲۴ساعته"] = early_df["تغییر ۲۴ساعته"].map(lambda x: f"{x:+.2f}%")
    early_df["حجم ۲۴ساعته (BTC)"] = early_df["حجم ۲۴ساعته (BTC)"].map(lambda x: f"{x:,.0f}")
    early_df["قیمت (USD)"] = early_df["قیمت (USD)"].map(fmt_price)
    st.dataframe(early_df, use_container_width=True, hide_index=True)
else:
    st.info("در داده فعلی، ارزی با حجم کافی و رشد محدود ۰٫۲۵٪ تا ۸٪ برای پایش اولیه پیدا نشد. این به معنی نبود جریان پول نیست؛ منبع عمومی ممکن است داده کافی نداشته باشد.")

cryptometer_key_early = get_cryptometer_key()
if cryptometer_key_early:
    with st.expander("سیگنال‌های حرکت سریع CryptoMeter (در صورت پشتیبانی پلن API)", expanded=False):
        try:
            rapid_data = fetch_cryptometer_endpoint("rapid-movements", cryptometer_key_early, {})
            rapid_rows = rapid_data if isinstance(rapid_data, list) else (
                [rapid_data] if isinstance(rapid_data, dict) else [])
            rapid_rows = [row for row in rapid_rows if isinstance(row, dict)]
            if rapid_rows:
                rapid_df = pd.json_normalize(rapid_rows).head(50)
                st.dataframe(rapid_df, use_container_width=True, hide_index=True)
                st.caption("فیلدها مطابق پاسخ خام CryptoMeter نمایش داده می‌شوند؛ نام‌گذاری فیلدها به‌تنهایی به‌معنی تأیید نهنگ یا ورود قطعی سرمایه نیست.")
            else:
                st.info("CryptoMeter در این پاسخ رکورد قابل نمایش برنگرداند.")
        except Exception as exc:
            st.warning(f"داده حرکت سریع CryptoMeter دریافت نشد: {type(exc).__name__}: {str(exc)[:160]}")
else:
    st.info("برای دریافت سیگنال‌های اضافه CryptoMeter، کلید CRYPTOMETER_API_KEY را در Streamlit Secrets تنظیم کن. هشدار فعلی فقط از حجم و تغییر قیمت LiveCoinWatch ساخته می‌شود و ادعای ردیابی مستقیم نهنگ‌ها ندارد.")

st.divider()
st.subheader(f"۳. مقایسه قیمت {selected_code}")

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
    st.warning("قیمت قابل استفاده‌ای دریافت نشد.")

if compare_errors:
    with st.expander("جزئیات خطاهای مقایسه قیمت"):
        st.code("\n".join(compare_errors))

# ---------------------------------------------------------------------------
st.divider()
st.subheader("۴. ارزش نسبت به بیت‌کوین (BTC Pair)")
try:
    with st.spinner("بررسی نسبت واقعی ارز به BTC..."):
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
            st.success(f"{selected_code} نسبت به BTC عملکرد بهتری داشته است.")
        elif btc_relative["change_pct"] < 0:
            st.warning(f"{selected_code} نسبت به BTC ضعیف‌تر شده است.")
        else:
            st.info("عملکرد نسبت به BTC تقریباً بدون تغییر بوده است.")
except Exception as exc:
    st.warning(f"تحلیل نسبت به BTC ناموفق: {type(exc).__name__}: {str(exc)[:180]}")

if selected_code in ("BTC", "XAUT"):
    st.markdown("#### نسبت بیت‌کوین به طلا (BTC/XAUT)")
    try:
        btc_xau = get_btc_xau_metrics(lbank_pairs)
        if btc_xau["current"] is None:
            st.info(btc_xau["method"])
        else:
            q1, q2, q3 = st.columns(3)
            q1.metric("BTC/XAUT مصنوعی", f"{btc_xau['current']:.4f} XAUT/BTC")
            q2.metric("تغییر یک‌روزه", f"{btc_xau['change_1d']:+.2f}%")
            q3.metric("تغییر هفت‌روزه", f"{btc_xau['change_7d']:+.2f}%")
            st.caption(btc_xau["method"])
    except Exception as exc:
        st.warning(f"محاسبه BTC/XAUT ناموفق: {type(exc).__name__}: {str(exc)[:160]}")

# ---------------------------------------------------------------------------
st.divider()
st.subheader("۵. تحلیل تکنیکال")
st.caption(
    "داده از بهترین منبع موجود انتخاب می‌شود: Binance → Bybit → OKX → KuCoin → LBank. "
    "سیگنال UT به‌صورت state-aware نمایش داده می‌شود: اگر آخرین سیگنال چند کندل قبل باشد، "
    "همان سیگنال معتبر است تا زمانی که سیگنال مخالف بیاید."
)

timeframe_results = {}
selected_tf_sources = {}

if not selected_lbank_pair and selected_code not in ("BTC", "ETH", "SOL"):
    st.warning(f"{selected_code} در جفت‌ارزهای USDT در LBank پیدا نشد و ممکن است در صرافی‌های دیگر هم نباشد.")

tf_tabs = st.tabs(["۱۵ دقیقه", "۴ ساعت", "روزانه"])
tf_names = ["15 دقیقه", "4 ساعت", "روزانه"]

for tab, tf_name in zip(tf_tabs, tf_names):
    with tab:
        try:
            with st.spinner(f"دریافت کندل‌های {tf_name}..."):
                candles, source_name, source_errors = fetch_klines_multi_source(
                    selected_code, tf_name, 300, quote="USDT")
                if candles is None or candles.empty:
                    st.error(f"داده‌ای برای {selected_code} در {tf_name} یافت نشد.")
                    with st.expander("جزئیات خطاها"):
                        st.code("\n".join(source_errors[-10:]) if source_errors else "بدون جزئیات")
                    continue

                indicators = add_market_indicators(candles, int(atr_period), float(ut_multiplier))
                summary = latest_signal_summary(indicators)
                plan = make_atr_trade_plan(indicators, float(ut_multiplier))

            selected_tf_sources[tf_name] = source_name
            timeframe_results[tf_name] = {
                "candles": candles, "indicators": indicators,
                "summary": summary, "plan": plan, "source": source_name,
            }

            st.caption(f"منبع داده این تایم‌فریم: **{source_name}**")

            # --- State-aware UT signal display ---
            ut_sig = summary.get("ut_last_signal")
            ut_fresh = summary.get("ut_fresh", False)
            ut_ago = summary.get("ut_candles_ago")
            ut_trend = summary.get("ut_trend", 0)

            if ut_sig == "buy" and ut_fresh:
                st.success("🟢 سیگنال خرید UT روی آخرین کندل بسته‌شده (تازه).")
            elif ut_sig == "sell" and ut_fresh:
                st.error("🔴 سیگنال فروش UT روی آخرین کندل بسته‌شده (تازه).")
            elif ut_sig == "buy" and ut_trend > 0:
                st.success(f"🟢 حالت صعودی UT — آخرین سیگنال خرید {ut_ago} کندل قبل ثبت شده و قیمت هنوز بالای حد UT است.")
            elif ut_sig == "sell" and ut_trend < 0:
                st.error(f"🔴 حالت نزولی UT — آخرین سیگنال فروش {ut_ago} کندل قبل ثبت شده و قیمت هنوز زیر حد UT است.")
            elif ut_sig == "buy" and ut_trend < 0:
                st.warning(f"⚠️ آخرین سیگنال خرید {ut_ago} کندل قبل بود، اما قیمت الان زیر حد UT است — احتمال تغییر روند.")
            elif ut_sig == "sell" and ut_trend > 0:
                st.warning(f"⚠️ آخرین سیگنال فروش {ut_ago} کندل قبل بود، اما قیمت الان بالای حد UT است — احتمال تغییر روند.")
            elif ut_trend > 0:
                st.info("قیمت بالای حد UT است؛ هنوز سیگنال خرید UT ثبت نشده.")
            elif ut_trend < 0:
                st.info("قیمت زیر حد UT است؛ هنوز سیگنال فروش UT ثبت نشده.")
            else:
                st.info("وضعیت UT نامشخص است.")

            st.caption(f"زمان شروع آخرین کندل بسته‌شده (UTC): "
                       f"{summary['time'].strftime('%Y-%m-%d %H:%M')}")

            # Freshness — try LBank ticker (order book source) as live price reference
            live_price = None
            if selected_lbank_pair:
                try:
                    live_price = fetch_lbank_ticker(selected_lbank_pair)
                except Exception:
                    pass

            fresh = freshness_report(summary, tf_name, source_name, live_price)
            price_gap_pct = None
            if live_price and summary.get("close", 0) > 0:
                price_gap_pct = (float(live_price) / float(summary["close"]) - 1.0) * 100.0
            if source_name and str(source_name).upper() != "LBANK" and price_gap_pct is not None and abs(price_gap_pct) >= 1.0:
                mismatch_reason = (
                    f"اختلاف قیمت منبع نمودار ({source_name}) با قیمت زنده LBank برابر "
                    f"{price_gap_pct:+.2f}% است؛ سناریوی معاملاتی تا بررسی دستی غیرفعال شد."
                )
                if isinstance(plan, dict):
                    plan.update({"status": "WAIT", "reason": mismatch_reason})
                st.error("⚠️ " + mismatch_reason)
            else:
                st.caption(
                    f"مقایسه قیمت: منبع نمودار {source_name or 'نامشخص'} | "
                    + (f"اختلاف با LBank: {price_gap_pct:+.2f}%" if price_gap_pct is not None else "قیمت زنده LBank در دسترس نیست")
                )
            if fresh["stale"]:
                st.warning(f"⚠️ داده این تایم‌فریم قدیمی است: {fresh['message']}")
                if fresh.get("ticker_note"):
                    st.info(fresh["ticker_note"])
                st.caption(
                    "توضیح: اگر منبع Binance/Bybit باشد و باز هم قدیمی است، یعنی نماد در آن منبع غیرفعال یا کم‌معامله است. "
                    "اگر LBank باشد، احتمالاً جفت‌ارز کم‌معامله است. "
                    "دکمهٔ «به‌روزرسانی داده‌ها» کش برنامه را پاک می‌کند، اما دادهٔ زیربنایی در صرافی همان است."
                )
            else:
                st.caption(f"✓ تازگی داده مناسب | پایان آخرین کندل: "
                           f"{fresh['last_candle_close'].strftime('%Y-%m-%d %H:%M UTC')}")

            a, b, c, d = st.columns(4)
            a.metric("آخرین قیمت بسته‌شده", fmt_price(summary["close"]))
            b.metric("ATR", fmt_price(summary["atr"]))
            c.metric("حد متحرک UT", fmt_price(summary["ut_stop"]))
            d.metric("وضعیت", plan["status"])

            last = indicators.iloc[-1]
            st.markdown("#### میانگین‌های متحرک")
            ma_cols = st.columns(3)
            ma_values = {}
            ma_explanations = {
                14: "کوتاه‌مدت؛ سریع‌تر واکنش نشان می‌دهد.",
                50: "میان‌مدت؛ جهت کلی‌تر.",
                200: "بلندمدت؛ تاریخچه کافی لازم است.",
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
                    st.success("قیمت بالای SMA 50 و 200؛ روند متمایل به صعودی.")
                elif summary["close"] < valid_ma[50] < valid_ma[200]:
                    st.warning("قیمت زیر SMA 50 و 200؛ روند متمایل به نزولی.")
                else:
                    st.info("میانگین‌ها هم‌جهت نیستند.")
            else:
                st.info("داده کافی برای SMA 50 و 200 موجود نیست.")

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

            st.markdown("#### نمودار قیمت و میانگین‌ها")
            elliott_enabled = st.checkbox("نمایش شمارش احتمالی امواج الیوت",
                                          value=False, key=f"elliott_overlay_{tf_name}")
            st.caption("⚠️ پیوت‌ها با تأخیر تأیید می‌شوند. پیش‌بینی جهت‌دار فقط در صورت وجود ۶ پیوت، اعتبارسنجی اولیه ایمپالس و هم‌جهتی با وضعیت UT نمایش داده می‌شود.")

            chart_df = indicators.tail(150).copy()
            fig = go.Figure()
            fig.add_trace(go.Candlestick(
                x=chart_df["Time"], open=chart_df["Open"], high=chart_df["High"],
                low=chart_df["Low"], close=chart_df["Close"], name="قیمت",
                increasing_line_color="#16a34a", decreasing_line_color="#dc2626",
            ))
            ma_styles = {
                14: ("#2563eb", "SMA 14"),
                50: ("#f59e0b", "SMA 50"),
                200: ("#9333ea", "SMA 200"),
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
                ut_now = ut_status(chart_df)
                forecast_direction = -context.get("direction", 0)
                direction_matches_ut = ((forecast_direction > 0 and ut_now.get("trend", 0) > 0)
                                        or (forecast_direction < 0 and ut_now.get("trend", 0) < 0))
                if not wave_points.empty and context.get("phase") in ("abc_correction_scenario", "post_impulse_unconfirmed"):
                    fig.add_trace(go.Scatter(
                        x=wave_points["Time"], y=wave_points["Price"], mode="lines+markers+text",
                        text=wave_points["Label"], textposition="top center", name="آخرین ایمپالس معتبر ۰–۵",
                        line={"color": "#7c3aed", "width": 2.5}, marker={"size": 8, "color": "#7c3aed"},
                        textfont={"color": "#7c3aed", "size": 12}, connectgaps=False,
                    ))
                    if context.get("phase") == "abc_correction_scenario" and context.get("forecast") and direction_matches_ut:
                        t0 = pd.to_datetime(chart_df["Time"].iloc[-1], utc=True, errors="coerce")
                        current_close = float(chart_df["Close"].iloc[-1])
                        step = int(TIMEFRAMES.get(tf_name, {}).get("seconds", 14400))
                        future_times = [t0 + pd.Timedelta(seconds=step*k) for k in (1,2,3)]
                        fig.add_trace(go.Scatter(
                            x=[t0]+future_times, y=[current_close]+context["forecast"],
                            mode="lines+markers+text", text=["پایان موج ۵"]+context["forecast_labels"],
                            textposition="top center", name="سناریوی اصلاح ABC",
                            line={"color": "#dc2626", "width": 2.5, "dash": "dash"},
                            marker={"size": 8, "color": "#dc2626"}, connectgaps=False,
                        ))
                    elif context.get("phase") == "post_impulse_unconfirmed":
                        st.warning("ایمپالس قبلی پیدا شد، اما قیمت از انتهای موج ۵ فاصله گرفته؛ پیش‌بینی جدید پنهان است.")
                    elif not direction_matches_ut:
                        st.warning("شمارش الیوت با وضعیت UT هم‌جهت نیست؛ پیش‌بینی جهت‌دار مخفی است.")
                    st.caption(f"آخرین ایمپالس معتبر | جهت: {'صعودی' if context['direction'] > 0 else 'نزولی'} | کیفیت فیبوناچی: {context['confidence']}")
                    if context.get("notes"): st.write("**منطق سناریو:** " + "؛ ".join(context["notes"]))
                else:
                    st.info("ایمپالس پنج‌موجی قابل‌قبولی تأیید نشد؛ عمداً سقف‌ها و کف‌ها را به‌صورت زیگزاگ برچسب نمی‌زنیم.")

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

# Cross-timeframe summary
if timeframe_results:
    st.markdown("#### خلاصه هم‌جهتی تایم‌فریم‌ها")
    st.caption(
        "وضعیت UT بر پایهٔ state اندیکاتور است، نه فقط رویداد آخرین کندل. "
        "یعنی اگر ۱۰ کندل قبل سیگنال فروش ثبت شده و هنوز خرید نیامده، وضعیت «نزولی» باقی می‌ماند."
    )
    overview = []
    up_count = 0
    down_count = 0
    for tf_name in tf_names:
        result = timeframe_results.get(tf_name)
        if not result:
            continue
        summary = result["summary"]
        plan = result["plan"]
        source = result.get("source", "—")

        ut_sig = summary.get("ut_last_signal")
        ut_fresh = summary.get("ut_fresh", False)
        ut_ago = summary.get("ut_candles_ago")
        ut_trend = summary.get("ut_trend", 0)

        if ut_sig == "buy" and ut_fresh:
            state_label = "🟢 خرید تازه"; up_count += 1
        elif ut_sig == "sell" and ut_fresh:
            state_label = "🔴 فروش تازه"; down_count += 1
        elif ut_sig == "buy" and ut_trend > 0:
            state_label = f"🟢 صعودی ({ut_ago} کندل قبل)"; up_count += 1
        elif ut_sig == "sell" and ut_trend < 0:
            state_label = f"🔴 نزولی ({ut_ago} کندل قبل)"; down_count += 1
        elif ut_trend > 0:
            state_label = "🟢 بالای UT"; up_count += 1
        elif ut_trend < 0:
            state_label = "🔴 زیر UT"; down_count += 1
        else:
            state_label = "⚪ بی‌طرف"

        overview.append({
            "تایم‌فریم": tf_name,
            "منبع": source,
            "قیمت بسته‌شده": fmt_price(summary["close"]),
            "وضعیت UT": state_label,
            "ساختار": summary["structure"],
            "سناریو": plan["status"],
        })
    st.dataframe(pd.DataFrame(overview), use_container_width=True, hide_index=True)

    if up_count >= 2 and up_count > down_count:
        st.success(f"هم‌جهتی صعودی: {up_count} تایم‌فریم بالای حد UT.")
    elif down_count >= 2 and down_count > up_count:
        st.warning(f"هم‌جهتی نزولی: {down_count} تایم‌فریم زیر حد UT.")
    else:
        st.info(f"هم‌جهتی روشن نیست: {up_count} صعودی، {down_count} نزولی.")

# ---------------------------------------------------------------------------
st.divider()
st.subheader("۶. نقدینگی و معاملات اخیر LBank")

with st.expander("جریان نقدینگی — CryptoMeter.io", expanded=False):
    cryptometer_key = get_cryptometer_key()
    if not cryptometer_key:
        st.info("برای فعال‌کردن، CRYPTOMETER_API_KEY را در Streamlit Secrets تنظیم کن.")
    else:
        try:
            with st.spinner("دریافت داده‌های رایگان CryptoMeter..."):
                trend_data = fetch_cryptometer_endpoint("trend-indicator-v3", cryptometer_key, {})
            trend_rows = trend_data if isinstance(trend_data, list) else [trend_data]
            trend_rows = [r for r in trend_rows if isinstance(r, dict)]
            trend = trend_rows[-1] if trend_rows else {}
            if trend:
                buy_pressure = number(trend.get("buy_pressure"), None)
                sell_pressure = number(trend.get("sell_pressure"), None)
                trend_score = number(trend.get("trend_score"), None)
                tc1, tc2, tc3 = st.columns(3)
                tc1.metric("امتیاز روند", f"{trend_score:.2f}" if trend_score is not None else "—")
                tc2.metric("فشار خرید", f"{buy_pressure:.2f}%" if buy_pressure is not None else "—")
                tc3.metric("فشار فروش", f"{sell_pressure:.2f}%" if sell_pressure is not None else "—")
        except Exception as exc:
            st.warning(f"CryptoMeter ناموفق: {type(exc).__name__}: {str(exc)[:180]}")

with st.expander("نقشه رایگان لیکوییدیشن BTC — راهنمای خواندن", expanded=False):
    st.markdown("**محورها:** افقی = زمان ۴ ساعت اخیر؛ عمودی = سطح قیمت BTC. رنگ روشن‌تر یعنی شدت نسبی بیشتری در داده‌ای که منبع ارائه کرده، نه لزوماً دلار دقیق.")
    st.markdown("**راهنمای عملی:** خط افقی سفید قیمت آخرین کندل بسته‌شده است. نواحی روشن بالای خط = تمرکز احتمالی لیکوییدیشن شورت؛ نواحی روشن پایین خط = تمرکز احتمالی لیکوییدیشن لانگ. نزدیک‌شدن قیمت به یک ناحیه به‌تنهایی تضمین نمی‌کند که قیمت به آن برسد.")
    st.markdown("**محدودیت مهم:** تا وقتی قرارداد API مشخص نکند رنگ‌ها مقدار واقعی دلاری‌اند یا امتیاز نرمال‌شده، از شدت رنگ فقط برای مقایسه نسبی استفاده کن. این نقشه پیش‌بینی قطعی قیمت نیست.")
    try:
        heatmap, heatmap_error = fetch_free_liquidation_heatmap("BTC", 240, 50)
        if heatmap and heatmap.get("z"):
            totals = heatmap.get("totals") or {}
            sides = heatmap.get("by_side") or {}
            m1, m2, m3 = st.columns(3)
            m1.metric("مجموع لیکوییدیشن", fmt_usd(number(totals.get("total_notional"))))
            m2.metric("لانگ", fmt_usd(number(sides.get("long", totals.get("long_liq_notional")))))
            m3.metric("شورت", fmt_usd(number(sides.get("short", totals.get("short_liq_notional")))))
            # Normalize outlier-heavy provider values for legibility while preserving relative intensity.
            heat_values = np.asarray(heatmap["z"], dtype=float)
            heat_values = np.nan_to_num(heat_values, nan=0.0, posinf=0.0, neginf=0.0)
            positive = heat_values[heat_values > 0]
            if positive.size:
                upper = float(np.percentile(positive, 98))
                if upper > 0:
                    heat_values = np.clip(heat_values, 0, upper) / upper * 100.0
            fig_liq = go.Figure(data=go.Heatmap(
                x=heatmap["x"], y=heatmap["y"], z=heat_values.tolist(),
                colorscale=[[0.0, "#0b1220"], [0.12, "#172554"], [0.3, "#1d4ed8"],
                            [0.52, "#06b6d4"], [0.72, "#facc15"], [0.88, "#f97316"], [1.0, "#f8fafc"]],
                zmin=0, zmax=100, connectgaps=False,
                colorbar={"title": "شدت نسبی", "ticksuffix": "%"},
                hovertemplate="زمان: %{x}<br>سطح قیمت: %{y}<br>شدت نسبی (نرمال‌شده): %{z:.1f}%<extra></extra>",
            ))
            try:
                btc_frame, btc_source, _ = fetch_klines_multi_source("BTC", "15 دقیقه", 60)
                if btc_frame is not None and not btc_frame.empty:
                    current_btc = float(btc_frame["Close"].iloc[-1])
                    fig_liq.add_hline(y=current_btc, line_dash="dash", line_color="white",
                                      annotation_text=f"آخرین بسته‌شدن BTC: {current_btc:,.2f}")
                    st.caption(f"خط افقی = آخرین کندل بسته‌شده از {btc_source}؛ قیمت زنده نیست.")
            except Exception:
                st.caption("قیمت مرجع BTC در دسترس نیست؛ نقشه بدون خط قیمت مرجع نمایش داده می‌شود.")
            fig_liq.update_layout(height=650, title="BTC — تمرکز نسبی لیکوییدیشن، ۴ ساعت اخیر",
                                  xaxis_title="زمان (UTC)", yaxis_title="قیمت BTC در منبع نقشه",
                                  template="plotly_dark", hovermode="closest",
                                  margin={"l": 15, "r": 15, "t": 55, "b": 35},
                                  xaxis={"showgrid": True, "gridcolor": "#263244", "tickformat": "%H:%M"},
                                  yaxis={"side": "right", "showgrid": True, "gridcolor": "#263244", "tickformat": ",.2f"})
            st.plotly_chart(fig_liq, use_container_width=True)
        else:
            st.info(f"نقشه فعلاً در دسترس نیست: {heatmap_error}")
    except Exception as exc:
        st.warning(f"نقشه ناموفق: {type(exc).__name__}: {str(exc)[:180]}")

# ---------------------------------------------------------------------------
if not selected_lbank_pair:
    st.warning(f"تحلیل دفتر سفارش LBank برای {selected_code} در دسترس نیست "
               "(داده کندل از صرافی‌های دیگر گرفته شده ولی دفتر سفارش فقط از LBank می‌آید).")
    st.stop()

st.write(f"جفت‌ارز LBank: **{selected_lbank_pair}**")

try:
    with st.spinner("دریافت دفتر سفارش..."):
        book = fetch_lbank_book(selected_lbank_pair)
        book_stats = analyze_book(book)
except Exception as exc:
    st.error(f"دفتر سفارش ناموفق: {exc}")
    st.stop()

try:
    with st.spinner("دریافت معاملات اخیر..."):
        trades = fetch_lbank_trades(selected_lbank_pair)
        trade_stats = analyze_trades(trades)
except Exception as exc:
    trade_stats = analyze_trades([])
    st.warning(f"معاملات اخیر قابل دریافت نبودند: {type(exc).__name__}")

book_age_seconds = max(0.0, time.time() - float(book.get("fetched_at", time.time())))
exchange_book_ts = book.get("timestamp")
if exchange_book_ts:
    exchange_age_seconds = max(0.0, time.time() - float(exchange_book_ts))
    st.caption(f"دفتر سفارش دریافت‌شده: {book_age_seconds:.0f} ثانیه قبل | timestamp صرافی: {exchange_age_seconds:.0f} ثانیه قبل")
else:
    st.caption(f"دفتر سفارش دریافت‌شده: {book_age_seconds:.0f} ثانیه قبل | timestamp صرافی در دسترس نیست")
if book_age_seconds > 300:
    st.error("دفتر سفارش بیش از ۵ دقیقه قدیمی است؛ تحلیل ورود را معتبر فرض نکنید.")
elif book_age_seconds > 120:
    st.warning("دفتر سفارش بیش از ۲ دقیقه قدیمی است؛ قبل از تصمیم، داده را تازه کنید.")

metrics = st.columns(4)
metrics[0].metric("بهترین خرید", fmt_price(book_stats["best_bid"]))
metrics[1].metric("بهترین فروش", fmt_price(book_stats["best_ask"]))
metrics[2].metric("فاصله خرید و فروش", format_percent(book_stats["spread_pct"]))
metrics[3].metric("عدم‌تعادل دفتر سفارش",
                  f"{book_stats['imbalance'] * 100:+.2f}%" if book_stats.get("imbalance") is not None else "ناموجود")

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
    liq_a.metric("قوی‌ترین ناحیه خرید", fmt_price(strongest_bid["price"]),
                 delta=f"{format_wall_distance(bid_distance)} پایین‌تر")
    liq_b.metric("قوی‌ترین ناحیه فروش", fmt_price(strongest_ask["price"]),
                 delta=f"{format_wall_distance(ask_distance)} بالاتر")
    st.write(f"ارزش: **خرید {fmt_usd(number(strongest_bid.get('value')))}** در برابر "
             f"**فروش {fmt_usd(number(strongest_ask.get('value')))}**.")
else:
    st.info("ناحیه تجمیعی معناداری پیدا نشد.")

flows = st.columns(4)
flows[0].metric("حجم خرید اخیر", fmt_usd(trade_stats["buy_usd"]))
flows[1].metric("حجم فروش اخیر", fmt_usd(trade_stats["sell_usd"]))
flows[2].metric("جریان خالص", fmt_usd(trade_stats["net_flow"]))
flows[3].metric("تعداد معاملات", trade_stats["total_count"])

bid_df = pd.DataFrame()
ask_df = pd.DataFrame()
with st.expander("جزئیات دفتر سفارش"):
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
st.subheader("۷. سناریوی احتمالی ورود و خروج — لانگ با کنترل ریسک")
st.caption("این بخش دیگر به تازه‌بودن سیگنال UT وابسته نیست؛ از قیمت، حمایت/مقاومت دفتر سفارش و جریان معاملات استفاده می‌کند. فقط وقتی نسبت سود به ریسک حداقل 3R باشد، وضعیت به‌عنوان ستاپ لانگ معتبر نشان داده می‌شود.")

plan = calculate_trade_plan(book_stats, trade_stats)
capital = st.number_input("سرمایه کل حساب (USDT)", min_value=10.0, value=1000.0, step=50.0, key="long_capital")
max_risk_pct = st.number_input("حداکثر ریسک سرمایه در این معامله (%)", min_value=0.1, max_value=3.0, value=3.0, step=0.1, key="long_risk_pct")
st.caption("سقف ۳٪ یعنی حداکثر زیان دلاری در صورت اجرای حدضرر؛ به معنی قرار دادن stop دقیقاً ۳٪ زیر قیمت نیست.")

if plan["status"] == "LONG SETUP":
    st.success("🟢 ستاپ لانگ اولیه پیدا شد؛ پیش از سفارش، تازگی قیمت و لغزش اجرا را بررسی کن.")
else:
    st.warning("🟠 فعلاً ستاپ لانگ با تمام شروط ریسک تأیید نشده؛ دلیل دقیق پایین نمایش داده می‌شود.")

st.write(plan["reason"])
if "entry" in plan:
    levels = st.columns(4)
    levels[0].metric("ورود پیشنهادی", fmt_price(plan["entry"]))
    levels[1].metric("هدف خروج", fmt_price(plan["target"]))
    levels[2].metric("حد ضرر", fmt_price(plan["stop"]))
    levels[3].metric("ریوارد به ریسک", f"{plan['rr']:.2f}R")
    risk_per_unit = max(0.0, number(plan["entry"]) - number(plan["stop"]))
    risk_budget = capital * max_risk_pct / 100.0
    position_units = min(risk_budget / risk_per_unit, capital / number(plan["entry"])) if risk_per_unit > 0 and number(plan["entry"]) > 0 else 0.0
    notional = position_units * number(plan["entry"])
    actual_risk = position_units * risk_per_unit
    m = st.columns(3)
    m[0].metric("زیان برآوردی در حدضرر", fmt_usd(actual_risk), help=f"سقف مجاز: {fmt_usd(risk_budget)}")
    m[1].metric("حجم تقریبی پوزیشن", f"{position_units:,.6f}")
    m[2].metric("ارزش تقریبی پوزیشن", fmt_usd(notional))
    st.caption("حجم با فاصله ورود تا حدضرر محاسبه و برای معامله اسپات به سرمایه قابل استفاده محدود شده است. کارمزد، اسلیپیج و گپ قیمت می‌تواند زیان واقعی را از مقدار مدل بیشتر کند.")
else:
    st.info("قیمت ورود/حدضرر/هدف قابل اتکا از دفتر سفارش فعلی ساخته نشد؛ داده کافی برای تعیین حجم پوزیشن نداریم.")

with st.expander("⚠️ آگاهی از ریسک ضرر زنجیره‌ای"):
    table = pd.DataFrame({
        "ریسک هر معامله": [f"{p}%" for p in [0.5, 1, 2, 3, 5]],
        "۵ ضرر متوالی": [f"-{round((1-(1-p/100)**5)*100,1)}%" for p in [0.5, 1, 2, 3, 5]],
        "۱۰ ضرر متوالی": [f"-{round((1-(1-p/100)**10)*100,1)}%" for p in [0.5, 1, 2, 3, 5]],
    })
    st.dataframe(table, hide_index=True, use_container_width=True)

# ---------------------------------------------------------------------------
# ============ Allocation section: ratio-dominant formula ====================
st.divider()
st.subheader("۸. جمع‌بندی تصمیم: طلا، رمزارز یا ترکیب؟")
st.caption(
    "این بخش حول نسبت BTC/XAUT می‌چرخد — سؤال اصلی «آیا طلا نسبت به بیت‌کوین قوی‌تر شده؟» است. "
    "روند مطلق BTC و XAUT فقط یک تِلْت کوچک (۱۵٪) اثر می‌گذارد."
)
use_hard_cap = st.checkbox(
    "اعمال سقف ۵۰٪ برای رمزارز وقتی BTC/XAUT در هر دو بازه ۱روزه و ۷روزه منفی است",
    value=True, key="use_hard_cap"
)
st.caption("اگر این گزینه خاموش باشد، قانون سقف سخت اعمال نمی‌شود؛ تخصیص همچنان تابع فرمول اصلی است.")
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

    ratio_blend = 0.70 * ratio_week + 0.30 * ratio_day
    ratio_signal = max(-1.0, min(1.0, ratio_blend / 5.0))
    trend_signal = max(-1.0, min(1.0, (float(btc_score) - float(gold_score)) / 8.0))
    crypto_edge = 0.85 * ratio_signal + 0.15 * trend_signal

    if use_hard_cap and ratio_week < 0 and ratio_day < 0:
        crypto_edge = min(crypto_edge, 0.0)
    elif ratio_week > 0 and ratio_day > 0:
        crypto_edge = max(crypto_edge, 0.0)

    crypto_pct = int(round(max(10.0, min(90.0, 50.0 + 40.0 * crypto_edge))))
    gold_pct = 100 - crypto_pct

    if crypto_pct >= 60:
        allocation = f"تمایل به رمزارز: {crypto_pct}% رمزارز + {gold_pct}% طلا"
        allocation_reason = (
            f"نسبت BTC/XAUT در ۷ روز {ratio_week:+.2f}% و در ۱ روز {ratio_day:+.2f}% است؛ "
            "بیت‌کوین نسبت به طلا قوی‌تر شده."
        )
    elif crypto_pct <= 40:
        allocation = f"تمایل به طلا: {gold_pct}% طلا + {crypto_pct}% رمزارز"
        allocation_reason = (
            f"نسبت BTC/XAUT در ۷ روز {ratio_week:+.2f}% و در ۱ روز {ratio_day:+.2f}% است؛ "
            "طلا نسبت به بیت‌کوین قوی‌تر شده."
        )
    else:
        allocation = f"سیگنال ترکیبی: {gold_pct}% طلا + {crypto_pct}% رمزارز"
        allocation_reason = (
            f"نسبت BTC/XAUT در ۷ روز {ratio_week:+.2f}% و در ۱ روز {ratio_day:+.2f}% است. "
            "نسبت نزدیک به خنثی است یا روند مطلق BTC و XAUT متناقض‌اند."
        )

    st.subheader(f"پیشنهاد فعلی مدل: {allocation}")
    ac1, ac2, ac3 = st.columns(3)
    ac1.metric("سهم طلا (XAUT)", f"{gold_pct}%")
    ac2.metric("سهم رمزارز", f"{crypto_pct}%")
    ac3.metric("تغییر هفت‌روزه BTC/XAUT", f"{ratio_week:+.2f}%")
    st.write(allocation_reason)

    with st.expander("🔍 چرا این عدد؟ تفکیک سیگنال‌ها"):
        contrib_ratio = 0.85 * ratio_signal * 40
        contrib_trend = 0.15 * trend_signal * 40
        breakdown = pd.DataFrame([
            {
                "سیگنال": "نسبت BTC/XAUT (۷۰٪ × ۷روزه + ۳۰٪ × ۱روزه)",
                "مقدار خام": f"{ratio_blend:+.3f}%",
                "نرمال": f"{ratio_signal:+.3f}",
                "وزن": "۸۵٪",
                "اثر روی درصد رمزارز": f"{contrib_ratio:+.1f}٪",
            },
            {
                "سیگنال": f"روند مطلق (BTC={btc_score:+d}، XAUT={gold_score:+d})",
                "مقدار خام": f"{float(btc_score) - float(gold_score):+.1f}",
                "نرمال": f"{trend_signal:+.3f}",
                "وزن": "۱۵٪",
                "اثر روی درصد رمزارز": f"{contrib_trend:+.1f}٪",
            },
        ])
        st.dataframe(breakdown, use_container_width=True, hide_index=True)
        st.caption(
            f"جمع اثر: {contrib_ratio + contrib_trend:+.1f}٪ روی ۵۰٪ پایه → "
            f"رمزارز {crypto_pct}٪ / طلا {gold_pct}٪"
        )
        raw_info = pd.DataFrame([
            {"پارامتر": "تغییر ۷ روزه BTC/XAUT", "مقدار": f"{ratio_week:+.4f}%"},
            {"پارامتر": "تغییر ۱ روزه BTC/XAUT", "مقدار": f"{ratio_day:+.4f}%"},
            {"پارامتر": "نرخ فعلی BTC/XAUT", "مقدار": f"{number(allocation_ratio.get('current')):.6f}"},
            {"پارامتر": "تعداد کندل مشترک", "مقدار": str(allocation_ratio.get("joined_count", "—"))},
            {"پارامتر": "منبع", "مقدار": allocation_ratio.get("source", "—")},
        ])
        st.dataframe(raw_info, use_container_width=True, hide_index=True)
        if use_hard_cap and ratio_week < 0 and ratio_day < 0:
            st.info(
                "قاعدهٔ سخت اعمال شد: چون هر دو بازهٔ زمانی BTC/XAUT منفی‌اند، "
                "سهم رمزارز به ۵۰٪ یا کمتر محدود شده."
            )
        elif not use_hard_cap:
            st.caption("سقف سخت تخصیص خاموش است؛ سهم رمزارز ممکن است از ۵۰٪ بیشتر شود حتی وقتی BTC/XAUT در هر دو بازه منفی است.")

    st.caption(
        "کنترل‌های تأیید — "
        f"BTC: {'؛ '.join(btc_trend.get('notes') or ['—'])} | "
        f"XAUT: {'؛ '.join(gold_trend.get('notes') or ['—'])}"
    )
except Exception as exc:
    allocation = None
    allocation_reason = None
    gold_pct = None
    crypto_pct = None
    st.warning(f"جمع‌بندی تخصیص ناموفق: {type(exc).__name__}: {str(exc)[:180]}")

st.warning(
    "این برنامه از داده‌های عمومی بازار استفاده می‌کند و معامله‌ای ثبت نمی‌کند. "
    "سیگنال‌ها، سطوح ATR و دفتر سفارش تخمینی هستند و سود را تضمین نمی‌کنند."
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
        "imbalance": (f"{book_stats['imbalance'] * 100:+.2f}%" if book_stats.get("imbalance") is not None else "ناموجود"),
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
