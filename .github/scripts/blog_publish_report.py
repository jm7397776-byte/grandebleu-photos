#!/usr/bin/env python3
"""외국블로그 매일 발행 보고 — blog_publish_today_state.json의 오늘 발행을 텔레그램 보고.
클릭 가능한 실제 blogspot 글 URL 포함. 매일 14:30 KST (발행 14:13 후).
사용자 요청 2026-06-01: 매일 보고 + 클릭 가능한 링크."""
import json
import urllib.request
import urllib.parse
import datetime
import sys
from pathlib import Path

STATE_URL = 'https://raw.githubusercontent.com/jm7397776-byte/grandebleu-photos/main/blog_publish_today_state.json'
CREDS = Path.home() / 'second-brain' / '.env' / 'credentials.env'
LANG_NAME = {'en': '영어', 'ja': '일본어', 'zh-CN': '중국어', 'zh': '중국어', 'ko': '한국어'}


def _creds():
    env = {}
    if CREDS.exists():
        for line in CREDS.read_text(encoding='utf-8').splitlines():
            if '=' in line and not line.strip().startswith('#'):
                k, _, v = line.partition('=')
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def tg(msg):
    import os
    e = _creds()
    t = os.environ.get('TG_TOKEN') or os.environ.get('SECOND_BRAIN_TG_TOKEN') or e.get('SECOND_BRAIN_TG_TOKEN')
    c = os.environ.get('TG_CHAT') or os.environ.get('SECOND_BRAIN_TG_CHAT_ID') or e.get('SECOND_BRAIN_TG_CHAT_ID')
    if not (t and c):
        return
    try:
        urllib.request.urlopen(
            f'https://api.telegram.org/bot{t}/sendMessage',
            data=urllib.parse.urlencode({'chat_id': c, 'text': msg}).encode(), timeout=10)
    except Exception:
        pass


def main():
    today = datetime.datetime.now().strftime('%Y-%m-%d')
    try:
        with urllib.request.urlopen(STATE_URL, timeout=30) as r:
            d = json.loads(r.read())
    except Exception as ex:
        tg(f'🚨 외국블로그 감시: 발행기록을 못 읽었어요 — {ex}')
        sys.exit(1)

    pub = d.get('published', [])
    # 오늘(KST) 또는 어제 UTC 발행 모두 잡기 위해 published_at/generated_at 날짜로 매칭
    today_posts = [p for p in pub
                   if (p.get('published_at', '') or p.get('generated_at', ''))[:10] == today]

    if not today_posts:
        # 이 보고의 목적은 '발행(14:13 KST) 후' 확인. 오전 catch-up dispatch 로 미리 돌면
        # 아직 발행 전이라 매일 허위 '실패' 경보가 났음 → 14:00 KST 이전엔 조용히 통과 (2026-07-14)
        kst_hour = (datetime.datetime.utcnow() + datetime.timedelta(hours=9)).hour
        if kst_hour < 14:
            print(f'not yet published today ({today}) — before 14:00 KST, skip report')
            return
        tg(f'🚨 외국블로그 경보: 오늘({today}) 발행 기록이 없어요.\n'
           f'github.com/jm7397776-byte/grandebleu-photos/actions 를 확인해 주세요.')
        sys.exit(1)

    p = today_posts[-1]
    lang = LANG_NAME.get(p.get('lang', ''), p.get('lang', ''))
    tg(f"""📝 오늘 외국블로그 발행 완료 ({lang})
{p.get('title', '')}
🔗 글 보기(클릭): {p.get('url', '')}""")
    print('OK + reported:', p.get('lang'), '/', p.get('url'))


if __name__ == '__main__':
    main()
