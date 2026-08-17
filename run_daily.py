#!/usr/bin/env python3
"""
The daily run. Point cron at this.

    python3 run_daily.py                 grade yesterday and send the reports
    python3 run_daily.py --dry-run       grade, write the reports to disk, send nothing
    python3 run_daily.py --days 7        widen the window
    python3 run_daily.py --producer a@b  just one person
    python3 run_daily.py --regrade       ignore the "already graded" record

Nothing is sent to a producer while shadow_mode is on in config.yaml. That is
deliberate: the first week of grades should be read by a human before anybody
gets told their email scored 54.
"""

from __future__ import annotations

import argparse
import sys
import traceback
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from grader import archive, checks, classify, gmail, report, store
from grader.config import Config, ConfigError, load
from grader.grade import Grader, Judgement, load_rubric
from grader.score import ProducerSummary, Result

OUT_DIR = Path(__file__).resolve().parent / "reports"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Grade outbound email and report on it.")
    p.add_argument("--dry-run", action="store_true",
                   help="write reports to ./reports instead of emailing them")
    p.add_argument("--days", type=int, default=None,
                   help="how many days back to look (default: from config)")
    p.add_argument("--producer", default="",
                   help="grade only this address")
    p.add_argument("--regrade", action="store_true",
                   help="grade messages even if they were graded before")
    p.add_argument("--no-model", action="store_true",
                   help="run the rule checks only, skip Claude (free, fast)")
    return p.parse_args()


def _fetch_all(cfg: Config, since, producers, errors: list[str]):
    """Returns {producer_email: [Email]}.

    Archive mode is one read of one mailbox, then messages are attributed by
    who sent them. Per-mailbox mode opens each producer's Sent folder in turn.
    """
    by_producer: dict[str, list] = {p.email.lower(): [] for p in producers}

    if cfg.mode == "archive":
        try:
            for email in archive.fetch_archive(cfg, since):
                by_producer.setdefault(email.sender, []).append(email)
        except gmail.GmailError as exc:
            errors.append(f"archive mailbox {cfg.archive_mailbox}: {exc}")
        return by_producer

    for producer in producers:
        try:
            by_producer[producer.email.lower()] = gmail.fetch_sent(
                cfg, producer.email, since
            )
        except gmail.GmailError as exc:
            errors.append(f"{producer.email}: {exc}")
    return by_producer


def collect(cfg: Config, args: argparse.Namespace, conn, grader: Grader | None):
    """Fetch, filter and grade. Returns (summaries, errors)."""
    since = datetime.now(timezone.utc) - timedelta(days=cfg.lookback_days)
    summaries: list[ProducerSummary] = []
    errors: list[str] = []

    producers = cfg.active_producers
    if args.producer:
        producers = [p for p in producers if p.email.lower() == args.producer.lower()]
        if not producers:
            errors.append(f"No active producer matches {args.producer}")

    by_producer = _fetch_all(cfg, since, producers, errors)

    # An archive can be silently incomplete in a way a Sent folder cannot: a
    # producer whose BCC rule was never set up looks exactly like a producer
    # who sent nothing. Say so rather than showing them a zero.
    if cfg.mode == "archive" and cfg.quiet_producer_warning:
        seen = {addr for addr, mail in by_producer.items() if mail}
        errors.extend(archive.coverage_warnings(cfg, seen, cfg.lookback_days))

    for producer in producers:
        summary = ProducerSummary(email=producer.email, name=producer.name)
        fetched = by_producer.get(producer.email.lower(), [])

        gradeable, skipped = classify.split(fetched, cfg)
        summary.skipped = skipped

        if not args.regrade:
            seen = store.already_graded(conn, [e.message_id for e in gradeable])
            before = len(gradeable)
            gradeable = [e for e in gradeable if e.message_id not in seen]
            if before != len(gradeable):
                summary.skipped["already graded"] = before - len(gradeable)

        for email in gradeable:
            # Set here rather than at parse time: it is a fact about how the
            # company works, not about the message, and both the rules and the
            # model need it.
            email.after_call = cfg.preceded_by_call
            findings = checks.run_all(email, set(cfg.disabled_rules))
            if grader is None:
                judgement = Judgement(error="model skipped (--no-model)")
            else:
                judgement = grader.grade(email)
                if not judgement.ok:
                    errors.append(
                        f"{producer.email}: could not grade "
                        f"\"{email.subject[:40]}\" ({judgement.error})"
                    )
            result = Result(email=email, findings=findings, judgement=judgement)
            summary.results.append(result)
            store.record(conn, producer.email, result)

        summaries.append(summary)
    return summaries, errors


def deliver(cfg: Config, args, summaries, errors, conn) -> None:
    today = date.today()
    previous = {s.email: store.previous_average(conn, s.email) for s in summaries}

    manager_body = report.manager_html(
        summaries, today, previous, errors, cfg.shadow_mode, cfg.disabled_rules
    )
    manager_plain = report.manager_text(
        summaries, today, cfg.shadow_mode, cfg.disabled_rules
    )
    subject = cfg.report_subject_manager.format(date=f"{today:%d %b %Y}")

    if args.dry_run:
        OUT_DIR.mkdir(exist_ok=True)
        path = OUT_DIR / f"{today:%Y-%m-%d}-manager.html"
        path.write_text(manager_body, encoding="utf-8")
        print(f"  wrote {path}")
    else:
        gmail.send_report(cfg, cfg.manager_email, subject, manager_body, manager_plain)
        store.note_report(conn, cfg.manager_email, "manager", f"{today}")
        print(f"  manager report sent to {cfg.manager_email}")

    if cfg.shadow_mode:
        print("  shadow mode on -- producers not emailed")
        return

    lookup = {p.email: p for p in cfg.active_producers}
    for summary in summaries:
        if not summary.results:
            continue
        producer = lookup.get(summary.email)
        to = (producer.report_to if producer else "") or summary.email
        body = report.producer_html(summary, today, previous.get(summary.email))
        plain = report.producer_text(summary, today)
        subj = cfg.report_subject_producer.format(date=f"{today:%d %b %Y}")
        if args.dry_run:
            OUT_DIR.mkdir(exist_ok=True)
            path = OUT_DIR / f"{today:%Y-%m-%d}-{summary.email.replace('@', '_at_')}.html"
            path.write_text(body, encoding="utf-8")
            print(f"  wrote {path}")
            continue
        try:
            gmail.send_report(cfg, to, subj, body, plain)
            store.note_report(conn, to, "producer", f"{today}")
            print(f"  report sent to {to}")
        except gmail.GmailError as exc:
            errors.append(f"could not send report to {to}: {exc}")


def main() -> int:
    args = parse_args()
    try:
        cfg = load()
    except ConfigError as exc:
        print(f"\n  {exc}\n")
        return 1

    if args.days is not None:
        cfg.lookback_days = args.days

    grader: Grader | None = None
    if not args.no_model:
        try:
            grader = Grader(cfg, load_rubric(cfg))
        except (FileNotFoundError, ValueError) as exc:
            print(f"\n  {exc}\n")
            return 1
        except Exception as exc:
            print(f"\n  Could not start the grader: {exc}\n")
            return 1

    print(f"\n  Grading the last {cfg.lookback_days} day(s)...")

    with store.connect(cfg.database) as conn:
        run_id = store.start_run(conn)
        try:
            summaries, errors = collect(cfg, args, conn, grader)
        except Exception:
            traceback.print_exc()
            store.finish_run(conn, run_id, 0, 0, 0, ["run crashed"])
            return 1

        graded = sum(s.graded_count for s in summaries)
        skipped = sum(s.skipped_count for s in summaries)
        print(f"  {graded} graded, {skipped} skipped, {len(errors)} problem(s)")

        if graded == 0 and not errors:
            print("  Nothing to report. No email sent.")
            store.finish_run(conn, run_id, len(summaries), 0, skipped, [])
            return 0

        deliver(cfg, args, summaries, errors, conn)
        store.finish_run(conn, run_id, len(summaries), graded, skipped, errors)

    if errors:
        print("\n  Problems:")
        for e in errors[:10]:
            print(f"    - {e}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
