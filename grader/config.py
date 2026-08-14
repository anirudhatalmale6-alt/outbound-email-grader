"""
Settings.

One file, config.yaml, sitting next to run_daily.py. Nothing is read from the
environment except the two secrets, so a misconfigured cron job cannot silently
pick up someone else's credentials.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

APP_DIR = Path(__file__).resolve().parent.parent
CONFIG_FILE = APP_DIR / "config.yaml"


class ConfigError(Exception):
    """Something in config.yaml is missing or wrong."""


@dataclass
class Producer:
    """One person whose outbound mail gets graded."""

    email: str
    name: str = ""
    report_to: str = ""      # blank = send to their own address
    active: bool = True

    @property
    def display(self) -> str:
        return self.name or self.email.split("@")[0]


@dataclass
class Config:
    # --- Google Workspace ---
    service_account_file: Path = Path()
    delegated_admin: str = ""          # an admin address in the domain
    domain: str = ""                   # e.g. dncsearch.com

    # "archive" reads one mailbox that every producer BCCs. "per_mailbox"
    # opens each producer's own Sent folder. Archive is the better default:
    # one account to authorise instead of the whole domain, and it is a copy,
    # so nothing here can touch anybody's real mail.
    mode: str = "archive"
    archive_mailbox: str = ""

    # --- Anthropic ---
    anthropic_api_key: str = ""
    model: str = "claude-opus-5"
    effort: str = "medium"

    # --- Who gets graded ---
    producers: list[Producer] = field(default_factory=list)

    # --- What counts as a gradeable email ---
    grade_first_contact_only: bool = False
    # Follow-ups chasing a prospect who has not replied. Graded, but the
    # grader is told which touch it is so it judges a follow-up as a follow-up.
    grade_follow_ups: bool = True
    max_touch: int = 4
    # Once the prospect has written back it is a conversation, not outreach,
    # and grading it against a cold-email standard is meaningless.
    grade_after_reply: bool = False
    internal_domains: list[str] = field(default_factory=list)
    exclude_recipients: list[str] = field(default_factory=list)
    min_body_chars: int = 40

    # --- Reporting ---
    manager_email: str = ""
    send_from: str = ""
    # Shadow mode: only the manager gets reports. Producers get nothing.
    # This stays on until the grades have been checked against real judgement.
    shadow_mode: bool = True
    report_subject_manager: str = "Outbound email report - {date}"
    report_subject_producer: str = "Your outbound email report - {date}"

    # --- Housekeeping ---
    lookback_days: int = 1
    max_emails_per_producer: int = 200
    max_threads: int = 1000
    quiet_producer_warning: bool = True
    database: Path = APP_DIR / "grader.sqlite3"
    rubric_file: Path = APP_DIR / "rubric.md"

    @property
    def all_internal_domains(self) -> list[str]:
        """The company's own domains -- mail to these is not cold outreach."""
        out = {d.lower().lstrip("@") for d in self.internal_domains if d.strip()}
        if self.domain:
            out.add(self.domain.lower().lstrip("@"))
        return sorted(out)

    @property
    def active_producers(self) -> list[Producer]:
        return [p for p in self.producers if p.active]


def _as_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    return [str(v).strip() for v in value if str(v).strip()]


def load(path: Path | None = None, strict: bool = True) -> Config:
    """Read config.yaml. With strict=False, return whatever parsed so the
    diagnostics can report several problems at once instead of the first."""
    path = path or CONFIG_FILE
    if not path.exists():
        if strict:
            raise ConfigError(
                f"No settings file at {path}.\n"
                "Copy config.yaml.example to config.yaml and fill it in."
            )
        return Config()

    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    cfg = Config()

    google = raw.get("google", {}) or {}
    sa = str(google.get("service_account_file", "")).strip()
    if sa:
        candidate = Path(os.path.expandvars(sa)).expanduser()
        cfg.service_account_file = candidate if candidate.is_absolute() else APP_DIR / candidate
    cfg.delegated_admin = str(google.get("delegated_admin", "")).strip()
    cfg.domain = str(google.get("domain", "")).strip()
    cfg.mode = str(google.get("mode", cfg.mode)).strip().lower() or cfg.mode
    cfg.archive_mailbox = str(google.get("archive_mailbox", "")).strip().lower()

    anthropic = raw.get("anthropic", {}) or {}
    # The key may come from the environment so it never has to sit in a file
    # that gets copied around.
    cfg.anthropic_api_key = (
        str(anthropic.get("api_key", "")).strip()
        or os.getenv("ANTHROPIC_API_KEY", "").strip()
    )
    cfg.model = str(anthropic.get("model", cfg.model)).strip() or cfg.model
    cfg.effort = str(anthropic.get("effort", cfg.effort)).strip() or cfg.effort

    for entry in raw.get("producers", []) or []:
        if isinstance(entry, str):
            cfg.producers.append(Producer(email=entry.strip()))
            continue
        email = str(entry.get("email", "")).strip()
        if not email:
            continue
        cfg.producers.append(
            Producer(
                email=email,
                name=str(entry.get("name", "")).strip(),
                report_to=str(entry.get("report_to", "")).strip(),
                active=bool(entry.get("active", True)),
            )
        )

    scope = raw.get("scope", {}) or {}
    cfg.grade_first_contact_only = bool(
        scope.get("first_contact_only", cfg.grade_first_contact_only)
    )
    cfg.grade_follow_ups = bool(scope.get("follow_ups", cfg.grade_follow_ups))
    cfg.max_touch = int(scope.get("max_touch", cfg.max_touch))
    cfg.grade_after_reply = bool(
        scope.get("grade_after_reply", cfg.grade_after_reply)
    )
    cfg.internal_domains = _as_list(scope.get("internal_domains"))
    cfg.exclude_recipients = [
        r.lower() for r in _as_list(scope.get("exclude_recipients"))
    ]
    cfg.min_body_chars = int(scope.get("min_body_chars", cfg.min_body_chars))

    reporting = raw.get("reporting", {}) or {}
    cfg.manager_email = str(reporting.get("manager_email", "")).strip()
    cfg.send_from = str(reporting.get("send_from", "")).strip() or cfg.delegated_admin
    cfg.shadow_mode = bool(reporting.get("shadow_mode", cfg.shadow_mode))
    cfg.report_subject_manager = str(
        reporting.get("subject_manager", cfg.report_subject_manager)
    )
    cfg.report_subject_producer = str(
        reporting.get("subject_producer", cfg.report_subject_producer)
    )

    run = raw.get("run", {}) or {}
    cfg.lookback_days = int(run.get("lookback_days", cfg.lookback_days))
    cfg.max_emails_per_producer = int(
        run.get("max_emails_per_producer", cfg.max_emails_per_producer)
    )
    cfg.max_threads = int(run.get("max_threads", cfg.max_threads))
    cfg.quiet_producer_warning = bool(
        run.get("quiet_producer_warning", cfg.quiet_producer_warning)
    )
    db = str(run.get("database", "")).strip()
    if db:
        p = Path(os.path.expandvars(db)).expanduser()
        cfg.database = p if p.is_absolute() else APP_DIR / p
    rubric = str(run.get("rubric_file", "")).strip()
    if rubric:
        p = Path(os.path.expandvars(rubric)).expanduser()
        cfg.rubric_file = p if p.is_absolute() else APP_DIR / p

    if strict:
        _validate(cfg)
    return cfg


def _validate(cfg: Config) -> None:
    problems: list[str] = []
    if not cfg.service_account_file or not cfg.service_account_file.exists():
        problems.append(
            f"google.service_account_file is missing or not found "
            f"({cfg.service_account_file or 'not set'})"
        )
    if not cfg.delegated_admin:
        problems.append("google.delegated_admin is not set")
    if cfg.mode not in ("archive", "per_mailbox"):
        problems.append(f"google.mode must be archive or per_mailbox, not {cfg.mode!r}")
    if cfg.mode == "archive" and not cfg.archive_mailbox:
        problems.append("google.archive_mailbox is not set (needed in archive mode)")
    if not cfg.anthropic_api_key:
        problems.append("anthropic.api_key is not set (or ANTHROPIC_API_KEY)")
    if not cfg.active_producers:
        problems.append("no active producers listed")
    if not cfg.manager_email:
        problems.append("reporting.manager_email is not set")
    if problems:
        raise ConfigError(
            "config.yaml needs attention:\n  - " + "\n  - ".join(problems)
        )
