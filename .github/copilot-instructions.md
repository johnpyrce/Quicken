# Quicken Cost Basis Repair Tool

This project automates the repair of investment cost basis discrepancies between Charles Schwab and Quicken by downloading Schwab lot data, processing CSV exports, and generating Quicken rebuild templates.

## Project Purpose

Investment cost basis in Quicken becomes corrupted due to OFX download issues, placeholder transactions, and automatic balance adjustments. This toolset uses **Schwab as the authoritative source** to rebuild accurate lot-level cost basis in Quicken, enabling correct capital gains reporting and tax calculations.

## Architecture: Three-Stage Pipeline

```
1. DATA ACQUISITION (../scrape_schwab_lots.py)
   Schwab Website → Playwright CDP → ./lots-{account}/*.csv (per-ticker lot files)
   
2. DATA TRANSFORMATION (../schwab_quicken_rebuild.py)  
   ./lots/*.csv → Lot bucketing by age → quicken_addshares_checklist.csv
                                      → quicken_security_subtotals.csv
                                      → schwab_quicken_validation_report.csv
   
3. VALIDATION (../basis.py)
   Quicken_Lots.csv + ./lots/*.csv → Lot-by-lot comparison → Console discrepancy report
```

## Key Scripts & Responsibilities

### Primary Workflow Scripts

**[scrape_schwab_lots.py](../scrape_schwab_lots.py)** - Playwright CDP automation for bulk lot export (PRIMARY TOOL)
- Connects to existing Chrome browser via CDP (Chrome DevTools Protocol)
- User must log in to Schwab manually first (avoids bot detection)
- Automatically discovers and processes **all securities** in specified account
- Saves to `./lots-{account}/TICKER.csv` (e.g., `lots-Inheritance/AAPL.csv`)
- **Usage**: `python scrape_schwab_lots.py [account_name]` (default: 'Inheritance')
- **Dependencies**: `playwright` only
- **Prerequisites**: Chrome running with `--remote-debugging-port=9222`
- **Advantages**: Works with existing session, avoids bot detection, robust error handling, processes all securities automatically
- **Limitations**: Requires Chrome debug port setup, requires manual directory rename to `lots/` before running rebuild script

**[schwab_quicken_rebuild.py](../schwab_quicken_rebuild.py)** - Primary transformation engine (CURRENT PRODUCTION SCRIPT)
- Reads **all** CSV files from `./lots/` directory
- Groups lots into 4 time-based buckets per security:
  - `VERY_OLD`: acquired < 2020-01-01 → Quicken date: 12/31/2019
  - `OLD`: 2020 ≤ acq < 2023-01-01 → Quicken date: 12/31/2022
  - `LONG_RECENT`: 2023 ≤ acq < (as_of - 365d) → Quicken date: 12/31/2025
  - `SHORT_TERM`: acq ≥ (as_of - 365d) → Quicken date: **file's as_of_date**
- Outputs:
  - `quicken_addshares_checklist.csv`: Manual entry template (4 rows per security max)
  - `quicken_security_subtotals.csv`: Per-security totals and counts
  - `schwab_quicken_validation_report.csv`: Discrepancies vs `schwab_positions.csv` (if present)
- **Usage**: `python schwab_quicken_rebuild.py`
- **No parameters required** - processes entire `./lots/` directory automatically

**[basis.py](../basis.py)** - Legacy lot-by-lot comparison tool
- Compares Schwab lots against Quicken export for **single ticker**
- Identifies missing lots, share mismatches, cost basis differences
- **Usage**: `python basis.py` (auto-discovers all `./lots/*.csv` files)
- Requires `Quicken_Lots.csv` in working directory
- **Status**: Still used for validation but superseded by `schwab_quicken_rebuild.py` for production

### Supporting Scripts

**schwab_lot_downloader.py** - Selenium automation for bulk lot export (LEGACY)
- Opens Chrome with Selenium WebDriver
- Automates Schwab login (manual 2FA completion required)
- Navigates to Positions → Cost Basis
- Downloads lot details CSV for **all securities** in account
- Saves to `./lots/TICKER.csv` (e.g., `AAPL.csv`, `GOOGL.csv`)
- **Usage**: `python schwab_lot_downloader.py` (configure `SCHWAB_USERNAME` first)
- **Dependencies**: `selenium`, `webdriver-manager`
- **Status**: Works but being phased out in favor of `scrape_schwab_lots.py` (CDP-based approach more reliable)

**[schwab_lots_to_quicken_checklist.py](../schwab_lots_to_quicken_checklist.py)** - Legacy single-file transformer
- Converts one Schwab CSV → checklist with **individual lot rows** (unbucketed)
- **Usage**: `python schwab_lots_to_quicken_checklist.py AAPL.csv`
- **Status**: Obsolete - use `schwab_quicken_rebuild.py` instead

**schwab_scraper.py** - Experimental: connects to existing Chrome session
- Requires Chrome launched with `--remote-debugging-port=9222`
- More fragile than `schwab_lot_downloader.py`
- **Status**: Proof-of-concept, not recommended for production

**[inspect_schwab.py](../inspect_schwab.py)** - Developer tool for HTML structure analysis
- Inspects Schwab page DOM to identify CSS selectors
- Useful when Schwab changes page structure
- **Usage**: `python inspect_schwab.py` (with Chrome debugging session open)

## Current Workspace Structure (January 2026)

```
Quicken/
├── scrape_schwab_lots.py           # Primary: download lots from Schwab
├── schwab_quicken_rebuild.py       # Primary: bucket lots and generate checklist
├── basis.py                        # Validation: compare Quicken vs Schwab
├── lots-Inheritance/               # Raw Schwab downloads (32 CSV files)
│   ├── AAPL.csv
│   ├── GOOGL.csv
│   └── ... (one per security)
├── orig_lots/                      # Backup of previous downloads
├── lots/                           # Working directory (manual copy/rename)
├── schwab_positions.csv            # Manual Schwab positions export
├── Quicken_Lots.csv                # Manual Quicken export for validation
├── quicken_addshares_checklist.csv # Generated: manual entry template
├── quicken_security_subtotals.csv  # Generated: totals per security
├── schwab_quicken_validation_report.csv  # Generated: discrepancy report
└── RepairQuickenTransactions.md    # Manual procedure documentation
```

**Key workflow**: `lots-Inheritance/` → manual rename to `lots/` → `schwab_quicken_rebuild.py` reads `lots/`

## Critical Data Conventions

### Schwab CSV Format (`./lots/AAPL.csv`)

**Structure**:
```
Row 1: "AAPL Lot Details for ... as of 12:18 PM ET, 12/23/2025"  ← Title line (symbol + date extraction)
Row 2: [blank]
Row 3: Open Date,Quantity,Cost Basis,Market Value,Gain/Loss $,Gain/Loss %  ← Headers (DictReader starts here)
Row 4+: 2/16/2024,0.6369,$74.05,$105.45,$31.40,42.41%  ← Lot data
```

**Parsing rules**:
- Skip first 2 rows, then `csv.DictReader`
- Extract ticker from filename stem: `AAPL.csv` → `"AAPL"`
- Extract as_of_date via regex: `r"(\d{1,2}/\d{1,2}/\d{4})"` from title line
- Date normalization: `02/16/2024` → `2/16/2024` (remove leading zeros for comparison)
- Required columns: `"Open Date"`, `"Quantity"`, `"Cost Basis"`

### Schwab Positions Summary (`schwab_positions.csv`)

**Optional validation file** - used by `../schwab_quicken_rebuild.py` to verify aggregate totals.

**How to export from Schwab**:
1. Log in to Schwab.com
2. Navigate to **Accounts → Positions**
3. Select the appropriate account from dropdown
4. Click **Export** icon (top-right, looks like spreadsheet with arrow)
5. Choose **CSV** format
6. Save as `schwab_positions.csv` in project directory

**Structure**:
```
Symbol,Description,Quantity,Price,Price Change $,Price Change %,Market Value,...
AAPL,APPLE INC,10.6341,$165.54,...,$1759.68,...
GOOGL,ALPHABET INC-CL A,5.2000,$138.12,...,$718.22,...
```

**Used for**:
- Validating that bucketed lot totals match Schwab's current position totals
- Detecting data entry errors before manual Quicken entry
- Identifying securities in Schwab but missing from `./lots/` directory

**If missing**: Script still runs successfully but skips validation, writes stub report

### Quicken Export Format (`Quicken_Lots.csv`)

**Nested structure** (not standard CSV):
```
Name,Ticker Symbol,Quote/Price,Shares,Market Value,Cost Basis
Apple Inc.,AAPL,165.54,10.25,1696.79,1234.56  ← Security header
Lot 2/16/2024,,,0.6369,,74.05  ← Lot detail row
Lot 5/16/2024,,,0.5972,,103.21  ← Lot detail row
Microsoft Corp,MSFT,380.12,5.00,1900.60,1500.00  ← Next security header
Lot 1/10/2023,,,5.00,,1500.00
```

**Parsing logic** (`parse_quicken_lots()` in ../basis.py):
1. Maintain `current_security` state variable
2. If `name.startswith("Lot ")`: parse as lot detail, append to `current_security.lots[]`
3. Else: parse as new security header, store in `securities[ticker]`
4. Extract date from lot name: `"Lot 12/13/2004"` → `"12/13/2004"`

**To generate**:
1. Quicken → Investments → Portfolio
2. **Expand all securities** (click arrows to show lots)
3. Export icon → Save as CSV
4. **CRITICAL**: Collapsed view omits lot details

### Number Parsing Pattern

All scripts use similar `parse_number()` / `parse_decimal_any()`:
```python
"$1,234.56"  → 1234.56   # Strip $ and commas
"(1,234)"    → -1234.0   # Parentheses = negative
"1,234-"     → -1234.0   # Trailing hyphen = negative
"*", "--"    → 0.0       # Placeholders
```

**Float tolerance**: Use `abs(diff) < 0.01` for currency comparisons (rounding tolerance)

## Developer Workflow

### 1. Initial Setup (One-Time)

```bash
# Install dependencies
pip install playwright
playwright install chromium

# Start Chrome with remote debugging (required for CDP mode)
chrome.exe --remote-debugging-port=9222
# Or on Windows: "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222

# Log in to Schwab manually in the Chrome browser (complete 2FA)
# Navigate to Positions page to verify account names
```

### 2. Download Schwab Lots (Monthly/As-Needed)

```bash
# With Chrome running on debug port and logged into Schwab:
python scrape_schwab_lots.py                  # Downloads from 'Inheritance' account
python scrape_schwab_lots.py "Joint Account" # Downloads from 'Joint Account'
```

**Expected output**: 
- Directory: `./lots-Inheritance/` (or `./lots-{account_name}/`)
- Files: `./lots-Inheritance/AAPL.csv`, `./lots-Inheritance/GOOGL.csv`, etc. (one per holding)
- Old directory removed and recreated fresh on each run

**Manual step (required)**: Copy or rename the account-specific directory to `lots/` before running rebuild:
```bash
# Option 1: Copy files (preserves original)
cp -r lots-Inheritance/ lots/

# Option 2: Rename directory (recommended)
mv lots-Inheritance/ lots/
```

### 3. Generate Quicken Rebuild Checklist

```bash
python schwab_quicken_rebuild.py
```

**Expected output**:
```
Read lot files: 32 from ./lots/
Wrote checklist: quicken_addshares_checklist.csv (128 Add-Shares lines)
Wrote subtotals: quicken_security_subtotals.csv (32 securities)
Validation written: schwab_quicken_validation_report.csv
```

**Manual step**: Open `quicken_addshares_checklist.csv` in Excel, manually enter "Add Shares" transactions into Quicken (see [RepairQuickenTransactions.md](../RepairQuickenTransactions.md) for complete procedure)

### 4. Validate Against Quicken (Optional)

```bash
# Export from Quicken first (see Quicken Export Format above)
python basis.py
```

**Compares lot-by-lot** for all tickers, prints mismatches to console.

### Debugging in VS Code

**Environment**:
- Workspace: `c:\cygwin64\home\John\Projects\Quicken`
- Python: `C:\Python314\python.exe` (Windows native, **not** Cygwin Python)
- Terminal: bash via Cygwin (Git Bash or Cygwin64)
- Dependencies: `playwright` for ../scrape_schwab_lots.py, `selenium` for legacy schwab_lot_downloader.py, stdlib for everything else

**Launch config** (F5 debugging):
```json
{
  "python": "C:\\Python314\\python.exe",
  "cwd": "${workspaceFolder}",
  "console": "integratedTerminal"
}
```

**Common issues**:
- CSV encoding: All scripts use `encoding="utf-8-sig"` to handle Excel BOM
- Path separators: `Path()` objects everywhere (Windows + Cygwin compatible)
- Date format mismatches: Schwab uses `M/D/YYYY`, Quicken may use `MM/DD/YYYY` → normalize with `int(parts[0])`
- Selenium ChromeDriver: Auto-managed by `webdriver-manager` (no manual download needed)

## What NOT to Do (Critical Quicken Gotchas)

From [RepairQuickenTransactions.md](../RepairQuickenTransactions.md):
- **NEVER** use Quicken's "Added Shares" or "Removed Shares" actions for rebuilds → Use "Add Shares" transactions instead
- **NEVER** connect Quicken account to Schwab OFX downloads after manual rebuild → Stay manual forever
- **NEVER** accept placeholder transactions → Delete and rebuild properly
- **NEVER** use balance adjustments → Fix root cause instead
- **NEVER** import Schwab CSVs directly into Quicken → Must transform via scripts first

## Bucketing Strategy (schwab_quicken_rebuild.py)

**Why bucket?** Quicken slows down with 100+ lot entries per security. Grouping old lots reduces manual entry burden while preserving tax lot accuracy for recent trades.

**Algorithm** (`bucket_name_for()` lines 242-251):
```python
if acq_date < 2020-01-01: return "VERY_OLD"
if acq_date < 2023-01-01: return "OLD"
if acq_date < (as_of - 365d): return "LONG_RECENT"
return "SHORT_TERM"
```

**Quicken acquisition dates** (fixed for long-term buckets, dynamic for short-term):
- `VERY_OLD` → **12/31/2019** (guarantees long-term status)
- `OLD` → **12/31/2022** (guarantees long-term status)
- `LONG_RECENT` → **12/31/2025** (guarantees long-term status **if entered in 2026+**)
- `SHORT_TERM` → **file's as_of_date** (preserves short-term status for tax purposes)

### Why These Specific Dates?

**Tax optimization principle**: Long-term capital gains (held >365 days) are taxed at lower rates than short-term gains. The fixed dates ensure all bucketed lots qualify as long-term when sold.

**Date selection rationale**:

1. **12/31/2019 (VERY_OLD)**:
   - Any lot acquired before 2020 is guaranteed 6+ years old as of 2026
   - Using year-end avoids mid-year confusion when entering in Quicken
   - Safe margin: even if entered months later, still clearly long-term

2. **12/31/2022 (OLD)**:
   - Covers 2020-2022 period (3-5 years old)
   - Separates "really old" from "moderately old" for better granularity
   - Again uses year-end for clean bucketing

3. **12/31/2026 (LONG_RECENT)**:
   - Most recent year-end (current as of January 2026)
   - Ensures 1+ year holding period for long-term treatment when entered in 2027+
   - **Critical timing**: Update this date each year (e.g., 12/31/2027 in 2028)
   - If you're rebuilding in 2026, lots acquired in 2023-2025 use this date

4. **as_of_date (SHORT_TERM)**:
   - Uses the actual "as of" date from each Schwab CSV file
   - Preserves accurate holding period for tax reporting
   - These are lots likely to be sold soon, so exact acquisition date matters
   - Allows accurate short-term vs long-term distinction when selling specific lots

**Key insight**: The bucketing strategy is **time-sensitive**. The `LONG_RECENT` date (12/31/2026) is current as of January 2026. Each year, increment this date by one year (e.g., 12/31/2027 for 2027, etc.) to maintain the >365 day rule and ensure long-term capital gains treatment.

**Trade-offs**:
- ✅ **Benefit**: Aggregation reduces 100+ lot entries to 4 rows per security (massive time savings)
- ✅ **Benefit**: All bucketed lots qualify for long-term capital gains treatment
- ❌ **Limitation**: Lose specific lot selection within each bucket (can't use "specific identification" method for old lots)
- ❌ **Limitation**: Average cost basis within buckets (may differ slightly from actual per-lot basis)

**When bucketing is acceptable**:
- Old holdings you plan to hold indefinitely (won't sell specific lots)
- Diversified index funds where specific lot selection is impractical
- Accounts with hundreds of DRIP (dividend reinvestment) micro-lots

**When to preserve individual lots** (modify script to skip bucketing):
- Securities you plan to partially sell with specific lot selection
- Recent acquisitions within wash sale periods
- Lots with significantly different cost bases where you want tax-loss harvesting flexibility

**Critical**: This strategy **aggregates shares and cost basis** but loses individual lot granularity within each bucket. Acceptable for old lots where specific lot selection is unlikely.

## File Dependency Graph

```
../scrape_schwab_lots.py [account_name]
    ↓ generates
./lots-{account}/*.csv (e.g., 32 files in lots-Inheritance/)
    ↓ MANUAL STEP: mv lots-Inheritance/ lots/
    ↓ consumed by
../schwab_quicken_rebuild.py (reads from ./lots/)
    ↓ generates
quicken_addshares_checklist.csv ←─── Manual entry into Quicken
quicken_security_subtotals.csv       (See RepairQuickenTransactions.md)
schwab_quicken_validation_report.csv
    ↓ validates against (optional)
schwab_positions.csv (manual Schwab export: Positions summary)

Parallel validation path:
Quicken_Lots.csv (manual Quicken export)
    ↓ consumed by
../basis.py + ./lots/*.csv
    ↓ prints
Console discrepancy report

Legacy alternative:
schwab_lot_downloader.py → ./lots/*.csv (deprecated, use ../scrape_schwab_lots.py instead)
```

## Production Script vs Legacy Scripts

**Use `../schwab_quicken_rebuild.py`** for actual Quicken rebuilds because:
- Processes entire account in one run (not one ticker at a time)
- Buckets lots to reduce manual entry (4 rows per security vs 100+)
- Validates totals against Schwab positions export
- Outputs checklist optimized for manual Quicken entry

**Use `../basis.py`** only for:
- Post-rebuild validation (lot-by-lot comparison)
- Debugging individual ticker discrepancies
- Understanding the original comparison logic

## Code Style & Patterns

- **Type hints everywhere**: Python 3.7+ with full type annotations
- **Dataclasses for data models**: `@dataclass` for `Lot`, `Security`, `AggRow`
- **Fail-fast validation**: Check column existence immediately, raise `SystemExit` with helpful message
- **Decimal vs Float**: `../schwab_quicken_rebuild.py` uses `Decimal` for precision; `../basis.py` uses `float`
- **Path objects**: `Path()` throughout, never string concatenation
- **CSV robustness**: `encoding="utf-8-sig"`, handle `$`, `,`, `()`, `-`, `*`, `--` in numbers
- **Date parsing flexibility**: Try multiple formats (`%m/%d/%Y`, `%m/%d/%y`, `%Y-%m-%d`), fallback to regex

## Future Enhancements (Not Yet Implemented)

- Parse Schwab transaction history CSV (dividends, interest, fees)
- Handle realized lots CSV (closed positions with gains/losses)
- Automated Selenium 2FA via TOTP integration
- Direct Quicken QFX file generation (bypass manual CSV entry)
- Web UI for non-technical users
