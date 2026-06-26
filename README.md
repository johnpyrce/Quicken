# Quicken Cost Basis Repair Tools

This repository contains local tools for comparing Quicken investment cost basis
data against Schwab lot exports. Schwab is the source
of truth, so Quicken lots are compared and adjustments needed to make share and cost
basis totals match are derived.

Use the repo-local virtual environment when running Python scripts:

```bash
source .venv/bin/activate
```

## Repository Layout

|Entry|Description|
|----|----|
| SchwabLots | a directory with Schwab lot data, with a file per investment account|
| QuickenExports | exports from Quicken. The XLSX file contains all investment accounts and lot details|
| scrape_schwab_lots.py | extracts a combined Schwab lot CSV from a logged-in browser session into SchwabLots.|
| lot_basis_comparison.py | compare lots between Quicken and Schwab, using the Schwab CSV with the newest dated Quicken lots workbook.|
| schwab_quicken_rebuild.py | builds grouped Add Shares checklist output from the combined Schwab CSV.|
| SchwabInChrome.sh | starts Chrome for use by the scarepe_schwab_lots.py script|
| RepairQuickenTransactions.md |  manual Quicken repair procedure|

The contents of QuickenExports is produced on PamelaPC by a Power Automation script.


## Current Data Contract

`lot_basis_comparison.py` expects:

- a combined Schwab CSV in `SchwabLots/`
- a Quicken lot workbooks named `QuickenExports/QuickenLots_YYYY-MM-DD.xlsx`

## Compare Schwab To Quicken

Run the default compact comparison table:

```bash
.venv/bin/python lot_basis_comparison.py
```

The default table has one row per symbol, paired Schwab and Quicken share/cost
basis totals, Schwab and Quicken lot counts, market value, and a totals row.
Matching Quicken-side values display as `=`.

`--show-discrepancies` appends detailed per-lot comparison lines.

`--show-recent-matches` adds the optional `Recent Matching Lots` column.

`-t` / `--transactions` appends one aggregate adjustment row per flagged symbol.
These rows are intended to make Quicken totals match Schwab totals without
recreating Schwab lots. Each row shows:

- `Share Change` = Schwab shares minus Quicken shares
- `Cost Basis Change` = Schwab cost basis minus Quicken cost basis
- `Target Shares` and `Target Cost Basis` = Schwab totals

## Extract Schwab Lots

Safari is the default scraper backend:

```bash
.venv/bin/python scrape_schwab_lots.py
.venv/bin/python scrape_schwab_lots.py "Inheritance"
```

Before running the Safari workflow, log in to Schwab manually and enable:

`Develop > Allow JavaScript from Apple Events`

Output is written to `SchwabLots/<Schwab account name>.csv`.

Chrome is still available as a fallback backend:

```bash
./SchwabInChrome.sh
.venv/bin/python scrape_schwab_lots.py --browser chrome-cdp
```

The scraper reads the visible Schwab Lot Details table and writes one combined
CSV with `Symbol` as the first column.

## Rebuild Checklist Script

`schwab_quicken_rebuild.py` reads the combined Schwab CSV and writes grouped
manual-entry files:

```bash
.venv/bin/python schwab_quicken_rebuild.py
```

Expected outputs:

- `quicken_addshares_checklist.csv`
- `quicken_security_subtotals.csv`
- `schwab_quicken_validation_report.csv`

This script does not currently have command-line options. With the current
checked-in Schwab CSV, it starts processing immediately and may fail if Schwab
exports a numeric cell in a format it does not recognize. Use
`lot_basis_comparison.py -t` for the current aggregate adjustment report.

