"""每週「GitHub 專案」段：蒐集 HN 高分 GitHub 連結 + GitHub Trending（weekly），
交給 Gemini 選出值得認識的專案並寫繁中介紹。選題口味的單一事實來源在 prompts/github-weekly.md。

去重邏輯：介紹過的 repo 記在 state.json 的 github_repos（{repo 小寫: {repo, last_featured, summary}}）。
再次入選的條件是「距上次介紹 ≥ refeature_days 且之後有新的 GitHub Release」，並把 release
內容餵給模型，讓列點寫「這次更新了什麼」而不是重覆介紹；沒有正式 Release 的 repo 不會重覆出現。

輸出形狀與脈動段相容（notify 直接沿用 pulse 渲染）：
{"kind": "github", "title", "emoji", "text", "points", "sources", "grounded": True,
 "repo_updates": {...}}   # repo_updates 由 main 在正式跑時合併進 state，dry-run 不寫
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from pydantic import BaseModel

import fetch
import summarize

PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "github-weekly.md"

HN_API = "https://hn.algolia.com/api/v1/search"
TRENDING_URL = "https://github.com/trending?since=weekly"
GH_API = "https://api.github.com"

MAX_CANDIDATES = 12      # 送進 Gemini 前的候選上限（控 README 抓取次數與 token）
README_EXCERPT = 3000    # README 節錄字元數
RELEASE_EXCERPT = 1500   # release notes 節錄字元數

# github.com/<owner>/<repo> 以外的第一層路徑（非 repo），比對時排除
_NON_REPO_OWNERS = {
    "trending", "topics", "collections", "sponsors", "orgs", "features", "about",
    "blog", "explore", "marketplace", "apps", "settings", "site", "contact",
    "pricing", "login", "signup", "search", "notifications", "enterprise", "events",
    "customer-stories", "readme", "resources", "solutions", "team", "security",
}
_REPO_RE = re.compile(r"^/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?(?:[/?#]|$)")


class RepoPick(BaseModel):
    repo: str        # 候選清單裡的 owner/name，一字不差
    headline: str    # 一句話：這是什麼、解決什麼
    detail: str      # 為什麼紅 / 更新了什麼
    is_update: bool  # 是否為「之前介紹過、這次講更新」


class GithubWeekly(BaseModel):
    picks: list[RepoPick]


def _gh_headers() -> dict:
    import os
    headers = {"Accept": "application/vnd.github+json", "User-Agent": fetch.UA}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _repo_from_url(url: str) -> str | None:
    """只認 github.com 主網域的 /owner/repo（gist.github.com、github.io 都不算）。"""
    from urllib.parse import urlparse
    parsed = urlparse(url or "")
    if parsed.netloc.lower() not in ("github.com", "www.github.com"):
        return None
    m = _REPO_RE.match(parsed.path)
    if not m:
        return None
    owner, name = m.group(1), m.group(2)
    if owner.lower() in _NON_REPO_OWNERS:
        return None
    return f"{owner}/{name}"


def _collect_hn(min_points: int, days: int = 7) -> dict[str, dict]:
    """HN 過去 N 天、高於 min_points、連到 github.com 的 story → {repo小寫: 候選}。"""
    cutoff = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())
    params = {
        "query": "github.com",
        "restrictSearchableAttributes": "url",
        "tags": "story",
        "hitsPerPage": 100,
        "numericFilters": f"points>={min_points},created_at_i>={cutoff}",
    }
    r = requests.get(HN_API, params=params, headers={"User-Agent": fetch.UA}, timeout=fetch.TIMEOUT)
    r.raise_for_status()
    out: dict[str, dict] = {}
    for hit in r.json().get("hits", []):
        repo = _repo_from_url(hit.get("url", ""))
        if not repo:
            continue
        key = repo.lower()
        points = int(hit.get("points") or 0)
        if key not in out or points > out[key]["hn_points"]:
            out[key] = {"repo": repo, "hn_points": points,
                        "hn_title": (hit.get("title") or "").strip(),
                        "stars_week": 0, "description": ""}
    return out


def _collect_trending() -> dict[str, dict]:
    """GitHub Trending（weekly）頁 → {repo小寫: 候選}，依頁面順序附 rank。"""
    r = requests.get(TRENDING_URL, headers={"User-Agent": fetch.UA}, timeout=fetch.TIMEOUT)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    out: dict[str, dict] = {}
    for rank, article in enumerate(soup.find_all("article", class_="Box-row")):
        a = article.find("h2")
        link = a.find("a") if a else None
        if not link or not link.get("href"):
            continue
        repo = link["href"].strip("/")
        if repo.count("/") != 1:
            continue
        desc_tag = article.find("p")
        desc = " ".join(desc_tag.get_text(" ", strip=True).split()) if desc_tag else ""
        stars_week = 0
        m = re.search(r"([\d,]+)\s+stars this week", article.get_text(" ", strip=True))
        if m:
            stars_week = int(m.group(1).replace(",", ""))
        out[repo.lower()] = {"repo": repo, "hn_points": 0, "hn_title": "",
                             "stars_week": stars_week, "description": desc,
                             "trending_rank": rank}
    return out


def _fetch_readme(repo: str) -> str:
    """GitHub API 的 readme endpoint（不管檔名叫什麼都拿得到），失敗回空字串。"""
    try:
        headers = _gh_headers() | {"Accept": "application/vnd.github.raw+json"}
        r = requests.get(f"{GH_API}/repos/{repo}/readme", headers=headers, timeout=fetch.TIMEOUT)
        if r.status_code != 200:
            return ""
        return r.text[:README_EXCERPT]
    except requests.RequestException:
        return ""


def _latest_release(repo: str) -> dict | None:
    """最新正式 Release；沒有 release（404）或失敗回 None。"""
    try:
        r = requests.get(f"{GH_API}/repos/{repo}/releases/latest",
                         headers=_gh_headers(), timeout=fetch.TIMEOUT)
        if r.status_code != 200:
            return None
        d = r.json()
        return {
            "tag": d.get("tag_name") or "",
            "name": d.get("name") or "",
            "published_at": (d.get("published_at") or "")[:10],
            "body": (d.get("body") or "")[:RELEASE_EXCERPT],
        }
    except requests.RequestException:
        return None


def _gather_candidates(min_points: int) -> list[dict]:
    """HN + Trending 合併去重；兩邊都上榜的優先，再依 HN 分數、trending 名次排序。"""
    cands: dict[str, dict] = {}
    for collector, label in ((lambda: _collect_hn(min_points), "HN"),
                             (_collect_trending, "Trending")):
        try:
            found = collector()
            print(f"    GitHub 候選（{label}）：{len(found)} 個")
        except Exception as e:
            print(f"    [warn] GitHub 候選（{label}）抓取失敗：{e}")
            found = {}
        for key, c in found.items():
            if key in cands:
                base = cands[key]
                base["hn_points"] = max(base["hn_points"], c["hn_points"])
                base["hn_title"] = base["hn_title"] or c["hn_title"]
                base["stars_week"] = max(base["stars_week"], c["stars_week"])
                base["description"] = base["description"] or c["description"]
                base["both"] = True
                if "trending_rank" in c:
                    base["trending_rank"] = c["trending_rank"]
            else:
                cands[key] = c
    # 名額分配：兩邊都上榜的優先，其餘 HN（依分數）與 Trending（依名次）各半，避免單邊佔滿
    both = [c for c in cands.values() if c.get("both")]
    hn_only = sorted((c for c in cands.values() if not c.get("both") and c["hn_points"]),
                     key=lambda c: -c["hn_points"])
    tr_only = sorted((c for c in cands.values() if not c.get("both") and not c["hn_points"]),
                     key=lambda c: c.get("trending_rank", 999))
    quota = max(0, MAX_CANDIDATES - len(both))
    picked = both + hn_only[:(quota + 1) // 2] + tr_only[:quota // 2]
    return picked[:MAX_CANDIDATES]


def _prepare_payload(cands: list[dict], featured: dict, date_str: str,
                     refeature_days: int) -> tuple[str, dict[str, dict]]:
    """把候選整理成給 Gemini 的文字；介紹過的 repo 套用 refeature 規則。

    回傳 (payload 文字, {repo小寫: 附加狀態}) —— 附加狀態記 is_refeature 供事後驗證。
    """
    today = datetime.strptime(date_str, "%Y-%m-%d")
    blocks: list[str] = []
    meta: dict[str, dict] = {}
    for c in cands:
        key = c["repo"].lower()
        prev = featured.get(key)
        refeature_note = ""
        if prev:
            try:
                last = datetime.strptime(prev.get("last_featured", ""), "%Y-%m-%d")
            except ValueError:
                last = today
            if (today - last).days < refeature_days:
                continue  # 冷卻期內不重覆
            rel = _latest_release(c["repo"])
            if not rel or not rel["published_at"] or rel["published_at"] <= prev.get("last_featured", ""):
                continue  # 上次介紹後沒有新 release → 不重覆
            refeature_note = (
                f"【之前於 {prev.get('last_featured')} 介紹過：{prev.get('summary', '')}】\n"
                f"之後發布新版 {rel['tag']} {rel['name']}（{rel['published_at']}），release notes 節錄：\n"
                f"{rel['body']}"
            )
        lines = [f"### {c['repo']}"]
        if c.get("description"):
            lines.append(f"描述：{c['description']}")
        if c.get("hn_points"):
            lines.append(f"Hacker News：{c['hn_points']} 分，標題「{c['hn_title']}」")
        if c.get("stars_week"):
            lines.append(f"GitHub Trending：本週 +{c['stars_week']:,} stars")
        if refeature_note:
            lines.append(refeature_note)
        else:
            readme = _fetch_readme(c["repo"])
            if readme:
                lines.append(f"README 節錄：\n{readme}")
        blocks.append("\n".join(lines))
        meta[key] = {"repo": c["repo"], "is_refeature": bool(refeature_note)}
    return "\n\n".join(blocks), meta


def generate_weekly(client, model: str, settings: dict, state: dict,
                    date_str: str) -> dict | None:
    """產生本週 GitHub 專案段；沒有值得寫的就回 None（該週不出段）。"""
    min_points = settings.get("github_hn_min_points", 100)
    top_n = settings.get("github_weekly_top_n", 5)
    refeature_days = settings.get("github_refeature_days", 14)

    cands = _gather_candidates(min_points)
    if not cands:
        print("    [note] 本週無 GitHub 候選")
        return None
    payload, meta = _prepare_payload(cands, state.get("github_repos") or {},
                                     date_str, refeature_days)
    if not meta:
        print("    [note] 候選都在冷卻期內且無新 release，本週不出 GitHub 段")
        return None

    user = (f"今天日期：{date_str}。以下是本週候選 GitHub 專案，請依系統指示挑出"
            f"最多 {top_n} 個並撰寫介紹：\n\n{payload}")
    config = {
        "system_instruction": PROMPT_PATH.read_text(encoding="utf-8"),
        "response_mime_type": "application/json",
        "response_schema": GithubWeekly,
        "temperature": 0.3,
    }
    resp = summarize.generate_with_retry(client, model, user, config, label="github-weekly")
    if resp is None:
        return None
    parsed = getattr(resp, "parsed", None)
    if isinstance(parsed, GithubWeekly):
        picks = parsed.picks
    else:
        try:
            picks = GithubWeekly.model_validate(json.loads(resp.text)).picks
        except Exception:
            print("    [warn] github-weekly 回傳非預期格式")
            return None

    # 只留真的在候選清單裡的 repo（防幻覺），並套 top_n 上限
    valid = [p for p in picks if p.repo.lower() in meta][:top_n]
    if not valid:
        print("    [note] 模型判定本週無值得介紹的專案")
        return None

    points, sources, repo_updates = [], [], {}
    for p in valid:
        key = p.repo.lower()
        prefix = "（更新）" if meta[key]["is_refeature"] else ""
        points.append(f"{prefix}{p.repo} — {p.headline} {p.detail}".strip())
        sources.append({"title": p.repo, "uri": f"https://github.com/{meta[key]['repo']}"})
        repo_updates[key] = {"repo": meta[key]["repo"], "last_featured": date_str,
                             "summary": p.headline}
    return {
        "kind": "github",
        "title": "本週 GitHub 專案",
        "emoji": ":star2:",
        "text": "\n".join(f"- {p}" for p in points),
        "points": points,
        "sources": sources,
        "grounded": True,
        "repo_updates": repo_updates,
    }


if __name__ == "__main__":  # 單獨手測：.venv/bin/python github_watch.py（花 1 次一般 Gemini 呼叫，不發送不寫 state）
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
    model = os.environ.get("GEMINI_MODEL") or settings.get("model", "gemini-2.5-flash")
    state = json.loads((root / "state.json").read_text(encoding="utf-8")) if (root / "state.json").exists() else {}
    date_str = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
    result = generate_weekly(summarize.make_client(api_key), model, settings, state, date_str)
    print(json.dumps(result, ensure_ascii=False, indent=2))
