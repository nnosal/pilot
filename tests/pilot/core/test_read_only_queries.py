"""Tests for the read-only SQL statement guard shared by all engines."""

from __future__ import annotations

import pytest

from pilot.core.database.engines import assert_read_only_query
from pilot.exceptions import ReadOnlyQueryError


@pytest.mark.parametrize(
    "query",
    [
        "SELECT 1",
        "  select * from tabUser",
        "SHOW TABLES",
        "DESCRIBE tabUser",
        "EXPLAIN SELECT 1",
        "WITH x AS (SELECT 1) SELECT * FROM x",
        "-- comment\nSELECT 1",
        "/* leading */ SELECT 1",
    ],
)
def test_read_statements_pass(query: str) -> None:
    assert_read_only_query(query)


@pytest.mark.parametrize(
    "query",
    [
        "CREATE TABLE t (id int)",
        "DROP TABLE tabUser",
        "ALTER TABLE tabUser ADD c int",
        "INSERT INTO t VALUES (1)",
        "UPDATE tabUser SET enabled=0",
        "DELETE FROM tabUser",
        "TRUNCATE tabUser",
        "GRANT ALL ON *.* TO x",
        "/* sneaky */ DROP TABLE t",
        "",
    ],
)
def test_mutating_statements_raise(query: str) -> None:
    with pytest.raises(ReadOnlyQueryError):
        assert_read_only_query(query)
