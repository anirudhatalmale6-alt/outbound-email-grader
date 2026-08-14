"""
The record.

Two jobs. First, never grade the same message twice -- a rerun after a crash
must not double-count anybody or spend the API budget again. Second, keep the
history, so "is this producer getting better" is a question the data can answer
rather than a matter of opinion.

Message bodies are deliberately NOT stored. The grade, the findings and the
subject line are enough to run the reports, and keeping a second copy of every
employee's outbound mail in a sqlite file next to the script is a liability
nobody asked for.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .score import ProducerSummary, Result

SCHEMA = """
CREATE TABLE IF NOT EXISTS graded (
    message_id      TEXT PRIMARY KEY,
    producer        TEXT NOT NULL,
    sent_at         TEXT,
    graded_at       TEXT NOT NULL,
    subject         TEXT,
    recipient_domain TEXT,
    overall         INTEGER,
    mechanical      INTEGER,
    writing         INTEGER,
    findings        TEXT,
    model_ok        INTEGER
);
CREATE INDEX IF NOT EXISTS graded_producer ON graded (producer, graded_at);

CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    producers   INTEGER,
    graded      INTEGER,
    skipped     INTEGER,
    errors      TEXT
);

CREATE TABLE IF NOT EXISTS sent_reports (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    sent_at     TEXT NOT NULL,
    recipient   TEXT NOT NULL,
    kind        TEXT NOT NULL,
    covering    TEXT
);
"""


@contextmanager
def connect(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def already_graded(conn: sqlite3.Connection, message_ids: list[str]) -> set[str]:
    if not message_ids:
        return set()
    found: set[str] = set()
    # sqlite caps the number of bound variables, so chunk it.
    for i in range(0, len(message_ids), 500):
        chunk = message_ids[i : i + 500]
        placeholders = ",".join("?" * len(chunk))
        rows = conn.execute(
            f"SELECT message_id FROM graded WHERE message_id IN ({placeholders})",
            chunk,
        ).fetchall()
        found.update(r["message_id"] for r in rows)
    return found


def record(conn: sqlite3.Connection, producer: str, result: Result) -> None:
    domains = sorted({r.rsplit("@", 1)[-1] for r in result.email.recipients if "@" in r})
    conn.execute(
        "INSERT OR REPLACE INTO graded (message_id, producer, sent_at, graded_at,"
        " subject, recipient_domain, overall, mechanical, writing, findings, model_ok)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            result.email.message_id,
            producer,
            result.email.date,
            _now(),
            result.email.subject[:300],
            ",".join(domains)[:300],
            result.overall,
            result.mechanical,
            result.writing,
            json.dumps([f.id for f in result.findings]),
            1 if result.judgement.ok else 0,
        ),
    )


def start_run(conn: sqlite3.Connection) -> int:
    cur = conn.execute("INSERT INTO runs (started_at) VALUES (?)", (_now(),))
    return int(cur.lastrowid)


def finish_run(
    conn: sqlite3.Connection,
    run_id: int,
    producers: int,
    graded: int,
    skipped: int,
    errors: list[str],
) -> None:
    conn.execute(
        "UPDATE runs SET finished_at=?, producers=?, graded=?, skipped=?, errors=?"
        " WHERE id=?",
        (_now(), producers, graded, skipped, json.dumps(errors), run_id),
    )


def note_report(conn: sqlite3.Connection, recipient: str, kind: str, covering: str) -> None:
    conn.execute(
        "INSERT INTO sent_reports (sent_at, recipient, kind, covering) VALUES (?,?,?,?)",
        (_now(), recipient, kind, covering),
    )


def trend(conn: sqlite3.Connection, producer: str, days: int = 30) -> list[tuple[str, int]]:
    """(day, average score) for the last `days` days, oldest first."""
    rows = conn.execute(
        "SELECT substr(graded_at, 1, 10) AS day, AVG(overall) AS avg"
        " FROM graded WHERE producer = ?"
        " AND graded_at >= datetime('now', ?)"
        " GROUP BY day ORDER BY day",
        (producer, f"-{int(days)} days"),
    ).fetchall()
    return [(r["day"], round(r["avg"] or 0)) for r in rows]


def previous_average(conn: sqlite3.Connection, producer: str, days: int = 7) -> int | None:
    """Average over the days *before* today, so the report can say whether
    somebody is improving."""
    row = conn.execute(
        "SELECT AVG(overall) AS avg FROM graded WHERE producer = ?"
        " AND graded_at >= datetime('now', ?)"
        " AND substr(graded_at, 1, 10) < date('now')",
        (producer, f"-{int(days)} days"),
    ).fetchone()
    if not row or row["avg"] is None:
        return None
    return round(row["avg"])
