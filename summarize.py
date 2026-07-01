"""用 Claude 把單篇文章摘要成結構化欄位。摘要規則的單一事實來源是 prompts/blog-digest.md。"""
from __future__ import annotations

import json
from pathlib import Path

import anthropic

PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "blog-digest.md"

# 結構化輸出 schema —— Slack 與 Email 各自從這些欄位 render。
SCHEMA = {
    "type": "object",
    "properties": {
        "title_zh": {"type": "string"},
        "tldr": {"type": "string"},
        "points": {"type": "array", "items": {"type": "string"}},
        "quotes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "quote": {"type": "string"},
                    "note": {"type": "string"},
                },
                "required": ["quote", "note"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["title_zh", "tldr", "points", "quotes"],
    "additionalProperties": False,
}


def _load_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def summarize(client: anthropic.Anthropic, model: str, item, article_text: str) -> dict | None:
    """回傳 {title_zh, tldr, points, quotes}；失敗回傳 None。"""
    system = _load_prompt()
    user = (
        f"來源：{item.source}\n"
        f"原標題：{item.title}\n"
        f"網址：{item.url}\n\n"
        f"=== 文章原文 ===\n{article_text}"
    )
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=4000,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
        )
    except anthropic.APIError as e:
        print(f"    [warn] Claude API error for {item.url}: {e}")
        return None
    if resp.stop_reason == "refusal":
        print(f"    [warn] Claude refused to summarize {item.url}")
        return None
    text = next((b.text for b in resp.content if b.type == "text"), "")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        print(f"    [warn] non-JSON summary for {item.url}")
        return None
