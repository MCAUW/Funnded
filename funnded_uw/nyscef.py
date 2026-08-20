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
                page.locator("[aria-labelledby*='Business' i] input:visible, [aria-labelledby*='Company' i] input:visible"),
                page.locator("input[name*=business i], input[id*=business i]"),
                page.locator("input[name*=org i], input[id*=org i]"),
                page.get_by_role("textbox", name=re.compile(r"business|company|organi[sz]ation", re.I)),
            ],
        )
        if field is None:
            raise NyscefError("could not find the business-name field on the search form")
        field.fill(name)
    else:
        # The person-name fields live inside a grouped panel
        # (<div role="group" aria-labelledby="SearchByPersonNameLabel">) —
        # target the inputs inside it, not the group itself.
        group = page.locator("[aria-labelledby*='PersonName' i], [aria-labelledby*='PartyName' i]")
        filled = False
        if group.count() > 0:
            inputs = group.first.locator("input:visible")
            if inputs.count() >= 1:
                inputs.nth(0).fill(name)
                if first_name and inputs.count() >= 2:
                    inputs.nth(1).fill(first_name)
                filled = True
        if not filled:
            last = _first_visible(
                page,
                [
                    page.locator("input[name*=last i], input[id*=last i]"),
                    page.get_by_role("textbox", name=re.compile(r"last\s*name", re.I)),
                ],
            )
            if last is None:
                raise NyscefError("could not find the last-name field on the search form")
            last.fill(name)
            if first_name:
                first = _first_visible(
                    page,
                    [
                        page.locator("input[name*=first i], input[id*=first i]"),
                        page.get_by_role("textbox", name=re.compile(r"first\s*name", re.I)),
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


NO_RESULTS_RE = re.compile(
    r"no\s+(records|cases|results|matches|documents)|did\s+not\s+(match|return)"
    r"|there\s+are\s+no|\b0\s+(records|results|cases)\b|nothing\s+found",
    re.I,
)


def _collect_rows(page):
    """Best row set on the page: real <table>, ARIA rows, or row-styled divs."""
    best = []
    for table in page.locator("table").all():
        try:
            rows = table.locator("tr").all()
        except Exception:
            continue
        if len(rows) > len(best):
            best = rows
    aria_rows = page.get_by_role("row").all()
    if len(aria_rows) > len(best):
        best = aria_rows
    div_rows = page.locator("div[class*='Row' i]").all()
    if len(div_rows) > len(best):
        best = div_rows
    return best


def _read_results(page) -> str:
    # Results may render asynchronously; poll instead of reading once.
    best_rows = []
    for _ in range(10):
        body_text = page.inner_text("body", timeout=15_000)
        if NO_RESULTS_RE.search(body_text):
            return "CLEAR — no cases found"
        best_rows = _collect_rows(page)
        if len(best_rows) >= 2:
            break
        page.wait_for_timeout(2000)
    if len(best_rows) < 2:
        # No rows and no explicit no-results text: don't guess.
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
        print(f"[debug] saved {debug_dir}/page.html and page.png")
    except Exception:
        pass
    try:
        print("[debug] --- page structure at time of failure ---")
        print("[debug] title:", page.title(), "| url:", page.url)
        for sel in (
            "input", "select", "button", "[role=group]", "[role=row]",
            "table", "div[class*='Row' i]", "iframe",
        ):
            els = page.locator(sel)
            n = els.count()
            print(f"[debug] {sel}: {n}")
            for j in range(min(n, 8)):
                try:
                    html = els.nth(j).evaluate("el => el.outerHTML.slice(0, 180)")
                    print("[debug]    ", " ".join(html.split()))
                except Exception:
                    pass
        print("[debug] --- end page structure ---")
    except Exception:
        pass
