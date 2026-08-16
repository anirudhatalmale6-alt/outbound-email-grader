# Giving the grader access to your Workspace mail

There are two ways to do this. Read the first paragraph of each and pick one —
you do not need both.

**OAuth (recommended, and the default).** You sign in once, in a browser, as the
archive mailbox. The grader can then read and send as that one account and
nothing else. No key file to leak, no domain-wide permission to grant, and it
works even where your organisation blocks service account keys. About ten
minutes.

**Service account with domain-wide delegation.** No interactive sign-in at all,
which suits a server nobody logs into. The trade is that the key can act as any
user in your Workspace, and only `config.yaml` keeps it pointed at the right
mailbox. About fifteen minutes, and it needs a super admin.

If your organisation has **Secure by Default** switched on you will hit
`iam.disableServiceAccountKeyCreation` when you try to download the key. That is
Google refusing to issue a permanent credential file. Use OAuth rather than
turning that protection off.

---

# Option A — OAuth

## 1. Make a project and turn on the Gmail API

1. Go to **console.cloud.google.com**, signed in as an admin on the domain.
2. Project dropdown, top left → **New Project**. Call it `email-grader`.
   Create it, then **select it** so it is the active project. If the wrong
   project is selected everything below lands somewhere else without complaining.
3. **APIs & Services → Library** → search **Gmail API** → **Enable**.

## 2. Set up the consent screen

Only needed once per project, and only because Google requires it before it will
issue an OAuth client.

1. **APIs & Services → OAuth consent screen**.
2. User type: **Internal**. This matters — internal means only people in your
   own Workspace can ever authorise it, and it skips Google's verification
   review entirely.
3. App name `email-grader`, and your own address for both support and developer
   contact. Save and continue.
4. On the Scopes step, click **Save and Continue** without adding anything. The
   scopes are requested by the application at sign-in time, not listed here.

## 3. Create the OAuth client

1. **APIs & Services → Credentials → Create credentials → OAuth client ID**.
2. Application type: **Desktop app**. Name it `email-grader`.
3. Create, then **Download JSON**.
4. Save that file next to `run_daily.py` as exactly:

```
oauth_client.json
```

## 4. Sign in

On a machine with a browser, in the application folder:

```
python3 authorise.py
```

It opens a browser. **Sign in as the archive mailbox** — the address every
producer BCCs, e.g. `cortex@yourdomain.com` — not as your admin account. Approve
the two permissions: read mail, send mail.

It writes `token.json` and prints which account it signed in as. Check that
line. Signing in as the wrong account succeeds exactly as smoothly as signing in
as the right one, and then reads the wrong mailbox every day without ever
erroring, so `authorise.py` and `check.py` both compare it against your settings
and refuse to continue if they disagree.

**If the machine that runs the grader has no browser** — a server or a VPS — run
`authorise.py` on your laptop and copy `token.json` across. It is the token that
matters, not where it was created.

## 5. Check it

```
python3 check.py
```

You should see the account it is signed in as, the archive mailbox, and a count
of producer messages found in it with the names it can see. That last part
matters more than the `[ ok ]` above it: the sign-in can work perfectly long
before the mailbox actually contains everybody's mail. A producer missing from
that list means their BCC rule is not firing.

## What this grants

Read and send, as one mailbox. It cannot delete, modify, archive or mark
anything as read, because those scopes are never requested and Google enforces
that. It cannot reach any other mailbox at all — not because the software
declines to, but because the token does not cover them.

To revoke it: **myaccount.google.com** on that account → **Security → Your
connections to third-party apps** → remove `email-grader`. Or just delete
`token.json`.

---

# Option B — service account with domain-wide delegation

You need to be a Workspace **super admin**, and your organisation must not be
blocking service account keys.

## 1. Project and service account

1. **console.cloud.google.com** → new project `email-grader` → select it.
2. **APIs & Services → Library** → **Gmail API** → **Enable**.
3. **APIs & Services → Credentials → Create credentials → Service account**.
   Name it `email-grader`. Skip both optional grant steps — it needs no project
   role. **Done**.
4. Click the service account → **Keys** → **Add key → Create new key → JSON**.
5. Rename the downloaded file to `service-account.json`, next to `run_daily.py`.
6. On the **Details** tab, copy the **Unique ID** — a long number.

If step 4 fails with *Service account key creation is disabled*, your
organization policy blocks key files. Use Option A.

## 2. Authorise it in the Admin console

1. **admin.google.com → Security → Access and data control → API controls**.
2. **Manage domain-wide delegation → Add new**.
3. **Client ID**: the Unique ID from step 6.
4. **OAuth scopes**, both on one line, comma separated:

```
https://www.googleapis.com/auth/gmail.readonly,https://www.googleapis.com/auth/gmail.send
```

5. **Authorise.**

Give it a couple of minutes. Google is sometimes slow here — if the check below
fails first time, wait five minutes and try again before changing anything.

## 3. Set `auth: service_account` in config.yaml, then check

```
python3 check.py
```

## What this grants

Domain-wide delegation lets the service account act as **any** user in the
domain, without their consent and without a prompt.

The scopes limit what it can do, and **Google enforces that**: read and send
only, never delete or modify.

The producer list limits whose mail is read, and **only this software enforces
that**. The key itself can reach every mailbox in the domain. So:

- Keep `service-account.json` in the application folder and nowhere else. Not in
  Drive, not in email, not in a repository.
- If it leaks, delete that key in the Cloud console (**Service account → Keys →
  delete**). That revokes it immediately. Then create a new one.

---

# When it does not work

**"Service account key creation is disabled"** — organization policy blocks key
files. Use Option A.

**"Not authorised yet - no token"** — run `python3 authorise.py`.

**"Signed in as X, but the settings read Y"** — you approved the browser prompt
as the wrong Google account. Run `authorise.py` again and pick the other one, or
change `google.archive_mailbox` to match.

**"The stored sign-in could not be refreshed"** — access was revoked, the
password changed, or the token sat unused for six months. Run `authorise.py`.

**"unauthorized_client"** (Option B) — the client ID is not authorised for those
scopes. Usually the wrong ID, a typo in a scope, or you are inside the couple of
minutes it takes to propagate. Delete the Admin console entry and re-add it,
pasting the scopes from this file rather than typing them.

**"invalid_grant"** (Option B) — the service account cannot act as that address.
Check it exists, is not suspended, and is in the same Workspace.

**"Gmail API has not been used in project..."** — the Gmail API was never
enabled. Enable it and wait a minute.

**The archive is reachable but contains nothing** — the BCC rule is not
reaching it. Check one producer's Sent folder against the archive by hand before
reading anything into a report.
