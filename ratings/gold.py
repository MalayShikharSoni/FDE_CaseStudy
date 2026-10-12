#!/usr/bin/env python3
"""
Build the hand-labelling page for the gold set.

    python -m ratings.gold

Picks a fixed random sample of in-scope Play reviews and writes
tools/label_gold.html, a self-contained page for labelling them by hand.
Open it in a browser, label every review, press "Export CSV" and save the
file as data/gold.csv. `python -m ratings.validate` then compares the
labeller against it.

The gold set must be labelled blind: without looking at data/labels.jsonl.
"""
import csv
import json
import os
import random
import sys

from ratings.classify import in_scope, load_taxonomy
from ratings.pull import load_config

HERE = os.path.dirname(os.path.abspath(__file__))
SEED = 42  # fixed, so the sample is the same every time it is rebuilt


def sample(cfg):
    pool = [r for r in in_scope(cfg) if r["source"] == "play"]
    pool.sort(key=lambda r: r["reviewId"])  # stable order before sampling
    rng = random.Random(SEED)
    return rng.sample(pool, min(cfg["gold_size"], len(pool)))


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    cfg = load_config()
    tax = load_taxonomy()
    rows = sample(cfg)

    with open(os.path.join(cfg["data_dir"], "gold_sample.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["reviewId", "score", "at", "content"])
        for r in rows:
            w.writerow([r["reviewId"], r["score"], r["at"], r["content"]])

    reviews = [{"id": r["reviewId"], "score": r["score"], "date": r["at"][:10], "text": r["content"]}
               for r in rows]
    themes = [{k: t[k] for k in ("id", "stage", "default_owner", "definition", "examples")}
              for t in tax["themes"]]
    with open(os.path.join(HERE, "templates", "label.html"), encoding="utf-8") as f:
        page = f.read()
    page = (page.replace("/*REVIEWS*/[]", json.dumps(reviews, ensure_ascii=False))
                .replace("/*THEMES*/[]", json.dumps(themes, ensure_ascii=False))
                .replace("/*OWNERS*/{}", json.dumps(tax["owners"], ensure_ascii=False)))
    os.makedirs("tools", exist_ok=True)
    out = os.path.join("tools", "label_gold.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(page)
    print(f"{len(rows)} reviews sampled -> {out}")


if __name__ == "__main__":
    main()
