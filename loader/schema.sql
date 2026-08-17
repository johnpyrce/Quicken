-- Schema definitions for Quicken QIF data in DuckDB

-- Accounts table
CREATE TABLE IF NOT EXISTS accounts (
    account_id INTEGER PRIMARY KEY,
    name VARCHAR,
    type VARCHAR,
    description VARCHAR,
    balance DECIMAL(15,2),
    credit_limit DECIMAL(15,2),
    note TEXT
);

-- Categories table
CREATE TABLE IF NOT EXISTS categories (
    category_id INTEGER PRIMARY KEY,
    name VARCHAR,
    description VARCHAR,
    expense_category BOOLEAN DEFAULT FALSE,
    income_category BOOLEAN DEFAULT FALSE,
    tax_related BOOLEAN DEFAULT FALSE,
    tax_schedule VARCHAR,
    parent_category VARCHAR
);

-- Category budget values captured from repeated category B lines.
CREATE TABLE IF NOT EXISTS category_budgets (
    category_budget_id INTEGER PRIMARY KEY,
    category_id INTEGER,
    amount DECIMAL(15,2),
    FOREIGN KEY (category_id) REFERENCES categories(category_id)
);

-- Security master data.
CREATE TABLE IF NOT EXISTS securities (
    security_id INTEGER PRIMARY KEY,
    name VARCHAR,
    symbol VARCHAR,
    security_type VARCHAR,
    raw_fields TEXT
);

-- Tags from !Type:Tag.
CREATE TABLE IF NOT EXISTS tags (
    tag_id INTEGER PRIMARY KEY,
    name VARCHAR,
    description VARCHAR,
    raw_fields TEXT
);

-- Classes from !Type:Class.
CREATE TABLE IF NOT EXISTS classes (
    class_id INTEGER PRIMARY KEY,
    name VARCHAR,
    description VARCHAR,
    raw_fields TEXT
);

-- Security prices from !Type:Price and !Type:Prices.
CREATE TABLE IF NOT EXISTS security_prices (
    price_id INTEGER PRIMARY KEY,
    security_symbol VARCHAR,
    date DATE,
    price DECIMAL(15,6),
    raw_fields TEXT
);

-- Transactions table
CREATE TABLE IF NOT EXISTS transactions (
    tx_id INTEGER PRIMARY KEY,
    account_id INTEGER,
    account_type VARCHAR,
    date DATE,
    payee VARCHAR,
    memo TEXT,
    amount DECIMAL(15,2),
    cleared VARCHAR,
    number VARCHAR,
    category VARCHAR,
    security VARCHAR,
    price DECIMAL(15,6),
    quantity DECIMAL(18,6),
    commission DECIMAL(15,2),
    percent DECIMAL(10,4),
    transfer_account VARCHAR,
    amount_u DECIMAL(15,2),
    action VARCHAR,
    address TEXT,
    raw_fields TEXT,
    FOREIGN KEY (account_id) REFERENCES accounts(account_id)
);

-- Transactions that were parsed but omitted from main transactions due to
-- missing required fields (date and/or amount).
CREATE TABLE IF NOT EXISTS transactions_rejected (
    rejected_tx_id INTEGER PRIMARY KEY,
    account_id INTEGER,
    account_type VARCHAR,
    date DATE,
    payee VARCHAR,
    memo TEXT,
    amount DECIMAL(15,2),
    cleared VARCHAR,
    number VARCHAR,
    category VARCHAR,
    security VARCHAR,
    price DECIMAL(15,6),
    quantity DECIMAL(18,6),
    commission DECIMAL(15,2),
    percent DECIMAL(10,4),
    transfer_account VARCHAR,
    amount_u DECIMAL(15,2),
    action VARCHAR,
    address TEXT,
    raw_fields TEXT,
    raw_lines TEXT,
    rejection_reason VARCHAR,
    FOREIGN KEY (account_id) REFERENCES accounts(account_id)
);

-- Transaction splits table (for transactions split across multiple categories)
CREATE TABLE IF NOT EXISTS transaction_splits (
    split_id INTEGER PRIMARY KEY,
    tx_id INTEGER,
    category VARCHAR,
    amount DECIMAL(15,2),
    memo TEXT,
    FOREIGN KEY (tx_id) REFERENCES transactions(tx_id)
);

-- Useful views for common queries

-- Transactions with category information
CREATE OR REPLACE VIEW transactions_with_categories AS
SELECT
    t.*,
    c.description as category_description,
    c.expense_category,
    c.income_category,
    c.tax_related
FROM transactions t
LEFT JOIN categories c ON t.category = c.name;

-- Monthly summaries
CREATE OR REPLACE VIEW monthly_summaries AS
SELECT
    strftime('%Y-%m', date) as month,
    category,
    COUNT(*) as transaction_count,
    SUM(amount) as total_amount,
    AVG(amount) as avg_amount,
    MIN(amount) as min_amount,
    MAX(amount) as max_amount
FROM transactions
WHERE date IS NOT NULL
GROUP BY strftime('%Y-%m', date), category
ORDER BY month DESC, total_amount DESC;

-- Category summaries
CREATE OR REPLACE VIEW category_summaries AS
SELECT
    category,
    COUNT(*) as transaction_count,
    SUM(amount) as total_amount,
    AVG(amount) as avg_amount,
    MIN(date) as first_transaction,
    MAX(date) as last_transaction
FROM transactions
WHERE category IS NOT NULL
GROUP BY category
ORDER BY total_amount DESC;

-- Account type summaries
CREATE OR REPLACE VIEW account_type_summaries AS
SELECT
    account_type,
    COUNT(*) as transaction_count,
    SUM(amount) as total_amount,
    AVG(amount) as avg_amount
FROM transactions
WHERE account_type IS NOT NULL
GROUP BY account_type
ORDER BY total_amount DESC;

-- Recent schema mirrors main objects but constrained to the last 5 years
-- relative to the latest transaction date.
CREATE SCHEMA IF NOT EXISTS recent;

-- Remove deprecated last_5_years views in case they exist from older schema versions.
DROP VIEW IF EXISTS main.transactions_last_5_years;
DROP VIEW IF EXISTS main.accounts_last_5_years;
DROP VIEW IF EXISTS recent.transactions_last_5_years;
DROP VIEW IF EXISTS recent.accounts_last_5_years;

CREATE OR REPLACE VIEW recent.transactions AS
WITH latest_transaction AS (
    SELECT MAX(date) AS max_date
    FROM main.transactions
    WHERE date IS NOT NULL
)
SELECT t.*
FROM main.transactions t
CROSS JOIN latest_transaction lt
WHERE t.date IS NOT NULL
  AND lt.max_date IS NOT NULL
  AND t.date >= lt.max_date - INTERVAL '5 years';

CREATE OR REPLACE VIEW recent.transactions_rejected AS
WITH latest_transaction AS (
    SELECT MAX(date) AS max_date
    FROM main.transactions
    WHERE date IS NOT NULL
)
SELECT t.*
FROM main.transactions_rejected t
CROSS JOIN latest_transaction lt
WHERE t.date IS NOT NULL
  AND lt.max_date IS NOT NULL
  AND t.date >= lt.max_date - INTERVAL '5 years';

CREATE OR REPLACE VIEW recent.security_prices AS
WITH latest_transaction AS (
    SELECT MAX(date) AS max_date
    FROM main.transactions
    WHERE date IS NOT NULL
)
SELECT p.*
FROM main.security_prices p
CROSS JOIN latest_transaction lt
WHERE p.date IS NOT NULL
  AND lt.max_date IS NOT NULL
  AND p.date >= lt.max_date - INTERVAL '5 years';

CREATE OR REPLACE VIEW recent.transaction_splits AS
SELECT s.*
FROM main.transaction_splits s
JOIN recent.transactions t ON t.tx_id = s.tx_id;

CREATE OR REPLACE VIEW recent.accounts AS
SELECT a.*
FROM main.accounts a
WHERE EXISTS (
    SELECT 1
    FROM recent.transactions t
    WHERE t.account_id = a.account_id
);

CREATE OR REPLACE VIEW recent.categories AS
SELECT c.*
FROM main.categories c
WHERE EXISTS (
    SELECT 1
    FROM recent.transactions t
    WHERE t.category = c.name
)
OR EXISTS (
    SELECT 1
    FROM recent.transaction_splits s
    WHERE s.category = c.name
);

CREATE OR REPLACE VIEW recent.category_budgets AS
SELECT cb.*
FROM main.category_budgets cb
JOIN recent.categories c ON c.category_id = cb.category_id;

CREATE OR REPLACE VIEW recent.securities AS
SELECT s.*
FROM main.securities s
WHERE EXISTS (
    SELECT 1
    FROM recent.transactions t
    WHERE t.security = s.symbol
)
OR EXISTS (
    SELECT 1
    FROM recent.security_prices p
    WHERE p.security_symbol = s.symbol
);

CREATE OR REPLACE VIEW recent.classes AS
SELECT * FROM main.classes;

CREATE OR REPLACE VIEW recent.tags AS
SELECT * FROM main.tags;

CREATE OR REPLACE VIEW recent.transactions_with_categories AS
SELECT
    t.*,
    c.description as category_description,
    c.expense_category,
    c.income_category,
    c.tax_related
FROM recent.transactions t
LEFT JOIN recent.categories c ON t.category = c.name;

CREATE OR REPLACE VIEW recent.monthly_summaries AS
SELECT
    strftime('%Y-%m', date) as month,
    category,
    COUNT(*) as transaction_count,
    SUM(amount) as total_amount,
    AVG(amount) as avg_amount,
    MIN(amount) as min_amount,
    MAX(amount) as max_amount
FROM recent.transactions
WHERE date IS NOT NULL
GROUP BY strftime('%Y-%m', date), category
ORDER BY month DESC, total_amount DESC;

CREATE OR REPLACE VIEW recent.category_summaries AS
SELECT
    category,
    COUNT(*) as transaction_count,
    SUM(amount) as total_amount,
    AVG(amount) as avg_amount,
    MIN(date) as first_transaction,
    MAX(date) as last_transaction
FROM recent.transactions
WHERE category IS NOT NULL
GROUP BY category
ORDER BY total_amount DESC;

CREATE OR REPLACE VIEW recent.account_type_summaries AS
SELECT
    account_type,
    COUNT(*) as transaction_count,
    SUM(amount) as total_amount,
    AVG(amount) as avg_amount
FROM recent.transactions
WHERE account_type IS NOT NULL
GROUP BY account_type
ORDER BY total_amount DESC;
