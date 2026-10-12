
import statistics
import time
import io
import os
import html
import requests
import pandas as pd
import streamlit as st
import ccxt
import plotly.graph_objects as go

from datetime import datetime, timezone
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT, TA_LEFT, TA_CENTER
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak,
    Image as ReportLabImage, LongTable, KeepTogether,
)

LCW_URL = "https://api.livecoinwatch.com"
LBANK_URL = "https://api.lbank.info/v2"
CRYPTOMETER_URL = "https://api.cryptometer.io"
OTHER_EXCHANGES = ["binance", "bybit", "okx", "kucoin", "kraken"]
QUOTE = "USDT"
PINNED_CODES = ("BTC", "XAUT", "SOL", "ETH")
PINNED_NAMES = {"BTC": "Bitcoin", "XAUT": "Tether Gold", "SOL": "Solana", "ETH": "Ethereum"}
COINGLASS_HEATMAP_URL = "https://www.coinglass.com/pro/futures/LiquidationHeatMap"

TIMEFRAMES = {
    "15 دقیقه": {"type": "minute15", "seconds": 15 * 60},
    "4 ساعت": {"type": "hour4", "seconds": 4 * 60 * 60},
    "روزانه": {"type": "day1", "seconds": 24 * 60 * 60},
}

st.set_page_config(page_title="SpotPilot AI", page_icon="📊", layout="wide")


# -------------------- Persian RTL presentation (UI only) --------------------
# Use system fonts only; no remote font dependency. Modern browsers shape Persian
# text automatically when the selected system font contains the required glyphs.
st.markdown(
    """
    <style>
    /* System fonts only: no external font downloads; browser handles Persian shaping. */
    :root {
        --spotpilot-persian-font: Tahoma, Arial, sans-serif;
    }

    html, body, .stApp,
    [data-testid="stAppViewContainer"],
    [data-testid="stHeader"],
    [data-testid="stMain"],
    [data-testid="stMainBlockContainer"],
    [data-testid="stSidebar"],
    [data-testid="stSidebarContent"],
    [data-testid="stBottom"],
    [data-testid="stPopover"],
    [data-testid="stDialog"] {
        direction: rtl;
        text-align: right;
        font-family: var(--spotpilot-persian-font) !important;
    }

    /* Apply Persian typography to Streamlit text and controls, but preserve icons. */
    .stApp p,
    .stApp label,
    .stApp button,
    .stApp input,
    .stApp textarea,
    .stApp select,
    .stApp option,
    .stApp h1, .stApp h2, .stApp h3,
    .stApp h4, .stApp h5, .stApp h6,
    .stApp li,
    .stApp td, .stApp th,
    .stApp [data-testid="stMarkdownContainer"],
    .stApp [data-testid="stWidgetLabel"],
    .stApp [data-testid="stCaptionContainer"],
    .stApp [data-testid="stAlert"],
    .stApp [data-testid="stMetric"],
    .stApp [data-testid="stExpander"],
    .stApp [data-testid="stTooltipContent"],
    [role="listbox"], [role="option"], [role="menu"], [role="menuitem"] {
        font-family: var(--spotpilot-persian-font) !important;
        direction: rtl;
        text-align: right;
        line-height: 1.65;
    }

    .block-container {
        max-width: 1500px;
        padding-top: 1.4rem;
        padding-bottom: 2.5rem;
    }

    [data-testid="stSidebar"],
    [data-testid="stSidebar"] *:not(svg):not(path):not(.material-icons):not(.material-symbols-rounded) {
        text-align: right;
    }

    /* Right-to-left order for Streamlit columns, tabs, and content blocks. */
    [data-testid="stHorizontalBlock"],
    [data-testid="stVerticalBlock"],
    [data-testid="stExpander"],
    [data-testid="stForm"],
    [data-testid="stAlert"],
    [data-testid="stNotificationContent"] {
        direction: rtl;
    }

    .stTabs [data-baseweb="tab-list"] {
        direction: rtl;
    }

    .stButton, .stDownloadButton,
    [data-testid="stButton"], [data-testid="stDownloadButton"] {
        direction: rtl;
        text-align: center;
    }

    .stSelectbox label, .stMultiSelect label, .stNumberInput label,
    .stSlider label, .stTextInput label, .stTextArea label,
    .stDateInput label, .stTimeInput label, .stRadio label,
    .stCheckbox label, .stFileUploader label {
        text-align: right;
        direction: rtl;
    }

    [data-baseweb="select"] > div,
    [data-baseweb="input"] > div,
    [data-baseweb="textarea"],
    [role="listbox"], [role="option"] {
        direction: rtl;
        text-align: right;
        font-family: var(--spotpilot-persian-font) !important;
    }

    .stMarkdown ul, .stMarkdown ol,
    [data-testid="stMarkdownContainer"] ul,
    [data-testid="stMarkdownContainer"] ol {
        padding-right: 1.5rem;
        padding-left: 0;
    }

    [data-testid="stMetricLabel"] {
        direction: rtl;
        text-align: right;
        justify-content: flex-start;
        font-family: var(--spotpilot-persian-font) !important;
    }

    /* Keep prices, percentages, dates, symbols, and exchange pairs in natural order. */
    [data-testid="stMetricValue"],
    [data-testid="stMetricDelta"] {
        direction: ltr;
        text-align: right;
        unicode-bidi: plaintext;
        font-variant-numeric: tabular-nums;
    }

    .stNumberInput input, .stDateInput input, .stTimeInput input {
        direction: ltr;
        text-align: left;
        font-variant-numeric: tabular-nums;
    }

    [data-testid="stDataFrame"],
    [data-testid="stTable"],
    [data-testid="stDataEditor"] {
        direction: rtl;
        text-align: right;
    }

    code, pre, .stCode, [data-testid="stCode"],
    .stDataFrame code, .stTable code {
        direction: ltr;
        text-align: left;
        font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace !important;
    }

    /* Never override Streamlit's icon font; SVG chart glyphs remain untouched. */
    .material-icons, .material-symbols-rounded,
    [data-testid="stIconMaterial"] {
        font-family: "Material Icons", "Material Symbols Rounded", sans-serif !important;
        direction: ltr;
    }
    </style>
    """,
    unsafe_allow_html=True,
)



# -------------------- PDF export helpers --------------------
def _pdf_rtl(value):
    """Shape Persian/Arabic text for correct display in the exported PDF."""
    text_value = "" if value is None else str(value)
    if any("\u0600" <= ch <= "\u06ff" for ch in text_value):
        try:
            import arabic_reshaper
            from bidi.algorithm import get_display
            return get_display(arabic_reshaper.reshape(text_value))
        except Exception:
            return text_value
    return text_value


def _pdf_font_name():
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    ]
    try:
        import matplotlib
        from matplotlib import font_manager
        candidates.append(font_manager.findfont("DejaVu Sans"))
    except Exception:
        pass
    for path in candidates:
        if os.path.isfile(path):
            try:
                from reportlab.pdfbase import pdfmetrics
                from reportlab.pdfbase.ttfonts import TTFont
                if "SpotPilotUnicode" not in pdfmetrics.getRegisteredFontNames():
                    pdfmetrics.registerFont(TTFont("SpotPilotUnicode", path))
                return "SpotPilotUnicode"
            except Exception:
                continue
    return "Helvetica"


def build_pdf_report(report_data):
    """Build a paginated PDF containing the visible report, tables and charts."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    font_name = _pdf_font_name()
    buffer = io.BytesIO()
    page_size = landscape(A4)

    def draw_footer(canvas, doc):
        canvas.saveState()
        canvas.setFont(font_name, 8)
        canvas.setFillColor(colors.grey)
        canvas.drawString(14 * mm, 8 * mm, "SpotPilot AI | Public market data | No auto-trading")
        canvas.drawRightString(page_size[0] - 14 * mm, 8 * mm, f"{doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(
        buffer, pagesize=page_size,
        rightMargin=13 * mm, leftMargin=13 * mm,
        topMargin=13 * mm, bottomMargin=15 * mm,
        title="SpotPilot AI - Market Analysis Report",
        author="SpotPilot AI",
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "SpotPilotTitle", parent=styles["Title"], fontName=font_name,
        fontSize=18, leading=23, alignment=TA_RIGHT, spaceAfter=5 * mm,
    )
    h_style = ParagraphStyle(
        "SpotPilotHeading", parent=styles["Heading2"], fontName=font_name,
        fontSize=12, leading=16, alignment=TA_RIGHT, spaceBefore=4 * mm,
        spaceAfter=2 * mm,
    )
    body_style = ParagraphStyle(
        "SpotPilotBody", parent=styles["BodyText"], fontName=font_name,
        fontSize=8.5, leading=12, alignment=TA_RIGHT, wordWrap="CJK",
    )
    cell_style = ParagraphStyle(
        "SpotPilotCell", parent=body_style, fontSize=7, leading=9,
        spaceAfter=0, spaceBefore=0,
    )
    header_style = ParagraphStyle(
        "SpotPilotHeader", parent=cell_style, textColor=colors.white,
        fontName=font_name, alignment=TA_CENTER,
    )
    story = []
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    story.append(Paragraph(_pdf_rtl("گزارش کامل SpotPilot AI"), title_style))
    story.append(Paragraph(_pdf_rtl(f"زمان تولید گزارش: {generated_at}"), body_style))
    story.append(Paragraph(_pdf_rtl(
        f"ارز انتخاب‌شده: {report_data.get('selected_code', '—')} | "
        f"جفت‌ارز LBank: {report_data.get('selected_pair') or 'در دسترس نیست'}"
    ), body_style))
    story.append(Spacer(1, 3 * mm))

    def add_paragraph(text):
        story.append(Paragraph(_pdf_rtl(text), body_style))
        story.append(Spacer(1, 1.5 * mm))

    def add_heading(text):
        story.append(Paragraph(_pdf_rtl(text), h_style))

    def add_table(df, max_rows=None, col_widths=None):
        if df is None:
            return
        try:
            frame = df.copy()
            if frame.empty:
                add_paragraph("داده‌ای برای نمایش وجود ندارد.")
                return
            if max_rows is not None:
                frame = frame.head(max_rows)
            headers = [Paragraph(_pdf_rtl(html.escape(str(c))), header_style) for c in frame.columns]
            rows = [headers]
            for row in frame.itertuples(index=False, name=None):
                cells = []
                for value in row:
                    if pd.isna(value):
                        value = "—"
                    lines = str(value).splitlines() or [str(value)]
                    safe = "<br/>".join(_pdf_rtl(html.escape(line)) for line in lines)
                    cells.append(Paragraph(safe, cell_style))
                rows.append(cells)
            available_width = page_size[0] - doc.leftMargin - doc.rightMargin
            if col_widths is None:
                ncols = max(1, len(frame.columns))
                col_widths = [available_width / ncols] * ncols
            else:
                total = sum(col_widths)
                col_widths = [available_width * w / total for w in col_widths]
            table_cls = LongTable if len(rows) > 35 else Table
            table = table_cls(rows, colWidths=col_widths, repeatRows=1, hAlign="LEFT")
            table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#263746")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#c7ced4")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f3f6f8")]),
            ]))
            story.append(table)
            story.append(Spacer(1, 3 * mm))
        except Exception as exc:
            add_paragraph(f"ساخت جدول {type(exc).__name__}: {exc}")

    add_heading("خلاصه بازار")
    add_paragraph(
        f"قیمت BTC/USD: {report_data.get('btc_usd', '—')} | "
        f"تعداد ارزهای دریافت‌شده: {report_data.get('fetched_count', '—')} | "
        f"تعداد ارزهای پس از فیلتر: {report_data.get('coin_count', '—')}"
    )
    add_paragraph(f"وضعیت LBank: {report_data.get('lbank_status', '—')}")

    add_heading("۱. فهرست ارزهای LiveCoinWatch")
    add_paragraph("فهرست زیر شامل همه ردیف‌های باقی‌مانده پس از اعمال فیلتر حجم فعلی است.")
    add_table(report_data.get("coins_df"), col_widths=[1.0, 1.1, 1.25, 1.25, 1.15, 1.1, 0.9])

    add_heading("۲. مقایسه قیمت صرافی‌ها")
    add_table(report_data.get("comparison_df"), col_widths=[1.2, 1.3, 1.1, 1.3])
    errors = report_data.get("compare_errors") or []
    if errors:
        add_paragraph("خطاهای دریافت مقایسه: " + " | ".join(str(e) for e in errors))

    add_heading("۳. تحلیل تکنیکال چندتایم‌فریمی")
    tf_results = report_data.get("timeframe_results") or {}
    if not tf_results:
        add_paragraph("نتیجه‌ای از تحلیل تایم‌فریم‌ها در دسترس نیست.")
    for tf_name in ["15 دقیقه", "4 ساعت", "روزانه"]:
        result = tf_results.get(tf_name)
        if not result:
            continue
        summary = result.get("summary", {})
        plan = result.get("plan", {})
        add_heading(f"تایم‌فریم {tf_name}")
        add_paragraph(
            f"زمان آخرین کندل بسته‌شده (UTC): {summary.get('time', '—')} | "
            f"قیمت بسته‌شده: {summary.get('close', '—')} | ATR: {summary.get('atr', '—')} | "
            f"حد متحرک UT: {summary.get('ut_stop', '—')}"
        )
        add_paragraph(
            f"سیگنال: {'خرید' if summary.get('buy_signal') else 'فروش' if summary.get('sell_signal') else 'بدون سیگنال تازه'} | "
            f"ساختار بازار: {summary.get('structure', '—')} | سناریو: {plan.get('status', '—')}"
        )
        if "stop" in plan:
            add_paragraph(
                f"قیمت مبنا: {plan.get('close', '—')} | حد ضرر: {plan.get('stop', '—')} | "
                f"هدف ۱: {plan.get('target1', '—')} | هدف ۲: {plan.get('target2', '—')} | "
                f"توضیح: {plan.get('reason', '—')}"
            )
        else:
            add_paragraph(f"توضیح سناریو: {plan.get('reason', '—')}")
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            indicators = result.get("indicators")
            if indicators is not None and not indicators.empty:
                chart = indicators.tail(150).copy()
                fig, ax = plt.subplots(figsize=(10, 3.1))
                for column in ["Close", "UT Stop", "SMA 14", "SMA 50", "SMA 200"]:
                    if column in chart.columns:
                        ax.plot(chart["Time"], chart[column], label=column, linewidth=1.0)
                ax.set_title(f"{report_data.get('selected_code', '')} / {tf_name}")
                ax.grid(True, alpha=0.25)
                ax.legend(loc="best", ncol=5, fontsize=7)
                fig.autofmt_xdate()
                fig.tight_layout()
                image_buffer = io.BytesIO()
                fig.savefig(image_buffer, format="png", dpi=120)
                plt.close(fig)
                image_buffer.seek(0)
                story.append(ReportLabImage(image_buffer, width=245 * mm, height=76 * mm))
                story.append(Spacer(1, 2 * mm))
        except Exception as exc:
            add_paragraph(f"نمودار این تایم‌فریم در PDF ساخته نشد: {type(exc).__name__}")

        indicators = result.get("indicators")
        if indicators is not None and not indicators.empty:
            export_cols = [c for c in ["Time", "Open", "High", "Low", "Close", "Volume", "ATR", "UT Stop", "UT Buy", "UT Sell", "Structure"] if c in indicators.columns]
            candles_table = indicators.tail(150)[export_cols].copy()
            if "Time" in candles_table.columns:
                candles_table["Time"] = pd.to_datetime(candles_table["Time"], utc=True, errors="coerce").dt.strftime("%Y-%m-%d %H:%M UTC")
            add_paragraph("داده‌های کندل و اندیکاتور مربوط به نمودار (حداکثر ۱۵۰ ردیف آخر):")
            add_table(candles_table, col_widths=[1.5] + [1.0] * (len(export_cols) - 1))

    add_heading("۴. دفتر سفارش و جریان معاملات LBank")
    add_paragraph(
        f"بهترین خرید: {report_data.get('best_bid', '—')} | بهترین فروش: {report_data.get('best_ask', '—')} | "
        f"فاصله خرید و فروش: {report_data.get('spread_pct', '—')} | عدم‌تعادل دفتر سفارش: {report_data.get('imbalance', '—')}"
    )
    add_paragraph(
        f"حجم خرید اخیر: {report_data.get('buy_usd', '—')} | حجم فروش اخیر: {report_data.get('sell_usd', '—')} | "
        f"جریان خالص: {report_data.get('net_flow', '—')} | تعداد معاملات: {report_data.get('trade_count', '—')}"
    )
    add_heading("سطوح خرید و حمایت احتمالی")
    add_table(report_data.get("bid_df"), col_widths=[1.0, 1.0, 1.5, 1.2])
    add_heading("سطوح فروش و مقاومت احتمالی")
    add_table(report_data.get("ask_df"), col_widths=[1.0, 1.0, 1.5, 1.2])

    add_heading("۵. سناریوی احتمالی ورود و خروج")
    order_plan = report_data.get("order_plan") or {}
    add_paragraph(f"وضعیت: {order_plan.get('status', '—')} | توضیح: {order_plan.get('reason', '—')}")
    if "entry" in order_plan:
        add_paragraph(
            f"ورود احتمالی: {order_plan.get('entry', '—')} | هدف خروج: {order_plan.get('target', '—')} | "
            f"حد ابطال سناریو: {order_plan.get('stop', '—')} | نسبت پاداش به ریسک: {order_plan.get('rr', '—')}"
        )
        support = order_plan.get("support") or {}
        resistance = order_plan.get("resistance") or {}
        add_paragraph(
            f"سطح خرید مبنا: {support.get('price', '—')} (ارزش {support.get('value', '—')}) | "
            f"سطح فروش مبنا: {resistance.get('price', '—')} (ارزش {resistance.get('value', '—')})"
        )
    add_heading("محدودیت‌ها و هشدار")
    add_paragraph(
        "این گزارش از داده‌های عمومی بازار استفاده می‌کند و هیچ معامله‌ای ثبت نمی‌کند. "
        "سیگنال‌ها و سطوح تحلیلی تخمینی هستند و سود را تضمین نمی‌کنند. "
        "BOS/CHoCH در این نسخه تقریبی است. کارمزد، لغزش قیمت، نقدشوندگی و احتمال لغو سفارش‌های بزرگ را در نظر بگیرید."
    )

    doc.build(story, onFirstPage=draw_footer, onLaterPages=draw_footer)
    buffer.seek(0)
    return buffer.getvalue()


# -------------------- Common helpers --------------------

def number(value, default=0.0):
    try:
        result = float(value) if value is not None else default
        return result if pd.notna(result) else default
    except (TypeError, ValueError):
        return default


def fmt_price(value):
    if value is None:
        return "—"
    try:
        value = float(value)
        if not pd.notna(value) or value <= 0:
            return "—"
        if value >= 1000:
            return f"{value:,.2f}"
        if value >= 1:
            return f"{value:,.6f}".rstrip("0").rstrip(".")
        return f"{value:.10f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return "—"


def fmt_usd(value):
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return "—"


def is_success(payload):
    result = payload.get("result")
    return result is True or str(result).lower() == "true"


def get_lcw_key():
    try:
        return str(st.secrets["LIVECOINWATCH_API_KEY"]).strip()
    except Exception:
        return ""


def get_cryptometer_key():
    """Read the CryptoMeter key from Streamlit Secrets or the environment."""
    try:
        secret = str(st.secrets.get("CRYPTOMETER_API_KEY", "")).strip()
        if secret:
            return secret
    except Exception:
        pass
    return os.environ.get("CRYPTOMETER_API_KEY", "").strip()


@st.cache_data(ttl=900, show_spinner=False)
def fetch_cryptometer_endpoint(endpoint, api_key, endpoint_params=None):
    """Fetch documented free CryptoMeter endpoints; never log or display the API key."""
    allowed_endpoints = {
        "trend-indicator-v3", "merged-orderbook", "rapid-movements",
        "24h-trade-volume-v2", "open-interest", "long-shorts-data",
    }
    if endpoint not in allowed_endpoints:
        raise ValueError("CryptoMeter endpoint is not enabled.")
    params = dict(endpoint_params or {})
    params["api_key"] = api_key
    response = requests.get(
        f"{CRYPTOMETER_URL}/{endpoint}/",
        params=params,
        headers={"accept": "application/json"},
        timeout=15,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("CryptoMeter پاسخ نامعتبر برگرداند.")
    if str(payload.get("success", "")).lower() not in {"true", "1"}:
        message = payload.get("message") or payload.get("error_message") or payload.get("error") or "خطای نامشخص API"
        raise RuntimeError(f"CryptoMeter: {str(message)[:180]}")
    data = payload.get("data")
    if data is None:
        raise RuntimeError("CryptoMeter در پاسخ، داده‌ای برنگرداند.")
    return data


def get_json(url, params=None, timeout=20):
    response = requests.get(url, params=params, timeout=timeout)
    response.raise_for_status()
    return response.json()


def lcw_post(endpoint, payload):
    key = get_lcw_key()
    if not key:
        raise RuntimeError(
            "LIVECOINWATCH_API_KEY در Streamlit Secrets تنظیم نشده است."
        )

    response = requests.post(
        f"{LCW_URL}/{endpoint}",
        headers={
            "content-type": "application/json",
            "x-api-key": key,
        },
        json=payload,
        timeout=25,
    )
    response.raise_for_status()
    return response.json()


# -------------------- LiveCoinWatch screening --------------------

@st.cache_data(ttl=60, show_spinner=False)
def fetch_lcw_market(min_btc_volume):
    btc = lcw_post(
        "coins/single",
        {"currency": "USD", "code": "BTC", "meta": False},
    )
    btc_usd = number(btc.get("rate"))

    if btc_usd <= 0:
        raise RuntimeError("قیمت BTC از LiveCoinWatch معتبر نیست.")

    all_coins = []

    for offset in range(0, 2000, 100):
        batch = lcw_post(
            "coins/list",
            {
                "currency": "USD",
                "sort": "volume",
                "order": "descending",
                "offset": offset,
                "limit": 100,
                "meta": True,
            },
        )

        if not isinstance(batch, list) or not batch:
            break

        all_coins.extend(batch)

        if len(batch) < 100:
            break

    min_usd_volume = min_btc_volume * btc_usd
    rows = []

    for coin in all_coins:
        coin_code = str(coin.get("code", "")).upper()
        volume_usd = number(coin.get("volume"))
        price_usd = number(coin.get("rate"))

        # Keep pinned symbols even when below the scanner's volume threshold.
        if volume_usd < min_usd_volume and coin_code not in PINNED_CODES:
            continue

        delta = coin.get("delta") or {}
        day_delta = delta.get("day")
        change_pct = (
            (number(day_delta) - 1) * 100
            if day_delta is not None else None
        )

        rows.append({
            "Name": coin.get("name", ""),
            "Code": coin_code,
            "Price USD": price_usd,
            "24h Change %": change_pct,
            "Volume USD": volume_usd,
            "Volume BTC equivalent": volume_usd / btc_usd,
            "Exchanges reported": coin.get("exchanges"),
            "Markets reported": coin.get("markets"),
            "Pairs reported": coin.get("pairs"),
        })

    pin_rank = {code: index for index, code in enumerate(PINNED_CODES)}
    rows.sort(
        key=lambda row: (
            0 if row["Code"] in pin_rank else 1,
            pin_rank.get(row["Code"], 999),
            -(row["24h Change %"] if row["24h Change %"] is not None else float("-inf")),
        )
    )

    return rows, btc_usd, len(all_coins)


# -------------------- Direct public LBank API --------------------

def lbank_symbol_from_code(code):
    return f"{code.lower()}_usdt"


def lbank_btc_symbol_from_code(code):
    return f"{code.lower()}_btc"


@st.cache_data(ttl=300, show_spinner=False)
def fetch_lbank_pairs():
    payload = get_json(f"{LBANK_URL}/currencyPairs.do")

    if not is_success(payload):
        raise RuntimeError(
            f"LBank currencyPairs API error: {payload.get('msg', payload)}"
        )

    raw = payload.get("data") or []
    pairs = set()

    for item in raw:
        if isinstance(item, str):
            pair = item.lower().replace("-", "_").replace("/", "_")
            if pair.endswith(("_usdt", "_btc")):
                pairs.add(pair)

        elif isinstance(item, dict):
            candidate = (
                item.get("symbol")
                or item.get("pair")
                or item.get("name")
                or ""
            )
            if candidate:
                pair = str(candidate).lower().replace("-", "_").replace("/", "_")
                if pair.endswith(("_usdt", "_btc")):
                    pairs.add(pair)

    if not pairs:
        raise RuntimeError(
            "جفت‌ارزهای USDT قابل شناسایی نبودند."
        )

    return sorted(pairs)


@st.cache_data(ttl=15, show_spinner=False)
def fetch_lbank_book(pair):
    payload = get_json(
        f"{LBANK_URL}/depth.do",
        params={"symbol": pair, "size": 100},
    )

    if not is_success(payload):
        raise RuntimeError(
            f"LBank depth API error: {payload.get('msg', payload)}"
        )

    data = payload.get("data") or {}
    bids = data.get("bids") or []
    asks = data.get("asks") or []

    if not bids or not asks:
        raise RuntimeError(f"دفتر سفارش {pair} خالی است.")

    return {
        "bids": bids,
        "asks": asks,
        "timestamp": data.get("timestamp") or payload.get("ts"),
    }


@st.cache_data(ttl=15, show_spinner=False)
def fetch_lbank_trades(pair):
    payload = get_json(
        f"{LBANK_URL}/supplement/trades.do",
        params={"symbol": pair, "size": 500},
    )

    if not is_success(payload):
        raise RuntimeError(
            f"LBank trades API error: {payload.get('msg', payload)}"
        )

    data = payload.get("data") or []
    normalized = []

    for trade in data:
        price = number(trade.get("price"))
        amount = number(trade.get("qty"))
        cost = number(trade.get("quoteQty"))

        if cost <= 0:
            cost = price * amount

        maker = trade.get("isBuyerMaker")

        if maker is True or str(maker).lower() == "true":
            side = "sell"
        elif maker is False or str(maker).lower() == "false":
            side = "buy"
        else:
            side = None

        normalized.append({
            "price": price,
            "amount": amount,
            "cost": cost,
            "side": side,
            "time": trade.get("time"),
        })

    return normalized


@st.cache_data(ttl=15, show_spinner=False)
def fetch_lbank_ticker(pair):
    payload = get_json(
        f"{LBANK_URL}/supplement/ticker/price.do",
        params={"symbol": pair},
    )

    if not is_success(payload):
        raise RuntimeError(
            f"LBank ticker API error: {payload.get('msg', payload)}"
        )

    data = payload.get("data") or []

    if isinstance(data, dict):
        data = [data]

    for ticker in data:
        if str(ticker.get("symbol", "")).lower() == pair.lower():
            return number(ticker.get("price"))

    if data:
        return number(data[0].get("price"))

    return 0.0


# -------------------- LBank candles and indicators --------------------

@st.cache_data(ttl=30, show_spinner=False)
def fetch_lbank_klines(pair, timeframe, size=300):
    """دریافت کندل‌های عمومی LBank و حذف کندل در حال تشکیل.

    پارامتر time در API کندل LBank زمان برحسب ثانیه است. برای گرفتن
    تاریخچه باید زمان شروعی در گذشته فرستاده شود؛ ارسال زمان فعلی می‌تواند
    پاسخ خالی بدهد. timestamp خروجی نیز در صورت نیاز به ثانیه نرمال می‌شود.
    """
    if timeframe not in TIMEFRAMES:
        raise ValueError("تایم‌فریم پشتیبانی نمی‌شود.")

    tf = TIMEFRAMES[timeframe]
    interval_seconds = int(tf["seconds"])
    now_seconds = int(time.time())
    requested_size = min(max(int(size), 1), 2000)

    # LBank بر اساس پارامتر time، کندل‌ها را از زمان ارسالی به بعد برمی‌گرداند.
    # کمی بافر اضافه می‌کنیم تا حذف کندل باز، تعداد کافی کندل باقی بگذارد.
    start_seconds = max(0, now_seconds - (requested_size + 5) * interval_seconds)

    payload = get_json(
        f"{LBANK_URL}/kline.do",
        params={
            "symbol": pair,
            "type": tf["type"],
            "size": requested_size,
            "time": start_seconds,
        },
        timeout=25,
    )

    if not isinstance(payload, dict) or not is_success(payload):
        message = payload.get("msg", payload) if isinstance(payload, dict) else payload
        raise RuntimeError(f"LBank kline API error: {message}")

    raw = payload.get("data") or []
    if not isinstance(raw, list) or not raw:
        raise RuntimeError(
            f"LBank برای {pair} در تایم‌فریم {timeframe} کندلی برنگرداند. "
            "پارامتر زمان، نماد و پاسخ API را بررسی کن."
        )

    rows = []
    for candle in raw:
        if not isinstance(candle, (list, tuple)) or len(candle) < 6:
            continue

        timestamp = number(candle[0])
        if timestamp <= 0:
            continue
        # مستندات LBank ثانیه را مشخص می‌کنند؛ این شرط برای پاسخ‌های
        # احتمالی با میلی‌ثانیه هم ایمنی اضافه می‌کند.
        if timestamp > 10_000_000_000:
            timestamp /= 1000.0

        open_price = number(candle[1])
        high_price = number(candle[2])
        low_price = number(candle[3])
        close_price = number(candle[4])
        volume = number(candle[5])
        if min(open_price, high_price, low_price, close_price) <= 0:
            continue

        rows.append({
            "timestamp": int(timestamp),
            "Open": open_price,
            "High": high_price,
            "Low": low_price,
            "Close": close_price,
            "Volume": volume,
        })

    df = pd.DataFrame(rows)
    if df.empty:
        raise RuntimeError("ساختار داده‌های کندل LBank قابل استفاده نیست.")

    df = (
        df.drop_duplicates(subset=["timestamp"])
        .sort_values("timestamp")
        .reset_index(drop=True)
    )

    # timestamp زمان شروع کندل است؛ تنها کندلی را نگه می‌داریم که پایانش
    # گذشته باشد. این شرط یک کندل اضافی را به اشتباه حذف نمی‌کند.
    df = df[(df["timestamp"] + interval_seconds) <= now_seconds].copy()
    df.reset_index(drop=True, inplace=True)

    # Short lookbacks (for example, 4 daily candles used only to calculate
    # 24h relative strength) must not be rejected by the 30-candle minimum
    # intended for longer technical-indicator history.
    min_required = min(30, requested_size)
    if len(df) < min_required:
        raise RuntimeError(
            f"پس از دریافت و حذف کندل باز، فقط {len(df)} کندل بسته‌شده برای "
            f"{pair} ({timeframe}) باقی ماند؛ حداقل موردنیاز برای این درخواست "
            f"{min_required} کندل است. پاسخ API یا موجود بودن این جفت‌ارز در LBank را بررسی کن."
        )

    df["Time"] = pd.to_datetime(df["timestamp"], unit="s", utc=True)
    return df


def calculate_atr(df, period=1):
    high = df["High"]
    low = df["Low"]
    close = df["Close"]
    prev_close = close.shift(1)

    true_range = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    # Wilder-style smoothing.
    atr = true_range.ewm(
        alpha=1 / max(int(period), 1),
        adjust=False,
        min_periods=max(int(period), 1),
    ).mean()

    return atr


def calculate_ut_bot(df, atr_period=1, key_value=2.0):
    """
    UT Bot ATR trailing stop, evaluated on closed candles.
    ATR period and multiplier can be changed in the sidebar.
    """
    out = df.copy()
    close = out["Close"]
    atr = calculate_atr(out, atr_period)
    loss = atr * float(key_value)

    stops = [float("nan")] * len(out)
    buy_signal = [False] * len(out)
    sell_signal = [False] * len(out)

    for i in range(len(out)):
        src = number(close.iloc[i])
        nloss = number(loss.iloc[i], float("nan"))

        if pd.isna(nloss) or nloss <= 0:
            continue

        if i == 0 or pd.isna(stops[i - 1]):
            stops[i] = src - nloss
            continue

        prev_src = number(close.iloc[i - 1])
        prev_stop = stops[i - 1]

        if src > prev_stop and prev_src > prev_stop:
            stops[i] = max(prev_stop, src - nloss)
        elif src < prev_stop and prev_src < prev_stop:
            stops[i] = min(prev_stop, src + nloss)
        elif src > prev_stop:
            stops[i] = src - nloss
        else:
            stops[i] = src + nloss

        # Crossover / crossunder of close against trailing stop.
        buy_signal[i] = src > stops[i] and prev_src <= prev_stop
        sell_signal[i] = src < stops[i] and prev_src >= prev_stop

    out["ATR"] = atr
    out["UT Stop"] = stops
    out["UT Buy"] = buy_signal
    out["UT Sell"] = sell_signal

    return out


def add_market_indicators(df, atr_period=1, key_value=2.0):
    out = calculate_ut_bot(df, atr_period, key_value)

    for period in (14, 50, 200):
        out[f"SMA {period}"] = out["Close"].rolling(
            window=period, min_periods=period
        ).mean()

    # Approximate structure breaks based on prior 20-candle extremes.
    lookback = 20
    previous_high = out["High"].shift(1).rolling(lookback).max()
    previous_low = out["Low"].shift(1).rolling(lookback).min()

    out["Prior Structure High"] = previous_high
    out["Prior Structure Low"] = previous_low
    out["BOS Up"] = out["Close"] > previous_high
    out["BOS Down"] = out["Close"] < previous_low

    # A simple trend-state approximation used to flag possible CHoCH.
    # This is not a full pivot-based reproduction of the Pine indicator.
    trend = []
    state = 0

    for i in range(len(out)):
        up = bool(out["BOS Up"].iloc[i]) if pd.notna(previous_high.iloc[i]) else False
        down = bool(out["BOS Down"].iloc[i]) if pd.notna(previous_low.iloc[i]) else False

        if up and state < 0:
            trend.append("CHoCH صعودی")
            state = 1
        elif down and state > 0:
            trend.append("CHoCH نزولی")
            state = -1
        elif up:
            trend.append("BOS صعودی")
            state = 1
        elif down:
            trend.append("BOS نزولی")
            state = -1
        else:
            trend.append("بدون شکست ساختار")

    out["Structure"] = trend
    return out


def latest_signal_summary(df):
    if df.empty:
        raise ValueError("داده‌ای برای تحلیل وجود ندارد.")

    last = df.iloc[-1]
    close = number(last["Close"])
    atr = number(last["ATR"])
    ut_stop = number(last["UT Stop"], float("nan"))

    if close <= 0 or atr <= 0 or pd.isna(ut_stop):
        status = "داده ناکافی"
    else:
        ma50 = number(last.get("SMA 50"), float("nan"))
        ma200 = number(last.get("SMA 200"), float("nan"))

        if bool(last["UT Buy"]):
            status = "سیگنال خرید UT"
        elif bool(last["UT Sell"]):
            status = "سیگنال فروش UT"
        elif close > ut_stop:
            status = "قیمت بالای حد UT"
        else:
            status = "قیمت زیر حد UT"

        if pd.notna(ma50) and pd.notna(ma200):
            if close > ma50 > ma200:
                status += " | روند MA صعودی"
            elif close < ma50 < ma200:
                status += " | روند MA نزولی"

    return {
        "status": status,
        "close": close,
        "atr": atr,
        "ut_stop": ut_stop,
        "buy_signal": bool(last["UT Buy"]),
        "sell_signal": bool(last["UT Sell"]),
        "structure": str(last["Structure"]),
        "time": last["Time"],
    }


def make_atr_trade_plan(df, key_value=2.0):
    """
    Indicative levels only:
    stop distance = ATR * multiplier
    targets = 1.5R and 2R.
    """
    summary = latest_signal_summary(df)
    close = summary["close"]
    atr = summary["atr"]

    if close <= 0 or atr <= 0:
        return {"status": "WAIT", "reason": "داده کافی برای محاسبه ATR وجود ندارد."}

    last = df.iloc[-1]
    ma50 = number(last.get("SMA 50"), float("nan"))
    ma200 = number(last.get("SMA 200"), float("nan"))

    bullish_ma = pd.notna(ma50) and pd.notna(ma200) and close > ma50 > ma200
    bearish_ma = pd.notna(ma50) and pd.notna(ma200) and close < ma50 < ma200

    if summary["buy_signal"] and not summary["sell_signal"]:
        direction = "LONG"
    elif summary["sell_signal"] and not summary["buy_signal"]:
        direction = "SHORT"
    elif bullish_ma and close > summary["ut_stop"]:
        direction = "LONG WATCH"
    elif bearish_ma and close < summary["ut_stop"]:
        direction = "SHORT WATCH"
    else:
        return {
            "status": "WAIT",
            "reason": "سیگنال UT و تأیید روند هم‌جهت نیستند؛ فعلاً ورود تأیید نشده است.",
            "close": close,
            "atr": atr,
        }

    risk = atr * float(key_value)
    if risk <= 0:
        return {"status": "WAIT", "reason": "ریسک محاسباتی معتبر نیست."}

    if direction.startswith("LONG"):
        stop = close - risk
        target1 = close + 1.5 * risk
        target2 = close + 2.0 * risk
    else:
        stop = close + risk
        target1 = close - 1.5 * risk
        target2 = close - 2.0 * risk

    is_confirmed_signal = direction in ("LONG", "SHORT")

    return {
        "status": direction,
        "reason": (
            "سیگنال UT روی آخرین کندل بسته‌شده ثبت شده است."
            if is_confirmed_signal
            else "این فقط واچ‌لیست روند است؛ سیگنال جدید UT ثبت نشده است."
        ),
        "close": close,
        "atr": atr,
        "stop": stop,
        "target1": target1,
        "target2": target2,
        "rr1": 1.5,
        "rr2": 2.0,
        "direction": direction,
    }


def get_btc_relative_metrics(code, lbank_pairs):
    """Return asset BTC value and 24h relative move from real LBank candles."""
    direct_pair = lbank_btc_symbol_from_code(code)
    btc_usdt_pair = "btc_usdt"
    asset_usdt_pair = lbank_symbol_from_code(code)

    if direct_pair in lbank_pairs:
        candles = fetch_lbank_klines(direct_pair, "روزانه", 4)
        if len(candles) >= 2:
            current = number(candles.iloc[-1]["Close"])
            previous = number(candles.iloc[-2]["Close"])
            change = (current / previous - 1) * 100 if previous > 0 else None
            return {
                "btc_value": current, "change_pct": change,
                "source_pair": direct_pair,
                "method": "جفت‌ارز مستقیم در بازار LBank",
            }

    # Derive the cross-rate only from actual asset/USDT and BTC/USDT candles.
    if asset_usdt_pair in lbank_pairs and btc_usdt_pair in lbank_pairs:
        asset = fetch_lbank_klines(asset_usdt_pair, "روزانه", 4)
        btc = fetch_lbank_klines(btc_usdt_pair, "روزانه", 4)
        if len(asset) >= 2 and len(btc) >= 2:
            asset_now = number(asset.iloc[-1]["Close"])
            asset_prev = number(asset.iloc[-2]["Close"])
            btc_now = number(btc.iloc[-1]["Close"])
            btc_prev = number(btc.iloc[-2]["Close"])
            if min(asset_now, asset_prev, btc_now, btc_prev) > 0:
                current_ratio = asset_now / btc_now
                previous_ratio = asset_prev / btc_prev
                return {
                    "btc_value": current_ratio,
                    "change_pct": (current_ratio / previous_ratio - 1) * 100,
                    "source_pair": f"{asset_usdt_pair} + {btc_usdt_pair}",
                    "method": "نرخ متقاطع محاسبه‌شده از کندل‌های واقعی USDT؛ بازار مستقیم BTC موجود نیست",
                }

    return {
        "btc_value": None, "change_pct": None,
        "source_pair": direct_pair,
        "method": "داده کافی در LBank در دسترس نیست",
    }


@st.cache_data(ttl=300, show_spinner=False)
def get_btc_xau_metrics(lbank_pairs):
    """Synthetic BTC/XAUT cross-rate calculated only from real LBank BTC/USDT and XAUT/USDT candles."""
    btc_pair = "btc_usdt"
    gold_pair = "xaut_usdt"
    if btc_pair not in lbank_pairs or gold_pair not in lbank_pairs:
        return {
            "current": None, "change_1d": None, "change_7d": None,
            "source": f"{btc_pair} + {gold_pair}",
            "method": "برای ساخت نرخ متقاطع، داده واقعی BTC/USDT و XAUT/USDT در LBank لازم است.",
        }

    btc = fetch_lbank_klines(btc_pair, "روزانه", 40)
    gold = fetch_lbank_klines(gold_pair, "روزانه", 40)
    joined = pd.merge(
        btc[["timestamp", "Close"]].rename(columns={"Close": "btc_usdt"}),
        gold[["timestamp", "Close"]].rename(columns={"Close": "xaut_usdt"}),
        on="timestamp", how="inner",
    )
    joined = joined[(joined["btc_usdt"] > 0) & (joined["xaut_usdt"] > 0)]
    if len(joined) < 8:
        return {
            "current": None, "change_1d": None, "change_7d": None,
            "source": f"{btc_pair} + {gold_pair}",
            "method": "برای محاسبه تغییر یک‌روزه و هفت‌روزه، کندل بسته‌شده کافی نیست.",
        }

    ratio = joined["btc_usdt"] / joined["xaut_usdt"]
    current = float(ratio.iloc[-1])
    prev = float(ratio.iloc[-2])
    week_ago = float(ratio.iloc[-8])
    return {
        "current": current,
        "change_1d": (current / prev - 1) * 100 if prev > 0 else None,
        "change_7d": (current / week_ago - 1) * 100 if week_ago > 0 else None,
        "source": f"{btc_pair} + {gold_pair}",
        "method": "نرخ متقاطع مصنوعی BTC/XAUT = قیمت BTC/USDT ÷ قیمت XAUT/USDT؛ قیمت جفت مستقیم ادعا نمی‌شود.",
    }


def rank_crypto_candidates(
    coins_df, lbank_pairs, atr_period=1, ut_multiplier=2.0,
    cryptometer_liquidity=None, cryptometer_whales=None,
):
    """Rank LBank-listed candidates using candles, BTC-relative strength and optional CryptoMeter flows."""
    excluded = {
        "XAUT", "USDT", "USDC", "FDUSD", "TUSD", "DAI", "USDE",
        "USDD", "USDP", "PYUSD", "EURC", "USD1", "USTC",
    }
    eligible = coins_df[
        coins_df["LBank listed"]
        & ~coins_df["Code"].astype(str).str.upper().isin(excluded)
    ].copy()
    if eligible.empty:
        return []

    eligible["Volume USD"] = pd.to_numeric(eligible["Volume USD"], errors="coerce").fillna(0)
    # Keep the three core benchmarks when listed, then add the most liquid assets.
    core = ["BTC", "ETH", "SOL"]
    selected_codes = [
        code for code in core
        if code in set(eligible["Code"].astype(str).str.upper())
    ]
    by_volume = eligible.sort_values("Volume USD", ascending=False)["Code"].astype(str).str.upper().tolist()
    for code in by_volume:
        if code not in selected_codes:
            selected_codes.append(code)
        if len(selected_codes) >= 7:
            break

    rows = []
    for code in selected_codes[:7]:
        coin_rows = eligible[eligible["Code"].astype(str).str.upper() == code]
        if coin_rows.empty:
            continue
        coin = coin_rows.iloc[0]
        day_change = number(coin.get("24h Change %"), float("nan"))
        trend = get_asset_trend_score(code, lbank_pairs, int(atr_period), float(ut_multiplier))
        relative = get_btc_relative_metrics(code, lbank_pairs)
        trend_score = trend.get("score")
        relative_change = relative.get("change_pct")
        liquidity_row = {}
        if isinstance(cryptometer_liquidity, dict):
            liquidity_row = cryptometer_liquidity.get(code, {}) or {}
        netflow_usd = number(liquidity_row.get("netflow"), None)

        whale_buy_usd = 0.0
        whale_sell_usd = 0.0
        whale_count = 0
        if isinstance(cryptometer_whales, list):
            for whale in cryptometer_whales:
                if not isinstance(whale, dict) or str(whale.get("symbol", "")).upper() != code:
                    continue
                side = str(whale.get("side", "")).upper()
                usd_value = number(whale.get("usd_total", whale.get("total")), 0.0)
                if usd_value <= 0:
                    continue
                if side == "BUY":
                    whale_buy_usd += usd_value
                    whale_count += 1
                elif side == "SELL":
                    whale_sell_usd += usd_value
                    whale_count += 1
        whale_net_usd = whale_buy_usd - whale_sell_usd

        if trend_score is None and relative_change is None and netflow_usd is None and whale_count == 0:
            score = None
        else:
            score = int(trend_score or 0)
            if relative_change is not None:
                score += 1 if relative_change > 0 else (-1 if relative_change < 0 else 0)
            if pd.notna(day_change):
                score += 1 if day_change > 0 else (-1 if day_change < 0 else 0)
            if netflow_usd is not None:
                score += 1 if netflow_usd > 0 else (-1 if netflow_usd < 0 else 0)
            if whale_count >= 2:
                score += 1 if whale_net_usd > 0 else (-1 if whale_net_usd < 0 else 0)

        flow_notes = []
        if netflow_usd is not None:
            flow_notes.append(f"CryptoMeter خالص جریان نقدینگی: {netflow_usd:,.0f} دلار")
        if whale_count:
            flow_notes.append(
                f"معاملات بزرگ: خرید {whale_buy_usd:,.0f} دلار در برابر فروش {whale_sell_usd:,.0f} دلار ({whale_count} معامله)"
            )

        if score is None:
            label = "داده ناکافی"
            reason = "برای روند و قدرت نسبی داده کافی نیست"
        elif score >= 3:
            label = "اولویت ۱: بررسی خرید"
            reason = "روند تکنیکال و قدرت نسبی بهتر؛ هنوز منتظر تأیید نقطه ورود"
        elif score >= 1:
            label = "اولویت ۲: انتظار برای تأیید"
            reason = "نشانه‌های مثبت محدود یا ترکیبی؛ خرید فوری تأیید نشده"
        elif score <= -2:
            label = "فعلاً اولویت پایین"
            reason = "روند یا قدرت نسبی ضعیف است"
        else:
            label = "خنثی: صبر"
            reason = "مزیت روشن نسبت به گزینه‌های دیگر دیده نمی‌شود"

        rows.append({
            "Code": code,
            "Score": score,
            "Priority": label,
            "24h Change %": day_change if pd.notna(day_change) else None,
            "BTC Relative %": relative_change,
            "Volume USD": number(coin.get("Volume USD")),
            "CryptoMeter Netflow USD": netflow_usd,
            "Whale Net USD": whale_net_usd if whale_count else None,
            "Reason": reason,
            "Trend notes": trend.get("notes") or [],
            "Flow notes": flow_notes,
            "Relative method": relative.get("method", ""),
        })

    rows.sort(
        key=lambda row: (
            row["Score"] is None,
            -(row["Score"] if row["Score"] is not None else -999),
            -row["Volume USD"],
        )
    )
    return rows


def get_asset_trend_score(code, lbank_pairs, atr_period=14, ut_multiplier=2.0):
    """Small rule-based trend score from closed 4h and daily LBank candles."""
    pair = lbank_symbol_from_code(code)
    if pair not in lbank_pairs:
        return {"score": None, "notes": [f"{pair} در LBank موجود نیست."]}
    score = 0
    notes = []
    for timeframe in ("4 ساعت", "روزانه"):
        try:
            candles = fetch_lbank_klines(pair, timeframe, 260)
            indicators = add_market_indicators(candles, atr_period, ut_multiplier)
            summary = latest_signal_summary(indicators)
            if summary["buy_signal"]:
                score += 2
                notes.append(f"{timeframe}: سیگنال خرید UT")
            elif summary["sell_signal"]:
                score -= 2
                notes.append(f"{timeframe}: سیگنال فروش UT")
            last = indicators.iloc[-1]
            ma50 = number(last.get("SMA 50"), float("nan"))
            ma200 = number(last.get("SMA 200"), float("nan"))
            if pd.notna(ma50) and pd.notna(ma200):
                if summary["close"] > ma50 > ma200:
                    score += 1
                    notes.append(f"{timeframe}: روند میانگین‌ها صعودی")
                elif summary["close"] < ma50 < ma200:
                    score -= 1
                    notes.append(f"{timeframe}: روند میانگین‌ها نزولی")
        except Exception as exc:
            notes.append(f"{timeframe}: داده کافی نیست ({type(exc).__name__})")
    return {"score": score, "notes": notes}


@st.cache_data(ttl=180, show_spinner=False)
def fetch_free_liquidation_heatmap(symbol="BTC", window_minutes=240, price_buckets=50):
    """Free public endpoint: observed executed liquidations, not projected liquidation levels."""
    response = requests.get(
        "https://api.smartmoneyapi.com/v1/liquidations/heatmap",
        params={
            "symbol": symbol,
            "window_minutes": int(window_minutes),
            "price_buckets": int(price_buckets),
        },
        headers={"accept": "application/json"},
        timeout=25,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        return None, "پاسخ API در قالب مورد انتظار نیست."

    prices = payload.get("price_levels") or []
    times = payload.get("time_buckets") or []
    matrix = payload.get("matrix") or []
    clusters = payload.get("clusters") or []
    if not prices or not times or not matrix:
        return {
            "clusters": clusters,
            "totals": payload.get("totals") or {},
            "by_side": payload.get("by_side") or {},
            "note": payload.get("note") or "در بازه انتخاب‌شده لیکوییدیشن ثبت‌شده کافی موجود نیست.",
            "public": payload.get("public", True),
        }, None

    # Normalize the provider's matrix to Plotly's [price rows][time columns].
    if len(matrix) == len(times) and matrix and len(matrix[0]) == len(prices):
        z = [list(row) for row in zip(*matrix)]
    elif len(matrix) == len(prices) and matrix and len(matrix[0]) == len(times):
        z = matrix
    else:
        return None, "ابعاد ماتریس لیکوییدیشن با محورهای قیمت/زمان سازگار نیست."

    x_values = []
    for value in times:
        timestamp = number(value, None)
        if timestamp is not None:
            # Normalize milliseconds if the provider ever returns them.
            if timestamp > 10_000_000_000:
                timestamp /= 1000.0
            x_values.append(datetime.fromtimestamp(timestamp, tz=timezone.utc))
        else:
            x_values.append(str(value))
    return {
        "x": x_values, "y": prices, "z": z, "clusters": clusters,
        "totals": payload.get("totals") or {},
        "by_side": payload.get("by_side") or {},
        "note": payload.get("note"), "public": payload.get("public", True),
    }, None


# -------------------- Other exchanges: comparison only --------------------

def make_exchange(exchange_id):
    exchange_class = getattr(ccxt, exchange_id)
    return exchange_class({
        "enableRateLimit": True,
        "timeout": 15000,
        "options": {"defaultType": "spot"},
    })


@st.cache_data(ttl=300, show_spinner=False)
def get_other_exchange_symbols(exchange_id):
    exchange = make_exchange(exchange_id)

    try:
        markets = exchange.load_markets()
        return [
            symbol
            for symbol, market in markets.items()
            if market.get("spot")
            and market.get("quote") == QUOTE
            and market.get("active") is not False
        ]
    finally:
        try:
            exchange.close()
        except Exception:
            pass


@st.cache_data(ttl=20, show_spinner=False)
def fetch_other_exchange_ticker(exchange_id, symbol):
    exchange = make_exchange(exchange_id)

    try:
        return exchange.fetch_ticker(symbol)
    finally:
        try:
            exchange.close()
        except Exception:
            pass


def compare_exchange_prices(code, lbank_pair=None):
    rows = []
    errors = []

    if lbank_pair:
        try:
            price = fetch_lbank_ticker(lbank_pair)
            if price > 0:
                rows.append({
                    "Exchange": "LBANK",
                    "Pair": lbank_pair,
                    "Last price": price,
                })
        except Exception as exc:
            errors.append(f"LBANK: {type(exc).__name__}: {str(exc)[:150]}")

    for exchange_id in OTHER_EXCHANGES:
        try:
            symbols = get_other_exchange_symbols(exchange_id)
            target_symbol = f"{code.upper()}/{QUOTE}"

            if target_symbol not in symbols:
                continue

            ticker = fetch_other_exchange_ticker(exchange_id, target_symbol)
            price = number(ticker.get("last"))

            if price > 0:
                rows.append({
                    "Exchange": exchange_id.upper(),
                    "Pair": target_symbol,
                    "Last price": price,
                })

        except Exception as exc:
            errors.append(
                f"{exchange_id.upper()}: {type(exc).__name__}: {str(exc)[:150]}"
            )

    if rows:
        median_price = statistics.median(
            row["Last price"] for row in rows
        )

        for row in rows:
            row["Difference from median %"] = (
                (row["Last price"] / median_price - 1) * 100
            )

    return rows, errors


# -------------------- Order book analysis --------------------

def analyze_book(book):
    bids = [
        (number(level[0]), number(level[1]))
        for level in book.get("bids", [])
        if len(level) >= 2
        and number(level[0]) > 0
        and number(level[1]) > 0
    ]
    asks = [
        (number(level[0]), number(level[1]))
        for level in book.get("asks", [])
        if len(level) >= 2
        and number(level[0]) > 0
        and number(level[1]) > 0
    ]

    if not bids or not asks:
        raise ValueError("دفتر سفارش خرید یا فروش معتبر نیست.")

    best_bid = max(price for price, qty in bids)
    best_ask = min(price for price, qty in asks)

    if best_ask < best_bid:
        raise ValueError("قیمت‌های دفتر سفارش نامعتبر یا متقاطع هستند.")

    mid = (best_bid + best_ask) / 2
    spread_pct = (best_ask - best_bid) / mid * 100

    bid_usd = sum(price * qty for price, qty in bids)
    ask_usd = sum(price * qty for price, qty in asks)
    total_usd = bid_usd + ask_usd

    imbalance = (
        (bid_usd - ask_usd) / total_usd
        if total_usd > 0 else 0
    )

    level_values = [price * qty for price, qty in bids + asks]
    median_value = statistics.median(level_values)
    sorted_values = sorted(level_values)
    percentile_90 = sorted_values[
        min(len(sorted_values) - 1, int(len(sorted_values) * 0.90))
    ]

    wall_threshold = max(median_value * 3, percentile_90)

    bid_walls = [
        {
            "price": price,
            "quantity": qty,
            "value": price * qty,
            "classification": "Large order",
        }
        for price, qty in bids
        if price * qty >= wall_threshold
    ]

    ask_walls = [
        {
            "price": price,
            "quantity": qty,
            "value": price * qty,
            "classification": "Large order",
        }
        for price, qty in asks
        if price * qty >= wall_threshold
    ]

    if not bid_walls:
        bid_walls = [
            {
                "price": p, "quantity": q, "value": p * q,
                "classification": "Largest visible bid level",
            }
            for p, q in sorted(
                bids, key=lambda level: level[0] * level[1], reverse=True
            )[:5]
        ]

    if not ask_walls:
        ask_walls = [
            {
                "price": p, "quantity": q, "value": p * q,
                "classification": "Largest visible ask level",
            }
            for p, q in sorted(
                asks, key=lambda level: level[0] * level[1], reverse=True
            )[:5]
        ]

    bid_walls.sort(key=lambda wall: wall["price"], reverse=True)
    ask_walls.sort(key=lambda wall: wall["price"])

    return {
        "bids": bids,
        "asks": asks,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "mid": mid,
        "spread_pct": spread_pct,
        "bid_usd": bid_usd,
        "ask_usd": ask_usd,
        "imbalance": imbalance,
        "bid_walls": bid_walls,
        "ask_walls": ask_walls,
        "wall_threshold": wall_threshold,
    }


def analyze_trades(trades):
    buy_usd = 0.0
    sell_usd = 0.0
    unknown_usd = 0.0
    buy_count = 0
    sell_count = 0

    for trade in trades:
        price = number(trade.get("price"))
        amount = number(trade.get("amount"))
        cost = number(trade.get("cost"))

        if cost <= 0:
            cost = price * amount

        side = trade.get("side")

        if side == "buy":
            buy_usd += cost
            buy_count += 1
        elif side == "sell":
            sell_usd += cost
            sell_count += 1
        else:
            unknown_usd += cost

    known_total = buy_usd + sell_usd

    return {
        "buy_usd": buy_usd,
        "sell_usd": sell_usd,
        "unknown_usd": unknown_usd,
        "net_flow": buy_usd - sell_usd,
        "buy_ratio": buy_usd / known_total if known_total else None,
        "buy_count": buy_count,
        "sell_count": sell_count,
        "total_count": len(trades),
    }


def calculate_trade_plan(book_stats, trade_stats):
    mid = book_stats["mid"]

    bids = [
        wall for wall in book_stats["bid_walls"]
        if wall["price"] < mid
    ]
    asks = [
        wall for wall in book_stats["ask_walls"]
        if wall["price"] > mid
    ]

    if not bids or not asks:
        return {
            "status": "WAIT",
            "reason": "برای تعیین سطوح، دفتر سفارش داده کافی ندارد.",
        }

    support = max(bids, key=lambda wall: wall["price"])
    resistance = min(asks, key=lambda wall: wall["price"])

    entry = support["price"] * 1.001
    target = resistance["price"] * 0.999
    stop = support["price"] * 0.995

    if entry >= mid or target <= entry:
        return {
            "status": "WAIT",
            "reason": "سطوح فعلی هدف بالاتری از ورود ایجاد نمی‌کنند.",
            "support": support,
            "resistance": resistance,
        }

    risk = entry - stop
    reward = target - entry
    rr = reward / risk if risk > 0 else 0
    support_distance_pct = (mid - support["price"]) / mid * 100

    confirmations = []
    warnings = []

    if book_stats["imbalance"] > 0:
        confirmations.append("ارزش دفتر سفارش به نفع خریداران است.")
    else:
        warnings.append("عدم‌تعادل دفتر سفارش به نفع خریداران نیست.")

    if trade_stats["net_flow"] > 0:
        confirmations.append("جریان خالص معاملات طبقه‌بندی‌شده مثبت است.")
    elif trade_stats["net_flow"] < 0:
        warnings.append("جریان خالص معاملات طبقه‌بندی‌شده منفی است.")
    else:
        warnings.append("جریان خالص خرید مثبت تأیید نشده است.")

    if support_distance_pct > 2:
        warnings.append("حمایت بیش از ۲٪ پایین‌تر از قیمت فعلی است.")

    if book_stats["spread_pct"] > 0.5:
        warnings.append("فاصله خرید و فروش بالاست.")

    if rr < 1.2:
        warnings.append("نسبت سود احتمالی به ریسک کمتر از ۱٫۲ است.")

    confirmed = (
        support_distance_pct <= 2
        and book_stats["spread_pct"] <= 0.5
        and rr >= 1.2
        and (
            book_stats["imbalance"] > 0
            or trade_stats["net_flow"] > 0
        )
    )

    return {
        "status": "ENTRY WATCH" if confirmed else "WAIT",
        "reason": " ".join(warnings + confirmations),
        "entry": entry,
        "target": target,
        "stop": stop,
        "rr": rr,
        "support": support,
        "resistance": resistance,
        "support_distance_pct": support_distance_pct,
    }


# -------------------- UI --------------------

st.title("📊 SpotPilot AI")
st.caption("داشبورد بررسی بازار رمزارزها با داده‌های عمومی؛ بدون ثبت سفارش و بدون معامله خودکار.")

with st.expander("راهنمای سریع: از کجا شروع کنم؟", expanded=False):
    st.markdown(
        "۱. **یک ارز انتخاب کن**؛ فهرست اولیه بر اساس حداقل حجم ۲۴ساعته فیلتر می‌شود.\n"
        "۲. **قیمت صرافی‌ها را مقایسه کن**؛ اختلاف قیمت به‌تنهایی فرصت آربیتراژ نیست و کارمزد، زمان و نقدشوندگی هم مهم‌اند.\n"
        "۳. **نمودار را بخوان**؛ کندل‌ها قیمت را نشان می‌دهند، میانگین‌ها روند را هموار می‌کنند و UT فقط یک ابزار کمکی است.\n"
        "۴. **دفتر سفارش را بررسی کن**؛ سفارش‌های نمایش‌داده‌شده ممکن است پیش از اجرا لغو شوند.\n\n"
        "**مهم:** خروجی‌ها ابزار بررسی‌اند، نه توصیه قطعی خرید یا فروش. این برنامه هیچ سفارشی ثبت نمی‌کند."
    )

with st.sidebar:
    pdf_download_placeholder = st.empty()
    st.header("فیلترهای بازار")

    min_btc_volume = st.number_input(
        "حداقل حجم ۲۴ساعته، معادل BTC",
        min_value=0.0,
        value=1000.0,
        step=100.0,
    )

    st.divider()
    with st.expander("تنظیمات پیشرفته UT Bot"):
        st.caption("اگر با این پارامترها آشنا نیستی، مقدار پیش‌فرض را تغییر نده.")
        atr_period = st.number_input(
            "دوره ATR",
            min_value=1,
            max_value=100,
            value=1,
            step=1,
            help="تعداد کندل‌های مورد استفاده برای برآورد نوسان. مقدار ۱ بسیار سریع و حساس است.",
        )

        ut_multiplier = st.number_input(
            "ضریب ATR (Key Value)",
            min_value=0.1,
            max_value=20.0,
            value=2.0,
            step=0.1,
            help="ضریب فاصله حد متحرک UT از قیمت؛ افزایش آن معمولاً فاصله را بیشتر می‌کند و سیگنال‌ها را تغییر می‌دهد.",
        )

    refresh = st.button("🔄 به‌روزرسانی داده‌ها", use_container_width=True)

if refresh:
    fetch_lcw_market.clear()
    fetch_lbank_pairs.clear()
    fetch_lbank_book.clear()
    fetch_lbank_trades.clear()
    fetch_lbank_ticker.clear()
    fetch_lbank_klines.clear()
    fetch_cryptometer_endpoint.clear()
    get_other_exchange_symbols.clear()
    fetch_other_exchange_ticker.clear()
    st.rerun()

if not get_lcw_key():
    st.error("LIVECOINWATCH_API_KEY در Streamlit Secrets تنظیم نشده است.")
    st.stop()

try:
    with st.spinner("دریافت ارزها از LiveCoinWatch..."):
        coins, btc_usd, fetched_count = fetch_lcw_market(min_btc_volume)
except Exception as exc:
    st.error(f"دریافت LiveCoinWatch ناموفق بود: {exc}")
    st.stop()

m1, m2, m3 = st.columns(3)
m1.metric("BTC/USD", fmt_usd(btc_usd))
m2.metric("ارزهای دریافت‌شده پیش از فیلتر", fetched_count)
m3.metric("ارزهای باقی‌مانده پس از فیلتر", len(coins))

if not coins:
    st.warning("با فیلتر فعلی ارزی باقی نمانده است. حداقل حجم را بررسی کن.")
    st.stop()

try:
    lbank_pairs = set(fetch_lbank_pairs())
    lbank_status = f"{sum(1 for pair in lbank_pairs if pair.endswith('_usdt'))} جفت‌ارز USDT و {sum(1 for pair in lbank_pairs if pair.endswith('_btc'))} جفت‌ارز BTC شناسایی شد"
except Exception as exc:
    lbank_pairs = set()
    lbank_status = f"دریافت جفت‌ارزهای LBank ناموفق بود: {exc}"

st.caption(f"وضعیت بازار LBank: {lbank_status}")

# Always retain pinned symbols in the list. Missing values remain blank.
existing_codes = {str(row.get("Code", "")).upper() for row in coins}
for pinned_code in PINNED_CODES:
    if pinned_code not in existing_codes:
        coins.append({
            "Name": PINNED_NAMES[pinned_code], "Code": pinned_code,
            "Price USD": None, "24h Change %": None,
            "Volume USD": None, "Volume BTC equivalent": None,
            "Exchanges reported": None, "Markets reported": None, "Pairs reported": None,
        })

coins_df = pd.DataFrame(coins)
pin_rank = {code: index for index, code in enumerate(PINNED_CODES)}
coins_df["_pin_rank"] = coins_df["Code"].map(lambda code: pin_rank.get(code, 999))
coins_df = coins_df.sort_values(
    by=["_pin_rank", "24h Change %"], ascending=[True, False], na_position="last"
).drop(columns=["_pin_rank"]).reset_index(drop=True)
coins_df["LBank listed"] = coins_df["Code"].apply(
    lambda code: lbank_symbol_from_code(code) in lbank_pairs
)

# A short, Persian-labelled view for the main screen. Raw fields stay in coins_df
# so selection and report generation continue to use the original data.
display_df = coins_df[
    [
        "Name", "Code", "Price USD", "24h Change %",
        "Volume USD", "Volume BTC equivalent", "LBank listed",
    ]
].copy()
display_df = display_df.rename(columns={
    "Name": "نام ارز",
    "Code": "نماد",
    "Price USD": "قیمت (دلار)",
    "24h Change %": "تغییر ۲۴ساعته",
    "Volume USD": "حجم ۲۴ساعته (دلار)",
    "Volume BTC equivalent": "حجم معادل BTC",
    "LBank listed": "در LBank",
})
display_df["قیمت (دلار)"] = display_df["قیمت (دلار)"].map(fmt_price)
display_df["حجم ۲۴ساعته (دلار)"] = display_df["حجم ۲۴ساعته (دلار)"].map(fmt_usd)
display_df["حجم معادل BTC"] = display_df["حجم معادل BTC"].map(
    lambda value: f"{value:,.2f}" if pd.notna(value) else "—"
)
display_df["تغییر ۲۴ساعته"] = display_df["تغییر ۲۴ساعته"].map(
    lambda value: f"{value:+.2f}%" if value is not None else "—"
)
display_df["در LBank"] = display_df["در LBank"].map(
    lambda listed: "بله" if listed else "خیر"
)

st.subheader("۱. انتخاب ارز")
st.caption(
    "چهار جفت‌ارز ثابت همیشه در ابتدای فهرست هستند: BTC/USDT، XAUT/USDT، "
    "SOL/USDT و ETH/USDT. پس از آن سایر ارزها بر اساس تغییر ۲۴ساعته نمایش داده می‌شوند. "
    "اگر داده واقعی یک نماد در دسترس نباشد، مقدار آن خالی می‌ماند."
)

event = st.dataframe(
    display_df,
    use_container_width=True,
    hide_index=True,
    height=360,
    on_select="rerun",
    selection_mode="single-row",
    key="lcw_coin_selector",
)

if event.selection.rows:
    selected_coin = coins_df.iloc[event.selection.rows[0]]
    selected_code = selected_coin["Code"]
else:
    selected_code = st.selectbox(
        "انتخاب ارز برای تحلیل",
        coins_df["Code"].tolist(),
    )
    selected_coin = coins_df[coins_df["Code"] == selected_code].iloc[0]

st.divider()
st.subheader("۲. اولویت رمزارزها برای بررسی خرید")
st.caption("این فهرست به‌صورت پویا از ارزهای موجود در فهرست شما که در LBank جفت USDT دارند ساخته می‌شود. رتبه‌ها از روند ۴ساعته/روزانه، عملکرد نسبت به BTC، تغییر ۲۴ساعته و حجم واقعی کمک می‌گیرند. داده‌های CryptoMeter رایگان در پنل جداگانه نمایش داده می‌شوند؛ «اولویت بررسی» به معنی خرید فوری نیست.")
try:
    with st.spinner("مقایسه روند و قدرت نسبی رمزارزهای برتر فهرست..."):
        crypto_ranking = rank_crypto_candidates(
            coins_df, lbank_pairs, int(atr_period), float(ut_multiplier)
        )
    if crypto_ranking:
        ranking_df = pd.DataFrame(crypto_ranking)
        best = ranking_df.iloc[0]
        if best["Score"] is not None and best["Score"] >= 3:
            st.success(f"**انتخاب اول فعلی برای بررسی: {best['Code']}** — {best['Reason']}. قبل از خرید، بخش نقطه ورود و واکنش به نقدینگی همین ارز را هم تأیید کن.")
        else:
            st.info("**فعلاً خرید فوری تأیید نشده است.** رتبه اول جدول فقط بهترین گزینه نسبی بین داده‌های موجود است؛ اگر امتیاز پایین یا داده ناکافی باشد، منتظر تأیید بمان.")
        display_ranking = ranking_df[[
            "Code", "Priority", "Score", "24h Change %", "BTC Relative %", "Reason"
        ]].copy()
        display_ranking = display_ranking.rename(columns={
            "Code": "رمزارز",
            "Priority": "اولویت",
            "Score": "امتیاز مدل",
            "24h Change %": "تغییر ۲۴ساعته (%)",
            "BTC Relative %": "تغییر نسبت به BTC (%)",
            "Reason": "دلیل ساده",
        })
        for col in ("تغییر ۲۴ساعته (%)", "تغییر نسبت به BTC (%)"):
            display_ranking[col] = display_ranking[col].map(
                lambda value: f"{value:+.2f}%" if value is not None and pd.notna(value) else "—"
            )
        display_ranking["امتیاز مدل"] = display_ranking["امتیاز مدل"].map(
            lambda value: str(value) if value is not None and pd.notna(value) else "—"
        )
        st.dataframe(display_ranking, use_container_width=True, hide_index=True)
        with st.expander("چرا این رتبه‌بندی را داده؟"):
            for item in crypto_ranking:
                st.markdown(f"**{item['Code']} — {item['Priority']}**")
                note_parts = (item["Trend notes"] or []) + (item.get("Flow notes") or [])
                st.caption("؛ ".join(note_parts[:5]) if note_parts else item["Reason"])
                st.caption(item["Relative method"])
    else:
        st.warning("از فهرست فعلی، رمزارز قابل بررسی با جفت USDT در LBank پیدا نشد.")
except Exception as exc:
    st.warning(f"رتبه‌بندی رمزارزها کامل نشد: {type(exc).__name__}: {str(exc)[:180]}")

st.divider()
st.subheader(f"۳. مقایسه قیمت {selected_code}")
st.caption(
    "این بخش قیمت‌های عمومی گزارش‌شده از چند صرافی را کنار هم می‌گذارد. "
    "اختلاف‌ها ممکن است ناشی از زمان به‌روزرسانی، بازار یا نقدشوندگی باشند."
)

selected_lbank_pair = lbank_symbol_from_code(selected_code)
selected_lbank_pair = (
    selected_lbank_pair if selected_lbank_pair in lbank_pairs else None
)

with st.spinner("مقایسه قیمت بازارهای عمومی..."):
    comparison, compare_errors = compare_exchange_prices(
        selected_code,
        selected_lbank_pair,
    )

comp_df = pd.DataFrame(comparison) if comparison else pd.DataFrame()
if comparison:
    comp_df = pd.DataFrame(comparison)
    comp_df["Last price"] = comp_df["Last price"].map(fmt_price)
    comp_df["Difference from median %"] = comp_df[
        "Difference from median %"
    ].map(lambda value: f"{value:+.3f}%")
    st.dataframe(comp_df, use_container_width=True, hide_index=True)
else:
    st.warning("قیمت قابل استفاده‌ای از صرافی‌های بررسی‌شده دریافت نشد.")

if compare_errors:
    with st.expander("جزئیات خطاهای مقایسه قیمت"):
        st.code("\n".join(compare_errors))

# -------------------- Multi-timeframe technical analysis --------------------

st.divider()
st.subheader("۴. ارزش نسبت به بیت‌کوین (BTC Pair)")
st.caption(
    "این بخش بررسی می‌کند خودِ ارز در برابر BTC قوی‌تر شده یا ضعیف‌تر؛ "
    "ممکن است قیمت دلاری بالا برود اما نسبت آن به بیت‌کوین افت کند."
)
try:
    with st.spinner("بررسی نسبت واقعی ارز به BTC در LBank..."):
        btc_relative = get_btc_relative_metrics(selected_code, lbank_pairs)
    rb1, rb2, rb3 = st.columns(3)
    rb1.metric(
        f"{selected_code} برحسب BTC",
        f"{btc_relative['btc_value']:.8f} BTC" if btc_relative["btc_value"] is not None else "—",
    )
    rb2.metric(
        "تغییر نسبت در آخرین ۲۴ ساعت بسته‌شده",
        f"{btc_relative['change_pct']:+.2f}%" if btc_relative["change_pct"] is not None else "—",
    )
    rb3.metric("منبع/بازار", btc_relative["source_pair"])
    st.caption(btc_relative["method"])
    if btc_relative["change_pct"] is not None:
        if btc_relative["change_pct"] > 0:
            st.success(f"در بازه محاسبه‌شده، {selected_code} نسبت به BTC عملکرد بهتری داشته است.")
        elif btc_relative["change_pct"] < 0:
            st.warning(f"در بازه محاسبه‌شده، {selected_code} نسبت به BTC ضعیف‌تر شده است.")
        else:
            st.info("عملکرد نسبت به BTC در بازه محاسبه‌شده تقریباً بدون تغییر بوده است.")
except Exception as exc:
    st.warning(f"تحلیل نسبت به BTC انجام نشد؛ داده واقعی کافی نیست: {type(exc).__name__}: {str(exc)[:180]}")

if selected_code in ("BTC", "XAUT"):
    st.markdown("#### نسبت بیت‌کوین به طلا (BTC/XAUT)")
    try:
        btc_xau = get_btc_xau_metrics(lbank_pairs)
        if btc_xau["current"] is None:
            st.info(btc_xau["method"])
        else:
            q1, q2, q3 = st.columns(3)
            q1.metric("BTC/XAUT مصنوعی", f"{btc_xau['current']:.4f} XAUT برای هر BTC")
            q2.metric("تغییر یک‌روزه", f"{btc_xau['change_1d']:+.2f}%")
            q3.metric("تغییر هفت‌روزه", f"{btc_xau['change_7d']:+.2f}%")
            st.caption(btc_xau["method"])
            if btc_xau["change_7d"] >= 2 and btc_xau["change_1d"] > 0:
                st.success("قدرت نسبی فعلاً به نفع بیت‌کوین است: با هر BTC طلای بیشتری می‌توان خرید. این به‌تنهایی سیگنال ورود نیست.")
            elif btc_xau["change_7d"] <= -2 and btc_xau["change_1d"] < 0:
                st.warning("قدرت نسبی فعلاً به نفع طلا (XAUT) است: ارزش BTC نسبت به طلا افت کرده است.")
            else:
                st.info("برتری نسبی روشن نیست؛ برای تصمیم، روند ۴ساعته/روزانه و نقاط ورود را هم تأیید کن.")
    except Exception as exc:
        st.warning(f"محاسبه BTC/XAUT انجام نشد: {type(exc).__name__}: {str(exc)[:160]}")

st.divider()
st.subheader("۵. تحلیل تکنیکال")
st.caption(
    "۱۵ دقیقه برای حرکت کوتاه‌مدت، ۴ ساعت برای روند میان‌مدت و روزانه برای تصویر بزرگ‌تر است. "
    "سیگنال‌ها از کندل‌های بسته‌شده محاسبه می‌شوند؛ هیچ شاخصی نتیجه را تضمین نمی‌کند."
)

if not selected_lbank_pair:
    st.warning(
        f"{selected_code} در فهرست جفت‌ارزهای USDT در LBank پیدا نشد. "
        "تحلیل تکنیکال LBank برای این ارز در دسترس نیست."
    )
else:
    st.write(f"جفت‌ارز تحلیل: **{selected_lbank_pair}**")

    tf_tabs = st.tabs(["۱۵ دقیقه", "۴ ساعت", "روزانه"])
    tf_names = ["15 دقیقه", "4 ساعت", "روزانه"]
    timeframe_results = {}

    for tab, tf_name in zip(tf_tabs, tf_names):
        with tab:
            try:
                with st.spinner(f"دریافت کندل‌های {tf_name}..."):
                    candles = fetch_lbank_klines(
                        selected_lbank_pair,
                        tf_name,
                        300,
                    )
                    indicators = add_market_indicators(
                        candles,
                        int(atr_period),
                        float(ut_multiplier),
                    )
                    summary = latest_signal_summary(indicators)
                    plan = make_atr_trade_plan(
                        indicators,
                        float(ut_multiplier),
                    )

                timeframe_results[tf_name] = {
                    "candles": candles,
                    "indicators": indicators,
                    "summary": summary,
                    "plan": plan,
                }

                if summary["buy_signal"]:
                    st.success("سیگنال UT Bot خرید روی آخرین کندل بسته‌شده")
                elif summary["sell_signal"]:
                    st.error("سیگنال UT Bot فروش روی آخرین کندل بسته‌شده")
                else:
                    st.info("روی آخرین کندل بسته‌شده سیگنال تازه UT ثبت نشده است.")

                a, b, c, d = st.columns(4)
                a.metric("آخرین قیمت بسته‌شده", fmt_price(summary["close"]))
                b.metric("ATR", fmt_price(summary["atr"]))
                c.metric("حد متحرک UT", fmt_price(summary["ut_stop"]))
                d.metric("وضعیت", plan["status"])

                st.caption(
                    f"زمان آخرین کندل بسته‌شده (UTC): "
                    f"{summary['time'].strftime('%Y-%m-%d %H:%M')}"
                )

                last = indicators.iloc[-1]
                st.markdown("#### میانگین‌های متحرک؛ قیمت نسبت به روند کجاست؟")
                st.caption(
                    "میانگین متحرک قیمت را هموار می‌کند. درصد کنار هر عدد نشان می‌دهد "
                    "قیمت آخرین کندل چند درصد بالاتر یا پایین‌تر از آن میانگین است. "
                    "عدد ۱۴، ۵۰ یا ۲۰۰ یعنی تعداد کندل‌ها، نه تعداد روزها؛ پس معنی آن با تایم‌فریم تغییر می‌کند."
                )
                ma_cols = st.columns(3)
                ma_explanations = {
                    14: "کوتاه‌مدت؛ سریع‌تر به تغییرات قیمت واکنش نشان می‌دهد.",
                    50: "میان‌مدت؛ برای دیدن جهت کلی‌تر حرکت قیمت.",
                    200: "بلندمدت؛ برای بررسی روند بزرگ‌تر، به تاریخچه کافی نیاز دارد.",
                }
                ma_values = {}
                for col, period in zip(ma_cols, (14, 50, 200)):
                    ma_value = number(last.get(f"SMA {period}"), float("nan"))
                    ma_values[period] = ma_value
                    if pd.isna(ma_value) or ma_value <= 0:
                        col.metric(f"SMA {period}", "داده کافی نیست")
                        col.caption(ma_explanations[period])
                    else:
                        distance_pct = (summary["close"] / ma_value - 1) * 100
                        col.metric(
                            f"SMA {period}",
                            fmt_price(ma_value),
                            delta=f"{distance_pct:+.2f}% قیمت نسبت به میانگین",
                            help=ma_explanations[period],
                        )
                        col.caption(ma_explanations[period])

                valid_ma = {
                    period: value for period, value in ma_values.items()
                    if pd.notna(value) and value > 0
                }
                if 50 in valid_ma and 200 in valid_ma:
                    if summary["close"] > valid_ma[50] > valid_ma[200]:
                        st.success("برداشت ساده: قیمت بالاتر از میانگین‌های ۵۰ و ۲۰۰ است؛ وضعیت روند فعلاً متمایل به صعودی است.")
                    elif summary["close"] < valid_ma[50] < valid_ma[200]:
                        st.warning("برداشت ساده: قیمت پایین‌تر از میانگین‌های ۵۰ و ۲۰۰ است؛ وضعیت روند فعلاً متمایل به نزولی است.")
                    else:
                        st.info("برداشت ساده: میانگین‌ها هم‌جهت نیستند؛ روند یک‌دست نیست یا بازار در حال تغییر جهت است.")
                else:
                    st.info("برای برداشت روند میان‌مدت و بلندمدت، ابتدا باید داده کافی برای SMA 50 و SMA 200 دریافت شود.")

                with st.expander("راهنمای خیلی کوتاه میانگین‌های متحرک"):
                    st.markdown(
                        "- **قیمت بالاتر از میانگین:** نشانه‌ای از قدرت نسبی قیمت است، نه تضمین رشد.\n"
                        "- **قیمت پایین‌تر از میانگین:** نشانه‌ای از ضعف نسبی قیمت است، نه پیش‌بینی قطعی ریزش.\n"
                        "- **SMA 14:** سریع‌تر واکنش نشان می‌دهد، اما نویز بیشتری هم دارد.\n"
                        "- **SMA 50:** برای دیدن روند میان‌مدت کاربرد دارد.\n"
                        "- **SMA 200:** دید بلندمدت می‌دهد و به تاریخچه بیشتری نیاز دارد.\n"
                        "- میانگین‌ها عقب‌نگرند؛ بهتر است همراه با ساختار قیمت، حجم و مدیریت ریسک بررسی شوند."
                    )

                st.write(f"**ساختار بازار:** {summary['structure']}")

                if "stop" in plan:
                    p1, p2, p3, p4 = st.columns(4)
                    p1.metric("قیمت مبنای سناریو", fmt_price(plan["close"]))
                    p2.metric("حد ضرر تحلیلی", fmt_price(plan["stop"]))
                    p3.metric("هدف ۱ (۱٫۵R)", fmt_price(plan["target1"]))
                    p4.metric("هدف ۲ (۲R)", fmt_price(plan["target2"]))
                    st.write(plan["reason"])
                else:
                    st.warning(plan["reason"])

                st.markdown("#### نمودار قیمت و میانگین‌ها")
                chart_df = indicators.tail(150).copy()
                fig = go.Figure()
                fig.add_trace(go.Candlestick(
                    x=chart_df["Time"],
                    open=chart_df["Open"],
                    high=chart_df["High"],
                    low=chart_df["Low"],
                    close=chart_df["Close"],
                    name="قیمت",
                    increasing_line_color="#16a34a",
                    decreasing_line_color="#dc2626",
                ))
                ma_styles = {
                    14: ("#2563eb", "SMA 14 — کوتاه‌مدت"),
                    50: ("#f59e0b", "SMA 50 — میان‌مدت"),
                    200: ("#9333ea", "SMA 200 — بلندمدت"),
                }
                for period, (line_color, line_name) in ma_styles.items():
                    column_name = f"SMA {period}"
                    if column_name in chart_df.columns and chart_df[column_name].notna().any():
                        fig.add_trace(go.Scatter(
                            x=chart_df["Time"],
                            y=chart_df[column_name],
                            mode="lines",
                            name=line_name,
                            line={"color": line_color, "width": 2},
                            connectgaps=False,
                        ))
                if "UT Stop" in chart_df.columns:
                    fig.add_trace(go.Scatter(
                        x=chart_df["Time"],
                        y=chart_df["UT Stop"],
                        mode="lines",
                        name="حد متحرک UT",
                        line={"color": "#64748b", "width": 1.5, "dash": "dash"},
                    ))
                buy_points = chart_df[chart_df["UT Buy"].astype(bool)]
                sell_points = chart_df[chart_df["UT Sell"].astype(bool)]
                if not buy_points.empty:
                    fig.add_trace(go.Scatter(
                        x=buy_points["Time"], y=buy_points["Low"] * 0.998,
                        mode="markers", name="علامت خرید UT",
                        marker={"symbol": "triangle-up", "size": 10, "color": "#16a34a"},
                    ))
                if not sell_points.empty:
                    fig.add_trace(go.Scatter(
                        x=sell_points["Time"], y=sell_points["High"] * 1.002,
                        mode="markers", name="علامت فروش UT",
                        marker={"symbol": "triangle-down", "size": 10, "color": "#dc2626"},
                    ))
                fig.update_layout(
                    height=520,
                    margin={"l": 12, "r": 12, "t": 28, "b": 12},
                    hovermode="x unified",
                    xaxis_title="زمان (UTC)",
                    yaxis_title="قیمت",
                    legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "xanchor": "left", "x": 0},
                    xaxis={"rangeslider": {"visible": False}, "showgrid": True},
                    yaxis={"side": "right", "showgrid": True},
                    template="plotly_white",
                )
                st.plotly_chart(
                    fig,
                    use_container_width=True,
                    config={"displaylogo": False, "scrollZoom": True},
                )

                signal_table = indicators.tail(12)[
                    [
                        "Time", "Open", "High", "Low", "Close", "Volume",
                        "ATR", "UT Stop", "UT Buy", "UT Sell", "Structure",
                    ]
                ].copy()

                signal_table["Time"] = signal_table["Time"].dt.strftime(
                    "%Y-%m-%d %H:%M UTC"
                )

                for col in ["Open", "High", "Low", "Close", "ATR", "UT Stop"]:
                    signal_table[col] = signal_table[col].map(fmt_price)

                with st.expander("جزئیات کندل‌های اخیر (برای بررسی پیشرفته)"):
                    st.caption("۱۲ کندل بسته‌شدهٔ اخیر؛ زمان‌ها بر اساس UTC هستند.")
                    st.dataframe(
                        signal_table,
                        use_container_width=True,
                        hide_index=True,
                    )

            except Exception as exc:
                st.error(
                    f"تحلیل تایم‌فریم {tf_name} ناموفق بود: "
                    f"{type(exc).__name__}: {str(exc)[:300]}"
                )

    # Cross-timeframe overview.
    if timeframe_results:
        st.markdown("#### خلاصه هم‌جهتی تایم‌فریم‌ها")
        overview = []

        for tf_name in tf_names:
            result = timeframe_results.get(tf_name)
            if not result:
                continue

            summary = result["summary"]
            plan = result["plan"]
            overview.append({
                "تایم‌فریم": tf_name,
                "قیمت بسته‌شده": fmt_price(summary["close"]),
                "سیگنال UT": (
                    "خرید" if summary["buy_signal"]
                    else "فروش" if summary["sell_signal"]
                    else "بدون سیگنال تازه"
                ),
                "ساختار": summary["structure"],
                "سناریو": plan["status"],
            })

        st.dataframe(
            pd.DataFrame(overview),
            use_container_width=True,
            hide_index=True,
        )

        buy_count = sum(
            1 for result in timeframe_results.values()
            if result["summary"]["buy_signal"]
        )
        sell_count = sum(
            1 for result in timeframe_results.values()
            if result["summary"]["sell_signal"]
        )

        if buy_count >= 2:
            st.success(
                f"هم‌جهتی اولیه: سیگنال خرید UT در {buy_count} تایم‌فریم ثبت شده است. "
                "پیش از هر تصمیم، روند، نقدشوندگی و حد ضرر را بررسی کن."
            )
        elif sell_count >= 2:
            st.warning(
                f"هم‌جهتی نزولی: سیگنال فروش UT در {sell_count} تایم‌فریم ثبت شده است."
            )
        else:
            st.info(
                "هنوز دست‌کم دو تایم‌فریم سیگنال هم‌جهت تازه ندارند."
            )

# -------------------- Order book and liquidation liquidity --------------------

st.divider()
st.subheader("۶. نقدینگی و معاملات اخیر LBank")
st.caption(
    "دفتر سفارش LBank نقدینگیِ سفارش‌های قابل‌مشاهده همین صرافی را نشان می‌دهد. "
    "نقشه لیکوییدیشن رایگان، معاملات لیکوییدشده واقعی را از چند بازار فیوچرز جمع می‌کند؛ "
    "این داده با سفارش‌های اسپات یا برآورد موقعیت‌های هنوز لیکوییدنشده فرق دارد."
)

with st.expander("جریان نقدینگی و فشار خرید/فروش — CryptoMeter.io", expanded=False):
    st.markdown(
        "این بخش داده‌های تجمیعی CryptoMeter را کنار دفتر سفارش LBank قرار می‌دهد. "
        "برای جلوگیری از درخواست‌های تکراری، داده‌ها تا ۵ دقیقه cache می‌شوند. "
        "کلید API را فقط در Streamlit Secrets نگه دار."
    )
    cryptometer_key = get_cryptometer_key()
    if not cryptometer_key:
        st.info(
            "برای فعال‌کردن این بخش، یک کلید رایگان از "
            "[مدیریت API در CryptoMeter.io](https://www.cryptometer.io/api-manage) بگیر "
            "و در Streamlit → App settings → Secrets مقدار CRYPTOMETER_API_KEY را تنظیم کن."
        )
        st.code('CRYPTOMETER_API_KEY = "کلید-شما"', language="toml")
    else:
        try:
            with st.spinner("دریافت داده‌های تجمیعی CryptoMeter..."):
                trend_data = fetch_cryptometer_endpoint("trend-indicator-v3", cryptometer_key)
                book_data = fetch_cryptometer_endpoint("merged-orderbook", cryptometer_key)
                movement_data = fetch_cryptometer_endpoint("rapid-movements", cryptometer_key)

            trend_rows = trend_data if isinstance(trend_data, list) else [trend_data]
            trend_rows = [row for row in trend_rows if isinstance(row, dict)]
            trend = trend_rows[-1] if trend_rows else {}

            if trend:
                buy_pressure = number(trend.get("buy_pressure"), None)
                sell_pressure = number(trend.get("sell_pressure"), None)
                trend_score = number(trend.get("trend_score"), None)
                tc1, tc2, tc3 = st.columns(3)
                tc1.metric("امتیاز روند تجمیعی", f"{trend_score:.2f}" if trend_score is not None else "—")
                tc2.metric("فشار خرید", f"{buy_pressure:.2f}%" if buy_pressure is not None else "—")
                tc3.metric("فشار فروش", f"{sell_pressure:.2f}%" if sell_pressure is not None else "—")
                if trend.get("timestamp"):
                    st.caption(f"زمان ثبت داده CryptoMeter: {trend['timestamp']}")
                if buy_pressure is not None and sell_pressure is not None:
                    if buy_pressure > sell_pressure:
                        st.info("در شاخص تجمیعی CryptoMeter فشار خرید اندکی بیشتر گزارش شده است؛ این شاخص به‌تنهایی سیگنال ورود نیست.")
                    elif sell_pressure > buy_pressure:
                        st.info("در شاخص تجمیعی CryptoMeter فشار فروش اندکی بیشتر گزارش شده است؛ این شاخص به‌تنهایی سیگنال خروج نیست.")
                    else:
                        st.info("فشار خرید و فروش در شاخص تجمیعی CryptoMeter برابر گزارش شده است.")
            else:
                st.warning("CryptoMeter در حال حاضر داده‌ای برای شاخص روند برنگرداند.")

            order_rows = book_data if isinstance(book_data, list) else []
            normalized_rows = []
            for row in order_rows:
                if not isinstance(row, dict):
                    continue
                symbol = str(row.get("symbol", "")).upper()
                bids = number(row.get("bids"), None)
                asks = number(row.get("asks"), None)
                if not symbol or bids is None or asks is None:
                    continue
                total = bids + asks
                imbalance_pct = ((bids - asks) / total * 100) if total > 0 else None
                normalized_rows.append({
                    "رمزارز": symbol,
                    "ارزش سفارش‌های خرید (BTC)": bids,
                    "ارزش سفارش‌های فروش (BTC)": asks,
                    "عدم‌تعادل خرید/فروش (%)": round(imbalance_pct, 2) if imbalance_pct is not None else None,
                })
            if normalized_rows:
                book_df = pd.DataFrame(normalized_rows)
                book_df = book_df.sort_values(
                    "ارزش سفارش‌های خرید (BTC)", ascending=False
                ).head(15)
                st.markdown("**دفتر سفارش تجمیعی چند صرافی**")
                st.caption(
                    "مقادیر bids/asks طبق مستندات CryptoMeter بر حسب BTC هستند و داده تجمیعی چند صرافی‌اند؛ "
                    "این جدول نه مختص LBank است و نه جریان خالص معاملات اجراشده."
                )
                st.dataframe(book_df, use_container_width=True, hide_index=True)
            else:
                st.warning("CryptoMeter در حال حاضر داده‌ای برای دفتر سفارش تجمیعی برنگرداند.")
            movement_rows = movement_data if isinstance(movement_data, list) else []
            movement_rows = [row for row in movement_rows if isinstance(row, dict)]
            st.markdown("**حرکت‌های سریع بازار در ۴ ساعت اخیر (API رایگان)**")
            if movement_rows:
                movement_df = pd.DataFrame([{
                    "جفت‌ارز": row.get("pair", "—"),
                    "صرافی": row.get("exchange", "—"),
                    "جهت حرکت": row.get("side", "—"),
                    "تغییر تشخیص‌داده‌شده (%)": number(row.get("change_detected"), None),
                    "زمان": row.get("timestamp", "—"),
                } for row in movement_rows[:30]])
                st.dataframe(movement_df, use_container_width=True, hide_index=True)
            else:
                st.info("در آخرین پاسخ CryptoMeter حرکت سریع قابل‌نمایشی وجود نداشت.")
            st.caption(
                "این APIهای بدون برچسب Paid در مستندات استفاده می‌شوند. Volume Flow، Liquidity Lens و xTrades API پولی‌اند و در این نسخه فراخوانی نمی‌شوند. "
                "فشار روند و دفتر سفارش تجمیعی مربوط به کل بازار/چند صرافی‌اند، نه صرفاً LBank."
            )
        except Exception as exc:
            st.warning(
                "دریافت داده CryptoMeter ناموفق بود؛ کلید، سهمیه ماهانه یا دسترس‌پذیری سرویس را بررسی کن. "
                f"جزئیات: {type(exc).__name__}: {str(exc)[:180]}"
            )

with st.expander("نقشه رایگان لیکوییدیشن BTC (بدون کلید API)", expanded=False):
    st.markdown(
        "این نقشه از API عمومی SmartMoneyAPI استفاده می‌کند؛ طبق مستندات ارائه‌دهنده، "
        "بدون کلید و رایگان است اما محدودیت درخواست بر اساس IP دارد. "
        "برای نقشه تخمینی سطوح احتمالی (نه لیکوییدیشن‌های انجام‌شده)، "
        "[LiqMap رایگان و متن‌باز را باز کن](https://liqmap.smithblock.ai/). "
        f"همچنین [نقشه CoinGlass]({COINGLASS_HEATMAP_URL}) برای مشاهده دستی در دسترس است."
    )
    try:
        heatmap, heatmap_error = fetch_free_liquidation_heatmap("BTC", 240, 50)
        if heatmap:
            totals = heatmap.get("totals") or {}
            sides = heatmap.get("by_side") or {}
            m1, m2, m3 = st.columns(3)
            m1.metric("مجموع لیکوییدیشن ثبت‌شده", fmt_usd(number(totals.get("total_notional"))))
            m2.metric("لیکوییدیشن پوزیشن‌های لانگ", fmt_usd(number(sides.get("long", totals.get("long_liq_notional")))))
            m3.metric("لیکوییدیشن پوزیشن‌های شورت", fmt_usd(number(sides.get("short", totals.get("short_liq_notional")))))
            if heatmap.get("z"):
                fig_liq = go.Figure(data=go.Heatmap(
                    x=heatmap["x"], y=heatmap["y"], z=heatmap["z"],
                    colorscale="Turbo", colorbar={"title": "لیکوییدیشن ثبت‌شده"},
                    hovertemplate="زمان: %{x}<br>قیمت: %{y:,.2f}<br>مقدار: %{z:,.0f}<extra></extra>",
                ))
                fig_liq.update_layout(
                    title="BTC — لیکوییدیشن‌های واقعی ثبت‌شده در ۴ ساعت اخیر",
                    xaxis_title="زمان (UTC)", yaxis_title="قیمت (USDT)", height=500,
                )
                st.plotly_chart(fig_liq, use_container_width=True)
            clusters = heatmap.get("clusters") or []
            if clusters:
                cluster_rows = []
                for cluster in clusters[:12]:
                    cluster_rows.append({
                        "قیمت": fmt_price(number(cluster.get("price"))),
                        "مبلغ لیکوییدشده": fmt_usd(number(cluster.get("notional"))),
                        "لانگ": fmt_usd(number(cluster.get("long_notional"))),
                        "شورت": fmt_usd(number(cluster.get("short_notional"))),
                        "تعداد": int(number(cluster.get("count"))),
                    })
                st.dataframe(pd.DataFrame(cluster_rows), use_container_width=True, hide_index=True)
            elif heatmap.get("note"):
                st.info(str(heatmap["note"]))
            long_total = number(sides.get("long", totals.get("long_liq_notional")))
            short_total = number(sides.get("short", totals.get("short_liq_notional")))
            if long_total > 0 or short_total > 0:
                if short_total >= long_total * 1.25 and short_total > 0:
                    st.info("تفسیر گذشته‌نگر: لیکوییدیشن شورت‌ها بیشتر بوده؛ در این بازه خریدهای اجباریِ بستن شورت غالب بوده‌اند. این ثابت نمی‌کند که قیمت بعداً به نقدینگی بالاتر می‌رود.")
                elif long_total >= short_total * 1.25 and long_total > 0:
                    st.info("تفسیر گذشته‌نگر: لیکوییدیشن لانگ‌ها بیشتر بوده؛ در این بازه فروش‌های اجباریِ بستن لانگ غالب بوده‌اند. این ثابت نمی‌کند که قیمت بعداً به نقدینگی پایین‌تر می‌رود.")
                else:
                    st.info("تفسیر گذشته‌نگر: لیکوییدیشن لانگ و شورت نسبتاً نزدیک‌اند؛ از این داده به‌تنهایی جهت بعدی قابل استنتاج نیست.")
            st.caption(
                "این داده‌ها لیکوییدیشن‌هایی هستند که واقعاً در جریان زنده صرافی‌ها ثبت شده‌اند؛ "
                "آن‌ها محل تجمع سفارش‌های آینده یا پیش‌بینی قیمت نیستند. برای سطح‌های تخمینیِ هنوز مصرف‌نشده، "
                "نقشه LiqMap را جداگانه بررسی کن. بازارهای منبع شامل Binance، OKX، Bybit، Bitget و BitMEX هستند "
                "و لزوماً مختص LBank نیستند."
            )
        else:
            st.info(f"نقشه رایگان فعلاً در دسترس نیست: {heatmap_error}")
    except Exception as exc:
        st.warning(
            "دریافت نقشه رایگان ناموفق بود؛ ممکن است API موقتاً در دسترس نباشد یا محدودیت IP فعال شده باشد. "
            f"جزئیات: {type(exc).__name__}: {str(exc)[:180]}"
        )

if not selected_lbank_pair:
    st.warning(
        f"{selected_code} در فهرست جفت‌ارزهای USDT دریافتی از LBank پیدا نشد. "
        "تحلیل دفتر سفارش برای این جفت‌ارز در دسترس نیست."
    )
    st.stop()

st.write(f"جفت‌ارز LBank: **{selected_lbank_pair}**")

try:
    with st.spinner("دریافت دفتر سفارش عمومی LBank..."):
        book = fetch_lbank_book(selected_lbank_pair)
        book_stats = analyze_book(book)
except Exception as exc:
    st.error(f"دریافت یا تحلیل دفتر سفارش ناموفق بود: {exc}")
    st.stop()

try:
    with st.spinner("دریافت معاملات اخیر LBank..."):
        trades = fetch_lbank_trades(selected_lbank_pair)
        trade_stats = analyze_trades(trades)
except Exception as exc:
    trade_stats = analyze_trades([])
    st.warning(
        "دفتر سفارش دریافت شد، اما معاملات اخیر قابل دریافت نبودند. "
        f"جزئیات: {type(exc).__name__}: {str(exc)[:250]}"
    )

metrics = st.columns(4)
metrics[0].metric(
    "بهترین خرید", fmt_price(book_stats["best_bid"]),
    help="بالاترین قیمت خریدِ موجود در دفتر سفارش.",
)
metrics[1].metric(
    "بهترین فروش", fmt_price(book_stats["best_ask"]),
    help="پایین‌ترین قیمت فروشِ موجود در دفتر سفارش.",
)
metrics[2].metric(
    "فاصله خرید و فروش", f"{book_stats['spread_pct']:.4f}%",
    help="فاصله نسبی بهترین قیمت فروش و خرید؛ فاصله کمتر معمولاً اجرای معامله را کم‌هزینه‌تر می‌کند، اما کافی نیست.",
)
metrics[3].metric(
    "عدم‌تعادل دفتر سفارش",
    f"{book_stats['imbalance'] * 100:+.2f}%",
    help="مقایسه ارزش سفارش‌های خرید و فروشِ قابل‌مشاهده؛ سفارش‌ها ممکن است لغو شوند.",
)

if book_stats["imbalance"] > 0:
    st.info("در لحظه دریافت داده، ارزش سفارش‌های خریدِ قابل‌مشاهده بیشتر است؛ این به‌تنهایی به معنی رشد قیمت نیست.")
elif book_stats["imbalance"] < 0:
    st.info("در لحظه دریافت داده، ارزش سفارش‌های فروشِ قابل‌مشاهده بیشتر است؛ این به‌تنهایی به معنی افت قیمت نیست.")
else:
    st.info("در لحظه دریافت داده، عدم‌تعادل دفتر سفارش نزدیک به صفر است.")

# Plain-language liquidity interpretation: visible order-book walls, not future liquidation estimates.
mid_price = number(book_stats.get("mid"))
# Only call a level a wall when it passes the significance threshold. The fallback
# rows are useful in the detailed table but may simply be ordinary top-of-book levels.
near_bids = [
    w for w in book_stats.get("bid_walls", [])
    if number(w.get("price")) < mid_price and w.get("classification") == "Large order"
]
near_asks = [
    w for w in book_stats.get("ask_walls", [])
    if number(w.get("price")) > mid_price and w.get("classification") == "Large order"
]
nearest_bid = max(near_bids, key=lambda w: number(w.get("price"))) if near_bids else None
nearest_ask = min(near_asks, key=lambda w: number(w.get("price"))) if near_asks else None
st.markdown("#### جمع‌بندی ساده نقدینگی")
st.caption(
    f"مرجع فاصله، قیمت میانی بهترین خرید و فروش LBank است: {fmt_price(mid_price)}. "
    "در این جمع‌بندی فقط سفارش‌هایی دیوار محسوب می‌شوند که از آستانه آماری اندازه سفارش عبور کنند؛ "
    "سطوح عادی دفتر سفارش در جدول جزئیات می‌مانند. این تحلیل پیش‌بینی قطعی جهت قیمت نیست."
)
def format_wall_distance(distance):
    if distance < 0.0001:
        return "<0.0001%"
    if distance < 0.01:
        return f"{distance:.4f}%"
    if distance < 1:
        return f"{distance:.3f}%"
    return f"{distance:.2f}%"

if nearest_bid and nearest_ask:
    bid_distance = (mid_price - number(nearest_bid["price"])) / mid_price * 100
    ask_distance = (number(nearest_ask["price"]) - mid_price) / mid_price * 100
    liq_a, liq_b = st.columns(2)
    liq_a.metric("دیوار خرید زیر قیمت", fmt_price(nearest_bid["price"]), delta=f"{format_wall_distance(bid_distance)} پایین‌تر")
    liq_b.metric("دیوار فروش بالای قیمت", fmt_price(nearest_ask["price"]), delta=f"{format_wall_distance(ask_distance)} بالاتر")
    bid_value = number(nearest_bid.get("value"))
    ask_value = number(nearest_ask.get("value"))
    if ask_distance <= bid_distance * 0.85 and ask_value >= bid_value:
        st.success(f"**اولویت بررسی: سطح بالایی {fmt_price(nearest_ask['price'])}.** هم نزدیک‌تر است و هم دیوار فروش آن دست‌کم به‌اندازه دیوار خرید زیر قیمت است. این فقط سطحی است که باید زیر نظر بگیریم، نه پیش‌بینی قطعی رشد.")
    elif bid_distance <= ask_distance * 0.85 and bid_value >= ask_value:
        st.warning(f"**اولویت بررسی: سطح پایینی {fmt_price(nearest_bid['price'])}.** هم نزدیک‌تر است و هم دیوار خرید آن دست‌کم به‌اندازه دیوار فروش بالای قیمت است. این به معنی قطعی بودن افت قیمت نیست.")
    else:
        st.info("**نتیجه: هنوز برنده مشخصی نداریم.** دو سطح از نظر فاصله و اندازه اختلاف واضحی ندارند؛ فعلاً فقط صبر کن و دنبال تأیید کندل بمان.")
    st.markdown("**وقتی قیمت به سطح رسید:**")
    st.markdown(
        f"- **اگر به سطح پایین {fmt_price(nearest_bid['price'])} رسید:** فقط وقتی خرید را بررسی کن که قیمت دوباره بالای سطح برگردد و کندل بسته‌شده آن را تأیید کند؛ اگر زیر سطح ماند، خرید نکن.\n"
        f"- **اگر به سطح بالا {fmt_price(nearest_ask['price'])} رسید:** اگر قیمت پس زده شد، خرید تازه نکن؛ اگر شکست و بالای آن تثبیت شد، با تصور مقاومت قطعی نفروش.\n"
        "- **اگر تأیید نگرفتی:** معامله نکن. این راهنما برای بازار اسپات است؛ سیگنال فروش الزاماً به معنی شورت نیست."
    )
    st.caption("این سطح‌ها دیوارهای سفارش‌اند، نه نقاط دقیق لیکوییدیشن پوزیشن‌ها. سفارش‌های بزرگ ممکن است قبل از اجرا لغو شوند.")
else:
    st.info(
        "در دفتر سفارش فعلی، دیوار خرید و فروشِ معنادار طبق آستانه آماری پیدا نشد. "
        "بنابراین سطح‌های نزدیکِ بهترین خرید/فروش را به‌اشتباه به‌عنوان دیوار نقدینگی معرفی نمی‌کنیم."
    )
    st.write("**نتیجه: فعلاً صبر کن.** دفتر سفارش را دوباره بررسی کن و فقط وقتی سطح معنادار و کندل تأییدی وجود دارد، تصمیم بگیر.")

flows = st.columns(4)
flows[0].metric("حجم خرید اخیر", fmt_usd(trade_stats["buy_usd"]))
flows[1].metric("حجم فروش اخیر", fmt_usd(trade_stats["sell_usd"]))
flows[2].metric("جریان خالص", fmt_usd(trade_stats["net_flow"]))
flows[3].metric("تعداد معاملات", trade_stats["total_count"])

with st.expander("جزئیات دفتر سفارش: سطوح خرید و فروش"):
    st.caption(
        "این سطوح از سفارش‌های قابل‌مشاهده استخراج شده‌اند و حمایت/مقاومت قطعی نیستند. "
        "سفارش‌ها می‌توانند تغییر کنند یا پیش از اجرا لغو شوند."
    )
    bid_col, ask_col = st.columns(2)

    with bid_col:
        st.markdown("**سطوح خرید و حمایت احتمالی**")
        bid_df = pd.DataFrame(book_stats["bid_walls"]).rename(columns={
            "price": "قیمت",
            "quantity": "مقدار",
            "value": "ارزش تقریبی USDT",
            "classification": "نوع سطح",
        })
        bid_df["قیمت"] = bid_df["قیمت"].map(fmt_price)
        bid_df["مقدار"] = bid_df["مقدار"].map(lambda value: f"{value:,.6f}")
        bid_df["ارزش تقریبی USDT"] = bid_df["ارزش تقریبی USDT"].map(fmt_usd)
        st.dataframe(bid_df, use_container_width=True, hide_index=True)

    with ask_col:
        st.markdown("**سطوح فروش و مقاومت احتمالی**")
        ask_df = pd.DataFrame(book_stats["ask_walls"]).rename(columns={
            "price": "قیمت",
            "quantity": "مقدار",
            "value": "ارزش تقریبی USDT",
            "classification": "نوع سطح",
        })
        ask_df["قیمت"] = ask_df["قیمت"].map(fmt_price)
        ask_df["مقدار"] = ask_df["مقدار"].map(lambda value: f"{value:,.6f}")
        ask_df["ارزش تقریبی USDT"] = ask_df["ارزش تقریبی USDT"].map(fmt_usd)
        st.dataframe(ask_df, use_container_width=True, hide_index=True)

st.divider()
st.subheader("۷. سناریوی احتمالی ورود و خروج")
st.caption(
    "این سناریو یک محاسبه تقریبی بر پایه دفتر سفارش است، نه سفارش واقعی یا توصیه خریدوفروش."
)

plan = calculate_trade_plan(book_stats, trade_stats)

if plan["status"] == "ENTRY WATCH":
    st.success("وضعیت: فرصت برای بررسی بیشتر")
else:
    st.warning("وضعیت: انتظار برای تأیید")

st.write(plan["reason"])

if "entry" in plan:
    levels = st.columns(4)
    levels[0].metric("ورود احتمالی", fmt_price(plan["entry"]))
    levels[1].metric("هدف خروج", fmt_price(plan["target"]))
    levels[2].metric("حد ابطال سناریو", fmt_price(plan["stop"]))
    levels[3].metric("نسبت پاداش به ریسک", f"{plan['rr']:.2f}")

    st.caption(
        f"سطح خرید مبنا: {fmt_price(plan['support']['price'])} "
        f"({fmt_usd(plan['support']['value'])}) | "
        f"سطح فروش مبنا: {fmt_price(plan['resistance']['price'])} "
        f"({fmt_usd(plan['resistance']['value'])})"
    )
else:
    st.info(
        "سطوح قابل استفاده برای سناریوی ورود و خروج از داده فعلی به دست نیامدند."
    )

st.divider()
st.subheader("۸. جمع‌بندی تصمیم: طلا، رمزارز یا ترکیب؟")
st.caption("قاعده‌ای شفاف بر پایه نسبت BTC/XAUT و روند واقعی ۴ساعته/روزانه BTC و XAUT؛ این پیشنهاد خودکار است، شخصی‌سازی مالی نیست و هیچ سفارشی ثبت نمی‌کند.")
try:
    allocation_ratio = get_btc_xau_metrics(lbank_pairs)
    btc_trend = get_asset_trend_score("BTC", lbank_pairs, int(atr_period), float(ut_multiplier))
    gold_trend = get_asset_trend_score("XAUT", lbank_pairs, int(atr_period), float(ut_multiplier))
    ratio_week = allocation_ratio.get("change_7d")
    ratio_day = allocation_ratio.get("change_1d")
    btc_score = btc_trend.get("score")
    gold_score = gold_trend.get("score")

    if ratio_week is None or ratio_day is None or btc_score is None or gold_score is None:
        allocation = "ترکیب ۵۰٪ طلا + ۵۰٪ رمزارز"
        allocation_reason = "داده کافی برای برتری معتبر یک طرف نداریم؛ پیش‌فرض محافظه‌کارانه‌ترِ این مدل ترکیب است، نه ۱۰۰٪ یک دارایی."
        gold_pct, crypto_pct = 50, 50
    elif ratio_week >= 2 and ratio_day > 0 and btc_score >= 1 and gold_score <= 0:
        allocation = "۱۰۰٪ رمزارز"
        allocation_reason = "BTC/XAUT در هفته تقویت شده، تغییر روزانه هم مثبت است و روند BTC از XAUT قوی‌تر است. در سبد رمزارز، BTC گزینه اصلی این سیگنال است؛ آلت‌کوین‌ها فقط با قدرت نسبی مثبت نسبت به BTC."
        gold_pct, crypto_pct = 0, 100
    elif ratio_week <= -2 and ratio_day < 0 and gold_score >= 1 and btc_score <= 0:
        allocation = "۱۰۰٪ طلا (XAUT)"
        allocation_reason = "BTC/XAUT در هفته افت کرده، تغییر روزانه هم منفی است و روند XAUT از BTC قوی‌تر است؛ این یعنی طلا در بازه بررسی‌شده قدرت نسبی بیشتری داشته است."
        gold_pct, crypto_pct = 100, 0
    else:
        allocation = "ترکیب ۵۰٪ طلا + ۵۰٪ رمزارز"
        allocation_reason = "نسبت BTC/XAUT و روندهای تکنیکال تأیید هم‌زمان و قوی نمی‌دهند؛ بنابراین این مدل از شرط‌بندی ۱۰۰٪ روی یک سمت اجتناب می‌کند."
        gold_pct, crypto_pct = 50, 50

    st.subheader(f"پیشنهاد فعلی مدل: {allocation}")
    ac1, ac2, ac3 = st.columns(3)
    ac1.metric("سهم طلا (XAUT)", f"{gold_pct}%")
    ac2.metric("سهم رمزارز", f"{crypto_pct}%")
    ac3.metric("تغییر هفت‌روزه BTC/XAUT", f"{ratio_week:+.2f}%" if ratio_week is not None else "داده ناکافی")
    st.write(allocation_reason)
    st.caption("کنترل‌های تأیید: " + "؛ ".join((btc_trend.get("notes") or []) + (gold_trend.get("notes") or []) + [allocation_ratio.get("method", "")]))
    st.markdown(
        "**چطور اجرا را مدیریت کنی؟** ورود یک‌باره نکن؛ در صورت تصمیم به اجرا، پله‌ای وارد شو. اگر قیمت به سطح نقدینگی رسید، تا واکنش و بسته‌شدن کندل صبر کن. در سناریوی خرید، حد ابطال باید پیش از ورود مشخص باشد. XAUT توکن طلای دیجیتال است، نه طلای فیزیکی، و ریسک ناشر/امانت‌داری و صرافی دارد."
    )
except Exception as exc:
    st.warning(f"جمع‌بندی تخصیص به‌دلیل نبود داده واقعی کامل نشد: {type(exc).__name__}: {str(exc)[:180]}")
    st.info("تا زمان دسترسی به داده، مدل درصد ۱۰۰٪ طلا یا ۱۰۰٪ رمزارز را توصیه نمی‌کند؛ نسبت BTC/XAUT و روندهای BTC و XAUT را دوباره بررسی کن.")

st.warning(
    "این برنامه از داده‌های عمومی بازار استفاده می‌کند و معامله‌ای ثبت نمی‌کند. "
    "سیگنال‌ها، سطوح ATR و سطوح دفتر سفارش تخمینی هستند و سود را تضمین نمی‌کنند. "
    "BOS/CHoCH در این نسخه تقریبی است؛ حجم دفتر سفارش، معاملات اخیر و "
    "حجم کندل‌ها شاخص‌های متفاوتی هستند. کارمزد، لغزش قیمت، نقدشوندگی و "
    "احتمال لغو سفارش‌های بزرگ را پیش از هر تصمیم در نظر بگیر."
)

# -------------------- Download complete PDF report --------------------
try:
    pdf_report_data = {
        "selected_code": selected_code,
        "selected_pair": selected_lbank_pair,
        "btc_usd": fmt_usd(btc_usd),
        "fetched_count": fetched_count,
        "coin_count": len(coins),
        "lbank_status": lbank_status,
        "coins_df": display_df,
        "comparison_df": comp_df,
        "compare_errors": compare_errors,
        "timeframe_results": locals().get("timeframe_results", {}),
        "best_bid": fmt_price(book_stats.get("best_bid")),
        "best_ask": fmt_price(book_stats.get("best_ask")),
        "spread_pct": f"{book_stats.get('spread_pct', 0):.4f}%",
        "imbalance": f"{book_stats.get('imbalance', 0) * 100:+.2f}%",
        "buy_usd": fmt_usd(trade_stats.get("buy_usd", 0)),
        "sell_usd": fmt_usd(trade_stats.get("sell_usd", 0)),
        "net_flow": fmt_usd(trade_stats.get("net_flow", 0)),
        "trade_count": trade_stats.get("total_count", 0),
        "bid_df": bid_df,
        "ask_df": ask_df,
        "order_plan": plan,
    }
    pdf_bytes = build_pdf_report(pdf_report_data)
    safe_code = "".join(ch for ch in str(selected_code) if ch.isalnum() or ch in "-_") or "market"
    pdf_download_placeholder.download_button(
        label="📄 دانلود گزارش کامل PDF",
        data=pdf_bytes,
        file_name=f"SpotPilot_AI_{safe_code}_report.pdf",
        mime="application/pdf",
        use_container_width=True,
        help="گزارش چندصفحه‌ای شامل جدول‌ها، تحلیل تایم‌فریم‌ها، نمودارها و داده‌های قابل مشاهده را دانلود می‌کند.",
    )
except Exception as pdf_exc:
    pdf_download_placeholder.error(
        f"ساخت PDF ناموفق بود: {type(pdf_exc).__name__}: {str(pdf_exc)[:250]}"
    )

