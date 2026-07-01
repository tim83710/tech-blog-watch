"""發送 digest：Slack（Incoming Webhook，沿用 company_dashboard 的 Slacker 模式）+ Email（Gmail SMTP）。

版面：標題 `[公司 News] 中文標題` → 全文摘要（tldr）→「文章摘要」列點（重要補充放該點下一階）。
Slack 一篇一則；Email 一篇一封。
"""
from __future__ import annotations

import html
import os
import smtplib
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


# ---------- Email（一篇一封）----------

def render_email_html(post: dict) -> str:
    def esc(x: str) -> str:
        return html.escape(x)

    s = post["summary"]
    parts = [
        "<div style=\"font-family:-apple-system,Segoe UI,Roboto,'Helvetica Neue',sans-serif;"
        "max-width:680px;margin:0 auto;color:#1a1a1a;line-height:1.7\">",
        f"<h2 style=\"border-bottom:2px solid #5a77ff;padding-bottom:6px;margin-bottom:4px\">"
        f"{esc(_title(post))}</h2>",
        f"<div style=\"font-size:13px;color:#888;margin-bottom:14px\">{esc(post['source'])} · "
        f"<a href=\"{esc(post['url'])}\" style=\"color:#5a77ff\">原文連結</a></div>",
        f"<p>{esc(s['tldr'])}</p>",
        "<p style=\"font-weight:600;margin:18px 0 6px\">文章摘要</p>",
        "<ul style=\"padding-left:20px\">",
    ]
    for p in s.get("points", []):
        parts.append(f"<li style=\"margin-bottom:6px\">{esc(p['point'])}")
        if p.get("detail", "").strip():
            parts.append(
                f"<ul style=\"margin-top:4px;color:#555\"><li>{esc(p['detail'])}</li></ul>"
            )
        parts.append("</li>")
    parts.append("</ul></div>")
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

    with smtplib.SMTP_SSL(host, port) as smtp:
        smtp.login(user, password)
        for post in posts:  # 一篇一封
            msg = EmailMessage()
            msg["Subject"] = _title(post)
            msg["From"] = from_addr
            msg["To"] = to_addr
            msg.set_content("這封信需要支援 HTML 的信箱檢視。")
            msg.add_alternative(render_email_html(post), subtype="html")
            smtp.send_message(msg)
    print(f"  [ok] Email 已寄出 {len(posts)} 封至 {to_addr}")
