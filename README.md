# MoveInSync Play Store ratings

A diagnosis of why the MoveInSync employee app's Play Store rating is where it
is, a ranked action plan, and a pipeline that re-answers "How are our Play Store
ratings?" every week.

Submitted for the MoveInSync Forward Deployed Engineer case study by Malay Shikhar Soni.

## Start here

| If you want | Open |
|---|---|
| The answer and the action plan | **[The live report](https://malayshikharsoni.github.io/FDE_CaseStudy/)** (source: [docs/index.html](docs/index.html)) |
| This week's short answer | [docs/digest.md](docs/digest.md) |
| The plan as importable tickets | [docs/backlog.csv](docs/backlog.csv) |
| Reviews waiting for a person to look at them | [docs/manual_review.csv](docs/manual_review.csv) |
| How the pipeline works, file by file, and how to run it end to end | [The pipeline guide](https://malayshikharsoni.github.io/FDE_CaseStudy/workflow.html) (source: [docs/workflow.html](docs/workflow.html)) |
| How it was done, and its limits | [METHOD.md](METHOD.md) |
| Every number in the report | [docs/data/metrics.json](docs/data/metrics.json) |

## The short version

- The app shows **4.64** on the Indian Play Store, but each country sees its own
  figure: **4.32** in the United States, 4.49 in the Philippines and the UK.
- It is not falling. About 27% of written reviews are one or two-star, month
  after month, and no single release is provably worse than the rest.
- Of low-star reviews that name a problem, about three in four are things
  product or engineering would fix. Getting into the app (login OTP, logouts,
  the app not opening, slowness) is the largest cluster.
- Under 4% of low-star reviews got a developer reply in the last year.
- No single fix moves the rating much, so the plan is a ranked set of 11 tickets.

Figures above are from the run of 12 October 2026; the report always has the current ones.

## Run it

Needs Python 3.10 or later.

```
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python run.py
```

`python run.py` pulls new reviews, checks them, computes the metrics and
rebuilds the report, digest, backlog and manual-review list. It takes under a minute after the first
run. `python run.py --no-pull` rebuilds from the data already in the repository,
with no network access.

Run the tests with `python -m pytest`.

## How it works

```
pull  ->  clean  ->  label  ->  validate  ->  metrics  ->  build
```

| Stage | File | What it does |
|---|---|---|
| Pull | [ratings/pull.py](ratings/pull.py), [ratings/ios.py](ratings/ios.py) | Play reviews (incremental), the rating on each country listing, iOS reviews |
| Clean | [ratings/clean.py](ratings/clean.py) | Checks duplicates, scores and dates; compares coverage with Play's own count |
| Label | [ratings/classify.py](ratings/classify.py) | Gives each low-star review a theme, an owner and two flags; each review is labelled once and cached |
| Validate | [ratings/validate.py](ratings/validate.py) | Compares the labels with a second, separately labelled sample of 120 reviews |
| Metrics | [ratings/metrics.py](ratings/metrics.py) | Computes every number into one JSON file |
| Build | [ratings/build.py](ratings/build.py), [ratings/charts.py](ratings/charts.py) | Writes the report, digest, backlog and manual-review list from that file |

The rules for labelling are in [ratings/prompt.md](ratings/prompt.md) and the
themes in [ratings/taxonomy.yaml](ratings/taxonomy.yaml). The action plan's
wording is in [recommendations.yaml](recommendations.yaml); its numbers are
filled in by the pipeline, so they cannot go stale.

### Who labels the reviews

Labelling is the one step a script cannot do alone. Two labellers fill the same
cache ([data/labels.jsonl](data/labels.jsonl)) using the same rules:

- **Claude Code**, through the `play-ratings` skill. This is how every label in
  this repository was made.
- **The Claude API**, through [ratings/llm.py](ratings/llm.py), for unattended
  runs. It runs when `ANTHROPIC_API_KEY` is set. It is unit-tested against a
  stubbed client and has not been run against the live API in this project.

Without either, the pipeline still runs: rating metrics update, and new
low-star reviews are counted as awaiting labels.

### When a complaint fits no theme

It is labelled `other` with a short note saying what it is about, never forced
into the nearest theme. Once enough reviews share a note (5 by default), the
pipeline counts that note as a theme of its own and flags it in the digest. A
person can later give it a written definition in the taxonomy, merge it, or
leave it. A second flag goes up if unthemed complaints pass 3% of recent
low-star reviews, which would mean the theme list is going out of date.

Every such review is also marked for manual review: listed one by one in the
report and in [docs/manual_review.csv](docs/manual_review.csv) until a person
records a decision with `python -m ratings.classify resolve`.

## Asking the question again

- **Weekly:** [.github/workflows/weekly.yml](.github/workflows/weekly.yml) runs
  the pipeline every Monday and commits the result. Add an `ANTHROPIC_API_KEY`
  repository secret to have new reviews labelled automatically.
- **On demand:** open this folder in Claude Code and ask "How are our Play Store
  ratings?". The [play-ratings skill](.claude/skills/play-ratings/SKILL.md)
  refreshes the data, labels new reviews and answers from the digest.

## Using this for another app or team

1. Add the app to `apps` in [config.yaml](config.yaml) with its Play package id,
   and set `role: employee` on the one to analyse in depth.
2. Run `python run.py --no-pull` once to confirm the setup, then `python run.py`.
3. Read 100 or so of its low-star reviews and adjust
   [ratings/taxonomy.yaml](ratings/taxonomy.yaml). Themes are data, not code.
4. Run `python -m ratings.gold`, label the sample in `tools/label_gold.html`
   without looking at the existing labels, and save the export as
   `data/gold.csv`. Set `check_set` in `config.yaml` to say who labelled it; a
   person is a stronger check than a second AI session. The report then shows
   how far to trust the labels.
5. Rewrite [recommendations.yaml](recommendations.yaml) for what the data shows.

Inside MoveInSync, replace the pull stage with the Play Developer Reviews API
and Play Console exports. They add ratings without text, per-country counts,
device and version data and crash rates, none of which public data has. The
other stages do not change.

## Limits

Public data only; reviewers are not a sample of users; reviews cannot be split
by country; rating effects are estimates. [METHOD.md](METHOD.md) has the detail.
