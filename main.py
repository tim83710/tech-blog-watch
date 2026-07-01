"""tech-blog-watch 主流程：抓新文章 → 繁中摘要 → 發 Slack + Email → 更新 state.json。

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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只印不發、不寫 state")
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
        mark_seen(state, candidates)
        save_state(state)
        print(f"完成 seed：{len(candidates)} 篇標記為已看過。")
        return 0

    new_items = pick_new(candidates, state, settings)
    print(f"== 新文章：{len(new_items)} 篇 ==")
    if not new_items:
        print("沒有新文章，結束。")
        return 0

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("[error] 缺 GEMINI_API_KEY（本機放 .env、雲端放 GitHub secret）")
        return 1
    client = summarize.make_client(api_key)
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

    date_str = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")

    if not posts:
        print("沒有成功摘要的文章，結束。")
        mark_seen(state, new_items)
        if not args.dry_run:
            save_state(state)
        return 0

    if args.dry_run:
        print("\n===== DRY RUN：以下為 Slack 內容 =====")
        print(notify.render_slack_post.__doc__ or "")
        for p in posts:
            print("\n" + notify.render_slack_post(p))
        print(f"\n(共 {len(posts)} 篇；dry-run 不發送、不寫 state)")
        return 0

    print("== 發送 ==")
    try:
        notify.send_slack(posts, date_str)
    except Exception as e:
        print(f"  [warn] Slack 發送失敗: {e}")
    try:
        notify.send_email(posts, date_str)
    except Exception as e:
        print(f"  [warn] Email 發送失敗: {e}")

    # 只要成功摘要就標記已看過（含發送）；發送失敗仍標記，避免下次重複灌爆
    mark_seen(state, new_items)
    save_state(state)
    print(f"完成：{len(posts)} 篇已發送、{len(new_items)} 篇標記已看過。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
