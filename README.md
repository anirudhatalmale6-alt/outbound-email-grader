# Outbound email grader

Reads what your producers actually sent, grades each cold email, and emails a
daily report: one to you with everybody's scores, one to each producer with
their own emails and what to change.

---

## Getting started

```
pip install -r requirements.txt
cp config.yaml.example config.yaml        # then edit it
cp rubric.md.example rubric.md            # then replace it with YOUR standard
python3 check.py                          # tells you what is still missing
python3 run_daily.py --dry-run --days 7   # grades a week, sends nothing
```

Google access takes about fifteen minutes to set up once:
**docs/GOOGLE-SETUP.md**.

When the reports look right, put it on a schedule:

```
0 18 * * 1-5  cd /path/to/email-grader && /usr/bin/python3 run_daily.py >> run.log 2>&1
```

---

## Three things worth knowing before you turn it on

**It starts in shadow mode.** Only you get reports. The producers get nothing
until you set `shadow_mode: false`. Leave it on for a week or two, read the
grades, and decide whether you agree with them first. An automated email telling
somebody they scored 54 is very hard to take back, and the first version of any
grader is wrong about something.

**It grades cold outreach — first contacts and the follow-ups chasing them —
and judges each as what it is.** A follow-up is expected to be shorter, to skip
re-introducing the company, and to give a new reason to reply; graded against
the first-contact standard it would lose marks for doing exactly the right
thing, so the grader is told which touch it is reading. Once a prospect replies,
that thread stops being graded: it is a conversation, and a cold-email standard
measures nothing useful about it. Internal mail and automated messages are
counted and shown, never scored.

**Your standard is the only standard.** Everything is graded against `rubric.md`
— your document, not the model's opinion of good cold email. If a rule is not in
that file, nobody is marked down for breaking it. The more specific each line,
the more consistent the grades: "be personalised" grades erratically, "the first
sentence must refer to something specific about the recipient's company" grades
the same way every time.

---

## Where it reads from

Two modes. **Archive** (the default) reads one mailbox that every producer BCCs.
**Per-mailbox** opens each producer's own Sent folder.

Archive is better on every axis: one account to authorise instead of the whole
domain, it is a copy so nothing here can touch anybody's real mail, and — the
part that actually changes the output — the whole thread is visible, so a
follow-up can be told apart from a first contact and from a genuine reply.

It brings one failure mode a Sent folder does not have. If a producer's BCC rule
was never set up, "no emails from Maria" and "Maria sent nothing" look identical
in the data, and only one of those is Maria's fault. So the report says out loud
when a producer's mail never arrived, rather than quietly showing them a zero.
`check.py` samples the archive and prints who it can actually see.

**Check the archive address character by character.** A wrong one does not
raise an error anywhere — it reads an empty mailbox and reports nothing, every
day, forever. `check.py` warns if its domain does not match the producers'.

---

## How the score is built

Two halves, always shown separately, because they are different problems with
different fixes.

**Technical (fixed rules).** Spam-filter risk, formatting and CAN-SPAM. These
are rules, not judgements — the same email always produces the same result, and
every one points at specific text. Among them: subject length and shouting,
spam-trigger phrases, exclamation runs, link count, link shorteners, image-only
bodies, attachments on a first contact, unfilled mail-merge tags, missing opt-out,
missing postal address, and fake `Re:` subjects.

**Writing (Claude, against your standard).** Whether the email opens on the
recipient rather than on you, whether the offer is concrete, whether there is a
single clear ask. It is asked about the writing only — it never sees or affects
the technical score, so a bad day from the model cannot invent a compliance
violation against somebody.

The headline number is the average of the two. If the model call fails on an
email, the report shows the technical half and says so rather than inventing a
number.

---

## What each report contains

**Yours**: a table of every producer with overall, technical and writing scores
and the direction of travel; a compliance section listing anything that could
actually cost money; and recurring habits — faults that show up in more than one
email, which is the difference between something worth a conversation and a
one-off.

**Theirs**: their own score, the one habit most worth changing, then each email
lowest-scoring first, with what to fix and often a stronger version of the
opening. No producer's report contains anything about any other producer.
There is a test that specifically checks that.

---

## What it does not do

It cannot delete, modify, archive, or mark as read — those permissions are never
requested, so they cannot happen by accident. It does not store message bodies:
the database keeps the score, the findings and the subject line, which is enough
for the reports without keeping a second copy of everyone's outbound mail on a
server. It never grades a reply as though it were a cold email unless you ask it
to.

---

## Running costs

Claude is charged per email graded, on your own key. Your standard is cached
between calls, so it is paid for once a day rather than once per email. For a
team sending a few hundred cold emails a day, expect a few dollars a month;
`--no-model` runs the technical half alone for nothing.

---

## If something breaks

`python3 check.py` tests every part separately — libraries, settings, the
standard, Claude, each mailbox, and the database — and says which one is unhappy
in plain English. The rule engine is tested even when nothing is configured, so
you can always tell a setup problem from a code problem.

---

## Tested

- 24 checks on the archive logic — touch numbering out of date order, two
  producers working one account without inflating each other, one producer
  writing to two people without the second looking like a follow-up, and
  detecting when the prospect wrote back. Three deliberate mutations of that
  logic were confirmed to fail the suite, so the tests are not decorative.
- 87 checks on the rule engine, most of them on false positives: acronyms are not
  shouting, a real `Re:` is not a fake one, one exclamation mark is fine, a logo
  in a signature is not an image-only email, and every opt-out wording the tool
  itself recommends is recognised by the tool. A rule that fires when it should
  not is not a cosmetic bug here — it tells an employee they did something wrong
  when they did not.
- 68 checks on the pipeline: filtering, scoring, storage and both reports,
  including that one producer's report cannot leak another's, that a subject
  line containing HTML is escaped rather than rendered, and that message bodies
  never reach the database.

```
python3 tests/test_checks.py
python3 tests/test_pipeline.py
python3 tests/test_archive.py
```
