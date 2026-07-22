from __future__ import annotations

from pathlib import Path

from flask import Blueprint, current_app, jsonify, request

from admin.backend.api.responses import error_response, no_content_response
from pilot.integrations.slim import SlimLoginSession, is_connected, logout

share_bp = Blueprint("share", __name__)

_NGROK_KEY = "NGROK_AUTHTOKEN"


def _project_env_path() -> Path:
    bench_root = Path(current_app.config["BENCH_ROOT"])
    return bench_root.parent / ".env"


def _read_env_var(env_path: Path, key: str) -> str:
    if not env_path.exists():
        return ""
    for line in env_path.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith(key) and stripped[len(key) :].lstrip().startswith("="):
            return stripped.split("=", 1)[1].strip()
    return ""


def _write_env_var(env_path: Path, key: str, value: str) -> None:
    lines = env_path.read_text().splitlines() if env_path.exists() else []
    if any(line.strip().startswith(key) for line in lines):
        new_lines = [f"{key} = {value}" if line.strip().startswith(key) else line for line in lines]
        env_path.write_text("\n".join(new_lines) + "\n")
    else:
        with env_path.open("a") as f:
            f.write(f"{key} = {value}\n")


def _delete_env_var(env_path: Path, key: str) -> None:
    if not env_path.exists():
        return
    lines = [line for line in env_path.read_text().splitlines() if not line.strip().startswith(key)]
    env_path.write_text("\n".join(lines) + ("\n" if lines else ""))


def _mask_token(token: str) -> str:
    if len(token) <= 8:
        return token
    return f"{token[:4]}{'x' * 8}{token[-4:]}"


@share_bp.get("/ngrok")
def ngrok_status():
    token = _read_env_var(_project_env_path(), _NGROK_KEY)
    return jsonify({"connected": bool(token), "token_preview": _mask_token(token) if token else ""})


@share_bp.put("/ngrok")
def ngrok_connect():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return error_response("malformed_request", "Expected a JSON object.", 400)
    token = (data.get("token") or "").strip()
    if not token:
        return error_response("token_required", "An ngrok authtoken is required.", 422)
    _write_env_var(_project_env_path(), _NGROK_KEY, token)
    return jsonify({"connected": True, "token_preview": _mask_token(token)})


@share_bp.delete("/ngrok")
def ngrok_disconnect():
    _delete_env_var(_project_env_path(), _NGROK_KEY)
    return no_content_response()


@share_bp.get("/slim")
def slim_status():
    return jsonify({"connected": is_connected()})


@share_bp.post("/slim/login")
def slim_login():
    return jsonify(SlimLoginSession.start().snapshot())


@share_bp.get("/slim/login")
def slim_login_status():
    session = SlimLoginSession.current()
    if session is None:
        return error_response("no_login_in_progress", "No slim login is in progress.", 404)
    return jsonify(session.snapshot())


@share_bp.delete("/slim")
def slim_disconnect():
    logout()
    return no_content_response()
