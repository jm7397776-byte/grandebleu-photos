#!/usr/bin/env python3
"""Pick the next Pinterest post inside GitHub Actions.

This mirrors the local picker but has no dependency on the user's Mac.
It updates pinterest_today.json, pinterest_today.jpg, pinterest_history.json,
and pinterest_run_pool.json in this repository.
"""

import argparse
import datetime as dt
import json
import random
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HISTORY_LIMIT = 60
LINK = (
    "https://www.klook.com/en-US/activity/170600-jeju-grandebleu-sunset-yacht-experience/"
    "?utm_source=pinterest&utm_medium=social&utm_campaign=jeju_yacht"
)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def now_kst():
    return (dt.datetime.utcnow() + dt.timedelta(hours=9)).replace(microsecond=0).isoformat() + "+09:00"


def load_history():
    path = ROOT / "pinterest_history.json"
    if not path.exists():
        return []
    try:
        payload = read_json(path)
        return payload.get("entries", []) if isinstance(payload, dict) else []
    except Exception:
        return []


def choose_not_recent(items, recent_values, key=None):
    fresh = [
        item for item in items
        if (item.get(key) if key else item) not in recent_values
    ]
    return random.choice(fresh or items)


def build_run_pool(photos, titles, history):
    recent_files = {entry.get("file") for entry in history[-HISTORY_LIMIT:]}
    jpg_photos = [
        p for p in photos["photos"]
        if str(p.get("file", "")).lower().endswith((".jpg", ".jpeg"))
        and p.get("file") not in recent_files
    ]
    old_jpg_photos = [
        p for p in photos["photos"]
        if str(p.get("file", "")).lower().endswith((".jpg", ".jpeg"))
        and p.get("file") in recent_files
    ]
    random.shuffle(jpg_photos)
    random.shuffle(old_jpg_photos)
    jpg_photos.extend(old_jpg_photos)

    title_pool = list(titles["titles"]["en"])
    desc_pool = list(titles["descriptions"]["en"])
    random.shuffle(title_pool)
    random.shuffle(desc_pool)

    posts = []
    for idx, photo in enumerate(jpg_photos):
        posts.append({
            "url": photo["url"],
            "file": photo.get("file", ""),
            "category": photo.get("category", ""),
            "title": title_pool[idx % len(title_pool)],
            "description": desc_pool[idx % len(desc_pool)],
            "link": LINK,
        })

    return {
        "generated_at": now_kst(),
        "strategy": "Full shuffled JPG pool generated in GitHub Actions so Pinterest automation works even when the Mac is off.",
        "pool_size": len(posts),
        "posts": posts,
    }


def pick():
    photos = read_json(ROOT / "photos_index.json")
    titles = read_json(ROOT / "titles_pool.json")
    history = load_history()
    recent = history[-HISTORY_LIMIT:]
    recent_files = {entry.get("file") for entry in recent}
    recent_titles = {entry.get("title") for entry in recent[-20:]}
    recent_descriptions = {entry.get("description") for entry in recent[-20:]}

    jpg_photos = [
        p for p in photos["photos"]
        if str(p.get("file", "")).lower().endswith((".jpg", ".jpeg"))
    ]
    photo = choose_not_recent(jpg_photos or photos["photos"], recent_files, key="file")
    title = choose_not_recent(titles["titles"]["en"], recent_titles)
    description = choose_not_recent(titles["descriptions"]["en"], recent_descriptions)

    data = {
        "url": photo["url"],
        "file": photo.get("file", ""),
        "category": photo.get("category", ""),
        "title": title,
        "description": description,
        "link": LINK,
        "board_id": "875176208778070248",
        "generated_at": now_kst(),
    }
    return data, photos, titles, history


def update_files(dry_run=False):
    data, photos, titles, history = pick()
    source_file = ROOT / data["file"]
    if not source_file.exists():
        raise FileNotFoundError(f"Selected photo missing in repo: {source_file}")

    history.append({
        "generated_at": data["generated_at"],
        "file": data.get("file", ""),
        "category": data.get("category", ""),
        "title": data.get("title", ""),
        "description": data.get("description", ""),
    })
    run_pool = build_run_pool(photos, titles, history)

    if not dry_run:
        write_json(ROOT / "pinterest_today.json", data)
        shutil.copyfile(source_file, ROOT / "pinterest_today.jpg")
        write_json(ROOT / "pinterest_history.json", {"entries": history[-500:]})
        write_json(ROOT / "pinterest_run_pool.json", run_pool)

    return data, run_pool


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    data, run_pool = update_files(dry_run=args.dry_run)
    print(f"selected_file={data['file']}")
    print(f"title={data['title']}")
    print(f"pool_size={run_pool['pool_size']}")
    print(f"dry_run={args.dry_run}")


if __name__ == "__main__":
    main()
