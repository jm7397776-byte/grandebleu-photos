#!/usr/bin/env python3
"""Render one Blogger-ready post into blog_today.json for Make.com.

This does not call the Blogger API. Make.com reads blog_today.json and publishes it.
"""
import html as html_lib
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
RECENT_IMAGE_WINDOW = 7

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
    body = pub.sanitize_body(post["body"], klook)
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
    stamp = datetime.now().strftime("%m%d-%H%M")
    title = f"[{meta['label']}] {post['title'][:160]}" if lang != "en" else post["title"][:180]
    urls = image_urls(body_html)
    return {
        "title": f"{title} | {stamp}",
        "content": body_html + cta,
        "labels": labels,
        "link": "https://jejugrandebleuyacht.blogspot.com/",
        "lang": lang,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "image_urls": urls,
        "image_count": len(urls),
    }


def main():
    lang = LANGS_CYCLE[datetime.now().weekday() % 3]
    path, key, post = pick_post(lang)
    if not post:
        raise SystemExit(f"{lang}: no unpublished post candidate")
    history = load_history()
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
    })
    history["selected"] = selected[-90:]
    save_history(history)
    print(f"blog_today.json rendered: {lang} {data['title']}")
    print(f"html={len(data['content'])} text={visible_text_len(data['content'])} images={data['image_count']}")


if __name__ == "__main__":
    main()
