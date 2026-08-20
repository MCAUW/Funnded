"""NYSCEF (NY courts e-filing) case search by party name.

Drives a real headless Chromium through the public search form at
https://iapps.courts.state.ny.us/nyscef/CaseSearch?TAB=name — the site sits
behind Cloudflare, so plain HTTP requests are blocked; a real browser is
required. Locators are semantic (label/role text) rather than hard-coded
field ids, to survive minor page changes.

Standalone test:
    python -m funnded_uw nyscef "MERCHANT LLC"
    python -m funnded_uw nyscef "Smith, John" --individual
    python -m funnded_uw nyscef "MERCHANT LLC" --debug   # dumps HTML+screenshot on failure
"""

import re
from pathlib import Path

SEARCH_URL = "https://iapps.courts.state.ny.us/nyscef/CaseSearch?TAB=name"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
)
MAX_RESULT_ROWS = 25


class NyscefError(Exception):
    """Search could not be completed — report as SEARCH FAILED, never CLEAR."""


def search_party(
    name: str,
    party_type: str = "business",
    first_name: str = "",
    headless: bool = True,
    debug_dir: Path | None = None,
) -> str:
    """Search NYSCEF by party name. Returns human-readable results text.

    party_type "business": `name` is the business/organization name.
    party_type "individual": `name` is the LAST name, `first_name` optional.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise NyscefError("playwright is not installed (pip install playwright && playwright install chromium)") from exc

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless, args=["--no-sandbox"])
        try:
            ctx = browser.new_context(user_agent=UA, viewport={"width": 1280, "height": 900})
            page = ctx.new_page()
            try:
                page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=60_000)
                _wait_out_cloudflare(page)
                _fill_and_submit(page, name, party_type, first_name)
                _wait_out_cloudflare(page)
                return _read_results(page)
            except NyscefError:
                _dump_debug(page, debug_dir)
                raise
            except Exception as exc:
                _dump_debug(page, debug_dir)
                raise NyscefError(f"search failed: {exc}") from exc
        finally:
            browser.close()


def _wait_out_cloudflare(page, timeout_s: int = 45) -> None:
    """Wait for Cloudflare's 'Just a moment...' interstitial to clear."""
    for _ in range(timeout_s):
        title = (page.title() or "").lower()
        if "just a moment" not in title and "attention required" not in title:
            return
        page.wait_for_timeout(1000)
    raise NyscefError("Cloudflare challenge did not clear — the search IP may be blocked")


def _first_visible(page, locators):
    for loc in locators:
        try:
            if loc.count() > 0 and loc.first.is_visible():
                return loc.first
        except Exception:
            continue
    return None


def _fill_and_submit(page, name: str, party_type: str, first_name: str) -> None:
    # Party-type selector (radio buttons or dropdown), when the form has one.
    want = "business" if party_type == "business" else "individual"
    type_choice = _first_visible(
        page,
        [
            page.get_by_label(re.compile(want, re.I)),
            page.locator(f"input[type=radio][value*={want} i]"),
            page.get_by_role("radio", name=re.compile(want, re.I)),
        ],
    )
    if type_choice is not None:
        try:
            type_choice.check()
        except Exception:
            type_choice.click()
        page.wait_for_timeout(500)

    if party_type == "business":
        field = _first_visible(
            page,
            [
                page.get_by_label(re.compile(r"business|company|organi[sz]ation", re.I)),
                page.locator("input[name*=business i], input[id*=business i]"),
                page.locator("input[name*=org i], input[id*=org i]"),
            ],
        )
        if field is None:
            raise NyscefError("could not find the business-name field on the search form")
        field.fill(name)
    else:
        last = _first_visible(
            page,
            [
                page.get_by_label(re.compile(r"last\s*name", re.I)),
                page.locator("input[name*=last i], input[id*=last i]"),
            ],
        )
        if last is None:
            raise NyscefError("could not find the last-name field on the search form")
        last.fill(name)
        if first_name:
            first = _first_visible(
                page,
                [
                    page.get_by_label(re.compile(r"first\s*name", re.I)),
                    page.locator("input[name*=first i], input[id*=first i]"),
                ],
            )
            if first is not None:
                first.fill(first_name)

    if page.locator("iframe[src*='captcha'], iframe[title*='captcha' i]").count() > 0:
        raise NyscefError("the search form is showing a captcha — manual search required")

    submit = _first_visible(
        page,
        [
            page.get_by_role("button", name=re.compile(r"search", re.I)),
            page.locator("input[type=submit][value*=search i]"),
            page.locator("button[type=submit]"),
        ],
    )
    if submit is None:
        raise NyscefError("could not find the search button")
    submit.click()
    page.wait_for_load_state("domcontentloaded", timeout=60_000)
    page.wait_for_timeout(2000)


def _read_results(page) -> str:
    body_text = page.inner_text("body", timeout=15_000)

    if re.search(r"no\s+(records|cases|results|matches)", body_text, re.I):
        return "CLEAR — no cases found"

    # Pull the largest data table on the results page.
    best_rows = []
    for table in page.locator("table").all():
        try:
            rows = table.locator("tr").all()
        except Exception:
            continue
        if len(rows) > len(best_rows):
            best_rows = rows
    if len(best_rows) < 2:
        # No table and no explicit no-results text: don't guess.
        raise NyscefError("could not identify a results table on the results page")

    lines = []
    for row in best_rows[: MAX_RESULT_ROWS + 1]:
        cells = [c.strip() for c in row.inner_text().split("\n") if c.strip()]
        if cells:
            lines.append(" | ".join(cells))
    total = len(best_rows) - 1
    header = f"{total} case(s) found" + (f" (showing first {MAX_RESULT_ROWS})" if total > MAX_RESULT_ROWS else "")
    return header + ":\n" + "\n".join(lines)


def _dump_debug(page, debug_dir: Path | None) -> None:
    if debug_dir is None:
        return
    try:
        debug_dir.mkdir(parents=True, exist_ok=True)
        (debug_dir / "page.html").write_text(page.content(), encoding="utf-8")
        page.screenshot(path=str(debug_dir / "page.png"), full_page=True)
    except Exception:
        pass
