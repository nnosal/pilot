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
"""

from __future__ import annotations

import http.client
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from flask import Blueprint, jsonify

from admin.backend.api.v1.mef import (
    _gate,
    _host_project_name,
    _iter_mef_projects,
    _mef_root,
    _read_frappe_version,
    _read_project_env,
)

registry_bp = Blueprint("registry", __name__)

_PING_TIMEOUT_SECONDS = 0.5
_PARALLEL_PING_WORKERS = 8

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
    entries: list[dict | None] = [None] * len(projects)
    worker_count = max(1, min(_PARALLEL_PING_WORKERS, len(projects))) if projects else 1
    with ThreadPoolExecutor(max_workers=worker_count) as pool:
        futures = {
            pool.submit(_project_entry, child, mef_root, host_name): idx
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
                    "ports": {"web": None, "db": None, "redis": None},
                    "pilot_port": None,
                    "pilot_running": False,
                    "sites_count": 0,
                    "apps_count": 0,
                }
    return {"mef_root": str(mef_root), "projects": entries}


def _project_entry(child: Path, mef_root: Path, host_name: str) -> dict:
    env = _read_project_env(child)
    profile = _read_miserc_profile(child / ".miserc.toml")
    frappe_version = _read_frappe_version(_profile_path(mef_root, profile))
    pilot_port = _pilot_port_from_name(child.name)
    return {
        "name": child.name,
        "is_self": child.name == host_name,
        "profile": profile,
        "frappe_version": frappe_version,
        "db_engine": env.get("DB_ENGINE", ""),
        "ports": {
            "web": _port_int(env.get("WEB_PORT")),
            "db": _port_int(env.get("DB_PORT")),
            "redis": _port_int(env.get("REDIS_PORT")),
        },
        "pilot_port": pilot_port,
        "pilot_running": _ping_pilot_health(pilot_port),
        "sites_count": _count_sites(child / "app" / "sites"),
        "apps_count": _count_apps(child / "app" / "apps"),
    }


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
