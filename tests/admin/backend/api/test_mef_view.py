"""Tests for the mef project management blueprint.

Covers gating (admin.allow_mef_management), project listing, the self-delete
guard, and the subprocess job lifecycle with a mocked Popen.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from unittest.mock import patch

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

    # The bench's parent is treated as the host mef project, so it needs an
    # .env for the self-delete guard to recognise its host_project_name and a
    # .miserc.toml marker for the listing scanner to surface it.
    (bench_root.parent / ".env").write_text("PROJECT_NAME=app\nDB_ENGINE=mariadb\n")
    (bench_root.parent / ".miserc.toml").write_text('env = ["v16"]\n')

    app = create_app(bench_root)
    app.config["TESTING"] = True
    client = app.test_client()
    client.set_cookie("sid", issue_token(secret))
    return app, client


def _reset_mef_module() -> None:
    """Clear the in-memory job registry between tests."""
    import admin.backend.api.v1.mef as mef_module

    mef_module._JOBS.clear()


@pytest.fixture(autouse=True)
def _isolate_jobs():
    _reset_mef_module()
    yield
    _reset_mef_module()


def test_routes_refused_when_allow_mef_management_false(tmp_path: Path) -> None:
    bench_root = tmp_path / "host" / "app"
    _, client = _client(bench_root, allow_mef=False)

    for method, path in (
        ("get", "/api/v1/mef/projects"),
        ("post", "/api/v1/mef/projects"),
        ("delete", "/api/v1/mef/projects/foo"),
        ("post", "/api/v1/mef/projects/foo/pilot-up"),
        ("post", "/api/v1/mef/projects/foo/pilot-down"),
        ("get", "/api/v1/mef/jobs/abc"),
    ):
        response = getattr(client, method)(path)
        assert response.status_code == 403, (method, path)
        assert response.get_json()["error"]["code"] == "mef_management_disabled"


def test_project_listing_scans_mef_root(tmp_path: Path) -> None:
    bench_root = tmp_path / "host" / "app"
    mef_root = bench_root.parent.parent
    (mef_root / "v16-frappe").mkdir(parents=True)
    (mef_root / "v16-frappe" / ".miserc.toml").write_text('env = ["v16"]\n')
    (mef_root / "v16-frappe" / ".env").write_text("PROJECT_NAME=app\nDB_ENGINE=mariadb\nWEB_PORT=8052\n")
    (mef_root / "develop-frappe").mkdir(parents=True)
    (mef_root / "develop-frappe" / ".miserc.toml").write_text('env = ["develop"]\n')
    # No .miserc.toml → skipped.
    (mef_root / "scratch-dir").mkdir()
    # Protected → skipped.
    (mef_root / "_demo").mkdir()
    (mef_root / "_demo" / ".miserc.toml").write_text('env = ["v16"]\n')

    _, client = _client(bench_root, allow_mef=True)

    response = client.get("/api/v1/mef/projects")

    assert response.status_code == 200
    payload = response.get_json()
    names = sorted(p["name"] for p in payload["projects"])
    assert names == ["develop-frappe", "host", "v16-frappe"]
    host = next(p for p in payload["projects"] if p["name"] == "host")
    assert host["is_self"] is True
    v16 = next(p for p in payload["projects"] if p["name"] == "v16-frappe")
    assert v16["is_self"] is False
    assert v16["env"]["DB_ENGINE"] == "mariadb"
    assert v16["env"]["WEB_PORT"] == "8052"
    assert payload["mef_root"] == str(mef_root)


def test_delete_refuses_self_project(tmp_path: Path) -> None:
    bench_root = tmp_path / "host" / "app"
    _, client = _client(bench_root, allow_mef=True)

    response = client.delete("/api/v1/mef/projects/host")

    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "mef_self_delete_forbidden"


def test_delete_404_for_missing_project(tmp_path: Path) -> None:
    bench_root = tmp_path / "host" / "app"
    _, client = _client(bench_root, allow_mef=True)

    response = client.delete("/api/v1/mef/projects/ghost")

    assert response.status_code == 404
    assert response.get_json()["error"]["code"] == "project_not_found"


def test_delete_refuses_protected_names(tmp_path: Path) -> None:
    bench_root = tmp_path / "host" / "app"
    _, client = _client(bench_root, allow_mef=True)

    response = client.delete("/api/v1/mef/projects/_demo")

    assert response.status_code == 422
    assert response.get_json()["error"]["code"] == "invalid_project"


def test_create_rejects_unknown_profile(tmp_path: Path) -> None:
    bench_root = tmp_path / "host" / "app"
    mef_root = bench_root.parent.parent
    (mef_root / ".config" / "mise").mkdir(parents=True)
    (mef_root / ".config" / "mise" / "config.v16.toml").write_text(
        'FRAPPE_VERSION = "16-hotfix"\n'
    )

    _, client = _client(bench_root, allow_mef=True)

    response = client.post("/api/v1/mef/projects", json={"profile": "bogus"})

    assert response.status_code == 422
    assert response.get_json()["error"]["code"] == "invalid_profile"


def test_create_rejects_sqlite_below_v16(tmp_path: Path) -> None:
    bench_root = tmp_path / "host" / "app"
    mef_root = bench_root.parent.parent
    config_dir = mef_root / ".config" / "mise"
    config_dir.mkdir(parents=True)
    (config_dir / "config.v15.toml").write_text('FRAPPE_VERSION = "15"\n')

    _, client = _client(bench_root, allow_mef=True)

    response = client.post(
        "/api/v1/mef/projects",
        json={"profile": "v15", "db_engine": "sqlite"},
    )

    assert response.status_code == 422
    assert response.get_json()["error"]["code"] == "invalid_db_engine"


def test_create_rejects_path_traversal_directory(tmp_path: Path) -> None:
    bench_root = tmp_path / "host" / "app"
    mef_root = bench_root.parent.parent
    config_dir = mef_root / ".config" / "mise"
    config_dir.mkdir(parents=True)
    (config_dir / "config.v16.toml").write_text('FRAPPE_VERSION = "16-hotfix"\n')

    _, client = _client(bench_root, allow_mef=True)

    response = client.post(
        "/api/v1/mef/projects",
        json={"profile": "v16", "directory": "../escape"},
    )

    assert response.status_code == 422
    assert response.get_json()["error"]["code"] == "invalid_directory"


def _stub_popen(returncode: int = 0, block: threading.Event | None = None):
    """Return a Popen factory.

    ``block`` (when provided) gates ``wait()``: if set, the reaper thread parks
    on it so the test can observe the running state. When ``None`` the stub
    resolves immediately with ``returncode``.
    """

    class StubProc:
        def __init__(self, args, cwd, env, stdout, stderr, start_new_session):
            self.args = args
            self.returncode = None
            self.pid = 4242
            try:
                stdout.write(b"mise r new: starting\n")
                stdout.flush()
            except Exception:
                pass
            self._final = returncode
            self._block = block

        def poll(self):
            if self._block is not None and not self._block.is_set():
                return None
            return self._final

        def wait(self):
            if self._block is not None:
                self._block.wait(timeout=5.0)
            self.returncode = self._final
            return self._final

    return StubProc


def test_create_spawns_headless_mise_new_and_streams_log(tmp_path: Path) -> None:
    bench_root = tmp_path / "host" / "app"
    mef_root = bench_root.parent.parent
    config_dir = mef_root / ".config" / "mise"
    config_dir.mkdir(parents=True)
    (config_dir / "config.v16.toml").write_text('FRAPPE_VERSION = "16-hotfix"\n')
    overlays_dir = mef_root / ".config" / "overlays" / "mcp"
    overlays_dir.mkdir(parents=True)

    _, client = _client(bench_root, allow_mef=True)

    captured: dict = {}
    release = threading.Event()

    def fake_popen(args, cwd, env, stdout, stderr, start_new_session):
        captured["args"] = args
        captured["cwd"] = cwd
        captured["env"] = dict(env)
        captured["stdout"] = stdout
        return _stub_popen(returncode=0, block=release)(args, cwd, env, stdout, stderr, start_new_session)

    with patch("admin.backend.api.v1.mef.subprocess.Popen", side_effect=fake_popen):
        response = client.post(
            "/api/v1/mef/projects",
            json={
                "profile": "v16",
                "directory": "_demo/x",
                "db_engine": "sqlite",
                "overlays": ["mcp"],
                "new_run_setup": 1,
            },
        )
        job_id = response.get_json()["job_id"]
        detail = client.get(f"/api/v1/mef/jobs/{job_id}").get_json()
    release.set()

    assert response.status_code == 202
    assert job_id.startswith("new-")

    assert captured["args"][0].endswith("mise") or captured["args"][0] == "mise"
    assert captured["args"][1:4] == ["r", "new"]
    assert captured["cwd"] == str(mef_root)
    assert captured["env"]["NONINTERACTIVE"] == "1"
    assert captured["env"]["NEW_PROFILE"] == "v16"
    assert captured["env"]["NEW_DIR"] == "_demo/x"
    assert captured["env"]["NEW_DB_ENGINE"] == "sqlite"
    # overlays list is joined into a comma-separated string
    assert captured["env"]["NEW_OVERLAYS"] == "mcp"
    assert captured["env"]["NEW_RUN_SETUP"] == "1"

    # No shell string anywhere.
    for value in captured["args"]:
        assert isinstance(value, str) and "&&" not in value

    assert detail["status"] == "running"
    assert detail["exit_code"] is None
    assert "mise r new: starting" in detail["log"]


def test_delete_spawns_headless_mise_delete_with_confirm(tmp_path: Path) -> None:
    bench_root = tmp_path / "host" / "app"
    mef_root = bench_root.parent.parent
    (mef_root / "victim").mkdir()
    (mef_root / "victim" / ".miserc.toml").write_text('env = ["v16"]\n')

    _, client = _client(bench_root, allow_mef=True)

    captured: dict = {}

    def fake_popen(args, cwd, env, stdout, stderr, start_new_session):
        captured["args"] = args
        captured["env"] = dict(env)
        return _stub_popen(returncode=0)(args, cwd, env, stdout, stderr, start_new_session)

    with patch("admin.backend.api.v1.mef.subprocess.Popen", side_effect=fake_popen):
        response = client.delete("/api/v1/mef/projects/victim")

    assert response.status_code == 202
    assert response.get_json()["job_id"].startswith("delete-")
    assert captured["args"][1:4] == ["r", "delete", "victim"]
    assert captured["env"]["DELETE_CONFIRM"] == "1"
    assert captured["env"]["NONINTERACTIVE"] == "1"


def test_pilot_up_runs_mise_in_project_dir(tmp_path: Path) -> None:
    bench_root = tmp_path / "host" / "app"
    mef_root = bench_root.parent.parent
    (mef_root / "v16-frappe").mkdir()
    (mef_root / "v16-frappe" / ".miserc.toml").write_text('env = ["v16"]\n')

    _, client = _client(bench_root, allow_mef=True)

    captured: dict = {}

    def fake_popen(args, cwd, env, stdout, stderr, start_new_session):
        captured["args"] = args
        captured["cwd"] = cwd
        captured["env"] = dict(env)
        return _stub_popen(returncode=0)(args, cwd, env, stdout, stderr, start_new_session)

    with patch("admin.backend.api.v1.mef.subprocess.Popen", side_effect=fake_popen):
        response = client.post("/api/v1/mef/projects/v16-frappe/pilot-up")

    assert response.status_code == 202
    assert response.get_json()["job_id"].startswith("pilot-up-")
    # pilot:up is project-scoped (#MISE dir="{{cwd}}") → cwd must be the project dir.
    assert captured["cwd"] == str(mef_root / "v16-frappe")
    assert captured["args"][1:4] == ["r", "pilot:up"]
    assert captured["env"]["NONINTERACTIVE"] == "1"


def test_pilot_down_runs_mise_in_project_dir(tmp_path: Path) -> None:
    bench_root = tmp_path / "host" / "app"
    mef_root = bench_root.parent.parent
    (mef_root / "v16-frappe").mkdir()
    (mef_root / "v16-frappe" / ".miserc.toml").write_text('env = ["v16"]\n')

    _, client = _client(bench_root, allow_mef=True)

    captured: dict = {}

    def fake_popen(args, cwd, env, stdout, stderr, start_new_session):
        captured["args"] = args
        captured["cwd"] = cwd
        return _stub_popen(returncode=0)(args, cwd, env, stdout, stderr, start_new_session)

    with patch("admin.backend.api.v1.mef.subprocess.Popen", side_effect=fake_popen):
        response = client.post("/api/v1/mef/projects/v16-frappe/pilot-down")

    assert response.status_code == 202
    assert response.get_json()["job_id"].startswith("pilot-down-")
    assert captured["cwd"] == str(mef_root / "v16-frappe")
    assert captured["args"][1:4] == ["r", "pilot:down"]


def test_pilot_control_refuses_self_project(tmp_path: Path) -> None:
    bench_root = tmp_path / "host" / "app"
    _, client = _client(bench_root, allow_mef=True)

    for path in ("/pilot-up", "/pilot-down"):
        response = client.post(f"/api/v1/mef/projects/host{path}")
        assert response.status_code == 409, path
        assert (
            response.get_json()["error"]["code"] == "mef_self_pilot_control_forbidden"
        ), path


def test_pilot_control_404_for_missing_project(tmp_path: Path) -> None:
    bench_root = tmp_path / "host" / "app"
    _, client = _client(bench_root, allow_mef=True)

    for path in ("/pilot-up", "/pilot-down"):
        response = client.post(f"/api/v1/mef/projects/ghost{path}")
        assert response.status_code == 404, path
        assert response.get_json()["error"]["code"] == "project_not_found"


def test_pilot_control_refuses_protected_names(tmp_path: Path) -> None:
    bench_root = tmp_path / "host" / "app"
    _, client = _client(bench_root, allow_mef=True)

    response = client.post("/api/v1/mef/projects/_demo/pilot-up")

    assert response.status_code == 422
    assert response.get_json()["error"]["code"] == "invalid_project"


def test_job_detail_404_for_unknown_id(tmp_path: Path) -> None:
    bench_root = tmp_path / "host" / "app"
    _, client = _client(bench_root, allow_mef=True)

    response = client.get("/api/v1/mef/jobs/nope")

    assert response.status_code == 404
    assert response.get_json()["error"]["code"] == "mef_job_not_found"


def test_job_transitions_to_success_when_process_exits_zero(tmp_path: Path) -> None:
    bench_root = tmp_path / "host" / "app"
    mef_root = bench_root.parent.parent
    config_dir = mef_root / ".config" / "mise"
    config_dir.mkdir(parents=True)
    (config_dir / "config.v16.toml").write_text('FRAPPE_VERSION = "16-hotfix"\n')

    _, client = _client(bench_root, allow_mef=True)

    with patch(
        "admin.backend.api.v1.mef.subprocess.Popen",
        side_effect=_stub_popen(returncode=0),
    ):
        response = client.post("/api/v1/mef/projects", json={"profile": "v16"})

    job_id = response.get_json()["job_id"]
    # Spin until the reaper marks the job terminal.
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        detail = client.get(f"/api/v1/mef/jobs/{job_id}").get_json()
        if detail["status"] != "running":
            break
        time.sleep(0.02)

    assert detail["status"] == "success"
    assert detail["exit_code"] == 0


def test_mef_routes_exempt_from_read_only_when_enabled(tmp_path: Path) -> None:
    bench_root = tmp_path / "host" / "app"
    # Tighten the fixture: flip read_only on top of allow_mef_management.
    bench_root.mkdir(parents=True, exist_ok=True)
    config = BenchConfig.from_flat(
        bench_root.name,
        {"admin_enabled": True, "admin_password": "secret"},
    )
    config.admin.allow_mef_management = True
    config.admin.read_only = True
    (bench_root / "bench.toml").write_text(config.dumps())
    from admin.backend.auth import ensure_jwt_secret, issue_token

    secret = ensure_jwt_secret(bench_root / "bench.toml")
    (bench_root.parent / ".env").write_text("PROJECT_NAME=app\n")
    app = create_app_under_test(bench_root)
    client = app.test_client()
    client.set_cookie("sid", issue_token(secret))

    response = client.get("/api/v1/mef/projects")

    assert response.status_code == 200


def create_app_under_test(bench_root: Path):
    from admin.backend.app import create_app

    app = create_app(bench_root)
    app.config["TESTING"] = True
    return app


def test_mef_routes_return_403_read_only_when_disabled(tmp_path: Path) -> None:
    """When read_only=True and allow_mef_management=False, mef routes return 403 read_only_mode."""
    bench_root = tmp_path / "host" / "app"
    bench_root.mkdir(parents=True, exist_ok=True)
    config = BenchConfig.from_flat(
        bench_root.name,
        {"admin_enabled": True, "admin_password": "secret"},
    )
    config.admin.allow_mef_management = False
    config.admin.read_only = True
    (bench_root / "bench.toml").write_text(config.dumps())
    from admin.backend.auth import ensure_jwt_secret, issue_token

    secret = ensure_jwt_secret(bench_root / "bench.toml")
    (bench_root.parent / ".env").write_text("PROJECT_NAME=app\n")
    app = create_app_under_test(bench_root)
    client = app.test_client()
    client.set_cookie("sid", issue_token(secret))

    # Mutation routes should return 403 read_only_mode
    for method, path in (
        ("post", "/api/v1/mef/projects"),
        ("delete", "/api/v1/mef/projects/foo"),
        ("post", "/api/v1/mef/projects/foo/pilot-up"),
        ("post", "/api/v1/mef/projects/foo/pilot-down"),
    ):
        response = getattr(client, method)(path)
        assert response.status_code == 403, (method, path)
        assert response.get_json()["error"]["code"] == "read_only_mode"
