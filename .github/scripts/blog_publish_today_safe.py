#!/usr/bin/env python3
"""Publish blog_today.json to Blogger once, safely.

Uses a separate OAuth client/token set from the old blocked publisher:
  BLOGGER_SAFE_CLIENT_ID
  BLOGGER_SAFE_CLIENT_SECRET
  BLOGGER_SAFE_REFRESH_TOKEN
  BLOGGER_BLOG_ID

If Blogger API writes are blocked, falls back to Blogger's official
Mail2Blogger address:
  MAIL2BLOGGER_EMAIL
  GMAIL_SEND_REFRESH_TOKEN
  SMTP_SERVER
  SMTP_PORT
  SMTP_USER
  SMTP_PASSWORD

The script exits successfully when safe credentials are absent, so the workflow can
stay scheduled without repeatedly failing before OAuth setup is complete.
"""
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import base64
import json
import os
import smtplib
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

REPO = Path(os.environ.get("REPO_DIR", ".")).resolve()
TODAY = REPO / "blog_today.json"
STATE = REPO / "blog_publish_today_state.json"
LOG = REPO / "blog_queue" / "publish_today.log"


def telegram_notify(message):
    token = os.environ.get("TG_TOKEN")
    chat = os.environ.get("TG_CHAT")
    if not token or not chat:
        log("Telegram credentials absent; notification skipped")
        return False
    body = urllib.parse.urlencode({
        "chat_id": chat,
        "text": message[:3900],
        "disable_web_page_preview": "false",
    }).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            json.load(resp)
        log("Telegram notification sent")
        return True
    except Exception as exc:
        log(f"Telegram notification failed: {type(exc).__name__}: {exc}")
        return False


def log(message):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {message}"
    print(line)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_json(path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return default


def save_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def refresh_access_token():
    client_id = os.environ.get("BLOGGER_SAFE_CLIENT_ID")
    client_secret = os.environ.get("BLOGGER_SAFE_CLIENT_SECRET")
    refresh_token = os.environ.get("BLOGGER_SAFE_REFRESH_TOKEN")
    if not (client_id and client_secret and refresh_token):
        log("safe OAuth credentials absent; skipping publish")
        return None
    body = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }).encode()
    req = urllib.request.Request(
        "https://oauth2.googleapis.com/token",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.load(resp)
    return data.get("access_token")


def blogger_insert(blog_id, token, post):
    payload = {
        "kind": "blogger#post",
        "title": post["title"],
        "content": post["content"],
        "labels": post.get("labels", []),
    }
    url = f"https://www.googleapis.com/blogger/v3/blogs/{blog_id}/posts/?isDraft=false"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
    )
    with urllib.request.urlopen(req, timeout=45) as resp:
        return json.load(resp)


def blogger_find_recent_post(blog_id, token, title, attempts=12, delay=10):
    url = (
        f"https://www.googleapis.com/blogger/v3/blogs/{blog_id}/posts/"
        "?fetchBodies=false&maxResults=10&status=live"
    )
    for attempt in range(1, attempts + 1):
        req = urllib.request.Request(
            url,
            headers={"Authorization": f"Bearer {token}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.load(resp)
        except Exception as exc:
            log(f"recent post lookup failed on attempt {attempt}: {type(exc).__name__}: {exc}")
            data = {}
        for item in data.get("items", []):
            if item.get("title") == title:
                return item
        if attempt < attempts:
            time.sleep(delay)
    return None


def build_mail_message(post):
    to_addr = os.environ.get("MAIL2BLOGGER_EMAIL")
    user = os.environ.get("SMTP_USER")
    gmail_user = os.environ.get("GMAIL_SEND_USER")
    from_addr = user or gmail_user or "me"
    if not to_addr:
        log("MAIL2BLOGGER_EMAIL absent; cannot publish fallback")
        return None

    msg = MIMEMultipart("alternative")
    msg["Subject"] = post["title"]
    msg["From"] = from_addr
    msg["To"] = to_addr

    text = f"{post['title']}\n\nGrande Bleu Jeju Yacht\n"
    html = post["content"]
    msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))
    return msg


def gmail_send_access_token():
    client_id = os.environ.get("GMAIL_SEND_CLIENT_ID") or os.environ.get("BLOGGER_SAFE_CLIENT_ID")
    client_secret = os.environ.get("GMAIL_SEND_CLIENT_SECRET") or os.environ.get("BLOGGER_SAFE_CLIENT_SECRET")
    refresh_token = os.environ.get("GMAIL_SEND_REFRESH_TOKEN")
    if not (client_id and client_secret and refresh_token):
        return None
    body = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }).encode()
    req = urllib.request.Request(
        "https://oauth2.googleapis.com/token",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp).get("access_token")


def gmail_api_send(post):
    msg = build_mail_message(post)
    if not msg:
        return None
    token = gmail_send_access_token()
    if not token:
        log("Gmail send token absent; trying SMTP fallback")
        return None

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
    payload = json.dumps({"raw": raw}).encode("utf-8")
    req = urllib.request.Request(
        "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
    )
    with urllib.request.urlopen(req, timeout=45) as resp:
        sent = json.load(resp)
    return {"id": None, "url": None, "published_by": "mail2blogger_gmail_api", "mail_id": sent.get("id")}


def smtp_send(post):
    msg = build_mail_message(post)
    if not msg:
        return None
    server = os.environ.get("SMTP_SERVER")
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASSWORD")
    if not all([server, user, password]):
        log("SMTP credentials absent; cannot publish fallback")
        return None

    context = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(server, port, timeout=45, context=context) as smtp:
            smtp.login(user, password)
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(server, port, timeout=45) as smtp:
            smtp.starttls(context=context)
            smtp.login(user, password)
            smtp.send_message(msg)
    return {"id": None, "url": None, "published_by": "mail2blogger_smtp"}


def mail2blogger_publish(post):
    try:
        result = gmail_api_send(post)
        if result:
            return result
    except Exception as exc:
        log(f"Gmail API send failed: {type(exc).__name__}: {exc}")
    return smtp_send(post)


def main():
    blog_id = os.environ.get("BLOGGER_BLOG_ID")
    if not blog_id:
        log("BLOGGER_BLOG_ID absent; skipping publish")
        return 0
    if not TODAY.exists():
        log("blog_today.json absent; skipping publish")
        return 0

    post = load_json(TODAY, {})
    required = ["title", "content", "lang", "generated_at"]
    missing = [key for key in required if not post.get(key)]
    if missing:
        raise SystemExit(f"blog_today.json missing required keys: {missing}")

    state = load_json(STATE, {"published": []})
    published = state.setdefault("published", [])
    post_key = f"{post.get('generated_at')}::{post.get('title')}"
    if any(item.get("key") == post_key for item in published):
        log(f"already published: {post.get('title')}")
        return 0

    token = refresh_access_token()
    if not token:
        return 0

    publish_method = "blogger_api"
    try:
        result = blogger_insert(blog_id, token, post)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1000]
        log(f"Blogger API publish failed HTTP {exc.code}: {detail}")
        if exc.code != 403:
            return 1
        try:
            result = mail2blogger_publish(post)
            publish_method = (result or {}).get("published_by", "mail2blogger")
        except Exception as mail_exc:
            log(f"Mail2Blogger publish failed: {type(mail_exc).__name__}: {mail_exc}")
            return 1

    if not result:
        return 1

    if result.get("published_by", "").startswith("mail2blogger"):
        found = blogger_find_recent_post(blog_id, token, post["title"])
        if found:
            result["id"] = found.get("id")
            result["url"] = found.get("url")

    item = {
        "key": post_key,
        "title": post.get("title"),
        "lang": post.get("lang"),
        "generated_at": post.get("generated_at"),
        "published_at": datetime.now().isoformat(timespec="seconds"),
        "post_id": result.get("id"),
        "url": result.get("url"),
        "method": publish_method,
    }
    published.append(item)
    state["published"] = published[-180:]
    save_json(STATE, state)
    log(f"published via {publish_method}: {item.get('url') or 'mail2blogger pending URL'}")
    telegram_notify(
        "🌍 그랑블루 외국인용 블로그 자동 업로드 완료\n"
        f"- 언어: {item.get('lang')}\n"
        f"- 제목: {item.get('title')}\n"
        f"- 방식: {item.get('method')}\n"
        f"- 링크: {item.get('url') or 'Blogger 반영 대기'}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
