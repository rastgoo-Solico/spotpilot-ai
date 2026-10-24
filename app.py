import streamlit as st
import pytse_client as tse
import pandas as pd

st.title("📊 داشبورد استخراج تعداد سهام شرکت‌های بورسی")

@st.cache_data(ttl=3600)  # کش کردن داده‌ها به مدت ۱ ساعت
def fetch_all_stocks_data():
    """
    دریافت لیست تمام نمادها و استخراج تعداد سهام هر شرکت.
    """
    # ۱. دریافت لیست تمام نمادهای بورسی
    # با قرار دادن "all" تمام سهام، حق تقدم‌ها و... دانلود می‌شوند
    all_tickers = tse.download(symbols="all", write_to_csv=False)
    
    data_list = []
    # ۲. حلقه روی تمام نمادها برای استخراج اطلاعات
    for symbol, ticker_data in all_tickers.items():
        try:
            # ایجاد شیء Ticker برای دسترسی به اطلاعات تکمیلی
            ticker = tse.Ticker(symbol)
            
            # استخراج تعداد سهام (در صورت وجود)
            # ویژگی shares_outstanding ممکن است در نسخه‌های مختلف نام متفاوتی داشته باشد
            shares = getattr(ticker, 'shares_outstanding', None)
            if shares is None:
                # تلاش برای ویژگی جایگزین
                shares = getattr(ticker, 'total_shares', None)
            
            data_list.append({
                "نماد": symbol,
                "نام شرکت": ticker.title,
                "تعداد سهام": shares
            })
        except Exception as e:
            # در صورت بروز خطا برای یک نماد، از آن عبور می‌کنیم
            continue
            
    return pd.DataFrame(data_list)

# --- بخش رابط کاربری Streamlit ---
with st.spinner('در حال دریافت داده از TSETMC...'):
    df = fetch_all_stocks_data()

if not df.empty:
    st.success(f"تعداد {len(df)} شرکت دریافت شد.")
    
    # نمایش جدول قابل جستجو
    search_term = st.text_input("جستجوی نماد یا نام شرکت:")
    if search_term:
        df_filtered = df[
            df['نماد'].str.contains(search_term, case=False, na=False) |
            df['نام شرکت'].str.contains(search_term, case=False, na=False)
        ]
    else:
        df_filtered = df
        
    st.dataframe(df_filtered, use_container_width=True)
    
    # دانلود خروجی به صورت CSV
    csv = df_filtered.to_csv(index=False).encode('utf-8-sig')
    st.download_button(
        label="📥 دانلود فایل CSV",
        data=csv,
        file_name='tsetmc_shares.csv',
        mime='text/csv',
    )
else:
    st.warning("داده‌ای یافت نشد. لطفاً اتصال اینترنت و دسترسی به سایت را بررسی کنید.")
