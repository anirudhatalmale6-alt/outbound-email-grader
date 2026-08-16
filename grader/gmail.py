"""
Reading sent mail out of Google Workspace.

Two ways in, and the difference is how much they can reach.

OAuth (the default) signs in once, in a browser, as one mailbox, and keeps a
refresh token. It can reach that mailbox and nothing else. Nothing about the
credential can be pointed at a colleague's mail, so the limit does not depend on
this program behaving.

Service account with domain-wide delegation needs no interactive sign-in, which
suits an unattended server, but the key can act as any user in the Workspace.
Only config.yaml keeps it aimed at the right mailbox -- that limit is enforced
here, not by Google. Many organisations now block downloadable service account
keys outright, which is what pushed oauth to being the default.

Either way the scopes are gmail.readonly plus gmail.send: read what was sent,
and send the reports. Nothing here can delete, move, or modify a message,
because the scope to do so is never requested, and that limit Google enforces.

See docs/GOOGLE-SETUP.md.
"""

from __future__ import annotations

import base64
import re
from datetime import datetime, timedelta, timezone
from email.utils import getaddresses, parsedate_to_datetime

from .checks import Email
from .config import Config

# Read mail, and send the reports. No modify, no delete, no settings.
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]


class GmailError(Exception):
    pass


def _build(creds):
    from googleapiclient.discovery import build

    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def _require_google_libraries() -> None:
    try:
        import googleapiclient.discovery  # noqa: F401
    except ImportError as exc:  # pragma: no cover
        raise GmailError(
            "The Google libraries are not installed. Run:\n"
            "  pip install -r requirements.txt"
        ) from exc


def load_oauth_credentials(cfg: Config):
    """The stored token, refreshed if it has expired.

    Never starts an interactive sign-in. A daily job running unattended must
    fail with something a human can act on rather than block forever waiting
    for a browser that nobody is looking at -- authorise.py does the sign-in.
    """
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
    except ImportError as exc:  # pragma: no cover
        raise GmailError(
            "The Google libraries are not installed. Run:\n"
            "  pip install -r requirements.txt"
        ) from exc

    if not cfg.oauth_token_file.exists():
        raise GmailError(
            f"Not authorised yet - no token at {cfg.oauth_token_file}.\n"
            "Run:  python3 authorise.py"
        )

    try:
        creds = Credentials.from_authorized_user_file(
            str(cfg.oauth_token_file), SCOPES
        )
    except Exception as exc:
        raise GmailError(
            f"Could not read {cfg.oauth_token_file}: {exc}\n"
            "Delete it and run: python3 authorise.py"
        ) from exc

    if creds.valid:
        return creds

    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception as exc:
            raise GmailError(
                f"The stored sign-in could not be refreshed: {exc}\n"
                "This usually means access was revoked, the password was "
                "changed, or the token went unused for six months. "
                "Run: python3 authorise.py"
            ) from exc
        save_oauth_token(cfg, creds)
        return creds

    raise GmailError(
        "The stored sign-in is no longer usable and cannot be refreshed. "
        "Run: python3 authorise.py"
    )


def save_oauth_token(cfg: Config, creds) -> None:
    """Write the token, readable only by the user running this."""
    import os

    path = cfg.oauth_token_file
    path.write_text(creds.to_json(), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        # Windows, or an exotic filesystem. Not worth failing the run over.
        pass


# Which mailbox a given token belongs to. Cached because _service() checks it on
# every call and it cannot change without the token file changing.
_AUTHORISED: dict[str, str] = {}


def authorised_address(cfg: Config, service=None) -> str:
    """Which mailbox the stored sign-in actually belongs to."""
    key = str(cfg.oauth_token_file)
    if key not in _AUTHORISED:
        service = service or _build(load_oauth_credentials(cfg))
        profile = service.users().getProfile(userId="me").execute()
        _AUTHORISED[key] = (profile.get("emailAddress") or "").lower()
    return _AUTHORISED[key]


def _service(cfg: Config, user: str):
    """A Gmail client acting as `user`.

    In oauth mode there is only one identity available -- whoever signed in --
    so `user` is checked against it rather than used to impersonate. Asking for
    a mailbox the token does not cover is a configuration mistake, and it is far
    better to say so than to quietly return the wrong mailbox's mail.
    """
    _require_google_libraries()

    if cfg.auth == "oauth":
        creds = load_oauth_credentials(cfg)
        service = _build(creds)
        wanted = (user or "").lower()
        if wanted:
            actual = authorised_address(cfg, service)
            if actual and wanted != actual:
                raise GmailError(
                    f"Asked for {wanted} but the stored sign-in is {actual}. "
                    "One sign-in covers one mailbox. Either point the settings at "
                    f"{actual}, or re-run authorise.py signed in as {wanted}."
                )
        return service

    from google.oauth2 import service_account

    if not cfg.service_account_file.is_file():
        raise GmailError(f"Service account file not found: {cfg.service_account_file}")

    try:
        creds = service_account.Credentials.from_service_account_file(
            str(cfg.service_account_file), scopes=SCOPES
        ).with_subject(user)
    except Exception as exc:
        raise GmailError(f"Could not read the service account key: {exc}") from exc

    return _build(creds)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _decode(data: str) -> str:
    if not data:
        return ""
    try:
        return base64.urlsafe_b64decode(data.encode("ascii")).decode(
            "utf-8", errors="replace"
        )
    except Exception:
        return ""


def _walk_parts(part: dict, out: dict) -> None:
    """Collect the text body, the html body and any attachment names."""
    mime = (part.get("mimeType") or "").lower()
    body = part.get("body") or {}
    filename = part.get("filename") or ""

    if filename and body.get("attachmentId"):
        out["attachments"].append(filename)
    elif mime == "text/plain":
        out["text"] += _decode(body.get("data", ""))
    elif mime == "text/html":
        out["html"] += _decode(body.get("data", ""))

    for child in part.get("parts") or []:
        _walk_parts(child, out)


def _headers(payload: dict) -> dict[str, str]:
    # Header names are case-insensitive and Gmail does not normalise them.
    return {
        (h.get("name") or "").lower(): (h.get("value") or "")
        for h in payload.get("headers") or []
    }


def _addresses(raw: str) -> list[str]:
    return [addr.lower() for _, addr in getaddresses([raw or ""]) if addr]


def parse_message(msg: dict) -> Email:
    """Turn Gmail's JSON into the shape the checks expect."""
    payload = msg.get("payload") or {}
    head = _headers(payload)
    collected = {"text": "", "html": "", "attachments": []}
    _walk_parts(payload, collected)

    date = head.get("date", "")
    try:
        iso = parsedate_to_datetime(date).astimezone(timezone.utc).isoformat()
    except Exception:
        iso = date

    sender = ""
    from_addrs = _addresses(head.get("from", ""))
    if from_addrs:
        sender = from_addrs[0]

    return Email(
        message_id=msg.get("id", ""),
        thread_id=msg.get("threadId", ""),
        date=iso,
        sender=sender,
        to=_addresses(head.get("to", "")),
        cc=_addresses(head.get("cc", "")),
        subject=head.get("subject", ""),
        body_text=collected["text"],
        body_html=collected["html"],
        has_attachments=bool(collected["attachments"]),
        attachment_names=collected["attachments"],
        in_reply_to=head.get("in-reply-to", ""),
        references=head.get("references", ""),
        labels=msg.get("labelIds") or [],
    )


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------


def fetch_sent(cfg: Config, user: str, since: datetime | None = None) -> list[Email]:
    """Everything `user` sent since `since` (default: cfg.lookback_days ago).

    Gmail's `after:` takes a date, not a datetime, so it is deliberately
    widened by a day and the precise cutoff applied afterwards in Python. A
    query that silently returns nothing is worse than one that returns a
    little too much.
    """
    since = since or (datetime.now(timezone.utc) - timedelta(days=cfg.lookback_days))
    service = _service(cfg, user)
    query_date = (since - timedelta(days=1)).strftime("%Y/%m/%d")
    query = f"in:sent after:{query_date}"

    ids: list[str] = []
    page_token = None
    try:
        while len(ids) < cfg.max_emails_per_producer:
            resp = (
                service.users()
                .messages()
                .list(
                    userId="me",
                    q=query,
                    pageToken=page_token,
                    maxResults=min(500, cfg.max_emails_per_producer - len(ids)),
                )
                .execute()
            )
            ids.extend(m["id"] for m in resp.get("messages", []))
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
    except Exception as exc:
        raise GmailError(f"Could not list sent mail for {user}: {exc}") from exc

    emails: list[Email] = []
    for message_id in ids:
        try:
            raw = (
                service.users()
                .messages()
                .get(userId="me", id=message_id, format="full")
                .execute()
            )
        except Exception:
            # One unreadable message must not lose the other 199.
            continue
        email = parse_message(raw)
        if _sent_after(email, since):
            emails.append(email)
    return emails


def _sent_after(email: Email, since: datetime) -> bool:
    try:
        return datetime.fromisoformat(email.date) >= since
    except Exception:
        # If the date could not be parsed, keep it -- better graded twice than
        # silently dropped. store.py de-duplicates on message id anyway.
        return True


# ---------------------------------------------------------------------------
# Sending the reports
# ---------------------------------------------------------------------------


def send_report(cfg: Config, to: str, subject: str, html: str, text: str) -> str:
    """Send one report. Returns the Gmail message id."""
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    sender = cfg.send_from or cfg.delegated_admin
    if not sender:
        raise GmailError("No send_from address configured.")

    if cfg.auth == "oauth" and not cfg.send_from_is_alias:
        # Gmail does not honour a From header the signed-in account does not own
        # -- it silently rewrites it. A report that says it came from the boss
        # but actually came from the archive mailbox is worse than a failed run,
        # because nobody finds out.
        actual = authorised_address(cfg)
        if actual and sender.lower() != actual:
            raise GmailError(
                f"reporting.send_from is {sender} but the sign-in is {actual}. "
                "Gmail would quietly rewrite the From header and the reports "
                f"would arrive from {actual} regardless.\n"
                f"Either set reporting.send_from to {actual}, or - if {sender} "
                f"is a verified send-as alias on {actual} - set "
                "reporting.send_from_is_alias: true."
            )

    message = MIMEMultipart("alternative")
    message["To"] = to
    message["From"] = sender
    message["Subject"] = subject
    message.attach(MIMEText(text, "plain", "utf-8"))
    message.attach(MIMEText(html, "html", "utf-8"))

    encoded = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
    service = _service(cfg, sender)
    try:
        sent = (
            service.users()
            .messages()
            .send(userId="me", body={"raw": encoded})
            .execute()
        )
    except Exception as exc:
        raise GmailError(f"Could not send the report to {to}: {exc}") from exc
    return sent.get("id", "")


def selftest(cfg: Config, user: str) -> tuple[bool, str]:
    """Used by check.py: can we actually reach this mailbox?"""
    try:
        service = _service(cfg, user)
        profile = service.users().getProfile(userId="me").execute()
        return True, (
            f"{profile.get('emailAddress')} reachable "
            f"({profile.get('messagesTotal', '?')} messages)"
        )
    except GmailError as exc:
        # Already a plain-English message from the credential loader.
        return False, str(exc)
    except Exception as exc:
        message = str(exc)
        if "has not been used in project" in message or "accessNotConfigured" in message:
            # Almost always two projects with the same name: the API was
            # enabled in one and the credential was created in the other.
            project = ""
            match = re.search(r"project (\d+)", message)
            if match:
                project = f" (project {match.group(1)})"
            return False, (
                f"The Gmail API is not enabled in the Google Cloud project this "
                f"credential belongs to{project}.\n"
                "Cloud console > APIs and Services > Library > Gmail API > "
                "Enable - making sure the project selector at the top is the "
                "same project the credential was created in."
            )
        if cfg.auth == "oauth" and (
            "invalid_grant" in message or "invalid_scope" in message
        ):
            return False, (
                "The stored sign-in was rejected. Access may have been revoked "
                "in the Google account's security settings, or the scopes "
                "changed. Run: python3 authorise.py"
            )
        if "unauthorized_client" in message:
            return False, (
                "Google rejected the delegation. The client ID is not "
                "authorised for these scopes in Admin console > Security > "
                "API controls > Domain-wide delegation. See docs/GOOGLE-SETUP.md."
            )
        if "invalid_grant" in message:
            return False, (
                f"Google would not let the service account act as {user}. "
                "Check that the address exists and is in the same domain."
            )
        return False, f"{type(exc).__name__}: {message[:300]}"
