"""
Schwab Lot Details Scraper (CDP Mode)

Automated downloader for Charles Schwab investment lot details using Playwright
in Chrome DevTools Protocol (CDP) mode. Connects to an existing Chrome browser
session and downloads lot-level cost basis CSV files for all securities in a
specified account.

PREREQUISITES:
1. Chrome must be running with remote debugging enabled:
   chrome.exe --remote-debugging-port=9222
   
2. User must be logged into Schwab (manual 2FA completion required beforehand)

3. Python dependencies:
   pip install playwright
   playwright install chromium

USAGE:
    python scrape_schwab_lots.py [account_name]
    
    account_name: Optional. Name of Schwab account to process (default: 'Inheritance')
                  Must match the exact account link name on Schwab positions page.

EXAMPLES:
    python scrape_schwab_lots.py                    # Downloads from 'Inheritance' account
    python scrape_schwab_lots.py "Joint Account"   # Downloads from 'Joint Account'

OUTPUT:
    Creates directory: lots-{account_name}/
    Downloads files:   lots-{account_name}/AAPL.csv
                       lots-{account_name}/GOOGL.csv
                       ... (one CSV per security)

BEHAVIOR:
    - Removes existing lots-{account_name} directory on startup (clean slate)
    - Processes ALL securities in the account automatically
    - Skips securities without lot details (e.g., single-lot positions)
    - Continues processing on errors (robust error handling)
    - Uses CDP mode to work with existing Chrome session (avoids bot detection)

TECHNICAL NOTES:
    - CDP mode limitations: Cannot use standard Playwright browser launch
    - Download capture: Listens for 'download' events on main page (not popup)
    - JavaScript trigger: OK button calls window.opener.$("#exportLotDetails").trigger('click')
    - Error handling: Skips securities where "Lot Details" menu item is unavailable
    - Timing: Uses explicit waits + sleep() for dynamic content loading

INTEGRATION:
    Output CSV files are consumed by schwab_quicken_rebuild.py for Quicken import
    processing. See RepairQuickenTransactions.md for complete workflow.

AUTHOR: Generated for Quicken Cost Basis Repair Tool
DATE: December 2025
"""

from playwright.sync_api import sync_playwright
import sys
import shutil
from pathlib import Path
import time
import csv

# ANSI color codes
CYAN = '\033[96m'      # Headings
GRAY = '\033[90m'      # Details
RED = '\033[91m'       # Errors
RESET = '\033[0m'      # Reset to default

def fail_startup(account_name, extra_steps=None):
    print(f"{RED}ERROR: Chrome must already be running on a logged-in Schwab page before this script starts.{RESET}")
    print(f"{CYAN}Required startup state:{RESET}")
    print("  1. Start Chrome with remote debugging enabled:")
    print("     chrome.exe --remote-debugging-port=9222")
    print("  2. Log in to Schwab manually in that Chrome window")
    print("  3. Navigate to Accounts > Positions")
    print(f"  4. Make sure the '{account_name}' account is available")
    if extra_steps:
        for step in extra_steps:
            print(step)
    raise SystemExit(1)

def extract_account_cash(page):
    return page.evaluate(
        """() => {
            const clean = (s) => (s || '').replace(/\\s+/g, ' ').trim();
            const extractValue = (text) => {
                const matches = clean(text).match(/-?\\$?[\\d,]+(?:\\.\\d+)?\\*?/g);
                return matches && matches.length ? matches[matches.length - 1] : null;
            };

            const cashLabels = new Set([
                'total cash & cash invest',
                'total cash & cash investments',
            ]);

            // Preferred path: the Account Summary tiles are sdps-display-value
            // components with a label and value under the same container.
            const tiles = Array.from(document.querySelectorAll('sdps-display-value'));
            for (const tile of tiles) {
                const labelEl = tile.querySelector('.sdps-display-value__label, sdps-sololayout, [slot="label"]');
                const label = clean(labelEl ? labelEl.innerText : '');
                if (!cashLabels.has(label.toLowerCase())) continue;

                const valueEl = tile.querySelector('.sdps-display-value__value, sdps-number, [slot="value"]');
                const value = extractValue(valueEl ? valueEl.innerText : tile.innerText);
                if (value) return value;
            }

            return null;
        }""",
    )

def write_cash_file(lots_dir, account_name, cash_value):
    cash_path = Path(lots_dir) / "Cash.csv"
    with cash_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([f"CASH Summary for {account_name}"])
        w.writerow([])
        w.writerow(["Symbol", "Shares", "Cost Basis"])
        w.writerow(["CASH", "0", cash_value])
    print(f"{GRAY}Saved cash summary to {cash_path}{RESET}")

def cleanup_overlays(page):
    try:
        close_buttons = page.locator("button.sdps-modal__close, button[aria-label='close modal']").all()
        for btn in close_buttons:
            try:
                btn.click(force=True, timeout=500)
            except:
                pass
    except:
        pass

    for _ in range(3):
        try:
            page.keyboard.press("Escape")
        except:
            pass
        time.sleep(0.2)

    try:
        page.evaluate("""
            () => {
                const backdrops = document.querySelectorAll('.sdps-backdrop');
                backdrops.forEach(b => b.remove());

                const modals = document.querySelectorAll('.sdps-modal__overlay, .sdps-modal__overlay--open');
                modals.forEach(m => m.remove());

                const dialogs = document.querySelectorAll('[role="dialog"]');
                dialogs.forEach(d => {
                    const overlay = d.closest('.sdps-modal__overlay');
                    if (overlay) overlay.remove();
                    else d.remove();
                });

                document.body.classList.remove('sdps-modal--open', 'sdps-overflow-hidden');
                document.body.style.overflow = '';
            }
        """)
    except:
        pass

    try:
        page.locator(".sdps-backdrop").wait_for(state="hidden", timeout=1000)
    except:
        pass

def find_next_steps_button_for_ticker(page, ticker):
    next_steps_buttons = page.locator("button[aria-label='Next Steps']")
    count = next_steps_buttons.count()

    for idx in range(count):
        button = next_steps_buttons.nth(idx)
        try:
            row = button.locator("xpath=ancestor::tr")
            row_text = row.inner_text()
            if row_text.split() and row_text.split()[0] == ticker:
                return button
        except:
            pass

    return None

def find_visible_lot_details_link(page, timeout_seconds=5):
    deadline = time.time() + timeout_seconds

    while time.time() < deadline:
        candidate_sets = [
            page.get_by_role("menuitem", name="Lot Details"),
            page.get_by_role("link", name="Lot Details"),
            page.get_by_text("Lot Details", exact=True),
        ]

        for candidates in candidate_sets:
            try:
                count = candidates.count()
            except:
                count = 0

            for idx in range(count):
                candidate = candidates.nth(idx)
                try:
                    if candidate.is_visible():
                        return candidate
                except:
                    pass

        time.sleep(0.2)

    return None

def open_lot_details_menu(page, row, button, ticker, attempts=3):
    for attempt in range(1, attempts + 1):
        cleanup_overlays(page)
        row.scroll_into_view_if_needed(timeout=5000)
        button.scroll_into_view_if_needed(timeout=5000)
        time.sleep(0.5)

        try:
            button.click(timeout=5000)
        except Exception as click_error:
            print(f"{GRAY}  [{ticker}] Next Steps click blocked on attempt {attempt}, retrying with force: {click_error}{RESET}")
            cleanup_overlays(page)
            button.click(timeout=5000, force=True)

        time.sleep(1)
        lot_details_link = find_visible_lot_details_link(page, timeout_seconds=3)
        if lot_details_link is not None:
            return lot_details_link

        print(f"{GRAY}  [{ticker}] Lot Details not visible after attempt {attempt}; retrying{RESET}")
        try:
            page.keyboard.press("Escape")
        except:
            pass
        time.sleep(0.5)

    return None

def main():
    # Get account name from command line, default to 'Inheritance'
    account_name = sys.argv[1] if len(sys.argv) > 1 else 'Inheritance'
    lots_dir = f"lots-{account_name}"

    print(f"{CYAN}Starting lot details download for account: {account_name}{RESET}")
    
    # Remove and recreate the lots directory
    if Path(lots_dir).exists():
        shutil.rmtree(lots_dir)
        print(f"{GRAY}Removed existing {lots_dir} directory{RESET}")
    Path(lots_dir).mkdir(exist_ok=True)
    print(f"{GRAY}Created {lots_dir} directory for downloads{RESET}")
    
    with sync_playwright() as p:
        # Connect to the already-running Chrome (from Step 1)
        print(f"{GRAY}Connecting to Chrome on port 9222...{RESET}")
        try:
            browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        except Exception as e:
            print(f"{RED}Could not connect to Chrome on port 9222: {e}{RESET}")
            fail_startup(account_name)
        print(f"{GRAY}Connected to Chrome{RESET}")

        # A "context" is basically a browser profile/session.
        # With CDP, Chrome usually exposes at least one context.
        if not browser.contexts:
            fail_startup(account_name, ["  5. Keep the Schwab tab open in that Chrome session"])
        context = browser.contexts[0]
        print(f"{GRAY}Got browser context{RESET}")

        # Find the Schwab tab (look for schwab.com in URL)
        page = None
        for p in context.pages:
            if 'schwab.com' in p.url:
                page = p
                print(f"{GRAY}Found Schwab tab: {p.url}{RESET}")
                break
        
        if not page:
            fail_startup(account_name, [
                "  5. Make sure at least one open tab is already on schwab.com",
            ])

        # Bring it to the front so you can watch it work
        page.bring_to_front()
        print(f"{GRAY}Brought page to front{RESET}")

        current_url = page.url.lower()
        if "schwab.com" not in current_url:
            fail_startup(account_name, [f"  5. Current tab URL was: {page.url}"])

        if any(token in current_url for token in ("login", "signin", "authenticate", "auth", "two-step")):
            fail_startup(account_name, [
                "  5. Finish the Schwab login / 2FA flow first",
                f"  6. Current tab URL was: {page.url}",
            ])
        
        # Set up download event handler on the MAIN page (not popup)
        # The JavaScript in the popup triggers download on window.opener (the main page)
        downloads = []
        current_ticker = None  # Track which ticker we're downloading
        def handle_download(download):
            # Use the current ticker as the filename instead of suggested name
            ticker_name = current_ticker if current_ticker else download.suggested_filename.replace(".csv", "")
            download_path = f"{lots_dir}/{ticker_name}.csv"
            download.save_as(download_path)
            downloads.append(download_path)
            print(f"{GRAY}Download event: {download.suggested_filename}{RESET}")
            print(f"{GRAY}Saved to {download_path}{RESET}")
        
        page.on("download", handle_download)
        print(f"{GRAY}Download handler registered{RESET}")

        # Navigate to the specified account page
        print(f"{GRAY}Looking for account link: '{account_name}'...{RESET}")
        
        # Check if we need to navigate to the account or if we're already there
        try:
            # Try to find the account link (only visible on accounts overview page)
            account_link = page.get_by_role("link", name=account_name)
            account_link.wait_for(timeout=5000)
            account_link.click()
            print(f"{GRAY}Clicked account link{RESET}")
            page.wait_for_load_state('load')
            time.sleep(3)
        except:
            # Link not found - check if we're already on the account positions page
            print(f"{GRAY}Account link not found - checking if already on positions page...{RESET}")
            
            # Check current URL to see if we're on a positions page
            current_url = page.url
            print(f"{GRAY}Current URL: {current_url}{RESET}")
            
            if "positions" not in current_url.lower():
                fail_startup(account_name, [
                    "  5. Navigate to Accounts > Positions before running this script",
                    f"  6. Current tab URL was: {current_url}",
                ])
            
            print(f"{GRAY}Already on positions page, continuing...{RESET}")
            time.sleep(2)
        
        print(f"{GRAY}Ready to process securities{RESET}")

        try:
            cash_value = extract_account_cash(page)
            if cash_value:
                write_cash_file(lots_dir, account_name, cash_value)
            else:
                print(f"{RED}Warning: Could not find account cash value on Schwab page{RESET}")
        except Exception as cash_error:
            print(f"{RED}Warning: Failed to capture cash summary - {cash_error}{RESET}")
     
        # Find all rows in the positions table that have "Next Steps" buttons
        # These represent securities with lot details available
        # Try multiple selectors in case the structure is different
        print(f"{GRAY}Looking for 'Next Steps' buttons...{RESET}")
        next_steps_buttons = page.locator("button[aria-label='Next Steps']").all()
        
        if len(next_steps_buttons) == 0:
            # Try alternative selector
            print(f"{GRAY}No buttons found with aria-label, trying alternative selectors...{RESET}")
            next_steps_buttons = page.locator("button:has-text('Next Steps')").all()
        
        if len(next_steps_buttons) == 0:
            # Try looking in the positions table specifically
            print(f"{GRAY}Still no buttons, checking positions table...{RESET}")
            next_steps_buttons = page.locator("table").locator("button[aria-label='Next Steps']").all()
        
        tickers_to_process = []
        for button in next_steps_buttons:
            try:
                row = button.locator("xpath=ancestor::tr")
                row_text = row.inner_text()
                ticker = row_text.split()[0]
                if ticker not in tickers_to_process:
                    tickers_to_process.append(ticker)
            except:
                pass

        num_securities = len(tickers_to_process)
        print(f"{CYAN}Found {num_securities} securities to process{RESET}")
        
        # Clean up any existing modals/backdrops from previous sessions BEFORE starting
        print(f"{GRAY}Cleaning up any existing modals/backdrops...{RESET}")
        try:
            cleanup_overlays(page)
            time.sleep(1)
            print(f"{GRAY}Initial cleanup complete{RESET}")
        except Exception as e:
            print(f"{GRAY}Initial cleanup failed (page may already be clean): {e}{RESET}")
        
        # Process each security
        for i, ticker in enumerate(tickers_to_process):
            # Re-find the row by ticker each time since the DOM can shift after
            # each modal open/close and download.
            try:
                button = find_next_steps_button_for_ticker(page, ticker)
                if button is None:
                    print(f"{RED}Could not find Next Steps button for {ticker}{RESET}")
                    continue
                row = button.locator("xpath=ancestor::tr")
            except Exception as query_error:
                print(f"{RED}Error querying button {i+1}: {query_error}{RESET}")
                print(f"{RED}Stopping script - page may have been closed{RESET}")
                break
            print(f"\n{CYAN}--- Processing {ticker} ({i+1}/{num_securities}) ---{RESET}")
            current_ticker = ticker
            
            try:
                # Find the visible Lot Details action after opening the menu.
                # Retry because some rows render/open their menu more slowly.
                lot_details_link = open_lot_details_menu(page, row, button, ticker, attempts=3)
                if lot_details_link is None:
                    print(f"{RED}  Skipping {ticker} - no lot details available{RESET}")
                    # Close the dropdown
                    page.keyboard.press("Escape")
                    time.sleep(1)
                    continue
                
                lot_details_link.evaluate("el => el.click()")
                time.sleep(2)
                
                # The export confirmation is a modal in the main page DOM.
                try:
                    export_trigger = page.get_by_title("Export Lots")
                    print(f"{GRAY}  [{ticker}] Clicking Export Lots{RESET}")
                    export_trigger.click()
                    page.get_by_text("Export Lot Details Data").wait_for(state="visible", timeout=5000)
                    ok_button = page.locator("button#button-bar-primary-action-desktop-button")
                    ok_button.wait_for(state="visible", timeout=5000)

                    print(f"{GRAY}  [{ticker}] Clicking modal OK and waiting for download{RESET}")
                    with page.expect_download(timeout=15000):
                        ok_button.evaluate("el => el.click()")
                    download_started = True
                    time.sleep(2)
                except Exception as popup_error:
                    print(f"{RED}  Warning: Popup handling issue - {popup_error}{RESET}")
                    # Continue anyway - download may have worked
                
                # Close modal dialog - use JavaScript to forcibly remove from DOM
                try:
                    cleanup_overlays(page)
                    time.sleep(0.5)
                except:
                    pass
                
            except Exception as e:
                print(f"{RED}  Error processing {ticker}: {e}{RESET}")
                # Aggressively clean up any open dialogs/modals/popups
                try:
                    # Try to close any popup pages first
                    for popup in context.pages[1:]:
                        try:
                            popup.close()
                        except:
                            pass
                    time.sleep(0.5)
                    
                    # Use JavaScript to forcibly remove modals from DOM
                    try:
                        cleanup_overlays(page)
                        time.sleep(0.5)
                    except:
                        pass
                    
                    time.sleep(1)  # Give page time to stabilize
                except Exception as cleanup_error:
                    print(f"{RED}  Cleanup error: {cleanup_error}{RESET}")
                continue
        
        print(f"\n{CYAN}=== Complete! Downloaded {len(downloads)} files ==={RESET}")

if __name__ == "__main__":
    main()
