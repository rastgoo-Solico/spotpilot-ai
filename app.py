
@st.cache_data(ttl=30, show_spinner=False)
def fetch_lbank_klines(pair, timeframe, size=300):
    """
    دریافت کندل‌های عمومی LBank.
    زمان درخواست از ابتدای بازه تاریخی محاسبه می‌شود،
    نه از زمان فعلی؛ سپس کندل‌های بسته‌شده جدا می‌شوند.
    """
    if timeframe not in TIMEFRAMES:
        raise ValueError("تایم‌فریم پشتیبانی نمی‌شود.")

    tf = TIMEFRAMES[timeframe]
    now_seconds = int(time.time())
    interval_seconds = tf["seconds"]
    size = min(max(int(size), 1), 2000)

    # API باید از یک زمان گذشته درخواست شود تا تاریخچه برگردد.
    start_seconds = now_seconds - (size + 5) * interval_seconds

    payload = get_json(
        f"{LBANK_URL}/kline.do",
        params={
            "symbol": pair,
            "type": tf["type"],
            "size": size,
            "time": start_seconds,
        },
        timeout=25,
    )

    if not is_success(payload):
        raise RuntimeError(
            f"LBank kline API error: {payload.get('msg', payload)}"
        )

    raw = payload.get("data") or []

    if not isinstance(raw, list) or not raw:
        raise RuntimeError(
            f"LBank برای {pair} در تایم‌فریم {timeframe} کندلی برنگرداند."
        )

    rows = []

    for candle in raw:
        if not isinstance(candle, (list, tuple)) or len(candle) < 6:
            continue

        timestamp = number(candle[0])

        # مستندات این endpoint زمان کندل را به ثانیه نشان می‌دهند.
        # این بررسی از تبدیل اشتباه timestamp میلی‌ثانیه‌ای جلوگیری می‌کند.
        if timestamp > 10_000_000_000:
            timestamp /= 1000

        rows.append({
            "timestamp": timestamp,
            "Open": number(candle[1]),
            "High": number(candle[2]),
            "Low": number(candle[3]),
            "Close": number(candle[4]),
            "Volume": number(candle[5]),
        })

    df = pd.DataFrame(rows)

    if df.empty:
        raise RuntimeError("ساختار داده‌های کندل قابل استفاده نیست.")

    df = (
        df.drop_duplicates(subset=["timestamp"])
        .sort_values("timestamp")
        .reset_index(drop=True)
    )

    # فقط کندل‌هایی که زمان بسته‌شدنشان گذشته است.
    cutoff = now_seconds - interval_seconds
    df = df[df["timestamp"] + interval_seconds <= cutoff].copy()
    df.reset_index(drop=True, inplace=True)

    if len(df) < 30:
        raise RuntimeError(
            f"پس از حذف کندل باز، فقط {len(df)} کندل بسته‌شده دریافت شد. "
            "بررسی جفت‌ارز، پاسخ API یا بازه زمانی لازم است."
        )

    df["Time"] = pd.to_datetime(
        df["timestamp"], unit="s", utc=True
    )

    return df
