"""Tests for GetAndInstallAppTask."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from pilot.core.site import Site
from pilot.tasks.get_and_install_app import GetAndInstallAppTask
from tests.pilot.commands.test_commands import make_bench


def make_task(bench_root: Path, sites: list[str]) -> GetAndInstallAppTask:
    bench = make_bench(bench_root)
    bench.create_directories()
    return GetAndInstallAppTask(
        bench=bench,
        bench_root=bench_root,
        repo="https://github.com/frappe/helpdesk",
        branch="",
        marketplace_app="",
        sites=sites,
    )


def test_site_alias_populates_sites_when_sites_is_omitted(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)

    task = GetAndInstallAppTask(
        bench=bench,
        bench_root=tmp_path,
        repo="https://github.com/frappe/helpdesk",
        site="site1.localhost",
    )

    assert task.sites == ["site1.localhost"]


def test_install_on_sites_only_installs_the_given_app(tmp_path: Path) -> None:
    task = make_task(tmp_path, ["site1.localhost"])
    app = MagicMock()
    app.config.name = "helpdesk"

    with patch.object(Site, "install_app") as mock_install:
        task.install_on_sites(app)

    mock_install.assert_called_once_with(app)


def test_build_assets_builds_for_app_and_every_dependency(tmp_path: Path) -> None:
    task = make_task(tmp_path, [])
    app = MagicMock()
    app.config.name = "helpdesk"
    dep = MagicMock()
    dep.config.name = "telephony"

    with patch("pilot.managers.environment.PythonEnvManager.build_assets_for_app") as mock_build:
        task.build_assets([app, dep])

    assert mock_build.call_args_list == [((app,),), ((dep,),)]


def test_run_installs_only_app_on_sites_but_builds_assets_for_dependencies_too(
    tmp_path: Path,
) -> None:
    """run() builds assets for dependencies cascaded by install-app."""
    task = make_task(tmp_path, ["site1.localhost"])

    fake_cmd = MagicMock()
    fake_cmd.app.config.name = "helpdesk"
    fake_cmd.installed_dependencies = [MagicMock()]

    with (
        patch.object(task, "fetch", return_value=fake_cmd),
        patch.object(task, "install_on_sites") as mock_install_on_sites,
        patch.object(task, "build_assets") as mock_build_assets,
        patch.object(task, "restart_workload") as mock_restart,
    ):
        task.run()

    mock_install_on_sites.assert_called_once_with(fake_cmd.app)
    mock_build_assets.assert_called_once_with([fake_cmd.app, *fake_cmd.installed_dependencies])
    mock_restart.assert_called_once_with()


def test_run_restarts_the_workload_after_building_assets(tmp_path: Path) -> None:
    """Processes started before the pip install can't import the new app - they must be
    recycled or every request fails with ModuleNotFoundError."""
    task = make_task(tmp_path, ["site1.localhost"])
    calls = []

    with (
        patch.object(task, "fetch", return_value=MagicMock(installed_dependencies=[])),
        patch.object(task, "install_on_sites", side_effect=lambda app: calls.append("install")),
        patch.object(task, "build_assets", side_effect=lambda apps: calls.append("build")),
        patch(
            "pilot.core.bench.settings.restart_running_workload",
            side_effect=lambda bench: calls.append("restart") or True,
        ),
    ):
        task.run()

    assert calls == ["install", "build", "restart"]


def test_restart_workload_reports_when_nothing_is_running(tmp_path: Path) -> None:
    task = make_task(tmp_path, [])

    with (
        patch("pilot.core.bench.settings.restart_running_workload", return_value=False),
        patch.object(task, "report") as mock_report,
    ):
        task.restart_workload()

    assert "restart yours" in mock_report.call_args[0][0]
