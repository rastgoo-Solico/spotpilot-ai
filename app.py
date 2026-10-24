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
SYMBOLS_FILE = CACHE_DIR / "symbols.json"
CACHE_TTL_HOURS = 12
PAGE_SIZE = 50
BATCH_PAUSE_SECONDS = 0.5

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
    for f in [DATA_FILE, META_FILE, SYMBOLS_FILE]:
        if f.exists():
            try:
                f.unlink()
            except Exception:
                pass


def get_symbol_list():
    if SYMBOLS_FILE.exists():
        try:
            return json.loads(SYMBOLS_FILE.read_text())
        except Exception:
            pass
    try:
        symbols = tse.all_symbols()
        if symbols:
            symbols = [str(s) for s in symbols]
            SYMBOLS_FILE.write_text(json.dumps(symbols))
            return symbols
    except Exception:
        pass
    try:
        d = tse.download(symbols="all", write_to_csv=False)
        symbols = list(d.keys())
        SYMBOLS_FILE.write_text(json.dumps(symbols))
        return symbols
    except Exception as e:
        st.error(f"خطا در دریافت لیست نمادها: {type(e).__name__}: {e}")
        return []


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

        if i % 3 == 0:
            done = already_done + i + 1
            pct = min(done / max(total, 1), 1.0)
            progress_bar.progress(pct, text=f"{done}/{total}: {sym}")
    return pd.DataFrame(rows), errors


def fetch_in_batches(symbols, batch_size, status_container=None, progress_bar=None):
    all_dfs = []
    all_errors = []
    total = len(symbols)
    if progress_bar is None:
        progress_bar = st.progress(0, text="شروع...")

    for bstart in range(0, total, batch_size):
        bsymbols = symbols[bstart:bstart + batch_size]
        if status_container is not None:
            batch_num = bstart // batch_size + 1
            total_batches = (total - 1) // batch_size + 1
            status_container.write(f"دسته {batch_num}/{total_batches}: {len(bsymbols)} نماد...")
        bdf, berrors = fetch_shares_for(bsymbols, progress_bar, bstart, total)
        all_dfs.append(bdf)
        all_errors.extend(berrors)
        if bstart + batch_size < total:
            time.sleep(BATCH_PAUSE_SECONDS)

    progress_bar.empty()
    if all_dfs and any(not d.empty for d in all_dfs):
        return pd.concat([d for d in all_dfs if not d.empty], ignore_index=True), all_errors
    return pd.DataFrame(), all_errors


# ==================== سایدبار ====================
with st.sidebar:
    st.header("⚙️ تنظیمات")
    age = cache_age_hours()
    if age is not None and age <= CACHE_TTL_HOURS and DATA_FILE.exists():
        st.success(f"📦 کش معتبر ({age:.1f} ساعت پیش)")
    elif age is not None:
        st.warning(f"⚠️ کش قدیمی ({age:.1f} ساعت)")
    else:
        st.info("ℹ️ کش موجود نیست")

    st.markdown("---")
    batch_size = st.slider("اندازه هر دسته:", 10, 100, 25, 5)
    st.markdown("---")

    if st.button("🗑️ پاک کردن کامل کش", use_container_width=True):
        clear_all_cache()
        for k in ['df', 'symbol_list']:
            st.session_state.pop(k, None)
        st.success("کش پاک شد")
        st.rerun()


# ==================== بارگذاری state ====================
if 'df' not in st.session_state:
    st.session_state.df = load_cache()
if 'symbol_list' not in st.session_state:
    st.session_state.symbol_list = None

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
                options=available_groups, default=[],
                help="اگر خالی باشد، همه گروه‌ها نمایش داده می‌شوند"
            )
        else:
            st.multiselect(
                "🏷️ فیلتر گروه صنعتی:",
                options=[], default=[], disabled=True,
                help="هنوز گروهی در داده وجود ندارد"
            )
            selected_groups = []

    with col3:
        max_shares = st.number_input(
            "📉 حداکثر تعداد سهام:", min_value=0, value=0, step=1_000_000,
            help="مقدار ۰ = بدون فیلتر"
        )

    # ---- اعمال فیلتر ----
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

    # ---- افزودن نمادهای بیشتر ----
    st.subheader("➕ افزودن نمادهای بیشتر")

    if st.session_state.symbol_list is None:
        with st.spinner("دریافت لیست نمادها..."):
            st.session_state.symbol_list = get_symbol_list()

    all_symbols = st.session_state.symbol_list or []
    existing_symbols = set(df['نماد'].astype(str).tolist())
    remaining = [s for s in all_symbols if s not in existing_symbols]

    if not remaining:
        st.success("🎉 همه نمادها دریافت شده‌اند!")
    else:
        st.write(f"**{len(remaining)}** نماد هنوز دریافت نشده‌اند (از {len(all_symbols)} کل).")

        col_a, col_b = st.columns([2, 1])
        with col_a:
            count = st.number_input(
                "تعداد برای افزودن:",
                min_value=10, max_value=min(500, len(remaining)),
                value=min(50, len(remaining)), step=10
            )
        with col_b:
            st.write("")
            st.write("")
            if st.button(f"⬇️ افزودن {count} نماد", type="primary", use_container_width=True):
                to_fetch = remaining[:count]
                status = st.status("دریافت...", expanded=True)
                with st.spinner(f"دریافت {len(to_fetch)} نماد..."):
                    new_df, errors = fetch_in_batches(to_fetch, batch_size, status)
                if not new_df.empty:
                    merged = pd.concat([df, new_df], ignore_index=True)
                    merged = merged.drop_duplicates(subset=['نماد'], keep='last')
                    save_cache(merged)
                    st.session_state.df = merged
                    status.update(label=f"✅ {len(new_df)} نماد اضافه شد", state="complete")
                    if errors:
                        st.warning(f"⚠️ {len(errors)} خطا")
                    time.sleep(0.4)
                    st.rerun()
                else:
                    status.update(label="❌ ناموفق", state="error")
                    if errors:
                        st.code("\n".join(errors[:10]))

    if st.button("🔄 بروزرسانی کامل (پاک کردن کش)"):
        clear_all_cache()
        for k in ['df', 'symbol_list']:
            st.session_state.pop(k, None)
        st.rerun()

else:
    # ==================== صفحه شروع ====================
    st.info("📥 برای شروع، یکی از گزینه‌های زیر را انتخاب کنید.")

    if st.session_state.symbol_list is None:
        with st.spinner("دریافت لیست نمادها از TSETMC..."):
            st.session_state.symbol_list = get_symbol_list()

    all_symbols = st.session_state.symbol_list or []

    if not all_symbols:
        st.error("❌ دریافت لیست نمادها ناموفق بود. برای عیب‌یابی روی دکمه زیر کلیک کنید.")
        with st.expander("🔬 تست اتصال به TSETMC", expanded=True):
            if st.button("🔬 تست دریافت یک نماد (فولاد)"):
                try:
                    with st.spinner("در حال تست..."):
                        t = tse.Ticker("فولاد")
                    st.success("✅ اتصال موفق!")
                    st.write("**عنوان:**", getattr(t, 'title', 'N/A'))
                    st.write("**تعداد سهام:**", getattr(t, 'shares_outstanding', 'N/A'))
                    attrs = [a for a in dir(t)
                             if not a.startswith('_') and not callable(getattr(t, a, None))]
                    st.write("**ویژگی‌های موجود:**")
                    st.code(", ".join(attrs[:40]))
                except Exception as e:
                    st.error(f"❌ خطا: {type(e).__name__}: {e}")
                    st.code(traceback.format_exc())
        st.stop()

    st.success(f"✅ **{len(all_symbols)}** نماد شناسایی شد.")
    st.markdown("### 🎯 انتخاب نحوه شروع")

    tab1, tab2, tab3 = st.tabs([
        "🚀 دریافت کامل (توصیه‌شده)",
        "⚡ دریافت سریع (تست)",
        "✍️ دریافت دستی نمادها"
    ])

    # ---------- Tab 1: دریافت کامل ----------
    with tab1:
        st.markdown(f"**همه {len(all_symbols)} نماد دریافت می‌شود** (همراه با گروه صنعتی).")
        st.write(f"⏱ **زمان تقریبی:** {int(len(all_symbols) * 1.5 / 60)} تا {int(len(all_symbols) * 3 / 60)} دقیقه")
        st.write("✅ **مزیت:** بعد از این یک‌بار، همه گروه‌ها در فیلترها ظاهر می‌شوند و تا ۱۲ ساعت نیازی به دریافت مجدد نیست.")
        st.write("💡 این تنها راه دسترسی به **فیلتر گروه صنعتی** است، چون اطلاعات گروه فقط از طریق Ticker قابل دریافت است.")

        if st.button("🚀 دریافت کامل همه نمادها", type="primary", use_container_width=True):
            status = st.status("در حال دریافت...", expanded=True)
            df_new, errors = fetch_in_batches(all_symbols, batch_size, status)

            if not df_new.empty:
                save_cache(df_new)
                st.session_state.df = df_new
                status.update(
                    label=f"✅ {len(df_new)} شرکت دریافت شد ({len(errors)} خطا)",
                    state="complete"
                )
                st.balloons()
                time.sleep(0.5)
                st.rerun()
            else:
                status.update(label="❌ هیچ داده‌ای دریافت نشد", state="error")
                if errors:
                    st.code("\n".join(errors[:10]))

    # ---------- Tab 2: دریافت سریع ----------
    with tab2:
        st.write("برای تست سریع عملکرد برنامه، فقط چند نماد اول را دریافت کنید.")

        quick_count = st.number_input(
            "تعداد نماد:", min_value=10, max_value=300, value=100, step=10,
            key="quick_count"
        )
        st.caption("⚠️ با این روش، فیلتر گروه فقط شامل گروه‌های همان چند نماد خواهد بود.")

        if st.button(f"⚡ دریافت {quick_count} نماد اول", use_container_width=True):
            subset = all_symbols[:quick_count]
            status = st.status("در حال دریافت...", expanded=True)
            df_new, errors = fetch_in_batches(subset, batch_size, status)

            if not df_new.empty:
                save_cache(df_new)
                st.session_state.df = df_new
                status.update(label=f"✅ {len(df_new)} شرکت دریافت شد", state="complete")
                if errors:
                    st.warning(f"⚠️ {len(errors)} خطا")
                time.sleep(0.4)
                st.rerun()
            else:
                status.update(label="❌ ناموفق", state="error")
                if errors:
                    st.code("\n".join(errors[:10]))

    # ---------- Tab 3: دریافت دستی ----------
    with tab3:
        st.write("نام نمادهای مورد نظر را وارد کنید (با کاما یا فاصله یا خط جدید جدا کنید).")
        st.caption("مثال: فولاد, خودرو, شپنا, خساپا")

        manual_input = st.text_area("نام نمادها:", placeholder="فولاد, خودرو, شپنا", height=100)

        if st.button("دریافت نمادهای وارد شده", use_container_width=True):
            raw = manual_input.replace('\n', ',').replace(' ', ',').replace('،', ',')
            user_symbols = [s.strip() for s in raw.split(',') if s.strip()]

            if not user_symbols:
                st.warning("هیچ نمادی وارد نشده است.")
            else:
                status = st.status("در حال دریافت...", expanded=True)
                df_new, errors = fetch_in_batches(user_symbols, batch_size, status)

                if not df_new.empty:
                    save_cache(df_new)
                    st.session_state.df = df_new
                    status.update(label=f"✅ {len(df_new)} نماد دریافت شد", state="complete")
                    if errors:
                        st.warning(f"⚠️ {len(errors)} خطا")
                        st.code("\n".join(errors[:5]))
                    time.sleep(0.4)
                    st.rerun()
                else:
                    status.update(label="❌ ناموفق", state="error")
                    if errors:
                        st.code("\n".join(errors[:10]))


# ==================== راهنما ====================
st.markdown("---")
with st.expander("💡 راهنما"):
    st.markdown("### چرا گروه صنعتی بدون دریافت همه نمادها ممکن نیست؟")
    st.write("سایت TSETMC گروه صنعتی را در API لیست نمادها برنمی‌گرداند. تنها راه، فراخوانی Ticker برای هر نماد است.")
    st.markdown("### راهکارها برای جلوگیری از Throttle")
    st.write("- **دریافت کامل یک‌بار:** بعد از آن تا ۱۲ ساعت از کش استفاده می‌شود.")
    st.write("- **دسته‌بندی:** اندازه دسته را در سایدبار کوچک نگه دارید (مثلاً ۲۵).")
    st.write("- **وقفه:** بین دسته‌ها ۰.۵ ثانیه وقفه وجود دارد.")
    st.markdown("### اگر تعداد سهام یا گروه «نامشخص» است")
    st.write("نسخه pytse-client ممکن است ویژگی group_name را نداشته باشد. در requirements.txt این خط را قرار دهید:")
    st.code("git+https://github.com/Glyphack/pytse-client.git", language="text")
