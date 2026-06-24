import argparse
import csv
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

SCHWAB_LOTS_DIR = Path("SchwabLots")
QUICKEN_EXPORTS_DIR = Path("QuickenExports")
QUICKEN_LOTS_RE = re.compile(r"^QuickenLots_(\d{4}-\d{2}-\d{2})\.xlsx$")

SYMBOL_COL = "Symbol"
OPEN_DATE_COL = "Open Date"
QUANTITY_COL = "Quantity"
COST_BASIS_COL = "Cost Basis"


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
    """Represents a Quicken security with totals and individual lots."""

    name: str
    ticker: str
    quote: float = 0.0
    shares: float = 0.0
    market_value: float = 0.0
    cost_basis: float = 0.0
    lots: list[Lot] = field(default_factory=list)


@dataclass
class AccountSecurities:
    securities: dict[str, Security]
    account_name: Optional[str]


@dataclass
class SchwabLots:
    path: Path
    account_suffix: Optional[str]
    lots_by_symbol: dict[str, dict[str, dict[str, float]]]


@dataclass
class LotComparison:
    """Result of comparing a lot between Schwab and Quicken."""

    date: str
    schwab_shares: float = 0.0
    quicken_shares: float = 0.0
    schwab_cost_basis: float = 0.0
    quicken_cost_basis: float = 0.0
    status: str = ""

    def __str__(self) -> str:
        if self.status == "match":
            return f"  OK {self.date}: {self.schwab_shares:.4f} shares @ ${self.schwab_cost_basis:.2f}"
        if self.status == "missing_in_quicken":
            return (
                f"  MISSING IN QUICKEN - {self.date}: "
                f"Schwab has {self.schwab_shares:.4f} shares @ ${self.schwab_cost_basis:.2f}"
            )
        if self.status == "missing_in_schwab":
            return (
                f"  MISSING IN SCHWAB - {self.date}: "
                f"Quicken has {self.quicken_shares:.4f} shares @ ${self.quicken_cost_basis:.2f}"
            )

        shares_diff = (
            f" shares: {self.schwab_shares:.4f} vs {self.quicken_shares:.4f}"
            if abs(self.schwab_shares - self.quicken_shares) > 0.01
            else ""
        )
        cost_diff = (
            f" cost: ${self.schwab_cost_basis:.2f} vs ${self.quicken_cost_basis:.2f}"
            if abs(self.schwab_cost_basis - self.quicken_cost_basis) > 0.01
            else ""
        )
        return f"  MISMATCH - {self.date}:{shares_diff}{cost_diff}"


def parse_number(value) -> float:
    """Convert Schwab/Quicken formatted numbers to floats."""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip()
    if not text or text == "*":
        return 0.0

    text = text.replace("$", "").replace(",", "").replace("*", "").strip()
    text = re.sub(r"\s+\d+$", "", text)

    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1].strip()
    if text.endswith("-"):
        negative = True
        text = text[:-1].strip()

    try:
        parsed = float(text)
    except ValueError:
        return 0.0
    return -parsed if negative else parsed


def normalize_date(value) -> Optional[str]:
    if value is None:
        return None

    if isinstance(value, datetime):
        return f"{value.month}/{value.day}/{value.year}"

    text = str(value).strip()
    if not text:
        return None

    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(text, fmt)
            return f"{parsed.month}/{parsed.day}/{parsed.year}"
        except ValueError:
            pass

    parts = text.split("/")
    if len(parts) == 3:
        try:
            year = int(parts[2])
            if year < 100:
                year += 2000
            return f"{int(parts[0])}/{int(parts[1])}/{year}"
        except ValueError:
            return None

    return None


def sortable_date(date_text: str) -> tuple[int, int, int]:
    month, day, year = (int(part) for part in date_text.split("/"))
    return year, month, day


def extract_account_suffix_from_title(title_line: str) -> Optional[str]:
    match = re.search(r"for\s+\.\.\.(\d{3,4})\b", (title_line or "").strip(), re.IGNORECASE)
    return match.group(1) if match else None


def find_schwab_lot_file() -> Path:
    if not SCHWAB_LOTS_DIR.exists():
        raise SystemExit(f"ERROR: Schwab lots directory not found: {SCHWAB_LOTS_DIR.resolve()}")

    paths = sorted(SCHWAB_LOTS_DIR.glob("*.csv"))
    if not paths:
        raise SystemExit(f"ERROR: No Schwab lots CSV files found in {SCHWAB_LOTS_DIR.resolve()}")
    if len(paths) > 1:
        listed = "\n".join(f"  - {path}" for path in paths)
        raise SystemExit(
            "ERROR: Expected exactly one combined Schwab lots CSV in "
            f"{SCHWAB_LOTS_DIR.resolve()}, found {len(paths)}:\n{listed}"
        )
    return paths[0]


def find_latest_quicken_lots_file() -> Path:
    if not QUICKEN_EXPORTS_DIR.exists():
        raise SystemExit(f"ERROR: Quicken exports directory not found: {QUICKEN_EXPORTS_DIR.resolve()}")

    dated_paths: list[tuple[datetime, Path]] = []
    for path in QUICKEN_EXPORTS_DIR.glob("QuickenLots_*.xlsx"):
        match = QUICKEN_LOTS_RE.match(path.name)
        if not match:
            continue
        dated_paths.append((datetime.strptime(match.group(1), "%Y-%m-%d"), path))

    if not dated_paths:
        raise SystemExit(
            "ERROR: No Quicken lots workbook found. Expected files like "
            f"{QUICKEN_EXPORTS_DIR / 'QuickenLots_YYYY-MM-DD.xlsx'}"
        )

    return max(dated_paths, key=lambda item: item[0])[1]


def find_header_row(rows: list[list[str]]) -> tuple[int, list[str]]:
    for index, row in enumerate(rows):
        normalized = [cell.strip() for cell in row]
        if SYMBOL_COL in normalized and OPEN_DATE_COL in normalized:
            missing = [
                column
                for column in (SYMBOL_COL, OPEN_DATE_COL, QUANTITY_COL, COST_BASIS_COL)
                if column not in normalized
            ]
            if missing:
                raise SystemExit(
                    "ERROR: Schwab lots file is missing required column(s): "
                    + ", ".join(missing)
                )
            return index, normalized

    raise SystemExit(
        "ERROR: Could not find the combined Schwab lots header row with "
        f"{SYMBOL_COL!r} and {OPEN_DATE_COL!r}."
    )


def add_lot(lots: dict[str, dict[str, float]], date_text: str, shares: float, cost: float) -> None:
    if date_text not in lots:
        lots[date_text] = {"shares": 0.0, "cost_basis": 0.0}
    lots[date_text]["shares"] += shares
    lots[date_text]["cost_basis"] += cost


def load_schwab_lots(path: Path) -> SchwabLots:
    text = path.read_text(encoding="utf-8-sig")
    rows = list(csv.reader(text.splitlines()))
    if not rows:
        raise SystemExit(f"ERROR: Schwab lots file is empty: {path.resolve()}")

    title_line = rows[0][0] if rows[0] else ""
    account_suffix = extract_account_suffix_from_title(title_line)
    header_index, headers = find_header_row(rows)

    lots_by_symbol: dict[str, dict[str, dict[str, float]]] = {}
    for row in rows[header_index + 1 :]:
        if not any(cell.strip() for cell in row):
            continue
        values = dict(zip(headers, row))
        symbol = values.get(SYMBOL_COL, "").strip().upper()
        open_date = values.get(OPEN_DATE_COL, "").strip()
        quantity = values.get(QUANTITY_COL, "").strip()

        if not symbol or symbol == "CASH":
            continue
        if not open_date or open_date.lower() == "total" or not quantity:
            continue

        date_text = normalize_date(open_date)
        if not date_text:
            continue

        shares = parse_number(quantity)
        cost = parse_number(values.get(COST_BASIS_COL, ""))
        if abs(shares) < 1e-9:
            continue

        add_lot(lots_by_symbol.setdefault(symbol, {}), date_text, shares, cost)

    if not lots_by_symbol:
        raise SystemExit(f"ERROR: No stock lots found in combined Schwab file: {path.resolve()}")

    return SchwabLots(path=path, account_suffix=account_suffix, lots_by_symbol=lots_by_symbol)


def parse_quicken_lots(path: Path, account_suffix: Optional[str]) -> AccountSecurities:
    """
    Parse the latest Quicken lots workbook and return securities for the account
    matching the Schwab masked suffix.
    """
    if not account_suffix:
        raise SystemExit("ERROR: Could not determine account suffix from the Schwab lots title line.")

    from openpyxl import load_workbook

    securities: dict[str, Security] = {}
    current_security: Optional[Security] = None
    current_account_matches = False
    matched_account_name: Optional[str] = None

    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb[wb.sheetnames[0]]

        for row in ws.iter_rows(values_only=True):
            cells = list(row[:7])
            while len(cells) < 7:
                cells.append(None)

            name = str(cells[1]).strip() if cells[1] is not None else ""
            ticker = str(cells[2]).strip().upper() if cells[2] is not None else ""
            quote = parse_number(cells[3])
            shares = parse_number(cells[4])
            market_value = parse_number(cells[5])
            cost_basis = parse_number(cells[6])

            # Account summary/header row, e.g. "Estate 3993".
            if name and not ticker and cells[4] is None:
                digits = "".join(ch for ch in name if ch.isdigit())
                current_account_matches = bool(digits) and digits.endswith(account_suffix)
                if current_account_matches:
                    matched_account_name = name
                current_security = None
                continue

            if not current_account_matches or not name:
                continue

            if name.startswith("Lot"):
                if current_security is not None:
                    current_security.lots.append(
                        Lot(
                            name=name,
                            quote=quote,
                            shares=shares,
                            market_value=market_value,
                            cost_basis=cost_basis,
                        )
                    )
                continue

            current_security = Security(
                name=name,
                ticker=ticker,
                quote=quote,
                shares=shares,
                market_value=market_value,
                cost_basis=cost_basis,
            )
            if ticker:
                securities[ticker] = current_security
    finally:
        wb.close()

    if not matched_account_name:
        raise SystemExit(
            "ERROR: Could not find a Quicken account ending in Schwab account suffix "
            f"{account_suffix}."
        )

    return AccountSecurities(securities=securities, account_name=matched_account_name)


def quicken_lots_by_date(security: Security) -> dict[str, dict[str, float]]:
    lots: dict[str, dict[str, float]] = {}
    for lot in security.lots:
        if not lot.name.startswith("Lot "):
            continue
        date_text = normalize_date(lot.name[4:].strip())
        if not date_text:
            continue
        add_lot(lots, date_text, lot.shares, lot.cost_basis)
    return lots


def compare_symbol(
    ticker: str,
    schwab_lots: dict[str, dict[str, float]],
    quicken_security: Optional[Security],
    show_discrepancies: bool = False,
) -> list[LotComparison]:
    print(f"\n=== Comparing {ticker} lots: Schwab vs Quicken ===\n")

    if quicken_security is None:
        print(f"WARNING: {ticker} not found in Quicken account")
        quicken_security = Security(name=ticker, ticker=ticker)

    quicken_lots = quicken_lots_by_date(quicken_security)
    comparisons: list[LotComparison] = []

    for date_text in sorted(set(schwab_lots) | set(quicken_lots), key=sortable_date):
        schwab_data = schwab_lots.get(date_text, {})
        quicken_data = quicken_lots.get(date_text, {})

        schwab_shares = schwab_data.get("shares", 0.0)
        schwab_cost = schwab_data.get("cost_basis", 0.0)
        quicken_shares = quicken_data.get("shares", 0.0)
        quicken_cost = quicken_data.get("cost_basis", 0.0)

        if date_text in schwab_lots and date_text not in quicken_lots:
            status = "missing_in_quicken"
        elif date_text not in schwab_lots and date_text in quicken_lots:
            status = "missing_in_schwab"
        elif abs(schwab_shares - quicken_shares) > 0.01 or abs(schwab_cost - quicken_cost) > 0.01:
            status = "mismatch"
        else:
            status = "match"

        comparisons.append(
            LotComparison(
                date=date_text,
                schwab_shares=schwab_shares,
                quicken_shares=quicken_shares,
                schwab_cost_basis=schwab_cost,
                quicken_cost_basis=quicken_cost,
                status=status,
            )
        )

    matches = sum(1 for comparison in comparisons if comparison.status == "match")
    mismatches = sum(1 for comparison in comparisons if comparison.status == "mismatch")
    missing_quicken = sum(1 for comparison in comparisons if comparison.status == "missing_in_quicken")
    missing_schwab = sum(1 for comparison in comparisons if comparison.status == "missing_in_schwab")

    print(f"Total lots compared: {len(comparisons)}")
    print(f"  Matches: {matches}")
    print(f"  Mismatches: {mismatches}")
    print(f"  Missing in Quicken: {missing_quicken}")
    print(f"  Missing in Schwab: {missing_schwab}")
    print()

    discrepancy_count = mismatches + missing_quicken + missing_schwab
    if discrepancy_count > 0 and show_discrepancies:
        print("Discrepancies:")
        for comparison in comparisons:
            if comparison.status != "match":
                print(comparison)
    elif discrepancy_count > 0:
        print(f"Discrepancies hidden ({discrepancy_count}); use --show-discrepancies to display lot details.")
    else:
        print("All lots match!")

    schwab_total_shares = sum(lot["shares"] for lot in schwab_lots.values())
    schwab_total_cost = sum(lot["cost_basis"] for lot in schwab_lots.values())
    quicken_total_shares = quicken_security.shares
    quicken_total_cost = quicken_security.cost_basis

    print("\nTotals:")
    print(f"  Schwab:  {schwab_total_shares:.4f} shares, ${schwab_total_cost:.2f} cost basis")
    print(f"  Quicken: {quicken_total_shares:.4f} shares, ${quicken_total_cost:.2f} cost basis")

    if abs(schwab_total_shares - quicken_total_shares) > 0.01:
        print(f"  WARNING: Share difference: {schwab_total_shares - quicken_total_shares:.4f}")
    if abs(schwab_total_cost - quicken_total_cost) > 0.01:
        print(f"  WARNING: Cost basis difference: ${schwab_total_cost - quicken_total_cost:.2f}")

    return comparisons


def print_quicken_summary(securities: dict[str, Security]) -> None:
    print(f"Found {len(securities)} securities in Quicken\n")

    for ticker, security in sorted(securities.items()):
        print(f"{ticker}: {security.name}")
        print(f"  Total Shares: {security.shares:.2f}")
        print(f"  Total Cost Basis: ${security.cost_basis:.2f}")
        print(f"  Market Value: ${security.market_value:.2f}")
        print(f"  Number of Lots: {len(security.lots)}")

        for lot in security.lots[:3]:
            print(f"    {lot.name}: {lot.shares:.2f} shares @ ${lot.cost_basis:.2f}")
        if len(security.lots) > 3:
            print(f"    ... and {len(security.lots) - 3} more lots")
        print()


def print_ticker_comparison(quicken_tickers: set[str], schwab_tickers: set[str]) -> None:
    print("\n" + "=" * 60)
    print("TICKER SYMBOL COMPARISON")
    print("=" * 60)

    only_in_quicken = quicken_tickers - schwab_tickers
    only_in_schwab = schwab_tickers - quicken_tickers
    in_both = quicken_tickers & schwab_tickers

    print(f"\nTickers in Quicken: {len(quicken_tickers)}")
    print(f"Tickers in Schwab CSV: {len(schwab_tickers)}")
    print(f"Tickers in both: {len(in_both)}")

    if only_in_quicken:
        print(f"\nTickers ONLY in Quicken ({len(only_in_quicken)}):")
        for ticker in sorted(only_in_quicken):
            print(f"  {ticker}")

    if only_in_schwab:
        print(f"\nTickers ONLY in Schwab CSV ({len(only_in_schwab)}):")
        for ticker in sorted(only_in_schwab):
            print(f"  {ticker}")

    if not only_in_quicken and not only_in_schwab:
        print("\nAll tickers match perfectly!")

    print("\n" + "=" * 60)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare Schwab lot data to the latest matching Quicken lots workbook."
    )
    parser.add_argument(
        "--show-discrepancies",
        action="store_true",
        help="display individual lot discrepancy details",
    )
    args = parser.parse_args()

    schwab_file = find_schwab_lot_file()
    quicken_file = find_latest_quicken_lots_file()

    print(f"\n=== Parsing Schwab lots: {schwab_file} ===")
    schwab = load_schwab_lots(schwab_file)
    print(f"Found {len(schwab.lots_by_symbol)} symbols in Schwab lots")

    print(f"\n=== Parsing Quicken lots: {quicken_file} ===")
    quicken = parse_quicken_lots(quicken_file, schwab.account_suffix)
    print(f"Using Quicken account: {quicken.account_name}")
    print_quicken_summary(quicken.securities)

    print(f"\n=== Processing {len(schwab.lots_by_symbol)} Schwab symbol(s) ===")
    for ticker in sorted(set(schwab.lots_by_symbol) | set(quicken.securities)):
        compare_symbol(
            ticker,
            schwab.lots_by_symbol.get(ticker, {}),
            quicken.securities.get(ticker),
            show_discrepancies=args.show_discrepancies,
        )
        print("\n" + "=" * 60 + "\n")

    print_ticker_comparison(set(quicken.securities), set(schwab.lots_by_symbol))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
