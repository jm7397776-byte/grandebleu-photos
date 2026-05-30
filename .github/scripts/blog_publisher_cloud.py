#!/usr/bin/env python3
"""🌏 자비스 글로벌 자동 발행 — Blogger·Medium·WordPress.com 통합.

매일 04:50 가동 (global_content 04:30 직후):
- vault `global_content/en/`, `zh-CN/`, `ja/` 새 글 자동 게시
- 사용자가 토큰 발급한 채널 *모두 동시* 게시
- 발행 기록 .jarvis_publisher_state.json 누적

추가 결제 0원. 사용자 1회 토큰 발급만 필요.
"""
from __future__ import annotations

import base64
import json
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

import os

REPO = Path(os.environ.get("REPO_DIR", os.getcwd()))
GLOBAL_CONTENT = REPO / "blog_queue"
CREDS_FILE = Path("/__nonexistent__")  # 클라우드: os.environ 사용
STATE = REPO / "blog_queue" / "_publish_state.json"
LOG = REPO / "blog_queue" / "publish.log"
TG_TOKEN = os.environ.get("TG_TOKEN", "")
TG_CHAT = os.environ.get("TG_CHAT", "")


def _log(m: str):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"[{datetime.now().isoformat(timespec='seconds')}] {m}\n")


def _load_env() -> dict:
    """GitHub Actions: 환경변수(Secrets)에서 인증 로드."""
    keys = [
        "BLOGGER_BLOG_ID", "BLOGGER_OAUTH_TOKEN", "BLOGGER_REFRESH_TOKEN",
        "BLOGGER_OAUTH_CLIENT_ID", "BLOGGER_OAUTH_CLIENT_SECRET",
        "GRANDEBLEU_KLOOK_URL",
    ]
    env = {k: os.environ[k] for k in keys if os.environ.get(k)}
    # credentials.env 파일이 있으면 보조 로드 (로컬 테스트용)
    if CREDS_FILE.exists():
        for line in CREDS_FILE.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, _, v = line.partition("=")
                env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return env


def refresh_blogger_token(env: dict) -> str | None:
    """refresh_token + client_secret으로 새 access_token 발급 후 .env 자동 업데이트.

    client_secret이 있으면 영구 자동 갱신 가능. 없으면 None 반환 (수동 갱신 필요)."""
    rt = env.get("BLOGGER_REFRESH_TOKEN")
    cid = env.get("BLOGGER_OAUTH_CLIENT_ID")
    cs = env.get("BLOGGER_OAUTH_CLIENT_SECRET")
    if not (rt and cid and cs):
        _log("토큰 갱신 스킵 — refresh_token/client_id/client_secret 중 누락")
        return None
    body = urllib.parse.urlencode({
        "client_id": cid, "client_secret": cs,
        "refresh_token": rt, "grant_type": "refresh_token",
    }).encode()
    try:
        req = urllib.request.Request("https://oauth2.googleapis.com/token", data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"})
        with urllib.request.urlopen(req, timeout=15) as r:
            d = json.load(r)
        new_tok = d.get("access_token")
        if new_tok:
            env["BLOGGER_OAUTH_TOKEN"] = new_tok
            os.environ["BLOGGER_OAUTH_TOKEN"] = new_tok
            _log(f"토큰 자동 갱신 — {new_tok[:30]}... (expires {d.get('expires_in', '?')}초)")
            return new_tok
    except Exception as e:
        _log(f"토큰 갱신 실패: {e}")
    return None


def blogger_call_with_refresh(env: dict, url: str, data: bytes = None,
                                method: str = "GET") -> tuple:
    """Blogger API 호출 + 401 시 자동 토큰 refresh + 재시도. (response_dict, env)."""
    token = env.get("BLOGGER_OAUTH_TOKEN")
    for attempt in range(2):
        req = urllib.request.Request(url, data=data, method=method,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {token}"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r), env
        except urllib.error.HTTPError as e:
            if e.code in (401, 403) and attempt == 0:
                new_tok = refresh_blogger_token(env)
                if new_tok:
                    token = new_tok
                    env["BLOGGER_OAUTH_TOKEN"] = new_tok
                    continue
            raise
    return None, env


def _load(p, d=None):
    if p.exists():
        try: return json.loads(p.read_text(encoding="utf-8"))
        except: pass
    return d if d is not None else {}


def _save(p, data):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def tg(msg):
    try:
        body = urllib.parse.urlencode({"chat_id": TG_CHAT, "text": msg[:4000]}).encode()
        urllib.request.urlopen(
            urllib.request.Request(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage", data=body),
            timeout=10)
    except: pass


def parse_post(text: str) -> dict:
    """글 파일에서 TITLE/META_DESC/KEYWORDS/BODY 파싱."""
    out = {"title": "", "meta_desc": "", "keywords": "", "body": text}
    m = re.search(r"TITLE:\s*(.+)", text); out["title"] = m.group(1).strip() if m else ""
    m = re.search(r"META_DESC:\s*(.+)", text); out["meta_desc"] = m.group(1).strip() if m else ""
    m = re.search(r"KEYWORDS:\s*(.+)", text); out["keywords"] = m.group(1).strip() if m else ""
    m = re.search(r"---BODY---\n?([\s\S]+)", text)
    if m: out["body"] = m.group(1).strip()
    return out


KLOOK_URL_DEFAULT = ("https://www.klook.com/en-US/activity/170600-jeju-grandebleu-sunset-yacht-experience/"
                     "?utm_source=blogger&utm_medium=cta&utm_campaign=jeju_yacht")

BOOKING_CHANNEL_NOTE = (
    "Reservations are available through major partner platforms such as Naver, "
    "Klook, KKday, and Ctrip/Trip.com. Availability, pricing, language support, "
    "and change/refund rules can differ by channel, so please check the platform "
    "you use before payment."
)

BOOKING_CHANNEL_NOTE_JA = (
    "Grande Bleu の予約は、Klook だけでなく、Naver、KKday、Ctrip/Trip.com など"
    "複数の提携プラットフォームから可能です。空席、料金、言語サポート、変更・返金条件は"
    "予約チャネルごとに異なる場合があるため、決済前に各ページでご確認ください。"
)

BOOKING_CHANNEL_NOTE_ZH = (
    "Grande Bleu 可通过多个合作平台预订，包括 Naver、Klook、KKday、携程/Ctrip/Trip.com 等。"
    "不同平台的余位、价格、语言支持及改退规则可能不同，请以付款前页面显示为准。"
)


def _booking_note(lang: str) -> str:
    return {"ja": BOOKING_CHANNEL_NOTE_JA,
            "zh-CN": BOOKING_CHANNEL_NOTE_ZH}.get(lang, BOOKING_CHANNEL_NOTE)


# "Klook 단독/독점" 오표기 탐지 패턴 — 언어별로 분리. 글의 언어와 같은 그룹만
# 적용해 교차언어 혼입(예: 중국어 글에 일본어 안내문 주입)을 원천 차단한다.
_EXCLUSIVE_PATTERNS = {
    "en": [
        r"(?im)^.*Klook[^.\n]*(?:only|exclusively|exclusive|one channel|one price|no phone-tag)[^.\n]*\.?$",
        r"(?im)^.*Booking is handled exclusively through Klook[^.\n]*\.?$",
        r"(?im)^.*Grande Bleu takes bookings through \*\*Klook only\*\*[^.\n]*\.?$",
        r"(?im)^.*Klook is the only booking channel[^.\n]*\.?$",
        r"(?im)^.*only official booking channel[^.\n]*Klook[^.\n]*\.?$",
    ],
    "ja": [
        r"(?m)^.*Klook[^。\n]*(?:一本化|のみ|だけ)[^。\n]*。?$",
        r"(?m)^.*自社予約ページ[^。\n]*(?:運営していません|ありません)[^。\n]*。?$",
    ],
    "zh-CN": [
        r"(?m)^.*(?:只有|唯一|独家)[^。\n]*Klook[^。\n]*。?$",
        r"(?m)^.*Klook[^。\n]*(?:一个渠道|官方预订渠道|独家|唯一)[^。\n]*。?$",
        r"(?m)^.*(?:官网、电话|官网|电话)[^。\n]*不是主要预订入口[^。\n]*。?$",
    ],
}

# 영어 전용: "자체 예약 페이지 없음" 류 → 일반 안내문
_EN_NO_SITE = (
    r"(?im)^.*(?:no separate website|no in-house reservation page|no third-party agent)[^.\n]*\.?$",
    "Please use the booking platform that best matches your country, language, payment method, and change/refund needs.",
)


def sanitize_body(text: str, klook_url: str, lang: str = "en") -> str:
    """grandebleu.co.kr 등 없는 도메인과 단일 예약 채널 오표기를 자동 치환한다.

    Grande Bleu는 Klook 단일 예약이 아니다. Naver, Klook, KKday,
    Ctrip/Trip.com 등 복수 채널 예약 가능성을 유지해야 한다.
    lang으로 글의 언어를 받아 *그 언어의 패턴·안내문만* 적용 → 언어 혼입 차단."""
    if not text: return text
    # 마크다운 링크 [text](grandebleu.co.kr) → Klook
    text = re.sub(
        r"\[([^\]]+)\]\((?:https?://)?(?:www\.)?grandebleu\.(?:co\.)?kr[^)]*\)",
        f"[\\1]({klook_url})", text, flags=re.IGNORECASE)
    # 평문 URL grandebleu.co.kr → Klook
    text = re.sub(
        r"(?:https?://)?(?:www\.)?grandebleu\.(?:co\.)?kr(?:/[\w\-/]*)?",
        klook_url, text, flags=re.IGNORECASE)
    # 예약 채널 오표기 → 글 언어에 맞는 안내문 (해당 언어 패턴만 적용)
    note = _booking_note(lang)
    for pattern in _EXCLUSIVE_PATTERNS.get(lang, _EXCLUSIVE_PATTERNS["en"]):
        text = re.sub(pattern, note, text)
    if lang == "en":
        text = re.sub(_EN_NO_SITE[0], _EN_NO_SITE[1], text)
    return text


_PHOTO_BASE = "https://raw.githubusercontent.com/jm7397776-byte/grandebleu-photos/main"

# 카테고리별 사진 풀 — 글마다 카테고리 균형 + 같은 인물 중복 금지
PHOTO_POOL_BY_CAT = {
    "scenery": [  # 요트 외관·세일·항해·바다 (sunset 제외 - hero용)
        {"url": f"{_PHOTO_BASE}/yacht_02.png", "alt": "Jeju sailing yacht open deck", "person": None},
        {"url": f"{_PHOTO_BASE}/yacht_03.png", "alt": "Daepo Port catamaran sailing", "person": None},
        {"url": f"{_PHOTO_BASE}/yacht_04.jpg", "alt": "Jeju yacht horizon view", "person": None},
        {"url": f"{_PHOTO_BASE}/yacht_05.jpg", "alt": "Grande Bleu sail closeup", "person": None},
        {"url": f"{_PHOTO_BASE}/yacht_06.jpg", "alt": "Jeju catamaran on calm sea", "person": None},
        {"url": f"{_PHOTO_BASE}/yacht_12_sailing.jpg", "alt": "Grande Bleu sailing Jeju coast", "person": None},
        {"url": f"{_PHOTO_BASE}/yacht_13_aerial.jpg", "alt": "Grande Bleu aerial Daepo Port", "person": None},
    ],
    "sunset": [  # 일몰 톤만 — hero 제외, 본문 중간 배치
        {"url": f"{_PHOTO_BASE}/yacht_01.png", "alt": "Grande Bleu Jeju catamaran sunset", "person": None},
        {"url": f"{_PHOTO_BASE}/yacht_07_sunset.jpg", "alt": "Grande Bleu Jeju sunset moment", "person": None},
    ],
    "interior": [  # 실내·라운지·조타실
        {"url": f"{_PHOTO_BASE}/yacht_10_interior.jpg", "alt": "Grande Bleu 620 indoor lounge", "person": None},
        {"url": f"{_PHOTO_BASE}/yacht_11_cockpit.jpg", "alt": "Grande Bleu cockpit Jeju", "person": None},
        {"url": f"{_PHOTO_BASE}/yacht_14_lounge.jpg", "alt": "Grande Bleu 550 interior lounge", "person": None},
    ],
    "detail": [  # 디테일·뱃머리·럭셔리
        {"url": f"{_PHOTO_BASE}/yacht_08_bow.jpg", "alt": "Grande Bleu bow deck Jeju", "person": None},
        {"url": f"{_PHOTO_BASE}/yacht_09_luxury.png", "alt": "Grande Bleu luxury tour Jeju", "person": None},
    ],
    "people": [  # 인플루언서·인물 (각 인물 1장씩 → 같은 인물 절대 중복 금지)
        {"url": f"{_PHOTO_BASE}/people_lala_mong.jpg", "alt": "Guest on Grande Bleu Jeju yacht", "person": "lala_mong"},
        {"url": f"{_PHOTO_BASE}/people_y_ugyu17.jpg", "alt": "Traveler on Jeju sunset yacht", "person": "y_ugyu17"},
        {"url": f"{_PHOTO_BASE}/people_luvj63.jpg", "alt": "Guest sailing Grande Bleu Jeju", "person": "luvj63"},
        {"url": f"{_PHOTO_BASE}/people_zzang_seo.jpg", "alt": "Traveler enjoying Jeju yacht tour", "person": "zzang_seo"},
        {"url": f"{_PHOTO_BASE}/people_ji___yuuuu.jpg", "alt": "Guest on catamaran deck Jeju", "person": "ji_yuuuu"},
        {"url": f"{_PHOTO_BASE}/people_leelee.jpg", "alt": "Traveler on Daepo Port yacht", "person": "leelee"},
        {"url": f"{_PHOTO_BASE}/people_dmsldmsldms.jpg", "alt": "Guest on Grande Bleu sunset cruise", "person": "dmsldmsldms"},
        {"url": f"{_PHOTO_BASE}/people_ohongss.jpg", "alt": "Traveler on Jeju catamaran tour", "person": "ohongss"},
        {"url": f"{_PHOTO_BASE}/people_m1n_sta.jpg", "alt": "Guest sailing Seogwipo coast", "person": "m1n_sta"},
        {"url": f"{_PHOTO_BASE}/people_0llllll0ii.jpg", "alt": "Traveler on Grande Bleu yacht", "person": "0llllll0ii"},
        {"url": f"{_PHOTO_BASE}/people_iyuune.jpg", "alt": "Guest on Jeju sunset sail", "person": "iyuune"},
        {"url": f"{_PHOTO_BASE}/people_amoufor_u.jpg", "alt": "Traveler on Grande Bleu catamaran", "person": "amoufor_u"},
        {"url": f"{_PHOTO_BASE}/people_j_danbi_o.jpg", "alt": "Guest on Jeju yacht Daepo", "person": "j_danbi_o"},
        {"url": f"{_PHOTO_BASE}/people_eeeeeunae.jpg", "alt": "Traveler on Grande Bleu sunset", "person": "eeeeeunae"},
        {"url": f"{_PHOTO_BASE}/people_gaeule.jpg", "alt": "Guest on Jeju sailing tour", "person": "gaeule"},
        {"url": f"{_PHOTO_BASE}/people_daxxni.jpg", "alt": "Traveler enjoying Grande Bleu", "person": "daxxni"},
        {"url": f"{_PHOTO_BASE}/people_siaxxiii.jpg", "alt": "Guest on catamaran Jeju sunset", "person": "siaxxiii"},
        {"url": f"{_PHOTO_BASE}/people_seoyurim_0818.jpg", "alt": "Traveler on Grande Bleu sail", "person": "seoyurim"},
        {"url": f"{_PHOTO_BASE}/people_o5__25.jpg", "alt": "Guest on Jeju yacht tour", "person": "o5_25"},
        {"url": f"{_PHOTO_BASE}/people_174___24.jpg", "alt": "Traveler sailing Grande Bleu", "person": "p174_24"},
        {"url": f"{_PHOTO_BASE}/people_park_bin.jpg", "alt": "Guest on Jeju sunset yacht", "person": "park_bin"},
        {"url": f"{_PHOTO_BASE}/people_basic_jyan.jpg", "alt": "Traveler on Grande Bleu Jeju", "person": "basic_jyan"},
        {"url": f"{_PHOTO_BASE}/people_hwa.jpg", "alt": "Guest enjoying Jeju catamaran", "person": "hwa_min"},
        {"url": f"{_PHOTO_BASE}/people_cher.jpg", "alt": "Traveler on Grande Bleu sunset cruise", "person": "cher_ixi"},
        {"url": f"{_PHOTO_BASE}/people_es.jpg", "alt": "Guest on Jeju yacht sail", "person": "es_blogger"},
        {"url": f"{_PHOTO_BASE}/people_s.jpg", "alt": "Traveler on Grande Bleu yacht", "person": "s_blogger"},
        {"url": f"{_PHOTO_BASE}/people_j.jpg", "alt": "Guest on Jeju sunset catamaran", "person": "j_blogger"},
    ],
}

# 글당 7장 = scenery 3 + people 3 + (interior 1 or detail 1)
SLOT_RECIPE = [
    ("scenery", 3),
    ("people", 3),
    ("interior_or_detail", 1),
]


def _seeded_rng(seed_key: str):
    """seed_key로 deterministic random.Random 반환."""
    import hashlib, random
    h = int(hashlib.md5(seed_key.encode()).hexdigest(), 16)
    return random.Random(h)


def _without_avoided(pool: list, avoid_urls: set[str]) -> list:
    filtered = [p for p in pool if p.get("url") not in avoid_urls]
    return filtered or list(pool)


def _pick_photos(seed_key: str, n: int = 7, avoid_urls: set[str] | None = None) -> list:
    """글마다 카테고리 균형·인물 중복 없는 7장 선택.

    규칙:
    - hero 슬롯(첫 번째): scenery_daytime/detail/people 중 랜덤 (sunset 절대 제외)
    - 본문: scenery 2 + sunset 1 + people 3 + interior/detail 1 = 6장 (+ hero = 7장)
    - people 카테고리에서 같은 인물 절대 중복 X
    - seed로 결정적 셔플 (같은 글·언어는 같은 사진)
    - 매 글마다 hero가 sunset이 아니라 다양 (선셋 편중 방지)
    """
    rng = _seeded_rng(seed_key)
    avoid_urls = set(avoid_urls or [])
    picked = []
    used_persons = set()
    # 1) Hero 사진 (첫 번째 슬롯) — sunset 제외하고 다양화
    hero_candidates = (
        PHOTO_POOL_BY_CAT["scenery"]  # daytime/sailing 풀
        + PHOTO_POOL_BY_CAT["detail"]
        + PHOTO_POOL_BY_CAT["interior"]
    )
    hero_candidates = _without_avoided(list(hero_candidates), avoid_urls)
    rng.shuffle(hero_candidates)
    hero = hero_candidates[0]
    picked.append(hero)

    # 2) 본문 슬롯 — 카테고리별 분배 (sunset 1장 포함하여 다양화)
    body_recipe = [
        ("scenery", 2),
        ("sunset", 1),
        ("people", 3),
        ("interior_or_detail", 1),  # interior 또는 detail 1장
    ]

    for cat, count in body_recipe:
        if cat == "interior_or_detail":
            sub = "interior" if rng.random() < 0.5 else "detail"
            pool = _without_avoided(list(PHOTO_POOL_BY_CAT[sub]), avoid_urls)
        else:
            pool = _without_avoided(list(PHOTO_POOL_BY_CAT.get(cat, [])), avoid_urls)
        rng.shuffle(pool)
        added = 0
        for ph in pool:
            if added >= count: break
            if ph in picked: continue  # hero와 중복 제거
            if ph.get("person") and ph["person"] in used_persons:
                continue
            picked.append(ph)
            if ph.get("person"):
                used_persons.add(ph["person"])
            added += 1

    # 3) 부족분 scenery에서 보충
    if len(picked) < n:
        extra = [p for p in PHOTO_POOL_BY_CAT["scenery"]
                  + PHOTO_POOL_BY_CAT.get("sunset", [])
                  if p not in picked and p.get("url") not in avoid_urls]
        if not extra:
            extra = [p for p in PHOTO_POOL_BY_CAT["scenery"]
                      + PHOTO_POOL_BY_CAT.get("sunset", [])
                      if p not in picked]
        rng.shuffle(extra)
        picked.extend(extra[:n - len(picked)])

    # 4) hero는 첫 번째 유지, 나머지만 본문 순서 셔플 (시각적 흐름)
    body = picked[1:]
    rng.shuffle(body)
    return [picked[0]] + body[:n-1]


# 호환성을 위한 flat 풀 (필요 시 참조)
PHOTO_POOL = [p for cat in PHOTO_POOL_BY_CAT.values() for p in cat]


def md_to_html(md: str, photo_seed: str = "", avoid_photo_urls: set[str] | None = None) -> str:
    """Markdown → Blogger 친화 HTML + H2 사이마다 사진 자동 삽입.

    Blogger는 markdown 렌더링 안 함 → 직접 HTML 변환 필수.
    네이버 블로그 톤 참고: 짧은 문단·여백·강조 절제·문단 사이 사진."""
    if not md: return ""
    explicit_urls = set(re.findall(r"!\[[^\]]*\]\(([^)]+)\)", md))
    explicit_urls.update(re.findall(r'<img[^>]+src=["\']([^"\']+)["\']', md, flags=re.I))
    avoid_urls = set(avoid_photo_urls or set()) | explicit_urls
    # 글마다 다른 사진 7장 선택 (hero 1 + H2 사이 6 = 중복 없음)
    photos = _pick_photos(photo_seed or md[:200], 7, avoid_urls=avoid_urls)
    hero_photo = photos[0] if photos else None
    photo_iter = iter(photos[1:])  # hero 제외한 나머지

    def next_photo_html():
        """H2 사이마다 끼울 사진 HTML."""
        try: ph = next(photo_iter)
        except StopIteration: return ""
        return (
            f'<figure style="margin:2.75rem auto;max-width:720px;">'
            f'<img src="{ph["url"]}" alt="{ph["alt"]}" loading="lazy" '
            f'style="width:100%;height:auto;border-radius:14px;'
            f'box-shadow:0 16px 48px rgba(13,38,69,0.14);display:block;" />'
            f'</figure>'
        )

    html_lines = []
    in_list = False
    in_code = False
    paragraph = []
    h2_count = 0  # H2 등장 카운트 (사진 위치)

    def flush_paragraph():
        nonlocal paragraph
        if paragraph:
            text = " ".join(paragraph).strip()
            if text:
                # inline 강조
                text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
                text = re.sub(r"\*(.+?)\*", r"<em>\1</em>", text)
                # 링크 [text](url)
                text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)",
                              r'<a href="\2" style="color:#0d2645;border-bottom:1px solid #d4a437;">\1</a>',
                              text)
                html_lines.append(
                    f'<p style="line-height:1.85;margin:0 0 1.25em;font-size:1.0625rem;color:#1a1a1a;">{text}</p>'
                )
            paragraph = []

    for raw in md.split("\n"):
        line = raw.rstrip()
        # 코드블록 통과
        if line.startswith("```"):
            flush_paragraph()
            in_code = not in_code
            continue
        if in_code:
            html_lines.append(f"<pre>{line}</pre>")
            continue
        # 이미지 (단독 라인) — 이미 HTML img면 그대로
        if line.startswith("<img"):
            flush_paragraph()
            html_lines.append(line)
            continue
        m = re.match(r"!\[([^\]]*)\]\(([^)]+)\)", line)
        if m:
            flush_paragraph()
            alt, src = m.group(1), m.group(2)
            html_lines.append(
                f'<figure style="margin:2.5em -1em;"><img src="{src}" alt="{alt}" '
                f'style="width:100%;border-radius:12px;box-shadow:0 12px 40px rgba(13,38,69,0.12);" /></figure>'
            )
            continue
        # 헤딩
        if line.startswith("# "):
            flush_paragraph()
            html_lines.append(
                f'<h1 style="font-family:Georgia,serif;font-size:2.25rem;color:#0d2645;'
                f'margin:0 0 1rem;line-height:1.2;font-weight:600;">{line[2:].strip()}</h1>'
            )
            continue
        if line.startswith("## "):
            flush_paragraph()
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            # H2 직전에 사진 삽입 (첫 번째 H2는 hero 직후라 스킵, 그 다음부터)
            h2_count += 1
            if h2_count >= 2:  # 2번째 H2부터 위에 사진
                ph = next_photo_html()
                if ph: html_lines.append(ph)
            html_lines.append(
                f'<h2 style="font-family:Georgia,serif;font-size:1.75rem;color:#0d2645;'
                f'margin:3rem 0 1rem;padding-top:1.5rem;border-top:1px solid #e5e7eb;'
                f'line-height:1.3;font-weight:600;">{line[3:].strip()}</h2>'
            )
            continue
        if line.startswith("### "):
            flush_paragraph()
            html_lines.append(
                f'<h3 style="font-family:Georgia,serif;font-size:1.375rem;color:#1a3a5c;'
                f'margin:2rem 0 0.75rem;font-weight:600;">{line[4:].strip()}</h3>'
            )
            continue
        # 리스트
        if re.match(r"^\s*[-*]\s+", line):
            flush_paragraph()
            if not in_list:
                html_lines.append('<ul style="padding-left:1.5em;margin:1em 0 1.5em;line-height:1.85;">')
                in_list = True
            item = re.sub(r"^\s*[-*]\s+", "", line)
            item = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", item)
            item = re.sub(r"\[([^\]]+)\]\(([^)]+)\)",
                          r'<a href="\2" style="color:#0d2645;border-bottom:1px solid #d4a437;">\1</a>',
                          item)
            html_lines.append(
                f'<li style="margin-bottom:0.5em;font-size:1.0625rem;">{item}</li>'
            )
            continue
        if in_list and line.strip() == "":
            html_lines.append("</ul>")
            in_list = False
            continue
        # blockquote
        if line.startswith("> "):
            flush_paragraph()
            html_lines.append(
                f'<blockquote style="border-left:3px solid #d4a437;background:#faf7f2;'
                f'padding:1rem 1.5rem;margin:1.5em 0;font-style:italic;color:#1a3a5c;'
                f'font-size:1.125rem;border-radius:0 8px 8px 0;">{line[2:].strip()}</blockquote>'
            )
            continue
        # 빈 줄 → 문단 종료
        if line.strip() == "":
            flush_paragraph()
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            continue
        # 일반 문장 → 문단 누적
        paragraph.append(line.strip())

    flush_paragraph()
    if in_list: html_lines.append("</ul>")

    # 본문 시작에 hero 사진 1장 (제목/H1 직후)
    body_inner = "\n".join(html_lines)
    hero_html = ""
    if hero_photo:
        hero_html = (
            f'<figure style="margin:1rem auto 2.5rem;max-width:720px;">'
            f'<img src="{hero_photo["url"]}" alt="{hero_photo["alt"]}" '
            f'style="width:100%;height:auto;border-radius:16px;'
            f'box-shadow:0 20px 60px rgba(13,38,69,0.18);display:block;" />'
            f'</figure>'
        )
    # H1 다음에 hero 끼우기
    if "</h1>" in body_inner and hero_html:
        parts = body_inner.split("</h1>", 1)
        body_inner = parts[0] + "</h1>\n" + hero_html + parts[1]
    elif hero_html:
        body_inner = hero_html + body_inner

    # Blogger 친화 wrapper (모바일 가독성 80% 트래픽)
    return (
        f'<div style="max-width:720px;margin:0 auto;font-family:-apple-system,BlinkMacSystemFont,'
        f'\'Noto Sans KR\',sans-serif;color:#1a1a1a;">'
        f'{body_inner}'
        f'</div>'
    )


# ─── 1. Blogger 자동 발행 (다국어 통합) ────────────
LANG_META = {
    "en": {"label": "English", "cta_book": "Book Your Sail",
            "cta_about": "About Grande Bleu", "related": "Related",
            "klook_headline": "Sail Before Sunset Tonight",
            "klook_sub": "Partner booking platforms · Free onboard food & drinks",
            "extra_keywords": "Jeju catamaran, sunset cruise, Daepo Port, Seogwipo, "
                               "Jeju honeymoon, Jeju family activities, Korea sailing"},
    "zh-CN": {"label": "中文简体", "cta_book": "立即预订",
              "cta_about": "关于Grande Bleu", "related": "相关",
              "klook_headline": "今日落日前出航",
              "klook_sub": "合作平台预订·船上餐饮免费",
              "extra_keywords": "济州岛游艇, 济州航海, Grande Bleu, 大浦港, "
                                 "西归浦, 韩国济州岛, 蜜月旅行, 韩剧打卡, 拍照圣地"},
    "ja": {"label": "日本語", "cta_book": "今すぐ予約",
            "cta_about": "Grande Bleuについて", "related": "関連",
            "klook_headline": "今日のサンセット前に出航",
            "klook_sub": "提携予約プラットフォーム・船上の食事ドリンク無料",
            "extra_keywords": "済州島 ヨット, 済州 セーリング, グランブルー, 大浦港, "
                               "西帰浦, 韓国旅行, ハネムーン, 家族旅行, 韓ドラ, "
                               "サンセットクルーズ, カタマラン, 双胴船"},
}


def publish_blogger(env: dict, post: dict, lang: str = "en") -> dict:
    """Blogger API v3 — 다국어 라벨로 분류·hreflang 메타·schema JSON-LD 주입."""
    token = env.get("BLOGGER_OAUTH_TOKEN")
    blog_id = env.get("BLOGGER_BLOG_ID")
    if not (token and blog_id):
        return {"channel": f"blogger-{lang}", "ok": False,
                "reason": "BLOGGER_OAUTH_TOKEN 또는 BLOGGER_BLOG_ID 없음"}
    meta = LANG_META.get(lang, LANG_META["en"])

    # 0) 본문 위생 처리 — grandebleu.co.kr 자동 치환 (도메인 없음)
    klook_url = env.get("GRANDEBLEU_KLOOK_URL", KLOOK_URL_DEFAULT)
    post = dict(post)
    post["body"] = sanitize_body(post["body"], klook_url, lang)
    post["meta_desc"] = sanitize_body(post["meta_desc"], klook_url, lang)

    # 1) Markdown → HTML 변환 + 문단 사이 사진 자동 삽입 (글마다 다른 6장)
    body_html = md_to_html(post["body"], photo_seed=f"{post['title']}_{lang}")

    # 2) Klook CTA 골드 배너 (글 중간·끝)
    klook_url = env.get("GRANDEBLEU_KLOOK_URL",
                        "https://www.klook.com/en-US/activity/170600-jeju-grandebleu-sunset-yacht-experience/?utm_source=blogger&utm_medium=cta&utm_campaign=jeju_yacht")
    klook_banner = (
        f'<a href="{klook_url}" target="_blank" rel="noopener" '
        f'style="display:block;background:linear-gradient(135deg,#d4a437 0%,#b8862a 100%);'
        f'color:#fff;padding:1.75rem 2rem;border-radius:14px;text-decoration:none;'
        f'margin:2.5rem auto;max-width:720px;text-align:center;'
        f'box-shadow:0 12px 32px rgba(212,164,55,0.3);">'
        f'<div style="font-family:Georgia,serif;font-size:1.5rem;font-weight:600;margin-bottom:0.5rem;">'
        f'{meta["klook_headline"]} →</div>'
        f'<div style="font-size:0.9375rem;opacity:0.92;letter-spacing:0.04em;">{meta["klook_sub"]}</div>'
        f'</a>'
    )

    # 3) schema.org JSON-LD — GEO·AEO·SEO 최적화 (TouristAttraction + LocalBusiness + FAQPage)
    schema_jsonld = (
        '<script type="application/ld+json">'
        + json.dumps({
            "@context": "https://schema.org",
            "@graph": [
                {
                    "@type": ["TouristAttraction", "LocalBusiness"],
                    "name": "Grande Bleu Jeju Yacht",
                    "alternateName": ["그랑블루요트", "济州游艇", "済州ヨット"],
                    "description": post["meta_desc"][:300],
                    "url": "https://jejugrandebleuyacht.blogspot.com/",
                    "image": "https://raw.githubusercontent.com/jm7397776-byte/grandebleu-photos/main/yacht_01.png",
                    "address": {"@type": "PostalAddress",
                                 "addressLocality": "Seogwipo", "addressRegion": "Jeju",
                                 "addressCountry": "KR", "streetAddress": "Daepo Port 172-7"},
                    "geo": {"@type": "GeoCoordinates",
                             "latitude": "33.2287", "longitude": "126.4318"},
                    "telephone": "+82-64-739-7776",
                    "priceRange": "₩40,000-₩70,000",
                    "inLanguage": lang,
                    "foundingDate": "2011",
                    "openingHours": "Mo-Su 09:00-19:00",
                    "paymentAccepted": ["Naver", "Klook", "KKday", "Ctrip/Trip.com", "Phone booking"],
                    "aggregateRating": {"@type": "AggregateRating",
                                         "ratingValue": "4.8", "reviewCount": "500"},
                    "amenityFeature": [
                        {"@type": "LocationFeatureSpecification", "name": "Free unlimited food", "value": True},
                        {"@type": "LocationFeatureSpecification", "name": "Free unlimited drinks (draft beer·wine·juice·water)", "value": True},
                        {"@type": "LocationFeatureSpecification", "name": "Licensed captain onboard", "value": True},
                        {"@type": "LocationFeatureSpecification", "name": "Fishing experience included", "value": True},
                        {"@type": "LocationFeatureSpecification", "name": "Twin-hull catamaran (stable)", "value": True},
                    ],
                },
                {
                    "@type": "FAQPage",
                    "mainEntity": [
                        {"@type": "Question", "name": "How long is the Jeju sunset cruise?",
                         "acceptedAnswer": {"@type": "Answer",
                            "text": "The Grande Bleu Jeju sunset cruise is 60 minutes, timed exactly to the daily sunset hour. Departure varies by season."}},
                        {"@type": "Question", "name": "Is food included on the Jeju yacht?",
                         "acceptedAnswer": {"@type": "Answer",
                            "text": "Yes — all food and drinks are FREE and unlimited onboard. This includes draft beer, wine, Jeju tangerine juice, water, Jeju local snacks, and Korean ramyeon."}},
                        {"@type": "Question", "name": "Where does the Jeju yacht depart from?",
                         "acceptedAnswer": {"@type": "Answer",
                            "text": "All cruises depart from Daepo Port in Seogwipo, southern Jeju Island. The Grande Bleu Yacht building 2F concierge handles check-in 20 minutes before sailing."}},
                        {"@type": "Question", "name": "Is the Jeju catamaran safe for kids and seniors?",
                         "acceptedAnswer": {"@type": "Answer",
                            "text": "Yes. Grande Bleu is Korea's only brand-certified catamaran with twin-hull design, making it significantly more stable than single-hull yachts. Licensed captain and engineer are onboard every cruise."}},
                        {"@type": "Question", "name": "How much does the Jeju yacht tour cost?",
                         "acceptedAnswer": {"@type": "Answer",
                            "text": "Adult tickets range from ₩60,000 to ₩70,000 depending on tour type. Children are ₩40,000. MICE group discounts available."}},
                        {"@type": "Question", "name": "What's the best time for Jeju sunset cruise?",
                         "acceptedAnswer": {"@type": "Answer",
                            "text": "The cruise launches exactly at golden hour, which varies by season — earlier in winter (around 17:00) and later in summer (around 19:30). Check the latest sailing schedule when booking."}},
                    ],
                },
            ],
        }, ensure_ascii=False) + '</script>'
    )

    # 4) 내부 링크 박스
    related_box = (
        f'<div style="background:#faf7f2;padding:1.75rem 2rem;border-radius:14px;'
        f'margin:2.5rem auto 0;max-width:720px;border-left:4px solid #d4a437;">'
        f'<p style="font-size:0.75rem;color:#6b7280;margin:0 0 0.875rem;'
        f'letter-spacing:0.22em;text-transform:uppercase;font-weight:600;">{meta["related"]}</p>'
        f'<ul style="list-style:none;padding:0;margin:0;line-height:2;">'
        f'<li><a href="/p/about-grande-bleu-jeju-yacht.html" '
        f'style="color:#0d2645;text-decoration:none;font-weight:500;">→ {meta["cta_about"]}</a></li>'
        f'<li><a href="/p/book-grande-bleu-jeju-yacht.html" '
        f'style="color:#0d2645;text-decoration:none;font-weight:500;">→ {meta["cta_book"]}</a></li>'
        f'</ul></div>'
    )

    # 5) hreflang 메타 — 다국어 alternate (2026-05-26 정정: grandebleu.co.kr 도메인 없음)
    _BLOGGER = "https://jejugrandebleuyacht.blogspot.com/"
    _CN_MIRROR = "https://grandebleu-jeju-cn.pages.dev/"
    _EN_MIRROR = "https://jm7397776-byte.github.io/grandebleu-jeju-en/"  # 신규 영문 미러
    _KLOOK = "https://www.klook.com/en-US/activity/170600-jeju-grandebleu-sunset-yacht-experience/"
    hreflang_meta = (
        f'<meta name="language" content="{lang}" />'
        f'<meta http-equiv="content-language" content="{lang}" />'
        f'<link rel="alternate" hreflang="{lang}" href="{_BLOGGER}" />'
        f'<link rel="alternate" hreflang="en" href="{_EN_MIRROR}" />'
        f'<link rel="alternate" hreflang="zh-CN" href="{_CN_MIRROR}" />'
        f'<link rel="alternate" hreflang="zh-TW" href="{_CN_MIRROR}" />'
        f'<link rel="alternate" hreflang="ja" href="{_BLOGGER}" />'
        f'<link rel="alternate" hreflang="ko-KR" href="{_KLOOK}" />'  # 한국어는 Klook 사용 (자체 도메인 없음)
        f'<link rel="alternate" hreflang="x-default" href="{_BLOGGER}" />'
        f'<meta name="DC.language" content="{lang}" scheme="ISO639-1" />'
    )

    # 본문 중간에 Klook 배너 1번, 끝에 1번 (2개 노출)
    # H2 첫번째 등장 직후 끼우기
    mid_split = body_html.split("<h2", 2)
    if len(mid_split) >= 3:
        body_with_banner = mid_split[0] + "<h2" + mid_split[1] + klook_banner + "<h2" + mid_split[2]
    else:
        body_with_banner = body_html

    final_content = (
        hreflang_meta
        + body_with_banner
        + klook_banner
        + related_box
        + schema_jsonld
    )

    # 라벨: 키워드 + 언어 태그 + 추가 시장 키워드
    labels_full = [k.strip() for k in post["keywords"].split(",") if k.strip()][:8]
    extra_kws = meta.get("extra_keywords", "")
    for kw in extra_kws.split(","):
        kw = kw.strip()
        if kw and kw not in labels_full and len(labels_full) < 18:  # Blogger 라벨 max 20
            labels_full.append(kw)
    labels_full.append(f"lang:{lang}")

    title_full = post["title"][:180]
    # ?isDraft=false → 즉시 공개 발행 (기본값 DRAFT라 공개 안 되는 버그 해결)
    url = f"https://www.googleapis.com/blogger/v3/blogs/{blog_id}/posts/?isDraft=false"

    # 가벼운 fallback payload — JA/ZH-CN에서 Blogger API HTTP 400 회피용
    # (2026-05-26 진단: schema.org JSON-LD + 한자 라벨 18개 조합이 다국어에서 400 유발)
    klook_text_cta = (
        f'<p style="text-align:center;margin:2rem 0;">'
        f'<a href="{klook_url}" target="_blank" rel="noopener" '
        f'style="display:inline-block;background:#d4a437;color:#fff;'
        f'padding:1rem 2rem;border-radius:8px;text-decoration:none;font-weight:600;">'
        f'{meta["klook_headline"]} →</a></p>'
    )
    minimal_content = body_html + klook_text_cta
    minimal_labels = [k.strip() for k in post["keywords"].split(",") if k.strip()][:6]
    minimal_labels.append(f"lang:{lang}")

    def _attempt(content_str: str, label_list: list) -> tuple:
        body_bytes = json.dumps({
            "kind": "blogger#post",
            "title": title_full,
            "content": content_str,
            "labels": label_list,
        }).encode()
        try:
            d, _ = blogger_call_with_refresh(env, url, data=body_bytes, method="POST")
            return (True, d, None)
        except Exception as e:
            return (False, None, str(e)[:200])

    # 1차: 풀 payload (schema·banner·label 18개) — EN은 늘 통과
    ok_full, d_full, err_full = _attempt(final_content, labels_full)
    if ok_full:
        # 2026-05-26 fix: 응답 published 날짜가 오늘인지 검증 (중복 제목 → 기존 글 ID 반환 회피)
        _today = datetime.now().strftime("%Y-%m-%d")
        _pub = (d_full.get("published") or "")[:10]
        if _pub and _pub != _today:
            err_full = f"중복 제목 — Blogger가 기존 {_pub} 글 반환 (id={d_full.get('id')})"
            _log(f"  [{lang}] {err_full} — 미니멀 재시도")
        else:
            return {"channel": f"blogger-{lang}", "ok": True, "url": d_full.get("url", ""),
                    "post_id": d_full.get("id", ""), "mode": "full"}

    # 2차: 미니멀 payload (text CTA·label 7개) — JA/ZH-CN HTTP 400 fallback
    _log(f"  [{lang}] 풀 payload 실패: {err_full[:100]} — 미니멀 재시도")
    ok_min, d_min, err_min = _attempt(minimal_content, minimal_labels)
    if ok_min:
        _today = datetime.now().strftime("%Y-%m-%d")
        _pub = (d_min.get("published") or "")[:10]
        if _pub and _pub != _today:
            err_min = f"중복 제목 (minimal) — Blogger가 기존 {_pub} 글 반환 (id={d_min.get('id')})"
        else:
            return {"channel": f"blogger-{lang}", "ok": True, "url": d_min.get("url", ""),
                    "post_id": d_min.get("id", ""), "mode": "minimal_fallback"}

    return {"channel": f"blogger-{lang}", "ok": False,
            "reason": f"full: {err_full[:100]} / minimal: {err_min[:100]}"}


# ─── 2. Medium 자동 발행 ──────────────────────────
def publish_medium(env: dict, post: dict) -> dict:
    """Medium API — 필요: MEDIUM_TOKEN."""
    token = env.get("MEDIUM_TOKEN")
    if not token:
        return {"channel": "medium", "ok": False, "reason": "MEDIUM_TOKEN 없음"}
    # 사용자 ID
    req = urllib.request.Request("https://api.medium.com/v1/me",
                                  headers={"Authorization": f"Bearer {token}",
                                           "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            user_id = json.load(r)["data"]["id"]
    except Exception as e:
        return {"channel": "medium", "ok": False, "reason": f"user 조회 실패: {e}"}
    body = json.dumps({
        "title": post["title"][:100],
        "contentFormat": "markdown",
        "content": f"# {post['title']}\n\n{post['body']}",
        "tags": [k.strip() for k in post["keywords"].split(",") if k.strip()][:5],
        "publishStatus": "public",
    }).encode()
    req = urllib.request.Request(
        f"https://api.medium.com/v1/users/{user_id}/posts", data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {token}",
                 "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.load(r)["data"]
        return {"channel": "medium", "ok": True,
                "url": d.get("url", ""), "post_id": d.get("id", "")}
    except Exception as e:
        return {"channel": "medium", "ok": False, "reason": str(e)[:200]}


# ─── 3. WordPress.com 자동 발행 ───────────────────
def publish_wordpress(env: dict, post: dict) -> dict:
    """WordPress.com REST API v2 — 필요: WP_SITE, WP_USER, WP_APP_PASSWORD."""
    site = env.get("WP_SITE")  # gbjeju.wordpress.com
    user = env.get("WP_USER")
    pwd = env.get("WP_APP_PASSWORD")
    if not all([site, user, pwd]):
        return {"channel": "wordpress", "ok": False, "reason": "WP_SITE/WP_USER/WP_APP_PASSWORD 없음"}
    auth = base64.b64encode(f"{user}:{pwd}".encode()).decode()
    body = json.dumps({
        "title": post["title"][:200],
        "content": post["body"],
        "status": "publish",
        "excerpt": post["meta_desc"],
    }).encode()
    url = f"https://public-api.wordpress.com/wp/v2/sites/{site}/posts"
    req = urllib.request.Request(url, data=body,
                                  headers={"Content-Type": "application/json",
                                           "Authorization": f"Basic {auth}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.load(r)
        return {"channel": "wordpress", "ok": True,
                "url": d.get("link", ""), "post_id": d.get("id", "")}
    except Exception as e:
        return {"channel": "wordpress", "ok": False, "reason": str(e)[:200]}


# ─── 4. GitHub Pages 자동 발행 (gh CLI) ───────────
def publish_github_pages(post: dict, slug: str) -> dict:
    """vault 영문 글 → Jekyll _posts/ → git push."""
    import subprocess, re, os, tempfile
    repo_dir = Path("/tmp/grandebleu-jeju-en")
    if not repo_dir.exists():
        # 처음이면 clone
        r = subprocess.run(["gh", "repo", "clone",
                             "jm7397776-byte/grandebleu-jeju-en", str(repo_dir)],
                            capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            return {"channel": "github_pages", "ok": False, "reason": "clone 실패"}
    # 최신 pull
    subprocess.run(["git", "pull", "-q"], cwd=str(repo_dir), capture_output=True, timeout=30)
    # 새 포스트 작성
    from datetime import datetime as _dt
    date = _dt.now().strftime("%Y-%m-%d")
    safe_slug = re.sub(r"[^a-z0-9가-힣]+", "-", slug.lower())[:60].strip("-")
    post_path = repo_dir / "_posts" / f"{date}-{safe_slug}.md"
    if post_path.exists():
        return {"channel": "github_pages", "ok": False, "reason": "이미 게시됨"}
    kws = [k.strip() for k in post["keywords"].split(",") if k.strip()][:6]
    content = (
        f"---\nlayout: post\ntitle: \"{post['title'][:160]}\"\n"
        f"date: {date}\ndescription: \"{post['meta_desc']}\"\n"
        f"tags: [{', '.join(kws)}]\n---\n\n{post['body']}\n"
    )
    post_path.write_text(content, encoding="utf-8")
    # commit + push
    env = os.environ.copy()
    subprocess.run(["git", "add", "-A"], cwd=str(repo_dir), capture_output=True, timeout=15)
    cr = subprocess.run(
        ["git", "-c", "user.email=jm7397776@gmail.com", "-c", "user.name=bo2cha",
          "commit", "-q", "-m", f"Auto: {post['title'][:60]}"],
        cwd=str(repo_dir), capture_output=True, text=True, timeout=15)
    pr = subprocess.run(["git", "push", "-q"],
                        cwd=str(repo_dir), capture_output=True, text=True, timeout=60)
    if pr.returncode == 0:
        url = f"https://jm7397776-byte.github.io/grandebleu-jeju-en/{safe_slug}/"
        return {"channel": "github_pages", "ok": True, "url": url,
                "post_id": post_path.name}
    return {"channel": "github_pages", "ok": False,
            "reason": (cr.stderr or pr.stderr or "")[-200:]}


# ─── 메인 — 새 글 발견 → 채널 모두에 게시 ─────────
def main():
    env = _load_env()
    _fresh = refresh_blogger_token(env)
    if _fresh:
        env["BLOGGER_OAUTH_TOKEN"] = _fresh
    state = _load(STATE, {"published": {}})
    n_new = 0
    results = []

    # 사용 가능한 채널 확인
    available = []  # github_pages 제거됨 (2026-05-20 — Blogger로 마이그레이션)
    if env.get("BLOGGER_OAUTH_TOKEN") and env.get("BLOGGER_BLOG_ID"):
        available.append("blogger")
    if env.get("MEDIUM_TOKEN"):
        available.append("medium")
    if env.get("WP_SITE") and env.get("WP_USER") and env.get("WP_APP_PASSWORD"):
        available.append("wordpress")

    _log(f"가용 채널: {available}")
    # 다국어 통합 발행 — en + zh-CN + ja 동시 (Blogger 라벨로 분류)
    LANGS = [["en", "ja", "zh-CN"][datetime.now().weekday() % 3]]  # 하루 1언어만 (월=en 화=ja 수=zh 회전)
    for lang in LANGS:
        lang_dir = GLOBAL_CONTENT / lang
        if not lang_dir.exists():
            _log(f"global_content/{lang} 폴더 없음 — skip")
            continue
        # 최근 7편 중 미발행 → 한 사이클당 언어별 1편씩 (콘텐츠 다양화·중복 방지)
        candidates = sorted(lang_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)[:7]
        for f in candidates:
            key = f"{f.stem}__{lang}"  # 언어별 키
            if key in state.get("published", {}):
                continue
            try:
                text = f.read_text(encoding="utf-8")
                post = parse_post(text)
                if not post["title"] or len(post["body"]) < 500:
                    _log(f"  스킵 ({f.name}): title 또는 본문 부족")
                    continue
                r_log = {}
                if "blogger" in available:
                    r = publish_blogger(env, post, lang=lang)
                    r_log[f"blogger-{lang}"] = r
                    if r.get("ok"): results.append(r)
                # Medium·WP는 영문만 (다국어 채널 미지원·SEO 분산 방지)
                if lang == "en":
                    if "medium" in available:
                        r = publish_medium(env, post)
                        r_log["medium"] = r
                        if r.get("ok"): results.append(r)
                    if "wordpress" in available:
                        r = publish_wordpress(env, post)
                        r_log["wordpress"] = r
                        if r.get("ok"): results.append(r)
                _ok_any = any(rr.get("ok") for rr in r_log.values() if isinstance(rr, dict))
                if not _ok_any:
                    _log(f"  발행 실패 — state 미기록 (재시도 대상): {f.stem} [{lang}]")
                    continue
                state.setdefault("published", {})[key] = {
                    "ts": datetime.now().isoformat(timespec="seconds"),
                    "lang": lang,
                    "results": r_log,
                }
                n_new += 1
                _log(f"발행 — {f.stem} [{lang}]: {[r.get('channel') for r in results if r.get('ok')]}")
                break  # 언어당 1편만 (콘텐츠 다양화)
            except Exception as e:
                _log(f"발행 실패 ({f.name} [{lang}]): {e}")
    _save(STATE, state)
    if n_new > 0 and results:
        ok_urls = "\n".join(f"• {r['channel']}: {r.get('url','')[:80]}" for r in results if r.get("url"))
        tg(f"🚀 글로벌 글 자동 발행 — {n_new}편 (en+zh+ja)\n\n{ok_urls}")


if __name__ == "__main__":
    try: main()
    except Exception as e: _log(f"예외: {e}"); sys.exit(0)
