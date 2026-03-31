"""
從台灣證交所 (TWSE) 抓取當日漲停股清單。
"""
import requests
import time
import logging
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# 新版 RWD API（優先嘗試）
TWSE_URL_RWD = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"
# 舊版 API（備援）
TWSE_URL_OLD = "https://www.twse.com.tw/exchangeReport/MI_INDEX"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.twse.com.tw/zh/trading/historical/mi-index.html",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
}

# 漲停判斷門檻（%）— 上市股票法定漲停為 +10%，保守用 9.5 避免浮點數誤差
LIMIT_UP_THRESHOLD = 9.5


def _clean_number(s: str) -> Optional[float]:
    """把 '1,234.56' 這類字串轉成 float，失敗回傳 None。"""
    try:
        return float(s.replace(",", "").strip())
    except (ValueError, AttributeError):
        return None


def fetch_limit_up_stocks(trade_date: str) -> list[dict]:
    """
    抓取指定交易日的漲停股清單。

    Parameters
    ----------
    trade_date : str
        格式 'YYYYMMDD'，例如 '20260330'

    Returns
    -------
    list[dict]
        每筆資料包含：
        - code     : 股票代號
        - name     : 股票名稱
        - price    : 收盤價（字串，保留原始格式）
        - open     : 開盤價
        - high     : 最高價
        - low      : 最低價
        - change   : 漲跌價差（帶正負號字串，例如 '+10.00'）
        - change_pct: 漲跌幅百分比字串，例如 '+10.00%'
        - volume   : 成交股數（字串）
    """
    params = {
        "date": trade_date,
        "type": "ALLBUT0999",
        "response": "json",
    }

    payload = None
    for url in [TWSE_URL_RWD, TWSE_URL_OLD]:
        for attempt in range(3):
            try:
                time.sleep(1)  # 避免被 TWSE 限流
                resp = requests.get(url, params=params, headers=HEADERS, timeout=30)
                resp.raise_for_status()
                data = resp.json()
                if data.get("stat") == "OK":
                    payload = data
                    logger.info("成功從 %s 取得資料", url)
                    break
                else:
                    logger.info("URL %s 回傳：%s", url, data.get("stat"))
            except requests.RequestException as e:
                logger.warning("第 %d 次請求失敗（%s）：%s", attempt + 1, url, e)
                time.sleep(3)
        if payload:
            break

    if not payload:
        logger.info("TWSE 兩個端點皆無資料（可能為非交易日）")
        return []

    # 印出所有回傳的 key，方便除錯
    logger.info("TWSE 回傳的 keys：%s", list(payload.keys()))

    # 找出主要資料表（type=ALLBUT0999 時在 'data9' 或 'data'）
    raw_rows = payload.get("data9") or payload.get("data") or []
    fields: list[str] = payload.get("fields9") or payload.get("fields") or []

    if not raw_rows:
        logger.info("當日無資料，回傳內容：%s", str(payload)[:300])
        return []

    # 動態找欄位索引，相容 TWSE 未來可能的欄位順序調整
    def idx(name: str) -> Optional[int]:
        for i, f in enumerate(fields):
            if name in f:
                return i
        return None

    i_code   = idx("證券代號") or 0
    i_name   = idx("證券名稱") or 1
    i_vol    = idx("成交股數") or 2
    i_open   = idx("開盤價")  or 5
    i_high   = idx("最高價")  or 6
    i_low    = idx("最低價")  or 7
    i_close  = idx("收盤價")  or 8
    i_sign   = idx("漲跌(+/-)")  # '+' / '-' / ' '
    i_diff   = idx("漲跌價差")

    results = []
    for row in raw_rows:
        if len(row) <= max(filter(None, [i_code, i_name, i_close, i_diff])):
            continue

        close = _clean_number(row[i_close])
        diff  = _clean_number(row[i_diff]) if i_diff is not None else None

        if close is None or diff is None or close == 0:
            continue

        # 判斷漲跌方向
        sign = "+"
        if i_sign is not None and str(row[i_sign]).strip() == "-":
            sign = "-"
            diff = -abs(diff)
        else:
            diff = abs(diff)

        prev_close = close - diff
        if prev_close <= 0:
            continue

        change_pct = (diff / prev_close) * 100

        if change_pct < LIMIT_UP_THRESHOLD:
            continue

        results.append({
            "code":       str(row[i_code]).strip(),
            "name":       str(row[i_name]).strip(),
            "price":      row[i_close],
            "open":       row[i_open]  if i_open  < len(row) else "--",
            "high":       row[i_high]  if i_high  < len(row) else "--",
            "low":        row[i_low]   if i_low   < len(row) else "--",
            "change":     f"+{diff:.2f}" if diff >= 0 else f"{diff:.2f}",
            "change_pct": f"+{change_pct:.2f}%" if change_pct >= 0 else f"{change_pct:.2f}%",
            "volume":     row[i_vol]   if i_vol   < len(row) else "--",
        })

    logger.info("找到 %d 檔漲停股（%s）", len(results), trade_date)
    return results


def last_trading_date(offset: int = 0) -> str:
    """
    回傳最近的交易日（跳過週末）。
    offset=-1 表示前一個交易日，以此類推。
    """
    d = datetime.today()
    count = 0
    direction = -1 if offset <= 0 else 1

    # 先移動到今天或前一個工作日
    while d.weekday() >= 5:  # 5=Sat, 6=Sun
        d -= timedelta(days=1)

    steps = abs(offset)
    while count < steps:
        d += timedelta(days=direction)
        while d.weekday() >= 5:
            d += timedelta(days=direction)
        count += 1

    return d.strftime("%Y%m%d")
