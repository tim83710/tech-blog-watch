"""發送 digest：Slack（Incoming Webhook，沿用 company_dashboard 的 Slacker 模式）+ Email（Gmail SMTP）。"""
from __future__ import annotations

import html
import os
import smtplib
from email.message import EmailMessage

from slack_sdk.webhook import WebhookClient


# ---------- Slack ----------

def render_slack_post(post: dict) -> str:
    s = post["summary"]
    lines = [f"*{s['title_zh']}*  ·  {post['source']}", f"<{post['url']}|原文連結>", "", s["tldr"]]
    if s.get("points"):
        lines.append("")
        lines += [f"• {p}" for p in s["points"]]
    for q in s.get("quotes", []):
        lines += ["", f"> {q['quote']}", f"_{q['note']}_"]
    return "\n".join(lines)


def send_slack(posts: list[dict], date_str: str) -> None:
    url = os.environ.get("SLACK_WEBHOOK_URL")
    if not url:
        print("  [skip] SLACK_WEBHOOK_URL 未設定，略過 Slack")
        return
    client = WebhookClient(url)
    channel = os.environ.get("SLACK_CHANNEL")  # 現代 webhook 可能忽略
    username = os.environ.get("SLACK_USERNAME", "tech-blog-watch")

    def _send(text: str) -> None:
        payload = {"text": text, "username": username, "icon_emoji": ":satellite_antenna:"}
        if channel:
            payload["channel"] = channel
        resp = client.send_dict(payload)
        if resp.status_code != 200:
            print(f"  [warn] Slack 回應 {resp.status_code}: {resp.body}")

    header = f":satellite_antenna: *tech-blog-watch — {date_str}*　今日 {len(posts)} 篇新文章"
    _send(header)
    for post in posts:
        _send(render_slack_post(post))
    print(f"  [ok] Slack 已送出 {len(posts)} 篇")


def send_slack_message(text: str) -> None:
    url = os.environ.get("SLACK_WEBHOOK_URL")
    if not url:
        return
    WebhookClient(url).send_dict(
        {"text": text, "username": os.environ.get("SLACK_USERNAME", "tech-blog-watch"),
         "icon_emoji": ":satellite_antenna:"}
    )


# ---------- Email ----------

def render_email_html(posts: list[dict], date_str: str) -> str:
    def esc(x: str) -> str:
        return html.escape(x)

    parts = [
        "<div style=\"font-family:-apple-system,Segoe UI,Roboto,'Helvetica Neue',sans-serif;"
        "max-width:680px;margin:0 auto;color:#1a1a1a;line-height:1.6\">",
        f"<h2 style=\"border-bottom:2px solid #5a77ff;padding-bottom:6px\">📡 tech-blog-watch"
        f"<span style=\"color:#888;font-weight:normal\"> — {esc(date_str)}</span></h2>",
        f"<p style=\"color:#666\">今日 {len(posts)} 篇新文章</p>",
    ]
    for post in posts:
        s = post["summary"]
        parts.append("<hr style=\"border:none;border-top:1px solid #eee;margin:28px 0\">")
        parts.append(
            f"<h3 style=\"margin-bottom:2px\">{esc(s['title_zh'])}</h3>"
            f"<div style=\"font-size:13px;color:#888;margin-bottom:10px\">{esc(post['source'])} · "
            f"<a href=\"{esc(post['url'])}\" style=\"color:#5a77ff\">原文連結</a></div>"
            f"<p>{esc(s['tldr'])}</p>"
        )
        if s.get("points"):
            parts.append("<ul>" + "".join(f"<li>{esc(p)}</li>" for p in s["points"]) + "</ul>")
        for q in s.get("quotes", []):
            parts.append(
                "<blockquote style=\"margin:12px 0;padding:8px 14px;border-left:3px solid #ff5d5b;"
                f"background:#fafafa;color:#333\">{esc(q['quote'])}"
                f"<div style=\"font-size:13px;color:#777;margin-top:6px\">{esc(q['note'])}</div>"
                "</blockquote>"
            )
    parts.append("</div>")
    return "\n".join(parts)


def send_email(posts: list[dict], date_str: str) -> None:
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASSWORD")
    to_addr = os.environ.get("EMAIL_TO")
    if not (user and password and to_addr):
        print("  [skip] SMTP_USER / SMTP_PASSWORD / EMAIL_TO 未齊，略過 Email")
        return
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "465"))

    msg = EmailMessage()
    msg["Subject"] = f"📡 tech-blog-watch {date_str} — {len(posts)} 篇新文章"
    msg["From"] = os.environ.get("EMAIL_FROM", user)
    msg["To"] = to_addr
    msg.set_content("這封信需要支援 HTML 的信箱檢視。")
    msg.add_alternative(render_email_html(posts, date_str), subtype="html")

    with smtplib.SMTP_SSL(host, port) as smtp:
        smtp.login(user, password)
        smtp.send_message(msg)
    print(f"  [ok] Email 已寄至 {to_addr}")
