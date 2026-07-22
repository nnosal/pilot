"""Multi-bench registry: aggregate status across sibling mef projects.

``GET /api/v1/mef/registry`` scans the mef root and returns, for each sibling
project, profile/frappe version/db engine/ports plus the derived pilot port
and an HTTP liveness probe of that port. Used by the pilot admin UI to render
a global project switcher.

Design (see ``.claude/plans/multi-bench-registry.md``):

* Reuses mef.py's scan filter (``_iter_mef_projects``) so protected names and
  the ``.miserc.toml`` requirement stay in one place.
* Pilot liveness probes ``/api/v1/health`` on each project's derived admin
  port. Probes run on a ``ThreadPoolExecutor`` so a handful of stopped
  projects (connection-refused, fast) does not stall the scan. The
  ``HTTPConnection`` timeout bounds both connect and read, so a port held by
  a non-pilot process cannot hang the pool.
* Filesystem-first: counting sites/apps walks the bench dir directly rather
  than constructing a ``Bench`` object, so a corrupt or half-setup project
  degrades to zero counts instead of raising.
* Pilot port mirrors the bash derivation in ``.config/mise/tasks/pilot/admin``:
  ``cksum <<< "<name>"`` then ``7101 + (cksum % 90)``. The herestring adds a
  trailing newline that is part of the hashed input.

Per-service status/control (pilot/app/redis/db) for the Projects page's
expandable rows lives in this module too — same "sibling project visibility"
concern:

* pilot/app/redis are pitchfork daemons. A single ``pitchfork list`` call
  (global, not scoped to one project directory) returns every daemon of every
  project in one shot, namespaced ``<project>/<daemon>`` — far cheaper than
  probing each project. ``app`` in the API maps to the ``bench`` daemon name.
* db (mariadb sandbox via dbdeployer) is not a pitchfork daemon, so its
  status is fetched lazily, one project at a time, only when that project's
  row is expanded (``GET /mef/projects/<name>/db-status``).
* Sites reuse ``SiteProvider`` directly against the sibling's bench root —
  no need for that project's pilot admin to be running.
* Starting/stopping a service spawns ``pitchfork start/stop <daemon>`` or
  ``mise r db:start``/``db:stop`` in the sibling project's directory via the
  same tracked-subprocess job model as ``mef.py``'s project create/delete.
  Self is only refused for the ``pilot`` service — stopping the *other*
  services of the project hosting this admin doesn't kill the admin process.
"""

from __future__ import annotations

import http.client
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from flask import Blueprint, jsonify

from admin.backend.api.responses import error_response
from admin.backend.api.v1.mef import (
    _PROTECTED_PROJECTS,
    _gate,
    _host_project_name,
    _is_valid_project_name,
    _iter_mef_projects,
    _mef_root,
    _mise_cmd,
    _read_frappe_version,
    _read_project_env,
    _spawn_job,
)
from admin.backend.providers.sites import SiteProvider

registry_bp = Blueprint("registry", __name__)

_PING_TIMEOUT_SECONDS = 0.5
_PARALLEL_PING_WORKERS = 8

# API service key -> pitchfork daemon name (pitchfork.toml's [daemons.*]).
_PITCHFORK_SERVICES = {"pilot": "pilot-admin", "app": "bench", "redis": "redis", "mailpit": "mailpit"}
_SERVICE_NAMES = {*_PITCHFORK_SERVICES, "db"}
_SUBPROCESS_TIMEOUT_SECONDS = 5

# Pilot admin port formula (mirror .config/mise/tasks/pilot/admin lines 20-24):
# ck=$(cksum <<< "$(basename "$PROJ")"); PILOT_ADMIN_PORT=$((7101 + ck % 90))
_PILOT_PORT_BASE = 7101
_PILOT_PORT_MOD = 90

_MISERC_ENV_RE = re.compile(r'^\s*env\s*=\s*\["([^"]+)"\]')


@registry_bp.get("/mef/registry")
def list_registry():
    """List sibling mef projects with status and metadata."""
    gate = _gate()
    if gate is not None:
        return gate
    return jsonify(_build_registry_payload())


def _build_registry_payload() -> dict:
    mef_root = _mef_root()
    host_name = _host_project_name()
    projects = list(_iter_mef_projects(mef_root))
    pitchfork_states = _pitchfork_daemon_states()
    entries: list[dict | None] = [None] * len(projects)
    worker_count = max(1, min(_PARALLEL_PING_WORKERS, len(projects))) if projects else 1
    with ThreadPoolExecutor(max_workers=worker_count) as pool:
        futures = {
            pool.submit(_project_entry, child, mef_root, host_name, pitchfork_states): idx
            for idx, child in enumerate(projects)
        }
        for future in as_completed(futures):
            idx = futures[future]
            try:
                entries[idx] = future.result()
            except Exception:
                # Degrade gracefully: one corrupt project must not break the whole scan
                entries[idx] = {
                    "name": projects[idx].name,
                    "is_self": projects[idx].name == host_name,
                    "profile": "",
                    "frappe_version": "",
                    "db_engine": "",
                    "ports": {"web": None, "db": None, "redis": None, "mailpit": None},
                    "pilot_port": None,
                    "pilot_running": False,
                    "sites_count": 0,
                    "apps_count": 0,
                    "services": {"pilot": "unknown", "app": "unknown", "redis": "unknown", "mailpit": "unknown"},
                }
    return {"mef_root": str(mef_root), "projects": entries}


def _project_entry(child: Path, mef_root: Path, host_name: str, pitchfork_states: dict) -> dict:
    env = _read_project_env(child)
    profile = _read_miserc_profile(child / ".miserc.toml")
    frappe_version = _read_frappe_version(_profile_path(mef_root, profile))
    pilot_port = _pilot_port_from_name(child.name)
    project_daemons = pitchfork_states.get(child.name, {})
    services = {
        api_key: project_daemons.get(daemon_name, "stopped")
        for api_key, daemon_name in _PITCHFORK_SERVICES.items()
    }
    return {
        "name": child.name,
        "is_self": child.name == host_name,
        "services": services,
        "profile": profile,
        "frappe_version": frappe_version,
        "db_engine": env.get("DB_ENGINE", ""),
        "overlays": [o.strip() for o in env.get("FRAPPE_OVERLAYS", "").split(",") if o.strip()],
        "ports": {
            "web": _port_int(env.get("WEB_PORT")),
            "db": _port_int(env.get("DB_PORT")),
            "redis": _port_int(env.get("REDIS_PORT")),
            "mailpit": _port_int(env.get("MAILPIT_UI_PORT")),
        },
        "pilot_port": pilot_port,
        "pilot_running": _ping_pilot_health(pilot_port),
        "sites_count": _count_sites(_bench_dir(child, env) / "sites"),
        "apps_count": _count_apps(_bench_dir(child, env) / "apps"),
    }


def _bench_dir(target: Path, env: dict | None = None) -> Path:
    """Sibling project's bench dir — ``PROJECT_NAME`` (default ``app``), not a
    hardcoded ``"app"``. mef derives it per-project (see ``.env``); a project
    created with a custom ``PROJECT_NAME`` (e.g. ``frappe``) has no ``app/``
    dir at all, which silently 404'd every route below before this helper."""
    return target / (env or _read_project_env(target)).get("PROJECT_NAME", "app")


def _profile_path(mef_root: Path, profile: str) -> Path:
    return mef_root / ".config" / "mise" / f"config.{profile}.toml"


def _read_miserc_profile(miserc_path: Path) -> str:
    if not miserc_path.is_file():
        return ""
    try:
        for line in miserc_path.read_text(encoding="utf-8").splitlines():
            match = _MISERC_ENV_RE.match(line)
            if match:
                return match.group(1).strip()
    except (OSError, ValueError):
        return ""
    return ""


def _port_int(raw: str | None) -> int | None:
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _count_sites(sites_path: Path) -> int:
    if not sites_path.is_dir():
        return 0
    try:
        return sum(
            1
            for child in sites_path.iterdir()
            if child.is_dir() and (child / "site_config.json").is_file()
        )
    except OSError:
        return 0


def _count_apps(apps_path: Path) -> int:
    if not apps_path.is_dir():
        return 0
    try:
        return sum(
            1
            for child in apps_path.iterdir()
            if child.is_dir() and (child / ".git").exists()
        )
    except OSError:
        return 0


def _ping_pilot_health(port: int) -> bool:
    """Return True if a pilot admin answers ``/api/v1/health`` on ``port``."""
    try:
        conn = http.client.HTTPConnection("localhost", port, timeout=_PING_TIMEOUT_SECONDS)
        try:
            conn.request("GET", "/api/v1/health")
            return conn.getresponse().status == 200
        finally:
            conn.close()
    except (OSError, http.client.HTTPException, TimeoutError):
        return False


def _pitchfork_daemon_states() -> dict[str, dict[str, str]]:
    """Parse ``pitchfork list`` into ``{project: {daemon: status}}``.

    One global call (not scoped to any project directory) covers every
    project's daemons at once. Missing pitchfork or an empty/failed listing
    degrades to an empty mapping — callers then default each service to
    "stopped" rather than raising.
    """
    pitchfork = shutil.which("pitchfork")
    if not pitchfork:
        return {}
    try:
        result = subprocess.run(
            [pitchfork, "list"],
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {}
    states: dict[str, dict[str, str]] = {}
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) < 2 or "/" not in parts[0]:
            continue
        project, _, daemon = parts[0].partition("/")
        states.setdefault(project, {})[daemon] = parts[1]
    return states


def _resolve_sibling_project(name: str):
    """Return ``(project_dir, None)`` or ``(None, error_response)``."""
    if not _is_valid_project_name(name):
        return None, error_response("invalid_project", f"'{name}' is not a valid project name.", 422)
    if name in _PROTECTED_PROJECTS:
        return None, error_response("invalid_project", f"'{name}' is protected.", 422)
    target = _mef_root() / name
    if not target.is_dir() or not (target / ".miserc.toml").is_file():
        return None, error_response("project_not_found", f"Project '{name}' not found.", 404)
    return target, None


def _first_usable_site(target: Path):
    try:
        sites = SiteProvider(_bench_dir(target)).get_all()
    except Exception:
        return None
    return next((s for s in sites if s.exists and not s.broken), None)


def _resolve_sibling_site(target: Path, site: str):
    """Return ``(SiteInfo, None)`` or ``(None, error_response)``."""
    try:
        sites = SiteProvider(_bench_dir(target)).get_all()
    except Exception:
        return None, error_response("site_not_found", f"Site '{site}' not found.", 404)
    resolved = next((s for s in sites if s.name == site), None)
    if resolved is None:
        return None, error_response("site_not_found", f"Site '{site}' not found.", 404)
    return resolved, None


@registry_bp.get("/mef/projects/<name>/db-status")
def project_db_status(name: str):
    gate = _gate()
    if gate is not None:
        return gate
    target, err = _resolve_sibling_project(name)
    if err is not None:
        return err
    return jsonify({"status": _read_db_status(target)})


def _read_db_status(project_dir: Path) -> str:
    try:
        result = subprocess.run(
            _mise_cmd(["db:status"]),
            cwd=str(project_dir),
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if stripped.endswith(" on"):
            return "running"
        if stripped.endswith(" off"):
            return "stopped"
    return "unknown"


@registry_bp.get("/mef/projects/<name>/sites")
def project_sites(name: str):
    gate = _gate()
    if gate is not None:
        return gate
    target, err = _resolve_sibling_project(name)
    if err is not None:
        return err
    try:
        sites = SiteProvider(_bench_dir(target)).get_all()
    except Exception:
        return jsonify({"sites": []})
    return jsonify({"sites": [_site_summary(site, target) for site in sites]})


def _site_summary(site, project_dir: Path) -> dict:
    from admin.backend.api.v1.sites.core import _mcp_status, _slim_domains

    bench_root = _bench_dir(project_dir)
    return {
        "name": site.name,
        "exists": site.exists,
        "broken": site.broken,
        "provisioning": site.provisioning,
        "installed_apps": [app for app in site.installed_apps if isinstance(app, str)],
        "slim": site.name in _slim_domains(bench_root),
        "mcp": _mcp_status(bench_root, site.name),
    }


@registry_bp.post("/mef/projects/<name>/mailpit/test")
def test_mailpit(name: str):
    """Send a test email through frappe.sendmail() to prove the mailpit outgoing wiring works."""
    gate = _gate()
    if gate is not None:
        return gate
    target, err = _resolve_sibling_project(name)
    if err is not None:
        return err
    site = _first_usable_site(target)
    if site is None:
        return error_response("no_site", f"No usable site found in project '{name}'.", 404)

    # bench execute --kwargs eval()s this string as a Python literal, not JSON —
    # repr() (not json.dumps, which emits lowercase true/false) keeps it valid.
    kwargs = repr(
        {
            "recipients": ["mailpit-test@example.test"],
            "subject": "Pilot -> mailpit test",
            "message": f"Test email sent from the pilot admin UI for project '{name}'.",
            "now": True,
        }
    )
    bench = shutil.which("bench") or "bench"
    args = [bench, "--site", site.name, "execute", "frappe.sendmail", "--kwargs", kwargs]
    job_id = _spawn_job(args=args, env_extras={}, cwd=_bench_dir(target), label="mailpit-test")
    return jsonify({"job_id": job_id, "log_url": f"/api/v1/mef/jobs/{job_id}"}), 202


# bench execute boots the full Frappe framework each call — slower than the
# plain shell probes above (db-status, pitchfork list), needs its own timeout.
_BENCH_EXECUTE_TIMEOUT_SECONDS = 15


@registry_bp.get("/mef/projects/<name>/sites/<site>/setup-status")
def project_site_setup_status(name: str, site: str):
    """Report whether a sibling site's Frappe setup wizard (System Settings.setup_complete) has run."""
    gate = _gate()
    if gate is not None:
        return gate
    target, err = _resolve_sibling_project(name)
    if err is not None:
        return err
    _, err = _resolve_sibling_site(target, site)
    if err is not None:
        return err
    return jsonify({"setup_complete": _read_setup_complete(target, site)})


def _read_setup_complete(target: Path, site: str) -> bool | None:
    bench = shutil.which("bench") or "bench"
    try:
        result = subprocess.run(
            [
                bench,
                "--site",
                site,
                "execute",
                "frappe.db.get_single_value",
                "--args",
                '["System Settings", "setup_complete"]',
            ],
            cwd=str(_bench_dir(target)),
            capture_output=True,
            text=True,
            timeout=_BENCH_EXECUTE_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    lines = [line for line in result.stdout.strip().splitlines() if line.strip()]
    last = lines[-1] if lines else ""
    return last in ("1", "True")


@registry_bp.post("/mef/projects/<name>/sites/<site>/wizard")
def run_site_wizard(name: str, site: str):
    """Spawn the headless setup wizard targeted at one sibling site.

    ``site`` is passed as the mise ``wizard`` task's positional arg, not just
    ``SITE_DOMAIN`` env: mise reloads the sibling project's ``.env`` (``_.file``
    in config.toml) before the task runs, which silently overwrites an
    env-only override with that project's default site — the wizard would
    then always target the default site regardless of which site was
    actually requested. ``PROJECT_NAME`` must come from the SIBLING's own
    ``.env``, not be inherited from this admin's process env (this admin's
    own project may use a different one — same bug class as the hardcoded
    ``"app"`` paths above).
    """
    gate = _gate()
    if gate is not None:
        return gate
    target, err = _resolve_sibling_project(name)
    if err is not None:
        return err
    _, err = _resolve_sibling_site(target, site)
    if err is not None:
        return err
    project_name = _read_project_env(target).get("PROJECT_NAME", "app")
    job_id = _spawn_job(
        args=_mise_cmd(["wizard", site]),
        env_extras={"NONINTERACTIVE": "1", "SITE_DOMAIN": site, "PROJECT_NAME": project_name},
        cwd=target,
        label="wizard",
    )
    return jsonify({"job_id": job_id, "log_url": f"/api/v1/mef/jobs/{job_id}"}), 202


@registry_bp.post("/mef/projects/<name>/services/<service>/start")
def start_service(name: str, service: str):
    return _control_service(name, service, "start")


@registry_bp.post("/mef/projects/<name>/services/<service>/stop")
def stop_service(name: str, service: str):
    return _control_service(name, service, "stop")


def _control_service(name: str, service: str, action: str):
    # Mirrors pilot up/down/open: any admin may control a sibling's services,
    # no allow_mef_management gate — only self+pilot is refused below.
    if service not in _SERVICE_NAMES:
        return error_response("invalid_service", f"'{service}' is not a known service.", 422)
    target, err = _resolve_sibling_project(name)
    if err is not None:
        return err
    if service == "pilot" and name == _host_project_name():
        return error_response(
            "self_pilot_control_forbidden",
            "Refusing to control the pilot daemon of the project hosting this admin.",
            409,
        )

    if service == "db":
        args = _mise_cmd([f"db:{action}"])
    else:
        pitchfork = shutil.which("pitchfork") or "pitchfork"
        args = [pitchfork, action, _PITCHFORK_SERVICES[service]]

    job_id = _spawn_job(args=args, env_extras={}, cwd=target, label=f"{service}-{action}")
    return jsonify({"job_id": job_id, "log_url": f"/api/v1/mef/jobs/{job_id}"}), 202


def _pilot_port_from_name(name: str) -> int:
    # Mirror `cksum <<< "$name"`: the bash herestring appends a trailing
    # newline that is part of the hashed input.
    return _PILOT_PORT_BASE + (_cksum(name.encode("utf-8") + b"\n") % _PILOT_PORT_MOD)


def _cksum(data: bytes) -> int:
    """POSIX cksum of ``data`` — matches the ``cksum`` core utility output."""
    crc = 0
    for byte in data:
        crc = ((crc << 8) ^ _CKSUM_TABLE[((crc >> 24) ^ byte) & 0xFF]) & 0xFFFFFFFF
    length = len(data)
    while length:
        crc = ((crc << 8) ^ _CKSUM_TABLE[((crc >> 24) ^ length) & 0xFF]) & 0xFFFFFFFF
        length >>= 8
    return (~crc) & 0xFFFFFFFF


def _make_cksum_table() -> list[int]:
    table: list[int] = []
    for n in range(256):
        crc = n << 24
        for _ in range(8):
            if crc & 0x80000000:
                crc = ((crc << 1) ^ 0x04C11DB7) & 0xFFFFFFFF
            else:
                crc = (crc << 1) & 0xFFFFFFFF
        table.append(crc)
    return table


_CKSUM_TABLE = _make_cksum_table()
