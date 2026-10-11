
import requests
import pandas as pd
import streamlit as st
from datetime import datetime, timezone

# =========================================================
# SpotPilot AI — Live Market Screener
# Data provider: LiveCoinWatch
# =========================================================

API_URL = "https://api.livecoinwatch.com"
MIN_VOLUME_BTC = 1000
PAGE_SIZE = 100
MAX_PAGES = 20
REQUEST_TIMEOUT = 25

st.set_page_config(
    page_title="SpotPilot AI",
    page_icon="📊",
    layout="wide",
)

st.title("📊 SpotPilot AI")
st.caption("Live crypto market screener | No automatic trading")

st.info(
    "این داشبورد از داده‌های API استفاده می‌کند. "
    "حجم گزارش‌شدهٔ بازار لزوماً به معنی حجم تأییدشدهٔ اسپات نیست. "
    "هیچ سفارش خرید یا فروشی به‌صورت خودکار ارسال نمی‌شود."
)


def get_api_key():
    """Read API key from Streamlit Secrets."""
    try:
        return st.secrets["LIVECOINWATCH_API_KEY"].strip()
    except Exception:
        return ""


def api_post(endpoint, api_key, payload=None):
    """Send a request to LiveCoinWatch and validate the response."""
    headers = {
        "content-type": "application/json",
        "accept": "application/json",
        "x-api-key": api_key,
    }

    response = requests.post(
        f"{API_URL}/{endpoint}",
        headers=headers,
        json=payload or {},
        timeout=REQUEST_TIMEOUT,
    )

    if not response.ok:
        message = response.text[:500]
        raise RuntimeError(
            f"LiveCoinWatch returned HTTP {response.status_code}: "
            f"{message}"
        )

    try:
        return response.json()
    except ValueError as exc:
        raise RuntimeError(
            "پاسخ API در قالب JSON معتبر نبود."
        ) from exc


def get_btc_usd_price(api_key):
    """Get the current BTC price in USD."""
    data = api_post(
        "coins/single",
        api_key,
        {
            "currency": "USD",
            "code": "BTC",
            "meta": False,
        },
    )

    price = float(data.get("rate") or 0)

    if price <= 0:
        raise RuntimeError(
            "قیمت معتبر BTC از API دریافت نشد."
        )

    return price


def fetch_market_data(api_key):
    """
    Fetch coins sorted by reported 24h volume in USD.
    Convert the 1000 BTC threshold to USD using the live BTC price.
    Stop once sorted results fall below the threshold.
    """
    btc_usd = get_btc_usd_price(api_key)
    minimum_volume_usd = MIN_VOLUME_BTC * btc_usd

    all_coins = []
    pages_checked = 0
    reached_threshold = False

    for page in range(MAX_PAGES):
        payload = {
            "currency": "USD",
            "sort": "volume",
            "order": "descending",
            "offset": page * PAGE_SIZE,
            "limit": PAGE_SIZE,
            "meta": True,
        }

        result = api_post("coins/list", api_key, payload)

        if not isinstance(result, list):
            raise RuntimeError(
                "ساختار پاسخ /coins/list مطابق انتظار نبود."
            )

        pages_checked += 1

        if not result:
            break

        # Keep coins meeting the reported 24h volume threshold.
        for coin in result:
            try:
                volume_usd = float(coin.get("volume") or 0)
            except (TypeError, ValueError):
                continue

            if volume_usd >= minimum_volume_usd:
                coin_copy = dict(coin)
                coin_copy["_volume_usd"] = volume_usd
                all_coins.append(coin_copy)

        # Results are requested in descending volume order.
        # If the last coin is below threshold, later pages should
        # not contain qualifying coins.
        try:
            last_volume = float(result[-1].get("volume") or 0)
        except (TypeError, ValueError):
            last_volume = 0

        if last_volume < minimum_volume_usd:
            reached_threshold = True
            break

        if len(result) < PAGE_SIZE:
            break

    return {
        "coins": all_coins,
        "btc_usd": btc_usd,
        "minimum_volume_usd": minimum_volume_usd,
        "pages_checked": pages_checked,
        "reached_threshold": reached_threshold,
    }


def prepare_dataframe(coins, btc_usd):
    """Convert live API response into a readable table."""
    rows = []

    for coin in coins:
        delta = coin.get("delta") or {}

        try:
            rate_usd = float(coin.get("rate") or 0)
        except (TypeError, ValueError):
            rate_usd = 0

        try:
            volume_usd = float(coin.get("_volume_usd") or 0)
        except (TypeError, ValueError):
            volume_usd = 0

        try:
            market_cap_usd = float(coin.get("cap") or 0)
        except (TypeError, ValueError):
            market_cap_usd = 0

        try:
            liquidity_usd = float(coin.get("liquidity") or 0)
        except (TypeError, ValueError):
            liquidity_usd = 0

        try:
            delta_day = float(delta.get("day") or 1)
            change_24h_pct = (delta_day - 1) * 100
        except (TypeError, ValueError):
            change_24h_pct = None

        rows.append({
            "Symbol": coin.get("code", "N/A"),
            "Name": coin.get("name", "N/A"),
            "Price (USD)": rate_usd,
            "24h Change (%)": change_24h_pct,
            "Reported Volume (USD)": volume_usd,
            "Volume (BTC equivalent)": (
                volume_usd / btc_usd if btc_usd > 0 else None
            ),
            "Market Cap (USD)": market_cap_usd,
            "Liquidity (USD)": liquidity_usd,
            "Exchanges": coin.get("exchanges"),
            "Markets": coin.get("markets"),
        })

    df = pd.DataFrame(rows)

    if not df.empty:
        df = df.sort_values(
            by="24h Change (%)",
            ascending=False,
            na_position="last",
        ).reset_index(drop=True)

    return df


# =========================================================
# Sidebar
# =========================================================

st.sidebar.header("Settings")

st.sidebar.metric(
    "Minimum reported volume",
    f"{MIN_VOLUME_BTC:,} BTC",
)

st.sidebar.write(
    "ترتیب نمایش: درصد تغییر ۲۴ ساعته، از بیشترین به کمترین."
)

st.sidebar.caption(
    f"حداکثر بررسی در هر بار اجرا: "
    f"{MAX_PAGES * PAGE_SIZE:,} رکورد."
)

refresh = st.sidebar.button(
    "🔄 دریافت داده‌های زنده",
    type="primary",
    use_container_width=True,
)

# =========================================================
# Main application
# =========================================================

api_key = get_api_key()

if not api_key:
    st.warning(
        "کلید API هنوز در Secrets تنظیم نشده است. "
        "طبق راهنمای پایین صفحه آن را اضافه کن."
    )
    st.stop()

if refresh or "market_result" not in st.session_state:
    with st.spinner(
        "در حال دریافت قیمت BTC و داده‌های بازار از LiveCoinWatch..."
    ):
        try:
            result = fetch_market_data(api_key)
            st.session_state["market_result"] = result
            st.session_state["market_error"] = None
            st.session_state["last_updated"] = datetime.now(
                timezone.utc
            ).strftime("%Y-%m-%d %H:%M:%S UTC")
        except Exception as exc:
            st.session_state["market_error"] = str(exc)

if st.session_state.get("market_error"):
    st.error(
        "دریافت داده ناموفق بود. "
        "پیام خطا برای بررسی:"
    )
    st.code(st.session_state["market_error"])
    st.caption(
        "کلید API را در چت یا در مخزن عمومی GitHub ارسال نکن."
    )
    st.stop()

result = st.session_state.get("market_result")

if not result:
    st.warning("هنوز داده‌ای دریافت نشده است.")
    st.stop()

df = prepare_dataframe(
    result["coins"],
    result["btc_usd"],
)

# Top-level metrics
col1, col2, col3 = st.columns(3)

with col1:
    st.metric(
        "ارزهای واجد شرایط",
        f"{len(df):,}",
    )

with col2:
    st.metric(
        "قیمت فعلی BTC",
        f"${result['btc_usd']:,.2f}",
    )

with col3:
    st.metric(
        "معادل آستانه حجم",
        f"${result['minimum_volume_usd']:,.0f}",
    )

st.caption(
    f"آخرین دریافت موفق: "
    f"{st.session_state.get('last_updated', 'نامشخص')} "
    f"| صفحات بررسی‌شده: {result['pages_checked']}"
)

if not result["reached_threshold"]:
    st.warning(
        "محدودیت تعداد صفحات به پایان رسید یا API داده کافی نداد. "
        "ممکن است بعضی ارزهای واجد شرایط خارج از محدوده بررسی‌شده باشند."
    )

st.subheader("ارزها بر اساس رشد ۲۴ ساعته")

if df.empty:
    st.warning(
        "در محدوده بررسی‌شده، ارزی با حجم گزارش‌شده برابر یا بیشتر "
        "از آستانه ۱۰۰۰ BTC پیدا نشد."
    )
else:
    search = st.text_input(
        "جست‌وجوی نام یا نماد ارز",
        placeholder="مثلاً BTC یا Ethereum",
    )

    display_df = df.copy()

    if search.strip():
        term = search.strip().lower()
        display_df = display_df[
            display_df["Symbol"].astype(str).str.lower().str.contains(
                term, na=False
            )
            | display_df["Name"].astype(str).str.lower().str.contains(
                term, na=False
            )
        ]

    st.dataframe(
        display_df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Price (USD)": st.column_config.NumberColumn(
                "Price (USD)",
                format="$%.8f",
            ),
            "24h Change (%)": st.column_config.NumberColumn(
                "24h Change (%)",
                format="%.2f%%",
            ),
            "Reported Volume (USD)": st.column_config.NumberColumn(
                "Reported Volume (USD)",
                format="$%.0f",
            ),
            "Volume (BTC equivalent)": st.column_config.NumberColumn(
                "Volume (BTC equivalent)",
                format="%.2f BTC",
            ),
            "Market Cap (USD)": st.column_config.NumberColumn(
                "Market Cap (USD)",
                format="$%.0f",
            ),
            "Liquidity (USD)": st.column_config.NumberColumn(
                "Liquidity (USD)",
                format="$%.0f",
            ),
        },
    )

    csv_data = display_df.to_csv(index=False).encode("utf-8-sig")

    st.download_button(
        label="⬇️ دانلود CSV",
        data=csv_data,
        file_name="spotpilot_live_market.csv",
        mime="text/csv",
    )

st.divider()

st.subheader("محدودیت‌های این نسخه")

st.markdown(
    """
    - داده‌ها از API زنده دریافت می‌شوند؛ در صورت خطا، خطا نمایش داده می‌شود.
    - حجم ۲۴ ساعته، حجم گزارش‌شده توسط LiveCoinWatch است؛
      این داده به‌تنهایی ثابت نمی‌کند که تمام حجم مربوط به معاملات اسپات است.
    - معادل BTC با استفاده از قیمت فعلی BTC به USD محاسبه می‌شود.
    - این صفحه هنوز تحلیل مستقل دفتر سفارش چند صرافی،
      شناسایی انتقال‌های آن‌چین نهنگ‌ها یا سیگنال خرید و فروش تأییدشده ندارد.
    - هیچ معامله‌ای اجرا نمی‌شود.
    """
)

st.markdown(
    '[داده‌ها و API از LiveCoinWatch](https://www.livecoinwatch.com/tools/api)'
)
```
