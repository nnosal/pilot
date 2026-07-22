from __future__ import annotations

import json
from typing import TYPE_CHECKING

from pilot.utils import write_private_text

if TYPE_CHECKING:
    from pilot.config import S3Config
    from pilot.core.bench import Bench


class BenchConfigFiles:
    def __init__(self, bench: "Bench") -> None:
        self.bench = bench

    @property
    def db_root_args(self) -> list[str]:
        # --mariadb-root-username/--mariadb-root-password: see the matching
        # comment in pilot/core/site/commands.py's mariadb_db_args - --db-root-*
        # doesn't exist on Frappe v12, the legacy names work on every version.
        if self.bench.config.db_type == "postgres":
            postgres = self.bench.config.postgres
            return [
                "--mariadb-root-username",
                postgres.admin_user,
                "--mariadb-root-password",
                self.postgres_root_password,
            ]
        if self.bench.config.db_type == "sqlite":
            return []
        mariadb = self.bench.config.mariadb
        return [
            "--mariadb-root-username",
            mariadb.admin_user,
            "--mariadb-root-password",
            mariadb.root_password,
        ]

    @property
    def drop_site_root_args(self) -> list[str]:
        """Root creds for `bench drop-site` specifically.

        Unlike new-site/restore/reinstall, v12's `drop-site` doesn't accept
        `--mariadb-root-username`/`--mariadb-root-password` at all - only
        `--root-login`/`--root-password` (confirmed by reading its
        frappe/commands/site.py). v13+ kept `--root-login`/`--root-password` as
        aliases of the same option there too, so this spelling is the one that
        works on every version - for drop-site only, `db_root_args`'s spelling
        doesn't apply here.
        """
        if self.bench.config.db_type == "postgres":
            postgres = self.bench.config.postgres
            return ["--root-login", postgres.admin_user, "--root-password", self.postgres_root_password]
        if self.bench.config.db_type == "sqlite":
            return []
        mariadb = self.bench.config.mariadb
        return ["--root-login", mariadb.admin_user, "--root-password", mariadb.root_password]

    @property
    def postgres_root_password(self) -> str:
        return self.bench.config.postgres.root_password or "trust_auth"

    def set_maintenance_mode(self, enabled: bool) -> None:
        config_path = self.bench.sites_path / "common_site_config.json"
        config = json.loads(config_path.read_text())
        config["maintenance_mode"] = 1 if enabled else 0
        write_private_text(config_path, json.dumps(config, indent=2))

    def sync_s3_credentials(self, s3_config: "S3Config") -> None:
        config_path = self.bench.sites_path / "common_site_config.json"
        if not config_path.exists():
            return

        config = json.loads(config_path.read_text())
        config["s3_access_key"] = s3_config.access_key
        config["s3_bucket"] = s3_config.bucket
        config["s3_secret_key"] = s3_config.secret_key
        config["s3_provider"] = s3_config.provider
        config["s3_region"] = s3_config.region
        write_private_text(config_path, json.dumps(config, indent=2) + "\n")

    def write_common_site_config(self) -> None:
        redis = self.bench.config.redis
        redis_cache = f"redis://localhost:{redis.cache_port}"
        config_path = self.bench.sites_path / "common_site_config.json"
        try:
            config = json.loads(config_path.read_text()) if config_path.exists() else {}
        except json.JSONDecodeError:
            config = {}
        config.update(
            {
                "redis_cache": redis_cache,
                # mef runs a single shared redis (no dedicated queue instance) — bench.toml's
                # redis.queue_port is a placeholder kept only to satisfy RedisConfig.validate()'s
                # cache_port != queue_port rule, nothing actually listens on it. Point both at
                # the one real redis, same as redis_socketio right below.
                "redis_queue": redis_cache,
                "redis_socketio": redis_cache,
                "socketio_port": self.bench.config.socketio_port,
                "webserver_port": self.bench.config.http_port,
                "socketio_backend": self.bench.config.socketio_backend,
                "monitor": True,
            }
        )
        write_private_text(config_path, json.dumps(config, indent=2) + "\n")
