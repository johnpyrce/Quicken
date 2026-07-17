#!/usr/bin/env python3
"""
Schwab Lot Details Scraper (Safari / Chrome DOM extraction)

Connects to an existing Safari tab or Chrome CDP session that is already logged
in to Schwab, opens the Positions page, opens each holding's Lot Details
overlay, and writes one CSV per security in the same shape expected by
schwab_quicken_rebuild.py.

This intentionally reads the on-screen Lot Details table instead of relying on
Schwab's Export download event. In local testing the browser download event was
not always exposed, while the Lot Details table itself was complete.

Prerequisites:
  Safari:
    1. Enable Safari > Develop > Allow JavaScript from Apple Events.
    2. Log in to Schwab manually in Safari.
    3. Run this script.

  Chrome fallback:
    1. Start Chrome with remote debugging enabled.
    2. Log in to Schwab manually in that Chrome window.
    3. Run this script with --browser chrome-cdp.

Usage:
  python scrape_schwab_lots.py
  python scrape_schwab_lots.py "Inheritance"
  python scrape_schwab_lots.py "Joint Account" --output SchwabLots
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright


CYAN = "\033[96m"
GRAY = "\033[90m"
RED = "\033[91m"
RESET = "\033[0m"

POSITIONS_URL = "https://client.schwab.com/app/accounts/positions/#/"
REQUIRED_LOT_HEADERS = {"Open Date", "Quantity", "Cost Basis"}
DEFAULT_OUTPUT_DIR = Path("SchwabLots")


def log_info(message: str) -> None:
    print(f"{GRAY}{message}{RESET}")


def log_heading(message: str) -> None:
    print(f"{CYAN}{message}{RESET}")


def log_error(message: str) -> None:
    print(f"{RED}{message}{RESET}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract Schwab Lot Details CSV files from a logged-in Safari or Chrome session."
    )
    parser.add_argument(
        "account_name",
        nargs="?",
        default="Inheritance",
        help="Schwab account display name to use; default: Inheritance",
    )
    parser.add_argument(
        "--output",
        help="Output directory; default: SchwabLots",
    )
    parser.add_argument(
        "--browser",
        choices=("safari", "chrome-cdp"),
        default="safari",
        help="Browser automation backend; default: safari",
    )
    parser.add_argument(
        "--cdp-url",
        default="http://127.0.0.1:9222",
        help="Chrome DevTools Protocol URL for --browser chrome-cdp; default: http://127.0.0.1:9222",
    )
    return parser.parse_args()


def fail_startup(
    account_name: str,
    extra_steps: list[str] | None = None,
    browser_name: str = "Safari",
) -> None:
    log_error(f"ERROR: {browser_name} must already be running on a logged-in Schwab page.")
    log_heading("Required startup state:")
    if browser_name.lower().startswith("safari"):
        print("  1. Open Safari")
        print("  2. Enable Develop > Allow JavaScript from Apple Events")
        print("  3. Log in to Schwab manually in Safari")
        print("  4. Do not use the Codex attached browser for this script; it is a separate session")
        print("  5. Navigate Safari anywhere inside Schwab, or directly to Accounts > Positions")
        print(f"  6. Make sure the '{account_name}' account is available")
    else:
        print("  1. Start Chrome with remote debugging enabled on port 9222")
        print("  2. Log in to Schwab manually in that Chrome window")
        print("  3. Navigate anywhere inside Schwab, or directly to Accounts > Positions")
        print(f"  4. Make sure the '{account_name}' account is available")
    if extra_steps:
        for step in extra_steps:
            print(step)
    raise SystemExit(1)


def clean_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def normalize_header(header: str) -> str:
    header = clean_text(header)
    if header.startswith("Open Date"):
        return "Open Date"
    if header.startswith("Cost/Share"):
        return "Cost/Share"
    if header.startswith("Cost Basis"):
        return "Cost Basis"
    if header.startswith("Gain/Loss $"):
        return "Gain/Loss $"
    if header.startswith("Gain/Loss %"):
        return "Gain/Loss %"
    return header


def csv_write(path: Path, rows: list[list[str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerows(rows)


def sanitize_filename(name: str) -> str:
    safe = re.sub(r"[/:]+", "-", clean_text(name))
    safe = re.sub(r"[\x00-\x1f]+", "", safe).strip(". ")
    return safe or "account"


def prepare_output_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    log_info(f"Writing CSV files to {path}")


def output_file_for_account(output_dir: Path, metadata: dict, fallback_account_name: str) -> Path:
    schwab_account_name = metadata.get("selectedAccount") or fallback_account_name
    return output_dir / f"{sanitize_filename(schwab_account_name)}.csv"


def safari_run_javascript(script: str, timeout: int = 30) -> str:
    jxa = f"""
const safari = Application('Safari');
if (!safari.running()) {{
  throw new Error('Safari is not running');
}}
safari.activate();
const docs = safari.documents();
let doc = null;
for (let i = 0; i < docs.length; i++) {{
  const url = String(docs[i].url() || '');
  if (url.includes('schwab.com')) {{
    doc = docs[i];
    break;
  }}
}}
if (doc === null) {{
  throw new Error('No Safari tab open on schwab.com. This script controls Safari, not the Codex attached browser. Open Schwab in Safari and log in there.');
}}
const result = safari.doJavaScript({json.dumps(script)}, {{in: doc}});
let output = '';
if (result === undefined || result === null) {{
  output = '';
}} else {{
  output = String(result);
}}
output;
"""
    try:
        completed = subprocess.run(
            ["osascript", "-l", "JavaScript", "-e", jxa],
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.CalledProcessError as exc:
        detail = clean_text(exc.stderr or exc.stdout or str(exc))
        if "JavaScript" in detail or "not allowed" in detail or "not authorized" in detail:
            raise RuntimeError(
                "Safari refused JavaScript from Apple Events. Enable Safari > "
                "Develop > Allow JavaScript from Apple Events, then rerun."
            ) from exc
        raise RuntimeError(detail) from exc
    return completed.stdout.strip()


def safari_eval_json(script: str, timeout: int = 30):
    raw = safari_run_javascript(script, timeout=timeout)
    if not raw:
        raise RuntimeError("Safari returned an empty JavaScript result.")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Safari returned non-JSON JavaScript result: {raw[:200]!r}") from exc


def safari_state() -> dict:
    return safari_eval_json(
        """JSON.stringify({
            url: window.location.href,
            title: document.title
        })"""
    )


def safari_wait_for(predicate_script: str, timeout_seconds: int = 30) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            result = safari_eval_json(f"JSON.stringify(Boolean({predicate_script}))", timeout=10)
            if result:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def safari_wait_for_positions_table(timeout_seconds: int = 30) -> None:
    ok = safari_wait_for(
        "Boolean(document.querySelector('table')) && "
        "Array.from(document.querySelectorAll('a[href*=\"SymbolRouting.aspx\"]')).length > 0",
        timeout_seconds=timeout_seconds,
    )
    if not ok:
        raise RuntimeError("Timed out waiting for the Schwab Positions table to load in Safari.")


def safari_ensure_logged_in_positions(account_name: str) -> None:
    state = safari_state()
    current_url = state["url"].lower()
    if any(token in current_url for token in ("login", "signin", "authenticate", "two-step")):
        fail_startup(
            account_name,
            [f"  6. Current Safari tab URL was: {state['url']}"],
            browser_name="Safari",
        )

    if "positions" not in current_url:
        # A Summary-row account link navigates directly to Positions *with that
        # account selected*.  Going straight to POSITIONS_URL loses that
        # context and Schwab falls back to its default (often Rollover IRA).
        if "/accounts/summary" in current_url:
            log_info(f"Opening Positions for Safari account: {account_name}")
            wanted_account = json.dumps(account_name.lower())
            if not safari_wait_for(
                f"""(function() {{
                    var clean = function(s) {{ return (s || '').replace(/\\s+/g, ' ').trim(); }};
                    return Array.prototype.some.call(document.querySelectorAll('a.acctNavigate-button-link'), function(el) {{
                        return clean(el.innerText || el.textContent).toLowerCase() === {wanted_account};
                    }});
                }})()""",
                timeout_seconds=20,
            ):
                raise RuntimeError(f"Requested Safari account was not found on Summary: {account_name}")
            result = safari_eval_json(
                f"""JSON.stringify((function() {{
                    var clean = function(s) {{ return (s || '').replace(/\\s+/g, ' ').trim(); }};
                    var wanted = {json.dumps(account_name.lower())};
                    var links = Array.prototype.slice.call(document.querySelectorAll('a.acctNavigate-button-link'));
                    var link = links.find(function(el) {{
                        return clean(el.innerText || el.textContent).toLowerCase() === wanted;
                    }});
                    if (!link) return {{clicked: false, reason: 'account link not found on Summary'}};
                    link.click();
                    return {{clicked: true, text: clean(link.innerText || link.textContent)}};
                }})())""",
                timeout=10,
            )
            if not result or not result.get("clicked"):
                raise RuntimeError(f"Could not open Safari account from Summary: {result}")
        else:
            log_info("Opening Schwab Positions page in Safari")
            safari_run_javascript(
                f"window.location.href = {json.dumps(POSITIONS_URL)}; JSON.stringify({{url: window.location.href}})"
            )
        safari_wait_for(
            "window.location.href.toLowerCase().includes('positions') || "
            "/login|signin|authenticate|two-step/i.test(window.location.href)",
            timeout_seconds=30,
        )
        time.sleep(2)

    state = safari_state()
    current_url = state["url"].lower()
    if any(token in current_url for token in ("login", "signin", "authenticate", "two-step")):
        fail_startup(
            account_name,
            [
                "  6. Finish the Schwab login / 2FA flow first",
                f"  7. Current Safari tab URL was: {state['url']}",
            ],
            browser_name="Safari",
        )
    if "positions" not in current_url:
        fail_startup(
            account_name,
            [
                "  6. Navigate Safari to Accounts > Positions before running this script",
                f"  7. Current Safari tab URL was: {state['url']}",
            ],
            browser_name="Safari",
        )


def safari_get_page_metadata() -> dict:
    return safari_eval_json(
        """JSON.stringify((function() {
            var clean = function(s) { return (s || '').replace(/\\s+/g, ' ').trim(); };
            var body = clean(document.body.innerText);
            var timeEl = document.querySelector('time');
            var updated = clean(timeEl ? timeEl.innerText : '');
            var suffixMatch = body.match(/Account ending in\\s+([\\d\\s]+)/i);
            var suffix = suffixMatch ? suffixMatch[1].replace(/\\s+/g, '') : '';

            var selectedMatch = body.match(/Account Selector:\\s*(.*?)\\s+Account ending in/i);
            var selectedAccount = selectedMatch ? clean(selectedMatch[1]) : '';

            var cash = null;
            var cashMatch = body.match(/Total cash & cash invest(?:ments)?\\s+\\$?([\\d,]+\\.\\d{2})/i);
            if (cashMatch) cash = '$' + cashMatch[1];

            var symbols = [];
            var table = document.querySelector('table');
            var links = table ? table.querySelectorAll('a[href*="SymbolRouting.aspx?symbol="], a[href*="SymbolRouting.aspx?Symbol="]') : [];
            for (var i = 0; i < links.length; i++) {
                var symbol = clean(links[i].textContent);
                if (symbol && symbols.indexOf(symbol) === -1) symbols.push(symbol);
            }

            return { updated: updated, suffix: suffix, cash: cash, symbols: symbols, selectedAccount: selectedAccount };
        })())""",
        timeout=30,
    )


def safari_select_account_if_needed(account_name: str) -> None:
    metadata = safari_get_page_metadata()
    if not isinstance(metadata, dict):
        raise RuntimeError("Could not read Schwab account metadata from Safari.")
    selected = metadata.get("selectedAccount") or ""
    if account_name.lower() in selected.lower():
        log_info(f"Account already selected: {selected}")
        return

    log_info(f"Trying to select account in Safari: {account_name}")
    result = safari_eval_json(
        f"""JSON.stringify((function() {{
            var clean = function(s) {{ return (s || '').replace(/\\s+/g, ' ').trim(); }};
            var selector = document.querySelector('button.account-selector-button') ||
                Array.prototype.find.call(document.querySelectorAll('button'), function(el) {{
                    return /account ending/i.test(clean(el.innerText || el.textContent));
                }});
            if (!selector) return {{clicked:false, reason:'account selector button not found'}};
            selector.click();
            return {{clicked:true, text:'account selector'}};
        }})())""",
        timeout=10,
    )
    if not result or not result.get("clicked"):
        raise RuntimeError(f"Could not switch Safari account automatically: {result}")

    account_name_json = json.dumps(account_name.lower())
    option_clicked = safari_wait_for(
        f"""(function() {{
            var clean = function(s) {{ return (s || '').replace(/\\s+/g, ' ').trim(); }};
            var option = Array.prototype.find.call(document.querySelectorAll('a, [role="option"], [role="menuitem"]'), function(el) {{
                var rect = el.getBoundingClientRect();
                var style = getComputedStyle(el);
                return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none' && clean(el.innerText || el.textContent).toLowerCase().indexOf({account_name_json}) !== -1;
            }});
            if (!option) return false;
            option.click();
            return true;
        }})()""",
        timeout_seconds=10,
    )
    if not option_clicked:
        raise RuntimeError(f"Could not find visible Safari account option: {account_name}")

    log_info(f"Clicked Safari account option: {account_name}")
    ok = safari_wait_for(
        f"""(function() {{
            var clean = function(s) {{ return (s || '').replace(/\\s+/g, ' ').trim(); }};
            var body = clean(document.body.innerText);
            var selectedMatch = body.match(/Account Selector:\\s*(.*?)\\s+Account ending in/i);
            var selected = selectedMatch ? clean(selectedMatch[1]).toLowerCase() : '';
            return selected.indexOf({json.dumps(account_name.lower())}) !== -1;
        }})()""",
        timeout_seconds=15,
    )
    if not ok:
        metadata = safari_get_page_metadata()
        raise RuntimeError(
            f"Clicked account option, but selected account is still "
            f"{metadata.get('selectedAccount') if isinstance(metadata, dict) else 'unknown'}."
        )
    time.sleep(1)


def safari_cleanup_ui() -> None:
    try:
        safari_run_javascript(
            """JSON.stringify((() => {
                for (let i = 0; i < 4; i++) {
                    document.dispatchEvent(new KeyboardEvent('keydown', {key:'Escape', bubbles:true}));
                }
                const visible = (el) => {
                    const rect = el.getBoundingClientRect();
                    const style = getComputedStyle(el);
                    return rect.width > 0 && rect.height > 0 &&
                        style.visibility !== 'hidden' && style.display !== 'none';
                };
                for (const selector of [
                    'button[aria-label="Close"]',
                    'button.sdps-modal__close',
                    'button[aria-label="close modal"]'
                ]) {
                    for (const button of Array.from(document.querySelectorAll(selector))) {
                        if (visible(button)) button.click();
                    }
                }
                document.querySelectorAll('.sdps-backdrop').forEach((el) => el.remove());
                document.body.classList.remove('sdps-modal--open', 'sdps-overflow-hidden');
                document.body.style.overflow = '';
                return true;
            })())""",
            timeout=10,
        )
    except Exception:
        pass
    time.sleep(0.3)


def safari_open_lot_details(ticker: str) -> bool:
    result = safari_eval_json(
        f"""JSON.stringify((() => {{
            const ticker = {json.dumps(ticker)};
            const clean = (s) => (s || '').replace(/\\s+/g, ' ').trim();
            const visible = (el) => {{
                const rect = el.getBoundingClientRect();
                const style = getComputedStyle(el);
                return rect.width > 0 && rect.height > 0 &&
                    style.visibility !== 'hidden' && style.display !== 'none';
            }};
            const links = Array.from(document.querySelectorAll(
                `a[href="/SymbolRouting.aspx?symbol=${{ticker}}"], ` +
                `a[href="/SymbolRouting.aspx?Symbol=${{ticker}}"], ` +
                `a[href*="symbol=${{ticker}}"], a[href*="Symbol=${{ticker}}"]`
            ));
            const link = links.find((el) => clean(el.textContent) === ticker) || links[0];
            if (!link) return {{clicked:false, reason:'symbol link not found'}};
            const row = link.closest('tr');
            if (!row) return {{clicked:false, reason:'symbol row not found'}};
            const menu = row.querySelector('button[aria-label="Open Menu"], button[aria-label="Next Steps"]');
            if (!menu) return {{clicked:false, reason:'row menu not found'}};
            menu.scrollIntoView({{block:'center', inline:'nearest'}});
            menu.click();
            return {{clicked:true}};
        }})())""",
        timeout=10,
    )
    if not result or not result.get("clicked"):
        raise RuntimeError(result.get("reason") if result else "could not click row menu")

    time.sleep(0.8)
    deadline = time.time() + 6
    while time.time() < deadline:
        result = safari_eval_json(
            """JSON.stringify((() => {
                const clean = (s) => (s || '').replace(/\\s+/g, ' ').trim();
                const visible = (el) => {
                    const rect = el.getBoundingClientRect();
                    const style = getComputedStyle(el);
                    return rect.width > 0 && rect.height > 0 &&
                        style.visibility !== 'hidden' && style.display !== 'none';
                };
                const item = Array.from(document.querySelectorAll('[role="menuitem"], a, button, div'))
                    .find((el) => visible(el) && clean(el.innerText || el.textContent) === 'Lot Details');
                if (!item) return {clicked:false};
                item.click();
                return {clicked:true};
            })())""",
            timeout=10,
        )
        if result and result.get("clicked"):
            return True
        time.sleep(0.25)
    return False


def safari_extract_lot_rows() -> tuple[str, list[list[str]]]:
    result = safari_eval_json(
        """JSON.stringify((() => {
            const clean = (s) => (s || '').replace(/\\s+/g, ' ').trim();
            const title = Array.from(document.querySelectorAll('h1,h2,h3,h4'))
                .map((el) => clean(el.innerText))
                .find((text) => text.startsWith('Lot Details:')) || '';

            const tables = Array.from(document.querySelectorAll('table'));
            const table = tables.find((tbl) => {
                const headers = Array.from(tbl.querySelectorAll('tr:first-child th, tr:first-child td'))
                    .map((el) => clean(el.innerText));
                return headers.some((h) => h.startsWith('Open Date')) &&
                    headers.includes('Quantity') &&
                    headers.some((h) => h.startsWith('Cost Basis'));
            });
            if (!table) return { title, rows: [] };

            const rows = Array.from(table.querySelectorAll('tr'))
                .map((tr) => Array.from(tr.children).map((td) => clean(td.innerText)))
                .filter((row) => row.some(Boolean));
            return { title, rows };
        })())""",
        timeout=30,
    )
    rows = result["rows"]
    if rows:
        rows[0] = [normalize_header(value) for value in rows[0]]
    return result["title"], rows


def safari_scrape_lots(
    output_file: Path,
    account_suffix: str,
    as_of: str,
    symbols: list[str],
    cash_value: str | None,
) -> None:
    collected: list[tuple[str, list[list[str]]]] = []
    skipped: list[tuple[str, str]] = []

    for index, ticker in enumerate(symbols, start=1):
        print(f"\n{CYAN}--- Processing {ticker} ({index}/{len(symbols)}) ---{RESET}")
        try:
            safari_cleanup_ui()
            if not safari_open_lot_details(ticker):
                raise RuntimeError("Lot Details menu item was not visible")
            safari_wait_for(
                "Array.from(document.querySelectorAll('button')).some((button) => "
                "button.title === 'Export Lots' && button.getBoundingClientRect().height > 0)",
                timeout_seconds=10,
            )
            time.sleep(0.25)

            overlay_title, rows = safari_extract_lot_rows()
            validate_lot_rows(rows)
            collected.append((ticker, rows))
            log_info(f"Collected {len(rows) - 1} lot rows; {overlay_title}")
        except Exception as exc:
            skipped.append((ticker, str(exc)))
            log_error(f"  Skipping {ticker}: {exc}")
        finally:
            safari_cleanup_ui()

    print()
    combined_rows = build_combined_lot_rows(collected, cash_value)
    path = save_combined_lot_file(output_file, account_suffix, as_of, combined_rows)
    log_heading(f"Saved combined lot CSV: {path}")
    log_info(f"Collected {len(collected)} symbols and {len(combined_rows) - 1} lot/cash rows")
    if skipped:
        log_error(f"Skipped {len(skipped)} symbols:")
        for ticker, reason in skipped:
            print(f"  {ticker}: {reason}")


def run_safari(args: argparse.Namespace, output_dir: Path) -> None:
    log_info("Using Safari Apple Events backend")
    try:
        safari_ensure_logged_in_positions(args.account_name)
        safari_wait_for_positions_table(timeout_seconds=30)
        safari_select_account_if_needed(args.account_name)
        safari_wait_for_positions_table(timeout_seconds=30)
        metadata = safari_get_page_metadata()
        if not isinstance(metadata, dict):
            raise RuntimeError("Could not read Schwab Positions metadata from Safari.")
    except Exception as exc:
        log_error(str(exc))
        log_heading("Safari setup needed:")
        print("  1. Open Safari")
        print("  2. Enable Develop > Allow JavaScript from Apple Events")
        print("  3. Log in to Schwab manually in Safari")
        print("  4. Do not use the Codex attached browser for this script; it is a separate session")
        print("  5. Navigate Safari anywhere inside Schwab, or directly to Accounts > Positions")
        raise SystemExit(1) from exc

    symbols = metadata["symbols"]
    if not symbols:
        fail_startup(
            args.account_name,
            ["  6. The Positions table is visible but no symbols were found"],
            browser_name="Safari",
        )

    log_info(f"Account suffix: ...{metadata['suffix'] or 'unknown'}")
    log_info(f"Positions updated: {metadata['updated'] or 'unknown'}")
    log_heading(f"Found {len(symbols)} symbols")

    output_file = output_file_for_account(output_dir, metadata, args.account_name)
    log_info(f"Output file: {output_file}")
    safari_scrape_lots(
        output_file,
        metadata["suffix"],
        as_of_text(metadata["updated"]),
        symbols,
        metadata["cash"],
    )


def find_schwab_page(context):
    for page in context.pages:
        if "schwab.com" in page.url.lower():
            return page
    return None


def ensure_logged_in_positions(page, account_name: str, browser_name: str = "Chrome") -> None:
    current_url = page.url.lower()
    if any(token in current_url for token in ("login", "signin", "authenticate", "two-step")):
        fail_startup(
            account_name,
            [f"  5. Current tab URL was: {page.url}"],
            browser_name=browser_name,
        )

    if "positions" not in current_url:
        log_info("Opening Schwab Positions page")
        page.goto(POSITIONS_URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2500)

    current_url = page.url.lower()
    if any(token in current_url for token in ("login", "signin", "authenticate", "two-step")):
        fail_startup(
            account_name,
            [
                "  5. Finish the Schwab login / 2FA flow first",
                f"  6. Current tab URL was: {page.url}",
            ],
            browser_name=browser_name,
        )
    if "positions" not in current_url:
        fail_startup(
            account_name,
            [
                "  5. Navigate to Accounts > Positions before running this script",
                f"  6. Current tab URL was: {page.url}",
            ],
            browser_name=browser_name,
        )


def select_account_if_needed(page, account_name: str) -> None:
    body = clean_text(page.locator("body").inner_text(timeout=10000))
    selected_match = re.search(r"Account Selector:\s*(.*?)\s+Account ending in", body, re.I)
    selected = clean_text(selected_match.group(1)) if selected_match else ""
    if account_name.lower() in selected.lower():
        log_info(f"Account already selected: {selected}")
        return

    log_info(f"Trying to select account: {account_name}")
    try:
        selector_button = page.locator("button").filter(has_text="Account ending").first
        selector_button.click(timeout=5000)
        page.get_by_text(account_name, exact=False).click(timeout=5000)
        page.wait_for_load_state("domcontentloaded", timeout=15000)
        page.wait_for_timeout(2000)
    except Exception as exc:
        log_info(f"Could not switch account automatically: {exc}")
        log_info("Continuing with the currently selected account.")


def get_page_metadata(page) -> dict:
    return page.evaluate(
        """() => {
            const clean = (s) => (s || '').replace(/\\s+/g, ' ').trim();
            const body = clean(document.body.innerText);
            const updated = clean(document.querySelector('time')?.innerText || '');
            const suffix = (body.match(/Account ending in\\s+([\\d\\s]+)/i)?.[1] || '')
                .replace(/\\s+/g, '');

            let cash = null;
            const cashMatch = body.match(/Total cash & cash invest(?:ments)?\\s+\\$?([\\d,]+\\.\\d{2})/i);
            if (cashMatch) cash = '$' + cashMatch[1];

            const symbols = [];
            const table = document.querySelector('table');
            for (const a of Array.from(table?.querySelectorAll('a[href*="SymbolRouting.aspx?symbol="]') || [])) {
                const symbol = clean(a.textContent);
                if (symbol && !symbols.includes(symbol)) symbols.push(symbol);
            }

            return { updated, suffix, cash, symbols, selectedAccount };
        }"""
    )


def as_of_text(updated: str) -> str:
    match = re.search(
        r"(\d{1,2}:\d{2}:\d{2}\s*[AP]M)\s*ET,\s*(\d{1,2}/\d{1,2}/\d{4})",
        updated or "",
        re.I,
    )
    if match:
        return f"{match.group(1).upper()} ET, {match.group(2)}"
    return datetime.now().strftime("%I:%M:%S %p ET, %m/%d/%Y")


def cleanup_ui(page) -> None:
    for _ in range(4):
        try:
            page.keyboard.press("Escape")
        except PlaywrightError:
            pass
        time.sleep(0.1)

    for selector in (
        "button[aria-label='Close']",
        "button.sdps-modal__close",
        "button[aria-label='close modal']",
    ):
        try:
            buttons = page.locator(selector)
            for idx in range(buttons.count()):
                button = buttons.nth(idx)
                if button.is_visible(timeout=250):
                    button.click(timeout=750)
                    time.sleep(0.2)
        except PlaywrightError:
            pass

    try:
        page.evaluate(
            """() => {
                document.querySelectorAll('.sdps-backdrop').forEach((el) => el.remove());
                document.body.classList.remove('sdps-modal--open', 'sdps-overflow-hidden');
                document.body.style.overflow = '';
            }"""
        )
    except PlaywrightError:
        pass


def find_symbol_menu(page, ticker: str):
    candidates = [
        f'a[href="/SymbolRouting.aspx?symbol={ticker}"]',
        f'a[href="/SymbolRouting.aspx?Symbol={ticker}"]',
        f'a[href*="symbol={ticker}"]',
        f'a[href*="Symbol={ticker}"]',
    ]
    for selector in candidates:
        link = page.locator(selector)
        try:
            if link.count() == 1:
                row = link.locator("xpath=ancestor::tr")
                menu = row.locator(
                    "button[aria-label='Open Menu'], button[aria-label='Next Steps']"
                )
                if menu.count() == 1:
                    return menu
        except PlaywrightError:
            pass
    return None


def open_lot_details(page, ticker: str) -> bool:
    menu = find_symbol_menu(page, ticker)
    if menu is None:
        raise RuntimeError("could not find row menu")

    menu.scroll_into_view_if_needed(timeout=5000)
    menu.click(timeout=7000)
    time.sleep(0.6)

    deadline = time.time() + 6
    while time.time() < deadline:
        items = page.locator("[role='menuitem']").filter(has_text="Lot Details")
        for idx in range(items.count()):
            item = items.nth(idx)
            try:
                if item.is_visible(timeout=250):
                    item.click(timeout=3000)
                    return True
            except PlaywrightError:
                pass
        time.sleep(0.2)
    return False


def extract_lot_rows(page) -> tuple[str, list[list[str]]]:
    result = page.evaluate(
        """() => {
            const clean = (s) => (s || '').replace(/\\s+/g, ' ').trim();
            const title = Array.from(document.querySelectorAll('h1,h2,h3,h4'))
                .map((el) => clean(el.innerText))
                .find((text) => text.startsWith('Lot Details:')) || '';

            const tables = Array.from(document.querySelectorAll('table'));
            const table = tables.find((tbl) => {
                const headers = Array.from(tbl.querySelectorAll('tr:first-child th, tr:first-child td'))
                    .map((el) => clean(el.innerText));
                return headers.some((h) => h.startsWith('Open Date')) &&
                    headers.includes('Quantity') &&
                    headers.some((h) => h.startsWith('Cost Basis'));
            });
            if (!table) return { title, rows: [] };

            const rows = Array.from(table.querySelectorAll('tr'))
                .map((tr) => Array.from(tr.children).map((td) => clean(td.innerText)))
                .filter((row) => row.some(Boolean));
            return { title, rows };
        }"""
    )

    rows = result["rows"]
    if rows:
        rows[0] = [normalize_header(value) for value in rows[0]]
    return result["title"], rows


def validate_lot_rows(rows: list[list[str]]) -> None:
    if len(rows) < 2:
        raise RuntimeError("no lot rows found")
    header_values = {clean_text(value) for value in rows[0]}
    if not REQUIRED_LOT_HEADERS <= header_values:
        raise RuntimeError(f"missing required headers: {sorted(REQUIRED_LOT_HEADERS - header_values)}")


def cash_lot_row(header: list[str], cash_value: str | None) -> list[str] | None:
    if not cash_value:
        log_error("Warning: Could not find account cash value on Schwab page")
        return None

    row = [""] * len(header)
    row[0] = "CASH"
    for idx, name in enumerate(header):
        if name == "Quantity":
            row[idx] = "0"
        elif name == "Cost Basis":
            row[idx] = cash_value
    return row


def build_combined_lot_rows(
    lots_by_symbol: list[tuple[str, list[list[str]]]],
    cash_value: str | None,
) -> list[list[str]]:
    first_header = next((rows[0] for _, rows in lots_by_symbol if rows), None)
    if not first_header:
        raise RuntimeError("No lot rows were collected.")

    header = ["Symbol", *first_header]
    combined = [header]
    for ticker, rows in lots_by_symbol:
        for row in rows[1:]:
            combined.append([ticker, *row])

    cash_row = cash_lot_row(header, cash_value)
    if cash_row:
        combined.append(cash_row)
    return combined


def save_combined_lot_file(
    output_path: Path,
    account_suffix: str,
    as_of: str,
    rows: list[list[str]],
) -> Path:
    title = f"Schwab Lot Details for ...{account_suffix or 'account'} as of {as_of}"
    csv_write(output_path, [[title], [], *rows])
    return output_path


def scrape_lots(
    page,
    output_file: Path,
    account_suffix: str,
    as_of: str,
    symbols: list[str],
    cash_value: str | None,
) -> None:
    collected: list[tuple[str, list[list[str]]]] = []
    skipped: list[tuple[str, str]] = []

    for index, ticker in enumerate(symbols, start=1):
        print(f"\n{CYAN}--- Processing {ticker} ({index}/{len(symbols)}) ---{RESET}")
        try:
            cleanup_ui(page)
            if not open_lot_details(page, ticker):
                raise RuntimeError("Lot Details menu item was not visible")

            page.locator("button[title='Export Lots']").wait_for(state="visible", timeout=10000)
            time.sleep(0.25)

            overlay_title, rows = extract_lot_rows(page)
            validate_lot_rows(rows)
            collected.append((ticker, rows))
            log_info(f"Collected {len(rows) - 1} lot rows; {overlay_title}")
        except Exception as exc:
            skipped.append((ticker, str(exc)))
            log_error(f"  Skipping {ticker}: {exc}")
        finally:
            cleanup_ui(page)

    print()
    combined_rows = build_combined_lot_rows(collected, cash_value)
    path = save_combined_lot_file(output_file, account_suffix, as_of, combined_rows)
    log_heading(f"Saved combined lot CSV: {path}")
    log_info(f"Collected {len(collected)} symbols and {len(combined_rows) - 1} lot/cash rows")
    if skipped:
        log_error(f"Skipped {len(skipped)} symbols:")
        for ticker, reason in skipped:
            print(f"  {ticker}: {reason}")


def main() -> None:
    args = parse_args()
    output_dir = Path(args.output) if args.output else DEFAULT_OUTPUT_DIR

    log_heading(f"Starting Schwab lot extraction for account: {args.account_name}")
    prepare_output_dir(output_dir)

    if args.browser == "safari":
        run_safari(args, output_dir)
        return

    with sync_playwright() as playwright:
        log_info(f"Connecting to Chrome at {args.cdp_url}")
        try:
            browser = playwright.chromium.connect_over_cdp(args.cdp_url)
        except Exception as exc:
            log_error(f"Could not connect to Chrome: {exc}")
            fail_startup(args.account_name, browser_name="Chrome")

        if not browser.contexts:
            fail_startup(
                args.account_name,
                ["  5. Keep the Schwab tab open in that Chrome session"],
                browser_name="Chrome",
            )

        context = browser.contexts[0]
        page = find_schwab_page(context)
        if page is None:
            fail_startup(
                args.account_name,
                ["  5. Open at least one tab on schwab.com"],
                browser_name="Chrome",
            )

        page.bring_to_front()
        ensure_logged_in_positions(page, args.account_name)
        select_account_if_needed(page, args.account_name)

        page.locator("table").first.wait_for(state="visible", timeout=30000)
        metadata = get_page_metadata(page)
        symbols = metadata["symbols"]
        if not symbols:
            fail_startup(
                args.account_name,
                ["  5. The Positions table is visible but no symbols were found"],
            )

        log_info(f"Account suffix: ...{metadata['suffix'] or 'unknown'}")
        log_info(f"Positions updated: {metadata['updated'] or 'unknown'}")
        log_heading(f"Found {len(symbols)} symbols")

        output_file = output_file_for_account(output_dir, metadata, args.account_name)
        log_info(f"Output file: {output_file}")
        scrape_lots(
            page,
            output_file,
            metadata["suffix"],
            as_of_text(metadata["updated"]),
            symbols,
            metadata["cash"],
        )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()
        log_error("Interrupted")
        sys.exit(130)
