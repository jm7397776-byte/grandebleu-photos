#!/usr/bin/env python3
"""Render one Blogger-ready post into blog_today.json for Make.com.

This does not call the Blogger API. Make.com reads blog_today.json and publishes it.
"""
import html as html_lib
import hashlib
import importlib.util
import json
import os
import re
from datetime import datetime
from pathlib import Path

REPO = Path(os.environ.get("REPO_DIR", ".")).resolve()
PUB_SRC = REPO / ".github" / "scripts" / "blog_publisher_cloud.py"
QUEUE = REPO / "blog_queue"
HISTORY = REPO / "blog_today_history.json"
LANGS_CYCLE = ["en", "ja", "zh-CN"]
TARGET_TEXT_CHARS = 3400
MAX_FIGURES = 9
RECENT_IMAGE_WINDOW = 60

# === 제미나이 카피 생성 (같은 주제라도 매번 다른 본문; 실패 시 아래 템플릿 폴백) ===
import urllib.request
_GKEY = os.environ.get("GEMINI_API_KEY", "")
try:
    _FACTS = (REPO / "google_posts" / "_data" / "brand_facts.json").read_text(encoding="utf-8")
except Exception:
    _FACTS = ""
try:
    _PBRULES = (REPO / "google_posts" / "_data" / "powerblogger_rules.md").read_text(encoding="utf-8")
except Exception:
    _PBRULES = ""
_LANG_NAME = {"en": "English", "ja": "Japanese", "zh-CN": "Simplified Chinese"}


def _gemini(prompt, timeout=120):
    if not _GKEY:
        return ""
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 1.0, "topP": 0.97, "maxOutputTokens": 4096,
                             "thinkingConfig": {"thinkingBudget": 0},
                             "responseMimeType": "application/json"},
    }).encode("utf-8")
    for model in ("gemini-2.5-pro", "gemini-2.5-flash"):
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={_GKEY}"
        try:
            with urllib.request.urlopen(
                urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}),
                timeout=timeout) as r:
                d = json.load(r)
            t = "".join(p.get("text", "") for p in d["candidates"][0]["content"]["parts"]).strip()
            if t:
                return t
        except Exception:
            continue
    return ""


def _recent_blog_titles(history, n=10):
    return [it.get("title", "") for it in history.get("selected", [])[-n:] if it.get("title")]


def _gemini_blog(lang, angle, audience, focus, history):
    if not _GKEY:
        return None
    from datetime import timezone as _tz, timedelta as _td
    _m = datetime.now(_tz(_td(hours=9))).month  # KST 월
    focus_l = _label(FOCUS_LABELS, focus, lang)
    aud_l = _label(AUDIENCE_LABELS, audience, lang)
    avoid = _recent_blog_titles(history or {}, 10)
    avoid_block = ("\n[AVOID — do NOT repeat the opening/structure of these recent posts]\n"
                   + "\n".join(f"- {a}" for a in avoid)) if avoid else ""
    lang_name = _LANG_NAME.get(lang, "English")
    prompt = (
        f"Write ONE Jeju travel blog post in {lang_name} for Grande Bleu Yacht.\n"
        f"[Angle/Focus] {focus_l}\n[Audience] {aud_l}\n"
        f"[Verified facts — use ONLY these, never invent prices] {json.dumps(FACTS, ensure_ascii=False)}\n"
        f"[Brand facts JSON] {_FACTS[:1200]}\n"
        f"[Korean power-blogger writing principles — apply the principles, not the Korean text] {_PBRULES[:2400]}\n\n"
        "[Rules]\n"
        "- Markdown body, 2500-3400 characters, 5-6 sections each with a ## header.\n"
        "- MUST be distinctly different in wording and structure every time, even for the same focus.\n"
        "- Conversational and informative, not ad copy. No invented price numbers.\n"
        f"- Place names strictly in {lang_name}'s own script (NO Korean Hangul in non-Korean text).\n"
        "- Fish species only: rockfish, scorpionfish, filefish, pufferfish. No engine/motor claims.\n"
        f"- It is now month {_m} in Korea; use ONLY weather and scenery appropriate to this month, never other seasons (no out-of-season snow/foliage/cherry-blossoms).\n"
        f"{avoid_block}\n"
        '- Output JSON only: {"title":"...","meta":"...","keywords":"...","body":"...markdown..."}'
    )
    raw = _gemini(prompt)
    if not raw:
        return None
    try:
        d = json.loads(raw)
        if d.get("title") and d.get("body") and len(str(d["body"])) > 800:
            return {"title": str(d["title"]).strip(), "meta": str(d.get("meta", "")).strip(),
                    "keywords": str(d.get("keywords", "")).strip(), "body": str(d["body"]).strip()}
    except Exception:
        pass
    return None

GENERATED_ANGLES = [
    "sunset timing and golden-hour photos",
    "Daepo Port check-in and boarding flow",
    "catamaran stability for families",
    "Jeju south coast geology from sea level",
    "Wolpyeong Jusangjeolli basalt columns",
    "Wolpyeong sea cave and Elephant Rock",
    "food and drinks included onboard",
    "fishing experience for first-timers",
    "life-jacket rule and onboard safety",
    "hotel and premium travel partnerships",
    "corporate and MICE group use",
    "solo traveler calm-hour itinerary",
    "couple and honeymoon photo route",
    "multi-generation family travel",
    "winter Jeju sailing comfort",
    "spring light and clear coastline",
    "summer evening sea breeze",
    "autumn shoulder-season sailing",
    "luxury daytime tour vs sunset tour",
    "what to wear on a Jeju yacht",
    "phone photography on a moving yacht",
    "one-hour itinerary around Seogwipo",
    "why Daepo Port works for yacht tours",
    "what first-time Korea travelers should know",
    "premium but practical Jeju activity",
    "quiet travel instead of crowded sightseeing",
    "brand-certified catamaran story",
    "licensed captain and engineer operations",
    "clear pricing and promotion notes",
    "partner-platform booking comparison",
]

AUDIENCES = [
    "first-time Jeju visitors",
    "Japan travelers",
    "Chinese-speaking travelers",
    "families with children",
    "couples and honeymooners",
    "corporate groups",
    "solo travelers",
    "premium hotel guests",
]

INFO_FOCUS = [
    "route",
    "safety",
    "price",
    "food",
    "photos",
    "booking",
    "season",
    "ship",
]


# 언어별 라벨 — 폴백 글 제목/본문을 시장별 고검색어 선두 + 완전 현지어로 (언어혼입 제거)
FOCUS_LABELS = {
    "route":   {"en": "Route & Highlights",    "ja": "航路と見どころ", "zh-CN": "航线亮点"},
    "safety":  {"en": "Safety & Comfort",      "ja": "安全と快適さ",   "zh-CN": "安全与舒适"},
    "price":   {"en": "Price Guide",           "ja": "料金ガイド",     "zh-CN": "价格指南"},
    "food":    {"en": "Onboard Food & Drinks", "ja": "船上グルメ",     "zh-CN": "船上美食"},
    "photos":  {"en": "Photo Spots",           "ja": "撮影スポット",   "zh-CN": "拍照打卡"},
    "booking": {"en": "How to Book",           "ja": "予約方法",       "zh-CN": "预订指南"},
    "season":  {"en": "Best Season",           "ja": "ベストシーズン", "zh-CN": "最佳季节"},
    "ship":    {"en": "The Catamaran",         "ja": "カタマラン船",   "zh-CN": "双体帆船"},
}
AUDIENCE_LABELS = {
    "first-time Jeju visitors":   {"en": "first-time visitors",        "ja": "はじめての済州島",       "zh-CN": "初游济州"},
    "Japan travelers":            {"en": "travelers from Japan",       "ja": "日本からの旅行者",       "zh-CN": "日本游客"},
    "Chinese-speaking travelers": {"en": "Chinese-speaking travelers", "ja": "中国語圏の旅行者",       "zh-CN": "华语游客"},
    "families with children":     {"en": "families with kids",         "ja": "子連れ家族",            "zh-CN": "亲子家庭"},
    "couples and honeymooners":   {"en": "couples and honeymooners",   "ja": "カップル・ハネムーン",   "zh-CN": "情侣蜜月"},
    "corporate groups":           {"en": "corporate groups",           "ja": "企業・団体",            "zh-CN": "企业团体"},
    "solo travelers":             {"en": "solo travelers",             "ja": "ひとり旅",              "zh-CN": "独自旅行"},
    "premium hotel guests":       {"en": "premium hotel guests",       "ja": "プレミアムホテル滞在者", "zh-CN": "高端酒店宾客"},
}


def _label(d, key, lang):
    return d.get(key, {}).get(lang) or d.get(key, {}).get("en") or key

FACTS = {
    "duration": "1 hour",
    "port": "Daepo Port, Seogwipo, Jeju",
    "boat": "catamaran sailing yacht",
    "capacity": "91 guests total: Yacht 550 seats 44 and Yacht 620 seats 47",
    "safety": "licensed captain and engineer onboard",
    "landmarks": "Wolpyeong Jusangjeolli, Wolpyeong Sea Cave, Elephant Rock, and the Daepo coastline",
    "food": "draft beer, wine, Jeju tangerine juice, bottled water, snacks, and cup ramen",
    "booking": "Naver, Klook, KKday, and Ctrip/Trip.com partner platforms",
    "price": "Luxury Tour adult ₩60,000 / child ₩40,000, promo ₩48,000 / ₩28,000; Sunset Tour adult ₩70,000 / child ₩40,000, promo ₩56,000 / ₩28,000",
    "phone": "+82-64-739-7776",
    "instagram": "@gb.jeju",
}

spec = importlib.util.spec_from_file_location("pub", PUB_SRC)
pub = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pub)


def load_history():
    if HISTORY.exists():
        try:
            return json.loads(HISTORY.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"selected": []}


def save_history(history):
    HISTORY.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")


def used_generated_keys(history):
    return {item.get("key") for item in history.get("selected", []) if str(item.get("key", "")).startswith("auto_")}


def generated_combo(lang, history):
    used = used_generated_keys(history)
    day_salt = datetime.now().strftime("%Y%m%d")
    combos = []
    for angle_idx, angle in enumerate(GENERATED_ANGLES):
        for audience_idx, audience in enumerate(AUDIENCES):
            for focus_idx, focus in enumerate(INFO_FOCUS):
                key = f"auto_{angle_idx:02d}_{audience_idx:02d}_{focus_idx:02d}__{lang}"
                seed = f"{day_salt}_{key}"
                score = int(hashlib.md5(seed.encode()).hexdigest(), 16)
                combos.append((score, key, angle, audience, focus))
    for _, key, angle, audience, focus in sorted(combos):
        if key not in used:
            return key, angle, audience, focus
    _, key, angle, audience, focus = sorted(combos)[0]
    base = key.rsplit("__", 1)[0]
    return f"{base}_{day_salt}__{lang}", angle, audience, focus


def generated_text(lang, angle, audience, focus, history=None):
    _g = _gemini_blog(lang, angle, audience, focus, history or {})
    if _g:
        return _g["title"], _g["meta"], _g["keywords"], _g["body"]
    focus_l = _label(FOCUS_LABELS, focus, lang)
    aud_l = _label(AUDIENCE_LABELS, audience, lang)
    if lang == "ja":
        title = f"済州島ヨット {focus_l}｜Grande Bleu 西帰浦セーリング（{aud_l}向け）"
        meta = f"済州島ヨット攻略：{focus_l}。Grande Bleu 西帰浦の1時間カタマラン、船上グルメ込み。{aud_l}向けの予約前ガイド。"
        keywords = "済州島ヨット, 済州島旅行, 済州島 観光, Grande Bleu, 大浦港, カタマラン, サンセットクルーズ, 韓国旅行"
        body = f"""# {title}

## 今日のテーマ
今回のテーマは **{focus_l}** です。Grande Bleu は済州島・西帰浦の大浦港から出航する {FACTS['boat']} で、所要時間は約 {FACTS['duration']}。短い時間でも、海から見る済州南岸の印象は陸上観光とはかなり違います。

この文章は {aud_l} 向けに、予約前に知っておくと安心な情報だけを整理しています。

## コースで見えるもの
航路では {FACTS['landmarks']} など、南岸らしい地形を海側から眺めます。特に柱状節理は、展望台から見下ろす姿と、船上から見上げる姿で印象が変わります。

1時間のコースなので、半日を使う大型ツアーではありません。済州旅行の予定に差し込みやすい、静かな海上時間と考えると選びやすくなります。

## 船と安全
船は双胴船のカタマランです。定員は {FACTS['capacity']}。運航時は {FACTS['safety']} で、天候と海況を見ながら進行します。

小さなお子様や年配の方を含むグループでも、揺れが少ない船を選びたい場合はカタマラン構造が判断材料になります。

## 船上で含まれるもの
船上では {FACTS['food']} などを用意しています。食事を目的にしたレストラン型の船ではなく、海を見ながら軽く楽しめる構成です。

写真を撮るなら、出航直後、海岸線に近づく時間、帰港前の光が変わる時間の3つを意識すると残しやすいです。

## 料金と予約チャネル
料金目安は {FACTS['price']}。実際の空席、プロモーション、変更・返金条件は予約チャネルごとに異なることがあります。

予約は {FACTS['booking']} など複数の提携プラットフォームから確認できます。日本語・英語・中国語など、自分の言語と決済方法に合うページを選んでください。

## 連絡 정보
電話は {FACTS['phone']}、Instagram は {FACTS['instagram']} です。電話は韓国語中心の対応になるため、海外からの予約確認は各予約プラットフォームの案内を先に見るとスムーズです。
"""
    elif lang == "zh-CN":
        title = f"济州岛游艇攻略 · {focus_l}｜Grande Bleu 西归浦帆船（{aud_l}）"
        meta = f"济州岛游艇攻略：{focus_l}。Grande Bleu 西归浦1小时双体帆船，含船上美食。面向{aud_l}的预订前指南。"
        keywords = "济州岛游艇, 济州岛旅游, 济州岛攻略, Grande Bleu, 大浦港, 双体帆船, 日落巡航, 韩国旅游"
        body = f"""# {title}

## 今日主题
这篇文章的主题是 **{focus_l}**。Grande Bleu 从济州岛西归浦大浦港出发，船型为 {FACTS['boat']}，航程约 {FACTS['duration']}。

内容主要写给 {aud_l}，帮助你在预订前快速理解这趟航行适不适合自己。

## 航线亮点
航行中可以看到 {FACTS['landmarks']} 等济州南岸地形。柱状节理从陆地看是一种风景，从海面看则更能感受到高度和岩石纹理。

这不是半日行程，而是刚好可以放进济州旅行日程中的1小时海上体验。

## 船只与安全
Grande Bleu 使用双体帆船。总载客量为 {FACTS['capacity']}。每次航行都有 {FACTS['safety']}，根据天气和海况进行判断。

如果同行者有儿童、长辈，或担心晕船，双体船的稳定性会是一个重要参考。

## 船上包含
船上提供 {FACTS['food']} 等。它不是正式餐厅，而是在看海、拍照、感受海风时轻松享用的配置。

拍照建议抓住三个时段: 刚出港、靠近海岸线、返航前光线变化的时候。

## 价格与预订渠道
价格参考: {FACTS['price']}。实际余位、活动价、改退规则会因平台而不同。

可通过 {FACTS['booking']} 等多个合作平台预订。请选择适合自己语言、支付方式和售后规则的平台。

## 联系方式
电话: {FACTS['phone']}。Instagram: {FACTS['instagram']}。电话主要以韩语沟通，海外旅客建议先查看预订平台页面。
"""
    else:
        title = f"Jeju Yacht Tour — {focus_l} | Grande Bleu Catamaran, Seogwipo Jeju ({aud_l})"
        meta = f"Jeju yacht tour guide: {focus_l} for your Grande Bleu catamaran in Seogwipo, Jeju. Written for {aud_l}."
        keywords = "Jeju yacht tour, things to do in Jeju, Jeju sunset cruise, Grande Bleu, Daepo Port, Seogwipo, catamaran sailing, Korea travel"
        body = f"""# {title}

## Today's Angle
This post focuses on **{focus_l}**. Grande Bleu sails from {FACTS['port']} on a {FACTS['boat']}, with a cruise time of about {FACTS['duration']}.

It is written for {aud_l}, with practical details that help before booking.

## What You See On The Route
The route introduces Jeju's southern coastline from sea level: {FACTS['landmarks']}. The basalt columns feel different when viewed from the water, because you see the height, texture, and coastline in one frame.

This is not a half-day tour. It is a compact one-hour sailing experience that fits easily into a Jeju itinerary.

## Ship And Safety
Grande Bleu operates catamarans, not single-hull boats. Capacity is {FACTS['capacity']}. Each sailing includes a {FACTS['safety']}.

For families, older travelers, and anyone worried about motion, the twin-hull structure is one of the clearest reasons to choose this style of boat.

## Onboard Inclusions
Onboard items include {FACTS['food']}. The experience is not a formal restaurant cruise; it is a relaxed sea-view hour with drinks, light food, photos, and coastal scenery.

For photos, watch three moments: just after departure, when the yacht approaches the coastline, and when the light changes before returning to port.

## Price And Booking Channels
Price guide: {FACTS['price']}. Final availability, promotions, language support, and refund rules can differ by booking platform.

Reservations are available through {FACTS['booking']}. Choose the platform that best matches your country, language, payment method, and change/refund needs.

## Contact
Phone: {FACTS['phone']}. Instagram: {FACTS['instagram']}. Phone support is mainly Korean, so overseas travelers should check the booking platform page first.
"""
    return title, meta, keywords, body


def ensure_generated_post(lang, history):
    key, angle, audience, focus = generated_combo(lang, history)
    title, meta, keywords, body = generated_text(lang, angle, audience, focus, history)
    lang_dir = QUEUE / lang
    lang_dir.mkdir(parents=True, exist_ok=True)
    stem = key.rsplit("__", 1)[0]
    path = lang_dir / f"{stem}.md"
    content = f"TITLE: {title}\nMETA_DESC: {meta}\nKEYWORDS: {keywords}\n---BODY---\n{body}\n"
    path.write_text(content, encoding="utf-8")
    return path, f"{path.stem}__{lang}", pub.parse_post(content)


def image_urls(content):
    return re.findall(r'<img[^>]+src=["\']([^"\']+)["\']', content, flags=re.I)


def recent_image_urls(history, window=RECENT_IMAGE_WINDOW):
    urls = []
    for item in history.get("selected", [])[-window:]:
        urls.extend(item.get("image_urls", []))
    return set(urls)


def pick_post(lang):
    lang_dir = QUEUE / lang
    history = load_history()
    used = {item.get("key") for item in history.get("selected", [])}
    candidates = sorted(lang_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in candidates:
        if path.name.startswith("_"):
            continue
        key = f"{path.stem}__{lang}"
        if key in used:
            continue
        post = pub.parse_post(path.read_text(encoding="utf-8"))
        if post.get("title") and len(post.get("body", "")) >= 500:
            return path, key, post
    return None, None, None


def count_remaining_candidates(lang):
    lang_dir = QUEUE / lang
    history = load_history()
    used = {item.get("key") for item in history.get("selected", [])}
    count = 0
    if not lang_dir.exists():
        return count
    for path in lang_dir.glob("*.md"):
        if path.name.startswith("_"):
            continue
        key = f"{path.stem}__{lang}"
        if key not in used:
            count += 1
    return count


def visible_text_len(content):
    text = re.sub(r"<(script|style)\b.*?</\1>", " ", content, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_lib.unescape(re.sub(r"\s+", " ", text)).strip()
    return len(text)


def strip_tags(fragment):
    return html_lib.unescape(re.sub(r"<[^>]+>", "", fragment)).strip()


def remove_repeated_tail_sections(content):
    pattern = re.compile(
        r"(<h2\b[^>]*>.*?</h2>)(.*?)(?=<h2\b|</div>\s*(?:$|<p\s))",
        flags=re.S | re.I,
    )
    sections = list(pattern.finditer(content))
    if not sections:
        return content

    def without_ranges(src, ranges):
        for start, end in sorted(ranges, reverse=True):
            src = src[:start] + src[end:]
        return src

    remove = []
    for match in sections:
        title = strip_tags(match.group(1)).lower()
        if title in {"about grande bleu", "about grande bleu yacht"}:
            remove.append((match.start(), match.end()))
    compact = without_ranges(content, remove)

    if visible_text_len(compact) <= TARGET_TEXT_CHARS:
        return compact

    sections = list(pattern.finditer(compact))
    removable = []
    for match in sections:
        title = strip_tags(match.group(1)).lower()
        if "faq" in title or "よくある" in title or "常见" in title or "常見" in title:
            continue
        removable.append((match.start(), match.end()))

    for item in reversed(removable):
        candidate = without_ranges(compact, [item])
        if visible_text_len(candidate) >= 1800:
            compact = candidate
        if visible_text_len(compact) <= TARGET_TEXT_CHARS:
            break
    return compact


def count_figures(content):
    return len(re.findall(r"<figure\b.*?</figure>", content, flags=re.S | re.I))


def limit_figures(content):
    seen = 0

    def repl(match):
        nonlocal seen
        seen += 1
        if seen > MAX_FIGURES:
            return ""
        return match.group(0)

    return re.sub(r"<figure\b.*?</figure>", repl, content, flags=re.S | re.I)


def remove_recent_figures(content, avoid_urls, min_figures=6):
    avoid_urls = set(avoid_urls or [])
    if not avoid_urls:
        return content

    remaining = count_figures(content)

    def repl(match):
        nonlocal remaining
        figure = match.group(0)
        src_match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', figure, flags=re.I)
        if not src_match:
            return figure
        if src_match.group(1) in avoid_urls and remaining > min_figures:
            remaining -= 1
            return ""
        return figure

    return re.sub(r"<figure\b.*?</figure>", repl, content, flags=re.S | re.I)


def dedupe_figures(content):
    seen = set()

    def repl(match):
        figure = match.group(0)
        src_match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', figure, flags=re.I)
        if not src_match:
            return figure
        src = src_match.group(1)
        if src in seen:
            return ""
        seen.add(src)
        return figure

    return re.sub(r"<figure\b.*?</figure>", repl, content, flags=re.S | re.I)


def compact_for_make(content, avoid_photo_urls=None):
    content = remove_repeated_tail_sections(content)
    content = remove_recent_figures(content, avoid_photo_urls)
    content = dedupe_figures(content)
    return limit_figures(content)


def render(lang, post, avoid_photo_urls=None):
    klook = os.environ.get("GRANDEBLEU_KLOOK_URL", pub.KLOOK_URL_DEFAULT)
    meta = pub.LANG_META.get(lang, pub.LANG_META["en"])
    body = pub.sanitize_body(post["body"], klook, lang)
    avoid_photo_urls = set(avoid_photo_urls or [])
    body_html = compact_for_make(pub.md_to_html(
        body,
        photo_seed=f"{post['title']}_{lang}",
        avoid_photo_urls=avoid_photo_urls,
    ), avoid_photo_urls=avoid_photo_urls)
    cta = (
        f'<p style="text-align:center;margin:2rem 0;">'
        f'<a href="{klook}" target="_blank" rel="noopener" '
        f'style="display:inline-block;background:#d4a437;color:#fff;'
        f'padding:1rem 2rem;border-radius:8px;text-decoration:none;font-weight:600;">'
        f'{meta["klook_headline"]} →</a></p>'
    )
    labels = [k.strip() for k in post["keywords"].split(",") if k.strip()][:6]
    labels.append(f"lang:{lang}")
    title = post["title"][:180]
    # 라벨을 본문 하단 태그 링크로(Mail2Blogger는 Blogger 라벨 미지원 → 본문 태그로 노출·SEO 보강)
    tag_labels = [l for l in labels if not l.startswith("lang:")]
    import urllib.parse as _up
    tag_html = ""
    if tag_labels:
        links = " · ".join(
            f'<a href="https://jejugrandebleuyacht.blogspot.com/search/label/{_up.quote(t)}" '
            f'style="color:#888;text-decoration:none;">#{t.replace(" ", "")}</a>' for t in tag_labels)
        tag_html = f'<p style="margin-top:2.2rem;font-size:0.9rem;color:#777;border-top:1px solid #eee;padding-top:1rem;">{links}</p>'
    urls = image_urls(body_html)
    return {
        "title": title,
        "content": body_html + cta + tag_html,
        "labels": labels,
        "link": "https://jejugrandebleuyacht.blogspot.com/",
        "lang": lang,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "image_urls": urls,
        "image_count": len(urls),
    }


def main():
    lang = LANGS_CYCLE[datetime.now().toordinal() % 3]  # 매일 강제 3순환(요일 무관, 연속 중복 없음)
    history = load_history()
    path, key, post = pick_post(lang)
    if not post:
        path, key, post = ensure_generated_post(lang, history)
        print(f"{lang}: generated fallback candidate {path.relative_to(REPO)}")
    data = render(lang, post, avoid_photo_urls=recent_image_urls(history))
    (REPO / "blog_today.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    selected = history.setdefault("selected", [])
    selected.append({
        "key": key,
        "lang": lang,
        "file": str(path.relative_to(REPO)),
        "title": data["title"],
        "generated_at": data["generated_at"],
        "image_urls": data["image_urls"],
        "image_count": data["image_count"],
        "remaining_candidates": count_remaining_candidates(lang),
    })
    history["selected"] = selected[-90:]
    save_history(history)
    print(f"blog_today.json rendered: {lang} {data['title']}")
    print(f"html={len(data['content'])} text={visible_text_len(data['content'])} images={data['image_count']}")


if __name__ == "__main__":
    main()
