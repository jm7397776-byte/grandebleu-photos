#!/usr/bin/env python3
"""GBP today.json 일일 렌더러 (클라우드 / GitHub Actions).

current.json(이번 주 4언어 메타)에서 오늘 요일 언어 1편을 골라 today.json으로 출력.
Mac launchd(jarvis_google_posts_today.py)를 대체 — Mac을 꺼놔도 365일 작동.

요일별 언어(KST 기준): 월 zh-CN · 화 en · 수 ja · 목 ko · 금/토/일 skip(publish=false).
GitHub Actions 러너는 UTC이므로 날짜·요일은 반드시 KST로 계산한다(naive datetime.now 금지).
"""
from __future__ import annotations

import json
import os
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
REPO_DIR = Path(os.environ.get("REPO_DIR", os.getcwd()))
RAW_BASE = "https://raw.githubusercontent.com/jm7397776-byte/grandebleu-photos/main"
CURRENT_JSON = REPO_DIR / "google_posts" / "current.json"
CURRENT_URL = f"{RAW_BASE}/google_posts/current.json"
TODAY_JSON = REPO_DIR / "google_posts" / "today.json"
KLOOK_FALLBACK = (
    "https://www.klook.com/en-US/activity/"
    "170600-jeju-grandebleu-sunset-yacht-experience/?utm_source=google_posts"
)

WEEKDAY_LANG = {0: "zh-CN", 1: "en", 2: "ja", 3: "ko"}  # Mon..Thu
WEEKDAY_NAME = {0: "월", 1: "화", 2: "수", 3: "목"}


def now_kst() -> datetime:
    """GitHub Actions(UTC)에서도 한국 시각 반환 (naive, 기존 출력 포맷 유지)."""
    return datetime.now(KST).replace(tzinfo=None)


def fetch_text(url: str, timeout: int = 15) -> str:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read().decode("utf-8")


def load_current() -> dict:
    """checkout된 repo 파일 우선, 없으면 raw fetch (로컬 테스트 호환)."""
    if CURRENT_JSON.exists():
        return json.loads(CURRENT_JSON.read_text(encoding="utf-8"))
    return json.loads(fetch_text(CURRENT_URL))


def photo_to_url(photo: str) -> str:
    """current.json의 photo는 파일명만(yacht_82_general.jpg)이라 raw URL로 조합."""
    if not photo:
        return ""
    if photo.startswith("http"):
        return photo
    return f"{RAW_BASE}/{photo.lstrip('/')}"


def build_today(current: dict) -> dict:
    today = now_kst().date()
    weekday = today.weekday()
    base = {
        "date": today.isoformat(),
        "weekday": weekday,
        "_generator": "google_posts_today_cloud.py",
    }

    if weekday not in WEEKDAY_LANG:
        return {**base, "publish": "false", "reason": "weekend skip (Fri/Sat/Sun)"}

    lang_code = WEEKDAY_LANG[weekday]
    lang_data = current.get("languages", {}).get(lang_code, {})
    if not lang_data:
        return {**base, "publish": "false", "reason": f"{lang_code} data missing in current.json"}

    body_text = ""
    body_url = lang_data.get("body_url", "")
    if body_url:
        try:
            body_text = fetch_text(body_url)
        except Exception as e:
            print(f"[WARN] body fetch failed: {e}")

    return {
        **base,
        "weekday_name": WEEKDAY_NAME[weekday],
        "publish": "true",
        "week": current.get("week"),
        "lang": lang_code,
        "lang_label": lang_data.get("label"),
        "body": body_text,
        "body_length": len(body_text),
        "photo_url": photo_to_url(lang_data.get("photo", "")),
        "seo_keywords": lang_data.get("seo_keywords"),
        "cta_action_type": "BOOK",
        "cta_url": current.get("klook_url") or KLOOK_FALLBACK,
        "_make_com_hint": "HTTP module → JSON parse → GBP Create Post (photo_url=media, body=summary, cta_action_type+cta_url)",
    }


def main() -> int:
    try:
        current = load_current()
    except Exception as e:
        print(f"[ERROR] current.json load 실패: {e}")
        return 1
    today_meta = build_today(current)
    TODAY_JSON.parent.mkdir(parents=True, exist_ok=True)
    TODAY_JSON.write_text(
        json.dumps(today_meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        f"today.json: date={today_meta['date']} "
        f"publish={today_meta['publish']} lang={today_meta.get('lang', 'skip')} "
        f"body_len={today_meta.get('body_length', 0)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
