"""Tests for reading installed apps via the dialect-aware database engines.

Covers the engine-level `get_installed_apps()` (quoting per dialect plus the
Frappe v12 fallback) and the site-level `query_installed_apps_via_db` /
`list_installed_apps` helpers that sit on top of it.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pilot.core.database.base import QueryResult
from pilot.core.database.engines import MariaDB, PostgreSQL, SQLite
from pilot.core.site.config import list_installed_apps, query_installed_apps_via_db


def _apps_result(*names: str) -> QueryResult:
    return QueryResult(columns=["app_name"], rows=[[name] for name in names], duration_ms=0.0)


def _default_result(value: str | None) -> QueryResult:
    rows = [[value]] if value is not None else []
    return QueryResult(columns=["defvalue"], rows=rows, duration_ms=0.0)


def _mariadb() -> MariaDB:
    return MariaDB(host="localhost", port=3306, user="u", password="p", database="d")


def _postgres() -> PostgreSQL:
    return PostgreSQL(host="localhost", port=5432, user="u", password="p", database="d")


def _sqlite() -> SQLite:
    return SQLite(db_path="/tmp/site.db")


@pytest.mark.parametrize(
    "factory, quote",
    [(_mariadb, "`"), (_postgres, '"'), (_sqlite, '"')],
    ids=["mariadb", "postgres", "sqlite"],
)
def test_get_installed_apps_quotes_identifiers_per_dialect(factory, quote: str) -> None:
    db = factory()
    db.execute = MagicMock(return_value=_apps_result("frappe", "erpnext"))

    assert db.get_installed_apps() == ["frappe", "erpnext"]

    sql = db.execute.call_args_list[0].args[0]
    assert f"{quote}tabInstalled Application{quote}" in sql
    assert "ORDER BY idx" in sql


@pytest.mark.parametrize("factory", [_mariadb, _postgres, _sqlite], ids=["mariadb", "postgres", "sqlite"])
def test_get_installed_apps_falls_back_to_default_on_frappe_v12(factory) -> None:
    """Frappe < v13 never populates `tabInstalled Application`; apps live as a
    JSON list in the `installed_apps` global default instead."""
    db = factory()
    db.execute = MagicMock(side_effect=[_apps_result(), _default_result('["frappe", "wsejapp"]')])

    assert db.get_installed_apps() == ["frappe", "wsejapp"]
    assert db.execute.call_count == 2


@pytest.mark.parametrize("factory", [_mariadb, _postgres, _sqlite], ids=["mariadb", "postgres", "sqlite"])
def test_get_installed_apps_returns_empty_when_neither_source_has_apps(factory) -> None:
    db = factory()
    db.execute = MagicMock(side_effect=[_apps_result(), _default_result("")])

    assert db.get_installed_apps() == []


def test_get_installed_apps_ignores_non_string_default_entries() -> None:
    db = _mariadb()
    db.execute = MagicMock(side_effect=[_apps_result(), _default_result('["frappe", 42, null]')])

    assert db.get_installed_apps() == ["frappe"]


def test_get_installed_apps_returns_empty_on_malformed_default_json() -> None:
    db = _mariadb()
    db.execute = MagicMock(side_effect=[_apps_result(), _default_result("not-json")])

    assert db.get_installed_apps() == []


def test_query_installed_apps_via_db_returns_engine_apps() -> None:
    database = MagicMock()
    database.get_installed_apps.return_value = ["frappe", "erpnext"]
    with patch("pilot.core.site.config.make_site_database", return_value=database):
        assert query_installed_apps_via_db(Path("/bench"), "site.local") == ["frappe", "erpnext"]


def test_query_installed_apps_via_db_returns_none_on_database_error() -> None:
    from pilot.exceptions import DatabaseError

    with patch("pilot.core.site.config.make_site_database", side_effect=DatabaseError("no connection")):
        assert query_installed_apps_via_db(Path("/bench"), "site.local") is None


def test_query_installed_apps_via_db_returns_none_when_credentials_missing() -> None:
    """make_site_database raises KeyError when db_user/db_password are absent."""
    with patch("pilot.core.site.config.make_site_database", side_effect=KeyError("db_password")):
        assert query_installed_apps_via_db(Path("/bench"), "site.local") is None


def test_query_installed_apps_via_db_returns_none_when_site_config_missing() -> None:
    with patch("pilot.core.site.config.make_site_database", side_effect=FileNotFoundError("no site")):
        assert query_installed_apps_via_db(Path("/bench"), "site.local") is None


def test_list_installed_apps_returns_cached_config_when_present() -> None:
    site_config = {"installed_apps": ["frappe"]}
    assert list_installed_apps(site_config, Path("/bench"), "site.local") == ["frappe"]


def test_list_installed_apps_falls_back_to_frappe_when_db_probe_fails() -> None:
    with (
        patch("pilot.core.site.config.query_installed_apps_via_db", return_value=None),
        patch("pilot.core.site.config.query_installed_apps_via_frappe", return_value=["frappe"]) as frappe,
    ):
        assert list_installed_apps({}, Path("/bench"), "site.local") == ["frappe"]
    frappe.assert_called_once_with(Path("/bench"), "site.local")
