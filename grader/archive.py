"""
Reading from a single BCC archive mailbox.

The client already BCCs every producer's outbound mail to one address, which is
a much better arrangement than reaching into each producer's mailbox: one
account to authorise instead of the whole domain, and the archive is a copy, so
nothing here can touch anybody's real mail even by accident.

It does move two problems into this file.

Attribution. Every message in the archive is from a different person, so the
producer is read off the From header rather than from whose mailbox we opened.
Anything from an address that is not on the producer list is ignored outright.

Sequence position. The archive holds the whole thread, so a message can be
placed as first contact, first follow-up, second follow-up and so on -- and,
where inbound mail is archived too, it can tell chasing silence apart from
answering someone who wrote back. Those are different jobs and grading them
against the same standard is how a grader starts producing nonsense.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .checks import Email
from .config import Config
from .gmail import GmailError, _service, parse_message


def _sort_key(email: Email) -> tuple:
    try:
        return (0, datetime.fromisoformat(email.date))
    except Exception:
        # Undated messages sort last rather than crashing the run.
        return (1, datetime.max.replace(tzinfo=timezone.utc))


def _primary_recipient(email: Email) -> str:
    """The person actually being pitched. Cc'd colleagues do not start a new
    sequence."""
    return email.to[0] if email.to else (email.cc[0] if email.cc else "")


def annotate_thread(messages: list[Email], producers: set[str]) -> None:
    """Fill in touch number and prospect_replied for one thread, in place."""
    ordered = sorted(messages, key=_sort_key)

    # Anything in the thread not written by a producer is the other side
    # replying. If inbound mail is not archived this stays False, which is the
    # safe default -- it means a follow-up is treated as chasing silence.
    replied = any(m.sender and m.sender not in producers for m in ordered)

    counts: dict[str, int] = {}
    for message in ordered:
        if message.sender not in producers:
            continue
        # Count per (sender, prospect) so two producers working the same
        # company do not inflate each other's touch numbers.
        key = f"{message.sender}|{_primary_recipient(message)}"
        counts[key] = counts.get(key, 0) + 1
        message.touch = counts[key]
        message.prospect_replied = replied


def fetch_archive(cfg: Config, since: datetime | None = None) -> list[Email]:
    """Every producer message in the archive mailbox since `since`.

    Whole threads are fetched rather than individual messages, because the
    thread is what makes it possible to say "this is the second follow-up" --
    and a follow-up graded as though it were a first contact scores badly for
    no reason a producer could act on.
    """
    since = since or (datetime.now(timezone.utc) - timedelta(days=cfg.lookback_days))
    if not cfg.archive_mailbox:
        raise GmailError("No archive mailbox configured.")

    service = _service(cfg, cfg.archive_mailbox)
    producers = {p.email.lower() for p in cfg.active_producers}

    # Widened by a day because Gmail's after: takes a date, not a time. The
    # exact cutoff is applied below.
    query = f"after:{(since - timedelta(days=1)):%Y/%m/%d}"

    thread_ids: list[str] = []
    page_token = None
    try:
        while True:
            resp = (
                service.users()
                .threads()
                .list(userId="me", q=query, pageToken=page_token, maxResults=500)
                .execute()
            )
            thread_ids.extend(t["id"] for t in resp.get("threads", []))
            page_token = resp.get("nextPageToken")
            if not page_token or len(thread_ids) >= cfg.max_threads:
                break
    except Exception as exc:
        raise GmailError(
            f"Could not list mail in {cfg.archive_mailbox}: {exc}"
        ) from exc

    out: list[Email] = []
    for thread_id in thread_ids[: cfg.max_threads]:
        try:
            thread = (
                service.users()
                .threads()
                .get(userId="me", id=thread_id, format="full")
                .execute()
            )
        except Exception:
            # One unreadable thread must not lose the rest of the day.
            continue

        messages = [parse_message(m) for m in thread.get("messages", [])]
        annotate_thread(messages, producers)

        for message in messages:
            if message.sender not in producers:
                continue
            if not _within(message, since):
                continue
            out.append(message)

    return out


def _within(email: Email, since: datetime) -> bool:
    try:
        return datetime.fromisoformat(email.date) >= since
    except Exception:
        # Unparseable date: keep it. store.py de-duplicates on message id, so
        # the cost of keeping it is nil and the cost of dropping it is a
        # silently ungraded email.
        return True


def coverage_warnings(
    cfg: Config, seen_senders: set[str], quiet_days: int
) -> list[str]:
    """Producers the archive has heard nothing from.

    This exists because of a failure mode the per-mailbox version did not have:
    with an archive, "no emails from Maria" can mean Maria sent none, or it can
    mean Maria's BCC rule was never set up. Those look identical in the data and
    only one of them is Maria's fault, so the report has to say it out loud
    rather than quietly showing her a zero.
    """
    warnings: list[str] = []
    for producer in cfg.active_producers:
        if producer.email.lower() in seen_senders:
            continue
        warnings.append(
            f"Nothing from {producer.display} ({producer.email}) in the last "
            f"{quiet_days} day(s). Either they sent no cold email, or their "
            f"mail is not reaching {cfg.archive_mailbox} - worth checking the "
            "BCC rule before reading anything into the score."
        )
    return warnings
