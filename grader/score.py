"""
Putting the two halves together.

Both halves are always shown separately in the report. A producer whose writing
is fine but who keeps forgetting the postal address has a different problem
from one whose emails are clean but say nothing, and a single blended number
hides which is which.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import mean

from .checks import Email, Finding, by_category, mechanical_score
from .grade import Judgement

# How the two halves combine into the headline number.
MECHANICAL_WEIGHT = 0.5
WRITING_WEIGHT = 0.5


def letter(score: int) -> str:
    if score >= 90:
        return "A"
    if score >= 80:
        return "B"
    if score >= 70:
        return "C"
    if score >= 60:
        return "D"
    return "F"


@dataclass
class Result:
    email: Email
    findings: list[Finding] = field(default_factory=list)
    judgement: Judgement = field(default_factory=Judgement)

    @property
    def mechanical(self) -> int:
        return mechanical_score(self.findings)

    @property
    def writing(self) -> int:
        return self.judgement.score if self.judgement.ok else 0

    @property
    def overall(self) -> int:
        if not self.judgement.ok:
            # The model failed on this one. Report the half that did work
            # rather than inventing a number.
            return self.mechanical
        return round(MECHANICAL_WEIGHT * self.mechanical + WRITING_WEIGHT * self.writing)

    @property
    def grade(self) -> str:
        return letter(self.overall)

    @property
    def blocking(self) -> list[Finding]:
        """Things that would stop the email arriving or land the company in
        trouble. These lead the report."""
        return [f for f in self.findings if f.severity == "high"]

    @property
    def compliance_failures(self) -> list[Finding]:
        return by_category(self.findings).get("compliance", [])


@dataclass
class ProducerSummary:
    email: str
    name: str
    results: list[Result] = field(default_factory=list)
    skipped: dict[str, int] = field(default_factory=dict)

    @property
    def graded_count(self) -> int:
        return len(self.results)

    @property
    def skipped_count(self) -> int:
        return sum(self.skipped.values())

    @property
    def average(self) -> int:
        return round(mean(r.overall for r in self.results)) if self.results else 0

    @property
    def average_mechanical(self) -> int:
        return round(mean(r.mechanical for r in self.results)) if self.results else 0

    @property
    def average_writing(self) -> int:
        usable = [r.writing for r in self.results if r.judgement.ok]
        return round(mean(usable)) if usable else 0

    @property
    def grade(self) -> str:
        return letter(self.average)

    @property
    def compliance_count(self) -> int:
        return sum(1 for r in self.results if r.compliance_failures)

    @property
    def worst(self) -> Result | None:
        return min(self.results, key=lambda r: r.overall) if self.results else None

    @property
    def best(self) -> Result | None:
        return max(self.results, key=lambda r: r.overall) if self.results else None

    @property
    def model_failures(self) -> int:
        return sum(1 for r in self.results if not r.judgement.ok)

    def recurring(self, minimum: int = 2) -> list[tuple[str, int, Finding]]:
        """Faults that show up in more than one email. A pattern is worth
        coaching; a one-off is worth ignoring."""
        counts: dict[str, int] = {}
        example: dict[str, Finding] = {}
        for result in self.results:
            for finding in {f.id: f for f in result.findings}.values():
                counts[finding.id] = counts.get(finding.id, 0) + 1
                example.setdefault(finding.id, finding)
        out = [
            (fid, n, example[fid])
            for fid, n in counts.items()
            if n >= minimum
        ]
        return sorted(out, key=lambda t: (-t[1], t[0]))
