#!/usr/bin/env python3
"""GBP 주간 4언어 콘텐츠 생성 (클라우드 / GitHub Actions, Gemini 2.5 Pro).

Mac launchd(jarvis_google_posts_generator.py + jarvis_google_posts_to_github.py)를 대체.
repo google_posts/_data/ 데이터(seo_pool·categorized_photos·memory·brand_facts)를 읽어
Gemini 2.5 Pro로 4언어 본문 생성 → {week}_{lang}.txt · current.json · md 출력 + memory 갱신.

요구: 환경변수 GEMINI_API_KEY (GitHub secret).
GitHub Actions=UTC이므로 ISO week·날짜는 KST 기준으로 계산(naive datetime.now 금지).
테스트: GBP_TEST_LANG=ko 면 해당 언어 1편만 생성(호출 1회).
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
REPO_DIR = Path(os.environ.get("REPO_DIR", os.getcwd()))
RAW_BASE = "https://raw.githubusercontent.com/jm7397776-byte/grandebleu-photos/main"
DATA = REPO_DIR / "google_posts" / "_data"
OUT_DIR = REPO_DIR / "google_posts"
SEO_POOL_FILE = DATA / "seo_pool.json"
CAT_FILE = DATA / "categorized_photos.json"
MEMORY_FILE = DATA / "memory.json"
BRAND_FILE = DATA / "brand_facts.json"
RULES_FILE = DATA / "powerblogger_rules.md"  # 파워블로거 학습 룰북 (매주 synthesizer가 갱신)

GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-pro")
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"

KLOOK_URL = (
    "https://www.klook.com/en-US/activity/170600-jeju-grandebleu-sunset-yacht-experience/"
    "?utm_source=google_posts&utm_medium=cta&utm_campaign=jeju_yacht"
)

LANGS = [("ko", "KO", "한국어", "🇰🇷"), ("en", "EN", "English", "🇺🇸"),
         ("zh-CN", "ZH-CN", "中文简体", "🇨🇳"), ("ja", "JA", "日本語", "🇯🇵")]

ANGLE_TO_CATEGORIES = {
    "sunset_timing": ["sunset"],
    "weather_seasonal": ["sunset", "aerial"],
    "food_unlimited": ["food", "interior_lounge"],
    "catamaran_stability": ["deck_exterior", "sunset"],
    "couple_romance": ["sunset", "interior_lounge"],
    "family_friendly": ["deck_exterior", "interior_lounge"],
    "price_value": ["luxury", "sunset"],
    "photo_spot": ["sunset", "aerial"],
    "route_scenery": ["aerial", "deck_exterior", "sunset"],
    "safety_certification": ["cockpit", "deck_exterior"],
    "weekend_urgency": ["sunset", "luxury"],
    "culture_local": ["sunset", "general"],
    "default": ["sunset", "deck_exterior", "general"],
}

LANG_FULL = {"ko": "Korean (한국어)", "en": "English",
             "zh-CN": "Simplified Chinese (中文简体)", "ja": "Japanese (日本語)"}
CTA_TPL = {"ko": "예약하기 →", "en": "Book now →", "zh-CN": "立即预订 →", "ja": "今すぐ予約 →"}
FOCUS_KEY = {"ko": "focus_ko", "en": "focus_en", "zh-CN": "focus_zh", "ja": "focus_ja"}


def now_kst() -> datetime:
    return datetime.now(KST).replace(tzinfo=None)


def load_json(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def gemini(prompt: str, timeout: int = 150) -> str:
    """Gemini 호출. 429/5xx는 지수 백오프 재시도, pro 지속 실패 시 flash 폴백."""
    if not GEMINI_KEY:
        print("[ERROR] GEMINI_API_KEY 환경변수 없음")
        return ""
    import time
    import urllib.error
    # pro 우선(짧게 2회) → 막히면 flash로 폴백(주력, 4회). 새벽엔 pro 한도 여유로 성공 잦음.
    plan = [(GEMINI_MODEL, 2)]
    if "flash" not in GEMINI_MODEL:
        plan.append(("gemini-2.5-flash", 4))
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.9, "topP": 0.95, "maxOutputTokens": 4096,
            "thinkingConfig": {"thinkingBudget": 0},  # 2.5 thinking 끄기 → 본문 토큰 확보(138자 잘림 방지)
        },
    }).encode("utf-8")
    last = ""
    for model, tries in plan:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_KEY}"
        delay = 10
        for attempt in range(tries):
            req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    d = json.load(r)
                cands = d.get("candidates", [])
                if not cands:
                    last = f"{model} no-candidates {str(d)[:120]}"
                    break
                parts = cands[0].get("content", {}).get("parts", [])
                text = "".join(p.get("text", "") for p in parts).strip()
                if text:
                    if model != GEMINI_MODEL:
                        print(f"  (폴백 {model} 사용)")
                    return text
                last = f"{model} empty-text"
                break
            except urllib.error.HTTPError as e:
                last = f"{model} HTTP {e.code}"
                if e.code in (429, 500, 503):
                    print(f"  [retry {attempt+1}/4] {last}, {delay}s 대기")
                    time.sleep(delay)
                    delay = min(delay * 2, 80)
                    continue
                break
            except Exception as e:
                last = f"{model} {type(e).__name__}"
                time.sleep(delay)
                delay = min(delay * 2, 80)
                continue
    print(f"[ERROR] gemini 최종 실패: {last}")
    return ""


def select_angle(pool: dict, week_iso: str) -> dict:
    angles = pool.get("angles", [])
    if not angles:
        return {"id": "default", "focus_ko": "그랑블루 요트 60분 선셋 크루즈",
                "focus_en": "60-min sunset cruise", "focus_zh": "60分钟日落航程",
                "focus_ja": "60分サンセットクルーズ"}
    try:
        week_num = int(week_iso.split("-W")[1])
    except Exception:
        week_num = 0
    return angles[week_num % len(angles)]


def select_weekly_hook(pool: dict, week_iso: str) -> dict:
    rotations = pool.get("weekly_hooks", {}).get("rotations", [])
    if not rotations:
        return {}
    try:
        week_num = int(week_iso.split("-W")[1])
    except Exception:
        week_num = 0
    return rotations[week_num % len(rotations)]


def select_keywords(pool: dict, lang: str, week_iso: str, n: int = 4) -> list:
    keywords = pool.get("seo_keywords", {}).get(lang, [])
    if not keywords:
        return []
    rng = random.Random(week_iso + lang)
    if len(keywords) <= n:
        return list(keywords)
    return rng.sample(keywords, n)


# GBP 사진 다양성: 게시물의 약 N%는 인플루언서/라이프스타일 사진(요트 위 인물)을
# 노을·풍경 대신 노출 → 매번 같은류만 올라가던 문제 해소. 0이면 끔. 자유롭게 조정.
INFLUENCER_PHOTO_PCT = 35


def pick_photo(cats: dict, lang: str, week_iso: str, angle_id: str) -> str:
    # 일정 확률로 인플루언서 사진을 우선 노출(랜덤 변주). 아니면 기존 카테고리 로직.
    influencer = cats.get("influencer") or []
    if influencer and (abs(hash(week_iso + lang + "inf")) % 100) < INFLUENCER_PHOTO_PCT:
        return influencer[abs(hash(week_iso + lang + "infpick")) % len(influencer)]
    categories = ANGLE_TO_CATEGORIES.get(angle_id, ANGLE_TO_CATEGORIES["default"])
    cat_seed = abs(hash(week_iso + lang + "cat")) % len(categories)
    chosen = categories[cat_seed]
    pool = cats.get(chosen) or cats.get("general") or []
    if not pool:
        return ""
    return pool[abs(hash(week_iso + lang + chosen)) % len(pool)]


def get_recent_phrases(mem: dict, lang: str, n: int = 5) -> list:
    recent = [p for p in mem.get("recent_posts", []) if p.get("lang") == lang]
    return [p["first_sentence"] for p in recent[-n:] if p.get("first_sentence")]


def add_to_memory(mem: dict, week_iso: str, lang: str, post_body: str, angle_id: str):
    entry = {
        "week": week_iso, "lang": lang, "angle": angle_id,
        "first_sentence": post_body.split("\n")[0][:200] if post_body else "",
        "hash": hashlib.sha1(post_body.encode("utf-8")).hexdigest()[:12],
        "added": now_kst().isoformat(timespec="seconds"),
    }
    mem.setdefault("recent_posts", []).append(entry)
    if len(mem["recent_posts"]) > 32:
        mem["recent_posts"] = mem["recent_posts"][-32:]


def build_prompt(lang, week_iso, angle, weekly_hook, keywords, avoid, brand) -> str:
    angle_focus = angle.get(FOCUS_KEY[lang], angle.get("focus_en", ""))
    angle_id = angle.get("id", "default")
    hook_text = weekly_hook.get(lang, "") or weekly_hook.get("ko", "")
    avoid_block = ""
    if avoid:
        avoid_block = (
            "\n\nIMPORTANT — AVOID these opening patterns (used recently, DO NOT REPEAT):\n"
            + "\n".join(f"- {p}" for p in avoid[-5:])
            + "\nWrite a COMPLETELY different opening sentence and structure.\n"
        )
    facts = brand.get("brand_voice", {}).get("facts", {})
    banned = brand.get("banned", {})
    facts_json = json.dumps(facts, ensure_ascii=False)
    banned_json = json.dumps(banned, ensure_ascii=False)
    try:
        rules = RULES_FILE.read_text(encoding="utf-8")
    except Exception:
        rules = ""
    pb_block = ""
    if rules:
        pb_block = (
            "\n=== KOREAN POWER-BLOGGER BEST PRACTICES (learned from 1,470 top Naver bloggers; "
            f"rules are in Korean but the principles are language-agnostic — apply them to your {LANG_FULL[lang]} "
            "post, compressed for Google Posts <=1500 chars) ===\n" + rules +
            "\nApply especially: experience-hook opening with a front-loaded keyword; varied sentence length; "
            "conversational tone; keyword woven ~3x (never stuffed); info-style CTA (no 'book now!' ad-speak); "
            "end with emotion, not data.\n"
        )

    return f"""You are a SEO copywriter for Grande Bleu Yacht — Korea's only certified catamaran in Jeju.

TASK: Write ONE Google Business Profile Post in {LANG_FULL[lang]} for ISO week {week_iso}.

=== THIS WEEK'S CONTENT ANGLE ({angle_id}) ===
{angle_focus}

=== THIS WEEK'S HOOK ===
{hook_text}

=== SEO KEYWORDS (weave 3-4 naturally into the post — NO keyword stuffing) ===
{", ".join(keywords)}

=== BRAND FACTS (do not deviate) ===
- Korea's only certified catamaran (since 2011, 100,000+ guests, two vessels: 47-seat + 44-seat)
- 60-minute sunset cruise from Daepo Port, Seogwipo, Jeju
- Free unlimited onboard: draft beer, wine, Jeju tangerine juice, water, Jeju local snacks, Korean ramyeon
- Order: Concierge -> Boarding -> Departure (NEVER reverse)
- Two products: Luxury and Sunset (DON'T mention prices in Google Posts — direct to Klook)
- No facts about engine type, no "no engine" / "no motor" claims
- No afternoon tea, no mackerel/galchi fishing — actual fish: rockfish, scorpionfish, filefish, pufferfish

=== ADDITIONAL VERIFIED FACTS (JSON, single source of truth — use ONLY these numbers/items; translate Korean values into the post language) ===
{facts_json}

=== STRICTLY BANNED (never write anything matching these categories) ===
{banned_json}

=== PLACE NAMES — STRICTLY USE THE LANGUAGE'S OWN SCRIPT ===
ABSOLUTE RULE: Do NOT include Korean Hangul (한글) characters in non-Korean posts. If a place has no native equivalent, use the romanization or the local-language transliteration shown below — never the Hangul.

For English (en):
- 대포항 -> Daepo Port
- 서귀포 -> Seogwipo
- 제주 -> Jeju
- 한라산 -> Hallasan
- Route: Elephant Rock, Jingotnae Sea Canyon, Wolpyeong Basalt Columns, Pangpaengideok, Large Sea Cave

For Simplified Chinese (zh-CN):
- 대포항 -> 大浦港
- 서귀포 -> 西归浦
- 제주 -> 济州岛
- 한라산 -> 汉拿山
- Route: 象岩、津串内海上峡谷、月坪柱状节理、彭辰德、大海蚀洞 (do NOT use Hangul like 진곶내·팡팽이덕)

For Japanese (ja):
- 대포항 -> 大浦港 (Daepo港)
- 서귀포 -> 西帰浦 (ソギポ)
- 제주 -> 済州島 (チェジュド)
- 한라산 -> 漢拏山 (ハルラサン)
- Route: 象岩、チンゴンネ海峡、ウォルピョン柱状節理、パンペンイドク、大型海食洞窟 (do NOT use Hangul like 서귀포·진곶내·팡팽이덕)

If you find yourself about to write any Hangul character in a non-Korean post, STOP and use the table above.

=== GOOGLE POSTS SEO REQUIREMENTS ===
1. FIRST SENTENCE: catchy hook with weekly angle. Front-load the most important SEO keyword.
2. LENGTH: 1000-1500 characters (Google Posts hard limit = 1500).
3. STRUCTURE: hook -> 3-4 bullet points (concrete benefits with SEO keywords woven in) -> urgency line -> CTA.
4. CTA: end with "{CTA_TPL[lang]} {KLOOK_URL}"
5. NO emojis (Google can penalize emoji-heavy posts).
6. NO em-dashes overuse; use periods and commas.
7. Include 2-3 LSI (semantic) keywords related to the main keyword cluster.
8. Mobile-first: keep paragraphs short (2-3 lines), bullets scannable.
9. PLAIN TEXT ONLY — Google Business Posts render raw characters, so NO markdown: no "*" bullets, no "**bold**", no "#" headers. If you list items, start the line with a middle dot "·" and a space.
10. It is now month {now_kst().month} in Korea — use ONLY weather and scenery appropriate to this month; never mention other seasons (no out-of-season snow, autumn foliage, or cherry blossoms).
{avoid_block}{pb_block}
=== OUTPUT ===
Output ONLY the final post text in {LANG_FULL[lang]}. No preamble, no explanation, no markdown headers.
Make this post DISTINCTLY DIFFERENT from any previous post — fresh wording, fresh examples, fresh structure.
"""


def main() -> int:
    week = now_kst().strftime("%G-W%V")
    today = now_kst().strftime("%Y-%m-%d")
    pool = load_json(SEO_POOL_FILE, {})
    cats = load_json(CAT_FILE, {})
    memory = load_json(MEMORY_FILE, {"recent_posts": []})
    brand = load_json(BRAND_FILE, {})
    if not pool or not cats:
        print(f"[ERROR] 데이터 누락 — seo_pool={bool(pool)} cats={bool(cats)}")
        return 1

    angle = select_angle(pool, week)
    weekly_hook = select_weekly_hook(pool, week)
    angle_id = angle.get("id", "default")
    print(f"week={week} angle={angle_id} hook={weekly_hook.get('ko','')[:30]}")

    test_lang = os.environ.get("GBP_TEST_LANG", "").strip()
    langs = [l for l in LANGS if l[0] == test_lang] if test_lang else LANGS

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    md_lines = [
        f"# 📰 Google Posts — {today} ({week})\n",
        f"> 이번 주 앵글: **{angle.get('name_ko', angle_id)}** — {angle.get('focus_ko','')[:80]}",
        f"> hook: **{weekly_hook.get('ko','')}**\n", "---\n",
    ]
    languages = {}
    success = 0
    for lang, marker, label, flag in langs:
        keywords = select_keywords(pool, lang, week, n=4)
        avoid = get_recent_phrases(memory, lang, n=5)
        prompt = build_prompt(lang, week, angle, weekly_hook, keywords, avoid, brand)
        post = ""
        for _try in range(3):  # 짧은/빈 응답(429 등) 재시도 — partial 방지
            post = gemini(prompt)
            if post and len(post) > 300:
                break
            print(f"  [{lang}] 짧은 응답(len={len(post) if post else 0}) — 재시도 {_try+1}/3")
        photo = pick_photo(cats, lang, week, angle_id)
        if post and len(post) > 300:
            (OUT_DIR / f"{week}_{lang}.txt").write_text(post, encoding="utf-8")
            kw_line = ", ".join(keywords) if keywords else "-"
            md_lines.append(
                f"\n## {flag} {lang.upper()}\n\n**사진**: {photo}\n\n"
                f"**SEO 키워드**: {kw_line}\n\n```\n{post}\n```\n\n---\n")
            languages[lang] = {
                "label": label, "photo": photo, "seo_keywords": kw_line,
                "body_url": f"{RAW_BASE}/google_posts/{week}_{lang}.txt",
                "body_length": len(post),
            }
            add_to_memory(memory, week, lang, post, angle_id)
            success += 1
            print(f"  ✓ {lang}: {len(post)} chars, photo={photo}")
        else:
            print(f"  ✗ {lang}: 생성 실패 (len={len(post) if post else 0})")

    if success == 0:
        print("[ERROR] 생성된 글 0편 — 기존 파일 보존, 종료")
        return 1

    # test 모드(부분 생성)에서는 current.json/md/memory를 덮어쓰지 않음 (운영 데이터 보호)
    if test_lang:
        print(f"[TEST] {test_lang} 1편 생성 검증 완료 — current.json/memory 미수정")
        return 0

    # partial(4언어 미만)이면 기존 current.json 언어를 보존(merge) — 침묵 손실 방지.
    # 이번에 실패한 언어는 지난 콘텐츠라도 유지 → publish=false(누락)보다 stale이 나음.
    if success < len(langs) and not test_lang:
        try:
            old = json.loads((OUT_DIR / "current.json").read_text(encoding="utf-8"))
            for lc, data in (old.get("languages") or {}).items():
                if lc not in languages:
                    languages[lc] = data
                    print(f"  [merge] {lc}: 이번 실패 → 기존 콘텐츠 보존(누락 방지)")
        except Exception as e:
            print(f"  [merge] 기존 current.json 병합 실패: {e}")
    if len(languages) < 4:
        print(f"[WARN] 최종 언어 {len(languages)}/4 — Mac 건강검진이 내일 재시도 알림")

    (OUT_DIR / f"google_posts_{week}.md").write_text("\n".join(md_lines), encoding="utf-8")
    current = {
        "week": week,
        "generated_at": now_kst().isoformat(timespec="seconds"),
        "klook_url": KLOOK_URL,
        "languages": languages,
        "_generator": "google_posts_generate_cloud.py (gemini)",
    }
    (OUT_DIR / "current.json").write_text(
        json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    MEMORY_FILE.write_text(
        json.dumps(memory, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"완료 {success}/4편 — current.json week={week}, memory={len(memory.get('recent_posts',[]))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
