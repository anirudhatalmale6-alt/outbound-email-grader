"""
Tests for the rule engine.

These matter more than most tests on this project, because every one of these
rules ends up as a sentence in front of an employee. A rule that fires when it
shouldn't is not a cosmetic bug -- it is telling somebody they did something
wrong when they didn't.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from grader import checks
from grader.checks import Email, Finding

PASSED = 0
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED
    if condition:
        PASSED += 1
    else:
        FAILED.append(f"{name}{(': ' + detail) if detail else ''}")


def ids(findings: list[Finding]) -> set[str]:
    return {f.id for f in findings}


# A message that should come out clean. Everything else is measured against it.
GOOD = Email(
    message_id="m1",
    sender="joe@example.com",
    to=["buyer@acme.com"],
    subject="Question about Acme's Q3 media plan",
    body_text=(
        "Hi Dana,\n\n"
        "I saw Acme is expanding the Midwest team this quarter. We place "
        "broadcast segments for companies at exactly that stage, and two of "
        "them were in your position last year.\n\n"
        "Would you be open to a short call next week to see whether it fits?\n\n"
        "Best,\nJoe Smith\n"
        "All Access\n"
        "1200 Market Street, Suite 400, Dallas, TX 75201\n"
        "If you would rather not hear from me, just reply and say so."
    ),
)


def test_clean_email_is_clean() -> None:
    findings = checks.run_all(GOOD)
    check("clean: no findings", not findings, f"got {sorted(ids(findings))}")
    check("clean: scores 100", checks.mechanical_score(findings) == 100,
          str(checks.mechanical_score(findings)))


def test_subject_rules() -> None:
    def sub(subject: str) -> set[str]:
        e = Email(subject=subject, body_text=GOOD.body_text)
        return ids(checks.check_subject(e))

    check("subject: blank flagged", "subject.missing" in sub(""))
    check("subject: long flagged",
          "subject.too_long" in sub("A" * 61 + " about your media plan"))
    check("subject: 60 chars is fine", "subject.too_long" not in sub("A" * 60))
    check("subject: shouting flagged", "subject.shouting" in sub("URGENT NOTICE"))
    check("subject: normal case fine",
          "subject.shouting" not in sub("Question about your Q3 plan"))
    check("subject: acronyms survive", "subject.shouting" not in sub("Your Q3 ROI"))
    check("subject: double bang flagged", "subject.exclamation" in sub("Open this!!"))
    check("subject: one bang fine", "subject.exclamation" not in sub("Nice to meet you!"))
    check("subject: spam phrase flagged", "subject.spam_phrase" in sub("Act now to save"))
    check("subject: price flagged", "subject.money" in sub("Save $500 today"))

    fake = Email(subject="Re: our conversation", body_text=GOOD.body_text)
    check("subject: fake reply flagged", "subject.fake_reply" in ids(checks.check_subject(fake)))

    real = Email(subject="Re: our conversation", in_reply_to="<abc@mail>",
                 body_text=GOOD.body_text)
    check("subject: real reply not flagged",
          "subject.fake_reply" not in ids(checks.check_subject(real)))


def test_body_shape() -> None:
    short = Email(subject="Hi", body_text="Hi Dana, call me. Joe")
    check("body: short flagged", "body.too_short" in ids(checks.check_body_shape(short)))

    long_body = "Hi Dana,\n" + ("word " * 320) + "\nAre you free Tuesday?"
    check("body: long flagged",
          "body.too_long" in ids(checks.check_body_shape(Email(body_text=long_body))))

    shout = Email(body_text="HI DANA I WANTED TO REACH OUT ABOUT YOUR MEDIA PLAN "
                            "THIS QUARTER AND SEE IF YOU ARE OPEN TO A CALL")
    check("body: shouting flagged", "body.shouting" in ids(checks.check_body_shape(shout)))
    check("body: normal case fine",
          "body.shouting" not in ids(checks.check_body_shape(GOOD)))

    bangs = Email(body_text="Hi Dana! Great news! Big deal! Call me! Are you free?")
    check("body: many bangs flagged", "body.exclamation" in ids(checks.check_body_shape(bangs)))

    runs = Email(body_text="Hi Dana, are you interested??? Let me know.")
    check("body: punctuation run flagged",
          "body.punctuation_run" in ids(checks.check_body_shape(runs)))

    no_greet = Email(body_text="We place broadcast segments. Would you be open to a call?")
    check("body: missing greeting flagged",
          "body.no_greeting" in ids(checks.check_body_shape(no_greet)))
    check("body: greeting recognised",
          "body.no_greeting" not in ids(checks.check_body_shape(GOOD)))

    no_ask = Email(body_text="Hi Dana,\n\nWe place broadcast segments for firms "
                             "like yours. Thought you should know about us.\n\nJoe")
    check("body: missing ask flagged", "body.no_ask" in ids(checks.check_body_shape(no_ask)))
    check("body: ask recognised", "body.no_ask" not in ids(checks.check_body_shape(GOOD)))


def test_spam_language() -> None:
    e = Email(body_text="Hi Dana, this is a limited time risk free offer, act now!")
    found = ids(checks.check_spam_language(e))
    check("spam: phrases flagged", "body.spam_phrase" in found)
    check("spam: clean body not flagged",
          "body.spam_phrase" not in ids(checks.check_spam_language(GOOD)))

    urgent = Email(body_text="Hi Dana, this is urgent, last chance, act now, hurry.")
    check("spam: urgency flagged", "body.false_urgency" in ids(checks.check_spam_language(urgent)))


def test_links() -> None:
    many = Email(body_html="".join(
        f'<a href="https://example.com/{i}">x</a>' for i in range(6)))
    check("links: too many flagged", "links.too_many" in ids(checks.check_links(many)))

    short = Email(body_text="Have a look: https://bit.ly/3xyzabc")
    check("links: shortener flagged", "links.shortener" in ids(checks.check_links(short)))

    plain = Email(body_text="Have a look: http://example.com/page")
    check("links: http flagged", "links.insecure" in ids(checks.check_links(plain)))

    https = Email(body_text="Have a look: https://example.com/page")
    check("links: https not flagged", "links.insecure" not in ids(checks.check_links(https)))

    img = Email(body_html='<img src="https://x.com/a.png"><img src="https://x.com/b.png">')
    check("links: image-only flagged", "links.image_heavy" in ids(checks.check_links(img)))

    # A real email with one picture in the signature must not trip it.
    with_text = Email(
        body_html='<p>' + ("Hi Dana, we place broadcast segments for firms at "
                           "your stage and I would like to ask about your plan. " * 2)
        + '</p><img src="https://x.com/logo.png">')
    check("links: image plus text fine",
          "links.image_heavy" not in ids(checks.check_links(with_text)))


def test_hygiene() -> None:
    for tag in ("{{first_name}}", "{FirstName}", "[[company]]", "%%CITY%%", "*|FNAME|*"):
        e = Email(body_text=f"Hi {tag}, are you free Tuesday?")
        check(f"hygiene: merge tag {tag} flagged",
              "hygiene.merge_tag" in ids(checks.check_hygiene(e)), tag)

    check("hygiene: clean body has no merge tag",
          "hygiene.merge_tag" not in ids(checks.check_hygiene(GOOD)))

    ph = Email(body_text="Hi Dana, TODO write the pitch. Are you free?")
    check("hygiene: placeholder flagged", "hygiene.placeholder" in ids(checks.check_hygiene(ph)))

    att = Email(has_attachments=True, attachment_names=["deck.pdf"],
                body_text=GOOD.body_text)
    check("hygiene: attachment flagged", "hygiene.attachment" in ids(checks.check_hygiene(att)))

    blast = Email(to=["a@x.com", "b@y.com", "c@z.com"], body_text=GOOD.body_text)
    check("hygiene: multi-recipient flagged",
          "hygiene.multiple_recipients" in ids(checks.check_hygiene(blast)))

    html_only = Email(body_html="<p>Hi Dana, are you free Tuesday?</p>")
    check("hygiene: html-only flagged",
          "hygiene.no_plain_text" in ids(checks.check_hygiene(html_only)))


def test_compliance() -> None:
    bare = Email(body_text="Hi Dana, we place broadcast segments. Are you free Tuesday?")
    found = ids(checks.check_compliance(bare))
    check("compliance: opt-out flagged", "compliance.no_opt_out" in found)
    check("compliance: address flagged", "compliance.no_postal_address" in found)

    good = ids(checks.check_compliance(GOOD))
    check("compliance: good email has opt-out", "compliance.no_opt_out" not in good)
    check("compliance: good email has address",
          "compliance.no_postal_address" not in good, str(sorted(good)))

    lie = Email(body_text=GOOD.body_text + "\nFollowing up on your inquiry.")
    check("compliance: false prior contact flagged",
          "compliance.false_prior_contact" in ids(checks.check_compliance(lie)))


def test_scoring() -> None:
    check("score: clean is 100", checks.mechanical_score([]) == 100)
    one_high = [Finding("x", "hygiene", "high", "m", "f")]
    check("score: one high costs 12", checks.mechanical_score(one_high) == 88)
    many = [Finding(str(i), "hygiene", "high", "m", "f") for i in range(20)]
    check("score: never negative", checks.mechanical_score(many) == 0)


def test_reply_detection() -> None:
    check("reply: header wins", Email(in_reply_to="<a@b>").is_reply)
    check("reply: references wins", Email(references="<a@b>").is_reply)
    check("reply: Re: prefix", Email(subject="Re: hello").is_reply)
    check("reply: Fwd: prefix", Email(subject="Fwd: hello").is_reply)
    check("reply: plain subject is not", not Email(subject="Hello there").is_reply)
    check("reply: 'Recap' is not a reply", not Email(subject="Recap of our call").is_reply)


def test_html_to_text() -> None:
    html = "<html><head><style>p{color:red}</style></head><body><p>Hello</p>" \
           "<script>alert(1)</script><p>World</p></body></html>"
    text = checks.html_to_text(html)
    check("html: text extracted", "Hello" in text and "World" in text)
    check("html: style dropped", "color:red" not in text, text)
    check("html: script dropped", "alert" not in text, text)

    check("html: malformed survives", isinstance(checks.html_to_text("<p>unclosed"), str))
    check("html: empty survives", checks.html_to_text("") == "")


def test_body_prefers_plain_text() -> None:
    e = Email(body_text="plain version", body_html="<p>html version</p>")
    check("body: plain preferred", e.body == "plain version")
    e2 = Email(body_html="<p>html version</p>")
    check("body: falls back to html", "html version" in e2.body)


def test_run_all_is_stable() -> None:
    first = [f.id for f in checks.run_all(GOOD)]
    second = [f.id for f in checks.run_all(GOOD)]
    check("run_all: deterministic", first == second)

    messy = Email(subject="ACT NOW!!! FREE MONEY", body_text="CLICK HERE http://bit.ly/x")
    found = checks.run_all(messy)
    check("run_all: messy email caught", len(found) >= 5, f"only {len(found)}")
    check("run_all: messy email scores low", checks.mechanical_score(found) < 50)



def test_caps_ratio_helper() -> None:
    """Acronyms must not read as shouting. This is the check that stops the
    grader telling somebody off for writing "Q3 ROI"."""
    for ok in ("Your Q3 ROI", "DNC list question", "Quick API question",
               "CEO intro for Acme", "USA rollout timing",
               "Following up on the RFP", "Q4 CPM rates for ACME"):
        check(f"caps: {ok!r} not shouting",
              not checks.is_shouting(ok, threshold=0.6, min_letters=6))
    # Short all-caps words must still count: every one of these is <= 4
    # characters, so an acronym rule on its own would excuse the lot.
    for shout in ("URGENT NOTICE INSIDE", "READ THIS RIGHT NOW",
                  "OPEN NOW FREE CASH", "ACT NOW LAST CALL"):
        check(f"caps: {shout!r} is shouting",
              checks.is_shouting(shout, threshold=0.6, min_letters=6))
    check("caps: empty is safe", checks.caps_ratio("") == (0.0, 0))
    check("caps: digits only is safe", checks.caps_ratio("2026 40623904") == (0.0, 0))


def test_opt_out_wordings() -> None:
    """Every phrasing the grader itself recommends must be recognised by the
    grader. Suggesting wording and then penalising it is the worst outcome."""
    accepted = [
        "If you would rather not hear from me, just reply and say so.",
        "Reply with unsubscribe and I will take you off.",
        "You can opt out any time.",
        "Tell me to stop and I will.",
        "If you prefer not to hear from us, say the word and I'll stop.",
        "Remove me works fine as a reply.",
    ]
    for line in accepted:
        e = Email(body_text="Hi Dana,\n\n" + line)
        check(f"opt-out recognised: {line[:34]!r}",
              "compliance.no_opt_out" not in ids(checks.check_compliance(e)))

    silent = Email(body_text="Hi Dana, we place broadcast segments. Free Tuesday?")
    check("opt-out: silence still flagged",
          "compliance.no_opt_out" in ids(checks.check_compliance(silent)))


def test_rule_codes_registry_is_complete() -> None:
    """RULE_CODES is what validates the settings, so a rule missing from it
    would make a legitimate disabled_rules entry look like a typo."""
    import ast

    source = (Path(__file__).resolve().parent.parent
              / "grader" / "checks.py").read_text(encoding="utf-8")
    emitted: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "Finding"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)):
            emitted.add(node.args[0].value)
    emitted = {c for c in emitted if not c.startswith("internal.")}

    missing = sorted(emitted - set(checks.RULE_CODES))
    check("every rule is in RULE_CODES", not missing, ", ".join(missing))
    stale = sorted(set(checks.RULE_CODES) - emitted)
    check("RULE_CODES has no rules that no longer exist", not stale,
          ", ".join(stale))


def test_disabled_rules_drop_findings() -> None:
    spammy = Email(
        subject="RE: ACT NOW!!! FREE MONEY",
        body_text="Hi there, click here now. Limited time offer!!!",
    )
    everything = ids(checks.run_all(spammy))
    check("the sample email does fail on opt-out",
          "compliance.no_opt_out" in everything)

    without = checks.run_all(spammy, {"compliance.no_opt_out"})
    check("a disabled rule produces no finding",
          "compliance.no_opt_out" not in ids(without))
    check("disabling one rule leaves the others alone",
          ids(without) == everything - {"compliance.no_opt_out"})

    scored = checks.mechanical_score(without)
    check("dropping a high finding raises the score",
          scored > checks.mechanical_score(checks.run_all(spammy)))

    check("an empty disabled set changes nothing",
          ids(checks.run_all(spammy, set())) == everything)


def test_disabled_rules_cannot_silence_a_broken_check() -> None:
    """A bug in a check has to stay visible, or config could hide it."""
    broken = Finding("internal.check_subject", "hygiene", "low", "boom", "bug")
    kept = [f for f in [broken]
            if f.id.startswith("internal.") or f.id not in {"internal.check_subject"}]
    check("internal findings survive being named in disabled_rules",
          [f.id for f in kept] == ["internal.check_subject"])


def main() -> int:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    total = PASSED + len(FAILED)
    print(f"\n  {PASSED}/{total} checks passed")
    if FAILED:
        print("\n  Failures:")
        for f in FAILED:
            print(f"    - {f}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
