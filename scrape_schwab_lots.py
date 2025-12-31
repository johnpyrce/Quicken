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

def main():
    # Get account name from command line, default to 'Inheritance'
    account_name = sys.argv[1] if len(sys.argv) > 1 else 'Inheritance'
    lots_dir = f"lots-{account_name}"

    print(f"Starting lot details download for account: {account_name}")
    
    # Remove and recreate the lots directory
    if Path(lots_dir).exists():
        shutil.rmtree(lots_dir)
        print(f"Removed existing {lots_dir} directory")
    Path(lots_dir).mkdir(exist_ok=True)
    print(f"Created {lots_dir} directory for downloads")
    
    with sync_playwright() as p:
        # Connect to the already-running Chrome (from Step 1)
        browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")

        # A "context" is basically a browser profile/session.
        # With CDP, Chrome usually exposes at least one context.
        context = browser.contexts[0]

        # Use the existing tab if there is one, otherwise open a new tab.
        page = context.pages[0] if context.pages else context.new_page()

        # Bring it to the front so you can watch it work
        page.bring_to_front()
        
        # Set up download event handler on the MAIN page (not popup)
        # The JavaScript in the popup triggers download on window.opener (the main page)
        downloads = []
        current_ticker = None  # Track which ticker we're downloading
        
        def handle_download(download):
            print(f"Download started: {download.suggested_filename}")
            # Use the current ticker as the filename instead of suggested name
            ticker_name = current_ticker if current_ticker else download.suggested_filename.replace(".csv", "")
            download_path = f"{lots_dir}/{ticker_name}.csv"
            download.save_as(download_path)
            downloads.append(download_path)
            print(f"Saved to {download_path}")
        
        page.on("download", handle_download)

        # Navigate to the specified account page
        page.get_by_role("link", name=account_name).click()
        page.wait_for_load_state('load')
        time.sleep(3)  # Wait for page to fully load including dynamic content
        
        # Debug: Check what's on the page
        print("Page URL:", page.url)
        print("Page title:", page.title())
        
        # Find all rows in the positions table that have "Next Steps" buttons
        # These represent securities with lot details available
        # Try multiple selectors in case the structure is different
        next_steps_buttons = page.locator("button[aria-label='Next Steps']").all()
        
        if len(next_steps_buttons) == 0:
            # Try alternative selector
            print("No buttons found with aria-label, trying alternative selectors...")
            next_steps_buttons = page.locator("button:has-text('Next Steps')").all()
        
        if len(next_steps_buttons) == 0:
            # Try looking in the positions table specifically
            print("Still no buttons, checking positions table...")
            next_steps_buttons = page.locator("table").locator("button[aria-label='Next Steps']").all()
        
        num_securities = len(next_steps_buttons)
        print(f"Found {num_securities} securities to process")
        
        # Process each security
        for i in range(num_securities):
            # Re-query the buttons each time since DOM changes after closing modals
            next_steps_buttons = page.locator("button[aria-label='Next Steps']").all()
            button = next_steps_buttons[i]
            
            # Extract ticker symbol from the row
            # The row contains the ticker at the start, e.g., "ABT Next Steps ABBOTT LABS"
            row = button.locator("xpath=ancestor::tr")
            row_text = row.inner_text()
            # Ticker is typically the first word/token before "Next Steps"
            ticker = row_text.split()[0]
            print(f"\n--- Processing {ticker} ({i+1}/{num_securities}) ---")
            current_ticker = ticker
            
            try:
                # Click Next Steps button
                button.click()
                # time.sleep(1)
                
                # Click "Lot Details" in dropdown menu - skip if not available
                lot_details_link = page.locator("text=Lot Details").first
                try:
                    lot_details_link.wait_for(state="visible", timeout=5000)
                except:
                    print(f"  Skipping {ticker} - no lot details available")
                    # Close the dropdown
                    page.keyboard.press("Escape")
                    time.sleep(1)
                    continue
                
                lot_details_link.click()
                # time.sleep(1)
                
                # Wait for lot details popup to open when we click Export
                with context.expect_page() as page_info:
                    page.get_by_title("Export Lots").click()
                lot_page = page_info.value
                lot_page.wait_for_load_state('load')
                # time.sleep(1)
                
                # Click OK button to trigger download
                try:
                    lot_page.locator("a.button-primary:has-text('OK')").click()
                except:
                    try:
                        lot_page.locator("a[onclick*='ExportLotDetails']").click()
                    except:
                        lot_page.get_by_role("link", name="OK").click()
                
                time.sleep(2)  # Wait for download to complete
                print(f"Downloaded: {downloads[-1] if downloads else 'NONE'}")
                
                # Close popup and modal
                lot_page.close()
                # time.sleep(1)
                page.get_by_role("button", name="close modal").click()
                # time.sleep(1)
                
            except Exception as e:
                print(f"  Error processing {ticker}: {e}")
                # Try to clean up any open dialogs
                try:
                    page.keyboard.press("Escape")
                    time.sleep(1)
                except:
                    pass
                continue
        
        print(f"\n=== Complete! Downloaded {len(downloads)} files ===")
        for path in downloads:
            print(f"  - {path}")

if __name__ == "__main__":
    main()
