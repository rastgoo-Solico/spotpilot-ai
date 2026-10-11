
import statistics
import requests
import pandas as pd
import streamlit as st
import ccxt

LCW_URL = "https://api.livecoinwatch.com"
LBANK_URL = "https://api.lbank.info/v2"
OTHER_EXCHANGES = ["binance", "bybit", "okx", "kucoin", "kraken"]
QUOTE = "USDT"

st.set_page_config(page_title="SpotPilot AI", page_icon="📊", layout="wide")


# -------------------- Common helpers --------------------

def number(value, default=0.0):
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def fmt_price(value):
    if value is None:
        return "—"
    try:
        value = float(value)
        if value <= 0:
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


def is_success(payload):
    result = payload.get("result")
    return result is True or str(result).lower() == "true"


def get_lcw_key():
    try:
        return str(st.secrets["LIVECOINWATCH_API_KEY"]).strip()
    except Exception:
        return ""


def get_json(url, params=None, timeout=20):
    response = requests.get(url, params=params, timeout=timeout)
    response.raise_for_status()
    return response.json()


def lcw_post(endpoint, payload):
    key = get_lcw_key()
    if not key:
        raise RuntimeError(
            "LIVECOINWATCH_API_KEY در Streamlit Secrets تنظیم نشده است."
        )

    response = requests.post(
        f"{LCW_URL}/{endpoint}",
        headers={
            "content-type": "application/json",
            "x-api-key": key,
        },
        json=payload,
        timeout=25,
    )
    response.raise_for_status()
    return response.json()


# -------------------- LiveCoinWatch screening --------------------

@st.cache_data(ttl=60, show_spinner=False)
def fetch_lcw_market(min_btc_volume):
    btc = lcw_post(
        "coins/single",
        {"currency": "USD", "code": "BTC", "meta": False},
    )
    btc_usd = number(btc.get("rate"))

    if btc_usd <= 0:
        raise RuntimeError("قیمت BTC از LiveCoinWatch معتبر نیست.")

    all_coins = []

    # Maximum 100 records per request; fetch up to 2,000 as before.
    for offset in range(0, 2000, 100):
        batch = lcw_post(
            "coins/list",
            {
                "currency": "USD",
                "sort": "volume",
                "order": "descending",
                "offset": offset,
                "limit": 100,
                "meta": True,
            },
        )

        if not isinstance(batch, list) or not batch:
            break

        all_coins.extend(batch)

        if len(batch) < 100:
            break

    min_usd_volume = min_btc_volume * btc_usd
    rows = []

    for coin in all_coins:
        volume_usd = number(coin.get("volume"))
        price_usd = number(coin.get("rate"))

        if volume_usd < min_usd_volume:
            continue

        delta = coin.get("delta") or {}
        day_delta = delta.get("day")
        change_pct = (
            (number(day_delta) - 1) * 100
            if day_delta is not None else None
        )

        rows.append({
            "Name": coin.get("name", ""),
            "Code": str(coin.get("code", "")).upper(),
            "Price USD": price_usd,
            "24h Change %": change_pct,
            "Volume USD": volume_usd,
            "Volume BTC equivalent": volume_usd / btc_usd,
            "Exchanges reported": coin.get("exchanges"),
            "Markets reported": coin.get("markets"),
            "Pairs reported": coin.get("pairs"),
        })

    rows.sort(
        key=lambda row: (
            row["24h Change %"]
            if row["24h Change %"] is not None else float("-inf")
        ),
        reverse=True,
    )

    return rows, btc_usd, len(all_coins)


# -------------------- Direct public LBank API --------------------

def lbank_symbol_from_code(code):
    return f"{code.lower()}_usdt"


@st.cache_data(ttl=300, show_spinner=False)
def fetch_lbank_pairs():
    payload = get_json(f"{LBANK_URL}/currencyPairs.do")

    if not is_success(payload):
        raise RuntimeError(
            f"LBank currencyPairs API error: {payload.get('msg', payload)}"
        )

    raw = payload.get("data") or []
    pairs = set()

    for item in raw:
        if isinstance(item, str):
            pair = item.lower().replace("-", "_").replace("/", "_")
            if pair.endswith("_usdt"):
                pairs.add(pair)

        elif isinstance(item, dict):
            candidate = (
                item.get("symbol")
                or item.get("pair")
                or item.get("name")
                or ""
            )
            if candidate:
                pair = str(candidate).lower().replace("-", "_").replace("/", "_")
                if pair.endswith("_usdt"):
                    pairs.add(pair)

    if not pairs:
        raise RuntimeError(
            "پاسخ LBank دریافت شد اما جفت‌ارزهای USDT قابل شناسایی نبودند."
        )

    return sorted(pairs)


@st.cache_data(ttl=15, show_spinner=False)
def fetch_lbank_book(pair):
    payload = get_json(
        f"{LBANK_URL}/depth.do",
        params={"symbol": pair, "size": 100},
    )

    if not is_success(payload):
        raise RuntimeError(
            f"LBank depth API error: {payload.get('msg', payload)}"
        )

    data = payload.get("data") or {}
    bids = data.get("bids") or []
    asks = data.get("asks") or []

    if not bids or not asks:
        raise RuntimeError(f"دفتر سفارش برای {pair} خالی است.")

    return {
        "bids": bids,
        "asks": asks,
        "timestamp": data.get("timestamp") or payload.get("ts"),
    }


@st.cache_data(ttl=15, show_spinner=False)
def fetch_lbank_trades(pair):
    payload = get_json(
        f"{LBANK_URL}/supplement/trades.do",
        params={"symbol": pair, "size": 500},
    )

    if not is_success(payload):
        raise RuntimeError(
            f"LBank trades API error: {payload.get('msg', payload)}"
        )

    data = payload.get("data") or []
    normalized = []

    for trade in data:
        price = number(trade.get("price"))
        amount = number(trade.get("qty"))
        cost = number(trade.get("quoteQty"))

        if cost <= 0:
            cost = price * amount

        maker = trade.get("isBuyerMaker")

        # A buyer-maker trade means the aggressive/taker side was selling.
        if maker is True or str(maker).lower() == "true":
            side = "sell"
        elif maker is False or str(maker).lower() == "false":
            side = "buy"
        else:
            side = None

        normalized.append({
            "price": price,
            "amount": amount,
            "cost": cost,
            "side": side,
            "time": trade.get("time"),
        })

    return normalized


@st.cache_data(ttl=15, show_spinner=False)
def fetch_lbank_ticker(pair):
    payload = get_json(
        f"{LBANK_URL}/supplement/ticker/price.do",
        params={"symbol": pair},
    )

    if not is_success(payload):
        raise RuntimeError(
            f"LBank ticker API error: {payload.get('msg', payload)}"
        )

    data = payload.get("data") or []

    if isinstance(data, dict):
        data = [data]

    for ticker in data:
        if str(ticker.get("symbol", "")).lower() == pair.lower():
            return number(ticker.get("price"))

    if data:
        return number(data[0].get("price"))

    return 0.0


# -------------------- Other exchanges: comparison only --------------------

def make_exchange(exchange_id):
    exchange_class = getattr(ccxt, exchange_id)
    return exchange_class({
        "enableRateLimit": True,
        "timeout": 15000,
        "options": {"defaultType": "spot"},
    })


@st.cache_data(ttl=300, show_spinner=False)
def get_other_exchange_symbols(exchange_id):
    exchange = make_exchange(exchange_id)

    try:
        markets = exchange.load_markets()
        return [
            symbol
            for symbol, market in markets.items()
            if market.get("spot")
            and market.get("quote") == QUOTE
            and market.get("active") is not False
        ]
    finally:
        try:
            exchange.close()
        except Exception:
            pass


@st.cache_data(ttl=20, show_spinner=False)
def fetch_other_exchange_ticker(exchange_id, symbol):
    exchange = make_exchange(exchange_id)

    try:
        return exchange.fetch_ticker(symbol)
    finally:
        try:
            exchange.close()
        except Exception:
            pass


def compare_exchange_prices(code, lbank_pair=None):
    rows = []
    errors = []

    # LBank price is obtained directly, without CCXT.
    if lbank_pair:
        try:
            price = fetch_lbank_ticker(lbank_pair)
            if price > 0:
                rows.append({
                    "Exchange": "LBANK",
                    "Pair": lbank_pair,
                    "Last price": price,
                })
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
                rows.append({
                    "Exchange": exchange_id.upper(),
                    "Pair": target_symbol,
                    "Last price": price,
                })

        except Exception as exc:
            errors.append(
                f"{exchange_id.upper()}: {type(exc).__name__}: {str(exc)[:150]}"
            )

    if rows:
        median_price = statistics.median(
            row["Last price"] for row in rows
        )

        for row in rows:
            row["Difference from median %"] = (
                (row["Last price"] / median_price - 1) * 100
            )

    return rows, errors


# -------------------- Analysis --------------------

def analyze_book(book):
    bids = [
        (number(level[0]), number(level[1]))
        for level in book.get("bids", [])
        if len(level) >= 2
        and number(level[0]) > 0
        and number(level[1]) > 0
    ]
    asks = [
        (number(level[0]), number(level[1]))
        for level in book.get("asks", [])
        if len(level) >= 2
        and number(level[0]) > 0
        and number(level[1]) > 0
    ]

    if not bids or not asks:
        raise ValueError("دفتر سفارش خرید یا فروش معتبر نیست.")

    best_bid = max(price for price, qty in bids)
    best_ask = min(price for price, qty in asks)

    if best_ask < best_bid:
        raise ValueError("قیمت‌های دفتر سفارش نامعتبر یا متقاطع هستند.")

    mid = (best_bid + best_ask) / 2
    spread_pct = (best_ask - best_bid) / mid * 100

    bid_usd = sum(price * qty for price, qty in bids)
    ask_usd = sum(price * qty for price, qty in asks)
    total_usd = bid_usd + ask_usd

    imbalance = (
        (bid_usd - ask_usd) / total_usd
        if total_usd > 0 else 0
    )

    level_values = [price * qty for price, qty in bids + asks]
    median_value = statistics.median(level_values)
    sorted_values = sorted(level_values)
    percentile_90 = sorted_values[
        min(len(sorted_values) - 1, int(len(sorted_values) * 0.90))
    ]

    wall_threshold = max(median_value * 3, percentile_90)

    bid_walls = [
        {"price": price, "quantity": qty, "value": price * qty,
         "classification": "Large order"}
        for price, qty in bids
        if price * qty >= wall_threshold
    ]

    ask_walls = [
        {"price": price, "quantity": qty, "value": price * qty,
         "classification": "Large order"}
        for price, qty in asks
        if price * qty >= wall_threshold
    ]

    # If no large level passes the threshold, expose the largest real levels
    # as candidate support/resistance, not as confirmed walls.
    if not bid_walls:
        bid_walls = [
            {"price": p, "quantity": q, "value": p * q,
             "classification": "Largest visible bid level"}
            for p, q in sorted(
                bids, key=lambda level: level[0] * level[1], reverse=True
            )[:5]
        ]

    if not ask_walls:
        ask_walls = [
            {"price": p, "quantity": q, "value": p * q,
             "classification": "Largest visible ask level"}
            for p, q in sorted(
                asks, key=lambda level: level[0] * level[1], reverse=True
            )[:5]
        ]

    bid_walls.sort(key=lambda wall: wall["price"], reverse=True)
    ask_walls.sort(key=lambda wall: wall["price"])

    return {
        "bids": bids,
        "asks": asks,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "mid": mid,
        "spread_pct": spread_pct,
        "bid_usd": bid_usd,
        "ask_usd": ask_usd,
        "imbalance": imbalance,
        "bid_walls": bid_walls,
        "ask_walls": ask_walls,
        "wall_threshold": wall_threshold,
    }


def analyze_trades(trades):
    buy_usd = 0.0
    sell_usd = 0.0
    unknown_usd = 0.0
    buy_count = 0
    sell_count = 0

    for trade in trades:
        price = number(trade.get("price"))
        amount = number(trade.get("amount"))
        cost = number(trade.get("cost"))

        if cost <= 0:
            cost = price * amount

        side = trade.get("side")

        if side == "buy":
            buy_usd += cost
            buy_count += 1
        elif side == "sell":
            sell_usd += cost
            sell_count += 1
        else:
            unknown_usd += cost

    known_total = buy_usd + sell_usd

    return {
        "buy_usd": buy_usd,
        "sell_usd": sell_usd,
        "unknown_usd": unknown_usd,
        "net_flow": buy_usd - sell_usd,
        "buy_ratio": buy_usd / known_total if known_total else None,
        "buy_count": buy_count,
        "sell_count": sell_count,
        "total_count": len(trades),
    }


def calculate_trade_plan(book_stats, trade_stats):
    mid = book_stats["mid"]

    bids = [
        wall for wall in book_stats["bid_walls"]
        if wall["price"] < mid
    ]
    asks = [
        wall for wall in book_stats["ask_walls"]
        if wall["price"] > mid
    ]

    if not bids or not asks:
        return {
            "status": "WAIT",
            "reason": "برای تعیین سطوح، دفتر سفارش داده کافی ندارد.",
        }

    support = max(bids, key=lambda wall: wall["price"])
    resistance = min(asks, key=lambda wall: wall["price"])

    entry = support["price"] * 1.001
    target = resistance["price"] * 0.999
    stop = support["price"] * 0.995

    if entry >= mid or target <= entry:
        return {
            "status": "WAIT",
            "reason": "سطوح فعلی هدف بالاتری از ورود ایجاد نمی‌کنند.",
            "support": support,
            "resistance": resistance,
        }

    risk = entry - stop
    reward = target - entry
    rr = reward / risk if risk > 0 else 0
    support_distance_pct = (mid - support["price"]) / mid * 100

    confirmations = []
    warnings = []

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

    if support_distance_pct > 2:
        warnings.append("حمایت بیش از ۲٪ پایین‌تر از قیمت فعلی است.")

    if book_stats["spread_pct"] > 0.5:
        warnings.append("فاصله خرید و فروش بالاست.")

    if rr < 1.2:
        warnings.append("نسبت سود احتمالی به ریسک کمتر از ۱٫۲ است.")

    confirmed = (
        support_distance_pct <= 2
        and book_stats["spread_pct"] <= 0.5
        and rr >= 1.2
        and (
            book_stats["imbalance"] > 0
            or trade_stats["net_flow"] > 0
        )
    )

    return {
        "status": "ENTRY WATCH" if confirmed else "WAIT",
        "reason": " ".join(warnings + confirmations),
        "entry": entry,
        "target": target,
        "stop": stop,
        "rr": rr,
        "support": support,
        "resistance": resistance,
        "support_distance_pct": support_distance_pct,
    }


# -------------------- UI --------------------

st.title("📊 SpotPilot AI")
st.caption(
    "LiveCoinWatch screening • Cross-exchange price comparison • "
    "LBank public order book • No auto-trading"
)

with st.sidebar:
    st.header("فیلترهای بازار")

    min_btc_volume = st.number_input(
        "حداقل حجم ۲۴ساعته، معادل BTC",
        min_value=0.0,
        value=1000.0,
        step=100.0,
    )

    refresh = st.button("🔄 به‌روزرسانی داده‌ها", use_container_width=True)

if refresh:
    fetch_lcw_market.clear()
    fetch_lbank_pairs.clear()
    fetch_lbank_book.clear()
    fetch_lbank_trades.clear()
    fetch_lbank_ticker.clear()
    get_other_exchange_symbols.clear()
    fetch_other_exchange_ticker.clear()
    st.rerun()

if not get_lcw_key():
    st.error(
        "LIVECOINWATCH_API_KEY در Streamlit Secrets تنظیم نشده است."
    )
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
    st.warning(
        "با فیلتر فعلی ارزی باقی نمانده است. مقدار حداقل حجم را بررسی کن."
    )
    st.stop()

try:
    lbank_pairs = set(fetch_lbank_pairs())
    lbank_status = f"{len(lbank_pairs)} جفت‌ارز USDT شناسایی شد"
except Exception as exc:
    lbank_pairs = set()
    lbank_status = f"دریافت فهرست جفت‌ارزهای LBank ناموفق بود: {exc}"

st.caption(f"وضعیت بازار LBank: {lbank_status}")

coins_df = pd.DataFrame(coins)
coins_df["LBank listed"] = coins_df["Code"].apply(
    lambda code: lbank_symbol_from_code(code) in lbank_pairs
)

display_df = coins_df.copy()
display_df["Price USD"] = display_df["Price USD"].map(fmt_price)
display_df["Volume USD"] = display_df["Volume USD"].map(fmt_usd)
display_df["Volume BTC equivalent"] = display_df[
    "Volume BTC equivalent"
].map(lambda value: f"{value:,.2f}")
display_df["24h Change %"] = display_df["24h Change %"].map(
    lambda value: f"{value:+.2f}%" if value is not None else "—"
)

st.subheader("۱. فهرست LiveCoinWatch")
st.caption("روی ردیف یک ارز کلیک کن تا همان ارز تحلیل شود.")

event = st.dataframe(
    display_df,
    use_container_width=True,
    hide_index=True,
    height=430,
    on_select="rerun",
    selection_mode="single-row",
    key="lcw_coin_selector",
)

if event.selection.rows:
    selected_coin = coins_df.iloc[event.selection.rows[0]]
    selected_code = selected_coin["Code"]
else:
    selected_code = st.selectbox(
        "انتخاب ارز برای تحلیل",
        coins_df["Code"].tolist(),
    )
    selected_coin = coins_df[coins_df["Code"] == selected_code].iloc[0]

st.divider()
st.subheader(f"۲. مقایسه قیمت {selected_code}")

selected_lbank_pair = lbank_symbol_from_code(selected_code)
selected_lbank_pair = (
    selected_lbank_pair if selected_lbank_pair in lbank_pairs else None
)

with st.spinner("مقایسه قیمت بازارهای عمومی..."):
    comparison, compare_errors = compare_exchange_prices(
        selected_code,
        selected_lbank_pair,
    )

if comparison:
    comp_df = pd.DataFrame(comparison)
    comp_df["Last price"] = comp_df["Last price"].map(fmt_price)
    comp_df["Difference from median %"] = comp_df[
        "Difference from median %"
    ].map(lambda value: f"{value:+.3f}%")
    st.dataframe(comp_df, use_container_width=True, hide_index=True)
else:
    st.warning("قیمت قابل استفاده‌ای از صرافی‌های بررسی‌شده دریافت نشد.")

if compare_errors:
    with st.expander("جزئیات خطاهای مقایسه قیمت"):
        st.code("\n".join(compare_errors))

st.divider()
st.subheader("۳. تحلیل دفتر سفارش LBank")

if not selected_lbank_pair:
    st.warning(
        f"{selected_code} در فهرست جفت‌ارزهای USDT دریافتی از LBank پیدا نشد. "
        "ارز از LiveCoinWatch حذف نشده، اما تحلیل دفتر سفارش LBank برای "
        "این جفت‌ارز در دسترس نیست."
    )
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
    st.warning(
        "دفتر سفارش دریافت شد، اما معاملات اخیر قابل دریافت نبودند. "
        f"جزئیات: {type(exc).__name__}: {str(exc)[:250]}"
    )

metrics = st.columns(4)
metrics[0].metric("بهترین خرید", fmt_price(book_stats["best_bid"]))
metrics[1].metric("بهترین فروش", fmt_price(book_stats["best_ask"]))
metrics[2].metric("فاصله خرید و فروش", f"{book_stats['spread_pct']:.4f}%")
metrics[3].metric(
    "عدم‌تعادل دفتر سفارش",
    f"{book_stats['imbalance'] * 100:+.2f}%",
)

flows = st.columns(4)
flows[0].metric("حجم خرید اخیر", fmt_usd(trade_stats["buy_usd"]))
flows[1].metric("حجم فروش اخیر", fmt_usd(trade_stats["sell_usd"]))
flows[2].metric("جریان خالص", fmt_usd(trade_stats["net_flow"]))
flows[3].metric("تعداد معاملات", trade_stats["total_count"])

bid_col, ask_col = st.columns(2)

with bid_col:
    st.markdown("**سطوح خرید و حمایت احتمالی**")
    bid_df = pd.DataFrame(book_stats["bid_walls"]).rename(columns={
        "price": "قیمت",
        "quantity": "مقدار",
        "value": "ارزش تقریبی USDT",
        "classification": "نوع سطح",
    })
    bid_df["قیمت"] = bid_df["قیمت"].map(fmt_price)
    bid_df["مقدار"] = bid_df["مقدار"].map(lambda value: f"{value:,.6f}")
    bid_df["ارزش تقریبی USDT"] = bid_df["ارزش تقریبی USDT"].map(fmt_usd)
    st.dataframe(bid_df, use_container_width=True, hide_index=True)

with ask_col:
    st.markdown("**سطوح فروش و مقاومت احتمالی**")
    ask_df = pd.DataFrame(book_stats["ask_walls"]).rename(columns={
        "price": "قیمت",
        "quantity": "مقدار",
        "value": "ارزش تقریبی USDT",
        "classification": "نوع سطح",
    })
    ask_df["قیمت"] = ask_df["قیمت"].map(fmt_price)
    ask_df["مقدار"] = ask_df["مقدار"].map(lambda value: f"{value:,.6f}")
    ask_df["ارزش تقریبی USDT"] = ask_df["ارزش تقریبی USDT"].map(fmt_usd)
    st.dataframe(ask_df, use_container_width=True, hide_index=True)

st.divider()
st.subheader("۴. سناریوی احتمالی ورود و خروج")

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

    st.caption(
        f"سطح خرید مبنا: {fmt_price(plan['support']['price'])} "
        f"({fmt_usd(plan['support']['value'])}) | "
        f"سطح فروش مبنا: {fmt_price(plan['resistance']['price'])} "
        f"({fmt_usd(plan['resistance']['value'])})"
    )
else:
    st.info(
        "سطوح قابل استفاده برای سناریوی ورود و خروج از داده فعلی به دست نیامدند."
    )

st.warning(
    "این تحلیل احتمالی است، نه تضمین سود یا توصیه مالی قطعی. "
    "سفارش‌های بزرگ ممکن است لغو شوند و قیمت در زمان اجرا تغییر کند. "
    "کارمزد، لغزش قیمت و ریسک اجرای معامله جداگانه باید بررسی شوند. "
    "این برنامه هیچ معامله‌ای ثبت نمی‌کند."
)
