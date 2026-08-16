#!/usr/bin/env python3
"""
One-time sign-in.

Opens a browser, asks you to sign in as the mailbox the grader should read, and
stores a token so it never has to ask again. Run it once. Run it again only if
access gets revoked or you want to point the grader at a different mailbox.

    python3 authorise.py

What it grants: read mail, and send mail, as that one account. Not delete, not
modify, not settings, and not anybody else's mailbox.

If the machine that runs the grader has no browser -- a server, a VPS -- do this
on your laptop instead and copy the resulting token.json across. It is the token
that matters, not where it was made.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from grader import config as config_module
from grader import gmail


def fail(message: str) -> int:
    print(f"\n  {message}\n")
    return 1


def main() -> int:
    print("\n  Authorising the email grader\n  " + "-" * 40)

    try:
        cfg = config_module.load(strict=False)
    except Exception as exc:
        return fail(f"Could not read config.yaml: {exc}")

    if cfg.auth != "oauth":
        print(
            f"  Note: google.auth is set to {cfg.auth!r}, so the grader will not\n"
            "  use what this creates. Set it to oauth if that is what you want.\n"
        )

    if not cfg.oauth_client_file.exists():
        return fail(
            f"No OAuth client file at {cfg.oauth_client_file}.\n\n"
            "  In the Google Cloud console: APIs and Services > Credentials >\n"
            "  Create credentials > OAuth client ID > Desktop app. Download the\n"
            "  JSON and save it at the path above.\n\n"
            "  Full walkthrough: docs/GOOGLE-SETUP.md"
        )

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        return fail(
            "The OAuth library is not installed. Run:\n"
            "    pip install -r requirements.txt"
        )

    if cfg.oauth_token_file.exists():
        print(f"  There is already a token at {cfg.oauth_token_file}.")
        try:
            existing = gmail.authorised_address(cfg)
            print(f"  It is signed in as: {existing}")
        except Exception:
            print("  It could not be used, so it is worth replacing.")
        if input("\n  Replace it? [y/N] ").strip().lower() not in ("y", "yes"):
            print("\n  Left alone.\n")
            return 0

    expected = cfg.archive_mailbox or ""
    if expected:
        print(f"\n  Sign in as: {expected}")
        print("  Signing in as anyone else will authorise the wrong mailbox.")
    print("\n  A browser window will open. Approve the two permissions it asks")
    print("  for -- reading mail, and sending mail.\n")

    try:
        flow = InstalledAppFlow.from_client_secrets_file(
            str(cfg.oauth_client_file), gmail.SCOPES
        )
        creds = flow.run_local_server(port=0, prompt="consent")
    except Exception as exc:
        return fail(
            f"Sign-in did not complete: {exc}\n\n"
            "  If this machine has no browser, run this on your laptop and copy\n"
            "  token.json across."
        )

    gmail.save_oauth_token(cfg, creds)

    # Which account actually signed in matters more than whether the flow
    # completed. Approving as the wrong user succeeds just as cleanly and then
    # reads the wrong mailbox forever.
    gmail._AUTHORISED.pop(str(cfg.oauth_token_file), None)
    try:
        actual = gmail.authorised_address(cfg)
    except Exception as exc:
        return fail(f"Signed in, but could not read the account back: {exc}")

    print(f"\n  Saved to {cfg.oauth_token_file}")
    print(f"  Signed in as: {actual}")

    if expected and actual != expected.lower():
        print(
            f"\n  WARNING: the settings expect {expected} but you signed in as\n"
            f"  {actual}. The grader will read {actual}'s mail, which is almost\n"
            "  certainly not what you want. Run this again and pick the other\n"
            "  account, or change google.archive_mailbox to match."
        )
        return 1

    print("\n  Done. Next: python3 check.py\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
