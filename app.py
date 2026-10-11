
import streamlit as st
import pandas as pd
import plotly.express as px

st.set_page_config(
    page_title="SpotPilot AI",
    page_icon="📊",
    layout="wide",
)

st.title("SpotPilot AI")
st.caption("Spot market scanner | Demo data — not live")

st.warning(
    "نسخه آزمایشی: اعداد زیر ساختگی‌اند و نباید "
    "برای تصمیم‌گیری معاملاتی استفاده شوند."
)

min_volume_btc = st.sidebar.number_input(
    "حداقل حجم اسپات ۲۴ ساعته (BTC)",
    min_value=1.0,
    value=1000.0,
    step=100.0,
)

# Sample data ONLY. Replace with verified live API data.
tokens = pd.DataFrame([
    {"Token": "BTC", "Volume BTC": 18000, "Change 24h %": 2.4},
    {"Token": "ETH", "Volume BTC": 9500, "Change 24h %": 1.2},
    {"Token": "SOL", "Volume BTC": 2400, "Change 24h %": -1.8},
    {"Token": "XRP", "Volume BTC": 780, "Change 24h %": 5.7},
    {"Token": "DEMO-A", "Volume BTC": 1500, "Change 24h %": 8.6},
])

# First: apply the aggregate volume threshold.
eligible = tokens[
    tokens["Volume BTC"] >= min_volume_btc
].copy()

# Then: rank eligible tokens by 24h percentage gain, descending.
eligible = eligible.sort_values(
    by="Change 24h %",
    ascending=False,
).reset_index(drop=True)

eligible.insert(0, "Rank", range(1, len(eligible) + 1))

st.subheader("توکن‌های واجد شرایط — مرتب‌شده بر اساس بیشترین رشد")

st.dataframe(
    eligible,
    use_container_width=True,
    hide_index=True,
)

if not eligible.empty:
    fig = px.bar(
        eligible,
        x="Token",
        y="Change 24h %",
        title="24h Percentage Change — Descending",
    )
    fig.update_layout(xaxis={"categoryorder": "total descending"})
    st.plotly_chart(fig, use_container_width=True)

st.info(
    "مرحله بعد: اتصال API واقعی LiveCoinWatch، "
    "اعتبارسنجی حجم تجمیعی اسپات و تحلیل چند صرافی."
)
