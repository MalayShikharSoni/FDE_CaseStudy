#!/usr/bin/env python3
"""
Theme labels for low-star reviews, with a cache so each review is labelled once.

    python -m ratings.classify status    how many reviews are in scope / labelled / pending
    python -m ratings.classify export    write pending reviews to data/pending/batch_NNN.txt
    python -m ratings.classify import    read data/pending/batch_NNN.labels.txt into the cache
    python -m ratings.classify requeue --theme other
                                         drop the labels with that theme so they are labelled
                                         again (used after a theme is added or redefined)

Labels live in data/labels.jsonl, one line per review, each marked with who
labelled it. Two labellers can fill the cache, and both follow ratings/prompt.md
and ratings/taxonomy.yaml:

  * the `play-ratings` Claude Code skill: export -> Claude writes the
    .labels.txt files -> import. This is how the current labels were made.
  * the API backend (ratings/llm.py), for unattended runs with an API key.
"""
import argparse
import glob
import json
import os
import sys
from datetime import datetime, timedelta

import yaml

from ratings.clean import load_ios_reviews, load_reviews
from ratings.pull import TIME_FMT, load_config

HERE = os.path.dirname(os.path.abspath(__file__))
BATCH = 150        # reviews per batch file
TEXT_LIMIT = 500   # characters of review text shown to the labeller
OWNERS = ("app", "ops", "policy", "unclear")


def load_taxonomy():
    with open(os.path.join(HERE, "taxonomy.yaml"), encoding="utf-8") as f:
        tax = yaml.safe_load(f)
    tax["by_id"] = {t["id"]: t for t in tax["themes"]}
    return tax


def employee_app(cfg):
    return next(a for a in cfg["apps"] if a["role"] == "employee")


def window_start(play_rows, cfg):
    """Start of the labelling window. Anchored on the newest pulled review, not
    on today's date, so the same data always gives the same window."""
    return play_rows[0]["dt"] - timedelta(days=cfg["label_window_days"])


def in_scope(cfg):
    """Reviews that should have a theme label: employee app, Play + iOS,
    low-star, inside the window, with some text."""
    play = load_reviews(cfg, employee_app(cfg)["id"])
    if not play:
        return []
    start = window_start(play, cfg)
    rows = play + load_ios_reviews(cfg)
    return [r for r in rows
            if r["dt"] >= start and r["score"] <= cfg["label_max_score"] and r["content"]]


def labels_path(cfg):
    return os.path.join(cfg["data_dir"], "labels.jsonl")


def load_labels(cfg):
    labels = {}
    path = labels_path(cfg)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    lab = json.loads(line)
                    labels[lab["reviewId"]] = lab  # a later line replaces an earlier one (relabels)
    return labels


def save_labels(cfg, new_labels):
    with open(labels_path(cfg), "a", encoding="utf-8") as f:
        for lab in new_labels:
            f.write(json.dumps(lab, ensure_ascii=False) + "\n")


def pending(cfg):
    done = load_labels(cfg)
    return [r for r in in_scope(cfg) if r["reviewId"] not in done]


def parse_line(line, tax):
    """Parse 'index|theme|theme2|owner|safety|after_update|note' (trailing fields optional).
    Returns (index, label fields). Raises ValueError on anything not in the taxonomy."""
    parts = [p.strip() for p in line.strip().split("|", 6)]
    if len(parts) < 2:
        raise ValueError(f"expected at least index and theme: {line!r}")
    parts += [""] * (7 - len(parts))
    idx, theme, theme2, owner, safety, after_update, note = parts
    if theme not in tax["by_id"]:
        raise ValueError(f"unknown theme {theme!r}: {line!r}")
    if theme2 and theme2 not in tax["by_id"]:
        raise ValueError(f"unknown theme2 {theme2!r}: {line!r}")
    if theme2 == theme:
        theme2 = ""
    owner = owner or tax["by_id"][theme]["default_owner"]
    if owner not in OWNERS:
        raise ValueError(f"unknown owner {owner!r}: {line!r}")
    if safety not in ("", "0", "1") or after_update not in ("", "0", "1"):
        raise ValueError(f"flags must be 0 or 1: {line!r}")
    # A complaint that fits no theme must say what it is about. These notes are
    # how a new theme gets noticed, so `other` is never a silent bucket.
    if theme == "other" and not note:
        raise ValueError(f"'other' needs a short note saying what the complaint is: {line!r}")
    return int(idx), {
        "theme": theme, "theme2": theme2, "owner": owner,
        "safety": safety == "1", "after_update": after_update == "1",
        "note": note.lower() if theme == "other" else "",
    }


def batch_text(rows, notes=()):
    """The text a labeller sees for a batch: one numbered review per line.
    `notes` lists the notes already used for `other`, so the same complaint gets
    the same wording and can be counted."""
    lines = [f"{i}\t{r['score']}*\t{r['content'][:TEXT_LIMIT]}" for i, r in enumerate(rows, 1)]
    if notes:
        lines.insert(0, "# notes already in use for 'other': " + "; ".join(notes))
    return "\n".join(lines)


def known_notes(cfg):
    return sorted({lab["note"] for lab in load_labels(cfg).values() if lab.get("note")})


def parse_batch(text, rows, tax, labelled_by):
    """Turn a labeller's output for `rows` into label records.
    Every review must get exactly one valid line, or the whole batch is rejected."""
    stamp = datetime.now().strftime(TIME_FMT)
    labels = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        idx, fields = parse_line(line, tax)
        if not 1 <= idx <= len(rows):
            raise ValueError(f"index {idx} is outside 1-{len(rows)}")
        if idx in labels:
            raise ValueError(f"index {idx} appears twice")
        r = rows[idx - 1]
        labels[idx] = {"reviewId": r["reviewId"], "source": r["source"], **fields,
                       "labelled_by": labelled_by, "labelled_at": stamp}
    missing = [i for i in range(1, len(rows) + 1) if i not in labels]
    if missing:
        raise ValueError(f"{len(missing)} reviews have no label (first missing index: {missing[0]})")
    return [labels[i] for i in sorted(labels)]


def pending_dir(cfg):
    return os.path.join(cfg["data_dir"], "pending")


def export_batches(cfg):
    rows = pending(cfg)
    notes = known_notes(cfg)
    out = pending_dir(cfg)
    os.makedirs(out, exist_ok=True)
    for old in glob.glob(os.path.join(out, "batch_*")):
        os.remove(old)
    for n, start in enumerate(range(0, len(rows), BATCH), 1):
        chunk = rows[start:start + BATCH]
        with open(os.path.join(out, f"batch_{n:03d}.txt"), "w", encoding="utf-8") as f:
            f.write(batch_text(chunk, notes) + "\n")
        with open(os.path.join(out, f"batch_{n:03d}.ids.json"), "w", encoding="utf-8") as f:
            json.dump([r["reviewId"] for r in chunk], f)
    print(f"{len(rows)} pending reviews written to {out} "
          f"in {-(-len(rows) // BATCH)} batch file(s)")
    return len(rows)


def import_batches(cfg, labelled_by):
    tax = load_taxonomy()
    by_id = {r["reviewId"]: r for r in in_scope(cfg)}
    out = pending_dir(cfg)
    total = 0
    for label_file in sorted(glob.glob(os.path.join(out, "batch_*.labels.txt"))):
        base = label_file[:-len(".labels.txt")]
        with open(base + ".ids.json", encoding="utf-8") as f:
            rows = [by_id[i] for i in json.load(f)]
        with open(label_file, encoding="utf-8") as f:
            labels = parse_batch(f.read(), rows, tax, labelled_by)
        save_labels(cfg, labels)
        for suffix in (".txt", ".ids.json", ".labels.txt"):
            os.remove(base + suffix)
        total += len(labels)
        print(f"  {os.path.basename(label_file)}: {len(labels)} labels saved")
    print(f"{total} labels imported; {len(pending(cfg))} still pending")
    return total


def requeue(cfg, theme):
    """Remove every label with this theme, so those reviews are labelled again.
    Run this after adding a theme (requeue `other`) or changing a definition."""
    labels = load_labels(cfg)
    keep = [lab for lab in labels.values() if lab["theme"] != theme]
    with open(labels_path(cfg), "w", encoding="utf-8") as f:
        for lab in keep:
            f.write(json.dumps(lab, ensure_ascii=False) + "\n")
    print(f"{len(labels) - len(keep)} labels with theme '{theme}' removed; "
          f"{len(pending(cfg))} reviews now pending")
    return len(labels) - len(keep)


def status(cfg):
    scope = in_scope(cfg)
    done = load_labels(cfg)
    labelled = sum(1 for r in scope if r["reviewId"] in done)
    print(f"in scope: {len(scope)}  labelled: {labelled}  pending: {len(scope) - labelled}")
    return len(scope) - labelled


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["status", "export", "import", "requeue"])
    ap.add_argument("--by", default="claude-code", help="who produced the labels being imported")
    ap.add_argument("--theme", help="for requeue: the theme whose labels to drop")
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    cfg = load_config()
    if args.command == "status":
        status(cfg)
    elif args.command == "export":
        export_batches(cfg)
    elif args.command == "requeue":
        if not args.theme:
            ap.error("requeue needs --theme")
        requeue(cfg, args.theme)
    else:
        import_batches(cfg, args.by)


if __name__ == "__main__":
    main()
