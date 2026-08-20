"""Human-friendly terminal output for the underwriting pipeline."""

import sys

USE_COLOR = sys.stdout.isatty()

RULE = "=" * 66


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if USE_COLOR else text


def header(text: str) -> None:
    print()
    print(_c("1;36", RULE))
    print(_c("1;36", text))
    print(_c("1;36", RULE), flush=True)


def step(text: str) -> None:
    print(_c("36", "  -> ") + text, flush=True)


def ok(text: str) -> None:
    print(_c("1;32", "  [OK] ") + text, flush=True)


def warn(text: str) -> None:
    print(_c("1;33", "  [!] ") + text, flush=True)


def fail(text: str) -> None:
    print(_c("1;31", "  [X] ") + text, flush=True)


def summary(text: str) -> None:
    print()
    print(_c("1", "-" * 66))
    print(_c("1", text), flush=True)
