"""Frappe forks: distributions that renumber the framework and ship their own app repositories.

A fork bench reports its own framework version - dodock's `develop` is `6.0.0-dev`
where upstream Frappe `develop` is `17.0.0-dev`. The marketplace registry expresses
compatibility against upstream Frappe (`>=17.0.0-dev,<18.0.0`), so without a
translation every marketplace app looks incompatible on a fork bench.

Apps the fork maintains itself must also come from the fork's repository: installing
`github.com/frappe/erpnext` on a dodock bench would be wrong even once the versions
line up. Which apps those are comes from the supplemental catalog (see
Marketplace._frappeverse_entries) - `repos` here is the floor that keeps the
framework's own app correct when that file is absent.
"""

import re
from dataclasses import dataclass, field

from packaging.version import Version


@dataclass(frozen=True)
class Fork:
    name: str
    title: str
    framework_repo: str
    namespace: str
    """Repository prefix shared by every app this fork publishes."""
    version_offset: int
    """Fork major + offset == upstream Frappe major (dodock 6 -> Frappe 17)."""
    cli_app: str = ""
    """The fork's bench CLI - published in the namespace, but not an installable app."""
    repos: dict[str, str] = field(default_factory=dict)
    """App name -> fork repository, used when no catalog is available."""
    branches: dict[int, str] = field(default_factory=dict)
    """Upstream Frappe major -> the branch the fork ships for it."""

    def to_upstream_version(self, version: str) -> str:
        """Translate a fork framework version into its upstream Frappe equivalent."""
        parsed = Version(version)
        _, separator, rest = str(parsed).partition(".")
        return f"{parsed.major + self.version_offset}{separator}{rest}"

    def repo_for(self, app_name: str) -> str:
        return self.repos.get(app_name, "")

    def branch_for(self, upstream_major: int) -> str:
        return self.branches.get(upstream_major, "")

    def publishes(self, repo: str) -> bool:
        return bool(repo) and self.namespace in repo

    @staticmethod
    def app_name(catalog_name: str) -> str:
        """Catalog entries disambiguate forks in the name itself: 'erpnext(dokos)'
        installs as the app 'erpnext'."""
        return re.sub(r"\(.*\)$", "", catalog_name or "").strip()


DOKOS = Fork(
    name="dokos",
    title="Dokos",
    framework_repo="dokos/dodock",
    namespace="gitlab.com/dokos/",
    version_offset=11,
    cli_app="dokos-cli",
    repos={"erpnext": "https://gitlab.com/dokos/dokos"},
    # dokos follows its hotfix branch where one exists; the suffix spelling changed
    # from "-hotfix" to "-fix" at v4, hence the irregular names.
    branches={
        17: "develop",
        16: "v5-fix",
        15: "v4-fix",
        14: "v3.x.x-hotfix",
        13: "v2.x.x",
        12: "v1.x.x",
    },
)

FORKS: tuple[Fork, ...] = (DOKOS,)


def detect_fork(framework_repo: str) -> Fork | None:
    """Identify the fork a bench runs from its framework app repository, if any."""
    return next((fork for fork in FORKS if fork.framework_repo in framework_repo), None)
