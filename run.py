#!/usr/bin/env python3
"""
Run the whole pipeline with one command.

    python run.py             pull new reviews, then rebuild everything
    python run.py --no-pull   rebuild from the data already on disk

Stages, in order:
    1. pull      new Play reviews, rating snapshots per country, iOS reviews
    2. clean     check the data and report coverage
    3. label     themes for new low-star reviews (API if a key is set; otherwise
                 they wait for the play-ratings skill in Claude Code)
    4. metrics   every number, written to docs/data/metrics.json
    5. build     the report, the weekly digest, the backlog and the manual-review list

If Google Play cannot be reached, the pull is skipped and the rest runs on the
data already saved, so the report can always be rebuilt.
"""
import argparse
import sys

from ratings import build, classify, clean, ios, llm, metrics, pull


def step(name):
    print(f"\n== {name} ==", flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-pull", action="store_true", help="skip the network; rebuild from saved data")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    cfg = pull.load_config()

    if not args.no_pull:
        step("1. Pull")
        try:
            results = pull.main([])
            new = sum(r["new"] for r in results)
            unreachable = [r["app_id"] for r in results if not r["reachable"]]
            print(f"{new} new Play reviews" + (f"; could not reach Play for {unreachable}" if unreachable else ""))
        except Exception as exc:   # the pull must never stop the rebuild
            print(f"Pull failed ({type(exc).__name__}: {exc}); continuing with saved data.")
        try:
            ios.main()
        except Exception as exc:
            print(f"iOS pull failed ({type(exc).__name__}: {exc}); continuing with saved data.")

    step("2. Clean")
    if not clean.main():
        print("Data problems found; fix them before trusting the report.")
        return 1

    step("3. Label")
    waiting = classify.status(cfg)
    if waiting and llm.has_credentials():
        done, _ = llm.label_pending(cfg)
        waiting -= done
    if waiting:
        print(f"{waiting} new reviews are awaiting theme labels. "
              "In Claude Code, ask: \"How are our Play Store ratings?\"")

    step("4. Metrics")
    metrics.main()

    step("5. Build")
    build.main()
    print("\nDone. Open docs/index.html, or read docs/digest.md for the short answer.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
