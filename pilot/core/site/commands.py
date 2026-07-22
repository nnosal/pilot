from __future__ import annotations

import contextlib
import os
from typing import TYPE_CHECKING

from pilot.exceptions import BenchError
from pilot.utils import run_command

if TYPE_CHECKING:
    from pilot.core.site import Site


class SiteCommands:
    def __init__(self, site: "Site") -> None:
        self.site = site

    def create(self, db_type: str | None = None) -> None:
        if (
            not isinstance(self.site.config.admin_password, str)
            or not self.site.config.admin_password.strip()
        ):
            raise BenchError("Site Administrator password must not be empty.")
        cmd = self.site._frappe_call(
            "frappe",
            "--site",
            self.site.config.name,
            "new-site",
            self.site.config.name,
        )
        cmd += ["--admin-password", self.site.config.admin_password]
        cmd += self.db_args(db_type or self.site.bench.config.db_type)
        run_command(cmd, cwd=self.site.bench.sites_path, stream_output=True, env=self._mysql_cli_env())
        self._enable_developer_mode()

    def _enable_developer_mode(self) -> None:
        """A fresh site defaults to developer_mode=0, which routes
        frappe.get_hooks() through a Redis-cached path (frappe/__init__.py:
        get_hooks -> load_app_hooks) that pickles the collected hooks dict to
        store it. If any installed app's hooks.py holds something unpicklable
        (a live module/function reference where a dotted-path string was
        expected), that raises "TypeError: can't pickle module objects" -
        confirmed live installing a real app. developer_mode=1 skips the cache
        entirely, sidestepping this; mef's own site:new task sets it for
        local dev sites for the same reason (`bench --site $SITE set-config
        developer_mode 1`), so mirror that here. Best-effort: a dev
        convenience setting shouldn't fail an otherwise-successful site
        creation.
        """
        cmd = self.site._frappe_call(
            "frappe", "--site", self.site.config.name, "set-config", "developer_mode", "1"
        )
        with contextlib.suppress(BenchError):
            run_command(cmd, cwd=self.site.bench.sites_path, stream_output=True)

    def restore(
        self,
        db_file: str,
        public_files: str | None = None,
        private_files: str | None = None,
    ) -> None:
        cmd = self.site._frappe_call("frappe", "--site", self.site.config.name, "restore", db_file)
        if public_files:
            cmd += ["--with-public-files", public_files]
        if private_files:
            cmd += ["--with-private-files", private_files]
        cmd += self.site.bench.db_root_args
        run_command(cmd, cwd=self.site.bench.sites_path, stream_output=True, env=self._mysql_cli_env())

    def reinstall(self, admin_password: str) -> None:
        if not isinstance(admin_password, str) or not admin_password.strip():
            raise BenchError("Site Administrator password must not be empty.")
        cmd = self.site._frappe_call(
            "frappe",
            "--site",
            self.site.config.name,
            "reinstall",
            "--yes",
            "--admin-password",
            admin_password,
        )
        cmd += self.site.bench.db_root_args
        run_command(cmd, cwd=self.site.bench.sites_path, stream_output=True, env=self._mysql_cli_env())

    def _mysql_cli_env(self) -> dict[str, str] | None:
        """Env for new-site/restore/reinstall subprocesses.

        Frappe's DbManager.restore_database (frappe/database/db_manager.py) shells
        out to the `mysql` CLI via `os.system` to import the initial SQL dump, and
        that command line only ever includes `-h {host}` - never `-P`/`--port`.
        Invisible on a default local MariaDB (port 3306); mef runs each project's
        MariaDB on its own dbdeployer-derived port, so without a port the `mysql`
        CLI falls back to its own default (3306), fails to connect, and the SQL
        import silently no-ops - confirmed live, repeatedly: "ERROR 2002 (HY000):
        Can't connect to server on '127.0.0.1' (36)" every time, gone the instant
        the port matches. `mysql` reads `MYSQL_TCP_PORT` as its fallback default
        port when `-P` is absent, so setting it here fixes the import regardless
        of how this process's own env was started. mise's dbdeployer activation
        sets this when a command is driven through `mise r ...`, but pilot spawns
        this subprocess directly - relying on inheriting it by accident was the
        actual bug: it only worked when pilot's own daemon happened to have last
        been (re)started from within this exact project's directory.
        """
        if self.site.bench.config.db_type != "mariadb":
            return None
        return {**os.environ, "MYSQL_TCP_PORT": str(self.site.bench.config.mariadb.port)}

    def migrate(self, skip_failing: bool) -> None:
        cmd = self.site._frappe_call("frappe", "--site", self.site.config.name, "migrate")
        if skip_failing:
            cmd.append("--skip-failing")
        run_command(cmd, cwd=self.site.bench.sites_path, stream_output=True)

    def db_args(self, db_type: str) -> list[str]:
        if db_type == "postgres":
            return self.postgres_db_args()
        if db_type == "sqlite":
            return ["--db-type", "sqlite"]

        from pilot.managers.database import MariaDBManager

        socket_path = MariaDBManager(self.site.bench.config.mariadb)._detect_socket()
        return self.mariadb_db_args(socket_path)

    def mariadb_db_args(self, socket_path: str) -> list[str]:
        # --mariadb-root-username/--mariadb-root-password, not --db-root-*: the
        # latter don't exist on Frappe v12 (confirmed live: "no such option"),
        # while the former work on every version - v13+ kept them as aliases of
        # the same option (see frappe/commands/site.py's click.option calls).
        mariadb = self.site.bench.config.mariadb
        args = ["--mariadb-root-username", mariadb.admin_user]
        if socket_path and self.site.bench.supports_db_socket_option:
            args += ["--db-socket", socket_path]
            args += ["--mariadb-root-password", mariadb.root_password or "socket_auth"]
        else:
            args += ["--db-host", mariadb.host, "--db-port", str(mariadb.port)]
            if mariadb.root_password:
                args += ["--mariadb-root-password", mariadb.root_password]
        return args

    def postgres_db_args(self) -> list[str]:
        postgres = self.site.bench.config.postgres
        return [
            "--db-type",
            "postgres",
            "--db-host",
            postgres.host,
            "--db-port",
            str(postgres.port),
            "--mariadb-root-username",
            postgres.admin_user,
            "--mariadb-root-password",
            self.site.bench.postgres_root_password,
        ]
