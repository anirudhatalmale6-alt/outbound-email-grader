#!/usr/bin/env python3
"""Generate sample reports with realistic data, no network needed."""
import sys, pathlib
from datetime import date
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from grader import checks, report
from grader.checks import Email
from grader.grade import Issue, Judgement
from grader.score import ProducerSummary, Result

def mk(subject, body, score, summary, well, issues):
    e = Email(message_id=subject[:10], to=["dana@acmemedia.com"], subject=subject, body_text=body)
    return Result(email=e, findings=checks.run_all(e),
                  judgement=Judgement(score=score, summary=summary, did_well=well,
                                      issues=[Issue(*i) for i in issues],
                                      rewritten_opening=""))

SIG = ("\n\nBest,\nJoe Bianco\nAll Access\n1200 Market Street, Suite 400, Dallas, TX 75201"
       "\nIf you would rather not hear from me, just reply and say so.")

joe = ProducerSummary(email="joe@allaccessptv.com", name="Joe Bianco")
joe.results = [
    mk("Question about Acme Media's Q3 slate",
       "Hi Dana,\n\nI saw Acme picked up two new regional affiliates last month. "
       "We produce the segment packages for three broadcasters at that stage, and "
       "the handover is usually the part that costs them a quarter.\n\nWould you be "
       "open to a short call next week?" + SIG,
       88, "Strong opening and a clear single ask.",
       ["Opens with something specific and checkable about Acme, not the industry.",
        "One ask, answerable yes or no."],
       [("The offer", "\"the part that costs them a quarter\" is vague about what it costs.",
         "Name the actual cost - weeks of delay, or a number.", "low")]),
    mk("Following up on the RFP",
       "Hi Marcus,\n\nI wanted to reach out because we are a full service production "
       "company with over 20 years of experience delivering best in class content "
       "solutions for a wide range of clients across many verticals. Our team is "
       "passionate about storytelling. Let me know if you would like to learn more "
       "about what we can do for you." + SIG,
       46, "Talks entirely about us and never about Marcus.",
       [],
       [("Opening", "\"I wanted to reach out because we are a full service production company\" "
         "opens on our own company.",
         "Open on something specific about Marcus's company instead.", "high"),
        ("The offer", "\"best in class content solutions\" and \"passionate about storytelling\" "
         "say nothing a competitor could not also say.",
         "Replace with one concrete thing you did for a company like his.", "high"),
        ("The ask", "\"let me know if you would like to learn more\" puts the work on him.",
         "Ask a specific yes/no question: are you free Thursday afternoon?", "medium")]),
]
joe.skipped = {"reply, not a first contact": 14, "internal only": 6}

maria = ProducerSummary(email="maria@allaccessptv.com", name="Maria Lopez")
maria.results = [
    mk("ACT NOW - LIMITED TIME OFFER!!!",
       "HI {{first_name}}!!!\n\nThis is a RISK FREE limited time offer and you have "
       "been selected! Act now, this expires today!!! CLICK HERE "
       "http://bit.ly/3xk9zq to buy now and save $500!!!\n\nMaria",
       22, "Reads as a mass promotion rather than a message to a person.",
       [],
       [("Opening", "The merge field never filled in, so the recipient read \"HI {{first_name}}\".",
         "Check the merge before sending.", "high"),
        ("Tone", "Capitals and exclamation marks throughout.",
         "Write it as one sentence you would say out loud.", "high")]),
    mk("Quick question",
       "Hi Priya,\n\nGot a minute?\n\nMaria",
       38, "Too short to give the reader anything to say yes to.",
       ["Short, which is the right instinct."],
       [("The offer", "There is no reason given for the call at all.",
         "One sentence on why it is worth her time.", "high")]),
]
maria.skipped = {"internal only": 3}

sam = ProducerSummary(email="sam@allaccessptv.com", name="Sam Okafor")

summaries = [joe, maria, sam]
out = pathlib.Path("reports"); out.mkdir(exist_ok=True)
(out / "sample-manager.html").write_text(
    report.manager_html(summaries, date(2026, 8, 14),
                        {"joe@allaccessptv.com": 62, "maria@allaccessptv.com": 34},
                        [], shadow=True), encoding="utf-8")
(out / "sample-producer.html").write_text(
    report.producer_html(maria, date(2026, 8, 14), previous=34), encoding="utf-8")
print("manager:", joe.average, maria.average)
print("maria findings:", sorted({f.id for r in maria.results for f in r.findings}))
print("wrote reports/sample-manager.html and reports/sample-producer.html")
