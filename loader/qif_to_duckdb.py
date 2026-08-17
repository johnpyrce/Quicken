#!/usr/bin/env python3
"""Create a persistent DuckDB database from a Quicken QIF export."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from time import perf_counter

import duckdb
from qif_loader import export_database_snapshot, load_qif_to_duckdb


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read a Quicken QIF file and create a DuckDB database.",
    )
    parser.add_argument("qif_file", type=Path, help="QIF file to import")
    parser.add_argument(
        "output_file",
        nargs="?",
        type=Path,
        help="Output database path (default: <QIF filename>.duckdb)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace the output database if it already exists",
    )
    return parser.parse_args()


def create_database(qif_file: Path, output_file: Path, *, force: bool = False) -> dict[str, object]:
    """Load *qif_file* and persist its tables and views in *output_file*."""
    qif_path = qif_file.expanduser().resolve()
    output_path = output_file.expanduser().resolve()

    if not qif_path.is_file():
        raise FileNotFoundError(f"QIF file does not exist or is not a file: {qif_path}")
    if qif_path == output_path:
        raise ValueError("The output database path must differ from the QIF input path")
    if output_path.exists() and not force:
        raise FileExistsError(
            f"Output database already exists: {output_path} (use --force to replace it)"
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)

    connection = duckdb.connect(database=":memory:")
    try:
        stats = load_qif_to_duckdb(str(qif_path), connection)
        export_database_snapshot(connection, str(output_path))
    finally:
        connection.close()

    # Opening the finished file catches persistence problems before reporting success.
    verification_connection = duckdb.connect(str(output_path), read_only=True)
    try:
        persisted_transactions = verification_connection.execute(
            "SELECT COUNT(*) FROM transactions"
        ).fetchone()[0]
    finally:
        verification_connection.close()

    if persisted_transactions != stats["transactions"]:
        raise RuntimeError(
            "Database verification failed: "
            f"loaded {stats['transactions']} transactions but persisted {persisted_transactions}"
        )

    return stats


def main() -> int:
    args = parse_args()
    qif_path = args.qif_file.expanduser()
    output_path = (
        args.output_file.expanduser()
        if args.output_file is not None
        else Path.cwd() / f"{qif_path.stem}.duckdb"
    )

    started = perf_counter()
    try:
        stats = create_database(qif_path, output_path, force=args.force)
    except (FileNotFoundError, FileExistsError, ValueError, RuntimeError, duckdb.Error) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    elapsed = perf_counter() - started
    timings = stats.get("timings", {})
    print(f"Created {output_path.resolve()}")
    print(
        "Loaded "
        f"{stats['accounts']} accounts, "
        f"{stats['categories']} categories, "
        f"{stats['transactions']} transactions, and "
        f"{stats['rejected_transactions']} rejected transactions"
    )
    if isinstance(timings, dict):
        print(
            "Timing: "
            f"parse={timings.get('parse_seconds', 0.0):.2f}s, "
            f"database load={timings.get('db_load_seconds', 0.0):.2f}s, "
            f"total={elapsed:.2f}s"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
