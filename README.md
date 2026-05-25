# Quicken Cost Basis Repair Tools

This repository contains scripts used to repair and validate Quicken investment cost basis data using Schwab lot exports as the source of truth.

Use the local `.venv` before running any Python scripts in this repo.


## Current Repository Layout

- `.venv/` - local Python virtual environment (recommended runtime)
- `Exports/` - Quicken exported files (for example Quicken `.QIF` output)
- `lots-Inheritance/` - Schwab lot CSV exports for the Inheritance account
- `scrape_schwab_lots.py` - downloads Schwab lot CSVs via Chrome CDP session
- `schwab_quicken_rebuild.py` - main lot bucketing + checklist generation script
- `lot_basis_comparison.py` - lot-by-lot comparison against Quicken export workbook
- `schwab_lots_to_quicken_checklist.py` - older single-file conversion utility
- `SchwabInChrome.sh` - helper for starting Chrome in Schwab workflow
- `RepairQuickenTransactions.md` - manual Quicken repair procedure


These scripts are run on the PC hosted Quicken to extract lots and a QIF version of the data:

- collector.ps1:  PowerShell version for Power Automation.
- flow.txt:  the Power Automatiion flow.
- collector.py:  pywinauto version.

## Primary Workflow

### 1) Start Chrome and log in to Schwab

Use `SchwabInChrome.sh` (or equivalent) so Chrome is running with remote debugging on port `9222`, then log in manually.

### 2) Download Schwab lot CSVs

```bash
python scrape_schwab_lots.py
# or
python scrape_schwab_lots.py "Inheritance"
python scrape_schwab_lots.py "Joint Account"
```

Output is written to `lots-<account>/` (for example `lots-Inheritance/`).

### 3) Prepare working lot folder for rebuild

`schwab_quicken_rebuild.py` expects input files in `./lots/*.csv`.

If you just downloaded `lots-Inheritance/`, copy or rename it:

```bash
cp -R lots-Inheritance lots
# or: mv lots-Inheritance lots
```

### 4) Build Quicken checklist output

```bash
python schwab_quicken_rebuild.py
```

Expected outputs:

- `quicken_addshares_checklist.csv`
- `quicken_security_subtotals.csv`
- `schwab_quicken_validation_report.csv`

Validation behavior:

- If `Quicken_Lots.xlsx` exists in the repo root, validation runs automatically.
- If `Quicken_Lots.xlsx` is missing, checklist/subtotals still generate and validation is skipped.

### 5) Optional: lot-level audit

```bash
python lot_basis_comparison.py
# or specific symbols/files:
python lot_basis_comparison.py lots/AAPL.csv lots/MSFT.csv
```

`lot_basis_comparison.py` compares Schwab lots to the matching account section in `Quicken_Lots.xlsx`.

## Data Expectations

- Schwab lot files are report-style CSVs where the first line contains title metadata, including `as of` date and masked account suffix.
- `Quicken_Lots.xlsx` should be a Portfolio export with lot rows expanded.
- Numeric fields may include `$`, commas, `*`, or trailing minus notation; scripts normalize these formats.

## Notes

- Rebuild output is designed for manual Add Shares entry in Quicken.
- Bucketed output intentionally reduces lot granularity to speed manual repair work.
