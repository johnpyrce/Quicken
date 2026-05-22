A good approach is to give ChatGPT:

1. the schema/context
2. your goals
3. the type of analysis you want
4. constraints/preferences

Here are some strong starter prompts for Quicken + DuckDB data.

⸻

General Financial Analyst Prompt

    You are a financial data analyst.
    I am uploading exported transaction data from my Quicken/DuckDB database.
    Important columns:
    - date
    - account
    - category
    - payee
    - amount
    - memo
    Conventions:
    - Negative amounts are spending
    - Positive amounts are income or transfers
    - Categories may be inconsistent
    - Some payees have multiple spellings
    Tasks:
    1. Clean and normalize the data where reasonable
    2. Identify duplicate or near-duplicate payees
    3. Exclude transfers from spending analysis where possible
    4. Produce insights, trends, anomalies, and useful summaries
    5. Suggest additional analyses that would be valuable
    When presenting results:
    - Use tables
    - Show yearly and monthly trends
    - Highlight unusual findings
    - Explain assumptions

⸻

Spending Analysis

```
Analyze my spending patterns.
Please:
- Identify top spending categories
- Show monthly trends
- Find categories increasing fastest
- Compare this year vs prior years
- Detect unusual spikes
- Identify subscriptions or recurring charges
- Find merchants with unexpectedly high spend
- Suggest areas where spending behavior changed
Create charts where useful.
```

⸻

Net Worth / Cash Flow

```
Analyze my cash flow.
Tasks:
- Compute monthly net cash flow
- Separate income, spending, transfers, and investments
- Show rolling averages
- Identify seasonal patterns
- Identify months with unusually high spending
- Show largest contributors to cash flow volatility
```

⸻

Investment Analysis

```
Analyze my investment-related transactions.
Tasks:
- Group buys/sells/dividends
- Estimate contributions over time
- Identify realized gains/loss patterns if possible
- Detect wash-sale-like behavior
- Summarize activity by account and security
```

⸻

Category Cleanup

This is extremely useful with Quicken exports.

```
Help me normalize my transaction categories and payees.
Tasks:
- Detect likely duplicate payees
- Suggest category merges
- Identify uncategorized or suspicious transactions
- Create a mapping table of inconsistent payees to canonical names
```

⸻

Advanced “Find Interesting Things” Prompt

This works surprisingly well.

```
Study this financial dataset and look for:
- anomalies
- behavioral changes
- recurring patterns
- hidden subscriptions
- unusual merchants
- tax-relevant items
- possible categorization mistakes
- duplicate transactions
- outlier months
- meaningful long-term trends
Do not just summarize totals.
Find things that a careful financial advisor or forensic accountant would notice.
```

⸻

SQL Assistant Prompt

Since your data is already in DuckDB, this can be very powerful.

```
I have a DuckDB database containing Quicken transaction data.
Help me write DuckDB SQL queries for financial analysis.
Assume tables may contain:
- transactions
- accounts
- securities
- prices
- categories
Prefer:
- efficient DuckDB SQL
- window functions
- pivoting
- parquet-friendly approaches
- queries suitable for dashboards
Explain each query.
```

⸻

Recommended Workflow for You

Given your existing setup with DuckDB + Python + dashboards:

Best architecture

Quicken/QIF
    ↓
Python ETL
    ↓
DuckDB
    ↓
Curated analysis views
    ↓
Export small focused datasets
    ↓
ChatGPT analysis

This is much better than uploading the raw Quicken export.

⸻

Particularly Valuable Analyses for Your Data

Based on the kinds of projects you’ve been building:

* recurring subscription detection
* utility spending seasonality
* insurance/tax tracking
* merchant normalization
* investment cash flow timelines
* anomaly detection
* budget drift
* inflation-adjusted spending
* account transfer graph analysis
* “what changed this month?” reports

⸻

One Prompt I’d Especially Recommend

```
Act as a combination forensic accountant, financial planner, and data scientist.
Analyze this Quicken transaction export.
Your goals:
- detect meaningful trends
- identify anomalies
- explain changes over time
- identify recurring payments
- find opportunities for cleanup or optimization
- identify likely categorization problems
- produce actionable insights
Avoid generic summaries.
Focus on findings that are surprising, statistically unusual, or financially meaningful.
```
