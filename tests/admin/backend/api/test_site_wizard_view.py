"""Tests for /api/v1/sites/<name>/setup-status and /wizard."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from tests.admin.backend.test_admin_app import _client


def _write_site(bench_root: Path, name: str = "s.localhost", **config) -> None:
    site_path = bench_root / "sites" / name
    site_path.mkdir(parents=True)
    (site_path / "site_config.json").write_text(json.dumps({"installed_apps": ["frappe"], **config}))


def test_setup_status_404_when_site_missing(tmp_path: Path) -> None:
    bench_root = tmp_path / "app"
    client = _client(bench_root)

    response = client.get("/api/v1/sites/ghost.localhost/setup-status")

    assert response.status_code == 404


def test_setup_status_parses_bench_execute_output(tmp_path: Path) -> None:
    bench_root = tmp_path / "app"
    client = _client(bench_root)
    _write_site(bench_root)

    completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="1\n", stderr="")
    with (
        patch("admin.backend.api.v1.sites.wizard.shutil.which", return_value="/usr/bin/bench"),
        patch("admin.backend.api.v1.sites.wizard.subprocess.run", return_value=completed) as run,
    ):
        response = client.get("/api/v1/sites/s.localhost/setup-status")

    assert response.status_code == 200
    assert response.get_json()["setup_complete"] is True
    args = run.call_args.args[0]
    assert args[:3] == ["/usr/bin/bench", "--site", "s.localhost"]


def test_setup_status_none_when_bench_execute_fails(tmp_path: Path) -> None:
    bench_root = tmp_path / "app"
    client = _client(bench_root)
    _write_site(bench_root)

    completed = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="boom")
    with (
        patch("admin.backend.api.v1.sites.wizard.shutil.which", return_value="/usr/bin/bench"),
        patch("admin.backend.api.v1.sites.wizard.subprocess.run", return_value=completed),
    ):
        response = client.get("/api/v1/sites/s.localhost/setup-status")

    assert response.status_code == 200
    assert response.get_json()["setup_complete"] is None


def test_run_wizard_404_when_site_missing(tmp_path: Path) -> None:
    bench_root = tmp_path / "app"
    client = _client(bench_root)

    response = client.post("/api/v1/sites/ghost.localhost/wizard")

    assert response.status_code == 404


def test_run_wizard_spawns_mise_task_with_site_env(tmp_path: Path) -> None:
    bench_root = tmp_path / "app"
    client = _client(bench_root)
    _write_site(bench_root)

    with patch("admin.backend.api.v1.sites.wizard._spawn_job", return_value="wizard-abc") as spawn:
        response = client.post("/api/v1/sites/s.localhost/wizard")

    assert response.status_code == 202
    assert response.get_json()["job_id"] == "wizard-abc"
    args = spawn.call_args.kwargs["args"]
    assert args[-2:] == ["r", "wizard"]
    assert spawn.call_args.kwargs["cwd"] == bench_root.parent
    assert spawn.call_args.kwargs["env_extras"] == {
        "NONINTERACTIVE": "1",
        "SITE_DOMAIN": "s.localhost",
        "PROJECT_NAME": bench_root.name,
    }
