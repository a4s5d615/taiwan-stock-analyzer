"""
台股漲停分析主程式

用法：
  python main.py                     # 抓今日（或最近交易日）資料
  python main.py --date 20260328     # 指定日期
  python main.py --date 20260328 --skip-ai   # 跳過 Claude，用假資料測試 HTML
"""
import argparse
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

# 載入 .env（本機開發用；CI 環境改用 Secrets）
load_dotenv()

from src.fetch_stocks import fetch_limit_up_stocks, last_trading_date
from src.analyze import analyze_limit_up_stocks
from src.generate_html import generate_page, generate_redirect_index

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("main")

# ── 日期導航：維護一份已存在頁面的日期清單 ──────────────────────────
def _list_existing_dates() -> list[str]:
    """掃描 docs/ 目錄，回傳已有頁面的日期清單（排序由舊到新）。"""
    docs = Path("docs")
    dates = []
    for f in docs.glob("index-????????.html"):
        date_str = f.stem.replace("index-", "")
        if date_str.isdigit() and len(date_str) == 8:
            dates.append(date_str)
    return sorted(dates)


def _get_adjacent_dates(trade_date: str) -> tuple[str, str]:
    """回傳 (prev_date, next_date)，若無相鄰頁面則回傳空字串。"""
    dates = _list_existing_dates()
    try:
        idx = dates.index(trade_date)
    except ValueError:
        # 目前日期尚未在清單中（第一次生成），先用暫定值
        dates_with_new = sorted(dates + [trade_date])
        idx = dates_with_new.index(trade_date)
        prev = dates_with_new[idx - 1] if idx > 0 else ""
        next_ = dates_with_new[idx + 1] if idx < len(dates_with_new) - 1 else ""
        return prev, next_

    prev  = dates[idx - 1] if idx > 0 else ""
    next_ = dates[idx + 1] if idx < len(dates) - 1 else ""
    return prev, next_


# ── 假資料（--skip-ai 時使用）──────────────────────────────────────
MOCK_STOCKS = [
    {"code": "2330", "name": "台積電",   "price": "1,050", "open": "1,020", "high": "1,055",
     "low": "1,018", "change": "+95.00",  "change_pct": "+9.95%", "volume": "45,678,000"},
    {"code": "2454", "name": "聯發科",   "price": "1,200", "open": "1,100", "high": "1,205",
     "low": "1,098", "change": "+109.00", "change_pct": "+9.99%", "volume": "12,345,000"},
    {"code": "3034", "name": "聯詠",     "price": "580",   "open": "530",   "high": "582",
     "low": "528",   "change": "+52.00",  "change_pct": "+9.84%", "volume": "8,901,000"},
    {"code": "2382", "name": "廣達",     "price": "310",   "open": "285",   "high": "312",
     "low": "283",   "change": "+28.00",  "change_pct": "+9.93%", "volume": "23,456,000"},
    {"code": "2303", "name": "聯電",     "price": "58.5",  "open": "53.5",  "high": "58.8",
     "low": "53.2",  "change": "+5.30",   "change_pct": "+9.96%", "volume": "156,789,000"},
    {"code": "2337", "name": "旺宏",     "price": "82",    "open": "75",    "high": "82.5",
     "low": "74.8",  "change": "+7.45",   "change_pct": "+9.99%", "volume": "34,567,000"},
    {"code": "5347", "name": "世界先進", "price": "135",   "open": "123",   "high": "136",
     "low": "122",   "change": "+12.27",  "change_pct": "+9.99%", "volume": "18,234,000"},
    {"code": "2367", "name": "燿華",     "price": "45.2",  "open": "41.2",  "high": "45.5",
     "low": "41.0",  "change": "+4.10",   "change_pct": "+9.98%", "volume": "9,876,000"},
]

MOCK_ANALYSIS = {
    "summary": "AI 算力需求持續爆發，台積電領銜 CoWoS 先進封裝概念股全面漲停，半導體供應鏈普遍受惠，市場氣氛樂觀。",
    "sectors": [
        {
            "name": "AI 晶片供應鏈",
            "theme": "CoWoS / HBM",
            "description": "受惠於輝達 GB300 大規模出貨，台積電先進封裝產能滿載，聯發科 AI 晶片出貨量創新高，帶動整體 AI 晶片供應鏈全面上攻。",
            "codes": ["2330", "2454", "3034"],
        },
        {
            "name": "AI 伺服器 ODM",
            "theme": "GB300 / 散熱",
            "description": "廣達、英業達等 ODM 廠受惠輝達新一代伺服器訂單持續增加，下半年出貨能見度明朗，法人大幅調升目標價。",
            "codes": ["2382"],
        },
        {
            "name": "成熟製程半導體",
            "theme": "車用 / IoT 回補",
            "description": "車用與工業 IoT 需求回溫，聯電、旺宏、世界先進等成熟製程廠商庫存去化完畢，客戶回補力道明顯。",
            "codes": ["2303", "2337", "5347"],
        },
    ],
    "uncategorized": ["2367"],
}


# ── 主流程 ────────────────────────────────────────────────────────
def run(trade_date: str, skip_ai: bool = False) -> None:
    logger.info("===== 開始處理 %s =====", trade_date)

    # 1. 抓資料
    if skip_ai:
        logger.info("使用假資料（--skip-ai 模式）")
        stocks   = MOCK_STOCKS
        analysis = MOCK_ANALYSIS
    else:
        stocks = fetch_limit_up_stocks(trade_date)
        if not stocks:
            logger.warning("當日無漲停股資料，跳過頁面生成")
            return

        # 2. Claude 分析
        analysis = analyze_limit_up_stocks(stocks, trade_date)

    # 3. 找相鄰日期
    prev_date, next_date = _get_adjacent_dates(trade_date)

    # 4. 生成 HTML
    out_path = generate_page(trade_date, stocks, analysis, prev_date, next_date)
    logger.info("頁面已生成：%s", out_path)

    # 5. 更新 index.html 導向最新頁面
    all_dates = _list_existing_dates()
    latest = max(all_dates + [trade_date])
    generate_redirect_index(latest)

    logger.info("===== 完成 =====")


def main() -> None:
    parser = argparse.ArgumentParser(description="台股漲停分析頁面產生器")
    parser.add_argument(
        "--date",
        default=None,
        help="交易日期，格式 YYYYMMDD（預設：最近交易日）",
    )
    parser.add_argument(
        "--skip-ai",
        action="store_true",
        help="跳過 Claude API，使用內建假資料（用於測試 HTML 輸出）",
    )
    args = parser.parse_args()

    trade_date = args.date or last_trading_date()
    logger.info("目標日期：%s", trade_date)

    run(trade_date, skip_ai=args.skip_ai)


if __name__ == "__main__":
    main()
