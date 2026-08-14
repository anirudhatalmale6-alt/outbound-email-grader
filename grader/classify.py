"""
Deciding which emails to grade.

The client asked to grade "every single outgoing email". Taken literally that
means grading a producer's reply to a colleague about lunch against a cold
outreach standard, which produces a bad score for an email that was never
supposed to meet that standard. The score stops meaning anything within a week
and people stop reading the report.

So the grader looks at first-contact outbound mail to people outside the
company, and everything else is counted and reported but not scored. The
report says how many were skipped and why, so nothing disappears quietly.
"""

from __future__ import annotations

from dataclasses import dataclass

from .checks import Email
from .config import Config

# Addresses that are machines, not people.
AUTOMATED_PREFIXES = (
    "noreply", "no-reply", "donotreply", "do-not-reply", "mailer-daemon",
    "postmaster", "bounce", "notifications", "notification", "alerts",
    "automated", "system", "root", "cron", "support+", "reply+",
)


@dataclass
class Verdict:
    graded: bool
    reason: str = ""       # why it was skipped, in words fit for the report


def _domain(address: str) -> str:
    return address.rsplit("@", 1)[-1].lower() if "@" in address else ""


def _is_automated(address: str) -> bool:
    local = address.split("@", 1)[0].lower()
    return any(local.startswith(p) for p in AUTOMATED_PREFIXES)


def classify(email: Email, cfg: Config) -> Verdict:
    internal = set(cfg.all_internal_domains)
    excluded = set(cfg.exclude_recipients)

    if not email.recipients:
        return Verdict(False, "no recipient")

    if email.labels and "DRAFT" in email.labels:
        return Verdict(False, "still a draft")

    external = [
        r for r in email.recipients
        if _domain(r) not in internal
        and r not in excluded
        and _domain(r) not in excluded
        and not _is_automated(r)
    ]
    if not external:
        if all(_domain(r) in internal for r in email.recipients):
            return Verdict(False, "internal only")
        return Verdict(False, "excluded or automated recipient")

    if cfg.grade_first_contact_only and email.is_reply:
        return Verdict(False, "reply, not a first contact")

    if len(email.body.strip()) < cfg.min_body_chars:
        return Verdict(False, "too short to assess")

    return Verdict(True)


def split(emails: list[Email], cfg: Config) -> tuple[list[Email], dict[str, int]]:
    """Returns (gradeable, {reason: count})."""
    keep: list[Email] = []
    skipped: dict[str, int] = {}
    for email in emails:
        verdict = classify(email, cfg)
        if verdict.graded:
            keep.append(email)
        else:
            skipped[verdict.reason] = skipped.get(verdict.reason, 0) + 1
    return keep, skipped
