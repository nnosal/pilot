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


def test_read_output_strips_ansi_color_codes_from_url(tmp_path: Path) -> None:
    """slim colors its stdout; a bare \\S+ regex would swallow the trailing
    escape sequence into the captured URL (e.g. 'https://host\\x1b[m')."""
    session = slim.SlimLoginSession()
    session._process = _fake_process(
        ["\x1b[38;5;2mhttps://app.slim.sh/cli-auth?code=abc123\x1b[m"], returncode=1
    )

    with patch.object(slim, "_STATE_FILE", tmp_path / "slim.json"), patch.object(slim, "_STATE_DIR", tmp_path):
        session._read_output()

    assert session.url == "https://app.slim.sh/cli-auth?code=abc123"


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


def _site_dir(bench_root: Path, name: str) -> Path:
    site_dir = bench_root / "sites" / name
    site_dir.mkdir(parents=True)
    return site_dir


def test_create_alias_symlinks_fixed_localhost_name_to_site(tmp_path: Path) -> None:
    """slim's local relay rewrites the Host header to localhost:<port> when
    forwarding — the alias must be literally "localhost", not the public
    slim.show hostname, or frappe.init() 404s with "localhost does not exist"."""
    _site_dir(tmp_path, "titi.localhost")
    session = slim.SlimShareSession(tmp_path, "titi.localhost", 8000)
    session.url = "https://abcd1234.slim.show"

    session._create_alias()

    alias = tmp_path / "sites" / "localhost"
    assert alias.is_symlink()
    assert alias.resolve() == (tmp_path / "sites" / "titi.localhost").resolve()
    assert session._alias == "localhost"


def test_create_alias_replaces_existing_symlink(tmp_path: Path) -> None:
    """Sharing a different site while another share's alias is still around
    (e.g. a previous session that didn't clean up) must repoint it, not skip."""
    _site_dir(tmp_path, "titi.localhost")
    _site_dir(tmp_path, "titi2.localhost")
    (tmp_path / "sites" / "localhost").symlink_to("titi.localhost")
    session = slim.SlimShareSession(tmp_path, "titi2.localhost", 8000)
    session.url = "https://abcd1234.slim.show"

    session._create_alias()

    alias = tmp_path / "sites" / "localhost"
    assert alias.resolve() == (tmp_path / "sites" / "titi2.localhost").resolve()


def test_create_alias_refuses_to_touch_a_real_localhost_site(tmp_path: Path) -> None:
    _site_dir(tmp_path, "titi.localhost")
    _site_dir(tmp_path, "localhost")
    session = slim.SlimShareSession(tmp_path, "titi.localhost", 8000)
    session.url = "https://abcd1234.slim.show"

    session._create_alias()

    assert (tmp_path / "sites" / "localhost").is_dir()
    assert not (tmp_path / "sites" / "localhost").is_symlink()
    assert session._alias is None


def test_share_read_output_strips_ansi_before_creating_alias(tmp_path: Path) -> None:
    _site_dir(tmp_path, "titi.localhost")
    session = slim.SlimShareSession(tmp_path, "titi.localhost", 8000)
    session._process = _fake_process(
        ["\x1b[38;5;2mhttps://brief-fawn.slim.show\x1b[m  \x1b[2mlocalhost:8000\x1b[m"], returncode=1
    )

    session._read_output()

    assert session.url == "https://brief-fawn.slim.show"


def test_share_read_output_ends_stopped_and_cleans_up_alias(tmp_path: Path) -> None:
    _site_dir(tmp_path, "titi.localhost")
    session = slim.SlimShareSession(tmp_path, "titi.localhost", 8000)
    session._process = _fake_process(
        ["Sharing localhost:8000 at:", "", "  https://abcd1234.slim.show", ""], returncode=0
    )

    session._read_output()

    alias = tmp_path / "sites" / "localhost"
    assert session.status == "stopped"
    assert not alias.exists()  # removed once the process (and tunnel) ends


def test_share_stop_terminates_process_and_removes_alias(tmp_path: Path) -> None:
    _site_dir(tmp_path, "titi.localhost")
    session = slim.SlimShareSession(tmp_path, "titi.localhost", 8000)
    session._alias = "localhost"
    alias = tmp_path / "sites" / session._alias
    alias.symlink_to("titi.localhost")
    process = MagicMock()
    process.poll.return_value = None
    session._process = process

    session.stop()

    process.terminate.assert_called_once()
    assert session.status == "stopped"
    assert not alias.exists()


def test_share_start_reuses_in_flight_session_per_site(tmp_path: Path) -> None:
    slim.SlimShareSession._sessions = {}
    with patch.object(slim.SlimShareSession, "_spawn", autospec=True) as spawn:
        first = slim.SlimShareSession.start(tmp_path, "titi.localhost", 8000)
        first.status = "live"
        second = slim.SlimShareSession.start(tmp_path, "titi.localhost", 8000)
        third = slim.SlimShareSession.start(tmp_path, "titi2.localhost", 8000)

    assert first is second
    assert first is not third
    assert spawn.call_count == 2
    slim.SlimShareSession._sessions = {}
