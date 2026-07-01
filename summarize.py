"""用 Gemini 把單篇文章摘要成結構化欄位。摘要規則的單一事實來源是 prompts/blog-digest.md。"""
from __future__ import annotations

import json
import time
from pathlib import Path

from google import genai
from google.genai import errors as genai_errors
from pydantic import BaseModel

PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "blog-digest.md"


# 結構化輸出 schema —— Slack 與 Email 各自從這些欄位 render。
class Point(BaseModel):
    point: str
    detail: str  # 重要補充，顯示在該點下一階；沒有就空字串 ""


class BlogSummary(BaseModel):
    title_zh: str
    tldr: str            # 全文摘要（第一段）
    points: list[Point]  # 文章摘要列點
    use_case: str        # 白話的實際應用範例


def _load_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def make_client(api_key: str) -> genai.Client:
    return genai.Client(api_key=api_key)


def summarize(client: genai.Client, model: str, item, article_text: str) -> dict | None:
    """回傳 {title_zh, tldr, points, quotes}；失敗回傳 None。"""
    system = _load_prompt()
    user = (
        f"來源：{item.source}\n"
        f"原標題：{item.title}\n"
        f"網址：{item.url}\n\n"
        f"=== 文章原文 ===\n{article_text}"
    )
    config = {
        "system_instruction": system,
        "response_mime_type": "application/json",
        "response_schema": BlogSummary,
        "temperature": 0.3,
    }

    for attempt in range(4):
        try:
            resp = client.models.generate_content(model=model, contents=user, config=config)
        except genai_errors.APIError as e:
            code = getattr(e, "code", None)
            if code in (429, 503) and attempt < 3:  # 免費 tier 限流 / 暫時過載 → 退避重試
                wait = 8 * (attempt + 1)
                print(f"    [rate] {code}，{wait}s 後重試 …")
                time.sleep(wait)
                continue
            print(f"    [warn] Gemini API error for {item.url}: {e}")
            return None

        parsed = getattr(resp, "parsed", None)
        if isinstance(parsed, BlogSummary):
            return parsed.model_dump()
        try:
            return json.loads(resp.text)
        except (json.JSONDecodeError, TypeError, AttributeError):
            print(f"    [warn] non-JSON summary for {item.url}")
            return None
    return None
