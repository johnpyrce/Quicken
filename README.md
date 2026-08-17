# Quicken Cost Basis Repair Tools

This repository contains local tools for extracting Schwab investment lots,
comparing them with Quicken, and preparing manual cost-basis repairs. Schwab is
treated as the source of truth.

Run the scripts from the repository root because their input and output paths
are relative to the current directory.

## Setup

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) if it is
not already available, then synchronize the project environment:

```bash
uv sync
```

`uv sync` creates and manages the project virtual environment, installs the
runtime dependencies, and installs the development tools from the lockfile.
The scripts require Playwright for browser extraction, openpyxl for reading
Quicken workbooks, and DuckDB/PyArrow for QIF loading. If Playwright browsers
are not installed yet, run:

```bash
uv run playwright install
```

## Repository Layout

| Entry | Description |
| --- | --- |
| `SchwabLots/` | Combined Schwab lot CSVs, normally one file per investment account. |
| `QuickenExports/` | Quicken exports. The dated XLSX workbook contains all investment accounts and their lot details. |
| `scrape_schwab_lots.py` | Extracts one account's visible Schwab lot details from a logged-in browser session. |
| `lot_basis_comparison.py` | Compares a selected Schwab account CSV with the matching account in the newest Quicken workbook. |
| `schwab_quicken_rebuild.py` | Legacy tool that creates grouped Add Shares checklist files. |
| `loader/` | Self-contained QIF-to-DuckDB loader (`qif_to_duckdb.py`, `qif_loader.py`, and `schema.sql`). |
| `SchwabInChrome.sh` | Starts Chrome with remote debugging for the Chrome scraper backend. |
| `RepairQuickenTransactions.md` | Manual Quicken repair guidance. |

## Create a DuckDB Database from QIF

The `loader/` directory contains the QIF parser, DuckDB schema, and command-line
utility. Pass a QIF export as the first argument and an optional output database
path as the second:

```bash
uv run python loader/qif_to_duckdb.py QuickenExports/Quicken_2026-08-16.QIF
uv run python loader/qif_to_duckdb.py \
  QuickenExports/Quicken_2026-08-16.QIF \
  QuickenExports/quicken.duckdb
```

If no output path is supplied, the utility creates `<qif filename>.duckdb` in
the current directory. Existing databases are protected by default; use
`--force` to replace one:

```bash
uv run python loader/qif_to_duckdb.py --force input.qif output.duckdb
```

The command reports loaded account, category, transaction, and rejected
transaction counts, and verifies the persisted transaction count before it
returns successfully.

## Quicken Extraction

The contents of `QuickenExports/` are produced on PamelaPC by a Power
Automation script.

The process is:

- Go to the "Investment" -> "Overview" from the toolbar.
- Click the button on the bottom to expand all entries.  This makes the lot details visible.
- Click the "Export" -> "XLSX" button on the top of the page to produce a XLSX file.

## Input Files and Account Matching

Schwab lot files are stored as:

```text
SchwabLots/<Schwab account name>.csv
```

Examples include `Inheritance.csv`, `Contributory.csv`, and
`Rollover IRA.csv`. Each CSV is a combined account export whose lot rows begin
with a `Symbol` column. Its title line must contain the masked Schwab account
suffix, such as `...3993`.

Quicken lot workbooks are stored as:

```text
QuickenExports/QuickenLots_YYYY-MM-DD.xlsx
```

The comparison tool automatically selects the newest workbook by the date in
its filename. It uses the masked suffix in the selected Schwab CSV to find the
corresponding account in that workbook.

## Compare Schwab with Quicken

Run the comparison for the default Schwab file, `SchwabLots/Inheritance.csv`:

```bash
uv run python lot_basis_comparison.py
```

To select another file, give its name without the `SchwabLots/` directory. The
`.csv` extension is optional. Names containing spaces may be quoted or entered
as separate words:

```bash
uv run python lot_basis_comparison.py Contributory
uv run python lot_basis_comparison.py Rollover IRA
uv run python lot_basis_comparison.py "Rollover IRA.csv"
```

The compact report contains one row per symbol, paired Schwab and Quicken
share and cost-basis totals, lot counts, Schwab market value, and a totals row.
A matching Quicken-side value is displayed as `=`. A `*` flags a symbol whose
share or cost-basis total differs.

Available report options are:

| Option | Behavior |
| --- | --- |
| `--show-discrepancies` | Appends individual lot differences. Mismatch lines explicitly label the Schwab and Quicken amounts. |
| `--show-recent-matches` | Adds the number of newest consecutive lots that match. |
| `-t`, `--transactions` | Appends aggregate adjustments needed to make Quicken totals equal Schwab totals. |
| `--format text` | Plain-text table; this is the default. |
| `--format markdown` | Markdown report. |
| `--format csv` | CSV report. |
| `--format html` | Complete HTML report. |

Options can be combined. For example:

```bash
uv run python lot_basis_comparison.py "Rollover IRA" \
  --show-discrepancies --show-recent-matches --transactions

uv run python lot_basis_comparison.py Inheritance \
  --format html --transactions > inheritance-report.html
```

For `--transactions`:

- `Share Change` is Schwab shares minus Quicken shares.
- `Cost Basis Change` is Schwab cost basis minus Quicken cost basis.
- `Target Shares` and `Target Cost Basis` are the Schwab totals.

These are aggregate adjustment suggestions; they do not recreate individual
Schwab lots.

## Extract Schwab Lots

The scraper reads the visible Schwab Lot Details tables and writes one combined
CSV for the selected account. The default account is `Inheritance`, and the
default output directory is `SchwabLots/`.

Safari is the default backend. Before running it:

1. Open Safari and log in to Schwab manually.
2. Enable `Develop > Allow JavaScript from Apple Events`.
3. Make sure the requested account is available in Schwab.

Then run:

```bash
uv run python scrape_schwab_lots.py
uv run python scrape_schwab_lots.py "Rollover IRA"
```

The second example writes `SchwabLots/Rollover IRA.csv`. Use `--output` to
choose another output directory:

```bash
uv run python scrape_schwab_lots.py "Rollover IRA" --output SchwabLots
```

Chrome with the DevTools Protocol is available as a fallback:

```bash
./SchwabInChrome.sh
uv run python scrape_schwab_lots.py "Inheritance" --browser chrome-cdp
```

By default the Chrome backend connects to `http://127.0.0.1:9222`. Supply a
different endpoint with `--cdp-url` if needed.

## Legacy Rebuild Checklist

`schwab_quicken_rebuild.py` groups Schwab lots into age buckets and produces
manual Add Shares checklist data:

```bash
uv run python schwab_quicken_rebuild.py
```

Unlike `lot_basis_comparison.py`, this legacy script has no command-line
options and requires **exactly one** CSV in `SchwabLots/`. Temporarily move the
other account CSVs elsewhere before running it.

Output filenames are prefixed with a lowercase, underscore-separated version
of the matching Quicken account name when one can be determined. The generated
file types are:

- `<account>_quicken_addshares_checklist.csv`
- `<account>_quicken_security_subtotals.csv`
- `<account>_schwab_quicken_validation_report.csv`

If a dated Quicken workbook is available, the newest one is used for automatic
share and cost-basis validation. Otherwise, the checklist and subtotals are
still written and the validation report records that validation was skipped.

For the current aggregate adjustment workflow, prefer
`lot_basis_comparison.py --transactions`.
