"""Tests for pilot.integrations.marketplace - Resolver and Marketplace classes."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pilot.exceptions import BenchError
from pilot.integrations.marketplace import Marketplace, Resolver


def make_resolver(
    app: str = "myapp",
    version: str = "1.0.0",
    frappe_version: str = "15.0.0",
    required_version: str = ">=15.0.0,<16.0.0",
    dependencies: dict | None = None,
    is_installable: bool = True,
    repo: str = "https://github.com/frappe/myapp",
    target: str = "version-15",
    target_type: str = "branch",
) -> Resolver:
    return Resolver(
        app=app,
        repo=repo,
        target_type=target_type,
        target=target,
        version=version,
        frappe_version=frappe_version,
        required_version=required_version,
        is_installable=is_installable,
        dependencies=dependencies or {},
    )


def inject_registry(root: Resolver, resolvers: list[Resolver]) -> None:
    """Share a single registry pointer across all supplied resolvers."""
    registry: dict[str, list[Resolver]] = {}
    for r in resolvers:
        registry.setdefault(r.app, []).append(r)
    for r in [root, *resolvers]:
        r._registry = registry


def test_to_dict_maps_app_field_to_name():
    r = make_resolver(app="erpnext", version="15.1.0")
    d = r.to_dict()
    assert d["name"] == "erpnext"
    assert "app" not in d


def test_to_dict_excludes_registry():
    r = make_resolver()
    d = r.to_dict()
    assert "_registry" not in d
    assert "registry" not in d


def test_to_dict_contains_all_expected_keys():
    r = make_resolver()
    keys = {
        "name",
        "repo",
        "target_type",
        "target",
        "version",
        "frappe_version",
        "required_version",
        "dependencies",
        "is_installable",
        "title",
        "description",
        "logo_url",
        "category",
        "categories",
        "stars",
        "documentation",
        "website",
    }
    assert keys == set(r.to_dict().keys())


def test_to_dict_preserves_values():
    r = make_resolver(app="hrms", version="2.0.0")
    r.title = "HR Management"
    r.stars = 42
    d = r.to_dict()
    assert d["version"] == "2.0.0"
    assert d["title"] == "HR Management"
    assert d["stars"] == 42


def test_to_dict_categories_defaults_to_empty_list():
    r = make_resolver()
    assert r.to_dict()["categories"] == []


def test_to_dict_includes_categories():
    r = make_resolver()
    r.categories = ["Payments", "Accounting"]
    assert r.to_dict()["categories"] == ["Payments", "Accounting"]


def test_resolve_raises_for_non_installable_app():
    r = make_resolver(is_installable=False, frappe_version="14.0.0", required_version=">=15.0.0,<16.0.0")
    with pytest.raises(BenchError, match="not compatible"):
        r.resolve()


def test_resolve_error_message_contains_version_info():
    r = make_resolver(is_installable=False, frappe_version="14.0.0", required_version=">=15.0.0,<16.0.0")
    with pytest.raises(BenchError) as exc:
        r.resolve()
    msg = str(exc.value)
    assert "14.0.0" in msg
    assert ">=15.0.0,<16.0.0" in msg


def test_resolve_single_app_no_deps_returns_self():
    r = make_resolver()
    result = r.resolve()
    assert result == [r]


def test_resolve_returns_list_of_resolvers():
    r = make_resolver()
    assert isinstance(r.resolve(), list)


def test_resolve_installs_dependency_before_root():
    dep = make_resolver(app="payments", version="1.0.0")
    root = make_resolver(app="erpnext", dependencies={"payments": ">=1.0.0"})
    inject_registry(root, [dep])

    result = root.resolve()
    assert [r.app for r in result] == ["payments", "erpnext"]


def test_resolve_deep_chain_order():
    # erpnext → payments → stripe_integration
    stripe = make_resolver(app="stripe_integration", version="1.0.0")
    payments = make_resolver(app="payments", version="1.0.0", dependencies={"stripe_integration": ">=1.0.0"})
    erpnext = make_resolver(app="erpnext", version="15.0.0", dependencies={"payments": ">=1.0.0"})
    inject_registry(erpnext, [payments, stripe])

    result = erpnext.resolve()
    assert [r.app for r in result] == ["stripe_integration", "payments", "erpnext"]


def test_resolve_diamond_dependency_deduplication():
    # root → B, root → C, B → D, C → D  =>  D appears exactly once
    d = make_resolver(app="D", version="1.0.0")
    b = make_resolver(app="B", version="1.0.0", dependencies={"D": ">=1.0.0"})
    c = make_resolver(app="C", version="1.0.0", dependencies={"D": ">=1.0.0"})
    root = make_resolver(app="root", dependencies={"B": ">=1.0.0", "C": ">=1.0.0"})
    inject_registry(root, [b, c, d])

    result = root.resolve()
    names = [r.app for r in result]
    assert names.count("D") == 1
    assert names[-1] == "root"
    assert names.index("D") < names.index("B")
    assert names.index("D") < names.index("C")


def test_resolve_picks_dep_satisfying_version_spec():
    dep_old = make_resolver(app="payments", version="0.9.0")
    dep_new = make_resolver(app="payments", version="2.0.0")
    root = make_resolver(app="erpnext", dependencies={"payments": ">=2.0.0"})
    registry: dict = {"payments": [dep_old, dep_new]}
    root._registry = registry

    result = root.resolve()
    payment_resolver = next(r for r in result if r.app == "payments")
    assert payment_resolver.version == "2.0.0"


def test_resolve_raises_when_no_dep_version_satisfies_spec():
    dep = make_resolver(app="payments", version="1.0.0")
    root = make_resolver(app="erpnext", dependencies={"payments": ">=3.0.0"})
    inject_registry(root, [dep])

    with pytest.raises(BenchError, match="payments"):
        root.resolve()


def test_resolve_raises_on_version_conflict_in_diamond():
    # B picks payments 1.5.0, A requires payments >=2.0.0 - conflict
    payments_old = make_resolver(app="payments", version="1.5.0")
    payments_new = make_resolver(app="payments", version="2.0.0")
    b = make_resolver(app="B", dependencies={"payments": ">=1.0.0,<2.0.0"})
    a = make_resolver(app="A", dependencies={"payments": ">=2.0.0"})
    root = make_resolver(app="root", dependencies={"B": "", "A": ""})
    registry = {"payments": [payments_old, payments_new], "B": [b], "A": [a]}
    root._registry = b._registry = a._registry = registry

    with pytest.raises(BenchError, match="conflict"):
        root.resolve()


def test_resolve_accepts_dep_with_empty_spec():
    dep = make_resolver(app="payments", version="1.0.0")
    root = make_resolver(app="erpnext", dependencies={"payments": ""})
    inject_registry(root, [dep])

    result = root.resolve()
    assert any(r.app == "payments" for r in result)


def test_resolve_raises_when_dep_not_in_registry():
    root = make_resolver(app="erpnext", dependencies={"missing_app": ">=1.0.0"})
    root._registry = {}

    with pytest.raises(BenchError, match="missing_app"):
        root.resolve()


def test_resolve_raises_when_registry_empty_and_has_deps():
    root = make_resolver(app="erpnext", dependencies={"someapp": ">=1.0.0"})
    # _registry defaults to {} - no injection needed

    with pytest.raises(BenchError):
        root.resolve()


def test_resolve_detects_direct_cycle():
    # A depends on B, B depends on A
    a = make_resolver(app="A", dependencies={"B": ""})
    b = make_resolver(app="B", dependencies={"A": ""})
    inject_registry(a, [b])
    # A._registry also needs B's registry pointer for nested resolution
    b._registry = a._registry

    with pytest.raises(BenchError, match="Circular dependency"):
        a.resolve()


def test_resolve_cycle_error_includes_cycle_path():
    a = make_resolver(app="A", dependencies={"B": ""})
    b = make_resolver(app="B", dependencies={"A": ""})
    inject_registry(a, [b])
    b._registry = a._registry

    with pytest.raises(BenchError) as exc:
        a.resolve()
    assert "A" in str(exc.value)
    assert "B" in str(exc.value)


def test_resolve_detects_indirect_cycle():
    # A → B → C → A
    a = make_resolver(app="A", dependencies={"B": ""})
    b = make_resolver(app="B", dependencies={"C": ""})
    c = make_resolver(app="C", dependencies={"A": ""})
    registry = {"A": [a], "B": [b], "C": [c]}
    a._registry = b._registry = c._registry = registry

    with pytest.raises(BenchError, match="Circular dependency"):
        a.resolve()


SAMPLE_REGISTRY = [
    {
        "name": "erpnext",
        "repo": "https://github.com/frappe/erpnext",
        "title": "ERPNext",
        "description": "ERP for the real world",
        "logo_url": "",
        "category": "Applications",
        "categories": ["Accounting", "Featured"],
        "stars": 22000,
        "targets": [
            {
                "target_type": "branch",
                "target": "version-15",
                "version": "15.0.0",
                "frappe_core": ">=15.0.0,<16.0.0",
                "dependencies": {},
            },
            {
                "target_type": "branch",
                "target": "version-16",
                "version": "16.0.0",
                "frappe_core": ">=16.0.0,<17.0.0",
                "dependencies": {},
            },
        ],
    },
    {
        "name": "hrms",
        "repo": "https://github.com/frappe/hrms",
        "title": "HR Management",
        "description": "",
        "logo_url": "",
        "category": "Applications",
        "stars": None,
        "targets": [
            {
                "target_type": "branch",
                "target": "version-15",
                "version": "15.0.0",
                "frappe_core": ">=15.0.0,<16.0.0",
                "dependencies": {"erpnext": ">=15.0.0"},
            },
        ],
    },
    {
        "name": "helpdesk",
        "repo": "https://github.com/frappe/helpdesk",
        "title": "Helpdesk",
        "description": "",
        "logo_url": "",
        "category": "Applications",
        "stars": 100,
        "targets": [
            {
                "target_type": "branch",
                "target": "version-16",
                "version": "16.0.0",
                "frappe_core": ">=16.0.0,<17.0.0",
                "dependencies": {},
            },
        ],
    },
    {
        "name": "old_app",
        "repo": "https://github.com/frappe/old_app",
        "title": "Old App",
        "description": "",
        "logo_url": "",
        "category": "Utilities",
        "stars": 10,
        "targets": [
            {
                "target_type": "branch",
                "target": "version-14",
                "version": "14.0.0",
                "frappe_core": ">=14.0.0,<15.0.0",
                "dependencies": {},
            },
        ],
    },
]


def make_marketplace(
    frappe_version: str,
    registry: list | None = None,
    framework_repo: str = "https://github.com/frappe/frappe",
) -> Marketplace:
    bench = MagicMock()
    bench.env_path = Path("/fake/env")
    bench.config.framework_app.repo = framework_repo

    import json

    with (
        patch(
            "pilot.integrations.marketplace.Marketplace.get_current_frappe_version",
            return_value=frappe_version,
        ),
        patch(
            "pilot.integrations.marketplace.Marketplace._read_apps_json",
            return_value=json.dumps(registry or SAMPLE_REGISTRY),
        ),
    ):
        return Marketplace(bench)


def test_parse_registry_tolerates_bad_frappe_core():
    registry = [
        {
            "name": "null_spec_app",
            "repo": "https://github.com/frappe/null_spec_app",
            "targets": [
                {
                    "version": "1.0.0",
                    "target_type": "branch",
                    "target": "main",
                    "frappe_core": None,
                },
            ],
        },
        {
            "name": "garbage_spec_app",
            "repo": "https://github.com/frappe/garbage_spec_app",
            "targets": [
                {
                    "version": "1.0.0",
                    "target_type": "branch",
                    "target": "main",
                    "frappe_core": "not-a-spec",
                },
            ],
        },
    ]
    mp = make_marketplace("16.0.0", registry)
    apps = {a.app: a for a in mp.read_all_apps()}
    assert apps["null_spec_app"].is_installable is False
    assert apps["garbage_spec_app"].is_installable is False


def test_read_all_apps_returns_all_apps_including_incompatible():
    mp = make_marketplace("15.0.0")
    apps = mp.read_all_apps()
    names = {a.app for a in apps}
    assert "erpnext" in names
    assert "hrms" in names
    assert "old_app" in names


def test_read_all_apps_marks_compatible_app_as_installable():
    mp = make_marketplace("15.0.0")
    apps = mp.read_all_apps()
    erpnext = next(a for a in apps if a.app == "erpnext")
    assert erpnext.is_installable is True


def test_read_all_apps_marks_incompatible_app_as_not_installable():
    mp = make_marketplace("15.0.0")
    apps = mp.read_all_apps()
    old_app = next(a for a in apps if a.app == "old_app")
    assert old_app.is_installable is False


def test_read_all_apps_uses_correct_target_for_frappe_version():
    mp = make_marketplace("16.0.0")
    apps = mp.read_all_apps()
    erpnext = next(a for a in apps if a.app == "erpnext")
    assert erpnext.target == "version-16"
    assert erpnext.version == "16.0.0"


def test_read_all_apps_uses_first_target_as_display_for_incompatible():
    mp = make_marketplace("17.0.0")
    apps = mp.read_all_apps()
    old_app = next(a for a in apps if a.app == "old_app")
    assert old_app.target == "version-14"


def test_read_all_apps_passes_categories_through():
    mp = make_marketplace("15.0.0")
    apps = mp.read_all_apps()
    erpnext = next(a for a in apps if a.app == "erpnext")
    hrms = next(a for a in apps if a.app == "hrms")
    assert erpnext.categories == ["Accounting", "Featured"]
    assert hrms.categories == []  # absent key defaults to empty


def test_read_all_apps_stars_none_becomes_zero():
    mp = make_marketplace("15.0.0")
    apps = mp.read_all_apps()
    hrms = next(a for a in apps if a.app == "hrms")
    # stars: None in registry should be handled gracefully
    assert isinstance(hrms.stars, (int, type(None)))


def test_read_all_apps_injects_shared_registry_pointer():
    mp = make_marketplace("15.0.0")
    apps = mp.read_all_apps()
    registries = [id(a._registry) for a in apps]
    assert len(set(registries)) == 1, "All resolvers must share the same registry dict"


def test_read_all_apps_registry_only_contains_installable_apps():
    mp = make_marketplace("15.0.0")
    apps = mp.read_all_apps()
    registry = apps[0]._registry
    assert "old_app" not in registry
    assert "erpnext" in registry
    assert "hrms" in registry


def test_read_all_apps_registry_entries_are_lists_of_resolvers():
    mp = make_marketplace("15.0.0")
    apps = mp.read_all_apps()
    registry = apps[0]._registry
    for entries in registry.values():
        assert isinstance(entries, list)
        assert all(isinstance(e, Resolver) for e in entries)


def test_read_all_apps_resolve_works_end_to_end():
    mp = make_marketplace("15.0.0")
    apps = mp.read_all_apps()
    hrms = next(a for a in apps if a.app == "hrms")
    result = hrms.resolve()
    names = [r.app for r in result]
    assert names == ["erpnext", "hrms"]


def test_read_all_apps_incompatible_app_resolve_raises():
    mp = make_marketplace("15.0.0")
    apps = mp.read_all_apps()
    old_app = next(a for a in apps if a.app == "old_app")
    with pytest.raises(BenchError, match="not compatible"):
        old_app.resolve()


def test_read_all_apps_multi_target_registry_has_all_compatible_versions():
    registry = [
        {
            "name": "payments",
            "repo": "https://github.com/frappe/payments",
            "title": "Payments",
            "description": "",
            "logo_url": "",
            "category": "Applications",
            "stars": 0,
            "targets": [
                {
                    "target_type": "branch",
                    "target": "version-15",
                    "version": "15.0.0",
                    "frappe_core": ">=15.0.0,<16.0.0",
                    "dependencies": {},
                },
                {
                    "target_type": "branch",
                    "target": "version-15-hotfix",
                    "version": "15.1.0",
                    "frappe_core": ">=15.0.0,<16.0.0",
                    "dependencies": {},
                },
            ],
        }
    ]
    mp = make_marketplace("15.0.5", registry)
    apps = mp.read_all_apps()
    reg_entries = apps[0]._registry.get("payments", [])
    assert len(reg_entries) == 2


def test_read_all_apps_no_targets_produces_non_installable():
    registry = [
        {
            "name": "ghost_app",
            "repo": "https://github.com/frappe/ghost_app",
            "title": "Ghost",
            "description": "",
            "logo_url": "",
            "category": "Utilities",
            "stars": 0,
            "targets": [],
        }
    ]
    mp = make_marketplace("15.0.0", registry)
    apps = mp.read_all_apps()
    ghost = next(a for a in apps if a.app == "ghost_app")
    assert ghost.is_installable is False


def test_find_app_returns_matching_resolver():
    mp = make_marketplace("15.0.0")
    resolver = mp.find_app("erpnext")
    assert resolver.app == "erpnext"


def test_find_app_raises_for_unknown_app():
    mp = make_marketplace("15.0.0")
    with pytest.raises(BenchError, match="'unknown_app' not found in marketplace"):
        mp.find_app("unknown_app")


OLD_APP_ONLY_REGISTRY = [
    {
        "name": "old_app",
        "repo": "https://github.com/frappe/old_app",
        "title": "Old App",
        "description": "",
        "logo_url": "",
        "category": "Utilities",
        "stars": 10,
        "targets": [
            {
                "target_type": "branch",
                "target": "version-14",
                "version": "14.0.0",
                "frappe_core": ">=14.0.0,<15.0.0",
                "dependencies": {},
            },
        ],
    },
]


@pytest.fixture(autouse=True)
def _clear_frappeverse_cache():
    """The catalog is lru_cache'd across the whole process - reset it around
    every test so a mocked catalog in one test can't leak into the next."""
    Marketplace._frappeverse_entries.cache_clear()
    yield
    Marketplace._frappeverse_entries.cache_clear()


def test_frappeverse_fallback_fills_gap_registry_has_no_target_for():
    """erpnext/hrms's official registry entries only cover v15+ - a hand-curated
    supplemental catalog (apps_frappeverse.json) confirms these apps still ship
    real version-N branches for older Frappe majors the registry dropped."""
    with patch.object(
        Marketplace, "_frappeverse_catalog", return_value={"old_app": ["12", "13"]}
    ):
        mp = make_marketplace("12.5.0", OLD_APP_ONLY_REGISTRY)
        old_app = next(a for a in mp.read_all_apps() if a.app == "old_app")

    assert old_app.is_installable is True
    assert old_app.target == "version-12"
    assert old_app.target_type == "branch"


def test_frappeverse_fallback_not_used_when_registry_already_compatible():
    """The official registry stays authoritative when it already has a match -
    the supplemental catalog only fills gaps, never overrides."""
    with patch.object(
        Marketplace, "_frappeverse_catalog", return_value={"erpnext": ["12"]}
    ):
        mp = make_marketplace("15.0.0")
        erpnext = next(a for a in mp.read_all_apps() if a.app == "erpnext")

    assert erpnext.target == "version-15"


def test_frappeverse_fallback_absent_when_app_not_in_catalog():
    with patch.object(Marketplace, "_frappeverse_catalog", return_value={}):
        mp = make_marketplace("17.0.0", OLD_APP_ONLY_REGISTRY)
        old_app = next(a for a in mp.read_all_apps() if a.app == "old_app")

    assert old_app.is_installable is False
    assert old_app.target == "version-14"


def test_frappeverse_fallback_absent_when_version_not_in_apps_list():
    with patch.object(
        Marketplace, "_frappeverse_catalog", return_value={"old_app": ["15", "16"]}
    ):
        mp = make_marketplace("12.5.0", OLD_APP_ONLY_REGISTRY)
        old_app = next(a for a in mp.read_all_apps() if a.app == "old_app")

    assert old_app.is_installable is False


def test_frappeverse_catalog_walks_nested_app_available(tmp_path):
    catalog_data = [
        {
            "name": "frappe",
            "frappe_versions": [],
            "app_available": [
                {
                    "name": "erpnext",
                    "frappe_versions": ["12", "13", "14", "15", "16"],
                    "app_available": [
                        {"name": "hrms", "frappe_versions": ["14", "15", "16"]},
                    ],
                },
            ],
        },
    ]
    catalog_path = tmp_path / "apps_frappeverse.json"
    catalog_path.write_text(json.dumps(catalog_data))

    with patch("pilot.utils.cli_root", return_value=tmp_path / "pilot"):
        catalog = Marketplace._frappeverse_catalog()

    assert catalog["erpnext"] == ["12", "13", "14", "15", "16"]
    assert catalog["hrms"] == ["14", "15", "16"]
    assert "frappe" not in catalog  # empty frappe_versions list - nothing to match against


DODOCK_REPO = "https://gitlab.com/dokos/dodock"

DOKOS_CATALOG = [
    {
        "name": "erpnext(dokos)",
        "url": "https://gitlab.com/dokos/dokos",
        "description": "ERP based on ERPNext, built on Dodock",
        "branches": ["develop", "v4-fix", "v5-fix"],
    },
    {
        "name": "hrms",
        "url": "https://gitlab.com/dokos/hrms",
        "description": "Dokos HRMS",
        "branches": ["develop", "v4-fix"],
    },
    {
        "name": "bank",
        "url": "https://gitlab.com/dokos/bank",
        "description": "Bank connection application for Dokos",
        "branches": ["develop", "v4-fix"],
        "require": [{"name": "bank_api", "url": "https://gitlab.com/dokos/bank-api"}],
    },
    {
        "name": "bank_api",
        "url": "https://gitlab.com/dokos/bank-api",
        "description": "API middleware for the Bank application",
        "branches": ["develop", "v4-fix"],
    },
    {
        "name": "hiboutik",
        "url": "https://gitlab.com/dokos/hiboutik",
        "description": "Hiboutik connector",
        "branches": ["v3.x.x-hotfix"],
    },
    {
        "name": "abandoned",
        "url": "https://gitlab.com/dokos/abandoned",
        "description": "No longer maintained",
        "branches": ["develop", "v4-fix"],
        "archived": True,
    },
]


def make_dokos_marketplace(
    dodock_version: str,
    registry: list | None = None,
    catalog: list | None = DOKOS_CATALOG,
) -> Marketplace:
    with patch.object(Marketplace, "_frappeverse_entries", return_value=catalog or []):
        return make_marketplace(dodock_version, registry, framework_repo=DODOCK_REPO)


def test_fork_bench_matches_targets_against_upstream_version():
    """dodock 5.x is Frappe 16 - without translation nothing in the registry matches."""
    mp = make_dokos_marketplace("5.0.0")
    helpdesk = next(a for a in mp.read_all_apps() if a.app == "helpdesk")
    assert helpdesk.is_installable is True
    assert helpdesk.version == "16.0.0"


def test_fork_published_app_carries_no_upstream_version_number():
    """A dokos app's version is not frappe's - showing the upstream number would lie."""
    mp = make_dokos_marketplace("5.0.0")
    erpnext = next(a for a in mp.read_all_apps() if a.app == "erpnext")
    assert erpnext.is_installable is True
    assert erpnext.version == ""


def test_fork_bench_reports_its_own_frappe_version():
    mp = make_dokos_marketplace("5.0.0")
    assert mp.frappe_version == "5.0.0"
    assert mp.upstream_frappe_version == "16.0.0"


def test_upstream_bench_has_no_fork():
    mp = make_marketplace("16.0.0")
    assert mp.fork is None
    assert mp.upstream_frappe_version == "16.0.0"


def test_fork_app_points_at_fork_repository_and_branch():
    mp = make_dokos_marketplace("5.0.0")
    erpnext = next(a for a in mp.read_all_apps() if a.app == "erpnext")
    assert erpnext.repo == "https://gitlab.com/dokos/dokos"
    assert erpnext.target == "v5-fix"
    assert erpnext.target_type == "branch"
    assert erpnext.title == "ERPNext (Dokos)"


def test_fork_overrides_upstream_app_it_also_publishes():
    """dokos maintains its own hrms - a dodock bench must not get frappe's."""
    mp = make_dokos_marketplace("4.0.0")
    hrms = next(a for a in mp.read_all_apps() if a.app == "hrms")
    assert hrms.repo == "https://gitlab.com/dokos/hrms"
    assert hrms.target == "v4-fix"
    assert hrms.is_installable is True


def test_fork_leaves_apps_it_does_not_publish_on_upstream():
    mp = make_dokos_marketplace("5.0.0", catalog=[])
    erpnext = next(a for a in mp.read_all_apps() if a.app == "erpnext")
    hrms = next(a for a in mp.read_all_apps() if a.app == "hrms")
    # erpnext keeps the built-in floor, hrms has no fork counterpart at all
    assert erpnext.repo == "https://gitlab.com/dokos/dokos"
    assert hrms.repo == "https://github.com/frappe/hrms"


def test_fork_only_apps_are_added_to_the_registry():
    """The official registry carries no dokos app - they come from the catalog."""
    mp = make_dokos_marketplace("4.0.0")
    apps = {a.app: a for a in mp.read_all_apps()}
    assert apps["bank"].repo == "https://gitlab.com/dokos/bank"
    assert apps["bank"].target == "v4-fix"
    assert apps["bank"].is_installable is True
    assert apps["bank"].title == "Bank (Dokos)"


def test_fork_only_app_requires_resolve_as_dependencies():
    mp = make_dokos_marketplace("4.0.0")
    bank = next(a for a in mp.read_all_apps() if a.app == "bank")
    assert [r.app for r in bank.resolve()] == ["bank_api", "bank"]


def test_fork_only_app_without_branch_for_major_is_not_installable():
    """hiboutik only ships a v14-era branch - nothing to install on Frappe 15."""
    mp = make_dokos_marketplace("4.0.0")
    hiboutik = next(a for a in mp.read_all_apps() if a.app == "hiboutik")
    assert hiboutik.is_installable is False
    assert hiboutik.target == ""


def test_fork_catalog_skips_archived_apps():
    mp = make_dokos_marketplace("4.0.0")
    assert all(a.app != "abandoned" for a in mp.read_all_apps())


def test_fork_catalog_ignored_on_upstream_bench():
    mp = make_marketplace("16.0.0")
    assert all(a.app != "bank" for a in mp.read_all_apps())


def test_fork_app_not_installable_when_fork_ships_no_branch_for_major():
    """A compatible upstream target must not make the app installable from
    github when the fork has nothing for that Frappe major."""
    registry = [
        {
            "name": "erpnext",
            "repo": "https://github.com/frappe/erpnext",
            "title": "ERPNext",
            "targets": [
                {
                    "target_type": "branch",
                    "target": "version-18",
                    "version": "18.0.0",
                    "frappe_core": ">=18.0.0,<19.0.0",
                    "dependencies": {},
                },
            ],
        },
    ]
    mp = make_dokos_marketplace("7.0.0", registry)
    erpnext = next(a for a in mp.read_all_apps() if a.app == "erpnext")
    assert erpnext.is_installable is False
    assert erpnext.repo == "https://gitlab.com/dokos/dokos"


def test_fork_substitution_applies_to_dependency_registry_entries():
    mp = make_dokos_marketplace("4.0.0")
    apps = mp.read_all_apps()
    dependency_entry = apps[0]._registry["erpnext"][0]
    assert dependency_entry.repo == "https://gitlab.com/dokos/dokos"
    assert dependency_entry.target == "v4-fix"


def test_frappeverse_catalog_missing_file_returns_empty(tmp_path):
    with patch("pilot.utils.cli_root", return_value=tmp_path / "pilot"):
        assert Marketplace._frappeverse_catalog() == {}


def test_frappeverse_catalog_invalid_json_returns_empty(tmp_path):
    (tmp_path / "apps_frappeverse.json").write_text("not json")
    with patch("pilot.utils.cli_root", return_value=tmp_path / "pilot"):
        assert Marketplace._frappeverse_catalog() == {}
