"""抓取與內文擷取：RSS 優先、無 RSS 的來源爬列表頁；文章內文用 trafilatura 淨化。"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urljoin

import feedparser
import requests
import trafilatura
from bs4 import BeautifulSoup

UA = "tech-blog-watch/1.0 (+https://github.com/)"
TIMEOUT = 30


@dataclass
class Item:
    source: str
    url: str
    title: str
    published: datetime | None = None  # timezone-aware UTC if known
    text: str = field(default="", repr=False)


def _http_get(url: str) -> str | None:
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT)
        r.raise_for_status()
        return r.text
    except requests.RequestException as e:
        print(f"    [warn] GET failed {url}: {e}")
        return None


def _parse_struct_time(st) -> datetime | None:
    if not st:
        return None
    try:
        return datetime.fromtimestamp(time.mktime(st), tz=timezone.utc)
    except (OverflowError, ValueError):
        return None


def _discover_rss(source: dict) -> list[Item]:
    feed = feedparser.parse(source["url"], agent=UA)
    items: list[Item] = []
    for e in feed.entries:
        link = getattr(e, "link", None)
        if not link:
            continue
        published = _parse_struct_time(
            getattr(e, "published_parsed", None) or getattr(e, "updated_parsed", None)
        )
        items.append(
            Item(
                source=source["name"],
                url=link.split("?")[0].rstrip("/"),
                title=(getattr(e, "title", "") or "").strip() or link,
                published=published,
            )
        )
    return items


def _discover_scrape(source: dict) -> list[Item]:
    html = _http_get(source["url"])
    if not html:
        return []
    pattern = re.compile(source["link_pattern"])
    base = source["base"]
    soup = BeautifulSoup(html, "html.parser")
    seen: set[str] = set()
    items: list[Item] = []
    for a in soup.find_all("a", href=True):
        href = a["href"].split("?")[0].split("#")[0]
        path = href[len(base):] if href.startswith(base) else href
        if not path.startswith("/"):
            continue
        if not pattern.match(path):
            continue
        url = urljoin(base + "/", path.lstrip("/")).rstrip("/")
        if url in seen:
            continue
        seen.add(url)
        title = " ".join(a.get_text(" ", strip=True).split())
        items.append(Item(source=source["name"], url=url, title=title or url))
    return items


def discover(source: dict) -> list[Item]:
    """回傳某來源目前列表上的候選文章（未過濾 state / 日期）。"""
    if source["mode"] == "rss":
        return _discover_rss(source)
    if source["mode"] == "scrape":
        return _discover_scrape(source)
    raise ValueError(f"unknown mode: {source['mode']}")


def extract_article(url: str, char_limit: int) -> tuple[str, str | None]:
    """回傳 (清乾淨的內文, 若擷取得到的標題)。抓不到內文回傳空字串。"""
    downloaded = trafilatura.fetch_url(url)
    if not downloaded:
        html = _http_get(url)
        if not html:
            return "", None
        downloaded = html
    text = trafilatura.extract(
        downloaded, include_comments=False, include_tables=False, favor_recall=True
    ) or ""
    title = None
    try:
        meta = trafilatura.extract_metadata(downloaded)
        if meta and meta.title:
            title = meta.title.strip()
    except Exception:
        pass
    return text[:char_limit], title
