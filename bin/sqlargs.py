#!/usr/bin/env python3
#
#  MIT Licence
#
#  Copyright (c) 2025 Brice Rosenzweig.
#
#  Permission is hereby granted, free of charge, to any person obtaining a copy
#  of this software and associated documentation files (the "Software"), to deal
#  in the Software without restriction, including without limitation the rights
#  to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
#  copies of the Software, and to permit persons to whom the Software is
#  furnished to do so, subject to the following conditions:
#  
#  The above copyright notice and this permission notice shall be included in all
#  copies or substantial portions of the Software.
#  
#  THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
#  IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
#  FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
#  AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
#  LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
#  OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
#  SOFTWARE.
#
"""
Turn the result of a SQLite query into a shell-friendly argument list.

Usage patterns:

- select a single column and join with commas:
    sqlargs airports.db "SELECT code FROM airports" --comma

- pick a specific column by name:
    sqlargs airports.db "SELECT code, name FROM airports" --column code --space

By default, the first column is used and values are joined with a comma.
"""

import argparse
import logging
import sqlite3
import sys
from typing import Iterable, List, Optional, Sequence, Tuple


LOG = logging.getLogger(__name__)


def _configure_logging(verbose: bool = False, debug: bool = False) -> None:
    """
    Configure the root logger based on verbosity flags.

    We keep logging separate from core logic so tests can configure it as needed.
    """
    if debug:
        level = logging.DEBUG
    elif verbose:
        level = logging.INFO
    else:
        level = logging.WARNING

    logging.basicConfig(
        level=level,
        format="%(levelname)s:%(name)s:%(message)s",
    )


def _resolve_column_index(column: str, description: Sequence[Tuple]) -> int:
    """
    Resolve a column specifier (name or 0-based index) into an integer index.
    """
    # Try numeric index first
    try:
        idx = int(column)
        if idx < 0 or idx >= len(description):
            raise IndexError(f"column index {idx} out of range (0-{len(description)-1})")
        LOG.debug("Resolved numeric column %s to index %d", column, idx)
        return idx
    except ValueError:
        pass

    # Otherwise treat as column name
    names = [col[0] for col in description]
    LOG.debug("Available column names: %s", names)
    if column not in names:
        raise KeyError(f"column name '{column}' not found in result set")
    idx = names.index(column)
    LOG.debug("Resolved column name %s to index %d", column, idx)
    return idx


def _quote_shell(value: str) -> str:
    """
    Very small helper to single-quote a string for POSIX shells.

    We keep this intentionally simple and predictable:
        abc   -> 'abc'
        a'b   -> 'a'\''b'
    """
    return "'" + value.replace("'", "'\"'\"'") + "'"


def _format_values(
    values: Iterable[Optional[object]],
    *,
    quote: str = "none",
    unique: bool = False,
    null_replacement: str = "",
) -> List[str]:
    """
    Convert raw SQLite values into strings according to formatting options.
    """
    seen = set()
    formatted: List[str] = []

    for val in values:
        if val is None:
            text = null_replacement
        else:
            text = str(val)

        if unique:
            if text in seen:
                continue
            seen.add(text)

        if quote == "shell":
            text = _quote_shell(text)
        elif quote == "none":
            pass
        else:
            raise ValueError(f"unsupported quote style '{quote}'")

        formatted.append(text)

    return formatted


def build_args(
    database: str,
    query: str,
    *,
    column: Optional[str] = None,
    separator: str = ",",
    quote: str = "none",
    unique: bool = False,
    null_replacement: str = "",
    limit: Optional[int] = None,
) -> str:
    """
    Core helper: execute a query and return the joined argument string.

    This function is used both by the CLI entry point and by tests.
    """
    LOG.info("Connecting to database %s", database)
    conn = sqlite3.connect(database)
    try:
        cur = conn.cursor()
        LOG.debug("Executing SQL: %s", query)
        cur.execute(query)

        description = cur.description
        if not description:
            LOG.warning("Query returned no columns; producing empty output")
            return ""

        if column is None:
            idx = 0
        else:
            idx = _resolve_column_index(column, description)

        # Fetch rows incrementally to avoid loading huge result sets at once.
        extracted: List[object] = []
        row_count = 0
        for row in cur:
            row_count += 1
            if limit is not None and row_count > limit:
                LOG.info("Row limit %d reached, stopping", limit)
                break
            extracted.append(row[idx])

        LOG.info("Fetched %d row(s)", len(extracted))
    finally:
        conn.close()

    formatted = _format_values(
        extracted,
        quote=quote,
        unique=unique,
        null_replacement=null_replacement,
    )
    joined = separator.join(formatted)
    LOG.debug("Joined result: %s", joined)
    return joined


def main(argv: Optional[Sequence[str]] = None) -> int:
    """
    CLI entry point. Parses arguments, configures logging, and prints output.
    """
    parser = argparse.ArgumentParser(
        description="Turn the result of a SQLite query into a shell argument list.",
        formatter_class=argparse.RawTextHelpFormatter,
    )

    parser.add_argument("database", help="Path to SQLite database file")
    parser.add_argument("query", help="SQL query to execute")

    parser.add_argument(
        "-c",
        "--column",
        metavar="COL",
        help="Column to extract (name or 0-based index, default: first column)",
    )

    parser.add_argument(
        "-s",
        "--separator",
        default=",",
        help="String used to join values (default: ',')",
    )

    parser.add_argument(
        "--comma",
        action="store_const",
        const=",",
        dest="separator",
        help="Join values with ','",
    )
    parser.add_argument(
        "--space",
        action="store_const",
        const=" ",
        dest="separator",
        help="Join values with ' '",
    )
    parser.add_argument(
        "--plus",
        action="store_const",
        const="+",
        dest="separator",
        help="Join values with '+'",
    )
    parser.add_argument(
        "--newline",
        action="store_const",
        const="\n",
        dest="separator",
        help="Join values with a newline character",
    )

    parser.add_argument(
        "-q",
        "--quote",
        choices=["none", "shell"],
        default="none",
        help="Quoting style for values (default: none)",
    )

    parser.add_argument(
        "--unique",
        action="store_true",
        help="De-duplicate values while preserving order",
    )

    parser.add_argument(
        "--null",
        dest="null_replacement",
        default="",
        help="Replacement string for NULL values (default: empty string)",
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional maximum number of rows to consume from the query",
    )

    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable informational logging",
    )

    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging (overrides --verbose)",
    )

    args = parser.parse_args(argv)

    _configure_logging(verbose=args.verbose, debug=args.debug)

    try:
        result = build_args(
            args.database,
            args.query,
            column=args.column,
            separator=args.separator,
            quote=args.quote,
            unique=args.unique,
            null_replacement=args.null_replacement,
            limit=args.limit,
        )
    except (sqlite3.Error, KeyError, IndexError, ValueError) as exc:
        LOG.error("%s", exc)
        return 1

    # Always print the result, even if empty, followed by a newline for shell use.
    sys.stdout.write(result + "\n")
    return 0


if __name__ == "__main__":  # pragma: no cover - simple delegation
    raise SystemExit(main())


