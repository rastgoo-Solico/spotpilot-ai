
import statistics
import requests
import pandas as pd
import streamlit as st
import ccxt

LCW_URL = "https://api.livecoinwatch.com"
EXCHANGE_IDS = ["binance", "bybit", "okx", "kucoin", "kraken", "lbank"]
QUOTE = "USDT"

st.set_page_config(page_title="SpotPilot AI", page_icon="📊", layout="wide")


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


def number(value, default=0.0):
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def get_lcw_key():
    try:
        key = st.secrets["LIVECOINWATCH_API_KEY"]
        return str(key).strip()
    except Exception:
        return ""


def lcw_post(endpoint, payload):
    key = get_lcw_key()
    if not key:
        raise RuntimeError(
            "کلید LIVECOINWATCH_API_KEY در Streamlit Secrets تنظیم نشده است."
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


@st.cache_data(ttl=60, show_spinner=False)
def fetch_lcw_market(min_btc_volume):
    """Load up to 2,000 LiveCoinWatch assets, then apply the BTC-volume filter."""
    btc = lcw_post(
        "coins/single",
        {"currency": "USD", "code": "BTC", "meta": False},
    )
    btc_usd = number(btc.get("rate"))

    if btc_usd <= 0:
        raise RuntimeError("قیمت BTC از LiveCoinWatch دریافت نشد.")

    all_coins = []

    # LiveCoinWatch supports up to 100 coins per request.
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

    minimum_usd_volume = min_btc_volume * btc_usd
    rows = []

    for coin in all_coins:
        volume_usd = number(coin.get("volume"))
        rate_usd = number(coin.get("rate"))
        delta = coin.get("delta") or {}
        day_delta = number(delta.get("day"), default=None)

        # LiveCoinWatch delta values are rate ratios, not percentages.
        change_pct = (
            (day_delta - 1) * 100
            if day_delta is not None else None
        )

        if volume_usd < minimum_usd_volume:
            continue

        rows.append({
            "Name": coin.get("name", ""),
            "Code": str(coin.get("code", "")).upper(),
            "Price USD": rate_usd,
            "24h Change %": change_pct,
            "Volume USD": volume_usd,
            "Volume BTC equivalent": volume_usd / btc_usd,
            "Exchanges reported": coin.get("exchanges"),
            "Markets reported": coin.get("markets"),
            "Pairs reported": coin.get("pairs"),
        })

    # Preserve the previous workflow: sort filtered coins by 24h gain.
    rows.sort(
        key=lambda row: (
            row["24h Change %"]
            if row["24h Change %"] is not None else float("-inf")
        ),
        reverse=True,
    )

    return rows, btc_usd, len(all_coins)


def make_exchange(exchange_id):
    exchange_class = getattr(ccxt, exchange_id)
    return exchange_class({
        "enableRateLimit": True,
        "timeout": 20000,
        "options": {"defaultType": "spot"},
    })


@st.cache_data(ttl=300, show_spinner=False)
def get_spot_symbols(exchange_id):
    exchange = make_exchange(exchange_id)
    try:
        markets = exchange.load_markets()
        result = []

        for symbol, market in markets.items():
            if not market.get("spot"):
                continue
            if market.get("quote") != QUOTE:
                continue
            if market.get("active") is False:
                continue

            result.append({
                "symbol": symbol,
                "base": str(market.get("base", "")).upper(),
                "quote": str(market.get("quote", "")).upper(),
            })

        return result
    finally:
        try:
            exchange.close()
        except Exception:
            pass


@st.cache_data(ttl=20, show_spinner=False)
def fetch_exchange_ticker(exchange_id, symbol):
    exchange = make_exchange(exchange_id)
    try:
        return exchange.fetch_ticker(symbol)
    finally:
        try:
            exchange.close()
        except Exception:
            pass


@st.cache_data(ttl=20, show_spinner=False)
def fetch_lbank_book(symbol):
    exchange = make_exchange("lbank")
    try:
        return exchange.fetch_order_book(symbol, limit=100)
    finally:
        try:
            exchange.close()
        except Exception:
            pass


@st.cache_data(ttl=20, show_spinner=False)
def fetch_lbank_trades(symbol):
    exchange = make_exchange("lbank")
    try:
        return exchange.fetch_trades(symbol, limit=500)
    finally:
        try:
            exchange.close()
        except Exception:
            pass


def find_market(exchange_id, coin_code):
    try:
        markets = get_spot_symbols(exchange_id)
        for market in markets:
            if market["base"] == coin_code.upper():
                return market["symbol"]
    except Exception:
        return None
    return None


def compare_exchange_prices(coin_code):
    rows = []
    errors = []

    for exchange_id in EXCHANGE_IDS:
        symbol = find_market(exchange_id, coin_code)
        if not symbol:
            continue

        try:
            ticker = fetch_exchange_ticker(exchange_id, symbol)
            price = number(ticker.get("last"))

            if price > 0:
                rows.append({
                    "Exchange": exchange_id.upper(),
                    "Pair": symbol,
                    "Last price": price,
                    "24h quote volume": ticker.get("quoteVolume"),
                })
        except Exception as exc:
            errors.append(
                f"{exchange_id.upper()}: {type(exc).__name__}"
            )

    if rows:
        prices = [row["Last price"] for row in rows]
        median_price = statistics.median(prices)

        for row in rows:
            row["Difference from median %"] = (
                (row["Last price"] / median_price - 1) * 100
            )

    return rows, errors


def analyze_book(book):
    bids = [
        (number(x[0]), number(x[1]))
        for x in book.get("bids", [])
        if len(x) >= 2 and number(x[0]) > 0 and number(x[1]) > 0
    ]
    asks = [
        (number(x[0]), number(x[1]))
        for x in book.get("asks", [])
        if len(x) >= 2 and number(x[0]) > 0 and number(x[1]) > 0
    ]

    if not bids or not asks:
        raise ValueError("دفتر سفارش خرید یا فروش خالی است.")

    best_bid = max(price for price, qty in bids)
    best_ask = min(price for price, qty in asks)

    if best_ask < best_bid:
        raise ValueError("قیمت‌های دفتر سفارش نامعتبر هستند.")

    mid = (best_bid + best_ask) / 2
    spread_pct = (best_ask - best_bid) / mid * 100

    bid_usd = sum(price * qty for price, qty in bids)
    ask_usd = sum(price * qty for price, qty in asks)
    total = bid_usd + ask_usd

    imbalance = (
        (bid_usd - ask_usd) / total if total > 0 else 0
    )

    level_values = [
        price * qty for price, qty in bids + asks
    ]
    median_value = statistics.median(level_values)
    percentile_value = sorted(level_values)[
        min(len(level_values) - 1, int(len(level_values) * 0.90))
    ]

    wall_threshold = max(median_value * 3, percentile_value)

    bid_walls = [
        {"price": price, "quantity": qty, "value": price * qty}
        for price, qty in bids
        if price * qty >= wall_threshold
    ]
    ask_walls = [
        {"price": price, "quantity": qty, "value": price * qty}
        for price, qty in asks
        if price * qty >= wall_threshold
    ]

    # If the threshold finds no walls, use the largest real levels
    # as candidate support/resistance. They are not labelled as confirmed walls.
    if not bid_walls:
        bid_walls = [
            {"price": p, "quantity": q, "value": p * q}
            for p, q in sorted(
                bids, key=lambda level: level[0] * level[1], reverse=True
            )[:5]
        ]

    if not ask_walls:
        ask_walls = [
            {"price": p, "quantity": q, "value": p * q}
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
        info = trade.get("info") or {}

        # Fallback for exchanges that expose isBuyerMaker in raw trade data.
        if side not in ("buy", "sell"):
            maker = info.get("isBuyerMaker")
            if maker is True or str(maker).lower() == "true":
                side = "sell"
            elif maker is False or str(maker).lower() == "false":
                side = "buy"

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
            "reason": "سطوح کافی برای ساخت سناریوی ورود و خروج پیدا نشد.",
        }

    support = max(bids, key=lambda wall: wall["price"])
    resistance = min(asks, key=lambda wall: wall["price"])

    entry = support["price"] * 1.001
    target = resistance["price"] * 0.999
    stop = support["price"] * 0.995

    support_distance_pct = (mid - support["price"]) / mid * 100

    risk = entry - stop
    reward = target - entry
    rr = reward / risk if risk > 0 else 0

    if entry >= mid or target <= entry or risk <= 0:
        return {
            "status": "WAIT",
            "reason": "چیدمان فعلی دفتر سفارش نسبت سود به ریسک مناسبی ایجاد نمی‌کند.",
            "support": support,
            "resistance": resistance,
        }

    # Do not require every signal to be positive merely to display candidate levels.
    # The status depends on price distance, spread, reward/risk and confirmations.
    confirmations = []
    warnings = []

    if book_stats["imbalance"] > 0:
        confirmations.append("توازن ارزش دفتر سفارش به نفع خرید است.")
    else:
        warnings.append("توازن دفتر سفارش فعلاً به نفع خرید نیست.")

    if trade_stats["net_flow"] > 0:
        confirmations.append("جریان خالص معاملات طبقه‌بندی‌شده مثبت است.")
    elif trade_stats["net_flow"] < 0:
        warnings.append("جریان خالص معاملات طبقه‌بندی‌شده منفی است.")
    else:
        warnings.append("جریان خرید/فروش خالص مثبت نیست یا داده کافی نیست.")

    if support_distance_pct > 2:
        warnings.append("سطح حمایت بیش از ۲٪ پایین‌تر از قیمت میانی قرار دارد.")

    if book_stats["spread_pct"] > 0.5:
        warnings.append("فاصله خرید و فروش بالاست؛ هزینه اجرای معامله ممکن است زیاد باشد.")

    if rr < 1.2:
        warnings.append("نسبت سود احتمالی به ریسک کمتر از ۱٫۲ است.")

    if (
        support_distance_pct <= 2
        and book_stats["spread_pct"] <= 0.5
        and rr >= 1.2
        and (book_stats["imbalance"] > 0 or trade_stats["net_flow"] > 0)
    ):
        status = "ENTRY WATCH"
        reason = " / ".join(confirmations + warnings)
    else:
        status = "WAIT"
        reason = " / ".join(warnings + confirmations)

    return {
        "status": status,
        "reason": reason,
        "entry": entry,
        "target": target,
        "stop": stop,
        "rr": rr,
        "support": support,
        "resistance": resistance,
        "support_distance_pct": support_distance_pct,
    }


# ---------------------------- UI ----------------------------

st.title("📊 SpotPilot AI")
st.caption(
    "غربال LiveCoinWatch + مقایسه چندصرافی + تحلیل دفتر سفارش LBank"
)

with st.sidebar:
    st.header("فیلترهای بازار")

    min_btc_volume = st.number_input(
        "حداقل حجم ۲۴ساعته، معادل BTC",
        min_value=0.0,
        value=1000.0,
        step=100.0,
        help="این فیلتر روی حجم دلاری LiveCoinWatch، با تبدیل به BTC اعمال می‌شود.",
    )

    refresh = st.button("🔄 به‌روزرسانی داده‌ها", use_container_width=True)

if refresh:
    fetch_lcw_market.clear()
    get_spot_symbols.clear()
    fetch_exchange_ticker.clear()
    fetch_lbank_book.clear()
    fetch_lbank_trades.clear()
    st.rerun()

if not get_lcw_key():
    st.error(
        "کلید API پیدا نشد. در Streamlit Cloud به Settings → Secrets برو "
        "و LIVECOINWATCH_API_KEY را تنظیم کن."
    )
    st.stop()

try:
    with st.spinner("دریافت فهرست ارزها از LiveCoinWatch..."):
        coins, btc_usd, total_fetched = fetch_lcw_market(min_btc_volume)
except Exception as exc:
    st.error(f"دریافت داده LiveCoinWatch ناموفق بود: {exc}")
    st.stop()

st.metric("قیمت BTC/USD از LiveCoinWatch", fmt_usd(btc_usd))
st.metric("تعداد ارزهای دریافت‌شده پیش از فیلتر", total_fetched)
st.metric("تعداد ارزهای باقی‌مانده پس از فیلتر", len(coins))

if not coins:
    st.warning(
        "با این فیلتر هیچ ارزی باقی نماند. مقدار حداقل حجم را بررسی کن؛ "
        "هیچ داده‌ای به‌صورت ساختگی اضافه نشده است."
    )
    st.stop()

coins_df = pd.DataFrame(coins)

# Add availability information without using it to remove coins.
lbank_symbols = []
lbank_errors = []

try:
    lbank_markets = get_spot_symbols("lbank")
    lbank_bases = {item["base"] for item in lbank_markets}
    lbank_symbols = [
        item["symbol"] for item in lbank_markets
    ]
    coins_df["LBank listed"] = coins_df["Code"].isin(lbank_bases)
except Exception as exc:
    coins_df["LBank listed"] = False
    lbank_errors.append(str(exc))

display_df = coins_df.copy()
display_df["Price USD"] = display_df["Price USD"].map(fmt_price)
display_df["Volume USD"] = display_df["Volume USD"].map(fmt_usd)
display_df["Volume BTC equivalent"] = display_df[
    "Volume BTC equivalent"
].map(lambda x: f"{x:,.2f}")
display_df["24h Change %"] = display_df["24h Change %"].map(
    lambda x: f"{x:+.2f}%" if x is not None else "—"
)

st.subheader("۱. فهرست LiveCoinWatch")
st.caption(
    "برای انتخاب ارز روی ردیف آن کلیک کن. ارزها بر اساس تغییر ۲۴ساعته "
    "مرتب شده‌اند؛ ارزهای خارج از LBank هم از فهرست حذف نمی‌شوند."
)

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
    selected_index = event.selection.rows[0]
    selected_coin = coins_df.iloc[selected_index]
    selected_code = selected_coin["Code"]
else:
    selected_code = st.selectbox(
        "یا ارز را از فهرست انتخاب کن",
        coins_df["Code"].tolist(),
    )
    selected_coin = coins_df[coins_df["Code"] == selected_code].iloc[0]

st.divider()
st.subheader(f"۲. مقایسه قیمت {selected_code} در صرافی‌ها")

with st.spinner("بررسی بازارهای عمومی صرافی‌ها..."):
    comparison, compare_errors = compare_exchange_prices(selected_code)

if comparison:
    comp_df = pd.DataFrame(comparison)
    comp_df["Last price"] = comp_df["Last price"].map(fmt_price)
    comp_df["Difference from median %"] = comp_df[
        "Difference from median %"
    ].map(lambda x: f"{x:+.3f}%")
    comp_df["24h quote volume"] = comp_df["24h quote volume"].map(
        lambda x: fmt_usd(x) if x is not None else "—"
    )
    st.dataframe(comp_df, use_container_width=True, hide_index=True)
else:
    st.warning(
        "برای این ارز قیمت قابل استفاده‌ای از صرافی‌های بررسی‌شده دریافت نشد."
    )

if compare_errors:
    with st.expander("جزئیات خطاهای دریافت قیمت"):
        st.write("\n".join(compare_errors))

st.divider()
st.subheader("۳. تحلیل دفتر سفارش LBank")

lbank_symbol = find_market("lbank", selected_code)

if not lbank_symbol:
    st.warning(
        f"{selected_code} در بازار اسپات USDT صرافی LBank پیدا نشد. "
        "فهرست ارزها حفظ شده است، اما دفتر سفارش و سیگنال LBank برای "
        "این ارز قابل محاسبه نیست."
    )
    st.stop()

st.write(f"جفت‌ارز انتخاب‌شده در LBank: **{lbank_symbol}**")

try:
    with st.spinner("دریافت دفتر سفارش LBank..."):
        book = fetch_lbank_book(lbank_symbol)
    book_stats = analyze_book(book)
except Exception as exc:
    st.error(f"دریافت یا تحلیل دفتر سفارش ناموفق بود: {exc}")
    st.stop()

try:
    with st.spinner("دریافت معاملات اخیر LBank..."):
        trades = fetch_lbank_trades(lbank_symbol)
    trade_stats = analyze_trades(trades)
except Exception as exc:
    trades = []
    trade_stats = analyze_trades([])
    st.warning(
        "دفتر سفارش دریافت شد، اما معاملات اخیر در دسترس نیستند. "
        f"جریان خریدوفروش تأیید نشده است: {type(exc).__name__}"
    )

metrics = st.columns(4)
metrics[0].metric("بهترین خرید", fmt_price(book_stats["best_bid"]))
metrics[1].metric("بهترین فروش", fmt_price(book_stats["best_ask"]))
metrics[2].metric("فاصله خرید و فروش", f"{book_stats['spread_pct']:.4f}%")
metrics[3].metric("عدم‌تعادل دفتر سفارش", f"{book_stats['imbalance'] * 100:+.2f}%")

flow_metrics = st.columns(4)
flow_metrics[0].metric("حجم خرید اخیر", fmt_usd(trade_stats["buy_usd"]))
flow_metrics[1].metric("حجم فروش اخیر", fmt_usd(trade_stats["sell_usd"]))
flow_metrics[2].metric("جریان خالص", fmt_usd(trade_stats["net_flow"]))
flow_metrics[3].metric("تعداد معاملات", trade_stats["total_count"])

bid_col, ask_col = st.columns(2)

with bid_col:
    st.markdown("**سطوح خرید / حمایت احتمالی**")
    bid_df = pd.DataFrame(book_stats["bid_walls"])
    bid_df = bid_df.rename(columns={
        "price": "قیمت",
        "quantity": "مقدار",
        "value": "ارزش تقریبی",
    })
    bid_df["قیمت"] = bid_df["قیمت"].map(fmt_price)
    bid_df["مقدار"] = bid_df["مقدار"].map(lambda x: f"{x:,.6f}")
    bid_df["ارزش تقریبی"] = bid_df["ارزش تقریبی"].map(fmt_usd)
    st.dataframe(bid_df, use_container_width=True, hide_index=True)

with ask_col:
    st.markdown("**سطوح فروش / مقاومت احتمالی**")
    ask_df = pd.DataFrame(book_stats["ask_walls"])
    ask_df = ask_df.rename(columns={
        "price": "قیمت",
        "quantity": "مقدار",
        "value": "ارزش تقریبی",
    })
    ask_df["قیمت"] = ask_df["قیمت"].map(fmt_price)
    ask_df["مقدار"] = ask_df["مقدار"].map(lambda x: f"{x:,.6f}")
    ask_df["ارزش تقریبی"] = ask_df["ارزش تقریبی"].map(fmt_usd)
    st.dataframe(ask_df, use_container_width=True, hide_index=True)

st.divider()
st.subheader("۴. نقاط ورود و خروج احتمالی")

plan = calculate_trade_plan(book_stats, trade_stats)

if plan["status"] == "ENTRY WATCH":
    st.success("وضعیت: فرصت برای بررسی ورود")
else:
    st.warning("وضعیت: انتظار برای تأیید")

st.write(plan["reason"])

if "entry" in plan:
    level_cols = st.columns(4)
    level_cols[0].metric("ورود احتمالی", fmt_price(plan["entry"]))
    level_cols[1].metric("هدف خروج", fmt_price(plan["target"]))
    level_cols[2].metric("حد ابطال سناریو", fmt_price(plan["stop"]))
    level_cols[3].metric("نسبت پاداش به ریسک", f"{plan['rr']:.2f}")

    st.caption(
        f"حمایت مبنا: {fmt_price(plan['support']['price'])} "
        f"({fmt_usd(plan['support']['value'])}) | "
        f"مقاومت مبنا: {fmt_price(plan['resistance']['price'])} "
        f"({fmt_usd(plan['resistance']['value'])})"
    )
else:
    st.info(
        "به دلیل نبود سطح مناسب یا نامناسب بودن چیدمان دفتر سفارش، "
        "قیمت ورود و خروج قابل ارائه نیست."
    )

st.warning(
    "این خروجی تحلیل احتمالی است، نه تضمین سود. دیوارهای سفارش ممکن است "
    "حذف شوند و قیمت‌ها پیش از رسیدن به سطوح پیشنهادی تغییر کنند. "
    "کارمزد، لغزش قیمت و ریسک اجرای سفارش باید جداگانه بررسی شوند. "
    "برنامه هیچ سفارش خرید یا فروشی ثبت نمی‌کند."
)
