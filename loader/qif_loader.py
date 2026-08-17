"""QIF file parser and DuckDB loader for the Quicken utilities."""

import json
import logging
import re
from datetime import datetime
from decimal import InvalidOperation
from pathlib import Path
from time import perf_counter
from typing import Dict, List, Optional

import pyarrow as pa

logger = logging.getLogger(__name__)


SKIPPED_SECTION_TYPES = {'memorized', 'budget'}
CACHE_MISS = object()

AMOUNT_CLEANUP_TABLE = str.maketrans('', '', ',$ \t\r\n')
DATE_PATTERNS = [
    re.compile(r'^\d{1,2}/\d{1,2}/\d{2}$'),
    re.compile(r'^\d{1,2}/\d{1,2}/\d{4}$'),
    re.compile(r'^\d{1,2}-\d{1,2}-\d{2}$'),
    re.compile(r'^\d{1,2}-\d{1,2}-\d{4}$'),
    re.compile(r'^\d{4}-\d{2}-\d{2}$'),
]
FRACTIONAL_AMOUNT_PATTERN = re.compile(r'^\s*([-+]?\d+)\s+(\d+)/(\d+)\s*$')


class QIFParser:
    """Parser for Quicken Interchange Format (QIF) files."""

    def __init__(self):
        self.accounts = []
        self.categories = []
        self.category_budgets = []
        self.transactions = []
        self.rejected_transactions = []
        self.securities = []
        self.tags = []
        self.classes = []
        self.security_prices = {
            'price_id': [],
            'security_symbol': [],
            'date': [],
            'price': [],
            'raw_fields': [],
        }
        self.current_account = None
        self._date_cache: Dict[str, Optional[str]] = {}
        self._amount_cache: Dict[str, Optional[float]] = {}

    def parse_file(self, file_path: str) -> Dict[str, List]:
        """Parse a QIF file and return structured data."""
        logger.info(f"Parsing QIF file: {file_path}")

        with open(file_path, 'r', encoding='utf-8', errors='ignore') as file_handle:
            content = file_handle.read()

        return self._parse_content(content)

    def _parse_content(self, content: str) -> Dict[str, List]:
        """Parse QIF content into structured data."""
        lines = [line.strip() for line in content.splitlines()]
        index = 0

        while index < len(lines):
            line = lines[index]

            if not line:
                index += 1
                continue

            if line == '!Option:AutoSwitch':
                index += 1
                continue

            if line == '!Clear:AutoSwitch':
                index += 1
                continue

            if line == '!Account':
                index = self._parse_accounts_section(lines, index + 1)
                continue

            if line.startswith('!Type:'):
                section_type = line.split(':', 1)[1].strip()
                lowered_type = section_type.lower()
                if lowered_type in ('cat', 'category', 'categories'):
                    index = self._parse_categories_section(lines, index + 1)
                elif lowered_type == 'tag':
                    index = self._parse_tags_section(lines, index + 1)
                elif lowered_type == 'class':
                    index = self._parse_classes_section(lines, index + 1)
                elif lowered_type in ('security', 'securities'):
                    index = self._parse_securities_section(lines, index + 1)
                elif lowered_type in ('price', 'prices'):
                    index = self._parse_prices_section(lines, index + 1)
                elif lowered_type in SKIPPED_SECTION_TYPES:
                    index = self._skip_section(lines, index + 1)
                else:
                    index = self._parse_transactions_section(lines, index + 1, section_type, self.current_account)
                continue

            if line.startswith('N') and index + 1 < len(lines) and lines[index + 1].startswith('D'):
                next_line = lines[index + 1][1:] if lines[index + 1] else ''
                if self._looks_like_date(next_line):
                    index = self._parse_transactions_section(lines, index, 'Unknown', self.current_account)
                else:
                    index = self._parse_category_definition(lines, index)
                continue

            if line.startswith('D') and self._looks_like_date(line[1:]):
                index = self._parse_transactions_section(lines, index, 'Unknown', self.current_account)
                continue

            index += 1

        logger.info(
            'Parsed %s accounts, %s categories, %s transactions, %s securities, %s tags, %s classes, %s prices',
            len(self.accounts),
            len(self.categories),
            len(self.transactions),
            len(self.securities),
            len(self.tags),
            len(self.classes),
            _security_price_count(self.security_prices),
        )

        return {
            'accounts': self.accounts,
            'categories': self.categories,
            'category_budgets': self.category_budgets,
            'transactions': self.transactions,
            'rejected_transactions': self.rejected_transactions,
            'securities': self.securities,
            'tags': self.tags,
            'classes': self.classes,
            'security_prices': self.security_prices,
        }

    def _parse_accounts_section(self, lines: List[str], start_idx: int) -> int:
        """Parse the accounts section."""
        index = start_idx

        while index < len(lines):
            line = lines[index]

            if line == '^':
                index += 1
                continue

            if line.startswith('!') or (not line.startswith(('N', 'T', 'D', 'B', 'L', 'A'))):
                break

            account = {}
            while index < len(lines) and lines[index] != '^':
                line = lines[index]
                if line.startswith('N'):
                    account['name'] = line[1:]
                elif line.startswith('T'):
                    account['type'] = line[1:]
                elif line.startswith('D'):
                    account['description'] = line[1:]
                elif line.startswith('B'):
                    try:
                        account['balance'] = float(line[1:]) if line[1:] else 0.0
                    except ValueError:
                        account['balance'] = 0.0
                elif line.startswith('L'):
                    try:
                        account['credit_limit'] = float(line[1:]) if line[1:] else None
                    except ValueError:
                        account['credit_limit'] = None
                elif line.startswith('A'):
                    account['note'] = line[1:]
                index += 1

            if account.get('name'):
                account['account_id'] = len(self.accounts) + 1
                self.accounts.append(account)
                self.current_account = account

        return index

    def _parse_category_definition(self, lines: List[str], start_idx: int) -> int:
        """Parse a category definition."""
        index = start_idx
        category = {}
        budgets = []

        while index < len(lines) and lines[index] != '^':
            line = lines[index]
            if line.startswith('N'):
                category['name'] = line[1:]
            elif line.startswith('D'):
                category['description'] = line[1:]
            elif line.startswith('E'):
                category['expense_category'] = True
            elif line.startswith('I'):
                category['income_category'] = True
            elif line.startswith('T'):
                category['tax_related'] = True
            elif line.startswith('R'):
                category['tax_schedule'] = line[1:]
            elif line.startswith('B'):
                parsed_budget = self._parse_amount(line[1:])
                if parsed_budget is not None:
                    budgets.append(parsed_budget)
            index += 1

        if category.get('name'):
            category['category_id'] = len(self.categories) + 1
            self.categories.append(category)
            for budget in budgets:
                self.category_budgets.append({
                    'category_budget_id': len(self.category_budgets) + 1,
                    'category_id': category['category_id'],
                    'amount': budget,
                })

        return index + 1

    def _parse_categories_section(self, lines: List[str], start_idx: int) -> int:
        """Parse a !Type:Cat categories section."""
        index = start_idx

        while index < len(lines):
            line = lines[index]
            if line.startswith('!'):
                break

            if not line or line == '^':
                index += 1
                continue

            index = self._parse_category_definition(lines, index)

        return index

    def _parse_tags_section(self, lines: List[str], start_idx: int) -> int:
        """Parse !Type:Tag section."""
        index = start_idx
        while index < len(lines):
            line = lines[index]
            if line.startswith('!'):
                break

            if not line or line == '^':
                index += 1
                continue

            tag: Dict[str, object] = {'tag_id': len(self.tags) + 1}
            while index < len(lines) and lines[index] != '^':
                entry = lines[index]
                if entry.startswith('N'):
                    tag['name'] = entry[1:]
                elif entry.startswith('D'):
                    tag['description'] = entry[1:]
                else:
                    raw_fields = tag.get('raw_fields')
                    if not isinstance(raw_fields, dict):
                        raw_fields = {}
                        tag['raw_fields'] = raw_fields
                    raw_fields[entry[:1]] = entry[1:]
                index += 1

            if tag.get('name'):
                tag['raw_fields'] = json.dumps(tag.get('raw_fields', {}), separators=(',', ':'))
                self.tags.append(tag)
            index += 1

        return index

    def _parse_classes_section(self, lines: List[str], start_idx: int) -> int:
        """Parse !Type:Class section."""
        index = start_idx
        while index < len(lines):
            line = lines[index]
            if line.startswith('!'):
                break

            if not line or line == '^':
                index += 1
                continue

            class_record: Dict[str, object] = {'class_id': len(self.classes) + 1}
            while index < len(lines) and lines[index] != '^':
                entry = lines[index]
                if entry.startswith('N'):
                    class_record['name'] = entry[1:]
                elif entry.startswith('D'):
                    class_record['description'] = entry[1:]
                else:
                    raw_fields = class_record.get('raw_fields')
                    if not isinstance(raw_fields, dict):
                        raw_fields = {}
                        class_record['raw_fields'] = raw_fields
                    raw_fields[entry[:1]] = entry[1:]
                index += 1

            if class_record.get('name'):
                class_record['raw_fields'] = json.dumps(class_record.get('raw_fields', {}), separators=(',', ':'))
                self.classes.append(class_record)
            index += 1

        return index

    def _parse_securities_section(self, lines: List[str], start_idx: int) -> int:
        """Parse !Type:Security section."""
        index = start_idx
        while index < len(lines):
            line = lines[index]
            if line.startswith('!'):
                break

            if not line or line == '^':
                index += 1
                continue

            security: Dict[str, object] = {'security_id': len(self.securities) + 1}
            while index < len(lines) and lines[index] != '^':
                entry = lines[index]
                if entry.startswith('N'):
                    security['name'] = entry[1:]
                elif entry.startswith('S'):
                    security['symbol'] = entry[1:]
                elif entry.startswith('T'):
                    security['security_type'] = entry[1:]
                else:
                    raw_fields = security.get('raw_fields')
                    if not isinstance(raw_fields, dict):
                        raw_fields = {}
                        security['raw_fields'] = raw_fields
                    raw_fields[entry[:1]] = entry[1:]
                index += 1

            if security.get('name') or security.get('symbol'):
                security['raw_fields'] = json.dumps(security.get('raw_fields', {}), separators=(',', ':'))
                self.securities.append(security)
            index += 1

        return index

    def _parse_prices_section(self, lines: List[str], start_idx: int) -> int:
        """Parse !Type:Price/!Type:Prices section."""
        index = start_idx
        security_prices = self.security_prices
        price_ids = security_prices['price_id']
        security_symbols = security_prices['security_symbol']
        dates = security_prices['date']
        prices = security_prices['price']
        raw_fields_column = security_prices['raw_fields']
        parse_amount = self._parse_amount
        parse_date = self._parse_date
        next_price_id = len(price_ids) + 1
        while index < len(lines):
            line = lines[index]
            if line.startswith('!'):
                break

            if not line or line == '^':
                index += 1
                continue

            # Quicken price history lines are formatted as: "SYMBOL",price,"date"
            # where symbol and date are quoted and price is unquoted.
            if line[0] == '"' and ',' in line:
                security_symbol = None
                parsed_price = None
                parsed_date = None

                symbol_end = line.find('"', 1)
                comma_after_symbol = symbol_end + 1
                line_length = len(line)
                if symbol_end > 1 and comma_after_symbol < line_length and line[comma_after_symbol] == ',':
                    price_start = comma_after_symbol + 1
                    while price_start < line_length and line[price_start] == ' ':
                        price_start += 1

                    price_end = line.find(',', price_start)
                    if price_end == -1:
                        price_stop = line_length
                        while price_stop > price_start and line[price_stop - 1] == ' ':
                            price_stop -= 1
                        date = None
                    else:
                        price_stop = price_end
                        while price_stop > price_start and line[price_stop - 1] == ' ':
                            price_stop -= 1

                        date_start = price_end + 1
                        while date_start < line_length and line[date_start] == ' ':
                            date_start += 1

                        date = None
                        if date_start < line_length:
                            if line[date_start] == '"':
                                date_end = line.rfind('"')
                                if date_end > date_start:
                                    date = line[date_start + 1:date_end]
                            else:
                                date = line[date_start:].strip()

                    symbol = line[1:symbol_end]
                    price = line[price_start:price_stop]
                    security_symbol = symbol
                    parsed_price = parse_amount(price)
                    if date:
                        parsed_date = parse_date(date)

                price_ids.append(next_price_id)
                security_symbols.append(security_symbol)
                dates.append(parsed_date)
                prices.append(parsed_price)
                raw_fields_column.append(None)
                next_price_id += 1
                index += 1
                continue

            price_record: Dict[str, object] = {'price_id': next_price_id}
            while index < len(lines) and lines[index] != '^':
                entry = lines[index]
                if entry.startswith('Y'):
                    price_record['security_symbol'] = entry[1:]
                elif entry.startswith('I'):
                    price_record['price'] = parse_amount(entry[1:])
                elif entry.startswith('D'):
                    price_record['date'] = parse_date(entry[1:])
                else:
                    raw_fields = price_record.get('raw_fields')
                    if not isinstance(raw_fields, dict):
                        raw_fields = {}
                        price_record['raw_fields'] = raw_fields
                    raw_fields[entry[:1]] = entry[1:]
                index += 1

            if price_record.get('security_symbol') or price_record.get('price') is not None:
                raw_fields = json.dumps(price_record.get('raw_fields', {}), separators=(',', ':'))
                price_ids.append(next_price_id)
                security_symbols.append(price_record.get('security_symbol'))
                dates.append(price_record.get('date'))
                prices.append(price_record.get('price'))
                raw_fields_column.append(raw_fields)
                next_price_id += 1
            index += 1

        return index

    def _skip_section(self, lines: List[str], start_idx: int) -> int:
        """Skip unsupported !Type sections until the next section header."""
        index = start_idx
        while index < len(lines):
            if lines[index].startswith('!'):
                break
            index += 1
        return index

    def _parse_transactions_section(
        self,
        lines: List[str],
        start_idx: int,
        account_type: str,
        account: Optional[Dict] = None,
    ) -> int:
        """Parse a transactions section."""
        index = start_idx
        is_investment_account = self._is_investment_account_type(account_type)
        account_id = account.get('account_id') if account else None

        while index < len(lines):
            line = lines[index]
            if line.startswith('!'):
                break

            parsed_tx: Dict[str, object] = {}
            transaction_start = index
            while index < len(lines) and lines[index] != '^':
                line = lines[index]
                if line:
                    self._parse_transaction_line(parsed_tx, line, is_investment_account=is_investment_account)
                index += 1

            if parsed_tx:
                self._finalize_transaction(parsed_tx)
                has_date = parsed_tx.get('date') is not None
                has_amount = parsed_tx.get('amount') is not None
                is_complete = has_date and (has_amount or is_investment_account)
                parsed_tx['account_type'] = account_type
                if account_id is not None:
                    parsed_tx['account_id'] = account_id

                if is_complete:
                    parsed_tx['tx_id'] = len(self.transactions) + 1
                    self.transactions.append(parsed_tx)
                else:
                    parsed_tx['rejected_tx_id'] = len(self.rejected_transactions) + 1
                    if parsed_tx.get('date') is None and parsed_tx.get('amount') is None:
                        parsed_tx['rejection_reason'] = 'missing_date_and_amount'
                    elif parsed_tx.get('date') is None:
                        parsed_tx['rejection_reason'] = 'missing_date'
                    else:
                        parsed_tx['rejection_reason'] = 'missing_amount'
                    parsed_tx['raw_lines'] = '\n'.join(line for line in lines[transaction_start:index] if line)
                    self.rejected_transactions.append(parsed_tx)

            index += 1

        return index

    def _parse_transaction_lines(self, lines: List[str], is_investment_account: bool = False) -> Optional[Dict]:
        """Parse individual transaction lines."""
        transaction = {}

        for line in lines:
            if not line:
                continue

            self._parse_transaction_line(transaction, line, is_investment_account=is_investment_account)

        self._finalize_transaction(transaction)

        if transaction:
            return transaction

        return None

    def _parse_transaction_line(self, transaction: Dict, line: str, is_investment_account: bool = False) -> None:
        """Parse one normalized transaction line into an in-progress transaction."""
        if line.startswith('TX'):
            transaction.setdefault('raw_fields', {}).setdefault('TX', []).append(line[2:])
            return

        code = line[0]
        value = line[1:] if len(line) > 1 else ''

        if code == 'D':
            transaction['date'] = self._parse_date(value)
        elif code == 'P':
            transaction['payee'] = value
        elif code == 'M':
            transaction['memo'] = value
        elif code == 'T':
            transaction['amount'] = self._parse_amount(value)
        elif code == 'C':
            transaction['cleared'] = value
        elif code == 'N':
            if is_investment_account:
                transaction['action'] = value
            else:
                transaction['number'] = value
        elif code == 'L':
            transaction['category'] = value
        elif code == 'Y':
            transaction['security'] = value
        elif code == 'I':
            transaction['price'] = self._parse_amount(value)
        elif code == 'Q':
            transaction['quantity'] = self._parse_amount(value)
        elif code == 'O':
            transaction['commission'] = self._parse_amount(value)
        elif code == '%':
            transaction['percent'] = self._parse_amount(value)
        elif code == 'X':
            transaction['transfer_account'] = value
        elif code == 'U':
            transaction['amount_u'] = self._parse_amount(value)
        elif code == 'F':
            transaction['action'] = value
        elif code == 'A':
            transaction.setdefault('address', []).append(value)
        elif code == 'S':
            transaction.setdefault('splits', []).append({'category': value})
        elif code == '$':
            if 'splits' in transaction and transaction['splits']:
                transaction['splits'][-1]['amount'] = self._parse_amount(value)
        elif code == 'E':
            if 'splits' in transaction and transaction['splits']:
                transaction['splits'][-1]['memo'] = value
        else:
            transaction.setdefault('raw_fields', {}).setdefault(code, []).append(value)

    @staticmethod
    def _finalize_transaction(transaction: Dict) -> None:
        """Convert collected multi-value fields into load-ready scalars."""
        if transaction.get('address') and isinstance(transaction['address'], list):
            transaction['address'] = '\n'.join(transaction['address'])
        if transaction.get('raw_fields') and isinstance(transaction['raw_fields'], dict):
            transaction['raw_fields'] = json.dumps(transaction['raw_fields'], separators=(',', ':'))

    def _parse_date(self, date_str: str) -> Optional[str]:
        """Parse various date formats into ISO format."""
        if not date_str:
            return None

        cached = self._date_cache.get(date_str, CACHE_MISS)
        if cached is not CACHE_MISS:
            return cached

        normalized = date_str.replace("'", '/').replace(' ', '0')
        parsed_date = self._parse_date_fast(normalized)
        if parsed_date is not None:
            self._date_cache[date_str] = parsed_date
            return parsed_date

        for fmt in ('%m/%d/%y', '%m/%d/%Y', '%m-%d-%y', '%m-%d-%Y', '%Y-%m-%d'):
            try:
                dt = datetime.strptime(normalized, fmt)
                if dt.year < 1950:
                    dt = dt.replace(year=dt.year + 100)
                parsed_date = dt.strftime('%Y-%m-%d')
                break
            except ValueError:
                continue

        self._date_cache[date_str] = parsed_date
        return parsed_date

    @staticmethod
    def _parse_date_fast(normalized: str) -> Optional[str]:
        """Fast path for common QIF date formats."""
        try:
            if len(normalized) == 10 and normalized[4] == '-' and normalized[7] == '-':
                year = int(normalized[:4])
                month = int(normalized[5:7])
                day = int(normalized[8:10])
            else:
                separator = '/' if '/' in normalized else '-'
                first, second, third = normalized.split(separator, 2)
                month = int(first)
                day = int(second)
                year = int(third)
                if year < 100:
                    year += 2000 if year <= 68 else 1900

            return datetime(year, month, day).date().isoformat()
        except (TypeError, ValueError):
            return None

    def _parse_amount(self, amount_str: str) -> Optional[float]:
        """Parse amount string to float, including fractional notation (e.g. '41 1/4')."""
        if not amount_str:
            return None

        cached = self._amount_cache.get(amount_str, CACHE_MISS)
        if cached is not CACHE_MISS:
            return cached

        cleaned = amount_str.translate(AMOUNT_CLEANUP_TABLE)
        if not cleaned:
            self._amount_cache[amount_str] = None
            return None

        try:
            parsed_amount = float(cleaned)
        except (ValueError, InvalidOperation):
            # Handle fractional notation like "41 1/4" or "-3 1/2".
            frac_match = FRACTIONAL_AMOUNT_PATTERN.match(amount_str)
            if frac_match:
                whole = int(frac_match.group(1))
                numer = int(frac_match.group(2))
                denom = int(frac_match.group(3))
                if denom != 0:
                    sign = -1 if whole < 0 else 1
                    parsed_amount = whole + sign * numer / denom
                else:
                    parsed_amount = None
            else:
                parsed_amount = None

        self._amount_cache[amount_str] = parsed_amount
        return parsed_amount

    def _looks_like_date(self, date_str: str) -> bool:
        """Check if a string looks like a date."""
        if not date_str:
            return False

        normalized = date_str.strip().replace("'", '/').replace(' ', '0')
        for pattern in DATE_PATTERNS:
            if pattern.match(normalized):
                return True

        return False

    @staticmethod
    def _is_investment_account_type(account_type: Optional[str]) -> bool:
        """Return True when the QIF section/account type is investment-like."""
        if not account_type:
            return False

        normalized = account_type.strip().lower()
        return any(token in normalized for token in ('invst', 'invest', 'portfolio', 'broker', 'security'))


def load_qif_to_duckdb(qif_path: str, db_connection) -> Dict[str, object]:
    """Load QIF file data into DuckDB tables."""
    load_start = perf_counter()

    parser = QIFParser()
    parse_start = perf_counter()
    data = parser.parse_file(qif_path)
    parse_elapsed = perf_counter() - parse_start

    create_tables_start = perf_counter()
    _create_tables(db_connection)
    create_tables_elapsed = perf_counter() - create_tables_start

    db_load_start = perf_counter()
    db_connection.execute('BEGIN TRANSACTION')
    try:
        db_connection.execute('DELETE FROM transactions_rejected')
        db_connection.execute('DELETE FROM security_prices')
        db_connection.execute('DELETE FROM tags')
        db_connection.execute('DELETE FROM classes')
        db_connection.execute('DELETE FROM category_budgets')
        db_connection.execute('DELETE FROM securities')
        db_connection.execute('DELETE FROM transaction_splits')
        db_connection.execute('DELETE FROM transactions')
        db_connection.execute('DELETE FROM categories')
        db_connection.execute('DELETE FROM accounts')

        accounts_loaded = _load_accounts(db_connection, data['accounts'])
        categories_loaded = _load_categories(db_connection, data['categories'])
        category_budgets_loaded = _load_category_budgets(db_connection, data['category_budgets'])
        transactions_loaded = _load_transactions(db_connection, data['transactions'])
        rejected_transactions_loaded = _load_rejected_transactions(db_connection, data['rejected_transactions'])
        securities_loaded = _load_securities(db_connection, data['securities'])
        tags_loaded = _load_tags(db_connection, data['tags'])
        classes_loaded = _load_classes(db_connection, data['classes'])
        prices_loaded = _load_security_prices(db_connection, data['security_prices'])
        db_connection.execute('COMMIT')
    except Exception:
        db_connection.execute('ROLLBACK')
        raise

    db_load_elapsed = perf_counter() - db_load_start
    total_elapsed = perf_counter() - load_start

    return {
        'accounts': accounts_loaded,
        'categories': categories_loaded,
        'category_budgets': category_budgets_loaded,
        'transactions': transactions_loaded,
        'rejected_transactions': rejected_transactions_loaded,
        'securities': securities_loaded,
        'tags': tags_loaded,
        'classes': classes_loaded,
        'security_prices': prices_loaded,
        'timings': {
            'total_seconds': total_elapsed,
            'parse_seconds': parse_elapsed,
            'create_tables_seconds': create_tables_elapsed,
            'db_load_seconds': db_load_elapsed,
        },
    }


def export_database_snapshot(db_connection, output_file: str) -> None:
    """Persist in-memory data and views to a DuckDB file."""
    output_path = Path(output_file)
    if output_path.exists():
        output_path.unlink()

    escaped_output_file = str(output_path).replace("'", "''")
    db_connection.execute(f"ATTACH '{escaped_output_file}' AS quicken_export")

    try:
        tables = db_connection.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'main'
              AND table_type = 'BASE TABLE'
            ORDER BY table_name
            """
        ).fetchall()

        for (table_name,) in tables:
            quoted_name = table_name.replace('"', '""')
            db_connection.execute(
                f'CREATE TABLE quicken_export."{quoted_name}" AS '
                f'SELECT * FROM main."{quoted_name}"'
            )

        db_connection.execute('CREATE SCHEMA IF NOT EXISTS quicken_export.recent')

        views = db_connection.execute(
            """
            SELECT v.table_schema, v.table_name, v.view_definition
            FROM information_schema.views v
            JOIN duckdb_views() d
              ON d.schema_name = v.table_schema
             AND d.view_name = v.table_name
            WHERE v.table_schema IN ('main', 'recent')
              AND d.internal = FALSE
              AND d.database_name = current_database()
            ORDER BY
                CASE v.table_schema WHEN 'main' THEN 0 ELSE 1 END,
                v.table_name
            """
        ).fetchall()

        pending_views = [
            (table_schema, table_name, view_definition)
            for table_schema, table_name, view_definition in views
            if view_definition
        ]

        while pending_views:
            created_any = False
            next_pending = []

            for table_schema, table_name, view_definition in pending_views:
                quoted_view_name = table_name.replace('"', '""')
                target = (
                    f'quicken_export."{quoted_view_name}"'
                    if table_schema == 'main'
                    else f'quicken_export.recent."{quoted_view_name}"'
                )
                select_sql = re.sub(
                    r'^CREATE\s+VIEW\s+[^\s]+\s+AS\s+',
                    '',
                    view_definition.strip(),
                    flags=re.IGNORECASE,
                ).rstrip().rstrip(';')

                try:
                    db_connection.execute(f'CREATE VIEW {target} AS {select_sql}')
                    created_any = True
                except Exception:
                    next_pending.append((table_schema, table_name, view_definition))

            if not created_any:
                unresolved = ', '.join(
                    f'{schema}.{name}' for schema, name, _ in next_pending
                )
                raise RuntimeError(f'Unable to export dependent views: {unresolved}')

            pending_views = next_pending
    finally:
        db_connection.execute('DETACH quicken_export')


def _create_tables(db_connection):
    """Create the necessary tables and views from schema.sql."""
    for statement in _iter_schema_statements():
        db_connection.execute(statement)


def _security_price_count(security_prices) -> int:
    """Return row count for column-oriented or legacy row-oriented security prices."""
    if isinstance(security_prices, dict):
        return len(security_prices.get('price_id', []))
    return len(security_prices)


def _iter_schema_statements() -> List[str]:
    """Load executable statements from the schema file."""
    schema_path = Path(__file__).with_name('schema.sql')
    schema_sql = schema_path.read_text(encoding='utf-8')

    statements = []
    for statement in schema_sql.split(';'):
        cleaned_statement = '\n'.join(
            line for line in statement.splitlines()
            if not line.strip().startswith('--')
        ).strip()
        if cleaned_statement:
            statements.append(cleaned_statement)

    return statements


def _load_accounts(db_connection, accounts: List[Dict]) -> int:
    """Load accounts into the database via Arrow."""
    if not accounts:
        return 0

    table = pa.table({
        'account_id': pa.array([a.get('account_id') for a in accounts], type=pa.int32()),
        'name': pa.array([a.get('name') for a in accounts], type=pa.string()),
        'type': pa.array([a.get('type') for a in accounts], type=pa.string()),
        'description': pa.array([a.get('description') for a in accounts], type=pa.string()),
        'balance': pa.array([a.get('balance') for a in accounts], type=pa.float64()),
        'credit_limit': pa.array([a.get('credit_limit') for a in accounts], type=pa.float64()),
        'note': pa.array([a.get('note') for a in accounts], type=pa.string()),
    })

    db_connection.register('_arrow_accounts', table)
    try:
        db_connection.execute('INSERT INTO accounts SELECT * FROM _arrow_accounts')
    finally:
        db_connection.unregister('_arrow_accounts')

    return len(accounts)


def _load_categories(db_connection, categories: List[Dict]) -> int:
    """Load categories into the database via Arrow."""
    if not categories:
        return 0

    table = pa.table({
        'category_id': pa.array([c.get('category_id') for c in categories], type=pa.int32()),
        'name': pa.array([c.get('name') for c in categories], type=pa.string()),
        'description': pa.array([c.get('description') for c in categories], type=pa.string()),
        'expense_category': pa.array([bool(c.get('expense_category', False)) for c in categories], type=pa.bool_()),
        'income_category': pa.array([bool(c.get('income_category', False)) for c in categories], type=pa.bool_()),
        'tax_related': pa.array([bool(c.get('tax_related', False)) for c in categories], type=pa.bool_()),
        'tax_schedule': pa.array([c.get('tax_schedule') for c in categories], type=pa.string()),
        'parent_category': pa.array([c.get('parent_category') for c in categories], type=pa.string()),
    })

    db_connection.register('_arrow_categories', table)
    try:
        db_connection.execute('INSERT INTO categories SELECT * FROM _arrow_categories')
    finally:
        db_connection.unregister('_arrow_categories')

    return len(categories)


def _load_category_budgets(db_connection, category_budgets: List[Dict]) -> int:
    """Load category budgets into the database via Arrow."""
    if not category_budgets:
        return 0

    table = pa.table({
        'category_budget_id': pa.array([b.get('category_budget_id') for b in category_budgets], type=pa.int32()),
        'category_id': pa.array([b.get('category_id') for b in category_budgets], type=pa.int32()),
        'amount': pa.array([b.get('amount') for b in category_budgets], type=pa.float64()),
    })

    db_connection.register('_arrow_category_budgets', table)
    try:
        db_connection.execute('INSERT INTO category_budgets SELECT * FROM _arrow_category_budgets')
    finally:
        db_connection.unregister('_arrow_category_budgets')

    return len(category_budgets)


def _load_securities(db_connection, securities: List[Dict]) -> int:
    """Load securities into the database via Arrow."""
    if not securities:
        return 0

    table = pa.table({
        'security_id': pa.array([s.get('security_id') for s in securities], type=pa.int32()),
        'name': pa.array([s.get('name') for s in securities], type=pa.string()),
        'symbol': pa.array([s.get('symbol') for s in securities], type=pa.string()),
        'security_type': pa.array([s.get('security_type') for s in securities], type=pa.string()),
        'raw_fields': pa.array([s.get('raw_fields') for s in securities], type=pa.string()),
    })

    db_connection.register('_arrow_securities', table)
    try:
        db_connection.execute('INSERT INTO securities SELECT * FROM _arrow_securities')
    finally:
        db_connection.unregister('_arrow_securities')

    return len(securities)


def _load_tags(db_connection, tags: List[Dict]) -> int:
    """Load tags into the database via Arrow."""
    if not tags:
        return 0

    table = pa.table({
        'tag_id': pa.array([t.get('tag_id') for t in tags], type=pa.int32()),
        'name': pa.array([t.get('name') for t in tags], type=pa.string()),
        'description': pa.array([t.get('description') for t in tags], type=pa.string()),
        'raw_fields': pa.array([t.get('raw_fields') for t in tags], type=pa.string()),
    })

    db_connection.register('_arrow_tags', table)
    try:
        db_connection.execute('INSERT INTO tags SELECT * FROM _arrow_tags')
    finally:
        db_connection.unregister('_arrow_tags')

    return len(tags)


def _load_classes(db_connection, classes: List[Dict]) -> int:
    """Load classes into the database via Arrow."""
    if not classes:
        return 0

    table = pa.table({
        'class_id': pa.array([c.get('class_id') for c in classes], type=pa.int32()),
        'name': pa.array([c.get('name') for c in classes], type=pa.string()),
        'description': pa.array([c.get('description') for c in classes], type=pa.string()),
        'raw_fields': pa.array([c.get('raw_fields') for c in classes], type=pa.string()),
    })

    db_connection.register('_arrow_classes', table)
    try:
        db_connection.execute('INSERT INTO classes SELECT * FROM _arrow_classes')
    finally:
        db_connection.unregister('_arrow_classes')

    return len(classes)


def _load_security_prices(db_connection, security_prices: List[Dict]) -> int:
    """Load security prices into the database via Arrow."""
    if not security_prices:
        return 0

    if isinstance(security_prices, dict):
        price_count = len(security_prices.get('price_id', []))
        table = pa.table({
            'price_id': pa.array(security_prices.get('price_id', []), type=pa.int32()),
            'security_symbol': pa.array(security_prices.get('security_symbol', []), type=pa.string()),
            'date': pa.array(security_prices.get('date', []), type=pa.string()),
            'price': pa.array(security_prices.get('price', []), type=pa.float64()),
            'raw_fields': pa.array(security_prices.get('raw_fields', []), type=pa.string()),
        })
    else:
        price_count = len(security_prices)
        table = pa.table({
            'price_id': pa.array([p.get('price_id') for p in security_prices], type=pa.int32()),
            'security_symbol': pa.array([p.get('security_symbol') for p in security_prices], type=pa.string()),
            'date': pa.array([p.get('date') for p in security_prices], type=pa.string()),
            'price': pa.array([p.get('price') for p in security_prices], type=pa.float64()),
            'raw_fields': pa.array([p.get('raw_fields') for p in security_prices], type=pa.string()),
        })

    db_connection.register('_arrow_security_prices', table)
    try:
        db_connection.execute(
            'INSERT INTO security_prices (price_id, security_symbol, date, price, raw_fields) '
            'SELECT price_id, security_symbol, CAST(date AS DATE), price, raw_fields '
            'FROM _arrow_security_prices'
        )
    finally:
        db_connection.unregister('_arrow_security_prices')

    return price_count


def _load_transactions(db_connection, transactions: List[Dict]) -> int:
    """Load transactions into the database via Arrow."""
    if not transactions:
        return 0

    split_rows: List[Dict] = []
    split_id = 1
    for transaction in transactions:
        for split in transaction.get('splits', []):
            split_rows.append({
                'split_id': split_id,
                'tx_id': transaction.get('tx_id'),
                'category': split.get('category'),
                'amount': split.get('amount'),
                'memo': split.get('memo'),
            })
            split_id += 1

    tx_table = pa.table({
        'tx_id': pa.array([t.get('tx_id') for t in transactions], type=pa.int32()),
        'account_id': pa.array([t.get('account_id') for t in transactions], type=pa.int32()),
        'account_type': pa.array([t.get('account_type') for t in transactions], type=pa.string()),
        'date': pa.array([t.get('date') for t in transactions], type=pa.string()),
        'payee': pa.array([t.get('payee') for t in transactions], type=pa.string()),
        'memo': pa.array([t.get('memo') for t in transactions], type=pa.string()),
        'amount': pa.array([t.get('amount') for t in transactions], type=pa.float64()),
        'cleared': pa.array([t.get('cleared') for t in transactions], type=pa.string()),
        'number': pa.array([t.get('number') for t in transactions], type=pa.string()),
        'category': pa.array([t.get('category') for t in transactions], type=pa.string()),
        'security': pa.array([t.get('security') for t in transactions], type=pa.string()),
        'price': pa.array([t.get('price') for t in transactions], type=pa.float64()),
        'quantity': pa.array([t.get('quantity') for t in transactions], type=pa.float64()),
        'commission': pa.array([t.get('commission') for t in transactions], type=pa.float64()),
        'percent': pa.array([t.get('percent') for t in transactions], type=pa.float64()),
        'transfer_account': pa.array([t.get('transfer_account') for t in transactions], type=pa.string()),
        'amount_u': pa.array([t.get('amount_u') for t in transactions], type=pa.float64()),
        'action': pa.array([t.get('action') for t in transactions], type=pa.string()),
        'address': pa.array([t.get('address') for t in transactions], type=pa.string()),
        'raw_fields': pa.array([t.get('raw_fields') for t in transactions], type=pa.string()),
    })

    db_connection.register('_arrow_transactions', tx_table)
    try:
        db_connection.execute(
            "INSERT INTO transactions SELECT tx_id, account_id, account_type, "
            "CAST(date AS DATE), payee, memo, amount, cleared, number, category, "
            "security, price, quantity, commission, percent, transfer_account, amount_u, action, address, raw_fields "
            "FROM _arrow_transactions"
        )
    finally:
        db_connection.unregister('_arrow_transactions')

    if split_rows:
        split_table = pa.table({
            'split_id': pa.array([s['split_id'] for s in split_rows], type=pa.int32()),
            'tx_id': pa.array([s['tx_id'] for s in split_rows], type=pa.int32()),
            'category': pa.array([s.get('category') for s in split_rows], type=pa.string()),
            'amount': pa.array([s.get('amount') for s in split_rows], type=pa.float64()),
            'memo': pa.array([s.get('memo') for s in split_rows], type=pa.string()),
        })

        db_connection.register('_arrow_splits', split_table)
        try:
            db_connection.execute('INSERT INTO transaction_splits SELECT * FROM _arrow_splits')
        finally:
            db_connection.unregister('_arrow_splits')

    return len(transactions)


def _load_rejected_transactions(db_connection, rejected_transactions: List[Dict]) -> int:
    """Load rejected/incomplete transactions into the database via Arrow."""
    if not rejected_transactions:
        return 0

    table = pa.table({
        'rejected_tx_id': pa.array([t.get('rejected_tx_id') for t in rejected_transactions], type=pa.int32()),
        'account_id': pa.array([t.get('account_id') for t in rejected_transactions], type=pa.int32()),
        'account_type': pa.array([t.get('account_type') for t in rejected_transactions], type=pa.string()),
        'date': pa.array([t.get('date') for t in rejected_transactions], type=pa.string()),
        'payee': pa.array([t.get('payee') for t in rejected_transactions], type=pa.string()),
        'memo': pa.array([t.get('memo') for t in rejected_transactions], type=pa.string()),
        'amount': pa.array([t.get('amount') for t in rejected_transactions], type=pa.float64()),
        'cleared': pa.array([t.get('cleared') for t in rejected_transactions], type=pa.string()),
        'number': pa.array([t.get('number') for t in rejected_transactions], type=pa.string()),
        'category': pa.array([t.get('category') for t in rejected_transactions], type=pa.string()),
        'security': pa.array([t.get('security') for t in rejected_transactions], type=pa.string()),
        'price': pa.array([t.get('price') for t in rejected_transactions], type=pa.float64()),
        'quantity': pa.array([t.get('quantity') for t in rejected_transactions], type=pa.float64()),
        'commission': pa.array([t.get('commission') for t in rejected_transactions], type=pa.float64()),
        'percent': pa.array([t.get('percent') for t in rejected_transactions], type=pa.float64()),
        'transfer_account': pa.array([t.get('transfer_account') for t in rejected_transactions], type=pa.string()),
        'amount_u': pa.array([t.get('amount_u') for t in rejected_transactions], type=pa.float64()),
        'action': pa.array([t.get('action') for t in rejected_transactions], type=pa.string()),
        'address': pa.array([t.get('address') for t in rejected_transactions], type=pa.string()),
        'raw_fields': pa.array([t.get('raw_fields') for t in rejected_transactions], type=pa.string()),
        'raw_lines': pa.array([t.get('raw_lines') for t in rejected_transactions], type=pa.string()),
        'rejection_reason': pa.array([t.get('rejection_reason') for t in rejected_transactions], type=pa.string()),
    })

    db_connection.register('_arrow_rejected_transactions', table)
    try:
        db_connection.execute(
            'INSERT INTO transactions_rejected SELECT rejected_tx_id, account_id, account_type, CAST(date AS DATE), '
            'payee, memo, amount, cleared, number, category, security, price, quantity, commission, percent, '
            'transfer_account, amount_u, action, address, raw_fields, raw_lines, rejection_reason '
            'FROM _arrow_rejected_transactions'
        )
    finally:
        db_connection.unregister('_arrow_rejected_transactions')

    return len(rejected_transactions)
