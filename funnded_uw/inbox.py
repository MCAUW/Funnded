"""IMAP inbox watcher: fetches unseen deal submissions and their attachments."""

import email
import email.policy
import imaplib
import io
import zipfile
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import parseaddr

PDF_MIME = "application/pdf"
IMAGE_MIMES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
ZIP_MIMES = {"application/zip", "application/x-zip-compressed"}

MAX_ZIP_MEMBERS = 50
MAX_MEMBER_BYTES = 30 * 1024 * 1024


@dataclass
class Attachment:
    filename: str
    mime: str
    data: bytes


@dataclass
class Submission:
    uid: bytes
    message_id: str
    subject: str
    from_addr: str
    reply_addr: str
    references: str
    body_text: str
    attachments: list = field(default_factory=list)


class Inbox:
    def __init__(self, cfg):
        self.cfg = cfg
        self.conn = None

    def __enter__(self):
        self._connect()
        return self

    def _connect(self):
        self.conn = imaplib.IMAP4_SSL(self.cfg.imap_host, self.cfg.imap_port)
        self.conn.login(self.cfg.email_address, self.cfg.email_password)
        status, _ = self.conn.select(self.cfg.imap_folder)
        if status != "OK":
            raise RuntimeError(f"Could not open IMAP folder {self.cfg.imap_folder!r}")

    def _reconnect(self):
        try:
            self.conn.logout()
        except Exception:
            pass
        self._connect()

    def __exit__(self, *exc):
        try:
            self.conn.close()
            self.conn.logout()
        except Exception:
            pass

    def fetch_unseen(self):
        status, data = self.conn.uid("search", None, "UNSEEN")
        if status != "OK":
            raise RuntimeError("IMAP search failed")
        uids = data[0].split()
        submissions = []
        for uid in uids:
            # BODY.PEEK keeps the message unseen until we explicitly mark it,
            # so a crash mid-processing means the deal is retried next poll.
            status, msg_data = self.conn.uid("fetch", uid, "(BODY.PEEK[])")
            if status != "OK" or not msg_data or msg_data[0] is None:
                continue
            raw = msg_data[0][1]
            submissions.append(parse_submission(uid, raw))
        return submissions

    def mark_seen(self, uid: bytes) -> None:
        # A long analysis can outlive the IMAP socket (the server hangs up on
        # idle connections); reconnect once and retry rather than crash.
        try:
            self.conn.uid("store", uid, "+FLAGS", "(\\Seen)")
        except (imaplib.IMAP4.abort, OSError):
            self._reconnect()
            self.conn.uid("store", uid, "+FLAGS", "(\\Seen)")


def parse_submission(uid: bytes, raw: bytes) -> Submission:
    msg: EmailMessage = email.message_from_bytes(raw, policy=email.policy.default)
    from_addr = parseaddr(msg.get("From", ""))[1]
    reply_addr = parseaddr(msg.get("Reply-To", ""))[1] or from_addr
    message_id = (msg.get("Message-ID") or "").strip()
    references = (msg.get("References") or "").strip()

    body_text = ""
    body_part = msg.get_body(preferencelist=("plain",))
    if body_part is not None:
        try:
            body_text = body_part.get_content()
        except Exception:
            body_text = ""

    attachments = []
    for part in msg.walk():
        if part.is_multipart():
            continue
        filename = part.get_filename() or ""
        mime = part.get_content_type()
        is_attachment = part.get_content_disposition() == "attachment" or bool(filename)
        if not is_attachment:
            continue
        try:
            payload = part.get_payload(decode=True)
        except Exception:
            continue
        if not payload:
            continue
        if mime == PDF_MIME or filename.lower().endswith(".pdf"):
            attachments.append(Attachment(filename or "statement.pdf", PDF_MIME, payload))
        elif mime in IMAGE_MIMES:
            attachments.append(Attachment(filename or "attachment", mime, payload))
        elif mime in ZIP_MIMES or filename.lower().endswith(".zip"):
            attachments.extend(extract_zip(payload))

    return Submission(
        uid=uid,
        message_id=message_id,
        subject=msg.get("Subject", "") or "",
        from_addr=from_addr,
        reply_addr=reply_addr,
        references=references,
        body_text=body_text or "",
        attachments=attachments,
    )


def extract_zip(payload: bytes):
    out = []
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as zf:
            for info in zf.infolist()[:MAX_ZIP_MEMBERS]:
                name = info.filename
                if info.is_dir() or info.file_size > MAX_MEMBER_BYTES:
                    continue
                lower = name.lower()
                if lower.endswith(".pdf"):
                    mime = PDF_MIME
                elif lower.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")):
                    ext = lower.rsplit(".", 1)[1]
                    mime = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}"
                else:
                    continue
                out.append(Attachment(name.rsplit("/", 1)[-1], mime, zf.read(info)))
    except zipfile.BadZipFile:
        pass
    return out
