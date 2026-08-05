from __future__ import annotations

from app.services.builtin_indicators import (
    _builtin_specs,
    seed_builtin_indicators_for_new_user,
)
from app.services.indicator_validation import validate_indicator_code


class _FakeCursor:
    def __init__(self, db):
        self.db = db
        self.rows = []
        self.rowcount = 0
        self.closed = False

    def execute(self, sql, params=()):
        normalized = " ".join(str(sql).split()).lower()
        self.rowcount = 0
        if normalized.startswith("select name from qd_indicator_codes"):
            self.rows = [{"name": name} for name in sorted(self.db.names)]
            return
        if normalized.startswith("insert into qd_indicator_codes"):
            name = str(params[1])
            self.db.names.add(name)
            self.rowcount = 1
            return
        raise AssertionError(f"unexpected SQL: {sql}")

    def fetchall(self):
        return list(self.rows)

    def close(self):
        self.closed = True


class _FakeDb:
    def __init__(self):
        self.names = set()
        self.commits = 0
        self.rollbacks = 0
        self.cursors = []

    def cursor(self):
        cursor = _FakeCursor(self)
        self.cursors.append(cursor)
        return cursor

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def test_builtin_pack_contains_multiple_chart_only_contracts():
    specs = _builtin_specs()

    assert len(specs) >= 6
    assert len({item["name"] for item in specs}) == len(specs)
    for spec in specs:
        assert spec["code"].strip()
        assert "my_indicator_name" in spec["code"]
        assert "my_indicator_description" in spec["code"]
        assert "output =" in spec["code"]
        assert "order_target" not in spec["code"]
        assert "submit_order" not in spec["code"]


def test_builtin_indicator_code_passes_chart_only_validation():
    for spec in _builtin_specs():
        result = validate_indicator_code(spec["code"])
        assert result["success"], (spec["name"], result)


def test_builtin_pack_backfills_existing_user_idempotently():
    db = _FakeDb()

    first = seed_builtin_indicators_for_new_user(db, 42)
    second = seed_builtin_indicators_for_new_user(db, 42)

    assert first == len(_builtin_specs())
    assert second == 0
    assert db.names == {item["name"] for item in _builtin_specs()}
    assert db.commits == 2
    assert db.rollbacks == 0
    assert all(cursor.closed for cursor in db.cursors)
