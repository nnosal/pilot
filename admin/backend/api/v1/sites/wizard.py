"""Setup wizard status/trigger for sites on the bench pilot is running against."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from flask import current_app, jsonify, request

from admin.backend.api.responses import error_response
from admin.backend.api.v1.mef import WIZARD_FIELD_TO_ENV, _env_extras, _mise_cmd, _spawn_job
from admin.backend.api.v1.sites import sites_bp
from admin.backend.api.v1.sites.shared import site_name, site_not_found
from admin.backend.middleware import require_scope
from pilot.internal.site_paths import site_exists

# bench execute boots the full Frappe framework each call, needs its own timeout.
_BENCH_EXECUTE_TIMEOUT_SECONDS = 15

# language/country/currency reference data only changes on a framework
# upgrade + migrate; cache it for the life of the daemon process instead of
# paying 3x "bench execute boots Frappe" (~1-2s each) on every dialog open.
_WIZARD_OPTIONS_CACHE: dict[str, dict] = {}


@sites_bp.get("/<name>/setup-status")
@require_scope(site_name)
def setup_status(name: str):
    """Report whether the site's Frappe setup wizard (System Settings.setup_complete) has run."""
    bench_root = Path(current_app.config["BENCH_ROOT"])
    if not site_exists(bench_root, name):
        return site_not_found()
    return jsonify({"setup_complete": _read_setup_complete(bench_root, name)})


def _bench_execute(bench_root: Path, name: str, method: str, args_json: str) -> subprocess.CompletedProcess:
    bench = shutil.which("bench") or "bench"
    return subprocess.run(
        [bench, "--site", name, "execute", method, "--args", args_json],
        cwd=str(bench_root),
        capture_output=True,
        text=True,
        timeout=_BENCH_EXECUTE_TIMEOUT_SECONDS,
        check=False,
    )


def _run_db_method(bench_root: Path, name: str, method: str, args_json: str) -> subprocess.CompletedProcess:
    """Call `frappe.db.<method>(*args)` directly via the site's venv Python.

    `bench execute frappe.db.<method>` resolves the dotted path through
    `frappe.get_attr` -> `importlib.import_module`, and `frappe.db` isn't a real
    importable submodule — only a runtime attribute `frappe.connect()` sets up. That
    fails on Frappe v12 (confirmed: v12's `execute` command has no fallback). Frappe
    v13+ added a fallback that `eval()`s the expression instead, which happens to work
    for a runtime attribute — but relying on that is version-fragile. Evaluating
    `frappe.db.<method>` as a plain attribute access here sidesteps the import
    machinery entirely, so it works identically on every supported version.
    """
    python = bench_root / "env" / "bin" / "python"
    program = (
        "import sys, json, frappe\n"
        "frappe.init(site=sys.argv[1], sites_path='.')\n"
        "frappe.connect()\n"
        f"result = frappe.db.{method}(*json.loads(sys.argv[2]))\n"
        "frappe.db.commit()\n"
        "sys.stdout.write(json.dumps(result))\n"
    )
    return subprocess.run(
        [str(python), "-c", program, name, args_json],
        cwd=str(bench_root / "sites"),
        capture_output=True,
        text=True,
        timeout=_BENCH_EXECUTE_TIMEOUT_SECONDS,
        check=False,
    )


def _run_db_method_kwargs(bench_root: Path, name: str, method: str, kwargs_json: str) -> subprocess.CompletedProcess:
    """Same rationale as `_run_db_method`, for a `frappe.db.<method>(**kwargs)` call."""
    python = bench_root / "env" / "bin" / "python"
    program = (
        "import sys, json, frappe\n"
        "frappe.init(site=sys.argv[1], sites_path='.')\n"
        "frappe.connect()\n"
        f"result = frappe.db.{method}(**json.loads(sys.argv[2]))\n"
        "frappe.db.commit()\n"
        "sys.stdout.write(json.dumps(result))\n"
    )
    return subprocess.run(
        [str(python), "-c", program, name, kwargs_json],
        cwd=str(bench_root / "sites"),
        capture_output=True,
        text=True,
        timeout=_BENCH_EXECUTE_TIMEOUT_SECONDS,
        check=False,
    )


def _read_setup_complete(bench_root: Path, name: str) -> bool | None:
    try:
        result = _run_db_method(bench_root, name, "get_single_value", '["System Settings", "setup_complete"]')
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    lines = [line for line in result.stdout.strip().splitlines() if line.strip()]
    if not lines:
        return None
    try:
        return bool(json.loads(lines[-1]))
    except ValueError:
        return None


def _reset_wizard_flags(bench_root: Path, name: str) -> subprocess.CompletedProcess:
    """Undo everything ``disable_future_access()`` (frappe/desk/page/setup_wizard/
    setup_wizard.py) sets when the wizard completes, so it can be run again from
    scratch.

    Three things get flipped there, all three must go back:

    - ``desktop:home_page`` default -> back to ``"setup-wizard"``. This is what
      actually drives the desk redirect on open (``boot.py``'s ``add_home_page``
      resolves ``bootinfo.home_page`` from this default): resetting only
      ``setup_complete`` left the desk boot fine but ``home_page`` still pointing
      at the normal desktop, so opening the site landed on the last module
      instead of the wizard — confirmed live, this was the actual reported bug.
    - ``System Settings.setup_complete`` -> 0. ``setup_complete()`` gates on this
      on every version. ``frappe.db.set_single_value`` is the only supported way
      to write a Single doctype field since v15 (``set_value`` was dropped for
      Singles); v12 has the opposite problem — ``set_single_value`` doesn't exist
      yet, only ``set_value`` (confirmed live: v12 raised ``AttributeError`` on
      ``set_single_value``). Both covered via a ``hasattr`` fallback.
    - ``Installed Application.is_setup_complete`` -> 0. v13+ additionally short
      circuits via ``frappe.is_setup_complete()``, which reads this per-app flag —
      resetting only ``System Settings`` left that guard tripped there too.
      Neither the function nor that doctype field exist on v12 (confirmed
      empirically), so it's only reset when the hook is present.
    """
    python = bench_root / "env" / "bin" / "python"
    program = (
        "import sys, frappe\n"
        "frappe.init(site=sys.argv[1], sites_path='.')\n"
        "frappe.connect()\n"
        "frappe.db.set_default('desktop:home_page', 'setup-wizard')\n"
        "if hasattr(frappe.db, 'set_single_value'):\n"
        "    frappe.db.set_single_value('System Settings', 'setup_complete', 0)\n"
        "else:\n"
        "    frappe.db.set_value('System Settings', 'System Settings', 'setup_complete', 0)\n"
        "if hasattr(frappe, 'is_setup_complete'):\n"
        "    frappe.db.set_value('Installed Application', {'is_setup_complete': 1}, 'is_setup_complete', 0)\n"
        "frappe.db.commit()\n"
    )
    return subprocess.run(
        [str(python), "-c", program, name],
        cwd=str(bench_root / "sites"),
        capture_output=True,
        text=True,
        timeout=_BENCH_EXECUTE_TIMEOUT_SECONDS,
        check=False,
    )


@sites_bp.post("/<name>/wizard/reset")
@require_scope(site_name)
def reset_wizard(name: str):
    """Reset the site's setup-wizard flags so it can be run again. See
    ``_reset_wizard_flags`` for what gets reset and why.

    ``get_setup_wizard_completed_apps()`` (frappe/core/doctype/installed_applications)
    reads the "Installed Applications" doc through ``frappe.client_cache``, a
    Redis-backed cache that survives across ``bench execute`` processes and is
    NOT cleared by ``bench clear-cache`` (that only clears the process-local
    half of the client cache — verified empirically). Without invalidating it
    here, a re-run still sees the pre-reset doc and silently skips every app's
    stage as "already complete", so the flag it never re-derives from stays 0
    forever. Key format is ``frappe.get_document_cache_key`` — internal/
    undocumented, but there's no public API for this specific invalidation.
    Only relevant on v13+ (client_cache doesn't exist on v12 either); the
    subprocess result below is intentionally not checked, same as before.
    """
    bench_root = Path(current_app.config["BENCH_ROOT"])
    if not site_exists(bench_root, name):
        return site_not_found()
    try:
        result = _reset_wizard_flags(bench_root, name)
        if result.returncode != 0:
            return error_response("reset_failed", result.stderr.strip() or "reset failed", 502)
        cache_key = "document_cache::Installed Applications::Installed Applications"
        bench = shutil.which("bench") or "bench"
        subprocess.run(
            [
                bench,
                "--site",
                name,
                "execute",
                "frappe.client_cache.delete_value",
                "--kwargs",
                f'{{"key": "{cache_key}", "shared": True}}',
            ],
            cwd=str(bench_root),
            capture_output=True,
            timeout=_BENCH_EXECUTE_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as caught:
        return error_response("reset_failed", str(caught), 502)
    subprocess.run(
        [shutil.which("bench") or "bench", "--site", name, "clear-cache"],
        cwd=str(bench_root),
        capture_output=True,
        timeout=_BENCH_EXECUTE_TIMEOUT_SECONDS,
        check=False,
    )
    return jsonify({"setup_complete": False})


@sites_bp.get("/<name>/wizard/options")
@require_scope(site_name)
def wizard_options(name: str):
    """Language/country/timezone/currency choices, sourced from Frappe itself.

    Same data the browser-based setup wizard populates its own selects from:
    ``frappe.geo.country_info.get_country_timezone_info`` (whitelisted, used by
    frappe/public/js/frappe/views/setup-wizard.js) for country -> timezones/
    default currency, plus the ``Language``/``Currency`` doctypes for the
    other two selects. ``Language.language_name`` (not the ``en``/``fr`` code)
    is what ``get_language_code()`` looks up when ``setup_complete()`` runs.
    """
    bench_root = Path(current_app.config["BENCH_ROOT"])
    if not site_exists(bench_root, name):
        return site_not_found()
    cache_key = str(bench_root)
    if cache_key not in _WIZARD_OPTIONS_CACHE:
        options = _fetch_wizard_options(bench_root, name)
        if options is None:
            return error_response("options_failed", "Could not load wizard options from the site", 502)
        _WIZARD_OPTIONS_CACHE[cache_key] = options
    return jsonify(_WIZARD_OPTIONS_CACHE[cache_key])


def _read_json_result(result: subprocess.CompletedProcess) -> object | None:
    if result.returncode != 0:
        return None
    lines = [line for line in result.stdout.strip().splitlines() if line.strip()]
    if not lines:
        return None
    try:
        return json.loads(lines[-1])
    except ValueError:
        return None


def _fetch_wizard_options(bench_root: Path, name: str) -> dict | None:
    # get_country_timezone_info is a real importable module (frappe.geo.country_info),
    # so bench execute resolves it fine on every version - unlike frappe.db.get_list,
    # which needs _run_db_method_kwargs (see its docstring).
    country_timezone_info = _read_json_result(
        _bench_execute_kwargs(bench_root, name, "frappe.geo.country_info.get_country_timezone_info", "{}")
    )
    currencies = _read_json_result(
        _run_db_method_kwargs(
            bench_root, name, "get_list", '{"doctype": "Currency", "fields": ["name"], "limit_page_length": 0, "order_by": "name"}'
        )
    )
    languages = _read_json_result(
        _run_db_method_kwargs(
            bench_root,
            name,
            "get_list",
            '{"doctype": "Language", "fields": ["language_name"], "limit_page_length": 0, "order_by": "language_name"}',
        )
    )
    if country_timezone_info is None or currencies is None or languages is None:
        return None
    country_info = country_timezone_info["country_info"]
    return {
        "languages": sorted({row["language_name"] for row in languages}),
        "countries": sorted(country_info.keys()),
        "currencies": sorted({row["name"] for row in currencies}),
        "all_timezones": country_timezone_info["all_timezones"],
        "country_timezones": {country: data.get("timezones", []) for country, data in country_info.items()},
        "country_currency": {country: data.get("currency") for country, data in country_info.items()},
    }


def _bench_execute_kwargs(bench_root: Path, name: str, method: str, kwargs_json: str) -> subprocess.CompletedProcess:
    bench = shutil.which("bench") or "bench"
    return subprocess.run(
        [bench, "--site", name, "execute", method, "--kwargs", kwargs_json],
        cwd=str(bench_root),
        capture_output=True,
        text=True,
        timeout=_BENCH_EXECUTE_TIMEOUT_SECONDS,
        check=False,
    )


@sites_bp.post("/<name>/wizard")
@require_scope(site_name)
def run_wizard(name: str):
    """Spawn the headless setup wizard mise task targeted at this site.

    ``BENCH_ROOT`` is the bench dir (mef's ``<project>/app``), so the mise
    task runs one level up with ``PROJECT_NAME`` set to the bench dir's name
    (normally ``app``) — mirroring how mef's own sibling-project wizard is
    spawned in ``admin/backend/api/v1/registry.py``.

    ``name`` is passed as the task's positional arg, not just ``SITE_DOMAIN``
    env: mise reloads the project's ``.env`` (``_.file`` in config.toml)
    before the task runs, which silently overwrites an env-only override with
    the project's default site — the wizard would then always target that
    default site regardless of which site was actually requested.
    """
    bench_root = Path(current_app.config["BENCH_ROOT"])
    if not site_exists(bench_root, name):
        return site_not_found()
    data = request.get_json(silent=True) or {}
    env_extras = {
        "NONINTERACTIVE": "1",
        "SITE_DOMAIN": name,
        "PROJECT_NAME": bench_root.name,
        **_env_extras(data, WIZARD_FIELD_TO_ENV),
    }
    job_id = _spawn_job(
        args=_mise_cmd(["wizard", name]),
        env_extras=env_extras,
        cwd=bench_root.parent,
        label="wizard",
    )
    return jsonify({"job_id": job_id, "log_url": f"/api/v1/mef/jobs/{job_id}"}), 202
