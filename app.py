
import time
import statistics
from collections import defaultdict

import ccxt
import pandas as pd
import streamlit as st


# ============================================================
# SpotPilot AI — Public Market Data + LBank Order Book Analysis
# No trading, no private API keys, no simulated market data.
# ============================================================

st.set_page_config(
    page_title="SpotPilot AI",
    page_icon="📊",
    layout="wide",
)

EXCHANGE_IDS = [
    "binance",
    "bybit",
    "okx",
    "kucoin",
    "kraken",
    "lbank",
]

QUOTE_CURRENCY = "USDT"
ORDER_BOOK_LIMIT = 100
RECENT_TRADES_LIMIT = 500


def make_exchange(exchange_id):
    """Create a public-data-only CCXT exchange client."""
    exchange_class = getattr(ccxt, exchange_id)
    return exchange_class({
        "enableRateLimit": True,
        "timeout": 20000,
        "options": {"defaultType": "spot"},
    })


def safe_float(value, default=0.0):
    try:
        if value is None:
            return default
        result = float(value)
        return result if pd.notna(result) else default
    except (TypeError, ValueError):
        return default


def fmt_price(value):
    if value is None or pd.isna(value):
        return "—"
    value = float(value)
    if value >= 1000:
        return f"{value:,.2f}"
    if value >= 1:
        return f"{value:,.6f}".rstrip("0").rstrip(".")
    if value >= 0.0001:
        return f"{value:.8f}".rstrip("0").rstrip(".")
    return f"{value:.12f}".rstrip("0").rstrip(".")


def fmt_money(value):
    if value is None or pd.isna(value):
        return "—"
    return f"${value:,.2f}"


@st.cache_data(ttl=60, show_spinner=False)
def load_market_snapshot():
    """
    Fetch real spot market data from public exchange endpoints.
    Returns:
      aggregated market rows, exchange status, LBank spot symbols.
    """
    aggregated = defaultdict(lambda: {
        "prices": [],
        "changes": [],
        "volumes": [],
        "exchange_names": [],
        "exchange_prices": {},
    })

    exchange_status = {}
    lbank_symbols = set()
    btc_prices = []

    for exchange_id in EXCHANGE_IDS:
        exchange = None

        try:
            exchange = make_exchange(exchange_id)
            markets = exchange.load_markets()

            spot_usdt_symbols = {
                symbol
                for symbol, market in markets.items()
                if market.get("spot")
                and market.get("quote") == QUOTE_CURRENCY
                and market.get("active") is not False
            }

            if exchange_id == "lbank":
                lbank_symbols = set(spot_usdt_symbols)

            if not exchange.has.get("fetchTickers"):
                exchange_status[exchange_id] = (
                    "Unavailable: bulk ticker endpoint not supported"
                )
                continue

            tickers = exchange.fetch_tickers()

            added = 0

            for symbol in spot_usdt_symbols:
                ticker = tickers.get(symbol)
                if not ticker:
                    continue

                last = safe_float(ticker.get("last"))
                if last <= 0:
                    continue

                quote_volume = safe_float(ticker.get("quoteVolume"))

                if quote_volume <= 0:
                    base_volume = safe_float(ticker.get("baseVolume"))
                    quote_volume = base_volume * last

                change_pct = ticker.get("percentage")
                if change_pct is not None:
                    change_pct = safe_float(change_pct, default=None)

                item = aggregated[symbol]
                item["prices"].append(last)
                item["volumes"].append(quote_volume)
                item["exchange_names"].append(exchange_id)
                item["exchange_prices"][exchange_id] = last

                if change_pct is not None:
                    item["changes"].append(change_pct)

                if symbol == "BTC/USDT":
                    btc_prices.append(last)

                added += 1

            exchange_status[exchange_id] = f"OK — {added} spot pairs"

        except Exception as exc:
            exchange_status[exchange_id] = (
                f"Failed: {type(exc).__name__}: {str(exc)[:160]}"
            )

        finally:
            if exchange is not None:
                try:
                    exchange.close()
                except Exception:
                    pass

    btc_price = statistics.median(btc_prices) if btc_prices else None

    rows = []

    for symbol, data in aggregated.items():
        prices = data["prices"]
        if not prices:
            continue

        # Median helps reduce the influence of a single abnormal quote.
        median_price = statistics.median(prices)
        avg_change = (
            statistics.mean(data["changes"])
            if data["changes"] else None
        )
        total_volume = sum(data["volumes"])

        base = symbol.split("/")[0]

        rows.append({
            "Symbol": symbol,
            "Base": base,
            "Price (median)": median_price,
            "24h change % (avg)": avg_change,
            "Combined reported volume USD": total_volume,
            "Exchanges": ", ".join(sorted(set(data["exchange_names"]))),
            "Exchange count": len(set(data["exchange_names"])),
            "LBank listed": symbol in lbank_symbols,
            "_exchange_prices": data["exchange_prices"],
        })

    return rows, exchange_status, lbank_symbols, btc_price


@st.cache_data(ttl=20, show_spinner=False)
def fetch_lbank_order_book(symbol):
    exchange = make_exchange("lbank")
    try:
        book = exchange.fetch_order_book(symbol, limit=ORDER_BOOK_LIMIT)
        return {
            "bids": book.get("bids", []),
            "asks": book.get("asks", []),
            "timestamp": book.get("timestamp"),
        }
    finally:
        try:
            exchange.close()
        except Exception:
            pass


@st.cache_data(ttl=20, show_spinner=False)
def fetch_lbank_recent_trades(symbol):
    exchange = make_exchange("lbank")
    try:
        return exchange.fetch_trades(symbol, limit=RECENT_TRADES_LIMIT)
    finally:
        try:
            exchange.close()
        except Exception:
            pass


def analyze_order_book(book):
    bids = [
        (safe_float(level[0]), safe_float(level[1]))
        for level in book.get("bids", [])
        if len(level) >= 2
    ]
    asks = [
        (safe_float(level[0]), safe_float(level[1]))
        for level in book.get("asks", [])
        if len(level) >= 2
    ]

    bids = [(p, q) for p, q in bids if p > 0 and q > 0]
    asks = [(p, q) for p, q in asks if p > 0 and q > 0]

    if not bids or not asks:
        raise ValueError("LBank returned an empty or invalid order book.")

    best_bid = max(p for p, _ in bids)
    best_ask = min(p for p, _ in asks)

    if best_bid <= 0 or best_ask <= 0 or best_ask < best_bid:
        raise ValueError("Order book prices are invalid or crossed.")

    mid = (best_bid + best_ask) / 2
    spread_pct = ((best_ask - best_bid) / mid) * 100

    bid_notional = sum(p * q for p, q in bids)
    ask_notional = sum(p * q for p, q in asks)
    total_notional = bid_notional + ask_notional

    imbalance = (
        (bid_notional - ask_notional) / total_notional
        if total_notional > 0 else 0.0
    )

    bid_level_values = [p * q for p, q in bids]
    ask_level_values = [p * q for p, q in asks]
    all_level_values = bid_level_values + ask_level_values

    median_level = (
        statistics.median(all_level_values)
        if all_level_values else 0.0
    )
    wall_threshold = median_level * 5

    bid_walls = [
        {"price": p, "quantity": q, "notional": p * q}
        for p, q in bids
        if p * q >= wall_threshold and wall_threshold > 0
    ]
    ask_walls = [
        {"price": p, "quantity": q, "notional": p * q}
        for p, q in asks
        if p * q >= wall_threshold and wall_threshold > 0
    ]

    bid_walls.sort(key=lambda x: x["price"], reverse=True)
    ask_walls.sort(key=lambda x: x["price"])

    return {
        "best_bid": best_bid,
        "best_ask": best_ask,
        "mid": mid,
        "spread_pct": spread_pct,
        "bid_notional": bid_notional,
        "ask_notional": ask_notional,
        "imbalance": imbalance,
        "bid_walls": bid_walls,
        "ask_walls": ask_walls,
        "bids": bids,
        "asks": asks,
    }


def analyze_recent_trades(trades):
    buy_volume_usd = 0.0
    sell_volume_usd = 0.0
    unknown_volume_usd = 0.0
    buy_count = 0
    sell_count = 0

    for trade in trades:
        price = safe_float(trade.get("price"))
        amount = safe_float(trade.get("amount"))
        cost = safe_float(trade.get("cost"))

        if cost <= 0:
            cost = price * amount

        side = trade.get("side")

        if side == "buy":
            buy_volume_usd += cost
            buy_count += 1
        elif side == "sell":
            sell_volume_usd += cost
            sell_count += 1
        else:
            unknown_volume_usd += cost

    known_total = buy_volume_usd + sell_volume_usd
    net_flow = buy_volume_usd - sell_volume_usd

    buy_ratio = (
        buy_volume_usd / known_total
        if known_total > 0 else None
    )

    return {
        "buy_volume_usd": buy_volume_usd,
        "sell_volume_usd": sell_volume_usd,
        "unknown_volume_usd": unknown_volume_usd,
        "net_flow": net_flow,
        "buy_ratio": buy_ratio,
        "buy_count": buy_count,
        "sell_count": sell_count,
        "trade_count": len(trades),
    }


def make_trade_plan(book_stats, trade_stats):
    """
    Heuristic candidate levels from the current LBank book.
    These are not guaranteed entry/exit prices.
    """
    mid = book_stats["mid"]
    imbalance = book_stats["imbalance"]

    bid_walls = [
        wall for wall in book_stats["bid_walls"]
        if wall["price"] < mid
    ]
    ask_walls = [
        wall for wall in book_stats["ask_walls"]
        if wall["price"] > mid
    ]

    if not bid_walls or not ask_walls:
        return {
            "status": "NO TRADE",
            "reason": (
                "برای تعیین سطوح، دیوار خرید و فروش معتبر کافی "
                "در دفتر سفارش فعلی پیدا نشد."
            ),
        }

    # Closest qualifying walls to the current mid price.
    bid_wall = max(bid_walls, key=lambda w: w["price"])
    ask_wall = min(ask_walls, key=lambda w: w["price"])

    # Candidate entry slightly above the bid wall.
    entry = bid_wall["price"] * 1.001
    target = ask_wall["price"] * 0.999
    stop = bid_wall["price"] * 0.997

    if entry >= mid:
        return {
            "status": "NO TRADE",
            "reason": "سطح ورود پیشنهادی دیگر پایین‌تر از قیمت میانی نیست.",
        }

    if target <= entry:
        return {
            "status": "NO TRADE",
            "reason": "دیوار فروش انتخاب‌شده هدفی بالاتر از ورود ایجاد نمی‌کند.",
        }

    risk = entry - stop
    reward = target - entry
    rr = reward / risk if risk > 0 else 0

    bid_distance_pct = ((mid - bid_wall["price"]) / mid) * 100
    net_flow = trade_stats["net_flow"]

    if bid_distance_pct > 2:
        status = "WAIT"
        reason = (
            f"دیوار خرید منتخب {bid_distance_pct:.2f}% پایین‌تر از "
            "قیمت میانی است؛ ورود بر اساس این دیوار ممکن است دور باشد."
        )
    elif net_flow <= 0 or imbalance < 0.05:
        status = "WAIT"
        reason = (
            "جریان معاملات اخیر یا توازن دفتر سفارش، تأیید کافی "
            "برای سناریوی خرید نمی‌دهد."
        )
    elif rr < 1.5:
        status = "NO TRADE"
        reason = (
            f"نسبت سود احتمالی به ریسک فقط {rr:.2f} است "
            "و به حداقل 1.5 نمی‌رسد."
        )
    else:
        status = "ENTRY WATCH"
        reason = (
            "سطوح دفتر سفارش و جریان معاملات با سناریوی خرید سازگارند؛ "
            "این فقط یک فرصت برای بررسی بیشتر است، نه سیگنال قطعی."
        )

    return {
        "status": status,
        "reason": reason,
        "entry": entry,
        "target": target,
        "stop": stop,
        "rr": rr,
        "bid_wall": bid_wall,
        "ask_wall": ask_wall,
        "bid_distance_pct": bid_distance_pct,
    }


def build_cross_exchange_rows(selected_row):
    prices = selected_row.get("_exchange_prices", {})
    rows = []

    for exchange_name, price in sorted(prices.items()):
        rows.append({
            "Exchange": exchange_name,
            "Last price": price,
        })

    if len(rows) >= 2:
        all_prices = [row["Last price"] for row in rows]
        median_price = statistics.median(all_prices)

        for row in rows:
            row["Difference from median %"] = (
                (row["Last price"] - median_price) / median_price * 100
                if median_price else None
            )
    else:
        for row in rows:
            row["Difference from median %"] = None

    return pd.DataFrame(rows)


# ========================= UI ================================

st.title("📊 SpotPilot AI")
st.caption(
    "غربال بازار چندصرافی + تحلیل دفتر سفارش LBank | "
    "فقط داده واقعی عمومی | بدون ثبت سفارش"
)

with st.sidebar:
    st.header("تنظیمات غربال بازار")

    min_btc_volume = st.number_input(
        "حداقل حجم تجمیعی ۲۴ساعته (BTC معادل)",
        min_value=0.0,
        max_value=100000.0,
        value=1000.0,
        step=100.0,
        help=(
            "حجم گزارش‌شده از بازارهای USDT جمع می‌شود و با قیمت BTC/USDT "
            "به BTC معادل تبدیل می‌شود. این حجم الزاماً حجم اسپاتِ "
            "تأییدشده یا قابل اجرا در یک صرافی واحد نیست."
        ),
    )

    min_exchange_count = st.number_input(
        "حداقل تعداد صرافی‌های گزارش‌دهنده",
        min_value=1,
        max_value=len(EXCHANGE_IDS),
        value=2,
        step=1,
    )

    show_lbank_only = st.checkbox(
        "فقط ارزهای قابل معامله در LBank",
        value=False,
    )

    st.caption("اطلاعات بازار هر ۶۰ ثانیه به‌روزرسانی می‌شود.")
    refresh = st.button("🔄 دریافت مجدد داده‌ها", use_container_width=True)

if refresh:
    load_market_snapshot.clear()
    fetch_lbank_order_book.clear()
    fetch_lbank_recent_trades.clear()
    st.rerun()

with st.spinner("در حال دریافت داده‌های عمومی از صرافی‌ها..."):
    try:
        market_rows, exchange_status, lbank_symbols, btc_price = (
            load_market_snapshot()
        )
    except Exception as exc:
        st.error(f"دریافت داده‌های بازار ناموفق بود: {exc}")
        st.stop()

if not market_rows:
    st.error(
        "هیچ داده‌ای دریافت نشد. ممکن است APIهای عمومی در دسترس نباشند "
        "یا اتصال سرور مسدود شده باشد. بعداً دوباره تلاش کن."
    )
    st.stop()

status_cols = st.columns(len(EXCHANGE_IDS))
for col, exchange_id in zip(status_cols, EXCHANGE_IDS):
    with col:
        st.caption(f"**{exchange_id.upper()}**")
        st.write(exchange_status.get(exchange_id, "No status"))

if btc_price:
    st.caption(f"BTC/USDT مرجع بازار: ${btc_price:,.2f}")
else:
    st.warning(
        "قیمت مرجع BTC/USDT دریافت نشد؛ فیلتر حجم بر اساس BTC قابل محاسبه نیست."
    )

market_df = pd.DataFrame(market_rows)

if btc_price:
    market_df["Combined volume BTC equivalent"] = (
        market_df["Combined reported volume USD"] / btc_price
    )
else:
    market_df["Combined volume BTC equivalent"] = 0.0

filtered = market_df[
    (market_df["Exchange count"] >= min_exchange_count)
    & (
        market_df["Combined volume BTC equivalent"]
        >= min_btc_volume
    )
].copy()

if show_lbank_only:
    filtered = filtered[filtered["LBank listed"]].copy()

filtered = filtered.sort_values(
    "Combined reported volume USD",
    ascending=False,
).head(500).reset_index(drop=True)

if filtered.empty:
    st.warning(
        "ارزی با این فیلترها پیدا نشد. حداقل حجم BTC یا تعداد صرافی‌ها "
        "را کمتر کن یا گزینه «فقط ارزهای قابل معامله در LBank» را بردار."
    )
    st.stop()

st.subheader("۱. فهرست بازار و انتخاب ارز")
st.caption(
    "برای تحلیل یک ارز، روی ردیف آن کلیک کن. "
    "قیمت تجمیعی، میانه قیمت صرافی‌های در دسترس است؛ "
    "حجم‌ها جمع حجم گزارش‌شده‌اند."
)

display_df = filtered[[
    "Symbol",
    "Price (median)",
    "24h change % (avg)",
    "Combined reported volume USD",
    "Combined volume BTC equivalent",
    "Exchange count",
    "Exchanges",
    "LBank listed",
]].copy()

display_df["Price (median)"] = display_df["Price (median)"].map(fmt_price)
display_df["Combined reported volume USD"] = (
    display_df["Combined reported volume USD"].map(fmt_money)
)
display_df["Combined volume BTC equivalent"] = (
    display_df["Combined volume BTC equivalent"].map(
        lambda x: f"{x:,.2f} BTC"
    )
)
display_df["24h change % (avg)"] = display_df[
    "24h change % (avg)"
].map(lambda x: f"{x:+.2f}%" if pd.notna(x) else "—")

event = st.dataframe(
    display_df,
    use_container_width=True,
    hide_index=True,
    height=420,
    on_select="rerun",
    selection_mode="single-row",
    key="market_coin_selector",
)

selected_rows = event.selection.rows

if selected_rows:
    selected_index = selected_rows[0]
    selected_row = filtered.iloc[selected_index]
    selected_symbol = selected_row["Symbol"]
else:
    selected_symbol = st.selectbox(
        "یا ارز را از این فهرست انتخاب کن",
        filtered["Symbol"].tolist(),
    )
    selected_row = filtered[
        filtered["Symbol"] == selected_symbol
    ].iloc[0]

st.divider()
st.subheader(f"۲. تحلیل ارز {selected_symbol}")

cross_df = build_cross_exchange_rows(selected_row)

left, right = st.columns([1, 1])

with left:
    st.markdown("**مقایسه قیمت بین صرافی‌ها**")
    if not cross_df.empty:
        cross_display = cross_df.copy()
        cross_display["Last price"] = cross_display["Last price"].map(fmt_price)
        cross_display["Difference from median %"] = cross_display[
            "Difference from median %"
        ].map(lambda x: f"{x:+.3f}%" if pd.notna(x) else "—")
        st.dataframe(
            cross_display,
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("قیمت قابل مقایسه‌ای دریافت نشد.")

with right:
    st.markdown("**خلاصه بازار**")
    st.metric(
        "تغییر میانگین گزارش‌شده ۲۴ساعته",
        (
            f"{selected_row['24h change % (avg)']:+.2f}%"
            if pd.notna(selected_row["24h change % (avg)"])
            else "—"
        ),
    )
    st.metric(
        "حجم گزارش‌شده تجمیعی",
        fmt_money(selected_row["Combined reported volume USD"]),
    )
    st.write(
        "صرافی‌های گزارش‌دهنده:",
        selected_row["Exchanges"],
    )

if not selected_row["LBank listed"]:
    st.warning(
        f"{selected_symbol} در بازارهای اسپات LBank که دریافت شدند "
        "پیدا نشد. مقایسه قیمت نمایش داده شده، اما تحلیل دفتر سفارش "
        "و سطوح ورود/خروج LBank برای این ارز در دسترس نیست."
    )
    st.stop()

st.divider()
st.subheader("۳. دفتر سفارش واقعی LBank")

try:
    with st.spinner("در حال دریافت دفتر سفارش و معاملات اخیر LBank..."):
        book = fetch_lbank_order_book(selected_symbol)
        trades = fetch_lbank_recent_trades(selected_symbol)

    book_stats = analyze_order_book(book)
    trade_stats = analyze_recent_trades(trades)

except Exception as exc:
    st.error(
        "داده دفتر سفارش یا معاملات LBank دریافت نشد. "
        "ممکن است جفت‌ارز در این API در دسترس نباشد یا اتصال موقتاً قطع باشد."
    )
    st.code(f"{type(exc).__name__}: {str(exc)[:500]}")
    st.stop()

metrics = st.columns(4)
metrics[0].metric("بهترین خرید (Bid)", fmt_price(book_stats["best_bid"]))
metrics[1].metric("بهترین فروش (Ask)", fmt_price(book_stats["best_ask"]))
metrics[2].metric("فاصله خرید و فروش", f"{book_stats['spread_pct']:.4f}%")
metrics[3].metric("عدم‌تعادل دفتر سفارش", f"{book_stats['imbalance'] * 100:+.2f}%")

flow_cols = st.columns(4)
flow_cols[0].metric("حجم خرید معاملات اخیر", fmt_money(trade_stats["buy_volume_usd"]))
flow_cols[1].metric("حجم فروش معاملات اخیر", fmt_money(trade_stats["sell_volume_usd"]))
flow_cols[2].metric("خالص جریان خرید", fmt_money(trade_stats["net_flow"]))
flow_cols[3].metric("تعداد معاملات دریافت‌شده", str(trade_stats["trade_count"]))

st.caption(
    "جریان خرید/فروش از فیلد side معاملات اخیر در CCXT محاسبه شده است. "
    "اگر صرافی سمت معامله را ارائه نکند، آن معامله در حجم خرید/فروش "
    "طبقه‌بندی نمی‌شود."
)

wall_col1, wall_col2 = st.columns(2)

with wall_col1:
    st.markdown("**دیوارهای خرید شناسایی‌شده**")
    if book_stats["bid_walls"]:
        bid_df = pd.DataFrame(book_stats["bid_walls"])
        bid_df = bid_df.rename(columns={
            "price": "قیمت",
            "quantity": "مقدار",
            "notional": "ارزش تقریبی USDT",
        })
        bid_df["قیمت"] = bid_df["قیمت"].map(fmt_price)
        bid_df["مقدار"] = bid_df["مقدار"].map(lambda x: f"{x:,.6f}")
        bid_df["ارزش تقریبی USDT"] = bid_df["ارزش تقریبی USDT"].map(fmt_money)
        st.dataframe(bid_df, use_container_width=True, hide_index=True)
    else:
        st.info("با آستانه فعلی دیوار خرید مشخصی پیدا نشد.")

with wall_col2:
    st.markdown("**دیوارهای فروش شناسایی‌شده**")
    if book_stats["ask_walls"]:
        ask_df = pd.DataFrame(book_stats["ask_walls"])
        ask_df = ask_df.rename(columns={
            "price": "قیمت",
            "quantity": "مقدار",
            "notional": "ارزش تقریبی USDT",
        })
        ask_df["قیمت"] = ask_df["قیمت"].map(fmt_price)
        ask_df["مقدار"] = ask_df["مقدار"].map(lambda x: f"{x:,.6f}")
        ask_df["ارزش تقریبی USDT"] = ask_df["ارزش تقریبی USDT"].map(fmt_money)
        st.dataframe(ask_df, use_container_width=True, hide_index=True)
    else:
        st.info("با آستانه فعلی دیوار فروش مشخصی پیدا نشد.")

st.divider()
st.subheader("۴. سناریوی احتمالی ورود و خروج")

plan = make_trade_plan(book_stats, trade_stats)

if plan["status"] == "ENTRY WATCH":
    st.success("وضعیت: بررسی فرصت ورود")
elif plan["status"] == "WAIT":
    st.warning("وضعیت: انتظار برای تأیید")
else:
    st.error("وضعیت: فعلاً معامله‌ای پیشنهاد نمی‌شود")

st.write(plan["reason"])

if "entry" in plan:
    plan_cols = st.columns(4)
    plan_cols[0].metric("ورود احتمالی", fmt_price(plan["entry"]))
    plan_cols[1].metric("هدف خروج احتمالی", fmt_price(plan["target"]))
    plan_cols[2].metric("حد ابطال سناریو", fmt_price(plan["stop"]))
    plan_cols[3].metric("نسبت پاداش به ریسک", f"{plan['rr']:.2f}")

    st.caption(
        f"دیوار خرید مبنا: {fmt_price(plan['bid_wall']['price'])} "
        f"({fmt_money(plan['bid_wall']['notional'])}) | "
        f"دیوار فروش مبنا: {fmt_price(plan['ask_wall']['price'])} "
        f"({fmt_money(plan['ask_wall']['notional'])})"
    )

st.warning(
    "این سطوح محاسباتی و موقت‌اند، نه تضمین سود یا توصیه مالی قطعی. "
    "دفتر سفارش فقط وضعیت فعلی را نشان می‌دهد؛ سفارش‌ها ممکن است حذف شوند، "
    "و کارمزد، لغزش قیمت، نقدشوندگی و تغییر ناگهانی بازار در این محاسبه "
    "کامل لحاظ نشده‌اند. برنامه هیچ معامله‌ای ثبت نمی‌کند."
)

with st.expander("جزئیات و محدودیت‌های داده"):
    st.write(
        "- داده‌ها از endpointهای عمومی صرافی‌ها دریافت می‌شوند؛ "
        "دسترسی به همه صرافی‌ها در همه شبکه‌ها تضمین‌شده نیست."
    )
    st.write(
        "- حجم‌ها گزارش خود صرافی‌ها هستند و ممکن است از نظر بازه زمانی، "
        "کیفیت یا پوشش با یکدیگر تفاوت داشته باشند."
    )
    st.write(
        "- مقایسه قیمت فقط بین بازارهای هم‌نام USDT انجام می‌شود؛ "
        "اختلاف قیمت به‌تنهایی به معنی فرصت آربیتراژ قابل اجرا نیست."
    )
    st.write(
        "- دیوار سفارش بر اساس سطحی با ارزش اسمی حداقل پنج برابر میانه "
        "ارزش سطوح دریافت‌شده تعریف شده است؛ این یک قاعده ابتکاری است."
    )
    st.write(
        "- اگر بعضی صرافی‌ها خطا بدهند، وضعیت همان صرافی در بالای برنامه "
        "نمایش داده می‌شود؛ داده‌ای برای آن صرافی ساخته یا جایگزین نمی‌شود."
    )

st.caption("SpotPilot AI • Public market data only • No auto-trading")
