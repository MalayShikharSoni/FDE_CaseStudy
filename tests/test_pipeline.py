"""Tests for the parts where a silent mistake would put a wrong number in the report."""
from datetime import datetime

import pytest

from ratings import classify, clean, metrics, validate

TAX = classify.load_taxonomy()


def row(rid, score, text="some text", at="2026-01-01 10:00:00"):
    return {"reviewId": rid, "score": score, "content": text, "at": at,
            "dt": datetime.strptime(at, "%Y-%m-%d %H:%M:%S"), "source": "play"}


# --- label parsing -----------------------------------------------------------

def test_short_line_uses_theme_defaults():
    idx, lab = classify.parse_line("17|login_otp", TAX)
    assert idx == 17
    assert lab == {"theme": "login_otp", "theme2": "", "owner": "app", "safety": False,
                   "after_update": False, "note": ""}


def test_other_must_say_what_the_complaint_is():
    with pytest.raises(ValueError, match="needs a short note"):
        classify.parse_line("4|other", TAX)
    _, lab = classify.parse_line("4|other||app|0|0|Call Audio Quality", TAX)
    assert lab["theme"] == "other" and lab["note"] == "call audio quality"
    # a note on any other theme is dropped: notes only describe what has no theme
    _, lab = classify.parse_line("5|login_otp||app|0|0|stray note", TAX)
    assert lab["note"] == ""


def test_batch_lists_notes_already_in_use():
    text = classify.batch_text([row("a", 1, "call keeps dropping")], notes=["call audio quality"])
    assert text.splitlines()[0].startswith("# notes already in use")
    assert "call audio quality" in text


def test_full_line():
    _, lab = classify.parse_line("3|no_show|driver_behaviour|ops|1|0", TAX)
    assert lab["theme2"] == "driver_behaviour" and lab["owner"] == "ops"
    assert lab["safety"] is True and lab["after_update"] is False


def test_theme2_same_as_theme_is_dropped():
    _, lab = classify.parse_line("1|no_show|no_show", TAX)
    assert lab["theme2"] == ""


@pytest.mark.parametrize("line", [
    "1|made_up_theme",            # theme not in the taxonomy
    "1|no_show|made_up",          # theme2 not in the taxonomy
    "1|no_show||nobody",          # owner not allowed
    "1|no_show||ops|yes|0",       # flag not 0/1
    "no_show",                    # no index
])
def test_bad_lines_are_rejected(line):
    with pytest.raises(ValueError):
        classify.parse_line(line, TAX)


def test_batch_must_label_every_review_once():
    rows = [row("a", 1), row("b", 1)]
    with pytest.raises(ValueError, match="no label"):
        classify.parse_batch("1|login_otp", rows, TAX, "test")
    with pytest.raises(ValueError, match="twice"):
        classify.parse_batch("1|login_otp\n1|no_show", rows, TAX, "test")
    with pytest.raises(ValueError, match="outside"):
        classify.parse_batch("1|login_otp\n3|no_show", rows, TAX, "test")
    labels = classify.parse_batch("2|no_show\n1|login_otp", rows, TAX, "test")
    assert [l["reviewId"] for l in labels] == ["a", "b"]
    assert labels[0]["labelled_by"] == "test"


# --- cleaning ----------------------------------------------------------------

def test_check_finds_duplicates_and_bad_scores():
    rows = [row("a", 1), row("a", 5), row("b", 7)]
    problems = clean.check(rows)
    assert any("duplicate" in p for p in problems)
    assert any("outside 1-5" in p for p in problems)
    assert clean.check([row("a", 1), row("b", 5)]) == []


def test_coverage_compares_with_play_count():
    cov = clean.coverage([row("a", 1), row("b", 5)], {"reviews": 4})
    assert cov["pulled"] == 2 and cov["coverage_pct"] == 50.0


# --- metrics -----------------------------------------------------------------

def test_histogram_stats():
    h = metrics.histogram_stats([10, 0, 0, 0, 90])   # 10 one-star, 90 five-star
    assert h["ratings"] == 100
    assert h["avg"] == 4.6
    assert h["avg_without_1_star"] == 5.0
    assert h["low_ratings"] == 10 and h["low_avg"] == 1.0


def test_rating_lift_matches_hand_calculation():
    hist = [10, 0, 0, 0, 90]
    # moving ALL ten 1-star ratings to 4 stars: (10*4 + 90*5)/100 = 4.9, a lift of 0.3
    assert metrics.rating_lift(hist, 1.0, to_star=4) == 0.3
    # half of them: lift 0.15
    assert metrics.rating_lift(hist, 0.5, to_star=4) == 0.15
    assert metrics.rating_lift(hist, 0.0) == 0.0


def test_review_stats():
    s = metrics.review_stats([row("a", 1), row("b", 2), row("c", 5), row("d", 5)])
    assert s == {"n": 4, "avg": 3.25, "pct_1": 25.0, "pct_low": 50.0, "pct_5": 50.0}


def test_wilson_low_is_cautious_with_small_samples():
    # 5 of 10 and 500 of 1000 are both 50%, but the small sample proves much less
    assert metrics.wilson_low(5, 10) < metrics.wilson_low(500, 1000) < 0.5


# --- validation --------------------------------------------------------------

def test_kappa():
    assert validate.cohen_kappa([("a", "a"), ("b", "b"), ("a", "a"), ("b", "b")]) == 1.0
    # labellers who agree exactly as often as chance would predict score 0
    assert validate.cohen_kappa([("a", "a"), ("a", "b"), ("b", "a"), ("b", "b")]) == 0.0


# --- API labelling backend (stubbed client: no network, no key) ----------------

class FakeClient:
    """Stands in for anthropic.Anthropic(): returns canned answers and records requests."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.requests = []
        self.beta = self
        self.messages = self

    def create(self, **kwargs):
        from types import SimpleNamespace
        self.requests.append(kwargs)
        text = self.answers.pop(0)
        return SimpleNamespace(stop_reason="end_turn", model="fake-model",
                               content=[SimpleNamespace(type="text", text=text)])


def test_api_backend_parses_labels_and_sends_the_rules():
    from ratings import llm
    rows = [row("a", 1, "otp not coming"), row("b", 1, "marked no show for no reason")]
    client = FakeClient(["1|login_otp\n2|no_show"])
    labels = llm.label_batch(client, "fake-model", llm.build_system(TAX), rows, TAX)
    assert [l["theme"] for l in labels] == ["login_otp", "no_show"]
    assert labels[0]["labelled_by"] == "api:fake-model"
    request = client.requests[0]
    assert "login_otp" in request["system"][0]["text"]          # taxonomy is in the prompt
    assert "otp not coming" in request["messages"][0]["content"]  # and the review text


def test_api_backend_retries_once_then_gives_up():
    from ratings import llm
    rows = [row("a", 1), row("b", 1)]
    system = llm.build_system(TAX)
    # first answer is unusable, second is fine
    client = FakeClient(["sorry, here are my thoughts", "1|login_otp\n2|no_show"])
    assert len(llm.label_batch(client, "m", system, rows, TAX)) == 2
    assert len(client.requests) == 2
    # two unusable answers: the batch is rejected rather than half-saved
    client = FakeClient(["1|login_otp", "1|not_a_theme\n2|no_show"])
    with pytest.raises(ValueError):
        llm.label_batch(client, "m", system, rows, TAX)


# --- themes created on the go ---------------------------------------------------

def test_recurring_note_becomes_a_theme():
    rows = [row(str(i), 1) for i in range(7)]
    labels = {str(i): {"theme": "other", "note": "driver call fails"} for i in range(5)}
    labels["5"] = {"theme": "other", "note": "one-off oddity"}
    labels["6"] = {"theme": "login_otp", "note": ""}
    promoted = metrics.promoted_notes(rows, labels, min_reviews=5)
    assert promoted == {"driver call fails"}          # five reviews: promoted
    assert metrics.theme_key(labels["0"], promoted) == "new:driver call fails"
    assert metrics.theme_key(labels["5"], promoted) == "other"      # a single review stays in `other`
    assert metrics.theme_key(labels["6"], promoted) == "login_otp"  # written themes are untouched


# --- personal details are removed before review text is saved ---------------------

def test_redaction():
    from ratings.pull import one_line
    assert one_line("Driver for cab number HR 47-G-7055 is rude") == "Driver for cab number [vehicle number removed] is rude"
    assert one_line("call me on 9876543210 or +91 9876543210") == "call me on [phone number removed] or [phone number removed]"
    assert one_line("mail someone@gmail.com") == "mail [email removed]"
    # the company's own support address, dates and version numbers are left alone
    for text in ["write to app@moveinsync.com", "booked on 06-10-2026", "version 12.19.0", "on 12 Oct 2026"]:
        assert one_line(text) == text
