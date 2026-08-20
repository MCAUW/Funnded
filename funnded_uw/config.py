import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
SYSTEM_PROMPT_PATH = ROOT / "prompts" / "uw_system.md"
FLAGS_PATH = ROOT / "config" / "flags.md"


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Config:
    anthropic_api_key: str
    email_address: str
    email_password: str
    imap_host: str
    imap_port: int
    smtp_host: str
    smtp_port: int
    imap_folder: str
    poll_seconds: int
    subject_filter: str
    dry_run: bool
    model: str
    db_path: Path

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
            email_address=os.environ.get("EMAIL_ADDRESS", ""),
            email_password=os.environ.get("EMAIL_PASSWORD", ""),
            imap_host=os.environ.get("IMAP_HOST", "imap.gmail.com"),
            imap_port=int(os.environ.get("IMAP_PORT", "993")),
            smtp_host=os.environ.get("SMTP_HOST", "smtp.gmail.com"),
            smtp_port=int(os.environ.get("SMTP_PORT", "465")),
            imap_folder=os.environ.get("IMAP_FOLDER", "INBOX"),
            poll_seconds=int(os.environ.get("POLL_SECONDS", "60")),
            subject_filter=os.environ.get("SUBJECT_FILTER", "").strip(),
            dry_run=_bool("DRY_RUN", True),
            model=os.environ.get("CLAUDE_MODEL", "claude-opus-5"),
            db_path=Path(os.environ.get("DB_PATH", str(ROOT / "data" / "processed.db"))),
        )

    def require_email(self) -> None:
        missing = [
            name
            for name, value in (
                ("EMAIL_ADDRESS", self.email_address),
                ("EMAIL_PASSWORD", self.email_password),
            )
            if not value
        ]
        if missing:
            raise SystemExit(f"Missing required settings: {', '.join(missing)} (see .env.example)")


def build_system_prompt() -> str:
    """Underwriting instructions + the editable decline-rules/flags file."""
    system = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
    if FLAGS_PATH.exists():
        system += "\n\n" + FLAGS_PATH.read_text(encoding="utf-8")
    return system
