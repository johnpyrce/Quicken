# Repair Quicken Registry

Below is a **battle-tested, Schwab-as-source-of-truth procedure** to rebuild a **clean Quicken investment account using *only* Schwab exports**. This avoids OFX corruption, phantom cash, broken cost basis, and the classic *Added/Removed* disasters.

I’ll assume **Quicken for Windows**, since that’s what makes this practical and reliable. (If you’re on Mac, say so and I’ll adapt it.)

---

# Goal (very important)

You want:

* **Correct share balances**
* **Correct lot-level cost basis**
* **Correct realized gains**
* **No hidden adjustments**
* **No reliance on Quicken downloads**

Schwab = **authoritative**
Quicken = **ledger + reporting tool**

---

# Files you need from Schwab (do not skip any)

You need **three CSV exports** from Schwab for the account.

## 1️⃣ Transactions (full history)

From **Accounts → History**

* Date range: **Inception → Today**
* Transaction type: **All**
* Format: **CSV**

Purpose:

* Cash flow timeline
* Dividends, interest, fees
* Buys & sells (but *not* authoritative for basis)

---

## 2️⃣ Unrealized Cost Basis (open lots)

From **Accounts → Positions → Cost Basis**

* View: **Unrealized**
* Download: **CSV**

Purpose:

* Open lots
* Share counts
* Per-lot basis (this is gold)

---

## 3️⃣ Realized Cost Basis (closed lots)

Same place:

* View: **Realized**
* Download: **CSV**

Purpose:

* Closed positions
* Taxable gain/loss truth
* Wash sale adjustments

---

# Step-by-step rebuild in Quicken (clean & deterministic)

## STEP 0 — Back up everything

**File → Backup and Restore → Backup**
Name it something like:

```
Before_Schwab_Rebuild_2025-01-XX.QDF
```

---

## STEP 1 — Create a NEW manual investment account

Do **not** connect to Schwab.

**Tools → Add Account**

* Investment
* Brokerage
* **Advanced setup**
* **Manual account**
* Name it something explicit:

  ```
  Schwab Taxable (Rebuilt 2025)
  ```

---

## STEP 2 — Set correct opening cash

From Schwab:

* Look at **starting cash** on the earliest transaction date

In Quicken:

* Enter **Opening Balance** with that exact cash amount
* Date = day *before* first transaction

This prevents:

* Phantom cash
* Balancing adjustments later

---

## STEP 3 — Recreate CLOSED positions (realized lots)

This is the **most important ordering rule**.

For each row in **Realized Cost Basis CSV**:

Create a **Sell** transaction:

* Security name (create if needed)
* Shares
* Sale date
* Proceeds
* **Specify lots manually**

  * Acquisition date
  * Cost basis per Schwab

Result:

* Realized gains in Quicken **match Schwab exactly**
* No guessing, no FIFO surprises

⚠️ Do **not** import buys for these lots — they are already embedded in the realized data.

---

## STEP 4 — Recreate OPEN lots (unrealized)

From **Unrealized Cost Basis CSV**:

For each lot:

* Enter a **Buy**

  * Acquisition date = Schwab lot date
  * Shares
  * Total cost (exact)
  * No commissions unless Schwab shows them

Result:

* Open positions match Schwab
* Per-lot basis is exact
* Capital gains reports will now reconcile

---

## STEP 5 — Recreate income & fees (transactions CSV)

Now process **Transactions CSV**, but **selectively**.

Enter:

* Dividends
* Reinvested dividends *(if applicable)*
* Interest
* Fees
* Cash transfers

Skip:

* Buys
* Sells
* Corporate actions already reflected in basis

Why:

* Basis is already authoritative from Steps 3 & 4
* Double-entering trades is how Quicken gets corrupted

---

## STEP 6 — Validate (non-negotiable)

### A. Share counts

**Investing → Portfolio**

* Compare share counts security-by-security with Schwab

### B. Cost basis

Run:

* **Reports → Investing → Capital Gains**
* Compare totals to Schwab **Realized Gain/Loss**

### C. Cash balance

Cash should match Schwab to the cent.
If not:

* You missed a dividend, fee, or transfer
* Do **not** use “Balance Adjustment”

---

# What NOT to do (these break accuracy)

❌ Do NOT connect the account to Schwab afterward
❌ Do NOT accept downloaded “placeholder” transactions
❌ Do NOT use *Added* or *Removed* shares
❌ Do NOT rely on Quicken’s average cost repair tools
❌ Do NOT import Schwab CSVs directly (they’re not Quicken-safe)

---

# Why this works (and keeps working)

* Schwab basis is **tax-grade accurate**
* Quicken’s strength is **reporting**, not ingestion
* Manual lots eliminate OFX ambiguity
* Future activity can be entered monthly or quarterly

Many long-time Quicken users do this once and **never reconnect downloads again**.

---

# Optional: make this much faster (your kind of workflow)

Given your past Python/CSV work, you can:

* Auto-generate a **Quicken entry checklist** per lot
* Validate Schwab vs Quicken mathematically
* Detect missing income automatically

If you want, I can:

* Give you a **Python script** that reads Schwab CSVs and outputs:

  * Buy/Sell instructions
  * Lot totals
  * Validation summaries
* Or create a **step-by-step checklist template** you can reuse for other accounts

Just tell me:
**Taxable or IRA?**
**How many years of history?**
