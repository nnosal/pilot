"""Setup wizard status/trigger for sites on the bench pilot is running against."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from flask import current_app, jsonify

from admin.backend.api.v1.mef import _mise_cmd, _spawn_job
from admin.backend.api.v1.sites import sites_bp
from admin.backend.api.v1.sites.shared import site_name, site_not_found
from admin.backend.middleware import require_scope
from pilot.internal.site_paths import site_exists

# bench execute boots the full Frappe framework each call, needs its own timeout.
_BENCH_EXECUTE_TIMEOUT_SECONDS = 15


@sites_bp.get("/<name>/setup-status")
@require_scope(site_name)
def setup_status(name: str):
    """Report whether the site's Frappe setup wizard (System Settings.setup_complete) has run."""
    bench_root = Path(current_app.config["BENCH_ROOT"])
    if not site_exists(bench_root, name):
        return site_not_found()
    return jsonify({"setup_complete": _read_setup_complete(bench_root, name)})


def _read_setup_complete(bench_root: Path, name: str) -> bool | None:
    bench = shutil.which("bench") or "bench"
    try:
        result = subprocess.run(
            [
                bench,
                "--site",
                name,
                "execute",
                "frappe.db.get_single_value",
                "--args",
                '["System Settings", "setup_complete"]',
            ],
            cwd=str(bench_root),
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


@sites_bp.post("/<name>/wizard")
@require_scope(site_name)
def run_wizard(name: str):
    """Spawn the headless setup wizard mise task targeted at this site.

    ``BENCH_ROOT`` is the bench dir (mef's ``<project>/app``), so the mise
    task runs one level up with ``PROJECT_NAME`` set to the bench dir's name
    (normally ``app``) — mirroring how mef's own sibling-project wizard is
    spawned in ``admin/backend/api/v1/registry.py``.
    """
    bench_root = Path(current_app.config["BENCH_ROOT"])
    if not site_exists(bench_root, name):
        return site_not_found()
    job_id = _spawn_job(
        args=_mise_cmd(["wizard"]),
        env_extras={"NONINTERACTIVE": "1", "SITE_DOMAIN": name, "PROJECT_NAME": bench_root.name},
        cwd=bench_root.parent,
        label="wizard",
    )
    return jsonify({"job_id": job_id, "log_url": f"/api/v1/mef/jobs/{job_id}"}), 202
