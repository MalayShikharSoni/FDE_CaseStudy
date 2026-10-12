#!/usr/bin/env python3
"""
Label pending reviews through the Claude API, for unattended runs.

    python -m ratings.llm

This is the production path: the weekly job calls it when an API key is
configured, so new reviews get themes without anyone opening Claude Code. It
follows the same rules (prompt.md) and taxonomy as the Claude Code skill, and
its output goes through the same strict parser, so both labellers fill the
same cache in the same format.

Status: unit-tested against a stubbed client (tests/test_pipeline.py). It has
NOT been run against the live API in this project, because no key was used.

Credentials come from the environment (ANTHROPIC_API_KEY or ANTHROPIC_AUTH_TOKEN).
"""
import os
import sys

import anthropic

from ratings.classify import batch_text, known_notes, load_taxonomy, parse_batch, pending, save_labels
from ratings.pull import load_config

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_MODEL = "claude-opus-5-5"
BATCH = 50          # reviews per request: small enough that one bad answer costs little
MAX_ATTEMPTS = 2    # a batch whose answer does not parse is asked for once more


def has_credentials():
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def build_system(tax):
    """Rules + taxonomy. Identical on every request, so it is cached."""
    with open(os.path.join(HERE, "prompt.md"), encoding="utf-8") as f:
        rules = f.read()
    themes = "\n".join(
        f"- {t['id']} (default owner: {t['default_owner']}): {t['definition']}"
        + (f" Examples: {' | '.join(t['examples'])}" if t["examples"] else "")
        for t in tax["themes"]
    )
    return f"{rules}\n\n## Themes\n\n{themes}\n"


def label_batch(client, model, system, rows, tax, notes=()):
    """Ask for labels for one batch. Returns label records, or raises ValueError
    if the answer still does not parse after MAX_ATTEMPTS."""
    error = None
    for _ in range(MAX_ATTEMPTS):
        response = client.beta.messages.create(
            model=model,
            max_tokens=16000,
            # the rules never change between requests, so cache them
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            output_config={"effort": "low"},
            # if a safety classifier declines a batch, let the API retry it on a fallback model
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            messages=[{"role": "user", "content":
                       "Label these reviews. Each line is: index, star rating, review text.\n\n"
                       + batch_text(rows, notes)}],
        )
        if response.stop_reason == "refusal":
            raise ValueError("the model declined this batch")
        text = "".join(block.text for block in response.content if block.type == "text")
        try:
            return parse_batch(text, rows, tax, labelled_by=f"api:{response.model}")
        except ValueError as exc:
            error = exc
    raise ValueError(f"answer did not match the label format: {error}")


def label_pending(cfg, client=None, model=None):
    """Label everything pending. Returns (labelled, failed) review counts.
    Labels are saved batch by batch, so an interrupted run keeps its progress."""
    rows = pending(cfg)
    if not rows:
        return 0, 0
    client = client or anthropic.Anthropic()
    model = model or cfg.get("llm_model", DEFAULT_MODEL)
    tax = load_taxonomy()
    system = build_system(tax)
    done = failed = 0
    for start in range(0, len(rows), BATCH):
        chunk = rows[start:start + BATCH]
        try:
            labels = label_batch(client, model, system, chunk, tax, known_notes(cfg))
        except ValueError as exc:
            print(f"  batch at {start}: skipped ({exc})")
            failed += len(chunk)
            continue
        except anthropic.AuthenticationError:
            print("  API key rejected; stopping.")
            return done, len(rows) - done
        except anthropic.RateLimitError:
            print("  rate limited after retries; stopping. Re-run later to finish.")
            return done, len(rows) - done
        except anthropic.APIStatusError as exc:
            print(f"  API error {exc.status_code}; stopping. Re-run later to finish.")
            return done, len(rows) - done
        except anthropic.APIConnectionError:
            print("  could not reach the API; stopping.")
            return done, len(rows) - done
        save_labels(cfg, labels)
        done += len(labels)
        print(f"  labelled {done} of {len(rows)}")
    return done, failed


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    cfg = load_config()
    if not has_credentials():
        print("No API credentials in the environment. Set ANTHROPIC_API_KEY, "
              "or label with the play-ratings skill in Claude Code.")
        return
    done, failed = label_pending(cfg)
    print(f"{done} reviews labelled, {failed} left pending")


if __name__ == "__main__":
    main()
