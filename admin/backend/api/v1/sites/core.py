from __future__ import annotations

import re
import secrets
from pathlib import Path

from flask import current_app, jsonify, request

from admin.backend.api.responses import accepted_task_response, created_response, error_response
from admin.backend.api.v1.sites import sites_bp
from admin.backend.api.v1.sites.login import no_store as _no_store
from admin.backend.api.v1.sites.shared import (
    internal_error,
    invalid_fields,
    malformed_body,
    new_site_name_error,
    site_name,
    site_name_failure,
    site_not_found,
    task_failure,
    text_fields,
)
from admin.backend.middleware import rate_limit, require_scope
from admin.backend.providers.apps import AppInfo, AppProvider
from admin.backend.providers.sites import SiteInfo, SiteProvider
from pilot.core.bench import Bench
from pilot.internal.site_paths import site_config_path, site_exists
from pilot.internal.validators import validate_site_name
from pilot.tasks.clear_cache import ClearCacheTask
from pilot.tasks.drop_site import DropSiteTask
from pilot.tasks.migrate import MigrateTask
from pilot.tasks.new_site import NewSiteTask
from pilot.tasks.reinstall_site import ReinstallSiteTask


@sites_bp.get("")
def list_sites():
    bench_root = Path(current_app.config["BENCH_ROOT"])
    try:
        sites = SiteProvider(bench_root).get_all()
    except Exception:
        return internal_error("Could not read sites.")

    payload = []
    for site in sites:
        payload.append(_site_resource(site, bench_root))
    return jsonify(payload)


@sites_bp.route("/<name>")
@require_scope(site_name)
def detail(name: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    if not site_exists(bench_root, name):
        return site_not_found()
    try:
        site = SiteProvider(bench_root).get_one(name)
    except Exception:
        return internal_error("Could not read site.")

    # Installable = apps that are cloned but not yet installed on this site
    try:
        apps = AppProvider(bench_root).get_all()
        installable = [a.name for a in apps if a.name not in site.installed_apps]
        framework_branch = _framework_branch(apps)
    except Exception:
        installable = []
        framework_branch = ""

    try:
        bench_config = Bench(bench_root).config
        http_port = bench_config.http_port
        nginx_enabled = bench_config.production.enabled
        admin_tls = bench_config.admin.tls
    except Exception:
        http_port = 8000
        nginx_enabled = False
        admin_tls = False

    return jsonify(
        {
            **_site_resource(site, bench_root, framework_branch),
            "ssl": bool(site.site_config.get("ssl")),
            "installable_apps": installable,
            "http_port": http_port,
            "nginx_enabled": nginx_enabled,
            "admin_tls": admin_tls,
        }
    )


@sites_bp.route("/wildcard-domains", methods=["GET"])
def wildcard_domains():
    """Wildcard domain suffixes (no leading '*') new site names may be built from."""
    from pilot.core.adapters.domain_provider import DomainRouteProvider
    from pilot.utils import wildcard_suffix

    try:
        patterns = DomainRouteProvider.wildcard_domains()
    except Exception:
        return internal_error("Could not read wildcard domains.")
    return jsonify({"domains": [wildcard_suffix(p) for p in patterns]})


@sites_bp.post("")
def create_site():
    bench_root = Path(current_app.config["BENCH_ROOT"])
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return malformed_body()
    fields = text_fields(data, "name")
    apps_value = data.get("apps", [])
    if (
        fields is None
        or not isinstance(apps_value, list)
        or not all(isinstance(app, str) for app in apps_value)
    ):
        return invalid_fields()

    is_https, name = _strip_scheme(fields["name"])
    admin_password = secrets.token_urlsafe(16)
    apps = [app.strip() for app in apps_value if app.strip()]
    err = validate_site_name(name) or new_site_name_error(bench_root, name)
    if err:
        return site_name_failure(err)

    try:
        task_id = NewSiteTask.queue(
            Bench(bench_root),
            name=name,
            admin_password=admin_password,
            apps=apps,
            idempotency_key=request.headers.get("Idempotency-Key"),
            resource_key=f"site:{name.lower()}",
        )
    except Exception as error:
        return task_failure(error)

    if is_https:
        _register_slim_domain(bench_root, name)

    return accepted_task_response(bench_root, task_id)


def _strip_scheme(name: str) -> tuple[bool, str]:
    """https://<host> -> (True, <host>) ; http://<host> or bare <host> -> (False, <host>).

    Mirrors .config/mise/tasks/site/new's _strip_scheme — this is pilot's own "New Site"
    dialog, a separate code path from the mise CLI that never shares its scheme parsing.
    """
    if name.startswith("https://"):
        return True, name[len("https://") :]
    if name.startswith("http://"):
        return False, name[len("http://") :]
    return False, name


def _add_slim_domain_to_env(env_path: Path, domain: str) -> None:
    """Append ``domain`` to the project .env's SLIM_DOMAINS list (creating the line if
    absent), deduplicating against whatever is already registered there."""
    lines = env_path.read_text().splitlines() if env_path.exists() else []
    current = next((line.split("=", 1)[1].strip() for line in lines if line.strip().startswith("SLIM_DOMAINS")), "")
    domains = [d.strip() for d in current.split(",") if d.strip()]
    if domain in domains:
        return
    updated = ",".join([*domains, domain])
    if any(line.strip().startswith("SLIM_DOMAINS") for line in lines):
        new_lines = [f"SLIM_DOMAINS = {updated}" if line.strip().startswith("SLIM_DOMAINS") else line for line in lines]
        env_path.write_text("\n".join(new_lines) + "\n")
    else:
        with env_path.open("a") as f:
            f.write(f"SLIM_DOMAINS = {updated}\n")


def _register_slim_domain(bench_root: Path, domain: str) -> None:
    """Register a local-dev HTTPS domain with slim (nilbuild) — mef-specific, mirrors
    .config/mise/tasks/site/new's _register_slim. Touches the mef PROJECT's own
    .env/pitchfork.toml (one level above the bench root), not pilot.core state, so it
    lives in this thin API layer rather than pilot.core.
    """
    import contextlib
    import shutil
    import subprocess

    project_root = bench_root.parent
    _add_slim_domain_to_env(project_root / ".env", domain)

    pitchfork_path = project_root / "pitchfork.toml"
    if pitchfork_path.exists() and "[daemons.slim]" not in pitchfork_path.read_text():
        with pitchfork_path.open("a") as f:
            f.write('\n[daemons.slim]\nrun = "mise run slim:server"\ndepends = ["bench"]\n')

    pitchfork = shutil.which("pitchfork")
    if pitchfork:
        # Best-effort, mirrors _register_slim's `|| echo "not up yet"` — a daemon that
        # isn't running yet (first site created before `mise r up`) makes `restart` hang
        # rather than fail fast, so this must never block the request past the timeout.
        with contextlib.suppress(subprocess.TimeoutExpired, OSError):
            subprocess.run(
                [pitchfork, "restart", "slim"],
                cwd=project_root,
                capture_output=True,
                timeout=10,
                check=False,
            )


@sites_bp.delete("/<name>")
@require_scope(site_name)
def drop_site(name: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    if not site_exists(bench_root, name):
        return site_not_found()
    try:
        task_id = DropSiteTask.queue(
            Bench(bench_root),
            site=name,
            idempotency_key=request.headers.get("Idempotency-Key"),
            resource_key=f"site:{name.lower()}",
        )
    except Exception as error:
        return task_failure(error)
    return accepted_task_response(bench_root, task_id)


@sites_bp.post("/<name>/actions/reinstall")
@require_scope(site_name)
def reinstall_site(name: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    if not site_exists(bench_root, name):
        return site_not_found()
    data = request.get_json(silent=True)
    if data is None:
        data = {}
    elif not isinstance(data, dict):
        return malformed_body()
    admin_password = data.get("admin_password")
    if not isinstance(admin_password, str) or not admin_password.strip():
        admin_password = secrets.token_urlsafe(16)
    try:
        task_id = ReinstallSiteTask.queue(
            Bench(bench_root),
            site=name,
            admin_password=admin_password,
            idempotency_key=request.headers.get("Idempotency-Key"),
            resource_key=f"site:{name.lower()}",
        )
    except Exception as error:
        return task_failure(error)
    return accepted_task_response(bench_root, task_id)


@sites_bp.post("/<name>/actions/clear-cache")
@require_scope(site_name)
def clear_cache(name: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    if not site_exists(bench_root, name):
        return site_not_found()
    try:
        task_id = ClearCacheTask.queue(
            Bench(bench_root),
            site=name,
            idempotency_key=request.headers.get("Idempotency-Key"),
            resource_key=f"site:{name.lower()}",
        )
    except Exception as error:
        return task_failure(error)
    return accepted_task_response(bench_root, task_id)


@sites_bp.post("/<name>/actions/migrate")
@require_scope(site_name)
def migrate_site(name: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    if not site_exists(bench_root, name):
        return site_not_found()
    try:
        task_id = MigrateTask.queue(
            Bench(bench_root),
            site=name,
            idempotency_key=request.headers.get("Idempotency-Key"),
            resource_key=f"site:{name.lower()}",
        )
    except Exception as error:
        return task_failure(error)
    return accepted_task_response(bench_root, task_id)


@sites_bp.post("/<name>/login")
@require_scope(site_name)
@rate_limit(10, 60, user_ip=True)
def create_login_link(name: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    config_path = site_config_path(bench_root, name)
    if config_path is None:
        return site_not_found()
    try:
        bench = Bench(bench_root)
        proxy_tls = current_app.config["SESSION_COOKIE_SECURE"] and not bench.config.admin.tls
        proxy_tls = proxy_tls or name in _slim_domains(bench_root)
        url = bench.site(name).admin_login_url(proxy_tls=proxy_tls)
    except Exception:
        return error_response(
            "configuration_unavailable",
            "Site login configuration is unavailable.",
            503,
        )
    if not url:
        return error_response(
            "site_login_unavailable",
            "Could not create a site login session.",
            503,
        )

    return _no_store(created_response({"url": url}, url))


def _slim_domains(bench_root: Path) -> set[str]:
    """Domains registered with slim (mef's local-dev HTTPS proxy) — read straight from
    the mef project's own .env (one level above the bench root), same source of truth
    as _register_slim_domain/_strip_scheme above."""
    env_path = bench_root.parent / ".env"
    if not env_path.exists():
        return set()
    for line in env_path.read_text().splitlines():
        if line.strip().startswith("SLIM_DOMAINS"):
            value = line.split("=", 1)[1].strip()
            return {d.strip() for d in value.split(",") if d.strip()}
    return set()


def _mcp_status(bench_root: Path, site: str) -> dict | None:
    """None unless the mcp overlay (.config/overlays/mcp) is active for this project
    AND this site's token pair was provisioned (`mise r mcp:token <site>`) — same
    project .env, same slug/var-name derivation as transform.py / the mcp:token task.
    Carries everything the frontend needs to reproduce transform.py's .mcp.json entry
    (client wiring) without guessing at ports or header shapes twice."""
    env_path = bench_root.parent / ".env"
    if not env_path.exists():
        return None
    lines = env_path.read_text().splitlines()
    overlays = next((line.split("=", 1)[1] for line in lines if line.strip().startswith("FRAPPE_OVERLAYS")), "")
    if "mcp" not in [o.strip() for o in overlays.split(",")]:
        return None
    token_var = "FRAPPE_MCP_TOKEN_" + re.sub(r"[^A-Z0-9]+", "_", site.upper()).strip("_")
    token = next(
        (line.split("=", 1)[1].strip() for line in lines if line.strip().startswith(f"{token_var}=")),
        None,
    )
    if not token:
        return None
    port = next((line.split("=", 1)[1].strip() for line in lines if line.strip().startswith("WEB_PORT")), "8000")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", site.replace(".localhost", "")).strip("-")
    return {
        "server_name": f"frappe-{slug}",
        "url": f"http://127.0.0.1:{port}/api/method/frappe.mcp.handle_mcp",
        "token_env": token_var,
        "token": token,
        "site": site,
    }


def _site_resource(site: SiteInfo, bench_root: Path, framework_branch: str = "") -> dict:
    return {
        "name": site.name,
        "exists": site.exists,
        "installed_apps": [app for app in site.installed_apps if isinstance(app, str)],
        "framework_branch": framework_branch,
        "broken": site.broken,
        "provisioning": site.provisioning,
        "slim": site.name in _slim_domains(bench_root),
        "mcp": _mcp_status(bench_root, site.name),
    }


def _framework_branch(apps: list[AppInfo]) -> str:
    """Frappe version for display: release branch ('version-16' -> 'Version 16'),
    else the pip-installed version ('17.0.0.dev0' on develop), else the branch.
    Empty when frappe isn't cloned."""
    frappe = next((a for a in apps if a.name == "frappe"), None)
    if frappe is None:
        return ""
    if re.match(r"version-\d+", frappe.branch):
        return frappe.branch
    return frappe.installed_version or frappe.branch
