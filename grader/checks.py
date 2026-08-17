"""
The deterministic half of the grade.

Everything in here is a rule, not a judgement. A rule always fires the same way
on the same email, it can be pointed at a specific character in the message, and
it can be argued with. That matters because these scores end up in front of the
people who wrote the emails: "your subject line is 94 characters and gets cut
off on a phone" is a conversation, "the AI gave you 62" is not.

The language-quality half of the grade lives in grade.py, where Claude reads the
email against the standard. Keeping the two apart means a bad day from the model
cannot quietly move somebody's spam-risk score.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------

# What each severity costs, out of 100.
SEVERITY_WEIGHT = {"high": 12, "medium": 6, "low": 2}

CATEGORIES = ("deliverability", "compliance", "hygiene")


@dataclass
class Finding:
    id: str
    category: str          # one of CATEGORIES
    severity: str          # high | medium | low
    message: str           # what is wrong, in plain words
    fix: str               # what to do about it
    evidence: str = ""     # the offending text, quoted

    @property
    def weight(self) -> int:
        return SEVERITY_WEIGHT.get(self.severity, 0)


@dataclass
class Email:
    """One outbound message, already parsed out of Gmail."""

    message_id: str = ""
    thread_id: str = ""
    date: str = ""
    sender: str = ""
    to: list[str] = field(default_factory=list)
    cc: list[str] = field(default_factory=list)
    subject: str = ""
    body_text: str = ""
    body_html: str = ""
    has_attachments: bool = False
    attachment_names: list[str] = field(default_factory=list)
    in_reply_to: str = ""
    references: str = ""
    labels: list[str] = field(default_factory=list)

    # Where this message sits in a sequence to the same prospect. 1 = first
    # contact, 2 = first follow-up, and so on. Set when reading from an archive
    # mailbox, where the whole thread is visible.
    touch: int = 1
    # True if the prospect wrote back at some point in the thread. A "follow-up"
    # to someone who already replied is a conversation, not chasing silence, and
    # must not be graded as cold outreach.
    prospect_replied: bool = False
    # True where the company phones the prospect before emailing. It changes
    # what is true about the message rather than what is good about it: an
    # email that says "following up on our call" is being accurate, and must
    # not be flagged for claiming contact that did happen.
    after_call: bool = False

    @property
    def is_follow_up(self) -> bool:
        return self.touch > 1

    @property
    def is_reply(self) -> bool:
        if self.in_reply_to.strip() or self.references.strip():
            return True
        return bool(re.match(r"^\s*(re|fw|fwd)\s*:", self.subject, re.I))

    @property
    def recipients(self) -> list[str]:
        return [*self.to, *self.cc]

    @property
    def body(self) -> str:
        """Prefer the plain text part; fall back to stripping the HTML."""
        if self.body_text.strip():
            return self.body_text
        return html_to_text(self.body_html)


# ---------------------------------------------------------------------------
# HTML handling
# ---------------------------------------------------------------------------


class _Stripper(HTMLParser):
    """Pull readable text, links and images out of an HTML body."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.chunks: list[str] = []
        self.links: list[str] = []
        self.images: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head"):
            self._skip += 1
        attrs_d = dict(attrs)
        if tag == "a" and attrs_d.get("href"):
            self.links.append(attrs_d["href"])
        if tag == "img":
            self.images.append(attrs_d.get("src", ""))
        if tag in ("br", "p", "div", "tr", "li"):
            self.chunks.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.chunks.append(data)


def _parse_html(html: str) -> _Stripper:
    parser = _Stripper()
    try:
        parser.feed(html or "")
        parser.close()
    except Exception:
        # A malformed body must never take the whole run down. Whatever was
        # parsed before the break is still usable.
        pass
    return parser


def html_to_text(html: str) -> str:
    text = "".join(_parse_html(html).chunks)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    return re.sub(r"\n\s*\n\s*\n+", "\n\n", text).strip()


# ---------------------------------------------------------------------------
# Rule data
# ---------------------------------------------------------------------------

# Phrases with a long history of tripping content filters. Kept deliberately
# short: a big list produces a finding on every email and the report stops
# being read. These are the ones that actually correlate with junk placement in
# ordinary business mail.
SPAM_PHRASES = [
    "act now", "apply now", "buy now", "call now", "click below", "click here",
    "limited time", "limited offer", "offer expires", "once in a lifetime",
    "order now", "special promotion", "urgent", "while supplies last",
    "100% free", "100% satisfied", "no obligation", "no strings attached",
    "risk free", "risk-free", "money back", "cash bonus", "extra income",
    "make money", "earn extra", "double your", "financial freedom",
    "guaranteed", "no catch", "no fees", "free trial", "free access",
    "congratulations", "you have been selected", "dear friend",
    "this is not spam", "not junk", "increase sales", "increase traffic",
    "best price", "lowest price", "incredible deal", "amazing offer",
]

URGENCY_PHRASES = [
    "act now", "urgent", "immediately", "expires today", "last chance",
    "don't miss", "hurry", "final notice", "today only", "ends tonight",
]

# Link shorteners hide the destination, which is exactly what filters dislike.
SHORTENER_HOSTS = {
    "bit.ly", "tinyurl.com", "goo.gl", "t.co", "ow.ly", "buff.ly", "is.gd",
    "rebrand.ly", "cutt.ly", "shorturl.at", "rb.gy", "tiny.cc", "lnkd.in",
}

# Merge tags from the usual sending tools. If one of these survives into a sent
# message the recipient sees "Hi {{first_name}}".
MERGE_TAG = re.compile(
    r"(\{\{[^}]{1,40}\}\})"        # {{first_name}}
    r"|(\{[A-Za-z_][A-Za-z0-9_ .]{0,38}\})"   # {FirstName}
    r"|(\[\[[^\]]{1,40}\]\])"      # [[company]]
    r"|(%%[A-Za-z0-9_]{1,38}%%)"   # %%CITY%%
    r"|(\*\|[A-Za-z0-9_ ]{1,38}\|\*)"        # *|FNAME|*  (Mailchimp)
)

# Looking BACK at a call or conversation. Deliberately past-tense only: an
# invitation to call ("give me a call", "happy to jump on a call") is the point
# of the email and must never be caught here.
PRIOR_CONTACT = re.compile(
    r"\bwe (?:spoke|talked|chatted)\b"
    r"|\bi (?:spoke|talked|chatted) (?:with|to)\b"
    r"|\bwhen we (?:spoke|talked)\b"
    r"|\b(?:great|nice|good|lovely) (?:speaking|talking|chatting)\b"
    r"|\bthanks? (?:for )?(?:taking|returning) (?:my|the|your) call\b"
    r"|\b(?:per|after|following|during|on|from) (?:our|the|that) "
    r"(?:call|conversation|chat|discussion)\b"
    r"|\bas (?:we )?discussed\b"
    r"|\bfollowing up on (?:our|the|your) (?:call|conversation|chat)\b"
    r"|\b(?:spoke|speaking) (?:with|to) (?:your|the) "
    r"(?:office|assistant|secretary|receptionist)\b"
    r"|\byour (?:office|assistant|secretary|receptionist) "
    r"(?:gave|passed|provided|shared)\b",
    re.I,
)

# A postal address, loosely. CAN-SPAM wants a real one in commercial mail.
ADDRESS_HINT = re.compile(
    r"\b(?:suite|ste\.?|floor|fl\.?|p\.?\s?o\.?\s?box|street|st\.|avenue|ave\.?"
    r"|road|rd\.?|boulevard|blvd\.?|drive|dr\.?|lane|ln\.?|parkway|pkwy\.?)\b",
    re.I,
)
US_ZIP = re.compile(r"\b\d{5}(?:-\d{4})?\b")

OPT_OUT = re.compile(
    r"\b(unsubscribe|opt[- ]?out|remove me|take me off|stop receiving"
    r"|no longer wish|reply\s+stop|manage (?:your )?preferences"
    # The conversational forms. These matter: a one-to-one cold email that
    # says "reply and I'll stop" is complying, and an earlier version of this
    # rule marked exactly that wording as a violation.
    r"|(?:rather|prefer|would\s+prefer)\s+not\s+to\s+hear"
    r"|rather\s+not\s+hear"
    r"|(?:don'?t|do\s+not)\s+want\s+to\s+hear"
    r"|say\s+(?:so|the\s+word)\s+and\s+i(?:'| w)ll\s+stop"
    r"|(?:tell|let)\s+me\s+(?:to\s+stop|and\s+i(?:'| w)ll\s+stop))\b",
    re.I,
)

URL_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.I)
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

# A question mark on its own is not a call to action; these are.
CTA_HINT = re.compile(
    r"\b(are you (?:free|available|open)|do you have|would you be|worth a"
    r"|open to|let me know|happy to|can we|shall we|book a|schedule a"
    r"|grab (?:a|15|20|30)|call this week|reply|interested)\b",
    re.I,
)

GREETING_RE = re.compile(
    r"^\s*(hi|hello|hey|dear|good (?:morning|afternoon|evening)|greetings)\b",
    re.I,
)


# ---------------------------------------------------------------------------
# Individual rules
# ---------------------------------------------------------------------------


def _quote(text: str, limit: int = 90) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def caps_ratio(text: str) -> tuple[float, int]:
    """Share of letters that are capitals, ignoring acronyms.

    Acronyms have to be excluded or the rule punishes ordinary business
    writing: "Your Q3 ROI" is 5 capitals out of 8 letters and would otherwise
    be reported as shouting. Anything four characters or shorter and fully
    upper case is treated as an acronym, which covers ROI, CEO, Q3, USA, API,
    DNC and the rest without needing a list.

    Returns (ratio, letters_considered) so callers can require a minimum
    amount of text before drawing a conclusion.
    """
    kept: list[str] = []
    for word in re.findall(r"[A-Za-z][A-Za-z0-9']*", text):
        letters = [c for c in word if c.isalpha()]
        if not letters:
            continue
        if len(word) <= 4 and all(c.isupper() for c in letters):
            continue  # acronym
        kept.extend(letters)
    if not kept:
        return 0.0, 0
    return sum(c.isupper() for c in kept) / len(kept), len(kept)


def _raw_caps_ratio(text: str) -> tuple[float, int]:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0, 0
    return sum(c.isupper() for c in letters) / len(letters), len(letters)


def is_shouting(text: str, threshold: float, min_letters: int) -> bool:
    """Two passes, because one is wrong in one direction or the other.

    Ignoring acronyms is what stops "Your Q3 ROI" being called shouting -- but
    taken alone it also swallows "READ THIS RIGHT NOW", where every word is
    short enough to look like an acronym. So: if nearly every letter in the
    text is a capital, it is shouting no matter how short the words are.
    Only below that do acronyms get excused.
    """
    raw, raw_letters = _raw_caps_ratio(text)
    if raw_letters >= min_letters and raw > 0.85:
        return True
    ratio, considered = caps_ratio(text)
    return considered >= min_letters and ratio > threshold


def check_subject(email: Email) -> list[Finding]:
    out: list[Finding] = []
    subject = email.subject.strip()

    if not subject:
        return [
            Finding(
                "subject.missing", "deliverability", "high",
                "The email has no subject line.",
                "Every cold email needs a subject. Blank subjects are filtered "
                "almost automatically.",
            )
        ]

    if len(subject) > 60:
        out.append(
            Finding(
                "subject.too_long", "hygiene", "medium",
                f"The subject is {len(subject)} characters. Phones cut off "
                "around 35 and most desktop inboxes around 60.",
                "Get the point into the first 6 or 7 words.",
                _quote(subject),
            )
        )

    if len(subject) < 3:
        out.append(
            Finding(
                "subject.too_short", "hygiene", "medium",
                "The subject is too short to say anything.",
                "Say what the email is about in a few words.",
                _quote(subject),
            )
        )

    if is_shouting(subject, threshold=0.6, min_letters=6):
        out.append(
            Finding(
                "subject.shouting", "deliverability", "high",
                "The subject is mostly capital letters.",
                "Write it in ordinary sentence case.",
                _quote(subject),
            )
        )

    if subject.count("!") >= 2 or "!!" in subject:
        out.append(
            Finding(
                "subject.exclamation", "deliverability", "medium",
                "More than one exclamation mark in the subject.",
                "One at most, and usually none.",
                _quote(subject),
            )
        )

    if re.search(r"[$€£]\s?\d", subject) or "%" in subject and re.search(r"\d\s?%", subject):
        out.append(
            Finding(
                "subject.money", "deliverability", "medium",
                "The subject leads with a price or a percentage.",
                "Money in the subject reads as an advert. Move it into the body.",
                _quote(subject),
            )
        )

    hits = [p for p in SPAM_PHRASES if p in subject.lower()]
    if hits:
        out.append(
            Finding(
                "subject.spam_phrase", "deliverability", "high",
                "The subject contains wording filters treat as promotional: "
                + ", ".join(f'"{h}"' for h in hits[:4]),
                "Say the same thing in your own words.",
                _quote(subject),
            )
        )

    if (
        email.touch == 1
        and email.is_reply
        and not (email.in_reply_to or email.references)
    ):
        out.append(
            Finding(
                "subject.fake_reply", "compliance", "high",
                'The subject starts with "Re:" or "Fwd:" but this is not a '
                "reply to anything.",
                "Faking a reply to a first contact is deceptive and is the kind "
                "of header that CAN-SPAM treats as misleading. Write a real "
                "subject.",
                _quote(subject),
            )
        )
    return out


def check_body_shape(email: Email) -> list[Finding]:
    out: list[Finding] = []
    body = email.body.strip()
    words = body.split()

    floor = 6 if email.is_follow_up else 20
    if len(words) < floor:
        out.append(
            Finding(
                "body.too_short", "hygiene", "medium",
                f"The message body is only {len(words)} words.",
                "A follow-up can be short, but it still needs a reason to "
                "reply." if email.is_follow_up else
                "A first contact needs enough for the reader to know who you "
                "are and why you are writing.",
            )
        )
    elif len(words) > 300:
        out.append(
            Finding(
                "body.too_long", "hygiene", "medium",
                f"The message is {len(words)} words. Cold emails past about "
                "150 tend not to get read to the end.",
                "Cut it to the ask and the one reason it is worth answering.",
            )
        )

    if is_shouting(body, threshold=0.35, min_letters=40):
        out.append(
            Finding(
                "body.shouting", "deliverability", "high",
                "A large share of the message is in capital letters.",
                "Use normal sentence case. Capitals are one of the oldest spam "
                "signals there is.",
            )
        )

    if body.count("!") >= 4:
        out.append(
            Finding(
                "body.exclamation", "deliverability", "medium",
                f"{body.count('!')} exclamation marks in the message.",
                "Keep it to one, if that.",
            )
        )

    if re.search(r"[!?]{3,}", body):
        out.append(
            Finding(
                "body.punctuation_run", "deliverability", "medium",
                "Runs of punctuation like !!! or ???.",
                "Use single punctuation.",
                _quote(re.search(r".{0,30}[!?]{3,}.{0,30}", body).group(0)),
            )
        )

    if not GREETING_RE.match(body):
        out.append(
            Finding(
                "body.no_greeting", "hygiene", "low",
                "The message does not open with a greeting.",
                "Open with the person's name. It is the cheapest signal that "
                "the email was meant for them.",
            )
        )

    if not CTA_HINT.search(body):
        out.append(
            Finding(
                "body.no_ask", "hygiene", "high",
                "There is no clear ask anywhere in the message.",
                "End with one specific question the reader can answer yes or "
                "no to. An email without an ask has nothing to reply to.",
            )
        )
    return out


def check_spam_language(email: Email) -> list[Finding]:
    out: list[Finding] = []
    body_lower = email.body.lower()

    hits = [p for p in SPAM_PHRASES if p in body_lower]
    if hits:
        severity = "high" if len(hits) >= 3 else "medium"
        out.append(
            Finding(
                "body.spam_phrase", "deliverability", severity,
                f"{len(hits)} phrase(s) filters associate with bulk mail: "
                + ", ".join(f'"{h}"' for h in hits[:5]),
                "Rewrite these in plain language. Any one of them is survivable; "
                "several together is what gets a message scored as promotional.",
            )
        )

    urgency = [p for p in URGENCY_PHRASES if p in body_lower]
    if len(urgency) >= 2:
        out.append(
            Finding(
                "body.false_urgency", "deliverability", "medium",
                "Manufactured urgency: " + ", ".join(f'"{u}"' for u in urgency[:4]),
                "Urgency that is not real reads as pressure and gets reported "
                "as spam more often than it gets replies.",
            )
        )
    return out


def check_links(email: Email) -> list[Finding]:
    out: list[Finding] = []
    parsed = _parse_html(email.body_html)
    links = list(parsed.links) + URL_RE.findall(email.body_text or "")
    links = [l for l in links if l.lower().startswith(("http://", "https://"))]

    if len(links) > 4:
        out.append(
            Finding(
                "links.too_many", "deliverability", "medium",
                f"{len(links)} links in a first-contact email.",
                "One link at most on a cold email. Several is a strong "
                "promotional signal and splits the reader's attention.",
            )
        )

    shortened = []
    insecure = []
    for link in links:
        host = (urlparse(link).hostname or "").lower().removeprefix("www.")
        if host in SHORTENER_HOSTS:
            shortened.append(link)
        if link.lower().startswith("http://"):
            insecure.append(link)

    if shortened:
        out.append(
            Finding(
                "links.shortener", "deliverability", "high",
                "Shortened links hide where they go: "
                + ", ".join(_quote(s, 40) for s in shortened[:3]),
                "Link to the real address. Shorteners are one of the strongest "
                "single spam signals because that is what phishing uses.",
            )
        )

    if insecure:
        out.append(
            Finding(
                "links.insecure", "deliverability", "low",
                f"{len(insecure)} link(s) use http:// rather than https://.",
                "Use https. Some clients warn on plain http links.",
                _quote(insecure[0], 50),
            )
        )

    # An image-only email is the classic way of hiding text from a filter.
    text_len = len(email.body.strip())
    if parsed.images and text_len < 120:
        out.append(
            Finding(
                "links.image_heavy", "deliverability", "high",
                f"{len(parsed.images)} image(s) and only {text_len} characters "
                "of text.",
                "Filters cannot read an image, so a message that is mostly "
                "picture gets treated as something with nothing to say. Put "
                "the message in text.",
            )
        )
    return out


def check_hygiene(email: Email) -> list[Finding]:
    out: list[Finding] = []
    haystack = f"{email.subject}\n{email.body}"

    tags = {m.group(0) for m in MERGE_TAG.finditer(haystack)}
    # A lone {word} is common in code samples, so only flag ones that look
    # like a real merge field.
    tags = {
        t for t in tags
        if not t.startswith("{") or t.startswith("{{")
        or re.match(r"^\{[A-Za-z_][A-Za-z0-9_ .]*\}$", t)
    }
    if tags:
        out.append(
            Finding(
                "hygiene.merge_tag", "hygiene", "high",
                "A mail-merge placeholder went out unfilled: "
                + ", ".join(sorted(tags)[:4]),
                "The recipient literally read that. Check the merge before "
                "sending, and make the sending tool refuse to send on a blank "
                "field.",
                _quote(next(iter(sorted(tags)))),
            )
        )

    # House rule, not a legal one, which is why it is hygiene rather than
    # compliance and why it can be switched off on its own. The company does
    # not want the email referring to a phone call at all -- the call usually
    # reached an assistant, so to the person reading it the reference means
    # nothing, or worse, reads as a claim they were part of something.
    prior = PRIOR_CONTACT.search(email.body)
    if prior:
        out.append(
            Finding(
                "hygiene.prior_contact_reference", "hygiene", "medium",
                "The message refers back to an earlier call or conversation.",
                "Take it out. Whoever was on the phone, the person reading "
                "this often was not, so it either means nothing to them or "
                "reads as a claim about them that is not true. Open on what "
                "the email is actually for.",
                _quote(prior.group(0)),
            )
        )

    if re.search(r"\b(lorem ipsum|todo|tbd|xxx|placeholder|insert \w+ here)\b",
                 haystack, re.I):
        out.append(
            Finding(
                "hygiene.placeholder", "hygiene", "high",
                "Placeholder text was left in the message.",
                "Read the message once before sending.",
            )
        )

    if email.has_attachments:
        out.append(
            Finding(
                "hygiene.attachment", "deliverability", "medium",
                "There is an attachment on a first contact: "
                + ", ".join(email.attachment_names[:3]),
                "Attachments from an unknown sender get quarantined and make "
                "people wary. Link to it instead, or offer to send it once "
                "they reply.",
            )
        )

    if len(email.recipients) > 1:
        out.append(
            Finding(
                "hygiene.multiple_recipients", "hygiene", "medium",
                f"{len(email.recipients)} recipients on one cold email.",
                "Send them individually. A visible list of strangers tells "
                "every one of them it was a blast.",
            )
        )

    if email.body_html and not email.body_text.strip():
        out.append(
            Finding(
                "hygiene.no_plain_text", "deliverability", "low",
                "The message was sent as HTML with no plain-text alternative.",
                "Sending both is a small but real deliverability gain.",
            )
        )
    return out


def check_compliance(email: Email) -> list[Finding]:
    """CAN-SPAM. This is the bit that costs money if it goes wrong."""
    out: list[Finding] = []
    body = email.body

    if not OPT_OUT.search(body):
        out.append(
            Finding(
                "compliance.no_opt_out", "compliance", "high",
                "There is no way for the recipient to opt out.",
                "US commercial email has to offer one, and it has to keep "
                "working for 30 days. One line at the bottom is enough: "
                '"If you would rather not hear from me, just reply and say so."',
            )
        )

    if not (ADDRESS_HINT.search(body) and US_ZIP.search(body)):
        out.append(
            Finding(
                "compliance.no_postal_address", "compliance", "high",
                "No physical postal address in the message.",
                "CAN-SPAM requires a valid physical address in commercial "
                "email. Put it in the signature once and it is solved forever.",
            )
        )

    # after_call only suppresses this where the call reached the RECIPIENT.
    # Calling a switchboard and being given an address by an assistant is the
    # common case, and to the person reading the email the claim is still
    # untrue -- so this stays on unless the company says otherwise.
    if not email.after_call and re.search(
            r"\b(you (?:requested|asked|signed up|opted in)|as you requested"
            r"|per your request|following up on your (?:enquiry|inquiry|request))\b",
            body, re.I):
        out.append(
            Finding(
                "compliance.false_prior_contact", "compliance", "medium",
                "The message claims the recipient asked to be contacted.",
                "Drop it. Even where a call did happen, it was often with an "
                "assistant rather than the person reading this -- so to them "
                "the claim is simply untrue, and an untrue claim of prior "
                "contact is what turns a complaint into a problem. Refer to "
                "the call plainly if you want to, without saying they asked.",
            )
        )
    return out


ALL_CHECKS = (
    check_subject,
    check_body_shape,
    check_spam_language,
    check_links,
    check_hygiene,
    check_compliance,
)

# Every code a rule can emit. Its job is to catch a typo in the settings: a
# disabled_rules entry that matches nothing would otherwise switch off nothing
# at all, silently, and the first anyone would know is a score that never
# changed. test_checks.py walks the source and fails if a rule is missing here.
RULE_CODES = frozenset({
    "body.exclamation",
    "body.false_urgency",
    "body.no_ask",
    "body.no_greeting",
    "body.punctuation_run",
    "body.shouting",
    "body.spam_phrase",
    "body.too_long",
    "body.too_short",
    "compliance.false_prior_contact",
    "compliance.no_opt_out",
    "compliance.no_postal_address",
    "hygiene.attachment",
    "hygiene.merge_tag",
    "hygiene.multiple_recipients",
    "hygiene.no_plain_text",
    "hygiene.placeholder",
    "hygiene.prior_contact_reference",
    "links.image_heavy",
    "links.insecure",
    "links.shortener",
    "links.too_many",
    "subject.exclamation",
    "subject.fake_reply",
    "subject.missing",
    "subject.money",
    "subject.shouting",
    "subject.spam_phrase",
    "subject.too_long",
    "subject.too_short",
})


def run_all(email: Email, disabled: set[str] | None = None) -> list[Finding]:
    """Every rule, in a stable order, with one broken rule unable to sink the
    rest.

    `disabled` drops findings the company has decided not to be measured on.
    They are dropped after the rule runs rather than by skipping the check, so
    turning one rule off cannot change what a sibling rule sees.
    """
    findings: list[Finding] = []
    for check in ALL_CHECKS:
        try:
            findings.extend(check(email))
        except Exception as exc:  # pragma: no cover - defensive
            findings.append(
                Finding(
                    f"internal.{check.__name__}", "hygiene", "low",
                    "A check could not be run on this email.",
                    "This is a bug in the grader, not a problem with the email.",
                    f"{type(exc).__name__}: {exc}",
                )
            )
    if disabled:
        # internal.* is a bug report, not a rule, so the settings cannot
        # silence it -- a broken check has to stay visible.
        findings = [
            f for f in findings
            if f.id.startswith("internal.") or f.id not in disabled
        ]
    return findings


def mechanical_score(findings: list[Finding]) -> int:
    """100 down to 0. Deductions, so the arithmetic is explainable to the
    person being graded."""
    return max(0, 100 - sum(f.weight for f in findings))


def by_category(findings: list[Finding]) -> dict[str, list[Finding]]:
    out: dict[str, list[Finding]] = {c: [] for c in CATEGORIES}
    for f in findings:
        out.setdefault(f.category, []).append(f)
    return out
