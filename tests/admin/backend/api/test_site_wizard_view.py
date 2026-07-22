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


def test_setup_status_parses_db_call_output(tmp_path: Path) -> None:
    """setup-status runs frappe.db.get_single_value via the venv Python directly, not
    `bench execute` — that fails on Frappe v12 (frappe.db isn't a real importable
    submodule there, only a runtime attribute; see _run_db_method's docstring)."""
    bench_root = tmp_path / "app"
    client = _client(bench_root)
    _write_site(bench_root)

    completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="1\n", stderr="")
    with patch("admin.backend.api.v1.sites.wizard.subprocess.run", return_value=completed) as run:
        response = client.get("/api/v1/sites/s.localhost/setup-status")

    assert response.status_code == 200
    assert response.get_json()["setup_complete"] is True
    args = run.call_args.args[0]
    assert args[0] == str(bench_root / "env" / "bin" / "python")
    assert args[1] == "-c"
    assert args[3] == "s.localhost"
    # frappe.init(sites_path='.') resolves relative to cwd - must be bench_root/sites,
    # not bench_root, or "Site s.localhost does not exist" (caught live, see commit).
    assert run.call_args.kwargs["cwd"] == str(bench_root / "sites")


def test_setup_status_none_when_db_call_fails(tmp_path: Path) -> None:
    bench_root = tmp_path / "app"
    client = _client(bench_root)
    _write_site(bench_root)

    completed = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="boom")
    with patch("admin.backend.api.v1.sites.wizard.subprocess.run", return_value=completed):
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
