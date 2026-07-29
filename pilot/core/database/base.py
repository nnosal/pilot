from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[list[Any]]
    duration_ms: float
    truncated: bool = False
    affected_rows: int = 0


class Database(ABC):
    # Identifier quoting for the dialect: backticks for MariaDB, double quotes
    # for PostgreSQL/SQLite. Overridden per engine.
    _identifier_quote: str = '"'

    @abstractmethod
    def execute(self, query: str, read_only: bool = True) -> QueryResult: ...

    @abstractmethod
    def get_tables(self) -> list[str]: ...

    @abstractmethod
    def get_table_columns(self, table: str) -> list[dict]: ...

    def get_schema(self) -> list[dict]:
        return [{"name": t, "columns": self.get_table_columns(t)} for t in self.get_tables()]

    def get_installed_apps(self) -> list[str]:
        quote = self._identifier_quote
        result = self.execute(
            f"SELECT app_name FROM {quote}tabInstalled Application{quote} ORDER BY idx"
        )
        apps = [row[0] for row in result.rows if row[0]]
        if apps:
            return apps
        # Frappe < v13 never populated `tabInstalled Application`; it tracked
        # installed apps as a JSON list in the `installed_apps` global default.
        result = self.execute(
            f"SELECT defvalue FROM {quote}tabDefaultValue{quote} "
            "WHERE defkey='installed_apps' AND parent='__global' LIMIT 1"
        )
        return _parse_installed_apps_default(result)


def _parse_installed_apps_default(result: QueryResult) -> list[str]:
    if not result.rows or not result.rows[0][0]:
        return []
    try:
        parsed = json.loads(result.rows[0][0])
    except (json.JSONDecodeError, TypeError):
        return []
    return [app for app in parsed if isinstance(app, str)]
