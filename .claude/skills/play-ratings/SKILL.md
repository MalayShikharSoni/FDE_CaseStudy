---
name: play-ratings
description: Answer "How are our Play Store ratings?" for the MoveInSync apps. Refreshes the review data, labels any new low-star reviews, rebuilds the report, and gives the short answer. Use when asked about Play Store or App Store ratings, reviews, what users are complaining about, or to refresh the ratings report.
---

# Play ratings

Answer the question from fresh data, not from memory of an earlier run.

## 1. Refresh

Run the pipeline from the repository root:

```
python run.py
```

Use `python run.py --no-pull` only if the user asks for no network access. If the
pull step reports that Google Play could not be reached, carry on: the report is
rebuilt from saved data, and you say so in the answer.

## 2. Label new reviews, if any are waiting

The run prints `N new reviews are awaiting theme labels` when there are some.
If it does:

1. `python -m ratings.classify export`
2. Read `ratings/prompt.md` and `ratings/taxonomy.yaml`. These are the rules. Follow them exactly.
3. For each `data/pending/batch_NNN.txt`, write `data/pending/batch_NNN.labels.txt`
   with one line per review in the format the rules give. Label every review.
4. `python -m ratings.classify import --by claude-code`
   The import rejects a batch with a missing, duplicated or invalid line. Fix the
   file and import again; do not edit `data/labels.jsonl` by hand.
5. `python run.py --no-pull` to rebuild with the new labels.

Do not relabel reviews that already have a label, and do not look at
`data/gold.csv` while labelling: it is the independent check on this step.

If a specific complaint fits no theme, label it `other` with a short note, as
the rules describe. Do not stretch a theme to avoid `other`, and do not add a
theme yourself.

## Themes created on the go

When enough `other` reviews share a note (the number is `new_theme_min_reviews`
in `config.yaml`), the pipeline counts that note as its own theme automatically
and the digest flags it. Nothing needs doing for it to appear in the report.

If the user wants to make it a written theme with a proper definition:

1. Add it to `ratings/taxonomy.yaml` with a definition, examples, stage and default owner.
2. `python -m ratings.classify requeue --theme other`, then label the requeued reviews (section 2).
3. If the new theme takes reviews from an existing theme, requeue that theme too.
4. `python -m ratings.gold` to rebuild the hand-labelling page with the new theme.

## 3. Answer

Read `docs/digest.md` and answer in this order, in a few lines:

1. The rating now: the Indian listing, the lowest country listing, and iOS.
2. Whether the last 30 days are in line with, better than or worse than the last 12 months.
3. Anything under "Needs attention".
4. The biggest named complaint in the last 90 days and whether it is growing.
5. How many low-star reviews are waiting for a reply.

Give numbers exactly as the digest has them. Say if the data is from a saved run
because Play was unreachable. Point to `docs/index.html` for the full report.

If asked a follow-up the digest does not cover, read `docs/data/metrics.json`,
which holds every figure the report uses, before computing anything yourself.
