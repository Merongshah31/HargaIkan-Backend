"""A tiny in-memory stand-in for the supabase-py client used by the Supabase repository tests.

It records every builder call (``FakeQuery.calls``) and applies the filters the repositories use
(eq, in_, like, order, range, limit, head counts) to rows held per table, so tests can check both
the mapping and that the right filters (e.g. ``publish_status = 'live'``) were sent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class FakeResponse:
    data: list[dict[str, Any]]
    count: int | None = None


class FakeQuery:
    def __init__(self, client: FakeClient, table: str) -> None:
        self.client = client
        self.table = table
        self.calls: list[tuple[str, tuple, dict]] = []
        self.auth_header = client.postgrest.headers.get("Authorization")
        client.queries.append(self)

    def _rec(self, name: str, *args: Any, **kwargs: Any) -> FakeQuery:
        self.calls.append((name, args, kwargs))
        return self

    def select(self, *a: Any, **k: Any) -> FakeQuery: return self._rec("select", *a, **k)
    def eq(self, *a: Any, **k: Any) -> FakeQuery: return self._rec("eq", *a, **k)
    def in_(self, *a: Any, **k: Any) -> FakeQuery: return self._rec("in_", *a, **k)
    def like(self, *a: Any, **k: Any) -> FakeQuery: return self._rec("like", *a, **k)
    def order(self, *a: Any, **k: Any) -> FakeQuery: return self._rec("order", *a, **k)
    def range(self, *a: Any, **k: Any) -> FakeQuery: return self._rec("range", *a, **k)
    def limit(self, *a: Any, **k: Any) -> FakeQuery: return self._rec("limit", *a, **k)
    def upsert(self, *a: Any, **k: Any) -> FakeQuery: return self._rec("upsert", *a, **k)
    def delete(self, *a: Any, **k: Any) -> FakeQuery: return self._rec("delete", *a, **k)

    def has(self, name: str, *args: Any) -> bool:
        return any(n == name and a == args for n, a, _ in self.calls)

    def method(self) -> str:
        for n, _, _ in self.calls:
            if n in ("select", "upsert", "delete"):
                return n
        return "?"

    def execute(self) -> FakeResponse:
        self.client.executed.append(self)
        if self.method() != "select":
            return FakeResponse([])
        rows = [dict(r) for r in self.client.tables.get(self.table, [])]
        head = count = False
        for name, args, kwargs in self.calls:
            if name == "select":
                head = bool(kwargs.get("head"))
                count = kwargs.get("count") is not None
            elif name == "eq":
                col, val = args
                rows = [r for r in rows if r.get(col) == val]
            elif name == "in_":
                col, vals = args
                rows = [r for r in rows if r.get(col) in list(vals)]
            elif name == "like":
                col, pattern = args
                needle = pattern.strip("%")
                rows = [r for r in rows if needle in str(r.get(col, ""))]
            elif name == "order":
                rows.sort(key=lambda r: str(r.get(args[0])), reverse=bool(kwargs.get("desc")))
            elif name == "range":
                start, end = args
                rows = rows[start : end + 1]
            elif name == "limit":
                rows = rows[: args[0]]
        n = len(rows) if count else None
        return FakeResponse([] if head else rows, n)


@dataclass
class FakePostgrest:
    headers: dict[str, str] = field(default_factory=dict)
    auth_calls: list[str] = field(default_factory=list)

    def auth(self, token: str) -> FakePostgrest:
        self.auth_calls.append(token)
        self.headers["Authorization"] = f"Bearer {token}"
        return self


class FakeClient:
    def __init__(self, tables: dict[str, list[dict[str, Any]]] | None = None) -> None:
        self.tables = {k: list(v) for k, v in (tables or {}).items()}
        self.queries: list[FakeQuery] = []
        self.executed: list[FakeQuery] = []
        self.postgrest = FakePostgrest()

    def table(self, name: str) -> FakeQuery:
        return FakeQuery(self, name)

    def queries_for(self, table: str) -> list[FakeQuery]:
        return [q for q in self.executed if q.table == table]
