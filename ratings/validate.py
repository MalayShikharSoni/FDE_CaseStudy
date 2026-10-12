#!/usr/bin/env python3
"""
Check the labeller against the check set: the same sample, labelled a second
time without sight of the first labels.

    python -m ratings.validate

Reads data/gold.csv (exported from tools/label_gold.html) and compares it with
data/labels.jsonl. Prints agreement figures and writes every disagreement to
data/gold_disagreements.csv so they can be read one by one.

Who labelled the check set is recorded in config.yaml (`check_set`). A person is
a real test of accuracy; a second AI session only tests consistency.

The check set is treated as the truth. "Precision" for a theme is: of the reviews
the labeller gave that theme, how many did the human also give it. "Recall" is:
of the reviews the human gave that theme, how many did the labeller find.
"""
import csv
import os
import sys
from collections import Counter

from ratings.classify import in_scope, load_labels, load_taxonomy
from ratings.pull import load_config

MIN_EXAMPLES = 3  # themes with fewer gold examples are too small to score


def load_gold(cfg):
    path = os.path.join(cfg["data_dir"], "gold.csv")
    if not os.path.exists(path):
        return None
    with open(path, newline="", encoding="utf-8-sig") as f:
        return {r["reviewId"]: r for r in csv.DictReader(f)}


def cohen_kappa(pairs):
    """Agreement between two labellers corrected for chance.
    0 = no better than chance, 1 = perfect."""
    n = len(pairs)
    observed = sum(a == b for a, b in pairs) / n
    left = Counter(a for a, _ in pairs)
    right = Counter(b for _, b in pairs)
    expected = sum(left[k] * right[k] for k in left) / (n * n)
    return (observed - expected) / (1 - expected) if expected < 1 else 1.0


def compute(cfg):
    """Agreement figures, or None if there is no gold set yet."""
    gold = load_gold(cfg)
    if not gold:
        return None
    tax = load_taxonomy()
    stage = {t["id"]: t["stage"] for t in tax["themes"]}
    labels = load_labels(cfg)
    text = {r["reviewId"]: r["content"] for r in in_scope(cfg)}

    pairs = [(g, labels[rid]) for rid, g in gold.items() if rid in labels]
    n = len(pairs)
    if not n:
        return None

    def share(test):
        return round(100 * sum(1 for g, l in pairs if test(g, l)) / n, 1)

    def either(g, l):
        # counts as agreement if the two agree on a complaint, even when they
        # disagree on which of two complaints is the main one
        return bool({g["theme"], g["theme2"]} & {l["theme"], l["theme2"]} - {""})

    result = {
        "n": n,
        "theme_exact_pct": share(lambda g, l: g["theme"] == l["theme"]),
        "theme_either_pct": share(either),
        "stage_pct": share(lambda g, l: stage[g["theme"]] == stage[l["theme"]]),
        "owner_pct": share(lambda g, l: g["owner"] == l["owner"]),
        "safety_pct": share(lambda g, l: (g["safety"] == "1") == l["safety"]),
        "after_update_pct": share(lambda g, l: (g["after_update"] == "1") == l["after_update"]),
        "theme_kappa": round(cohen_kappa([(g["theme"], l["theme"]) for g, l in pairs]), 2),
        "themes": [],
    }

    gold_count = Counter(g["theme"] for g, _ in pairs)
    label_count = Counter(l["theme"] for _, l in pairs)
    both = Counter(g["theme"] for g, l in pairs if g["theme"] == l["theme"])
    for theme, k in gold_count.most_common():
        if k >= MIN_EXAMPLES:
            result["themes"].append({
                "theme": theme, "gold": k, "labeller": label_count[theme],
                "precision_pct": round(100 * both[theme] / label_count[theme]) if label_count[theme] else None,
                "recall_pct": round(100 * both[theme] / k),
            })

    disagreements = [(g, l) for g, l in pairs if g["theme"] != l["theme"]]
    result["confusions"] = [
        {"gold": a, "labeller": b, "count": c}
        for (a, b), c in Counter((g["theme"], l["theme"]) for g, l in disagreements).most_common(8)
    ]
    with open(os.path.join(cfg["data_dir"], "gold_disagreements.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["reviewId", "gold_theme", "gold_theme2", "labeller_theme", "labeller_theme2", "content"])
        for g, l in disagreements:
            w.writerow([g["reviewId"], g["theme"], g["theme2"], l["theme"], l["theme2"], text.get(g["reviewId"], "")])
    return result


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    cfg = load_config()
    res = compute(cfg)
    if res is None:
        print("No gold set yet. Label tools/label_gold.html and save the export as data/gold.csv.")
        return
    print(f"Compared {res['n']} reviews")
    print(f"  same main theme:            {res['theme_exact_pct']}%   (kappa {res['theme_kappa']})")
    print(f"  agree on a complaint:       {res['theme_either_pct']}%   (main or second theme matches)")
    print(f"  same journey stage:         {res['stage_pct']}%")
    print(f"  same owner:                 {res['owner_pct']}%")
    print(f"  same safety flag:           {res['safety_pct']}%")
    print(f"  same after-update flag:     {res['after_update_pct']}%")
    print("  theme                    gold  labeller  precision  recall")
    for t in res["themes"]:
        p = "-" if t["precision_pct"] is None else f"{t['precision_pct']}%"
        print(f"  {t['theme']:22s} {t['gold']:5d} {t['labeller']:9d} {p:>10s} {t['recall_pct']:6d}%")
    if res["confusions"]:
        print("  most common disagreements (human -> labeller):")
        for c in res["confusions"]:
            print(f"    {c['gold']} -> {c['labeller']}: {c['count']}")
    print("  every disagreement is listed in data/gold_disagreements.csv")


if __name__ == "__main__":
    main()
