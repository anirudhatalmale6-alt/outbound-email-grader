"""
The archive-mailbox logic.

This is the part with the most room to be quietly wrong. Everything in the
archive arrives mixed together -- several producers, several prospects, replies
and outbound in one pile -- and the sequence position is derived rather than
given. Get it wrong and a first contact gets graded as a follow-up, or two
producers working the same account inflate each other's touch numbers.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from grader import archive
from grader.checks import Email
from grader.config import Config, Producer

PASSED = 0
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED
    if condition:
        PASSED += 1
    else:
        FAILED.append(f"{name}{(': ' + detail) if detail else ''}")


PRODUCERS = {"joe@allaccessptv.com", "maria@allaccessptv.com"}


def msg(sender: str, to: str, day: int, subject: str = "Hello") -> Email:
    return Email(
        message_id=f"{sender}-{to}-{day}",
        sender=sender,
        to=[to],
        subject=subject,
        date=f"2026-08-{day:02d}T10:00:00+00:00",
        body_text="Hi there, would you be open to a short call?",
    )


def test_touch_numbering() -> None:
    thread = [
        msg("joe@allaccessptv.com", "dana@acme.com", 1),
        msg("joe@allaccessptv.com", "dana@acme.com", 5),
        msg("joe@allaccessptv.com", "dana@acme.com", 9),
    ]
    archive.annotate_thread(thread, PRODUCERS)
    check("touch: numbered in order", [m.touch for m in thread] == [1, 2, 3],
          str([m.touch for m in thread]))
    check("touch: first is not a follow-up", not thread[0].is_follow_up)
    check("touch: second is a follow-up", thread[1].is_follow_up)
    check("touch: no reply detected", not any(m.prospect_replied for m in thread))


def test_out_of_order_dates() -> None:
    """Gmail does not promise the messages come back in order."""
    thread = [
        msg("joe@allaccessptv.com", "dana@acme.com", 9),
        msg("joe@allaccessptv.com", "dana@acme.com", 1),
        msg("joe@allaccessptv.com", "dana@acme.com", 5),
    ]
    archive.annotate_thread(thread, PRODUCERS)
    by_day = {m.date[8:10]: m.touch for m in thread}
    check("order: earliest is touch 1", by_day["01"] == 1, str(by_day))
    check("order: middle is touch 2", by_day["05"] == 2, str(by_day))
    check("order: latest is touch 3", by_day["09"] == 3, str(by_day))


def test_prospect_reply_detected() -> None:
    thread = [
        msg("joe@allaccessptv.com", "dana@acme.com", 1),
        msg("dana@acme.com", "joe@allaccessptv.com", 2),      # she wrote back
        msg("joe@allaccessptv.com", "dana@acme.com", 3),
    ]
    archive.annotate_thread(thread, PRODUCERS)
    outbound = [m for m in thread if m.sender in PRODUCERS]
    check("reply: flagged on every outbound message",
          all(m.prospect_replied for m in outbound))
    check("reply: inbound does not consume a touch number",
          [m.touch for m in outbound] == [1, 2], str([m.touch for m in outbound]))


def test_two_producers_same_thread() -> None:
    """Two producers working one account must not inflate each other."""
    thread = [
        msg("joe@allaccessptv.com", "dana@acme.com", 1),
        msg("maria@allaccessptv.com", "dana@acme.com", 2),
        msg("joe@allaccessptv.com", "dana@acme.com", 3),
    ]
    archive.annotate_thread(thread, PRODUCERS)
    joe = [m.touch for m in thread if m.sender.startswith("joe")]
    maria = [m.touch for m in thread if m.sender.startswith("maria")]
    check("two producers: joe counts his own", joe == [1, 2], str(joe))
    check("two producers: maria starts at 1", maria == [1], str(maria))


def test_different_prospects_same_thread() -> None:
    """One producer writing to two people does not make the second a
    follow-up."""
    thread = [
        msg("joe@allaccessptv.com", "dana@acme.com", 1),
        msg("joe@allaccessptv.com", "raj@othercorp.com", 1),
    ]
    archive.annotate_thread(thread, PRODUCERS)
    check("prospects: both are first contacts",
          [m.touch for m in thread] == [1, 1], str([m.touch for m in thread]))


def test_unknown_sender_ignored() -> None:
    """Mail in the archive from somebody who is not a producer must never be
    attributed to one."""
    thread = [
        msg("stranger@elsewhere.com", "dana@acme.com", 1),
        msg("joe@allaccessptv.com", "dana@acme.com", 2),
    ]
    archive.annotate_thread(thread, PRODUCERS)
    stranger, joe = thread
    check("unknown: not numbered", stranger.touch == 1)
    check("unknown: producer still starts at 1", joe.touch == 1, str(joe.touch))
    check("unknown: counts as a reply", joe.prospect_replied)


def test_undated_message_does_not_crash() -> None:
    thread = [
        msg("joe@allaccessptv.com", "dana@acme.com", 1),
        Email(message_id="x", sender="joe@allaccessptv.com", to=["dana@acme.com"],
              date="not a date", body_text="hi"),
    ]
    archive.annotate_thread(thread, PRODUCERS)
    check("undated: survives", sorted(m.touch for m in thread) == [1, 2],
          str([m.touch for m in thread]))


def test_cc_does_not_start_a_sequence() -> None:
    a = msg("joe@allaccessptv.com", "dana@acme.com", 1)
    b = msg("joe@allaccessptv.com", "dana@acme.com", 2)
    b.cc = ["assistant@acme.com"]
    archive.annotate_thread([a, b], PRODUCERS)
    check("cc: still counts as touch 2", b.touch == 2, str(b.touch))


def test_coverage_warnings() -> None:
    cfg = Config()
    cfg.archive_mailbox = "cortex@allaccessptv.com"
    cfg.producers = [
        Producer(email="joe@allaccessptv.com", name="Joe Bianco"),
        Producer(email="maria@allaccessptv.com", name="Maria Lopez"),
        Producer(email="old@allaccessptv.com", name="Left Last Year", active=False),
    ]

    warnings = archive.coverage_warnings(cfg, {"joe@allaccessptv.com"}, 1)
    check("coverage: one warning", len(warnings) == 1, str(warnings))
    check("coverage: names the silent producer", "Maria Lopez" in warnings[0])
    check("coverage: names the archive", "cortex@allaccessptv.com" in warnings[0])
    check("coverage: mentions the BCC rule", "BCC" in warnings[0])
    check("coverage: inactive producer ignored",
          not any("Left Last Year" in w for w in warnings))

    quiet = archive.coverage_warnings(cfg, set(), 1)
    check("coverage: all silent warns for both", len(quiet) == 2, str(len(quiet)))

    none = archive.coverage_warnings(
        cfg, {"joe@allaccessptv.com", "maria@allaccessptv.com"}, 1)
    check("coverage: nothing to warn about", none == [], str(none))


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
