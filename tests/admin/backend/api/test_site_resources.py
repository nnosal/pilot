from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from admin.backend.api.v1.sites.core import _framework_branch, _mcp_status
from admin.backend.providers.apps import AppInfo
from tests.admin.backend.test_admin_app import _client


def _write_site(bench_root: Path, name: str) -> None:
    site_path = bench_root / "sites" / name
    site_path.mkdir(parents=True)
    (site_path / "site_config.json").write_text(json.dumps({"installed_apps": []}))


def _frappe_app(**overrides) -> AppInfo:
    fields = dict(
        name="frappe",
        title="Frappe Framework",
        description="",
        repo="",
        branch="develop",
        is_cloned=True,
        current_commit="",
        commit_message="",
        has_local_changes=False,
        installed_version="",
        has_update=False,
    )
    fields.update(overrides)
    return AppInfo(**fields)


def test_delete_site_returns_accepted_task_resource(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    _write_site(bench_root, "s.localhost")

    with patch(
        "pilot.internal.tasks.runner.task_workers.wake",
        return_value=False,
    ):
        response = client.delete("/api/v1/sites/s.localhost")

    body = response.get_json()
    assert response.status_code == 202
    assert response.headers["Location"] == f"/api/v1/tasks/{body['task_id']}"
    assert body["command"] == "drop-site"
    assert body["args"] == {"site": "s.localhost"}


def test_delete_site_returns_not_found_without_starting_task(tmp_path: Path) -> None:
    client = _client(tmp_path / "benches" / "current")

    with patch("admin.backend.api.v1.sites.core.DropSiteTask.queue") as queue:
        response = client.delete("/api/v1/sites/missing.localhost")

    assert response.status_code == 404
    queue.assert_not_called()


def test_delete_site_rejects_symlink_without_starting_task(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    outside = tmp_path / "outside"
    _write_site(outside, "linked.localhost")
    sites = bench_root / "sites"
    sites.mkdir()
    (sites / "linked.localhost").symlink_to(outside / "sites" / "linked.localhost", target_is_directory=True)

    with patch("admin.backend.api.v1.sites.core.DropSiteTask.queue") as queue:
        response = client.delete("/api/v1/sites/linked.localhost")

    assert response.status_code == 404
    queue.assert_not_called()


def test_same_site_mutations_cannot_queue_together(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)

    with (
        patch("admin.backend.api.v1.sites.core.new_site_name_error", return_value=None),
        patch(
            "pilot.internal.tasks.runner.task_workers.wake",
            return_value=False,
        ),
    ):
        first = client.post("/api/v1/sites", json={"name": "s.localhost"})
        conflict = client.post("/api/v1/sites", json={"name": "s.localhost"})

    assert first.status_code == 202
    assert conflict.status_code == 409
    assert conflict.get_json()["error"]["code"] == "task_conflict"


def test_invalid_idempotency_key_is_a_validation_error(tmp_path: Path) -> None:
    client = _client(tmp_path / "benches" / "current")

    with patch("admin.backend.api.v1.sites.core.new_site_name_error", return_value=None):
        response = client.post(
            "/api/v1/sites",
            json={"name": "s.localhost"},
            headers={"Idempotency-Key": "x" * 256},
        )

    assert response.status_code == 422
    assert response.get_json()["error"]["code"] == "invalid_task"


def test_mcp_status_none_without_project_env(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    assert _mcp_status(bench_root, "s.localhost") is None


def test_mcp_status_none_when_overlay_not_enabled(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    project_dir = bench_root.parent
    project_dir.mkdir(parents=True)
    (project_dir / ".env").write_text(
        "FRAPPE_OVERLAYS = kaliteos\nFRAPPE_MCP_TOKEN_S_LOCALHOST=key:secret\n"
    )
    assert _mcp_status(bench_root, "s.localhost") is None


def test_mcp_status_none_when_token_missing(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    project_dir = bench_root.parent
    project_dir.mkdir(parents=True)
    (project_dir / ".env").write_text("FRAPPE_OVERLAYS = mcp\n")
    assert _mcp_status(bench_root, "s.localhost") is None


def test_mcp_status_when_overlay_and_token_present(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    project_dir = bench_root.parent
    project_dir.mkdir(parents=True)
    (project_dir / ".env").write_text(
        "FRAPPE_OVERLAYS = erpnext,mcp\nFRAPPE_MCP_TOKEN_S_LOCALHOST=key:secret\nWEB_PORT = 8123\n"
    )
    assert _mcp_status(bench_root, "s.localhost") == {
        "server_name": "frappe-s",
        "url": "http://127.0.0.1:8123/api/method/frappe.mcp.handle_mcp",
        "token_env": "FRAPPE_MCP_TOKEN_S_LOCALHOST",
        "token": "key:secret",
        "site": "s.localhost",
    }


def test_site_creation_rejects_symlinked_sites_root(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    outside = tmp_path / "outside-sites"
    outside.mkdir()
    (bench_root / "sites").symlink_to(outside, target_is_directory=True)

    with patch("admin.backend.api.v1.sites.core.NewSiteTask.queue") as queue:
        create = client.post(
            "/api/v1/sites",
            json={"name": "s.localhost"},
        )

    assert create.status_code == 422
    queue.assert_not_called()


def test_framework_branch_release_keeps_branch_name() -> None:
    # 'version-16' must round-trip unchanged so the frontend regex renders 'Version 16'.
    assert _framework_branch([_frappe_app(branch="version-16", installed_version="16.0.0")]) == "version-16"


def test_framework_branch_develop_prefers_installed_version() -> None:
    assert _framework_branch([_frappe_app(branch="develop", installed_version="17.0.0.dev0")]) == "17.0.0.dev0"


def test_framework_branch_falls_back_to_branch_without_pip_metadata() -> None:
    assert _framework_branch([_frappe_app(branch="develop", installed_version="")]) == "develop"


def test_framework_branch_empty_when_frappe_absent() -> None:
    other = _frappe_app(name="erpnext", branch="develop", installed_version="14.0.0")
    assert _framework_branch([other]) == ""


def test_detail_populates_framework_branch_from_frappe_app(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    _write_site(bench_root, "s.localhost")
    frappe = _frappe_app(branch="develop", installed_version="17.0.0.dev0")

    with patch("admin.backend.api.v1.sites.core.AppProvider") as provider:
        provider.return_value.get_all.return_value = [frappe]
        body = client.get("/api/v1/sites/s.localhost").get_json()

    assert body["framework_branch"] == "17.0.0.dev0"
