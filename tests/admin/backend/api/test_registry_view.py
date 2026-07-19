"""Tests for the multi-bench registry blueprint.

Covers gating (``admin.allow_mef_management``), the project scan with
metadata (profile, frappe version, db engine, ports, counts), the pilot
port derivation (mirrors the bash ``cksum`` formula), pilot liveness probe
behavior under success/failure/timeout, and graceful degradation when
projects lack ``.env``, ``.miserc.toml``, or a bench dir.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pilot.config import BenchConfig


def _client(
    bench_root: Path,
    *,
    allow_mef: bool,
    password: str = "secret",
):
    from admin.backend.app import create_app
    from admin.backend.auth import ensure_jwt_secret, issue_token

    bench_root.mkdir(parents=True, exist_ok=True)
    config = BenchConfig.from_flat(
        bench_root.name,
        {"admin_enabled": True, "admin_password": password},
    )
    config.admin.allow_mef_management = allow_mef
    (bench_root / "bench.toml").write_text(config.dumps())
    secret = ensure_jwt_secret(bench_root / "bench.toml")

    # bench parent = host mef project; needs .env + .miserc.toml markers so
    # the scanner surfaces it (and is_self detection works).
    (bench_root.parent / ".env").write_text("PROJECT_NAME=app\nDB_ENGINE=mariadb\n")
    (bench_root.parent / ".miserc.toml").write_text('env = ["v16"]\n')

    app = create_app(bench_root)
    app.config["TESTING"] = True
    client = app.test_client()
    client.set_cookie("sid", issue_token(secret))
    return app, client


def _seed_project(project_dir: Path, *, profile: str, env: str = "") -> None:
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / ".miserc.toml").write_text(f'env = ["{profile}"]\n')
    if env:
        (project_dir / ".env").write_text(env)


def _seed_profile(mef_root: Path, profile: str, frappe_version: str) -> None:
    config_dir = mef_root / ".config" / "mise"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / f"config.{profile}.toml").write_text(
        f'FRAPPE_VERSION = "{frappe_version}"\n'
    )


def test_registry_refused_when_allow_mef_management_false(tmp_path: Path) -> None:
    bench_root = tmp_path / "host" / "app"
    _, client = _client(bench_root, allow_mef=False)

    response = client.get("/api/v1/mef/registry")

    assert response.status_code == 403
    assert response.get_json()["error"]["code"] == "mef_management_disabled"


def test_registry_scans_all_projects_with_metadata(tmp_path: Path) -> None:
    bench_root = tmp_path / "host" / "app"
    mef_root = bench_root.parent.parent
    _seed_project(
        mef_root / "v16-frappe",
        profile="v16",
        env="PROJECT_NAME=app\nDB_ENGINE=sqlite\nWEB_PORT=8052\n",
    )
    _seed_project(
        mef_root / "develop-frappe",
        profile="develop",
        env="PROJECT_NAME=app\nDB_ENGINE=mariadb\n",
    )
    # Protected → skipped.
    _seed_project(mef_root / "_demo", profile="v16")
    # No .miserc.toml → skipped.
    (mef_root / "scratch").mkdir()
    _seed_profile(mef_root, "v16", "16-hotfix")
    _seed_profile(mef_root, "develop", "develop")

    _, client = _client(bench_root, allow_mef=True)

    with patch("admin.backend.api.v1.registry._ping_pilot_health", return_value=False):
        response = client.get("/api/v1/mef/registry")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["mef_root"] == str(mef_root)
    names = sorted(p["name"] for p in payload["projects"])
    assert names == ["develop-frappe", "host", "v16-frappe"]

    by_name = {p["name"]: p for p in payload["projects"]}
    assert by_name["host"]["is_self"] is True
    assert by_name["v16-frappe"]["is_self"] is False

    v16 = by_name["v16-frappe"]
    assert v16["profile"] == "v16"
    assert v16["frappe_version"] == "16-hotfix"
    assert v16["db_engine"] == "sqlite"
    assert v16["ports"] == {"web": 8052, "db": None, "redis": None}
    assert v16["pilot_port"] == 7152
    assert v16["pilot_running"] is False
    assert v16["sites_count"] == 0
    assert v16["apps_count"] == 0

    develop = by_name["develop-frappe"]
    assert develop["profile"] == "develop"
    assert develop["frappe_version"] == "develop"
    assert develop["pilot_port"] == 7157


def test_registry_pilot_running_when_health_reports_200(tmp_path: Path) -> None:
    bench_root = tmp_path / "host" / "app"
    mef_root = bench_root.parent.parent
    _seed_project(mef_root / "v16-frappe", profile="v16")

    _, client = _client(bench_root, allow_mef=True)

    with patch("admin.backend.api.v1.registry._ping_pilot_health", return_value=True):
        response = client.get("/api/v1/mef/registry")

    payload = response.get_json()
    v16 = next(p for p in payload["projects"] if p["name"] == "v16-frappe")
    assert v16["pilot_running"] is True


def test_ping_pilot_health_true_when_status_200() -> None:
    from admin.backend.api.v1.registry import _ping_pilot_health

    fake_conn = MagicMock()
    fake_conn.getresponse.return_value.status = 200
    with patch(
        "admin.backend.api.v1.registry.http.client.HTTPConnection",
        return_value=fake_conn,
    ):
        assert _ping_pilot_health(7152) is True
    fake_conn.close.assert_called_once()


def test_ping_pilot_health_false_on_connection_refused() -> None:
    from admin.backend.api.v1.registry import _ping_pilot_health

    fake_conn = MagicMock()
    fake_conn.request.side_effect = ConnectionRefusedError("no server")
    with patch(
        "admin.backend.api.v1.registry.http.client.HTTPConnection",
        return_value=fake_conn,
    ):
        assert _ping_pilot_health(7152) is False


def test_ping_pilot_health_false_on_timeout() -> None:
    from admin.backend.api.v1.registry import _ping_pilot_health

    fake_conn = MagicMock()
    fake_conn.request.side_effect = TimeoutError()
    with patch(
        "admin.backend.api.v1.registry.http.client.HTTPConnection",
        return_value=fake_conn,
    ):
        assert _ping_pilot_health(7152) is False


def test_ping_pilot_health_false_on_constructor_failure() -> None:
    """Defensive: HTTPConnection itself raising must not propagate."""
    from admin.backend.api.v1.registry import _ping_pilot_health

    with patch(
        "admin.backend.api.v1.registry.http.client.HTTPConnection",
        side_effect=OSError("bootstrap failure"),
    ):
        assert _ping_pilot_health(7152) is False


def test_ping_pilot_health_false_on_non_200_status() -> None:
    from admin.backend.api.v1.registry import _ping_pilot_health

    fake_conn = MagicMock()
    fake_conn.getresponse.return_value.status = 503
    with patch(
        "admin.backend.api.v1.registry.http.client.HTTPConnection",
        return_value=fake_conn,
    ):
        assert _ping_pilot_health(7152) is False


def test_ping_pilot_health_passes_short_timeout() -> None:
    from admin.backend.api.v1.registry import _PING_TIMEOUT_SECONDS, _ping_pilot_health

    captured: dict = {}
    fake_conn = MagicMock()
    fake_conn.getresponse.return_value.status = 200

    def capture(host, port, timeout=None):
        captured["timeout"] = timeout
        return fake_conn

    with patch(
        "admin.backend.api.v1.registry.http.client.HTTPConnection",
        side_effect=capture,
    ):
        _ping_pilot_health(7152)

    assert captured["timeout"] == _PING_TIMEOUT_SECONDS
    assert _PING_TIMEOUT_SECONDS == 0.5


def test_registry_handles_missing_env_and_missing_bench_dir(tmp_path: Path) -> None:
    bench_root = tmp_path / "host" / "app"
    mef_root = bench_root.parent.parent
    # .miserc.toml present, but no .env and no app/ bench dir.
    (mef_root / "bare").mkdir()
    (mef_root / "bare" / ".miserc.toml").write_text('env = ["v16"]\n')

    _, client = _client(bench_root, allow_mef=True)

    with patch("admin.backend.api.v1.registry._ping_pilot_health", return_value=False):
        response = client.get("/api/v1/mef/registry")

    payload = response.get_json()
    bare = next(p for p in payload["projects"] if p["name"] == "bare")
    assert bare["db_engine"] == ""
    assert bare["ports"] == {"web": None, "db": None, "redis": None}
    assert bare["sites_count"] == 0
    assert bare["apps_count"] == 0


def test_registry_handles_corrupted_miserc_profile(tmp_path: Path) -> None:
    bench_root = tmp_path / "host" / "app"
    mef_root = bench_root.parent.parent
    (mef_root / "broken").mkdir()
    # Garbage that matches the .miserc.toml file requirement but yields no env.
    (mef_root / "broken" / ".miserc.toml").write_text("this is not valid toml\n")
    (mef_root / "broken" / ".env").write_text("PROJECT_NAME=app\nDB_ENGINE=mariadb\n")

    _, client = _client(bench_root, allow_mef=True)

    with patch("admin.backend.api.v1.registry._ping_pilot_health", return_value=False):
        response = client.get("/api/v1/mef/registry")

    payload = response.get_json()
    broken = next(p for p in payload["projects"] if p["name"] == "broken")
    assert broken["profile"] == ""
    # No profile → no config.<profile>.toml lookup → empty frappe version.
    assert broken["frappe_version"] == ""
    # .env values still surface.
    assert broken["db_engine"] == "mariadb"


def test_registry_counts_sites_and_apps_in_bench(tmp_path: Path) -> None:
    bench_root = tmp_path / "host" / "app"
    mef_root = bench_root.parent.parent
    _seed_project(mef_root / "v16-frappe", profile="v16")
    bench = mef_root / "v16-frappe" / "app"
    # Two sites with site_config.json, one without (not counted).
    (bench / "sites" / "site1").mkdir(parents=True)
    (bench / "sites" / "site1" / "site_config.json").write_text("{}")
    (bench / "sites" / "site2").mkdir(parents=True)
    (bench / "sites" / "site2" / "site_config.json").write_text("{}")
    (bench / "sites" / "incomplete").mkdir(parents=True)
    # Two apps with .git, one without (not counted).
    (bench / "apps" / "frappe").mkdir(parents=True)
    (bench / "apps" / "frappe" / ".git").mkdir()
    (bench / "apps" / "erpnext").mkdir(parents=True)
    (bench / "apps" / "erpnext" / ".git").mkdir()
    (bench / "apps" / "scratch-app").mkdir(parents=True)

    _, client = _client(bench_root, allow_mef=True)

    with patch("admin.backend.api.v1.registry._ping_pilot_health", return_value=False):
        response = client.get("/api/v1/mef/registry")

    payload = response.get_json()
    v16 = next(p for p in payload["projects"] if p["name"] == "v16-frappe")
    assert v16["sites_count"] == 2
    assert v16["apps_count"] == 2


def test_registry_skips_protected_and_unmarked_dirs(tmp_path: Path) -> None:
    bench_root = tmp_path / "host" / "app"
    mef_root = bench_root.parent.parent
    _seed_project(mef_root / "real", profile="v16")
    # Protected names.
    _seed_project(mef_root / "_demo", profile="v16")
    _seed_project(mef_root / "_o", profile="v16")
    # Hidden dir.
    (mef_root / ".cache").mkdir()
    (mef_root / ".cache" / ".miserc.toml").write_text('env = ["v16"]\n')
    # No .miserc.toml.
    (mef_root / "scratch").mkdir()

    _, client = _client(bench_root, allow_mef=True)

    with patch("admin.backend.api.v1.registry._ping_pilot_health", return_value=False):
        response = client.get("/api/v1/mef/registry")

    payload = response.get_json()
    names = sorted(p["name"] for p in payload["projects"])
    assert names == ["host", "real"]


@pytest.mark.parametrize(
    "name,expected_port",
    [
        ("v16-frappe", 7152),
        ("develop-frappe", 7157),
        ("toto", 7150),
        ("host", 7183),
    ],
)
def test_pilot_port_matches_bash_cksum_formula(name: str, expected_port: int) -> None:
    """Port derivation mirrors `cksum <<< "$name"` in pilot:admin (lines 20-24)."""
    from admin.backend.api.v1.registry import _pilot_port_from_name

    assert _pilot_port_from_name(name) == expected_port


def test_registry_returns_empty_projects_when_mef_root_has_no_siblings(
    tmp_path: Path,
) -> None:
    bench_root = tmp_path / "host" / "app"
    mef_root = bench_root.parent.parent

    _, client = _client(bench_root, allow_mef=True)

    with patch("admin.backend.api.v1.registry._ping_pilot_health", return_value=False):
        response = client.get("/api/v1/mef/registry")

    payload = response.get_json()
    assert payload["mef_root"] == str(mef_root)
    names = [p["name"] for p in payload["projects"]]
    # Only the host project (created by _client) shows up.
    assert names == ["host"]
