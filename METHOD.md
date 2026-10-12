# How I got there

## The approach

The brief gave no data and said the question would be asked again. So I treated
it as two jobs: answer it once properly, and leave behind something that
re-answers it without me.

1. **Find out what "low" means before explaining it.** The Indian listing shows
   4.64, which does not look low. Checking other countries' listings showed the
   same app at 4.32 in the United States and 4.49 in the Philippines and the UK,
   and the same app on iOS at 4.75. So "lower than it should be" has three
   concrete readings, and the report gives all three.
2. **Pull everything public.** All 24,318 Play reviews back to 2014, the rating
   on seven country listings, the 450 most recent iOS reviews, MoveInSync's help
   center and Google's guidance on review prompts.
3. **Read before categorising.** The themes were drafted from about 650
   low-star reviews, not decided beforehand. Two were added during labelling
   because the reviews needed them.
4. **Label, then check the labelling.** An AI model labelled 1,028 reviews to
   written rules. A fixed random sample of 120 is set aside as a check set. A
   second Claude session, which had not seen the first session's labels,
   labelled those 120 from the same written rules, and I then reviewed that
   set myself. The report gives the agreement figures, and says plainly how
   the check set was made.
5. **Size each cause honestly.** Each theme gets an estimate of its effect on
   the rating. The largest is about +0.015, which is why the plan is a ranked
   set and not one feature.
6. **Write tickets, not advice.** Each recommendation has evidence, a suspected
   cause, a concrete change, an owner, a first step and a way to tell it worked.
7. **Make it repeatable.** One command rebuilds everything; a weekly job and a
   Claude Code skill run it.

## Tools

| Tool | Used for |
|---|---|
| Python | The whole pipeline |
| `google-play-scraper` (open source) | Reading public Play reviews and listing data |
| Apple's public review feed | iOS reviews, as a cross-check |
| Claude Code (Claude, an AI model) | Writing the pipeline with me, labelling the reviews (and, in a separate session, the 120-review check set), drafting the recommendations |
| pytest | Tests on parsing, cleaning and the rating arithmetic |
| GitHub Actions and Pages | The weekly run and hosting the report |

## What the AI did and what I did

Claude wrote most of the code and did the labelling, including the check set.
I am responsible for every claim in the report. These were my calls:

- I used Python for the pipeline even though my background is JavaScript.
- I chose not to use an API key. So the labelling ran inside Claude Code
  sessions and is saved in `data/labels.jsonl`. The API path for unattended
  runs is in the repository, tested with a stand-in client, and has never been
  run against the live API.
- I chose quality over speed.

Questions I pushed on:

- The blank spots. I asked what the plan was missing. That added iOS reviews
  and MoveInSync's help center as evidence beyond Play reviews, and the rule
  that every ticket first confirms whether the feature already exists.
- What would make the report stand out. That added the reply queue with draft
  replies, the safety queue and the first-week questions.
- Fixed categories. I questioned what happens to a complaint that fits no
  theme. The labeller now records a short note for it, and a note shared by
  five reviews becomes a theme automatically.

The check set: a second Claude session labelled the 120 reviews, and I then
reviewed them myself. I say this plainly because the check is only as
independent as that makes it (see Limits).

## The three corrections

Three places where checking changed the answer:

- An early draft said the driver app's 3.5 rating was a problem. It rests on 46
  ratings, so it was dropped.
- An early draft said some releases were clearly worse. A confidence-interval
  check showed none is, at these sample sizes. The report now says so.
- An early draft compared one-star reviews with one-star ratings as head-counts.
  Play scales each country's percentages to the global total, so those are not
  real counts. The claim was removed.

## Assumptions

- **The Indian listing is the main one.** India accounts for nearly all the
  reviews Play counts by country. The report still shows the others.
- **Low-star raters who write nothing have the same complaints as those who
  write.** This is what lets review themes be scaled to ratings. It cannot be
  tested from outside.
- **A reviewer who would have been satisfied rates four stars.** The rating
  estimates use this. Using five would make every estimate about a third larger.
- **The last 12 months describe the current product.** Themes are measured over
  that window; older reviews are used only for the long-run trend.
- **A complaint means what it says.** "OTP not received" is counted as a login
  problem, though some may be unregistered users (ticket R5 separates the two).

## Limits

- **Public data only.** No crash rates, no ratings by version or device, no
  count of ratings per country, no support tickets.
- **Reviewers are not a sample of users.** They are a small, unhappier group.
  Themes explain low ratings; they do not measure how many employees are affected.
- **Reviews cannot be split by country.** Play returns one review list for every
  country, so the lower ratings abroad cannot be traced to their own complaints.
- **The rating estimates are steady-state.** They say where the average settles,
  not how fast, and Play weights recent ratings in a way it does not publish.
- **About a quarter of low-star reviews give no reason.**
- **Small samples per release.** 30 to 150 reviews each, too few to rank releases.
- **The iOS sample is the latest 450 reviews**, which is all Apple's feed exposes.
- **I could not use the app.** It needs an employer account, so no complaint was
  reproduced first-hand. Every ticket starts by confirming current behaviour.
- **Scraping is fragile and against the spirit of Play's terms for production
  use.** It is right for an outside analysis; inside MoveInSync the official
  reviews API and Play Console exports should replace it.
- **The check set is not fully independent of the labeller.** Both sets of
  labels started from Claude. I reviewed the check set, but reviewing labels
  that are already filled in is a weaker test than labelling from scratch: it
  is easy to accept a plausible label. So the agreement figure probably
  overstates accuracy.
- **The API labelling path is untested live.** It is unit-tested with a stubbed
  client only.