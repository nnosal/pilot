import json
import shlex
from pathlib import Path

import pytest

from pilot.config import (
    AppConfig,
    BenchConfig,
    MariaDBConfig,
    RedisConfig,
    SiteConfig,
    WorkerConfig,
    WorkerGroup,
)
from pilot.core.app import App, RevisionPin
from pilot.core.bench import Bench
from pilot.core.server import Server
from pilot.core.site import Site
from pilot.exceptions import BenchError
from pilot.managers.processes.local import ProcessManager

FIXTURES_DIR = Path(__file__).parent.parent.parent / "fixtures"


def make_bench(tmp_path: Path) -> Bench:
    config = BenchConfig(
        name="test-bench",
        python_version="3.14",
        apps=[
            AppConfig(name="frappe", repo="https://github.com/frappe/frappe", branch="version-16"),
        ],
        mariadb=MariaDBConfig(root_password="root"),
        redis=RedisConfig(cache_port=13000, queue_port=11000),
        workers=WorkerConfig(
            groups=[
                WorkerGroup(queues=["default"], count=2),
                WorkerGroup(queues=["short"], count=1),
                WorkerGroup(queues=["long"], count=1),
            ]
        ),
    )
    return Bench(config, tmp_path)


def _write_bench_toml(bench_dir: Path, name: str) -> None:
    bench_dir.mkdir(parents=True)
    (bench_dir / "bench.toml").write_text(BenchConfig.from_flat(name).dumps())


def test_bench_loads_from_path(tmp_path: Path) -> None:
    bench_dir = tmp_path / "benches" / "alpha"
    _write_bench_toml(bench_dir, "alpha")

    bench = Bench(bench_dir)

    assert bench.path == bench_dir
    assert bench.config.name == "alpha"


def test_bench_loads_name_from_known_benches_dir(tmp_path: Path, monkeypatch) -> None:
    bench_dir = tmp_path / "benches" / "alpha"
    _write_bench_toml(bench_dir, "alpha")
    monkeypatch.setattr("pilot.utils.cli_root", lambda: tmp_path)

    bench = Bench("alpha")

    assert bench.path == bench_dir
    assert bench.config.name == "alpha"


def test_server_resolves_bench_by_name(tmp_path: Path, monkeypatch) -> None:
    bench_dir = tmp_path / "benches" / "alpha"
    _write_bench_toml(bench_dir, "alpha")
    monkeypatch.setattr("pilot.utils.cli_root", lambda: tmp_path)

    bench = Server().bench("alpha")

    assert bench.path == bench_dir
    assert bench.config.name == "alpha"


def test_app_is_cloned_returns_false_for_nonexistent_path(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    app_config = AppConfig(name="frappe", repo="https://example.com/frappe", branch="main")
    app = App(app_config, bench)
    assert app.is_cloned is False


def test_app_is_cloned_returns_false_when_no_git_directory(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    app_config = AppConfig(name="myapp", repo="https://example.com/myapp", branch="main")
    app = App(app_config, bench)
    app.path.mkdir(parents=True)
    assert app.is_cloned is False


def test_app_is_cloned_returns_true_when_git_directory_exists(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    app_config = AppConfig(name="myapp", repo="https://example.com/myapp", branch="main")
    app = App(app_config, bench)
    app.path.mkdir(parents=True)
    (app.path / ".git").mkdir()
    assert app.is_cloned is True


def _init_git_repo(path: Path) -> None:
    import subprocess

    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "t@t.com"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "t"], check=True)


def _commit(path: Path, message: str) -> None:
    import subprocess

    (path / "f").write_text(message)
    subprocess.run(["git", "-C", str(path), "add", "f"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", message], check=True)


def _tag(path: Path, tag: str) -> None:
    import subprocess

    subprocess.run(["git", "-C", str(path), "tag", tag], check=True)


def test_revision_pin_from_marketplace_target_tag() -> None:
    pin = RevisionPin.from_marketplace_target({"target_type": "tag", "target": "v1.0.0"})
    assert pin == RevisionPin(kind="tag", ref="v1.0.0")


def test_revision_pin_from_marketplace_target_commit() -> None:
    pin = RevisionPin.from_marketplace_target({"target_type": "commit", "target": "abc123"})
    assert pin == RevisionPin(kind="commit", ref="abc123")


def test_revision_pin_from_marketplace_target_branch_is_none() -> None:
    # A branch is not a fixed revision to pin to - no RevisionPin for it.
    assert RevisionPin.from_marketplace_target({"target_type": "branch", "target": "main"}) is None


def test_app_is_on_revision_tag_matches(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    app = App(AppConfig(name="myapp", repo="r", branch=""), bench)
    app.path.mkdir(parents=True)
    _init_git_repo(app.path)
    _commit(app.path, "c1")
    _tag(app.path, "v1.0.0")

    assert app.is_on_revision(RevisionPin(kind="tag", ref="v1.0.0")) is True


def test_app_is_on_revision_tag_mismatch(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    app = App(AppConfig(name="myapp", repo="r", branch=""), bench)
    app.path.mkdir(parents=True)
    _init_git_repo(app.path)
    _commit(app.path, "c1")
    _tag(app.path, "v1.0.0")

    # Marketplace has since moved to a newer tag the app hasn't been updated to.
    assert app.is_on_revision(RevisionPin(kind="tag", ref="v2.0.0")) is False


def test_app_is_on_revision_no_tag_at_head(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    app = App(AppConfig(name="myapp", repo="r", branch=""), bench)
    app.path.mkdir(parents=True)
    _init_git_repo(app.path)
    _commit(app.path, "c1")  # HEAD has no tag

    assert app.is_on_revision(RevisionPin(kind="tag", ref="v1.0.0")) is False


def test_app_is_on_revision_commit_prefix_matches(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    app = App(AppConfig(name="myapp", repo="r", branch=""), bench)
    app.path.mkdir(parents=True)
    _init_git_repo(app.path)
    _commit(app.path, "c1")
    full_sha = app.installed_hash

    # Registry may store an abbreviated hash; a full HEAD sha starting with it counts.
    assert app.is_on_revision(RevisionPin(kind="commit", ref=full_sha[:8])) is True


def test_app_is_on_revision_commit_mismatch(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    app = App(AppConfig(name="myapp", repo="r", branch=""), bench)
    app.path.mkdir(parents=True)
    _init_git_repo(app.path)
    _commit(app.path, "c1")

    assert app.is_on_revision(RevisionPin(kind="commit", ref="0" * 40)) is False


def test_app_has_remote_update_false_without_tracked_branch(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    app = App(AppConfig(name="myapp", repo="r", branch=""), bench)
    app.path.mkdir(parents=True)
    _init_git_repo(app.path)
    _commit(app.path, "c1")

    # No configured remote at all - must not crash, and detached HEAD has
    # no branch tip to compare against.
    assert app.has_remote_update() is False


def _clone_at_tag(remote: Path, clone_dir: Path, tag: str, shallow: bool = True) -> None:
    import subprocess

    subprocess.run(["git", "clone", "-q", str(remote), str(clone_dir)], check=True)
    if shallow:
        subprocess.run(
            ["git", "-C", str(clone_dir), "fetch", "-q", "origin", tag, "--depth", "1"], check=True
        )
    subprocess.run(["git", "-C", str(clone_dir), "checkout", "-q", tag], check=True)


def test_app_update_with_tag_target_checks_out_advertised_tag_not_latest(tmp_path: Path) -> None:
    remote = tmp_path / "remote"
    remote.mkdir()
    _init_git_repo(remote)
    _commit(remote, "c1")
    _tag(remote, "v1.0.0")
    _commit(remote, "c2")
    _tag(remote, "v2.0.0")  # the repo's actual latest tag
    _commit(remote, "c3")
    _tag(remote, "v3.0.0")  # marketplace hasn't advertised this one yet

    bench = make_bench(tmp_path)
    app = App(AppConfig(name="myapp", repo="r", branch=""), bench)
    _clone_at_tag(remote, app.path, "v1.0.0")

    # Marketplace's next advertised pin is v2.0.0 - not the repo's true latest (v3.0.0).
    app.update(pin=RevisionPin(kind="tag", ref="v2.0.0"))

    assert app.is_on_revision(RevisionPin(kind="tag", ref="v2.0.0")) is True
    assert app.is_on_revision(RevisionPin(kind="tag", ref="v3.0.0")) is False


def test_app_update_with_commit_target_checks_out_advertised_commit(tmp_path: Path) -> None:
    remote = tmp_path / "remote"
    remote.mkdir()
    _init_git_repo(remote)
    _commit(remote, "c1")
    _tag(remote, "v1.0.0")
    _commit(remote, "c2")

    bench = make_bench(tmp_path)
    app = App(AppConfig(name="myapp", repo="r", branch=""), bench)
    _clone_at_tag(remote, app.path, "v1.0.0")

    import subprocess

    target_sha = subprocess.run(
        ["git", "-C", str(remote), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()

    app.update(pin=RevisionPin(kind="commit", ref=target_sha))

    assert app.installed_hash == target_sha


def test_app_has_marketplace_update_false_when_pinned_tag_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bench = make_bench(tmp_path)
    app = App(AppConfig(name="myapp", repo="https://github.com/frappe/myapp", branch=""), bench)
    app.path.mkdir(parents=True)
    _init_git_repo(app.path)
    _commit(app.path, "c1")
    _tag(app.path, "v1.0.0")
    monkeypatch.setattr("pilot.core.app.installed_app_version", lambda *_: "1.0.0")
    entry = {
        "repo": "https://github.com/frappe/myapp",
        "targets": [{"version": "1.0.0", "target_type": "tag", "target": "v1.0.0"}],
    }

    assert app.has_marketplace_update(entry) is False


def test_app_has_marketplace_update_true_when_marketplace_tag_moved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bench = make_bench(tmp_path)
    app = App(AppConfig(name="myapp", repo="https://github.com/frappe/myapp", branch=""), bench)
    app.path.mkdir(parents=True)
    _init_git_repo(app.path)
    _commit(app.path, "c1")
    _tag(app.path, "v0.9.0")  # installed at the version's old tag
    monkeypatch.setattr("pilot.core.app.installed_app_version", lambda *_: "1.0.0")
    entry = {
        "repo": "https://github.com/frappe/myapp",
        # Entries only ever advance - the tag for 1.0.0 has since moved.
        "targets": [{"version": "1.0.0", "target_type": "tag", "target": "v1.0.0"}],
    }

    assert app.has_marketplace_update(entry) is True


def test_app_has_marketplace_update_falls_back_to_remote_check_on_repo_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A fork with a different repo URL isn't the marketplace's app.
    bench = make_bench(tmp_path)
    app = App(AppConfig(name="myapp", repo="https://github.com/someone/fork", branch=""), bench)
    monkeypatch.setattr(App, "has_remote_update", lambda self: True)
    entry = {
        "repo": "https://github.com/frappe/myapp",
        "targets": [{"version": "1.0.0", "target_type": "tag", "target": "v1.0.0"}],
    }

    assert app.has_marketplace_update(entry) is True


def test_app_has_marketplace_update_falls_back_to_remote_check_when_not_in_marketplace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bench = make_bench(tmp_path)
    app = App(AppConfig(name="myapp", repo="https://github.com/frappe/myapp", branch=""), bench)
    monkeypatch.setattr(App, "has_remote_update", lambda self: False)

    assert app.has_marketplace_update(None) is False


def test_app_has_marketplace_update_falls_back_to_remote_check_for_branch_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bench = make_bench(tmp_path)
    app = App(AppConfig(name="myapp", repo="https://github.com/frappe/myapp", branch="main"), bench)
    monkeypatch.setattr("pilot.core.app.installed_app_version", lambda *_: "3.0.0")
    monkeypatch.setattr(App, "has_remote_update", lambda self: True)
    entry = {
        "repo": "https://github.com/frappe/myapp",
        "targets": [{"version": "3.0.0", "target_type": "branch", "target": "main"}],
    }

    assert app.has_marketplace_update(entry) is True


def test_app_path_is_under_apps_directory(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    app_config = AppConfig(name="frappe", repo="https://example.com", branch="main")
    app = App(app_config, bench)
    assert app.path == tmp_path / "apps" / "frappe"


def test_site_exists_returns_false_for_nonexistent_path(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    site_config = SiteConfig(name="site1.localhost", apps=["frappe"])
    site = Site(site_config, bench)
    assert site.exists is False


def test_site_exists_returns_false_when_no_site_config_json(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    site_config = SiteConfig(name="site1.localhost", apps=["frappe"])
    site = Site(site_config, bench)
    site.path.mkdir(parents=True)
    assert site.exists is False


def test_site_exists_returns_true_when_site_config_json_present(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    site_config = SiteConfig(name="site1.localhost", apps=["frappe"])
    site = Site(site_config, bench)
    site.path.mkdir(parents=True)
    (site.path / "site_config.json").write_text("{}")
    assert site.exists is True


def test_site_path_is_under_sites_directory(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    site_config = SiteConfig(name="site1.localhost", apps=["frappe"])
    site = Site(site_config, bench)
    assert site.path == tmp_path / "sites" / "site1.localhost"


def test_bench_create_directories(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.create_directories()
    assert (tmp_path / "apps").is_dir()
    assert (tmp_path / "sites").is_dir()
    assert (tmp_path / "sites" / "assets").is_dir()
    assert (tmp_path / "logs").is_dir()
    assert (tmp_path / "config").is_dir()
    assert (tmp_path / "pids").is_dir()


def test_bench_apps_scans_filesystem(tmp_path: Path) -> None:
    """bench.apps() discovers apps from apps/ directory, not bench.toml."""
    bench = make_bench(tmp_path)
    bench.create_directories()

    # Create a fake cloned app
    app_dir = tmp_path / "apps" / "testapp"
    app_dir.mkdir()
    (app_dir / ".git").mkdir()

    apps = bench.apps()
    assert len(apps) == 1
    assert apps[0].config.name == "testapp"


def test_bench_apps_ignores_non_git_directories(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.create_directories()
    (tmp_path / "apps" / "notapp").mkdir()  # no .git

    apps = bench.apps()
    assert apps == []


def test_bench_sites_scans_filesystem(tmp_path: Path) -> None:
    """bench.sites() discovers sites from sites/ directory."""
    bench = make_bench(tmp_path)
    bench.create_directories()

    site_dir = tmp_path / "sites" / "site1.localhost"
    site_dir.mkdir()
    (site_dir / "site_config.json").write_text("{}")

    sites = bench.sites()
    assert len(sites) == 1
    assert sites[0].config.name == "site1.localhost"


def test_bench_init_apps_comes_from_config(tmp_path: Path) -> None:
    """bench.init_apps() returns apps from bench.toml (used during bench init)."""
    bench = make_bench(tmp_path)
    init_apps = bench.init_apps()
    assert len(init_apps) == 1
    assert init_apps[0].config.name == "frappe"


def test_process_definitions_returns_correct_count(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    # workers: default=2, short=1, long=1 => 4 worker processes
    # plus web, socketio, redis_cache, redis_queue = 4
    # plus admin = 1
    # total = 9
    process_manager = ProcessManager(bench)
    definitions = process_manager._process_definitions()
    assert len(definitions) == 9
    assert "watch" not in [pd.name for pd in definitions]
    assert "admin-ui" not in [pd.name for pd in definitions]


def test_process_definitions_watch_admin_js_adds_vite_ui(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    definitions = ProcessManager(bench, watch_admin_js=True)._process_definitions()
    assert "admin-ui" in [pd.name for pd in definitions]
    assert len(definitions) == 10


def test_process_definitions_can_enable_app_watch(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.config.watch_apps_js = True
    definitions = ProcessManager(bench)._process_definitions()
    assert "watch" in [pd.name for pd in definitions]
    watch = next(pd for pd in definitions if pd.name == "watch")
    assert "frappe watch" in shlex.join(watch.argv)
    assert watch.working_dir == bench.sites_path
    assert len(definitions) == 10


def test_process_definitions_worker_names_are_numbered(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    process_manager = ProcessManager(bench)
    definitions = process_manager._process_definitions()
    names = [pd.name for pd in definitions]
    assert "worker_default_1" in names
    assert "worker_default_2" in names
    assert "worker_short_1" in names
    assert "worker_long_1" in names


def test_process_definitions_includes_redis_processes(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    process_manager = ProcessManager(bench)
    definitions = process_manager._process_definitions()
    names = [pd.name for pd in definitions]
    assert "redis_cache" in names
    assert "redis_queue" in names
    assert "redis_socketio" not in names


def test_process_definitions_order_starts_with_web(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    process_manager = ProcessManager(bench)
    definitions = process_manager._process_definitions()
    assert definitions[0].name == "web"


def test_dev_web_disables_python_reloader_by_default(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    web = ProcessManager(bench)._process_definitions()[0]
    assert web.name == "web"
    assert "--noreload" in web.argv


def test_dev_web_can_enable_python_reloader(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.config.reload_python = True
    web = ProcessManager(bench)._process_definitions()[0]
    assert web.name == "web"
    assert "--noreload" not in web.argv


def test_honcho_generate_config_writes_procfile(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.create_directories()
    process_manager = ProcessManager(bench)
    process_manager.write_config()

    procfile = tmp_path / "config" / "Procfile"
    assert procfile.exists()
    content = procfile.read_text()
    assert "web:" in content
    assert "socketio:" in content
    assert "worker_default_1:" in content
    assert "watch:" not in content
    assert "redis_cache:" in content


def test_honcho_generate_config_writes_redis_configs(tmp_path: Path) -> None:
    # The generated Procfile runs `redis-server config/redis_{cache,queue}.conf`,
    # so write_config must (re)create those files. Regression: an upgraded
    # bench whose config dir predates the split redis layout used to fail to
    # start with "can't open config file".
    bench = make_bench(tmp_path)
    bench.create_directories()
    process_manager = ProcessManager(bench)
    process_manager.write_config()

    assert (tmp_path / "config" / "redis_cache.conf").exists()
    assert (tmp_path / "config" / "redis_queue.conf").exists()


def test_honcho_generate_config_procfile_format(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.create_directories()
    process_manager = ProcessManager(bench)
    process_manager.write_config()

    procfile = tmp_path / "config" / "Procfile"
    content = procfile.read_text()
    for line in content.strip().splitlines():
        assert ": " in line, f"Line missing ': ' separator: {line!r}"


def test_honcho_start_writes_per_process_pid_files(tmp_path: Path) -> None:
    """Each spawned process gets its own pids/<name>.pid file."""
    from unittest.mock import MagicMock, patch

    bench = make_bench(tmp_path)
    bench.create_directories()
    process_manager = ProcessManager(bench)
    process_manager.write_config()

    fake_proc = MagicMock()
    fake_proc.pid = 12345
    fake_proc.stdout = iter([])
    fake_proc.poll.return_value = None
    fake_proc.wait.return_value = 0

    def fake_popen(cmd, **kwargs):
        return fake_proc

    with (
        patch("pilot.managers.processes.local.subprocess.Popen", side_effect=fake_popen),
        patch.object(process_manager, "_stop_all"),
    ):
        for pd in process_manager._process_definitions():
            proc = fake_popen(pd.argv)
            process_manager._procs[pd.name] = proc
            (bench.pids_path / f"{pd.name}.pid").write_text(str(proc.pid))

    for name in process_manager._procs:
        pid_file = bench.pids_path / f"{name}.pid"
        assert pid_file.exists(), f"Missing PID file for process '{name}'"
        assert pid_file.read_text().strip() == "12345"


def _capture_site_cmd(monkeypatch) -> dict:
    """`cmd`/`kwargs` always reflect the FIRST run_command call - the one under
    test for restore/reinstall/migrate (which only ever make one call) and for
    create (whose first call is new-site; create() also fires a second,
    best-effort set-config call - see `calls` for that one)."""
    captured: dict = {"calls": []}

    def _fake_run_command(cmd, **kw):
        captured["calls"].append({"cmd": cmd, "kwargs": kw})
        captured["cmd"] = captured["calls"][0]["cmd"]
        captured["kwargs"] = captured["calls"][0]["kwargs"]

    monkeypatch.setattr("pilot.core.site.commands.run_command", _fake_run_command)
    return captured


def _postgres_bench(tmp_path: Path, **postgres):
    bench = make_bench(tmp_path)
    bench.config.db_type = "postgres"
    for key, value in postgres.items():
        setattr(bench.config.postgres, key, value)
    return bench


def test_site_create_postgres_builds_db_args(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bench = _postgres_bench(tmp_path, root_password="pgsecret", port=5433)
    captured = _capture_site_cmd(monkeypatch)

    Site(SiteConfig(name="pg.localhost", apps=["frappe"], admin_password="secret"), bench).create()

    cmd = captured["cmd"]
    assert cmd[cmd.index("--db-type") + 1] == "postgres"
    assert cmd[cmd.index("--db-host") + 1] == "localhost"
    assert cmd[cmd.index("--db-port") + 1] == "5433"
    assert cmd[cmd.index("--mariadb-root-username") + 1] == "postgres"
    assert cmd[cmd.index("--mariadb-root-password") + 1] == "pgsecret"
    assert "--db-socket" not in cmd


def test_site_create_mariadb_when_bench_is_mariadb(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bench = make_bench(tmp_path)  # bench db_type defaults to mariadb
    captured = _capture_site_cmd(monkeypatch)
    monkeypatch.setattr("pilot.managers.database.mariadb.MariaDBManager._detect_socket", lambda self: "")

    Site(SiteConfig(name="mdb.localhost", apps=["frappe"], admin_password="secret"), bench).create()

    cmd = captured["cmd"]
    # mariadb is frappe's default engine - no --db-type flag is passed
    assert "--db-type" not in cmd
    assert cmd[cmd.index("--mariadb-root-username") + 1] == "root"
    assert "--db-host" in cmd


def test_site_create_enables_developer_mode_after_new_site(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """developer_mode=0 (the frappe default) routes get_hooks() through a
    Redis-cached path that pickles the collected hooks dict - if any installed
    app's hooks.py holds an unpicklable value, that raises "can't pickle
    module objects" (confirmed live, installing a real app on a freshly
    created site). developer_mode=1 skips the cache entirely; mef's own
    site:new task sets it for the same reason, so create() must too."""
    bench = make_bench(tmp_path)
    captured = _capture_site_cmd(monkeypatch)
    monkeypatch.setattr("pilot.managers.database.mariadb.MariaDBManager._detect_socket", lambda self: "")

    Site(SiteConfig(name="mdb.localhost", apps=["frappe"], admin_password="secret"), bench).create()

    assert len(captured["calls"]) == 2
    set_config_cmd = captured["calls"][1]["cmd"]
    assert set_config_cmd[-3:] == ["set-config", "developer_mode", "1"]


def test_site_create_ignores_set_config_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The developer_mode convenience setting is best-effort - it must not fail
    an otherwise-successful site creation."""
    from pilot.exceptions import CommandError

    bench = make_bench(tmp_path)
    monkeypatch.setattr("pilot.managers.database.mariadb.MariaDBManager._detect_socket", lambda self: "")
    calls = []

    def _fake_run_command(cmd, **kw):
        calls.append(cmd)
        if len(calls) == 2:
            raise CommandError("boom", 1)

    monkeypatch.setattr("pilot.core.site.commands.run_command", _fake_run_command)

    Site(SiteConfig(name="mdb.localhost", apps=["frappe"], admin_password="secret"), bench).create()

    assert len(calls) == 2


def test_supports_db_socket_option_false_when_frappe_checkout_missing(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    assert bench.supports_db_socket_option is False


def test_supports_db_socket_option_true_when_frappe_has_the_flag(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    site_py = bench.apps_path / "frappe" / "frappe" / "commands" / "site.py"
    site_py.parent.mkdir(parents=True)
    site_py.write_text("@click.option('--db-socket', '--mariadb-db-socket')\n")

    assert bench.supports_db_socket_option is True


def test_supports_db_socket_option_false_on_frappe_v12(tmp_path: Path) -> None:
    """v12's frappe/commands/site.py has no --db-socket option at all (confirmed
    live and by reading its source: only --db-host/--db-port/--no-mariadb-socket)."""
    bench = make_bench(tmp_path)
    site_py = bench.apps_path / "frappe" / "frappe" / "commands" / "site.py"
    site_py.parent.mkdir(parents=True)
    site_py.write_text("@click.option('--no-mariadb-socket', is_flag=True)\n")

    assert bench.supports_db_socket_option is False


def test_site_create_mariadb_sets_mysql_tcp_port_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """frappe's DbManager.restore_database shells out to the `mysql` CLI with
    -h but no -P/--port - on mef's non-default dbdeployer ports that connects
    to the wrong (default) port and the SQL import silently fails. `mysql`
    falls back to MYSQL_TCP_PORT when no -P is given, so it must be set here.
    Confirmed live: new-site failed with "Can't connect to server on
    '127.0.0.1'" every time until this env var matched the real port."""
    bench = make_bench(tmp_path)
    bench.config.mariadb.port = 8416
    captured = _capture_site_cmd(monkeypatch)
    monkeypatch.setattr("pilot.managers.database.mariadb.MariaDBManager._detect_socket", lambda self: "")

    Site(SiteConfig(name="mdb.localhost", apps=["frappe"], admin_password="secret"), bench).create()

    assert captured["kwargs"]["env"]["MYSQL_TCP_PORT"] == "8416"


def test_site_create_sqlite_does_not_set_mysql_tcp_port_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bench = make_bench(tmp_path)
    bench.config.db_type = "sqlite"
    captured = _capture_site_cmd(monkeypatch)

    Site(SiteConfig(name="sq.localhost", apps=["frappe"], admin_password="secret"), bench).create()

    assert captured["kwargs"]["env"] is None


def test_site_restore_mariadb_sets_mysql_tcp_port_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bench = make_bench(tmp_path)
    bench.config.mariadb.port = 8416
    captured = _capture_site_cmd(monkeypatch)

    Site(SiteConfig(name="m.localhost", apps=[]), bench).restore("/tmp/db.sql.gz")

    assert captured["kwargs"]["env"]["MYSQL_TCP_PORT"] == "8416"


def test_site_reinstall_mariadb_sets_mysql_tcp_port_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bench = make_bench(tmp_path)
    bench.config.mariadb.port = 8416
    captured = _capture_site_cmd(monkeypatch)

    Site(SiteConfig(name="m.localhost", apps=[]), bench).reinstall("secret")

    assert captured["kwargs"]["env"]["MYSQL_TCP_PORT"] == "8416"


def test_site_create_mariadb_falls_back_to_db_host_when_socket_unsupported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """v12 has no --db-socket option at all - even when a real socket path is
    detected, new-site must fall back to --db-host/--db-port there (confirmed
    live: passing --db-socket raised "no such option: --db-[redacted]-username"
    for the neighboring flag, and --db-socket is equally absent)."""
    bench = make_bench(tmp_path)
    captured = _capture_site_cmd(monkeypatch)
    monkeypatch.setattr(
        "pilot.managers.database.mariadb.MariaDBManager._detect_socket", lambda self: "/tmp/mysql.sock"
    )

    Site(SiteConfig(name="mdb.localhost", apps=["frappe"], admin_password="secret"), bench).create()

    cmd = captured["cmd"]
    assert "--db-socket" not in cmd
    assert "--db-host" in cmd


def test_site_create_mariadb_uses_db_socket_when_supported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bench = make_bench(tmp_path)
    site_py = bench.apps_path / "frappe" / "frappe" / "commands" / "site.py"
    site_py.parent.mkdir(parents=True)
    site_py.write_text("@click.option('--db-socket', '--mariadb-db-socket')\n")
    captured = _capture_site_cmd(monkeypatch)
    monkeypatch.setattr(
        "pilot.managers.database.mariadb.MariaDBManager._detect_socket", lambda self: "/tmp/mysql.sock"
    )

    Site(SiteConfig(name="mdb.localhost", apps=["frappe"], admin_password="secret"), bench).create()

    cmd = captured["cmd"]
    assert cmd[cmd.index("--db-socket") + 1] == "/tmp/mysql.sock"


def test_site_restore_uses_postgres_root_creds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bench = _postgres_bench(tmp_path, root_password="pgpw")
    captured = _capture_site_cmd(monkeypatch)

    Site(SiteConfig(name="pg.localhost", apps=[]), bench).restore("/tmp/db.sql.gz")

    cmd = captured["cmd"]
    assert "restore" in cmd
    assert "--db-type" not in cmd  # restore reads the engine from the site's config
    assert cmd[cmd.index("--mariadb-root-username") + 1] == "postgres"
    assert cmd[cmd.index("--mariadb-root-password") + 1] == "pgpw"


def test_site_restore_uses_mariadb_root_creds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bench = make_bench(tmp_path)  # mariadb bench, root_password="root"
    captured = _capture_site_cmd(monkeypatch)

    Site(SiteConfig(name="m.localhost", apps=[]), bench).restore(
        "/tmp/db.sql.gz", public_files="/tmp/pub.tar", private_files="/tmp/priv.tar"
    )

    cmd = captured["cmd"]
    assert "--db-type" not in cmd
    assert cmd[cmd.index("--mariadb-root-username") + 1] == "root"
    assert cmd[cmd.index("--with-public-files") + 1] == "/tmp/pub.tar"
    assert cmd[cmd.index("--with-private-files") + 1] == "/tmp/priv.tar"


def test_site_reinstall_postgres_root_creds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bench = _postgres_bench(tmp_path, root_password="pgpw")
    captured = _capture_site_cmd(monkeypatch)

    Site(SiteConfig(name="pg.localhost", apps=[]), bench).reinstall("secret")

    cmd = captured["cmd"]
    assert "reinstall" in cmd and "--yes" in cmd
    assert cmd[cmd.index("--admin-password") + 1] == "secret"
    assert cmd[cmd.index("--mariadb-root-username") + 1] == "postgres"
    assert cmd[cmd.index("--mariadb-root-password") + 1] == "pgpw"


def test_site_reinstall_mariadb_root_creds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bench = make_bench(tmp_path)
    captured = _capture_site_cmd(monkeypatch)

    Site(SiteConfig(name="m.localhost", apps=[]), bench).reinstall("secret")

    cmd = captured["cmd"]
    assert cmd[cmd.index("--mariadb-root-username") + 1] == "root"


def test_site_create_and_reinstall_reject_empty_admin_password(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    site = Site(SiteConfig(name="m.localhost", apps=[]), bench)

    with pytest.raises(BenchError, match="must not be empty"):
        site.create()
    with pytest.raises(BenchError, match="must not be empty"):
        site.reinstall("   ")


def test_site_migrate_skip_failing_adds_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bench = make_bench(tmp_path)
    captured = _capture_site_cmd(monkeypatch)

    Site(SiteConfig(name="m.localhost", apps=[]), bench).migrate(skip_failing=True)

    assert "--skip-failing" in captured["cmd"]


def test_site_migrate_without_skip_failing_omits_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bench = make_bench(tmp_path)
    captured = _capture_site_cmd(monkeypatch)

    Site(SiteConfig(name="m.localhost", apps=[]), bench).migrate()

    assert "--skip-failing" not in captured["cmd"]


def test_site_create_postgres_empty_password_uses_placeholder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bench = _postgres_bench(tmp_path, root_password="")  # trust/peer auth - no password
    captured = _capture_site_cmd(monkeypatch)

    Site(SiteConfig(name="pg.localhost", apps=[], admin_password="secret"), bench).create()

    cmd = captured["cmd"]
    # frappe prompts on an empty password (hanging the task); a placeholder avoids it.
    assert cmd[cmd.index("--mariadb-root-password") + 1] == "trust_auth"


def test_bench_db_root_args_postgres(tmp_path: Path) -> None:
    bench = _postgres_bench(tmp_path, root_password="pgpw")
    assert bench.db_root_args == ["--mariadb-root-username", "postgres", "--mariadb-root-password", "pgpw"]


def test_bench_db_root_args_mariadb(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    assert bench.db_root_args == ["--mariadb-root-username", "root", "--mariadb-root-password", "root"]


def test_bench_drop_site_root_args_mariadb(tmp_path: Path) -> None:
    """drop-site uses a different flag spelling than new-site/restore/reinstall - v12's
    drop-site doesn't accept --mariadb-root-username at all, only --root-login
    (confirmed by reading frappe/commands/site.py; v13+ kept it as an alias there)."""
    bench = make_bench(tmp_path)
    assert bench.drop_site_root_args == ["--root-login", "root", "--root-password", "root"]


def test_bench_drop_site_root_args_postgres(tmp_path: Path) -> None:
    bench = _postgres_bench(tmp_path, root_password="pgpw")
    assert bench.drop_site_root_args == ["--root-login", "postgres", "--root-password", "pgpw"]


def _write_site_dir(bench: Bench, name: str) -> None:
    site_dir = bench.sites_path / name
    site_dir.mkdir(parents=True)
    (site_dir / "site_config.json").write_text("{}")


def test_clear_default_site_noop_for_single_site_bench(tmp_path: Path) -> None:
    from pilot.core.site.provisioning import SiteProvisioner

    bench = make_bench(tmp_path)
    bench.create_directories()
    _write_site_dir(bench, "only.localhost")
    (bench.sites_path / "currentsite.txt").write_text("only.localhost")

    SiteProvisioner(bench, "only.localhost", [], "secret").clear_default_site_if_multi_site()

    assert (bench.sites_path / "currentsite.txt").read_text() == "only.localhost"


def test_clear_default_site_removes_currentsite_txt_when_multi_site(tmp_path: Path) -> None:
    """bench start/frappe serve (no --site) falls back to sites/currentsite.txt for
    which site to pin the whole dev-server process to (frappe/utils/bench_helper.py's
    get_sites()) - stale content here silently breaks Host-based routing for every
    site except the one it names. Confirmed live: a real, valid session for a 2nd
    site got looked up against the wrong site's database and read back as Guest."""
    from pilot.core.site.provisioning import SiteProvisioner

    bench = make_bench(tmp_path)
    bench.create_directories()
    _write_site_dir(bench, "first.localhost")
    _write_site_dir(bench, "second.localhost")
    (bench.sites_path / "currentsite.txt").write_text("first.localhost")

    SiteProvisioner(bench, "second.localhost", [], "secret").clear_default_site_if_multi_site()

    assert not (bench.sites_path / "currentsite.txt").exists()


def test_clear_default_site_removes_common_config_default_site_when_multi_site(tmp_path: Path) -> None:
    from pilot.core.site.provisioning import SiteProvisioner

    bench = make_bench(tmp_path)
    bench.create_directories()
    _write_site_dir(bench, "first.localhost")
    _write_site_dir(bench, "second.localhost")
    (bench.sites_path / "common_site_config.json").write_text('{"default_site": "first.localhost"}')

    SiteProvisioner(bench, "second.localhost", [], "secret").clear_default_site_if_multi_site()

    config = json.loads((bench.sites_path / "common_site_config.json").read_text())
    assert "default_site" not in config
