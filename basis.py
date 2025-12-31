import csv
import sys
import glob
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

"""
This script processes a Charles Schwab CSV export of unrealized lots against a Quicken export
to create a Quicken rebuild template CSV with individual "Add Shares" transactions for each lot.
To use this script:
1. Export your unrealized lots from Schwab as a CSV file.
2. Adjust the CONFIG dictionary below to match the column names in your Schwab CSV.
3. Run this script. It will generate a CSV file named 'quicken_rebuild_template.csv'.
4. Import the generated CSV into Quicken to rebuild your cost basis.

The Estate_logs.csv file has this format:
Field Names:
- Security Description
- Symbol 
- Account Name
- Acquired Date
- Quantity
- Cost Basis
- Total Cost

"""

# ==== CONFIGURE THESE TO MATCH YOUR SCHWAB CSV COLUMN NAMES ====
# Open your Schwab CSV in Excel/Notepad and adjust these strings
CONFIG = {
    "security_col": "Security Description",   # e.g. "Security Description" or "Description"
    "symbol_col": "Symbol",                  # e.g. "Symbol" or "Ticker"
    "account_col": "Account Name",           # If not present, you can set this to None
    "acquired_date_col": "Acquired Date",    # e.g. "Acquired Date" or "Purchase Date"
    "shares_col": "Quantity",                # e.g. "Quantity" or "Shares"
    "cost_basis_col": "Cost Basis",          # e.g. "Cost Basis" or "Total Cost"
}

INPUT_CSV = "schwab_unrealized_lots.csv"      # <-- change to your Schwab CSV file name
OUTPUT_CSV = "quicken_rebuild_template.csv"   # output file


@dataclass
class Lot:
    """Represents a single lot of shares within a security."""
    name: str
    quote: float = 0.0
    shares: float = 0.0
    market_value: float = 0.0
    cost_basis: float = 0.0


@dataclass
class Security:
    """Represents a security with its total holdings and individual lots."""
    name: str
    ticker: str
    quote: float = 0.0
    shares: float = 0.0
    market_value: float = 0.0
    cost_basis: float = 0.0
    lots: List[Lot] = field(default_factory=list)

# An account is a list of securities, indexed by ticker symbol
AccountSecurities = dict[str, Security ]


def parse_number(s: str) -> float:
    """Convert Schwab-style numbers like '1,234.56' or '1,234-' or '$1,234.56' to float."""
    if s is None:
        return 0.0
    s = s.strip()
    if not s or s == '*':
        return 0.0
    # Remove dollar signs and commas
    s = s.replace("$", "").replace(",", "")
    # Some brokers use trailing '-' for negatives
    if s.endswith("-") and s[:-1].replace(".", "", 1).isdigit():
        s = "-" + s[:-1]
    try:
        return float(s)
    except ValueError:
        return 0.0


def parse_quicken_lots(filepath: str) -> AccountSecurities:
    """
    Parse Quicken Quicken_Lots.csv file into a dictionary indexed by Ticker Symbol.
    
    Format:
    - First line: column headers
    - Security header line: Name, Ticker Symbol, Quote/Price, Shares, Market Value, Cost Basis
    - Lot lines: Name starts with "Lot", followed by lot-specific data
    
    Returns:
        dict[str, Security]: Dictionary mapping ticker symbols to Security objects
    """
    securities = {}
    current_security = None
    
    with open(filepath, "r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        
        for row in reader:
            name = row.get("Name", "").strip()
            ticker = row.get("Ticker Symbol", "").strip()
            
            # Skip empty rows
            if not name:
                continue
            
            # Check if this is a lot line (starts with "Lot")
            if name.startswith("Lot"):
                if current_security is not None:
                    # Parse lot data
                    lot = Lot(
                        name=name,
                        quote=parse_number(row.get("Quote/Price", "")),
                        shares=parse_number(row.get("Shares", "")),
                        market_value=parse_number(row.get("Market Value", "")),
                        cost_basis=parse_number(row.get("Cost Basis", "")),
                    )
                    current_security.lots.append(lot)
            else:
                # This is a new security header
                current_security = Security(
                    name=name,
                    ticker=ticker,
                    quote=parse_number(row.get("Quote/Price", "")),
                    shares=parse_number(row.get("Shares", "")),
                    market_value=parse_number(row.get("Market Value", "")),
                    cost_basis=parse_number(row.get("Cost Basis", "")),
                )
                if ticker:  # Only store if ticker exists
                    securities[ticker] = current_security
    
    return securities


@dataclass
class LotComparison:
    """Result of comparing a lot between Schwab and Quicken."""
    date: str
    schwab_shares: float = 0.0
    quicken_shares: float = 0.0
    schwab_cost_basis: float = 0.0
    quicken_cost_basis: float = 0.0
    status: str = ""  # "match", "missing_in_quicken", "missing_in_schwab", "mismatch"
    
    def __str__(self):
        if self.status == "match":
            return f"  OK {self.date}: {self.schwab_shares:.4f} shares @ ${self.schwab_cost_basis:.2f}"
        elif self.status == "missing_in_quicken":
            return f"  MISSING IN QUICKEN - {self.date}: Schwab has {self.schwab_shares:.4f} shares @ ${self.schwab_cost_basis:.2f}"
        elif self.status == "missing_in_schwab":
            return f"  MISSING IN SCHWAB - {self.date}: Quicken has {self.quicken_shares:.4f} shares @ ${self.quicken_cost_basis:.2f}"
        else:  # mismatch
            shares_diff = f" shares: {self.schwab_shares:.4f} vs {self.quicken_shares:.4f}" if abs(self.schwab_shares - self.quicken_shares) > 0.01 else ""
            cost_diff = f" cost: ${self.schwab_cost_basis:.2f} vs ${self.quicken_cost_basis:.2f}" if abs(self.schwab_cost_basis - self.quicken_cost_basis) > 0.01 else ""
            return f"  MISMATCH - {self.date}:{shares_diff}{cost_diff}"


def process_stock_lots(securities: AccountSecurities, stock_lots_file: str) -> List[LotComparison]:
    """
    Compare lots between Schwab CSV export and Quicken account data.
    
    Args:
        securities: Dictionary of Security objects from Quicken, indexed by ticker
        stock_lots_file: Path to Schwab CSV file (e.g., 'aapl.csv')
    
    Returns:
        List of LotComparison objects showing matches and discrepancies
    """
    input_path = Path(stock_lots_file)
    if not input_path.exists():
        raise SystemExit(f"Input file not found: {input_path.resolve()}")
    
    # Extract ticker from filename (e.g., 'aapl.csv' -> 'AAPL')
    ticker = input_path.stem.upper()
    
    print(f"\n=== Comparing {ticker} lots: Schwab vs Quicken ===\n")
    
    # Get Quicken security data
    quicken_security = securities.get(ticker)
    if not quicken_security:
        print(f"WARNING: {ticker} not found in Quicken account")
        quicken_security = Security(name=ticker, ticker=ticker)
    
    # Parse Schwab CSV
    schwab_lots = {}
    with input_path.open("r", newline="", encoding="utf-8-sig") as f:
        # Skip first two header rows in Schwab CSV
        next(f)  # Skip title row
        next(f)  # Skip blank row
        reader = csv.DictReader(f)
        
        for row in reader:
            open_date = row.get("Open Date", "").strip()
            quantity = row.get("Quantity", "").strip()
            cost_basis = row.get("Cost Basis", "").strip()
            
            # Skip header rows, empty rows, or total rows
            if not open_date or not quantity or open_date == "Open Date" or open_date == "Total":
                continue
            
            shares = parse_number(quantity)
            cost = parse_number(cost_basis)
            
            if abs(shares) < 1e-9:  # Skip zero-share rows
                continue
            
            # Normalize date format: remove leading zeros (e.g., "02/16/2024" -> "2/16/2024")
            parts = open_date.split("/")
            if len(parts) == 3:
                normalized_date = f"{int(parts[0])}/{int(parts[1])}/{parts[2]}"
                schwab_lots[normalized_date] = {"shares": shares, "cost_basis": cost}
    
    # Parse Quicken lots - extract date from "Lot MM/DD/YYYY" format
    quicken_lots = {}
    for lot in quicken_security.lots:
        # Extract date from lot name (e.g., "Lot 12/13/2004" -> "12/13/2004")
        if lot.name.startswith("Lot "):
            date_str = lot.name[4:].strip()
            # Normalize date format: remove leading zeros for comparison (e.g., "02/16/2024" -> "2/16/2024")
            # This handles cases where Schwab uses M/D/YYYY and Quicken uses MM/DD/YYYY
            parts = date_str.split("/")
            if len(parts) == 3:
                normalized_date = f"{int(parts[0])}/{int(parts[1])}/{parts[2]}"
                quicken_lots[normalized_date] = {"shares": lot.shares, "cost_basis": lot.cost_basis}
    
    # Compare lots
    comparisons = []
    all_dates = set(schwab_lots.keys()) | set(quicken_lots.keys())
    
    for date in sorted(all_dates):
        schwab_data = schwab_lots.get(date, {})
        quicken_data = quicken_lots.get(date, {})
        
        schwab_shares = schwab_data.get("shares", 0.0)
        schwab_cost = schwab_data.get("cost_basis", 0.0)
        quicken_shares = quicken_data.get("shares", 0.0)
        quicken_cost = quicken_data.get("cost_basis", 0.0)
        
        # Determine status
        if date in schwab_lots and date not in quicken_lots:
            status = "missing_in_quicken"
        elif date not in schwab_lots and date in quicken_lots:
            status = "missing_in_schwab"
        elif abs(schwab_shares - quicken_shares) > 0.01 or abs(schwab_cost - quicken_cost) > 0.01:
            status = "mismatch"
        else:
            status = "match"
        
        comparison = LotComparison(
            date=date,
            schwab_shares=schwab_shares,
            quicken_shares=quicken_shares,
            schwab_cost_basis=schwab_cost,
            quicken_cost_basis=quicken_cost,
            status=status
        )
        comparisons.append(comparison)
    
    # Print summary
    matches = sum(1 for c in comparisons if c.status == "match")
    mismatches = sum(1 for c in comparisons if c.status == "mismatch")
    missing_quicken = sum(1 for c in comparisons if c.status == "missing_in_quicken")
    missing_schwab = sum(1 for c in comparisons if c.status == "missing_in_schwab")
    
    print(f"Total lots compared: {len(comparisons)}")
    print(f"  Matches: {matches}")
    print(f"  Mismatches: {mismatches}")
    print(f"  Missing in Quicken: {missing_quicken}")
    print(f"  Missing in Schwab: {missing_schwab}")
    print()
    
    # Print details for non-matching lots
    if mismatches + missing_quicken + missing_schwab > 0:
        print("Discrepancies:")
        for comp in comparisons:
            if comp.status != "match":
                print(comp)
    else:
        print("All lots match!")
    
    # Print totals comparison
    schwab_total_shares = sum(lot["shares"] for lot in schwab_lots.values())
    schwab_total_cost = sum(lot["cost_basis"] for lot in schwab_lots.values())
    quicken_total_shares = quicken_security.shares
    quicken_total_cost = quicken_security.cost_basis
    
    print(f"\nTotals:")
    print(f"  Schwab:  {schwab_total_shares:.4f} shares, ${schwab_total_cost:.2f} cost basis")
    print(f"  Quicken: {quicken_total_shares:.4f} shares, ${quicken_total_cost:.2f} cost basis")
    
    if abs(schwab_total_shares - quicken_total_shares) > 0.01:
        print(f"  WARNING: Share difference: {schwab_total_shares - quicken_total_shares:.4f}")
    if abs(schwab_total_cost - quicken_total_cost) > 0.01:
        print(f"  WARNING: Cost basis difference: ${schwab_total_cost - quicken_total_cost:.2f}")
    
    return comparisons


def stock_lots(securities: AccountSecurities, stock_lots_file: str):
    input_path = Path(stock_lots_file)
    if not input_path.exists():
        raise SystemExit(f"Input file not found: {input_path.resolve()}")

    with input_path.open("r", newline="", encoding="utf-8-sig") as f_in:
        reader = csv.DictReader(f_in)

        # Check that required columns exist
        missing = [
            col_name for col_name in CONFIG.values()
            if col_name is not None and col_name not in reader.fieldnames
        ]
        if missing:
            raise SystemExit(
                "These configured columns were not found in the CSV header:\n"
                + "\n".join(f"  - {m}" for m in missing)
                + "\n\nEdit CONFIG at the top of the script to match the actual column names."
            )

        # Prepare output
        with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f_out:
            fieldnames = [
                "#",
                "Security",
                "Symbol",
                "Schwab Account",
                "Acquisition Date",
                "Shares",
                "Cost Basis ($)",
                "Transaction Type (Buy / Reinvest / Transfer)",
                "Quicken Action (Add Shares / Edit Buy)",
                "Memo / Notes",
            ]
            writer = csv.DictWriter(f_out, fieldnames=fieldnames)
            writer.writeheader()

            # We'll also accumulate totals per (account, security, symbol)
            totals = {}

            row_num = 0
            for row in reader:
                security = row.get(CONFIG["security_col"], "").strip()
                symbol = row.get(CONFIG["symbol_col"], "").strip()
                account = (
                    row.get(CONFIG["account_col"], "").strip()
                    if CONFIG["account_col"] is not None
                    else ""
                )
                acquired_date = row.get(CONFIG["acquired_date_col"], "").strip()
                shares_str = row.get(CONFIG["shares_col"], "").strip()
                cost_str = row.get(CONFIG["cost_basis_col"], "").strip()

                # Skip blank or summary rows
                if not security or not shares_str:
                    continue

                shares = parse_number(shares_str)
                cost_basis = parse_number(cost_str)

                # Skip rows with zero shares (often headers/totals)
                if abs(shares) < 1e-9:
                    continue

                row_num += 1

                # Guess transaction type from description; user can edit
                if "reinvest" in security.lower():
                    txn_type = "Reinvest"
                    memo = "Dividend reinvestment (from Schwab CSV)"
                else:
                    txn_type = "Buy"
                    memo = "Rebuild basis from Schwab CSV"

                writer.writerow({
                    "#": row_num,
                    "Security": security,
                    "Symbol": symbol,
                    "Schwab Account": account,
                    "Acquisition Date": acquired_date,
                    "Shares": f"{shares:.6f}",
                    "Cost Basis ($)": f"{cost_basis:.2f}",
                    "Transaction Type (Buy / Reinvest / Transfer)": txn_type,
                    "Quicken Action (Add Shares / Edit Buy)": "Add Shares",
                    "Memo / Notes": memo,
                })

                key = (account, security, symbol)
                if key not in totals:
                    totals[key] = {"shares": 0.0, "cost": 0.0}
                totals[key]["shares"] += shares
                totals[key]["cost"] += cost_basis

            # Add blank line then totals summary
            writer.writerow({})
            writer.writerow({
                "#": "",
                "Security": "=== TOTALS PER SECURITY (for Remove Shares & verification) ===",
            })

            for (account, security, symbol), agg in sorted(totals.items()):
                row_num += 1
                writer.writerow({
                    "#": row_num,
                    "Security": security,
                    "Symbol": symbol,
                    "Schwab Account": account,
                    "Acquisition Date": "",
                    "Shares": f"{agg['shares']:.6f}",
                    "Cost Basis ($)": f"{agg['cost']:.2f}",
                    "Transaction Type (Buy / Reinvest / Transfer)": "TOTAL",
                    "Quicken Action (Add Shares / Edit Buy)": "Use for Remove Shares total",
                    "Memo / Notes": "Use this as total shares when entering Remove Shares in Quicken",
                })

    print(f"Done. Wrote template to: {Path(OUTPUT_CSV).resolve()}")


if __name__ == "__main__":
    quicken_lots = 'Quicken_Lots.csv'
    lots_dir = 'lots'
    
    # Get list of stock CSV files to process
    if len(sys.argv) > 1:
        # Use command-line arguments
        stock_files = sys.argv[1:]
    else:
        # Auto-discover CSV files in lots subdirectory
        lots_path = Path(lots_dir)
        if not lots_path.exists():
            print(f"ERROR: Lots directory not found: {lots_path.resolve()}")
            print(f"Please create a '{lots_dir}' subdirectory and place Schwab CSV files there.")
            print("Usage: python basis.py [ticker1.csv ticker2.csv ...]")
            sys.exit(1)
        
        stock_files = glob.glob(f'{lots_dir}/*.csv')
        if not stock_files:
            print(f"No CSV files found in {lots_dir}/ directory.")
            print(f"Please place Schwab lot CSV files in the '{lots_dir}' subdirectory.")
            print("Usage: python basis.py [ticker1.csv ticker2.csv ...]")
            sys.exit(1)
    
    # Parse Quicken lots file
    quicken_lots_file = Path(quicken_lots)
    if not quicken_lots_file.exists():
        print(f"ERROR: Quicken lots file not found: {quicken_lots_file.resolve()}")
        print(f"Please export from Quicken: Investments → Portfolio → Expand lots → Export CSV")
        sys.exit(1)
    
    print(f"\n=== Parsing {quicken_lots_file} ===")
    securities = parse_quicken_lots(str(quicken_lots_file))
    print(f"Found {len(securities)} securities in Quicken\n")
    
    for ticker, data in securities.items():
        print(f"{ticker}: {data.name}")
        print(f"  Total Shares: {data.shares:.2f}")
        print(f"  Total Cost Basis: ${data.cost_basis:.2f}")
        print(f"  Market Value: ${data.market_value:.2f}")
        print(f"  Number of Lots: {len(data.lots)}")
        
        # Show first few lots as example
        for i, lot in enumerate(data.lots[:3]):
            print(f"    {lot.name}: {lot.shares:.2f} shares @ ${lot.cost_basis:.2f}")
        if len(data.lots) > 3:
            print(f"    ... and {len(data.lots) - 3} more lots")
        print()
    
    # Process each stock CSV file
    print(f"\n=== Processing {len(stock_files)} stock file(s) ===")
    for stock_file in stock_files:
        stock_path = Path(stock_file)
        if not stock_path.exists():
            print(f"WARNING: File not found, skipping: {stock_file}")
            continue
        
        try:
            process_stock_lots(securities, stock_file)
            print("\n" + "="*60 + "\n")
        except Exception as e:
            print(f"ERROR processing {stock_file}: {e}")
            print("\n" + "="*60 + "\n")
    
    # Compare ticker symbols between Quicken and CSV files
    print("\n" + "="*60)
    print("TICKER SYMBOL COMPARISON")
    print("="*60)
    
    # Get tickers from Quicken
    quicken_tickers = set(securities.keys())
    
    # Get tickers from CSV filenames (stem without path)
    csv_tickers = set()
    for stock_file in stock_files:
        ticker = Path(stock_file).stem.upper()
        csv_tickers.add(ticker)
    
    # Calculate differences
    only_in_quicken = quicken_tickers - csv_tickers
    only_in_csv = csv_tickers - quicken_tickers
    in_both = quicken_tickers & csv_tickers
    
    print(f"\nTickers in Quicken: {len(quicken_tickers)}")
    print(f"Tickers in CSV files: {len(csv_tickers)}")
    print(f"Tickers in both: {len(in_both)}")
    
    if only_in_quicken:
        print(f"\nTickers ONLY in Quicken ({len(only_in_quicken)}):")
        for ticker in sorted(only_in_quicken):
            security = securities[ticker]
            print(f"  {ticker}: {security.name}")
    
    if only_in_csv:
        print(f"\nTickers ONLY in CSV files ({len(only_in_csv)}):")
        for ticker in sorted(only_in_csv):
            print(f"  {ticker}")
    
    if not only_in_quicken and not only_in_csv:
        print("\n✓ All tickers match perfectly!")
    
    print("\n" + "="*60)
     