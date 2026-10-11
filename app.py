
import streamlit as st
import pandas as pd
import plotly.express as px

st.set_page_config(
    page_title="SpotPilot AI",
    page_icon="📊",
    layout="wide",
)

st.title("📊 SpotPilot AI")
st.caption(
    "Spot Market Scanner | Multi-Exchange Analysis | "
    "No Automatic Trading"
)

st.warning(
    "DEMO MODE — داده‌های این نسخه آزمایشی هستند، "
    "قیمت و حجم زنده نیستند و سیگنال‌ها برای معامله واقعی مناسب نیستند."
)

# -----------------------------
# Sidebar settings
# -----------------------------
st.sidebar.header("Scanner Settings")

min_volume_btc = st.sidebar.number_input(
    "Minimum 24h aggregate spot volume (BTC)",
    min_value=1.0,
    value=1000.0,
    step=100.0,
)

selected_exchanges = st.sidebar.multiselect(
    "Exchanges to analyze",
    ["Binance", "OKX", "Bybit", "Coinbase"],
    default=["Binance", "OKX", "Bybit", "Coinbase"],
)

st.sidebar.caption(
    "Exchange coverage depends on available market data."
)

# -----------------------------
# Clearly simulated demo data
# Replace with verified API data later.
# -----------------------------
tokens = pd.DataFrame([
    {
        "Token": "BTC",
        "Price USD": 68000.0,
        "24h Volume BTC": 18000.0,
        "Change 24h %": 2.4,
        "Order Flow": 0.68,
        "Liquidity Score": 82,
        "Whale Activity": "Accumulation candidate",
    },
    {
        "Token": "ETH",
        "Price USD": 3200.0,
        "24h Volume BTC": 9500.0,
        "Change 24h %": 1.2,
        "Order Flow": 0.31,
        "Liquidity Score": 76,
        "Whale Activity": "Mixed",
    },
    {
        "Token": "SOL",
        "Price USD": 155.0,
        "24h Volume BTC": 2400.0,
        "Change 24h %": -1.8,
        "Order Flow": -0.44,
        "Liquidity Score": 63,
        "Whale Activity": "Distribution candidate",
    },
    {
        "Token": "XRP",
        "Price USD": 0.62,
        "24h Volume BTC": 780.0,
        "Change 24h %": -0.4,
        "Order Flow": -0.12,
        "Liquidity Score": 45,
        "Whale Activity": "Insufficient evidence",
    },
])

# Demo data only: not a real trading signal.
eligible = tokens[
    tokens["24h Volume BTC"] >= min_volume_btc
].copy()

def demo_signal(row):
    if (
        row["Order Flow"] > 0.5
        and row["Change 24h %"] > 0
        and row["Liquidity Score"] >= 70
    ):
        return "BUY WATCH (DEMO)"

    if (
        row["Order Flow"] < -0.3
        and row["Change 24h %"] < 0
    ):
        return "SELL WATCH (DEMO)"

    return "NO TRADE (DEMO)"

eligible["Demo Signal"] = eligible.apply(
    demo_signal, axis=1
)

# -----------------------------
# Overview
# -----------------------------
c1, c2, c3, c4 = st.columns(4)

c1.metric("Eligible Tokens", len(eligible))
c2.metric("Volume Threshold", f"{min_volume_btc:,.0f} BTC")
c3.metric("Exchanges Selected", len(selected_exchanges))
c4.metric("Trading Mode", "Disabled")

st.divider()

# -----------------------------
# Scanner results
# -----------------------------
st.subheader("🔎 Eligible Spot Tokens")

if eligible.empty:
    st.info("No demo tokens meet the selected volume threshold.")
else:
    st.dataframe(
        eligible[
            [
                "Token",
                "Price USD",
                "24h Volume BTC",
                "Change 24h %",
                "Order Flow",
                "Liquidity Score",
                "Whale Activity",
                "Demo Signal",
            ]
        ],
        use_container_width=True,
        hide_index=True,
    )

# -----------------------------
# Volume chart
# -----------------------------
st.subheader("📈 24h Aggregate Volume — Demo Data")

fig = px.bar(
    eligible,
    x="Token",
    y="24h Volume BTC",
    title="Simulated Volume by Token",
)

fig.add_hline(
    y=min_volume_btc,
    line_dash="dash",
    annotation_text="Minimum volume threshold",
)

st.plotly_chart(fig, use_container_width=True)

# -----------------------------
# Multi-exchange analysis placeholder
# -----------------------------
st.subheader("🌐 Multi-Exchange Analysis")

if selected_exchanges:
    st.write("Selected exchanges:", ", ".join(selected_exchanges))
    st.info(
        "Live order books, trade feeds, spreads and exchange-specific "
        "liquidity will be added in the next development stage."
    )
else:
    st.warning("Select at least one exchange.")

# -----------------------------
# Whale and liquidity sections
# -----------------------------
left, right = st.columns(2)

with left:
    st.subheader("🐋 Whale Activity")
    st.write(
        "Next stage: verified large trades, tagged-wallet transfers, "
        "and exchange inflow/outflow data."
    )
    st.caption(
        "A large transfer alone does not prove buying or selling."
    )

with right:
    st.subheader("💧 Liquidity Analysis")
    st.write(
        "Next stage: order-book depth, bid/ask imbalance, "
        "liquidity clusters and potential sweep zones."
    )
    st.caption(
        "Visible order books cannot reveal every hidden order or stop."
    )

st.divider()

st.caption(
    "SpotPilot AI — Research dashboard. "
    "Demo values are illustrative, not investment advice."
)
