"""每週「YouTube 精選」段：頻道白名單 RSS 蒐集近一週新片，交給 Gemini 選出值得看的
並寫繁中導讀（metadata-only MVP：只用標題與說明節錄，不看影片本身）。
選題口味的單一事實來源在 prompts/youtube-weekly.md；頻道白名單在 sources.yaml 的 youtube_channels。

抓法：channel_id（UC 開頭）轉成 UULF playlist feed（https://www.youtube.com/feeds/videos.xml?playlist_id=UULF...）
＝該頻道「一般影片」清單，天然排除 Shorts；feed 失敗或為空就退回 channel_id feed。
feed 免 API key、每頻道回最近 15 支；欄位沒有時長，promo 短片靠選題 prompt 從說明厚薄判斷。

去重：介紹過的 video id 記在 state.json 的 youtube_seen（{video_id: {…, featured: 日期}}），
候選階段直接跳過；正式跑時由 main._remember_pulses 合併寫入。

輸出形狀與脈動段相容（notify 直接沿用 pulse 渲染）：
{"kind": "youtube", "title", "emoji", "text", "points", "sources", "grounded": True,
 "video_updates": {...}}   # video_updates 由 main 在正式跑時合併進 state，dry-run 不寫
"""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from pydantic import BaseModel

import fetch
import summarize

PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "youtube-weekly.md"

FEED_URL = "https://www.youtube.com/feeds/videos.xml"
DESC_EXCERPT = 800   # 每支影片說明節錄字元數（訪談類頻道的說明常是完整 show notes）
MAX_CANDIDATES = 40  # 送進 Gemini 的候選總量上限（控 token；conference 頻道批次上傳週的保險絲）

_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/",
}


class VideoPick(BaseModel):
    video_id: str    # 候選清單裡的 video_id，一字不差
    headline: str    # 一句話：這支在講什麼
    detail: str      # 為什麼值得看，一〜兩句


class YoutubeWeekly(BaseModel):
    picks: list[VideoPick]


def _fetch_feed(params: dict) -> list[dict]:
    """抓一個 YouTube feed，回傳 entry dict 清單；HTTP 錯誤往外丟給呼叫端決定 fallback。"""
    r = requests.get(FEED_URL, params=params, headers={"User-Agent": fetch.UA},
                     timeout=fetch.TIMEOUT)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    entries = []
    for e in root.findall("atom:entry", _NS):
        vid = e.findtext("yt:videoId", "", _NS)
        title = (e.findtext("atom:title", "", _NS) or "").strip()
        published = e.findtext("atom:published", "", _NS)
        desc = e.findtext("media:group/media:description", "", _NS) or ""
        if vid and title:
            entries.append({"video_id": vid, "title": title,
                            "published": published, "description": desc})
    return entries


def _channel_videos(channel: dict) -> list[dict]:
    """優先 UULF playlist feed（排除 Shorts）；失敗或為空退回 channel feed。"""
    cid = channel["channel_id"]
    entries: list[dict] = []
    if cid.startswith("UC"):
        try:
            entries = _fetch_feed({"playlist_id": "UULF" + cid[2:]})
        except requests.RequestException:
            entries = []
    if not entries:
        entries = _fetch_feed({"channel_id": cid})
    for e in entries:
        e["channel"] = channel["name"]
        e["category"] = channel.get("category", "")
    return entries


def _gather_candidates(channels: list[dict], seen: dict, max_age_days: int) -> list[dict]:
    """白名單各頻道近 N 天、沒介紹過的新片；單一頻道失敗不拖垮整段。"""
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    out: list[dict] = []
    for ch in channels:
        try:
            entries = _channel_videos(ch)
        except Exception as e:
            print(f"    [warn] YouTube 候選（{ch['name']}）抓取失敗：{e}")
            continue
        fresh = 0
        for e in entries:
            if e["video_id"] in seen:
                continue
            try:
                pub = datetime.fromisoformat(e["published"])
            except ValueError:
                continue
            if pub < cutoff:
                continue
            out.append(e)
            fresh += 1
        print(f"    YouTube 候選（{ch['name']}）：{fresh} 支")
    out.sort(key=lambda e: e["published"], reverse=True)
    if len(out) > MAX_CANDIDATES:
        print(f"    [note] 候選 {len(out)} 支超過上限 {MAX_CANDIDATES}，只送最新的（其餘捨棄）")
        out = out[:MAX_CANDIDATES]
    return out


def _prepare_payload(cands: list[dict]) -> str:
    cat_label = {"insight": "大咖觀點/訪談", "applied": "應用面/工程實務"}
    blocks = []
    for c in cands:
        lines = [
            f"### {c['video_id']}",
            f"頻道：{c['channel']}（{cat_label.get(c['category'], c['category'])}）",
            f"標題：{c['title']}",
            f"發佈：{c['published'][:10]}",
        ]
        desc = " ".join(c["description"].split())
        if desc:
            lines.append(f"說明節錄：{desc[:DESC_EXCERPT]}")
        else:
            lines.append("說明節錄：（無說明，注意可能是宣傳短片）")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def generate_weekly(client, model: str, settings: dict, channels: list[dict],
                    state: dict, date_str: str) -> dict | None:
    """產生本週 YouTube 精選段；沒有值得寫的就回 None（該週不出段）。"""
    if not channels:
        print("    [note] sources.yaml 沒有 youtube_channels，略過")
        return None
    top_n = settings.get("youtube_weekly_top_n", 3)
    max_age = settings.get("youtube_max_age_days", 7)
    seen = state.get("youtube_seen") or {}

    cands = _gather_candidates(channels, seen, max_age)
    if not cands:
        print("    [note] 本週白名單頻道無新片")
        return None

    user = (f"今天日期：{date_str}。以下是本週白名單頻道的新影片候選，請依系統指示挑出"
            f"最多 {top_n} 支並各寫一則導讀：\n\n{_prepare_payload(cands)}")
    config = {
        "system_instruction": PROMPT_PATH.read_text(encoding="utf-8"),
        "response_mime_type": "application/json",
        "response_schema": YoutubeWeekly,
        "temperature": 0.3,
    }
    resp = summarize.generate_with_retry(client, model, user, config, label="youtube-weekly")
    if resp is None:
        return None
    parsed = getattr(resp, "parsed", None)
    if isinstance(parsed, YoutubeWeekly):
        picks = parsed.picks
    else:
        try:
            picks = YoutubeWeekly.model_validate(json.loads(resp.text)).picks
        except Exception:
            print("    [warn] youtube-weekly 回傳非預期格式")
            return None

    by_id = {c["video_id"]: c for c in cands}
    valid = [p for p in picks if p.video_id in by_id][:top_n]
    if not valid:
        print("    [note] 模型判定本週無值得介紹的影片")
        return None

    points, sources, video_updates = [], [], {}
    for p in valid:
        c = by_id[p.video_id]
        points.append(f"{c['channel']}：{p.headline} {p.detail}".strip())
        sources.append({"title": f"{c['channel']}｜{c['title'][:60]}",
                        "uri": f"https://www.youtube.com/watch?v={p.video_id}"})
        video_updates[p.video_id] = {"video_id": p.video_id, "channel": c["channel"],
                                     "title": c["title"], "featured": date_str,
                                     "headline": p.headline}
    return {
        "kind": "youtube",
        "title": "本週 YouTube 精選",
        "emoji": ":movie_camera:",
        "text": "\n".join(f"- {p}" for p in points),
        "points": points,
        "sources": sources,
        "grounded": True,
        "video_updates": video_updates,
    }


def prune_seen(state: dict, date_str: str, keep_days: int = 60) -> None:
    """清掉太舊的 youtube_seen（候選窗只有 youtube_max_age_days，舊紀錄不會再撞到）。"""
    seen = state.get("youtube_seen") or {}
    today = datetime.strptime(date_str, "%Y-%m-%d")
    for vid in list(seen):
        try:
            if (today - datetime.strptime(seen[vid].get("featured", ""), "%Y-%m-%d")).days > keep_days:
                del seen[vid]
        except ValueError:
            del seen[vid]


if __name__ == "__main__":  # 單獨手測：.venv/bin/python youtube_watch.py（花 1 次一般 Gemini 呼叫，不發送不寫 state）
    import os

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
    channels = cfg.get("youtube_channels", [])
    model = os.environ.get("GEMINI_MODEL") or settings.get("model", "gemini-2.5-flash")
    state = json.loads((root / "state.json").read_text(encoding="utf-8")) if (root / "state.json").exists() else {}
    date_str = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
    result = generate_weekly(summarize.make_client(api_key), model, settings, channels, state, date_str)
    print(json.dumps(result, ensure_ascii=False, indent=2))
