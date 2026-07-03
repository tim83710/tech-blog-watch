"""tech-blog-watch 主流程：抓新文章 → 產業脈動（可選）→ 繁中摘要 → 發 Slack + Email → 更新 state.json。

跑法：
    python main.py            # 正常跑（發送 + 更新 state）
    python main.py --dry-run  # 只印出會發什麼，不發送、不寫 state
    python main.py --seed     # 只把目前列表標記為已看過，不摘要不發送（初始化用）
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

import fetch
import notify
import pulse
import summarize

ROOT = Path(__file__).resolve().parent
SOURCES_PATH = ROOT / "sources.yaml"
STATE_PATH = ROOT / "state.json"


def _load_dotenv() -> None:
    """本機測試：若有 .env 就載入（GitHub Actions 走 Secrets、不會有這檔）。"""
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip())


def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {"seen": {}}


def save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def discover_all(sources: list[dict]) -> list[fetch.Item]:
    items: list[fetch.Item] = []
    for src in sources:
        print(f"  抓 {src['name']} ({src['mode']}) …")
        try:
            found = fetch.discover(src)
        except Exception as e:  # 單一來源掛掉不要拖垮全部
            print(f"    [warn] discover 失敗 {src['name']}: {e}")
            found = []
        print(f"    找到 {len(found)} 篇候選")
        items.extend(found)
    return items


def pick_new(items: list[fetch.Item], state: dict, settings: dict) -> list[fetch.Item]:
    seen: dict = state["seen"]
    max_age = timedelta(days=settings.get("max_age_days", 7))
    cutoff = datetime.now(timezone.utc) - max_age
    per_source_cap = settings.get("max_per_source", 6)
    overall_cap = settings.get("max_per_run", 20)

    picked: list[fetch.Item] = []
    per_source_count: dict[str, int] = {}
    for it in items:
        if it.url in seen:
            continue
        if it.published and it.published < cutoff:
            continue  # 有日期且太舊 → 跳過（無日期的照收，靠 state 去重）
        if per_source_count.get(it.source, 0) >= per_source_cap:
            continue
        picked.append(it)
        per_source_count[it.source] = per_source_count.get(it.source, 0) + 1

    if len(picked) > overall_cap:
        print(f"  [note] 新文章 {len(picked)} 篇超過上限 {overall_cap}，本次只處理前 {overall_cap} 篇")
        picked = picked[:overall_cap]
    return picked


def mark_seen(state: dict, items: list[fetch.Item]) -> None:
    now = datetime.now(timezone.utc).isoformat()
    for it in items:
        state["seen"][it.url] = now


def _generate_pulse_safe(client, model: str, date_str: str) -> dict | None:
    """產業脈動失敗絕不影響文章 digest：任何例外都吞掉、回 None。"""
    try:
        return pulse.generate_pulse(client, model, date_str)
    except Exception as e:
        print(f"  [warn] 產業脈動生成失敗（不影響文章 digest）: {e}")
        return None


def _print_pulse(pulse_data: dict) -> None:
    print("\n===== DRY RUN：AI 產業脈動 =====")
    print(pulse_data["text"])
    for s in pulse_data.get("sources", []):
        print(f"  來源: {s['title']} — {s['uri']}")
    if not pulse_data.get("grounded", True):
        print("  [note] 本段無搜尋佐證（grounding_metadata 為空）")


def _send_all(posts: list[dict], date_str: str, pulse_data: dict | None) -> None:
    try:
        notify.send_slack(posts, date_str, pulse=pulse_data)
    except Exception as e:
        print(f"  [warn] Slack 發送失敗: {e}")
    try:
        notify.send_email(posts, date_str, pulse=pulse_data)
    except Exception as e:
        print(f"  [warn] Email 發送失敗: {e}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="只印不發、不寫 state（pulse 啟用時仍會實際打 1 次 grounded 查詢）")
    ap.add_argument("--seed", action="store_true", help="只標記已看過、不摘要不發送")
    args = ap.parse_args()

    _load_dotenv()
    cfg = yaml.safe_load(SOURCES_PATH.read_text(encoding="utf-8"))
    settings = cfg.get("settings", {})
    sources = cfg["sources"]
    company_map = {s["name"]: s.get("company", s["name"]) for s in sources}
    # 模型優先序：GEMINI_MODEL 環境變數 > sources.yaml 的 model > 預設
    model = os.environ.get("GEMINI_MODEL") or settings.get("model", "gemini-2.5-flash")
    char_limit = settings.get("article_char_limit", 12000)
    pulse_enabled = bool(settings.get("pulse_enabled", False))  # 程式預設關，sources.yaml 明文開
    pulse_model = settings.get("pulse_model") or model

    state = load_state()
    first_run = not state["seen"]

    print("== 抓取候選文章 ==")
    candidates = discover_all(sources)

    # 首次執行（state 空）：只做 seed，避免第一次就把整個 backlog 灌成一封巨量 digest
    if first_run and not args.dry_run:
        print("== 首次執行：seed 模式（標記已看過、不摘要）==")
        mark_seen(state, candidates)
        save_state(state)
        notify.send_slack_message(
            f":satellite_antenna: tech-blog-watch 已初始化，開始監看 {len(sources)} 個來源，"
            f"目前列表 {len(candidates)} 篇標記為已看過。之後只推新文章。"
        )
        print(f"完成 seed：{len(candidates)} 篇標記為已看過。")
        return 0

    if args.seed:
        if args.dry_run:
            print(f"(dry-run) seed 會把 {len(candidates)} 篇標記為已看過，不寫 state。")
            return 0
        mark_seen(state, candidates)
        save_state(state)
        print(f"完成 seed：{len(candidates)} 篇標記為已看過。")
        return 0

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("[error] 缺 GEMINI_API_KEY（本機放 .env、雲端放 GitHub secret）")
        return 1
    client = summarize.make_client(api_key)
    date_str = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")

    new_items = pick_new(candidates, state, settings)
    print(f"== 新文章：{len(new_items)} 篇 ==")

    pulse_data = None
    if pulse_enabled:
        if args.dry_run or notify.any_channel_configured():
            print("== 產業脈動 ==")
            pulse_data = _generate_pulse_safe(client, pulse_model, date_str)
        else:
            print("  [skip] 無任何發送管道設定，略過產業脈動（不花 grounded 額度）")

    # 沒有新文章：有脈動就單獨發，沒有就跟過去一樣安靜結束
    if not new_items:
        if not pulse_data:
            print("沒有新文章、無產業脈動，結束。")
            return 0
        if args.dry_run:
            _print_pulse(pulse_data)
            print("\n(沒有新文章；dry-run 不發送、不寫 state)")
            return 0
        print("== 發送（僅產業脈動）==")
        _send_all([], date_str, pulse_data)
        print("沒有新文章，已單獨發送產業脈動。")
        return 0

    posts: list[dict] = []
    for it in new_items:
        print(f"  摘要 {it.source}: {it.title[:60]} …")
        text, extracted_title = fetch.extract_article(it.url, char_limit)
        if not text or len(text) < 200:
            print("    [skip] 內文太短或抓不到，略過（仍標記已看過）")
            continue
        if extracted_title and (not it.title or it.title == it.url):
            it.title = extracted_title
        result = summarize.summarize(client, model, it, text)
        if not result:
            continue
        posts.append({
            "source": it.source,
            "company": company_map.get(it.source, it.source),
            "url": it.url,
            "title": it.title,
            "summary": result,
        })

    # 全部摘要失敗：仍標記已看過；有脈動就單獨發
    if not posts:
        print("沒有成功摘要的文章。")
        mark_seen(state, new_items)
        if not args.dry_run:
            save_state(state)
        if not pulse_data:
            return 0
        if args.dry_run:
            _print_pulse(pulse_data)
            return 0
        print("== 發送（僅產業脈動）==")
        _send_all([], date_str, pulse_data)
        return 0

    if args.dry_run:
        if pulse_data:
            _print_pulse(pulse_data)
        print("\n===== DRY RUN：以下為 Slack 內容 =====")
        for p in posts:
            print("\n" + notify.render_slack_post(p))
        print(f"\n(共 {len(posts)} 篇；dry-run 不發送、不寫 state)")
        return 0

    print("== 發送 ==")
    _send_all(posts, date_str, pulse_data)

    # 只要成功摘要就標記已看過（含發送）；發送失敗仍標記，避免下次重複灌爆
    mark_seen(state, new_items)
    save_state(state)
    print(f"完成：{len(posts)} 篇已發送、{len(new_items)} 篇標記已看過。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
