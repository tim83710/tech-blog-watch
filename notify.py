"""發送 digest：Slack（Incoming Webhook，沿用 company_dashboard 的 Slacker 模式）+ Email（Gmail SMTP）。

版面：標題 `[公司 News] 中文標題` → 全文摘要（tldr）→「文章摘要」列點（重要補充放該點下一階）。
Slack 一篇一則；Email 一天彙整成一封（總覽 → 標題清單 → 每篇全文）。
"""
from __future__ import annotations

import html
import os
import smtplib
from collections import OrderedDict
from email.message import EmailMessage

from slack_sdk.webhook import WebhookClient


def _company(post: dict) -> str:
    return post.get("company") or post["source"]


def _title(post: dict) -> str:
    return f"[{_company(post)} News] {post['summary']['title_zh']}"


# ---------- Slack ----------

def render_slack_post(post: dict) -> str:
    s = post["summary"]
    lines = [f"*{_title(post)}*", f"<{post['url']}|原文連結>", "", s["tldr"], "", "*文章摘要*"]
    for p in s.get("points", []):
        lines.append(f"• {p['point']}")
        if p.get("detail", "").strip():
            lines.append(f"    ↳ {p['detail']}")
    if s.get("use_case", "").strip():
        lines += ["", "*實際應用*", s["use_case"]]
    return "\n".join(lines)


def _slack_send(client: WebhookClient, text: str) -> None:
    payload = {"text": text, "username": os.environ.get("SLACK_USERNAME", "tech-blog-watch"),
               "icon_emoji": ":satellite_antenna:"}
    channel = os.environ.get("SLACK_CHANNEL")
    if channel:
        payload["channel"] = channel
    resp = client.send_dict(payload)
    if resp.status_code != 200:
        print(f"  [warn] Slack 回應 {resp.status_code}: {resp.body}")


def send_slack(posts: list[dict], date_str: str) -> None:
    url = os.environ.get("SLACK_WEBHOOK_URL")
    if not url:
        print("  [skip] SLACK_WEBHOOK_URL 未設定，略過 Slack")
        return
    client = WebhookClient(url)
    _slack_send(client, f"*tech-blog-watch — {date_str}*　今日 {len(posts)} 篇新文章")
    for post in posts:
        _slack_send(client, render_slack_post(post))
    print(f"  [ok] Slack 已送出 {len(posts)} 篇")


def send_slack_message(text: str) -> None:
    url = os.environ.get("SLACK_WEBHOOK_URL")
    if not url:
        return
    _slack_send(WebhookClient(url), text)


# ---------- Email（一天彙整成一封）----------

def _esc(x: str) -> str:
    return html.escape(x or "")


def _counts_by_company(posts: list[dict]) -> "OrderedDict[str, int]":
    """依出現順序回傳 {公司: 篇數}。"""
    counts: "OrderedDict[str, int]" = OrderedDict()
    for p in posts:
        c = _company(p)
        counts[c] = counts.get(c, 0) + 1
    return counts


def _render_article_html(post: dict, anchor: str) -> str:
    s = post["summary"]
    parts = [
        f"<div id=\"{anchor}\" style=\"margin:0 0 34px\">",
        f"<h2 style=\"border-bottom:2px solid #5a77ff;padding-bottom:6px;margin-bottom:4px;"
        f"font-size:19px\">{_esc(_title(post))}</h2>",
        f"<div style=\"font-size:13px;color:#888;margin-bottom:14px\">{_esc(post['source'])} · "
        f"<a href=\"{_esc(post['url'])}\" style=\"color:#5a77ff\">原文連結</a></div>",
        f"<p>{_esc(s['tldr'])}</p>",
        "<p style=\"font-weight:600;margin:18px 0 6px\">文章摘要</p>",
        "<ul style=\"padding-left:20px\">",
    ]
    for p in s.get("points", []):
        parts.append(f"<li style=\"margin-bottom:6px\">{_esc(p['point'])}")
        if p.get("detail", "").strip():
            parts.append(
                f"<ul style=\"margin-top:4px;color:#555\"><li>{_esc(p['detail'])}</li></ul>"
            )
        parts.append("</li>")
    parts.append("</ul>")
    if s.get("use_case", "").strip():
        parts.append("<p style=\"font-weight:600;margin:18px 0 6px\">實際應用</p>")
        parts.append(
            f"<p style=\"background:#f5f7ff;border-left:3px solid #5a77ff;"
            f"padding:10px 14px;margin:0\">{_esc(s['use_case'])}</p>"
        )
    parts.append("</div>")
    return "\n".join(parts)


def render_email_digest_html(posts: list[dict], date_str: str) -> str:
    counts = _counts_by_company(posts)
    overview = "、".join(f"{c}（{n} 篇）" for c, n in counts.items())

    parts = [
        "<div style=\"font-family:-apple-system,Segoe UI,Roboto,'Helvetica Neue',sans-serif;"
        "max-width:680px;margin:0 auto;color:#1a1a1a;line-height:1.7\">",
        f"<h1 style=\"font-size:22px;margin:0 0 4px\">{_esc(date_str)} Tech News Summary</h1>",
        f"<p style=\"font-size:14px;color:#555;margin:0 0 18px\">今日 {len(counts)} 家公司、"
        f"共 {len(posts)} 篇：{_esc(overview)}</p>",
        "<div style=\"background:#f5f7ff;border-radius:8px;padding:14px 18px;margin:0 0 30px\">",
        "<p style=\"font-weight:600;margin:0 0 8px\">今日文章</p>",
        "<ol style=\"padding-left:20px;margin:0\">",
    ]
    for i, post in enumerate(posts):
        anchor = f"a{i}"
        parts.append(
            f"<li style=\"margin-bottom:5px\"><a href=\"#{anchor}\" "
            f"style=\"color:#333;text-decoration:none\">{_esc(_title(post))}</a></li>"
        )
    parts += ["</ol>", "</div>"]

    for i, post in enumerate(posts):
        parts.append(_render_article_html(post, f"a{i}"))

    parts.append("</div>")
    return "\n".join(parts)


def _smtp_config() -> tuple[str, str, str, str, str, int] | None:
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASSWORD")
    to_addr = os.environ.get("EMAIL_TO")
    if not (user and password and to_addr):
        return None
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "465"))
    from_addr = os.environ.get("EMAIL_FROM", user)
    return user, password, to_addr, from_addr, host, port


def send_email(posts: list[dict], date_str: str) -> None:
    cfg = _smtp_config()
    if not cfg:
        print("  [skip] SMTP_USER / SMTP_PASSWORD / EMAIL_TO 未齊，略過 Email")
        return
    user, password, to_addr, from_addr, host, port = cfg

    msg = EmailMessage()
    msg["Subject"] = f"{date_str} Tech News Summary"
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg.set_content("這封信需要支援 HTML 的信箱檢視。")
    msg.add_alternative(render_email_digest_html(posts, date_str), subtype="html")

    with smtplib.SMTP_SSL(host, port) as smtp:
        smtp.login(user, password)
        smtp.send_message(msg)
    print(f"  [ok] Email 已寄出 1 封（{len(posts)} 篇）至 {to_addr}")
