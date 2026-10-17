"""
SpotPilot AI — Read-only on-chain / bot-behavior observer.
This page never signs transactions, requests private keys, or submits orders.
"""
import time
from datetime import datetime, timezone

import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="ردیاب رفتار آن‌چین", page_icon="🔎", layout="wide")

SOLANA_RPC = "https://api.mainnet-beta.solana.com"
MEMPOOL_API = "https://mempool.space/api"
ETHERSCAN_API = "https://api.etherscan.io/v2/api"
XAUT_ETH = "0x68749665FF8D2d112Fa859AA293F07A622782F38"
TIMEOUT = 15


def get_json(url, params=None, payload=None):
    response = requests.post(url, json=payload, timeout=TIMEOUT) if payload else requests.get(url, params=params, timeout=TIMEOUT)
    response.raise_for_status()
    return response.json()


def classify_cadence(timestamps):
    """Heuristic description only; cadence cannot prove that an address is a bot."""
    values = sorted(set(int(x) for x in timestamps if x))
    if len(values) < 4:
        return "داده ناکافی برای ارزیابی تناوب"
    gaps = [b - a for a, b in zip(values, values[1:]) if b > a]
    if not gaps:
        return "تناوب قابل محاسبه نیست"
    median_gap = sorted(gaps)[len(gaps) // 2]
    near_equal = sum(abs(g - median_gap) <= max(2, median_gap * 0.15) for g in gaps) / len(gaps)
    if near_equal >= 0.7 and len(gaps) >= 5:
        return f"تناوب نسبتاً منظم (میانه فاصله {median_gap} ثانیه)؛ احتمال خودکار بودن، نه اثبات"
    if median_gap < 60:
        return f"فعالیت پرتکرار (میانه فاصله {median_gap} ثانیه)؛ انسان یا ربات هر دو ممکن‌اند"
    return f"الگوی زمانی نامنظم (میانه فاصله {median_gap} ثانیه)"


def solana_address_activity(address, limit=25):
    signatures = get_json(SOLANA_RPC, payload={
        "jsonrpc": "2.0", "id": 1, "method": "getSignaturesForAddress",
        "params": [address, {"limit": limit}]
    }).get("result", [])
    rows = []
    for item in signatures[:15]:
        sig = item.get("signature")
        if not sig or item.get("err") is not None:
            continue
        try:
            tx = get_json(SOLANA_RPC, payload={
                "jsonrpc": "2.0", "id": 1, "method": "getTransaction",
                "params": [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}]
            }).get("result")
        except Exception:
            continue
        if not tx:
            continue
        meta = tx.get("meta") or {}
        message = ((tx.get("transaction") or {}).get("message") or {})
        keys = message.get("accountKeys") or []
        key_names = [k.get("pubkey") if isinstance(k, dict) else k for k in keys]
        if address not in key_names:
            continue
        idx = key_names.index(address)
        pre = (meta.get("preBalances") or [])
        post = (meta.get("postBalances") or [])
        delta_sol = ((post[idx] - pre[idx]) / 1e9) if idx < len(pre) and idx < len(post) else None
        block_time = tx.get("blockTime") or item.get("blockTime")
        rows.append({
            "زمان UTC": datetime.fromtimestamp(block_time, timezone.utc).strftime("%Y-%m-%d %H:%M:%S") if block_time else "—",
            "امضای تراکنش": sig,
            "تغییر SOL کیف پول": delta_sol,
            "کارمزد SOL": (meta.get("fee") or 0) / 1e9,
            "وضعیت": "موفق" if meta.get("err") is None else "ناموفق",
            "_timestamp": block_time or 0,
        })
        time.sleep(0.05)
    return rows


def bitcoin_address_activity(address):
    summary = get_json(f"{MEMPOOL_API}/address/{address}")
    txs = get_json(f"{MEMPOOL_API}/address/{address}/txs")
    rows = []
    for tx in txs[:25]:
        status = tx.get("status") or {}
        ts = status.get("block_time")
        vin_total = sum((v.get("prevout") or {}).get("value", 0) for v in tx.get("vin", []))
        vout_total = sum(v.get("value", 0) for v in tx.get("vout", []))
        rows.append({
            "زمان UTC": datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M:%S") if ts else "تأییدنشده/معلق",
            "شناسه تراکنش": tx.get("txid", ""),
            "ورودی کل BTC (تراکنش)": vin_total / 1e8,
            "خروجی کل BTC (تراکنش)": vout_total / 1e8,
            "تأییدشده": bool(status.get("confirmed")),
            "_timestamp": ts or 0,
        })
    return summary, rows


def xaut_address_activity(address, api_key):
    if not api_key:
        raise RuntimeError("برای داده انتقال‌های XAUT در اتریوم، ETHERSCAN_API_KEY را در Streamlit Secrets تنظیم کن.")
    result = get_json(ETHERSCAN_API, params={
        "chainid": "1", "module": "account", "action": "tokentx",
        "contractaddress": XAUT_ETH, "address": address,
        "page": 1, "offset": 25, "sort": "desc", "apikey": api_key,
    })
    if result.get("status") == "0" and not result.get("result"):
        raise RuntimeError(str(result.get("message", "API انتقال‌ها نتیجه‌ای نداد")))
    rows = []
    for tx in result.get("result", []):
        decimals = int(tx.get("tokenDecimal") or 6)
        ts = int(tx.get("timeStamp") or 0)
        rows.append({
            "زمان UTC": datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M:%S") if ts else "—",
            "نوع": "ورودی" if str(tx.get("to", "")).lower() == address.lower() else "خروجی/تعامل",
            "مقدار XAUT": int(tx.get("value") or 0) / (10 ** decimals),
            "از": tx.get("from", ""),
            "به": tx.get("to", ""),
            "تراکنش": tx.get("hash", ""),
            "_timestamp": ts,
        })
    return rows


st.title("🔎 ردیاب رفتار آن‌چین و فعالیت‌های مشکوک به ربات")
st.caption("فقط خواندنی • بدون اتصال کیف پول • بدون کلید خصوصی • بدون ثبت سفارش")
st.warning(
    "این صفحه ربات‌ها را با قطعیت شناسایی نمی‌کند و قصد آینده آن‌ها را نمی‌خواند. "
    "الگوهای زمانی و جریان تراکنش فقط قرینه‌اند؛ صرافی‌های متمرکز نیز معمولاً هویت سفارش‌دهنده را در زنجیره نشان نمی‌دهند."
)

tab1, tab2, tab3 = st.tabs(["Solana / SOL", "Bitcoin / BTC", "Tether Gold / XAUT"])

with tab1:
    st.subheader("فعالیت یک آدرس روی شبکه سولانا")
    st.write("برای شروع باید آدرس کیف پول یا حسابی را وارد کنی که می‌خواهی رصد شود. این نسخه کل شبکه را برای کشف خودکار همه ربات‌ها پیمایش نمی‌کند.")
    sol_addr = st.text_input("آدرس عمومی سولانا", key="sol_addr", placeholder="آدرس عمومی (نه عبارت بازیابی)")
    sol_limit = st.slider("تعداد امضاهای اخیر برای دریافت", 5, 25, 15, key="sol_limit")
    if st.button("بررسی فعالیت سولانا", key="sol_run"):
        if not sol_addr.strip():
            st.info("یک آدرس عمومی وارد کن.")
        else:
            try:
                with st.spinner("در حال خواندن داده عمومی شبکه..."):
                    rows = solana_address_activity(sol_addr.strip(), sol_limit)
                if rows:
                    df = pd.DataFrame(rows)
                    st.metric("تراکنش‌های موفق بررسی‌شده", len(df))
                    st.info(classify_cadence(df["_timestamp"].tolist()))
                    st.dataframe(df.drop(columns=["_timestamp"]), use_container_width=True, hide_index=True)
                    st.caption("تغییر SOL، تغییر موجودی بومی SOL در همان تراکنش است؛ تغییر SPL-tokenها و تعاملات DEX در این نمای اولیه به‌طور کامل محاسبه نمی‌شود.")
                else:
                    st.info("تراکنش قابل‌تحلیل پیدا نشد؛ آدرس، محدودیت RPC یا داده‌های اخیر را بررسی کن.")
            except Exception as exc:
                st.error(f"دریافت داده سولانا ناموفق بود: {exc}")

with tab2:
    st.subheader("فعالیت یک آدرس بیت‌کوین")
    st.write("از API عمومی mempool.space برای خلاصه آدرس و تراکنش‌های اخیر استفاده می‌شود.")
    btc_addr = st.text_input("آدرس عمومی بیت‌کوین", key="btc_addr", placeholder="آدرس عمومی BTC")
    if st.button("بررسی فعالیت بیت‌کوین", key="btc_run"):
        if not btc_addr.strip():
            st.info("یک آدرس عمومی وارد کن.")
        else:
            try:
                with st.spinner("در حال خواندن تراکنش‌های عمومی بیت‌کوین..."):
                    summary, rows = bitcoin_address_activity(btc_addr.strip())
                c1, c2, c3 = st.columns(3)
                c1.metric("کل تراکنش‌های تأییدشده", (summary.get("chain_stats") or {}).get("tx_count", 0))
                c2.metric("ورودی تاریخی BTC", f"{((summary.get('chain_stats') or {}).get('funded_txo_sum', 0) / 1e8):,.8f}")
                c3.metric("خروجی تاریخی BTC", f"{((summary.get('chain_stats') or {}).get('spent_txo_sum', 0) / 1e8):,.8f}")
                if rows:
                    df = pd.DataFrame(rows)
                    st.info(classify_cadence(df["_timestamp"].tolist()))
                    st.dataframe(df.drop(columns=["_timestamp"]), use_container_width=True, hide_index=True)
                    st.caption("ورودی/خروجی جدول مربوط به کل تراکنش است، نه لزوماً خالص جریان همین آدرس؛ محاسبه دقیق نیازمند تفکیک UTXOهای متعلق به آدرس است.")
                else:
                    st.info("تراکنشی برای این آدرس در پاسخ فعلی موجود نیست.")
            except Exception as exc:
                st.error(f"دریافت داده بیت‌کوین ناموفق بود: {exc}")

with tab3:
    st.subheader("انتقال‌های توکن Tether Gold (XAUT) روی اتریوم")
    st.write("XAUT روی شبکه‌های مختلف منتشر شده است؛ این پنل فعلاً قرارداد رسمی اتریوم را بررسی می‌کند، نه تمام شبکه‌ها یا معاملات صرافی‌ها.")
    st.code(XAUT_ETH, language="text")
    xaut_addr = st.text_input("آدرس عمومی اتریوم برای رصد XAUT", key="xaut_addr", placeholder="0x...")
    if st.button("بررسی انتقال‌های XAUT", key="xaut_run"):
        if not xaut_addr.strip():
            st.info("یک آدرس عمومی اتریوم وارد کن.")
        else:
            try:
                api_key = str(st.secrets.get("ETHERSCAN_API_KEY", "")).strip()
                with st.spinner("در حال دریافت انتقال‌های توکن..."):
                    rows = xaut_address_activity(xaut_addr.strip(), api_key)
                if rows:
                    df = pd.DataFrame(rows)
                    st.metric("انتقال‌های اخیر", len(df))
                    st.info(classify_cadence(df["_timestamp"].tolist()))
                    st.dataframe(df.drop(columns=["_timestamp"]), use_container_width=True, hide_index=True)
                else:
                    st.info("انتقال XAUT برای این آدرس در نتیجه فعلی پیدا نشد.")
            except Exception as exc:
                st.error(f"دریافت داده XAUT ناموفق بود: {exc}")

st.divider()
st.subheader("چطور از این داده‌ها رفتار احتمالی ربات را استنباط کنیم؟")
st.markdown(
    "- **تناوب زمانی:** فاصله‌های منظم و پرتکرار می‌توانند با اجرای خودکار سازگار باشند، اما اثبات ربات بودن نیستند.\n"
    "- **انباشت/توزیع:** به تاریخچه کامل موجودی و انتقال‌های ورودی/خروجی نیاز دارد؛ صرف تعداد تراکنش‌ها کافی نیست.\n"
    "- **آربیتراژ و بازارسازی:** معمولاً نیازمند تطبیق زمان‌بندی معاملات در چند بازار و بررسی اختلاف قیمت پس از کارمزد است.\n"
    "- **قصد خریدوفروش آینده:** از داده‌های عمومی قابل مشاهده نیست؛ فقط می‌توان سناریوهای احتمالی و سطح اطمینان ارائه کرد."
)
st.caption("نسخه اولیه پژوهشی است. پیش از نتیجه‌گیری درباره یک آدرس، تراکنش‌ها و برچسب‌های اکتشافی را دستی بررسی کن.")
