#!/usr/bin/env python3
"""
Build the outputs people read, all from docs/data/metrics.json.

    python -m ratings.build

Writes:
    docs/index.html    the report: verdict, causes, action plan, evidence, queues, method
    docs/digest.md     the short weekly answer to "How are our Play Store ratings?"
    docs/backlog.csv   the action plan as rows that can be imported into a ticket tracker

No number is typed into these files by hand. The wording of each recommendation
comes from recommendations.yaml; its counts come from metrics.json.
"""
import csv
import json
import os
import sys

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ratings import charts
from ratings.metrics import NEW_PREFIX, NO_DETAIL, OUT as METRICS_PATH, load_recommendations, rating_lift
from ratings.pull import load_config

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = "docs"

THEME_NAMES = {
    "login_otp": "Login OTP never arrives",
    "session_logout": "Logged out repeatedly",
    "app_wont_open": "App won't open or crashes",
    "slow_performance": "Slow or hangs",
    "booking_flow": "Booking fails or confuses",
    "booking_rules": "Booking rules and cut-offs",
    "address_location": "Address or pickup point wrong",
    "trip_details": "Trip details missing or wrong",
    "tracking_eta": "Tracking and ETA inaccurate",
    "boarding": "Boarding OTP or QR scan",
    "no_show": "Marked no-show unfairly",
    "cab_late_or_missing": "Cab late or missing",
    "routing_time": "Route too long or pickup too early",
    "driver_behaviour": "Driver conduct",
    "support_unreachable": "Can't reach support or give feedback",
    "forced_prompts": "Forced prompts and pop-ups",
    "ui_design": "Interface or redesign disliked",
    "feature_request": "Feature request",
    "generic_negative": "No detail given",
    "not_a_complaint": "Not a complaint",
    "other": "Other",
}
OWNER_KEYS = [
    ("app", "c-s1", "product / engineering"),
    ("ops", "c-s2", "operations / vendor"),
    ("policy", "c-s3", "employer policy"),
    ("unclear", "c-neutral", "not clear"),
]
COUNTRY_NAMES = {"in": "India", "us": "United States", "ph": "Philippines", "za": "South Africa",
                 "gb": "United Kingdom", "lk": "Sri Lanka", "sg": "Singapore"}
HORIZONS = [("week", "This week"), ("30 days", "Next 30 days"), ("90 days", "Next 90 days")]
ACCESS_THEMES = ("login_otp", "session_logout", "app_wont_open", "slow_performance")


def theme_name(tid):
    """Display name for a theme. Automatic themes are shown by their note, marked as new."""
    if tid.startswith(NEW_PREFIX):
        return tid[len(NEW_PREFIX):].capitalize() + " (new)"
    return THEME_NAMES.get(tid, tid)


def country_name(code):
    return COUNTRY_NAMES.get(code, code.upper())


def month_label(ym):
    """'2026-03' -> 'Mar 26'"""
    names = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
    return f"{names[int(ym[5:7]) - 1]} {ym[2:4]}"


def alerts(m, cfg):
    """Plain rules that decide whether the weekly digest should raise a flag."""
    rules = cfg.get("alerts", {})
    out = []
    r = m["reviews"]
    jump = rules.get("low_share_jump_points", 5)
    if r["last_30"]["n"] >= rules.get("min_reviews", 50) and \
            r["last_30"]["pct_low"] > r["last_365"]["pct_low"] + jump:
        out.append(f"Low-star share in the last 30 days is {r['last_30']['pct_low']}%, "
                   f"more than {jump} points above the 12-month figure of {r['last_365']['pct_low']}%.")
    for t in m["themes"]:
        if t["id"] in NO_DETAIL:
            continue
        if t["recent_share_pct"] >= rules.get("theme_min_share", 5) and \
                t["recent_share_pct"] >= rules.get("theme_growth", 2) * max(t["earlier_share_pct"], 0.1):
            out.append(f"\"{theme_name(t['id'])}\" is {t['recent_share_pct']}% of low-star reviews in the "
                       f"last 90 days, up from {t['earlier_share_pct']}% before.")
    if m["versions"] and m["versions"][-1]["worse"]:
        v = m["versions"][-1]
        out.append(f"Version {v['version']} has {v['pct_low']}% low-star reviews ({v['n']} reviews), "
                   f"clearly above the usual {r['last_365']['pct_low']}%.")
    unanswered_safety = [s for s in m["safety"] if s["source"] == "play" and not s["replied"]]
    if unanswered_safety:
        out.append(f"{len(unanswered_safety)} safety-flagged Play reviews in the last 12 months have no reply.")
    if m["labelling"]["pending"]:
        out.append(f"{m['labelling']['pending']} new low-star reviews are waiting for a theme label.")
    un = m["unthemed"]
    if un["recent_share_pct"] >= rules.get("other_share_alert", 3):
        out.append(f"{un['recent_share_pct']}% of low-star reviews in the last 90 days fit no theme. "
                   "The themes may be going out of date; review the unthemed complaints.")
    for g in un["groups"]:
        if g["promoted"]:
            out.append(f"New theme created automatically: \"{g['note']}\" ({g['n']} low-star reviews, "
                       f"{g['recent_n']} in the last 90 days). Review it: define it in taxonomy.yaml, "
                       "merge it into another theme, or leave it as it is.")
    return out


def make_charts(m):
    r = m["ratings"]
    by_code = {c["country"]: c for c in m["countries"]}
    dots = [(country_name(c["country"]) + " (Play)", c["score"],
             f"{c['pct_1']}% one-star. {c['country_reviews']:,} reviews counted in this country.", True)
            for c in m["countries"]]
    if m["ios"]:
        dots.append(("India (App Store)", round(m["ios"]["score"], 2),
                     f"{m['ios']['ratings']:,} ratings on iOS.", False))
    dots.sort(key=lambda d: d[1])

    months = [(month_label(p["month"]), p["pct_low"], f"{p['month']}: {p['pct_low']}% of {p['n']} reviews were 1-2 star")
              for p in m["reviews"]["monthly"]]

    low_n = m["labelling"]["play_low"]
    themes = [t for t in m["themes"] if t["n"] and t["id"] not in NO_DETAIL]
    theme_rows = [(theme_name(t["id"]),
                   {k: round(100 * v / low_n, 1) for k, v in t["owners"].items()},
                   f"{t['share_pct']}%") for t in themes]

    versions = [(v["version"], v["pct_low"],
                 f"Version {v['version']} (from {v['first_seen']}): {v['pct_low']}% of {v['n']} reviews were 1-2 star")
                for v in m["versions"]]
    base = m["reviews"]["last_365"]["pct_low"]
    return {
        "countries": charts.dot_plot(dots, 4.0, 5.0, [4.0, 4.25, 4.5, 4.75, 5.0],
                                     "Rating shown on each country's store listing"),
        "stars": charts.star_bar(r["pct_by_star"], "Share of ratings by star, Indian Play listing"),
        "monthly": charts.line_chart(months, 50, 10, "%", "Share of written reviews that are 1-2 star, by month",
                                     reference=(base, f"12-mo. {base}%")),
        "themes": charts.stacked_bars(theme_rows, OWNER_KEYS, max(t["share_pct"] for t in themes) * 1.05, "%",
                                      "What low-star reviews are about, split by who can fix it"),
        "versions": charts.columns(versions, 50, 10, "%", "Share of 1-2 star reviews by app version",
                                   reference=(base, f"12-mo. {base}%")),
        "india": by_code.get("in"),
    }


def summary(m):
    """A few derived figures the page and digest both quote."""
    themes = {t["id"]: t for t in m["themes"]}
    low_n = m["labelling"]["play_low"]
    named_share = sum(t["n"] for t in m["themes"] if t["id"] not in NO_DETAIL) / low_n
    access_share = sum(themes[t]["n"] for t in ACCESS_THEMES) / low_n
    hist = m["ratings"]["histogram"]
    owners = {o["id"]: o["share_pct"] for o in m["owners_of_specific"]}
    countries = m["countries"]
    top_named = max((t for t in m["themes"] if t["id"] not in NO_DETAIL), key=lambda t: t["rating_lift"])
    recent_top = max((t for t in m["themes"] if t["id"] not in NO_DETAIL), key=lambda t: t["recent_share_pct"])
    return {
        "lowest_country": countries[0] if countries else None,
        "home_country": next((c for c in countries if c["country"] == "in"), None),
        "access_share_pct": round(100 * access_share, 1),
        "access_lift": rating_lift(hist, access_share),
        "named_lift": rating_lift(hist, named_share),
        "top_theme_lift": top_named["rating_lift"],
        "owner_app_pct": owners.get("app", 0),
        "owner_ops_pct": owners.get("ops", 0),
        "owner_policy_pct": owners.get("policy", 0),
        "no_detail_pct": themes["generic_negative"]["share_pct"],
        "recent_top": recent_top,
        "safety_play": sum(1 for s in m["safety"] if s["source"] == "play"),
        "safety_unreplied": sum(1 for s in m["safety"] if s["source"] == "play" and not s["replied"]),
    }


def evidence_line(rec, m, short=False):
    """The evidence for a recommendation as one line. `short` keeps only the headline figure."""
    ev = m["recommendations"][rec["id"]]
    parts = []
    if rec.get("themes"):
        parts.append(f"{ev['n']} of {m['labelling']['play_low']} low-star Play reviews in 12 months "
                     f"({ev['share_pct']}%); {ev['ios_share_pct']}% on iOS")
    if rec.get("use_reply_stats"):
        rp = m["replies"]
        parts.append(f"{rp['low_replied']} of {rp['low_n']} low-star reviews got a reply ({rp['low_pct']}%); "
                     f"median reply time {rp['median_lag_hours']} hours; "
                     f"{rp['to_positive_pct']}% of replies went to 4-5 star reviews")
    if rec.get("use_safety"):
        parts.append(f"{len(m['safety'])} safety-flagged low-star reviews in 12 months (Play and iOS)")
    if rec.get("use_countries") and m["countries"]:
        parts.append("listing ratings: " + ", ".join(f"{country_name(c['country'])} {c['score']}"
                                                      for c in m["countries"]))
    for p in ev["patterns"]:
        parts.append(f"{p['n']} {p['where']} {p['label']} ({p['last_90']} in the last 90 days)")
    return ". ".join(parts[:1] if short else parts) + "."


def write_backlog(recs, m):
    path = os.path.join(DOCS, "backlog.csv")
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["ID", "Title", "Horizon", "Owner", "Effort", "Problem", "Evidence", "Suspected cause",
                    "Change", "First step", "Already exists?", "Success metric", "How to verify", "Guardrail"])
        for r in recs:
            w.writerow([r["id"], r["title"], r["horizon"], r["owner"], r["effort"],
                        " ".join(r["problem"].split()), evidence_line(r, m), " ".join(r["cause"].split()),
                        " | ".join(r["change"]), r["first_step"], r["exists_check"], r["metric"], r["verify"],
                        " ".join(r["guardrail"].split())])
    return path


def write_digest(recs, m, s, flags, cfg):
    r, rv = m["ratings"], m["reviews"]
    lines = [f"# How are our Play Store ratings? ({m['window']['to']})", ""]
    answer = f"**{r['score']:.2f} on the Indian listing**"
    if s["lowest_country"]:
        answer += f", {s['lowest_country']['score']} on the lowest listing ({country_name(s['lowest_country']['country'])})"
    if m["ios"]:
        answer += f", {m['ios']['score']:.2f} on iOS"
    trend = rv["last_30"]["pct_low"] - rv["last_365"]["pct_low"]
    # same threshold as the alert, so the digest never calls a normal wobble "worse"
    jump = cfg.get("alerts", {}).get("low_share_jump_points", 5)
    word = "in line with" if abs(trend) < jump else ("worse than" if trend > 0 else "better than")
    lines += [answer + ".",
              f"In the last 30 days {rv['last_30']['pct_low']}% of {rv['last_30']['n']} written reviews were 1-2 star, "
              f"{word} the 12-month figure of {rv['last_365']['pct_low']}%.",
              f"The biggest named problem in the last 90 days is \"{theme_name(s['recent_top']['id'])}\" "
              f"({s['recent_top']['recent_share_pct']}% of low-star reviews).", ""]
    lines += ["## Needs attention", ""]
    lines += [f"- {a}" for a in flags] or ["- Nothing has crossed an alert threshold."]
    lines += ["", "## Listings by country", "", "| Listing | Rating | 1-star share |", "|---|---|---|"]
    lines += [f"| {country_name(c['country'])} | {c['score']} | {c['pct_1']}% |" for c in m["countries"]]
    if m["new_ratings"]:
        nr = m["new_ratings"]
        lines += ["", f"New ratings {nr['from']} to {nr['to']}: {nr['added']} added, averaging {nr['avg']}, "
                      f"{nr['pct_low']}% of them 1-2 star."]
    else:
        lines += ["", "The star mix of newly added ratings appears here once two snapshots a week apart exist."]
    lines += ["", "## Written reviews", "", "| Period | Reviews | Average | 1-2 star |", "|---|---|---|---|"]
    for name, key in (("Last 30 days", "last_30"), ("Last 90 days", "last_90"),
                      ("Last 12 months", "last_365"), ("All time", "lifetime")):
        lines.append(f"| {name} | {rv[key]['n']:,} | {rv[key]['avg']} | {rv[key]['pct_low']}% |")
    lines += ["", "## What low-star reviews are about", "",
              "| Theme | Last 90 days | Before that | 12-month share |", "|---|---|---|---|"]
    for t in sorted((t for t in m["themes"] if t["id"] not in NO_DETAIL and t["n"]),
                    key=lambda t: -t["recent_share_pct"])[:10]:
        lines.append(f"| {theme_name(t['id'])} | {t['recent_share_pct']}% | {t['earlier_share_pct']}% | {t['share_pct']}% |")
    un = m["unthemed"]
    lines += ["", "## Complaints that fit no theme", "",
              f"{un['n']} of {m['labelling']['play_low']} low-star reviews in 12 months ({un['share_pct']}%); "
              f"{un['recent_share_pct']}% in the last 90 days.", ""]
    lines += [f"- {g['note']}: {g['n']} ({g['recent_n']} in the last 90 days)"
              + (" - now counted as its own theme" if g["promoted"] else "") for g in un["groups"]] or ["- None."]
    lines += ["", "## Latest versions", "", "| Version | First seen | Reviews | 1-2 star |", "|---|---|---|---|"]
    for v in m["versions"][-5:]:
        lines.append(f"| {v['version']} | {v['first_seen']} | {v['n']} | {v['pct_low']}%{' (clearly above usual)' if v['worse'] else ''} |")
    rp = m["replies"]
    lines += ["", "## Replies", "",
              f"{rp['low_replied']} of {rp['low_n']} low-star reviews in the last 12 months have a developer reply "
              f"({rp['low_pct']}%). {m['reply_queue']['waiting']} are waiting; the newest "
              f"{len(m['reply_queue']['shown'])}, each with a draft reply, are in the report.", ""]
    lines += ["## Action plan", ""]
    lines += [f"- {rec['id']} ({rec['horizon']}): {rec['title']}" for rec in recs]
    lines += ["", f"Generated {m['generated_at']} from {rv['pulled']:,} reviews. Full report: index.html."]
    path = os.path.join(DOCS, "digest.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    cfg = load_config()
    with open(METRICS_PATH, encoding="utf-8") as f:
        m = json.load(f)
    recs = load_recommendations()
    s = summary(m)
    flags = alerts(m, cfg)

    env = Environment(loader=FileSystemLoader(os.path.join(HERE, "templates")),
                      autoescape=select_autoescape(["html"]), trim_blocks=True, lstrip_blocks=True)
    env.filters["theme"] = theme_name
    env.filters["country"] = country_name
    env.filters["signed"] = lambda v: f"{v:+.3f}".rstrip("0").rstrip(".") if v else "0"
    env.filters["commas"] = lambda v: f"{v:,}"
    page = env.get_template("report.html").render(
        m=m, s=s, cfg=cfg, recs=recs, flags=flags, charts=make_charts(m), horizons=HORIZONS,
        owner_keys=OWNER_KEYS, evidence_line=lambda rec: evidence_line(rec, m, short=True),
        named_themes=[t for t in m["themes"] if t["n"] and t["id"] not in NO_DETAIL],
        # GitHub shows Markdown as a formatted page; a bare .md link on the site would show raw text
        digest_href=(cfg["repo_url"].rstrip("/") + "/blob/main/docs/digest.md") if cfg.get("repo_url") else "digest.md",
    )
    os.makedirs(DOCS, exist_ok=True)
    open(os.path.join(DOCS, ".nojekyll"), "w").close()   # serve the files as they are
    with open(os.path.join(DOCS, "index.html"), "w", encoding="utf-8") as f:
        f.write(page)
    print(f"wrote {os.path.join(DOCS, 'index.html')}")
    print(f"wrote {write_digest(recs, m, s, flags, cfg)}")
    print(f"wrote {write_backlog(recs, m)}")
    for a in flags:
        print(f"  ALERT: {a}")


if __name__ == "__main__":
    main()
