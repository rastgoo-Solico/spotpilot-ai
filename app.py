import statistics
import time
import io
import os
import html
import requests
import pandas as pd
import streamlit as st
import ccxt

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
OTHER_EXCHANGES = ["binance", "bybit", "okx", "kucoin", "kraken"]
QUOTE = "USDT"

TIMEFRAMES = {
    "15 دقیقه": {"type": "minute15", "seconds": 15 * 60},
    "4 ساعت": {"type": "hour4", "seconds": 4 * 60 * 60},
    "روزانه": {"type": "day1", "seconds": 24 * 60 * 60},
}

st.set_page_config(page_title="SpotPilot AI", page_icon="📊", layout="wide")


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
        canvas.drawString(
            14 * mm, 8 * mm,
            "SpotPilot AI | Public market data | No auto-trading"
        )
        canvas.drawRightString(page_size[0] - 14 * mm, 8 * mm, f"{doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=page_size,
        rightMargin=13 * mm,
        leftMargin=13 * mm,
        topMargin=13 * mm,
        bottomMargin=15 * mm,
        title="SpotPilot AI - Market Analysis Report",
        author="SpotPilot AI",
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "SpotPilotTitle",
        parent=styles["Title"],
        fontName=font_name,
        fontSize=18,
        leading=23,
        alignment=TA_RIGHT,
        spaceAfter=5 * mm,
    )
    h_style = ParagraphStyle(
        "SpotPilotHeading",
        parent=styles["Heading2"],
        fontName=font_name,
        fontSize=12,
        leading=16,
        alignment=TA_RIGHT,
        spaceBefore=4 * mm,
        spaceAfter=2 * mm,
    )
    body_style = ParagraphStyle(
        "SpotPilotBody",
        parent=styles["BodyText"],
        fontName=font_name,
        fontSize=8.5,
        leading=12,
        alignment=TA_RIGHT,
        wordWrap="CJK",
    )
    cell_style = ParagraphStyle(
        "SpotPilotCell",
        parent=body_style,
        fontSize=7,
        leading=9,
        spaceAfter=0,
        spaceBefore=0,
    )
    header_style = ParagraphStyle(
        "SpotPilotHeader",
        parent=cell_style,
        textColor=colors.white,
        fontName=font_name,
        alignment=TA_CENTER,
    )

    story = []
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    story.append(Paragraph(_pdf_rtl("گزارش کامل SpotPilot AI"), title_style))
    story.append(Paragraph(
        _pdf_rtl(f"زمان تولید گزارش: {generated_at}"), body_style
    ))
    story.append(Paragraph(
        _pdf_rtl(
            f"ارز انتخاب‌شده: {report_data.get('selected_code', '—')} | "
            f"جفت‌ارز LBank: {report_data.get('selected_pair') or 'در دسترس نیست'}"
        ),
        body_style,
    ))
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

            headers = [
                Paragraph(_pdf_rtl(html.escape(str(c))), header_style)
                for c in frame.columns
            ]
            rows = [headers]

            for row in frame.itertuples(index=False, name=None):
                cells = []
                for value in row:
                    try:
                        if pd.isna(value):
                            value = "—"
                    except (TypeError, ValueError):
                        pass
                    lines = str(value).splitlines() or [str(value)]
                    safe = "<br/>".join(
                        _pdf_rtl(html.escape(line)) for line in lines
                    )
                    cells.append(Paragraph(safe, cell_style))
                rows.append(cells)

            available_width = (
                page_size[0] - doc.leftMargin - doc.rightMargin
            )

            if col_widths is None:
                ncols = max(1, len(frame.columns))
                col_widths = [available_width / ncols] * ncols
            else:
                total = sum(col_widths)
                col_widths = [
                    available_width * width / total
                    for width in col_widths
                ]

            table_cls = LongTable if len(rows) > 35 else Table
            table = table_cls(
                rows,
                colWidths=col_widths,
                repeatRows=1,
                hAlign="LEFT",
            )
            table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#263746")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#c7ced4")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [
                    colors.white,
                    colors.HexColor("#f3f6f8"),
                ]),
            ]))
            story.append(table)
            story.append(Spacer(1, 3 * mm))
        except Exception as exc:
            add_paragraph(
                f"ساخت جدول {type(exc).__name__}: {exc}"
            )

    add_heading("خلاصه بازار")
    add_paragraph(
        f"قیمت BTC/USD: {report_data.get('btc_usd', '—')} | "
        f"تعداد ارزهای دریافت‌شده: {report_data.get('fetched_count', '—')} | "
        f"تعداد ارزهای پس از فیلتر: {report_data.get('coin_count', '—')}"
    )
    add_paragraph(f"وضعیت LBank: {report_data.get('lbank_status', '—')}")

    add_heading("۱. فهرست ارزهای LiveCoinWatch")
    add_paragraph(
        "فهرست زیر شامل همه ردیف‌های باقی‌مانده پس از اعمال فیلتر حجم فعلی است."
    )
    add_table(
        report_data.get("coins_df"),
        col_widths=[1.0, 1.1, 1.25, 1.25, 1.15, 1.1, 0.9],
    )

    add_heading("۲. مقایسه قیمت صرافی‌ها")
    add_table(
        report_data.get("comparison_df"),
        col_widths=[1.2, 1.3, 1.1, 1.3],
    )
    errors = report_data.get("compare_errors") or []
    if errors:
        add_paragraph(
            "خطاهای دریافت مقایسه: " + " | ".join(str(e) for e in errors)
        )

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
            f"قیمت بسته‌شده: {summary.get('close', '—')} | "
            f"ATR: {summary.get('atr', '—')} | "
            f"حد متحرک UT: {summary.get('ut_stop', '—')}"
        )
        add_paragraph(
            f"سیگنال: {'خرید' if summary.get('buy_signal') else 'فروش' if summary.get('sell_signal') else 'بدون سیگنال تازه'} | "
            f"ساختار بازار: {summary.get('structure', '—')} | "
            f"سناریو: {plan.get('status', '—')}"
        )

        if "stop" in plan:
            add_paragraph(
                f"قیمت مبنا: {plan.get('close', '—')} | "
                f"حد ضرر: {plan.get('stop', '—')} | "
                f"هدف ۱: {plan.get('target1', '—')} | "
                f"هدف ۲: {plan.get('target2', '—')} | "
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
                for column in [
                    "Close", "UT Stop", "SMA 14", "SMA 50", "SMA 200"
                ]:
                    if column in chart.columns:
                        ax.plot(
                            chart["Time"],
                            chart[column],
                            label=column,
                            linewidth=1.0,
                        )
                ax.set_title(
                    f"{report_data.get('selected_code', '')} / {tf_name}"
                )
                ax.grid(True, alpha=0.25)
                ax.legend(loc="best", ncol=5, fontsize=7)
                fig.autofmt_xdate()
                fig.tight_layout()

                image_buffer = io.BytesIO()
                fig.savefig(image_buffer, format="png", dpi=120)
                plt.close(fig)
                image_buffer.seek(0)
                story.append(ReportLabImage(
                    image_buffer,
                    width=245 * mm,
                    height=76 * mm,
                ))
                story.append(Spacer(1, 2 * mm))
        except Exception as exc:
            add_paragraph(
                f"نمودار این تایم‌فریم در PDF ساخته نشد: {type(exc).__name__}"
            )

        indicators = result.get("indicators")
        if indicators is not None and not indicators.empty:
            export_cols = [
                c for c in [
                    "Time", "Open", "High", "Low", "Close", "Volume",
                    "ATR", "UT Stop", "UT Buy", "UT Sell", "Structure",
                ]
                if c in indicators.columns
            ]
            candles_table = indicators.tail(150)[export_cols].copy()
            if "Time" in candles_table.columns:
                candles_table["Time"] = pd.to_datetime(
                    candles_table["Time"],
                    utc=True,
                    errors="coerce",
                ).dt.strftime("%Y-%m-%d %H:%M UTC")

            add_paragraph(
                "داده‌های کندل و اندیکاتور مربوط به نمودار (حداکثر ۱۵۰ ردیف آخر):"
            )
            add_table(
                candles_table,
                col_widths=[1.5] + [1.0] * (len(export_cols) - 1),
            )

    add_heading("۴. دفتر سفارش و جریان معاملات LBank")
    add_paragraph(
        f"بهترین خرید: {report_data.get('best_bid', '—')} | "
        f"بهترین فروش: {report_data.get('best_ask', '—')} | "
        f"فاصله خرید و فروش: {report_data.get('spread_pct', '—')} | "
        f"عدم‌تعادل دفتر سفارش: {report_data.get('imbalance', '—')}"
    )
    add_paragraph(
        f"حجم خرید اخیر: {report_data.get('buy_usd', '—')} | "
        f"حجم فروش اخیر: {report_data.get('sell_usd', '—')} | "
        f"جریان خالص: {report_data.get('net_flow', '—')} | "
        f"تعداد معاملات: {report_data.get('trade_count', '—')}"
    )

    add_heading("سطوح خرید و حمایت احتمالی")
    add_table(
        report_data.get("bid_df"),
        col_widths=[1.0, 1.0, 1.5, 1.2],
    )
    add_heading("سطوح فروش و مقاومت احتمالی")
    add_table(
        report_data.get("ask_df"),
        col_widths=[1.0, 1.0, 1.5, 1.2],
    )

    add_heading("۵. سناریوی احتمالی ورود و خروج")
    order_plan = report_data.get("order_plan") or {}
    add_paragraph(
        f"وضعیت: {order_plan.get('status', '—')} | "
        f"توضیح: {order_plan.get('reason', '—')}"
    )
    if "entry" in order_plan:
        add_paragraph(
            f"ورود احتمالی: {order_plan.get('entry', '—')} | "
            f"هدف خروج: {order_plan.get('target', '—')} | "
            f"حد ابطال سناریو: {order_plan.get('stop', '—')} | "
            f"نسبت پاداش به ریسک: {order_plan.get('rr', '—')}"
        )
        support = order_plan.get("support") or {}
        resistance = order_plan.get("resistance") or {}
        add_paragraph(
            f"سطح خرید مبنا: {support.get('price', '—')} "
            f"(ارزش {support.get('value', '—')}) | "
            f"سطح فروش مبنا: {resistance.get('price', '—')} "
            f"(ارزش {resistance.get('value', '—')})"
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
