from __future__ import annotations

from pathlib import Path

from flask import current_app, jsonify

from admin.backend.api.responses import error_response, no_content_response
from admin.backend.api.v1.sites import sites_bp
from admin.backend.api.v1.sites.shared import site_name, site_not_found
from admin.backend.middleware import require_scope
from pilot.core.bench import Bench
from pilot.integrations.slim import SlimShareSession
from pilot.internal.site_paths import site_exists


@sites_bp.post("/<name>/share")
@require_scope(site_name)
def start_share(name: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    if not site_exists(bench_root, name):
        return site_not_found()
    web_port = Bench(bench_root).config.http_port
    session = SlimShareSession.start(bench_root, name, web_port)
    return jsonify(session.snapshot())


@sites_bp.get("/<name>/share")
@require_scope(site_name)
def share_status(name: str):
    session = SlimShareSession.current(name)
    if session is None:
        return error_response("no_share_in_progress", "No share is in progress for this site.", 404)
    return jsonify(session.snapshot())


@sites_bp.delete("/<name>/share")
@require_scope(site_name)
def stop_share(name: str):
    session = SlimShareSession.current(name)
    if session is None:
        return error_response("no_share_in_progress", "No share is in progress for this site.", 404)
    session.stop()
    return no_content_response()
