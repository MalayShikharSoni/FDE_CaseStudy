#!/usr/bin/env python3
"""
Pull the iOS App Store rating and recent reviews for the same app, as a cross-check.

    python -m ratings.ios

The same employees, cabs and operations sit behind both apps. So a complaint seen
on both stores points at the product or operations, and one seen only on Android
points at the Android app.

Apple's public review feed only goes back 10 pages of 50 (the ~500 most recent
reviews per country), so this is a sample for comparison, not a full history.

Outputs (in data_dir):
    ios_reviews.csv       one row per review (no reviewer names)
    ios_snapshots.jsonl   one line per run: average rating, rating count, version
"""
import csv
import json
import os
import sys
import time
import urllib.request
from datetime import datetime

from ratings.pull import load_config, one_line

FIELDS = ["reviewId", "score", "at", "title", "content", "appVersion"]
MAX_PAGES = 10


def get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def take_snapshot(ios_id, country):
    info = get_json(f"https://itunes.apple.com/lookup?id={ios_id}&country={country}")["results"][0]
    return {
        "ios_id": ios_id,
        "title": info.get("trackName"),
        "score": info.get("averageUserRating"),
        "ratings": info.get("userRatingCount"),
        "version": info.get("version"),
        "updated": info.get("currentVersionReleaseDate"),
        "pulled_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def fetch_reviews(ios_id, country):
    rows = {}
    for page in range(1, MAX_PAGES + 1):
        url = (f"https://itunes.apple.com/{country}/rss/customerreviews/"
               f"page={page}/id={ios_id}/sortby=mostrecent/json")
        try:
            entries = get_json(url)["feed"].get("entry", [])
        except Exception as exc:
            print(f"  page {page}: {type(exc).__name__}: {exc}")
            break
        if isinstance(entries, dict):  # a single review comes back as an object, not a list
            entries = [entries]
        if not entries:
            break
        for e in entries:
            rid = e["id"]["label"]
            rows[rid] = {
                "reviewId": rid,
                "score": int(e["im:rating"]["label"]),
                "at": e["updated"]["label"][:19].replace("T", " "),
                "title": one_line(e["title"]["label"]),
                "content": one_line(e["content"]["label"]),
                "appVersion": e["im:version"]["label"],
            }
        time.sleep(1)
    return rows


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    cfg = load_config()
    data_dir = cfg["data_dir"]
    os.makedirs(data_dir, exist_ok=True)
    for app in cfg["apps"]:
        ios_id = app.get("ios_id")
        if not ios_id:
            continue
        snap = take_snapshot(ios_id, cfg["country"])
        with open(os.path.join(data_dir, "ios_snapshots.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(snap) + "\n")
        print(f"iOS {snap['title']}: score {snap['score']:.3f} from {snap['ratings']} ratings, v{snap['version']}")

        path = os.path.join(data_dir, "ios_reviews.csv")
        rows = {}
        if os.path.exists(path):  # the feed only shows the latest ~500, so keep what we already have
            with open(path, newline="", encoding="utf-8-sig") as f:
                rows = {r["reviewId"]: r for r in csv.DictReader(f)}
        before = len(rows)
        rows.update(fetch_reviews(ios_id, cfg["country"]))
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            w.writeheader()
            w.writerows(sorted(rows.values(), key=lambda r: r["at"], reverse=True))
        print(f"  {len(rows) - before} new reviews, {len(rows)} total -> {path}")


if __name__ == "__main__":
    main()
