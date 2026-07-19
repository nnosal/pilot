"""Tests for admin.read_only: destructive routes refused, safe mutations allowed."""

from __future__ import annotations

from pathlib import Path

from pilot.config import BenchConfig


def _client(bench_root: Path, *, read_only: bool, password: str = "secret"):
    from admin.backend.app import create_app
    from admin.backend.auth import ensure_jwt_secret, issue_token

    bench_root.mkdir(parents=True, exist_ok=True)
    config = BenchConfig.from_flat(bench_root.name, {"admin_enabled": True, "admin_password": password})
    config.admin.read_only = read_only
    (bench_root / "bench.toml").write_text(config.dumps())
    secret = ensure_jwt_secret(bench_root / "bench.toml")
    app = create_app(bench_root)
    app.config["TESTING"] = True
    client = app.test_client()
    client.set_cookie("sid", issue_token(secret))
    return client


def test_destructive_route_refused_in_read_only(tmp_path: Path) -> None:
    client = _client(tmp_path / "benches" / "current", read_only=True)

    response = client.post("/api/v1/sites/some-site.local/actions/migrate")

    assert response.status_code == 403
    assert response.get_json()["error"]["code"] == "read_only_mode"


def test_get_routes_still_allowed_in_read_only(tmp_path: Path) -> None:
    client = _client(tmp_path / "benches" / "current", read_only=True)

    response = client.get("/api/v1/settings")

    assert response.status_code == 200


def test_settings_patch_allowed_in_read_only(tmp_path: Path) -> None:
    client = _client(tmp_path / "benches" / "current", read_only=True)

    response = client.patch("/api/v1/settings", json={})

    assert response.status_code != 403


def test_destructive_route_allowed_without_read_only(tmp_path: Path) -> None:
    client = _client(tmp_path / "benches" / "current", read_only=False)

    response = client.post("/api/v1/sites/some-site.local/actions/migrate")

    assert response.status_code != 403 or response.get_json()["error"]["code"] != "read_only_mode"


def test_settings_report_read_only(tmp_path: Path) -> None:
    client = _client(tmp_path / "benches" / "current", read_only=True)

    response = client.get("/api/v1/settings")

    assert response.get_json()["admin"]["read_only"] is True
