"""Tests for /api/v1/share/ngrok and /api/v1/share/slim."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from pilot.config import BenchConfig


def _client(bench_root: Path, password: str = "secret"):
    from admin.backend.app import create_app
    from admin.backend.auth import ensure_jwt_secret, issue_token

    bench_root.mkdir(parents=True, exist_ok=True)
    (bench_root / "bench.toml").write_text(
        BenchConfig.from_flat(bench_root.name, {"admin_enabled": True, "admin_password": password}).dumps()
    )
    secret = ensure_jwt_secret(bench_root / "bench.toml")
    app = create_app(bench_root)
    app.config["TESTING"] = True
    client = app.test_client()
    client.set_cookie("sid", issue_token(secret))
    return client


def test_ngrok_status_disconnected_by_default(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    response = _client(bench_root).get("/api/v1/share/ngrok")

    assert response.status_code == 200
    assert response.get_json() == {"connected": False, "token_preview": ""}


def test_ngrok_connect_writes_project_env(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)

    response = client.put("/api/v1/share/ngrok", json={"token": "2abc_verylongngroktoken1234"})

    assert response.status_code == 200
    body = response.get_json()
    assert body["connected"] is True
    assert body["token_preview"].startswith("2abc")
    env_text = (tmp_path / "benches" / ".env").read_text()
    assert "NGROK_AUTHTOKEN = 2abc_verylongngroktoken1234" in env_text


def test_ngrok_connect_rejects_blank_token(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    response = _client(bench_root).put("/api/v1/share/ngrok", json={"token": "  "})

    assert response.status_code == 422
    assert response.get_json()["error"]["code"] == "token_required"


def test_ngrok_disconnect_removes_token(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    client.put("/api/v1/share/ngrok", json={"token": "sometoken"})

    response = client.delete("/api/v1/share/ngrok")

    assert response.status_code == 204
    status = client.get("/api/v1/share/ngrok").get_json()
    assert status == {"connected": False, "token_preview": ""}


def test_slim_status_reflects_connected_marker(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)

    with patch("admin.backend.api.v1.share.is_connected", return_value=True):
        response = client.get("/api/v1/share/slim")

    assert response.status_code == 200
    assert response.get_json() == {"connected": True}


def test_slim_login_returns_session_snapshot(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)

    fake_snapshot = {"status": "waiting", "url": "https://app.slim.sh/cli-auth?code=xyz", "message": "..."}
    with patch("admin.backend.api.v1.share.SlimLoginSession.start") as start:
        start.return_value.snapshot.return_value = fake_snapshot
        response = client.post("/api/v1/share/slim/login")

    assert response.status_code == 200
    assert response.get_json() == fake_snapshot


def test_slim_login_status_404_when_no_session(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)

    with patch("admin.backend.api.v1.share.SlimLoginSession.current", return_value=None):
        response = client.get("/api/v1/share/slim/login")

    assert response.status_code == 404


def test_slim_disconnect_calls_logout(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)

    with patch("admin.backend.api.v1.share.logout") as logout:
        response = client.delete("/api/v1/share/slim")

    logout.assert_called_once()
    assert response.status_code == 204
