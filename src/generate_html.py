"""
將分析結果渲染成靜態 HTML 檔案，輸出至 docs/ 目錄。
"""
import logging
import os
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

TEMPLATE_PATH = Path(__file__).parent.parent / "templates" / "page.html"
OUTPUT_DIR    = Path(__file__).parent.parent / "docs"


def _format_date_display(date_str: str) -> str:
    """'20260330' → '2026/03/30'"""
    return f"{date_str[:4]}/{date_str[4:6]}/{date_str[6:]}"


def _volume_display(vol_str: str) -> str:
    """把成交股數格式化成 '萬股' 單位。"""
    try:
        v = int(vol_str.replace(",", ""))
        if v >= 100_000_000:
            return f"{v / 100_000_000:.1f} 億股"
        elif v >= 10_000:
            return f"{v / 1_000:.0f} 千股"
        return f"{v:,} 股"
    except (ValueError, AttributeError):
        return vol_str


def _build_stock_card(stock: dict) -> str:
    search_text = f"{stock['code']} {stock['name']}".lower()
    return f"""
        <div class="stock-card" data-search="{search_text}">
          <div class="stock-header">
            <div class="stock-name">{stock['name']}</div>
            <span class="stock-code">{stock['code']}</span>
          </div>
          <div class="stock-price">{stock['price']}</div>
          <div class="stock-change">{stock['change']} ({stock['change_pct']})</div>
          <div class="stock-volume">成交 {_volume_display(stock['volume'])}</div>
        </div>"""


def _build_sectors_html(stocks: list[dict], analysis: dict) -> str:
    stock_map = {s["code"]: s for s in stocks}
    parts = []

    for sector in analysis.get("sectors", []):
        codes = sector.get("codes", [])
        matched = [stock_map[c] for c in codes if c in stock_map]
        if not matched:
            continue

        cards_html = "".join(_build_stock_card(s) for s in matched)
        theme_tag  = (
            f'<span class="sector-theme">{sector["theme"]}</span>'
            if sector.get("theme") else ""
        )

        parts.append(f"""
  <div class="sector">
    <div class="sector-header">
      <div class="sector-title">{sector['name']}</div>
      <span class="sector-count">{len(matched)} 檔</span>
      {theme_tag}
    </div>
    <div class="sector-desc">{sector.get('description', '')}</div>
    <div class="stocks-grid">{cards_html}
    </div>
  </div>""")

    # 未分類股票
    uncategorized_codes = analysis.get("uncategorized", [])
    uncategorized_stocks = [stock_map[c] for c in uncategorized_codes if c in stock_map]

    if uncategorized_stocks:
        chips = "".join(
            f'<span class="uncategorized-chip">{s["code"]} {s["name"]}</span>'
            for s in uncategorized_stocks
        )
        parts.append(f"""
  <div class="sector uncategorized-section">
    <div class="sector-header">
      <div class="sector-title" style="border-color: var(--muted);">其他個股</div>
      <span class="sector-count">{len(uncategorized_stocks)} 檔</span>
    </div>
    <div class="uncategorized-list">{chips}</div>
  </div>""")

    return "\n".join(parts)


def generate_page(
    trade_date: str,
    stocks: list[dict],
    analysis: dict,
    prev_date: str = "",
    next_date: str = "",
) -> Path:
    """
    生成單一日期的分析頁面並存檔。

    Parameters
    ----------
    trade_date : str   格式 'YYYYMMDD'
    stocks     : list  漲停股資料
    analysis   : dict  Claude 分析結果
    prev_date  : str   前一交易日，空字串表示無
    next_date  : str   後一交易日，空字串表示無

    Returns
    -------
    Path  生成的 HTML 檔案路徑
    """
    template = TEMPLATE_PATH.read_text(encoding="utf-8")

    sectors_html = _build_sectors_html(stocks, analysis)
    date_display = _format_date_display(trade_date)
    update_time  = datetime.now().strftime("%H:%M")

    html = (
        template
        .replace("%%DATE_DISPLAY%%",  date_display)
        .replace("%%DATE_RAW%%",      trade_date)
        .replace("%%PREV_DATE%%",     prev_date)
        .replace("%%NEXT_DATE%%",     next_date)
        .replace("%%PREV_DISABLED%%", "" if prev_date else "disabled")
        .replace("%%NEXT_DISABLED%%", "" if next_date else "disabled")
        .replace("%%TOTAL_COUNT%%",   str(len(stocks)))
        .replace("%%SECTOR_COUNT%%",  str(len(analysis.get("sectors", []))))
        .replace("%%UPDATE_TIME%%",   update_time)
        .replace("%%SUMMARY%%",       analysis.get("summary", ""))
        .replace("%%SECTORS_HTML%%",  sectors_html)
    )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / f"index-{trade_date}.html"
    out_path.write_text(html, encoding="utf-8")
    logger.info("已輸出：%s", out_path)
    return out_path


def generate_redirect_index(latest_date: str) -> Path:
    """
    生成 docs/index.html，自動導向最新日期頁面。
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / "index.html"
    out_path.write_text(
        f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
  <meta charset="UTF-8">
  <meta http-equiv="refresh" content="0; url=index-{latest_date}.html">
  <title>台股漲停分析</title>
</head>
<body>
  <p>正在導向最新頁面 (<a href="index-{latest_date}.html">{_format_date_display(latest_date)}</a>)...</p>
</body>
</html>""",
        encoding="utf-8",
    )
    logger.info("已更新首頁導向 → index-%s.html", latest_date)
    return out_path
