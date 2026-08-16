#!/usr/bin/env python3
"""
One-time sign-in.

Opens a browser, asks you to sign in as the mailbox the grader should read, and
stores a token so it never has to ask again. Run it once. Run it again only if
access gets revoked or you want to point the grader at a different mailbox.

    python3 authorise.py

What it grants: read mail, and send mail, as that one account. Not delete, not
modify, not settings, and not anybody else's mailbox.

On a server with no browser -- a VPS, anything you reach over SSH -- use a fixed
port and forward it from the machine you are sitting at:

    ssh -L 8080:localhost:8080 you@your-server
    python3 authorise.py --port 8080

Then open the printed link in your own browser. The sign-in happens on your
machine, the token lands on the server, and nothing has to be copied by hand.

Failing that, run this on your laptop and copy the resulting token.json across.
It is the token that matters, not where it was made.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from grader import config as config_module
from grader import gmail


def fail(message: str) -> int:
    print(f"\n  {message}\n")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sign in once so the grader can read the archive mailbox."
    )
    parser.add_argument(
        "--port", type=int, default=0,
        help="Fixed port for the sign-in callback. Use with an SSH tunnel when "
             "the machine running this has no browser of its own.",
    )
    parser.add_argument(
        "--no-browser", action="store_true",
        help="Print the sign-in link instead of trying to open a browser.",
    )
    args = parser.parse_args()

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
    if args.no_browser or args.port:
        print("\n  Open the link below in your own browser and approve the two")
        print("  permissions it asks for -- reading mail, and sending mail.\n")
    else:
        print("\n  A browser window will open. Approve the two permissions it asks")
        print("  for -- reading mail, and sending mail.\n")

    try:
        flow = InstalledAppFlow.from_client_secrets_file(
            str(cfg.oauth_client_file), gmail.SCOPES
        )
        creds = flow.run_local_server(
            port=args.port,
            prompt="consent",
            open_browser=not (args.no_browser or args.port),
        )
    except Exception as exc:
        return fail(
            f"Sign-in did not complete: {exc}\n\n"
            "  On a server with no browser, forward the port from the machine\n"
            "  you are sitting at and use it:\n\n"
            "      ssh -L 8080:localhost:8080 you@your-server\n"
            "      python3 authorise.py --port 8080\n\n"
            "  Then open the printed link in your own browser. Or run this on\n"
            "  your laptop and copy token.json across."
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
