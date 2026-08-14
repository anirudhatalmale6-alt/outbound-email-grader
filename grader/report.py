"""
The two reports.

The manager's report is a league table plus the things that need a decision.
The producer's report is coaching: their own emails, what to change, and the
sentence they should have written instead. Nobody's report contains anybody
else's emails.

Both go out as HTML with a plain-text alternative, because a grading email that
lands in the junk folder is a poor advert for a grader that measures junk-folder
risk.
"""

from __future__ import annotations

import html
from datetime import date

from .score import ProducerSummary, Result

# Inline styles only. Gmail strips <style> blocks in a lot of situations.
BODY = "font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#1a1a1a;line-height:1.5"
MUTED = "color:#666;font-size:13px"
CARD = "border:1px solid #e3e3e3;border-radius:8px;padding:14px 16px;margin:12px 0"


def _colour(score: int) -> str:
    if score >= 80:
        return "#1a7f37"
    if score >= 60:
        return "#9a6700"
    return "#b42318"


def _e(text: str) -> str:
    return html.escape(str(text or ""))


def _pill(text: str, colour: str) -> str:
    return (
        f'<span style="display:inline-block;background:{colour};color:#fff;'
        f'border-radius:11px;padding:2px 10px;font-size:12px;font-weight:600">'
        f"{_e(text)}</span>"
    )


def _arrow(now: int, before: int | None) -> str:
    if before is None:
        return ""
    delta = now - before
    if delta >= 3:
        return f' <span style="color:#1a7f37">&#9650; up {delta}</span>'
    if delta <= -3:
        return f' <span style="color:#b42318">&#9660; down {abs(delta)}</span>'
    return ' <span style="color:#666">level</span>'


# ---------------------------------------------------------------------------
# Manager report
# ---------------------------------------------------------------------------


def manager_html(
    summaries: list[ProducerSummary],
    when: date,
    previous: dict[str, int | None],
    errors: list[str],
    shadow: bool,
) -> str:
    ranked = sorted(summaries, key=lambda s: (-s.average, s.name or s.email))
    total_graded = sum(s.graded_count for s in summaries)
    total_skipped = sum(s.skipped_count for s in summaries)
    compliance_total = sum(s.compliance_count for s in summaries)
    total_follow_ups = sum(
        1 for s in summaries for r in s.results if r.email.is_follow_up
    )

    rows = []
    for s in ranked:
        if not s.graded_count:
            rows.append(
                f'<tr><td style="padding:8px 10px;border-bottom:1px solid #eee">'
                f"{_e(s.name or s.email)}</td>"
                f'<td colspan="4" style="padding:8px 10px;border-bottom:1px solid #eee;{MUTED}">'
                f"no outreach emails today</td></tr>"
            )
            continue
        rows.append(
            f'<tr><td style="padding:8px 10px;border-bottom:1px solid #eee">'
            f"<strong>{_e(s.name or s.email)}</strong></td>"
            f'<td style="padding:8px 10px;border-bottom:1px solid #eee;text-align:center">'
            f'<span style="color:{_colour(s.average)};font-weight:700;font-size:17px">'
            f"{s.average}</span> <span style=\"{MUTED}\">{s.grade}</span>"
            f"{_arrow(s.average, previous.get(s.email))}</td>"
            f'<td style="padding:8px 10px;border-bottom:1px solid #eee;text-align:center">'
            f"{s.graded_count}</td>"
            f'<td style="padding:8px 10px;border-bottom:1px solid #eee;text-align:center;'
            f'color:{_colour(s.average_mechanical)}">{s.average_mechanical}</td>'
            f'<td style="padding:8px 10px;border-bottom:1px solid #eee;text-align:center;'
            f'color:{_colour(s.average_writing)}">{s.average_writing}</td></tr>'
        )

    # Anything with a compliance failure gets its own section -- this is the
    # part with money attached to it.
    compliance_blocks = []
    for s in ranked:
        offenders = [r for r in s.results if r.compliance_failures]
        if not offenders:
            continue
        items = []
        for r in offenders[:5]:
            faults = "; ".join(f.message for f in r.compliance_failures)
            items.append(
                f'<li style="margin:6px 0">"{_e(r.email.subject or "(no subject)")}"'
                f' &mdash; <span style="color:#b42318">{_e(faults)}</span></li>'
            )
        more = (
            f'<li style="{MUTED}">and {len(offenders) - 5} more</li>'
            if len(offenders) > 5 else ""
        )
        compliance_blocks.append(
            f'<div style="{CARD};border-color:#f3c4c4;background:#fffafa">'
            f"<strong>{_e(s.name or s.email)}</strong>"
            f'<ul style="margin:8px 0 0;padding-left:20px">{"".join(items)}{more}</ul></div>'
        )

    pattern_blocks = []
    for s in ranked:
        recurring = s.recurring(minimum=2)[:3]
        if not recurring:
            continue
        items = "".join(
            f'<li style="margin:4px 0">{_e(f.message)} '
            f'<span style="{MUTED}">({n} of {s.graded_count} emails)</span></li>'
            for _, n, f in recurring
        )
        pattern_blocks.append(
            f'<div style="{CARD}"><strong>{_e(s.name or s.email)}</strong>'
            f'<ul style="margin:8px 0 0;padding-left:20px">{items}</ul></div>'
        )

    shadow_note = ""
    if shadow:
        shadow_note = (
            f'<div style="{CARD};background:#fff8e6;border-color:#f0d9a0">'
            "<strong>Nobody but you is receiving these.</strong><br>"
            f'<span style="{MUTED}">The grader is in shadow mode, so the '
            "producers have not been emailed. Turn it off in config.yaml once "
            "the scores look right to you.</span></div>"
        )

    error_note = ""
    if errors:
        items = "".join(f"<li>{_e(e)}</li>" for e in errors[:10])
        error_note = (
            f'<div style="{CARD};background:#fffafa;border-color:#f3c4c4">'
            f"<strong>Problems during this run</strong>"
            f'<ul style="margin:8px 0 0;padding-left:20px">{items}</ul></div>'
        )

    return f"""<div style="{BODY};max-width:720px;margin:0 auto">
<h2 style="margin:0 0 4px">Outbound email report</h2>
<div style="{MUTED}">{when:%A %d %B %Y}</div>

{shadow_note}

<div style="{CARD}">
<span style="font-size:15px">
<strong>{total_graded}</strong> outreach emails graded across
<strong>{len(summaries)}</strong> producers{f' ({total_follow_ups} of them follow-ups)' if total_follow_ups else ''}.
{f'<strong style="color:#b42318">{compliance_total}</strong> had a compliance problem.' if compliance_total else 'No compliance problems.'}
</span><br>
<span style="{MUTED}">{total_skipped} other messages were not graded (replies and
internal mail &mdash; see the bottom of this email).</span>
</div>

<h3 style="margin:22px 0 6px">Scores</h3>
<table style="border-collapse:collapse;width:100%;font-size:14px">
<tr style="text-align:left;{MUTED}">
  <th style="padding:6px 10px">Producer</th>
  <th style="padding:6px 10px;text-align:center">Overall</th>
  <th style="padding:6px 10px;text-align:center">Emails</th>
  <th style="padding:6px 10px;text-align:center">Technical</th>
  <th style="padding:6px 10px;text-align:center">Writing</th>
</tr>
{"".join(rows)}
</table>
<div style="{MUTED};margin-top:6px">Technical = spam-filter risk, formatting and
compliance, measured by fixed rules. Writing = how the email reads against your
standard.</div>

{f'<h3 style="margin:22px 0 6px;color:#b42318">Compliance &mdash; needs a decision</h3>{"".join(compliance_blocks)}' if compliance_blocks else ""}

{f'<h3 style="margin:22px 0 6px">Recurring habits worth a word</h3>{"".join(pattern_blocks)}' if pattern_blocks else ""}

{error_note}

<div style="{MUTED};margin-top:26px;border-top:1px solid #eee;padding-top:10px">
Cold outreach to people outside the company is scored -- first contacts and the
follow-ups chasing them, each judged as what it is. Once a prospect replies the
thread becomes a conversation and is no longer graded, along with internal mail
and automated messages. Those are counted, not scored.
</div>
</div>"""


def manager_text(summaries: list[ProducerSummary], when: date, shadow: bool) -> str:
    ranked = sorted(summaries, key=lambda s: (-s.average, s.name or s.email))
    lines = [f"Outbound email report - {when:%d %B %Y}", ""]
    if shadow:
        lines += ["SHADOW MODE: producers have not been emailed.", ""]
    for s in ranked:
        if not s.graded_count:
            lines.append(f"  {s.name or s.email}: no outreach emails today")
            continue
        lines.append(
            f"  {s.name or s.email}: {s.average} ({s.grade}) over "
            f"{s.graded_count} emails "
            f"[technical {s.average_mechanical}, writing {s.average_writing}]"
        )
        if s.compliance_count:
            lines.append(f"      {s.compliance_count} with a compliance problem")
    lines += ["", "Cold outreach only: first contacts and follow-ups chasing "
              "them. Threads where the prospect replied are not graded."]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Producer report
# ---------------------------------------------------------------------------


def _result_block(result: Result) -> str:
    parts = [
        f'<div style="{CARD}">'
        f'<div style="display:flex;justify-content:space-between;align-items:baseline">'
        f'<strong style="font-size:15px">{_e(result.email.subject or "(no subject)")}'
        + (f'<span style="{MUTED};font-weight:400"> &middot; follow-up '
           f'{result.email.touch - 1}</span>' if result.email.is_follow_up else "")
        + "</strong>"
        f'<span style="color:{_colour(result.overall)};font-weight:700;font-size:17px">'
        f"{result.overall}</span></div>"
    ]

    if result.judgement.summary:
        parts.append(f'<div style="margin:6px 0 0">{_e(result.judgement.summary)}</div>')

    if result.judgement.did_well:
        items = "".join(f"<li>{_e(d)}</li>" for d in result.judgement.did_well)
        parts.append(
            '<div style="margin:10px 0 0"><span style="color:#1a7f37;font-weight:600">'
            "What worked</span>"
            f'<ul style="margin:4px 0 0;padding-left:20px">{items}</ul></div>'
        )

    blocking = result.blocking
    if blocking:
        items = "".join(
            f'<li style="margin:5px 0"><strong>{_e(f.message)}</strong><br>'
            f'<span style="{MUTED}">{_e(f.fix)}</span></li>'
            for f in blocking
        )
        parts.append(
            '<div style="margin:10px 0 0"><span style="color:#b42318;font-weight:600">'
            "Fix these first</span>"
            f'<ul style="margin:4px 0 0;padding-left:20px">{items}</ul></div>'
        )

    lesser = [f for f in result.findings if f.severity != "high"]
    if lesser:
        items = "".join(
            f'<li style="margin:4px 0">{_e(f.message)} '
            f'<span style="{MUTED}">{_e(f.fix)}</span></li>'
            for f in lesser
        )
        parts.append(
            '<div style="margin:10px 0 0"><span style="color:#9a6700;font-weight:600">'
            "Smaller things</span>"
            f'<ul style="margin:4px 0 0;padding-left:20px">{items}</ul></div>'
        )

    if result.judgement.issues:
        items = "".join(
            f'<li style="margin:6px 0"><strong>{_e(i.criterion)}</strong> &mdash; '
            f'{_e(i.problem)}<br><span style="{MUTED}">Try: {_e(i.fix)}</span></li>'
            for i in result.judgement.issues
        )
        parts.append(
            '<div style="margin:10px 0 0"><span style="font-weight:600">'
            "Against the standard</span>"
            f'<ul style="margin:4px 0 0;padding-left:20px">{items}</ul></div>'
        )

    if result.judgement.rewritten_opening:
        parts.append(
            f'<div style="margin:12px 0 0;padding:10px 12px;background:#f6f8fa;'
            f'border-radius:6px"><span style="{MUTED}">A stronger opening</span><br>'
            f"<em>{_e(result.judgement.rewritten_opening)}</em></div>"
        )

    if not result.judgement.ok:
        parts.append(
            f'<div style="margin:10px 0 0;{MUTED}">The writing half of this grade '
            f"could not be produced ({_e(result.judgement.error)}). The score "
            "shown is the technical half only.</div>"
        )

    parts.append("</div>")
    return "".join(parts)


def producer_html(
    summary: ProducerSummary, when: date, previous: int | None
) -> str:
    # First name only. A report that opens "Hi Maria" reads like feedback from
    # a colleague; one that opens with a bare score reads like a performance
    # file, and people stop opening it.
    first_name = _e((summary.name or "").split()[0]) if summary.name else ""
    greeting = f"<p>Hi {first_name},</p>" if first_name else ""

    if not summary.results:
        return (
            f'<div style="{BODY};max-width:680px;margin:0 auto">'
            f"<h2>Your outbound email report</h2>"
            f'<div style="{MUTED}">{when:%A %d %B %Y}</div>'
            f"{greeting}"
            f"<p>No outreach emails to grade today.</p></div>"
        )

    worst_first = sorted(summary.results, key=lambda r: r.overall)
    blocks = "".join(_result_block(r) for r in worst_first[:8])
    more = (
        f'<div style="{MUTED}">and {len(worst_first) - 8} more, all scoring '
        f"{worst_first[8].overall} or above</div>"
        if len(worst_first) > 8 else ""
    )

    recurring = summary.recurring(minimum=2)[:3]
    habit_block = ""
    if recurring:
        items = "".join(
            f'<li style="margin:5px 0">{_e(f.message)} '
            f'<span style="{MUTED}">({n} of your {summary.graded_count} emails) '
            f"&mdash; {_e(f.fix)}</span></li>"
            for _, n, f in recurring
        )
        habit_block = (
            f'<div style="{CARD};background:#f6f8fa">'
            "<strong>The one thing worth changing</strong>"
            f'<ul style="margin:8px 0 0;padding-left:20px">{items}</ul></div>'
        )

    return f"""<div style="{BODY};max-width:680px;margin:0 auto">
<h2 style="margin:0 0 4px">Your outbound email report</h2>
<div style="{MUTED}">{when:%A %d %B %Y}</div>
{greeting}

<div style="{CARD};text-align:center">
<div style="font-size:34px;font-weight:700;color:{_colour(summary.average)}">
{summary.average}<span style="font-size:19px;color:#666"> / 100</span></div>
<div>{_pill(summary.grade, _colour(summary.average))}{_arrow(summary.average, previous)}</div>
<div style="{MUTED};margin-top:6px">across {summary.graded_count} outreach
email{"s" if summary.graded_count != 1 else ""}</div>
</div>

{habit_block}

<h3 style="margin:22px 0 6px">Email by email</h3>
<div style="{MUTED};margin-bottom:4px">Lowest scoring first, so the useful bit is
at the top. Follow-ups are marked.</div>
{blocks}
{more}

<div style="{MUTED};margin-top:26px;border-top:1px solid #eee;padding-top:10px">
Your cold outreach is graded -- first contacts and the follow-ups chasing them.
A follow-up is judged as a follow-up, not against the first-contact standard.
Once someone replies, that thread stops being graded. Internal mail is never
looked at. Scores come from two things: fixed technical rules (spam-filter risk,
formatting, compliance) and a reading of the email against the company standard.
</div>
</div>"""


def producer_text(summary: ProducerSummary, when: date) -> str:
    if not summary.results:
        return (
            f"Your outbound email report - {when:%d %B %Y}\n\n"
            "No outreach emails to grade today."
        )
    lines = [
        f"Your outbound email report - {when:%d %B %Y}",
        "",
        f"Overall: {summary.average}/100 ({summary.grade}) across "
        f"{summary.graded_count} outreach emails.",
        "",
    ]
    for result in sorted(summary.results, key=lambda r: r.overall)[:8]:
        lines.append(f"  [{result.overall}] {result.email.subject or '(no subject)'}")
        if result.judgement.summary:
            lines.append(f"      {result.judgement.summary}")
        for finding in result.blocking:
            lines.append(f"      ! {finding.message} -> {finding.fix}")
        for issue in result.judgement.issues[:3]:
            lines.append(f"      - {issue.criterion}: {issue.problem}")
            lines.append(f"        Try: {issue.fix}")
        lines.append("")
    lines.append("Cold outreach only: first contacts and the follow-ups "
                 "chasing them.")
    return "\n".join(lines)
