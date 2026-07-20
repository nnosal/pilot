from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from pilot.exceptions import BenchError
from pilot.integrations.s3.backups import OffsiteBackup
from pilot.tasks import Task, step


@dataclass(kw_only=True)
class RestoreSiteTask(Task):
    command: ClassVar[str] = "restore-site"

    site: str
    timestamp: str

    def run(self) -> None:
        self.require_production_privileges()
        self.restore()

    @step("restore", lambda self: f"Restore site {self.site} from backup {self.timestamp}")
    def restore(self) -> None:
        site = self.bench.site(self.site)
        db_file, public_files, private_files = self._resolve_files(site)
        site.restore(str(db_file), str(public_files) if public_files else None,
                     str(private_files) if private_files else None)

    def _resolve_files(self, site) -> tuple[Path, Path | None, Path | None]:
        directory = site.backups.directory
        directory.mkdir(parents=True, exist_ok=True)
        local = self._local_files(directory)
        missing = {"database", "files", "private_files"} - set(local)
        if missing and self.bench.config.s3.is_configured:
            local.update(self._download_missing(site, directory, missing))

        if "database" not in local:
            raise BenchError(f"No database backup file found for {self.site} at {self.timestamp}.")
        return local["database"], local.get("files"), local.get("private_files")

    def _local_files(self, directory: Path) -> dict[str, Path]:
        from pilot.integrations.s3.backups import file_type_of

        if not directory.is_dir():
            return {}
        return {
            file_type_of(path.name): path
            for path in directory.glob(f"{self.timestamp}-*")
            if path.is_file()
        }

    def _download_missing(self, site, directory: Path, missing: set[str]) -> dict[str, Path]:
        offsite = OffsiteBackup.from_config(self.bench.config.s3, self.bench_root)
        run = offsite.get_backup(self.site, self.timestamp)
        if not run:
            return {}
        downloaded = {}
        for file_type, entry in run.items():
            if file_type not in missing:
                continue
            destination = directory / entry["filename"]
            offsite.download(self.site, self.timestamp, entry["filename"], destination)
            downloaded[file_type] = destination
        return downloaded


if __name__ == "__main__":
    RestoreSiteTask.main()
