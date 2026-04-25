# Quicken Cost Basis Repair Tools

This directory contains the current Schwab-to-Quicken rebuild workflow.

The basic idea is:

1. Download per-ticker Schwab lot detail CSVs from the Schwab website.
2. Convert those lot files into a reduced Quicken entry checklist.
3. Validate the results against `Quicken_Lots.xlsx`, which contains all brokerage accounts.

Schwab is treated as the source of truth. The scripts here exist to rebuild or audit Quicken cost basis when Quicken's downloaded lots become unreliable.

## Current Primary Workflow

### 1. Download Schwab lot files

Launch Chrome with SchwabInChrome.sh.  Login to Schwab.

Use [scrape_schwab_lots.py](/C:/cygwin64/home/John/Projects/Quicken/scrape_schwab_lots.py).

What it does:
- Connects to an already running Chrome session on debug port `9222`
- Opens `Next Steps` / `Lot Details` / `Export Lots`
- Clicks the `Export Lot Details Data` modal `OK` button in the main page DOM
- Downloads one CSV per ticker for a single Schwab account

Usage:

```bash
python scrape_schwab_lots.py
python scrape_schwab_lots.py "Inheritance"
python scrape_schwab_lots.py "Joint Account"
```

Output:
- `lots-<account>/TICKER.csv`

Example:
- `lots-Inheritance/AAPL.csv`

Notes:
- The script processes one Schwab account at a time.
- The generated lot directory is account-specific, such as `lots-Inheritance/`.
- The rebuild script expects the active working lot files in `./lots/`, so copy or rename the desired account directory before rebuilding.

### 2. Extract Quicken Investment Accounts, Holdings, and Lots

- Launch Quicken.
- Go to Investing -> Portfolio
- Click the button on the bottom:  "Expand All"
- Click the button on the top right:  Export -> As Spreadsheet.
- Copy that file to `Quicken_Lots.xlsx`.

### 3. Build Quicken entry files

Use [schwab_quicken_rebuild.py](/C:/cygwin64/home/John/Projects/Quicken/schwab_quicken_rebuild.py).

What it does:
- Reads every CSV in `./lots/`
- Parses Schwab lot detail files
- Groups lots into four age buckets per symbol
- Produces a reduced Add Shares checklist for manual entry into Quicken
- Validates totals against `Quicken_Lots.xlsx`
- Detects the correct brokerage account by matching the masked account suffix in the Schwab lot file titles to the account section inside `Quicken_Lots.xlsx`
- Prefixes output filenames with the detected account name

Usage:

```bash
python schwab_quicken_rebuild.py
```

Current output naming:
- `<account>_quicken_addshares_checklist.csv`
- `<account>_quicken_security_subtotals.csv`
- `<account>_schwab_quicken_validation_report.csv`

Example:
- `estate_3993_quicken_addshares_checklist.csv`
- `estate_3993_quicken_security_subtotals.csv`
- `estate_3993_schwab_quicken_validation_report.csv`

Bucket strategy:
- `VERY_OLD`: acquired before `2020-01-01`
- `OLD`: acquired from `2020-01-01` through `2022-12-31`
- `LONG_RECENT`: acquired after that but still long-term
- `SHORT_TERM`: acquired within one year of the Schwab file's `as of` date

Why it buckets:
- To reduce manual Quicken entry from many individual lots down to at most a few rows per security

### 4. Compare Schwab lots against Quicken lots (Optional)

Use [lot_basis_comparison.py](/C:/cygwin64/home/John/Projects/Quicken/lot_basis_comparison.py).

What it does:
- Reads `Quicken_Lots.xlsx`
- Detects the matching brokerage account the same way as `schwab_quicken_rebuild.py`
- Loads only that account's securities and lot rows from the workbook
- Compares each Schwab lot CSV in `./lots/*.csv` against Quicken lot-by-lot
- Reports matches, mismatches, missing lots, and total share/cost differences to the console

Usage:

```bash
python lot_basis_comparison.py
```

or for specific files:

```bash
python lot_basis_comparison.py lots/AAPL.csv lots/MSFT.csv
```

Purpose:
- Audit tool
- Debugging tool
- Useful when the grouped rebuild output looks wrong and you need exact lot-level comparisons

## Key Files In This Directory

### Active scripts

- [scrape_schwab_lots.py](/C:/cygwin64/home/John/Projects/Quicken/scrape_schwab_lots.py)
  Downloads Schwab lot detail CSVs for one account.

- [schwab_quicken_rebuild.py](/C:/cygwin64/home/John/Projects/Quicken/schwab_quicken_rebuild.py)
  Main production rebuild script.

- [lot_basis_comparison.py](/C:/cygwin64/home/John/Projects/Quicken/lot_basis_comparison.py)
  Lot-by-lot comparison and audit tool.

### Supporting files

- [Quicken_Lots.xlsx](/C:/cygwin64/home/John/Projects/Quicken/Quicken_Lots.xlsx)
  Quicken export workbook containing multiple brokerage accounts.

- [RepairQuickenTransactions.md](/C:/cygwin64/home/John/Projects/Quicken/RepairQuickenTransactions.md)
  Manual Quicken repair procedure and operational notes.

- [SchwabInChrome.sh](/C:/cygwin64/home/John/Projects/Quicken/SchwabInChrome.sh)
  Helper shell script related to launching or using Chrome for Schwab work.

### Legacy / secondary scripts

- [schwab_lots_to_quicken_checklist.py](/C:/cygwin64/home/John/Projects/Quicken/schwab_lots_to_quicken_checklist.py)
  Older converter that emits one Add Shares row per original lot. Kept mainly as a fallback/reference tool.

- [quicken_portfolio_2026_04_24.xlsx](/C:/cygwin64/home/John/Projects/Quicken/quicken_portfolio_2026_04_24.xlsx)
  Snapshot workbook in the directory; not part of the core scripted pipeline.

## Current Data Conventions

### Schwab lot detail CSVs

Each ticker file is a report-style CSV.

Typical shape:
- Row 1: title line, including symbol and masked account suffix
- Row 2: blank
- Row 3: headers
- Remaining rows: lots

Example title:

```text
AAPL Lot Details for  ...993 as of 01:13 PM ET, 04/25/2026
```

Important details extracted from this title:
- symbol
- `as of` date
- masked account suffix such as `993`

### Quicken_Lots.xlsx

This workbook is a report sheet, not a flat table.

The active scripts assume:
- account sections appear as rows like `Estate 3993`
- security summary rows contain ticker, shares, market value, and cost basis
- lot detail rows begin with names like `Lot 12/13/2004`

The scripts do not load the whole workbook as one account. They:
- read the masked suffix from the Schwab lot CSVs
- locate the matching account section in the workbook
- process only that section

### Numeric parsing

The current scripts handle:
- dollar signs
- commas
- trailing `*` markers in `Quicken_Lots.xlsx`
- trailing `-` negatives
- blank / placeholder values

Examples:
- `$1,234.56` -> `1234.56`
- `201,681.88*` -> `201681.88`
- `123.45-` -> `-123.45`

## Recommended Usage Order

### Standard rebuild

```bash
python scrape_schwab_lots.py "Inheritance"
mv lots-Inheritance lots
python schwab_quicken_rebuild.py
```

Then manually use the generated checklist file in Quicken.

### Standard audit

```bash
python lot_basis_comparison.py
```

## What Is Current vs Legacy

Preferred:
- `scrape_schwab_lots.py`
- `schwab_quicken_rebuild.py`
- `lot_basis_comparison.py`
- `Quicken_Lots.xlsx`

Legacy / less central:
- `schwab_lots_to_quicken_checklist.py`
- older CSV-only assumptions
- `basis.py` has been renamed to `lot_basis_comparison.py`

## Important Limitations

- Schwab UI automation is brittle because the Schwab web UI changes and uses modal overlays.
- The downloader currently depends on a live, manually logged-in Chrome session.
- The rebuild is designed for manual Quicken entry, not direct Quicken import automation.
- `schwab_quicken_rebuild.py` intentionally buckets lots, so it is not a one-row-per-lot export.
- `lot_basis_comparison.py` is the right tool when you need exact lot-by-lot comparison.

## Quick Summary

If you only need the modern workflow:

1. Download lots with `scrape_schwab_lots.py`
2. Put the chosen account's files in `./lots/`
3. Run `schwab_quicken_rebuild.py`
4. Use `lot_basis_comparison.py` when you need deeper lot-level auditing
