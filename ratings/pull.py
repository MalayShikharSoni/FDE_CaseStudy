#!/usr/bin/env python3
"""
Pull public Google Play reviews + an app snapshot for every app in config.yaml.

    python -m ratings.pull --test    connectivity check: 50 reviews, writes nothing
    python -m ratings.pull           first run = full backfill, later runs = incremental
    python -m ratings.pull --full    force a full backfill

Outputs (in data_dir):
    reviews_<app_id>.csv    one row per unique review
    snapshots.jsonl         one line per app per run: score, rating histogram, installs, version
    pull_log.txt            what happened, including any incomplete sweeps

Notes:
  * Reviewer names / avatars are deliberately NOT saved (not needed, avoids personal data).
  * The library swallows errors and just stops, so this script retries and logs
    suspicious early stops as INCOMPLETE. Check pull_log.txt after the run.
  * Be polite: ~1-2 s between requests. Ctrl+C is safe, whatever was collected is saved.
"""
import argparse
import csv
import json
import os
import random
import re
import sys
import time
from datetime import datetime, timedelta

import google_play_scraper as gps
import yaml

BATCH = 200
FIELDS = [
    "reviewId", "app_id", "lang", "sweep", "score", "at", "content",
    "thumbsUpCount", "reviewCreatedVersion", "appVersion",
    "replyContent", "repliedAt",
]
SNAPSHOT_KEYS = (
    "title", "score", "ratings", "reviews", "histogram", "installs",
    "realInstalls", "version", "updated", "lastUpdatedOn", "released",
)
TIME_FMT = "%Y-%m-%d %H:%M:%S"

LOG_LINES = []


def log(msg):
    line = f"[{datetime.now().strftime(TIME_FMT)}] {msg}"
    print(line, flush=True)
    LOG_LINES.append(line)


def polite_sleep():
    time.sleep(1.0 + random.random())


# Details a reviewer may have typed that identify a person. The reviews are public
# on Play, but this project republishes them, so these are removed before saving.
REDACTIONS = [
    (re.compile(r"\b[A-Z]{2}[ -]?\d{1,2}[ -]?[A-Z]{1,3}[ -]?\d{4}\b"), "[vehicle number removed]"),
    (re.compile(r"(?<!\d)(?:\+?91[ -]?)?\d{10}(?!\d)"), "[phone number removed]"),
    (re.compile(r"\b[\w.+-]+@(?!moveinsync\.com)[\w-]+\.[\w.-]+\b", re.I), "[email removed]"),
]


def redact(text):
    for pattern, replacement in REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


def one_line(text):
    """Review text as stored: on one line, with identifying details removed."""
    return redact((text or "").replace("\r", " ").replace("\n", " ").strip())


def fetch_page(app_id, lang, country, score, token, retries=4):
    """One page of reviews, retrying silent failures with backoff.
    Returns (batch, next_token, ok). ok=False means we gave up after retries."""
    for attempt in range(retries):
        try:
            if token is None:
                batch, nxt = gps.reviews(
                    app_id, lang=lang, country=country, sort=gps.Sort.NEWEST,
                    count=BATCH, filter_score_with=score,
                )
            else:
                batch, nxt = gps.reviews(app_id, continuation_token=token, count=BATCH)
            if batch:
                return batch, nxt, True
            # empty page: either true end or a silent failure -> retry before believing it
        except Exception as exc:  # network, parse, rate limit
            log(f"  warning: {type(exc).__name__}: {exc}")
        time.sleep(3 * (attempt + 1) + random.random())
    return [], None, False


def sweep(app_id, lang, country, score, since, label, store, retries=4):
    """Page newest->oldest until reviews are older than `since`.
    Adds/updates rows in `store` (dict keyed by reviewId).
    Returns (new_count, complete). complete=False means the sweep stopped early."""
    token = None
    pages = 0
    added = 0
    complete = True
    while True:
        batch, nxt, ok = fetch_page(app_id, lang, country, score, token, retries)
        pages += 1
        if not batch:
            if pages == 1:
                log(f"  {label}/{lang}: no reviews returned (none in this language, or blocked)")
            else:
                complete = False  # empty page mid-sweep after retries = suspicious early stop
            break
        reached_cutoff = False
        for r in batch:
            if r["at"] < since:
                reached_cutoff = True
                continue
            rid = r["reviewId"]
            is_new = rid not in store
            row = {
                "reviewId": rid, "app_id": app_id, "lang": lang, "sweep": label,
                "score": r["score"], "at": r["at"].strftime(TIME_FMT),
                "content": one_line(r["content"]),
                "thumbsUpCount": r["thumbsUpCount"],
                "reviewCreatedVersion": r.get("reviewCreatedVersion") or "",
                "appVersion": r.get("appVersion") or "",
                "replyContent": one_line(r.get("replyContent")),
                "repliedAt": r["repliedAt"].strftime(TIME_FMT) if r.get("repliedAt") else "",
            }
            if not is_new:
                # seen before: refresh it (edits, new replies) but keep how we first found it
                row["sweep"] = store[rid]["sweep"]
                row["lang"] = store[rid]["lang"]
            store[rid] = row
            added += is_new
        if reached_cutoff:
            break
        if nxt is None or getattr(nxt, "token", True) is None:
            break  # genuine end of list
        token = nxt
        if pages % 10 == 0:
            log(f"  {label}/{lang}: {pages} pages, {added} new so far")
        polite_sleep()
    log(f"  {label}/{lang}: done, {added} new reviews in {pages} pages"
        + ("" if complete else "  <-- INCOMPLETE (stopped early, re-run later)"))
    return added, complete


def reviews_path(data_dir, app_id):
    return os.path.join(data_dir, f"reviews_{app_id}.csv")


def load_csv(path):
    if not os.path.exists(path):
        return {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        store = {}
        for row in csv.DictReader(f):
            row["score"] = int(row["score"])
            row["thumbsUpCount"] = int(row["thumbsUpCount"] or 0)
            store[row["reviewId"]] = row
        return store


def write_csv(path, store):
    rows = sorted(store.values(), key=lambda r: r["at"], reverse=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    return rows


def take_snapshot(app_id, country, data_dir):
    """Save what the Play page itself says right now. The histogram covers ALL
    ratings (not just written reviews), so run-over-run differences give the
    true star mix of new ratings."""
    info = gps.app(app_id, lang="en", country=country)
    snap = {k: info.get(k) for k in SNAPSHOT_KEYS}
    snap["app_id"] = app_id
    snap["pulled_at"] = datetime.now().strftime(TIME_FMT)
    with open(os.path.join(data_dir, "snapshots.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(snap, default=str) + "\n")
    return snap


def take_country_snapshots(app_id, cfg):
    """Play shows a different rating in each country's store, so record what each
    listing says. Only the score and the star percentages are usable here: Play
    reports one global ratings count everywhere, and the review list is the same
    in every country, so reviews cannot be split by country."""
    rows = []
    for country in cfg.get("countries", []):
        try:
            info = gps.app(app_id, lang="en", country=country)
        except Exception as exc:
            log(f"  country {country}: {type(exc).__name__}: {exc}")
            continue
        hist = info.get("histogram") or []
        total = sum(hist)
        if not info.get("score") or not total:
            continue
        rows.append({
            "app_id": app_id, "country": country, "score": info["score"],
            "pct_by_star": [round(100 * h / total, 1) for h in hist],
            "country_reviews": info.get("reviews"),
            "pulled_at": datetime.now().strftime(TIME_FMT),
        })
        polite_sleep()
    with open(os.path.join(cfg["data_dir"], "country_snapshots.jsonl"), "a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
    log("  by country: " + ", ".join(f"{r['country']} {r['score']:.2f}" for r in rows))
    return rows


def pull_app(app_id, cfg, full=False):
    """Pull one app. Returns a summary dict that run.py prints."""
    data_dir = cfg["data_dir"]
    path = reviews_path(data_dir, app_id)
    store = load_csv(path)
    before = len(store)
    result = {"app_id": app_id, "reachable": True, "complete": True, "new": 0}

    log(f"=== {app_id} ===")
    try:
        snap = take_snapshot(app_id, cfg["country"], data_dir)
        log(f"  Play page: score={snap['score']:.3f} ratings={snap['ratings']} "
            f"reviews={snap['reviews']} histogram={snap['histogram']} version={snap['version']}")
    except Exception as exc:
        log(f"  could not read app snapshot ({type(exc).__name__}: {exc}); using cached reviews")
        result["reachable"] = False
        result["total"] = before
        return result

    backfill = full or not store
    if backfill:
        since = datetime.strptime(cfg["since"], "%Y-%m-%d")
        neg_since = datetime.strptime(cfg["neg_since"], "%Y-%m-%d")
    else:
        newest = max(datetime.strptime(r["at"], TIME_FMT) for r in store.values())
        since = newest - timedelta(days=cfg["overlap_days"])
        log(f"  incremental: re-reading from {since:%Y-%m-%d}")

    # an empty page is retried before it is believed; a short incremental run needs fewer retries
    retries = 4 if backfill else 2
    try:
        for lang in cfg["langs"]:
            _, ok = sweep(app_id, lang, cfg["country"], None, since, "all", store, retries)
            result["complete"] &= ok
            if backfill:
                for sc in (1, 2):
                    _, ok = sweep(app_id, lang, cfg["country"], sc, neg_since, f"neg{sc}", store)
                    result["complete"] &= ok
    finally:
        write_csv(path, store)
    result["new"] = len(store) - before
    result["total"] = len(store)
    log(f"  {app_id}: {result['new']} new, {result['total']} total")
    return result


def run_test(cfg):
    for app in cfg["apps"]:
        app_id = app["id"]
        try:
            info = gps.app(app_id, lang="en", country=cfg["country"])
        except Exception as exc:
            print(f"{app_id}: could not read app page ({type(exc).__name__}: {exc})")
            continue
        print(f"{app_id} | {info['title']} | by {info.get('developer')} | score {info['score']:.3f} | "
              f"ratings {info['ratings']} | reviews {info['reviews']} | "
              f"histogram {info['histogram']} | version {info['version']}")
        batch, _ = gps.reviews(app_id, lang="en", country=cfg["country"],
                               sort=gps.Sort.NEWEST, count=50)
        print(f"  fetched {len(batch)} reviews")
        for r in batch[:5]:
            print("  ", r["at"], r["score"], one_line(r["content"])[:100])
        if not batch:
            print("  0 reviews returned: blocked/rate-limited or library broken.")


def load_config(path="config.yaml"):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--full", action="store_true", help="force a full backfill")
    ap.add_argument("--test", action="store_true", help="fetch 50 reviews per app, print, write nothing")
    args = ap.parse_args(argv)

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    cfg = load_config(args.config)
    if args.test:
        run_test(cfg)
        return []

    os.makedirs(cfg["data_dir"], exist_ok=True)
    results = []
    try:
        for app in cfg["apps"]:
            result = pull_app(app["id"], cfg, full=args.full)
            results.append(result)
            if app["role"] == "employee" and result["reachable"]:
                take_country_snapshots(app["id"], cfg)
    except KeyboardInterrupt:
        log("Interrupted by user; partial results were saved.")
    finally:
        with open(os.path.join(cfg["data_dir"], "pull_log.txt"), "a", encoding="utf-8") as f:
            f.write("\n".join(LOG_LINES) + "\n")
    return results


if __name__ == "__main__":
    main()
