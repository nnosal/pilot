"""Tests for pilot.integrations.slim: the slim login session and connected marker."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from pilot.integrations import slim


def _fake_process(lines: list[str], returncode: int) -> MagicMock:
    process = MagicMock()
    process.stdout = iter(lines)
    process.wait.return_value = returncode
    return process


def test_read_output_captures_url_and_marks_waiting(tmp_path: Path) -> None:
    session = slim.SlimLoginSession()
    session._process = _fake_process(
        ["To log in, open this URL in your browser:", "", "  https://app.slim.sh/cli-auth?code=abc123", ""],
        returncode=1,
    )

    with patch.object(slim, "_STATE_FILE", tmp_path / "slim.json"), patch.object(slim, "_STATE_DIR", tmp_path):
        session._read_output()

    assert session.url == "https://app.slim.sh/cli-auth?code=abc123"
    assert session.status == "failed"


def test_read_output_success_persists_connected(tmp_path: Path) -> None:
    session = slim.SlimLoginSession()
    session._process = _fake_process(["https://app.slim.sh/cli-auth?code=abc123", "Logged in as dev@example.com"], returncode=0)

    with patch.object(slim, "_STATE_FILE", tmp_path / "slim.json"), patch.object(slim, "_STATE_DIR", tmp_path):
        session._read_output()

    assert session.status == "success"
    assert json.loads((tmp_path / "slim.json").read_text()) == {"connected": True}


def test_is_connected_reflects_state_file(tmp_path: Path) -> None:
    state_file = tmp_path / "slim.json"
    with patch.object(slim, "_STATE_FILE", state_file):
        assert slim.is_connected() is False
        state_file.write_text(json.dumps({"connected": True}))
        assert slim.is_connected() is True


def test_logout_runs_cli_and_clears_marker(tmp_path: Path) -> None:
    state_file = tmp_path / "slim.json"
    state_file.write_text(json.dumps({"connected": True}))

    with (
        patch.object(slim, "_STATE_FILE", state_file),
        patch.object(slim, "_STATE_DIR", tmp_path),
        patch("shutil.which", return_value="/usr/local/bin/slim"),
        patch("subprocess.run") as run,
    ):
        slim.logout()

    run.assert_called_once()
    assert json.loads(state_file.read_text()) == {"connected": False}


def test_start_reuses_in_flight_session() -> None:
    slim.SlimLoginSession._current = None
    with patch.object(slim.SlimLoginSession, "_spawn", autospec=True) as spawn:
        first = slim.SlimLoginSession.start()
        first.status = "waiting"
        second = slim.SlimLoginSession.start()

    assert first is second
    spawn.assert_called_once()
    slim.SlimLoginSession._current = None
