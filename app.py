import streamlit as st
import pytse_client as tse
import pandas as pd
import json
import time
import traceback
from datetime import datetime
from pathlib import Path

# ==================== تنظیمات ====================
CACHE_DIR = Path("cache")
CACHE_DIR.mkdir(exist_ok=True)
DATA_FILE = CACHE_DIR / "shares_data.parquet"
META_FILE = CACHE_DIR / "meta.json"
SYMBOLS_META_FILE = CACHE_DIR / "symbols_with_sectors.parquet"
CACHE_TTL_HOURS = 12
PAGE_SIZE = 50
BATCH_PAUSE_SECONDS = 0.8

st.set_page_config(page_title="داشبورد بورس", page_icon="📊", layout="wide")
st.title("📊 داشبورد تحلیل بازار بورس ایران")


# ==================== توابع کمکی ====================

def safe_attr_extract(obj, candidate_names, default='نامشخص'):
    for name in candidate_names:
        try:
            val = getattr(obj, name, None)
            if val is None:
                continue
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
    if not DATA_FILE.exists():
        return None
    age = cache_age_hours()
    if age is None or age > CACHE_TTL_HOURS:
        return None
    try:
        df = pd.read_parquet(DATA_FILE)
        if df is None or df.empty:
            return None
        return df
    except Exception:
        return None


def save_cache(df):
    try:
        df.to_parquet(DATA_FILE, index=False)
        META_FILE.write_text(json.dumps({'cached_at': datetime.now().isoformat()}))
    except Exception as e:
        st.warning(f"خطا در ذخیره کش: {e}")


def clear_all_cache():
    for f in [DATA_FILE, META_FILE, SYMBOLS_META_FILE]:
        if f.exists():
            try:
                f.unlink()
            except Exception:
                pass


# ==================== لیست نمادها با گروه (یک درخواست سبک) ====================

def get_symbols_with_sectors():
    """
    لیست همه نمادها با گروه صنعتی‌شان را با یک درخواست سبک برمی‌گرداند.
    اگر کش موجود و معتبر باشد، از کش می‌خواند.
    """
    # تلاش برای خواندن از کش
    if SYMBOLS_META_FILE.exists():
        try:
            df = pd.read_parquet(SYMBOLS_META_FILE)
            if df is not None and not df.empty and 'sector' in df.columns:
                return df
        except Exception:
            pass

    # دریافت از TSETMC
    try:
        from pytse_client import SymbolList
        sl = SymbolList()
        try:
            df = sl.all_symbols(show_progress=False)
        except TypeError:
            df = sl.all_symbols()

        if df is None or df.empty:
            return None

        df = df.copy()
        # استانداردسازی نام ستون‌ها
        rename_map = {}
        for c in df.columns:
            cl = c.lower()
            if cl in ('symbol', 'ticker', 'نماد'):
                rename_map[c] = 'symbol'
            elif cl in ('name', 'title', 'company_name', 'نام'):
                rename_map[c] = 'name'
            elif cl in ('sector', 'sector_name', 'group', 'group_name',
                        'industry', 'industry_name'):
                rename_map[c] = 'sector'
        df = df.rename(columns=rename_map)

        if 'symbol' not in df.columns or 'sector' not in df.columns:
            return None

        df['sector'] = df['sector'].fillna('نامشخص').astype(str).str.strip()
        df = df[df['sector'] != '']

        # ذخیره در کش
        try:
            df.to_parquet(SYMBOLS_META_FILE, index=False)
        except Exception:
            pass

        return df

    except Exception as e:
        st.error(f"خطا در دریافت لیست نمادها با گروه: {type(e).__name__}: {e}")
        return None


def fetch_shares_for(symbols, progress_bar, already_done, total):
    rows = []
    errors = []
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
        except Exception as e:
            errors.append(f"{sym}: {type(e).__name__}: {e}")
            continue
        if i % 5 == 0:
            done = already_done + i + 1
            pct = min(done / total, 1.0)
            progress_bar.progress(pct, text=f"{done}/{total}: {sym}")
    return pd.DataFrame(rows), errors


# ==================== سایدبار ====================
with st.sidebar:
    st.header("⚙️ تنظیمات")
    age = cache_age_hours()
    if age is not None and age <= CACHE_TTL_HOURS and DATA_FILE.exists():
        st.success(f"📦 کش معتبر ({age:.1f} ساعت پیش)")
    else:
        st.info("ℹ️ کش معتبر موجود نیست")

    st.markdown("---")
    batch_size = st.slider("اندازه هر دسته:", 10, 200, 50, 10)
    st.markdown("---")

    if st.button("🗑️ پاک کردن کامل کش", use_container_width=True):
        clear_all_cache()
        for k in ['df', 'meta_df']:
            st.session_state.pop(k, None)
        st.success("کش پاک شد")
        st.rerun()


# ==================== بارگذاری state ====================
if 'df' not in st.session_state:
    st.session_state.df = load_cache()
if 'meta_df' not in st.session_state:
    st.session_state.meta_df = None  # DataFrame با ستون‌های symbol, name, sector

df = st.session_state.df


# ==================== بخش اصلی ====================
if df is not None and not df.empty:
    # ============ داشبورد با داده ============
    st.success(f"✅ {len(df)} شرکت در پایگاه داده")

    with st.expander("🔍 اطلاعات دیباگ"):
        st.write("**ستون‌ها:**", df.columns.tolist())
        st.write("**تعداد رکورد:**", len(df))
        if 'گروه' in df.columns:
            vc = df['گروه'].fillna('نامشخص').value_counts()
            st.write("**توزیع گروه‌ها:**")
            st.dataframe(vc.rename_axis("گروه").reset_index(name="تعداد"))

    # ---- فیلترها ----
    col1, col2, col3 = st.columns([2, 2, 2])
    with col1:
        search = st.text_input("🔍 جستجو:", placeholder="نام نماد یا شرکت...")
    with col2:
        available_groups = sorted(
            [g for g in df['گروه'].dropna().unique().tolist()
             if g and g != 'نامشخص']
        ) if 'گروه' in df.columns else []
        selected_groups = st.multiselect(
            "🏷️ فیلتر گروه صنعتی:",
            options=available_groups, default=[],
            help="اگر خالی باشد، همه گروه‌ها نمایش داده می‌شوند"
        )
    with col3:
        max_shares = st.number_input(
            "📉 حداکثر تعداد سهام:", min_value=0, value=0, step=1_000_000,
            help="مقدار ۰ = بدون فیلتر"
        )

    df_view = df.copy()
    if search:
        mask = (
            df_view['نماد'].astype(str).str.contains(search, case=False, na=False) |
            df_view['نام شرکت'].astype(str).str.contains(search, case=False, na=False)
        )
        df_view = df_view[mask]
    if selected_groups and 'گروه' in df_view.columns:
        df_view = df_view[df_view['گروه'].isin(selected_groups)]
    if max_shares > 0:
        shares_numeric = pd.to_numeric(df_view['تعداد سهام'], errors='coerce')
        df_view = df_view[shares_numeric.notna() & (shares_numeric <= max_shares)]

    # ---- مرتب‌سازی ----
    sort_col, sort_dir = st.columns([2, 1])
    with sort_col:
        sort_by = st.selectbox("مرتب‌سازی:", ["—", "نماد", "نام شرکت", "گروه", "تعداد سهام"])
    with sort_dir:
        ascending = st.radio(
            "ترتیب:", ["صعودی", "نزولی"], horizontal=True,
            label_visibility="collapsed"
        ) == "صعودی"
    if sort_by != "—" and sort_by in df_view.columns:
        df_view = df_view.sort_values(sort_by, ascending=ascending, na_position='last')

    st.caption(f"📊 {len(df_view)} رکورد از {len(df)} مطابق فیلترها.")

    # ---- صفحه‌بندی ----
    total = len(df_view)
    if total > 0:
        total_pages = max(1, (total - 1) // PAGE_SIZE + 1)
        page = st.number_input("صفحه", min_value=1, max_value=total_pages, value=1, step=1)
        start = (page - 1) * PAGE_SIZE
        end = start + PAGE_SIZE
        st.caption(f"نمایش {start+1} تا {min(end, total)} از {total}")
        st.dataframe(df_view.iloc[start:end], use_container_width=True, hide_index=True)
    else:
        st.info("هیچ رکوردی با فیلترهای انتخابی یافت نشد.")

    csv = df_view.to_csv(index=False).encode('utf-8-sig')
    st.download_button("📥 دانلود CSV (فیلترشده)", csv, "tsetmc_filtered.csv", "text/csv")

    st.markdown("---")

    # ---- افزودن گروه‌های جدید ----
    st.subheader("➕ افزودن گروه صنعتی دیگر")

    if st.session_state.meta_df is None:
        with st.spinner("دریافت لیست گروه‌ها (یک درخواست سبک)..."):
            st.session_state.meta_df = get_symbols_with_sectors()

    meta_df = st.session_state.meta_df
    if meta_df is None or meta_df.empty:
        st.error("دریافت لیست گروه‌ها ناموفق بود. لطفاً صفحه را رفرش کنید.")
    else:
        # گروه‌های موجود در متادیتا
        all_sectors = sorted(meta_df['sector'].dropna().unique().tolist())

        # گروه‌هایی که قبلاً دریافت شده‌اند
        existing_groups = set(df['گروه'].dropna().unique()) if 'گروه' in df.columns else set()

        # گروه‌های قابل افزودن (گروه‌هایی که بخشی از نمادهاشان هنوز نیامده)
        existing_symbols = set(df['نماد'].astype(str).tolist())
        rows_before = len(df)

        addable_sectors = []
        for sector in all_sectors:
            sector_syms = meta_df[meta_df['sector'] == sector]['symbol'].astype(str).tolist()
            if any(s not in existing_symbols for s in sector_syms):
                addable_sectors.append(sector)

        if not addable_sectors:
            st.success("🎉 همه گروه‌ها و نمادها دریافت شده‌اند!")
        else:
            sectors_to_add = st.multiselect(
                "گروه‌های جدید برای افزودن:",
                options=addable_sectors, default=[]
            )

            if sectors_to_add:
                # لیست نمادهای گروه‌های انتخاب‌شده که هنوز در df نیستند
                symbols_to_fetch = []
                for sector in sectors_to_add:
                    sector_syms = meta_df[meta_df['sector'] == sector]['symbol'].astype(str).tolist()
                    for s in sector_syms:
                        if s not in existing_symbols:
                            symbols_to_fetch.append(s)
                # حذف تکراری‌ها ولی حفظ ترتیب
                symbols_to_fetch = list(dict.fromkeys(symbols_to_fetch))

                st.write(f"**تعداد نمادهای جدید:** {len(symbols_to_fetch)}")

                if st.button(f"⬇️ دریافت {len(symbols_to_fetch)} نماد", type="primary"):
                    progress = st.progress(0, text="شروع...")
                    all_dfs = []
                    all_errors = []
                    for bstart in range(0, len(symbols_to_fetch), batch_size):
                        bsymbols = symbols_to_fetch[bstart:bstart + batch_size]
                        bdf, berrors = fetch_shares_for(
                            bsymbols, progress, bstart, len(symbols_to_fetch)
                        )
                        all_dfs.append(bdf)
                        all_errors.extend(berrors)
                        if bstart + batch_size < len(symbols_to_fetch):
                            time.sleep(BATCH_PAUSE_SECONDS)
                    progress.empty()

                    if all_dfs and any(not d.empty for d in all_dfs):
                        new_df = pd.concat([d for d in all_dfs if not d.empty], ignore_index=True)
                        merged = pd.concat([df, new_df], ignore_index=True)
                        # حذف تکراری‌ها بر اساس نماد
                        merged = merged.drop_duplicates(subset=['نماد'], keep='last')
                        save_cache(merged)
                        st.session_state.df = merged
                        st.success(f"✅ {len(new_df)} نماد جدید اضافه شد")
                        if all_errors:
                            st.warning(f"⚠️ {len(all_errors)} خطا در دریافت")
                        time.sleep(0.5)
                        st.rerun()
                    else:
                        st.error("❌ هیچ نمادی دریافت نشد.")
                        if all_errors:
                            st.code("\n".join(all_errors[:10]))

    if st.button("🔄 بروزرسانی کامل (پاک کردن کش)"):
        clear_all_cache()
        for k in ['df', 'meta_df']:
            st.session_state.pop(k, None)
        st.rerun()

else:
    # ==================== صفحه راه‌اندازی: انتخاب گروه ====================
    st.info("📥 برای شروع، یک گروه صنعتی انتخاب کنید تا فقط نمادهای آن دریافت شود.")

    # دریافت لیست گروه‌ها
    if st.session_state.meta_df is None:
        with st.spinner("دریافت لیست گروه‌ها از TSETMC (یک درخواست سبک)..."):
            st.session_state.meta_df = get_symbols_with_sectors()

    meta_df = st.session_state.meta_df

    if meta_df is None or meta_df.empty:
        st.error(
            "❌ دریافت لیست گروه‌ها ناموفق بود. "
            "ممکن است اتصال به TSETMC برقرار نباشد یا کتابخانه مشکل داشته باشد."
        )

        with st.expander("🔬 ابزار عیب‌یابی"):
            if st.button("🔬 تست دریافت یک نماد (فولاد)"):
                try:
                    with st.spinner("در حال تست..."):
                        t = tse.Ticker("فولاد")
                    st.success("✅ اتصال موفق")
                    st.write("**عنوان:**", getattr(t, 'title', 'N/A'))
                    st.write("**تعداد سهام:**", getattr(t, 'shares_outstanding', 'N/A'))
                    attrs = [a for a in dir(t) if not a.startswith('_') and not callable(getattr(t, a, None))]
                    st.write("**ویژگی‌ها:**")
                    st.code(", ".join(attrs[:40]))
                except Exception as e:
                    st.error(f"❌ خطا: {type(e).__name__}: {e}")
                    st.code(traceback.format_exc())
        st.stop()

    # گروه‌ها و تعداد نمادهاشان
    sector_counts = (
        meta_df.groupby('sector')['symbol']
        .count()
        .sort_values(ascending=False)
    )

    st.markdown(f"**{len(sector_counts)}** گروه صنعتی در دسترس است:")

    # نمایش سریع در جدول کوچک
    with st.expander("مشاهده لیست کامل گروه‌ها", expanded=False):
        show_df = sector_counts.reset_index()
        show_df.columns = ['گروه صنعتی', 'تعداد نماد']
        st.dataframe(show_df, use_container_width=True, hide_index=True)

    st.markdown("### 🏷️ انتخاب گروه برای شروع:")

    selected_sectors = st.multiselect(
        "گروه‌های مورد نظر:",
        options=sector_counts.index.tolist(),
        default=[],
        help="می‌توانید چند گروه را همزمان انتخاب کنید. سپس فقط نمادهای همان گروه‌ها دریافت می‌شوند."
    )

    # پیش‌نمایش
    if selected_sectors:
        symbols_to_fetch = []
        for sector in selected_sectors:
            syms = meta_df[meta_df['sector'] == sector]['symbol'].astype(str).tolist()
            symbols_to_fetch.extend(syms)
        symbols_to_fetch = list(dict.fromkeys(symbols_to_fetch))

        st.info(f"📊 **{len(symbols_to_fetch)}** نماد از گروه‌های انتخابی دریافت خواهد شد.")

        with st.expander("پیش‌نمایش نمادها"):
            preview = meta_df[meta_df['sector'].isin(selected_sectors)][['symbol', 'sector', 'name']]
            st.dataframe(preview, use_container_width=True, hide_index=True)

        if st.button(f"🚀 شروع دریافت {len(symbols_to_fetch)} نماد", type="primary", use_container_width=True):
            status = st.status("در حال دریافت...", expanded=True)
            progress = st.progress(0, text="شروع...")

            all_dfs = []
            all_errors = []
            for bstart in range(0, len(symbols_to_fetch), batch_size):
                bsymbols = symbols_to_fetch[bstart:bstart + batch_size]
                status.write(f"   دسته {bstart//batch_size + 1}: {len(bsymbols)} نماد...")
                bdf, berrors = fetch_shares_for(
                    bsymbols, progress, bstart, len(symbols_to_fetch)
                )
                all_dfs.append(bdf)
                all_errors.extend(berrors)
                if bstart + batch_size < len(symbols_to_fetch):
                    time.sleep(BATCH_PAUSE_SECONDS)

            progress.empty()

            if all_dfs and any(not d.empty for d in all_dfs):
                new_df = pd.concat([d for d in all_dfs if not d.empty], ignore_index=True)
                save_cache(new_df)
                st.session_state.df = new_df
                status.update(
                    label=f"✅ {len(new_df)} شرکت دریافت شد",
                    state="complete"
                )
                if all_errors:
                    st.warning(f"⚠️ {len(all_errors)} خطا در دریافت")
                time.sleep(0.5)
                st.rerun()
            else:
                status.update(label="❌ هیچ داده‌ای دریافت نشد", state="error")
                st.error("دریافت ناموفق. علت‌های محتمل: اتصال به TSETMC، مشکل کتابخانه.")
                if all_errors:
                    st.code("\n".join(all_errors[:10]))


# ==================== راهنما ====================
st.markdown("---")
with st.expander("💡 راهنما"):
    st.markdown("""
    ### 🎯 چطور فقط گروه دلخواهم را دریافت کنم؟
    1. در صفحه شروع، از لیست گروه‌ها (مثل «بانک‌ها و موسسات اعتباری»، «دارو») یکی را انتخاب کنید.
    2. تعداد نمادها نمایش داده می‌شود (مثلاً ۳۰ نماد).
    3. روی **🚀 شروع دریافت** کلیک کنید. فقط همان ۳۰ نماد دریافت می‌شوند.
    4. بعداً می‌توانید از داشبورد، گروه دیگری را از بخش **➕ افزودن گروه صنعتی دیگر** اضافه کنید.

    ### 💡 چرا این روش بهتر است؟
    - به‌جای دریافت تصادفی ۵۰ نماد و هدر دادن CPU، فقط نمادهای گروه انتخابی دریافت می‌شوند.
    - لیست گروه‌ها از یک API سبک خوانده می‌شود (نه ۵۰۰ درخواست).
    - هر گروه معمولاً ۱۰ تا ۴۰ نماد دارد → خیلی سریع و بدون Throttle.

    ### 🛡 جلوگیری از Throttle
    - کش ۱۲ ساعته
    - دریافت هدفمند گروه‌به‌گروه
    - وقفه ۰.۸ ثانیه بین دسته‌ها
    """)
