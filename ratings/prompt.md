# Labelling rules

These rules are the classifier. The Claude Code skill and the API backend both
follow this file together with `taxonomy.yaml`, and the human gold set is
labelled by the same rules.

You are labelling low-star reviews of the MoveInSync employee commute app.
Employees use it to book office cabs and shuttles, see pickup times, track the
vehicle, and board with an OTP or QR scan. Reviews are short and often mix
Hindi and English; label by meaning, whatever the language.

## For each review decide

**theme** - the main complaint, one id from `taxonomy.yaml`.
- Choose what the reviewer says went wrong, not their tone.
- A specific complaint always beats a general one: "worst app, OTP not coming" is `login_otp`.
- If there are several complaints, the main one is the one they spend the most words on. If that is a tie, take the first.
- `generic_negative` is only for reviews with no usable detail ("worst app", "bad service").
- `not_a_complaint` is for positive or neutral text with a low star.
- `other` is for a specific complaint that fits no theme. Do not use it for vague reviews. Never stretch a theme to avoid `other`: a wrong theme hides a new problem, while `other` with a note reveals it.

**theme2** - a second, clearly separate complaint. Leave empty if there is none. Never repeat `theme`.

**owner** - who would have to act to fix the main complaint.
- `app`: product or engineering (bugs, flows, missing information in the app).
- `ops`: transport operations or the cab vendor (late cabs, drivers, allocation, helpdesk staff).
- `policy`: a configured rule (booking cut-offs, how far ahead one can book, no-show penalties).
- `unclear`: not enough detail.
- Start from the theme's `default_owner` and change it only when the review points elsewhere. Example: `no_show` is `ops` when a driver marked it without calling, `app` when the system marked it after a missed check-in with no reminder, `policy` when the complaint is about the penalty.

**safety** - 1 if the reviewer describes a risk to a person: unsafe or drunk driving, speeding, harassment or abuse, being dropped or left waiting somewhere unsafe, being stranded at night, or the SOS or emergency helpdesk failing when needed. Also 1 when the reviewer says plainly that the service is unsafe ("no safety", "unsafe for women"), even without detail. Lateness alone is not a safety issue.

**Conventions for cases the themes do not name directly**
- No way to give feedback or rate a trip, a feedback box that does not work, or a rating screen that only accepts high stars: `support_unreachable` with owner `app`.
- "No option to call the driver": `trip_details`. A request for chat or messaging: `feature_request`.
- Problems with the call itself (no audio, call drops, shows busy): `other`.
- "App not working" with nothing else: `generic_negative`. "Not opening", "crashes", "blank screen": `app_wont_open`. "Keeps loading", "hangs": `slow_performance`.

**after_update** - 1 if the reviewer blames an update, a new version, a new feature or a new UI. "Not working since a few days" without mentioning an update is 0.

**note** - only for `other`, and required there: two to four plain words naming
the complaint, such as `call audio quality`. If the batch lists notes already in
use and one fits, reuse its exact wording. Matching notes are counted, and a
note that keeps recurring is how a new theme gets proposed.

## Output format

One line per review, fields separated by `|`:

    index|theme|theme2|owner|safety|after_update|note

Trailing fields may be left off when they are the defaults (no theme2, the
theme's default owner, 0, 0, no note). So `17|login_otp` is a complete line,
`18|no_show|driver_behaviour|ops|1|0` is a full one, and an `other` line looks
like `19|other||app|0|0|call audio quality`.

A line starting with `#` in the input is information for you, not a review.

Output only these lines, one for every review given, in the same order.
