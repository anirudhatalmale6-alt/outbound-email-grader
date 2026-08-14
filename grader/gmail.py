"""
Reading sent mail out of Google Workspace.

Uses a service account with domain-wide delegation, so there is no per-person
consent screen and nobody has to hand over a password. The scope is
gmail.readonly plus gmail.send -- read what was sent, and send the reports.
Nothing in this program can delete, move, or modify a message, because the
scope to do so is never requested.

Worth knowing: domain-wide delegation is a lot of power. The admin who grants
it is granting the ability to read every mailbox in the domain. The scope list
below is the only thing limiting it, and it is enforced by Google, not by this
code -- see docs/GOOGLE-SETUP.md.
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


def _service(cfg: Config, user: str):
    """A Gmail client acting as `user`."""
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
    except ImportError as exc:  # pragma: no cover
        raise GmailError(
            "The Google libraries are not installed. Run:\n"
            "  pip install -r requirements.txt"
        ) from exc

    if not cfg.service_account_file.exists():
        raise GmailError(f"Service account file not found: {cfg.service_account_file}")

    try:
        creds = service_account.Credentials.from_service_account_file(
            str(cfg.service_account_file), scopes=SCOPES
        ).with_subject(user)
    except Exception as exc:
        raise GmailError(f"Could not read the service account key: {exc}") from exc

    return build("gmail", "v1", credentials=creds, cache_discovery=False)


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
    except Exception as exc:
        message = str(exc)
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
