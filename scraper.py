# scraper.py
import pandas as pd
import requests
import json
from pathlib import Path

# استفاده از API عمومی cdn.tsetmc.com که محدودیت IP ایران ندارد
BASE_URL = "https://cdn.tsetmc.com/api"

def fetch_all_symbols():
    """دریافت لیست کامل نمادها با یک درخواست."""
    url = f"{BASE_URL}/Instrument/GetInstrumentSearch/ف" # جستجو با یک حرف پرتکرار
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        data = response.json()
        # ساختار پاسخ ممکن است نیاز به بررسی داشته باشد
        if 'instrumentSearch' in data:
            return data['instrumentSearch']
        return []
    except Exception as e:
        print(f"Error fetching symbol list: {e}")
        return []

def fetch_details(ins_code):
    """دریافت جزئیات یک نماد با InsCode (تعداد سهام، نام، گروه)."""
    # Endpointهای ممکن برای جزئیات نماد
    endpoints = [
        f"{BASE_URL}/Instrument/GetInstrumentInfo/{ins_code}",
        f"{BASE_URL}/ClosingPrice/GetClosingPriceInfo/{ins_code}",
    ]
    for url in endpoints:
        try:
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                data = response.json()
                # استخراج فیلدهای مورد نیاز (نیاز به تطبیق با ساختار واقعی)
                return {
                    "نماد": data.get("lVal18AFC", ""),
                    "نام شرکت": data.get("lVal30", ""),
                    "گروه": data.get("sector", {}).get("name", "نامشخص"),
                    "تعداد سهام": data.get("sharesOutstanding", None),
                }
        except Exception:
            continue
    return None

def main():
    print("Fetching symbol list...")
    instruments = fetch_all_symbols()
    if not instruments:
        print("No instruments found.")
        return

    print(f"Found {len(instruments)} instruments. Fetching details...")
    rows = []
    for i, inst in enumerate(instruments):
        ins_code = inst.get("insCode") or inst.get("InsCode")
        if not ins_code:
            continue
        details = fetch_details(ins_code)
        if details:
            rows.append(details)
        if (i + 1) % 50 == 0:
            print(f"  Processed {i+1}/{len(instruments)}")

    if rows:
        df = pd.DataFrame(rows)
        df.to_parquet("data.parquet", index=False)
        print(f"Saved {len(df)} rows to data.parquet")
    else:
        print("No details fetched.")

if __name__ == "__main__":
    main()
