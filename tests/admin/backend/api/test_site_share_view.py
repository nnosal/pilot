"""Tests for /api/v1/sites/<name>/share."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from tests.admin.backend.test_admin_app import _client


def _write_site(bench_root: Path, name: str = "s.localhost", **config) -> None:
    site_path = bench_root / "sites" / name
    site_path.mkdir(parents=True)
    (site_path / "site_config.json").write_text(json.dumps(config))


def test_start_share_404_for_unknown_site(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    response = _client(bench_root).post("/api/v1/sites/missing.localhost/share")

    assert response.status_code == 404


def test_start_share_spawns_session_for_site(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    _write_site(bench_root)

    fake_snapshot = {"status": "starting", "url": None, "message": ""}
    with patch("admin.backend.api.v1.sites.share.SlimShareSession.start") as start:
        start.return_value.snapshot.return_value = fake_snapshot
        response = client.post("/api/v1/sites/s.localhost/share")

    assert response.status_code == 200
    assert response.get_json() == fake_snapshot
    args, _ = start.call_args
    assert args[1] == "s.localhost"


def test_share_status_404_when_no_session(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    _write_site(bench_root)

    with patch("admin.backend.api.v1.sites.share.SlimShareSession.current", return_value=None):
        response = client.get("/api/v1/sites/s.localhost/share")

    assert response.status_code == 404


def test_share_status_returns_session_snapshot(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    _write_site(bench_root)

    fake_snapshot = {"status": "live", "url": "https://abcd1234.slim.show", "message": "..."}
    with patch("admin.backend.api.v1.sites.share.SlimShareSession.current") as current:
        current.return_value.snapshot.return_value = fake_snapshot
        response = client.get("/api/v1/sites/s.localhost/share")

    assert response.status_code == 200
    assert response.get_json() == fake_snapshot


def test_stop_share_calls_session_stop(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    _write_site(bench_root)

    with patch("admin.backend.api.v1.sites.share.SlimShareSession.current") as current:
        response = client.delete("/api/v1/sites/s.localhost/share")

    current.return_value.stop.assert_called_once()
    assert response.status_code == 204


def test_stop_share_404_when_no_session(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    _write_site(bench_root)

    with patch("admin.backend.api.v1.sites.share.SlimShareSession.current", return_value=None):
        response = client.delete("/api/v1/sites/s.localhost/share")

    assert response.status_code == 404
