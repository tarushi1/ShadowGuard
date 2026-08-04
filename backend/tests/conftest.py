"""
Shared test fixtures.
================================================================

One job, and it is a correctness job rather than a convenience one:
make sure no test can touch the real database.

The first run after persistence landed wrote 30-odd rows into
`data/shadowguard.db` — the file that, in real use, is a record of what
someone was working on and when. Nothing sensitive was stored, because
the schema has no column that can hold prompt text, but a test suite
that writes to live user data is a defect regardless of what it writes.

`autouse=True` is deliberate. Opting in per test means the one test
someone adds without the fixture writes to the real file, and the only
symptom is rows that nobody notices for months.
"""

import os

import pytest

from backend.app.db import session


@pytest.fixture(autouse=True)
def isolated_database(tmp_path, monkeypatch):
    """Point every test at its own throwaway database.

    A file rather than `:memory:`, and the reason matters: SQLite gives
    each CONNECTION its own in-memory database, so a test that writes
    through one session and reads through another would find an empty
    schema. That failure looks exactly like a persistence bug in the
    code under test, which is the worst possible way to lose an hour.
    """
    monkeypatch.setenv("SHADOWGUARD_DB_URL", f"sqlite:///{tmp_path / 'test.db'}")

    # The engine is cached, so it has to be dropped on the way in AND
    # on the way out — in once so this test does not inherit the
    # previous one's engine, out once so the next caller does not
    # inherit a handle to a tmp_path that pytest is about to delete.
    session.reset_for_tests()
    session.create_tables()

    yield

    session.reset_for_tests()
