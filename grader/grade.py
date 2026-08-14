"""
The judgement half of the grade: Claude reading one email against the client's
own written standard.

Two rules shape everything here.

The standard is the client's, not the model's. The rubric file is loaded and
passed in verbatim, and the prompt says so explicitly. Without that, the grader
quietly starts marking against generic cold-email advice from the internet, and
the producers get told to change things their employer never asked for.

The model never sees a spam-risk score to move. Deliverability and CAN-SPAM are
decided by the rules in checks.py. Claude is asked about writing quality only,
so a bad generation cannot invent a compliance violation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from .checks import Email
from .config import Config

SYSTEM = """You are grading one outbound sales email against a written standard \
supplied by the company that sent it.

The standard is the whole of the law here. Do not add advice from general \
cold-email practice, and do not mark something down because you would have \
written it differently. If the standard does not mention something, it is not a \
fault. If the standard contradicts what you believe is best practice, follow the \
standard.

You are judging the writing only. Spam-filter risk, legal compliance and \
technical formatting are assessed separately by a different system, so ignore \
them entirely - say nothing about unsubscribe links, postal addresses, subject \
length in characters, or link counts.

Grade honestly. Most real emails are somewhere in the middle; a 90 should mean \
the email genuinely could not be improved much, and a 40 should mean it is going \
to fail. Do not cluster everything around 70 to be safe.

Write the feedback to the person who sent it. Be specific and quote their actual \
words when you point at something. Never be sarcastic or personal. The goal is \
that they can rewrite the email tomorrow knowing exactly what to change."""

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {
            "type": "integer",
            "description": "0-100 against the supplied standard.",
        },
        "summary": {
            "type": "string",
            "description": "One sentence on how this email did.",
        },
        "did_well": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Specific things the writer got right. May be empty.",
        },
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "criterion": {
                        "type": "string",
                        "description": "The part of the standard this relates to.",
                    },
                    "problem": {
                        "type": "string",
                        "description": "What is wrong, quoting their words.",
                    },
                    "fix": {
                        "type": "string",
                        "description": "What to write instead. Be concrete.",
                    },
                    "severity": {
                        "type": "string",
                        "enum": ["high", "medium", "low"],
                    },
                },
                "required": ["criterion", "problem", "fix", "severity"],
                "additionalProperties": False,
            },
        },
        "rewritten_opening": {
            "type": "string",
            "description": (
                "A stronger version of the first two sentences only, in the "
                "sender's own voice. Empty string if the opening is already good."
            ),
        },
    },
    "required": ["score", "summary", "did_well", "issues", "rewritten_opening"],
    "additionalProperties": False,
}


@dataclass
class Issue:
    criterion: str
    problem: str
    fix: str
    severity: str


@dataclass
class Judgement:
    score: int = 0
    summary: str = ""
    did_well: list[str] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    rewritten_opening: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


def load_rubric(cfg: Config) -> str:
    if not cfg.rubric_file.exists():
        raise FileNotFoundError(
            f"No standard found at {cfg.rubric_file}.\n"
            "This is the document describing what a good cold email looks like "
            "at this company. Nothing can be graded without it."
        )
    text = cfg.rubric_file.read_text(encoding="utf-8").strip()
    if len(text) < 80:
        raise ValueError(
            f"{cfg.rubric_file} is nearly empty. The grader needs the actual "
            "standard, not a placeholder."
        )
    return text


def _render(email: Email) -> str:
    body = email.body.strip()
    # Long bodies get trimmed rather than blowing the request up; the opening
    # is what the standard mostly concerns anyway.
    if len(body) > 8000:
        body = body[:8000] + "\n[... truncated for grading ...]"
    return (
        f"Subject: {email.subject}\n"
        f"To: {', '.join(email.recipients) or '(none)'}\n"
        f"---\n{body}"
    )


class Grader:
    def __init__(self, cfg: Config, rubric: str) -> None:
        import anthropic

        self.cfg = cfg
        self.rubric = rubric
        self.client = anthropic.Anthropic(api_key=cfg.anthropic_api_key)

    def grade(self, email: Email) -> Judgement:
        try:
            response = self.client.messages.create(
                model=self.cfg.model,
                max_tokens=4096,
                system=[
                    {"type": "text", "text": SYSTEM},
                    {
                        "type": "text",
                        # The standard is identical on every call, so caching it
                        # means it is paid for once a day rather than once per
                        # email. On a few hundred emails that is most of the bill.
                        "text": "THE COMPANY'S STANDARD:\n\n" + self.rubric,
                        "cache_control": {"type": "ephemeral"},
                    },
                ],
                thinking={"type": "adaptive"},
                output_config={
                    "effort": self.cfg.effort,
                    "format": {"type": "json_schema", "schema": RESPONSE_SCHEMA},
                },
                messages=[{"role": "user", "content": _render(email)}],
            )
        except Exception as exc:
            return Judgement(error=f"{type(exc).__name__}: {str(exc)[:200]}")

        if getattr(response, "stop_reason", None) == "refusal":
            return Judgement(error="The model declined to grade this email.")

        text = next((b.text for b in response.content if b.type == "text"), "")
        if not text.strip():
            return Judgement(error="The model returned nothing.")

        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            return Judgement(error=f"Could not read the model's answer: {exc}")

        return Judgement(
            score=max(0, min(100, int(data.get("score", 0)))),
            summary=str(data.get("summary", "")).strip(),
            did_well=[str(d).strip() for d in data.get("did_well", []) if str(d).strip()],
            issues=[
                Issue(
                    criterion=str(i.get("criterion", "")).strip(),
                    problem=str(i.get("problem", "")).strip(),
                    fix=str(i.get("fix", "")).strip(),
                    severity=str(i.get("severity", "medium")).strip().lower(),
                )
                for i in data.get("issues", [])
            ],
            rewritten_opening=str(data.get("rewritten_opening", "")).strip(),
        )
