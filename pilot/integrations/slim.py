from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
from pathlib import Path
from typing import ClassVar

_STATE_DIR = Path.home() / ".local" / "share" / "pilot"
_STATE_FILE = _STATE_DIR / "slim.json"
# slim colors its stdout: a URL is wrapped as "\x1b[...mhttps://host\x1b[m", and a bare
# \S+ swallows that trailing escape sequence into the captured string. Strip ANSI SGR
# sequences before matching, or the captured URL (and any alias derived from it) is
# corrupted with escape bytes.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
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
            line = _ANSI_RE.sub("", raw_line).strip()
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


class SlimShareSession:
    """Tracks one ``slim share`` tunnel for one site in one bench.

    slim's local relay does not preserve the public Host header (``xxx.slim.show``)
    when forwarding to the local port — it rewrites it to ``localhost:<port>``, so
    frappe.init() always resolves the request to a site directory literally named
    ``localhost``. This creates that one alias as a plain symlink (the same
    mechanism ``bench setup add-domain`` relies on for real custom domains,
    without going through pilot's own SiteDomains, which verifies DNS points at
    this server — never true for a slim.show relay) and removes it when the
    tunnel stops. Only one site can be usefully shared at a time per bench:
    starting a share for a different site replaces the alias.
    """

    _lock = threading.Lock()
    _sessions: ClassVar[dict[str, "SlimShareSession"]] = {}

    def __init__(self, bench_root: Path, site: str, web_port: int) -> None:
        self.bench_root = bench_root
        self.site = site
        self.web_port = web_port
        self.status = "starting"  # starting -> live -> stopped | failed
        self.url: str | None = None
        self.message = ""
        self._process: subprocess.Popen | None = None
        self._alias: str | None = None

    @classmethod
    def start(cls, bench_root: Path, site: str, web_port: int) -> "SlimShareSession":
        with cls._lock:
            existing = cls._sessions.get(site)
            if existing is not None and existing.status in ("starting", "live"):
                return existing
            session = cls(bench_root, site, web_port)
            cls._sessions[site] = session
        session._spawn()
        return session

    @classmethod
    def current(cls, site: str) -> "SlimShareSession | None":
        return cls._sessions.get(site)

    def snapshot(self) -> dict:
        return {"status": self.status, "url": self.url, "message": self.message}

    def stop(self) -> None:
        self.status = "stopped"
        if self._process is not None and self._process.poll() is None:
            self._process.terminate()
        self._remove_alias()

    def _spawn(self) -> None:
        slim = shutil.which("slim")
        if not slim:
            self.status = "failed"
            self.message = "slim is not installed."
            return
        self._process = subprocess.Popen(
            [slim, "share", "--port", str(self.web_port)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        threading.Thread(target=self._read_output, daemon=True).start()

    def _read_output(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        for raw_line in process.stdout:
            line = _ANSI_RE.sub("", raw_line).strip()
            if not line:
                continue
            self.message = line
            if self.url is None:
                match = _URL_RE.search(line)
                if match:
                    self.url = match.group(0)
                    self._create_alias()
                    if self.status != "stopped":
                        self.status = "live"
        code = process.wait()
        if self.status != "stopped":
            self.status = "failed" if code != 0 else "stopped"
        self._remove_alias()

    def _create_alias(self) -> None:
        target = self.bench_root / "sites" / self.site
        if not target.is_dir():
            return
        alias_path = self.bench_root / "sites" / "localhost"
        if alias_path.exists() and not alias_path.is_symlink():
            # A real site directory named "localhost" — never overwrite site data.
            self.message = "A site directory literally named 'localhost' already exists; cannot share."
            return
        if alias_path.is_symlink():
            alias_path.unlink()
        alias_path.symlink_to(self.site)
        self._alias = "localhost"

    def _remove_alias(self) -> None:
        if not self._alias:
            return
        alias_path = self.bench_root / "sites" / self._alias
        if alias_path.is_symlink():
            alias_path.unlink()
        self._alias = None


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
