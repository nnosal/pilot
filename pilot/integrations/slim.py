from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
from pathlib import Path

_STATE_DIR = Path.home() / ".local" / "share" / "pilot"
_STATE_FILE = _STATE_DIR / "slim.json"
_URL_RE = re.compile(r"https://\S+")
_LOGIN_TIMEOUT_SECONDS = 120


class SlimLoginSession:
    """Tracks the one in-flight ``slim login`` subprocess for this machine.

    Login is a machine-wide, single-account operation (slim's own credentials
    live outside any bench), so a single shared session is the right shape —
    a second login attempt while one is running just returns the same session.
    """

    _lock = threading.Lock()
    _current: "SlimLoginSession | None" = None

    def __init__(self) -> None:
        self.status = "starting"  # starting -> waiting -> success | failed
        self.url: str | None = None
        self.message = ""
        self._process: subprocess.Popen | None = None

    @classmethod
    def start(cls) -> "SlimLoginSession":
        with cls._lock:
            if cls._current is not None and cls._current.status in ("starting", "waiting"):
                return cls._current
            session = cls()
            cls._current = session
        session._spawn()
        return session

    @classmethod
    def current(cls) -> "SlimLoginSession | None":
        return cls._current

    def snapshot(self) -> dict:
        return {"status": self.status, "url": self.url, "message": self.message}

    def _spawn(self) -> None:
        slim = shutil.which("slim")
        if not slim:
            self.status = "failed"
            self.message = "slim is not installed."
            return
        self._process = subprocess.Popen(
            [slim, "login"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
        )
        threading.Thread(target=self._read_output, daemon=True).start()

    def _read_output(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        for raw_line in process.stdout:
            line = raw_line.strip()
            if not line:
                continue
            self.message = line
            match = _URL_RE.search(line)
            if match and not self.url:
                self.url = match.group(0)
                self.status = "waiting"
        try:
            code = process.wait(timeout=_LOGIN_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            self.status = "failed"
            self.message = "Login timed out waiting for authentication."
            return
        if code == 0:
            self.status = "success"
            _write_connected(True)
        else:
            self.status = "failed"


def is_connected() -> bool:
    try:
        return bool(json.loads(_STATE_FILE.read_text()).get("connected", False))
    except (FileNotFoundError, ValueError):
        return False


def _write_connected(value: bool) -> None:
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    _STATE_FILE.write_text(json.dumps({"connected": value}))


def logout() -> None:
    slim = shutil.which("slim")
    if slim:
        subprocess.run([slim, "logout"], capture_output=True, timeout=10, check=False)
    _write_connected(False)
