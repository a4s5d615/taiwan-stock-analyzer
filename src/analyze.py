"""
使用 Claude API 將漲停股分類並生成產業說明。
"""
import json
import logging
import re
from typing import Optional

import anthropic

logger = logging.getLogger(__name__)

# 單次請求最多送給 Claude 的股票數量（避免超過 token 限制）
MAX_STOCKS_PER_REQUEST = 80


def analyze_limit_up_stocks(
    stocks: list[dict],
    trade_date: str,
    model: str = "claude-opus-4-6",
) -> dict:
    """
    讓 Claude 將漲停股依產業主題分類，並為每個類別生成說明。

    Parameters
    ----------
    stocks : list[dict]
        來自 fetch_stocks.py 的漲停股清單
    trade_date : str
        格式 'YYYYMMDD'
    model : str
        使用的 Claude 模型

    Returns
    -------
    dict  結構如下：
    {
        "summary": "整體市場簡評...",
        "sectors": [
            {
                "name": "AI 伺服器",
                "theme": "主要題材關鍵字",
                "description": "說明文字（50-100字）",
                "codes": ["2330", "2454", ...]
            }
        ],
        "uncategorized": ["1234", ...]  // 未被分入任何類別的股票代號
    }
    """
    if not stocks:
        return {"summary": "今日無漲停股", "sectors": [], "uncategorized": []}

    # 截斷過多股票
    target_stocks = stocks[:MAX_STOCKS_PER_REQUEST]
    stock_lines = "\n".join(
        f"{s['code']} {s['name']} ({s['change_pct']})" for s in target_stocks
    )

    formatted_date = f"{trade_date[:4]}/{trade_date[4:6]}/{trade_date[6:]}"

    prompt = f"""你是一位專業的台股分析師。以下是 {formatted_date} 台股收盤後的漲停股清單：

{stock_lines}

請完成以下任務：
1. 依照「產業主題 / 概念股題材」將這些股票分組（例如：AI 伺服器、車用電子、低軌衛星、航運等）
2. 為每個分組寫一段 50-100 字的說明，解釋這批股票今日漲停的主要原因或市場背景
3. 寫一段 80 字以內的整體市場總結

**請以 JSON 格式回傳，結構如下（只回傳 JSON，不要有其他文字）：**
{{
  "summary": "整體市場總結文字",
  "sectors": [
    {{
      "name": "類別名稱（簡短，4-8字）",
      "theme": "核心題材關鍵字",
      "description": "說明文字",
      "codes": ["股票代號1", "股票代號2"]
    }}
  ],
  "uncategorized": ["無法分類的股票代號"]
}}

注意：
- 每檔股票只能出現在一個分組中
- 如果某檔股票你不確定其產業，放入 uncategorized
- codes 只放股票代號（4-5位數字），不含名稱
"""

    client = anthropic.Anthropic()

    logger.info("呼叫 Claude API 分析 %d 檔漲停股...", len(target_stocks))

    message = client.messages.create(
        model=model,
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )

    raw_text = message.content[0].text.strip()
    logger.debug("Claude 原始回應：%s", raw_text[:200])

    # 提取 JSON（有時 Claude 會在前後加說明文字）
    result = _extract_json(raw_text)

    # 補上未出現在分析結果的股票
    categorized_codes = set()
    for sector in result.get("sectors", []):
        categorized_codes.update(sector.get("codes", []))

    all_codes = {s["code"] for s in target_stocks}
    truly_uncategorized = list(all_codes - categorized_codes)
    result.setdefault("uncategorized", [])
    result["uncategorized"] = list(set(result["uncategorized"]) | set(truly_uncategorized))

    logger.info(
        "分析完成：%d 個類別，%d 檔未分類",
        len(result.get("sectors", [])),
        len(result.get("uncategorized", [])),
    )
    return result


def _extract_json(text: str) -> dict:
    """從文字中萃取 JSON，容錯處理 Claude 可能包裹的 markdown code block。"""
    # 去掉 ```json ... ``` 包裹
    cleaned = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
    cleaned = re.sub(r"\s*```\s*$", "", cleaned, flags=re.MULTILINE)
    cleaned = cleaned.strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # 嘗試找到第一個 { ... } 區塊
        match = re.search(r"\{[\s\S]+\}", cleaned)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass

    logger.error("無法解析 Claude 回傳的 JSON，回傳空結果")
    return {"summary": "分析失敗，請稍後重試", "sectors": [], "uncategorized": []}
