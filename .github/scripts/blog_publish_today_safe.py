#!/usr/bin/env python3
"""Publish blog_today.json to Blogger once, safely.

Uses a separate OAuth client/token set from the old blocked publisher:
  BLOGGER_SAFE_CLIENT_ID
  BLOGGER_SAFE_CLIENT_SECRET
  BLOGGER_SAFE_REFRESH_TOKEN
  BLOGGER_BLOG_ID

The script exits successfully when safe credentials are absent, so the workflow can
stay scheduled without repeatedly failing before OAuth setup is complete.
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

REPO = Path(os.environ.get("REPO_DIR", ".")).resolve()
TODAY = REPO / "blog_today.json"
STATE = REPO / "blog_publish_today_state.json"
LOG = REPO / "blog_queue" / "publish_today.log"


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

    try:
        result = blogger_insert(blog_id, token, post)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1000]
        log(f"Blogger publish failed HTTP {exc.code}: {detail}")
        return 1

    item = {
        "key": post_key,
        "title": post.get("title"),
        "lang": post.get("lang"),
        "generated_at": post.get("generated_at"),
        "published_at": datetime.now().isoformat(timespec="seconds"),
        "post_id": result.get("id"),
        "url": result.get("url"),
    }
    published.append(item)
    state["published"] = published[-180:]
    save_json(STATE, state)
    log(f"published: {item['url']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
