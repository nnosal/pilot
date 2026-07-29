"""mef project management blueprint.

``/api/v1/mef/projects`` lets the pilot admin of one mef project create and
delete its sibling projects by driving the headless ``mise r new`` / ``mise r
delete`` tasks at the mef root (above the bench). These operations are out of
scope for the bench-scoped Task model, so each POST/DELETE spawns a tracked
subprocess whose stdout is tailed by ``GET /mef/jobs/<id>``.

Design constraints (see .claude/plans/pilot-project-ui.md):

* ``admin.allow_mef_management`` gates every route. Default false — the mef
  ``pilot:admin`` task opts in.
* Inputs reach ``mise`` only via ``env=`` (no ``shell=True``); no UI value is
  ever interpolated into a command string. ``NEW_DIR``/``DELETE_CONFIRM``
  rejections stay owned by the task (it exits 1 on path traversal).
* Refuses to delete the mef project hosting this admin: ``mise r down`` would
  kill the admin mid-request.
"""

from __future__ import annotations

import contextlib
import json
import os
import shlex
import shutil
import socket
import subprocess
import threading
import time
import uuid
from pathlib import Path

import psutil
import requests
from flask import Blueprint, current_app, jsonify, request

from admin.backend.api.responses import error_response
from pilot.config import BenchConfig

mef_bp = Blueprint("mef", __name__)

_JOBS_LOCK = threading.Lock()
_JOBS: dict[str, dict] = {}

# Job cleanup: cap at 100 entries, reap completed jobs older than 1 hour.
_MAX_JOBS = 100
_JOB_COMPLETED_TTL = 3600  # seconds

# Reserved/skipped by the ``delete`` task; mirror its filter on the way out.
_PROTECTED_PROJECTS = {"_demo", "_o"}

# postgres is a native frappe backend but only sound from v16; sqlite/dolt_sqlite exist
# only from v16. Mirrors the same gate in .config/mise/tasks/new (step 3).
_DB_ENGINES_V16 = {"postgres", "sqlite", "dolt_sqlite"}
_DB_ENGINES_ALL = {"mariadb", "dolt", *_DB_ENGINES_V16}

# A fork is an overlay on a version profile, not a profile of its own: the task adds it
# last in `.miserc.toml`'s `env` (see tasks/new step 1b).
_FORKS = {"frappe", "dokos"}

# Subset of .env keys the dialog surfaces (matches stats._mef_context).
_MEF_ENV_KEYS = (
    "PROJECT_NAME",
    "DB_ENGINE",
    "FRAPPE_OVERLAYS",
    "SITE_DOMAIN",
    "WEB_PORT",
    "DB_PORT",
    "REDIS_PORT",
    "MAILPIT_UI_PORT",
)

# Pilot admin port formula (mirror .config/mise/tasks/pilot/admin lines 20-24):
_PILOT_PORT_BASE = 7101
_PILOT_PORT_MOD = 90


def _bench_root() -> Path:
    return Path(current_app.config["BENCH_ROOT"])


def _mef_root() -> Path:
    """Folder above the bench's host project: where ``mise r new`` runs."""
    return _bench_root().parent.parent


def _host_project_name() -> str:
    return _bench_root().parent.name


def _read_admin_config() -> BenchConfig | None:
    try:
        return BenchConfig.read(_bench_root())
    except Exception:
        return None


def _allow_mef_management(config: BenchConfig | None) -> bool:
    return bool(config and config.admin.allow_mef_management)


def _gate():
    config = _read_admin_config()
    if not _allow_mef_management(config):
        return error_response(
            "mef_management_disabled",
            "mef project management is not enabled on this admin.",
            403,
        )
    return None


def _read_project_env(project_dir: Path) -> dict:
    env_path = project_dir / ".env"
    if not env_path.is_file():
        return {}
    values: dict = {}
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, _, raw = stripped.partition("=")
            key = key.strip()
            if key in _MEF_ENV_KEYS:
                values[key] = raw.strip().strip('"').strip("'")
    except (OSError, ValueError):
        return {}
    return values


def _host_project_env_keys() -> set[str]:
    """Every key the hosting project's ``.env`` defines.

    mise injects them into this daemon's own environment - it runs as
    ``mise r pilot:admin`` inside that project. A job spawned for a DIFFERENT
    project inherits them, and any key the target leaves unset keeps the host's
    value: ``DB_ENGINE`` is commented out when it's the mariadb default, so a
    mariadb project created from a postgres host was set up on postgres.
    """
    env_path = _bench_root().parent / ".env"
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return set()
    keys = set()
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        keys.add(stripped.partition("=")[0].strip())
    return keys


def _host_injected_env_keys() -> set[str]:
    """Every variable mise injects into this daemon from its hosting project.

    Two sources: the project's ``.env`` and the ``[env]`` tables of its mise configs
    (``config.<profile>.toml``, ``config.<fork>.toml``). Both are inherited by a job
    spawned for a DIFFERENT project, and the version profiles read some of them back:

        FRAPPE_REPO = "{{ get_env(name='FRAPPE_REPO', default='…/frappe/frappe') }}"

    so a dokos host silently turned a plain frappe project into a dodock clone.

    Read from the config files rather than ``mise env``: only the key names matter,
    so there is nothing to render, and no subprocess to run per spawn.
    """
    project_dir = _bench_root().parent
    mise_dir = _mef_root() / ".config" / "mise"
    sources = [
        mise_dir / "config.toml",
        *(mise_dir / f"config.{profile}.toml" for profile in _host_mise_profiles(project_dir)),
        project_dir / "mise.toml",
    ]
    keys = _host_project_env_keys()
    for source in sources:
        keys |= _toml_env_keys(source)
    return keys


def _host_mise_profiles(project_dir: Path) -> list[str]:
    """The profiles the host project selects in ``.miserc.toml`` (``env = [...]``)."""
    data = _read_toml(project_dir / ".miserc.toml")
    selected = data.get("env")
    if isinstance(selected, str):
        return [selected]
    return [name for name in selected or [] if isinstance(name, str)]


def _toml_env_keys(path: Path) -> set[str]:
    """Names declared in a mise config's ``[env]`` table, minus mise's own
    directives (``_.file``, ``_.path``…)."""
    env = _read_toml(path).get("env") or {}
    return {key for key in env if isinstance(key, str) and not key.startswith("_")}


def _read_toml(path: Path) -> dict:
    import tomllib

    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def _iter_mef_projects(mef_root: Path):
    """Yield mef project directories under ``mef_root``, sorted by name.

    Skips hidden dirs, protected names, and dirs without a ``.miserc.toml``.
    Shared with the registry blueprint so the filter has one owner.
    """
    if not mef_root.is_dir():
        return
    for child in sorted(mef_root.iterdir(), key=lambda p: p.name):
        if not child.is_dir() or child.name.startswith("."):
            continue
        if child.name in _PROTECTED_PROJECTS:
            continue
        if not (child / ".miserc.toml").is_file():
            continue
        yield child


@mef_bp.get("/mef/projects")
def list_projects():
    gate = _gate()
    if gate is not None:
        return gate

    mef_root = _mef_root()
    projects = [
        {
            "name": child.name,
            "is_self": child.name == _host_project_name(),
            "env": _read_project_env(child),
        }
        for child in _iter_mef_projects(mef_root)
    ]
    return jsonify({"projects": projects, "mef_root": str(mef_root)})


@mef_bp.post("/mef/projects")
def create_project():
    gate = _gate()
    if gate is not None:
        return gate

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return error_response("malformed_request", "Expected a JSON object.", 400)

    config = _read_admin_config()
    response = _validate_new_request(data, config)
    if response is not None:
        return response

    env_extras = _new_env_extras(data)
    job_id = _spawn_job(
        args=_mise_cmd(["new"]),
        env_extras=env_extras,
        cwd=_mef_root(),
        label="new",
    )
    return jsonify({"job_id": job_id, "log_url": f"/api/v1/mef/jobs/{job_id}"}), 202


@mef_bp.delete("/mef/projects/<name>")
def delete_project(name: str):
    gate = _gate()
    if gate is not None:
        return gate

    if not _is_valid_project_name(name):
        return error_response("invalid_project", f"'{name}' is not a valid project name.", 422)
    if name in _PROTECTED_PROJECTS:
        return error_response("invalid_project", f"'{name}' is protected.", 422)
    if name == _host_project_name():
        return error_response(
            "mef_self_delete_forbidden",
            "Refusing to delete the mef project hosting this admin.",
            409,
        )

    target = _mef_root() / name
    if not target.is_dir() or not (target / ".miserc.toml").is_file():
        return error_response("project_not_found", f"Project '{name}' not found.", 404)

    job_id = _spawn_job(
        args=_mise_cmd(["delete", name]),
        env_extras={"DELETE_CONFIRM": "1"},
        cwd=_mef_root(),
        label="delete",
    )
    return jsonify({"job_id": job_id, "log_url": f"/api/v1/mef/jobs/{job_id}"}), 202


@mef_bp.post("/mef/projects/<name>/resume")
def resume_project(name: str):
    """Re-run the setup pipeline (via ``mise r resume``) for a project whose
    ``mise r new`` failed partway, from the project's own directory — not
    re-running ``new`` itself, which would refuse: the directory already
    exists. ``resume`` sets ``MEF_RESUME=1``, which makes ``site:new`` skip
    an already-created site instead of erroring, and ``apps:install`` skip
    re-cloning an app whose directory already exists (a bench build/get-app
    failure otherwise leaves it half-cloned, and a plain retry aborts on
    "directory already exists"). Does not force a ``bench migrate`` — that
    was tried and reverted: it replays every fixture on the site, including
    ones with environment-dependent validation (e.g. Email Account tests a
    real SMTP connection), and broke an otherwise-healthy project in testing.
    """
    gate = _gate()
    if gate is not None:
        return gate

    if not _is_valid_project_name(name):
        return error_response("invalid_project", f"'{name}' is not a valid project name.", 422)
    if name in _PROTECTED_PROJECTS:
        return error_response("invalid_project", f"'{name}' is protected.", 422)

    target = _mef_root() / name
    if not target.is_dir() or not (target / ".miserc.toml").is_file():
        return error_response("project_not_found", f"Project '{name}' not found.", 404)

    job_id = _spawn_job(
        args=_mise_cmd(["resume"]),
        env_extras={},
        cwd=target,
        label="resume",
    )
    return jsonify({"job_id": job_id, "log_url": f"/api/v1/mef/jobs/{job_id}"}), 202


@mef_bp.get("/mef/projects/<name>/doctor")
def doctor_project(name: str):
    """Diagnose a project's daemons/ports without touching anything.

    Surfaces the two failure shapes this admin can't tell apart from
    ``pitchfork list`` alone: a daemon pitchfork thinks is running but whose
    tracked PID doesn't actually own the port it's supposed to serve (an
    orphaned sibling process is squatting it instead — the "phantom daemon"
    class of bug), and a bench that's up but whose site doesn't answer
    ``frappe.ping`` (wrong frappe version/broken app install).
    """
    gate = _gate()
    if gate is not None:
        return gate

    if not _is_valid_project_name(name):
        return error_response("invalid_project", f"'{name}' is not a valid project name.", 422)

    target = _mef_root() / name
    if not target.is_dir() or not (target / ".miserc.toml").is_file():
        return error_response("project_not_found", f"Project '{name}' not found.", 404)

    return jsonify(_run_doctor(target, name))


@mef_bp.get("/mef/overlays")
def list_overlays():
    """Overlay names available under ``.config/overlays`` — same set the New Project
    dialog's picker validates against (``_available_overlays``), reused here so the
    Projects page can offer only overlays that actually exist."""
    gate = _gate()
    if gate is not None:
        return gate
    return jsonify({"overlays": sorted(_available_overlays(_mef_root()))})


@mef_bp.post("/mef/projects/<name>/overlays")
def add_overlay(name: str):
    """Activate an overlay on an already-set-up project: append it to the project's
    ``FRAPPE_OVERLAYS`` and re-run ``frappe:overlay`` (idempotent — see the overlay
    contract in CLAUDE.md) so it applies immediately instead of waiting for the next
    full ``mise r setup``."""
    gate = _gate()
    if gate is not None:
        return gate

    if not _is_valid_project_name(name):
        return error_response("invalid_project", f"'{name}' is not a valid project name.", 422)
    if name in _PROTECTED_PROJECTS:
        return error_response("invalid_project", f"'{name}' is protected.", 422)

    target = _mef_root() / name
    if not target.is_dir() or not (target / ".miserc.toml").is_file():
        return error_response("project_not_found", f"Project '{name}' not found.", 404)

    data = request.get_json(silent=True)
    overlay = (data.get("overlay") or "").strip() if isinstance(data, dict) else ""
    if not overlay:
        return error_response("invalid_overlay", "overlay is required.", 422)
    if overlay not in _available_overlays(_mef_root()):
        return error_response("invalid_overlay", f"Unknown overlay '{overlay}'.", 422)

    active = [o.strip() for o in _read_project_env(target).get("FRAPPE_OVERLAYS", "").split(",") if o.strip()]
    if overlay in active:
        return error_response("overlay_already_active", f"Overlay '{overlay}' is already active.", 409)

    _append_overlay(target / ".env", overlay)

    job_id = _spawn_job(
        args=_mise_cmd(["frappe:overlay"]),
        env_extras={},
        cwd=target,
        label="overlay",
    )
    return jsonify({"job_id": job_id, "log_url": f"/api/v1/mef/jobs/{job_id}"}), 202


@mef_bp.delete("/mef/projects/<name>/overlays/<overlay>")
def remove_overlay(name: str, overlay: str):
    """Deactivate an overlay: drop it from ``FRAPPE_OVERLAYS`` so it stops being
    re-applied on the next setup/overlay run. Does NOT undo file changes an already
    applied overlay made (rsync'd files, regex-patched frappe source) — the overlay
    contract has no "unapply" concept; only a fresh bench rebuild removes those."""
    gate = _gate()
    if gate is not None:
        return gate

    if not _is_valid_project_name(name):
        return error_response("invalid_project", f"'{name}' is not a valid project name.", 422)
    if name in _PROTECTED_PROJECTS:
        return error_response("invalid_project", f"'{name}' is protected.", 422)

    target = _mef_root() / name
    if not target.is_dir() or not (target / ".miserc.toml").is_file():
        return error_response("project_not_found", f"Project '{name}' not found.", 404)

    active = [o.strip() for o in _read_project_env(target).get("FRAPPE_OVERLAYS", "").split(",") if o.strip()]
    if overlay not in active:
        return error_response("overlay_not_active", f"Overlay '{overlay}' is not active.", 404)

    _remove_overlay_env(target / ".env", overlay)
    return jsonify({"overlays": [o for o in active if o != overlay]})


def _append_overlay(env_path: Path, overlay: str) -> None:
    """Append ``overlay`` to the project .env's FRAPPE_OVERLAYS list (creating the
    line if absent), deduplicating — mirrors sites/core.py's _add_slim_domain_to_env."""
    lines = env_path.read_text().splitlines() if env_path.exists() else []
    current = next((line.split("=", 1)[1].strip() for line in lines if line.strip().startswith("FRAPPE_OVERLAYS")), "")
    overlays = [o.strip() for o in current.split(",") if o.strip()]
    if overlay in overlays:
        return
    updated = ",".join([*overlays, overlay])
    if any(line.strip().startswith("FRAPPE_OVERLAYS") for line in lines):
        new_lines = [f"FRAPPE_OVERLAYS = {updated}" if line.strip().startswith("FRAPPE_OVERLAYS") else line for line in lines]
        env_path.write_text("\n".join(new_lines) + "\n")
    else:
        with env_path.open("a") as f:
            f.write(f"FRAPPE_OVERLAYS = {updated}\n")


def _remove_overlay_env(env_path: Path, overlay: str) -> None:
    """Drop ``overlay`` from the project .env's FRAPPE_OVERLAYS list."""
    lines = env_path.read_text().splitlines() if env_path.exists() else []
    current = next((line.split("=", 1)[1].strip() for line in lines if line.strip().startswith("FRAPPE_OVERLAYS")), "")
    remaining = [o.strip() for o in current.split(",") if o.strip() and o.strip() != overlay]
    new_lines = [
        f"FRAPPE_OVERLAYS = {','.join(remaining)}" if line.strip().startswith("FRAPPE_OVERLAYS") else line
        for line in lines
    ]
    env_path.write_text("\n".join(new_lines) + "\n")


@mef_bp.post("/mef/projects/<name>/pilot-up")
def pilot_up(name: str):
    """Start the pilot admin daemon for a sibling project.

    ``mise r pilot:up`` is project-scoped (``#MISE dir="{{cwd}}"``), so the
    subprocess runs inside ``<mef_root>/<name>`` rather than at the mef root.
    Refuses the host project: re-running pilot:up would rewrite ``bench.toml``
    and bounce the very admin handling this request.
    """
    # Allow any admin to start sibling pilots (no _gate() check)
    return _spawn_pilot_control(name, "pilot:up", "pilot-up")


@mef_bp.post("/mef/projects/<name>/pilot-down")
def pilot_down(name: str):
    """Stop the pilot admin daemon for a sibling project."""
    # Allow any admin to stop sibling pilots (no _gate() check)
    return _spawn_pilot_control(name, "pilot:down", "pilot-down")


@mef_bp.post("/mef/projects/<name>/pilot-open")
def pilot_open(name: str):
    """Open pilot admin in browser with auto-login for a sibling project."""
    # Allow any admin to open sibling pilots (no _gate() check)
    return _spawn_pilot_control(name, "pilot:open", "pilot-open")


def _spawn_pilot_control(name: str, task: str, label: str):
    # Skip _gate() check for pilot control - allow any admin to control sibling pilots
    if not _is_valid_project_name(name):
        return error_response("invalid_project", f"'{name}' is not a valid project name.", 422)
    if name in _PROTECTED_PROJECTS:
        return error_response("invalid_project", f"'{name}' is protected.", 422)
    if name == _host_project_name():
        return error_response(
            "mef_self_pilot_control_forbidden",
            "Refusing to control the pilot daemon of the project hosting this admin.",
            409,
        )

    target = _mef_root() / name
    if not target.is_dir() or not (target / ".miserc.toml").is_file():
        return error_response("project_not_found", f"Project '{name}' not found.", 404)

    job_id = _spawn_job(
        args=_mise_cmd([task]),
        env_extras={},
        cwd=target,
        label=label,
    )
    return jsonify({"job_id": job_id, "log_url": f"/api/v1/mef/jobs/{job_id}"}), 202


@mef_bp.post("/mef/projects/<name>/auto-login-token")
def project_auto_login_token(name: str):
    """Generate auto-login sid for a sibling project."""
    gate = _gate()
    if gate is not None:
        return gate

    if not _is_valid_project_name(name):
        return error_response("invalid_project", f"'{name}' is not a valid project name.", 422)
    if name in _PROTECTED_PROJECTS:
        return error_response("invalid_project", f"'{name}' is protected.", 422)

    target = _mef_root() / name
    if not target.is_dir() or not (target / ".miserc.toml").is_file():
        return error_response("project_not_found", f"Project '{name}' not found.", 404)

    # Calculate pilot port for target project (same formula as registry)
    pilot_port = _pilot_port_from_name(name)

    # Call target project's auto-login-token endpoint
    import requests

    config = _read_admin_config()
    password = config.admin.password if config else "admin"

    try:
        response = requests.post(
            f"http://localhost:{pilot_port}/api/v1/auto-login-token",
            json={"password": str(password)},
            timeout=5,
        )
        if response.status_code == 200:
            return jsonify(response.json())
        return error_response(
            "auto_login_failed",
            f"Failed to get auto-login token from project '{name}'.",
            response.status_code,
        )
    except requests.RequestException:
        return error_response(
            "auto_login_failed",
            f"Could not reach project '{name}' on port {pilot_port}. Ensure pilot is running.",
            503,
        )


@mef_bp.post("/mef/self/pilot-down")
def self_pilot_down():
    """Stop the pilot admin daemon serving this very request.

    Unlike ``_spawn_pilot_control``, self-control is the point here: the
    operator wants to close this instance. ``mise r pilot:down`` runs
    detached (``start_new_session=True``), so the 202 response reaches the
    browser before the daemon actually exits.
    """
    gate = _gate()
    if gate is not None:
        return gate

    job_id = _spawn_job(
        args=_mise_cmd(["pilot:down"]),
        env_extras={},
        cwd=_bench_root().parent,
        label="self-pilot-down",
    )
    return jsonify({"job_id": job_id, "log_url": f"/api/v1/mef/jobs/{job_id}"}), 202


@mef_bp.get("/mef/jobs/<job_id>")
def get_job(job_id: str):
    gate = _gate()
    if gate is not None:
        return gate

    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if job is not None:
            job = dict(job)
    if job is None:
        return error_response("mef_job_not_found", "Unknown mef job.", 404)

    process = job.pop("process", None)
    payload = {
        "job_id": job["job_id"],
        "status": job["status"],
        "exit_code": process.returncode if process and process.poll() is not None else None,
        "started_at": job["started_at"],
        "log": _tail_log(Path(job["log_path"])),
    }
    return jsonify(payload)


def _validate_new_request(data: dict, config: BenchConfig | None):
    mef_root = _mef_root()
    profile_file = _validate_profile(data, mef_root)
    if not isinstance(profile_file, Path):
        return profile_file

    engine_error = _validate_db_engine(data, profile_file)
    if engine_error is not None:
        return engine_error

    fork_error = _validate_fork(data, mef_root)
    if fork_error is not None:
        return fork_error

    directory_error = _validate_directory(data, mef_root)
    if directory_error is not None:
        return directory_error

    return _validate_overlays(data, mef_root)


def _validate_profile(data: dict, mef_root: Path):
    profile = (data.get("profile") or "").strip()
    if not profile:
        return error_response("invalid_profile", "profile is required.", 422)
    profile_file = mef_root / ".config" / "mise" / f"config.{profile}.toml"
    if not profile_file.is_file():
        return error_response("invalid_profile", f"Unknown profile '{profile}'.", 422)
    return profile_file


def _validate_db_engine(data: dict, profile_file: Path):
    engine = (data.get("db_engine") or "mariadb").strip()
    if engine not in _DB_ENGINES_ALL:
        return error_response("invalid_db_engine", f"Unknown DB engine '{engine}'.", 422)
    if engine in _DB_ENGINES_V16 and not _is_v16_compatible(_read_frappe_version(profile_file)):
        return error_response(
            "invalid_db_engine",
            f"DB engine '{engine}' requires frappe v16+ or develop.",
            422,
        )
    return None


def _validate_fork(data: dict, mef_root: Path):
    fork = (data.get("fork") or "frappe").strip()
    if fork not in _FORKS:
        return error_response("invalid_fork", f"Unknown fork '{fork}'.", 422)
    if fork != "frappe" and not (mef_root / ".config" / "mise" / f"config.{fork}.toml").is_file():
        return error_response("invalid_fork", f"Fork '{fork}' is not available in this mef checkout.", 422)
    return None


def _validate_directory(data: dict, mef_root: Path):
    directory = (data.get("directory") or "").strip()
    if not directory:
        return None
    if directory.startswith("/") or ".." in Path(directory).parts:
        return error_response(
            "invalid_directory",
            "directory must be a relative path without '..'.",
            422,
        )
    if (mef_root / directory.split("/", 1)[0]).resolve() == mef_root.resolve():
        return error_response("invalid_directory", "directory escapes the mef root.", 422)
    return None


def _validate_overlays(data: dict, mef_root: Path):
    overlays = data.get("overlays")
    if overlays is None:
        return None
    if not isinstance(overlays, list) or any(not isinstance(o, str) for o in overlays):
        return error_response("invalid_overlays", "overlays must be a list of strings.", 422)
    unknown = [o for o in overlays if o not in _available_overlays(mef_root)]
    if unknown:
        return error_response(
            "invalid_overlays",
            f"Unknown overlay(s): {', '.join(unknown)}.",
            422,
        )
    return None


def _read_frappe_version(profile_file: Path) -> str:
    try:
        for line in profile_file.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("FRAPPE_VERSION"):
                _, _, value = stripped.partition("=")
                return value.strip().strip('"').strip("'")
    except (OSError, ValueError):
        return ""
    return ""


def _is_v16_compatible(frappe_version: str) -> bool:
    return frappe_version.startswith("16") or frappe_version == "develop"


def _available_overlays(mef_root: Path) -> set[str]:
    overlays_dir = mef_root / ".config" / "overlays"
    if not overlays_dir.is_dir():
        return set()
    skip = {"dolt", "dolt_sqlite", "sqlite", "__pycache__"}
    return {
        child.name
        for child in overlays_dir.iterdir()
        if child.is_dir() and child.name not in skip
    }


# Shared with wizard.py: the headless ``mise r wizard`` task reads these
# regardless of whether it runs standalone or chained from ``mise r new``.
WIZARD_FIELD_TO_ENV = {
    "wizard_language": "WIZARD_LANG",
    "wizard_country": "WIZARD_COUNTRY",
    "wizard_timezone": "WIZARD_TZ",
    "wizard_currency": "WIZARD_CURRENCY",
    "wizard_company": "WIZARD_COMPANY",
}


def _env_extras(data: dict, field_to_env: dict) -> dict:
    extras: dict[str, str] = {}
    for field, env_var in field_to_env.items():
        if data.get(field) is None:
            continue
        value = data[field]
        if isinstance(value, list):
            value = ",".join(str(item) for item in value)
        extras[env_var] = str(value)
    return extras


def _new_env_extras(data: dict) -> dict:
    """Translate dialog field names to the ``NEW_*``/``WIZARD_*`` env vars the task consumes.

    UI fields are lowercase nouns (profile, db_engine, overlays, …); the
    headless task reads ``NEW_PROFILE``, ``NEW_DB_ENGINE`` and friends. Inputs
    reach ``mise`` via ``env=`` only — never interpolated into a shell string.
    """
    field_to_env = {
        "profile": "NEW_PROFILE",
        "fork": "NEW_FORK",
        "directory": "NEW_DIR",
        "project_name": "NEW_PROJECT_NAME",
        "db_engine": "NEW_DB_ENGINE",
        "db_password": "NEW_DB_PASSWORD",
        "db_port": "NEW_DB_PORT",
        "redis_port": "NEW_REDIS_PORT",
        "web_port": "NEW_WEB_PORT",
        "site_domain": "NEW_SITE_DOMAIN",
        "site_passwd": "NEW_SITE_PASSWD",
        "apps_preset": "APPS_PRESELECT",
        "custom_apps": "NEW_CUSTOM_APPS",
        "overlays": "NEW_OVERLAYS",
        "overwrite": "NEW_OVERWRITE",
        "new_run_setup": "NEW_RUN_SETUP",
        "new_run_wizard": "NEW_RUN_WIZARD",
        **WIZARD_FIELD_TO_ENV,
    }
    return _env_extras(data, field_to_env)


def _is_valid_project_name(name: str) -> bool:
    if not name or name in (".", "..") or "/" in name or "\\" in name:
        return False
    if name.startswith(".") or name in _PROTECTED_PROJECTS:
        return False
    return True


_DOCTOR_PORT_KEYS = {
    "bench": "WEB_PORT",
    "redis": "REDIS_PORT",
    "mailpit": "MAILPIT_UI_PORT",
}


def _run_doctor(target: Path, name: str) -> dict:
    env = _read_project_env(target)
    daemons = _pitchfork_daemon_pids(name)

    port_map = dict(_DOCTOR_PORT_KEYS)
    ports: dict[str, dict] = {}
    for daemon, env_key in port_map.items():
        raw_port = env.get(env_key)
        if not raw_port:
            continue
        ports[daemon] = _check_port(daemon, int(raw_port), daemons.get(daemon))
    pilot_pid = daemons.get("pilot-admin")
    ports["pilot-admin"] = _check_port("pilot-admin", _pilot_port_from_name(name), pilot_pid)

    frappe_ping = None
    bench = daemons.get("bench")
    site_domain, web_port = env.get("SITE_DOMAIN"), env.get("WEB_PORT")
    if bench and bench.get("status") == "running" and site_domain and web_port:
        frappe_ping = _ping_frappe(site_domain, int(web_port))

    return {"project": name, "daemons": daemons, "ports": ports, "frappe_ping": frappe_ping}


def _pitchfork_daemon_pids(name: str) -> dict[str, dict]:
    """``{daemon: {status, pid}}`` for one project's daemons, via ``pitchfork list --json``."""
    pitchfork = shutil.which("pitchfork")
    if not pitchfork:
        return {}
    try:
        result = subprocess.run(
            [pitchfork, "list", "--json"], capture_output=True, text=True, timeout=5, check=False
        )
        entries = json.loads(result.stdout or "[]")
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return {}
    return {
        entry["name"]: {"status": entry.get("status"), "pid": entry.get("pid")}
        for entry in entries
        if entry.get("namespace") == name and entry.get("name")
    }


def _check_port(daemon: str, port: int, tracked: dict | None) -> dict:
    reachable = _port_reachable(port)
    pid = tracked.get("pid") if tracked else None
    owned = _pid_owns_port(pid, port) if pid else None
    return {
        "port": port,
        "reachable": reachable,
        "owned_by_tracked_pid": owned,
        # Something answers the port, but the daemon pitchfork tracks for it either
        # isn't running or doesn't actually hold it — a stray sibling process (e.g.
        # a not-fully-reaped restart) is squatting the port instead.
        "orphan_suspected": reachable and owned is False,
    }


def _port_reachable(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _pid_owns_port(pid: int, port: int) -> bool | None:
    """True/False if determinable, None if no descendant's sockets were inspectable.

    The pitchfork-tracked PID is a supervisor (``mise run start`` -> honcho) — the
    actual listening socket belongs to a grandchild it spawned (e.g. bench's own
    werkzeug reloader worker), never the tracked PID itself. Must walk the whole
    process tree, not just the one PID, or every daemon reads as "orphaned".
    """
    try:
        proc = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return None

    inspected = False
    for candidate in (proc, *proc.children(recursive=True)):
        try:
            conns = candidate.net_connections(kind="inet")
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
        inspected = True
        if any(conn.status == psutil.CONN_LISTEN and conn.laddr and conn.laddr.port == port for conn in conns):
            return True
    return False if inspected else None


def _ping_frappe(site_domain: str, port: int) -> dict:
    try:
        response = requests.get(
            f"http://127.0.0.1:{port}/api/method/frappe.ping",
            headers={"Host": site_domain},
            timeout=2,
        )
        return {"ok": response.status_code == 200, "status": response.status_code, "body": response.text[:500]}
    except requests.RequestException as exc:
        return {"ok": False, "error": str(exc)}


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


def _mise_cmd(task_args: list[str]) -> list[str]:
    """Use ``shutil.which`` rather than the ambient ``mise`` shell function."""
    import shutil

    mise = shutil.which("mise") or "mise"
    return [mise, "r", *task_args]


def _spawn_job(args: list[str], env_extras: dict, cwd: Path, label: str) -> str:
    job_id = f"{label}-{uuid.uuid4().hex[:10]}"
    log_path = _job_log_path(job_id)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_handle = log_path.open("ab")

    # Strip MISE_* from our own inherited env before merging: this daemon runs as a
    # mise task itself (pitchfork -> `mise r pilot:admin`), so os.environ carries
    # MISE_ENV/MISE_PROJECT_ROOT/MISE_SESSION/... pinned to ITS OWN host project's
    # profile. Left in place, a spawned `mise r new` targeting a different project
    # (different profile/frappe version) inherits that pinned profile instead of
    # resolving its own — mise config becomes correct-looking (.miserc.toml is read)
    # but tool versions (python/node) and MISE_ENV-derived vars still resolve to the
    # daemon's host profile, not the target directory's.
    # Same reasoning for everything mise injected from the host project (see
    # _host_injected_env_keys): mise recomputes them for the target project anyway, so
    # dropping them is safe here and keeps a value the target leaves unset from
    # silently defaulting to the host's.
    leaked = _host_injected_env_keys()
    base_env = {
        k: v for k, v in os.environ.items() if not k.startswith("MISE_") and k not in leaked
    }
    env = {**base_env, **env_extras, "NONINTERACTIVE": "1"}

    quoted = " ".join(shlex.quote(arg) for arg in args)
    log_handle.write(f"$ cd {cwd} && {quoted}\n".encode())
    log_handle.flush()

    try:
        process = subprocess.Popen(
            args,
            cwd=str(cwd),
            env=env,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except OSError as exc:
        log_handle.write(f"spawn failed: {exc}\n".encode())
        log_handle.close()
        with _JOBS_LOCK:
            _JOBS[job_id] = _terminal_job(job_id, args, log_path, exc)
        return job_id
    finally:
        # Popen dup'd the descriptor; the handle can close in the parent.
        with contextlib.suppress(OSError):
            log_handle.close()

    with _JOBS_LOCK:
        _JOBS[job_id] = {
            "job_id": job_id,
            "command": args,
            "log_path": str(log_path),
            "started_at": time.time(),
            "status": "running",
            "process": process,
        }

    threading.Thread(target=_reap_job, args=(job_id, process), daemon=True).start()
    return job_id


def _terminal_job(job_id: str, args: list[str], log_path: Path, exc: BaseException) -> dict:
    return {
        "job_id": job_id,
        "command": args,
        "log_path": str(log_path),
        "started_at": time.time(),
        "status": "failed",
        "process": _FailedProcess(exc),
    }


def _reap_job(job_id: str, process: subprocess.Popen) -> None:
    code = process.wait()
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if job is None:
            return
        job["status"] = "success" if code == 0 else "failed"
        job["completed_at"] = time.time()
    _cleanup_jobs()


def _cleanup_jobs() -> None:
    """Reap completed jobs older than _JOB_COMPLETED_TTL, cap total at _MAX_JOBS."""
    now = time.time()
    with _JOBS_LOCK:
        # Remove old completed jobs
        to_remove = [
            jid
            for jid, job in _JOBS.items()
            if job.get("status") in ("success", "failed")
            and now - job.get("completed_at", 0) > _JOB_COMPLETED_TTL
        ]
        for jid in to_remove:
            _JOBS.pop(jid, None)

        # If still over cap, remove oldest completed jobs
        if len(_JOBS) > _MAX_JOBS:
            completed = [
                (jid, job.get("completed_at", 0))
                for jid, job in _JOBS.items()
                if job.get("status") in ("success", "failed")
            ]
            completed.sort(key=lambda x: x[1])  # oldest first
            overage = len(_JOBS) - _MAX_JOBS
            for jid, _ in completed[:overage]:
                _JOBS.pop(jid, None)


def _job_log_path(job_id: str) -> Path:
    scratch = _bench_root().parent / ".pilot" / "mef-jobs"
    return scratch / f"{job_id}.log"


def _tail_log(path: Path, max_bytes: int = 65536) -> str:
    """Read the last ~64KB of a log file. Efficient for long-running job polls."""
    if not path.is_file():
        return ""
    try:
        size = path.stat().st_size
        if size == 0:
            return ""
        # Read last max_bytes; if file smaller, read from start.
        start = max(0, size - max_bytes)
        with path.open("rb") as f:
            f.seek(start)
            if start > 0:
                f.readline()  # discard partial line
            data = f.read()
        return data.decode("utf-8", errors="replace")
    except OSError:
        return ""


class _FailedProcess:
    """Stand-in for Popen when spawning failed before exec."""

    returncode = 1

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    def poll(self) -> int:
        return self.returncode

    def wait(self) -> int:
        return self.returncode
