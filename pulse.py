"""每日「AI 產業脈動」：用 Gemini Grounding with Google Search 產生一段繁中產業快訊。

脈動規則的單一事實來源是 prompts/industry-pulse.md。
輸出形狀：{"text": str, "sources": [{"title", "uri"}], "grounded": bool}；失敗回傳 None。
"""
from __future__ import annotations

from pathlib import Path

from google import genai
from google.genai import types

import summarize

PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "industry-pulse.md"

MAX_SOURCES = 4  # digest 最多附幾個來源連結


def _load_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def _extract_sources(resp) -> list[dict]:
    """從 grounding_metadata 取出去重後的 {title, uri}，最多 MAX_SOURCES 個。

    grounding 欄位鏈（candidates / grounding_metadata / grounding_chunks / chunk.web）
    任一層都可能缺，全部防禦式讀取。
    """
    candidates = getattr(resp, "candidates", None) or []
    if not candidates:
        return []
    meta = getattr(candidates[0], "grounding_metadata", None)
    chunks = getattr(meta, "grounding_chunks", None) or []
    sources: list[dict] = []
    seen: set[str] = set()
    for chunk in chunks:
        web = getattr(chunk, "web", None)
        uri = getattr(web, "uri", None)
        if not uri or uri in seen:
            continue
        seen.add(uri)
        sources.append({"title": getattr(web, "title", None) or uri, "uri": uri})
        if len(sources) >= MAX_SOURCES:
            break
    return sources


def _text_and_sources(resp) -> tuple[str, list[dict]]:
    if resp is None:
        return "", []
    text = (getattr(resp, "text", None) or "").strip()
    return text, _extract_sources(resp)


def generate_pulse(client: genai.Client, model: str, date_str: str) -> dict | None:
    """回傳 {"text", "sources", "grounded"}；失敗回傳 None。"""
    config = {
        "system_instruction": _load_prompt(),
        # grounding 不可與 response_schema 併用（已知 bug：citations 會空）→ 純文字輸出
        "tools": [types.Tool(google_search=types.GoogleSearch())],
        "temperature": 0.4,
    }
    base = f"今天日期：{date_str}。請搜尋並總結過去 24 小時 AI 產業動態。"

    resp = summarize.generate_with_retry(client, model, base, config, label="industry-pulse")
    if resp is None:  # API 整包失敗（quota 耗盡、壞模型…）→ 直接放棄，別誤判成「沒搜尋」再燒一輪
        return None
    text, sources = _text_and_sources(resp)
    if text and sources:
        return {"text": text, "sources": sources, "grounded": True}

    # 模型偶爾不搜尋就作答（grounding_metadata 為空）→ 加強提示再試一次
    print("    [note] 脈動無搜尋佐證，加強提示重試一次 …")
    resp2 = summarize.generate_with_retry(
        client, model, base + "你必須先使用 Google Search 工具查證，再作答。",
        config, label="industry-pulse",
    )
    text2, sources2 = _text_and_sources(resp2)
    if text2 and sources2:
        return {"text": text2, "sources": sources2, "grounded": True}

    final = text2 or text
    if final:  # 仍無佐證 → 保留文字但標記，render 端會註明
        return {"text": final, "sources": [], "grounded": False}
    return None


if __name__ == "__main__":  # 單獨手測：.venv/bin/python pulse.py
    import json
    import os
    from datetime import datetime, timezone

    import yaml

    root = Path(__file__).resolve().parent
    env = root / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise SystemExit("缺 GEMINI_API_KEY（本機放 .env、雲端放 GitHub secret）")
    cfg = yaml.safe_load((root / "sources.yaml").read_text(encoding="utf-8"))
    settings = cfg.get("settings", {})
    # 與 main.py 相同的優先序：pulse_model > GEMINI_MODEL 環境變數 > model
    model = (settings.get("pulse_model") or os.environ.get("GEMINI_MODEL")
             or settings.get("model", "gemini-2.5-flash"))
    date_str = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
    result = generate_pulse(summarize.make_client(api_key), model, date_str)
    print(json.dumps(result, ensure_ascii=False, indent=2))
