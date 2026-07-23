"""tech-blog-watch 主流程：抓新文章 → 脈動段（AI 產業、金融×AI，可選）→ 繁中摘要 → 發 Slack + Email → 更新 state.json。

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
from zoneinfo import ZoneInfo

import yaml

import fetch
import github_watch
import notify
import pulse
import summarize

TAIPEI = ZoneInfo("Asia/Taipei")

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


def _generate_pulse_safe(client, model: str, date_str: str, section: dict,
                         avoid: list[str]) -> dict | None:
    """脈動段失敗絕不影響文章 digest：任何例外都吞掉、回 None。"""
    try:
        return pulse.generate_pulse(client, model, date_str, section=section, avoid=avoid)
    except Exception as e:
        print(f"  [warn] {section['title']} 生成失敗（不影響文章 digest）: {e}")
        return None


def _generate_pulses(client, model: str, date_str: str, state: dict, settings: dict) -> list[dict]:
    """依 settings 開關逐段生成；回傳的每段附上 title/emoji 供渲染。"""
    last = state.get("last_pulse") or {}
    avoid = [p for p in (last.get("points") or []) if "無重大新動態" not in p]
    pulses: list[dict] = []
    for sec in pulse.enabled_sections(settings):
        print(f"  {sec['title']} …")
        data = _generate_pulse_safe(client, model, date_str, sec, avoid)
        if data:
            pulses.append({"title": sec["title"], "emoji": sec["emoji"], **data})
    return pulses


def _remember_pulses(state: dict, pulses: list[dict], date_str: str) -> None:
    """把本次脈動列點記進 state，明天的 48 小時窗口靠它去重；本次沒產出就保留昨日的。

    GitHub 週段（kind == "github"）不進 last_pulse（跟 grounded 脈動的去重無關），
    改把 repo_updates 合併進 state["github_repos"]，供下次「介紹過就不重覆」判斷。
    """
    grounded = [pu for pu in pulses if pu.get("kind") != "github"]
    if grounded:
        points = [p for pu in grounded for p in pu.get("points", []) if "無重大新動態" not in p]
        state["last_pulse"] = {"date": date_str, "points": points}
    for pu in pulses:
        if pu.get("kind") == "github" and pu.get("repo_updates"):
            state.setdefault("github_repos", {}).update(pu["repo_updates"])


def _maybe_github_weekly(client, model: str, settings: dict, state: dict,
                         date_str: str, force: bool = False) -> dict | None:
    """每週固定幾天（台北時間，預設週二、週五）產生 GitHub 專案段；失敗絕不影響 digest。"""
    if not settings.get("github_weekly_enabled"):
        return None
    # 允許多天：github_weekly_weekdays 為 list（0=週一）；沿用舊的單數 github_weekly_weekday 也可
    weekdays = settings.get("github_weekly_weekdays")
    if weekdays is None:
        weekdays = [settings.get("github_weekly_weekday", 2)]
    if datetime.now(TAIPEI).weekday() not in weekdays and not force:
        return None
    print("  本週 GitHub 專案 …")
    try:
        return github_watch.generate_weekly(client, model, settings, state, date_str)
    except Exception as e:
        print(f"  [warn] GitHub 週段生成失敗（不影響 digest）: {e}")
        return None


def _print_pulses(pulses: list[dict]) -> None:
    for pu in pulses:
        print(f"\n===== DRY RUN：{pu['title']} =====")
        for p in pu.get("points") or [pu["text"]]:
            print(f"• {p}")
        for s in pu.get("sources", []):
            print(f"  來源: {s['title']} — {s['uri']}")
        if not pu.get("grounded", True):
            print("  [note] 本段無搜尋佐證（grounding_metadata 為空）")


def _channel_enabled(name: str) -> bool:
    """平台開關：SEND_SLACK / SEND_EMAIL（GitHub repo Variables 或本機 .env）。沒設或空值＝開。"""
    return os.environ.get(name, "").strip().lower() not in ("false", "0", "no", "off")


def _send_all(posts: list[dict], date_str: str, pulses: list[dict]) -> None:
    if _channel_enabled("SEND_SLACK"):
        try:
            notify.send_slack(posts, date_str, pulses=pulses)
        except Exception as e:
            print(f"  [warn] Slack 發送失敗: {e}")
    else:
        print("  [skip] SEND_SLACK=false，略過 Slack")
    if _channel_enabled("SEND_EMAIL"):
        try:
            notify.send_email(posts, date_str, pulses=pulses)
        except Exception as e:
            print(f"  [warn] Email 發送失敗: {e}")
    else:
        print("  [skip] SEND_EMAIL=false，略過 Email")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="只印不發、不寫 state（每個啟用的脈動段仍會實際打 1 次 grounded 查詢）")
    ap.add_argument("--seed", action="store_true", help="只標記已看過、不摘要不發送")
    ap.add_argument("--force-github", action="store_true",
                    help="不管今天星期幾都跑 GitHub 週段（測試用）")
    args = ap.parse_args()

    _load_dotenv()
    cfg = yaml.safe_load(SOURCES_PATH.read_text(encoding="utf-8"))
    settings = cfg.get("settings", {})
    sources = cfg["sources"]
    company_map = {s["name"]: s.get("company", s["name"]) for s in sources}
    # 模型優先序：GEMINI_MODEL 環境變數 > sources.yaml 的 model > 預設
    model = os.environ.get("GEMINI_MODEL") or settings.get("model", "gemini-2.5-flash")
    char_limit = settings.get("article_char_limit", 12000)
    # 脈動段開關在 sources.yaml settings（pulse_enabled / finance_pulse_enabled），程式預設全關
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
        if _channel_enabled("SEND_SLACK"):
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
    # 用台北時間標日期（Actions runner 是 UTC，22:30 UTC 跑的時候台北已是隔天早上）
    date_str = datetime.now(TAIPEI).strftime("%Y-%m-%d")

    new_items = pick_new(candidates, state, settings)
    print(f"== 新文章：{len(new_items)} 篇 ==")

    pulses: list[dict] = []
    can_send = args.dry_run or notify.any_channel_configured()
    if pulse.enabled_sections(settings):
        if can_send:
            print("== 脈動段 ==")
            pulses = _generate_pulses(client, pulse_model, date_str, state, settings)
        else:
            print("  [skip] 無任何發送管道設定，略過脈動段（不花 grounded 額度）")
    if can_send:
        gh = _maybe_github_weekly(client, model, settings, state, date_str,
                                  force=args.force_github)
        if gh:
            pulses.append(gh)

    # 沒有新文章：有脈動就單獨發，沒有就跟過去一樣安靜結束
    if not new_items:
        if not pulses:
            print("沒有新文章、無脈動段，結束。")
            return 0
        if args.dry_run:
            _print_pulses(pulses)
            print("\n(沒有新文章；dry-run 不發送、不寫 state)")
            return 0
        print("== 發送（僅脈動段）==")
        _send_all([], date_str, pulses)
        _remember_pulses(state, pulses, date_str)
        save_state(state)
        print("沒有新文章，已單獨發送脈動段。")
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
            _remember_pulses(state, pulses, date_str)
            save_state(state)
        if not pulses:
            return 0
        if args.dry_run:
            _print_pulses(pulses)
            return 0
        print("== 發送（僅脈動段）==")
        _send_all([], date_str, pulses)
        return 0

    if args.dry_run:
        if pulses:
            _print_pulses(pulses)
        print("\n===== DRY RUN：以下為 Slack 內容 =====")
        for p in posts:
            print("\n" + notify.render_slack_post(p))
        print(f"\n(共 {len(posts)} 篇；dry-run 不發送、不寫 state)")
        return 0

    print("== 發送 ==")
    _send_all(posts, date_str, pulses)

    # 只要成功摘要就標記已看過（含發送）；發送失敗仍標記，避免下次重複灌爆
    mark_seen(state, new_items)
    _remember_pulses(state, pulses, date_str)
    save_state(state)
    print(f"完成：{len(posts)} 篇已發送、{len(new_items)} 篇標記已看過。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
