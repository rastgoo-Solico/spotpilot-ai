import streamlit as st
import pandas as pd
import json
import time
import io
import traceback
from datetime import datetime
from pathlib import Path

# ==================== وارد کردن کتابخانه جدید ====================
from pytsetmc_api import TSETMCClient
from pytsetmc_api.exceptions import TSETMCError

# ==================== تنظیمات ====================
CACHE_DIR = Path("cache")
CACHE_DIR.mkdir(exist_ok=True)
DATA_FILE = CACHE_DIR / "shares_data.parquet"
META_FILE = CACHE_DIR / "meta.json"
SYMBOLS_FILE = CACHE_DIR / "symbols.json"
FONT_PATH = Path("Vazirmatn-Regular.ttf")
CACHE_TTL_HOURS = 12
PAGE_SIZE = 50
BATCH_PAUSE_SECONDS = 0.4

st.set_page_config(page_title="داشبورد بورس", page_icon="📊", layout="wide")
st.title("📊 داشبورد تحلیل بازار بورس ایران")


# ==================== ساخت کلاینت با تنظیمات مقاوم ====================
@st.cache_resource
def get_tsetmc_client():
    """ساخت یک نمونه کلاینت که در حافظه کش می‌شود."""
    return TSETMCClient(
        timeout=30,        # تایم‌اوت ۳۰ ثانیه
        max_retries=3,     # حداکثر ۳ بار تلاش مجدد
        enable_logging=False
    )


# ==================== توابع کمکی (بدون تغییر) ====================

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
        return df if df is not None and not df.empty else None
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


# ==================== توابع دریافت داده (بازنویسی شده) ====================

def get_symbol_list():
    """دریافت لیست نمادها با استفاده از pytsetmc-api."""
    if SYMBOLS_FILE.exists():
        try:
            return json.loads(SYMBOLS_FILE.read_text())
        except Exception:
            pass

    client = get_tsetmc_client()
    try:
        # جستجوی خالی، لیست همه نمادها را برمی‌گرداند
        stocks_df = client.search_stock("")
        if stocks_df is not None and not stocks_df.empty:
            symbols = stocks_df['Symbol'].astype(str).tolist()
            SYMBOLS_FILE.write_text(json.dumps(symbols))
            return symbols
    except TSETMCError as e:
        st.error(f"خطا در دریافت لیست نمادها: {e}")
    except Exception as e:
        st.error(f"خطای غیرمنتظره: {e}")
    return []


def fetch_one_symbol(sym):
    """دریافت اطلاعات یک نماد با pytsetmc-api."""
    client = get_tsetmc_client()
    try:
        info = client.stock.get_stock_info(sym)
        if info is None:
            return None, f"{sym}: اطلاعاتی یافت نشد"

        # استخراج تعداد سهام از اطلاعات
        shares = getattr(info, 'shares_outstanding', None) or getattr(info, 'total_shares', None)

        # استخراج گروه/صنعت
        sector = getattr(info, 'sector', None) or getattr(info, 'industry', None) or 'نامشخص'

        return {
            "نماد": sym,
            "نام شرکت": getattr(info, 'name', ''),
            "گروه": sector,
            "تعداد سهام": shares,
        }, None
    except TSETMCError as e:
        return None, f"{sym}: {e}"
    except Exception as e:
        return None, f"{sym}: {type(e).__name__}: {e}"


def fetch_in_batches(symbols, batch_size, progress_bar=None, status_container=None):
    """دریافت دسته‌ای با استفاده از fetch_one_symbol."""
    all_rows = []
    all_errors = []
    total = len(symbols)

    if progress_bar is None:
        progress_bar = st.progress(0, text="شروع...")

    for bstart in range(0, total, batch_size):
        batch = symbols[bstart:bstart + batch_size]
        if status_container is not None:
            bn = bstart // batch_size + 1
            tb = (total - 1) // batch_size + 1
            status_container.write(f"دسته {bn}/{tb} — دریافت {len(batch)} نماد...")

        for i, sym in enumerate(batch):
            row, err = fetch_one_symbol(sym)
            if row:
                all_rows.append(row)
            if err:
                all_errors.append(err)

            done = bstart + i + 1
            pct = min(done / max(total, 1), 1.0)
            progress_bar.progress(pct, text=f"{done}/{total}: {sym}")

        if bstart + batch_size < total:
            time.sleep(BATCH_PAUSE_SECONDS)

    progress_bar.empty()
    return pd.DataFrame(all_rows), all_errors


# ==================== خروجی اکسل و PDF (بدون تغییر) ====================

def to_excel_bytes(df):
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False, sheet_name='بورس')
    return buffer.getvalue()


def to_pdf_bytes(df, title="گزارش بورس"):
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        import arabic_reshaper
        from bidi.algorithm import get_display

        font_name = 'Helvetica'
        if FONT_PATH.exists():
            pdfmetrics.registerFont(TTFont('Vazir', str(FONT_PATH)))
            font_name = 'Vazir'

        def fa(text):
            if font_name == 'Helvetica':
                return str(text)
            try:
                reshaped = arabic_reshaper.reshape(str(text))
                return get_display(reshaped)
            except Exception:
                return str(text)

        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer, pagesize=landscape(A4),
            rightMargin=10*mm, leftMargin=10*mm,
            topMargin=10*mm, bottomMargin=10*mm,
        )

        styles = getSampleStyleSheet()
        title_style = ParagraphStyle('FaTitle', parent=styles['Heading1'], fontName=font_name, alignment=1)
        cell_style = ParagraphStyle('FaCell', parent=styles['Normal'], fontName=font_name, fontSize=8, alignment=1)

        elements = [Paragraph(fa(title), title_style)]
        data = [[Paragraph(fa(c), cell_style) for c in df.columns]]
        for _, row in df.iterrows():
            data.append([Paragraph(fa(v), cell_style) if pd.notna(v) else '' for v in row.values])

        table = Table(data, repeatRows=1)
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('FONTNAME', (0, 0), (-1, -1), font_name),
        ]))
        elements.append(table)
        doc.build(elements)
        return buffer.getvalue()
    except Exception as e:
        st.warning(f"خطا در تولید PDF: {e}")
        return None


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


# ==================== State ====================
if 'df' not in st.session_state:
    st.session_state.df = load_cache()
if 'symbol_list' not in st.session_state:
    st.session_state.symbol_list = None

df = st.session_state.df


# ==================== داشبورد اصلی ====================
if df is not None and not df.empty:
    st.success(f"✅ {len(df)} شرکت در پایگاه داده")

    with st.expander("🔍 اطلاعات دیباگ"):
        st.write("**ستون‌ها:**", df.columns.tolist())
        st.write("**تعداد رکورد:**", len(df))
        if 'گروه' in df.columns:
            vc = df['گروه'].fillna('نامشخص').value_counts()
            st.dataframe(vc.rename_axis("گروه").reset_index(name="تعداد"))

    col1, col2, col3 = st.columns([2, 2, 2])
    with col1:
        search = st.text_input("🔍 جستجو:", placeholder="نام نماد یا شرکت...")
    with col2:
        available_groups = sorted(
            [g for g in df['گروه'].dropna().unique().tolist() if g and g != 'نامشخص']
        ) if 'گروه' in df.columns else []
        selected_groups = st.multiselect(
            "🏷️ فیلتر گروه صنعتی:", options=available_groups, default=[],
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

    sort_col, sort_dir = st.columns([2, 1])
    with sort_col:
        sort_by = st.selectbox("مرتب‌سازی:", ["—", "نماد", "نام شرکت", "گروه", "تعداد سهام"])
    with sort_dir:
        ascending = st.radio("ترتیب:", ["صعودی", "نزولی"], horizontal=True, label_visibility="collapsed") == "صعودی"
    if sort_by != "—" and sort_by in df_view.columns:
        df_view = df_view.sort_values(sort_by, ascending=ascending, na_position='last')

    st.caption(f"📊 {len(df_view)} رکورد از {len(df)} مطابق فیلترها.")

    total_view = len(df_view)
    if total_view > 0:
        total_pages = max(1, (total_view - 1) // PAGE_SIZE + 1)
        page = st.number_input("صفحه", min_value=1, max_value=total_pages, value=1, step=1)
        start = (page - 1) * PAGE_SIZE
        end = start + PAGE_SIZE
        st.caption(f"نمایش {start+1} تا {min(end, total_view)} از {total_view}")
        st.dataframe(df_view.iloc[start:end], use_container_width=True, hide_index=True)
    else:
        st.info("هیچ رکوردی با فیلترهای انتخابی یافت نشد.")

    st.subheader("📥 خروجی گرفتن")
    dl1, dl2, dl3 = st.columns(3)
    with dl1:
        csv_data = df_view.to_csv(index=False).encode('utf-8-sig')
        st.download_button("دانلود CSV", csv_data, "bourse_data.csv", "text/csv", use_container_width=True)
    with dl2:
        try:
            excel_data = to_excel_bytes(df_view)
            st.download_button(
                "دانلود Excel", excel_data, "bourse_data.xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True
            )
        except Exception as e:
            st.caption(f"Excel در دسترس نیست: {e}")
    with dl3:
        if st.button("تولید PDF", use_container_width=True):
            with st.spinner("در حال ساخت PDF..."):
                pdf_data = to_pdf_bytes(df_view, "گزارش بورس تهران")
            if pdf_data:
                st.download_button(
                    "⬇️ ذخیره PDF", pdf_data, "bourse_data.pdf", "application/pdf",
                    use_container_width=True
                )

    st.markdown("---")
    st.subheader("➕ افزودن نمادهای بیشتر")

    if st.session_state.symbol_list is None:
        with st.spinner("دریافت لیست نمادها..."):
            st.session_state.symbol_list = get_symbol_list()

    all_symbols = st.session_state.symbol_list or []
    existing = set(df['نماد'].astype(str).tolist())
    remaining = [s for s in all_symbols if s not in existing]

    if not remaining:
        st.success("🎉 همه نمادها دریافت شده‌اند!")
    else:
        st.write(f"**{len(remaining)}** نماد هنوز دریافت نشده‌اند (از {len(all_symbols)} کل).")
        col_a, col_b = st.columns([2, 1])
        with col_a:
            count = st.number_input(
                "تعداد برای افزودن:", min_value=10,
                max_value=min(500, len(remaining)),
                value=min(50, len(remaining)), step=10
            )
        with col_b:
            st.write("")
            st.write("")
            if st.button(f"⬇️ افزودن {count} نماد", type="primary", use_container_width=True):
                to_fetch = remaining[:count]
                status = st.status("دریافت...", expanded=True)
                new_df, errors = fetch_in_batches(to_fetch, batch_size, status_container=status)
                if not new_df.empty:
                    merged = pd.concat([df, new_df], ignore_index=True)
                    merged = merged.drop_duplicates(subset=['نماد'], keep='last')
                    save_cache(merged)
                    st.session_state.df = merged
                    status.update(label=f"✅ {len(new_df)} نماد اضافه شد", state="complete")
                    if errors:
                        st.warning(f"⚠️ {len(errors)} خطا")
                    time.sleep(0.3)
                    st.rerun()
                else:
                    status.update(label="❌ ناموفق", state="error")
                    if errors:
                        st.code("\n".join(errors[:8]))

    if st.button("🔄 بروزرسانی کامل (پاک کردن کش)"):
        clear_all_cache()
        for k in ['df', 'symbol_list']:
            st.session_state.pop(k, None)
        st.rerun()

else:
    st.info("📥 برای شروع، یکی از گزینه‌های زیر را انتخاب کنید.")

    if st.session_state.symbol_list is None:
        with st.spinner("دریافت لیست نمادها از TSETMC..."):
            st.session_state.symbol_list = get_symbol_list()

    all_symbols = st.session_state.symbol_list or []

    if not all_symbols:
        st.error("❌ دریافت لیست نمادها ناموفق بود. برای عیب‌یابی روی دکمه زیر کلیک کنید.")
        with st.expander("🔬 تست اتصال", expanded=True):
            if st.button("🔬 تست دریافت یک نماد (فولاد)"):
                try:
                    with st.spinner("در حال تست..."):
                        client = get_tsetmc_client()
                        info = client.stock.get_stock_info("فولاد")
                    st.success("✅ اتصال موفق!")
                    st.write("**نام:**", getattr(info, 'name', 'N/A'))
                    st.write("**تعداد سهام:**", getattr(info, 'shares_outstanding', 'N/A'))
                except Exception as e:
                    st.error(f"❌ خطا: {type(e).__name__}: {e}")
                    st.code(traceback.format_exc())
        st.stop()

    st.success(f"✅ **{len(all_symbols)}** نماد شناسایی شد.")
    st.markdown("### 🎯 انتخاب نحوه شروع")

    tab1, tab2, tab3, tab4 = st.tabs([
        "🚀 دریافت کامل (توصیه‌شده)",
        "⚡ دریافت سریع (تست)",
        "✍️ دریافت دستی نمادها",
        "🏭 انتخاب بر اساس صنعت",
    ])

    with tab1:
        st.markdown(f"**همه {len(all_symbols)} نماد دریافت می‌شود** (همراه با گروه صنعتی).")
        st.write(f"⏱ **زمان تقریبی:** {int(len(all_symbols) * 1.5 / 60)} تا {int(len(all_symbols) * 3 / 60)} دقیقه")
        st.write("✅ **مزیت:** بعد از این یک‌بار، همه گروه‌ها در فیلترها ظاهر می‌شوند و تا ۱۲ ساعت نیازی به دریافت مجدد نیست.")
        if st.button("🚀 دریافت کامل همه نمادها", type="primary", use_container_width=True):
            status = st.status("در حال دریافت...", expanded=True)
            df_new, errors = fetch_in_batches(all_symbols, batch_size, status_container=status)
            if not df_new.empty:
                save_cache(df_new)
                st.session_state.df = df_new
                status.update(label=f"✅ {len(df_new)} شرکت دریافت شد ({len(errors)} خطا)", state="complete")
                st.balloons()
                time.sleep(0.4)
                st.rerun()
            else:
                status.update(label="❌ هیچ داده‌ای دریافت نشد", state="error")
                if errors:
                    st.code("\n".join(errors[:10]))

    with tab2:
        st.write("برای تست سریع، فقط چند نماد اول را دریافت کنید.")
        quick_count = st.number_input("تعداد نماد:", min_value=10, max_value=300, value=100, step=10, key="quick_count")
        st.caption("⚠️ با این روش، فیلتر گروه فقط شامل گروه‌های همان چند نماد خواهد بود.")
        if st.button(f"⚡ دریافت {quick_count} نماد اول", use_container_width=True):
            subset = all_symbols[:quick_count]
            status = st.status("در حال دریافت...", expanded=True)
            df_new, errors = fetch_in_batches(subset, batch_size, status_container=status)
            if not df_new.empty:
                save_cache(df_new)
                st.session_state.df = df_new
                status.update(label=f"✅ {len(df_new)} شرکت دریافت شد", state="complete")
                if errors:
                    st.warning(f"⚠️ {len(errors)} خطا")
                time.sleep(0.3)
                st.rerun()
            else:
                status.update(label="❌ ناموفق", state="error")
                if errors:
                    st.code("\n".join(errors[:8]))

    with tab3:
        st.write("نام نمادها را وارد کنید (با کاما، فاصله یا خط جدید جدا کنید).")
        st.caption("مثال: فولاد, خودرو, شپنا, خساپا")
        manual_input = st.text_area("نام نمادها:", placeholder="فولاد, خودرو, شپنا", height=100)
        st.info(
            "⏱ **چرا دریافت دستی کمی طول می‌کشد؟**\n"
            "برای هر نماد، حداقل یک درخواست شبکه به سرور TSETMC ارسال می‌شود. "
            "سرعت پاسخ‌دهی سرور متغیر است و برای چند نماد، مجموع زمان طبیعی است. "
            "این فرآیند برای حفظ پایداری اتصال و جلوگیری از مسدود شدن IP طراحی شده است."
        )
        if st.button("دریافت نمادهای وارد شده", use_container_width=True):
            raw = manual_input.replace('\n', ',').replace(' ', ',').replace('،', ',')
            user_symbols = [s.strip() for s in raw.split(',') if s.strip()]
            if not user_symbols:
                st.warning("هیچ نمادی وارد نشده است.")
            else:
                status = st.status(f"دریافت {len(user_symbols)} نماد...", expanded=True)
                progress = st.progress(0)
                df_new, errors = fetch_in_batches(
                    user_symbols, batch_size=min(batch_size, len(user_symbols)),
                    progress_bar=progress, status_container=status,
                )
                if not df_new.empty:
                    save_cache(df_new)
                    st.session_state.df = df_new
                    status.update(label=f"✅ {len(df_new)} نماد دریافت شد", state="complete")
                    if errors:
                        st.warning(f"⚠️ {len(errors)} خطا")
                        st.code("\n".join(errors[:5]))
                    time.sleep(0.3)
                    st.rerun()
                else:
                    status.update(label="❌ ناموفق", state="error")
                    if errors:
                        st.code("\n".join(errors[:8]))

    with tab4:
        st.markdown("### 🏭 دریافت تمام نمادهای یک یا چند صنعت")
        st.write(
            "ابتدا یک‌بار لیست نمادها و صنایع دریافت می‌شود، سپس می‌توانید صنعت مورد نظر را انتخاب کنید. "
            "برای جلوگیری از فشار به سرور، فرآیند در پس‌زمینه با نمایش پیشرفت انجام می‌شود."
        )
        if 'industry_map' not in st.session_state:
            st.session_state.industry_map = None

        if st.session_state.industry_map is None:
            if st.button("🔄 دریافت لیست صنایع (نیازمند یک‌بار دریافت اطلاعات پایه)", use_container_width=True):
                with st.spinner("در حال دریافت اطلاعات پایه صنایع..."):
                    base_df, base_errors = fetch_in_batches(all_symbols[:200], batch_size=25)
                if not base_df.empty and 'گروه' in base_df.columns:
                    industry_map = {}
                    for _, row in base_df.iterrows():
                        g = row['گروه']
                        if g and g != 'نامشخص':
                            industry_map.setdefault(g, []).append(row['نماد'])
                    st.session_state.industry_map = industry_map
                    st.success(f"✅ {len(industry_map)} صنعت شناسایی شد.")
                    time.sleep(0.3)
                    st.rerun()
                else:
                    st.error("دریافت پایه ناموفق بود. لطفاً از تب «دریافت کامل» استفاده کنید.")
        else:
            industry_map = st.session_state.industry_map
            industries = sorted(industry_map.keys())
            st.write(f"**{len(industries)}** صنعت در دسترس است:")
            selected = st.multiselect("صنعت‌های مورد نظر را انتخاب کنید:", options=industries, default=[])
            if selected:
                symbols_in_industries = []
                for ind in selected:
                    symbols_in_industries.extend(industry_map.get(ind, []))
                symbols_in_industries = list(dict.fromkeys(symbols_in_industries))
                st.info(f"📊 **{len(symbols_in_industries)}** نماد از صنایع انتخابی دریافت خواهد شد.")
                with st.expander("پیش‌نمایش نمادها"):
                    preview = []
                    for ind in selected:
                        for sym in industry_map.get(ind, []):
                            preview.append({"صنعت": ind, "نماد": sym})
                    st.dataframe(pd.DataFrame(preview), use_container_width=True, hide_index=True)
                if st.button(f"🚀 دریافت {len(symbols_in_industries)} نماد", type="primary", use_container_width=True):
                    status = st.status("در حال دریافت...", expanded=True)
                    progress = st.progress(0)
                    df_new, errors = fetch_in_batches(
                        symbols_in_industries, batch_size,
                        progress_bar=progress, status_container=status
                    )
                    if not df_new.empty:
                        save_cache(df_new)
                        st.session_state.df = df_new
                        status.update(label=f"✅ {len(df_new)} نماد دریافت شد", state="complete")
                        if errors:
                            st.warning(f"⚠️ {len(errors)} خطا")
                        time.sleep(0.3)
                        st.rerun()
                    else:
                        status.update(label="❌ ناموفق", state="error")
                        if errors:
                            st.code("\n".join(errors[:8]))
