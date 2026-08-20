"""Run directly: python tests/test_inbox.py"""

import io
import sys
import zipfile
from email.message import EmailMessage
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from funnded_uw.inbox import parse_submission

FAKE_PDF = b"%PDF-1.4 fake statement bytes"


def make_email(attach_zip=False):
    msg = EmailMessage()
    msg["From"] = "Ops <ops@company.com>"
    msg["To"] = "uw@company.com"
    msg["Subject"] = "New Deal - Joe's Diner LLC"
    msg["Message-ID"] = "<abc123@company.com>"
    msg.set_content("Statements attached. Requesting $50k.")
    msg.add_attachment(
        FAKE_PDF, maintype="application", subtype="pdf", filename="june.pdf"
    )
    if attach_zip:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("statements/july.pdf", FAKE_PDF)
            zf.writestr("readme.txt", "ignore me")
        msg.add_attachment(
            buf.getvalue(), maintype="application", subtype="zip", filename="more.zip"
        )
    return msg.as_bytes()


def test_basic_parse():
    sub = parse_submission(b"1", make_email())
    assert sub.subject == "New Deal - Joe's Diner LLC"
    assert sub.from_addr == "ops@company.com"
    assert sub.reply_addr == "ops@company.com"
    assert sub.message_id == "<abc123@company.com>"
    assert "Requesting $50k" in sub.body_text
    assert len(sub.attachments) == 1
    assert sub.attachments[0].mime == "application/pdf"
    assert sub.attachments[0].data == FAKE_PDF


def test_zip_extraction():
    sub = parse_submission(b"2", make_email(attach_zip=True))
    names = sorted(a.filename for a in sub.attachments)
    assert names == ["july.pdf", "june.pdf"], names
    assert all(a.mime == "application/pdf" for a in sub.attachments)


if __name__ == "__main__":
    test_basic_parse()
    test_zip_extraction()
    print("all inbox tests passed")
