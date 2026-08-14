"""
Everything except the two network calls.

Gmail and Claude are the only parts that need the outside world, so both are
faked here and the rest of the pipeline -- filtering, scoring, storage,
report generation -- runs for real against realistic messages.
"""

from __future__ import annotations

import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from grader import checks, classify, report, store
from grader.checks import Email
from grader.config import Config, Producer
from grader.grade import Issue, Judgement
from grader.score import ProducerSummary, Result, letter

PASSED = 0
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED
    if condition:
        PASSED += 1
    else:
        FAILED.append(f"{name}{(': ' + detail) if detail else ''}")


def make_config() -> Config:
    cfg = Config()
    cfg.domain = "allaccess.com"
    cfg.internal_domains = ["allaccess.com"]
    cfg.exclude_recipients = ["accountant@example.com", "legal.example.com"]
    cfg.producers = [Producer(email="joe@allaccess.com", name="Joe Smith")]
    cfg.manager_email = "boss@allaccess.com"
    return cfg


# ---------------------------------------------------------------------------
# classify
# ---------------------------------------------------------------------------


def test_classify() -> None:
    cfg = make_config()
    body = "Hi Dana,\n\nWould you be open to a short call next week?\n\nJoe"

    cold = Email(message_id="1", to=["dana@acme.com"], subject="Your Q3 plan",
                 body_text=body)
    check("classify: cold email graded", classify.classify(cold, cfg).graded)

    internal = Email(message_id="2", to=["sam@allaccess.com"], subject="Lunch",
                     body_text=body)
    v = classify.classify(internal, cfg)
    check("classify: internal skipped", not v.graded)
    check("classify: internal reason", v.reason == "internal only", v.reason)

    reply = Email(message_id="3", to=["dana@acme.com"], subject="Re: Your Q3 plan",
                  in_reply_to="<x@y>", body_text=body)
    v = classify.classify(reply, cfg)
    check("classify: reply skipped", not v.graded)
    check("classify: reply reason", "reply" in v.reason, v.reason)

    excluded = Email(message_id="4", to=["accountant@example.com"], subject="Hi",
                     body_text=body)
    check("classify: excluded address skipped",
          not classify.classify(excluded, cfg).graded)

    excluded_domain = Email(message_id="5", to=["anyone@legal.example.com"],
                            subject="Hi", body_text=body)
    check("classify: excluded domain skipped",
          not classify.classify(excluded_domain, cfg).graded)

    robot = Email(message_id="6", to=["noreply@acme.com"], subject="Hi",
                  body_text=body)
    check("classify: automated recipient skipped",
          not classify.classify(robot, cfg).graded)

    tiny = Email(message_id="7", to=["dana@acme.com"], subject="Hi", body_text="ok")
    check("classify: too-short skipped", not classify.classify(tiny, cfg).graded)

    none = Email(message_id="8", subject="Hi", body_text=body)
    check("classify: no recipient skipped", not classify.classify(none, cfg).graded)

    draft = Email(message_id="9", to=["dana@acme.com"], subject="Hi",
                  body_text=body, labels=["DRAFT"])
    check("classify: draft skipped", not classify.classify(draft, cfg).graded)

    # Mixed internal + external must still be graded -- the external person
    # received it.
    mixed = Email(message_id="10", to=["dana@acme.com"], cc=["sam@allaccess.com"],
                  subject="Your Q3 plan", body_text=body)
    check("classify: mixed recipients graded", classify.classify(mixed, cfg).graded)

    # first_contact_only off means replies get graded too
    cfg2 = make_config()
    cfg2.grade_first_contact_only = False
    check("classify: replies graded when configured",
          classify.classify(reply, cfg2).graded)


def test_split_counts() -> None:
    cfg = make_config()
    body = "Hi Dana,\n\nWould you be open to a short call next week?\n\nJoe"
    emails = [
        Email(message_id="a", to=["dana@acme.com"], subject="X", body_text=body),
        Email(message_id="b", to=["sam@allaccess.com"], subject="X", body_text=body),
        Email(message_id="c", to=["pat@allaccess.com"], subject="X", body_text=body),
        Email(message_id="d", to=["dana@acme.com"], subject="Re: X",
              in_reply_to="<1@2>", body_text=body),
    ]
    keep, skipped = classify.split(emails, cfg)
    check("split: one kept", len(keep) == 1, str(len(keep)))
    check("split: two internal counted", skipped.get("internal only") == 2,
          str(skipped))
    check("split: one reply counted",
          skipped.get("reply, not a first contact") == 1, str(skipped))
    check("split: nothing lost", len(keep) + sum(skipped.values()) == len(emails))


# ---------------------------------------------------------------------------
# scoring
# ---------------------------------------------------------------------------


GOOD_BODY = (
    "Hi Dana,\n\nI saw Acme is expanding the Midwest team. We place broadcast "
    "segments for companies at that stage.\n\nWould you be open to a short call "
    "next week?\n\nBest,\nJoe Smith\nAll Access\n1200 Market Street, Suite 400, "
    "Dallas, TX 75201\nIf you would rather not hear from me, just reply and say so."
)


def result_for(subject: str, body: str, judged: int = 80) -> Result:
    email = Email(message_id=subject[:8], to=["dana@acme.com"], subject=subject,
                  body_text=body)
    return Result(
        email=email,
        findings=checks.run_all(email),
        judgement=Judgement(score=judged, summary="Reads well.",
                            did_well=["Specific opening."],
                            issues=[Issue("The ask", "Slightly vague.",
                                          "Name a day.", "medium")]),
    )


def test_result_scoring() -> None:
    clean = result_for("Question about Acme's Q3 plan", GOOD_BODY, judged=90)
    check("result: clean mechanical is 100", clean.mechanical == 100,
          str(clean.mechanical))
    check("result: overall blends halves", clean.overall == 95, str(clean.overall))
    check("result: grade A", clean.grade == "A", clean.grade)

    bad = result_for("ACT NOW!!! FREE MONEY", "CLICK HERE http://bit.ly/x")
    check("result: bad mechanical is low", bad.mechanical < 50, str(bad.mechanical))
    check("result: blocking findings surfaced", len(bad.blocking) >= 2)

    # A model failure must not silently become a zero.
    broken = result_for("Question about Acme's Q3 plan", GOOD_BODY)
    broken.judgement = Judgement(error="timeout")
    check("result: model failure falls back to mechanical",
          broken.overall == broken.mechanical == 100, str(broken.overall))

    check("letter: boundaries", [letter(n) for n in (90, 80, 70, 60, 59)]
          == ["A", "B", "C", "D", "F"])


def test_summary() -> None:
    s = ProducerSummary(email="joe@allaccess.com", name="Joe Smith")
    # Two of these share the same fault (no postal address, no opt-out), which
    # is what recurring() is supposed to surface. A habit is coachable; a
    # one-off is noise.
    no_signature = ("Hi Dana,\n\nWe place broadcast segments for firms at your "
                    "stage. Would you be open to a short call next week?\n\nJoe")
    s.results = [
        result_for("Question about Acme's Q3 plan", GOOD_BODY, judged=90),
        result_for("ACT NOW!!! BUY NOW", "CLICK HERE!!! http://bit.ly/x", judged=30),
        result_for("Following up on the RFP", no_signature, judged=70),
    ]
    s.skipped = {"internal only": 4, "reply, not a first contact": 9}

    check("summary: counts", s.graded_count == 3 and s.skipped_count == 13)
    check("summary: average sane", 0 < s.average < 100, str(s.average))
    check("summary: worst is the bad one",
          s.worst.email.subject.startswith("ACT NOW"))
    check("summary: best is a good one", s.best.overall >= s.worst.overall)
    check("summary: compliance counted", s.compliance_count >= 1,
          str(s.compliance_count))

    recurring = s.recurring(minimum=2)
    check("summary: recurring found", len(recurring) >= 1, str(recurring))
    ids_found = {fid for fid, _, _ in recurring}
    check("summary: recurring names the shared fault",
          "compliance.no_postal_address" in ids_found, str(ids_found))
    for _, n, _ in recurring:
        check("summary: recurring counts <= emails", n <= s.graded_count)
    # A fault appearing once must NOT be reported as a habit.
    once = ProducerSummary(email="solo@allaccess.com", name="Solo")
    once.results = [result_for("ACT NOW!!!", "CLICK HERE http://bit.ly/x")]
    check("summary: single fault is not a habit", once.recurring(minimum=2) == [])

    empty = ProducerSummary(email="x@y.com", name="Nobody")
    check("summary: empty averages to 0 not a crash", empty.average == 0)
    check("summary: empty has no worst", empty.worst is None)


# ---------------------------------------------------------------------------
# reports
# ---------------------------------------------------------------------------


def build_summaries() -> list[ProducerSummary]:
    a = ProducerSummary(email="joe@allaccess.com", name="Joe Smith")
    a.results = [
        result_for("Question about Acme's Q3 media plan", GOOD_BODY, judged=88),
        result_for("Following up on the RFP", GOOD_BODY, judged=74),
    ]
    a.skipped = {"internal only": 6, "reply, not a first contact": 11}

    b = ProducerSummary(email="maria@allaccess.com", name="Maria Lopez")
    b.results = [
        result_for("ACT NOW - LIMITED TIME OFFER!!!",
                   "HI {{first_name}}!!! This is a RISK FREE limited time offer, "
                   "act now! CLICK HERE http://bit.ly/xyz to buy now!!!", judged=28),
        result_for("Re: our chat", "Hi there, just circling back. Thanks", judged=45),
    ]
    b.skipped = {"internal only": 2}

    c = ProducerSummary(email="quiet@allaccess.com", name="Quiet Colleague")
    return [a, b, c]


def test_manager_report() -> None:
    summaries = build_summaries()
    html = report.manager_html(summaries, date(2026, 8, 14),
                               {"joe@allaccess.com": 70}, ["a problem happened"],
                               shadow=True)
    check("manager: is html", html.strip().startswith("<div"))
    check("manager: names both producers",
          "Joe Smith" in html and "Maria Lopez" in html)
    check("manager: shows shadow warning", "shadow mode" in html.lower())
    check("manager: shows the error", "a problem happened" in html)
    check("manager: mentions compliance", "ompliance" in html)
    check("manager: quiet producer listed", "Quiet Colleague" in html)
    check("manager: no raw braces left", "{date}" not in html)

    # Nobody's email body may leak into the manager report.
    check("manager: no body text leaked", "Would you be open" not in html)

    text = report.manager_text(summaries, date(2026, 8, 14), shadow=True)
    check("manager text: has names", "Joe Smith" in text and "Maria Lopez" in text)
    check("manager text: shadow noted", "SHADOW" in text)


def test_producer_report() -> None:
    summaries = build_summaries()
    maria = summaries[1]
    html = report.producer_html(maria, date(2026, 8, 14), previous=52)
    check("producer: is html", html.strip().startswith("<div"))
    check("producer: shows own subject", "ACT NOW" in html)
    check("producer: shows a fix", "shorten" in html.lower() or "reply" in html.lower())
    check("producer: shows the merge-tag catch", "first_name" in html)

    # The critical privacy property: one producer's report must contain
    # nothing at all about another producer.
    check("producer: no other producer named", "Joe Smith" not in html)
    check("producer: no other subject", "Following up on the RFP" not in html)

    empty = summaries[2]
    empty_html = report.producer_html(empty, date(2026, 8, 14), previous=None)
    check("producer: empty day handled", "No first-contact emails" in empty_html)

    text = report.producer_text(maria, date(2026, 8, 14))
    check("producer text: has score", "/100" in text)


def test_html_escaping() -> None:
    """A subject line with a tag in it must not become markup."""
    nasty = Result(
        email=Email(message_id="x", to=["a@b.com"],
                    subject='<script>alert("xss")</script>',
                    body_text=GOOD_BODY),
        findings=[],
        judgement=Judgement(score=80, summary="<b>bold</b>"),
    )
    # The greeting uses the first name only, so put the markup there.
    s = ProducerSummary(email="joe@allaccess.com", name="<b>Joe</b> Smith")
    s.results = [nasty]
    html = report.producer_html(s, date(2026, 8, 14), None)
    check("escape: script tag neutralised", "<script>" not in html)
    check("escape: entity present", "&lt;script&gt;" in html)
    check("escape: name escaped", "&lt;b&gt;Joe&lt;/b&gt;" in html,
          "greeting did not escape the name")
    check("escape: summary escaped", "<b>bold</b>" not in html)
    check("escape: greeting rendered", "Hi " in html)

    # A producer with no name configured must not produce "Hi ,".
    unnamed = ProducerSummary(email="anon@allaccess.com", name="")
    unnamed.results = [nasty]
    check("escape: no name means no greeting",
          "Hi ," not in report.producer_html(unnamed, date(2026, 8, 14), None))


# ---------------------------------------------------------------------------
# storage
# ---------------------------------------------------------------------------


def test_store() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "t.sqlite3"
        result = result_for("Question about Acme's Q3 plan", GOOD_BODY)
        with store.connect(db) as conn:
            run_id = store.start_run(conn)
            check("store: run id", isinstance(run_id, int) and run_id > 0)
            check("store: nothing graded yet",
                  store.already_graded(conn, ["Question"]) == set())
            store.record(conn, "joe@allaccess.com", result)
            store.finish_run(conn, run_id, 1, 1, 0, [])

        with store.connect(db) as conn:
            seen = store.already_graded(conn, [result.email.message_id, "other"])
            check("store: remembers across connections",
                  seen == {result.email.message_id}, str(seen))

            # Re-recording the same message must not create a duplicate.
            store.record(conn, "joe@allaccess.com", result)
            rows = conn.execute("SELECT COUNT(*) c FROM graded").fetchone()["c"]
            check("store: no duplicate rows", rows == 1, str(rows))

            # The body must never be persisted.
            dump = " ".join(
                str(v) for row in conn.execute("SELECT * FROM graded").fetchall()
                for v in tuple(row)
            )
            check("store: body not stored", "Would you be open" not in dump)

            check("store: trend returns rows", isinstance(store.trend(conn, "joe@allaccess.com"), list))
            check("store: previous average empty is None",
                  store.previous_average(conn, "nobody@x.com") is None)

        # A large batch must not hit sqlite's variable limit.
        with store.connect(db) as conn:
            many = [f"id-{i}" for i in range(1200)]
            check("store: big batch survives",
                  store.already_graded(conn, many) == set())


def main() -> int:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    total = PASSED + len(FAILED)
    print(f"\n  {PASSED}/{total} checks passed")
    if FAILED:
        print("\n  Failures:")
        for f in FAILED:
            print(f"    - {f}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
