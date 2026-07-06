"""每日脈動段（AI 產業 / 金融×AI）：用 Gemini Grounding with Google Search 產生繁中快訊列點。

各段規則的單一事實來源在 prompts/（見 SECTIONS 的 prompt 欄位）。
輸出形狀：{"text": str, "points": [str], "sources": [{"title", "uri"}], "grounded": bool}；失敗回傳 None。
"""
from __future__ import annotations

from pathlib import Path

from google import genai
from google.genai import types

import summarize

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

MAX_SOURCES = 4  # digest 每段最多附幾個來源連結

# 每個脈動段：setting = sources.yaml settings 的開關名；title/emoji 供 notify 渲染
SECTIONS = [
    {"setting": "pulse_enabled", "title": "AI 產業脈動", "emoji": ":globe_with_meridians:",
     "prompt": "industry-pulse.md", "label": "industry-pulse"},
    {"setting": "finance_pulse_enabled", "title": "金融×AI 脈動", "emoji": ":chart_with_upwards_trend:",
     "prompt": "finance-ai-pulse.md", "label": "finance-ai-pulse"},
]


def enabled_sections(settings: dict) -> list[dict]:
    return [s for s in SECTIONS if settings.get(s["setting"])]


def _load_prompt(section: dict) -> str:
    return (PROMPTS_DIR / section["prompt"]).read_text(encoding="utf-8")


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


def _split_points(text: str) -> list[str]:
    """prompt 要求每行一點（「- 」開頭）；防禦式去掉各種列點符號，空行略過。"""
    points = [line.strip().lstrip("-•*").strip() for line in text.splitlines() if line.strip()]
    return [p for p in points if p]


def generate_pulse(client: genai.Client, model: str, date_str: str,
                   section: dict = SECTIONS[0], avoid: list[str] | None = None) -> dict | None:
    """產一個脈動段。回傳 {"text", "points", "sources", "grounded"}；失敗回傳 None。

    avoid：昨日已報導的列點（48 小時窗口靠這份清單去重）。
    """
    label = section["label"]
    config = {
        "system_instruction": _load_prompt(section),
        # grounding 不可與 response_schema 併用（已知 bug：citations 會空）→ 純文字輸出
        "tools": [types.Tool(google_search=types.GoogleSearch())],
        "temperature": 0.4,
    }
    base = f"今天日期：{date_str}。請依系統指示，用 Google Search 查證並總結最新動態。"
    if avoid:
        base += "\n\n昨日已報導（除非有重大新進展，不要重複）：\n" + "\n".join(f"- {p}" for p in avoid)

    resp = summarize.generate_with_retry(client, model, base, config, label=label)
    if resp is None:  # API 整包失敗（quota 耗盡、壞模型…）→ 直接放棄，別誤判成「沒搜尋」再燒一輪
        return None
    text, sources = _text_and_sources(resp)
    if text and sources:
        return {"text": text, "points": _split_points(text), "sources": sources, "grounded": True}

    # 模型偶爾不搜尋就作答（grounding_metadata 為空）→ 加強提示再試一次
    print(f"    [note] {label} 無搜尋佐證，加強提示重試一次 …")
    resp2 = summarize.generate_with_retry(
        client, model, base + "\n你必須先使用 Google Search 工具查證，再作答。",
        config, label=label,
    )
    text2, sources2 = _text_and_sources(resp2)
    if text2 and sources2:
        return {"text": text2, "points": _split_points(text2), "sources": sources2, "grounded": True}

    final = text2 or text
    if final:  # 仍無佐證 → 保留文字但標記，render 端會註明
        return {"text": final, "points": _split_points(final), "sources": [], "grounded": False}
    return None


if __name__ == "__main__":  # 單獨手測：.venv/bin/python pulse.py（每個啟用的段各花 1 次 grounded query）
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
    client = summarize.make_client(api_key)
    for sec in enabled_sections(settings):
        print(f"== {sec['title']} ==")
        result = generate_pulse(client, model, date_str, section=sec)
        print(json.dumps(result, ensure_ascii=False, indent=2))
