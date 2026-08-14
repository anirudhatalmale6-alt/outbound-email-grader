# Giving the grader access to your Workspace mail

About fifteen minutes, once. You need to be a Google Workspace **super admin**.

Read the warning at the bottom before you start. This is the most powerful
access Google hands out and it is worth understanding what you are switching on.

---

## 1. Make a project and a service account

1. Go to **console.cloud.google.com** and sign in as the admin.
2. Top left, the project dropdown → **New Project**. Call it something like
   `email-grader`. Create it, then make sure it is the selected project.
3. **APIs & Services → Library** → search **Gmail API** → **Enable**.
4. **APIs & Services → Credentials → Create credentials → Service account**.
   - Name: `email-grader`
   - Skip the optional "grant access" steps. Click **Done**.
5. Click the service account you just made → **Keys** tab → **Add key → Create
   new key → JSON** → Create.
6. A `.json` file downloads. Rename it to `service-account.json` and put it in
   the same folder as `run_daily.py`.

## 2. Copy the client ID

Still on the service account page, on the **Details** tab, copy the
**Unique ID** — a long number. You need it in the next step.

(If you would rather not go back and look: run `python3 check.py` and it prints
the client ID out of the key file for you.)

## 3. Authorise it in the Admin console

1. Go to **admin.google.com**.
2. **Security → Access and data control → API controls**.
3. At the bottom, **Manage domain-wide delegation** → **Add new**.
4. **Client ID**: the number from step 2.
5. **OAuth scopes**: paste this exactly, both on one line, comma separated:

```
https://www.googleapis.com/auth/gmail.readonly,https://www.googleapis.com/auth/gmail.send
```

6. **Authorise**.

Give it a couple of minutes to take effect. Google is sometimes slow here — if
step 4 below fails on the first try, wait five minutes and run it again before
assuming something is wrong.

## 4. Check it worked

```
python3 check.py
```

Under **Google Workspace** you should see a line for the admin address and one
for each producer. If they are all `[ ok ]`, you are done.

---

## When it does not work

**"unauthorized_client"** — the client ID is not authorised for those scopes.
Almost always one of: the wrong ID was pasted, a scope has a typo, or you are
still inside the couple of minutes it takes to propagate. Delete the entry in
the Admin console and add it again, pasting the scopes from this file rather
than typing them.

**"invalid_grant"** — the service account cannot act as that address. Check the
address exists, is not suspended, and is in the same Workspace domain.

**"Gmail API has not been used in project..."** — step 1.3 was missed. Enable
the Gmail API and wait a minute.

**Everything works for the admin but not for one producer** — that address is
probably a group or an alias rather than a real mailbox. Only real user
accounts have a Sent folder.

---

## What you are actually switching on

Domain-wide delegation means the service account can act as any user in the
domain, without their consent and without any prompt. It is how server software
is meant to read Workspace mail, and it is genuinely a lot of power.

Two things limit it, and it is worth knowing which is which:

**The scopes limit what it can do, and Google enforces that.** With the two
scopes above, anything holding this key can read mail and send mail. It cannot
delete, modify, archive, or mark anything as read, because the scopes for those
were never granted. That limit is enforced on Google's side, so it holds even if
the software asks for something else.

**The producer list limits whose mail is read, and only this software enforces
that.** The key itself can reach every mailbox in the domain. Nothing outside
`config.yaml` stops it. So the key file matters:

- Keep `service-account.json` in the application folder and nowhere else. Not in
  Drive, not in email, not in a repository.
- If it ever leaks, delete that key in the Cloud console (**Service account →
  Keys → delete**). That revokes it immediately. Then create a new one.
- Only super admins can grant delegation, so anyone able to add scopes here can
  already read the mail anyway — the risk is the key file getting out, not the
  console.

If reading every producer's Sent folder is more access than you want to grant,
there is a narrower option: have each producer set up a filter that copies their
outbound mail to one shared mailbox, and point the grader at that instead. It is
more work for them and easier to switch off quietly, but the key then only opens
one mailbox. Say the word and I will set it up that way.
