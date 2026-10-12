#!/usr/bin/env python3
"""
Load the pulled reviews, check them, and report coverage against Play's own counts.

    python -m ratings.clean

Other stages import `load_reviews` / `load_ios_reviews` from here, so every
stage works from the same checked rows.
"""
import csv
import json
import os
import sys
from datetime import datetime

from ratings.pull import TIME_FMT, load_config, reviews_path


def load_reviews(cfg, app_id):
    """Rows for one app, newest first, with typed fields added:
    score (int), dt (datetime), words (int)."""
    path = reviews_path(cfg["data_dir"], app_id)
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["score"] = int(r["score"])
        r["thumbsUpCount"] = int(r["thumbsUpCount"] or 0)
        r["dt"] = datetime.strptime(r["at"], TIME_FMT)
        r["words"] = len(r["content"].split())
        r["source"] = "play"
    rows.sort(key=lambda r: r["dt"], reverse=True)
    return rows


def load_ios_reviews(cfg):
    path = os.path.join(cfg["data_dir"], "ios_reviews.csv")
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["score"] = int(r["score"])
        r["dt"] = datetime.strptime(r["at"], TIME_FMT)
        # the App Store has a title and a body; reviewers often put the complaint in the title
        r["content"] = f"{r['title']} :: {r['content']}" if r["title"] else r["content"]
        r["words"] = len(r["content"].split())
        r["source"] = "ios"
    rows.sort(key=lambda r: r["dt"], reverse=True)
    return rows


def latest_snapshots(cfg, filename="snapshots.jsonl", key="app_id"):
    """Most recent Play-page snapshot per app."""
    path = os.path.join(cfg["data_dir"], filename)
    latest = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    snap = json.loads(line)
                    latest[snap[key]] = snap  # file is in time order, so the last one wins
    return latest


def check(rows):
    """Return a list of problems found in the rows (empty list = clean)."""
    problems = []
    ids = [r["reviewId"] for r in rows]
    if len(ids) != len(set(ids)):
        problems.append(f"{len(ids) - len(set(ids))} duplicate reviewIds")
    if any(not r["reviewId"] for r in rows):
        problems.append("rows with an empty reviewId")
    bad_score = sum(1 for r in rows if r["score"] not in (1, 2, 3, 4, 5))
    if bad_score:
        problems.append(f"{bad_score} rows with a score outside 1-5")
    future = sum(1 for r in rows if r["dt"] > datetime.now())
    if future:
        problems.append(f"{future} rows dated in the future")
    return problems


def coverage(rows, snapshot):
    """How much of what Play reports did we actually pull?"""
    out = {"pulled": len(rows), "empty_text": sum(1 for r in rows if not r["content"])}
    if rows:
        out["first"] = rows[-1]["at"][:10]
        out["last"] = rows[0]["at"][:10]
    if snapshot and snapshot.get("reviews"):
        out["play_reviews"] = snapshot["reviews"]
        out["coverage_pct"] = round(100 * len(rows) / snapshot["reviews"], 1)
    return out


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    cfg = load_config()
    snaps = latest_snapshots(cfg)
    ok = True
    for app in cfg["apps"]:
        rows = load_reviews(cfg, app["id"])
        problems = check(rows)
        cov = coverage(rows, snaps.get(app["id"]))
        print(f"{app['id']}: {cov}")
        for p in problems:
            ok = False
            print(f"  PROBLEM: {p}")
    ios = load_ios_reviews(cfg)
    if ios:
        print(f"ios: {len(ios)} reviews, {ios[-1]['at'][:10]} -> {ios[0]['at'][:10]}")
    print("clean: OK" if ok else "clean: PROBLEMS FOUND")
    return ok


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
