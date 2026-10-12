#!/usr/bin/env python3
"""
Compute every number the report uses and write them to docs/data/metrics.json.

    python -m ratings.metrics

The report, the weekly digest, the backlog and the manual-review list are all built from that one file,
so a figure cannot differ between them.

Two kinds of numbers, kept apart on purpose:
  * RATINGS  - every star rating on the Play page (the histogram). Most have no text.
  * REVIEWS  - the ratings that came with written text. A smaller, more negative group.
The store rating is made of ratings. Reviews are only used to explain why low
ratings happen.
"""
import json
import math
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from statistics import median

import yaml

from ratings import validate
from ratings.classify import (employee_app, in_scope, load_labels, load_taxonomy, needs_manual_review,
                              window_start)
from ratings.clean import latest_snapshots, load_ios_reviews, load_reviews
from ratings.pull import TIME_FMT, load_config, now_utc

OUT = os.path.join("docs", "data", "metrics.json")
MIN_VERSION_REVIEWS = 30   # versions with fewer reviews are too small to judge
NO_DETAIL = ("generic_negative", "not_a_complaint")
NEW_PREFIX = "new:"        # id prefix of a theme created automatically from a recurring note
QUOTES_PER_THEME = 3
REPLY_QUEUE_SIZE = 20
HERE = os.path.dirname(os.path.abspath(__file__))


def pct(part, whole):
    return round(100 * part / whole, 1) if whole else 0.0


def review_stats(rows):
    n = len(rows)
    if not n:
        return {"n": 0}
    return {
        "n": n,
        "avg": round(sum(r["score"] for r in rows) / n, 2),
        "pct_1": pct(sum(r["score"] == 1 for r in rows), n),
        "pct_low": pct(sum(r["score"] <= 2 for r in rows), n),
        "pct_5": pct(sum(r["score"] == 5 for r in rows), n),
    }


def country_table(cfg, app_id):
    """Latest rating shown in each country's Play listing, lowest first."""
    latest = {}
    path = os.path.join(cfg["data_dir"], "country_snapshots.jsonl")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    snap = json.loads(line)
                    if snap["app_id"] == app_id:
                        latest[snap["country"]] = snap
    table = [{"country": s["country"], "score": round(s["score"], 2),
              "pct_1": s["pct_by_star"][0], "pct_low": round(s["pct_by_star"][0] + s["pct_by_star"][1], 1),
              "country_reviews": s["country_reviews"], "pulled_at": s["pulled_at"]}
             for s in latest.values()]
    table.sort(key=lambda c: c["score"])
    return table


def histogram_stats(hist):
    """Figures from the Play rating histogram [1-star, ..., 5-star counts].

    Play scales each country's star percentages up to the global ratings count,
    so the individual counts are not real head-counts. Only the proportions
    between them are used here."""
    n = sum(hist)
    total = sum((i + 1) * h for i, h in enumerate(hist))
    low = hist[0] + hist[1]
    return {
        "ratings": n,
        "avg": round(total / n, 3),
        "pct_by_star": [pct(h, n) for h in hist],
        "low_ratings": low,
        "low_avg": round((hist[0] + 2 * hist[1]) / low, 3) if low else None,
        # what the average would be if the 1-star ratings were simply not there
        "avg_without_1_star": round((total - hist[0]) / (n - hist[0]), 3),
    }


def rating_lift(hist, share, to_star=4):
    """Estimated change in the average rating if `share` (0-1) of the 1-2 star
    ratings had been `to_star` instead.

        lift = share * low_ratings * (to_star - low_avg) / all_ratings

    This is a steady-state estimate: it says where the average settles if the
    mix of incoming ratings changes like this, not how fast it gets there. It
    assumes low-star raters who write nothing have the same mix of complaints
    as those who write a review.
    """
    h = histogram_stats(hist)
    if not h["low_ratings"]:
        return 0.0
    return round(share * h["low_ratings"] * (to_star - h["low_avg"]) / h["ratings"], 3)


def wilson_low(k, n, z=1.96):
    """Lower end of the 95% confidence interval for a share k/n."""
    if not n:
        return 0.0
    p = k / n
    centre = p + z * z / (2 * n)
    spread = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (centre - spread) / (1 + z * z / n)


def monthly_trend(rows, months=24):
    by_month = defaultdict(list)
    for r in rows:
        by_month[r["at"][:7]].append(r)
    return [{"month": m, **review_stats(by_month[m])} for m in sorted(by_month)[-months:]]


def version_table(rows, labels, baseline_low_pct):
    """One line per app version: how its reviews compare with the usual share of
    1-2 star reviews. `worse` is only set when the gap is beyond what chance
    would explain (95% interval), so a small unlucky sample is not flagged."""
    by_version = defaultdict(list)
    for r in rows:
        if r["reviewCreatedVersion"]:
            by_version[r["reviewCreatedVersion"]].append(r)
    table = []
    for version, rs in by_version.items():
        if len(rs) < MIN_VERSION_REVIEWS:
            continue
        low = [r for r in rs if r["score"] <= 2]
        themes = Counter(labels[r["reviewId"]]["theme"] for r in low
                         if r["reviewId"] in labels and labels[r["reviewId"]]["theme"] not in NO_DETAIL)
        table.append({
            "version": version,
            "first_seen": min(r["at"] for r in rs)[:10],
            **review_stats(rs),
            "worse": 100 * wilson_low(len(low), len(rs)) > baseline_low_pct,
            "top_themes": [t for t, _ in themes.most_common(2)],
        })
    table.sort(key=lambda v: v["first_seen"])
    return table


def reply_stats(rows):
    by_score = []
    for score in range(1, 6):
        group = [r for r in rows if r["score"] == score]
        replied = sum(1 for r in group if r["replyContent"])
        by_score.append({"score": score, "n": len(group), "replied": replied, "pct": pct(replied, len(group))})
    replied = [r for r in rows if r["replyContent"] and r["repliedAt"]]
    lags = [(datetime.strptime(r["repliedAt"], TIME_FMT) - r["dt"]).total_seconds() / 3600 for r in replied]
    low = [r for r in rows if r["score"] <= 2]
    return {
        "by_score": by_score,
        "replies": len(replied),
        "low_n": len(low),
        "low_replied": sum(1 for r in low if r["replyContent"]),
        "low_pct": pct(sum(1 for r in low if r["replyContent"]), len(low)),
        "to_positive_pct": pct(sum(1 for r in replied if r["score"] >= 4), len(replied)),
        "median_lag_hours": round(median(lags), 1) if lags else None,
    }


def pick_quotes(reviews):
    """Most representative quotes: detailed enough to act on, most-upvoted first."""
    usable = [r for r in reviews if 60 <= len(r["content"]) <= 400]
    usable.sort(key=lambda r: (-r.get("thumbsUpCount", 0), -len(r["content"])))
    return [{"reviewId": r["reviewId"], "date": r["at"][:10], "score": r["score"],
             "version": r.get("reviewCreatedVersion") or r.get("appVersion") or "",
             "text": r["content"]} for r in usable[:QUOTES_PER_THEME]]


def promoted_notes(play_low, labels, min_reviews):
    """Notes on `other` reviews that have recurred often enough to count as a theme."""
    counts = Counter(labels[r["reviewId"]].get("note") for r in play_low
                     if labels[r["reviewId"]]["theme"] == "other")
    return {note for note, n in counts.items() if note and n >= min_reviews}


def theme_key(lab, promoted):
    """The theme a review is counted under. An `other` review whose note has
    been promoted is counted under that note, as a theme created on the go."""
    if lab["theme"] == "other" and lab.get("note") in promoted:
        return NEW_PREFIX + lab["note"]
    return lab["theme"]


def theme_table(tax, play_low, ios_low, labels, hist, recent_start, promoted=frozenset()):
    """One line per theme, for Play 1-2 star reviews in the labelling window.
    Themes from taxonomy.yaml come first; themes created automatically from
    recurring notes are added and marked `provisional` until a person reviews them."""
    n = len(play_low)
    key = lambda r: theme_key(labels[r["reviewId"]], promoted)
    specific = [r for r in play_low if key(r) not in NO_DETAIL]
    recent = [r for r in play_low if r["dt"] >= recent_start]
    earlier = [r for r in play_low if r["dt"] < recent_start]
    entries = [(t["id"], t["stage"], t["definition"], False) for t in tax["themes"]]
    entries += [(NEW_PREFIX + note, "other",
                 "Created automatically because several reviews share this note. Not yet reviewed by a person.",
                 True) for note in sorted(promoted)]
    table = []
    for tid, stage, definition, provisional in entries:
        main = [r for r in play_low if key(r) == tid]
        mentions = main + [r for r in play_low if key(r) != tid and labels[r["reviewId"]]["theme2"] == tid]
        share = len(main) / n if n else 0
        table.append({
            "id": tid, "stage": stage, "definition": definition, "provisional": provisional,
            "n": len(main), "mentions": len(mentions),
            "share_pct": pct(len(main), n),
            "share_of_specific_pct": pct(len(main), len(specific)) if tid not in NO_DETAIL else None,
            "owners": dict(Counter(labels[r["reviewId"]]["owner"] for r in main)),
            "after_update": sum(labels[r["reviewId"]]["after_update"] for r in main),
            "safety": sum(labels[r["reviewId"]]["safety"] for r in main),
            "recent_share_pct": pct(sum(key(r) == tid for r in recent), len(recent)),
            "earlier_share_pct": pct(sum(key(r) == tid for r in earlier), len(earlier)),
            "ios_share_pct": pct(sum(theme_key(labels[r["reviewId"]], promoted) == tid for r in ios_low), len(ios_low)),
            "rating_lift": rating_lift(hist, share),
            "quotes": pick_quotes(main),
        })
    table.sort(key=lambda x: -x["n"])
    return table


def rollup(play_low, labels, key):
    counts = Counter(key(labels[r["reviewId"]]) for r in play_low)
    return [{"id": k, "n": v, "share_pct": pct(v, len(play_low))} for k, v in counts.most_common()]


def load_recommendations(path="recommendations.yaml"):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)["recommendations"]


def recommendation_evidence(recs, play_low, ios_low, window, labels, hist, recent_start):
    """The counts shown beside each recommendation. recommendations.yaml names
    themes and phrases; the numbers are worked out here so they cannot go stale."""
    evidence = {}
    for rec in recs:
        themes = rec.get("themes") or []
        main = [r for r in play_low if labels[r["reviewId"]]["theme"] in themes]
        share = len(main) / len(play_low) if play_low else 0
        ev = {
            "n": len(main), "share_pct": pct(len(main), len(play_low)),
            "ios_share_pct": pct(sum(labels[r["reviewId"]]["theme"] in themes for r in ios_low), len(ios_low)),
            "rating_lift": rating_lift(hist, share),
            "patterns": [], "quotes": pick_quotes(main),
        }
        for i, pat in enumerate(rec.get("patterns") or []):
            if pat.get("source") == "ios":
                pool, where = ios_low, "low-star iOS reviews"
            elif pat.get("all_scores"):
                pool, where = window, "Play reviews of any rating"
            else:
                pool, where = play_low, "low-star Play reviews"
            rx = re.compile(pat["regex"], re.I)
            hits = [r for r in pool if rx.search(r["content"])]
            ev["patterns"].append({"label": pat["label"], "n": len(hits), "of": len(pool), "where": where,
                                   "last_90": sum(r["dt"] >= recent_start for r in hits)})
            if i == rec.get("quotes_from_pattern"):
                ev["quotes"] = pick_quotes(hits)
        evidence[rec["id"]] = ev
    return evidence


def reply_queue(window, labels):
    """Newest low-star Play reviews that have no developer reply, each with a
    draft reply for its theme. The drafts are starting points to edit, not text to paste."""
    with open(os.path.join(HERE, "replies.yaml"), encoding="utf-8") as f:
        drafts = yaml.safe_load(f)
    waiting = [r for r in window if r["score"] <= 2 and not r["replyContent"] and r["reviewId"] in labels]
    queue = []
    for r in waiting[:REPLY_QUEUE_SIZE]:
        lab = labels[r["reviewId"]]
        queue.append({"reviewId": r["reviewId"], "date": r["at"][:10], "score": r["score"],
                      "version": r["reviewCreatedVersion"], "theme": lab["theme"], "safety": lab["safety"],
                      "text": r["content"][:500],
                      "draft": (drafts["safety"] if lab["safety"] else drafts["themes"][lab["theme"]]).strip()})
    return {"waiting": len(waiting), "shown": queue}


def unthemed(play_low, labels, recent_start, promoted=frozenset()):
    """Complaints that fit no theme in taxonomy.yaml, grouped by the labeller's
    note. A group marked `promoted` has recurred often enough to be counted as
    its own theme already."""
    others = [r for r in play_low if labels[r["reviewId"]]["theme"] == "other"]
    recent = [r for r in play_low if r["dt"] >= recent_start]
    groups = defaultdict(list)
    for r in others:
        groups[labels[r["reviewId"]].get("note") or "(no note)"].append(r)
    return {
        "n": len(others), "share_pct": pct(len(others), len(play_low)),
        "recent_n": sum(r["dt"] >= recent_start for r in others),
        "recent_share_pct": pct(sum(r["dt"] >= recent_start for r in others), len(recent)),
        "groups": sorted(({"note": note, "n": len(rs), "promoted": note in promoted,
                           "recent_n": sum(r["dt"] >= recent_start for r in rs),
                           "examples": [r["content"][:220] for r in rs[:2]]}
                          for note, rs in groups.items()), key=lambda g: -g["n"]),
    }


def manual_review_list(labelled, labels, promoted):
    """Every review that fitted no written theme and that no person has looked
    at yet, newest first. Covers Play and iOS and all labelled star ratings."""
    rows = [r for r in labelled if needs_manual_review(labels[r["reviewId"]])]
    rows.sort(key=lambda r: r["dt"], reverse=True)
    out = []
    for r in rows:
        note = labels[r["reviewId"]].get("note") or "(no note)"
        out.append({
            "reviewId": r["reviewId"], "short_id": r["reviewId"][:8], "source": r["source"],
            "date": r["at"][:10], "score": r["score"], "note": note,
            "counted_as_new_theme": note in promoted,
            "text": r["content"][:500],
        })
    return out


def new_ratings(cfg, app_id, min_days=6):
    """Star mix of ratings added between the oldest and newest snapshot.
    Needs two snapshots at least `min_days` apart, so it is empty on the first runs."""
    path = os.path.join(cfg["data_dir"], "snapshots.jsonl")
    snaps = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            snaps = [json.loads(line) for line in f if line.strip()]
    snaps = [s for s in snaps if s["app_id"] == app_id and s.get("histogram")]
    if len(snaps) < 2:
        return None
    first, last = snaps[0], snaps[-1]
    days = (datetime.strptime(last["pulled_at"], TIME_FMT) - datetime.strptime(first["pulled_at"], TIME_FMT)).days
    if days < min_days:
        return None
    delta = [b - a for a, b in zip(first["histogram"], last["histogram"])]
    added = sum(delta)
    if added <= 0 or min(delta) < 0:   # Play removed ratings in between; not a clean comparison
        return None
    return {"from": first["pulled_at"][:10], "to": last["pulled_at"][:10], "days": days,
            "added": added, "by_star": delta,
            "avg": round(sum((i + 1) * d for i, d in enumerate(delta)) / added, 2),
            "pct_low": pct(delta[0] + delta[1], added)}


def compute(cfg):
    tax = load_taxonomy()
    app = employee_app(cfg)
    rows = load_reviews(cfg, app["id"])
    snaps = latest_snapshots(cfg)
    snap = snaps[app["id"]]
    hist = snap["histogram"]
    labels = load_labels(cfg)

    newest = rows[0]["dt"]
    start = window_start(rows, cfg)
    window = [r for r in rows if r["dt"] >= start]
    scope = in_scope(cfg)
    labelled = [r for r in scope if r["reviewId"] in labels]
    play_low = [r for r in labelled if r["source"] == "play" and r["score"] <= 2]
    ios_low = [r for r in labelled if r["source"] == "ios" and r["score"] <= 2]
    baseline = review_stats(window)

    ios_snap = latest_snapshots(cfg, "ios_snapshots.jsonl", "ios_id").get(app.get("ios_id"))
    h = histogram_stats(hist)
    specific = [r for r in play_low if labels[r["reviewId"]]["theme"] not in NO_DETAIL]
    owner_specific = Counter(labels[r["reviewId"]]["owner"] for r in specific)

    safety = [r for r in labelled if labels[r["reviewId"]]["safety"]]
    safety.sort(key=lambda r: r["dt"], reverse=True)
    recent_start = newest - timedelta(days=90)
    promoted = promoted_notes(play_low, labels, cfg.get("alerts", {}).get("new_theme_min_reviews", 5))

    return {
        "generated_at": now_utc().strftime(TIME_FMT) + " UTC",
        "app": {"id": app["id"], "name": app["name"], "version": snap["version"],
                "snapshot_at": snap["pulled_at"]},
        "window": {"from": start.strftime("%Y-%m-%d"), "to": newest.strftime("%Y-%m-%d"),
                   "days": cfg["label_window_days"]},
        "ratings": {**h, "score": round(snap["score"], 3), "histogram": hist,
                    "written_reviews": snap["reviews"]},
        "ios": ios_snap and {"score": round(ios_snap["score"], 3), "ratings": ios_snap["ratings"],
                             "version": ios_snap["version"],
                             "gap": round(ios_snap["score"] - snap["score"], 3)},
        "countries": country_table(cfg, app["id"]),
        "other_apps": [{"id": a["id"], "name": a["name"], "role": a["role"],
                        "score": snaps[a["id"]]["score"], "ratings": snaps[a["id"]]["ratings"]}
                       for a in cfg["apps"] if a["id"] != app["id"] and a["id"] in snaps],
        "reviews": {
            "pulled": len(rows),
            "lifetime": review_stats(rows),
            "last_365": baseline,
            "last_90": review_stats([r for r in rows if r["dt"] >= newest - timedelta(days=90)]),
            "last_30": review_stats([r for r in rows if r["dt"] >= newest - timedelta(days=30)]),
            "monthly": monthly_trend(rows),
        },
        "versions": version_table(window, labels, baseline["pct_low"]),
        "replies": reply_stats(window),
        "labelling": {
            "in_scope": len(scope), "labelled": len(labelled), "pending": len(scope) - len(labelled),
            "by": dict(Counter(labels[r["reviewId"]]["labelled_by"] for r in labelled)),
            "play_low": len(play_low), "ios_low": len(ios_low),
            "specific": len(specific), "specific_pct": pct(len(specific), len(play_low)),
        },
        "themes": theme_table(tax, play_low, ios_low, labels, hist, recent_start, promoted),
        "recommendations": recommendation_evidence(load_recommendations(), play_low, ios_low, window,
                                                   labels, hist, recent_start),
        "reply_queue": reply_queue(window, labels),
        "unthemed": unthemed(play_low, labels, recent_start, promoted),
        "manual_review": manual_review_list(labelled, labels, promoted),
        "stages": rollup(play_low, labels, lambda lab: tax["by_id"][lab["theme"]]["stage"]),
        "owners": rollup(play_low, labels, lambda lab: lab["owner"]),
        "owners_of_specific": [{"id": k, "n": v, "share_pct": pct(v, len(specific))}
                               for k, v in owner_specific.most_common()],
        "after_update": {"n": sum(labels[r["reviewId"]]["after_update"] for r in play_low),
                         "share_pct": pct(sum(labels[r["reviewId"]]["after_update"] for r in play_low), len(play_low))},
        "safety": [{"reviewId": r["reviewId"], "source": r["source"], "date": r["at"][:10],
                    "score": r["score"], "theme": labels[r["reviewId"]]["theme"],
                    "replied": bool(r.get("replyContent")),
                    "text": r["content"][:400]} for r in safety],
        "new_ratings": new_ratings(cfg, app["id"]),
        "validation": validate.compute(cfg),
    }


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    cfg = load_config()
    m = compute(cfg)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(m, f, indent=1, ensure_ascii=False)
    r = m["ratings"]
    print(f"{m['app']['name']}: {r['score']} from {r['ratings']} ratings "
          f"({r['pct_by_star'][0]}% one-star; {r['avg_without_1_star']} without them)")
    if m["ios"]:
        print(f"iOS: {m['ios']['score']} from {m['ios']['ratings']} ratings (gap {m['ios']['gap']:+})")
    print(f"window {m['window']['from']} to {m['window']['to']}: "
          f"{m['labelling']['play_low']} low-star Play reviews labelled, {m['labelling']['pending']} pending")
    for t in m["themes"][:8]:
        print(f"  {t['id']:22s} {t['share_pct']:5.1f}%  lift {t['rating_lift']:+.3f}")
    print(f"wrote {OUT}")
    return m


if __name__ == "__main__":
    main()
