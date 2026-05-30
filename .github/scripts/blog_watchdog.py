#!/usr/bin/env python3
"""외국어 블로그 일일 발행 워치독 (self-heal + 누락 알림).

매일 렌더/발행 스케줄 창이 지난 뒤 실행한다. 오늘치 발행이 없으면
(예약 크론이 드롭됐거나 실패한 경우) 렌더 + 발행을 직접 수행해 복구하고
텔레그램으로 알린다. 이 워치독은 두 구멍을 동시에 메운다:

  1) GitHub Actions는 혼잡 시간(:00/:30)에 예약(cron) 실행을 *조용히 드롭*할 수 있다.
  2) 기존 failure-notify는 "돌다가 실패"만 알림 → "아예 안 돔"은 못 잡는다.

healthy면 아무 파일도 바꾸지 않고 상태만 로그한다.
"""
import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(os.environ.get("REPO_DIR", ".")).resolve()
STATE = REPO / "blog_publish_today_state.json"
SCRIPTS = REPO / ".github" / "scripts"
TG_TOKEN = os.environ.get("TG_TOKEN")
TG_CHAT = os.environ.get("TG_CHAT")
KST = timezone(timedelta(hours=9))


def tg(msg: str) -> None:
    if not (TG_TOKEN and TG_CHAT):
        return
    try:
        body = urllib.parse.urlencode({"chat_id": TG_CHAT, "text": msg[:4000]}).encode()
        urllib.request.urlopen(
            urllib.request.Request(
                f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage", data=body),
            timeout=10)
    except Exception:
        pass


def published_today() -> bool:
    """오늘(UTC 또는 KST) 날짜로 발행된 글이 state에 있으면 True."""
    if not STATE.exists():
        return False
    try:
        data = json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return False
    today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    today_kst = datetime.now(KST).strftime("%Y-%m-%d")
    for item in data.get("published", []):
        day = str(item.get("published_at", ""))[:10]
        if day in (today_utc, today_kst):
            return True
    return False


def run(script: str) -> tuple[bool, str]:
    try:
        r = subprocess.run(
            [sys.executable, str(SCRIPTS / script)],
            env=dict(os.environ), capture_output=True, text=True, timeout=1200)
        return r.returncode == 0, (r.stdout + r.stderr)[-1500:]
    except subprocess.TimeoutExpired:
        return False, f"{script}: timeout"


def main() -> None:
    if published_today():
        print("watchdog: healthy — 오늘치 이미 발행됨")
        return

    print("watchdog: 오늘치 누락 감지 → 자가 복구(렌더 + 발행) 시작")
    ok_r, tail_r = run("blog_today_render_cloud.py")
    if not ok_r:
        tg(f"⚠️ 그랑블루 외국블로그 워치독 — 렌더 실패\n{tail_r[-400:]}")
        raise SystemExit("watchdog render failed")

    ok_p, tail_p = run("blog_publish_today_safe.py")
    if not ok_p:
        tg(f"⚠️ 그랑블루 외국블로그 워치독 — 발행 실패\n{tail_p[-400:]}")
        raise SystemExit("watchdog publish failed")

    if published_today():
        tg("🛟 그랑블루 외국블로그 워치독 — 오늘치 누락 감지 후 자동 복구 발행 완료")
        print("watchdog: 자가 복구 성공")
    else:
        tg("⚠️ 그랑블루 외국블로그 워치독 — 복구 시도했으나 오늘치 발행 확인 안 됨 (사람 확인 필요)")
        raise SystemExit("watchdog self-heal unverified")


if __name__ == "__main__":
    main()
