#!/usr/bin/env python3
"""Diagnose Blogger API 403 — pinpoint why writes fail and whether native
scheduling (posts.publish with future publishDate) is possible.

Tests BOTH OAuth credential sets present in repo secrets:
  SAFE: BLOGGER_SAFE_CLIENT_ID / BLOGGER_SAFE_CLIENT_SECRET / BLOGGER_SAFE_REFRESH_TOKEN
  OLD : BLOGGER_OAUTH_CLIENT_ID / BLOGGER_OAUTH_CLIENT_SECRET / BLOGGER_REFRESH_TOKEN

For each working token it reports:
  1) which Google account the token belongs to (users/self)
  2) which blogs that account can author (users/self/blogs)
  3) whether BLOGGER_BLOG_ID is in that authorable list  <-- the key signal
  4) a real DRAFT insert test (write capability)
  5) a real SCHEDULED publish test 3 days out (native-scheduling capability)
     -> if it succeeds, the scheduled post is immediately reverted + deleted.

No public post is ever created. Read-only except the draft test, which is
cleaned up. Results go to stdout + Telegram.
"""
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

BLOG_ID = os.environ.get("BLOGGER_BLOG_ID", "")
KST = timezone(timedelta(hours=9))


def tg(msg):
    t = os.environ.get("TG_TOKEN")
    c = os.environ.get("TG_CHAT")
    if not (t and c):
        return
    try:
        urllib.request.urlopen(
            urllib.request.Request(
                f"https://api.telegram.org/bot{t}/sendMessage",
                data=urllib.parse.urlencode({"chat_id": c, "text": msg[:4000]}).encode(),
            ),
            timeout=10,
        )
    except Exception:
        pass


def refresh(cid, cs, rt):
    if not (cid and cs and rt):
        return None, "creds 없음"
    body = urllib.parse.urlencode({
        "client_id": cid, "client_secret": cs,
        "refresh_token": rt, "grant_type": "refresh_token",
    }).encode()
    try:
        with urllib.request.urlopen(
            urllib.request.Request(
                "https://oauth2.googleapis.com/token", data=body,
                headers={"Content-Type": "application/x-www-form-urlencoded"}),
            timeout=20) as r:
            return json.load(r).get("access_token"), None
    except urllib.error.HTTPError as e:
        return None, f"refresh HTTP {e.code}: {e.read().decode('utf-8','replace')[:200]}"
    except Exception as e:
        return None, f"refresh err: {type(e).__name__}: {e}"


def api(token, url, method="GET", data=None):
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        try:
            d = json.loads(e.read().decode("utf-8", "replace"))
        except Exception:
            d = {"_raw": "parse fail"}
        return e.code, d
    except Exception as e:
        return -1, {"_err": f"{type(e).__name__}: {e}"}


def reason_of(d):
    try:
        err = d.get("error", {})
        msg = err.get("message", "")
        rs = ",".join(e.get("reason", "") for e in err.get("errors", []))
        return f"{err.get('status','')} {msg} [{rs}]".strip()
    except Exception:
        return json.dumps(d, ensure_ascii=False)[:160]


def diagnose(label, cid, cs, rt, out):
    out.append(f"\n===== [{label}] OAuth 클라이언트 =====")
    token, err = refresh(cid, cs, rt)
    if not token:
        out.append(f"  토큰 refresh 실패: {err}")
        return
    out.append(f"  토큰 refresh OK ({token[:10]}...)")

    # 0) 토큰의 실제 scope 확인 (쓰기 권한 유무 결정적)
    try:
        with urllib.request.urlopen(
            "https://oauth2.googleapis.com/tokeninfo?access_token=" + urllib.parse.quote(token),
            timeout=15) as r:
            ti = json.load(r)
        scopes = (ti.get("scope", "") or "").split()
        out.append(f"  토큰 scope: {ti.get('scope','(없음)')}")
        has_write = "https://www.googleapis.com/auth/blogger" in scopes
        has_ro = "https://www.googleapis.com/auth/blogger.readonly" in scopes
        out.append(f"  >> 쓰기 scope(auth/blogger)={'있음 ✅' if has_write else '없음 ❌ ← 원인!'} / "
                   f"readonly={'있음' if has_ro else '없음'}")
        if not has_write:
            out.append("  => 토큰이 읽기전용. 쓰기 scope(auth/blogger)로 재인증하면 해결.")
    except Exception as e:
        out.append(f"  tokeninfo 실패: {type(e).__name__}: {e}")

    # 1) 토큰이 속한 계정
    st, me = api(token, "https://www.googleapis.com/blogger/v3/users/self")
    if st == 200:
        out.append(f"  계정(users/self): id={me.get('id')} displayName={me.get('displayName')}")
    else:
        out.append(f"  users/self HTTP {st}: {reason_of(me)}")

    # 2) 작성 가능한 블로그 목록
    st, blogs = api(token, "https://www.googleapis.com/blogger/v3/users/self/blogs")
    authorable_ids = []
    if st == 200 and isinstance(blogs, dict):
        for b in blogs.get("items", []):
            authorable_ids.append(str(b.get("id")))
            out.append(f"    · 작성가능 블로그 id={b.get('id')} name={b.get('name')} url={b.get('url')}")
        if not blogs.get("items"):
            out.append("    · 작성가능 블로그 0개 (이 계정은 어떤 블로그도 작성권한 없음)")
    else:
        out.append(f"  users/self/blogs HTTP {st}: {reason_of(blogs)}")

    in_list = str(BLOG_ID) in authorable_ids
    out.append(f"  >> 대상 BLOG_ID={BLOG_ID} 가 작성가능 목록에 있나? {'예 ✅' if in_list else '아니오 ❌ ← 원인 후보'}")

    # 3) 대상 블로그 GET
    st, b = api(token, f"https://www.googleapis.com/blogger/v3/blogs/{BLOG_ID}")
    out.append(f"  대상블로그 GET HTTP {st} name={b.get('name') if isinstance(b,dict) else '?'}")

    # 4) DRAFT 쓰기 테스트
    payload = json.dumps({
        "kind": "blogger#post",
        "title": f"__diag_{datetime.now(KST).strftime('%Y%m%d_%H%M%S')}",
        "content": "<p>oauth write capability diagnostic — auto-deleted</p>",
    }).encode()
    st, ins = api(token, f"https://www.googleapis.com/blogger/v3/blogs/{BLOG_ID}/posts/?isDraft=true",
                  method="POST", data=payload)
    out.append(f"  DRAFT insert HTTP {st}")
    if st != 200:
        out.append(f"    ❌ 쓰기 거부: {reason_of(ins)}")
        out.append("    => 쓰기 자체가 막힘. native 예약도 불가(같은 권한). 계정/작성자 권한 수리 필요.")
        return
    draft_id = ins.get("id")
    out.append(f"    ✅ draft 쓰기 성공 id={draft_id} => 쓰기 권한 있음!")

    # 5) SCHEDULED publish 테스트 (3일 뒤)
    future = (datetime.now(KST) + timedelta(days=3)).replace(microsecond=0).isoformat()
    st, pub = api(token,
                  f"https://www.googleapis.com/blogger/v3/blogs/{BLOG_ID}/posts/{draft_id}/publish"
                  f"?publishDate={urllib.parse.quote(future)}",
                  method="POST")
    out.append(f"  SCHEDULED publish ({future}) HTTP {st}")
    if st == 200:
        out.append(f"    ★ native 예약 성공! status={pub.get('status')} published={pub.get('published')}")
        out.append("    => 이 클라이언트로 native 예약발행 가능 확정! 바로 빌드 가능.")
    else:
        out.append(f"    예약 publish 거부: {reason_of(pub)}")

    # cleanup: revert(예약취소) → delete
    api(token, f"https://www.googleapis.com/blogger/v3/blogs/{BLOG_ID}/posts/{draft_id}/revert", method="POST")
    sd, _ = api(token, f"https://www.googleapis.com/blogger/v3/blogs/{BLOG_ID}/posts/{draft_id}", method="DELETE")
    out.append(f"  cleanup(revert+delete) HTTP {sd}")


def main():
    out = [f"🔎 Blogger OAuth 진단 (BLOG_ID={BLOG_ID})  {datetime.now(KST).isoformat(timespec='seconds')}"]
    diagnose("SAFE",
             os.environ.get("BLOGGER_SAFE_CLIENT_ID"),
             os.environ.get("BLOGGER_SAFE_CLIENT_SECRET"),
             os.environ.get("BLOGGER_SAFE_REFRESH_TOKEN"), out)
    diagnose("OLD",
             os.environ.get("BLOGGER_OAUTH_CLIENT_ID"),
             os.environ.get("BLOGGER_OAUTH_CLIENT_SECRET"),
             os.environ.get("BLOGGER_REFRESH_TOKEN"), out)
    report = "\n".join(out)
    print(report)
    tg(report)


if __name__ == "__main__":
    main()
