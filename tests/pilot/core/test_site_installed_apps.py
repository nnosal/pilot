"""Tests for pilot.core.site.config.query_installed_apps_via_db."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from pilot.core.site.config import query_installed_apps_via_db

_SITE_CONFIG = {
    "db_name": "_test_db",
    "db_password": "secret",
    "db_host": "127.0.0.1",
    "db_port": 8476,
}


def _run(returncode: int, stdout: str) -> MagicMock:
    result = MagicMock()
    result.returncode = returncode
    result.stdout = stdout
    return result


def test_reads_from_installed_application_table_when_populated() -> None:
    with (
        patch("shutil.which", return_value="/usr/bin/mariadb"),
        patch("subprocess.run", return_value=_run(0, "frappe\nerpnext\n")) as run,
    ):
        assert query_installed_apps_via_db(_SITE_CONFIG) == ["frappe", "erpnext"]
    assert run.call_count == 1


def test_falls_back_to_installed_apps_default_on_frappe_v12() -> None:
    """Frappe < v13 never populates `tabInstalled Application`; apps live as a
    JSON list in the `installed_apps` global default instead."""
    responses = [_run(0, ""), _run(0, '["frappe", "wsejapp"]\n')]
    with (
        patch("shutil.which", return_value="/usr/bin/mariadb"),
        patch("subprocess.run", side_effect=responses) as run,
    ):
        assert query_installed_apps_via_db(_SITE_CONFIG) == ["frappe", "wsejapp"]
    assert run.call_count == 2


def test_returns_empty_list_when_neither_source_has_apps() -> None:
    responses = [_run(0, ""), _run(0, "")]
    with (
        patch("shutil.which", return_value="/usr/bin/mariadb"),
        patch("subprocess.run", side_effect=responses),
    ):
        assert query_installed_apps_via_db(_SITE_CONFIG) == []


def test_returns_none_when_db_unreachable() -> None:
    with (
        patch("shutil.which", return_value="/usr/bin/mariadb"),
        patch("subprocess.run", return_value=_run(1, "")),
    ):
        assert query_installed_apps_via_db(_SITE_CONFIG) is None


def test_returns_none_without_credentials() -> None:
    assert query_installed_apps_via_db({}) is None


def test_returns_none_without_mariadb_cli() -> None:
    with patch("shutil.which", return_value=None):
        assert query_installed_apps_via_db(_SITE_CONFIG) is None
