"""
Schwab Page Inspector

This script connects to an already-open Chrome browser and helps you inspect
the Schwab page structure to identify the correct selectors for scraping.

Usage:
1. Start Chrome with remote debugging:
   chrome.exe --remote-debugging-port=9222 --user-data-dir="C:\selenium\ChromeProfile"
   
2. Login to Schwab and navigate to the Positions/Cost Basis page

3. Run this script:
   python inspect_schwab.py
"""

import sys
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoSuchElementException
import json


class SchwabInspector:
    """Inspect Schwab page structure to find correct selectors."""
    
    def __init__(self, chrome_debugger_address="localhost:9222"):
        """Connect to existing Chrome session."""
        options = webdriver.ChromeOptions()
        options.add_experimental_option("debuggerAddress", chrome_debugger_address)
        
        try:
            self.driver = webdriver.Chrome(options=options)
            print("✓ Connected to Chrome")
        except Exception as e:
            print(f"✗ Could not connect to Chrome")
            print(f"  Error: {e}")
            print(f"\nStart Chrome with:")
            print(f'  chrome.exe --remote-debugging-port=9222 --user-data-dir="C:\\selenium\\ChromeProfile"')
            sys.exit(1)
    
    def print_page_info(self):
        """Print basic page information."""
        print("\n" + "="*70)
        print("PAGE INFORMATION")
        print("="*70)
        print(f"URL: {self.driver.current_url}")
        print(f"Title: {self.driver.title}")
        print()
    
    def find_tables(self):
        """Find all tables on the page."""
        print("\n" + "="*70)
        print("TABLES ON PAGE")
        print("="*70)
        
        tables = self.driver.find_elements(By.TAG_NAME, "table")
        print(f"Found {len(tables)} table(s)\n")
        
        for i, table in enumerate(tables, 1):
            print(f"--- Table {i} ---")
            try:
                table_classes = table.get_attribute("class")
                table_id = table.get_attribute("id")
                print(f"  ID: {table_id or '(none)'}")
                print(f"  Classes: {table_classes or '(none)'}")
                
                # Count rows
                rows = table.find_elements(By.TAG_NAME, "tr")
                print(f"  Rows: {len(rows)}")
                
                # Show first row content (header)
                if rows:
                    first_row = rows[0]
                    cells = first_row.find_elements(By.TAG_NAME, "th") + first_row.find_elements(By.TAG_NAME, "td")
                    if cells:
                        headers = [cell.text.strip() for cell in cells[:10]]  # First 10 columns
                        print(f"  First row: {headers}")
                
                # Show a sample data row
                if len(rows) > 1:
                    data_row = rows[1]
                    cells = data_row.find_elements(By.TAG_NAME, "td")
                    if cells:
                        data = [cell.text.strip()[:30] for cell in cells[:5]]  # First 5 columns, truncated
                        print(f"  Sample data: {data}")
                
            except Exception as e:
                print(f"  Error reading table: {e}")
            print()
        
        return tables
    
    def find_security_elements(self):
        """Try different strategies to find security/ticker elements."""
        print("\n" + "="*70)
        print("SEARCHING FOR SECURITY/TICKER ELEMENTS")
        print("="*70)
        
        # Common patterns for ticker symbols
        patterns = [
            # By text content pattern (ticker format)
            ("XPath - contains uppercase 2-5 chars", "//td[string-length(normalize-space(.)) >= 2 and string-length(normalize-space(.)) <= 5 and translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', '') = '']"),
            ("XPath - td with class containing 'symbol'", "//td[contains(@class, 'symbol')]"),
            ("XPath - td with class containing 'ticker'", "//td[contains(@class, 'ticker')]"),
            ("XPath - span with class containing 'symbol'", "//span[contains(@class, 'symbol')]"),
            ("CSS - .symbol", ".symbol"),
            ("CSS - .ticker", ".ticker"),
            ("CSS - [data-symbol]", "[data-symbol]"),
            ("CSS - td containing 'symbol' class", "td[class*='symbol']"),
            # By common Schwab patterns
            ("CSS - .positions-table-row", ".positions-table-row"),
            ("CSS - .position-row", ".position-row"),
            ("XPath - tr with security data", "//tr[@data-security-id]"),
            ("XPath - tr with position data", "//tr[contains(@class, 'position')]"),
        ]
        
        results = {}
        for description, selector in patterns:
            try:
                if selector.startswith("//"):
                    elements = self.driver.find_elements(By.XPATH, selector)
                else:
                    elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                
                if elements:
                    count = len(elements)
                    # Get sample text from first few elements
                    samples = []
                    for elem in elements[:5]:
                        text = elem.text.strip()
                        if text:
                            samples.append(text[:50])  # Truncate long text
                    
                    results[description] = {
                        'count': count,
                        'samples': samples,
                        'selector': selector
                    }
                    
                    print(f"\n✓ {description}")
                    print(f"  Selector: {selector}")
                    print(f"  Found: {count} element(s)")
                    if samples:
                        print(f"  Samples: {samples}")
                        
            except Exception as e:
                pass  # Selector didn't work, skip it
        
        if not results:
            print("\n✗ No ticker/security elements found with common patterns")
            print("  You may need to inspect the page manually with Chrome DevTools (F12)")
        
        return results
    
    def analyze_links(self):
        """Find all clickable links that might be securities."""
        print("\n" + "="*70)
        print("CLICKABLE LINKS")
        print("="*70)
        
        links = self.driver.find_elements(By.TAG_NAME, "a")
        print(f"Found {len(links)} link(s) total\n")
        
        # Filter to likely security links
        security_links = []
        for link in links:
            text = link.text.strip()
            href = link.get_attribute("href") or ""
            
            # Look for links that might be securities
            # (uppercase 2-5 characters, or contains "symbol" in href)
            if (text and 2 <= len(text) <= 5 and text.isupper()) or "symbol" in href.lower():
                security_links.append({
                    'text': text,
                    'href': href,
                    'classes': link.get_attribute("class")
                })
        
        if security_links:
            print(f"Found {len(security_links)} potential security link(s):\n")
            for i, link in enumerate(security_links[:10], 1):  # Show first 10
                print(f"{i}. Text: {link['text']}")
                print(f"   Href: {link['href'][:80]}")
                print(f"   Classes: {link['classes']}")
                print()
        else:
            print("No obvious security links found")
        
        return security_links
    
    def export_page_source(self, filename="schwab_page_source.html"):
        """Save the page source HTML for manual inspection."""
        print("\n" + "="*70)
        print("EXPORTING PAGE SOURCE")
        print("="*70)
        
        try:
            with open(filename, "w", encoding="utf-8") as f:
                f.write(self.driver.page_source)
            print(f"✓ Saved page source to: {filename}")
            print(f"  Open this file to inspect the HTML structure")
        except Exception as e:
            print(f"✗ Error saving page source: {e}")
    
    def test_selector(self, selector, by=By.CSS_SELECTOR):
        """Test a custom selector interactively."""
        print(f"\nTesting selector: {selector}")
        try:
            elements = self.driver.find_elements(by, selector)
            print(f"✓ Found {len(elements)} element(s)")
            
            for i, elem in enumerate(elements[:5], 1):
                print(f"\n  Element {i}:")
                print(f"    Tag: {elem.tag_name}")
                print(f"    Text: {elem.text.strip()[:100]}")
                print(f"    Classes: {elem.get_attribute('class')}")
                print(f"    ID: {elem.get_attribute('id')}")
                
                # Try to find child elements
                children = elem.find_elements(By.XPATH, "./*")
                if children:
                    print(f"    Children: {len(children)} element(s)")
                    for child in children[:3]:
                        print(f"      - {child.tag_name}: {child.text.strip()[:50]}")
            
            return True
        except Exception as e:
            print(f"✗ Error: {e}")
            return False
    
    def interactive_mode(self):
        """Interactive mode to test selectors."""
        print("\n" + "="*70)
        print("INTERACTIVE MODE")
        print("="*70)
        print("Test custom selectors. Type 'exit' to quit.\n")
        
        while True:
            try:
                selector = input("Enter CSS selector (or 'xpath:' prefix for XPath): ").strip()
                
                if selector.lower() in ('exit', 'quit', 'q'):
                    break
                
                if not selector:
                    continue
                
                if selector.startswith("xpath:"):
                    self.test_selector(selector[6:], By.XPATH)
                else:
                    self.test_selector(selector, By.CSS_SELECTOR)
                    
            except KeyboardInterrupt:
                print("\n")
                break
    
    def full_analysis(self):
        """Run complete page analysis."""
        self.print_page_info()
        self.find_tables()
        self.find_security_elements()
        self.analyze_links()
        self.export_page_source()
        
        print("\n" + "="*70)
        print("ANALYSIS COMPLETE")
        print("="*70)
        print("\nNext steps:")
        print("1. Review the output above to identify the correct selectors")
        print("2. Check schwab_page_source.html for detailed HTML structure")
        print("3. Use interactive mode to test specific selectors")
        print("4. Update schwab_scraper.py with the correct selectors")
    
    def close(self):
        """Note: Don't close driver, we're using existing session."""
        pass


def main():
    """Main entry point."""
    print("="*70)
    print("SCHWAB PAGE INSPECTOR")
    print("="*70)
    
    try:
        inspector = SchwabInspector()
        
        print("\nWhat would you like to do?")
        print("  1. Run full analysis (recommended)")
        print("  2. Interactive mode (test custom selectors)")
        print("  3. Export page source only")
        
        choice = input("\nChoice (1/2/3): ").strip()
        
        if choice == "1":
            inspector.full_analysis()
            
            # Offer interactive mode after analysis
            response = input("\nEnter interactive mode to test selectors? (y/n): ").strip().lower()
            if response == 'y':
                inspector.interactive_mode()
                
        elif choice == "2":
            inspector.print_page_info()
            inspector.interactive_mode()
            
        elif choice == "3":
            inspector.print_page_info()
            inspector.export_page_source()
        
        else:
            print("Invalid choice")
    
    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()
    
    print("\nDone. Chrome session remains open.")


if __name__ == "__main__":
    main()
