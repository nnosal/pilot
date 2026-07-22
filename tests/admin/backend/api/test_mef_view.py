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


def test_create_strips_inherited_mise_env_vars(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """admin runs as `mise r pilot:admin` itself — its own MISE_ENV/MISE_PROJECT_ROOT/...
    are pinned to the project hosting this admin. Left in the spawned env, a `mise r new`
    targeting a *different* project would inherit this admin's profile instead of resolving
    the target directory's own (wrong frappe version, wrong python/node tool versions)."""
    bench_root = tmp_path / "host" / "app"
    mef_root = bench_root.parent.parent
    config_dir = mef_root / ".config" / "mise"
    config_dir.mkdir(parents=True)
    (config_dir / "config.v16.toml").write_text('FRAPPE_VERSION = "16-hotfix"\n')
    overlays_dir = mef_root / ".config" / "overlays" / "mcp"
    overlays_dir.mkdir(parents=True)

    monkeypatch.setenv("MISE_ENV", "v16")
    monkeypatch.setenv("MISE_PROJECT_ROOT", str(mef_root / "some-other-project"))
    monkeypatch.setenv("MISE_SESSION", "opaque-session-blob")
    monkeypatch.setenv("SOME_UNRELATED_VAR", "keep-me")

    _, client = _client(bench_root, allow_mef=True)

    captured: dict = {}
    release = threading.Event()

    def fake_popen(args, cwd, env, stdout, stderr, start_new_session):
        captured["env"] = dict(env)
        return _stub_popen(returncode=0, block=release)(args, cwd, env, stdout, stderr, start_new_session)

    with patch("admin.backend.api.v1.mef.subprocess.Popen", side_effect=fake_popen):
        client.post(
            "/api/v1/mef/projects",
            json={"profile": "v16", "directory": "_demo/y", "overlays": ["mcp"]},
        )
    release.set()

    assert not any(key.startswith("MISE_") for key in captured["env"])
    assert captured["env"]["SOME_UNRELATED_VAR"] == "keep-me"


def test_resume_spawns_headless_mise_resume_in_project_dir(tmp_path: Path) -> None:
    bench_root = tmp_path / "host" / "app"
    mef_root = bench_root.parent.parent
    target = mef_root / "stuck"
    target.mkdir()
    (target / ".miserc.toml").write_text('env = ["v12"]\n')

    _, client = _client(bench_root, allow_mef=True)

    captured: dict = {}

    def fake_popen(args, cwd, env, stdout, stderr, start_new_session):
        captured["args"] = args
        captured["cwd"] = cwd
        return _stub_popen(returncode=0)(args, cwd, env, stdout, stderr, start_new_session)

    with patch("admin.backend.api.v1.mef.subprocess.Popen", side_effect=fake_popen):
        response = client.post("/api/v1/mef/projects/stuck/resume")

    assert response.status_code == 202
    assert response.get_json()["job_id"].startswith("resume-")
    assert captured["args"][1:4] == ["r", "resume"]
    # Scoped to the project's own dir, not the mef root — same reasoning as pilot-up/down.
    assert captured["cwd"] == str(target)


def test_resume_rejects_unknown_project(tmp_path: Path) -> None:
    bench_root = tmp_path / "host" / "app"
    _, client = _client(bench_root, allow_mef=True)

    response = client.post("/api/v1/mef/projects/ghost/resume")

    assert response.status_code == 404
    assert response.get_json()["error"]["code"] == "project_not_found"


def test_doctor_rejects_unknown_project(tmp_path: Path) -> None:
    bench_root = tmp_path / "host" / "app"
    _, client = _client(bench_root, allow_mef=True)

    response = client.get("/api/v1/mef/projects/ghost/doctor")

    assert response.status_code == 404
    assert response.get_json()["error"]["code"] == "project_not_found"


def test_doctor_flags_orphan_when_reachable_but_pid_mismatch(tmp_path: Path) -> None:
    """The "phantom daemon" bug from this session: pitchfork's tracked PID for a
    daemon no longer owns the port (a not-fully-reaped restart left a stray sibling
    process squatting it) — the port answers, but the tracked PID doesn't hold it."""
    bench_root = tmp_path / "host" / "app"
    mef_root = bench_root.parent.parent
    target = mef_root / "flaky"
    target.mkdir()
    (target / ".miserc.toml").write_text('env = ["v12"]\n')
    (target / ".env").write_text("WEB_PORT=8039\nREDIS_PORT=6436\nSITE_DOMAIN=flaky.localhost\n")

    _, client = _client(bench_root, allow_mef=True)

    pitchfork_json = [
        {"namespace": "flaky", "name": "bench", "status": "running", "pid": 111},
        {"namespace": "flaky", "name": "redis", "status": "running", "pid": 222},
        {"namespace": "other-project", "name": "bench", "status": "running", "pid": 999},
    ]

    class FakeCompleted:
        stdout = __import__("json").dumps(pitchfork_json)

    class FakeConn:
        def __init__(self, port):
            self.status = "LISTEN"
            self.laddr = type("Addr", (), {"port": port})()

    class FakeProcess:
        def __init__(self, pid):
            self.pid = pid

        def net_connections(self, kind="inet"):
            # pid 111 (bench, the tracked supervisor) doesn't itself hold 8039 -> checked
            # via children() instead, same as the real "mise run start" -> honcho tree.
            # pid 222 (redis) correctly holds 6436 directly -> no orphan.
            return [FakeConn(6436)] if self.pid == 222 else []

        def children(self, recursive=True):
            # bench's real listening socket lives on a grandchild it spawned, never on
            # the tracked supervisor PID itself -> still no match, orphan_suspected stays.
            return []

    def fake_connect_ex(self, addr):
        # Both ports answer (something is listening), regardless of who.
        return 0

    class FakePingResponse:
        status_code = 200
        text = '{"message":"pong"}'

    with (
        patch("admin.backend.api.v1.mef.subprocess.run", return_value=FakeCompleted()),
        patch("admin.backend.api.v1.mef.psutil.Process", side_effect=FakeProcess),
        patch("admin.backend.api.v1.mef.socket.socket.connect_ex", fake_connect_ex),
        patch("admin.backend.api.v1.mef.requests.get", return_value=FakePingResponse()) as ping,
    ):
        response = client.get("/api/v1/mef/projects/flaky/doctor")

    # bench is "running" with a site_domain/web_port -> doctor pings frappe.ping too.
    ping.assert_called_once()
    assert ping.call_args.kwargs["headers"] == {"Host": "flaky.localhost"}

    assert response.status_code == 200
    body = response.get_json()
    assert body["daemons"]["bench"] == {"status": "running", "pid": 111}
    # "other-project"'s daemon must not leak into "flaky"'s report.
    assert set(body["daemons"]) == {"bench", "redis"}

    assert body["ports"]["bench"]["reachable"] is True
    assert body["ports"]["bench"]["owned_by_tracked_pid"] is False
    assert body["ports"]["bench"]["orphan_suspected"] is True

    assert body["ports"]["redis"]["owned_by_tracked_pid"] is True
    assert body["ports"]["redis"]["orphan_suspected"] is False

    assert body["frappe_ping"] == {"ok": True, "status": 200, "body": '{"message":"pong"}'}


def test_pid_owns_port_checks_descendants_not_just_the_tracked_pid() -> None:
    """Real bug caught testing this against a live project: pitchfork tracks the
    ``mise run start`` supervisor PID, but the actual listening socket belongs to a
    grandchild it spawned via honcho (bench's own werkzeug worker). Checking only the
    tracked PID's own sockets makes every healthy daemon read as "orphaned"."""
    import admin.backend.api.v1.mef as mef_module

    class FakeConn:
        def __init__(self, port):
            self.status = "LISTEN"
            self.laddr = type("Addr", (), {"port": port})()

    class FakeProc:
        def __init__(self, pid, own_ports=(), kids=()):
            self.pid = pid
            self._own_ports = own_ports
            self._kids = kids

        def net_connections(self, kind="inet"):
            return [FakeConn(p) for p in self._own_ports]

        def children(self, recursive=True):
            return self._kids

    grandchild = FakeProc(3, own_ports=[8039])
    child = FakeProc(2, own_ports=[], kids=[grandchild])
    supervisor = FakeProc(1, own_ports=[], kids=[child, grandchild])

    with patch("admin.backend.api.v1.mef.psutil.Process", return_value=supervisor):
        assert mef_module._pid_owns_port(1, 8039) is True
        assert mef_module._pid_owns_port(1, 9999) is False


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
