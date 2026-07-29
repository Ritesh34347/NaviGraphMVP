"""Dialect-neutral -> Trino-qualified name rewriting.

NaviGraph's SQL Generation agent (a sibling Query-domain agent, built in
parallel with this package -- not yet wired up) produces SQL against
dialect-neutral `SCHEMA.TABLE` references, since it has no notion of which
physical engine (a tenant's own Snowflake connector, or Trino spanning
several sources) will actually execute a given `ExecutionPlan`. This module
supplies the one real translation step needed to run that same SQL through
Trino: `to_trino_qualified_name` for a single already-known `SCHEMA.TABLE`
string, and `rewrite_sql_for_trino` for finding and rewriting every such
reference inside a full generated SQL string.

Both functions are real, careful string/regex operations, NOT a SQL parser.
See `rewrite_sql_for_trino`'s docstring for the honest, specific limits of
what it can and cannot safely rewrite.
"""

from __future__ import annotations

import re

_IDENT = r"[A-Za-z_][A-Za-z0-9_]*"

# Matches a bare two-part dotted identifier (`SCHEMA.TABLE`) that is not
# itself part of a longer, already-qualified chain. `\b` alone is NOT enough
# for this (a `.` is a non-word character, so `\b` matches on both sides of
# *every* dot in a chain like `catalog.schema.table`, which would wrongly
# let `catalog.schema` match as if it were a standalone two-part name). The
# explicit `(?<![.\w])` / `(?![.\w])` lookaround instead requires that
# neither side of the two-part match touches a `.` (or a word character,
# i.e. no partial-identifier match either) -- so a three-part
# `catalog.schema.table` never matches at the `catalog.schema` position
# (rejected by the trailing `(?![.\w])`, since the next character is `.`)
# nor at the `schema.table` position (rejected by the leading
# `(?<![.\w])`, since the preceding character is `.`), leaving
# already-qualified names untouched.
_QUALIFIED_NAME_RE = re.compile(rf"(?<![.\w])({_IDENT})\.({_IDENT})(?![.\w])")

# Matches a standard SQL single-quoted string literal, including the `''`
# (doubled single quote) escape convention both Snowflake and Trino support
# for an embedded quote character. Deliberately does NOT recognize
# double-quoted identifier-quoting or Postgres-style `$$...$$` dollar
# quoting as a "literal to skip" -- see the module-level limitations note in
# `rewrite_sql_for_trino`'s docstring.
_STRING_LITERAL_RE = re.compile(r"'(?:[^']|'')*'")

# Matches a FROM/JOIN keyword followed by a comma-separated list of
# `SCHEMA.TABLE [[AS] alias]` table references. This is the ONE real,
# specific shape `rewrite_sql_for_trino` rewrites -- see its docstring for
# why deliberately scoping the rewrite to right after FROM/JOIN (rather than
# rewriting every `SCHEMA.TABLE`-shaped substring anywhere in the SQL) is a
# load-bearing design choice, not an arbitrary restriction.
_TABLE_REF = rf"(?<![.\w]){_IDENT}\.{_IDENT}(?![.\w])"
_TABLE_REF_LIST_RE = re.compile(
    rf"\b(FROM|JOIN)(\s+)("
    rf"{_TABLE_REF}(?:\s+(?:AS\s+)?{_IDENT})?"
    rf"(?:\s*,\s*{_TABLE_REF}(?:\s+(?:AS\s+)?{_IDENT})?)*"
    rf")",
    re.IGNORECASE,
)


def to_trino_qualified_name(schema_table: str, *, catalog: str) -> str:
    """Convert a dialect-neutral `"SCHEMA.TABLE"` string to Trino-qualified
    `"catalog.schema.table"` form.

    Lowercases `schema` and `table`: Trino folds unquoted identifiers to
    lowercase internally (its catalog/information_schema resolution is
    case-insensitive but normalizes to lowercase), which differs from
    Snowflake's own convention of folding unquoted identifiers to
    UPPERCASE -- so a name produced against Snowflake's native casing (e.g.
    `"ANALYTICS.CUSTOMERS"`) must be lowercased here, not passed through
    verbatim, or Trino would silently look for a differently-cased (and
    likely nonexistent, since Trino's own fold is lowercase) identifier.

    Raises:
        ValueError: if `schema_table` is not exactly two dot-separated parts.
    """

    parts = schema_table.split(".")
    if len(parts) != 2:
        raise ValueError(
            f"expected a dialect-neutral 'SCHEMA.TABLE' name, got {schema_table!r}"
        )
    schema, table = parts
    return f"{catalog}.{schema.lower()}.{table.lower()}"


def rewrite_sql_for_trino(sql: str, *, catalog: str) -> str:
    """Rewrite every `SCHEMA.TABLE` table reference in `sql` to its
    Trino-qualified `catalog.schema.table` form.

    Real limits, stated honestly (this is a regex/string operation, not a
    SQL parser):

    - Only rewrites two-part dotted identifiers that appear immediately
      after a `FROM` or `JOIN` keyword (as a single reference or a
      comma-separated list, each optionally followed by an `[AS] alias`).
      This is deliberately NARROWER than "rewrite every `SCHEMA.TABLE`-
      shaped substring in the SQL": a naive global rewrite would also match
      -- and corrupt -- ordinary `alias.column` references (e.g.
      `o.customer_id`), which share the exact same two-part dotted shape as
      a table reference and are far more common in a real query's SELECT
      list, WHERE clause, and ON clause. Restricting to right after
      FROM/JOIN is the one place a bare `SCHEMA.TABLE` reference (as opposed
      to an alias-qualified column) can actually appear. This assumes SQL
      Generation always introduces a table via an explicit `FROM`/`JOIN`
      clause -- true for ordinary `SELECT ... FROM ... JOIN ...` SQL, not
      guaranteed for every construct a full SQL grammar allows (e.g. a
      table-valued function call, or a bare CTE name reused without ever
      appearing after FROM/JOIN in this same string).
    - String literals are excluded via `_STRING_LITERAL_RE`, so a WHERE
      clause like `WHERE label = 'ANALYTICS.CUSTOMERS'` is never touched --
      confirmed by a real test in `tests/test_dialect.py`, not just assumed.
      That literal-detection regex only recognizes the standard single-quote
      (with `''`-doubling) convention; it does not recognize double-quoted
      identifier-quoting or dollar-quoted (`$$...$$`) literals as "a literal
      to skip" -- not a real gap for this project today (Snowflake and Trino
      SQL generated here both use single-quoted string literals), but worth
      naming explicitly rather than silently assuming away.
    - SQL comments (`-- ...` or `/* ... */`) are NOT recognized or excluded:
      a `SCHEMA.TABLE`-shaped reference appearing right after FROM/JOIN
      inside a comment would still be rewritten. NaviGraph's SQL Generation
      agent does not emit comments in generated SQL today, so this has not
      been a real problem in practice, but it is a genuine gap versus a real
      SQL parser.
    - Idempotent for its own output: re-running this function against
      already-rewritten (three-part, catalog-qualified) SQL is a no-op,
      since `_TABLE_REF_LIST_RE`'s inner `SCHEMA.TABLE` sub-match only
      matches an exact two-part identifier (see `_QUALIFIED_NAME_RE`'s
      comment) and a three-part `catalog.schema.table` never matches that
      shape.
    """

    def _rewrite_qualified_name(inner: re.Match[str]) -> str:
        schema, table = inner.group(1), inner.group(2)
        return to_trino_qualified_name(f"{schema}.{table}", catalog=catalog)

    def _rewrite_table_ref_list(outer: re.Match[str]) -> str:
        keyword, spacing, ref_list = outer.group(1), outer.group(2), outer.group(3)
        rewritten_list = _QUALIFIED_NAME_RE.sub(_rewrite_qualified_name, ref_list)
        return f"{keyword}{spacing}{rewritten_list}"

    # Process the SQL in literal / non-literal alternating chunks so a
    # string literal that happens to look like a FROM/JOIN table reference
    # is never touched -- only the non-literal chunks are run through
    # `_TABLE_REF_LIST_RE`.
    pieces: list[str] = []
    last_end = 0
    for literal_match in _STRING_LITERAL_RE.finditer(sql):
        chunk = sql[last_end : literal_match.start()]
        pieces.append(_TABLE_REF_LIST_RE.sub(_rewrite_table_ref_list, chunk))
        pieces.append(literal_match.group(0))
        last_end = literal_match.end()
    pieces.append(_TABLE_REF_LIST_RE.sub(_rewrite_table_ref_list, sql[last_end:]))

    return "".join(pieces)
