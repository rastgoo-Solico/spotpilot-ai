import streamlit as st
import pytse_client as tse
import pandas as pd
import json
import time
from datetime import datetime
from pathlib import Path

# ==================== تنظیمات ====================
CACHE_DIR = Path("cache")
CACHE_DIR.mkdir(exist_ok=True)
DATA_FILE = CACHE_DIR / "shares_data.parquet"
META_FILE = CACHE_DIR / "meta.json"
SYMBOLS_FILE = CACHE_DIR / "symbols.json"
CACHE_TTL_HOURS = 12
PAGE_SIZE = 50
BATCH_PAUSE_SECONDS = 0.8

st.set_page_config(page_title="داشبورد بورس", page_icon="📊", layout="wide")
st.title("📊 داشبورد تحلیل بازار بورس ایران")


# ==================== توابع کمکی ====================

def safe_attr_extract(obj, candidate_names, default='نامشخص'):
    """
    تلاش می‌کند مقدار یک ویژگی را با چند نام مختلف بخواند.
    حتی اگر ویژگی یک property باشد که Exception بدهد، مدیریت می‌شود.
    """
    for name in candidate_names:
        try:
            val = getattr(obj, name, None)
            if val is None:
                continue
            # اگر val یک dict بود، سعی می‌کنیم name را از داخلش بکشیم
            if isinstance(val, dict):
                for key in ('name', 'title', 'sector_name', 'group_name'):
                    if key in val and val[key]:
                        val = val[key]
                        break
                else:
                    continue
            val = str(val).strip()
            if val and val.lower() not in ('none', 'nan', 'null', ''):
                return val
        except Exception:
            continue
    return default


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
    """
    فقط کشی را معتبر می‌داند که:
    - قدیمی نباشد
    - ستون «گروه» را داشته باشد
    - حداقل دو گروه یکتا داشته باشد (نه همه نامشخص)
    """
    if not DATA_FILE.exists():
        return None

    age = cache_age_hours()
    if age is None or age > CACHE_TTL_HOURS:
        return None

    try:
        df = pd.read_parquet(DATA_FILE)
    except Exception:
        return None

    # اعتبارسنجی ساختار
    if 'گروه' not in df.columns:
        return None  # کش قدیمی است، دوباره ساخته می‌شود

    # اگر همه گروه‌ها نامشخص بودند، کش بی‌فایده است
    unique_groups = df['گروه'].dropna().unique()
    if len(unique_groups) == 0:
        return None
    if len(unique_groups) == 1 and unique_groups[0] == 'نامشخص':
        return None

    return df


def save_cache(df):
    try:
        df.to_parquet(DATA_FILE, index=False)
        META_FILE.write_text(json.dumps({'cached_at': datetime.now().isoformat()}))
    except Exception as e:
        st.warning(f"خطا در ذخیره کش: {e}")


def clear_all_cache():
    for f in [DATA_FILE, META_FILE, SYMBOLS_FILE]:
        if f.exists():
            try:
                f.unlink()
            except Exception:
                pass


# ==================== دریافت داده ====================

def get_symbol_list():
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
    rows = []
    for i, sym in enumerate(symbols):
        try:
            t = tse.Ticker(sym)

            shares = None
            for attr in ('shares_outstanding', 'total_shares'):
                try:
                    v = getattr(t, attr, None)
                    if v is not None:
                        shares = v
                        break
                except Exception:
                    continue

            rows.append({
                "نماد": sym,
                "نام شرکت": safe_attr_extract(t, ['title', 'name'], default=''),
                "گروه": safe_attr_extract(
                    t,
                    ['group_name', 'sector_name', 'industry_name',
                     'sector', 'industry', 'group', 'groupName'],
                    default='نامشخص'
                ),
                "تعداد سهام": shares,
            })
        except Exception:
            continue
        if i % 5 == 0:
            done = already_done + i + 1
            pct = min(done / total, 1.0)
            progress_bar.progress(pct, text=f"{done}/{total}: {sym}")
    return pd.DataFrame(rows)


# ==================== سایدبار ====================
with st.sidebar:
    st.header("⚙️ تنظیمات")

    age = cache_age_hours()
    if age is not None and age <= CACHE_TTL_HOURS and DATA_FILE.exists():
        st.success(f"📦 کش معتبر ({age:.1f} ساعت پیش)")
    else:
        st.info("ℹ️ کش معتبر موجود نیست")

    st.markdown("---")

    batch_size = st.slider(
        "اندازه هر دسته:", min_value=10, max_value=200, value=50, step=10,
    )

    st.markdown("---")

    if st.button("🗑️ پاک کردن کامل کش", use_container_width=True):
        clear_all_cache()
        for k in ['df', 'symbols']:
            st.session_state.pop(k, None)
        st.success("کش پاک شد")
        st.rerun()


# ==================== بارگذاری از کش ====================
if 'df' not in st.session_state:
    st.session_state.df = load_cache()

if 'symbols' not in st.session_state:
    st.session_state.symbols = None

df = st.session_state.df


# ==================== نمایش داده ====================
if df is not None and not df.empty:

    st.success(f"✅ {len(df)} شرکت در پایگاه داده")

    # ---- دیباگ ----
    with st.expander("🔍 اطلاعات دیباگ (برای عیب‌یابی)"):
        st.write("**ستون‌های موجود:**", df.columns.tolist())
        st.write("**تعداد رکوردها:**", len(df))
        if 'گروه' in df.columns:
            vc = df['گروه'].fillna('نامشخص').value_counts()
            st.write("**توزیع گروه‌ها (۱۰ گروه اول):**")
            st.dataframe(vc.head(10).rename_axis("گروه").reset_index(name="تعداد"))
            st.write("**تعداد گروه‌های یکتا:**", df['گروه'].nunique())
        else:
            st.error("ستون «گروه» در داده وجود ندارد! لطفاً کش را پاک کنید و داده را دوباره دریافت کنید.")

    # ---- فیلترها ----
    col1, col2, col3 = st.columns([2, 2, 2])

    with col1:
        search = st.text_input("🔍 جستجو:", placeholder="نام نماد یا شرکت...")

    with col2:
        if 'گروه' in df.columns:
            available_groups = sorted(
                [g for g in df['گروه'].dropna().unique().tolist()
                 if g and g != 'نامشخص']
            )
        else:
            available_groups = []

        if available_groups:
            selected_groups = st.multiselect(
                "🏷️ فیلتر گروه صنعتی:",
                options=available_groups,
                default=[],
                help="اگر خالی باشد، همه گروه‌ها نمایش داده می‌شوند"
            )
        else:
            st.multiselect(
                "🏷️ فیلتر گروه صنعتی:",
                options=[],
                default=[],
                disabled=True,
                help="هیچ گروهی در داده یافت نشد. کش را پاک کرده و داده را دوباره دریافت کنید."
            )
            selected_groups = []

    with col3:
        max_shares = st.number_input(
            "📉 حداکثر تعداد سهام:",
            min_value=0, value=0, step=1_000_000,
            help="مقدار ۰ به معنای عدم اعمال فیلتر است"
        )

    # ---- اعمال فیلترها ----
    df_view = df.copy()

    if search:
        mask_search = (
            df_view['نماد'].astype(str).str.contains(search, case=False, na=False) |
            df_view['نام شرکت'].astype(str).str.contains(search, case=False, na=False)
        )
        df_view = df_view[mask_search]

    if selected_groups and 'گروه' in df_view.columns:
        df_view = df_view[df_view['گروه'].isin(selected_groups)]

    if max_shares > 0:
        shares_numeric = pd.to_numeric(df_view['تعداد سهام'], errors='coerce')
        df_view = df_view[shares_numeric.notna() & (shares_numeric <= max_shares)]

    # ---- مرتب‌سازی ----
    sort_col, sort_dir = st.columns([2, 1])
    with sort_col:
        sort_by = st.selectbox(
            "مرتب‌سازی بر اساس:",
            ["—", "نماد", "نام شرکت", "گروه", "تعداد سهام"]
        )
    with sort_dir:
        ascending = st.radio(
            "ترتیب:", ["صعودی", "نزولی"], horizontal=True,
            label_visibility="collapsed"
        ) == "صعودی"

    if sort_by != "—" and sort_by in df_view.columns:
        df_view = df_view.sort_values(sort_by, ascending=ascending, na_position='last')

    st.caption(f"📊 {len(df_view)} رکورد از {len(df)} رکورد مطابق فیلترها یافت شد.")

    # ---- صفحه‌بندی ----
    total = len(df_view)
    if total > 0:
        total_pages = max(1, (total - 1) // PAGE_SIZE + 1)
        page = st.number_input("صفحه", min_value=1, max_value=total_pages, value=1, step=1)
        start = (page - 1) * PAGE_SIZE
        end = start + PAGE_SIZE
        st.caption(f"نمایش {start+1} تا {min(end, total)} از {total} رکورد")
        st.dataframe(df_view.iloc[start:end], use_container_width=True, hide_index=True)
    else:
        st.info("هیچ رکوردی با فیلترهای انتخابی یافت نشد.")

    csv = df_view.to_csv(index=False).encode('utf-8-sig')
    st.download_button(
        "📥 دانلود CSV (بر اساس فیلتر فعلی)",
        csv, "tsetmc_data_filtered.csv", "text/csv"
    )

    st.markdown("---")

    # ---- دریافت دسته‌های بعدی ----
    if st.session_state.symbols is None:
        st.session_state.symbols = get_symbol_list()

    total_symbols = len(st.session_state.symbols) if st.session_state.symbols else 0
    remaining = total_symbols - len(df) if total_symbols else 0

    if remaining > 0:
        st.info(
            f"📊 {len(df)} از {total_symbols} نماد دریافت شده. "
            f"{remaining} نماد باقی مانده."
        )
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

    if st.button("🔄 بروزرسانی کامل داده‌ها (پاک کردن کش و دریافت مجدد)"):
        clear_all_cache()
        for k in ['df', 'symbols']:
            st.session_state.pop(k, None)
        st.rerun()

else:
    # ==================== بدون کش: دریافت اولیه ====================
    st.info("📥 داده‌ای برای نمایش موجود نیست.")

    if DATA_FILE.exists():
        st.warning(
            "⚠️ فایل کش موجود است اما معتبر نیست "
            "(احتمالاً قدیمی است یا ستون «گروه» را ندارد). "
            "برای ساخت داده جدید، روی دکمه زیر کلیک کنید یا از سایدبار کش را پاک کنید."
        )

    st.subheader("🆕 دریافت اولیه")

    initial_count = st.number_input(
        "تعداد نماد برای شروع:", min_value=10, max_value=300, value=50, step=10
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

            for bstart in range(0, len(initial), batch_size):
                bsymbols = initial[bstart:bstart + batch_size]
                bdf = fetch_shares_for(bsymbols, progress_bar, bstart, len(initial))
                all_dfs.append(bdf)
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
with st.expander("💡 راهنما و عیب‌یابی"):
    st.markdown("""
    **اگر فیلتر گروه خالی است:**
    1. از سایدبار دکمه **«🗑️ پاک کردن کامل کش»** را بزنید.
    2. سپس روی **«🚀 شروع دریافت»** کلیک کنید.
    3. بعد از دریافت، بخش **«🔍 اطلاعات دیباگ»** را باز کنید و ببینید آیا گروه‌ها پر شده‌اند یا نه.

    اگر در دیباگ دیدید که همه گروه‌ها «نامشخص» هستند، یعنی نسخه‌ی `pytse-client` شما این اطلاعات را برنمی‌گرداند. در این صورت، گزینه‌ها:
    - ارتقاء به نسخه git: در `requirements.txt` بنویسید `git+https://github.com/Glyphack/pytse-client.git`
    - یا با یک کتابخانه دیگر مثل `pytsetmc-api` ادغام کنید.

    **فیلترها:**
    - **جستجو**: در نماد و نام شرکت.
    - **گروه صنعتی**: می‌توانید چند گروه را همزمان انتخاب کنید.
    - **حداکثر تعداد سهام**: اگر بزرگ‌تر از صفر باشد، شرکت‌های با تعداد سهام بیشتر حذف می‌شوند.
    """)
