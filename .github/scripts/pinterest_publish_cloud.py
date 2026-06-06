#!/usr/bin/env python3
"""Pinterest API v5 직접 발행 — make.com 우회로 중복 사진 원천 차단.

pinterest_today.json(picker가 만든 무중복 1장)을 읽어 Pinterest API로 직접 게시.
- make.com 제거 → make 측 중복/재실행 사고 차단.
- picker의 무중복(1489장 풀·history 60+ 회피)을 그대로 활용.
- 발행 성공분은 pinterest_published.json에 기록 → 같은 파일 재발행 2차 차단.

필요 시크릿(둘 중 하나):
  PINTEREST_ACCESS_TOKEN                         (장기 토큰이면 이것만)
  또는 PINTEREST_APP_ID + PINTEREST_APP_SECRET + PINTEREST_REFRESH_TOKEN (자동 갱신)
Pinterest 앱은 Standard access여야 쓰기(게시) 가능 (Trial은 403).
"""
import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(os.environ.get("REPO_DIR", ".")).resolve()
TODAY = ROOT / "pinterest_today.json"
PUBLISHED = ROOT / "pinterest_published.json"
KST = timezone(timedelta(hours=9))


def tg(msg):
    t, c = os.environ.get("TG_TOKEN"), os.environ.get("TG_CHAT")
    if not (t and c):
        return
    try:
        urllib.request.urlopen(urllib.request.Request(
            f"https://api.telegram.org/bot{t}/sendMessage",
            data=urllib.parse.urlencode({"chat_id": c, "text": msg[:3500]}).encode()), timeout=10)
    except Exception:
        pass


def access_token():
    tok = os.environ.get("PINTEREST_ACCESS_TOKEN")
    if tok:
        return tok
    app_id = os.environ.get("PINTEREST_APP_ID")
    secret = os.environ.get("PINTEREST_APP_SECRET")
    refresh = os.environ.get("PINTEREST_REFRESH_TOKEN")
    if not (app_id and secret and refresh):
        return None
    basic = base64.b64encode(f"{app_id}:{secret}".encode()).decode()
    body = urllib.parse.urlencode({"grant_type": "refresh_token", "refresh_token": refresh}).encode()
    req = urllib.request.Request(
        "https://api.pinterest.com/v5/oauth/token", data=body,
        headers={"Authorization": f"Basic {basic}",
                 "Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r).get("access_token")


def load_published():
    if PUBLISHED.exists():
        try:
            return json.loads(PUBLISHED.read_text(encoding="utf-8")).get("files", [])
        except Exception:
            pass
    return []


def main():
    if not TODAY.exists():
        print("pinterest_today.json 없음 — skip"); return 0
    post = json.loads(TODAY.read_text(encoding="utf-8"))
    fname = post.get("file", "")
    published = load_published()

    # 2차 중복 차단: 최근 200개 발행 파일과 겹치면 skip
    if fname and fname in set(published[-200:]):
        msg = f"⏭️ 핀터 중복 차단: {fname} 은 이미 최근 발행됨 → 게시 안 함"
        print(msg); tg(msg); return 0

    try:
        token = access_token()
    except urllib.error.HTTPError as e:
        d = e.read().decode("utf-8", "replace")[:200]
        print(f"토큰 발급 실패 {e.code}: {d}"); tg(f"⚠️ 핀터 토큰 실패: {d}"); return 1
    if not token:
        print("PINTEREST 토큰 시크릿 없음 — 게시 skip (PINTEREST_ACCESS_TOKEN 또는 APP_ID/SECRET/REFRESH 필요)")
        return 0

    payload = {
        "board_id": post.get("board_id", ""),
        "title": (post.get("title", "") or "")[:100],
        "description": (post.get("description", "") or "")[:800],
        "link": post.get("link", ""),
        "media_source": {"source_type": "image_url", "url": post["url"]},
    }
    req = urllib.request.Request(
        "https://api.pinterest.com/v5/pins",
        data=json.dumps(payload).encode("utf-8"), method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.load(r)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        print(f"핀 생성 실패 HTTP {e.code}: {detail}")
        tg(f"⚠️ 핀터레스트 발행 실패 {e.code}: {detail[:200]}")
        return 1

    pin_id = d.get("id", "")
    published.append(fname)
    PUBLISHED.write_text(json.dumps({"files": published[-500:],
                                     "last": {"pin_id": pin_id, "file": fname,
                                              "at": datetime.now(KST).isoformat(timespec="seconds")}},
                                    ensure_ascii=False, indent=2), encoding="utf-8")
    msg = f"📌 핀터레스트 발행 완료 (API 직접)\n- 사진: {fname}\n- 제목: {payload['title']}\n- pin_id: {pin_id}"
    print(msg); tg(msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
