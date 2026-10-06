#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GBP 보이스 게이트 — 게시 전 본문 자동 검열 (2026-10-06 신설).

배경(실측): 2026-10-06 today.json(en, 1128자)이 회사 **본인 계정**에서
  "I just experienced ... with my family / Our little ones loved ..." 라는
  **1인칭 고객 후기**로 나갔다. 같은 주 한국어판도 "선택했는데 ... 있었어요".
  추가로 `Korea's only certified catamaran`(유일 주장)과
  영문 본문에 한글 혼입(`제주 tangerine juice`)까지 함께 발행됐다.
  업체가 고객인 척 후기를 쓰는 건 전역 룰 위반 + 구글 리뷰정책 위반 소지.

이 모듈은 **순수 표준 라이브러리**이며 파이썬 3.9에서도 동작한다
(이 저장소엔 /usr/bin/python3=3.9 로 도는 진입점이 섞여 있다).

쓰는 곳 (3중 방어):
  1. .github/scripts/google_posts_generate_cloud.py — 클라우드 주간 생성(4언어)
  2. .scripts/jarvis_google_posts_generator.py      — 로컬 주간 생성(13언어 회전)
  3. .scripts/jarvis_gbp_direct_post.py             — 일일 게시 직전 최종 차단

⚠️ 이 파일은 GitHub Actions(repo .github/scripts/)에도 **같은 내용으로** 둔다.
   Actions 러너엔 second-brain 이 없어서 import 할 수 없기 때문이다.
   한쪽만 고치지 말 것. 동일성 확인:
     md5 ~/second-brain/.scripts/gbp_voice_gate.py \
         <repo>/.github/scripts/gbp_voice_gate.py
"""
from __future__ import annotations

import re

GATE_VERSION = "2026-10-06b"

# ─────────────────────────────────────────────────────────────────────────────
# 1) 1인칭 — 회사는 3인칭. 페르소나인 척 1인칭 후기 금지(전역 룰).
#    CJK·태국어 등은 \b 가 동작하지 않으므로 단순 부분문자열로 둔다.
# ─────────────────────────────────────────────────────────────────────────────
_FP_EN = [
    r"(?<![A-Za-z])I(?![A-Za-z])", r"(?<![A-Za-z])I['’](m|ve|ll|d)\b",
    r"\bme\b", r"\bmy\b", r"\bmine\b", r"\bmyself\b",
    r"\bwe\b", r"\bwe['’](re|ve|ll|d)\b",
    r"\bour\b", r"\bours\b", r"\bourselves\b", r"(?-i:\bus\b)",
]
FIRST_PERSON = {
    "en": _FP_EN,
    # 한국어: 1인칭 대명사 + '후기체' 과거 경험 종결/연결어미.
    # 회사 3인칭 문장은 ~합니다/~입니다/~됩니다 로 쓴다.
    "ko": [r"저는", r"제가", r"저희", r"우리\s*(가족|아이|애들|일행)", r"우리는",
           r"제\s*아이", r"했는데", r"했더니", r"더라고요", r"였는데",
           r"았어요", r"었어요", r"였어요", r"봤어요", r"왔어요", r"갔어요"],
    "zh-CN": [r"我们", r"我家", r"咱们", r"我(?![们家])"],
    "zh-TW": [r"我們", r"我家", r"咱們", r"我(?![們家])"],
    "ja": [r"私たち", r"私", r"僕", r"わたし", r"うちの", r"我々"],
    "es": [r"\byo\b", r"\bmi\b", r"\bmis\b", r"\bm[ií]o\b", r"\bnosotros\b",
           r"\bnuestr[oa]s?\b", r"\bdisfrutamos\b", r"\bfuimos\b"],
    "fr": [r"\bje\b", r"\bj['’]ai\b", r"\bmon\b", r"\bma\b", r"\bmes\b",
           r"\bnous\b", r"\bnotre\b", r"\bnos\b"],
    "de": [r"\bich\b", r"\bmein\w*\b", r"\bwir\b", r"\bunser\w*\b", r"\buns\b"],
    "ru": [r"\bя\b", r"\bмо[йяиёе]\w*\b", r"\bмы\b", r"\bнаш\w*\b", r"\bнас\b"],
    "ar": [r"أنا", r"نحن", r"عائلتي", r"أطفالي", r"رحلتنا"],
    "th": [r"ผม", r"ฉัน", r"ดิฉัน", r"พวกเรา", r"ของเรา", r"เรา"],
    "vi": [r"\btôi\b", r"\bmình\b", r"\bchúng tôi\b", r"\bchúng ta\b", r"\bcủa tôi\b"],
    "id": [r"\bsaya\b", r"\bkami\b", r"\bkita\b", r"\bkeluarga saya\b"],
    "ms": [r"\bsaya\b", r"\bkami\b", r"\bkita\b", r"\bkeluarga saya\b"],
    "pt": [r"\beu\b", r"\bmeu\w*\b", r"\bminha\w*\b", r"\bn[óo]s\b", r"\bnoss[oa]s?\b"],
    "it": [r"\bio\b", r"\bmio\b", r"\bmia\b", r"\bmiei\b", r"\bnoi\b", r"\bnostr\w+\b"],
    "tr": [r"\bben\b", r"\bbenim\b", r"\bbiz\b", r"\bbizim\b", r"ailemle", r"ailemiz"],
    "mn": [r"\bби\b", r"\bбид\b", r"\bманай\b", r"\bминий\b"],
    "hi": [r"\bमैं\b", r"\bमेरा\b", r"\bमेरी\b", r"\bमेरे\b", r"\bहम\b", r"\bहमार[ाीे]\b"],
}

# ─────────────────────────────────────────────────────────────────────────────
# 2) 배타·1위 주장 — 룰: "1위/최고 금지('제주 대표'는 OK)".
#    `Korea's only certified catamaran` 이 실제로 발행됐다.
# ─────────────────────────────────────────────────────────────────────────────
_EX_COMMON = [r"No\.?\s*1\b", r"#1\b"]
EXCLUSIVITY = {
    "en": _EX_COMMON + [r"\bonly\b", r"\bsole\b", r"\bbest\b", r"\blargest\b",
                        r"\bunrivall?ed\b", r"\bunmatched\b", r"\bfirst and only\b"],
    "ko": _EX_COMMON + [r"유일", r"최고", r"1위", r"넘버원", r"최상의", r"가장\s*좋은"],
    "zh-CN": _EX_COMMON + [r"唯一", r"第一", r"最佳", r"最好", r"独家", r"冠军"],
    "zh-TW": _EX_COMMON + [r"唯一", r"第一", r"最佳", r"最好", r"獨家", r"冠軍"],
    "ja": _EX_COMMON + [r"唯一", r"一番", r"最高", r"日本一", r"韓国一"],
    "es": _EX_COMMON + [r"\b[úu]nic[oa]\b", r"\bel mejor\b", r"\bla mejor\b"],
    "fr": _EX_COMMON + [r"\bunique\b", r"\bseul\b", r"\bseule\b", r"\ble meilleur\b", r"\bla meilleure\b"],
    "de": _EX_COMMON + [r"\beinzige\w*\b", r"\bbeste\w*\b"],
    "ru": _EX_COMMON + [r"единственн\w*", r"лучш\w*"],
    "ar": _EX_COMMON + [r"الوحيد", r"الأفضل"],
    "th": _EX_COMMON + [r"แห่งเดียว", r"ที่ดีที่สุด"],
    "vi": _EX_COMMON + [r"duy nhất", r"tốt nhất"],
    "id": _EX_COMMON + [r"satu-satunya", r"terbaik"],
    "ms": _EX_COMMON + [r"satu-satunya", r"terbaik"],
    "pt": _EX_COMMON + [r"\b[úu]nic[oa]\b", r"\bo melhor\b", r"\ba melhor\b"],
    "it": _EX_COMMON + [r"\bunic[oa]\b", r"\bil migliore\b", r"\bla migliore\b"],
    "tr": _EX_COMMON + [r"\btek\b", r"\ben iyi\b"],
    "mn": _EX_COMMON + [r"цорын ганц", r"хамгийн сайн"],
    "hi": _EX_COMMON + [r"एकमात्र", r"सबसे अच्छा"],
}

# ─────────────────────────────────────────────────────────────────────────────
# 3) 대상 언어 외 문자 — 영문 본문에 `제주`(한글)가 그대로 섞여 발행됐다.
#    언어별 '허용 문자군'만 두고 그 밖의 문자군이 1자라도 있으면 위반.
# ─────────────────────────────────────────────────────────────────────────────
SCRIPTS = {
    "hangul": r"[가-힣ᄀ-ᇿ㄰-㆏]",
    "kana":   r"[぀-ヿㇰ-ㇿ]",
    "han":    r"[一-鿿㐀-䶿]",
    "cyrillic": r"[Ѐ-ӿ]",
    "thai":   r"[฀-๿]",
    "arabic": r"[؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿]",
    "devanagari": r"[ऀ-ॿ]",
}
ALLOWED_SCRIPTS = {
    "ko": {"hangul", "han"},
    "en": set(),
    "zh-CN": {"han"}, "zh-TW": {"han"},
    "ja": {"kana", "han"},
    "ru": {"cyrillic"}, "mn": {"cyrillic"},
    "th": {"thai"},
    "ar": {"arabic"},
    "hi": {"devanagari"},
    # 라틴 문자권 — 라틴은 어느 언어에서나 허용이라 집합에 넣지 않는다
    "es": set(), "fr": set(), "de": set(), "pt": set(), "it": set(),
    "tr": set(), "id": set(), "ms": set(), "vi": set(),
}

# 구글 소식 본문 하드 리밋 1500자. 발행기는 예약경로에서 body[:1490] 으로 자르므로
# 넘는 본문은 CTA 가 잘려 나간다 — 게이트가 길이도 본다.
MAX_LEN = 1500

_URL_RE = re.compile(r"https?://\S+")
# 브랜드 표기·해시태그는 라틴이라 영향 없음. URL 만 걷어낸다(en-US 같은 조각이 1인칭 us 로 오검출).
# ─────────────────────────────────────────────────────────────────────────────
# 4) 검증된 사실 위반 — 전역 절대 규칙 (어종 / 엔진·무동력 / 반려동물)
#    실측 2026-10-06: 수리 후 생성된 ko 본문이 어종을 "볼락"으로 썼다
#    (프롬프트 영문 'rockfish' 를 모델이 볼락으로 번역 — 허용은 우럭뿐).
# ─────────────────────────────────────────────────────────────────────────────
BANNED_FACTS = [
    # 어종 — 허용은 우럭·쏨뱅이·쥐치·복어뿐
    ("FISH", r"볼락"), ("FISH", r"갈치"), ("FISH", r"고등어"), ("FISH", r"참돔"),
    ("FISH", r"방어"), ("FISH", r"광어"), ("FISH", r"\bmackerel\b"),
    ("FISH", r"\bhairtail\b"), ("FISH", r"\bcutlassfish\b"), ("FISH", r"\bsea bass\b"),
    ("FISH", r"サバ"), ("FISH", r"タチウオ"), ("FISH", r"鲭鱼"), ("FISH", r"带鱼"),
    # 엔진·무동력 — 그랑블루는 세일링 요트지만 엔진을 함께 쓴다
    ("ENGINE", r"엔진\s*없이"), ("ENGINE", r"모터\s*없이"), ("ENGINE", r"무동력"),
    ("ENGINE", r"\bno engine\b"), ("ENGINE", r"\bwithout an? engine\b"),
    ("ENGINE", r"\bengine-?less\b"), ("ENGINE", r"\bno motor\b"),
    ("ENGINE", r"エンジンなし"), ("ENGINE", r"無動力"), ("ENGINE", r"无动力"),
    # 반려동물 탑승 불가
    ("PET", r"반려동물"), ("PET", r"애견"), ("PET", r"\bpet-friendly\b"),
    ("PET", r"\bpets? (are )?welcome\b"), ("PET", r"\bbring your dog\b"),
]

# ─────────────────────────────────────────────────────────────────────────────
# 5) 지금이 아닌 달 — SEO 키워드 풀에 `제주 6월 가볼만한곳` 같은 월 고정 키워드가
#    섞여 있어 10월 글에 "제주 6월 가볼만한곳 코스로 손꼽히는"이 들어갔다(실측).
# ─────────────────────────────────────────────────────────────────────────────
_EN_MONTHS = ["January", "February", "March", "April", "June", "July",
              "August", "September", "October", "November", "December"]  # May는 조동사와 겹쳐 따로
_KST_OFFSET_H = 9


def current_month():
    """KST 기준 현재 월. GitHub Actions(UTC)에서도 한국 날짜를 쓴다."""
    import datetime as _dt
    return (_dt.datetime.utcnow() + _dt.timedelta(hours=_KST_OFFSET_H)).month


def month_mismatch(text, now_month=None):
    """본문에 '지금이 아닌 달'이 적혔으면 [(표기, 월)] 반환."""
    m_now = now_month or current_month()
    body = _URL_RE.sub(" ", text or "")
    out = []
    for m in re.finditer(r"(?<![0-9])([1-9]|1[0-2])\s*[월月]", body):
        if int(m.group(1)) != m_now:
            out.append((m.group(0).strip(), int(m.group(1))))
    for i, name in enumerate(_EN_MONTHS):
        num = [1, 2, 3, 4, 6, 7, 8, 9, 10, 11, 12][i]
        if num != m_now and re.search(r"\b%s\b" % name, body, re.IGNORECASE):
            out.append((name, num))
    if m_now != 5 and re.search(r"\b(?:in|during|by|before|after|since)\s+May\b|\bMay\s+\d", body):
        out.append(("May", 5))
    # 중복 제거(순서 유지)
    seen, uniq = set(), []
    for hit, num in out:
        if (hit.lower(), num) in seen:
            continue
        seen.add((hit.lower(), num))
        uniq.append((hit, num))
    return uniq


def _scan(text, patterns, code, why):
    out = []
    seen = set()
    for pat in patterns:
        for m in re.finditer(pat, text, re.IGNORECASE):
            hit = m.group(0).strip()
            key = (code, hit.lower())
            if key in seen:
                continue
            seen.add(key)
            out.append({"code": code, "hit": hit, "why": why})
            break          # 패턴당 1건만 — 리포트가 터지지 않게
    return out


def check(text, lang):
    """본문 1편 검사. 반환: 위반 리스트(빈 리스트면 통과)."""
    if not text:
        return [{"code": "EMPTY", "hit": "", "why": "본문이 비었다"}]
    body = _URL_RE.sub(" ", text)
    viol = []
    if len(text) > MAX_LEN:
        viol.append({"code": "TOO_LONG", "hit": "%d자" % len(text),
                     "why": "구글 소식 한도 %d자 초과 — 더 짧게 다시 써라" % MAX_LEN})
    viol += _scan(body, FIRST_PERSON.get(lang, _FP_EN), "FIRST_PERSON",
                  "회사 계정에서 1인칭(고객인 척) 서술 — 3인칭으로 바꿔라")
    viol += _scan(body, EXCLUSIVITY.get(lang, EXCLUSIVITY["en"]), "EXCLUSIVITY",
                  "유일/최고/1위 류 배타적 주장 금지 — 사실만 서술")
    viol += _scan(body, [p for _, p in BANNED_FACTS], "BANNED_FACT",
                  "검증된 사실 위반 — 어종은 우럭·쏨뱅이·쥐치·복어만, 엔진/무동력 표현 금지, 반려동물 탑승 불가")
    _mm = month_mismatch(text)
    if _mm:
        viol.append({"code": "WRONG_MONTH",
                     "hit": ", ".join("%s" % h for h, _ in _mm[:3]),
                     "why": "지금은 %d월 — 다른 달 표기 금지(월 고정 SEO 키워드는 버려라)" % current_month()})
    allowed = ALLOWED_SCRIPTS.get(lang, set())
    for name, rng in SCRIPTS.items():
        if name in allowed:
            continue
        m = re.search(rng, body)
        if m:
            viol.append({"code": "SCRIPT_" + name.upper(), "hit": m.group(0),
                         "why": "대상 언어(%s) 밖의 문자 혼입 — 해당 언어 표기로 바꿔라" % lang})
    return viol


def brief(viol):
    """LLM 재생성 프롬프트에 붙일 한 줄 피드백."""
    if not viol:
        return ""
    parts = []
    for v in viol:
        hit = v["hit"]
        parts.append("%s(\"%s\")" % (v["code"], hit[:24]))
    return "; ".join(parts)


def report(viol):
    """사람·로그용 여러 줄 리포트."""
    if not viol:
        return "PASS (위반 0건)"
    return "\n".join("  ✗ %-16s hit=%r — %s" % (v["code"], v["hit"][:30], v["why"])
                     for v in viol)


def counts(text, lang):
    """검증용 — 지표별 실제 등장 횟수(0인지 눈으로 확인하는 용도)."""
    body = _URL_RE.sub(" ", text or "")
    fp = sum(len(re.findall(p, body, re.IGNORECASE))
             for p in FIRST_PERSON.get(lang, _FP_EN))
    ex = sum(len(re.findall(p, body, re.IGNORECASE))
             for p in EXCLUSIVITY.get(lang, EXCLUSIVITY["en"]))
    allowed = ALLOWED_SCRIPTS.get(lang, set())
    fo = sum(len(re.findall(r, body)) for n, r in SCRIPTS.items() if n not in allowed)
    return {"first_person": fp, "exclusivity": ex, "foreign_chars": fo}


if __name__ == "__main__":
    import json
    import sys
    if len(sys.argv) >= 3:
        lang = sys.argv[1]
        txt = open(sys.argv[2], encoding="utf-8").read()
    else:
        print("usage: gbp_voice_gate.py <lang> <file>  |  --selftest")
        sys.exit(2)
    v = check(txt, lang)
    print(report(v))
    print(json.dumps(counts(txt, lang), ensure_ascii=False))
    sys.exit(1 if v else 0)
