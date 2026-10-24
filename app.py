import streamlit as st
import pytse_client as tse
import pandas as pd
import json
import os
import time
from datetime import datetime
from pathlib import Path

# ==================== تنظیمات ====================
CACHE_DIR = Path("cache")
CACHE_DIR.mkdir(exist_ok=True)
DATA_FILE = CACHE_DIR / "shares_data.parquet"
META_FILE = CACHE_DIR / "meta.json"
SYMBOLS_FILE = CACHE_DIR / "symbols.json"
CACHE_TTL_HOURS = 12          # کش به مدت ۱۲ ساعت معتبر است
PAGE_SIZE = 50                # تعداد ردیف در هر صفحه
BATCH_PAUSE_SECONDS = 0.8     # وقفه بین دسته‌ها (کاهش فشار روی CPU)

st.set_page_config(page_title="داشبورد بورس", page_icon="📊", layout="wide")
st.title("📊 داشبورد تحلیل بازار بورس ایران")


# ==================== توابع کش ====================

def _read_meta():
    if not META_FILE.exists():
        return {}
    try:
        return json.loads(META_FILE.read_text())
    except Exception:
        return {}


def cache_age_hours():
    m = _read_meta()
    if 'cached_at' not in m:
        return None
    try:
        return (datetime.now() - datetime.fromisoformat(m['cached_at'])).total_seconds() / 3600
    except Exception:
        return None


def load_cache():
    if not DATA_FILE.exists():
        return None
    age = cache_age_hours()
    if age is None or age > CACHE_TTL_HOURS:
        return None
    try:
        return pd.read_parquet(DATA_FILE)
    except Exception:
        return None


def save_cache(df):
    try:
        df.to_parquet(DATA_FILE, index=False)
        META_FILE.write_text(json.dumps({'cached_at': datetime.now().isoformat()}))
    except Exception as e:
        st.warning(f"خطا در ذخیره کش: {e}")


def clear_all_cache():
    for f in [DATA_FILE, META_FILE, SYMBOLS_FILE]:
        if f.exists():
            f.unlink()


# ==================== دریافت داده ====================

def get_symbol_list():
    """دریافت لیست کامل نمادها فقط یک‌بار و کش کردن آن."""
    if SYMBOLS_FILE.exists():
        try:
            return json.loads(SYMBOLS_FILE.read_text())
        except Exception:
            pass
    try:
        symbols_dict = tse.download(symbols="all", write_to_csv=False)
        symbols = list(symbols_dict.keys())
        SYMBOLS_FILE.write_text(json.dumps(symbols))
        return symbols
    except Exception as e:
        st.error(f"خطا در دریافت لیست نمادها: {e}")
        return []


def fetch_shares_for(symbols, progress_bar, already_done, total):
    """دریافت تعداد سهام برای یک لیست از نمادها."""
    rows = []
    for i, sym in enumerate(symbols):
        try:
            t = tse.Ticker(sym)
            shares = getattr(t, 'shares_outstanding', None) or getattr(t, 'total_shares', None)
            rows.append({
                "نماد": sym,
                "نام شرکت": getattr(t, 'title', ''),
                "تعداد سهام": shares,
            })
        except Exception:
            continue
        # به‌روزرسانی نوار پیشرفت هر ۵ مورد (نه هر مورد)
        if i % 5 == 0:
            done = already_done + i + 1
            pct = min(done / total, 1.0)
            progress_bar.progress(pct, text=f"{done}/{total}: {sym}")
    return pd.DataFrame(rows)


# ==================== سایدبار ====================
with st.sidebar:
    st.header("⚙️ تنظیمات")

    age = cache_age_hours()
    if age is not None and age <= CACHE_TTL_HOURS:
        st.success(f"📦 کش معتبر ({age:.1f} ساعت پیش)")
    elif age is not None:
        st.warning(f"⚠️ کش قدیمی ({age:.1f} ساعت)")
    else:
        st.info("ℹ️ کش موجود نیست")

    st.markdown("---")

    batch_size = st.slider(
        "اندازه هر دسته:", min_value=10, max_value=200, value=50, step=10,
        help="دسته‌های کوچک‌تر فشار کمتری به CPU وارد می‌کنند"
    )

    st.markdown("---")

    if st.button("🗑️ پاک کردن کش", use_container_width=True):
        clear_all_cache()
        st.session_state.pop('df', None)
        st.success("کش پاک شد")
        st.rerun()


# ==================== بارگذاری از کش ====================
if 'df' not in st.session_state:
    cached = load_cache()
    st.session_state.df = cached if cached is not None else None

if 'symbols' not in st.session_state:
    st.session_state.symbols = None


# ==================== نمایش داده ====================
df = st.session_state.df

if df is not None and not df.empty:
    st.success(f"✅ {len(df)} شرکت در پایگاه داده")

    # ---- فیلترها ----
    col1, col2 = st.columns([3, 1])
    with col1:
        search = st.text_input("🔍 جستجو:", placeholder="نام نماد یا شرکت...")
    with col2:
        sort_by = st.selectbox("مرتب‌سازی:", ["—", "نماد", "نام شرکت", "تعداد سهام"])

    df_view = df.copy()
    if search:
        mask = (
            df_view['نماد'].astype(str).str.contains(search, case=False, na=False) |
            df_view['نام شرکت'].astype(str).str.contains(search, case=False, na=False)
        )
        df_view = df_view[mask]

    if sort_by != "—" and sort_by in df_view.columns:
        df_view = df_view.sort_values(sort_by, na_position='last')

    # ---- صفحه‌بندی ----
    total = len(df_view)
    total_pages = max(1, (total - 1) // PAGE_SIZE + 1)
    page = st.number_input("صفحه", min_value=1, max_value=total_pages, value=1, step=1)
    start = (page - 1) * PAGE_SIZE
    end = start + PAGE_SIZE

    st.caption(f"نمایش {start+1} تا {min(end, total)} از {total} رکورد")
    st.dataframe(df_view.iloc[start:end], use_container_width=True, hide_index=True)

    csv = df_view.to_csv(index=False).encode('utf-8-sig')
    st.download_button("📥 دانلود CSV", csv, "tsetmc_data.csv", "text/csv")

    st.markdown("---")

    # ---- دریافت دسته‌های بعدی ----
    if st.session_state.symbols is None:
        st.session_state.symbols = get_symbol_list()

    total_symbols = len(st.session_state.symbols) if st.session_state.symbols else 0
    remaining = total_symbols - len(df) if total_symbols else 0

    if remaining > 0:
        st.info(f"📊 {len(df)} از {total_symbols} نماد دریافت شده. {remaining} نماد باقی مانده.")
        if st.button(f"⬇️ دریافت {min(batch_size, remaining)} نماد بعدی", type="primary"):
            next_symbols = st.session_state.symbols[len(df):len(df) + batch_size]
            progress = st.progress(0, text="شروع...")
            new_df = fetch_shares_for(next_symbols, progress, len(df), total_symbols)
            progress.empty()

            if not new_df.empty:
                merged = pd.concat([df, new_df], ignore_index=True)
                save_cache(merged)
                st.session_state.df = merged
                st.success(f"✅ {len(new_df)} نماد جدید اضافه شد")
                time.sleep(0.5)
                st.rerun()
    else:
        st.success("🎉 تمام نمادها دریافت شد!")

    if st.button("🔄 بروزرسانی کامل داده‌ها"):
        st.session_state.df = None
        if DATA_FILE.exists():
            DATA_FILE.unlink()
        if META_FILE.exists():
            META_FILE.unlink()
        st.rerun()

else:
    # ==================== بدون کش: دریافت اولیه ====================
    st.info("📥 هنوز داده‌ای بارگذاری نشده است.")

    st.subheader("🆕 دریافت اولیه")
    st.caption(
        "برای جلوگیری از Throttle، با تعداد کمی نماد شروع کنید. "
        "پس از دریافت موفق، می‌توانید دسته‌های بعدی را اضافه کنید."
    )

    initial_count = st.number_input(
        "تعداد نماد برای شروع:",
        min_value=10, max_value=300, value=50, step=10
    )

    if st.button("🚀 شروع دریافت", type="primary", use_container_width=True):
        with st.spinner("دریافت لیست نمادها..."):
            symbols = get_symbol_list()
        st.session_state.symbols = symbols

        if symbols:
            st.write(f"**{len(symbols)}** نماد در دسترس است.")
            initial = symbols[:initial_count]

            progress_bar = st.progress(0, text="شروع...")
            all_dfs = []

            # پردازش دسته‌ای با وقفه بین دسته‌ها
            for bstart in range(0, len(initial), batch_size):
                bsymbols = initial[bstart:bstart + batch_size]
                bdf = fetch_shares_for(
                    bsymbols, progress_bar, bstart, len(initial)
                )
                all_dfs.append(bdf)
                # وقفه برای کاهش فشار روی CPU
                if bstart + batch_size < len(initial):
                    time.sleep(BATCH_PAUSE_SECONDS)

            if all_dfs:
                df_new = pd.concat(all_dfs, ignore_index=True)
                save_cache(df_new)
                st.session_state.df = df_new
                progress_bar.empty()
                st.success(f"✅ {len(df_new)} شرکت دریافت شد")
                time.sleep(0.5)
                st.rerun()


# ==================== راهنما ====================
st.markdown("---")
with st.expander("💡 چگونه از Throttle جلوگیری کنیم؟"):
    st.markdown("""
    - **از کش استفاده کنید**: داده‌ها در پوشه `cache/` ذخیره می‌شوند و فقط با کلیک شما بروزرسانی می‌شوند.
    - **دسته‌های کوچک**: با ۵۰ نماد شروع کنید و به تدریج اضافه کنید.
    - **وقفه بین دسته‌ها**: بین هر دسته ۰.۸ ثانیه وقفه وجود دارد تا CPU نفس بکشد.
    - **عدم دریافت مجدد**: تا ۱۲ ساعت داده‌ها از کش خوانده می‌شوند.
    """)
