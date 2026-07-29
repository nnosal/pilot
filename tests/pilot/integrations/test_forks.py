"""Tests for pilot.integrations.forks - fork detection and version translation."""

from pilot.integrations.forks import DOKOS, Fork, detect_fork


def test_detect_fork_identifies_dodock():
    assert detect_fork("https://gitlab.com/dokos/dodock") is DOKOS


def test_detect_fork_returns_none_for_upstream_frappe():
    assert detect_fork("https://github.com/frappe/frappe") is None


def test_detect_fork_returns_none_for_empty_repo():
    assert detect_fork("") is None


def test_to_upstream_version_translates_prerelease():
    assert DOKOS.to_upstream_version("6.0.0-dev") == "17.0.0.dev0"


def test_to_upstream_version_translates_stable_release():
    assert DOKOS.to_upstream_version("5.12.3") == "16.12.3"


def test_to_upstream_version_keeps_major_only_version():
    assert DOKOS.to_upstream_version("5") == "16"


def test_repo_for_returns_fork_repository():
    assert DOKOS.repo_for("erpnext") == "https://gitlab.com/dokos/dokos"


def test_repo_for_unknown_app_is_empty():
    assert DOKOS.repo_for("helpdesk") == ""


def test_branch_for_maps_upstream_major_to_fork_branch():
    assert DOKOS.branch_for(16) == "v5-fix"
    assert DOKOS.branch_for(17) == "develop"


def test_branch_for_unsupported_major_is_empty():
    assert DOKOS.branch_for(11) == ""


def test_fork_defaults_have_no_repos_or_branches():
    fork = Fork(
        name="x", title="X", framework_repo="acme/xdock", namespace="acme.com/x/", version_offset=1
    )
    assert fork.repos == {}
    assert fork.branches == {}


def test_publishes_matches_namespace():
    assert DOKOS.publishes("https://gitlab.com/dokos/bank") is True
    assert DOKOS.publishes("https://github.com/frappe/hrms") is False
    assert DOKOS.publishes("") is False


def test_app_name_strips_fork_suffix():
    assert Fork.app_name("erpnext(dokos)") == "erpnext"
    assert Fork.app_name("frappe(dodock)") == "frappe"
    assert Fork.app_name("bank_api") == "bank_api"
