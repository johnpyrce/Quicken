#!/usr/bin/env python3
"""
schwab_lots_to_quicken_checklist.py

Converts a Schwab "Lot Details" CSV export (report-style, per-security like your AAPL.csv)
into:
  1) quicken_entry_checklist.csv  (one row per tax lot to enter in Quicken as Add Shares)
  2) quicken_symbol_summary.csv   (totals by symbol)

Usage:
  py schwab_lots_to_quicken_checklist.py AAPL.csv
"""

from __future__ import annotations

import sys
import csv
import re
from pathlib import Path
from datetime import datetime
import pandas as pd


def parse_symbol_from_title(title_cell: str) -> str | None:
    """
    Example title cell: "AAPL Lot Details for ... as of 12:18 PM ET, 12/23/2025"
    """
    if not title_cell:
        return None
    m = re.match(r"\s*([A-Z][A-Z0-9\.\-]{0,9})\s+Lot\s+Details\b", title_cell.strip())
    return m.group(1) if m else None


def find_header_row(rows: list[list[str]]) -> int | None:
    """
    Find the row index that contains "Open Date" and "Quantity" and "Cost Basis".
    """
    for i, r in enumerate(rows):
        norm = [c.strip().lower() for c in r]
        if ("open date" in norm) and ("quantity" in norm) and ("cost basis" in norm):
            return i
    return None


def to_float_money_or_num(x: str) -> float | None:
    if x is None:
        return None
    s = str(x).strip()
    if s == "":
        return None
    s = s.replace("$", "").replace(",", "")
    if re.match(r"^\(.*\)$", s):
        s = "-" + s.strip("()")
    try:
        return float(s)
    except ValueError:
        return None


def parse_date_mmddyyyy(x: str) -> str | None:
    s = str(x).strip()
    if s == "":
        return None
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d"):
        try:
            d = datetime.strptime(s, fmt).date()
            return d.strftime("%m/%d/%Y")  # Quicken-friendly
        except ValueError:
            pass
    # last resort
    dt = pd.to_datetime(s, errors="coerce")
    if pd.isna(dt):
        return None
    return dt.strftime("%m/%d/%Y")


def process_file(infile: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Process a single Schwab CSV file and return checklist and summary DataFrames."""

    # Read raw rows (because Schwab report exports have title rows before headers)
    with infile.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        rows = [row for row in reader]

    if not rows:
        print("CSV is empty.")
        sys.exit(2)

    # Symbol typically lives in the first cell of the first row
    symbol = parse_symbol_from_title(rows[0][0] if rows[0] else "")
    if not symbol:
        symbol = "UNKNOWN"  # still produce output; user can edit later

    header_i = find_header_row(rows)
    if header_i is None:
        print("Could not find the header row containing: Open Date, Quantity, Cost Basis")
        print("First 5 rows seen:")
        for r in rows[:5]:
            print(r)
        sys.exit(2)

    headers = rows[header_i]
    data_rows = rows[header_i + 1 :]

    # Build DataFrame
    df = pd.DataFrame(data_rows, columns=headers)

    # Required columns in your Schwab format
    date_col = "Open Date"
    qty_col = "Quantity"
    cost_col = "Cost Basis"

    # Clean/convert
    out = pd.DataFrame({
        "Symbol": symbol,
        "Shares": df[qty_col].map(to_float_money_or_num),
        "AcqDate": df[date_col].map(parse_date_mmddyyyy),
        "TotalCostBasis": df[cost_col].map(to_float_money_or_num),
    })

    # Drop bad/blank rows
    out = out.dropna(subset=["Shares", "AcqDate", "TotalCostBasis"])
    out = out[out["Shares"] != 0]

    if out.empty:
        print(f"  WARNING: No usable lots found")
        return None, None

    out["PerShareBasis"] = (out["TotalCostBasis"] / out["Shares"]).round(6)

    # Sort lots for easy entry
    out = out.sort_values(["Symbol", "AcqDate", "Shares"], ascending=[True, True, False])  # type: ignore[call-overload]

    # Checklist output (what you’ll type into Quicken)
    checklist = out.copy()
    checklist.insert(0, "Done", "")
    checklist.insert(1, "QuickenAction", "Add Shares")
    checklist["Memo"] = "Rebuild from Schwab tax lot CSV"

    checklist_cols = ["Done", "QuickenAction", "Symbol", "Shares", "AcqDate",
                      "TotalCostBasis", "PerShareBasis", "Memo"]
    checklist = checklist[checklist_cols]

    # Summary output (sanity check vs Schwab totals)
    summary = (
        out.groupby("Symbol", as_index=False)
           .agg(TotalShares=("Shares", "sum"),
                TotalCostBasis=("TotalCostBasis", "sum"),
                LotCount=("Shares", "count"))
    )
    summary["PerShareAvgBasis"] = (summary["TotalCostBasis"] / summary["TotalShares"]).round(6)

    return checklist, summary


def main():
    lots_dir = Path("lots")
    
    # Check if lots directory exists
    if not lots_dir.exists():
        print(f"ERROR: '{lots_dir}' directory not found.")
        print(f"Please create a '{lots_dir}' subdirectory and place Schwab CSV files there.")
        sys.exit(2)
    
    # Find all CSV files
    csv_files = list(lots_dir.glob("*.csv"))
    if not csv_files:
        print(f"No CSV files found in '{lots_dir}/' directory.")
        sys.exit(2)
    
    print(f"\n{'='*60}")
    print(f"Processing {len(csv_files)} file(s) from '{lots_dir}/'")
    print(f"{'='*60}\n")
    
    all_checklists = []
    all_summaries = []
    
    for infile in sorted(csv_files):
        print(f"Processing: {infile.name}")
        
        try:
            checklist, summary = process_file(infile)
            
            if checklist is not None and summary is not None:
                all_checklists.append(checklist)
                all_summaries.append(summary)
                
                symbol = summary.loc[0, "Symbol"]
                lots = summary.loc[0, "LotCount"]
                shares = summary.loc[0, "TotalShares"]
                cost = summary.loc[0, "TotalCostBasis"]
                
                print(f"  ✓ {symbol}: {lots} lots, {shares:.6f} shares, ${cost:,.2f} cost basis")
        
        except Exception as e:
            print(f"  ✗ Error: {e}")
        
        print()
    
    if not all_checklists:
        print("No valid data processed from any files.")
        sys.exit(2)
    
    # Combine all checklists and summaries
    combined_checklist = pd.concat(all_checklists, ignore_index=True)
    combined_summary = pd.concat(all_summaries, ignore_index=True)
    
    # Sort combined checklist
    combined_checklist = combined_checklist.sort_values(
        ["Symbol", "AcqDate", "Shares"], 
        ascending=[True, True, False]
    )  # type: ignore[call-overload]
    
    # Save combined outputs
    checklist_path = Path("quicken_entry_checklist.csv")
    summary_path = Path("quicken_symbol_summary.csv")
    
    combined_checklist.to_csv(checklist_path, index=False)
    combined_summary.to_csv(summary_path, index=False)
    
    print(f"{'='*60}")
    print("COMBINED OUTPUT")
    print(f"{'='*60}")
    print(f"Wrote: {checklist_path.resolve()}")
    print(f"Wrote: {summary_path.resolve()}")
    print(f"\nTotal: {len(combined_summary)} symbols, {len(combined_checklist)} lots")
    print(f"Total shares: {combined_summary['TotalShares'].sum():.6f}")
    print(f"Total cost basis: ${combined_summary['TotalCostBasis'].sum():,.2f}")


if __name__ == "__main__":
    main()
