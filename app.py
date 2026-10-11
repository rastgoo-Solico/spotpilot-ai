
import requests
import pandas as pd
import streamlit as st
from datetime import datetime, timezone

# =========================================================
# SpotPilot AI — LiveCoinWatch + LBank Spot Analytics
# Public market data only. No order execution.
# =========================================================

LCW_URL = "https://api.livecoinwatch.com"
LBANK_URL = "https://api.lbank.info/v2"

MIN_VOLUME_BTC = 1000
PAGE_SIZE = 100
MAX_PAGES = 20
TIMEOUT = 20

st.set_page_config(
    page_title="SpotPilot AI",
    page_icon="📊",
    layout="wide",
)

st.title("📊 SpotPilot AI")
st.caption("Live market screening · LBank order book · Trade-flow analysis")
st.markdown(
    "[LiveCoinWatch API](https://www.livecoinwatch.com/tools/api) · "
    "[LBank API documentation](https://www.lbank.com/docs/index.html)"
)

st.info(
    "فقط داده عمومی بازار خوانده می‌شود. هیچ سفارشی ارسال نمی‌شود. "
    "معاملات بزرگ، هویت نهنگ را اثبات نمی‌کنند؛ "
    "جریان معاملات نیز معادل ورود قطعی پول تازه به بازار نیست."
)


# ---------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------

def get_json(url, params=None):
    response = requests.get(
        url,
        params=params or {},
        timeout=TIMEOUT,
        headers={"User-Agent": "SpotPilotAI/1.0"},
    )
    response.raise_for_status()
    return response.json()


def lcw_post(api_key, endpoint, payload):
    response = requests.post(
        f"{LCW_URL}/{endpoint}",
        headers={
            "content-type": "application/json",
            "x-api-key": api_key,
        },
        json=payload,
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    return response.json()


def read_lcw_key():
    try:
        return str(st.secrets["LIVECOINWATCH_API_KEY"]).strip()
    except Exception:
        return ""


# ---------------------------------------------------------
# LiveCoinWatch market screener
# ---------------------------------------------------------

@st.cache_data(ttl=60, show_spinner=False)
def fetch_lcw_market(api_key):
    btc_data = lcw_post(
        api_key,
        "coins/single",
        {
            "currency": "USD",
            "code": "BTC",
            "meta": False,
        },
    )
    btc_usd = float(btc_data["rate"])
    if btc_usd <= 0:
        raise ValueError("قیمت BTC از LiveCoinWatch معتبر نیست.")

    threshold_usd = MIN_VOLUME_BTC * btc_usd
    coins = []
    threshold_reached = False
    pages_checked = 0

    for page in range(MAX_PAGES):
        data = lcw_post(
            api_key,
            "coins/list",
            {
                "currency": "USD",
                "sort": "volume",
                "order": "descending",
                "offset": page * PAGE_SIZE,
                "limit": PAGE_SIZE,
                "meta": True,
            },
        )

        if not isinstance(data, list):
            raise ValueError("ساختار پاسخ LiveCoinWatch غیرمنتظره است.")

        pages_checked += 1
        if not data:
            break

        for coin in data:
            try:
                volume_usd = float(coin.get("volume") or 0)
            except (TypeError, ValueError):
                continue

            if volume_usd < threshold_usd:
                continue

            delta = coin.get("delta") or {}
            try:
                change_pct = (float(delta["day"]) - 1) * 100
            except (KeyError, TypeError, ValueError):
                change_pct = None

            code = str(coin.get("code") or "").strip().upper()
            if not code:
                continue

            coins.append({
                "Symbol": code,
                "Name": coin.get("name") or code,
                "Price USD": float(coin.get("rate") or 0),
                "Change 24h %": change_pct,
                "Reported Volume USD": volume_usd,
                "Volume BTC equivalent": volume_usd / btc_usd,
                "Market Cap USD": float(coin.get("cap") or 0),
                "LBank Pair": code.lower() + "_usdt",
            })

        try:
            last_volume = float(data[-1].get("volume") or 0)
        except (TypeError, ValueError):
            last_volume = 0

        # Results were requested by descending volume.
        if last_volume < threshold_usd:
            threshold_reached = True
            break

        if len(data) < PAGE_SIZE:
            break

    df = pd.DataFrame(coins)
    if not df.empty:
        df = df.sort_values(
            "Change 24h %",
            ascending=False,
            na_position="last",
        ).reset_index(drop=True)

    return {
        "coins": df,
        "btc_usd": btc_usd,
        "threshold_usd": threshold_usd,
        "pages_checked": pages_checked,
        "threshold_reached": threshold_reached,
    }


# ---------------------------------------------------------
# LBank public spot data
# ---------------------------------------------------------

@st.cache_data(ttl=300, show_spinner=False)
def fetch_lbank_pairs():
    result = get_json(f"{LBANK_URL}/currencyPairs.do")
    pairs = result.get("data", [])
    if not isinstance(pairs, list):
        raise ValueError("فهرست جفت‌ارزهای LBank معتبر نیست.")
    return {str(pair).lower() for pair in pairs}


@st.cache_data(ttl=10, show_spinner=False)
def fetch_order_book(symbol, depth_size=100):
    result = get_json(
        f"{LBANK_URL}/depth.do",
        {"symbol": symbol, "size": depth_size},
    )
    data = result.get("data", result)

    bids = data.get("bids") or []
    asks = data.get("asks") or []

    def parse_levels(levels):
        parsed = []
        for level in levels:
            try:
                price = float(level[0])
                qty = float(level[1])
                if price > 0 and qty >= 0:
                    parsed.append({
                        "price": price,
                        "qty": qty,
                        "notional": price * qty,
                    })
            except (TypeError, ValueError, IndexError):
                continue
        return parsed

    return {
        "bids": parse_levels(bids),
        "asks": parse_levels(asks),
        "timestamp": data.get("timestamp"),
    }


@st.cache_data(ttl=10, show_spinner=False)
def fetch_recent_trades(symbol, size=500):
    result = get_json(
        f"{LBANK_URL}/supplement/trades.do",
        {"symbol": symbol, "size": size},
    )

    data = result.get("data", result)
    if not isinstance(data, list):
        raise ValueError("ساختار معاملات اخیر LBank غیرمنتظره است.")

    rows = []
    for trade in data:
        try:
            price = float(trade.get("price") or 0)
            qty = float(trade.get("qty") or 0)
            quote_qty = float(trade.get("quoteQty") or price * qty)

            if quote_qty <= 0:
                continue

            # LBank: if buyer is maker, seller was the taker.
            # Aggressive sell => seller crossed the spread.
            buyer_maker = trade.get("isBuyerMaker")

            if buyer_maker is True:
                side = "Sell"
            elif buyer_maker is False:
                side = "Buy"
            else:
                side = "Unknown"

            rows.append({
                "Time": trade.get("time"),
                "Price": price,
                "Quantity": qty,
                "Value USDT": quote_qty,
                "Aggressor": side,
                "Trade ID": trade.get("id"),
            })
        except (TypeError, ValueError):
            continue

    return pd.DataFrame(rows)


# ---------------------------------------------------------
# Order-book analytics
# ---------------------------------------------------------

def analyze_order_book(book, levels=20, wall_multiplier=5.0):
    bids = book["bids"][:levels]
    asks = book["asks"][:levels]

    if not bids or not asks:
        raise ValueError("دفتر سفارش خرید یا فروش خالی است.")

    best_bid = bids[0]["price"]
    best_ask = asks[0]["price"]
    mid = (best_bid + best_ask) / 2

    spread = best_ask - best_bid
    spread_pct = spread / mid * 100 if mid > 0 else 0

    bid_value = sum(level["notional"] for level in bids)
    ask_value = sum(level["notional"] for level in asks)
    total = bid_value + ask_value

    imbalance_pct = (
        (bid_value - ask_value) / total * 100
        if total > 0 else 0
    )

    all_levels = bids + asks
    median_level_value = (
        sorted(x["notional"] for x in all_levels)[len(all_levels) // 2]
        if all_levels else 0
    )
    wall_threshold = median_level_value * wall_multiplier

    walls = []
    for side, side_levels in (("Bid wall", bids), ("Ask wall", asks)):
        for level in side_levels:
            if level["notional"] >= wall_threshold and level["notional"] > 0:
                distance = (
                    (level["price"] - mid) / mid * 100
                    if mid else 0
                )
                walls.append({
                    "Side": side,
                    "Price": level["price"],
                    "Quantity": level["qty"],
                    "Value USDT": level["notional"],
                    "Distance from mid %": distance,
                })

    walls.sort(key=lambda row: row["Value USDT"], reverse=True)

    if imbalance_pct >= 15:
        pressure = "فشار سفارش‌های خرید بیشتر"
    elif imbalance_pct <= -15:
        pressure = "فشار سفارش‌های فروش بیشتر"
    else:
        pressure = "تعادل نسبی دفتر سفارش"

    return {
        "best_bid": best_bid,
        "best_ask": best_ask,
        "mid": mid,
        "spread": spread,
        "spread_pct": spread_pct,
        "bid_value": bid_value,
        "ask_value": ask_value,
        "imbalance_pct": imbalance_pct,
        "pressure": pressure,
        "walls": walls,
    }


# ---------------------------------------------------------
# Recent-trade flow and large-trade flags
# ---------------------------------------------------------

def analyze_trades(trades, whale_threshold, median_multiplier=5.0):
    if trades.empty:
        return {
            "trades": trades,
            "buy_value": 0,
            "sell_value": 0,
            "net_flow": 0,
            "buy_share": None,
            "unknown_count": 0,
            "large_trades": pd.DataFrame(),
            "median_trade": 0,
        }

    buys = trades[trades["Aggressor"] == "Buy"]
    sells = trades[trades["Aggressor"] == "Sell"]
    unknown = trades[trades["Aggressor"] == "Unknown"]

    buy_value = float(buys["Value USDT"].sum())
    sell_value = float(sells["Value USDT"].sum())
    known_total = buy_value + sell_value

    median_trade = float(trades["Value USDT"].median())
    adaptive_threshold = max(
        float(whale_threshold),
        median_trade * float(median_multiplier),
    )

    large_trades = trades[
        (trades["Value USDT"] >= adaptive_threshold)
        & (trades["Aggressor"] != "Unknown")
    ].copy()

    if not large_trades.empty:
        large_trades["Large trade threshold USDT"] = adaptive_threshold

    return {
        "trades": trades,
        "buy_value": buy_value,
        "sell_value": sell_value,
        "net_flow": buy_value - sell_value,
        "buy_share": buy_value / known_total * 100 if known_total else None,
        "unknown_count": len(unknown),
        "large_trades": large_trades.sort_values(
            "Value USDT", ascending=False
        ),
        "median_trade": median_trade,
        "adaptive_threshold": adaptive_threshold,
    }


def flow_summary(net_flow, imbalance_pct):
    if net_flow > 0 and imbalance_pct > 0:
        return (
            "خرید تهاجمی و دفتر سفارش هر دو متمایل به خریدند؛ "
            "این هم‌جهتی نیازمند تأیید روند قیمت است."
        )
    if net_flow < 0 and imbalance_pct < 0:
        return (
            "فروش تهاجمی و دفتر سفارش هر دو متمایل به فروشند؛ "
            "ریسک فشار نزولی بیشتر است."
        )
    return (
        "جریان معاملات و دفتر سفارش هم‌جهت نیستند یا خنثی‌اند؛ "
        "برای نتیجه‌گیری عجله نکن."
    )


# ---------------------------------------------------------
# UI
# ---------------------------------------------------------

api_key = read_lcw_key()
if not api_key:
    st.error(
        "کلید LiveCoinWatch تنظیم نشده است. "
        "در Streamlit Cloud → Settings → Secrets این خط را اضافه کن:"
    )
    st.code('LIVECOINWATCH_API_KEY = "YOUR_API_KEY"')
    st.stop()

with st.sidebar:
    st.header("تنظیمات تحلیل")
    st.caption("همه اعداد بازار از API دریافت می‌شوند.")

    whale_threshold = st.number_input(
        "حداقل ارزش معامله بزرگ (USDT)",
        min_value=100.0,
        max_value=10_000_000.0,
        value=10_000.0,
        step=1000.0,
    )

    levels = st.select_slider(
        "تعداد سطوح دفتر سفارش",
        options=[10, 20, 50, 100, 200],
        value=20,
    )

    wall_multiplier = st.slider(
        "آستانه دیوار سفارش نسبت به میانه سطح‌ها",
        min_value=2.0,
        max_value=20.0,
        value=5.0,
        step=0.5,
    )

    refresh = st.button(
        "🔄 تازه‌سازی داده‌ها",
        use_container_width=True,
    )

if refresh:
    st.cache_data.clear()
    st.rerun()

try:
    with st.spinner("دریافت فیلتر زنده LiveCoinWatch..."):
        market = fetch_lcw_market(api_key)
        coins = market["coins"]
except Exception as exc:
    st.error(f"خطا در LiveCoinWatch: {exc}")
    st.stop()

if coins.empty:
    st.warning(
        "در محدوده بررسی‌شده ارز واجد شرایط پیدا نشد. "
        "ممکن است محدودیت صفحات یا پاسخ API روی نتیجه اثر گذاشته باشد."
    )
    st.stop()

try:
    with st.spinner("دریافت جفت‌ارزهای قابل معامله در LBank..."):
        lbank_pairs = fetch_lbank_pairs()
except Exception as exc:
    st.error(f"خطا در دریافت فهرست جفت‌ارزهای LBank: {exc}")
    st.stop()

coins["Available on LBank"] = coins["LBank Pair"].isin(lbank_pairs)
eligible = coins[coins["Available on LBank"]].copy()

col1, col2, col3, col4 = st.columns(4)
col1.metric("ارزهای فیلترشده", f"{len(coins):,}")
col2.metric("جفت‌ارزهای موجود در LBank", f"{len(eligible):,}")
col3.metric("قیمت BTC", f"${market['btc_usd']:,.2f}")
col4.metric("آستانه حجم گزارش‌شده", f"{MIN_VOLUME_BTC:,} BTC")

st.caption(
    f"آخرین دریافت موفق: "
    f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')} "
    f"| صفحات LiveCoinWatch: {market['pages_checked']}"
)

if not market["threshold_reached"]:
    st.warning(
        "محدوده بررسی LiveCoinWatch به پایان رسیده یا داده کافی نبود؛ "
        "ممکن است فهرست کامل ارزهای واجد شرایط پوشش داده نشده باشد."
    )

st.subheader("۱. فیلتر بازار")
st.dataframe(
    eligible[
        [
            "Symbol",
            "Name",
            "Price USD",
            "Change 24h %",
            "Reported Volume USD",
            "Volume BTC equivalent",
        ]
    ],
    use_container_width=True,
    hide_index=True,
    column_config={
        "Price USD": st.column_config.NumberColumn(format="$%.8f"),
        "Change 24h %": st.column_config.NumberColumn(format="%.2f%%"),
        "Reported Volume USD": st.column_config.NumberColumn(format="$%.0f"),
        "Volume BTC equivalent": st.column_config.NumberColumn(format="%.2f"),
    },
)

if eligible.empty:
    st.warning("هیچ ارز فیلترشده‌ای جفت‌ارز USDT در LBank ندارد.")
    st.stop()

symbols = eligible["LBank Pair"].tolist()
selected_pair = st.selectbox(
    "انتخاب جفت‌ارز برای تحلیل LBank",
    symbols,
    format_func=lambda pair: pair.upper(),
)

selected_coin = eligible[eligible["LBank Pair"] == selected_pair].iloc[0]
st.subheader(
    f"۲. تحلیل دفتر سفارش — {selected_pair.upper()}"
)

try:
    with st.spinner("دریافت دفتر سفارش و معاملات اخیر LBank..."):
        book = fetch_order_book(selected_pair, levels)
        trades = fetch_recent_trades(selected_pair, 500)

    book_stats = analyze_order_book(
        book,
        levels=levels,
        wall_multiplier=wall_multiplier,
    )
    trade_stats = analyze_trades(
        trades,
        whale_threshold=whale_threshold,
    )
except Exception as exc:
    st.error(
        f"دریافت یا تحلیل داده LBank برای {selected_pair} ناموفق بود: {exc}"
    )
    st.caption(
        "این خطا ممکن است به محدودیت API، جفت‌ارز ناموجود یا "
        "ساختار متفاوت پاسخ مربوط باشد."
    )
    st.stop()

a, b, c, d = st.columns(4)
a.metric("بهترین خرید (Bid)", f"{book_stats['best_bid']:.8g} USDT")
b.metric("بهترین فروش (Ask)", f"{book_stats['best_ask']:.8g} USDT")
c.metric("Spread", f"{book_stats['spread_pct']:.4f}%")
d.metric("عدم تعادل دفتر سفارش", f"{book_stats['imbalance_pct']:+.2f}%")

st.write(f"**نتیجه دفتر سفارش:** {book_stats['pressure']}")

left, right = st.columns(2)
with left:
    st.metric(
        f"ارزش سفارش‌های خرید در {levels} سطح",
        f"${book_stats['bid_value']:,.2f}",
    )
with right:
    st.metric(
        f"ارزش سفارش‌های فروش در {levels} سطح",
        f"${book_stats['ask_value']:,.2f}",
    )

st.caption(
    "عدم تعادل = (ارزش سفارش‌های خرید − ارزش سفارش‌های فروش) "
    "/ مجموع ارزش دو طرف × ۱۰۰. سفارش‌ها ممکن است لغو شوند؛ "
    "این شاخص تضمین‌کننده حرکت قیمت نیست."
)

st.subheader("۳. دیوارهای بزرگ سفارش")

walls = pd.DataFrame(book_stats["walls"])
if walls.empty:
    st.info(
        "با آستانه فعلی، دیوار سفارش برجسته‌ای در سطوح بررسی‌شده پیدا نشد."
    )
else:
    st.dataframe(
        walls.head(20),
        use_container_width=True,
        hide_index=True,
        column_config={
            "Price": st.column_config.NumberColumn(format="%.8g"),
            "Quantity": st.column_config.NumberColumn(format="%.8g"),
            "Value USDT": st.column_config.NumberColumn(format="$%.2f"),
            "Distance from mid %": st.column_config.NumberColumn(format="%.3f%%"),
        },
    )

st.subheader("۴. جریان خریدوفروش در معاملات اخیر")

if trades.empty:
    st.warning("LBank معامله‌ای با داده قابل استفاده برنگرداند.")
else:
    x, y, z = st.columns(3)
    x.metric("خرید تهاجمی در نمونه", f"${trade_stats['buy_value']:,.2f}")
    y.metric("فروش تهاجمی در نمونه", f"${trade_stats['sell_value']:,.2f}")
    z.metric(
        "جریان خالص نمونه",
        f"${trade_stats['net_flow']:+,.2f}",
    )

    if trade_stats["buy_share"] is not None:
        st.metric(
            "سهم خرید از ارزش معاملات با جهت مشخص",
            f"{trade_stats['buy_share']:.2f}%",
        )

    st.caption(
        f"تعداد معاملات دریافت‌شده: {len(trades)}؛ "
        f"تعداد جهت‌های نامشخص: {trade_stats['unknown_count']}. "
        "این محاسبه روی نمونه آخرین معاملات برگشتی API است، "
        "نه لزوماً کل یک ساعت."
    )

    st.write(
        "**جمع‌بندی جریان و دفتر سفارش:** "
        + flow_summary(
            trade_stats["net_flow"],
            book_stats["imbalance_pct"],
        )
    )

    st.subheader("۵. معاملات بزرگ یا غیرعادی")

    large_trades = trade_stats["large_trades"]
    if large_trades.empty:
        st.info(
            "در نمونه فعلی معامله‌ای از آستانه انتخاب‌شده عبور نکرده است."
        )
    else:
        st.caption(
            f"آستانه علامت‌گذاری: حداقل "
            f"${trade_stats['adaptive_threshold']:,.2f} "
            "و حداقل چند برابر میانه ارزش معامله؛ "
            "این‌ها معاملات بزرگ علامت‌گذاری‌شده‌اند، نه نهنگ‌های شناسایی‌شده."
        )
        st.dataframe(
            large_trades[
                ["Time", "Price", "Quantity", "Value USDT", "Aggressor", "Trade ID"]
            ],
            use_container_width=True,
            hide_index=True,
            column_config={
                "Price": st.column_config.NumberColumn(format="%.8g"),
                "Quantity": st.column_config.NumberColumn(format="%.8g"),
                "Value USDT": st.column_config.NumberColumn(format="$%.2f"),
            },
        )

    st.subheader("۶. معاملات اخیر")
    st.dataframe(
        trades.sort_values("Time", ascending=False, na_position="last"),
        use_container_width=True,
        hide_index=True,
        column_config={
            "Price": st.column_config.NumberColumn(format="%.8g"),
            "Quantity": st.column_config.NumberColumn(format="%.8g"),
            "Value USDT": st.column_config.NumberColumn(format="$%.2f"),
        },
    )

st.divider()
st.subheader("محدودیت‌های تحلیل")
st.markdown(
    """
    - حجم LiveCoinWatch حجم گزارش‌شده بازار است و لزوماً حجم صرفاً اسپات نیست.
    - آستانه ۱۰۰۰ BTC با قیمت جاری BTC به USD تبدیل می‌شود.
    - فیلتر LiveCoinWatch حداکثر تعداد صفحات مشخص‌شده را بررسی می‌کند.
    - جریان خرید/فروش فقط از نمونه معاملات اخیر LBank محاسبه می‌شود؛
      برای تاریخچه پیوسته باید داده‌ها در طول زمان ذخیره شوند.
    - معامله بزرگ به‌تنهایی اثبات حضور نهنگ نیست.
    - دفتر سفارش یک تصویر لحظه‌ای است و سفارش‌ها ممکن است لغو یا جابه‌جا شوند.
    - این داشبورد توصیه مالی قطعی نمی‌دهد و سفارش ثبت نمی‌کند.
    """
)
