# Funnded UW — Automated Underwriting Analysis

Watches the underwriting inbox for new MCA deal submissions, breaks down the
attached bank statements with Claude, and replies to the email with a UW
analysis in the company's standard format — the same workflow the underwriting
team runs manually today.

## How it works

```
broker/ops email w/ statements ──> monitored inbox (IMAP)
                                        │  polled every 60s
                                        ▼
                          extract PDF/image attachments (zips too)
                                        │
                                        ▼
                     Claude (claude-opus-5) + prompts/uw_system.md
                     + config/flags.md (your decline rules & flags)
                                        │
                                        ▼
                     reply on the same email thread with the analysis
```

- Emails **without** PDF/image attachments are skipped (they're not deals).
- Every processed email is tracked in SQLite (`data/processed.db`), so restarts
  never double-reply to a deal.
- Transient failures retry up to 3 times; a deal that can't be auto-analyzed
  gets a reply saying it needs manual review, so nothing silently dies.

## Setup

1. **Install** (Python 3.10+):

   ```bash
   pip install -r requirements.txt
   ```

2. **Configure** — copy `.env.example` to `.env` and fill in:
   - `ANTHROPIC_API_KEY` — from console.anthropic.com
   - `EMAIL_ADDRESS` / `EMAIL_PASSWORD` — the monitored UW mailbox.
     Gmail/Google Workspace: create an **App Password** (Google Account →
     Security → 2-Step Verification → App passwords) — your normal password
     will not work. Microsoft 365: use `outlook.office365.com` /
     `smtp.office365.com` port 587 and ensure IMAP/SMTP AUTH are enabled.

3. **Put your rules in `config/flags.md`** — decline rules and the flags the
   team uses. This file is injected into the underwriting prompt on every deal
   and can be edited any time without restarting.

## Test before going live

Analyze local statement files with no email involved:

```bash
python -m funnded_uw analyze deal1/june.pdf deal1/july.pdf deal1/aug.pdf
```

Then run against the real inbox in **dry-run mode** (`DRY_RUN=true` in `.env`,
the default): it analyzes new emails and prints what it *would* reply, without
sending anything:

```bash
python -m funnded_uw once     # process current unread emails, then exit
```

When the output looks right, set `DRY_RUN=false` and run the service:

```bash
python -m funnded_uw run      # poll forever
```

## Deploying

The service must run 24/7 somewhere (a small VPS is plenty). Docker:

```bash
docker build -t funnded-uw .
docker run -d --restart unless-stopped --env-file .env \
  -v $(pwd)/data:/app/data funnded-uw
```

Or as a systemd service running `python -m funnded_uw run`.

## Notes & current limits

- **Only unread emails are processed.** Going live won't touch the existing
  read backlog; mark anything as unread to (re)process it.
- **NYSCEF / DataMerch are not searched yet** — the analysis prints
  `NYSCEF - NOT CHECKED` rather than falsely claiming "clear". Court search
  integration is planned once the search URL/account is provided.
- **Cost**: a typical 3-month statement package runs roughly $1–3 per deal on
  claude-opus-5.
- The prompt treats email bodies/attachments as untrusted data and flags
  submissions that try to manipulate the analysis or look like edited PDFs.
