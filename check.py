#!/usr/bin/env python3
"""
Diagnostics. Run this first whenever something is not working.

Every part is tested separately so one failure does not hide the rest, and
each failure says in plain words what to do about it.

    python3 check.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

OK, WARN, BAD = "  [ ok ]", "  [warn]", "  [FAIL]"
problems: list[str] = []


def report(state: str, label: str, detail: str = "") -> None:
    print(f"{state}  {label}")
    for line in (detail or "").splitlines():
        print(f"           {line}")
    if state == BAD:
        problems.append(label)


def heading(text: str) -> None:
    print(f"\n{text}\n{'-' * len(text)}")


def check_python() -> None:
    heading("Python")
    v = sys.version_info
    if v < (3, 10):
        report(BAD, f"Python {v.major}.{v.minor}", "Python 3.10 or newer is needed.")
    else:
        report(OK, f"Python {v.major}.{v.minor}.{v.micro}")

    missing = []
    for module, package in (
        ("yaml", "PyYAML"),
        ("anthropic", "anthropic"),
        ("googleapiclient", "google-api-python-client"),
        ("google.oauth2", "google-auth"),
    ):
        try:
            __import__(module)
        except ImportError:
            missing.append(package)
    if missing:
        report(BAD, "Missing libraries: " + ", ".join(missing),
               "Run:  pip install -r requirements.txt")
    else:
        report(OK, "All libraries installed")


def check_settings():
    heading("Settings")
    from grader import config as config_mod

    if not config_mod.CONFIG_FILE.exists():
        report(BAD, "config.yaml is missing",
               f"Expected at: {config_mod.CONFIG_FILE}\n"
               "Copy config.yaml.example to config.yaml and fill it in.")
        return None

    report(OK, f"Found {config_mod.CONFIG_FILE.name}")
    try:
        cfg = config_mod.load(strict=False)
    except Exception as exc:
        report(BAD, "config.yaml could not be read", str(exc)[:300])
        return None

    if cfg.service_account_file and cfg.service_account_file.exists():
        report(OK, f"Service account key found ({cfg.service_account_file.name})")
        try:
            import json
            data = json.loads(cfg.service_account_file.read_text())
            report(OK, f"Key is for {data.get('client_email', '?')}")
            if data.get("client_id"):
                print(f"           Client ID (needed in the Admin console): "
                      f"{data['client_id']}")
        except Exception as exc:
            report(BAD, "The key file is not readable JSON", str(exc)[:200])
    else:
        report(BAD, "Service account key not found",
               f"Looked for: {cfg.service_account_file or '(not set)'}\n"
               "See docs/GOOGLE-SETUP.md.")

    if not cfg.delegated_admin:
        report(BAD, "google.delegated_admin is not set")
    else:
        report(OK, f"Admin address: {cfg.delegated_admin}")

    if cfg.mode == "archive":
        if not cfg.archive_mailbox:
            report(BAD, "google.archive_mailbox is not set",
                   "Archive mode needs the address every producer BCCs.")
        else:
            report(OK, f"Archive mode, reading {cfg.archive_mailbox}")
            # A typo here fails silently forever: the grader reads an empty
            # mailbox and reports nothing, with no error anywhere. So the
            # domain gets checked against the one the producers actually use.
            archive_domain = cfg.archive_mailbox.rsplit("@", 1)[-1]
            producer_domains = {
                p.email.rsplit("@", 1)[-1].lower() for p in cfg.active_producers
            }
            if producer_domains and archive_domain not in producer_domains:
                report(WARN,
                       f"The archive is on {archive_domain} but the producers "
                       f"are on {', '.join(sorted(producer_domains))}",
                       "Check the spelling. A wrong archive address does not "
                       "raise an error - it just reads an empty mailbox and "
                       "reports nothing, every day.")
    elif cfg.mode == "per_mailbox":
        report(OK, "Per-mailbox mode, reading each producer's Sent folder")
    else:
        report(BAD, f"google.mode is {cfg.mode!r}",
               "Must be either archive or per_mailbox.")

    if not cfg.anthropic_api_key:
        report(BAD, "No Anthropic API key",
               "Set anthropic.api_key in config.yaml, or export ANTHROPIC_API_KEY.")
    elif not cfg.anthropic_api_key.startswith("sk-ant-"):
        report(WARN, "The API key looks unusual",
               "Anthropic keys normally start with sk-ant-.")
    else:
        report(OK, f"API key present ({cfg.anthropic_api_key[:11]}...)")

    if cfg.active_producers:
        report(OK, f"{len(cfg.active_producers)} active producer(s)")
    else:
        report(BAD, "No active producers listed")

    if cfg.manager_email:
        report(OK, f"Reports go to {cfg.manager_email}")
    else:
        report(BAD, "reporting.manager_email is not set")

    if cfg.shadow_mode:
        report(OK, "Shadow mode is ON - only the manager will be emailed")
    else:
        report(WARN, "Shadow mode is OFF",
               "Producers will receive their scores directly. Make sure that "
               "is what you want.")
    return cfg


def check_rubric(cfg) -> None:
    heading("The standard")
    if cfg is None:
        report(WARN, "Skipped - no settings")
        return
    from grader.grade import load_rubric
    try:
        rubric = load_rubric(cfg)
    except Exception as exc:
        report(BAD, "The standard could not be loaded", str(exc)[:300])
        return
    words = len(rubric.split())
    report(OK, f"{cfg.rubric_file.name} loaded ({words} words)")
    if "REPLACE THIS FILE" in rubric:
        report(BAD, "This is still the example standard",
               "Everything would be graded against placeholder text. Put your "
               "own document in this file.")
    elif words < 60:
        report(WARN, "The standard is very short",
               "Vague standards produce vague and inconsistent grades.")


def check_claude(cfg) -> None:
    heading("Claude")
    if cfg is None or not cfg.anthropic_api_key:
        report(WARN, "Skipped - no API key to test with")
        return
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=cfg.anthropic_api_key)
        reply = client.messages.create(
            model=cfg.model,
            max_tokens=32,
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": "Reply with the single word: ready"}],
        )
        text = next((b.text for b in reply.content if b.type == "text"), "")
        report(OK, f"{cfg.model} answered", f"Said: {text.strip()[:40]}")
    except Exception as exc:
        name = type(exc).__name__
        hint = {
            "AuthenticationError": "The API key was rejected.",
            "NotFoundError": f"Model {getattr(cfg, 'model', '?')} not found. Check the model line.",
            "PermissionDeniedError": "This key cannot use that model.",
            "APIConnectionError": "Could not reach the internet.",
        }.get(name, str(exc)[:250])
        report(BAD, f"Could not reach Claude ({name})", hint)


def check_gmail(cfg) -> None:
    heading("Google Workspace")
    if cfg is None or not cfg.service_account_file or not cfg.service_account_file.exists():
        report(WARN, "Skipped - no service account key")
        return
    from grader import gmail

    # The admin first: if delegation is broken it is broken for everybody, and
    # saying so once is more useful than the same error per producer.
    good, message = gmail.selftest(cfg, cfg.delegated_admin)
    report(OK if good else BAD,
           f"Admin mailbox {cfg.delegated_admin}" if good else "Delegation is not working",
           message)
    if not good:
        return

    if cfg.mode == "archive":
        if not cfg.archive_mailbox:
            return
        good, message = gmail.selftest(cfg, cfg.archive_mailbox)
        report(OK if good else BAD,
               f"Archive mailbox {cfg.archive_mailbox}", message)
        if good:
            _archive_contents(cfg)
        return

    for producer in cfg.active_producers:
        good, message = gmail.selftest(cfg, producer.email)
        report(OK if good else BAD, producer.email, "" if good else message)


def _archive_contents(cfg) -> None:
    """Reading the archive is not enough - it has to actually contain the
    producers' mail. An empty or mis-routed archive is the failure mode most
    likely to go unnoticed, because nothing errors."""
    from grader import archive
    from datetime import datetime, timedelta, timezone
    try:
        found = archive.fetch_archive(
            cfg, datetime.now(timezone.utc) - timedelta(days=7)
        )
    except Exception as exc:
        report(WARN, "Could not sample the archive", str(exc)[:250])
        return

    if not found:
        report(BAD, "The archive contains no producer mail from the last 7 days",
               "Either nobody sent anything, or the BCC rule is not routing to "
               "this address. Check one producer's Sent folder against it.")
        return

    senders = sorted({e.sender for e in found})
    report(OK, f"{len(found)} producer message(s) in the last 7 days")
    for sender in senders[:10]:
        n = sum(1 for e in found if e.sender == sender)
        touches = sorted({e.touch for e in found if e.sender == sender})
        print(f"           {sender}: {n} message(s), touches {touches}")

    silent = [
        p.email for p in cfg.active_producers
        if p.email.lower() not in {s.lower() for s in senders}
    ]
    if silent:
        report(WARN, f"Nothing at all from: {', '.join(silent)}",
               "Check their BCC rule before reading anything into a zero score.")


def check_rules() -> None:
    """The rule engine needs no configuration, so it can always be tested."""
    heading("Rule engine")
    from grader import checks
    clean = checks.Email(
        subject="Question about Acme's Q3 plan",
        to=["dana@acme.com"],
        body_text=(
            "Hi Dana,\n\nI saw Acme is expanding the Midwest team. Would you be "
            "open to a short call next week?\n\nBest, Joe\n"
            "1200 Market Street, Suite 400, Dallas, TX 75201\n"
            "If you would rather not hear from me, just reply and say so."
        ),
    )
    spammy = checks.Email(
        subject="ACT NOW!!! LIMITED TIME",
        to=["dana@acme.com"],
        body_text="HI {{first_name}}!!! RISK FREE, act now! http://bit.ly/x",
    )
    good_score = checks.mechanical_score(checks.run_all(clean))
    bad_score = checks.mechanical_score(checks.run_all(spammy))
    if good_score == 100 and bad_score < 50:
        report(OK, f"Working (clean email {good_score}, spammy email {bad_score})")
    else:
        report(BAD, f"Not discriminating (clean {good_score}, spammy {bad_score})",
               "This is a bug in the grader. Send this output to the developer.")


def check_database(cfg) -> None:
    heading("Database")
    if cfg is None:
        report(WARN, "Skipped - no settings")
        return
    from grader import store
    try:
        with store.connect(cfg.database) as conn:
            rows = conn.execute("SELECT COUNT(*) c FROM graded").fetchone()["c"]
            runs = conn.execute("SELECT COUNT(*) c FROM runs").fetchone()["c"]
        report(OK, f"{cfg.database.name}: {rows} emails graded so far, {runs} run(s)")
    except Exception as exc:
        report(BAD, "The database could not be opened", str(exc)[:250])


def main() -> int:
    print("\n  Outbound email grader -- system check\n" + "=" * 46)
    check_python()
    cfg = check_settings()
    check_rules()
    check_rubric(cfg)
    check_claude(cfg)
    check_gmail(cfg)
    check_database(cfg)

    print("\n" + "=" * 46)
    if problems:
        print(f"  {len(problems)} thing(s) need fixing:")
        for p in problems:
            print(f"    - {p}")
        print("\n  Anything marked [warn] is optional.")
        return 1
    print("  Everything checks out.")
    print("  Try:  python3 run_daily.py --dry-run --days 7")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
