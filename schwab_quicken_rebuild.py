#!/usr/bin/env python3
"""
schwab_quicken_rebuild.py

Does ALL the upgrades:

1) Reads ONE Schwab "Lot Details" CSV per security from ./lots/   (no parameter)
2) Groups lots into 4 buckets:
      VERY_OLD  : acq < 2020-01-01
      OLD       : 2020-01-01 <= acq < 2023-01-01
      LONG_RECENT: acq >= 2023-01-01 and acq < (as_of_date - 365 days)
      SHORT_TERM: acq >= (as_of_date - 365 days)
   - as_of_date is parsed from each file’s title line (falls back to today if missing)

3) Outputs:
   - quicken_addshares_checklist.csv     (what you key into Quicken: Add Shares)
   - quicken_security_subtotals.csv      (per-security totals + counts)
   - schwab_quicken_validation_report.csv (diff vs Schwab positions summary, if found)

4) Validation (optional but automatic):
   - If a Schwab "Positions/Summary" CSV exists in the current folder, named:
        schwab_positions.csv
     …it will compare per-symbol Shares + Cost Basis totals and write a report.
   - If not found, it still writes the other files and notes validation was skipped.

How to run:
  Put your Schwab per-security lot CSVs in ./lots/*.csv
  (Optional) Put Schwab positions export in ./schwab_positions.csv
  Run: python schwab_quicken_rebuild.py
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import datetime, date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Dict, List, Tuple, Optional


# -------------------------
# Fixed locations / default outputs (per your request)
# -------------------------

LOTS_DIR = Path("lots")  # required name; no parameter
POSITIONS_SUMMARY_FILE = Path("schwab_positions.csv")  # optional; auto-used if present

OUT_CHECKLIST = Path("quicken_addshares_checklist.csv")
OUT_SUBTOTALS = Path("quicken_security_subtotals.csv")
OUT_VALIDATION = Path("schwab_quicken_validation_report.csv")

MEMO_TEXT = "Schwab rebuild – grouped lots"


# -------------------------
# Schwab lot-details column names (from your AAPL file)
# -------------------------
DATE_COL = "Open Date"
SHARES_COL = "Quantity"
COST_COL = "Cost Basis"


# -------------------------
# Bucket definitions
# -------------------------
BREAK_1 = date(2020, 1, 1)
BREAK_2 = date(2023, 1, 1)

BUCKET_NAMES = ["VERY_OLD", "OLD", "LONG_RECENT", "SHORT_TERM"]

# Quicken acquisition dates used for the aggregated Add Shares lines.
# Note: SHORT_TERM uses the file’s as_of_date (so it stays obviously short-term).
QUICKEN_DATES_FIXED = {
    "VERY_OLD": date(2019, 12, 31),
    "OLD": date(2022, 12, 31),
    "LONG_RECENT": date(2024, 12, 31),
    # SHORT_TERM: dynamic per file
}


# -------------------------
# Parsing helpers
# -------------------------

def parse_date_any(s: str) -> date:
    s = (s or "").strip()
    if not s:
        raise ValueError("Empty date")
    fmts = ["%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%m-%d-%Y", "%Y/%m/%d"]
    for f in fmts:
        try:
            return datetime.strptime(s, f).date()
        except ValueError:
            pass
    # last resort: try extracting MM/DD/YYYY
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)
    if m:
        return date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
    raise ValueError(f"Unrecognized date format: {s!r}")


def parse_decimal_any(s: str) -> Decimal:
    s = (s or "").strip()
    if not s:
        raise ValueError("Empty numeric field")
    
    # Handle placeholder values from Schwab exports
    if s in ("--", "N/A", "n/a", "*"):
        return Decimal(0)

    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg = True
        s = s[1:-1].strip()

    s = s.replace("$", "").replace(",", "").strip()
    try:
        v = Decimal(s)
    except InvalidOperation:
        raise ValueError(f"Unrecognized numeric format: {s!r}")

    return -v if neg else v


def mmddyyyy(d: date) -> str:
    return d.strftime("%m/%d/%Y")


# -------------------------
# Data
# -------------------------

@dataclass(frozen=True)
class Lot:
    symbol: str
    acq_date: date
    shares: Decimal
    cost: Decimal


# -------------------------
# Schwab lot CSV parsing (your per-security format)
# -------------------------

def extract_symbol_from_title(title_line: str) -> str:
    """
    Example: "AAPL Lot Details for ... as of 12:18 PM ET, 12/23/2025"
    """
    title_line = (title_line or "").strip()
    m = re.match(r"^\s*([A-Z0-9.\-]+)\s+Lot Details\b", title_line)
    if m:
        return m.group(1).upper()
    # fallback: first token
    tok = title_line.split(" ", 1)[0] if title_line else ""
    return tok.upper() if tok else "UNKNOWN"


def extract_as_of_date_from_title(title_line: str) -> Optional[date]:
    """
    Tries to find "... as of ..., 12/23/2025" or any MM/DD/YYYY in the title line.
    """
    title_line = (title_line or "").strip()
    m = re.search(r"(\d{1,2}/\d{1,2}/\d{4})", title_line)
    if m:
        try:
            return parse_date_any(m.group(1))
        except Exception:
            return None
    return None


def find_header_row(rows: List[List[str]]) -> int:
    """Find the row containing the required column headers (in any order)."""
    for i, r in enumerate(rows):
        # Check if this row contains all required column names
        row_stripped = [c.strip() for c in r]
        if DATE_COL in row_stripped and SHARES_COL in row_stripped and COST_COL in row_stripped:
            return i
    # Debug: show what we found
    print(f"  DEBUG: Could not find header. First 5 rows:")
    for i, r in enumerate(rows[:5]):
        print(f"    Row {i}: {r[:5] if len(r) >= 5 else r}")
    raise ValueError(f"Could not find header row containing '{DATE_COL}', '{SHARES_COL}', and '{COST_COL}'.")


def load_lots_file(path: Path) -> Tuple[List[Lot], date]:
    """
    Returns (lots, as_of_date_used)
    """
    # Read entire CSV with csv.reader to handle the title row + header row
    text = path.read_text(encoding="utf-8-sig")
    rows = list(csv.reader(text.splitlines()))
    if not rows:
        return ([], date.today())

    title_line = rows[0][0] if rows[0] else ""
    symbol = extract_symbol_from_title(title_line)
    as_of = extract_as_of_date_from_title(title_line) or date.today()

    hdr_idx = find_header_row(rows)
    header = rows[hdr_idx]
    idx = {h.strip(): i for i, h in enumerate(header)}

    for needed in (DATE_COL, SHARES_COL, COST_COL):
        if needed not in idx:
            raise ValueError(f"{path.name}: Missing expected column {needed!r}. Found: {header}")

    lots: List[Lot] = []
    for r in rows[hdr_idx + 1 :]:
        if not r or all((c or "").strip() == "" for c in r):
            continue

        d_raw = r[idx[DATE_COL]].strip() if idx[DATE_COL] < len(r) else ""
        if not d_raw:
            continue
        
        # Skip summary/total rows
        if d_raw.lower() in ("total", "totals", "grand total", "summary"):
            continue

        try:
            acq = parse_date_any(d_raw)
        except ValueError:
            # Skip rows with invalid dates (likely summary rows)
            continue
            
        shares_raw = r[idx[SHARES_COL]] if idx[SHARES_COL] < len(r) else ""
        cost_raw = r[idx[COST_COL]] if idx[COST_COL] < len(r) else ""

        shares = parse_decimal_any(shares_raw)
        cost = parse_decimal_any(cost_raw)

        if shares == 0:
            continue

        lots.append(Lot(symbol=symbol, acq_date=acq, shares=shares, cost=cost))

    return (lots, as_of)


# -------------------------
# Bucketing
# -------------------------

def bucket_name_for(acq: date, as_of: date) -> str:
    cutoff_short = as_of - timedelta(days=365)

    if acq < BREAK_1:
        return "VERY_OLD"
    if acq < BREAK_2:
        return "OLD"
    if acq < cutoff_short:
        return "LONG_RECENT"
    return "SHORT_TERM"


def quicken_bucket_date(bucket: str, as_of: date) -> date:
    if bucket == "SHORT_TERM":
        return as_of
    return QUICKEN_DATES_FIXED[bucket]


# -------------------------
# Aggregation
# -------------------------

@dataclass
class AggRow:
    symbol: str
    bucket: str
    shares: Decimal
    cost: Decimal
    quicken_date: date
    lots_count: int


def aggregate_all(files: List[Path]) -> Tuple[List[AggRow], Dict[str, Dict[str, Decimal]], Dict[str, Dict[str, int]]]:
    """
    Returns:
      - list of aggregated bucket rows (for checklist)
      - per-security totals {"shares":..., "cost":...}
      - per-security counts {"lot_files":..., "lots":..., "bucket_rows":...}
    """
    agg: Dict[Tuple[str, str], Dict[str, Decimal]] = {}
    counts: Dict[Tuple[str, str], int] = {}
    quick_dates: Dict[Tuple[str, str], date] = {}
    per_symbol_totals: Dict[str, Dict[str, Decimal]] = {}
    per_symbol_counts: Dict[str, Dict[str, int]] = {}

    for f in files:
        lots, as_of = load_lots_file(f)
        if not lots:
            continue

        sym = lots[0].symbol if lots else "UNKNOWN"
        per_symbol_counts.setdefault(sym, {"lot_files": 0, "lots": 0, "bucket_rows": 0})
        per_symbol_counts[sym]["lot_files"] += 1

        per_symbol_totals.setdefault(sym, {"shares": Decimal("0"), "cost": Decimal("0")})

        for lot in lots:
            b = bucket_name_for(lot.acq_date, as_of)
            key = (lot.symbol, b)
            agg.setdefault(key, {"shares": Decimal("0"), "cost": Decimal("0")})
            counts[key] = counts.get(key, 0) + 1

            agg[key]["shares"] += lot.shares
            agg[key]["cost"] += lot.cost

            # Remember the quicken date per (symbol,bucket). For SHORT_TERM, we want the *latest* as_of date encountered.
            qd = quicken_bucket_date(b, as_of)
            prev = quick_dates.get(key)
            if prev is None or (b == "SHORT_TERM" and qd > prev):
                quick_dates[key] = qd

            per_symbol_totals[lot.symbol]["shares"] += lot.shares
            per_symbol_totals[lot.symbol]["cost"] += lot.cost
            per_symbol_counts[lot.symbol]["lots"] += 1

    # Build AggRow list
    out_rows: List[AggRow] = []
    for (sym, b), sums in agg.items():
        sh = sums["shares"]
        if sh == 0:
            continue
        out_rows.append(
            AggRow(
                symbol=sym,
                bucket=b,
                shares=sh,
                cost=sums["cost"],
                quicken_date=quick_dates[(sym, b)],
                lots_count=counts.get((sym, b), 0),
            )
        )

    # count bucket rows per symbol
    for r in out_rows:
        per_symbol_counts.setdefault(r.symbol, {"lot_files": 0, "lots": 0, "bucket_rows": 0})
        per_symbol_counts[r.symbol]["bucket_rows"] += 1

    # Sort rows for easy manual entry
    bucket_order = {name: i for i, name in enumerate(BUCKET_NAMES)}
    out_rows.sort(key=lambda r: (r.symbol, bucket_order.get(r.bucket, 99)))

    return out_rows, per_symbol_totals, per_symbol_counts


# -------------------------
# Positions summary validation
# -------------------------

def detect_positions_columns(header: List[str]) -> Tuple[str, str, str]:
    """
    Try to find columns for:
      symbol/ticker, shares/quantity, total cost basis
    """
    h = [c.strip() for c in header]
    lower_map = {c.lower(): c for c in h}

    def pick(candidates: List[str]) -> Optional[str]:
        for c in candidates:
            if c.lower() in lower_map:
                return lower_map[c.lower()]
        return None

    sym = pick(["Symbol", "Ticker", "Security", "Security Symbol"])
    qty = pick(["Quantity", "Qty", "Qty (Quantity)", "Shares", "Units", "Position", "Total Shares"])
    cost = pick(["Cost Basis", "Total Cost Basis", "Total Cost", "Cost"])

    if not sym or not qty or not cost:
        raise ValueError(
            "Could not detect required columns in schwab_positions.csv.\n"
            f"Found header: {header}\n"
            "Need something like Symbol/Ticker, Quantity/Shares, Cost Basis."
        )
    return sym, qty, cost


def load_positions_summary(path: Path) -> Dict[str, Dict[str, Decimal]]:
    """
    Returns per-symbol totals from Schwab positions CSV:
      { "AAPL": {"shares":..., "cost":...}, ... }
    """
    out: Dict[str, Dict[str, Decimal]] = {}
    
    # Read raw rows to skip title/blank rows like lot files
    text = path.read_text(encoding="utf-8-sig")
    rows = list(csv.reader(text.splitlines()))
    if not rows:
        return out
    
    # Find header row containing Symbol, Quantity, Cost Basis
    header_idx = None
    for i, r in enumerate(rows):
        row_stripped = [c.strip() for c in r]
        # Look for a row that has Symbol-like and Quantity-like columns
        has_symbol = any("symbol" in c.lower() or "ticker" in c.lower() for c in row_stripped)
        has_qty = any("quantity" in c.lower() or "qty" in c.lower() or "shares" in c.lower() for c in row_stripped)
        has_cost = any("cost basis" in c.lower() or "cost" in c.lower() for c in row_stripped)
        if has_symbol and has_qty and has_cost:
            header_idx = i
            break
    
    if header_idx is None:
        return out
    
    header = rows[header_idx]
    sym_col, qty_col, cost_col = detect_positions_columns(list(header))
    
    # Create dict reader manually from remaining rows
    idx = {h.strip(): i for i, h in enumerate(header)}
    
    for r in rows[header_idx + 1:]:
        if not r or all((c or "").strip() == "" for c in r):
            continue
        
        sym_val = r[idx[sym_col]].strip().upper() if idx[sym_col] < len(r) else ""
        if not sym_val:
            continue
        
        # Skip summary/total rows
        if sym_val in ("ACCOUNT TOTAL", "CASH & CASH INVESTMENTS", "TOTAL", "TOTALS", "GRAND TOTAL"):
            continue
        
        qty_val = r[idx[qty_col]] if idx[qty_col] < len(r) else "0"
        cost_val = r[idx[cost_col]] if idx[cost_col] < len(r) else "0"
        
        shares = parse_decimal_any(qty_val)
        cost = parse_decimal_any(cost_val)
        out[sym_val] = {"shares": shares, "cost": cost}
    
    return out


def write_validation_report(
    schwab_pos: Dict[str, Dict[str, Decimal]],
    lot_totals: Dict[str, Dict[str, Decimal]],
) -> None:
    """
    Writes per-symbol diffs and a status column.
    """
    symbols = sorted(set(schwab_pos.keys()) | set(lot_totals.keys()))
    rows = []
    for sym in symbols:
        s = schwab_pos.get(sym)
        q = lot_totals.get(sym)

        s_sh = s["shares"] if s else None
        s_cost = s["cost"] if s else None
        q_sh = q["shares"] if q else None
        q_cost = q["cost"] if q else None

        sh_diff = (q_sh - s_sh) if (q_sh is not None and s_sh is not None) else None
        cost_diff = (q_cost - s_cost) if (q_cost is not None and s_cost is not None) else None

        status = "OK"
        if s is None:
            status = "MISSING_IN_SCHWAB_POSITIONS"
        elif q is None:
            status = "MISSING_IN_LOT_FILES"
        else:
            # Tolerances: shares exact (to 0.0001), cost within $0.01
            if sh_diff is not None and cost_diff is not None:
                if abs(sh_diff) > Decimal("0.0001") or abs(cost_diff) > Decimal("0.01"):
                    status = "MISMATCH"

        rows.append({
            "Symbol": sym,
            "SchwabShares": "" if s_sh is None else str(s_sh),
            "LotsShares": "" if q_sh is None else str(q_sh),
            "ShareDiff(Lots-Schwab)": "" if sh_diff is None else str(sh_diff),
            "SchwabCostBasis": "" if s_cost is None else str(s_cost),
            "LotsCostBasis": "" if q_cost is None else str(q_cost),
            "CostDiff(Lots-Schwab)": "" if cost_diff is None else str(cost_diff),
            "Status": status,
        })

    with OUT_VALIDATION.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else [
            "Symbol","SchwabShares","LotsShares","ShareDiff(Lots-Schwab)","SchwabCostBasis",
            "LotsCostBasis","CostDiff(Lots-Schwab)","Status"
        ])
        w.writeheader()
        w.writerows(rows)


# -------------------------
# Writers
# -------------------------

def write_checklist(rows: List[AggRow]) -> None:
    with OUT_CHECKLIST.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f,
            fieldnames=["Symbol", "Bucket", "Shares", "TotalCost", "AcqDateForQuicken", "LotsInBucket", "Memo"],
        )
        w.writeheader()
        for r in rows:
            w.writerow({
                "Symbol": r.symbol,
                "Bucket": r.bucket,
                "Shares": str(r.shares),
                "TotalCost": str(r.cost),
                "AcqDateForQuicken": mmddyyyy(r.quicken_date),
                "LotsInBucket": r.lots_count,
                "Memo": MEMO_TEXT,
            })


def write_subtotals(
    totals: Dict[str, Dict[str, Decimal]],
    counts: Dict[str, Dict[str, int]],
) -> None:
    syms = sorted(totals.keys())
    with OUT_SUBTOTALS.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f,
            fieldnames=["Symbol", "TotalShares", "TotalCostBasis", "LotFilesRead", "LotsRead", "BucketRowsEmitted"],
        )
        w.writeheader()
        for sym in syms:
            t = totals[sym]
            c = counts.get(sym, {"lot_files": 0, "lots": 0, "bucket_rows": 0})
            w.writerow({
                "Symbol": sym,
                "TotalShares": str(t["shares"]),
                "TotalCostBasis": str(t["cost"]),
                "LotFilesRead": c["lot_files"],
                "LotsRead": c["lots"],
                "BucketRowsEmitted": c["bucket_rows"],
            })


# -------------------------
# Main
# -------------------------

def main() -> None:
    if not LOTS_DIR.exists() or not LOTS_DIR.is_dir():
        raise SystemExit("Expected a subdirectory named 'lots' in the current folder.")

    lot_files = sorted(LOTS_DIR.glob("*.csv"))
    if not lot_files:
        raise SystemExit("No CSV files found in ./lots/")

    agg_rows, per_symbol_totals, per_symbol_counts = aggregate_all(lot_files)

    write_checklist(agg_rows)
    write_subtotals(per_symbol_totals, per_symbol_counts)

    # Optional validation
    if POSITIONS_SUMMARY_FILE.exists():
        schwab_pos = load_positions_summary(POSITIONS_SUMMARY_FILE)
        write_validation_report(schwab_pos, per_symbol_totals)
        validation_msg = f"Validation written: {OUT_VALIDATION}"
    else:
        # Still emit an empty-ish report that says skipped (handy reminder)
        with OUT_VALIDATION.open("w", newline="", encoding="utf-8") as f:
            f.write("Validation skipped: file schwab_positions.csv not found in current folder.\n")
        validation_msg = "Validation skipped (no schwab_positions.csv); stub report written."

    print(f"Read lot files: {len(lot_files)} from ./{LOTS_DIR}/")
    print(f"Wrote checklist: {OUT_CHECKLIST} ({len(agg_rows)} Add-Shares lines)")
    print(f"Wrote subtotals: {OUT_SUBTOTALS} ({len(per_symbol_totals)} securities)")
    print(validation_msg)


if __name__ == "__main__":
    main()
