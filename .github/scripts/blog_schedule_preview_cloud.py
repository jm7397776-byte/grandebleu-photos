#!/usr/bin/env python3
"""외국블로그 '앞으로 발행될 글' 미리보기 — 텔레그램 전송 + repo 파일 갱신.

실제 생성기 generated_combo(when=날짜)를 그대로 호출 → 계절필터·무중복이
실제 발행과 100% 동일하게 반영된다. (네이버 예약목록처럼 미리 확인용)
"""
import copy
import os
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta

REPO = os.environ.get("REPO_DIR", ".")
sys.path.insert(0, os.path.join(REPO, ".github", "scripts"))
os.environ.setdefault("REPO_DIR", REPO)
import blog_today_render_cloud as R  # noqa: E402

END = date(2026, 12, 31)
DAYS = int(os.environ.get("PREVIEW_DAYS", "14"))
FLAG = {"en": "🇬🇧영어", "ja": "🇯🇵일본어", "zh-CN": "🇨🇳중국어"}

ANGLE_KO = {
    "sunset timing and golden-hour photos": "선셋 타이밍·골든아워 사진",
    "Daepo Port check-in and boarding flow": "대포항 체크인·승선 절차",
    "catamaran stability for families": "가족용 쌍동선 안정성",
    "Jeju south coast geology from sea level": "바다에서 본 제주 남부 해안",
    "Wolpyeong Jusangjeolli basalt columns": "월평 주상절리 현무암",
    "Wolpyeong sea cave and Elephant Rock": "월평 해식동굴·코끼리바위",
    "food and drinks included onboard": "선상 무료 음식·음료",
    "fishing experience for first-timers": "초보 낚시 체험",
    "life-jacket rule and onboard safety": "구명조끼·선상 안전",
    "hotel and premium travel partnerships": "호텔·프리미엄 제휴",
    "corporate and MICE group use": "기업·단체(MICE) 이용",
    "solo traveler calm-hour itinerary": "혼자 여행 조용한 코스",
    "couple and honeymoon photo route": "커플·허니문 포토 코스",
    "multi-generation family travel": "3대 가족 여행",
    "winter Jeju sailing comfort": "겨울 제주 세일링",
    "spring light and clear coastline": "봄 햇살·맑은 해안선",
    "summer evening sea breeze": "여름 저녁 바닷바람",
    "autumn shoulder-season sailing": "가을 세일링",
    "luxury daytime tour vs sunset tour": "럭셔리 데이타임 vs 선셋",
    "what to wear on a Jeju yacht": "요트 복장 팁",
    "phone photography on a moving yacht": "요트 위 폰 사진 팁",
    "one-hour itinerary around Seogwipo": "서귀포 1시간 코스",
    "why Daepo Port works for yacht tours": "대포항이 좋은 이유",
    "what first-time Korea travelers should know": "한국 첫 방문 필수정보",
    "premium but practical Jeju activity": "실속 프리미엄 액티비티",
    "quiet travel instead of crowded sightseeing": "조용한 여행",
    "brand-certified catamaran story": "브랜드 인증 쌍동선",
    "licensed captain and engineer operations": "면허 선장·기관사",
    "clear pricing and promotion notes": "명확한 가격·프로모션",
    "partner-platform booking comparison": "예약 플랫폼 비교",
}
AUD_KO = {
    "first-time Jeju visitors": "제주 첫방문", "Japan travelers": "일본 여행객",
    "Chinese-speaking travelers": "중화권 여행객", "families with children": "아이동반 가족",
    "couples and honeymooners": "커플·허니문", "corporate groups": "기업·단체",
    "solo travelers": "혼자 여행", "premium hotel guests": "고급호텔 투숙객",
}
FOCUS_KO = {"route": "동선", "safety": "안전", "price": "가격", "food": "음식",
            "photos": "사진", "booking": "예약", "season": "시즌", "ship": "선박"}


def plan(days_limit, end=END):
    hist = R.load_history()
    fake = copy.deepcopy(hist)
    rows, d, n = [], datetime.now().date(), 0
    wrong_season = 0
    while d <= end and n < days_limit:
        lang = R.LANGS_CYCLE[d.toordinal() % 3]
        key, angle, aud, focus = R.generated_combo(lang, fake, when=d)
        fake.setdefault("selected", []).append({"key": key})
        if not R._season_ok(angle, d.month):
            wrong_season += 1
        rows.append((d, lang, angle, aud, focus))
        d += timedelta(days=1)
        n += 1
    return rows, wrong_season


def full_count(end=END):
    """오늘~12/31 전체 무중복 가능 여부 + 계절오류 카운트."""
    hist = R.load_history()
    fake = copy.deepcopy(hist)
    d, n, wrong, seen = datetime.now().date(), 0, 0, set()
    dup = 0
    while d <= end:
        lang = R.LANGS_CYCLE[d.toordinal() % 3]
        key, angle, aud, focus = R.generated_combo(lang, fake, when=d)
        if key in seen:
            dup += 1
        seen.add(key)
        fake.setdefault("selected", []).append({"key": key})
        if not R._season_ok(angle, d.month):
            wrong += 1
        d += timedelta(days=1)
        n += 1
    return n, dup, wrong


def tg(msg):
    t, c = os.environ.get("TG_TOKEN"), os.environ.get("TG_CHAT")
    if not (t and c):
        print("(TG 미설정 — 전송 생략)")
        return
    try:
        urllib.request.urlopen(urllib.request.Request(
            f"https://api.telegram.org/bot{t}/sendMessage",
            data=urllib.parse.urlencode({"chat_id": c, "text": msg[:4000]}).encode()), timeout=10)
        print("TG 전송 OK")
    except Exception as e:
        print(f"TG 전송 실패: {e}")


def main():
    rows, _ = plan(DAYS)
    total, dup, wrong = full_count()
    lines = [f"📅 외국블로그 앞으로 {DAYS}일 발행 예정 (자동·무중복)"]
    for d, lang, angle, aud, focus in rows:
        a = ANGLE_KO.get(angle, angle)
        au = AUD_KO.get(aud, aud)
        fo = FOCUS_KO.get(focus, focus)
        lines.append(f"{d.strftime('%m/%d')} {FLAG.get(lang, lang)} · {a} · {au} · {fo}")
    lines.append("")
    lines.append(f"→ 12/31까지 {total}편 전부 다른 주제 (중복 {dup}건 · 철지난주제 {wrong}건)")
    lines.append("맥 꺼져도 GitHub 클라우드가 매일 자동 발행 ☁️")
    msg = "\n".join(lines)
    print(msg)

    # repo 파일로도 저장 (항상 최신 일정 확인 가능)
    out = os.path.join(REPO, "blog_queue", "UPCOMING_SCHEDULE.md")
    try:
        with open(out, "w", encoding="utf-8") as f:
            f.write("# 외국블로그 발행 예정 일정 (자동 갱신)\n\n")
            f.write(f"_업데이트: {datetime.now().isoformat(timespec='minutes')}_\n\n")
            f.write(f"- 12/31까지 **{total}편**, 주제중복 **{dup}건**, 철지난주제 **{wrong}건**\n\n")
            f.write("| 날짜 | 언어 | 주제 | 대상 | 포커스 |\n|---|---|---|---|---|\n")
            for d, lang, angle, aud, focus in rows:
                f.write(f"| {d.strftime('%m/%d')} | {FLAG.get(lang, lang)} | "
                        f"{ANGLE_KO.get(angle, angle)} | {AUD_KO.get(aud, aud)} | {FOCUS_KO.get(focus, focus)} |\n")
        print(f"저장: {out}")
    except Exception as e:
        print(f"파일 저장 실패: {e}")

    if os.environ.get("PREVIEW_SEND_TG") == "1":
        tg(msg)


if __name__ == "__main__":
    main()
