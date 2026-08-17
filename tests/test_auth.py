"""
How the grader proves who it is.

Two sign-in methods that fail in opposite ways. A service account key reaches
every mailbox in the Workspace and is kept honest only by config.yaml. An OAuth
token reaches exactly one mailbox, which is safer, but it makes "which mailbox"
a question that can be answered wrongly -- signing in as the wrong account
succeeds just as cleanly as signing in as the right one, and then reads
somebody else's mail every day without ever erroring.

So most of what is checked here is the mismatch handling rather than the happy
path.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tempfile

from grader import config as config_module
from grader import gmail
from grader.config import Config, ConfigError, Producer

PASSED = 0
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED
    if condition:
        PASSED += 1
    else:
        FAILED.append(f"{name}{(': ' + detail) if detail else ''}")


def write_config(body: str) -> Path:
    handle = tempfile.NamedTemporaryFile(
        "w", suffix=".yaml", delete=False, encoding="utf-8"
    )
    handle.write(body)
    handle.close()
    return Path(handle.name)


BASE = """
google:
  auth: {auth}
  mode: {mode}
  archive_mailbox: cortex@allaccessptv.com
  domain: allaccessptv.com
anthropic:
  api_key: sk-ant-test
producers:
  - email: joe@allaccessptv.com
    name: Joe Bianco
reporting:
  manager_email: boss@allaccessptv.com
"""


def load(auth: str = "oauth", mode: str = "archive", strict: bool = False) -> Config:
    path = write_config(BASE.format(auth=auth, mode=mode))
    try:
        return config_module.load(path, strict=strict)
    finally:
        path.unlink(missing_ok=True)


def problems(auth: str = "oauth", mode: str = "archive") -> str:
    path = write_config(BASE.format(auth=auth, mode=mode))
    try:
        config_module.load(path, strict=True)
        return ""
    except ConfigError as exc:
        return str(exc)
    finally:
        path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------


def test_default_is_oauth() -> None:
    check("default: oauth", Config().auth == "oauth", Config().auth)
    check("default: reads the auth key", load(auth="service_account").auth
          == "service_account")


def test_oauth_needs_a_token() -> None:
    """An unattended daily job cannot sit waiting for a browser, so a missing
    token has to be a loud configuration error rather than a prompt."""
    text = problems(auth="oauth")
    check("oauth: missing token is a problem", "authorise.py" in text, text[:200])


def test_oauth_rejects_per_mailbox() -> None:
    """One sign-in covers one mailbox. Per-mailbox mode would need every
    producer to sit down and consent, which is not a thing anyone will do."""
    text = problems(auth="oauth", mode="per_mailbox")
    check("oauth: per_mailbox refused", "per_mailbox" in text, text[:200])
    check("oauth: says what to do instead",
          "archive" in text and "service_account" in text, text[:200])


def test_service_account_still_validates_its_own_way() -> None:
    text = problems(auth="service_account")
    check("service account: wants a key file",
          "service_account_file" in text, text[:200])
    check("service account: wants an admin",
          "delegated_admin" in text, text[:200])
    check("service account: does not ask for a token",
          "authorise.py" not in text, text[:200])


def test_unknown_auth_method() -> None:
    text = problems(auth="magic")
    check("unknown auth: rejected", "google.auth" in text, text[:200])


def test_send_from_defaults_to_the_authorised_mailbox() -> None:
    """In oauth mode the admin address is not necessarily reachable, so
    defaulting send_from to it would produce a run that reads fine and then
    fails at the last step."""
    cfg = load(auth="oauth")
    check("send_from: defaults to the archive in oauth mode",
          cfg.send_from == "cortex@allaccessptv.com", cfg.send_from)


class FakeProfile:
    def __init__(self, address: str) -> None:
        self.address = address

    def execute(self) -> dict:
        return {"emailAddress": self.address}


def stub_authorised(address: str):
    gmail._AUTHORISED.clear()
    original = gmail.authorised_address
    gmail.authorised_address = lambda cfg, service=None: address
    return original


def test_send_refuses_a_from_it_does_not_own() -> None:
    """Gmail silently rewrites a From header the account does not own. A report
    that claims to be from the boss but arrives from the archive mailbox is
    worse than a failed run, because nobody finds out."""
    cfg = load(auth="oauth")
    cfg.send_from = "boss@allaccessptv.com"
    original = stub_authorised("cortex@allaccessptv.com")
    try:
        try:
            gmail.send_report(cfg, "joe@allaccessptv.com", "s", "<p>h</p>", "t")
            check("send: mismatch refused", False, "no error raised")
        except gmail.GmailError as exc:
            message = str(exc)
            check("send: mismatch refused", True)
            check("send: names both addresses",
                  "boss@allaccessptv.com" in message
                  and "cortex@allaccessptv.com" in message, message[:200])
            check("send: offers the alias escape hatch",
                  "send_from_is_alias" in message, message[:200])
    finally:
        gmail.authorised_address = original


def test_send_allows_a_declared_alias() -> None:
    cfg = load(auth="oauth")
    cfg.send_from = "boss@allaccessptv.com"
    cfg.send_from_is_alias = True
    original = stub_authorised("cortex@allaccessptv.com")
    try:
        try:
            gmail.send_report(cfg, "joe@allaccessptv.com", "s", "<p>h</p>", "t")
            check("alias: not blocked", False, "reached the network unexpectedly")
        except gmail.GmailError as exc:
            # It should get past the sender guard and fail later, on the token.
            check("alias: not blocked by the sender guard",
                  "send_from_is_alias" not in str(exc), str(exc)[:200])
        except Exception:
            check("alias: not blocked by the sender guard", True)
    finally:
        gmail.authorised_address = original


def test_service_account_mode_skips_the_sender_guard() -> None:
    """Delegation can genuinely send as another user, so the guard must not
    fire there."""
    cfg = load(auth="service_account")
    cfg.send_from = "boss@allaccessptv.com"
    called = {"asked": False}
    original = gmail.authorised_address

    def spy(cfg, service=None):
        called["asked"] = True
        return "cortex@allaccessptv.com"

    gmail.authorised_address = spy
    try:
        try:
            gmail.send_report(cfg, "joe@allaccessptv.com", "s", "<p>h</p>", "t")
        except Exception:
            pass
        check("service account: sender guard not applied", not called["asked"])
    finally:
        gmail.authorised_address = original


def test_missing_token_message_names_the_script() -> None:
    cfg = load(auth="oauth")
    cfg.oauth_token_file = Path("/nonexistent/token.json")
    try:
        gmail.load_oauth_credentials(cfg)
        check("token: missing raises", False, "no error")
    except gmail.GmailError as exc:
        check("token: missing raises", True)
        check("token: message says how to fix it",
              "authorise.py" in str(exc), str(exc)[:200])
    except ImportError:
        check("token: missing raises", True)  # libraries absent, fine


def test_disabled_rules_are_validated() -> None:
    """A misspelt code disables nothing while looking like it disabled
    something. That has to be an error, not a shrug."""
    body = BASE.format(auth="oauth", mode="archive") + """
scoring:
  disabled_rules:
    - compliance.no_opt_out
    - compliance.no_postal_address
"""
    path = write_config(body)
    try:
        cfg = config_module.load(path, strict=False)
        check("valid codes are read",
              cfg.disabled_rules == ["compliance.no_opt_out",
                                     "compliance.no_postal_address"],
              str(cfg.disabled_rules))
    finally:
        path.unlink(missing_ok=True)

    typo = BASE.format(auth="oauth", mode="archive") + """
scoring:
  disabled_rules:
    - compliance.no_optout
"""
    path = write_config(typo)
    try:
        config_module.load(path, strict=True)
        check("a misspelt rule code is rejected", False, "no error raised")
    except ConfigError as exc:
        message = str(exc)
        check("a misspelt rule code is rejected", "no rule called" in message)
        check("the error lists the real codes in that group",
              "compliance.no_opt_out" in message, message)
    except Exception as exc:
        check("a misspelt rule code is rejected", False, repr(exc))
    finally:
        path.unlink(missing_ok=True)

    check("nothing is disabled by default", Config().disabled_rules == [])


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
