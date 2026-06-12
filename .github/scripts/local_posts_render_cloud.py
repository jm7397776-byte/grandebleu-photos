#!/usr/bin/env python3
"""Render Make.com payloads for local Korean channels.

The target channels are Daangn/Karrot business profile posts and KakaoTalk
Channel posts. As of this workflow, neither channel exposes a stable public
"create channel post" API that Make can call directly for every account, so
this script publishes a Make-ready posting package: copy, photos, URLs, and
status metadata. Make can route it to any approved API connector, Telegram
approval, or browser-assisted posting step without regenerating copy.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))


def now_kst() -> datetime:
    """GitHub Actions(UTC)에서 돌아도 한국 시각을 반환. naive로 맞춰 기존 출력 포맷 유지."""
    return datetime.now(KST).replace(tzinfo=None)


REPO = Path(os.environ.get("REPO_DIR", os.getcwd()))
PHOTO_INDEX = REPO / "photos_index.json"
OUT_DIR = REPO / "local_posts"
TODAY_JSON = OUT_DIR / "today.json"
HISTORY_JSON = OUT_DIR / "history.json"

BLOG_URL = "https://jejugrandebleuyacht.blogspot.com/"
KLOOK_URL = "https://www.klook.com/en-US/activity/170600-jeju-grandebleu-sunset-yacht-experience/?utm_source={source}&utm_medium=local_social&utm_campaign=jeju_yacht"
PHONE = "064-739-7776"
INSTAGRAM = "@gb.jeju"

TOPICS = [
    {
        "key": "sunset",
        "category_hint": ["sunset", "yacht", "general"],
        "title": "제주 선셋을 가장 조용하게 보는 방법",
        "hook": "대포항에서 출발해 한 시간, 해가 낮아지는 시간을 바다 위에서 보냅니다.",
        "proof": "쌍동선 카타마란이라 가족, 커플, 부모님 동반 여행에서도 안정감이 좋습니다.",
        "local_angle": "서귀포에서 저녁 일정 전후로 넣기 좋은 짧은 코스입니다.",
    },
    {
        "key": "family",
        "category_hint": ["people", "facility", "yacht"],
        "title": "부모님과 아이가 함께 타기 좋은 제주 요트",
        "hook": "멀리 이동하지 않아도 대포항에서 바로 제주의 남쪽 바다를 만날 수 있습니다.",
        "proof": "선장과 기관장이 함께 운항하고, 550호와 620호 두 척으로 총 91명까지 운영합니다.",
        "local_angle": "가족 여행 중 짧지만 기억에 남는 코스를 찾는 분께 맞습니다.",
    },
    {
        "key": "course",
        "category_hint": ["aerial", "yacht", "general"],
        "title": "월평 주상절리와 코끼리바위를 바다에서",
        "hook": "육지 전망대와는 다른 각도로 제주의 현무암 해안을 바라봅니다.",
        "proof": "월평 주상절리, 월평 해식동굴, 코끼리바위 일대를 따라가는 1시간 항해입니다.",
        "local_angle": "중문과 서귀포 사이 여행 동선에 자연스럽게 넣기 좋습니다.",
    },
    {
        "key": "food",
        "category_hint": ["food", "yacht", "facility"],
        "title": "맥주와 간식까지 포함된 가벼운 요트 시간",
        "hook": "바다를 보며 생맥주, 와인, 제주 감귤주스, 물, 간식, 컵라면을 가볍게 즐깁니다.",
        "proof": "식사를 크게 잡지 않아도 사진과 풍경, 간단한 먹거리가 함께 있는 구성입니다.",
        "local_angle": "제주 여행 중 부담 없는 특별한 한 시간을 만들기 좋습니다.",
    },
    {
        "key": "fishing",
        "category_hint": ["fishing", "yacht", "people"],
        "title": "처음 해보는 바다낚시도 편하게",
        "hook": "요트 위에서 우럭, 쏨뱅이, 쥐치 같은 제주 바다 어종을 만날 수 있습니다.",
        "proof": "낚시만 하는 배가 아니라 항해, 풍경, 사진, 가벼운 체험이 함께 갑니다.",
        "local_angle": "아이와 함께할 체험형 제주 일정으로 쓰기 좋습니다.",
    },
    {
        "key": "premium",
        "category_hint": ["interior", "facility", "yacht"],
        "title": "호텔 여행에 어울리는 프리미엄 요트 코스",
        "hook": "신라, 파르나스 등 프리미엄 여행 동선과 함께 잡기 좋은 대포항 요트입니다.",
        "proof": "브랜드 인증 카타마란과 실내 라운지, 갑판 풍경이 함께 있는 코스입니다.",
        "local_angle": "기념일, 데이트, 접대성 여행에도 과하지 않게 어울립니다.",
    },
    {
        "key": "price",
        "category_hint": ["yacht", "general", "facility"],
        "title": "제주 요트투어 가격과 예약 전 확인할 점",
        "hook": "주간 럭셔리 투어와 선셋 투어는 시간대와 가격이 다릅니다.",
        "proof": "프로모션 기준 주간 성인 48,000원부터, 선셋 성인 56,000원부터 확인할 수 있습니다.",
        "local_angle": "네이버, Klook, KKday, Ctrip/Trip.com 등 예약 채널별 조건을 비교하면 좋습니다.",
    },
]


def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def text_seed() -> str:
    return now_kst().strftime("%Y-%m-%d")


def pick_topic(history: dict) -> dict:
    used = [item.get("topic_key") for item in history.get("items", [])[-14:]]
    scored = []
    for topic in TOPICS:
        score = int(hashlib.md5(f"{text_seed()}::{topic['key']}".encode()).hexdigest(), 16)
        penalty = 10**40 if topic["key"] in used[-4:] else 0
        scored.append((penalty + score, topic))
    return sorted(scored, key=lambda x: x[0])[0][1]


def photo_alt(photo: dict, topic: dict) -> str:
    category = photo.get("category", "yacht")
    label = {
        "sunset": "그랑블루요트 선셋 항해",
        "food": "그랑블루요트 선상 음식",
        "fishing": "그랑블루요트 낚시 체험",
        "facility": "그랑블루요트 시설",
        "interior": "그랑블루요트 실내 라운지",
        "aerial": "대포항 그랑블루요트 항공 사진",
        "people": "그랑블루요트 탑승객 풍경",
        "dolphin": "제주 바다 돌고래 풍경",
    }.get(category, "제주 대포항 그랑블루요트")
    return f"{label} - {topic['title']}"


def _is_infl_cloud(photo: dict) -> bool:
    f = str(photo.get("file", "") or photo.get("url", "")).lower()
    return photo.get("category") in ("influencer", "people") or "influencer" in f or "gb.jeju" in f


def pick_photos(topic: dict, history: dict, count: int = 3) -> list[dict]:
    # [2026-06-12 주인님] 클라우드 발행에 인플루언서 사진이 아예 안 들어가던 문제 수정.
    # 로컬 local_social_render와 동일 규칙: 인플루언서 정확히 2장 + 나머지(=썸네일 첫 사진)는
    # 풍경/요트. 같은 인물 셀카가 3장 다 차서 '반복'처럼 보이던 것도 함께 해소. used 10→20.
    index = load_json(PHOTO_INDEX, {"photos": []})
    photos = [p for p in index.get("photos", []) if p.get("url")]
    recent = {url for item in history.get("items", [])[-20:] for url in item.get("image_urls", [])}
    seed = int(hashlib.md5(f"{text_seed()}::{topic['key']}".encode()).hexdigest(), 16)
    rnd = random.Random(seed)

    def _rank(pool: list[dict]) -> list[dict]:
        pool = pool[:]
        rnd.shuffle(pool)
        fresh = [p for p in pool if p.get("url") not in recent]
        stale = [p for p in pool if p.get("url") in recent]
        return fresh + stale  # 최근 사용분은 모자랄 때만

    infl = _rank([p for p in photos if _is_infl_cloud(p)])
    scene = _rank([p for p in photos if not _is_infl_cloud(p) and p.get("category") in topic["category_hint"]])
    if not scene:
        scene = _rank([p for p in photos if not _is_infl_cloud(p)])

    selected, seen = [], set()
    # 풍경/요트(썸네일) 1장 → 인플루언서 2장 → 부족분 보충
    for photo in scene[:max(1, count - 2)] + infl[:2] + scene + infl + photos:
        url = photo.get("url")
        if not url or url in seen:
            continue
        seen.add(url); selected.append(photo)
        if len(selected) >= count:
            break
    for photo in selected:
        photo["alt"] = photo_alt(photo, topic)
    return selected


def hashtags(*items: str) -> list[str]:
    tags = []
    for item in items:
        for raw in re.split(r"[\s,/#]+", item):
            clean = re.sub(r"[^0-9A-Za-z가-힣_]", "", raw)
            if clean and clean not in tags:
                tags.append(clean)
    return tags[:10]


def render_daangn(topic: dict, photos: list[dict]) -> dict:
    body = "\n".join([
        topic["hook"],
        "",
        topic["proof"],
        topic["local_angle"],
        "",
        "운항: 대포항 출발 1시간",
        "포함: 생맥주, 와인, 감귤주스, 물, 간식, 컵라면",
        "예약: 네이버, Klook, KKday, Ctrip/Trip.com 등에서 확인 가능",
        f"문의: {PHONE}",
    ])
    return {
        "platform": "daangn",
        "api_direct_supported": False,
        "make_action": "queue_or_notify_for_business_profile_post",
        "title": topic["title"],
        "body": body,
        "hashtags": hashtags("제주요트", "서귀포", "대포항", "제주도요트투어", topic["key"]),
        "image_urls": [p["url"] for p in photos],
        "images": photos,
        "cta_label": "예약 채널 확인",
        "cta_url": KLOOK_URL.format(source="daangn"),
        "manual_target": "Daangn/Karrot Business Profile post composer",
        "compliance_note": "당근 비즈프로필 공개 게시 API 확인 전까지 자동 생성/알림/복사 패키지로 운영합니다.",
    }


def render_kakao(topic: dict, photos: list[dict]) -> dict:
    body = "\n".join([
        topic["title"],
        "",
        topic["hook"],
        topic["proof"],
        "",
        "대포항 출발 1시간 카타마란 요트투어",
        "선셋, 가족 여행, 기념일 일정에 어울리는 바다 위의 짧은 여백.",
        "",
        f"문의 {PHONE}",
        f"Instagram {INSTAGRAM}",
    ])
    return {
        "platform": "kakao_channel",
        "api_direct_supported": False,
        "make_action": "queue_or_notify_for_kakaotalk_channel_post",
        "title": topic["title"],
        "body": body,
        "button_title": "예약 확인하기",
        "button_url": KLOOK_URL.format(source="kakao_channel"),
        "image_url": photos[0]["url"] if photos else "",
        "image_urls": [p["url"] for p in photos[:2]],
        "images": photos[:2],
        "manual_target": "KakaoTalk Channel manager post composer",
        "compliance_note": "카카오 공개 API는 채널 포스트 발행보다 메시지/고객관리 중심입니다. 포스트 API 권한 확인 전까지 Make는 게시 패키지와 알림을 담당합니다.",
    }


def main():
    history = load_json(HISTORY_JSON, {"items": []})
    topic = pick_topic(history)
    photos = pick_photos(topic, history, 3)
    generated_at = now_kst().isoformat(timespec="seconds")
    payload = {
        "date": now_kst().date().isoformat(),
        "generated_at": generated_at,
        "publish": True,
        "topic_key": topic["key"],
        "topic_title": topic["title"],
        "channels": {
            "daangn": render_daangn(topic, photos),
            "kakao_channel": render_kakao(topic, photos),
        },
        "make": {
            "source_url": "https://raw.githubusercontent.com/jm7397776-byte/grandebleu-photos/main/local_posts/today.json",
            "suggested_schedule_kst": "10:30",
            "routing": [
                "HTTP GET today.json",
                "Iterator over channels",
                "Telegram/Slack approval or HTTP connector if an approved posting API exists",
                "Log success/failure",
            ],
        },
        "image_urls": [p["url"] for p in photos],
    }
    save_json(TODAY_JSON, payload)
    history.setdefault("items", []).append({
        "date": payload["date"],
        "generated_at": generated_at,
        "topic_key": topic["key"],
        "title": topic["title"],
        "image_urls": payload["image_urls"],
    })
    history["items"] = history["items"][-120:]
    save_json(HISTORY_JSON, history)
    print(f"local_posts/today.json rendered: {topic['key']} images={len(photos)}")


if __name__ == "__main__":
    main()
