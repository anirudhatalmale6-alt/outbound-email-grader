#!/usr/bin/env python3
"""
Who is actually sending mail into the archive?

Run this after signing in but before filling in the producer list. It reads the
archive mailbox and prints every address that has sent anything, with counts,
so the producer list can be copied from what is really there rather than typed
from memory.

    python3 discover.py
    python3 discover.py --days 30

Worth doing even when the list is known. A producer whose BCC rule was never set
up simply will not appear here, and that is far easier to notice now than as a
zero in a report three weeks from now.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from grader import config as config_module
from grader.gmail import GmailError, _service, parse_message


def main() -> int:
    parser = argparse.ArgumentParser(
        description="List everyone sending mail into the archive mailbox."
    )
    parser.add_argument("--days", type=int, default=14,
                        help="How far back to look (default 14).")
    parser.add_argument("--max-threads", type=int, default=400,
                        help="Cap on threads read (default 400).")
    args = parser.parse_args()

    try:
        cfg = config_module.load(strict=False)
    except Exception as exc:
        print(f"\n  Could not read config.yaml: {exc}\n")
        return 1

    if not cfg.archive_mailbox:
        print("\n  google.archive_mailbox is not set in config.yaml.\n")
        return 1

    since = datetime.now(timezone.utc) - timedelta(days=args.days)
    print(f"\n  Reading {cfg.archive_mailbox}, last {args.days} days")
    print("  " + "-" * 52)

    try:
        service = _service(cfg, cfg.archive_mailbox)
    except GmailError as exc:
        print(f"\n  {exc}\n")
        return 1

    query = f"after:{(since - timedelta(days=1)):%Y/%m/%d}"
    thread_ids: list[str] = []
    page_token = None
    try:
        while len(thread_ids) < args.max_threads:
            resp = (
                service.users().threads()
                .list(userId="me", q=query, pageToken=page_token, maxResults=500)
                .execute()
            )
            thread_ids.extend(t["id"] for t in resp.get("threads", []))
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
    except Exception as exc:
        print(f"\n  Could not list mail: {exc}\n")
        return 1

    senders: Counter[str] = Counter()
    outbound: Counter[str] = Counter()
    for thread_id in thread_ids[: args.max_threads]:
        try:
            thread = (
                service.users().threads()
                .get(userId="me", id=thread_id, format="metadata",
                     metadataHeaders=["From", "To", "Date"])
                .execute()
            )
        except Exception:
            continue
        for raw in thread.get("messages", []):
            email = parse_message(raw)
            if not email.sender:
                continue
            senders[email.sender] += 1
            # Mail TO someone outside the company is the outreach we care
            # about. Internal chatter inflates the counts and hides who the
            # producers actually are.
            internal = set(cfg.all_internal_domains)
            recipients = email.to + email.cc
            if internal and recipients and any(
                r.rsplit("@", 1)[-1] not in internal for r in recipients
            ):
                outbound[email.sender] += 1

    if not senders:
        print("\n  Nothing found. Either the mailbox is empty for that period,")
        print("  or the BCC rule is not delivering into it.\n")
        return 1

    own = set(cfg.all_internal_domains)
    inside = [(a, n) for a, n in senders.items() if a.rsplit("@", 1)[-1] in own]
    outside = [(a, n) for a, n in senders.items() if a.rsplit("@", 1)[-1] not in own]

    print(f"\n  {len(thread_ids)} threads read\n")
    print("  YOUR PEOPLE - these are your producer candidates")
    print("  " + "-" * 52)
    if inside:
        for address, count in sorted(inside, key=lambda x: -x[1]):
            out = outbound.get(address, 0)
            print(f"  {count:5d} sent  {out:5d} to outsiders   {address}")
    else:
        print("  None found on " + ", ".join(sorted(own) or ["(no domain set)"]))
        print("  Check google.domain in config.yaml.")

    print("\n  EVERYONE ELSE - prospects replying, notifications, and so on")
    print("  " + "-" * 52)
    for address, count in sorted(outside, key=lambda x: -x[1])[:15]:
        print(f"  {count:5d}            {address}")
    if len(outside) > 15:
        print(f"  ... and {len(outside) - 15} more")

    print("\n  Paste the ones you want graded into config.yaml like this:\n")
    print("  producers:")
    for address, _ in sorted(inside, key=lambda x: -x[1])[:8]:
        name = address.split("@")[0].replace(".", " ").title()
        print(f"    - email: {address}")
        print(f"      name: {name}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
