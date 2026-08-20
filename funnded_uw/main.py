import argparse
import logging
import sys
import time
from pathlib import Path

from anthropic import Anthropic

from .analyzer import UnprocessableSubmission, analyze
from .config import Config
from .inbox import Attachment, Inbox, Submission
from .mailer import send_reply
from .store import Store

log = logging.getLogger("funnded_uw")

MAX_ATTEMPTS = 3

MANUAL_REVIEW_NOTE = (
    "This deal could not be auto-analyzed and needs manual underwriting.\n"
    "Reason: {reason}\n"
)


def process_submission(cfg, client, store, inbox, sub):
    label = sub.subject or sub.message_id or "(no subject)"

    if sub.message_id and store.is_finished(sub.message_id):
        inbox.mark_seen(sub.uid)
        return

    if cfg.subject_filter and cfg.subject_filter.lower() not in sub.subject.lower():
        log.info("Skipping (subject filter): %s", label)
        store.record(sub.message_id, "skipped", sub.subject)
        inbox.mark_seen(sub.uid)
        return

    if not sub.attachments:
        log.info("Skipping (no statement attachments): %s", label)
        store.record(sub.message_id, "skipped", sub.subject, "no PDF/image attachments")
        inbox.mark_seen(sub.uid)
        return

    log.info("Analyzing deal: %s (%d attachments)", label, len(sub.attachments))
    try:
        analysis = analyze(client, cfg, sub)
    except UnprocessableSubmission as exc:
        log.warning("Needs manual review: %s — %s", label, exc)
        deliver(cfg, sub, MANUAL_REVIEW_NOTE.format(reason=exc))
        store.record(sub.message_id, "failed", sub.subject, str(exc))
        inbox.mark_seen(sub.uid)
        return
    except Exception as exc:
        attempts = store.bump_attempts(sub.message_id, sub.subject, str(exc))
        if attempts >= MAX_ATTEMPTS:
            log.error("Giving up after %d attempts: %s — %s", attempts, label, exc)
            deliver(cfg, sub, MANUAL_REVIEW_NOTE.format(reason=f"repeated errors ({exc})"))
            store.record(sub.message_id, "failed", sub.subject, str(exc))
            inbox.mark_seen(sub.uid)
        else:
            # Leave unseen so the next poll retries it.
            log.warning("Attempt %d/%d failed, will retry: %s — %s", attempts, MAX_ATTEMPTS, label, exc)
        return

    deliver(cfg, sub, analysis)
    store.record(sub.message_id, "done", sub.subject)
    inbox.mark_seen(sub.uid)
    log.info("Replied with UW analysis: %s", label)


def deliver(cfg, sub, body: str) -> None:
    if cfg.dry_run:
        print(f"\n===== DRY RUN — would reply to {sub.reply_addr} re: {sub.subject!r} =====")
        print(body)
        print("===== END DRY RUN =====\n")
    else:
        send_reply(cfg, sub, body)


def poll_once(cfg, client, store) -> None:
    with Inbox(cfg) as inbox:
        for sub in inbox.fetch_unseen():
            process_submission(cfg, client, store, inbox, sub)


def cmd_run(cfg, once: bool) -> None:
    cfg.require_email()
    client = Anthropic(api_key=cfg.anthropic_api_key or None)
    store = Store(cfg.db_path)
    if cfg.dry_run:
        log.warning("DRY_RUN is on — analyses will be printed, not emailed. Set DRY_RUN=false to go live.")
    while True:
        try:
            poll_once(cfg, client, store)
        except Exception:
            log.exception("Poll failed; retrying in %ds", cfg.poll_seconds)
        if once:
            return
        time.sleep(cfg.poll_seconds)


def cmd_analyze(cfg, files) -> None:
    """Dry-run a set of local statement files without touching email."""
    client = Anthropic(api_key=cfg.anthropic_api_key or None)
    attachments = []
    for f in files:
        path = Path(f)
        lower = path.name.lower()
        if lower.endswith(".pdf"):
            mime = "application/pdf"
        elif lower.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")):
            ext = lower.rsplit(".", 1)[1]
            mime = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}"
        else:
            raise SystemExit(f"Unsupported file type: {path}")
        attachments.append(Attachment(path.name, mime, path.read_bytes()))

    sub = Submission(
        uid=b"local",
        message_id="<local-test>",
        subject="Local test submission",
        from_addr="local@test",
        reply_addr="local@test",
        references="",
        body_text="",
        attachments=attachments,
    )
    print(analyze(client, cfg, sub))


def main(argv=None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(prog="funnded_uw", description="Automated MCA underwriting analysis")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help="watch the inbox continuously and reply to new deals")
    sub.add_parser("once", help="process the inbox once and exit")
    p_an = sub.add_parser("analyze", help="analyze local statement files (no email involved)")
    p_an.add_argument("files", nargs="+", help="bank statement PDFs/images")

    args = parser.parse_args(argv)
    cfg = Config.from_env()

    if args.command == "run":
        cmd_run(cfg, once=False)
    elif args.command == "once":
        cmd_run(cfg, once=True)
    elif args.command == "analyze":
        cmd_analyze(cfg, args.files)


if __name__ == "__main__":
    main()
