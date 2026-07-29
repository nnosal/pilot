"""Resolve installable apps and their dependency versions against the bench's current Frappe version."""

import json
import typing
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Literal

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import Version

from pilot.exceptions import AppNotFoundError, DependencyResolutionError
from pilot.integrations.forks import Fork, detect_fork
from pilot.utils import run_command

if typing.TYPE_CHECKING:
    from pilot.core.bench import Bench


@dataclass
class Resolver:
    app: str
    repo: str
    target_type: Literal["tag", "branch", "target"]
    target: str
    version: str
    frappe_version: str
    required_version: str
    is_installable: bool
    dependencies: dict[str, str] = field(default_factory=dict)
    title: str = ""
    description: str = ""
    logo_url: str = ""
    category: str = ""
    categories: list[str] = field(default_factory=list)
    stars: int | None = 0
    documentation: str = ""
    website: str = ""
    _registry: dict[str, list["Resolver"]] = field(default_factory=dict, init=False, repr=False)

    def to_dict(self) -> dict:
        return {
            "name": self.app,
            "repo": self.repo,
            "target_type": self.target_type,
            "target": self.target,
            "version": self.version,
            "frappe_version": self.frappe_version,
            "required_version": self.required_version,
            "dependencies": self.dependencies,
            "is_installable": self.is_installable,
            "title": self.title,
            "description": self.description,
            "logo_url": self.logo_url,
            "category": self.category,
            "categories": self.categories,
            "stars": self.stars,
            "documentation": self.documentation,
            "website": self.website,
        }

    def _resolve(
        self,
        app: str,
        required_spec: str,
        visited: dict[str, str],
        path: list[str],
        result: list["Resolver"],
    ):
        if app in path:
            cycle = " -> ".join([*path[path.index(app) :], app])
            raise DependencyResolutionError(f"Circular dependency detected: {cycle}")
        if app in visited:
            if required_spec and Version(visited[app]) not in SpecifierSet(required_spec):
                raise DependencyResolutionError(
                    f"Version conflict: '{app}' {visited[app]!r} already selected "
                    f"but {required_spec!r} is required by '{path[-1]}'."
                )
            return

        path.append(app)
        candidate_resolvers = self._registry.get(app, [])
        spec = SpecifierSet(required_spec) if required_spec else None
        resolver = next(
            (r for r in candidate_resolvers if spec is None or Version(r.version) in spec),
            None,
        )
        if not resolver:
            raise DependencyResolutionError(
                f"Dependency '{app}' has no version satisfying {required_spec!r} "
                f"compatible with Frappe {self.frappe_version}.\n"
                f"Needed by '{path[-2]}' in the marketplace registry."
            )

        for dep, dep_spec in resolver.dependencies.items():
            self._resolve(dep, dep_spec, visited, path, result)
        result.append(resolver)

        visited[app] = resolver.version
        path.pop()

    def resolve(self) -> list["Resolver"]:
        """Returns dependencies in install order (deepest first, self last)."""
        if not self.is_installable:
            raise DependencyResolutionError(
                f"'{self.app}' is not compatible with the current Frappe version.\nRequired: {self.required_version} Current: {self.frappe_version}"
            )
        result: list["Resolver"] = []
        visited: dict[str, str] = {}
        for dep, spec in self.dependencies.items():
            self._resolve(dep, spec, visited, [self.app], result)
        result.append(self)
        return result


@dataclass
class Marketplace:
    bench: "Bench"
    frappe_version: str = field(default="", init=False)
    fork: Fork | None = field(default=None, init=False)

    def __post_init__(self):
        self.frappe_version = self.get_current_frappe_version()
        self.fork = detect_fork(self.bench.config.framework_app.repo)
        self._fork_catalog = self._read_fork_catalog()
        # Snapshot at construction so callers see a consistent registry for this instance.
        registry = json.loads(self._read_apps_json())
        registry += self._fork_only_entries({app["name"] for app in registry})
        self._registry = self._parse_registry(registry)

    @property
    def upstream_frappe_version(self) -> str:
        """The version registry `frappe_core` specs are written against - the bench's
        own version unless it runs a fork with its own numbering."""
        if not self.fork:
            return self.frappe_version
        return self.fork.to_upstream_version(self.frappe_version)

    @staticmethod
    def _read_apps_json() -> str:
        from pilot.core.registry_cache import RegistryCache
        from pilot.utils import cli_root

        cache = RegistryCache(cli_root())
        cache.ensure_fresh()
        return cache.apps_json_path.read_text()

    def get_current_frappe_version(self) -> str:
        cmd = [
            str(self.bench.env_path / "bin" / "python"),
            "-c",
            "import frappe; print(frappe.__version__)",
        ]
        result = run_command(cmd)
        return result.stdout.strip().decode()

    @staticmethod
    @lru_cache(maxsize=1)
    def registry() -> list[dict]:
        """Parsed registry for callers that don't have a Marketplace/bench (e.g. tasks). Cached once."""
        return Marketplace._parse_registry(json.loads(Marketplace._read_apps_json()))

    @staticmethod
    def _parse_registry(raw: list[dict]) -> list[dict]:
        for app in raw:
            for target in app.get("targets") or []:
                target["_spec"] = Marketplace._safe_spec(target.get("frappe_core"))
        return raw

    @staticmethod
    def _safe_spec(frappe_core: str | None) -> SpecifierSet | None:
        """None means unparseable - excluded from compatibility matching."""
        try:
            return SpecifierSet(frappe_core or "", prereleases=True)
        except InvalidSpecifier:
            return None

    @staticmethod
    @lru_cache(maxsize=1)
    def _frappeverse_entries() -> list[dict]:
        """Every app entry of the hand-curated sibling catalog (../apps_frappeverse.json,
        next to this checkout's own root - this pilot is meant to run from a mef-style
        .config/pilot layout), flattened out of its nesting.

        The official marketplace registry (registry-cache/apps.json, a shallow
        clone of github.com/frappe/marketplace) stopped carrying targets for
        Frappe 12-14, and never carried fork ecosystems (dokos & co) at all.
        Any read/parse failure here just means no supplemental data - this file
        won't exist for anyone running pilot outside this repo, and that's not
        an error.
        """
        from pilot.utils import cli_root

        entries: list[dict] = []
        try:
            raw = json.loads((cli_root().parent / "apps_frappeverse.json").read_text())
        except (OSError, json.JSONDecodeError):
            return entries

        def walk(nested: object) -> None:
            if not isinstance(nested, list):
                return
            for entry in nested:
                if not isinstance(entry, dict):
                    continue
                if entry.get("name"):
                    entries.append(entry)
                walk(entry.get("app_available"))
                walk(entry.get("other_app_available"))
                walk(entry.get("require"))

        walk(raw)
        return entries

    @classmethod
    def _frappeverse_catalog(cls) -> dict[str, list[str]]:
        """Best-effort app -> supported major Frappe versions, for majors the
        official registry dropped: erpnext/hrms still ship real version-12..
        version-16 branches upstream, the registry just no longer lists them."""
        return {
            entry["name"]: [str(v) for v in entry["frappe_versions"]]
            for entry in cls._frappeverse_entries()
            if entry.get("frappe_versions")
        }

    @classmethod
    def _frappeverse_fallback_target(cls, app_name: str, current_frappe: Version) -> dict | None:
        entry = next((e for e in cls._frappeverse_entries() if e.get("name") == app_name), None)
        if entry is None:
            return None
        versions = [str(v) for v in (entry.get("frappe_versions") or [])]
        major = current_frappe.major
        if not versions or str(major) not in versions:
            return None
        # Only pick the version-N branch when the app actually ships it; some
        # apps are single-branch and vN-compatible via their default branch
        # (e.g. frappe-better-list-view has only "main" but targets v12).
        version_branch = f"version-{major}"
        branches = entry.get("branches") or []
        target = (
            version_branch
            if version_branch in branches
            else (entry.get("default_branch") or entry.get("branch") or "")
        )
        return {
            "version": str(major),
            "target_type": "branch",
            "target": target,
            "frappe_core": f">={major}.0.0,<{major + 1}.0.0",
            "dependencies": {},
        }

    def _read_fork_catalog(self) -> dict[str, dict]:
        """Apps the running fork publishes, keyed by the name they install under.
        The official registry carries no fork ecosystem, so the supplemental
        catalog is the only source for them."""
        if not self.fork:
            return {}
        catalog: dict[str, dict] = {}
        for entry in self._frappeverse_entries():
            if not self.fork.publishes(entry.get("url", "")) or entry.get("archived"):
                continue
            name = self.fork.app_name(entry["name"])
            if name and name not in ("frappe", self.fork.cli_app):
                catalog[name] = entry
        return catalog

    def _fork_branch(self, entry: dict | None) -> str:
        """The fork branch for this bench's Frappe major, empty when the fork
        ships nothing for it."""
        if not self.fork:
            return ""
        branch = self.fork.branch_for(Version(self.upstream_frappe_version).major)
        if entry and branch not in (entry.get("branches") or []):
            return ""
        return branch

    def _fork_target(self, entry: dict) -> dict:
        major = Version(self.upstream_frappe_version).major
        requires = entry.get("require")
        dependencies = (
            {Fork.app_name(r["name"]): "" for r in requires if isinstance(r, dict) and r.get("name")}
            if isinstance(requires, list)
            else {}
        )
        frappe_core = f">={major}.0.0.dev0,<{major + 1}.0.0"
        return {
            "target_type": "branch",
            "target": self._fork_branch(entry),
            "version": "",
            "frappe_core": frappe_core,
            "dependencies": dependencies,
            "_spec": self._safe_spec(frappe_core),
        }

    def _fork_only_entries(self, upstream_names: set[str]) -> list[dict]:
        """Registry entries for fork apps that have no upstream counterpart."""
        return [
            {
                "name": name,
                "repo": entry["url"],
                "title": name.replace("_", " ").replace("-", " ").title(),
                "description": entry.get("description", ""),
                "targets": [self._fork_target(entry)],
            }
            for name, entry in self._fork_catalog.items()
            if name not in upstream_names
        ]

    def _apply_fork(self, app: dict, target: dict) -> tuple[dict, dict]:
        """Point an app the fork maintains at the fork's own repository and branch.
        An empty target means the fork ships nothing for this Frappe major, which
        makes the app non-installable rather than silently upstream."""
        if not self.fork:
            return app, target
        entry = self._fork_catalog.get(app["name"])
        repo = entry["url"] if entry else self.fork.repo_for(app["name"])
        if not repo:
            return app, target
        title = f"{app.get('title') or app['name']} ({self.fork.title})"
        return (
            {**app, "repo": repo, "title": title},
            {**target, "target_type": "branch", "target": self._fork_branch(entry)},
        )

    def _make_resolver(self, app: dict, target: dict, is_installable: bool) -> "Resolver":
        app, target = self._apply_fork(app, target)
        is_installable = is_installable and bool(target.get("target"))
        return Resolver(
            app=app["name"],
            repo=app["repo"],
            target_type=target.get("target_type", ""),
            target=target.get("target", ""),
            version=target.get("version", ""),
            frappe_version=self.frappe_version,
            required_version=target.get("frappe_core") or "",
            dependencies=target.get("dependencies", {}),
            title=app.get("title", app["name"]),
            description=app.get("description", ""),
            logo_url=app.get("logo_url", ""),
            category=app.get("category", ""),
            categories=app.get("categories", []),
            stars=app.get("stars") or 0,
            documentation=app.get("documentation", ""),
            website=app.get("website", ""),
            is_installable=is_installable,
        )

    def read_all_apps(self) -> list[Resolver]:
        resolvers = []
        dependency_lookup: dict[str, list[Resolver]] = {}
        current_frappe = Version(self.upstream_frappe_version)

        for app in self._registry:
            # For an app the fork publishes, the fork's own branches decide
            # compatibility - upstream's targets describe a repository we won't clone.
            fork_entry = self._fork_catalog.get(app["name"])
            targets = [self._fork_target(fork_entry)] if fork_entry else (app.get("targets") or [])
            compatible_targets = [t for t in targets if t["_spec"] and current_frappe in t["_spec"]]
            if not compatible_targets:
                fallback = self._frappeverse_fallback_target(app["name"], current_frappe)
                if fallback:
                    compatible_targets = [fallback]
            best_match = compatible_targets[0] if compatible_targets else None
            display_target = best_match or (targets[0] if targets else {})

            resolvers.append(self._make_resolver(app, display_target, is_installable=bool(best_match)))

            if compatible_targets:
                dependency_lookup[app["name"]] = [
                    self._make_resolver(app, t, is_installable=True) for t in compatible_targets
                ]

        for resolver in resolvers:
            resolver._registry = dependency_lookup
        return resolvers

    def find_app(self, name: str) -> Resolver:
        """Look up a marketplace app by name, or raise AppNotFoundError - the
        single place every caller resolves a marketplace name to its Resolver."""
        resolver = next((r for r in self.read_all_apps() if r.app == name), None)
        if resolver is None:
            raise AppNotFoundError(f"'{name}' not found in marketplace.")
        return resolver
